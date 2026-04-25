from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


# Путь до SQLite-файла по умолчанию.
DEFAULT_DB_PATH = "data/shifts.db"

# Путь до директории с файлами логов по умолчанию.
DEFAULT_LOG_DIR = "logs"

# Таймзона приложения по умолчанию.
DEFAULT_TIMEZONE = "Europe/Moscow"

# Время начала смены по умолчанию.
DEFAULT_SHIFT_OPEN_TIME = "11:00"

# Время окончания смены по умолчанию.
DEFAULT_SHIFT_CLOSE_TIME = "22:00"


@dataclass(slots=True, frozen=True)
class Settings:
    """Структура настроек приложения."""

    bot_token: str
    owner_id: int
    work_chat_id: int
    work_chat_thread_id: int | None
    db_path: Path
    log_dir: Path
    timezone: str
    shift_open_time: time
    shift_close_time: time
    bot_proxy_url: str | None


def _parse_time_hhmm(raw: str, *, var_name: str, default: str) -> time:
    """Преобразует строку формата HH:MM в объект времени.

    Args:
        raw: Входное строковое значение.
        var_name: Имя переменной окружения для текста ошибки.
        default: Значение по умолчанию, если входная строка пустая.

    Returns:
        Объект времени.
    """
    text = raw.strip() or default
    try:
        return datetime.strptime(text, "%H:%M").time()
    except ValueError as exc:
        raise RuntimeError(f"{var_name} must be in HH:MM format") from exc


def load_settings(env_file: str | Path = ".env") -> Settings:
    """Загружает настройки приложения из .env.

    Args:
        env_file: Путь к файлу окружения.

    Returns:
        Экземпляр настроек приложения.
    """
    load_dotenv(dotenv_path=env_file, override=False)

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    owner_id_raw = os.getenv("OWNER_ID", "").strip()
    work_chat_id_raw = os.getenv("WORK_CHAT_ID", "").strip()

    if not bot_token:
        raise RuntimeError("BOT_TOKEN is not set in .env")
    if not owner_id_raw:
        raise RuntimeError("OWNER_ID is not set in .env")

    try:
        owner_id = int(owner_id_raw)
    except ValueError as exc:
        raise RuntimeError("OWNER_ID must be an integer Telegram user id") from exc

    if not work_chat_id_raw:
        work_chat_id = owner_id
    else:
        try:
            work_chat_id = int(work_chat_id_raw)
        except ValueError as exc:
            raise RuntimeError("WORK_CHAT_ID must be an integer Telegram chat id") from exc

    work_chat_thread_id_raw = os.getenv("WORK_CHAT_THREAD_ID", "").strip()
    if not work_chat_thread_id_raw:
        work_chat_thread_id: int | None = None
    else:
        try:
            work_chat_thread_id = int(work_chat_thread_id_raw)
        except ValueError as exc:
            raise RuntimeError("WORK_CHAT_THREAD_ID must be an integer topic id") from exc

    db_path = Path(os.getenv("DB_PATH", DEFAULT_DB_PATH)).expanduser()
    log_dir = Path(os.getenv("LOG_DIR", DEFAULT_LOG_DIR)).expanduser()
    timezone = os.getenv("BOT_TIMEZONE", DEFAULT_TIMEZONE)
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, KeyError):
        raise RuntimeError(f"BOT_TIMEZONE '{timezone}' is not a valid timezone") from None
    bot_proxy_url = os.getenv("BOT_PROXY_URL", "").strip() or None
    shift_open_time = _parse_time_hhmm(
        os.getenv("SHIFT_OPEN_TIME", DEFAULT_SHIFT_OPEN_TIME),
        var_name="SHIFT_OPEN_TIME",
        default=DEFAULT_SHIFT_OPEN_TIME,
    )
    shift_close_time = _parse_time_hhmm(
        os.getenv("SHIFT_CLOSE_TIME", DEFAULT_SHIFT_CLOSE_TIME),
        var_name="SHIFT_CLOSE_TIME",
        default=DEFAULT_SHIFT_CLOSE_TIME,
    )

    return Settings(
        bot_token=bot_token,
        owner_id=owner_id,
        work_chat_id=work_chat_id,
        work_chat_thread_id=work_chat_thread_id,
        db_path=db_path,
        log_dir=log_dir,
        timezone=timezone,
        shift_open_time=shift_open_time,
        shift_close_time=shift_close_time,
        bot_proxy_url=bot_proxy_url,
    )
