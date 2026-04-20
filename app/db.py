from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from app.db_schema import (
    close_stale_open_shifts as close_stale_open_shifts_schema,
    ensure_camera_tables as ensure_camera_tables_schema,
    ensure_close_residual_columns as ensure_close_residual_schema_columns,
    ensure_employee_profiles_table as ensure_employee_profiles_schema_table,
    ensure_employee_schedule_entries_table as ensure_employee_schedule_entries_schema_table,
    ensure_last_mid_at_column as ensure_last_mid_at_schema_column,
    ensure_media_local_path_columns as ensure_media_local_path_schema_columns,
    ensure_mid_started_at_column as ensure_mid_started_at_schema_column,
    ensure_mid_checklist_data_table as ensure_mid_checklist_data_schema_table,
    ensure_open_checklist_media_table as ensure_open_checklist_media_schema_table,
    ensure_shift_audit_columns as ensure_shift_audit_schema_columns,
    ensure_shift_periodic_residuals_table as ensure_shift_periodic_residuals_schema_table,
    ensure_shift_status_column as ensure_shift_status_schema_column,
    ensure_shift_status_index as ensure_shift_status_schema_index,
)


class Database:
    def __init__(self, db_path: str | Path) -> None:
        """Создаёт подключение к SQLite и инициализирует блокировку.

        Args:
            db_path: Путь к файлу базы данных.

        Returns:
            None.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=15.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()

    def _execute(self, query: str, params: tuple[Any, ...] = ()) -> int:
        """Выполняет SQL-запрос и возвращает id последней записи.

        Args:
            query: SQL-запрос.
            params: Параметры SQL-запроса.

        Returns:
            Идентификатор последней вставленной записи.
        """
        with self._lock:
            cursor = self._conn.execute(query, params)
            self._conn.commit()
            return cursor.lastrowid

    def _execute_script(self, script: str) -> None:
        """Выполняет SQL-скрипт.

        Args:
            script: SQL-скрипт.

        Returns:
            None.
        """
        with self._lock:
            self._conn.executescript(script)
            self._conn.commit()

    def _fetchone(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        """Возвращает одну запись по SQL-запросу.

        Args:
            query: SQL-запрос.
            params: Параметры SQL-запроса.

        Returns:
            Словарь с данными строки или None.
        """
        with self._lock:
            row = self._conn.execute(query, params).fetchone()
        return dict(row) if row else None

    def _fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Возвращает все строки по SQL-запросу.

        Args:
            query: SQL-запрос.
            params: Параметры SQL-запроса.

        Returns:
            Список словарей с результатами запроса.
        """
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def _ensure_shift_status_column(self) -> None:
        """Проверяет и синхронизирует колонку статуса смены.

        Args:
            Нет параметров.

        Returns:
            None.
        """
        with self._lock:
            ensure_shift_status_schema_column(self._conn)

    def _ensure_shift_audit_columns(self) -> None:
        """Проверяет и синхронизирует колонки аудита открытия/закрытия смены.

        Args:
            Нет параметров.

        Returns:
            None.
        """
        with self._lock:
            ensure_shift_audit_schema_columns(self._conn)

    def _ensure_close_residual_columns(self) -> None:
        """Проверяет и синхронизирует поля нормализации остатков закрытия.

        Args:
            Нет параметров.

        Returns:
            None.
        """
        with self._lock:
            ensure_close_residual_schema_columns(self._conn)

    def _ensure_shift_status_index(self) -> None:
        """Создаёт индекс статуса смены, если он отсутствует.

        Args:
            Нет параметров.

        Returns:
            None.
        """
        with self._lock:
            ensure_shift_status_schema_index(self._conn)

    def _close_stale_open_shifts(self, today: str | None = None) -> None:
        """Закрывает брошенные OPEN-смены с прошедшей датой.

        Args:
            today: Сегодняшняя дата в формате YYYY-MM-DD в timezone приложения.

        Returns:
            None.
        """
        with self._lock:
            close_stale_open_shifts_schema(self._conn, today=today)

    def _ensure_last_mid_at_column(self) -> None:
        """Добавляет колонку last_mid_at в таблицу shifts."""
        with self._lock:
            ensure_last_mid_at_schema_column(self._conn)

    def _ensure_mid_started_at_column(self) -> None:
        """Добавляет колонку mid_started_at в таблицу shifts."""
        with self._lock:
            ensure_mid_started_at_schema_column(self._conn)

    def _ensure_open_checklist_media_table(self) -> None:
        """Создаёт таблицу open_checklist_media."""
        with self._lock:
            ensure_open_checklist_media_schema_table(self._conn)

    def _ensure_mid_checklist_data_table(self) -> None:
        """Создаёт таблицу mid_checklist_data."""
        with self._lock:
            ensure_mid_checklist_data_schema_table(self._conn)

    def _ensure_camera_tables(self) -> None:
        """Создаёт таблицы camera_devices и camera_videos."""
        with self._lock:
            ensure_camera_tables_schema(self._conn)

    def _ensure_employee_profiles_table(self) -> None:
        """Создаёт таблицу employee_profiles."""
        with self._lock:
            ensure_employee_profiles_schema_table(self._conn)

    def _ensure_employee_schedule_entries_table(self) -> None:
        """Создаёт таблицу employee_schedule_entries."""
        with self._lock:
            ensure_employee_schedule_entries_schema_table(self._conn)

    def _ensure_shift_periodic_residuals_table(self) -> None:
        """Создаёт таблицу shift_periodic_residuals."""
        with self._lock:
            ensure_shift_periodic_residuals_schema_table(self._conn)

    def _ensure_media_local_path_columns(self) -> None:
        """Добавляет колонку local_path в таблицы медиа."""
        with self._lock:
            ensure_media_local_path_schema_columns(self._conn)

    async def init(self, today: str | None = None) -> None:
        """Инициализирует схему базы данных и миграции.

        Args:
            today: Сегодняшняя дата (YYYY-MM-DD) в timezone приложения для
                   корректного закрытия брошенных смен. Если None, используется
                   системный localtime SQLite.

        Returns:
            None.
        """
        schema = """
        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee TEXT NOT NULL,
            employee_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            open_time TEXT NOT NULL,
            close_time TEXT,
            opened_at TEXT,
            closed_at TEXT,
            opened_by TEXT,
            close_started_at TEXT,
            closed_by_id INTEGER,
            closed_by_name TEXT,
            close_duration_sec INTEGER,
            status TEXT NOT NULL DEFAULT 'OPEN',
            revenue REAL,
            photo TEXT,
            meat_start REAL,
            meat_end REAL,
            meat_used REAL,
            lavash_end REAL
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            item TEXT NOT NULL,
            quantity REAL NOT NULL,
            employee TEXT NOT NULL,
            employee_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item TEXT NOT NULL,
            quantity REAL NOT NULL,
            date TEXT NOT NULL,
            employee TEXT,
            employee_id INTEGER,
            time TEXT
        );

        CREATE TABLE IF NOT EXISTS checklist_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shift_id INTEGER NOT NULL,
            checklist_type TEXT NOT NULL,
            completed TEXT NOT NULL DEFAULT '[]',
            active_section INTEGER NOT NULL DEFAULT 0,
            UNIQUE(shift_id, checklist_type)
        );

        CREATE TABLE IF NOT EXISTS close_residuals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shift_id INTEGER NOT NULL,
            item_key TEXT NOT NULL,
            item_label TEXT NOT NULL,
            quantity REAL NOT NULL,
            unit TEXT NOT NULL,
            input_value REAL,
            unit_type TEXT,
            normalized_quantity REAL,
            normalized_unit TEXT,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            employee TEXT,
            employee_id INTEGER,
            UNIQUE(shift_id, item_key)
        );

        CREATE TABLE IF NOT EXISTS close_checklist_media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shift_id INTEGER NOT NULL,
            item_index INTEGER NOT NULL,
            item_label TEXT NOT NULL,
            file_id TEXT NOT NULL,
            file_unique_id TEXT,
            mime_type TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(shift_id, item_index)
        );

        CREATE INDEX IF NOT EXISTS idx_shifts_employee_id ON shifts(employee_id);
        CREATE INDEX IF NOT EXISTS idx_shifts_date ON shifts(date);
        CREATE INDEX IF NOT EXISTS idx_orders_date ON orders(date);
        CREATE INDEX IF NOT EXISTS idx_stock_date ON stock(date);
        CREATE INDEX IF NOT EXISTS idx_checklist_state_shift ON checklist_state(shift_id);
        CREATE INDEX IF NOT EXISTS idx_close_residuals_shift ON close_residuals(shift_id);
        CREATE INDEX IF NOT EXISTS idx_close_residuals_date ON close_residuals(date);
        CREATE INDEX IF NOT EXISTS idx_close_checklist_media_shift ON close_checklist_media(shift_id);
        """
        await asyncio.to_thread(self._execute_script, schema)
        await asyncio.to_thread(self._ensure_shift_status_column)
        await asyncio.to_thread(self._ensure_shift_audit_columns)
        await asyncio.to_thread(self._ensure_close_residual_columns)
        await asyncio.to_thread(self._ensure_shift_status_index)
        await asyncio.to_thread(self._close_stale_open_shifts, today)
        await asyncio.to_thread(self._ensure_last_mid_at_column)
        await asyncio.to_thread(self._ensure_mid_started_at_column)
        await asyncio.to_thread(self._ensure_open_checklist_media_table)
        await asyncio.to_thread(self._ensure_mid_checklist_data_table)
        await asyncio.to_thread(self._ensure_employee_profiles_table)
        await asyncio.to_thread(self._ensure_employee_schedule_entries_table)
        await asyncio.to_thread(self._ensure_shift_periodic_residuals_table)
        await asyncio.to_thread(self._ensure_media_local_path_columns)

    async def create_shift(
        self,
        *,
        employee: str,
        employee_id: int,
        shift_date: str,
        open_time: str,
    ) -> int:
        """Создаёт новую смену со статусом OPEN.

        Args:
            employee: Имя сотрудника.
            employee_id: Telegram ID сотрудника.
            shift_date: Дата смены.
            open_time: Время открытия смены.

        Returns:
            Идентификатор созданной смены.
        """
        query = """
        INSERT INTO shifts (
            employee,
            employee_id,
            date,
            open_time,
            opened_at,
            opened_by,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, 'OPEN')
        """
        return await asyncio.to_thread(
            self._execute,
            query,
            (
                employee,
                employee_id,
                shift_date,
                open_time,
                open_time,
                employee,
            ),
        )

    async def get_active_shift(self) -> dict[str, Any] | None:
        """Возвращает единственную открытую смену на сегодня (общая для всех сотрудников).

        Returns:
            Данные активной смены или None.
        """
        query = "SELECT * FROM shifts WHERE status = 'OPEN' ORDER BY id DESC"
        rows = await asyncio.to_thread(self._fetchall, query, ())
        if len(rows) > 1:
            ids = [r["id"] for r in rows]
            logger.warning("Multiple OPEN shifts detected: ids=%s — data integrity issue", ids)
        return rows[0] if rows else None

    async def get_active_shifts(
        self,
        *,
        shift_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Возвращает список активных смен, опционально фильтруя по дате.

        Args:
            shift_date: Дата в формате YYYY-MM-DD; если None — все активные смены.

        Returns:
            Список активных смен.
        """
        if shift_date is not None:
            query = """
            SELECT *
            FROM shifts
            WHERE status = 'OPEN' AND date = ?
            ORDER BY id ASC
            """
            return await asyncio.to_thread(self._fetchall, query, (shift_date,))
        query = """
        SELECT *
        FROM shifts
        WHERE status = 'OPEN'
        ORDER BY id ASC
        """
        return await asyncio.to_thread(self._fetchall, query)

    async def get_checklist_state(
        self,
        *,
        shift_id: int,
        checklist_type: str,
    ) -> dict[str, Any] | None:
        """Возвращает сохранённое состояние чек-листа смены.

        Args:
            shift_id: Идентификатор смены.
            checklist_type: Тип чек-листа.

        Returns:
            Состояние чек-листа или None.
        """
        query = """
        SELECT completed, active_section
        FROM checklist_state
        WHERE shift_id = ? AND checklist_type = ?
        LIMIT 1
        """
        row = await asyncio.to_thread(self._fetchone, query, (shift_id, checklist_type))
        if not row:
            return None

        completed_raw = row.get("completed", "[]")
        try:
            completed_data = json.loads(completed_raw)
        except (TypeError, json.JSONDecodeError):
            logger.error(
                "Corrupted checklist JSON for shift_id=%s type=%s: %r",
                shift_id,
                checklist_type,
                completed_raw,
            )
            completed_data = []

        completed: list[int] = []
        if isinstance(completed_data, list):
            for value in completed_data:
                try:
                    completed.append(int(value))
                except (TypeError, ValueError):
                    continue

        active_section_raw = row.get("active_section", 0)
        try:
            active_section = int(active_section_raw)
        except (TypeError, ValueError):
            active_section = 0

        return {
            "completed": sorted(set(completed)),
            "active_section": active_section,
        }

    async def upsert_checklist_state(
        self,
        *,
        shift_id: int,
        checklist_type: str,
        completed: list[int],
        active_section: int,
    ) -> None:
        """Создаёт или обновляет состояние чек-листа.

        Args:
            shift_id: Идентификатор смены.
            checklist_type: Тип чек-листа.
            completed: Список выполненных пунктов.
            active_section: Текущая секция.

        Returns:
            None.
        """
        unique_completed = sorted(set(int(value) for value in completed))
        query = """
        INSERT INTO checklist_state (shift_id, checklist_type, completed, active_section)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(shift_id, checklist_type) DO UPDATE
        SET completed = excluded.completed,
            active_section = excluded.active_section
        """
        await asyncio.to_thread(
            self._execute,
            query,
            (shift_id, checklist_type, json.dumps(unique_completed), active_section),
        )

    async def delete_checklist_state(self, shift_id: int, checklist_type: str) -> None:
        """Удаляет сохранённое состояние чек-листа после его завершения.

        Args:
            shift_id: Идентификатор смены.
            checklist_type: Тип чек-листа.

        Returns:
            None.
        """
        query = "DELETE FROM checklist_state WHERE shift_id = ? AND checklist_type = ?"
        await asyncio.to_thread(self._execute, query, (shift_id, checklist_type))

    async def upsert_close_residual(
        self,
        *,
        shift_id: int,
        item_key: str,
        item_label: str,
        quantity: float,
        unit: str,
        input_value: float | None,
        unit_type: str | None,
        normalized_quantity: float | None,
        normalized_unit: str | None,
        residual_date: str,
        residual_time: str,
        employee: str | None = None,
        employee_id: int | None = None,
    ) -> None:
        """Создаёт или обновляет запись по остаткам при закрытии.

        Args:
            shift_id: Идентификатор смены.
            item_key: Ключ остатка.
            item_label: Название остатка.
            quantity: Количество остатка.
            unit: Единица измерения.
            input_value: Значение в интерфейсной единице.
            unit_type: Тип единицы для нормализации.
            normalized_quantity: Нормализованное значение.
            normalized_unit: Базовая единица нормализации.
            residual_date: Дата фиксации.
            residual_time: Время фиксации.
            employee: Имя сотрудника.
            employee_id: Telegram ID сотрудника.

        Returns:
            None.
        """
        query = """
        INSERT INTO close_residuals (
            shift_id,
            item_key,
            item_label,
            quantity,
            unit,
            input_value,
            unit_type,
            normalized_quantity,
            normalized_unit,
            date,
            time,
            employee,
            employee_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(shift_id, item_key) DO UPDATE
        SET item_label = excluded.item_label,
            quantity = excluded.quantity,
            unit = excluded.unit,
            input_value = excluded.input_value,
            unit_type = excluded.unit_type,
            normalized_quantity = excluded.normalized_quantity,
            normalized_unit = excluded.normalized_unit,
            date = excluded.date,
            time = excluded.time,
            employee = excluded.employee,
            employee_id = excluded.employee_id
        """
        await asyncio.to_thread(
            self._execute,
            query,
            (
                shift_id,
                item_key,
                item_label,
                quantity,
                unit,
                input_value,
                unit_type,
                normalized_quantity,
                normalized_unit,
                residual_date,
                residual_time,
                employee,
                employee_id,
            ),
        )

    async def get_close_residuals(self, shift_id: int) -> dict[str, dict[str, Any]]:
        """Возвращает остатки закрытия по смене.

        Args:
            shift_id: Идентификатор смены.

        Returns:
            Словарь остатков по ключу item_key.
        """
        query = """
        SELECT
            item_key,
            item_label,
            quantity,
            unit,
            input_value,
            unit_type,
            normalized_quantity,
            normalized_unit,
            date,
            time,
            employee,
            employee_id
        FROM close_residuals
        WHERE shift_id = ?
        """
        rows = await asyncio.to_thread(self._fetchall, query, (shift_id,))
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            normalized_quantity = (
                float(row["normalized_quantity"])
                if row["normalized_quantity"] is not None
                else None
            )
            input_value = float(row["input_value"]) if row["input_value"] is not None else None
            result[str(row["item_key"])] = {
                "item_label": row["item_label"],
                "quantity": float(row["quantity"]),
                "unit": row["unit"],
                "input_value": input_value,
                "unit_type": row["unit_type"],
                "normalized_quantity": normalized_quantity,
                "normalized_unit": row["normalized_unit"],
                "date": row["date"],
                "time": row["time"],
                "employee": row["employee"],
                "employee_id": row["employee_id"],
            }
        return result

    async def upsert_close_checklist_media(
        self,
        *,
        shift_id: int,
        item_index: int,
        item_label: str,
        file_id: str,
        file_unique_id: str | None,
        mime_type: str | None,
        created_at: str,
        local_path: str | None = None,
    ) -> None:
        """Создаёт или обновляет фото для пункта закрытия смены.

        Args:
            shift_id: Идентификатор смены.
            item_index: Индекс пункта в мастере закрытия.
            item_label: Текст пункта.
            file_id: Telegram file_id.
            file_unique_id: Telegram file_unique_id.
            mime_type: MIME-тип файла.
            created_at: Время фиксации фото.
            local_path: Путь к файлу на диске (если скачан).

        Returns:
            None.
        """
        query = """
        INSERT INTO close_checklist_media (
            shift_id,
            item_index,
            item_label,
            file_id,
            file_unique_id,
            mime_type,
            created_at,
            local_path
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(shift_id, item_index) DO UPDATE
        SET item_label = excluded.item_label,
            file_id = excluded.file_id,
            file_unique_id = excluded.file_unique_id,
            mime_type = excluded.mime_type,
            created_at = excluded.created_at,
            local_path = excluded.local_path
        """
        await asyncio.to_thread(
            self._execute,
            query,
            (
                shift_id,
                item_index,
                item_label,
                file_id,
                file_unique_id,
                mime_type,
                created_at,
                local_path,
            ),
        )

    async def update_shift_mid_started_at(self, shift_id: int, timestamp: str) -> None:
        """Фиксирует момент запуска чек-листа ведения смены.

        Args:
            shift_id: Идентификатор смены.
            timestamp: ISO-время старта mid-чек-листа.

        Returns:
            None.
        """
        query = "UPDATE shifts SET mid_started_at = ? WHERE id = ?"
        await asyncio.to_thread(self._execute, query, (timestamp, shift_id))

    async def update_shift_last_mid(self, shift_id: int, timestamp: str) -> None:
        """Обновляет время последнего завершённого чек-листа ведения смены.

        Args:
            shift_id: Идентификатор смены.
            timestamp: ISO-время завершения mid-чек-листа.

        Returns:
            None.
        """
        query = "UPDATE shifts SET last_mid_at = ? WHERE id = ?"
        await asyncio.to_thread(self._execute, query, (timestamp, shift_id))

    async def update_shift_opened_at(self, shift_id: int, timestamp: str) -> None:
        """Обновляет время завершения чек-листа открытия смены.

        Args:
            shift_id: Идентификатор смены.
            timestamp: ISO-время завершения open-чек-листа.

        Returns:
            None.
        """
        query = "UPDATE shifts SET opened_at = ? WHERE id = ?"
        await asyncio.to_thread(self._execute, query, (timestamp, shift_id))

    async def upsert_open_checklist_media(
        self,
        *,
        shift_id: int,
        item_index: int,
        item_label: str,
        file_id: str,
        file_unique_id: str | None,
        mime_type: str | None,
        created_at: str,
        local_path: str | None = None,
    ) -> None:
        """Создаёт или обновляет фото для пункта открытия смены.

        Args:
            shift_id: Идентификатор смены.
            item_index: Индекс пункта в чек-листе открытия.
            item_label: Текст пункта.
            file_id: Telegram file_id.
            file_unique_id: Telegram file_unique_id.
            mime_type: MIME-тип файла.
            created_at: Время фиксации фото.
            local_path: Путь к файлу на диске (если скачан).

        Returns:
            None.
        """
        query = """
        INSERT INTO open_checklist_media (
            shift_id,
            item_index,
            item_label,
            file_id,
            file_unique_id,
            mime_type,
            created_at,
            local_path
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(shift_id, item_index) DO UPDATE
        SET item_label = excluded.item_label,
            file_id = excluded.file_id,
            file_unique_id = excluded.file_unique_id,
            mime_type = excluded.mime_type,
            created_at = excluded.created_at,
            local_path = excluded.local_path
        """
        await asyncio.to_thread(
            self._execute,
            query,
            (
                shift_id,
                item_index,
                item_label,
                file_id,
                file_unique_id,
                mime_type,
                created_at,
                local_path,
            ),
        )

    async def get_open_checklist_media(
        self,
        shift_id: int,
        item_index: int,
    ) -> dict[str, Any] | None:
        """Возвращает фото пункта открытия смены.

        Args:
            shift_id: Идентификатор смены.
            item_index: Индекс пункта.

        Returns:
            Словарь с данными фото или None.
        """
        query = """
        SELECT * FROM open_checklist_media
        WHERE shift_id = ? AND item_index = ?
        LIMIT 1
        """
        return await asyncio.to_thread(self._fetchone, query, (shift_id, item_index))

    async def upsert_mid_checklist_data(
        self,
        *,
        shift_id: int,
        key: str,
        value: float,
        unit: str,
        created_at: str,
    ) -> None:
        """Сохраняет числовые данные ведения смены.

        Args:
            shift_id: Идентификатор смены.
            key: Ключ показателя.
            value: Числовое значение.
            unit: Единица измерения.
            created_at: Время ввода данных.

        Returns:
            None.
        """
        query = """
        INSERT INTO mid_checklist_data (shift_id, key, value, unit, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(shift_id, key) DO UPDATE
        SET value = excluded.value,
            unit = excluded.unit,
            created_at = excluded.created_at
        """
        await asyncio.to_thread(
            self._execute,
            query,
            (shift_id, key, value, unit, created_at),
        )

    async def set_shift_meat_start(self, shift_id: int, meat_start: float) -> None:
        """Сохраняет стартовый остаток мяса для смены.

        Args:
            shift_id: Идентификатор смены.
            meat_start: Количество мяса в начале смены.

        Returns:
            None.
        """
        query = "UPDATE shifts SET meat_start = ? WHERE id = ?"
        await asyncio.to_thread(self._execute, query, (meat_start, shift_id))

    async def mark_close_flow_started(
        self,
        *,
        shift_id: int,
        started_at: str,
    ) -> None:
        """Фиксирует момент запуска сценария закрытия смены.

        Args:
            shift_id: Идентификатор смены.
            started_at: Время старта сценария закрытия.

        Returns:
            None.
        """
        query = """
        UPDATE shifts
        SET close_started_at = COALESCE(NULLIF(close_started_at, ''), ?)
        WHERE id = ?
        """
        await asyncio.to_thread(self._execute, query, (started_at, shift_id))

    async def close_shift(
        self,
        *,
        shift_id: int,
        close_time: str,
        revenue: float | None = None,
        photo: str | None = None,
        meat_end: float,
        lavash_end: float,
        closed_by_id: int | None = None,
        closed_by_name: str | None = None,
        close_duration_sec: int | None = None,
    ) -> float | None:
        """Закрывает смену и рассчитывает расход мяса.

        Args:
            shift_id: Идентификатор смены.
            close_time: Время закрытия.
            revenue: Выручка смены.
            photo: Идентификатор фото.
            meat_end: Остаток мяса.
            lavash_end: Остаток лаваша.
            closed_by_id: Telegram ID сотрудника, закрывшего смену.
            closed_by_name: Имя сотрудника, закрывшего смену.
            close_duration_sec: Длительность закрытия в секундах.

        Returns:
            Расход мяса или None, если стартовое значение отсутствует.
        """
        def _close_shift_atomic() -> float | None:
            with self._lock:
                row = self._conn.execute(
                    "SELECT meat_start FROM shifts WHERE id = ?", (shift_id,)
                ).fetchone()
                meat_start = row["meat_start"] if row else None
                meat_used_val = (
                    round(meat_start - meat_end, 3) if meat_start is not None else None
                )
                self._conn.execute(
                    """
                    UPDATE shifts
                    SET close_time = ?,
                        closed_at = ?,
                        status = 'CLOSED',
                        revenue = ?,
                        photo = ?,
                        meat_end = ?,
                        meat_used = ?,
                        lavash_end = ?,
                        closed_by_id = ?,
                        closed_by_name = ?,
                        close_duration_sec = ?
                    WHERE id = ?
                    """,
                    (
                        close_time,
                        close_time,
                        revenue,
                        photo,
                        meat_end,
                        meat_used_val,
                        lavash_end,
                        closed_by_id,
                        closed_by_name,
                        close_duration_sec,
                        shift_id,
                    ),
                )
                self._conn.commit()
                return meat_used_val

        return await asyncio.to_thread(_close_shift_atomic)

    async def save_order(
        self,
        *,
        order_type: str,
        item: str,
        quantity: float,
        employee: str,
        employee_id: int,
        order_date: str,
        order_time: str,
    ) -> int:
        """Сохраняет строку заказа в базе.

        Args:
            order_type: Тип заказа.
            item: Наименование позиции.
            quantity: Количество.
            employee: Имя сотрудника.
            employee_id: Telegram ID сотрудника.
            order_date: Дата заказа.
            order_time: Время заказа.

        Returns:
            Идентификатор записи заказа.
        """
        query = """
        INSERT INTO orders (type, item, quantity, employee, employee_id, date, time)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        return await asyncio.to_thread(
            self._execute,
            query,
            (order_type, item, quantity, employee, employee_id, order_date, order_time),
        )

    async def save_stock(
        self,
        *,
        item: str,
        quantity: float,
        stock_date: str,
        employee: str | None = None,
        employee_id: int | None = None,
        stock_time: str | None = None,
    ) -> int:
        """Сохраняет остаток товара.

        Args:
            item: Наименование позиции.
            quantity: Количество.
            stock_date: Дата фиксации.
            employee: Имя сотрудника.
            employee_id: Telegram ID сотрудника.
            stock_time: Время фиксации.

        Returns:
            Идентификатор записи остатков.
        """
        query = """
        INSERT INTO stock (item, quantity, date, employee, employee_id, time)
        VALUES (?, ?, ?, ?, ?, ?)
        """
        return await asyncio.to_thread(
            self._execute,
            query,
            (item, quantity, stock_date, employee, employee_id, stock_time),
        )

    async def get_active_employee_ids(self) -> list[int]:
        """Возвращает список сотрудников с открытыми сменами.

        Args:
            Нет параметров.

        Returns:
            Список Telegram ID сотрудников.
        """
        query = """
        SELECT DISTINCT employee_id
        FROM shifts
        WHERE status = 'OPEN'
        """
        rows = await asyncio.to_thread(self._fetchall, query)
        return [int(row["employee_id"]) for row in rows]

    async def get_employee_display_name(self, telegram_id: int) -> str | None:
        """Возвращает display_name сотрудника из employee_profiles или None.

        Args:
            telegram_id: Telegram ID сотрудника.

        Returns:
            display_name или None если не задан.
        """
        row = await asyncio.to_thread(
            self._fetchone,
            "SELECT display_name FROM employee_profiles WHERE telegram_id = ?",
            (telegram_id,),
        )
        if not row:
            return None
        name = str(row.get("display_name") or "").strip()
        return name or None

    async def has_shift_opened_on(
        self,
        shift_date: str,
        *,
        open_checklist_total: int | None = None,
    ) -> bool:
        """Проверяет, была ли реально открыта смена в указанную дату.

        Args:
            shift_date: Дата в формате YYYY-MM-DD.
            open_checklist_total: Если задано, требует завершённый open-чек-лист
                минимум на указанное количество пунктов.

        Returns:
            True, если смена считается открытой.
        """
        if open_checklist_total is None or open_checklist_total <= 0:
            query = "SELECT 1 FROM shifts WHERE date = ? LIMIT 1"
            row = await asyncio.to_thread(self._fetchone, query, (shift_date,))
            return row is not None

        rows = await asyncio.to_thread(
            self._fetchall,
            "SELECT id FROM shifts WHERE date = ? ORDER BY id DESC",
            (shift_date,),
        )
        for row in rows:
            try:
                shift_id = int(row["id"])
            except (TypeError, ValueError, KeyError):
                continue
            open_state = await self.get_checklist_state(
                shift_id=shift_id,
                checklist_type="open",
            )
            done_items = len(open_state.get("completed", [])) if open_state else 0
            if done_items >= open_checklist_total:
                return True
        return False

    async def get_shifts_by_date(self, shift_date: str) -> list[dict[str, Any]]:
        """Возвращает все смены за указанную дату.

        Args:
            shift_date: Дата в формате YYYY-MM-DD.

        Returns:
            Список смен.
        """
        query = """
        SELECT *
        FROM shifts
        WHERE date = ?
        ORDER BY COALESCE(opened_at, open_time) ASC, id ASC
        """
        return await asyncio.to_thread(self._fetchall, query, (shift_date,))

    async def get_shift_dates(self, limit: int = 30) -> list[str]:
        """Возвращает список дат, в которые были смены.

        Args:
            limit: Максимальное количество дат.

        Returns:
            Список дат в формате YYYY-MM-DD.
        """
        safe_limit = max(1, min(int(limit), 365))
        query = """
        SELECT date
        FROM shifts
        GROUP BY date
        ORDER BY date DESC
        LIMIT ?
        """
        rows = await asyncio.to_thread(self._fetchall, query, (safe_limit,))
        return [str(row["date"]) for row in rows]

    async def get_shift_by_id(self, shift_id: int) -> dict[str, Any] | None:
        """Возвращает смену по идентификатору.

        Args:
            shift_id: Идентификатор смены.

        Returns:
            Словарь данных смены или None.
        """
        query = "SELECT * FROM shifts WHERE id = ? LIMIT 1"
        return await asyncio.to_thread(self._fetchone, query, (shift_id,))

    async def get_checklists_completion_by_shift(
        self,
        shift_id: int,
    ) -> dict[str, int]:
        """Возвращает количество выполненных пунктов по чек-листам смены.

        Args:
            shift_id: Идентификатор смены.

        Returns:
            Словарь: тип чек-листа -> число выполненных пунктов.
        """
        query = """
        SELECT checklist_type, completed
        FROM checklist_state
        WHERE shift_id = ?
        """
        rows = await asyncio.to_thread(self._fetchall, query, (shift_id,))
        result: dict[str, int] = {}
        for row in rows:
            checklist_type = str(row.get("checklist_type", "")).strip()
            completed_raw = row.get("completed", "[]")
            try:
                completed_data = json.loads(str(completed_raw))
            except (TypeError, ValueError, json.JSONDecodeError):
                logger.error(
                    "Corrupted checklist JSON in completion count shift_id=%s type=%s: %r",
                    shift_id,
                    checklist_type,
                    completed_raw,
                )
                completed_data = []
            if isinstance(completed_data, list):
                result[checklist_type] = len(
                    {
                        int(item)
                        for item in completed_data
                        if isinstance(item, int)
                        or (isinstance(item, str) and item.strip().lstrip("-").isdigit())
                    }
                )
        return result

    async def get_close_residuals_by_date(
        self,
        residual_date: str,
    ) -> list[dict[str, Any]]:
        """Возвращает остатки закрытия за указанную дату.

        Args:
            residual_date: Дата в формате YYYY-MM-DD.

        Returns:
            Список строк остатков.
        """
        query = """
        SELECT cr.*, s.employee AS shift_employee
        FROM close_residuals cr
        LEFT JOIN shifts s ON s.id = cr.shift_id
        WHERE cr.date = ?
        ORDER BY cr.time ASC, cr.id ASC
        """
        return await asyncio.to_thread(self._fetchall, query, (residual_date,))

    async def get_orders_by_date(
        self, shift_date: str, order_type: str | None = None
    ) -> list[dict[str, Any]]:
        """Возвращает заказы за дату с опциональной фильтрацией типа.

        Args:
            shift_date: Дата в формате YYYY-MM-DD.
            order_type: Тип заказа или None.

        Returns:
            Список записей заказов.
        """
        if order_type:
            query = """
            SELECT * FROM orders
            WHERE date = ? AND type = ?
            ORDER BY time ASC, id ASC
            """
            params: tuple[Any, ...] = (shift_date, order_type)
        else:
            query = "SELECT * FROM orders WHERE date = ? ORDER BY time ASC, id ASC"
            params = (shift_date,)
        return await asyncio.to_thread(self._fetchall, query, params)

    async def get_latest_stock_by_date(self, stock_date: str) -> dict[str, float]:
        """Возвращает последние остатки по каждой позиции за дату.

        Args:
            stock_date: Дата в формате YYYY-MM-DD.

        Returns:
            Словарь остатков по наименованию позиции.
        """
        query = """
        SELECT s.item, s.quantity
        FROM stock s
        INNER JOIN (
            SELECT item, MAX(id) AS max_id
            FROM stock
            WHERE date = ?
            GROUP BY item
        ) latest ON latest.max_id = s.id
        """
        rows = await asyncio.to_thread(self._fetchall, query, (stock_date,))
        return {row["item"]: float(row["quantity"]) for row in rows}

    async def get_latest_stock_item_quantity(self, item: str) -> float | None:
        """Возвращает последнее сохранённое количество по позиции склада.

        Args:
            item: Наименование позиции.

        Returns:
            Последнее количество либо None, если данных нет.
        """
        query = """
        SELECT quantity
        FROM stock
        WHERE item = ?
        ORDER BY id DESC
        LIMIT 1
        """
        row = await asyncio.to_thread(self._fetchone, query, (item,))
        if not row:
            return None
        value = row.get("quantity")
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def get_all_open_checklist_media(
        self,
        shift_id: int,
    ) -> list[dict[str, Any]]:
        """Возвращает все фото открытия смены.

        Args:
            shift_id: Идентификатор смены.

        Returns:
            Список словарей с данными фото, отсортированных по item_index.
        """
        query = """
        SELECT * FROM open_checklist_media
        WHERE shift_id = ?
        ORDER BY item_index
        """
        return await asyncio.to_thread(self._fetchall, query, (shift_id,))

    async def insert_periodic_residual(
        self,
        *,
        shift_id: int,
        key: str,
        value: float,
        unit: str,
        recorded_at: str,
    ) -> None:
        """Сохраняет запись периодического остатка.

        Args:
            shift_id: Идентификатор смены.
            key: Ключ позиции.
            value: Числовое значение.
            unit: Единица измерения.
            recorded_at: Время записи (ISO).

        Returns:
            None.
        """
        query = """
        INSERT INTO shift_periodic_residuals (shift_id, key, value, unit, recorded_at)
        VALUES (?, ?, ?, ?, ?)
        """
        await asyncio.to_thread(
            self._execute,
            query,
            (shift_id, key, value, unit, recorded_at),
        )

    async def get_periodic_residuals_for_shift(
        self,
        shift_id: int,
    ) -> list[dict[str, Any]]:
        """Возвращает периодические остатки смены.

        Args:
            shift_id: Идентификатор смены.

        Returns:
            Список словарей, отсортированных по recorded_at.
        """
        query = """
        SELECT * FROM shift_periodic_residuals
        WHERE shift_id = ?
        ORDER BY recorded_at
        """
        return await asyncio.to_thread(self._fetchall, query, (shift_id,))

    async def close(self) -> None:
        """Закрывает подключение к базе данных.

        Args:
            Нет параметров.

        Returns:
            None.
        """
        await asyncio.to_thread(self._conn.close)
