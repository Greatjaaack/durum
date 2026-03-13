from __future__ import annotations

import asyncio
import sqlite3
import threading
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

    def _execute(self, query: str, params: tuple[Any, ...] = ()) -> int:
        with self._lock:
            cursor = self._conn.execute(query, params)
            self._conn.commit()
            return cursor.lastrowid

    def _execute_script(self, script: str) -> None:
        with self._lock:
            self._conn.executescript(script)
            self._conn.commit()

    def _fetchone(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(query, params).fetchone()
        return dict(row) if row else None

    def _fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    async def init(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee TEXT NOT NULL,
            employee_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            open_time TEXT NOT NULL,
            close_time TEXT,
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

        CREATE INDEX IF NOT EXISTS idx_shifts_employee_id ON shifts(employee_id);
        CREATE INDEX IF NOT EXISTS idx_shifts_date ON shifts(date);
        CREATE INDEX IF NOT EXISTS idx_orders_date ON orders(date);
        CREATE INDEX IF NOT EXISTS idx_stock_date ON stock(date);
        """
        await asyncio.to_thread(self._execute_script, schema)

    async def create_shift(
        self,
        *,
        employee: str,
        employee_id: int,
        shift_date: str,
        open_time: str,
    ) -> int:
        query = """
        INSERT INTO shifts (employee, employee_id, date, open_time)
        VALUES (?, ?, ?, ?)
        """
        return await asyncio.to_thread(
            self._execute, query, (employee, employee_id, shift_date, open_time)
        )

    async def get_active_shift(self, employee_id: int) -> dict[str, Any] | None:
        query = """
        SELECT *
        FROM shifts
        WHERE employee_id = ? AND close_time IS NULL
        ORDER BY id DESC
        LIMIT 1
        """
        return await asyncio.to_thread(self._fetchone, query, (employee_id,))

    async def set_shift_meat_start(self, shift_id: int, meat_start: float) -> None:
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
        shift = await asyncio.to_thread(
            self._fetchone, "SELECT meat_start FROM shifts WHERE id = ?", (shift_id,)
        )
        meat_start = shift["meat_start"] if shift else None
        meat_used = round(meat_start - meat_end, 3) if meat_start is not None else None

        query = """
        UPDATE shifts
        SET close_time = ?,
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
        query = "SELECT DISTINCT employee_id FROM shifts WHERE close_time IS NULL"
        rows = await asyncio.to_thread(self._fetchall, query)
        return [int(row["employee_id"]) for row in rows]

    async def has_shift_opened_on(self, shift_date: str) -> bool:
        query = "SELECT 1 FROM shifts WHERE date = ? LIMIT 1"
        row = await asyncio.to_thread(self._fetchone, query, (shift_date,))
        return row is not None

    async def get_shifts_by_date(self, shift_date: str) -> list[dict[str, Any]]:
        query = "SELECT * FROM shifts WHERE date = ? ORDER BY open_time ASC"
        return await asyncio.to_thread(self._fetchall, query, (shift_date,))

    async def get_orders_by_date(
        self, shift_date: str, order_type: str | None = None
    ) -> list[dict[str, Any]]:
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

    async def close(self) -> None:
        await asyncio.to_thread(self._conn.close)
