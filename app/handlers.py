from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from checklists import CHECKLISTS, build_checklist_keyboard, build_checklist_text
from config import Settings
from db import Database
from orders import build_order_keyboard, resolve_order_item
from reports import build_daily_report

logger = logging.getLogger(__name__)

router = Router()

STOCK_REFERENCE_LEVELS = {
    "мясо": 20.0,
    "лаваш": 200.0,
    "картофель": 30.0,
}
STOCK_ALERT_THRESHOLD = 0.30


class OpenShiftStates(StatesGroup):
    waiting_meat_start = State()


class CloseShiftStates(StatesGroup):
    waiting_revenue = State()
    waiting_photo = State()
    waiting_meat_end = State()
    waiting_lavash_end = State()


class OrderStates(StatesGroup):
    waiting_quantity = State()


class StockStates(StatesGroup):
    waiting_meat = State()
    waiting_lavash = State()
    waiting_potato = State()


class ProblemStates(StatesGroup):
    waiting_text = State()


def _employee_name(message: Message) -> str:
    user = message.from_user
    if not user:
        return "Unknown employee"
    if user.username:
        return f"@{user.username}"
    return user.full_name


def _now(settings: Settings) -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


def _parse_non_negative_number(raw: str) -> float | None:
    try:
        value = float(raw.replace(",", ".").strip())
    except ValueError:
        return None
    if value < 0:
        return None
    return value


def _parse_positive_number(raw: str) -> float | None:
    value = _parse_non_negative_number(raw)
    if value is None or value <= 0:
        return None
    return value


async def _notify_owner(bot: Bot, settings: Settings, text: str) -> None:
    try:
        await bot.send_message(settings.owner_id, text)
    except Exception:
        logger.exception("Failed to notify owner")


async def _start_checklist(
    message: Message,
    state: FSMContext,
    checklist_type: str,
) -> None:
    completed: set[int] = set()
    await state.update_data({f"checklist_{checklist_type}_done": []})
    await message.answer(
        build_checklist_text(checklist_type, completed),
        reply_markup=build_checklist_keyboard(checklist_type, completed),
    )


@router.message(Command("start"))
async def start_command(message: Message) -> None:
    text = (
        "Команды бота:\n"
        "/open — открыть смену\n"
        "/mid — чек-лист в течение смены\n"
        "/close — закрыть смену\n\n"
        "/order_products — заказ продукции\n"
        "/order_supplies — заказ хозтоваров\n\n"
        "/stock — ввод остатков\n"
        "/problem — сообщение о проблеме\n"
        "/report YYYY-MM-DD — отчёт за дату"
    )
    await message.answer(text)


