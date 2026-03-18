from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.dashboard.service import DashboardFilters, build_dashboard_payload
from app.db_schema import (
    ensure_close_residual_columns as ensure_close_residual_schema_columns,
    ensure_shift_audit_columns as ensure_shift_audit_schema_columns,
    ensure_shift_status_column as ensure_shift_status_schema_column,
    ensure_shift_status_index as ensure_shift_status_schema_index,
)


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
DASHBOARD_ROUTE = "/dashboard"
logger = logging.getLogger(__name__)

app = FastAPI(title="Durum Dashboard")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _ensure_shift_columns(
    conn: sqlite3.Connection,
) -> None:
    """Досоздаёт колонки shifts, если база старая.

    Args:
        conn: Подключение SQLite.

    Returns:
        None.
    """
    ensure_shift_status_schema_column(conn)
    ensure_shift_audit_schema_columns(conn)
    ensure_shift_status_schema_index(conn)


def _ensure_close_residual_columns(
    conn: sqlite3.Connection,
) -> None:
    """Досоздаёт и синхронизирует поля нормализации close_residuals.

    Args:
        conn: Подключение SQLite.

    Returns:
        None.
    """
    ensure_close_residual_schema_columns(conn)


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


def _prepare_dashboard_schema() -> None:
    """Применяет схему и миграции дашборда один раз при запуске.

    Args:
        Нет параметров.

    Returns:
        None.
    """
    conn = _connect()
    try:
        _ensure_shift_columns(conn)
        _ensure_close_residual_columns(conn)
    finally:
        conn.close()


@app.on_event("startup")
def dashboard_startup() -> None:
    """Инициализирует схему дашборда перед обработкой запросов.

    Args:
        Нет параметров.

    Returns:
        None.
    """
    try:
        _prepare_dashboard_schema()
    except sqlite3.Error:
        logger.exception("Failed to prepare dashboard schema on startup")


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
        payload = build_dashboard_payload(conn, filters)
    finally:
        conn.close()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=payload,
    )
