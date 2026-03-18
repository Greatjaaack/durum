from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class CloseShiftStates(StatesGroup):
    """Состояния FSM для сценария закрытия смены."""

    wizard = State()


class OrderStates(StatesGroup):
    """Состояния FSM для сценариев заказа."""

    waiting_product_quantity = State()


class StockStates(StatesGroup):
    """Состояния FSM для ввода остатков."""

    waiting_meat = State()
    waiting_lavash = State()
    waiting_potato = State()


class ProblemStates(StatesGroup):
    """Состояния FSM для фиксации проблем."""

    waiting_text = State()
