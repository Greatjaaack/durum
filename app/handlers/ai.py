from __future__ import annotations

import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.ai_client import OpenRouterClient
from app.config import Settings
from app.db import Database
from app.handlers.constants import MENU_AI_DISABLE, MENU_AI_ENABLE, MENU_FACT


logger = logging.getLogger(__name__)
ai_router = Router()

# Системный промпт по умолчанию для текстовых AI-диалогов.
AI_DEFAULT_SYSTEM_PROMPT = (
    "Ты помощник кухни дюрюмной. Отвечай коротко, ясно и по делу. "
    "Никогда не пиши, что ты не можешь открывать/закрывать смену или не имеешь доступа. "
    "Если просят действие в боте, укажи конкретную кнопку или команду."
)

# Количество последних фактов для проверки на повторения.
FACT_RECENT_WINDOW = 10

# Максимальное количество попыток генерации уникального факта.
FACT_RETRY_ATTEMPTS = 4

# Порог схожести фактов для фильтра дубликатов.
FACT_SIMILARITY_THRESHOLD = 0.74

# Фразы отказа модели, которые не должны попадать сотруднику кухни.
AI_REFUSAL_PATTERNS = (
    "не могу",
    "нет доступа",
    "не имею доступа",
    "i can't",
    "cannot",
    "i am unable",
)

# Унифицированный безопасный ответ вместо отказа модели.
AI_SAFE_OPERATION_REPLY = (
    "Для действий по смене используйте меню бота:\n"
    "• `▶ Открыть смену` / `/open`\n"
    "• `📝 Ведение смены` / `/mid`\n"
    "• `🔒 Закрыть смену` / `/close`"
)

# Быстрые операционные подсказки, чтобы не отправлять системные запросы в LLM.
OPERATIONS_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("открыть смену", "открой смену", "open shift"),
        "Открытие смены: нажмите `▶ Открыть смену` или команду `/open`.",
    ),
    (
        ("ведение смены", "чек-лист ведения", "mid"),
        "Ведение смены: нажмите `📝 Ведение смены` или команду `/mid`.",
    ),
    (
        ("закрыть смену", "закрой смену", "close shift"),
        "Закрытие смены: нажмите `🔒 Закрыть смену` или команду `/close`.",
    ),
)


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


def _operational_hint(
    user_text: str,
) -> str | None:
    """Возвращает локальную подсказку по операциям смены.

    Args:
        user_text: Текст пользователя.

    Returns:
        Текст подсказки или None.
    """
    normalized = user_text.lower().strip()
    if not normalized:
        return None
    for triggers, hint in OPERATIONS_HINTS:
        if any(trigger in normalized for trigger in triggers):
            return hint
    return None


def _sanitize_ai_reply(
    reply_text: str,
) -> str:
    """Нормализует ответ AI для рабочего UX без отказов.

    Args:
        reply_text: Сырой ответ модели.

    Returns:
        Безопасный ответ для пользователя.
    """
    clean_text = reply_text.strip()
    if not clean_text:
        return clean_text
    lower_text = clean_text.lower()
    if any(pattern in lower_text for pattern in AI_REFUSAL_PATTERNS):
        return AI_SAFE_OPERATION_REPLY
    return clean_text


def _fact_tokens(
    fact_text: str,
) -> set[str]:
    """Нормализует текст факта в набор токенов для сравнения.

    Args:
        fact_text: Текст факта.

    Returns:
        Множество токенов.
    """
    normalized = re.sub(r"[^\w\s]+", " ", fact_text.lower(), flags=re.UNICODE)
    return {token for token in normalized.split() if len(token) >= 3}


