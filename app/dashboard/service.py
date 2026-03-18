from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from statistics import mean

from app.checklist.data import (
    CLOSE_CHECKLIST,
    CLOSE_RESIDUAL_LABELS_BY_KEY,
    CLOSE_RESIDUAL_UNITS_BY_KEY,
    CLOSE_SECTION_EMOJI_BY_TITLE,
)
from app.checklist.ui import checklist_total_items


# Максимальное количество строк смен на экране.
DASHBOARD_SHIFTS_LIMIT = 250

# Коэффициенты отклонений остатка относительно среднего.
ANOMALY_UPPER_FACTOR = 1.5
ANOMALY_LOWER_FACTOR = 0.5

RESIDUAL_LABELS = dict(CLOSE_RESIDUAL_LABELS_BY_KEY)
RESIDUAL_UNITS = dict(CLOSE_RESIDUAL_UNITS_BY_KEY)

# Префиксы в старых человекочитаемых названиях остатков.
RESIDUAL_LABEL_PREFIXES = (
    "Зафиксировать остаток ",
    "Остаток ",
)

CHECKLIST_SECTION_SHORT_TITLES = {
    "Передача заготовок на следующую смену": "Передача",
    "Остатки продуктов": "Остатки",
    "Продукты": "Продукты",
    "Рабочая зона": "Рабочая зона",
    "Фритюр": "Фритюр",
    "Расходники": "Расходники",
    "Зал": "Зал",
    "Выключение оборудования": "Оборудование",
}


@dataclass(slots=True, frozen=True)
class DashboardFilters:
    """Фильтры операционного дашборда.

    Args:
        date: Дата в формате YYYY-MM-DD.
    """

    date: str | None


def format_duration(
    minutes: int | None,
) -> str:
    """Форматирует длительность в человекочитаемый вид.

    Args:
        minutes: Длительность в минутах.

    Returns:
        Строка в формате `X мин` или `Y ч Z мин`.
    """
    if minutes is None:
        return "—"
    if minutes < 60:
        return f"{minutes} мин"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours} ч {mins} мин"


