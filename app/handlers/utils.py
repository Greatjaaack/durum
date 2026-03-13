from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from app.config import Settings


logger = logging.getLogger(__name__)


def build_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Строит основную reply-клавиатуру бота.

    Args:
        Нет параметров.

    Returns:
        Готовая клавиатура главного меню.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="/open"),
                KeyboardButton(text="/mid"),
                KeyboardButton(text="/close"),
            ],
            [
                KeyboardButton(text="/order_products"),
                KeyboardButton(text="/order_supplies"),
            ],
            [
                KeyboardButton(text="/stock"),
                KeyboardButton(text="/problem"),
            ],
            [
                KeyboardButton(text="/report"),
                KeyboardButton(text="/fact"),
            ],
            [
                KeyboardButton(text="/ai"),
                KeyboardButton(text="/stop"),
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
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

    if item_key != "sauce":
        return parse_non_negative_number(text)

    if "/" in text:
        parts = text.split("/")
        if len(parts) != 2:
            return None
        numerator = parse_non_negative_number(parts[0])
        denominator = parse_positive_number(parts[1])
        if numerator is None or denominator is None:
            return None
        return numerator / denominator

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
    except Exception:
        logger.exception("Failed to send message to work chat")
