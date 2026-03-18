from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.db import Database
from app.handlers.constants import (
    MENU_ORDER_PRODUCTS,
    MENU_ORDER_SUPPLIES,
    ORDER_MESSAGE_CHAT_KEY,
    ORDER_MESSAGE_ID_KEY,
    ORDER_PENDING_ITEM_KEY,
    ORDER_QUANTITIES_KEY,
    ORDER_SECTION_KEY,
    ORDER_SELECTED_KEY,
    ORDER_TYPE_KEY,
)
from app.handlers.states import OrderStates
from app.handlers.utils import notify_work_chat, now_local, parse_positive_number
from app.order_catalog import (
    ORDER_TITLES,
    build_order_keyboard,
    build_order_text,
    normalize_order_section,
    order_item_meta,
    order_section_for_item,
    order_total_items,
)


logger = logging.getLogger(__name__)
order_router = Router()


def _restore_selected_indexes(
    state_data: dict[str, object],
) -> set[int]:
    """Извлекает и валидирует выбранные индексы заказа из FSM.

    Args:
        state_data: Сырые данные FSM.

    Returns:
        Множество валидных индексов пунктов.
    """
    selected: set[int] = set()
    selected_raw = state_data.get(ORDER_SELECTED_KEY, [])
    if isinstance(selected_raw, list):
        for raw in selected_raw:
            try:
                selected.add(int(raw))
            except (TypeError, ValueError):
                continue
    return selected


def _restore_quantities(
    state_data: dict[str, object],
) -> dict[str, float | str]:
    """Извлекает словарь количеств выбранных позиций из FSM.

    Args:
        state_data: Сырые данные FSM.

    Returns:
        Словарь количеств по ключу позиции.
    """
    raw = state_data.get(ORDER_QUANTITIES_KEY, {})
    if not isinstance(raw, dict):
        return {}
    return dict(raw)


def _order_selected_items(
    order_type: str,
    selected: set[int],
    quantities: dict[str, float | str],
) -> list[dict[str, float | str]]:
    """Собирает отмеченные позиции заказа.

    Args:
        order_type: Тип заказа.
        selected: Индексы отмеченных пунктов.
        quantities: Количества для заказа продуктов.

    Returns:
        Список выбранных позиций.
    """
    rows: list[dict[str, float | str]] = []
    for item_index in range(order_total_items(order_type)):
        if item_index not in selected:
            continue
        meta = order_item_meta(order_type, item_index)
        if not meta:
            continue

        key = str(meta["key"])
        if order_type == "products":
            if key not in quantities:
                continue
            quantity = quantities[key]
        else:
            quantity = 1.0

        rows.append(
            {
                "key": key,
                "title": str(meta["title"]),
                "unit": str(meta.get("unit", "")).strip(),
                "quantity": quantity,
            }
        )
    return rows


def _format_order_quantity(
    value: float | str,
) -> str:
    """Форматирует количество позиции заказа.

    Args:
        value: Значение количества.

    Returns:
        Строка количества.
    """
    if isinstance(value, str):
        return value.strip()
    return f"{float(value):.3f}".rstrip("0").rstrip(".")


def _build_order_report_text(
    order_type: str,
    rows: list[dict[str, float | str]],
) -> str:
    """Формирует итоговый текст сообщения о заказе.

    Args:
        order_type: Тип заказа.
        rows: Строки заказа.

    Returns:
        Готовый текст отчёта по заказу.
    """
    title = "📦 Заказ продуктов" if order_type == "products" else "📦 Заказ хозтоваров"
    lines = [title, ""]
    for row in rows:
        quantity_text = _format_order_quantity(row["quantity"])
        unit = str(row.get("unit", "")).strip()
        if order_type == "products":
            suffix = f" {unit}" if unit else ""
            lines.append(f"{row['title']} — {quantity_text}{suffix}")
        else:
            lines.append(f"— {row['title']}")
    return "\n".join(lines)


async def _start_order_checklist(
    message: Message,
    state: FSMContext,
    order_type: str,
) -> None:
    """Запускает интерфейс чек-листа заказа.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.
        order_type: Тип заказа.

    Returns:
        None.
    """
    selected: set[int] = set()
    active_section = 0
    quantities: dict[str, float | str] = {}
    checklist_message = await message.answer(
        build_order_text(order_type, selected, active_section),
        reply_markup=build_order_keyboard(order_type, selected, active_section, quantities),
    )
    await state.update_data(
        {
            ORDER_TYPE_KEY: order_type,
            ORDER_SELECTED_KEY: [],
            ORDER_SECTION_KEY: active_section,
            ORDER_QUANTITIES_KEY: {},
            ORDER_MESSAGE_CHAT_KEY: checklist_message.chat.id,
            ORDER_MESSAGE_ID_KEY: checklist_message.message_id,
            ORDER_PENDING_ITEM_KEY: None,
        }
    )
    logger.info("Пользователь открыл интерфейс заказа type=%s", order_type)