@router.message(Command("cancel"))
async def cancel_state(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Текущее действие отменено.")


@router.message(Command("open"))
async def open_shift(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    if not message.from_user:
        return

    active_shift = await db.get_active_shift(message.from_user.id)
    if active_shift:
        await message.answer("У вас уже есть открытая смена. Сначала закройте её командой /close.")
        return

    await state.clear()
    now = _now(settings)
    await db.create_shift(
        employee=_employee_name(message),
        employee_id=message.from_user.id,
        shift_date=now.date().isoformat(),
        open_time=now.isoformat(timespec="minutes"),
    )
    await _start_checklist(message, state, "open")


@router.message(Command("mid"))
async def mid_shift(
    message: Message,
    state: FSMContext,
    db: Database,
) -> None:
    if not message.from_user:
        return

    active_shift = await db.get_active_shift(message.from_user.id)
    if not active_shift:
        await message.answer("Сначала откройте смену командой /open.")
        return

    await _start_checklist(message, state, "mid")


@router.message(Command("close"))
async def close_shift_start(
    message: Message,
    state: FSMContext,
    db: Database,
) -> None:
    if not message.from_user:
        return

    active_shift = await db.get_active_shift(message.from_user.id)
    if not active_shift:
        await message.answer("Нет открытой смены. Откройте её командой /open.")
        return

    await _start_checklist(message, state, "close")


@router.callback_query(F.data.startswith("checklist:"))
async def checklist_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not callback.message:
        return

    _, checklist_type, index_raw = callback.data.split(":")
    index = int(index_raw)

    key = f"checklist_{checklist_type}_done"
    state_data = await state.get_data()
    completed = set(state_data.get(key, []))

    if index in completed:
        completed.remove(index)
    else:
        completed.add(index)

    await state.update_data({key: sorted(completed)})

    checklist_len = len(CHECKLISTS[checklist_type])
    checklist_text = build_checklist_text(checklist_type, completed)

    if len(completed) == checklist_len:
        await callback.message.edit_text(checklist_text)
        await callback.answer("Чек-лист завершён.")
        if checklist_type == "open":
            await state.set_state(OpenShiftStates.waiting_meat_start)
            await callback.message.answer("Чек-лист завершён.\nСколько мяса сейчас (кг)?")
        elif checklist_type == "close":
            await state.set_state(CloseShiftStates.waiting_revenue)
            await callback.message.answer("Чек-лист завершён.\nВведите выручку за смену (₽).")
        else:
            await callback.message.answer("Чек-лист завершён.")
        return

    await callback.message.edit_text(
        checklist_text,
        reply_markup=build_checklist_keyboard(checklist_type, completed),
    )
    await callback.answer()


@router.message(OpenShiftStates.waiting_meat_start)
async def save_open_meat_start(
    message: Message,
    state: FSMContext,
    db: Database,
) -> None:
    if not message.from_user or not message.text:
        return

    meat_start = _parse_non_negative_number(message.text)
    if meat_start is None:
        await message.answer("Введите корректное число (кг), например: 12.5")
        return

    active_shift = await db.get_active_shift(message.from_user.id)
    if not active_shift:
        await state.clear()
        await message.answer("Не удалось найти открытую смену. Откройте смену снова командой /open.")
        return

    await db.set_shift_meat_start(active_shift["id"], meat_start)
    await state.clear()
    await message.answer("Смена открыта. Значение meat_start сохранено.")


@router.message(Command("order_products"))
async def order_products_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Выберите продукцию для заказа:",
        reply_markup=build_order_keyboard("products"),
    )


@router.message(Command("order_supplies"))
async def order_supplies_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Выберите хозтовар для заказа:",
        reply_markup=build_order_keyboard("supplies"),
    )


