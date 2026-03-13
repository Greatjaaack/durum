from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


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
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
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
            rows = self._conn.execute("PRAGMA table_info(shifts)").fetchall()
            columns = {str(row["name"]) for row in rows}
            if "status" not in columns:
                self._conn.execute(
                    "ALTER TABLE shifts ADD COLUMN status TEXT NOT NULL DEFAULT 'OPEN'"
                )
            self._conn.execute(
                """
                UPDATE shifts
                SET status = CASE WHEN close_time IS NULL THEN 'OPEN' ELSE 'CLOSED' END
                """
            )
            self._conn.commit()

    async def init(self) -> None:
        """Инициализирует схему базы данных и миграции.

        Args:
            Нет параметров.

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
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            employee TEXT,
            employee_id INTEGER,
            UNIQUE(shift_id, item_key)
        );

        CREATE TABLE IF NOT EXISTS food_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fact TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ai_state (
            user_id INTEGER PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            history TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_shifts_employee_id ON shifts(employee_id);
        CREATE INDEX IF NOT EXISTS idx_shifts_date ON shifts(date);
        CREATE INDEX IF NOT EXISTS idx_orders_date ON orders(date);
        CREATE INDEX IF NOT EXISTS idx_stock_date ON stock(date);
        CREATE INDEX IF NOT EXISTS idx_checklist_state_shift ON checklist_state(shift_id);
        CREATE INDEX IF NOT EXISTS idx_close_residuals_shift ON close_residuals(shift_id);
        CREATE INDEX IF NOT EXISTS idx_close_residuals_date ON close_residuals(date);
        CREATE INDEX IF NOT EXISTS idx_food_facts_created_at ON food_facts(created_at);
        """
        await asyncio.to_thread(self._execute_script, schema)
        await asyncio.to_thread(self._ensure_shift_status_column)
        await asyncio.to_thread(
            self._execute_script,
            "CREATE INDEX IF NOT EXISTS idx_shifts_status ON shifts(status);",
        )

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
        INSERT INTO shifts (employee, employee_id, date, open_time, status)
        VALUES (?, ?, ?, ?, 'OPEN')
        """
        return await asyncio.to_thread(
            self._execute, query, (employee, employee_id, shift_date, open_time)
        )

    async def get_active_shift(self, employee_id: int) -> dict[str, Any] | None:
        """Возвращает активную смену сотрудника.

        Args:
            employee_id: Telegram ID сотрудника.

        Returns:
            Данные активной смены или None.
        """
        query = """
        SELECT *
        FROM shifts
        WHERE employee_id = ? AND (status = 'OPEN' OR close_time IS NULL)
        ORDER BY id DESC
        LIMIT 1
        """
        return await asyncio.to_thread(self._fetchone, query, (employee_id,))

    async def get_active_shifts(self) -> list[dict[str, Any]]:
        """Возвращает список всех активных смен.

        Args:
            Нет параметров.

        Returns:
            Список активных смен.
        """
        query = """
        SELECT *
        FROM shifts
        WHERE status = 'OPEN' OR close_time IS NULL
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

    async def upsert_close_residual(
        self,
        *,
        shift_id: int,
        item_key: str,
        item_label: str,
        quantity: float,
        unit: str,
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
            residual_date: Дата фиксации.
            residual_time: Время фиксации.
            employee: Имя сотрудника.
            employee_id: Telegram ID сотрудника.

        Returns:
            None.
        """
        query = """
        INSERT INTO close_residuals (
            shift_id, item_key, item_label, quantity, unit, date, time, employee, employee_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(shift_id, item_key) DO UPDATE
        SET item_label = excluded.item_label,
            quantity = excluded.quantity,
            unit = excluded.unit,
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
        SELECT item_key, item_label, quantity, unit, date, time, employee, employee_id
        FROM close_residuals
        WHERE shift_id = ?
        """
        rows = await asyncio.to_thread(self._fetchall, query, (shift_id,))
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            result[str(row["item_key"])] = {
                "item_label": row["item_label"],
                "quantity": float(row["quantity"]),
                "unit": row["unit"],
                "date": row["date"],
                "time": row["time"],
                "employee": row["employee"],
                "employee_id": row["employee_id"],
            }
        return result

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

    async def close_shift(
        self,
        *,
        shift_id: int,
        close_time: str,
        revenue: float,
        photo: str,
        meat_end: float,
        lavash_end: float,
    ) -> float | None:
        """Закрывает смену и рассчитывает расход мяса.

        Args:
            shift_id: Идентификатор смены.
            close_time: Время закрытия.
            revenue: Выручка смены.
            photo: Идентификатор фото.
            meat_end: Остаток мяса.
            lavash_end: Остаток лаваша.

        Returns:
            Расход мяса или None, если стартовое значение отсутствует.
        """
        shift = await asyncio.to_thread(
            self._fetchone, "SELECT meat_start FROM shifts WHERE id = ?", (shift_id,)
        )
        meat_start = shift["meat_start"] if shift else None
        meat_used = round(meat_start - meat_end, 3) if meat_start is not None else None

        query = """
        UPDATE shifts
        SET close_time = ?,
            status = 'CLOSED',
            revenue = ?,
            photo = ?,
            meat_end = ?,
            meat_used = ?,
            lavash_end = ?
        WHERE id = ?
        """
        await asyncio.to_thread(
            self._execute,
            query,
            (close_time, revenue, photo, meat_end, meat_used, lavash_end, shift_id),
        )
        return meat_used

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
        WHERE status = 'OPEN' OR close_time IS NULL
        """
        rows = await asyncio.to_thread(self._fetchall, query)
        return [int(row["employee_id"]) for row in rows]

    async def has_open_shift(self) -> bool:
        """Проверяет наличие хотя бы одной открытой смены.

        Args:
            Нет параметров.

        Returns:
            True, если открытая смена существует.
        """
        query = """
        SELECT 1
        FROM shifts
        WHERE status = 'OPEN' OR close_time IS NULL
        LIMIT 1
        """
        row = await asyncio.to_thread(self._fetchone, query)
        return row is not None

    async def has_shift_opened_on(self, shift_date: str) -> bool:
        """Проверяет, была ли открыта смена в указанную дату.

        Args:
            shift_date: Дата в формате YYYY-MM-DD.

        Returns:
            True, если запись смены найдена.
        """
        query = "SELECT 1 FROM shifts WHERE date = ? LIMIT 1"
        row = await asyncio.to_thread(self._fetchone, query, (shift_date,))
        return row is not None

    async def get_shifts_by_date(self, shift_date: str) -> list[dict[str, Any]]:
        """Возвращает все смены за указанную дату.

        Args:
            shift_date: Дата в формате YYYY-MM-DD.

        Returns:
            Список смен.
        """
        query = "SELECT * FROM shifts WHERE date = ? ORDER BY open_time ASC"
        return await asyncio.to_thread(self._fetchall, query, (shift_date,))

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

    async def save_food_fact(self, *, fact: str, created_at: str) -> int:
        """Сохраняет факт о еде.

        Args:
            fact: Текст факта.
            created_at: Дата и время создания.

        Returns:
            Идентификатор записи факта.
        """
        query = """
        INSERT INTO food_facts (fact, created_at)
        VALUES (?, ?)
        """
        return await asyncio.to_thread(self._execute, query, (fact, created_at))

    async def get_latest_food_fact(self) -> dict[str, Any] | None:
        """Возвращает последний сохранённый факт о еде.

        Args:
            Нет параметров.

        Returns:
            Словарь факта или None.
        """
        query = """
        SELECT id, fact, created_at
        FROM food_facts
        ORDER BY id DESC
        LIMIT 1
        """
        return await asyncio.to_thread(self._fetchone, query)

    async def get_ai_state(self, user_id: int) -> dict[str, Any]:
        """Возвращает состояние AI-режима пользователя.

        Args:
            user_id: Telegram ID пользователя.

        Returns:
            Словарь со статусом режима и историей.
        """
        query = """
        SELECT enabled, history, updated_at
        FROM ai_state
        WHERE user_id = ?
        LIMIT 1
        """
        row = await asyncio.to_thread(self._fetchone, query, (user_id,))
        if not row:
            return {"enabled": False, "history": []}

        enabled = bool(int(row.get("enabled", 0)))
        history_raw = row.get("history", "[]")
        try:
            history_data = json.loads(history_raw)
        except (TypeError, json.JSONDecodeError):
            history_data = []

        history: list[dict[str, str]] = []
        if isinstance(history_data, list):
            for item in history_data:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role", "")).strip()
                text = str(item.get("text", "")).strip()
                if role not in {"user", "assistant"} or not text:
                    continue
                history.append({"role": role, "text": text})

        return {"enabled": enabled, "history": history}

    async def save_ai_state(
        self,
        *,
        user_id: int,
        enabled: bool,
        history: list[dict[str, str]],
        updated_at: str,
    ) -> None:
        """Сохраняет состояние AI-режима пользователя.

        Args:
            user_id: Telegram ID пользователя.
            enabled: Признак включённого AI-режима.
            history: История сообщений.
            updated_at: Дата и время обновления.

        Returns:
            None.
        """
        sanitized_history: list[dict[str, str]] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip()
            text = str(item.get("text", "")).strip()
            if role not in {"user", "assistant"} or not text:
                continue
            sanitized_history.append({"role": role, "text": text})

        query = """
        INSERT INTO ai_state (user_id, enabled, history, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE
        SET enabled = excluded.enabled,
            history = excluded.history,
            updated_at = excluded.updated_at
        """
        await asyncio.to_thread(
            self._execute,
            query,
            (
                user_id,
                1 if enabled else 0,
                json.dumps(sanitized_history, ensure_ascii=False),
                updated_at,
            ),
        )

    async def set_ai_enabled(self, *, user_id: int, enabled: bool, updated_at: str) -> None:
        """Обновляет только флаг включения AI-режима.

        Args:
            user_id: Telegram ID пользователя.
            enabled: Новый статус AI-режима.
            updated_at: Дата и время обновления.

        Returns:
            None.
        """
        state = await self.get_ai_state(user_id)
        history = state.get("history", [])
        if not isinstance(history, list):
            history = []
        await self.save_ai_state(
            user_id=user_id,
            enabled=enabled,
            history=history,
            updated_at=updated_at,
        )

    async def close(self) -> None:
        """Закрывает подключение к базе данных.

        Args:
            Нет параметров.

        Returns:
            None.
        """
        await asyncio.to_thread(self._conn.close)
