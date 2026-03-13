from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.ai_client import OpenRouterClient
from app.config import Settings
from app.db import Database


logger = logging.getLogger(__name__)
ai_router = Router()

# Системный промпт по умолчанию для текстовых AI-диалогов.
AI_DEFAULT_SYSTEM_PROMPT = "Ты помощник кухни дюрюмной. Отвечай коротко, ясно и по делу."


def _now(
    settings: Settings,
) -> datetime:
    """Возвращает текущее время в таймзоне приложения.

    Args:
        settings: Настройки приложения.

    Returns:
        Текущее локальное время.
    """
    return datetime.now(ZoneInfo(settings.timezone))


def _limit_text(
    text: str,
    limit: int,
) -> tuple[str, bool]:
    """Ограничивает строку заданной длиной.

    Args:
        text: Исходный текст.
        limit: Максимальная длина текста.

    Returns:
        Кортеж из ограниченного текста и флага усечения.
    """
    value = text.strip()
    if len(value) <= limit:
        return value, False
    return value[:limit].rstrip(), True


def _build_text_ai_messages(
    user_text: str,
) -> list[dict[str, str]]:
    """Формирует payload сообщений для одиночного AI-запроса.

    Args:
        user_text: Текст пользователя.

    Returns:
        Список сообщений для OpenRouter.
    """
    return [
        {"role": "system", "content": AI_DEFAULT_SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]


async def _generate_and_save_food_fact(
    db: Database,
    settings: Settings,
    ai_client: OpenRouterClient,
) -> str:
    """Генерирует факт о еде через AI и сохраняет его в базе.

    Args:
        db: Экземпляр базы данных.
        settings: Настройки приложения.
        ai_client: Клиент OpenRouter.

    Returns:
        Сгенерированный факт.
    """
    fact = await ai_client.generate_food_fact()
    created_at = _now(settings).replace(microsecond=0).isoformat()
    await db.save_food_fact(fact=fact, created_at=created_at)
    return fact


@ai_router.message(Command("fact"))
async def fact_command(
    message: Message,
    db: Database,
    settings: Settings,
    ai_client: OpenRouterClient,
) -> None:
    """Отправляет последний факт о еде или генерирует новый.

    Args:
        message: Входящее сообщение Telegram.
        db: Экземпляр базы данных.
        settings: Настройки приложения.
        ai_client: Клиент OpenRouter.

    Returns:
        None.
    """
    latest = await db.get_latest_food_fact()
    fact_text = ""
    if latest and latest.get("fact"):
        fact_text = str(latest["fact"]).strip()

    if not fact_text:
        if not ai_client.enabled:
            await message.answer("AI не настроен. Добавьте OPENROUTER_API_KEY в .env.")
            logger.warning("Запрошен /fact без настроенного OPENROUTER_API_KEY")
            return
        try:
            fact_text = await _generate_and_save_food_fact(db, settings, ai_client)
            logger.info("Сгенерирован новый факт по команде /fact")
        except Exception:
            logger.exception("Не удалось сгенерировать факт по команде /fact")
            await message.answer("Не удалось сгенерировать факт. Попробуйте позже.")
            return

    await message.answer(f"🍔 Факт о еде\n\n{fact_text}")


@ai_router.message(Command("ai"))
async def ai_mode_enable(
    message: Message,
    db: Database,
    settings: Settings,
    ai_client: OpenRouterClient,
) -> None:
    """Включает AI-режим для пользователя.

    Args:
        message: Входящее сообщение Telegram.
        db: Экземпляр базы данных.
        settings: Настройки приложения.
        ai_client: Клиент OpenRouter.

    Returns:
        None.
    """
    if not message.from_user:
        return

    if not ai_client.enabled:
        await message.answer("AI не настроен. Добавьте OPENROUTER_API_KEY в .env.")
        logger.warning("Пользователь %s запросил /ai без настроенного OpenRouter", message.from_user.id)
        return

    now = _now(settings).replace(microsecond=0).isoformat()
    await db.save_ai_state(
        user_id=message.from_user.id,
        enabled=True,
        history=[],
        updated_at=now,
    )
    logger.info("AI-режим включён для пользователя %s", message.from_user.id)
    await message.answer("AI режим включён. Напишите сообщение.")


@ai_router.message(Command("stop"))
async def ai_mode_disable(
    message: Message,
    db: Database,
    settings: Settings,
) -> None:
    """Выключает AI-режим для пользователя.

    Args:
        message: Входящее сообщение Telegram.
        db: Экземпляр базы данных.
        settings: Настройки приложения.

    Returns:
        None.
    """
    if not message.from_user:
        return

    now = _now(settings).replace(microsecond=0).isoformat()
    await db.set_ai_enabled(
        user_id=message.from_user.id,
        enabled=False,
        updated_at=now,
    )
    logger.info("AI-режим выключен для пользователя %s", message.from_user.id)
    await message.answer("AI режим выключен.")


@ai_router.message(F.text.regexp(r"^(?!/).+"))
async def ai_text_message(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
    ai_client: OpenRouterClient,
) -> None:
    """Обрабатывает текст пользователя в AI-режиме.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-состояние пользователя.
        db: Экземпляр базы данных.
        settings: Настройки приложения.
        ai_client: Клиент OpenRouter.

    Returns:
        None.
    """
    if not message.from_user or not message.text:
        return

    if await state.get_state() is not None:
        return

    ai_state = await db.get_ai_state(message.from_user.id)
    if not ai_state.get("enabled", False):
        return

    if not ai_client.enabled:
        await message.answer("AI недоступен. Проверьте настройки OpenRouter.")
        logger.warning("AI-режим активен без настроенного OpenRouter для %s", message.from_user.id)
        return

    user_text, user_trimmed = _limit_text(message.text, settings.ai_max_input_chars)
    if not user_text:
        await message.answer("Введите текст для AI.")
        return

    request_messages = _build_text_ai_messages(user_text)
    try:
        ai_reply = await ai_client.chat(request_messages)
    except Exception:
        logger.exception("Ошибка AI-запроса для пользователя %s", message.from_user.id)
        await message.answer("Не удалось получить ответ от AI. Попробуйте ещё раз.")
        return

    answer, _ = _limit_text(ai_reply, settings.ai_max_input_chars * 3)
    if not answer:
        answer = "Не удалось получить содержательный ответ."

    await db.set_ai_enabled(
        user_id=message.from_user.id,
        enabled=True,
        updated_at=_now(settings).replace(microsecond=0).isoformat(),
    )

    if user_trimmed:
        answer = f"⚠️ Сообщение было сокращено до {settings.ai_max_input_chars} символов.\n\n{answer}"

    logger.info("Успешный AI-ответ для пользователя %s", message.from_user.id)
    await message.answer(answer)
