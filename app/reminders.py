from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import Settings
from db import Database

logger = logging.getLogger(__name__)


def setup_scheduler(bot: Bot, db: Database, settings: Settings) -> AsyncIOScheduler:
    timezone = ZoneInfo(settings.timezone)
    scheduler = AsyncIOScheduler(timezone=timezone)

    async def _send_to_active_employees(text: str) -> None:
        employee_ids = await db.get_active_employee_ids()
        for employee_id in employee_ids:
            try:
                await bot.send_message(employee_id, text)
            except Exception:
                logger.exception("Failed to send reminder to employee %s", employee_id)

    async def remind_meat_and_sauces() -> None:
        await _send_to_active_employees("Проверьте мясо и соусы.")

    async def remind_product_order() -> None:
        await _send_to_active_employees("Проверьте необходимость заказа продукции.")

    async def notify_if_shift_not_opened() -> None:
        shift_date = datetime.now(timezone).date().isoformat()
        has_opened = await db.has_shift_opened_on(shift_date)
        if has_opened:
            return
        try:
            await bot.send_message(
                settings.owner_id,
                f"Смена не открыта до 10:00 ({shift_date}).",
            )
        except Exception:
            logger.exception("Failed to notify owner about unopened shift")

    scheduler.add_job(
        remind_meat_and_sauces,
        trigger=CronTrigger(minute=0, hour="*/2", timezone=timezone),
        id="meat_and_sauces_reminder",
        replace_existing=True,
    )
    scheduler.add_job(
        remind_product_order,
        trigger=CronTrigger(hour=19, minute=0, timezone=timezone),
        id="product_order_reminder",
        replace_existing=True,
    )
    scheduler.add_job(
        notify_if_shift_not_opened,
        trigger=CronTrigger(hour=10, minute=0, timezone=timezone),
        id="opening_deadline_check",
        replace_existing=True,
    )

    return scheduler
