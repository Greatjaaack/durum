from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from app.config import Settings
from app.units_config import parse_mixed_number
from app.handlers.constants import (
    MENU_BACK,
    MENU_CLOSE_SHIFT,
    MENU_FACT,
    MENU_MID_SHIFT,
    MENU_MORE,
    MENU_OPEN_SHIFT,
    MENU_ORDER_PRODUCTS,
    MENU_ORDER_SUPPLIES,
    MENU_PROBLEM,
    MENU_STOCK,
)


logger = logging.getLogger(__name__)


def build_shift_menu_keyboard(
    *,
    is_shift_open: bool,
) -> ReplyKeyboardMarkup:
    """Строит меню первого уровня в зависимости от статуса смены.

    Args:
        is_shift_open: Признак открытой смены у сотрудника.

    Returns:
        Reply-клавиатура первого уровня.
    """
    if is_shift_open:
        keyboard = [
            [
                KeyboardButton(text=MENU_MID_SHIFT),
                KeyboardButton(text=MENU_CLOSE_SHIFT),
            ],
            [KeyboardButton(text=MENU_MORE)],
        ]
    else:
        keyboard = [
            [KeyboardButton(text=MENU_OPEN_SHIFT)],
            [KeyboardButton(text=MENU_MORE)],
        ]

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def build_additional_menu_keyboard() -> ReplyKeyboardMarkup:
    """Строит меню второго уровня «Дополнительно».

    Args:
        Нет параметров.

    Returns:
        Reply-клавиатура дополнительных действий.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=MENU_ORDER_PRODUCTS),
                KeyboardButton(text=MENU_ORDER_SUPPLIES),
            ],
            [
                KeyboardButton(text=MENU_STOCK),
                KeyboardButton(text=MENU_PROBLEM),
            ],
            [KeyboardButton(text=MENU_FACT)],
            [KeyboardButton(text=MENU_BACK)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Дополнительные действия",
    )


def employee_name(
    message: Message,
) -> str:
    """Возвращает отображаемое имя сотрудника из сообщения.

    Args:
        message: Входящее сообщение Telegram.

    Returns:
        Имя сотрудника или fallback-значение.
    """
    user = message.from_user
    if not user:
        return "Unknown employee"
    if user.username:
        return f"@{user.username}"
    return user.full_name


def now_local(
    settings: Settings,
) -> datetime:
    """Возвращает текущее время в таймзоне приложения.

    Args:
        settings: Настройки приложения.

    Returns:
        Текущее локальное время.
    """
    return datetime.now(ZoneInfo(settings.timezone))


def parse_non_negative_number(
    raw: str,
) -> float | None:
    """Преобразует строку в неотрицательное число.

    Args:
        raw: Входная строка.

    Returns:
        Число или None при ошибке парсинга.
    """
    try:
        value = float(raw.replace(",", ".").strip())
    except ValueError:
        return None
    if value < 0:
        return None
    return value


def parse_positive_number(
    raw: str,
) -> float | None:
    """Преобразует строку в положительное число.

    Args:
        raw: Входная строка.

    Returns:
        Положительное число или None.
    """
    value = parse_non_negative_number(raw)
    if value is None or value <= 0:
        return None
    return value


def parse_close_residual_value(
    raw: str,
    item_key: str,
) -> float | None:
    """Парсит значение остатка закрытия смены.

    Args:
        raw: Входная строка.
        item_key: Ключ остатка.

    Returns:
        Число остатка или None.
    """
    text = raw.strip().replace(",", ".")
    if not text:
        return None

    if item_key == "sauce":
        return parse_mixed_number(text)

    return parse_non_negative_number(text)


def fmt_number(
    value: float,
) -> str:
    """Форматирует число без лишних нулей.

    Args:
        value: Числовое значение.

    Returns:
        Отформатированная строка.
    """
    return f"{value:.3f}".rstrip("0").rstrip(".")


async def notify_owner(
    bot: Bot,
    settings: Settings,
    text: str,
) -> None:
    """Отправляет уведомление владельцу.

    Args:
        bot: Экземпляр Telegram-бота.
        settings: Настройки приложения.
        text: Текст уведомления.

    Returns:
        None.
    """
    try:
        await bot.send_message(settings.owner_id, text)
    except Exception:
        logger.exception("Failed to notify owner")


async def notify_work_chat(
    bot: Bot,
    settings: Settings,
    text: str,
) -> None:
    """Отправляет уведомление в рабочий чат.

    Args:
        bot: Экземпляр Telegram-бота.
        settings: Настройки приложения.
        text: Текст сообщения.

    Returns:
        None.
    """
    try:
        await bot.send_message(settings.work_chat_id, text)
    except TelegramBadRequest as error:
        if "chat not found" in str(error).lower():
            logger.warning(
                "Work chat not found (work_chat_id=%s). Sending message to owner instead.",
                settings.work_chat_id,
            )
            try:
                await bot.send_message(
                    settings.owner_id,
                    "⚠ Рабочий чат недоступен, отправляю отчёт владельцу.\n\n" + text,
                )
            except Exception:
                logger.exception("Failed to send fallback message to owner")
            return
        logger.exception("Failed to send message to work chat")
    except Exception:
        logger.exception("Failed to send message to work chat")