@router.callback_query(F.data.startswith("order:"))
async def order_item_selected(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not callback.message:
        return

    _, order_type, item_key = callback.data.split(":")
    item_title = resolve_order_item(order_type, item_key)

    if not item_title:
        await callback.answer("Неизвестный товар", show_alert=True)
        return

    await state.set_state(OrderStates.waiting_quantity)
    await state.update_data(order_type=order_type, order_item=item_title)
    await callback.answer()
    await callback.message.answer(f"Введите количество для «{item_title}».")


@router.message(OrderStates.waiting_quantity)
async def save_order_quantity(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
    bot: Bot,
) -> None:
    if not message.text or not message.from_user:
        return

    quantity = _parse_positive_number(message.text)
    if quantity is None:
        await message.answer("Введите количество числом больше нуля.")
        return

    state_data = await state.get_data()
    order_type = state_data.get("order_type")
    item = state_data.get("order_item")
    if not order_type or not item:
        await state.clear()
        await message.answer("Не удалось определить товар. Повторите команду заказа.")
        return

    now = _now(settings)
    employee = _employee_name(message)
    await db.save_order(
        order_type=order_type,
        item=item,
        quantity=quantity,
        employee=employee,
        employee_id=message.from_user.id,
        order_date=now.date().isoformat(),
        order_time=now.time().replace(microsecond=0).isoformat(),
    )

    type_text = "продукция" if order_type == "products" else "хозтовары"
    await _notify_owner(
        bot,
        settings,
        (
            "Новый заказ\n"
            f"Тип: {type_text}\n"
            f"Товар: {item}\n"
            f"Количество: {quantity}\n"
            f"Сотрудник: {employee}"
        ),
    )

    await state.clear()
    await message.answer("Заказ сохранён и отправлен владельцу.")


@router.message(Command("stock"))
async def stock_start(message: Message, state: FSMContext) -> None:
    await state.set_state(StockStates.waiting_meat)
    await state.update_data(stock={})
    await message.answer("Введите остаток мяса (кг):")


@router.message(StockStates.waiting_meat)
async def stock_meat(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    value = _parse_non_negative_number(message.text)
    if value is None:
        await message.answer("Введите корректное число для мяса (кг).")
        return
    data = await state.get_data()
    stock = data.get("stock", {})
    stock["мясо"] = value
    await state.update_data(stock=stock)
    await state.set_state(StockStates.waiting_lavash)
    await message.answer("Введите остаток лаваша (шт):")


@router.message(StockStates.waiting_lavash)
async def stock_lavash(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    value = _parse_non_negative_number(message.text)
    if value is None:
        await message.answer("Введите корректное число для лаваша (шт).")
        return
    data = await state.get_data()
    stock = data.get("stock", {})
    stock["лаваш"] = value
    await state.update_data(stock=stock)
    await state.set_state(StockStates.waiting_potato)
    await message.answer("Введите остаток картофеля (кг):")


@router.message(StockStates.waiting_potato)
async def stock_potato(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
    bot: Bot,
) -> None:
    if not message.text or not message.from_user:
        return

    value = _parse_non_negative_number(message.text)
    if value is None:
        await message.answer("Введите корректное число для картофеля (кг).")
        return

    data = await state.get_data()
    stock = data.get("stock", {})
    stock["картофель"] = value

    now = _now(settings)
    stock_date = now.date().isoformat()
    stock_time = now.time().replace(microsecond=0).isoformat()
    employee = _employee_name(message)

    for item, quantity in stock.items():
        await db.save_stock(
            item=item,
            quantity=quantity,
            stock_date=stock_date,
            employee=employee,
            employee_id=message.from_user.id,
            stock_time=stock_time,
        )

    alerts = []
    for item, quantity in stock.items():
        reference = STOCK_REFERENCE_LEVELS[item]
        if quantity < reference * STOCK_ALERT_THRESHOLD:
            alerts.append(f"{item}: {quantity}")

    if alerts:
        await _notify_owner(
            bot,
            settings,
            (
                "⚠️ Низкий остаток:\n"
                + "\n".join(alerts)
                + f"\nСотрудник: {employee}"
            ),
        )

    await state.clear()
    await message.answer("Остатки сохранены.")


@router.message(Command("problem"))
async def problem_start(message: Message, state: FSMContext) -> None:
    await state.set_state(ProblemStates.waiting_text)
    await message.answer("Опишите проблему одним сообщением.")


@router.message(ProblemStates.waiting_text)
async def problem_forward(
    message: Message,
    state: FSMContext,
    settings: Settings,
    bot: Bot,
) -> None:
    if not message.from_user:
        return
    if not message.text:
        await message.answer("Отправьте текстовое описание проблемы.")
        return

    employee = _employee_name(message)
    try:
        await bot.send_message(
            settings.owner_id,
            f"Проблема от сотрудника: {employee}",
        )
        await bot.forward_message(
            chat_id=settings.owner_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
    except Exception:
        logger.exception("Failed to forward problem message, sending plain text fallback")
        await _notify_owner(
            bot,
            settings,
            (
                "Проблема от сотрудника\n"
                f"Сотрудник: {employee}\n"
                f"Сообщение: {message.text}"
            ),
        )
    await state.clear()
    await message.answer("Проблема отправлена владельцу.")


@router.message(CloseShiftStates.waiting_revenue)
async def close_revenue(message: Message, state: FSMContext) -> None:
    if not message.text:
        return

    revenue = _parse_non_negative_number(message.text)
    if revenue is None:
        await message.answer("Введите выручку числом, например: 25430.5")
        return

    await state.update_data(close_revenue=revenue)
    await state.set_state(CloseShiftStates.waiting_photo)
    await message.answer("Отправьте фото кухни.")


@router.message(CloseShiftStates.waiting_photo, F.photo)
async def close_photo_ok(message: Message, state: FSMContext) -> None:
    if not message.photo:
        return
    photo_file_id = message.photo[-1].file_id
    await state.update_data(close_photo=photo_file_id)
    await state.set_state(CloseShiftStates.waiting_meat_end)
    await message.answer("Введите остаток мяса (кг):")


@router.message(CloseShiftStates.waiting_photo)
async def close_photo_invalid(message: Message) -> None:
    await message.answer("Нужно отправить фото кухни.")


@router.message(CloseShiftStates.waiting_meat_end)
async def close_meat_end(message: Message, state: FSMContext) -> None:
    if not message.text:
        return

    meat_end = _parse_non_negative_number(message.text)
    if meat_end is None:
        await message.answer("Введите корректное число для остатка мяса (кг).")
        return

    await state.update_data(close_meat_end=meat_end)
    await state.set_state(CloseShiftStates.waiting_lavash_end)
    await message.answer("Введите остаток лаваша (шт):")


@router.message(CloseShiftStates.waiting_lavash_end)
async def close_finish(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
    bot: Bot,
) -> None:
    if not message.text or not message.from_user:
        return

    lavash_end = _parse_non_negative_number(message.text)
    if lavash_end is None:
        await message.answer("Введите корректное число для остатка лаваша (шт).")
        return

    shift = await db.get_active_shift(message.from_user.id)
    if not shift:
        await state.clear()
        await message.answer("Открытая смена не найдена. Начните снова с /open.")
        return

    data = await state.get_data()
    revenue = float(data.get("close_revenue", 0))
    photo_file_id = data.get("close_photo")
    meat_end = float(data.get("close_meat_end", 0))
    now = _now(settings)

    meat_used = await db.close_shift(
        shift_id=shift["id"],
        close_time=now.isoformat(timespec="minutes"),
        revenue=revenue,
        photo=photo_file_id,
        meat_end=meat_end,
        lavash_end=lavash_end,
    )

    employee = _employee_name(message)
    stock_date = now.date().isoformat()
    stock_time = now.time().replace(microsecond=0).isoformat()
    await db.save_stock(
        item="мясо",
        quantity=meat_end,
        stock_date=stock_date,
        employee=employee,
        employee_id=message.from_user.id,
        stock_time=stock_time,
    )
    await db.save_stock(
        item="лаваш",
        quantity=lavash_end,
        stock_date=stock_date,
        employee=employee,
        employee_id=message.from_user.id,
        stock_time=stock_time,
    )

    await _notify_owner(
        bot,
        settings,
        (
            "Смена закрыта\n"
            f"Сотрудник: {employee}\n"
            f"Выручка: {revenue}\n"
            f"Расход мяса: {meat_used if meat_used is not None else 'н/д'}\n"
            f"Остаток мяса: {meat_end}\n"
            f"Остаток лаваша: {lavash_end}"
        ),
    )

    await state.clear()
    await message.answer("Смена закрыта. Данные сохранены.")


@router.message(Command("report"))
async def report_for_date(
    message: Message,
    command: CommandObject,
    db: Database,
) -> None:
    date_arg = (command.args or "").strip()
    if not date_arg:
        await message.answer("Использование: /report YYYY-MM-DD")
        return

    try:
        report_text = await build_daily_report(db, date_arg)
    except ValueError:
        await message.answer("Неверный формат даты. Используйте YYYY-MM-DD.")
        return

    await message.answer(report_text)
