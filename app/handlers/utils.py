from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, KeyboardButton, Message, ReplyKeyboardMarkup

from app.config import Settings
from app.units_config import parse_mixed_number
from app.handlers.constants import (
    MENU_CLOSE_SHIFT,
    MENU_MID_SHIFT,
    MENU_OPEN_SHIFT,
    MENU_RESIDUALS,
    MENU_SHIFT_PHOTOS,
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
            [
                KeyboardButton(text=MENU_RESIDUALS),
                KeyboardButton(text=MENU_SHIFT_PHOTOS),
            ],
        ]
    else:
        keyboard = [
            [KeyboardButton(text=MENU_OPEN_SHIFT)],
        ]

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
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
    if not math.isfinite(value) or value < 0:
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

    if item_key in {"sauce", "soup"}:
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


async def safe_edit_text(
    message: Message,
    text: str,
    reply_markup: object | None = None,
    *,
    log_context: str = "message",
) -> None:
    """Безопасно редактирует сообщение Telegram.

    Игнорирует ошибку Telegram `message is not modified` для повторных кликов.

    Args:
        message: Сообщение для редактирования.
        text: Новый текст.
        reply_markup: Inline-клавиатура или None.
        log_context: Короткая метка контекста для лога.

    Returns:
        None.
    """
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            logger.info("Skipped %s edit: no changes", log_context)
            return
        raise


async def safe_answer_callback(
    callback: CallbackQuery,
    text: str | None = None,
    *,
    show_alert: bool = False,
    log_context: str = "callback",
) -> None:
    """Безопасно отвечает на callback-запрос.

    Игнорирует ошибку Telegram `query is too old`, которая возникает
    после сетевых сбоев/переподключений или при повторных кликах по старым кнопкам.

    Args:
        callback: Callback-запрос Telegram.
        text: Текст уведомления или None.
        show_alert: Показать alert-окно вместо toast.
        log_context: Короткая метка контекста для лога.

    Returns:
        None.
    """
    try:
        if text is None:
            await callback.answer()
        else:
            await callback.answer(text, show_alert=show_alert)
    except TelegramBadRequest as error:
        lowered = str(error).lower()
        if (
            "query is too old" in lowered
            or "query id is invalid" in lowered
            or "response timeout expired" in lowered
        ):
            logger.info("Skipped %s callback answer: stale query", log_context)
            return
        raise


async def safe_delete_message(
    message: Message,
    *,
    log_context: str = "message",
) -> None:
    """Пытается удалить сообщение, не прерывая сценарий при ошибках Telegram.

    Args:
        message: Сообщение для удаления.
        log_context: Короткая метка контекста для лога.

    Returns:
        None.
    """
    try:
        await message.delete()
    except TelegramBadRequest as error:
        lowered = str(error).lower()
        if (
            "message to delete not found" in lowered
            or "message can't be deleted" in lowered
            or "message cannot be deleted" in lowered
            or "not enough rights" in lowered
        ):
            logger.info("Skipped %s delete: %s", log_context, error)
            return
        raise
    except Exception:
        logger.exception("Failed to delete %s", log_context)


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
) -> Literal["work_chat", "owner_fallback", "failed"]:
    """Отправляет уведомление в рабочий чат.

    Args:
        bot: Экземпляр Telegram-бота.
        settings: Настройки приложения.
        text: Текст сообщения.

    Returns:
        `work_chat`, если отправлено в рабочий чат;
        `owner_fallback`, если отправлено владельцу как fallback;
        `failed`, если отправить не удалось.
    """
    try:
        await bot.send_message(settings.work_chat_id, text)
        return "work_chat"
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
                return "owner_fallback"
            except Exception:
                logger.exception("Failed to send fallback message to owner")
                return "failed"
        logger.exception("Failed to send message to work chat")
        return "failed"
    except Exception:
        logger.exception("Failed to send message to work chat")
        return "failed"
