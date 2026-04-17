from __future__ import annotations

import calendar
import sqlite3


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def fetch_all_employees_with_profiles(
    conn: sqlite3.Connection,
) -> list[dict]:
    """Возвращает всех сотрудников с данными профилей.

    Берёт уникальные пары (employee_id, employee) из shifts,
    присоединяет данные из employee_profiles.

    Args:
        conn: Подключение SQLite.

    Returns:
        Список словарей: telegram_id, raw_name, display_name, is_active, shifts_count.
    """
    if not _table_exists(conn, "shifts"):
        return []

    rows = conn.execute(
        """
        SELECT
            s.employee_id AS telegram_id,
            MAX(s.employee) AS raw_name,
            COUNT(*) AS shifts_count,
            COALESCE(ep.display_name, '') AS display_name,
            COALESCE(ep.is_active, 1) AS is_active
        FROM shifts s
        LEFT JOIN employee_profiles ep ON ep.telegram_id = s.employee_id
        WHERE s.employee_id IS NOT NULL
        GROUP BY s.employee_id
        ORDER BY shifts_count DESC, raw_name ASC
        """
    ).fetchall()

    result = []
    for row in rows:
        result.append(
            {
                "telegram_id": int(row["telegram_id"]),
                "raw_name": str(row["raw_name"] or ""),
                "display_name": str(row["display_name"] or ""),
                "is_active": bool(row["is_active"]),
                "shifts_count": int(row["shifts_count"]),
            }
        )
    return result


def upsert_employee_profile(
    conn: sqlite3.Connection,
    telegram_id: int,
    display_name: str,
    is_active: int,
) -> None:
    """Создаёт или обновляет профиль сотрудника.

    Args:
        conn: Подключение SQLite.
        telegram_id: Telegram ID сотрудника.
        display_name: Отображаемое имя.
        is_active: Флаг активности (1/0).
    """
    conn.execute(
        """
        INSERT INTO employee_profiles (telegram_id, display_name, is_active)
        VALUES (?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            display_name = excluded.display_name,
            is_active = excluded.is_active
        """,
        (telegram_id, display_name, int(is_active)),
    )
    conn.commit()


def upsert_schedule_entry(
    conn: sqlite3.Connection,
    employee_telegram_id: int,
    date: str,
    shift_type: str,
    start_time: str | None,
    end_time: str | None,
) -> None:
    """Создаёт или обновляет запись расписания.

    Args:
        conn: Подключение SQLite.
        employee_telegram_id: Telegram ID сотрудника.
        date: Дата в формате YYYY-MM-DD.
        shift_type: Тип смены ('full' или 'half').
        start_time: Время начала (HH:MM, только для half).
        end_time: Время окончания (HH:MM, только для half).
    """
    conn.execute(
        """
        INSERT INTO employee_schedule_entries
            (employee_telegram_id, date, shift_type, start_time, end_time)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(employee_telegram_id, date) DO UPDATE SET
            shift_type = excluded.shift_type,
            start_time = excluded.start_time,
            end_time = excluded.end_time
        """,
        (employee_telegram_id, date, shift_type, start_time, end_time),
    )
    conn.commit()


def delete_schedule_entry(
    conn: sqlite3.Connection,
    employee_telegram_id: int,
    date: str,
) -> None:
    """Удаляет запись расписания.

    Args:
        conn: Подключение SQLite.
        employee_telegram_id: Telegram ID сотрудника.
        date: Дата в формате YYYY-MM-DD.
    """
    conn.execute(
        "DELETE FROM employee_schedule_entries WHERE employee_telegram_id = ? AND date = ?",
        (employee_telegram_id, date),
    )
    conn.commit()


def fetch_schedule_matrix(
    conn: sqlite3.Connection,
    year: int,
    month: int,
) -> dict:
    """Строит матрицу расписания на указанный месяц.

    Args:
        conn: Подключение SQLite.
        year: Год.
        month: Месяц (1-12).

    Returns:
        Словарь: employees, days, day_labels, entries.
    """
    _, days_in_month = calendar.monthrange(year, month)
    days = list(range(1, days_in_month + 1))

    ru_weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    day_labels = []
    for d in days:
        wd = calendar.weekday(year, month, d)
        day_labels.append(f"{d}\n{ru_weekdays[wd]}")

    employees: list[dict] = []
    if _table_exists(conn, "employee_profiles"):
        emp_rows = conn.execute(
            "SELECT telegram_id, display_name, is_active FROM employee_profiles ORDER BY display_name ASC"
        ).fetchall()
        for row in emp_rows:
            employees.append(
                {
                    "telegram_id": int(row["telegram_id"]),
                    "display_name": str(row["display_name"] or ""),
                    "is_active": bool(row["is_active"]),
                }
            )

    entries: dict[int, dict[int, dict]] = {}
    if _table_exists(conn, "employee_schedule_entries"):
        month_str = f"{year}-{month:02d}"
        sched_rows = conn.execute(
            """
            SELECT employee_telegram_id, date, shift_type, start_time, end_time
            FROM employee_schedule_entries
            WHERE date LIKE ?
            """,
            (f"{month_str}-%",),
        ).fetchall()
        for row in sched_rows:
            tid = int(row["employee_telegram_id"])
            try:
                day = int(str(row["date"]).split("-")[2])
            except (IndexError, ValueError):
                continue
            entries.setdefault(tid, {})[day] = {
                "shift_type": str(row["shift_type"] or "full"),
                "start_time": str(row["start_time"] or ""),
                "end_time": str(row["end_time"] or ""),
            }

    return {
        "employees": employees,
        "days": days,
        "day_labels": day_labels,
        "entries": entries,
    }