def _facts_similarity(
    left_text: str,
    right_text: str,
) -> float:
    """Считает схожесть двух фактов по коэффициенту Жаккара.

    Args:
        left_text: Первый факт.
        right_text: Второй факт.

    Returns:
        Значение схожести от 0 до 1.
    """
    left_tokens = _fact_tokens(left_text)
    right_tokens = _fact_tokens(right_text)
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = left_tokens.intersection(right_tokens)
    union = left_tokens.union(right_tokens)
    if not union:
        return 0.0
    return len(intersection) / len(union)


def _is_duplicate_fact(
    candidate_fact: str,
    recent_facts: list[str],
) -> bool:
    """Проверяет факт на повтор среди последних сохранённых.

    Args:
        candidate_fact: Новый сгенерированный факт.
        recent_facts: Последние факты из БД.

    Returns:
        True, если факт считается дубликатом.
    """
    candidate_clean = candidate_fact.strip()
    if not candidate_clean:
        return True

    candidate_lower = candidate_clean.lower()
    for saved_fact in recent_facts:
        saved_clean = saved_fact.strip()
        if not saved_clean:
            continue
        if candidate_lower == saved_clean.lower():
            return True
        similarity = _facts_similarity(candidate_clean, saved_clean)
        if similarity >= FACT_SIMILARITY_THRESHOLD:
            return True
    return False


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
    recent_facts = await db.get_recent_food_facts(limit=FACT_RECENT_WINDOW)

    for _ in range(FACT_RETRY_ATTEMPTS):
        fact = (await ai_client.generate_food_fact()).strip()
        if _is_duplicate_fact(fact, recent_facts):
            continue
        created_at = _now(settings).replace(microsecond=0).isoformat()
        await db.save_food_fact(fact=fact, created_at=created_at)
        return fact

    raise RuntimeError("Не удалось сгенерировать уникальный факт")


@ai_router.message(Command("fact"))
@ai_router.message(F.text == MENU_FACT)
async def fact_command(
    message: Message,
    db: Database,
    settings: Settings,
    ai_client: OpenRouterClient,
) -> None:
    """Генерирует и отправляет факт о еде по запросу пользователя.

    Args:
        message: Входящее сообщение Telegram.
        db: Экземпляр базы данных.
        settings: Настройки приложения.
        ai_client: Клиент OpenRouter.

    Returns:
        None.
    """
    latest = await db.get_latest_food_fact()
    latest_fact = str((latest or {}).get("fact") or "").strip()

    if not ai_client.enabled:
        if latest_fact:
            await message.answer(f"🍔 Факт:\n\n{latest_fact}")
            return
        await message.answer("AI не настроен. Добавьте OPENROUTER_API_KEY в .env.")
        logger.warning("Запрошен /fact без настроенного OPENROUTER_API_KEY")
        return

    try:
        fact_text = await _generate_and_save_food_fact(db, settings, ai_client)
        logger.info("Сгенерирован уникальный факт по команде /fact")
    except Exception:
        logger.exception("Не удалось сгенерировать уникальный факт")
        if latest_fact:
            await message.answer(
                "Новый факт сейчас недоступен. Показываю последний сохранённый.\n\n"
                f"🍔 Факт:\n\n{latest_fact}"
            )
            return
        await message.answer("Не удалось сгенерировать факт. Попробуйте позже.")
        return

    await message.answer(f"🍔 Факт:\n\n{fact_text}")


@ai_router.message(Command("ai"))
@ai_router.message(F.text == MENU_AI_ENABLE)
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
@ai_router.message(F.text == MENU_AI_DISABLE)
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

    hint = _operational_hint(user_text)
    if hint:
        await message.answer(hint)
        return

    request_messages = _build_text_ai_messages(user_text)
    logger.info("AI-запрос от пользователя %s", message.from_user.id)
    try:
        ai_reply = await ai_client.chat(request_messages)
    except Exception:
        logger.exception("Ошибка AI-запроса для пользователя %s", message.from_user.id)
        await message.answer("Не удалось получить ответ от AI. Попробуйте ещё раз.")
        return

    ai_reply = _sanitize_ai_reply(ai_reply)
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
