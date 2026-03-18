from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app.ai_client import OpenRouterClient
from app.config import load_settings
from app.db import Database
from app.handlers import router
from app.logging_setup import configure_logging
from app.reminders import setup_scheduler


async def set_commands(bot: Bot) -> None:
    """Регистрирует список команд бота в Telegram.

    Args:
        bot: Экземпляр Telegram-бота.

    Returns:
        None.
    """
    commands = [
        BotCommand(command="open", description="Открыть смену"),
        BotCommand(command="mid", description="Чек-лист ведения смены"),
        BotCommand(command="close", description="Закрыть смену"),
        BotCommand(command="order_products", description="Заказ продукции"),
        BotCommand(command="order_supplies", description="Заказ хозтоваров"),
        BotCommand(command="stock", description="Ввод остатков"),
        BotCommand(command="problem", description="Сообщение о проблеме"),
        BotCommand(command="report", description="Отчёт за дату"),
        BotCommand(command="reports", description="Интерактивные отчёты"),
        BotCommand(command="fact", description="Последний факт о еде"),
        BotCommand(command="ai", description="Включить AI режим"),
        BotCommand(command="stop", description="Выключить AI режим"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
    ]
    await bot.set_my_commands(commands)


async def main() -> None:
    """Запускает приложение и стартует polling Telegram-бота.

    Args:
        Нет параметров.

    Returns:
        None.
    """
    settings = load_settings()
    configure_logging(settings.log_dir, settings.timezone)
    db = Database(settings.db_path)
    await db.init()
    ai_client = OpenRouterClient(
        api_key=settings.openrouter_api_key,
        model=settings.ai_model,
        timeout_sec=settings.ai_request_timeout_sec,
    )

    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)

    dispatcher["db"] = db
    dispatcher["settings"] = settings
    dispatcher["ai_client"] = ai_client

    scheduler = setup_scheduler(bot, db, settings)
    scheduler.start()
    try:
        await set_commands(bot)
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
