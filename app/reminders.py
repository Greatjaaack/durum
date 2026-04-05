from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.checklist.ui import checklist_total_items
from app.config import Settings
from app.db import Database

logger = logging.getLogger(__name__)

# Текст напоминания о проверке хозов и чистоты.
SUPPLIES_AND_CLEANLINESS_REMINDER_TEXT = (
    "Проверьте все хозы: каждую упаковку, а также чистоту в зале и на кухне."
)

# Текст напоминания о необходимости заказа продукции.
PRODUCT_ORDER_REMINDER_TEXT = "Проверьте необходимость заказа продукции."

# Сообщение о незавершённом чек-листе закрытия смены.
INCOMPLETE_CLOSE_CHECKLIST_TEXT = "⚠️ Смена ещё не закрыта\n\nЧек-лист закрытия смены не завершён."

# Шаг часов для периодического напоминания о хозах и чистоте.
SUPPLIES_AND_CLEANLINESS_REMINDER_HOUR_STEP = "*/2"

# Час ежедневной проверки заказа продукции.
PRODUCT_ORDER_REMINDER_HOUR = 19

# Минута ежедневной проверки заказа продукции.
PRODUCT_ORDER_REMINDER_MINUTE = 0

# Смещение (в минутах) для дедлайна открытия смены относительно времени старта.
OPENING_DEADLINE_OFFSET_MIN = -60

# Смещение (в минутах) для первого напоминания после времени закрытия смены.
CLOSE_CHECKLIST_FIRST_REMINDER_OFFSET_MIN = 90

# Смещение (в минутах) для второго напоминания после времени закрытия смены.
CLOSE_CHECKLIST_SECOND_REMINDER_OFFSET_MIN = 105


