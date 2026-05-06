"""Тесты вычисления дедлайнов в app/reminders.py."""
from __future__ import annotations

from datetime import time
from zoneinfo import ZoneInfo

from app.reminders import (
    CLOSE_CHECKLIST_FIRST_REMINDER_OFFSET_MIN,
    CLOSE_CHECKLIST_SECOND_REMINDER_OFFSET_MIN,
    OPENING_DEADLINE_OFFSET_MIN,
    _time_with_offset,
)


def test_opening_deadline_runs_after_shift_start() -> None:
    """OPENING_DEADLINE_OFFSET_MIN должен давать дедлайн ПОСЛЕ старта смены.

    При SHIFT_OPEN_TIME=11:00 проверка должна выполняться в 11:30 (через
    30 минут после планового старта). Если кто-то случайно поменяет offset
    на отрицательный — тест поймает это, чтобы напоминание «смена не
    открыта» не приходило раньше времени смены.
    """
    h, m = _time_with_offset(
        time(11, 0),
        offset_minutes=OPENING_DEADLINE_OFFSET_MIN,
        timezone=ZoneInfo("Europe/Moscow"),
    )
    assert OPENING_DEADLINE_OFFSET_MIN > 0
    assert (h, m) == (11, 30)


def test_close_reminders_offsets_are_positive_and_ordered() -> None:
    """Напоминания о незакрытой смене идут после shift_close_time
    и второе — позже первого."""
    assert CLOSE_CHECKLIST_FIRST_REMINDER_OFFSET_MIN > 0
    assert CLOSE_CHECKLIST_SECOND_REMINDER_OFFSET_MIN > CLOSE_CHECKLIST_FIRST_REMINDER_OFFSET_MIN
