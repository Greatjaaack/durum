from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)


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


def _table_columns(
    conn: sqlite3.Connection,
    table_name: str,
) -> set[str]:
    """Возвращает множество колонок таблицы.

    Args:
        conn: Подключение SQLite.
        table_name: Имя таблицы.

    Returns:
        Множество имён колонок.
    """
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    columns: set[str] = set()
    for row in rows:
        if isinstance(row, sqlite3.Row):
            columns.add(str(row["name"]))
        else:
            columns.add(str(row[1]))
    return columns


def ensure_shift_status_column(
    conn: sqlite3.Connection,
) -> None:
    """Проверяет и синхронизирует колонку статуса смены.

    Args:
        conn: Подключение SQLite.

    Returns:
        None.
    """
    if not _table_exists(conn, "shifts"):
        return

    try:
        columns = _table_columns(conn, "shifts")
        if "status" not in columns:
            conn.execute("ALTER TABLE shifts ADD COLUMN status TEXT NOT NULL DEFAULT 'OPEN'")
        # Исправляем только те записи, где close_time заполнен, но status ошибочно OPEN.
        # Не трогаем смены с close_time IS NULL — они могут быть CLOSED через close_stale_open_shifts.
        conn.execute(
            """
            UPDATE shifts
            SET status = 'CLOSED'
            WHERE close_time IS NOT NULL AND status != 'CLOSED'
            """
        )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Migration ensure_shift_status_column failed")
        raise


def ensure_shift_audit_columns(
    conn: sqlite3.Connection,
) -> None:
    """Проверяет и синхронизирует колонки аудита открытия/закрытия смены.

    Args:
        conn: Подключение SQLite.

    Returns:
        None.
    """
    if not _table_exists(conn, "shifts"):
        return

    try:
        columns = _table_columns(conn, "shifts")
        if "opened_at" not in columns:
            conn.execute("ALTER TABLE shifts ADD COLUMN opened_at TEXT")
        if "closed_at" not in columns:
            conn.execute("ALTER TABLE shifts ADD COLUMN closed_at TEXT")
        if "opened_by" not in columns:
            conn.execute("ALTER TABLE shifts ADD COLUMN opened_by TEXT")
        if "close_started_at" not in columns:
            conn.execute("ALTER TABLE shifts ADD COLUMN close_started_at TEXT")
        if "closed_by_id" not in columns:
            conn.execute("ALTER TABLE shifts ADD COLUMN closed_by_id INTEGER")
        if "closed_by_name" not in columns:
            conn.execute("ALTER TABLE shifts ADD COLUMN closed_by_name TEXT")
        if "close_duration_sec" not in columns:
            conn.execute("ALTER TABLE shifts ADD COLUMN close_duration_sec INTEGER")

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
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Migration ensure_shift_audit_columns failed")
        raise


