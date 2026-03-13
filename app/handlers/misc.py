from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.config import Settings
from app.db import Database
from app.handlers.states import ProblemStates
from app.handlers.utils import build_main_menu_keyboard, employee_name, notify_owner
from app.reports import build_daily_report


logger = logging.getLogger(__name__)
misc_router = Router()


@misc_router.message(Command("start"))
async def start_command(
    message: Message,
) -> None:
    """Отправляет стартовое сообщение и главное меню.

    Args:
        message: Входящее сообщение Telegram.

    Returns:
        None.
    """
    text = (
        "Выберите действие кнопками ниже.\n"
        "Для отчёта используйте формат: /report YYYY-MM-DD"
    )
    await message.answer(text, reply_markup=build_main_menu_keyboard())


@misc_router.message(Command("cancel"))
async def cancel_state(
    message: Message,
    state: FSMContext,
) -> None:
    """Сбрасывает текущее FSM-состояние пользователя.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.

    Returns:
        None.
    """
    await state.clear()
    await message.answer("Текущее действие отменено.")


@misc_router.message(Command("problem"))
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
