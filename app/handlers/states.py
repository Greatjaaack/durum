from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class CloseShiftStates(StatesGroup):
    """Состояния FSM для сценария закрытия смены."""

    wizard = State()


class StockStates(StatesGroup):
    """Состояния FSM для ввода остатков."""

    waiting_meat = State()
    waiting_lavash = State()
    waiting_potato = State()


class OpenShiftStates(StatesGroup):
    """Состояния FSM для открытия смены."""

    waiting_photo = State()


class MidShiftStates(StatesGroup):
    """Состояния FSM для ведения смены."""

    waiting_numeric = State()


class PeriodicResidualStates(StatesGroup):
    """Состояния FSM для записи периодических остатков."""

    collecting = State()


class ProblemStates(StatesGroup):
    """Состояния FSM для фиксации проблем."""

    waiting_text = State()