@order_router.message(Command("order_products"))
@order_router.message(F.text == MENU_ORDER_PRODUCTS)
async def order_products_start(
    message: Message,
    state: FSMContext,
) -> None:
    """Запускает сценарий заказа продукции.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.

    Returns:
        None.
    """
    await state.clear()
    await _start_order_checklist(message, state, "products")


@order_router.message(Command("order_supplies"))
@order_router.message(F.text == MENU_ORDER_SUPPLIES)
async def order_supplies_start(
    message: Message,
    state: FSMContext,
) -> None:
    """Запускает сценарий заказа хозтоваров.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.

    Returns:
        None.
    """
    await state.clear()
    await _start_order_checklist(message, state, "supplies")


@order_router.callback_query(F.data.startswith("orderlist:"))
async def order_checklist_callback(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    settings: Settings,
    bot: Bot,
) -> None:
    """Обрабатывает нажатия inline-кнопок в интерфейсе заказа.

    Args:
        callback: Callback-запрос Telegram.
        state: FSM-контекст пользователя.
        db: Экземпляр базы данных.
        settings: Настройки приложения.
        bot: Экземпляр Telegram-бота.

    Returns:
        None.
    """
    if not callback.data or not callback.message:
        return

    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return

    _, order_type, action, value_raw = parts
    if order_type not in ORDER_TITLES:
        await callback.answer("Неизвестный тип заказа", show_alert=True)
        return

    state_data = await state.get_data()
    selected = _restore_selected_indexes(state_data)
    quantities = _restore_quantities(state_data)

    section_raw = state_data.get(ORDER_SECTION_KEY, 0)
    try:
        active_section = normalize_order_section(order_type, int(section_raw))
    except (TypeError, ValueError):
        active_section = 0

    if action == "section":
        try:
            active_section = normalize_order_section(order_type, int(value_raw))
        except ValueError:
            await callback.answer("Некорректный блок", show_alert=True)
            return

    elif action == "item":
        try:
            item_index = int(value_raw)
        except ValueError:
            await callback.answer("Некорректный пункт", show_alert=True)
            return

        if item_index < 0 or item_index >= order_total_items(order_type):
            await callback.answer("Некорректный пункт", show_alert=True)
            return

        item_meta = order_item_meta(order_type, item_index)
        if not item_meta:
            await callback.answer("Пункт не найден", show_alert=True)
            return

        item_key = str(item_meta["key"])
        item_title = str(item_meta["title"])
        item_unit = str(item_meta.get("unit", "")).strip()
        active_section = order_section_for_item(order_type, item_index)

        if order_type == "products":
            await state.set_state(OrderStates.waiting_product_quantity)
            await state.update_data(
                {
                    ORDER_TYPE_KEY: order_type,
                    ORDER_SELECTED_KEY: sorted(selected),
                    ORDER_SECTION_KEY: active_section,
                    ORDER_QUANTITIES_KEY: quantities,
                    ORDER_MESSAGE_CHAT_KEY: callback.message.chat.id,
                    ORDER_MESSAGE_ID_KEY: callback.message.message_id,
                    ORDER_PENDING_ITEM_KEY: {
                        "item_index": item_index,
                        "item_key": item_key,
                        "item_title": item_title,
                        "item_unit": item_unit,
                    },
                }
            )
            unit_hint = f" ({item_unit})" if item_unit else ""
            await callback.answer("Введите количество")
            await callback.message.answer(f"Введите количество для «{item_title}»{unit_hint}.")
            return

        if item_index in selected:
            selected.remove(item_index)
        else:
            selected.add(item_index)

    elif action == "submit":
        rows = _order_selected_items(order_type, selected, quantities)
        if not rows:
            await callback.answer("Отметьте хотя бы одну позицию", show_alert=True)
            return

        if not callback.from_user:
            await callback.answer("Не удалось определить пользователя", show_alert=True)
            return

        now = now_local(settings)
        employee = callback.from_user.full_name
        if callback.from_user.username:
            employee = f"@{callback.from_user.username}"

        for row in rows:
            quantity = row["quantity"]
            quantity_value = (
                float(quantity)
                if not isinstance(quantity, str)
                else parse_positive_number(quantity)
            )
            if quantity_value is None:
                quantity_value = 1.0

            await db.save_order(
                order_type=order_type,
                item=str(row["title"]),
                quantity=quantity_value,
                employee=employee,
                employee_id=callback.from_user.id,
                order_date=now.date().isoformat(),
                order_time=now.time().replace(microsecond=0).isoformat(),
            )

        report_text = _build_order_report_text(order_type, rows)
        await notify_work_chat(bot, settings, report_text)

        await callback.message.edit_text(f"{ORDER_TITLES[order_type]}\n\nЗаказ отправлен в рабочий чат.")
        await callback.answer("Заказ отправлен")
        logger.info("Пользователь %s отправил заказ type=%s", callback.from_user.id, order_type)
        await state.clear()
        return
    else:
        await callback.answer()
        return

    await state.set_state(None)
    await state.update_data(
        {
            ORDER_TYPE_KEY: order_type,
            ORDER_SELECTED_KEY: sorted(selected),
            ORDER_SECTION_KEY: active_section,
            ORDER_QUANTITIES_KEY: quantities,
            ORDER_MESSAGE_CHAT_KEY: callback.message.chat.id,
            ORDER_MESSAGE_ID_KEY: callback.message.message_id,
            ORDER_PENDING_ITEM_KEY: None,
        }
    )

    await callback.message.edit_text(
        build_order_text(order_type, selected, active_section),
        reply_markup=build_order_keyboard(order_type, selected, active_section, quantities),
    )
    await callback.answer()


