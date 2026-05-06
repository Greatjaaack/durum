"""Тесты миграций схемы и принудительного закрытия смен.

Используют временный SQLite-файл; реальная prod-БД не затрагивается.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from app.db import Database
from app.db_schema import drop_camera_tables, force_close_all_open_shifts


@pytest.fixture()
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "shifts.db"


def test_init_creates_core_tables(tmp_db_path: Path) -> None:
    db = Database(tmp_db_path)
    asyncio.run(db.init(today="2026-05-06"))
    asyncio.run(db.close())

    conn = sqlite3.connect(tmp_db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    names = {r[0] for r in rows}
    for required in {
        "shifts",
        "checklist_state",
        "close_residuals",
        "close_checklist_media",
        "open_checklist_media",
        "stock",
    }:
        assert required in names, f"таблица {required} не создана"


def test_force_close_all_open_shifts_closes_open(tmp_db_path: Path) -> None:
    db = Database(tmp_db_path)
    asyncio.run(db.init(today="2026-05-06"))

    conn = sqlite3.connect(tmp_db_path)
    try:
        conn.execute(
            """
            INSERT INTO shifts (date, employee, employee_id, open_time, close_time, status)
            VALUES ('2026-05-06', '@emp', 1, '11:00', NULL, 'OPEN')
            """
        )
        conn.execute(
            """
            INSERT INTO shifts (date, employee, employee_id, open_time, close_time, status)
            VALUES ('2026-05-05', '@emp', 1, '11:00', '22:00', 'CLOSED')
            """
        )
        conn.commit()
    finally:
        conn.close()

    affected = asyncio.run(db.force_close_all_open_shifts())
    asyncio.run(db.close())
    assert affected == 1

    conn = sqlite3.connect(tmp_db_path)
    try:
        statuses = sorted(r[0] for r in conn.execute("SELECT status FROM shifts"))
    finally:
        conn.close()
    assert statuses == ["CLOSED", "CLOSED"]


def test_force_close_idempotent(tmp_db_path: Path) -> None:
    """Повторный вызов на уже закрытых сменах ничего не трогает."""
    db = Database(tmp_db_path)
    asyncio.run(db.init(today="2026-05-06"))
    first = asyncio.run(db.force_close_all_open_shifts())
    second = asyncio.run(db.force_close_all_open_shifts())
    asyncio.run(db.close())
    assert first == 0
    assert second == 0


def test_force_close_handles_missing_shifts_table(tmp_db_path: Path) -> None:
    """Если таблицы shifts ещё нет — функция не падает, а возвращает 0."""
    conn = sqlite3.connect(tmp_db_path)
    try:
        affected = force_close_all_open_shifts(conn)
    finally:
        conn.close()
    assert affected == 0


def test_drop_camera_tables_removes_legacy_artifacts(tmp_db_path: Path) -> None:
    """drop_camera_tables удаляет camera_devices/camera_videos и идемпотентна."""
    conn = sqlite3.connect(tmp_db_path)
    try:
        conn.execute("CREATE TABLE camera_devices (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE camera_videos (id INTEGER PRIMARY KEY)")
        conn.commit()
        first = drop_camera_tables(conn)
        # Проверяем, что таблиц больше нет.
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'camera%'"
        ).fetchall()
        assert rows == []
        # Идемпотентность — повторный вызов ничего не дропает.
        second = drop_camera_tables(conn)
    finally:
        conn.close()
    assert first == 2
    assert second == 0


def test_drop_camera_tables_runs_on_db_init(tmp_db_path: Path) -> None:
    """db.init автоматически выпиливает camera_* (наследие старой версии)."""
    # Имитируем БД старой версии: создадим camera-таблицы.
    conn = sqlite3.connect(tmp_db_path)
    try:
        conn.execute("CREATE TABLE camera_devices (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE camera_videos (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

    db = Database(tmp_db_path)
    asyncio.run(db.init(today="2026-05-06"))
    asyncio.run(db.close())

    conn = sqlite3.connect(tmp_db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'camera%'"
        ).fetchall()
    finally:
        conn.close()
    assert rows == []