def _table_exists(
    conn: sqlite3.Connection,
    table_name: str,
) -> bool:
    """Проверяет существование таблицы в SQLite.

    Args:
        conn: Подключение SQLite.
        table_name: Название таблицы.

    Returns:
        True, если таблица существует.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(
    conn: sqlite3.Connection,
    table_name: str,
) -> set[str]:
    """Возвращает список колонок таблицы SQLite.

    Args:
        conn: Подключение SQLite.
        table_name: Название таблицы.

    Returns:
        Множество имён колонок.
    """
    if not _table_exists(conn, table_name):
        return set()
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]) for row in rows}


def _parse_iso_datetime(
    raw_value: str | None,
) -> datetime | None:
    """Пытается распарсить ISO-дату/время.

    Args:
        raw_value: Сырой текст даты/времени.

    Returns:
        datetime или None.
    """
    if not raw_value:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _duration_minutes(
    start: datetime | None,
    end: datetime | None,
) -> int | None:
    """Считает длительность в минутах.

    Args:
        start: Время начала.
        end: Время окончания.

    Returns:
        Длительность в минутах или None.
    """
    if not start or not end:
        return None
    seconds = (end - start).total_seconds()
    if seconds < 0:
        return None
    return int(round(seconds / 60))


def _fmt_date(
    raw_date: str | None,
) -> str:
    """Форматирует дату в вид DD.MM.YYYY.

    Args:
        raw_date: Дата в формате YYYY-MM-DD.

    Returns:
        Отформатированная дата.
    """
    if not raw_date:
        return "—"
    text = str(raw_date).strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return text


def _fmt_time(
    raw_value: str | None,
) -> str:
    """Форматирует время в HH:MM.

    Args:
        raw_value: ISO-время или datetime-текст.

    Returns:
        Время в формате HH:MM.
    """
    dt = _parse_iso_datetime(raw_value)
    if dt:
        return dt.strftime("%H:%M")

    if not raw_value:
        return "—"
    text = str(raw_value).strip()
    if len(text) >= 5 and text[2] == ":":
        return text[:5]
    if "T" in text:
        tail = text.split("T", maxsplit=1)[1]
        if len(tail) >= 5 and tail[2] == ":":
            return tail[:5]
    return text or "—"


def _fmt_number(
    value: float | None,
) -> str:
    """Форматирует число без лишних нулей.

    Args:
        value: Число.

    Returns:
        Строковое представление числа.
    """
    if value is None:
        return "—"
    return f"{float(value):.3f}".rstrip("0").rstrip(".")


def _humanize_residual_label(
    item_key: str,
    raw_label: str | None,
) -> str:
    """Возвращает короткое название позиции остатка.

    Args:
        item_key: Ключ продукта.
        raw_label: Сырое название позиции из БД.

    Returns:
        Короткое человекочитаемое название.
    """
    mapped = RESIDUAL_LABELS.get(item_key)
    if mapped:
        return mapped

    label = str(raw_label or "").strip()
    if not label:
        return item_key

    for prefix in RESIDUAL_LABEL_PREFIXES:
        if label.startswith(prefix):
            return label[len(prefix) :].strip().capitalize()
    return label


def _parse_completed_indexes(
    raw_json: str | None,
) -> set[int]:
    """Парсит JSON-список выполненных индексов чек-листа.

    Args:
        raw_json: JSON-строка.

    Returns:
        Множество индексов.
    """
    if not raw_json:
        return set()
    try:
        data = json.loads(raw_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return set()
    if not isinstance(data, list):
        return set()

    result: set[int] = set()
    for value in data:
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _close_checklist_sections() -> list[dict[str, object]]:
    """Возвращает структуру секций чек-листа закрытия.

    Args:
        Нет параметров.

    Returns:
        Список секций с диапазонами индексов.
    """
    sections: list[dict[str, object]] = []
    cursor = 0
    for section in CLOSE_CHECKLIST:
        title = str(section["title"]).strip()
        count = len(section["items"])
        emoji = CLOSE_SECTION_EMOJI_BY_TITLE.get(title, "▫️")
        sections.append(
            {
                "title": title,
                "emoji": emoji,
                "start": cursor,
                "end": cursor + count - 1,
                "total": count,
            }
        )
        cursor += count
    return sections


def _close_checklist_status_labels(
    completed_indexes: set[int],
) -> tuple[list[str], int, int]:
    """Собирает статусные ярлыки блоков закрытия.

    Args:
        completed_indexes: Выполненные индексы.

    Returns:
        Кортеж: список ярлыков, выполнено, всего.
    """
    labels: list[str] = []
    done_total = 0
    total = checklist_total_items("close")
    for section in _close_checklist_sections():
        start = int(section["start"])
        end = int(section["end"])
        section_total = int(section["total"])
        section_done = sum(1 for idx in range(start, end + 1) if idx in completed_indexes)
        done_total += section_done
        section_title = str(section["title"])
        short_title = CHECKLIST_SECTION_SHORT_TITLES.get(section_title, section_title)
        if section_done == section_total:
            labels.append(f"✔ {short_title}")
        else:
            labels.append(f"⚠ {short_title}")
    return labels, done_total, total


def _coalesce_employee(
    row: sqlite3.Row,
) -> str:
    """Возвращает имя сотрудника из строки смены.

    Args:
        row: Строка таблицы shifts.

    Returns:
        Имя сотрудника.
    """
    opened_by = str(row["opened_by"] or "").strip()
    if opened_by:
        return opened_by
    employee = str(row["employee"] or "").strip()
    if employee:
        return employee
    return "—"


def _fetch_shifts(
    conn: sqlite3.Connection,
    filters: DashboardFilters,
) -> list[sqlite3.Row]:
    """Читает смены с фильтром по дате.

    Args:
        conn: Подключение SQLite.
        filters: Фильтры дашборда.

    Returns:
        Список строк смен.
    """
    if not _table_exists(conn, "shifts"):
        return []

    conditions: list[str] = []
    params: list[object] = []
    if filters.date:
        conditions.append("s.date = ?")
        params.append(filters.date)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
    SELECT
        s.id,
        s.date,
        s.employee,
        s.open_time,
        s.close_time,
        s.opened_at,
        s.closed_at,
        s.opened_by,
        s.close_started_at,
        s.close_duration_sec,
        s.status
    FROM shifts s
    {where_clause}
    ORDER BY s.date DESC, s.id DESC
    LIMIT ?
    """
    params.append(DASHBOARD_SHIFTS_LIMIT)
    return conn.execute(query, tuple(params)).fetchall()


