from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.config import Settings
from app.db import Database
from app.handlers.constants import MENU_STOCK, STOCK_ALERT_THRESHOLD, STOCK_REFERENCE_LEVELS
from app.handlers.states import StockStates
from app.handlers.utils import employee_name, notify_owner, now_local, parse_non_negative_number


logger = logging.getLogger(__name__)
stock_router = Router()


def _restore_stock_payload(
    state_data: dict[str, object],
) -> dict[str, float]:
    """Извлекает словарь остатков из FSM и валидирует типы.

    Args:
        state_data: Сырые данные FSM.

    Returns:
        Словарь остатков по наименованию позиции.
    """
    raw_stock = state_data.get("stock", {})
    if not isinstance(raw_stock, dict):
        return {}

    stock: dict[str, float] = {}
    for item, value in raw_stock.items():
        try:
            stock[str(item)] = float(value)
        except (TypeError, ValueError):
            continue
    return stock


@stock_router.message(Command("stock"))
@stock_router.message(F.text == MENU_STOCK)
async def stock_start(
    message: Message,
    state: FSMContext,
) -> None:
    """Запускает пошаговый сценарий ввода остатков.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.

    Returns:
        None.
    """
    await state.set_state(StockStates.waiting_meat)
    await state.update_data(stock={})
    await message.answer("Введите остаток мяса (кг):")


@stock_router.message(StockStates.waiting_meat)
async def stock_meat(
    message: Message,
    state: FSMContext,
) -> None:
    """Сохраняет остаток мяса и переводит сценарий к вводу лаваша.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.

    Returns:
        None.
    """
    if not message.text:
        return

    value = parse_non_negative_number(message.text)
    if value is None:
        await message.answer("Введите корректное число для мяса (кг).")
        return

    data = await state.get_data()
    stock = _restore_stock_payload(data)
    stock["мясо"] = value
    await state.update_data(stock=stock)
    await state.set_state(StockStates.waiting_lavash)
    await message.answer("Введите остаток лаваша (шт):")


@stock_router.message(StockStates.waiting_lavash)
async def stock_lavash(
    message: Message,
    state: FSMContext,
) -> None:
    """Сохраняет остаток лаваша и переводит сценарий к вводу картофеля.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.

    Returns:
        None.
    """
    if not message.text:
        return

    value = parse_non_negative_number(message.text)
    if value is None:
        await message.answer("Введите корректное число для лаваша (шт).")
        return

    data = await state.get_data()
    stock = _restore_stock_payload(data)
    stock["лаваш"] = value
    await state.update_data(stock=stock)
    await state.set_state(StockStates.waiting_potato)
    await message.answer("Введите остаток картофеля (кг):")


@stock_router.message(StockStates.waiting_potato)
async def stock_potato(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
    bot: Bot,
) -> None:
    """Сохраняет остаток картофеля, пишет все остатки в БД и проверяет пороги.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.
        db: Экземпляр базы данных.
        settings: Настройки приложения.
        bot: Экземпляр Telegram-бота.

    Returns:
        None.
    """
    if not message.text or not message.from_user:
        return

    value = parse_non_negative_number(message.text)
    if value is None:
        await message.answer("Введите корректное число для картофеля (кг).")
        return

    data = await state.get_data()
    stock = _restore_stock_payload(data)
    stock["картофель"] = value

    now = now_local(settings)
    stock_date = now.date().isoformat()
    stock_time = now.time().replace(microsecond=0).isoformat()
    employee = employee_name(message)

    for item, quantity in stock.items():
        await db.save_stock(
            item=item,
            quantity=quantity,
            stock_date=stock_date,
            employee=employee,
            employee_id=message.from_user.id,
            stock_time=stock_time,
        )

    alerts: list[str] = []
    for item, quantity in stock.items():
        reference = STOCK_REFERENCE_LEVELS.get(item)
        if reference is None:
            continue
        if quantity < reference * STOCK_ALERT_THRESHOLD:
            alerts.append(f"{item}: {quantity}")

    if alerts:
        await notify_owner(
            bot,
            settings,
            "⚠️ Низкий остаток:\n" + "\n".join(alerts) + f"\nСотрудник: {employee}",
        )
        logger.warning("Зафиксирован низкий остаток: %s", ", ".join(alerts))

    await state.clear()
    await message.answer("Остатки сохранены.")
