#!/usr/bin/env python3
"""Синхронизация данных с камер Xiaomi в основную SQLite БД.

Запуск:
    DB_PATH=../data/shifts.db venv/bin/python sync_to_db.py

Переменные окружения:
    DB_PATH — путь к SQLite файлу (дефолт ../data/shifts.db)
    XIAOMI_USERNAME, XIAOMI_PASSWORD, XIAOMI_REGION — из .env
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

sys.path.insert(0, str(Path(__file__).parent))
from xiaomi_cloud import XiaomiCloudConnector  # noqa: E402

USERNAME = os.getenv("XIAOMI_USERNAME", "")
PASSWORD = os.getenv("XIAOMI_PASSWORD", "")
REGION = os.getenv("XIAOMI_REGION", "ru")
DB_PATH = Path(os.getenv("DB_PATH", str(Path(__file__).parent.parent / "data" / "shifts.db")))


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS camera_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            did TEXT UNIQUE NOT NULL,
            name TEXT,
            model TEXT,
            localip TEXT,
            is_online INTEGER DEFAULT 0,
            firmware TEXT,
            last_seen TEXT,
            synced_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS camera_videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_did TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT,
            duration_seconds INTEGER,
            video_url TEXT,
            thumbnail_url TEXT,
            event_type TEXT,
            synced_at TEXT NOT NULL,
            UNIQUE(device_did, start_time)
        );
        CREATE INDEX IF NOT EXISTS idx_camera_videos_device
            ON camera_videos(device_did);
        """
    )
    conn.commit()


def _upsert_device(conn: sqlite3.Connection, device: dict, synced_at: str) -> None:
    conn.execute(
        """
        INSERT INTO camera_devices (did, name, model, localip, is_online, firmware, last_seen, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(did) DO UPDATE SET
            name = excluded.name,
            model = excluded.model,
            localip = excluded.localip,
            is_online = excluded.is_online,
            firmware = excluded.firmware,
            last_seen = excluded.last_seen,
            synced_at = excluded.synced_at
        """,
        (
            device.get("did", ""),
            device.get("name"),
            device.get("model"),
            device.get("localip"),
            1 if device.get("isOnline") else 0,
            device.get("extra", {}).get("fw_version") if isinstance(device.get("extra"), dict) else None,
            synced_at,
            synced_at,
        ),
    )


def _is_camera(device: dict) -> bool:
    model = device.get("model", "").lower()
    return any(k in model for k in (
        "camera", "cam", "c200", "chuangmi", "isa.cam", "miot.cam", "xiaomi.cam"
    ))


def _save_videos(conn: sqlite3.Connection, did: str, videos: list[dict], synced_at: str) -> int:
    """Вставляет новые видеозаписи, пропускает дубликаты. Возвращает кол-во вставленных."""
    inserted = 0
    for v in videos:
        # Формат ответа может отличаться по модели — обрабатываем оба варианта
        # Вариант 1: {begin_time, end_time, duration, url, thumbnail}
        # Вариант 2: {start, end, type, ...}
        start = v.get("begin_time") or v.get("start")
        end = v.get("end_time") or v.get("end")
        if not start:
            continue
        start_iso = datetime.fromtimestamp(int(start), tz=timezone.utc).isoformat()
        end_iso = datetime.fromtimestamp(int(end), tz=timezone.utc).isoformat() if end else None
        duration = v.get("duration") or (int(end) - int(start) if end else None)

        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO camera_videos
                (device_did, start_time, end_time, duration_seconds, video_url, thumbnail_url, event_type, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                did,
                start_iso,
                end_iso,
                int(duration) if duration else None,
                v.get("url") or v.get("video_url"),
                v.get("thumbnail") or v.get("preview_url"),
                v.get("type") or v.get("event_type"),
                synced_at,
            ),
        )
        if cursor.rowcount:
            inserted += 1
    return inserted


def _collect_devices(connector: XiaomiCloudConnector) -> list[dict]:
    """Собирает устройства из всех домов (включая shared) + fallback."""
    all_devices: list[dict] = []
    seen_dids: set[str] = set()

    homes_resp = connector.get_homes()
    if not homes_resp:
        return connector.get_all_devices()

    result = homes_resp.get("result", {})
    all_home_lists = (result.get("homelist") or []) + (result.get("share_home_list") or [])

    for home in all_home_lists:
        hid = home.get("id")
        uid = home.get("uid") or connector.user_id
        devs_resp = connector.get_devices(hid, uid)
        if devs_resp:
            dev_result = devs_resp.get("result") or {}
            devlist = dev_result.get("device_info") or dev_result.get("device_list") or []
            for d in devlist:
                did = d.get("did", "")
                if did and did not in seen_dids:
                    seen_dids.add(did)
                    all_devices.append(d)

    if not all_devices:
        all_devices = connector.get_all_devices()

    return all_devices


def main() -> None:
    if not USERNAME or not PASSWORD:
        print("ERROR: XIAOMI_USERNAME / XIAOMI_PASSWORD не заданы в camera_sync/.env", file=sys.stderr)
        sys.exit(1)

    if not DB_PATH.parent.exists():
        print(f"ERROR: директория БД не существует: {DB_PATH.parent}", file=sys.stderr)
        sys.exit(1)

    connector = XiaomiCloudConnector(USERNAME, PASSWORD, REGION)
    if not connector.login():
        print("ERROR: Xiaomi login не удался", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    try:
        _ensure_tables(conn)

        synced_at = datetime.now(timezone.utc).isoformat()
        devices = _collect_devices(connector)

        if not devices:
            print("Устройства не найдены. Проверь аутентификацию (запусти explore2.py).")
            return

        cameras = [d for d in devices if _is_camera(d)]
        total_videos = 0

        for device in devices:
            _upsert_device(conn, device, synced_at)

        conn.commit()

        # Запрашиваем видеозаписи для камер (последние 25 часов)
        now_ts = int(time.time())
        start_ts = now_ts - 25 * 3600

        for cam in cameras:
            did = cam.get("did", "")
            if not did:
                continue
            resp = connector.get_video_list(did, start_ts, now_ts)
            if resp:
                out = resp.get("result", {}).get("out", [])
                # out может быть списком напрямую или содержать ключ "list"
                if isinstance(out, list):
                    videos = out
                elif isinstance(out, dict):
                    videos = out.get("list", [])
                else:
                    videos = []
                count = _save_videos(conn, did, videos, synced_at)
                total_videos += count
                conn.commit()

        print(
            f"Синхронизировано: {len(devices)} устройств "
            f"({len(cameras)} камер), {total_videos} новых видео"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