def _fetch_checklists_for_shifts(
    conn: sqlite3.Connection,
    shift_ids: list[int],
) -> dict[int, dict[str, set[int]]]:
    """Возвращает состояния чек-листов по сменам.

    Args:
        conn: Подключение SQLite.
        shift_ids: Список ID смен.

    Returns:
        Словарь: shift_id -> checklist_type -> completed_indexes.
    """
    if not shift_ids or not _table_exists(conn, "checklist_state"):
        return {}

    placeholders = ",".join("?" for _ in shift_ids)
    rows = conn.execute(
        f"""
        SELECT shift_id, checklist_type, completed
        FROM checklist_state
        WHERE shift_id IN ({placeholders})
        """,
        tuple(shift_ids),
    ).fetchall()

    result: dict[int, dict[str, set[int]]] = {}
    for row in rows:
        shift_id = int(row["shift_id"])
        checklist_type = str(row["checklist_type"] or "").strip()
        result.setdefault(shift_id, {})
        result[shift_id][checklist_type] = _parse_completed_indexes(str(row["completed"] or "[]"))
    return result


def _fetch_residuals_for_shifts(
    conn: sqlite3.Connection,
    shift_ids: list[int],
) -> list[sqlite3.Row]:
    """Возвращает остатки закрытия для выбранных смен.

    Args:
        conn: Подключение SQLite.
        shift_ids: Список ID смен.

    Returns:
        Список строк close_residuals.
    """
    if not shift_ids or not _table_exists(conn, "close_residuals"):
        return []

    columns = _table_columns(conn, "close_residuals")
    input_value_expr = "input_value" if "input_value" in columns else "NULL AS input_value"
    unit_type_expr = "unit_type" if "unit_type" in columns else "NULL AS unit_type"
    normalized_quantity_expr = (
        "normalized_quantity" if "normalized_quantity" in columns else "NULL AS normalized_quantity"
    )
    normalized_unit_expr = (
        "normalized_unit" if "normalized_unit" in columns else "NULL AS normalized_unit"
    )

    placeholders = ",".join("?" for _ in shift_ids)
    return conn.execute(
        f"""
        SELECT
            shift_id,
            item_key,
            item_label,
            quantity,
            unit,
            {input_value_expr},
            {unit_type_expr},
            {normalized_quantity_expr},
            {normalized_unit_expr},
            date,
            time
        FROM close_residuals
        WHERE shift_id IN ({placeholders})
        ORDER BY date DESC, time DESC, id DESC
        """,
        tuple(shift_ids),
    ).fetchall()