def ensure_close_residual_columns(
    conn: sqlite3.Connection,
) -> None:
    """Проверяет и синхронизирует поля нормализации остатков закрытия.

    Args:
        conn: Подключение SQLite.

    Returns:
        None.
    """
    if not _table_exists(conn, "close_residuals"):
        return

    try:
        columns = _table_columns(conn, "close_residuals")
        if "input_value" not in columns:
            conn.execute("ALTER TABLE close_residuals ADD COLUMN input_value REAL")
        if "unit_type" not in columns:
            conn.execute("ALTER TABLE close_residuals ADD COLUMN unit_type TEXT")
        if "normalized_quantity" not in columns:
            conn.execute("ALTER TABLE close_residuals ADD COLUMN normalized_quantity REAL")
        if "normalized_unit" not in columns:
            conn.execute("ALTER TABLE close_residuals ADD COLUMN normalized_unit TEXT")

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
                unit_type = 'liter',
                input_value = CASE
                    WHEN LOWER(COALESCE(unit, '')) IN ('мл', 'ml') THEN quantity / 1000.0
                    WHEN LOWER(COALESCE(unit, '')) IN ('г', 'g') THEN quantity / 1000.0
                    WHEN LOWER(COALESCE(unit, '')) IN ('кг', 'kg') THEN quantity
                    ELSE quantity
                END,
                normalized_quantity = CASE
                    WHEN LOWER(COALESCE(unit, '')) IN ('мл', 'ml') THEN quantity / 1000.0
                    WHEN LOWER(COALESCE(unit, '')) IN ('г', 'g') THEN quantity / 1000.0
                    WHEN LOWER(COALESCE(unit, '')) IN ('кг', 'kg') THEN quantity
                    ELSE quantity
                END,
                normalized_unit = 'л',
                unit = 'л'
            WHERE unit_type = 'legacy' AND item_key = 'soup'
            """
        )
        conn.execute(
            """
            UPDATE close_residuals
            SET
                unit_type = 'liter',
                quantity = CASE
                    WHEN LOWER(COALESCE(unit, '')) IN ('г', 'g', 'мл', 'ml') THEN quantity / 1000.0
                    WHEN LOWER(COALESCE(unit, '')) IN ('кг', 'kg') THEN quantity
                    ELSE quantity
                END,
                input_value = CASE
                    WHEN input_value IS NULL THEN NULL
                    WHEN LOWER(COALESCE(unit, '')) IN ('г', 'g', 'мл', 'ml') THEN input_value / 1000.0
                    WHEN LOWER(COALESCE(unit, '')) IN ('кг', 'kg') THEN input_value
                    ELSE input_value
                END,
                normalized_quantity = CASE
                    WHEN normalized_quantity IS NULL THEN NULL
                    WHEN LOWER(COALESCE(unit, '')) IN ('г', 'g', 'мл', 'ml') THEN normalized_quantity / 1000.0
                    WHEN LOWER(COALESCE(unit, '')) IN ('кг', 'kg') THEN normalized_quantity
                    ELSE normalized_quantity
                END,
                normalized_unit = 'л',
                unit = 'л'
            WHERE item_key = 'soup' AND unit_type = 'weight_g'
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
    except Exception:
        conn.rollback()
        logger.exception("Migration ensure_close_residual_columns failed")
        raise


def ensure_shift_status_index(
    conn: sqlite3.Connection,
) -> None:
    """Создаёт индекс по статусу смены, если его нет.

    Args:
        conn: Подключение SQLite.

    Returns:
        None.
    """
    if not _table_exists(conn, "shifts"):
        return
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_shifts_status ON shifts(status);")
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Migration ensure_shift_status_index failed")
        raise


def ensure_last_mid_at_column(
    conn: sqlite3.Connection,
) -> None:
    """Добавляет колонку last_mid_at в таблицу shifts.

    Args:
        conn: Подключение SQLite.

    Returns:
        None.
    """
    if not _table_exists(conn, "shifts"):
        return
    try:
        columns = _table_columns(conn, "shifts")
        if "last_mid_at" not in columns:
            conn.execute("ALTER TABLE shifts ADD COLUMN last_mid_at TEXT")
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Migration ensure_last_mid_at_column failed")
        raise


def ensure_mid_started_at_column(
    conn: sqlite3.Connection,
) -> None:
    """Добавляет колонку mid_started_at в таблицу shifts.

    Args:
        conn: Подключение SQLite.

    Returns:
        None.
    """
    if not _table_exists(conn, "shifts"):
        return
    try:
        columns = _table_columns(conn, "shifts")
        if "mid_started_at" not in columns:
            conn.execute("ALTER TABLE shifts ADD COLUMN mid_started_at TEXT")
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Migration ensure_mid_started_at_column failed")
        raise


def ensure_open_checklist_media_table(
    conn: sqlite3.Connection,
) -> None:
    """Создаёт таблицу open_checklist_media, если её нет.

    Args:
        conn: Подключение SQLite.

    Returns:
        None.
    """
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS open_checklist_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id INTEGER NOT NULL,
                item_index INTEGER NOT NULL,
                item_label TEXT,
                file_id TEXT,
                file_unique_id TEXT,
                mime_type TEXT,
                created_at TEXT,
                UNIQUE(shift_id, item_index)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_open_checklist_media_shift "
            "ON open_checklist_media(shift_id)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Migration ensure_open_checklist_media_table failed")
        raise


