"""Тесты загрузки настроек."""
from __future__ import annotations

from pathlib import Path

from app.config import load_settings


def test_load_settings_minimal(tmp_path: Path, monkeypatch) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "BOT_TOKEN=token\n"
        "OWNER_ID=1\n",
        encoding="utf-8",
    )
    # load_dotenv использует override=False, поэтому очистим переменные на всякий случай.
    for key in (
        "WORK_CHAT_ID",
        "WORK_CHAT_THREAD_ID",
        "FORCE_CLOSE_OPEN_SHIFTS_ON_START",
        "BOT_PROXY_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = load_settings(env_file=env)
    assert settings.owner_id == 1
    assert settings.work_chat_id == 1  # дефолт = owner_id
    assert settings.force_close_open_shifts_on_start is False


def test_force_close_flag_parsed(tmp_path: Path, monkeypatch) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "BOT_TOKEN=token\n"
        "OWNER_ID=1\n"
        "FORCE_CLOSE_OPEN_SHIFTS_ON_START=true\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("FORCE_CLOSE_OPEN_SHIFTS_ON_START", raising=False)
    settings = load_settings(env_file=env)
    assert settings.force_close_open_shifts_on_start is True


def test_force_close_flag_off_by_various_inputs(tmp_path: Path, monkeypatch) -> None:
    for raw in ("false", "0", "no", "", "off"):
        env = tmp_path / ".env"
        env.write_text(
            "BOT_TOKEN=token\n"
            "OWNER_ID=1\n"
            f"FORCE_CLOSE_OPEN_SHIFTS_ON_START={raw}\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("FORCE_CLOSE_OPEN_SHIFTS_ON_START", raising=False)
        settings = load_settings(env_file=env)
        assert settings.force_close_open_shifts_on_start is False, f"raw={raw!r}"