def _fetch_residual_baseline_avg(
    conn: sqlite3.Connection,
) -> dict[str, float]:
    """Возвращает средние значения остатков по всем сменам.

    Args:
        conn: Подключение SQLite.

    Returns:
        Словарь: item_key -> average_quantity.
    """
    if not _table_exists(conn, "close_residuals"):
        return {}

    columns = _table_columns(conn, "close_residuals")
    quantity_expr = (
        "COALESCE(normalized_quantity, quantity)"
        if "normalized_quantity" in columns
        else "quantity"
    )
    rows = conn.execute(
        f"""
        SELECT item_key, AVG({quantity_expr}) AS avg_quantity
        FROM close_residuals
        GROUP BY item_key
        """
    ).fetchall()

    result: dict[str, float] = {}
    for row in rows:
        key = str(row["item_key"] or "").strip()
        if not key:
            continue
        avg_quantity = row["avg_quantity"]
        if avg_quantity is None:
            continue
        result[key] = float(avg_quantity)
    return result


def _is_residual_anomaly(
    value: float,
    avg: float,
) -> bool:
    """Проверяет значение остатка на сильное отклонение от среднего.

    Args:
        value: Текущее значение.
        avg: Среднее значение.

    Returns:
        True, если значение выходит за диапазон.
    """
    if avg <= 0:
        return False
    return value > avg * ANOMALY_UPPER_FACTOR or value < avg * ANOMALY_LOWER_FACTOR


def _residual_row_normalized_value(
    row: sqlite3.Row,
) -> float:
    """Возвращает нормализованное значение строки остатков.

    Args:
        row: Строка таблицы close_residuals.

    Returns:
        Нормализованное значение.
    """
    if row["normalized_quantity"] is not None:
        return float(row["normalized_quantity"])
    return float(row["quantity"])


def _residual_row_normalized_unit(
    row: sqlite3.Row,
    item_key: str,
) -> str:
    """Возвращает базовую единицу строки остатков.

    Args:
        row: Строка таблицы close_residuals.
        item_key: Ключ продукта.

    Returns:
        Единица измерения.
    """
    normalized_unit = str(row["normalized_unit"] or "").strip()
    if normalized_unit:
        return normalized_unit
    unit = str(row["unit"] or "").strip()
    if unit:
        return unit
    return RESIDUAL_UNITS.get(item_key, "")


def _build_shift_models(
    shifts_rows: list[sqlite3.Row],
    checklist_state: dict[int, dict[str, set[int]]],
) -> list[dict[str, object]]:
    """Формирует модели смен для таблицы.

    Args:
        shifts_rows: Сырые строки смен.
        checklist_state: Состояния чек-листов по сменам.

    Returns:
        Список моделей смен.
    """
    models: list[dict[str, object]] = []
    for row in shifts_rows:
        shift_id = int(row["id"])
        status_raw = str(row["status"] or "").strip().upper()
        opened_raw = str(row["opened_at"] or row["open_time"] or "")
        closed_raw = str(row["closed_at"] or row["close_time"] or "")

        opened_dt = _parse_iso_datetime(opened_raw)
        closed_dt = _parse_iso_datetime(closed_raw)
        duration_minutes = _duration_minutes(opened_dt, closed_dt)

        close_started_raw = str(row["close_started_at"] or "").strip()
        close_started_dt = _parse_iso_datetime(close_started_raw)
        if row["close_duration_sec"] is not None:
            close_duration_minutes = int(round(float(row["close_duration_sec"]) / 60))
        else:
            close_duration_minutes = _duration_minutes(close_started_dt, closed_dt)

        close_state = checklist_state.get(shift_id, {}).get("close", set())
        close_status_labels, close_done, close_total = _close_checklist_status_labels(close_state)
        sections_total = len(close_status_labels)
        sections_done = sum(1 for label in close_status_labels if label.startswith("✔"))
        is_closed_shift = status_raw == "CLOSED" or closed_dt is not None
        has_checklist_error = bool(is_closed_shift and close_done < close_total)
        close_status_summary = (
            f"{sections_done}/{sections_total} блоков"
            if is_closed_shift
            else "В процессе"
        )
        close_status_details = close_status_labels if is_closed_shift else []

        models.append(
            {
                "id": shift_id,
                "date_label": _fmt_date(str(row["date"] or "")),
                "employee": _coalesce_employee(row),
                "status": status_raw or "—",
                "opened_at": _fmt_time(opened_raw),
                "closed_at": _fmt_time(closed_raw),
                "duration_minutes": duration_minutes,
                "duration_label": format_duration(duration_minutes),
                "close_duration_minutes": close_duration_minutes,
                "close_duration_label": format_duration(close_duration_minutes),
                "close_checklist_done": close_done,
                "close_checklist_total": close_total,
                "close_status_labels": close_status_details,
                "close_status_summary": close_status_summary,
                "has_checklist_error": has_checklist_error,
            }
        )
    return models


