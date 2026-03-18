from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

from dotenv import load_dotenv


# Значение модели OpenRouter по умолчанию.
DEFAULT_AI_MODEL = "openrouter/free"

# Ограничение длины входного AI-текста по умолчанию.
DEFAULT_AI_MAX_INPUT_CHARS = 1000

# Таймаут AI-запроса в секундах по умолчанию.
DEFAULT_AI_REQUEST_TIMEOUT_SEC = 45

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
    openrouter_api_key: str
    ai_model: str
    ai_max_input_chars: int
    ai_request_timeout_sec: int
    db_path: Path
    log_dir: Path
    timezone: str
    shift_open_time: time
    shift_close_time: time


def _parse_positive_int(raw: str, *, var_name: str, default: int) -> int:
    """Преобразует строку в положительное целое число.

    Args:
        raw: Входное строковое значение.
        var_name: Имя переменной окружения для текста ошибки.
        default: Значение по умолчанию, если входная строка пустая.

    Returns:
        Положительное целое число.
    """
    text = raw.strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError as exc:
        raise RuntimeError(f"{var_name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"{var_name} must be > 0")
    return value


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
    openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    ai_model = os.getenv("AI_MODEL", DEFAULT_AI_MODEL).strip()

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

    ai_max_input_chars = _parse_positive_int(
        os.getenv("AI_MAX_INPUT_CHARS", str(DEFAULT_AI_MAX_INPUT_CHARS)),
        var_name="AI_MAX_INPUT_CHARS",
        default=DEFAULT_AI_MAX_INPUT_CHARS,
    )
    ai_request_timeout_sec = _parse_positive_int(
        os.getenv("AI_REQUEST_TIMEOUT_SEC", str(DEFAULT_AI_REQUEST_TIMEOUT_SEC)),
        var_name="AI_REQUEST_TIMEOUT_SEC",
        default=DEFAULT_AI_REQUEST_TIMEOUT_SEC,
    )

    db_path = Path(os.getenv("DB_PATH", DEFAULT_DB_PATH)).expanduser()
    log_dir = Path(os.getenv("LOG_DIR", DEFAULT_LOG_DIR)).expanduser()
    timezone = os.getenv("BOT_TIMEZONE", DEFAULT_TIMEZONE)
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
        openrouter_api_key=openrouter_api_key,
        ai_model=ai_model,
        ai_max_input_chars=ai_max_input_chars,
        ai_request_timeout_sec=ai_request_timeout_sec,
        db_path=db_path,
        log_dir=log_dir,
        timezone=timezone,
        shift_open_time=shift_open_time,
        shift_close_time=shift_close_time,
    )
