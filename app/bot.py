from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from config import load_settings
from db import Database
from handlers import router
from reminders import setup_scheduler


async def set_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="open", description="Открыть смену"),
        BotCommand(command="mid", description="Чек-лист в течение смены"),
        BotCommand(command="close", description="Закрыть смену"),
        BotCommand(command="order_products", description="Заказ продукции"),
        BotCommand(command="order_supplies", description="Заказ хозтоваров"),
        BotCommand(command="stock", description="Ввод остатков"),
        BotCommand(command="problem", description="Сообщение о проблеме"),
        BotCommand(command="report", description="Отчёт за дату"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
    ]
    await bot.set_my_commands(commands)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    settings = load_settings()
    db = Database(settings.db_path)
    await db.init()

    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)

    dispatcher["db"] = db
    dispatcher["settings"] = settings

    scheduler = setup_scheduler(bot, db, settings)
    scheduler.start()

    await set_commands(bot)
    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
