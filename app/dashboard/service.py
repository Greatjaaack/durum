from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone as _utc_tz

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
    "Остатки продуктов": "Остатки",
    "Убрать продукты": "Убрать",
    "Подготовка к следующей смене": "Подготовка",
    "Передача заготовок на следующую смену": "Передача",
    "Чистота": "Чистота",
    "Зал": "Зал",
    "Фритюр": "Фритюр",
    "Выключение оборудования": "Оборудование",
}


@dataclass(slots=True, frozen=True)
class DashboardFilters:
    """Фильтры операционного дашборда.

    Args:
        date_from: Начало диапазона в формате YYYY-MM-DD.
        date_to: Конец диапазона в формате YYYY-MM-DD.
    """

    date_from: str | None
    date_to: str | None


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


def _to_utc(dt: datetime) -> datetime:
    """Приводит datetime к UTC: aware остаётся, naive считается UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_utc_tz.utc)
    return dt.astimezone(_utc_tz.utc)


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
    seconds = (_to_utc(end) - _to_utc(start)).total_seconds()
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


def _load_display_name_map(
    conn: sqlite3.Connection,
) -> dict[int, str]:
    """Загружает маппинг telegram_id → display_name из employee_profiles.

    Args:
        conn: Подключение SQLite.

    Returns:
        Словарь telegram_id → display_name.
    """
    if not _table_exists(conn, "employee_profiles"):
        return {}
    rows = conn.execute(
        "SELECT telegram_id, display_name FROM employee_profiles WHERE display_name != ''"
    ).fetchall()
    return {int(row["telegram_id"]): str(row["display_name"]) for row in rows}


def _coalesce_employee(
    row: sqlite3.Row,
    name_map: dict[int, str] | None = None,
) -> str:
    """Возвращает имя сотрудника из строки смены.

    Args:
        row: Строка таблицы shifts.
        name_map: Маппинг telegram_id → display_name.

    Returns:
        Имя сотрудника.
    """
    employee_id_raw = None
    try:
        employee_id_raw = row["employee_id"]
    except (IndexError, KeyError):
        pass

    if name_map and employee_id_raw is not None:
        try:
            display = name_map.get(int(employee_id_raw))
            if display:
                return display
        except (TypeError, ValueError):
            pass

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
    if filters.date_from:
        conditions.append("s.date >= ?")
        params.append(filters.date_from)
    if filters.date_to:
        conditions.append("s.date <= ?")
        params.append(filters.date_to)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
    SELECT
        s.id,
        s.date,
        s.employee,
        s.employee_id,
        s.open_time,
        s.close_time,
        s.opened_at,
        s.closed_at,
        s.opened_by,
        s.close_started_at,
        s.close_duration_sec,
        s.last_mid_at,
        s.mid_started_at,
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


def _short_media_label(raw_label: str) -> str:
    """Сокращает подпись пункта для отображения рядом с фото.

    Args:
        raw_label: Полный текст пункта.

    Returns:
        Короткая подпись.
    """
    text = raw_label.strip()
    if len(text) <= 36:
        return text
    return f"{text[:35].rstrip()}…"


def _fetch_close_media_for_shifts(
    conn: sqlite3.Connection,
    shift_ids: list[int],
) -> dict[int, list[dict[str, object]]]:
    """Возвращает фото-подтверждения по сменам.

    Args:
        conn: Подключение SQLite.
        shift_ids: Список ID смен.

    Returns:
        Словарь: shift_id -> список фото.
    """
    if not shift_ids or not _table_exists(conn, "close_checklist_media"):
        return {}

    placeholders = ",".join("?" for _ in shift_ids)
    rows = conn.execute(
        f"""
        SELECT id, shift_id, item_index, item_label, created_at
        FROM close_checklist_media
        WHERE shift_id IN ({placeholders})
        ORDER BY shift_id DESC, item_index ASC, id DESC
        """,
        tuple(shift_ids),
    ).fetchall()

    result: dict[int, list[dict[str, object]]] = {}
    for row in rows:
        shift_id = int(row["shift_id"])
        item_label = str(row["item_label"] or "Фото")
        result.setdefault(shift_id, []).append(
            {
                "media_id": int(row["id"]),
                "item_label": item_label,
                "item_short": _short_media_label(item_label),
                "created_at": str(row["created_at"] or ""),
            }
        )
    return result


def _fetch_open_media_for_shifts(
    conn: sqlite3.Connection,
    shift_ids: list[int],
) -> dict[int, list[dict[str, object]]]:
    """Возвращает фото открытия по сменам.

    Args:
        conn: Подключение SQLite.
        shift_ids: Список ID смен.

    Returns:
        Словарь: shift_id -> список фото.
    """
    if not shift_ids or not _table_exists(conn, "open_checklist_media"):
        return {}

    placeholders = ",".join("?" for _ in shift_ids)
    rows = conn.execute(
        f"""
        SELECT id, shift_id, item_index, item_label, created_at
        FROM open_checklist_media
        WHERE shift_id IN ({placeholders})
        ORDER BY shift_id DESC, item_index ASC, id DESC
        """,
        tuple(shift_ids),
    ).fetchall()

    result: dict[int, list[dict[str, object]]] = {}
    for row in rows:
        shift_id = int(row["shift_id"])
        item_label = str(row["item_label"] or "Фото открытия")
        result.setdefault(shift_id, []).append(
            {
                "media_id": int(row["id"]),
                "item_label": item_label,
                "item_short": _short_media_label(item_label),
                "created_at": str(row["created_at"] or ""),
            }
        )
    return result


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
    if avg < 0:
        return False
    if avg == 0:
        # Среднее нулевое, но есть реальное значение — это аномалия
        return value > 0
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
    close_media_by_shift: dict[int, list[dict[str, object]]],
    open_media_by_shift: dict[int, list[dict[str, object]]],
    name_map: dict[int, str] | None = None,
) -> list[dict[str, object]]:
    """Формирует модели смен для таблицы.

    Args:
        shifts_rows: Сырые строки смен.
        checklist_state: Состояния чек-листов по сменам.
        close_media_by_shift: Фото закрытия по сменам.
        open_media_by_shift: Фото открытия по сменам.
        name_map: Маппинг telegram_id → display_name.

    Returns:
        Список моделей смен.
    """
    if name_map is None:
        name_map = {}

    models: list[dict[str, object]] = []
    for row in shifts_rows:
        shift_id = int(row["id"])
        status_raw = str(row["status"] or "").strip().upper()

        # Время начала открытия (когда сотрудник начал процесс открытия)
        open_started_raw = str(row["open_time"] or "").strip()
        open_started_dt = _parse_iso_datetime(open_started_raw)

        # Время конца открытия (когда смена была официально открыта)
        opened_raw = str(row["opened_at"] or row["open_time"] or "")
        opened_dt = _parse_iso_datetime(opened_raw)

        # Время начала и конца ведения смены
        mid_started_raw = str(row["mid_started_at"] or "").strip()
        mid_started_dt = _parse_iso_datetime(mid_started_raw)
        last_mid_raw = str(row["last_mid_at"] or "").strip()
        last_mid_dt = _parse_iso_datetime(last_mid_raw)

        # Время закрытия
        closed_raw = str(row["closed_at"] or row["close_time"] or "")
        closed_dt = _parse_iso_datetime(closed_raw)

        # Длительность процесса открытия
        opening_duration_minutes = _duration_minutes(open_started_dt, opened_dt)

        # Длительность последнего ведения смены
        mid_duration_minutes = _duration_minutes(mid_started_dt, last_mid_dt)

        # Общая длительность смены (с момента начала открытия до закрытия)
        work_duration_minutes = _duration_minutes(open_started_dt, closed_dt)

        # Для совместимости: общий duration от opened_at до closed_at
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
        if is_closed_shift:
            close_status_class = "badge--ok" if sections_done == sections_total else "badge--warn"
        else:
            close_status_class = "badge--neutral"
        close_status_details = close_status_labels if is_closed_shift else []
        media_items = close_media_by_shift.get(shift_id, [])
        open_media_items = open_media_by_shift.get(shift_id, [])

        employee_name = _coalesce_employee(row, name_map)

        # Определяем аномалии строки
        if status_raw == "OPEN":
            row_class = "row-shift-open"
        elif status_raw == "CLOSED" and not closed_raw:
            row_class = "row-shift-anomaly"
        else:
            row_class = ""

        models.append(
            {
                "id": shift_id,
                "date_label": _fmt_date(str(row["date"] or "")),
                "date_raw": str(row["date"] or ""),
                "employee": employee_name,
                "status": status_raw or "—",
                "row_class": row_class,
                # Открытие
                "open_started_at": _fmt_time(open_started_raw),
                "opened_at": _fmt_time(opened_raw),
                "opening_duration_minutes": opening_duration_minutes,
                "opening_duration_label": format_duration(opening_duration_minutes),
                # Ведение
                "mid_started_at": _fmt_time(mid_started_raw),
                "last_mid_at": _fmt_time(last_mid_raw),
                "mid_duration_minutes": mid_duration_minutes,
                "mid_duration_label": format_duration(mid_duration_minutes),
                # Закрытие
                "close_started_at_label": _fmt_time(close_started_raw),
                "closed_at": _fmt_time(closed_raw),
                "close_duration_minutes": close_duration_minutes,
                "close_duration_label": format_duration(close_duration_minutes),
                # Итого смена
                "duration_minutes": duration_minutes,
                "duration_label": format_duration(duration_minutes),
                "work_duration_minutes": work_duration_minutes,
                "work_duration_label": format_duration(work_duration_minutes),
                # Чек-лист (сохраняем для совместимости)
                "close_checklist_done": close_done,
                "close_checklist_total": close_total,
                "close_status_labels": close_status_details,
                "close_status_summary": close_status_summary,
                "close_status_class": close_status_class,
                "has_checklist_error": has_checklist_error,
                "close_media_count": len(media_items),
                "close_media_items": media_items,
                "open_media_count": len(open_media_items),
                "open_media_items": open_media_items,
                # Сырые данные для Gantt
                "open_time_raw": open_started_raw,
                "opened_at_raw": opened_raw,
                "close_started_raw": close_started_raw,
                "closed_at_raw": closed_raw,
                "employee_raw": str(row["employee"] or ""),
            }
        )
    return models


def _build_residual_analytics(
    residual_rows: list[sqlite3.Row],
    conn: sqlite3.Connection,
) -> list[dict[str, object]]:
    """Строит таблицу остатков по последней дате с детекцией аномалий.

    Показывает только текущие значения из самого свежего дня в выборке.
    Значения, отклоняющиеся от исторического среднего в 0.5×–1.5×, помечаются
    флагом is_anomaly.

    Args:
        residual_rows: Остатки по выбранным сменам (уже отсортированы по date DESC).
        conn: Подключение SQLite для расчёта исторических средних.

    Returns:
        Список строк для таблицы остатков.
    """
    # Находим самую позднюю дату в остатках
    max_date: str | None = None
    for row in residual_rows:
        date_val = str(row["date"] or "").strip()
        if date_val and (max_date is None or date_val > max_date):
            max_date = date_val

    latest_by_key: dict[str, sqlite3.Row] = {}
    for row in residual_rows:
        if max_date and str(row["date"] or "").strip() != max_date:
            continue
        item_key = str(row["item_key"] or "").strip()
        if not item_key or item_key in latest_by_key:
            continue
        latest_by_key[item_key] = row

    baseline_avg = _fetch_residual_baseline_avg(conn)

    rows: list[dict[str, object]] = []
    for item_key in sorted(latest_by_key.keys()):
        latest = latest_by_key[item_key]
        current_value = _residual_row_normalized_value(latest)
        unit = _residual_row_normalized_unit(latest, item_key)
        label = _humanize_residual_label(item_key, str(latest["item_label"] or ""))
        avg = baseline_avg.get(item_key, 0.0)
        is_anomaly = _is_residual_anomaly(current_value, avg)
        rows.append(
            {
                "item_key": item_key,
                "item_label": label,
                "current_value": current_value,
                "current_label": _fmt_number(current_value),
                "unit": unit,
                "is_anomaly": is_anomaly,
            }
        )

    rows.sort(key=lambda r: str(r["item_label"]))
    return rows


def _build_employee_analytics(
    shifts: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Строит аналитику по сотрудникам.

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
        work_times = [
            int(shift["work_duration_minutes"])
            for shift in employee_shifts
            if shift["work_duration_minutes"] is not None
        ]
        total_work_minutes = sum(work_times) if work_times else None
        rows.append(
            {
                "employee": employee,
                "shifts_count": len(employee_shifts),
                "total_work_minutes": total_work_minutes,
                "total_work_label": format_duration(total_work_minutes),
            }
        )

    rows.sort(
        key=lambda row: (
            -int(row["shifts_count"]),
            str(row["employee"]),
        )
    )
    return rows


_RU_WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _build_work_schedule_matrix(
    shifts_rows: list[sqlite3.Row],
    name_map: dict[int, str],
) -> dict[str, object]:
    """Строит матрицу «кто в какой день работал».

    Args:
        shifts_rows: Сырые строки смен.
        name_map: Маппинг telegram_id → display_name.

    Returns:
        Словарь: dates, date_labels, employees, matrix.
    """
    date_employees: dict[str, set[str]] = {}
    for row in shifts_rows:
        date_val = str(row["date"] or "").strip()
        if not date_val:
            continue
        employee_id_raw = None
        try:
            employee_id_raw = row["employee_id"]
        except (IndexError, KeyError):
            pass
        employee_name: str
        if employee_id_raw is not None:
            try:
                display = name_map.get(int(employee_id_raw))
                if display:
                    employee_name = display
                else:
                    employee_name = str(row["opened_by"] or row["employee"] or "—").strip()
            except (TypeError, ValueError):
                employee_name = str(row["opened_by"] or row["employee"] or "—").strip()
        else:
            employee_name = str(row["opened_by"] or row["employee"] or "—").strip()

        date_employees.setdefault(date_val, set()).add(employee_name)

    sorted_dates = sorted(date_employees.keys())
    date_labels: list[str] = []
    for date_val in sorted_dates:
        try:
            dt = datetime.strptime(date_val, "%Y-%m-%d")
            wd = _RU_WEEKDAYS[dt.weekday()]
            date_labels.append(f"{dt.strftime('%d.%m')} {wd}")
        except ValueError:
            date_labels.append(date_val)

    all_employees = sorted(
        {emp for employees in date_employees.values() for emp in employees}
    )

    matrix: dict[str, dict[str, bool]] = {}
    for emp in all_employees:
        matrix[emp] = {date_val: emp in date_employees[date_val] for date_val in sorted_dates}

    return {
        "dates": sorted_dates,
        "date_labels": date_labels,
        "employees": all_employees,
        "matrix": matrix,
    }


def _minutes_from_midnight(dt: datetime | None) -> int | None:
    """Конвертирует datetime в минуты от полуночи.

    Args:
        dt: Дата-время.

    Returns:
        Минуты от полуночи или None.
    """
    if dt is None:
        return None
    return dt.hour * 60 + dt.minute


def _clamp(value: int | None, lo: int, hi: int) -> int | None:
    """Ограничивает значение диапазоном.

    Args:
        value: Значение.
        lo: Нижняя граница.
        hi: Верхняя граница.

    Returns:
        Зажатое значение или None.
    """
    if value is None:
        return None
    return max(lo, min(hi, value))


def _fmt_tooltip_phase(
    start_dt: datetime | None,
    end_dt: datetime | None,
) -> str:
    """Форматирует подсказку фазы смены.

    Args:
        start_dt: Начало фазы.
        end_dt: Конец фазы.

    Returns:
        Строка вида «HH:MM — HH:MM (X ч Y мин)».
    """
    if start_dt is None or end_dt is None:
        return ""
    mins = _duration_minutes(start_dt, end_dt)
    return f"{start_dt.strftime('%H:%M')} — {end_dt.strftime('%H:%M')} ({format_duration(mins)})"


def _build_gantt_chart_data(
    shifts: list[dict[str, object]],
) -> dict[str, object]:
    """Строит данные для Gantt-диаграммы смен.

    Args:
        shifts: Список моделей смен (уже построенных).

    Returns:
        Словарь с данными для Chart.js floating bars.
    """
    GANTT_MIN = 480   # 08:00
    GANTT_MAX = 1470  # 24:30

    labels: list[str] = []
    opening_blocks: list[list[int] | None] = []
    operation_blocks: list[list[int] | None] = []
    closing_blocks: list[list[int] | None] = []
    tooltips: list[dict[str, str]] = []

    # Идём от старых к новым для хронологии
    for shift in reversed(shifts):
        shift_id = shift["id"]
        date_raw = str(shift.get("date_raw") or "")
        employee = str(shift.get("employee") or shift.get("employee_raw") or "")
        try:
            dt_date = datetime.strptime(date_raw, "%Y-%m-%d")
            date_label = [dt_date.strftime("%d.%m"), employee] if employee else dt_date.strftime("%d.%m")
        except ValueError:
            date_label = f"#{shift_id}"

        open_started_dt = _parse_iso_datetime(str(shift.get("open_time_raw") or ""))
        opened_dt = _parse_iso_datetime(str(shift.get("opened_at_raw") or ""))
        close_started_dt = _parse_iso_datetime(str(shift.get("close_started_raw") or ""))
        closed_dt = _parse_iso_datetime(str(shift.get("closed_at_raw") or ""))

        def block(start: datetime | None, end: datetime | None) -> list[int] | None:
            s = _clamp(_minutes_from_midnight(start), GANTT_MIN, GANTT_MAX)
            e = _clamp(_minutes_from_midnight(end), GANTT_MIN, GANTT_MAX)
            if s is None or e is None or e <= s:
                return None
            return [s, e]

        labels.append(date_label)
        opening_blocks.append(block(open_started_dt, opened_dt))
        operation_blocks.append(block(opened_dt, close_started_dt))
        closing_blocks.append(block(close_started_dt, closed_dt))
        tooltips.append(
            {
                "opening": _fmt_tooltip_phase(open_started_dt, opened_dt),
                "operation": _fmt_tooltip_phase(opened_dt, close_started_dt),
                "closing": _fmt_tooltip_phase(close_started_dt, closed_dt),
            }
        )

    return {
        "labels": labels,
        "opening_blocks": opening_blocks,
        "operation_blocks": operation_blocks,
        "closing_blocks": closing_blocks,
        "tooltips": tooltips,
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
    for row in residual_analytics:
        if row["current_value"] is None:
            continue
        residual_labels.append(str(row["item_label"]))
        residual_current.append(float(row["current_value"] or 0))

    return {
        "residual_bar": {
            "labels": residual_labels,
            "current_values": residual_current,
        },
        "gantt": _build_gantt_chart_data(shifts),
    }


def get_active_shift(conn: sqlite3.Connection) -> dict | None:
    """Возвращает текущую открытую смену (если есть).

    Returns:
        Словарь с id, employee, open_time или None.
    """
    row = conn.execute(
        "SELECT id, employee, open_time FROM shifts WHERE status = 'OPEN' ORDER BY open_time DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "employee": str(row["employee"] or ""),
        "open_time": _fmt_time(str(row["open_time"] or "")),
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
    name_map = _load_display_name_map(conn)
    shifts_rows = _fetch_shifts(conn, filters)
    shift_ids = [int(row["id"]) for row in shifts_rows]

    checklist_state = _fetch_checklists_for_shifts(conn, shift_ids)
    residual_rows = _fetch_residuals_for_shifts(conn, shift_ids)
    close_media_by_shift = _fetch_close_media_for_shifts(conn, shift_ids)
    open_media_by_shift = _fetch_open_media_for_shifts(conn, shift_ids)

    shifts = _build_shift_models(
        shifts_rows=shifts_rows,
        checklist_state=checklist_state,
        close_media_by_shift=close_media_by_shift,
        open_media_by_shift=open_media_by_shift,
        name_map=name_map,
    )
    residual_analytics = _build_residual_analytics(residual_rows, conn)
    employee_analytics = _build_employee_analytics(shifts)
    schedule_matrix = _build_work_schedule_matrix(shifts_rows, name_map)
    charts = _build_charts(residual_analytics, shifts)

    from_date = filters.date_from
    to_date = filters.date_to
    if from_date and to_date:
        if from_date == to_date:
            period_label = _fmt_date(from_date)
        else:
            period_label = f"{_fmt_date(from_date)} — {_fmt_date(to_date)}"
    elif from_date:
        period_label = f"с {_fmt_date(from_date)}"
    elif to_date:
        period_label = f"по {_fmt_date(to_date)}"
    else:
        period_label = "Все смены"

    return {
        "schedule_matrix": schedule_matrix,
        "shifts": shifts,
        "residuals": residual_analytics,
        "employees": employee_analytics,
        "charts": charts,
        "filters": {
            "date_from": filters.date_from or "",
            "date_to": filters.date_to or "",
        },
        "period_label": period_label,
        "subtitle": f"Данные за: {period_label}",
    }
