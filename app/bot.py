from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app.config import Settings, load_settings
from app.db import Database
from app.handlers import router
from app.logging_setup import configure_logging
from app.reminders import setup_scheduler


logger = logging.getLogger(__name__)


def _create_bot(settings: Settings) -> Bot:
    """Создаёт Bot с прокси-сессией, если прокси задан в настройках."""
    if settings.bot_proxy_url:
        logger.info("Telegram bot will use proxy from BOT_PROXY_URL")
        session = AiohttpSession(proxy=settings.bot_proxy_url)
        return Bot(token=settings.bot_token, session=session)
    return Bot(token=settings.bot_token)


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
    ]
    try:
        await bot.set_my_commands(commands)
    except TelegramNetworkError:
        # Команды можно выставить позже, это не должно останавливать запуск бота.
        logger.warning("Failed to set bot commands due to Telegram network error")


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
    today = datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
    await db.init(today=today)

    bot = _create_bot(settings)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)

    dispatcher["db"] = db
    dispatcher["settings"] = settings

    scheduler = setup_scheduler(bot, db, settings)
    scheduler.start()
    try:
        while True:
            try:
                await set_commands(bot)
                await dispatcher.start_polling(bot)
                break
            except TelegramNetworkError:
                logger.warning("Polling startup failed due to Telegram network error; retrying in 5s")
                await asyncio.sleep(5)
    finally:
        scheduler.shutdown(wait=True)
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
