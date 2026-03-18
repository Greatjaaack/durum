from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.checklists import checklist_total_items
from app.config import Settings
from app.db import Database

logger = logging.getLogger(__name__)

# Текст напоминания о контроле мяса и соусов.
MEAT_AND_SAUCES_REMINDER_TEXT = "Проверьте мясо и соусы."

# Текст напоминания о необходимости заказа продукции.
PRODUCT_ORDER_REMINDER_TEXT = "Проверьте необходимость заказа продукции."

# Сообщение о незавершённом чек-листе закрытия смены.
INCOMPLETE_CLOSE_CHECKLIST_TEXT = "⚠️ Смена ещё не закрыта\n\nЧек-лист закрытия смены не завершён."

# Шаг часов для периодического напоминания о мясе и соусах.
MEAT_AND_SAUCES_REMINDER_HOUR_STEP = "*/2"

# Час ежедневной проверки заказа продукции.
PRODUCT_ORDER_REMINDER_HOUR = 19

# Минута ежедневной проверки заказа продукции.
PRODUCT_ORDER_REMINDER_MINUTE = 0

# Час дедлайна открытия смены для уведомления владельца.
OPENING_DEADLINE_HOUR = 10

# Минута дедлайна открытия смены для уведомления владельца.
OPENING_DEADLINE_MINUTE = 0

# Форматированная подпись дедлайна открытия смены.
OPENING_DEADLINE_LABEL = f"{OPENING_DEADLINE_HOUR:02d}:{OPENING_DEADLINE_MINUTE:02d}"

# Час первого напоминания о незавершённом закрытии.
CLOSE_CHECKLIST_FIRST_REMINDER_HOUR = 23

# Минута первого напоминания о незавершённом закрытии.
CLOSE_CHECKLIST_FIRST_REMINDER_MINUTE = 30

# Час второго напоминания о незавершённом закрытии.
CLOSE_CHECKLIST_SECOND_REMINDER_HOUR = 23

# Минута второго напоминания о незавершённом закрытии.
CLOSE_CHECKLIST_SECOND_REMINDER_MINUTE = 45

def setup_scheduler(
    bot: Bot,
    db: Database,
    settings: Settings,
) -> AsyncIOScheduler:
    """Создаёт и настраивает планировщик фоновых задач.

    Args:
        bot: Экземпляр Telegram-бота.
        db: Экземпляр базы данных.
        settings: Настройки приложения.

    Returns:
        Настроенный планировщик APScheduler.
    """
    timezone = ZoneInfo(settings.timezone)
    scheduler = AsyncIOScheduler(timezone=timezone)
    close_total_items = checklist_total_items("close")

    async def _send_to_active_employees(text: str) -> None:
        """Отправляет уведомление всем сотрудникам с открытой сменой.

        Args:
            text: Текст уведомления.

        Returns:
            None.
        """
        employee_ids = await db.get_active_employee_ids()
        for employee_id in employee_ids:
            try:
                await bot.send_message(employee_id, text)
            except Exception:
                logger.exception("Failed to send reminder to employee %s", employee_id)

    async def remind_meat_and_sauces() -> None:
        """Напоминает проверить мясо и соусы.

        Args:
            Нет параметров.

        Returns:
            None.
        """
        await _send_to_active_employees(MEAT_AND_SAUCES_REMINDER_TEXT)

    async def remind_product_order() -> None:
        """Напоминает проверить необходимость заказа продукции.

        Args:
            Нет параметров.

        Returns:
            None.
        """
        await _send_to_active_employees(PRODUCT_ORDER_REMINDER_TEXT)

    async def notify_if_shift_not_opened() -> None:
        """Уведомляет владельца, если смена не открыта к 10:00.

        Args:
            Нет параметров.

        Returns:
            None.
        """
        shift_date = datetime.now(timezone).date().isoformat()
        has_opened = await db.has_shift_opened_on(shift_date)
        if has_opened:
            return
        try:
            await bot.send_message(
                settings.owner_id,
                f"Смена не открыта до {OPENING_DEADLINE_LABEL} ({shift_date}).",
            )
        except Exception:
            logger.exception("Failed to notify owner about unopened shift")

    async def _has_incomplete_close_checklist() -> bool:
        """Проверяет наличие незавершённого чек-листа закрытия.

        Args:
            Нет параметров.

        Returns:
            True, если найден незавершённый чек-лист.
        """
        active_shifts = await db.get_active_shifts()
        if not active_shifts:
            return False

        for shift in active_shifts:
            shift_id = int(shift["id"])
            close_state = await db.get_checklist_state(
                shift_id=shift_id,
                checklist_type="close",
            )
            done_items = len(close_state["completed"]) if close_state else 0
            if done_items < close_total_items:
                return True
        return False

    async def remind_incomplete_close_checklist() -> None:
        """Отправляет уведомление о незавершённом закрытии смены.

        Args:
            Нет параметров.

        Returns:
            None.
        """
        if not await _has_incomplete_close_checklist():
            return

        try:
            await bot.send_message(
                settings.work_chat_id,
                INCOMPLETE_CLOSE_CHECKLIST_TEXT,
            )
        except Exception:
            logger.exception("Failed to send close checklist reminder to work chat")

    scheduler.add_job(
        remind_meat_and_sauces,
        trigger=CronTrigger(
            minute=0,
            hour=MEAT_AND_SAUCES_REMINDER_HOUR_STEP,
            timezone=timezone,
        ),
        id="meat_and_sauces_reminder",
        replace_existing=True,
    )
    scheduler.add_job(
        remind_product_order,
        trigger=CronTrigger(
            hour=PRODUCT_ORDER_REMINDER_HOUR,
            minute=PRODUCT_ORDER_REMINDER_MINUTE,
            timezone=timezone,
        ),
        id="product_order_reminder",
        replace_existing=True,
    )
    scheduler.add_job(
        notify_if_shift_not_opened,
        trigger=CronTrigger(
            hour=OPENING_DEADLINE_HOUR,
            minute=OPENING_DEADLINE_MINUTE,
            timezone=timezone,
        ),
        id="opening_deadline_check",
        replace_existing=True,
    )
    scheduler.add_job(
        remind_incomplete_close_checklist,
        trigger=CronTrigger(
            hour=CLOSE_CHECKLIST_FIRST_REMINDER_HOUR,
            minute=CLOSE_CHECKLIST_FIRST_REMINDER_MINUTE,
            timezone=timezone,
        ),
        id="close_checklist_reminder_2330",
        replace_existing=True,
    )
    scheduler.add_job(
        remind_incomplete_close_checklist,
        trigger=CronTrigger(
            hour=CLOSE_CHECKLIST_SECOND_REMINDER_HOUR,
            minute=CLOSE_CHECKLIST_SECOND_REMINDER_MINUTE,
            timezone=timezone,
        ),
        id="close_checklist_reminder_2345",
        replace_existing=True,
    )
    return scheduler
