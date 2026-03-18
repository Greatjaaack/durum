from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.config import Settings
from app.db import Database
from app.handlers.constants import (
    MENU_CANCEL,
    MENU_PROBLEM,
    MENU_REPORT_BY_DATE,
)
from app.handlers.states import ProblemStates
from app.handlers.utils import (
    build_shift_menu_keyboard,
    employee_name,
    notify_owner,
)
from app.report_builder import build_daily_report


logger = logging.getLogger(__name__)
misc_router = Router()


async def _is_shift_open_for_user(
    db: Database,
    telegram_id: int,
) -> bool:
    """Проверяет, открыта ли смена у сотрудника.

    Args:
        db: Экземпляр базы данных.
        telegram_id: Telegram ID сотрудника.

    Returns:
        True, если смена открыта.
    """
    active_shift = await db.get_active_shift(telegram_id)
    return active_shift is not None


@misc_router.message(Command("start"))
async def start_command(
    message: Message,
    db: Database,
) -> None:
    """Отправляет стартовое сообщение и главное меню.

    Args:
        message: Входящее сообщение Telegram.
        db: Экземпляр базы данных.

    Returns:
        None.
    """
    is_shift_open = False
    if message.from_user:
        is_shift_open = await _is_shift_open_for_user(db, message.from_user.id)
    await message.answer(
        "Выберите действие:",
        reply_markup=build_shift_menu_keyboard(is_shift_open=is_shift_open),
    )


@misc_router.message(Command("cancel"))
@misc_router.message(F.text == MENU_CANCEL)
async def cancel_state(
    message: Message,
    state: FSMContext,
    db: Database,
) -> None:
    """Сбрасывает текущее FSM-состояние пользователя.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.
        db: Экземпляр базы данных.

    Returns:
        None.
    """
    await state.clear()
    is_shift_open = False
    if message.from_user:
        is_shift_open = await _is_shift_open_for_user(db, message.from_user.id)
    await message.answer(
        "Действие отменено.",
        reply_markup=build_shift_menu_keyboard(is_shift_open=is_shift_open),
    )


@misc_router.message(Command("problem"))
@misc_router.message(F.text == MENU_PROBLEM)
async def problem_start(
    message: Message,
    state: FSMContext,
) -> None:
    """Запускает сценарий отправки сообщения о проблеме.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.

    Returns:
        None.
    """
    await state.set_state(ProblemStates.waiting_text)
    await message.answer("Опишите проблему одним сообщением.")


@misc_router.message(ProblemStates.waiting_text)
async def problem_forward(
    message: Message,
    state: FSMContext,
    settings: Settings,
    bot: Bot,
) -> None:
    """Пересылает сообщение о проблеме владельцу.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.
        settings: Настройки приложения.
        bot: Экземпляр Telegram-бота.

    Returns:
        None.
    """
    if not message.from_user:
        return
    if not message.text:
        await message.answer("Отправьте текстовое описание проблемы.")
        return

    employee = employee_name(message)
    try:
        await bot.send_message(
            settings.owner_id,
            f"Проблема от сотрудника: {employee}",
        )
        await bot.forward_message(
            chat_id=settings.owner_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
    except Exception:
        logger.exception("Failed to forward problem message, sending plain text fallback")
        await notify_owner(
            bot,
            settings,
            (
                "Проблема от сотрудника\n"
                f"Сотрудник: {employee}\n"
                f"Сообщение: {message.text}"
            ),
        )
    await state.clear()
    await message.answer("Проблема отправлена владельцу.")


@misc_router.message(Command("report"))
async def report_for_date(
    message: Message,
    command: CommandObject,
    db: Database,
) -> None:
    """Формирует и отправляет отчёт за указанную дату.

    Args:
        message: Входящее сообщение Telegram.
        command: Объект команды с аргументами.
        db: Экземпляр базы данных.

    Returns:
        None.
    """
    date_arg = (command.args or "").strip()
    if not date_arg:
        await message.answer("Использование: /report YYYY-MM-DD")
        return

    try:
        report_text = await build_daily_report(db, date_arg)
    except ValueError:
        await message.answer("Неверный формат даты. Используйте YYYY-MM-DD.")
        return

    await message.answer(report_text)


@misc_router.message(F.text == MENU_REPORT_BY_DATE)
async def report_help_from_menu(
    message: Message,
) -> None:
    """Подсказывает формат команды отчёта по дате.

    Args:
        message: Входящее сообщение Telegram.

    Returns:
        None.
    """
    await message.answer("Для отчёта по дате используйте команду: /report YYYY-MM-DD")
