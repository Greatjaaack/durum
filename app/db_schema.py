from __future__ import annotations

import sqlite3


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

    columns = _table_columns(conn, "shifts")
    if "status" not in columns:
        conn.execute("ALTER TABLE shifts ADD COLUMN status TEXT NOT NULL DEFAULT 'OPEN'")
    conn.execute(
        """
        UPDATE shifts
        SET status = CASE WHEN close_time IS NULL THEN 'OPEN' ELSE 'CLOSED' END
        """
    )
    conn.commit()


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
            unit_type = 'portion',
            input_value = quantity,
            normalized_quantity = quantity,
            normalized_unit = 'порц'
        WHERE unit_type = 'legacy' AND item_key = 'soup'
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shifts_status ON shifts(status);")
    conn.commit()