def ensure_mid_checklist_data_table(
    conn: sqlite3.Connection,
) -> None:
    """Создаёт таблицу mid_checklist_data, если её нет.

    Args:
        conn: Подключение SQLite.

    Returns:
        None.
    """
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mid_checklist_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT,
                created_at TEXT,
                UNIQUE(shift_id, key)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mid_checklist_data_shift "
            "ON mid_checklist_data(shift_id)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Migration ensure_mid_checklist_data_table failed")
        raise


def ensure_camera_tables(
    conn: sqlite3.Connection,
) -> None:
    """Создаёт таблицы camera_devices и camera_videos, если их нет.

    Args:
        conn: Подключение SQLite.

    Returns:
        None.
    """
    try:
        conn.execute(
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
            )
            """
        )
        conn.execute(
            """
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
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_camera_videos_device "
            "ON camera_videos(device_did)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Migration ensure_camera_tables failed")
        raise


def ensure_employee_profiles_table(
    conn: sqlite3.Connection,
) -> None:
    """Создаёт таблицу employee_profiles, если её нет.

    Args:
        conn: Подключение SQLite.

    Returns:
        None.
    """
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS employee_profiles (
                telegram_id INTEGER PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Migration ensure_employee_profiles_table failed")
        raise


def ensure_employee_schedule_entries_table(
    conn: sqlite3.Connection,
) -> None:
    """Создаёт таблицу employee_schedule_entries, если её нет.

    Args:
        conn: Подключение SQLite.

    Returns:
        None.
    """
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS employee_schedule_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_telegram_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                shift_type TEXT NOT NULL DEFAULT 'full',
                start_time TEXT,
                end_time TEXT,
                UNIQUE(employee_telegram_id, date)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_schedule_entries_date "
            "ON employee_schedule_entries(date)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Migration ensure_employee_schedule_entries_table failed")
        raise


def close_stale_open_shifts(
    conn: sqlite3.Connection,
    today: str | None = None,
) -> None:
    """Закрывает OPEN-смены, дата которых раньше сегодняшней.

    Такие смены были брошены без завершения. Активная текущая смена
    всегда имеет date = сегодня, поэтому фильтр безопасен.

    Args:
        conn: Подключение SQLite.
        today: Дата в формате YYYY-MM-DD в таймзоне приложения.
               Если не передана — используется UTC-дата SQLite.

    Returns:
        None.
    """
    if not _table_exists(conn, "shifts"):
        return
    try:
        if today:
            conn.execute(
                """
                UPDATE shifts
                SET status = 'CLOSED'
                WHERE status = 'OPEN'
                  AND close_time IS NULL
                  AND date < ?
                """,
                (today,),
            )
        else:
            conn.execute(
                """
                UPDATE shifts
                SET status = 'CLOSED'
                WHERE status = 'OPEN'
                  AND close_time IS NULL
                  AND date < date('now', 'localtime')
                """
            )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Migration close_stale_open_shifts failed")
        raise


def ensure_shift_periodic_residuals_table(
    conn: sqlite3.Connection,
) -> None:
    """Создаёт таблицу shift_periodic_residuals, если её нет.

    Args:
        conn: Подключение SQLite.

    Returns:
        None.
    """
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shift_periodic_residuals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT,
                recorded_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_periodic_residuals_shift "
            "ON shift_periodic_residuals(shift_id)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Migration ensure_shift_periodic_residuals_table failed")
        raise


def ensure_media_local_path_columns(
    conn: sqlite3.Connection,
) -> None:
    """Добавляет колонку local_path в таблицы медиа, если её нет.

    Args:
        conn: Подключение SQLite.

    Returns:
        None.
    """
    _allowed = {"close_checklist_media", "open_checklist_media"}
    for table in _allowed:
        if not _table_exists(conn, table):
            continue
        cols = _table_columns(conn, table)
        if "local_path" in cols:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN local_path TEXT")
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("Migration ensure_media_local_path_columns failed for %s", table)
            raise
