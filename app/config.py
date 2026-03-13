from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(slots=True, frozen=True)
class Settings:
    bot_token: str
    owner_id: int
    db_path: Path
    timezone: str


def load_settings(env_file: str | Path = ".env") -> Settings:
    load_dotenv(dotenv_path=env_file, override=False)

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    owner_id_raw = os.getenv("OWNER_ID", "").strip()

    if not bot_token:
        raise RuntimeError("BOT_TOKEN is not set in .env")
    if not owner_id_raw:
        raise RuntimeError("OWNER_ID is not set in .env")

    try:
        owner_id = int(owner_id_raw)
    except ValueError as exc:
        raise RuntimeError("OWNER_ID must be an integer Telegram user id") from exc

    db_path = Path(os.getenv("DB_PATH", "shifts.db")).expanduser()
    timezone = os.getenv("BOT_TIMEZONE", "Europe/Moscow")

    return Settings(
        bot_token=bot_token,
        owner_id=owner_id,
        db_path=db_path,
        timezone=timezone,
    )