def _time_with_offset(
    base_time: time,
    *,
    offset_minutes: int,
    timezone: ZoneInfo,
) -> tuple[int, int]:
    """Считает часы и минуты для cron-триггера со смещением от базового времени.

    Args:
        base_time: Базовое время.
        offset_minutes: Смещение в минутах.
        timezone: Часовой пояс приложения.

    Returns:
        Кортеж (hour, minute).
    """
    anchor = datetime.combine(datetime.now(timezone).date(), base_time, tzinfo=timezone)
    shifted = anchor + timedelta(minutes=offset_minutes)
    return shifted.hour, shifted.minute


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
    open_total_items = checklist_total_items("open")
    close_total_items = checklist_total_items("close")
    opening_deadline_hour, opening_deadline_minute = _time_with_offset(
        settings.shift_open_time,
        offset_minutes=OPENING_DEADLINE_OFFSET_MIN,
        timezone=timezone,
    )
    opening_deadline_label = f"{opening_deadline_hour:02d}:{opening_deadline_minute:02d}"
    close_first_hour, close_first_minute = _time_with_offset(
        settings.shift_close_time,
        offset_minutes=CLOSE_CHECKLIST_FIRST_REMINDER_OFFSET_MIN,
        timezone=timezone,
    )
    close_second_hour, close_second_minute = _time_with_offset(
        settings.shift_close_time,
        offset_minutes=CLOSE_CHECKLIST_SECOND_REMINDER_OFFSET_MIN,
        timezone=timezone,
    )

    def _current_shift_date() -> str:
        """Возвращает текущую дату смены в часовом поясе приложения."""
        return datetime.now(timezone).date().isoformat()

    async def _get_active_shifts_for_current_date() -> list[dict[str, object]]:
        """Возвращает активные смены только за текущую дату."""
        shift_date = _current_shift_date()
        return await db.get_active_shifts(shift_date=shift_date)

    async def _send_to_active_employees(text: str) -> None:
        """Отправляет уведомление всем сотрудникам с открытой сменой.

        Args:
            text: Текст уведомления.

        Returns:
            None.
        """
        active_shifts = await _get_active_shifts_for_current_date()
        employee_ids: set[int] = set()
        for shift in active_shifts:
            try:
                employee_ids.add(int(shift["employee_id"]))
            except (KeyError, TypeError, ValueError):
                continue

        if not employee_ids:
            logger.debug("Reminder skipped — no active employees today")
            return

        logger.debug("Sending reminder to %d employee(s): %s", len(employee_ids), sorted(employee_ids))
        for employee_id in sorted(employee_ids):
            try:
                await bot.send_message(employee_id, text)
            except Exception:
                logger.exception("Failed to send reminder to employee %s", employee_id)

    async def remind_supplies_and_cleanliness() -> None:
        """Напоминает проверить хозы и чистоту.

        Args:
            Нет параметров.

        Returns:
            None.
        """
        logger.debug("Scheduler job: supplies_and_cleanliness_reminder")
        await _send_to_active_employees(SUPPLIES_AND_CLEANLINESS_REMINDER_TEXT)

    async def remind_product_order() -> None:
        """Напоминает проверить необходимость заказа продукции.

        Args:
            Нет параметров.

        Returns:
            None.
        """
        logger.debug("Scheduler job: product_order_reminder")
        await _send_to_active_employees(PRODUCT_ORDER_REMINDER_TEXT)

    async def notify_if_shift_not_opened() -> None:
        """Уведомляет владельца, если смена не открыта к дедлайну старта.

        Args:
            Нет параметров.

        Returns:
            None.
        """
        logger.debug("Scheduler job: opening_deadline_check")
        shift_date = datetime.now(timezone).date().isoformat()
        has_opened = await db.has_shift_opened_on(
            shift_date,
            open_checklist_total=open_total_items,
        )
        if has_opened:
            logger.debug("Opening deadline check: shift already opened on %s", shift_date)
            return
        try:
            await bot.send_message(
                settings.owner_id,
                f"Смена не открыта до {opening_deadline_label} ({shift_date}).",
            )
            logger.info("Owner notified: shift not opened by %s on %s", opening_deadline_label, shift_date)
        except Exception:
            logger.exception("Failed to notify owner about unopened shift")

    async def _has_incomplete_close_checklist() -> bool:
        """Проверяет наличие незавершённого чек-листа закрытия.

        Args:
            Нет параметров.

        Returns:
            True, если найден незавершённый чек-лист.
        """
        active_shifts = await _get_active_shifts_for_current_date()
        if not active_shifts:
            return False

        for shift in active_shifts:
            shift_id = int(shift["id"])
            close_state = await db.get_checklist_state(
                shift_id=shift_id,
                checklist_type="close",
            )
            done_items = len(close_state.get("completed", [])) if close_state else 0
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
        logger.debug("Scheduler job: close_checklist_reminder")
        if not await _has_incomplete_close_checklist():
            logger.debug("Close checklist reminder: all shifts closed, skipping")
            return

        try:
            await bot.send_message(
                settings.work_chat_id,
                INCOMPLETE_CLOSE_CHECKLIST_TEXT,
            )
            logger.info("Work chat notified: incomplete close checklist")
        except Exception:
            logger.exception("Failed to send close checklist reminder to work chat")

    scheduler.add_job(
        remind_supplies_and_cleanliness,
        trigger=CronTrigger(
            minute=0,
            hour=SUPPLIES_AND_CLEANLINESS_REMINDER_HOUR_STEP,
            timezone=timezone,
        ),
        id="supplies_and_cleanliness_reminder",
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
            hour=opening_deadline_hour,
            minute=opening_deadline_minute,
            timezone=timezone,
        ),
        id="opening_deadline_check",
        replace_existing=True,
    )
    scheduler.add_job(
        remind_incomplete_close_checklist,
        trigger=CronTrigger(
            hour=close_first_hour,
            minute=close_first_minute,
            timezone=timezone,
        ),
        id="close_checklist_reminder_first",
        replace_existing=True,
    )
    scheduler.add_job(
        remind_incomplete_close_checklist,
        trigger=CronTrigger(
            hour=close_second_hour,
            minute=close_second_minute,
            timezone=timezone,
        ),
        id="close_checklist_reminder_second",
        replace_existing=True,
    )
    return scheduler
