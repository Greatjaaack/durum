from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as URLRequest
from urllib.request import urlopen

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.dashboard.service import DashboardFilters, build_dashboard_payload
from app.db_schema import (
    close_stale_open_shifts as close_stale_open_shifts_schema,
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


def _dashboard_bot_token() -> str:
    """Возвращает BOT_TOKEN для прокси медиа dashboard.

    Args:
        Нет параметров.

    Returns:
        Значение BOT_TOKEN или пустую строку.
    """
    return os.getenv("BOT_TOKEN", "").strip()


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
        close_stale_open_shifts_schema(conn)
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


def _telegram_file_path(
    bot_token: str,
    file_id: str,
) -> str | None:
    """Получает путь файла в Telegram по file_id.

    Args:
        bot_token: Токен Telegram-бота.
        file_id: Идентификатор файла Telegram.

    Returns:
        Относительный путь файла или None.
    """
    payload = urlencode({"file_id": file_id}).encode("utf-8")
    request = URLRequest(f"https://api.telegram.org/bot{bot_token}/getFile", data=payload)
    with urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    result = data.get("result")
    if not isinstance(result, dict):
        return None
    file_path = str(result.get("file_path") or "").strip()
    return file_path or None


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
    date_from: str | None = Query(default=None, description="Начало периода YYYY-MM-DD"),
    date_to: str | None = Query(default=None, description="Конец периода YYYY-MM-DD"),
):
    """Рендерит операционный dashboard смен.

    Args:
        request: Объект HTTP-запроса FastAPI.
        date_from: Начало диапазона дат.
        date_to: Конец диапазона дат.

    Returns:
        Jinja2 TemplateResponse.
    """
    filters = DashboardFilters(
        date_from=_normalize_date(date_from),
        date_to=_normalize_date(date_to),
    )

    conn = _connect()
    try:
        payload = build_dashboard_payload(conn, filters)
    except Exception:
        logger.exception("Failed to build dashboard payload")
        payload = {
            "kpi": None,
            "shifts": [],
            "residuals": [],
            "employees": [],
            "charts": {},
            "filters": {"date_from": filters.date_from or "", "date_to": filters.date_to or ""},
            "subtitle": "",
            "period_label": "",
            "error": "Не удалось загрузить данные дашборда.",
        }
    finally:
        conn.close()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=payload,
    )


@app.get("/dashboard/media/{media_id}", name="dashboard_media")
def dashboard_media(
    media_id: int,
) -> Response:
    """Проксирует фото чек-листа из Telegram для дашборда.

    Args:
        media_id: Идентификатор фото в таблице close_checklist_media.

    Returns:
        Бинарный ответ изображения.
    """
    bot_token = _dashboard_bot_token()
    if not bot_token:
        raise HTTPException(status_code=503, detail="BOT_TOKEN is not configured")

    conn = _connect()
    try:
        try:
            row = conn.execute(
                """
                SELECT file_id, mime_type
                FROM close_checklist_media
                WHERE id = ?
                LIMIT 1
                """,
                (media_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            raise HTTPException(status_code=404, detail="Photo not found") from None
    finally:
        conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Photo not found")

    file_id = str(row["file_id"] or "").strip()
    if not file_id:
        raise HTTPException(status_code=404, detail="Photo not found")

    try:
        file_path = _telegram_file_path(bot_token, file_id)
        if not file_path:
            raise HTTPException(status_code=404, detail="Photo file is unavailable")

        file_request = URLRequest(f"https://api.telegram.org/file/bot{bot_token}/{file_path}")
        with urlopen(file_request, timeout=25) as file_response:
            content = file_response.read()
            content_type = str(file_response.headers.get("Content-Type") or "").strip()
    except HTTPException:
        raise
    except (HTTPError, URLError, TimeoutError):
        logger.exception("Failed to proxy checklist photo media_id=%s", media_id)
        raise HTTPException(status_code=502, detail="Failed to fetch photo from Telegram") from None
    except Exception:
        logger.exception("Unexpected error while proxying checklist photo media_id=%s", media_id)
        raise HTTPException(status_code=500, detail="Unexpected media proxy error") from None

    media_type = content_type or str(row["mime_type"] or "").strip() or "image/jpeg"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=300"},
    )
