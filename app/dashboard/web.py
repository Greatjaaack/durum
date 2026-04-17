from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as URLRequest
from urllib.request import urlopen

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from app.dashboard.employees_service import (
    delete_schedule_entry,
    fetch_all_employees_with_profiles,
    fetch_schedule_matrix,
    upsert_employee_profile,
    upsert_schedule_entry,
)
from app.dashboard.service import DashboardFilters, build_dashboard_payload, get_active_shift
from app.db_schema import (
    close_stale_open_shifts as close_stale_open_shifts_schema,
    ensure_close_residual_columns as ensure_close_residual_schema_columns,
    ensure_employee_profiles_table as ensure_employee_profiles_schema_table,
    ensure_employee_schedule_entries_table as ensure_employee_schedule_entries_schema_table,
    ensure_last_mid_at_column as ensure_last_mid_at_schema_column,
    ensure_mid_started_at_column as ensure_mid_started_at_schema_column,
    ensure_mid_checklist_data_table as ensure_mid_checklist_data_schema_table,
    ensure_open_checklist_media_table as ensure_open_checklist_media_schema_table,
    ensure_shift_audit_columns as ensure_shift_audit_schema_columns,
    ensure_shift_periodic_residuals_table as ensure_shift_periodic_residuals_schema_table,
    ensure_shift_status_column as ensure_shift_status_schema_column,
    ensure_shift_status_index as ensure_shift_status_schema_index,
)


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
DASHBOARD_ROUTE = "/dashboard"
_SESSION_COOKIE = "ds_session"
_PUBLIC_PATHS = {"/login", "/logout"}
logger = logging.getLogger(__name__)


def _auth_secret() -> str:
    return os.getenv("DASHBOARD_SECRET", "change-me-secret-key")


def _auth_credentials() -> tuple[str, str]:
    username = os.getenv("DASHBOARD_USERNAME", "").strip()
    password = os.getenv("DASHBOARD_PASSWORD", "").strip()
    return username, password


def _auth_enabled() -> bool:
    username, password = _auth_credentials()
    return bool(username and password)


def _make_session_token(username: str) -> str:
    secret = _auth_secret()
    sig = hmac.new(secret.encode(), username.encode(), hashlib.sha256).hexdigest()
    payload = base64.urlsafe_b64encode(username.encode()).decode()
    return f"{payload}.{sig}"


def _verify_session_token(token: str) -> str | None:
    try:
        payload, sig = token.split(".", 1)
        username = base64.urlsafe_b64decode(payload.encode()).decode()
        secret = _auth_secret()
        expected = hmac.new(secret.encode(), username.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(sig, expected):
            return username
    except Exception:
        pass
    return None


def _is_authenticated(request: Request) -> bool:
    if not _auth_enabled():
        return True
    token = request.cookies.get(_SESSION_COOKIE, "")
    return _verify_session_token(token) is not None


class _AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith("/static"):
            return await call_next(request)
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        return await call_next(request)


app = FastAPI(title="Durum Dashboard")
app.add_middleware(_AuthMiddleware)
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
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(os.getenv("BOT_TIMEZONE", "UTC"))
    today = datetime.now(tz).date().isoformat()

    conn = _connect()
    try:
        _ensure_shift_columns(conn)
        _ensure_close_residual_columns(conn)
        close_stale_open_shifts_schema(conn, today=today)
        ensure_last_mid_at_schema_column(conn)
        ensure_mid_started_at_schema_column(conn)
        ensure_open_checklist_media_schema_table(conn)
        ensure_mid_checklist_data_schema_table(conn)
        ensure_employee_profiles_schema_table(conn)
        ensure_employee_schedule_entries_schema_table(conn)
        ensure_shift_periodic_residuals_schema_table(conn)
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


@app.get("/login")
def login_page(request: Request):
    if _is_authenticated(request):
        return RedirectResponse(url=DASHBOARD_ROUTE, status_code=302)
    return templates.TemplateResponse(request=request, name="login.html", context={})


@app.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    expected_user, expected_pass = _auth_credentials()
    ok = (
        hmac.compare_digest(username.strip(), expected_user)
        and hmac.compare_digest(password, expected_pass)
    )
    if not ok:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Неверный логин или пароль"},
            status_code=401,
        )
    token = _make_session_token(username.strip())
    response = RedirectResponse(url=DASHBOARD_ROUTE, status_code=302)
    response.set_cookie(
        key=_SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(key=_SESSION_COOKIE)
    return response


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
    tz = ZoneInfo(os.getenv("BOT_TIMEZONE", "UTC"))
    normalized_from = _normalize_date(date_from)
    normalized_to = _normalize_date(date_to)
    if not normalized_from:
        normalized_from = datetime.now(tz).date().isoformat()

    filters = DashboardFilters(
        date_from=normalized_from,
        date_to=normalized_to,
    )

    conn = _connect()
    try:
        payload = build_dashboard_payload(conn, filters)
        payload["active_shift"] = get_active_shift(conn)
    except Exception:
        logger.exception("Failed to build dashboard payload")
        payload = {
            "schedule_matrix": {"dates": [], "date_labels": [], "employees": [], "matrix": {}},
            "shifts": [],
            "residuals": [],
            "employees": [],
            "charts": {"gantt": {"labels": [], "datasets": []}, "residuals": {"labels": [], "current_values": []}},
            "filters": {"date_from": filters.date_from or "", "date_to": filters.date_to or ""},
            "subtitle": "",
            "period_label": "",
            "error": "Не удалось загрузить данные дашборда.",
            "active_shift": None,
        }
    finally:
        conn.close()

    payload["active_tab"] = "dashboard"
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=payload,
    )