def _build_residual_analytics(
    residual_rows: list[sqlite3.Row],
    residual_baseline: dict[str, float],
) -> list[dict[str, object]]:
    """Строит упрощённую таблицу остатков.

    Args:
        residual_rows: Остатки по выбранным сменам.
        residual_baseline: Средние значения остатков.

    Returns:
        Список строк для таблицы остатков.
    """
    latest_by_key: dict[str, sqlite3.Row] = {}
    for row in residual_rows:
        item_key = str(row["item_key"] or "").strip()
        if not item_key or item_key in latest_by_key:
            continue
        latest_by_key[item_key] = row

    rows: list[dict[str, object]] = []
    item_keys = set(residual_baseline.keys()) | set(latest_by_key.keys())
    for item_key in sorted(item_keys):
        baseline_avg = residual_baseline.get(item_key)
        latest = latest_by_key.get(item_key)

        if latest:
            current_value = _residual_row_normalized_value(latest)
            unit = _residual_row_normalized_unit(latest, item_key)
            label = _humanize_residual_label(item_key, str(latest["item_label"] or ""))
        else:
            current_value = None
            unit = RESIDUAL_UNITS.get(item_key, "")
            label = _humanize_residual_label(item_key, None)

        deviation_pct: float | None = None
        is_anomaly = False
        deviation_indicator = "•"
        if current_value is not None and baseline_avg is not None and baseline_avg > 0:
            deviation_pct = round(((current_value - baseline_avg) / baseline_avg) * 100, 1)
            is_anomaly = _is_residual_anomaly(current_value, baseline_avg)
            if deviation_pct > 0:
                deviation_indicator = "🔺"
            elif deviation_pct < 0:
                deviation_indicator = "🔻"

        rows.append(
            {
                "item_key": item_key,
                "item_label": label,
                "current_value": current_value,
                "current_label": _fmt_number(current_value),
                "avg_value": baseline_avg,
                "avg_label": _fmt_number(baseline_avg),
                "unit": unit,
                "deviation_pct": deviation_pct,
                "deviation_indicator": deviation_indicator if deviation_pct is not None else "",
                "deviation_label": (
                    f"{deviation_indicator} {int(round(deviation_pct)):+d}%"
                    if deviation_pct is not None
                    else "—"
                ),
                "is_anomaly": is_anomaly,
            }
        )

    rows.sort(
        key=lambda row: (
            not bool(row["is_anomaly"]),
            str(row["item_label"]),
        )
    )
    return rows


