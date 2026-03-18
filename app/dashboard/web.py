from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.dashboard.service import DashboardFilters, build_dashboard_payload


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
DASHBOARD_ROUTE = "/dashboard"

app = FastAPI(title="Durum Dashboard")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _table_exists(
    conn: sqlite3.Connection,
    table_name: str,
) -> bool:
    """Проверяет существование таблицы в SQLite.

    Args:
        conn: Подключение SQLite.
        table_name: Имя таблицы.

    Returns:
        True, если таблица существует.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _ensure_shift_columns(
    conn: sqlite3.Connection,
) -> None:
    """Досоздаёт колонки shifts, если база старая.

    Args:
        conn: Подключение SQLite.

    Returns:
        None.
    """
    if not _table_exists(conn, "shifts"):
        return

    rows = conn.execute("PRAGMA table_info(shifts)").fetchall()
    columns = {str(row["name"]) for row in rows}
    modified = False

    required_columns = (
        ("status", "ALTER TABLE shifts ADD COLUMN status TEXT NOT NULL DEFAULT 'OPEN'"),
        ("opened_at", "ALTER TABLE shifts ADD COLUMN opened_at TEXT"),
        ("closed_at", "ALTER TABLE shifts ADD COLUMN closed_at TEXT"),
        ("opened_by", "ALTER TABLE shifts ADD COLUMN opened_by TEXT"),
        ("close_started_at", "ALTER TABLE shifts ADD COLUMN close_started_at TEXT"),
        ("closed_by_id", "ALTER TABLE shifts ADD COLUMN closed_by_id INTEGER"),
        ("closed_by_name", "ALTER TABLE shifts ADD COLUMN closed_by_name TEXT"),
        ("close_duration_sec", "ALTER TABLE shifts ADD COLUMN close_duration_sec INTEGER"),
    )

    for column_name, ddl in required_columns:
        if column_name not in columns:
            conn.execute(ddl)
            modified = True

    if not modified:
        return

    conn.execute(
        """
        UPDATE shifts
        SET opened_at = COALESCE(NULLIF(opened_at, ''), open_time)
        WHERE opened_at IS NULL OR opened_at = ''
        """
    )
    conn.execute(
        """
        UPDATE shifts
        SET closed_at = COALESCE(NULLIF(closed_at, ''), close_time)
        WHERE close_time IS NOT NULL AND (closed_at IS NULL OR closed_at = '')
        """
    )
    conn.execute(
        """
        UPDATE shifts
        SET opened_by = COALESCE(NULLIF(opened_by, ''), employee)
        WHERE opened_by IS NULL OR opened_by = ''
        """
    )
    conn.execute(
        """
        UPDATE shifts
        SET closed_by_name = COALESCE(NULLIF(closed_by_name, ''), employee)
        WHERE close_time IS NOT NULL AND (closed_by_name IS NULL OR closed_by_name = '')
        """
    )
    conn.execute(
        """
        UPDATE shifts
        SET close_started_at = COALESCE(NULLIF(close_started_at, ''), close_time, open_time)
        WHERE close_time IS NOT NULL AND (close_started_at IS NULL OR close_started_at = '')
        """
    )
    conn.execute(
        """
        UPDATE shifts
        SET status = CASE WHEN close_time IS NULL THEN 'OPEN' ELSE 'CLOSED' END
        """
    )
    conn.commit()


def _ensure_close_residual_columns(
    conn: sqlite3.Connection,
) -> None:
    """Досоздаёт и синхронизирует поля нормализации close_residuals.

    Args:
        conn: Подключение SQLite.

    Returns:
        None.
    """
    if not _table_exists(conn, "close_residuals"):
        return

    rows = conn.execute("PRAGMA table_info(close_residuals)").fetchall()
    columns = {str(row["name"]) for row in rows}

    required_columns = (
        ("input_value", "ALTER TABLE close_residuals ADD COLUMN input_value REAL"),
        ("unit_type", "ALTER TABLE close_residuals ADD COLUMN unit_type TEXT"),
        ("normalized_quantity", "ALTER TABLE close_residuals ADD COLUMN normalized_quantity REAL"),
        ("normalized_unit", "ALTER TABLE close_residuals ADD COLUMN normalized_unit TEXT"),
    )
    for column_name, ddl in required_columns:
        if column_name not in columns:
            conn.execute(ddl)

    conn.execute(
        """
        UPDATE close_residuals
        SET input_value = COALESCE(input_value, quantity)
        WHERE input_value IS NULL
        """
    )
    conn.execute(
        """
        UPDATE close_residuals
        SET normalized_quantity = COALESCE(normalized_quantity, quantity)
        WHERE normalized_quantity IS NULL
        """
    )
    conn.execute(
        """
        UPDATE close_residuals
        SET normalized_unit = COALESCE(NULLIF(normalized_unit, ''), unit)
        WHERE normalized_unit IS NULL OR normalized_unit = ''
        """
    )
    conn.execute(
        """
        UPDATE close_residuals
        SET unit_type = COALESCE(NULLIF(unit_type, ''), 'legacy')
        WHERE unit_type IS NULL OR unit_type = ''
        """
    )
    conn.execute(
        """
        UPDATE close_residuals
        SET
            unit_type = 'weight_g',
            input_value = CASE
                WHEN LOWER(COALESCE(unit, '')) IN ('кг', 'kg') THEN quantity * 1000.0
                ELSE quantity
            END,
            normalized_quantity = CASE
                WHEN LOWER(COALESCE(unit, '')) IN ('кг', 'kg') THEN quantity * 1000.0
                ELSE quantity
            END,
            normalized_unit = 'г'
        WHERE unit_type = 'legacy' AND item_key IN ('marinated_chicken', 'fried_chicken')
        """
    )
    conn.execute(
        """
        UPDATE close_residuals
        SET
            unit_type = 'piece',
            input_value = quantity,
            normalized_quantity = quantity,
            normalized_unit = 'шт'
        WHERE unit_type = 'legacy' AND item_key = 'lavash'
        """
    )
    conn.execute(
        """
        UPDATE close_residuals
        SET
            unit_type = 'portion',
            input_value = quantity,
            normalized_quantity = quantity,
            normalized_unit = 'порц'
        WHERE unit_type = 'legacy' AND item_key = 'soup'
        """
    )
    conn.execute(
        """
        UPDATE close_residuals
        SET
            unit_type = CASE
                WHEN LOWER(COALESCE(unit, '')) IN ('мл', 'ml') THEN 'legacy_ml'
                ELSE 'gastro_unit'
            END,
            input_value = quantity,
            normalized_quantity = quantity,
            normalized_unit = COALESCE(NULLIF(unit, ''), 'гастроёмк')
        WHERE unit_type = 'legacy' AND item_key = 'sauce'
        """
    )
    conn.commit()


def _dashboard_db_path() -> Path:
    """Возвращает путь к SQLite базе дашборда.

    Args:
        Нет параметров.

    Returns:
        Путь к файлу базы данных.
    """
    return Path(os.getenv("DB_PATH", "data/shifts.db")).expanduser()


def _connect() -> sqlite3.Connection:
    """Создаёт подключение к SQLite.

    Args:
        Нет параметров.

    Returns:
        Подключение SQLite.
    """
    conn = sqlite3.connect(_dashboard_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _normalize_date(
    raw_date: str | None,
) -> str | None:
    """Проверяет формат даты YYYY-MM-DD.

    Args:
        raw_date: Сырой параметр даты.

    Returns:
        Валидная дата или None.
    """
    if not raw_date:
        return None
    value = raw_date.strip()
    if not value:
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return value
    except ValueError:
        return None


@app.get("/")
def root_redirect() -> RedirectResponse:
    """Перенаправляет корневой URL на дашборд.

    Args:
        Нет параметров.

    Returns:
        HTTP-редирект.
    """
    return RedirectResponse(url=DASHBOARD_ROUTE, status_code=307)


@app.head("/")
def root_redirect_head() -> RedirectResponse:
    """Перенаправляет HEAD-запрос с / на /dashboard.

    Args:
        Нет параметров.

    Returns:
        HTTP-редирект.
    """
    return RedirectResponse(url=DASHBOARD_ROUTE, status_code=307)


@app.get("/favicon.ico")
@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
def icon_placeholder() -> Response:
    """Возвращает пустой ответ для иконок.

    Args:
        Нет параметров.

    Returns:
        HTTP 204.
    """
    return Response(status_code=204)


@app.head("/favicon.ico")
@app.head("/apple-touch-icon.png")
@app.head("/apple-touch-icon-precomposed.png")
def icon_placeholder_head() -> Response:
    """Возвращает пустой ответ на HEAD-запросы иконок.

    Args:
        Нет параметров.

    Returns:
        HTTP 204.
    """
    return Response(status_code=204)


@app.get(DASHBOARD_ROUTE)
def dashboard(
    request: Request,
    date: str | None = Query(default=None, description="Дата в формате YYYY-MM-DD"),
):
    """Рендерит операционный dashboard смен.

    Args:
        request: Объект HTTP-запроса FastAPI.
        date: Фильтр по дате.

    Returns:
        Jinja2 TemplateResponse.
    """
    filters = DashboardFilters(
        date=_normalize_date(date),
    )

    conn = _connect()
    try:
        _ensure_shift_columns(conn)
        _ensure_close_residual_columns(conn)
        payload = build_dashboard_payload(conn, filters)
    finally:
        conn.close()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=payload,
    )