@order_router.message(OrderStates.waiting_product_quantity)
async def save_product_order_quantity(
    message: Message,
    state: FSMContext,
) -> None:
    """Сохраняет введённое количество для выбранной позиции заказа.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.

    Returns:
        None.
    """
    if not message.text or not message.from_user:
        return

    quantity = parse_positive_number(message.text)
    if quantity is None:
        await message.answer("Введите количество числом больше нуля.")
        return

    state_data = await state.get_data()
    pending = state_data.get(ORDER_PENDING_ITEM_KEY)
    if not isinstance(pending, dict):
        await state.set_state(None)
        await message.answer("Не удалось определить позицию. Нажмите на пункт ещё раз.")
        return

    order_type = str(state_data.get(ORDER_TYPE_KEY, "products"))
    if order_type != "products":
        await state.set_state(None)
        await message.answer("Режим заказа не определён. Запустите /order_products заново.")
        return

    item_key = str(pending.get("item_key", ""))
    item_index_raw = pending.get("item_index")
    try:
        item_index = int(item_index_raw)
    except (TypeError, ValueError):
        await state.set_state(None)
        await message.answer("Не удалось определить позицию. Нажмите на пункт ещё раз.")
        return

    selected = _restore_selected_indexes(state_data)
    selected.add(item_index)
    quantities = _restore_quantities(state_data)
    quantities[item_key] = quantity

    active_section = order_section_for_item(order_type, item_index)
    await state.set_state(None)
    await state.update_data(
        {
            ORDER_TYPE_KEY: order_type,
            ORDER_SELECTED_KEY: sorted(selected),
            ORDER_SECTION_KEY: active_section,
            ORDER_QUANTITIES_KEY: quantities,
            ORDER_PENDING_ITEM_KEY: None,
        }
    )

    chat_id_raw = state_data.get(ORDER_MESSAGE_CHAT_KEY)
    message_id_raw = state_data.get(ORDER_MESSAGE_ID_KEY)
    try:
        checklist_chat_id = int(chat_id_raw)
        checklist_message_id = int(message_id_raw)
    except (TypeError, ValueError):
        checklist_chat_id = None
        checklist_message_id = None

    text = build_order_text(order_type, selected, active_section)
    reply_markup = build_order_keyboard(order_type, selected, active_section, quantities)
    if checklist_chat_id is not None and checklist_message_id is not None:
        try:
            await message.bot.edit_message_text(
                text=text,
                chat_id=checklist_chat_id,
                message_id=checklist_message_id,
                reply_markup=reply_markup,
            )
            return
        except Exception:
            logger.exception("Failed to update product order checklist message")

    await message.answer(text, reply_markup=reply_markup)