def _build_employee_analytics(
    shifts: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Строит упрощённую аналитику по сотрудникам.

    Args:
        shifts: Список смен.

    Returns:
        Список строк для таблицы сотрудников.
    """
    grouped: dict[str, list[dict[str, object]]] = {}
    for shift in shifts:
        employee = str(shift["employee"])
        grouped.setdefault(employee, []).append(shift)

    rows: list[dict[str, object]] = []
    for employee, employee_shifts in grouped.items():
        close_times = [
            float(shift["close_duration_minutes"])
            for shift in employee_shifts
            if shift["close_duration_minutes"] is not None
        ]
        avg_close = int(round(mean(close_times))) if close_times else None
        rows.append(
            {
                "employee": employee,
                "shifts_count": len(employee_shifts),
                "avg_close_minutes": avg_close,
                "avg_close_label": format_duration(avg_close),
            }
        )

    rows.sort(
        key=lambda row: (
            -int(row["shifts_count"]),
            str(row["employee"]),
        )
    )
    return rows


def _build_kpi(
    shifts: list[dict[str, object]],
) -> dict[str, object]:
    """Собирает KPI-блок операционного дашборда.

    Args:
        shifts: Список смен.

    Returns:
        Словарь с тремя KPI.
    """
    close_times = [
        float(row["close_duration_minutes"])
        for row in shifts
        if row["close_duration_minutes"] is not None
    ]
    avg_close = int(round(mean(close_times))) if close_times else None

    checklist_rates: list[float] = []
    for row in shifts:
        total = int(row["close_checklist_total"])
        done = int(row["close_checklist_done"])
        if total > 0:
            checklist_rates.append((done / total) * 100)
    checklist_completion_pct = int(round(mean(checklist_rates))) if checklist_rates else 0

    return {
        "shifts_count": len(shifts),
        "avg_close_minutes": avg_close,
        "avg_close_label": format_duration(avg_close),
        "checklist_completion_pct": checklist_completion_pct,
        "checklist_completion_label": f"{checklist_completion_pct}%",
    }


def _build_charts(
    residual_analytics: list[dict[str, object]],
    shifts: list[dict[str, object]],
) -> dict[str, object]:
    """Готовит данные для графиков Chart.js.

    Args:
        residual_analytics: Таблица остатков.
        shifts: Список смен.

    Returns:
        Словарь с данными графиков.
    """
    residual_labels: list[str] = []
    residual_current: list[float] = []
    residual_avg: list[float] = []
    for row in residual_analytics:
        if row["current_value"] is None and row["avg_value"] is None:
            continue
        residual_labels.append(str(row["item_label"]))
        residual_current.append(float(row["current_value"] or 0))
        residual_avg.append(float(row["avg_value"] or 0))

    shifts_timeline = sorted(shifts, key=lambda row: int(row["id"]))
    close_labels: list[str] = []
    close_values: list[float] = []
    for row in shifts_timeline:
        close_minutes = row["close_duration_minutes"]
        if close_minutes is None:
            continue
        close_labels.append(f"#{row['id']}")
        close_values.append(float(close_minutes))

    return {
        "residual_bar": {
            "labels": residual_labels,
            "current_values": residual_current,
            "avg_values": residual_avg,
        },
        "close_line": {
            "labels": close_labels,
            "values": close_values,
        },
    }


def build_dashboard_payload(
    conn: sqlite3.Connection,
    filters: DashboardFilters,
) -> dict[str, object]:
    """Собирает полный payload операционного дашборда.

    Args:
        conn: Подключение SQLite.
        filters: Фильтры UI.

    Returns:
        Контекст для Jinja2.
    """
    shifts_rows = _fetch_shifts(conn, filters)
    shift_ids = [int(row["id"]) for row in shifts_rows]

    checklist_state = _fetch_checklists_for_shifts(conn, shift_ids)
    residual_rows = _fetch_residuals_for_shifts(conn, shift_ids)
    residual_baseline = _fetch_residual_baseline_avg(conn)

    shifts = _build_shift_models(
        shifts_rows=shifts_rows,
        checklist_state=checklist_state,
    )
    residual_analytics = _build_residual_analytics(
        residual_rows=residual_rows,
        residual_baseline=residual_baseline,
    )
    employee_analytics = _build_employee_analytics(shifts)
    kpi = _build_kpi(shifts)
    charts = _build_charts(residual_analytics, shifts)

    return {
        "kpi": kpi,
        "shifts": shifts,
        "residuals": residual_analytics,
        "employees": employee_analytics,
        "charts": charts,
        "filters": {
            "date": filters.date or "",
        },
    }