@app.get("/dashboard/employees")
def employees_page(
    request: Request,
    year: int = Query(default=None),
    month: int = Query(default=None),
):
    """Рендерит страницу управления сотрудниками.

    Args:
        request: Объект HTTP-запроса FastAPI.
        year: Год для матрицы расписания.
        month: Месяц для матрицы расписания.

    Returns:
        Jinja2 TemplateResponse.
    """
    now = datetime.now(ZoneInfo(os.getenv("BOT_TIMEZONE", "UTC")))
    current_year = year or now.year
    current_month = month or now.month

    if current_month == 1:
        prev_year, prev_month = current_year - 1, 12
    else:
        prev_year, prev_month = current_year, current_month - 1

    if current_month == 12:
        next_year, next_month = current_year + 1, 1
    else:
        next_year, next_month = current_year, current_month + 1

    conn = _connect()
    try:
        employees = fetch_all_employees_with_profiles(conn)
        schedule = fetch_schedule_matrix(conn, current_year, current_month)
        active_shift = get_active_shift(conn)
    except Exception:
        logger.exception("Failed to build employees page payload")
        employees = []
        schedule = {"employees": [], "days": [], "entries": {}}
        active_shift = None
    finally:
        conn.close()

    month_names = [
        "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
        "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
    ]

    return templates.TemplateResponse(
        request=request,
        name="employees.html",
        context={
            "employees": employees,
            "schedule": schedule,
            "current_year": current_year,
            "current_month": current_month,
            "month_name": month_names[current_month],
            "prev_year": prev_year,
            "prev_month": prev_month,
            "next_year": next_year,
            "next_month": next_month,
            "active_tab": "employees",
            "subtitle": "",
            "active_shift": active_shift,
        },
    )


@app.post("/dashboard/employees/profile")
def update_employee_profile(
    telegram_id: int = Form(...),
    display_name: str = Form(default=""),
    is_active: int = Form(default=1),
):
    """Обновляет профиль сотрудника.

    Args:
        telegram_id: Telegram ID сотрудника.
        display_name: Отображаемое имя.
        is_active: Флаг активности (1/0).

    Returns:
        HTTP-редирект на страницу сотрудников.
    """
    conn = _connect()
    try:
        upsert_employee_profile(conn, telegram_id, display_name.strip(), is_active)
    except Exception:
        logger.exception("Failed to update employee profile telegram_id=%s", telegram_id)
    finally:
        conn.close()
    return RedirectResponse(url="/dashboard/employees", status_code=303)


@app.post("/dashboard/employees/schedule")
def update_employee_schedule(
    employee_telegram_id: int = Form(...),
    date: str = Form(...),
    shift_type: str = Form(default="full"),
    start_time: str = Form(default=""),
    end_time: str = Form(default=""),
    action: str = Form(default="upsert"),
):
    """Назначает или удаляет смену в расписании сотрудника.

    Args:
        employee_telegram_id: Telegram ID сотрудника.
        date: Дата в формате YYYY-MM-DD.
        shift_type: Тип смены (full/half).
        start_time: Время начала (для half).
        end_time: Время окончания (для half).
        action: Действие (upsert/delete).

    Returns:
        HTTP-редирект на страницу сотрудников.
    """
    conn = _connect()
    try:
        if action == "delete":
            delete_schedule_entry(conn, employee_telegram_id, date)
        else:
            upsert_schedule_entry(
                conn,
                employee_telegram_id,
                date,
                shift_type,
                start_time.strip() or None,
                end_time.strip() or None,
            )
    except Exception:
        logger.exception(
            "Failed to update schedule for telegram_id=%s date=%s",
            employee_telegram_id,
            date,
        )
    finally:
        conn.close()
    return RedirectResponse(url="/dashboard/employees", status_code=303)


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
    return _proxy_dashboard_media(media_id=media_id, table_name="close_checklist_media")


@app.get("/dashboard/open-media/{media_id}", name="dashboard_open_media")
def dashboard_open_media(
    media_id: int,
) -> Response:
    """Проксирует фото открытия смены из Telegram для дашборда.

    Args:
        media_id: Идентификатор фото в таблице open_checklist_media.

    Returns:
        Бинарный ответ изображения.
    """
    return _proxy_dashboard_media(media_id=media_id, table_name="open_checklist_media")


def _proxy_dashboard_media(
    media_id: int,
    table_name: str,
) -> Response:
    """Проксирует фото чек-листа из Telegram по таблице-источнику.

    Args:
        media_id: Идентификатор фото.
        table_name: Название таблицы с media-метаданными.

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
                f"""
                SELECT file_id, mime_type
                FROM {table_name}
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
        logger.exception(
            "Failed to proxy checklist photo media_id=%s table=%s",
            media_id,
            table_name,
        )
        raise HTTPException(status_code=502, detail="Failed to fetch photo from Telegram") from None
    except Exception:
        logger.exception(
            "Unexpected error while proxying checklist photo media_id=%s table=%s",
            media_id,
            table_name,
        )
        raise HTTPException(status_code=500, detail="Unexpected media proxy error") from None

    media_type = content_type or str(row["mime_type"] or "").strip() or "image/jpeg"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=300"},
    )
