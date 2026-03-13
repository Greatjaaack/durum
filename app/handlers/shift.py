from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.checklist_data import CHECKLISTS, CLOSE_RESIDUAL_INPUTS
from app.checklists import (
    build_checklist_keyboard,
    build_checklist_text,
    checklist_item_text,
    checklist_section_for_item,
    checklist_total_items,
    normalize_checklist_section,
)
from app.config import Settings
from app.db import Database
from app.handlers.constants import (
    CLOSE_RESIDUAL_LABELS,
    CLOSE_RESIDUAL_PENDING_KEY,
    CLOSE_REQUIRED_RESIDUAL_KEYS,
)
from app.handlers.states import CloseShiftStates, OpenShiftStates
from app.handlers.utils import (
    employee_name,
    fmt_number,
    notify_owner,
    notify_work_chat,
    now_local,
    parse_close_residual_value,
    parse_non_negative_number,
)


logger = logging.getLogger(__name__)
shift_router = Router()


def _checklist_state_keys(
    checklist_type: str,
) -> dict[str, str]:
    """Возвращает набор ключей FSM-данных для конкретного чек-листа.

    Args:
        checklist_type: Тип чек-листа.

    Returns:
        Словарь ключей FSM-данных.
    """
    return {
        "done_key": f"checklist_{checklist_type}_done",
        "section_key": f"checklist_{checklist_type}_section",
        "shift_key": f"checklist_{checklist_type}_shift_id",
        "message_chat_key": f"checklist_{checklist_type}_message_chat_id",
        "message_id_key": f"checklist_{checklist_type}_message_id",
    }


def _restore_completed_indexes(
    state_data: dict[str, object],
    done_key: str,
    saved_state: dict[str, object] | None,
) -> set[int]:
    """Извлекает множество выполненных индексов из FSM или сохранённого состояния.

    Args:
        state_data: Данные FSM пользователя.
        done_key: Ключ списка выполненных пунктов в FSM.
        saved_state: Сохранённое состояние чек-листа из БД.

    Returns:
        Множество индексов выполненных пунктов.
    """
    completed: set[int] = set()
    completed_raw = state_data.get(done_key)

    if isinstance(completed_raw, list):
        for value in completed_raw:
            try:
                completed.add(int(value))
            except (TypeError, ValueError):
                continue
        return completed

    if saved_state:
        raw_saved = saved_state.get("completed", [])
        if isinstance(raw_saved, list):
            for value in raw_saved:
                try:
                    completed.add(int(value))
                except (TypeError, ValueError):
                    continue
    return completed


async def _start_checklist(
    message: Message,
    state: FSMContext,
    db: Database,
    checklist_type: str,
    shift_id: int,
) -> None:
    """Запускает чек-лист смены с восстановлением состояния.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.
        db: Экземпляр базы данных.
        checklist_type: Тип чек-листа.
        shift_id: Идентификатор смены.

    Returns:
        None.
    """
    keys = _checklist_state_keys(checklist_type)
    saved_state = await db.get_checklist_state(
        shift_id=shift_id,
        checklist_type=checklist_type,
    )
    completed = _restore_completed_indexes({}, keys["done_key"], saved_state)
    if saved_state:
        active_section = normalize_checklist_section(
            checklist_type,
            int(saved_state.get("active_section", 0)),
        )
    else:
        active_section = 0

    total_items = checklist_total_items(checklist_type)
    checklist_text = build_checklist_text(checklist_type, completed, active_section)
    is_close_completed = checklist_type == "close" and len(completed) == total_items

    if is_close_completed:
        checklist_text = (
            f"{checklist_text}\n\n"
            "Чек-лист завершён.\n"
            "Введите выручку за смену (₽) следующим сообщением."
        )
        checklist_message = await message.answer(checklist_text)
    else:
        checklist_message = await message.answer(
            checklist_text,
            reply_markup=build_checklist_keyboard(checklist_type, completed, active_section),
        )

    await state.update_data(
        {
            keys["done_key"]: sorted(completed),
            keys["section_key"]: active_section,
            keys["shift_key"]: shift_id,
            keys["message_chat_key"]: checklist_message.chat.id,
            keys["message_id_key"]: checklist_message.message_id,
            CLOSE_RESIDUAL_PENDING_KEY: None,
        }
    )
    if is_close_completed:
        await state.set_state(CloseShiftStates.waiting_revenue)


@shift_router.message(Command("open"))
async def open_shift(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    """Открывает новую смену и запускает чек-лист открытия.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.
        db: Экземпляр базы данных.
        settings: Настройки приложения.

    Returns:
        None.
    """
    if not message.from_user:
        return

    active_shift = await db.get_active_shift(message.from_user.id)
    if active_shift:
        await message.answer("У вас уже есть открытая смена. Сначала закройте её командой /close.")
        return

    await state.clear()
    now = now_local(settings)
    shift_id = await db.create_shift(
        employee=employee_name(message),
        employee_id=message.from_user.id,
        shift_date=now.date().isoformat(),
        open_time=now.isoformat(timespec="minutes"),
    )
    logger.info("Открыта смена shift_id=%s employee_id=%s", shift_id, message.from_user.id)
    await _start_checklist(message, state, db, "open", shift_id)


@shift_router.message(Command("mid"))
async def mid_shift(
    message: Message,
    state: FSMContext,
    db: Database,
) -> None:
    """Запускает чек-лист ведения для активной смены.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.
        db: Экземпляр базы данных.

    Returns:
        None.
    """
    if not message.from_user:
        return

    active_shift = await db.get_active_shift(message.from_user.id)
    if not active_shift:
        await message.answer("Сначала откройте смену командой /open.")
        return

    await state.clear()
    await _start_checklist(message, state, db, "mid", int(active_shift["id"]))


@shift_router.message(Command("close"))
async def close_shift_start(
    message: Message,
    state: FSMContext,
    db: Database,
) -> None:
    """Запускает чек-лист закрытия активной смены.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.
        db: Экземпляр базы данных.

    Returns:
        None.
    """
    if not message.from_user:
        return

    active_shift = await db.get_active_shift(message.from_user.id)
    if not active_shift:
        await message.answer("Нет открытой смены. Откройте её командой /open.")
        return

    await state.clear()
    await _start_checklist(message, state, db, "close", int(active_shift["id"]))


@shift_router.callback_query(F.data.startswith("checklist:"))
async def checklist_callback(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
) -> None:
    """Обрабатывает нажатия inline-кнопок в чек-листах смены.

    Args:
        callback: Callback-запрос Telegram.
        state: FSM-контекст пользователя.
        db: Экземпляр базы данных.

    Returns:
        None.
    """
    if not callback.data or not callback.message:
        return

    parts = callback.data.split(":")
    if len(parts) == 4:
        _, checklist_type, action, value_raw = parts
    elif len(parts) == 3:
        _, checklist_type, value_raw = parts
        action = "item"
    else:
        await callback.answer()
        return

    if checklist_type not in CHECKLISTS:
        await callback.answer("Неизвестный чек-лист", show_alert=True)
        return

    keys = _checklist_state_keys(checklist_type)
    state_data = await state.get_data()

    shift_id_raw = state_data.get(keys["shift_key"])
    try:
        shift_id = int(shift_id_raw)
    except (TypeError, ValueError):
        shift_id = None

    if shift_id is None and callback.from_user:
        active_shift = await db.get_active_shift(callback.from_user.id)
        if active_shift:
            shift_id = int(active_shift["id"])

    if shift_id is None:
        await callback.answer("Не удалось определить смену", show_alert=True)
        return

    saved_state = await db.get_checklist_state(
        shift_id=shift_id,
        checklist_type=checklist_type,
    )
    completed = _restore_completed_indexes(
        state_data,
        keys["done_key"],
        saved_state,
    )

    section_default_raw = saved_state.get("active_section", 0) if saved_state else 0
    section_raw = state_data.get(keys["section_key"], section_default_raw)
    try:
        section_index = int(section_raw)
    except (TypeError, ValueError):
        section_index = int(section_default_raw)
    active_section = normalize_checklist_section(checklist_type, section_index)

    if action == "section":
        try:
            active_section = normalize_checklist_section(checklist_type, int(value_raw))
        except ValueError:
            await callback.answer("Некорректный блок", show_alert=True)
            return
    elif action == "item":
        try:
            index = int(value_raw)
        except ValueError:
            await callback.answer("Некорректный пункт", show_alert=True)
            return

        checklist_len = checklist_total_items(checklist_type)
        if index < 0 or index >= checklist_len:
            await callback.answer("Некорректный пункт", show_alert=True)
            return

        item_text = checklist_item_text(checklist_type, index)
        if checklist_type == "close" and item_text and item_text in CLOSE_RESIDUAL_INPUTS:
            residual_config = CLOSE_RESIDUAL_INPUTS[item_text]
            active_section = checklist_section_for_item(checklist_type, index)
            await state.set_state(CloseShiftStates.waiting_residual_value)
            await state.update_data(
                {
                    keys["done_key"]: sorted(completed),
                    keys["section_key"]: active_section,
                    keys["shift_key"]: shift_id,
                    keys["message_chat_key"]: callback.message.chat.id,
                    keys["message_id_key"]: callback.message.message_id,
                    CLOSE_RESIDUAL_PENDING_KEY: {
                        "item_index": index,
                        "item_text": item_text,
                        "item_key": residual_config["key"],
                        "unit": residual_config["unit"],
                        "prompt": residual_config["prompt"],
                    },
                }
            )
            await callback.answer("Введите значение остатка")
            await callback.message.answer(str(residual_config["prompt"]))
            return

        if index in completed:
            completed.remove(index)
        else:
            completed.add(index)
        active_section = checklist_section_for_item(checklist_type, index)
    else:
        await callback.answer()
        return

    await state.set_state(None)
    completed_sorted = sorted(completed)
    await state.update_data(
        {
            keys["done_key"]: completed_sorted,
            keys["section_key"]: active_section,
            keys["shift_key"]: shift_id,
            keys["message_chat_key"]: callback.message.chat.id,
            keys["message_id_key"]: callback.message.message_id,
            CLOSE_RESIDUAL_PENDING_KEY: None,
        }
    )
    await db.upsert_checklist_state(
        shift_id=shift_id,
        checklist_type=checklist_type,
        completed=completed_sorted,
        active_section=active_section,
    )

    checklist_len = checklist_total_items(checklist_type)
    checklist_text = build_checklist_text(checklist_type, completed, active_section)
    if len(completed) == checklist_len:
        if checklist_type == "mid":
            await callback.message.edit_text(
                checklist_text,
                reply_markup=build_checklist_keyboard(checklist_type, completed, active_section),
            )
            await callback.answer("Чек-лист завершён.")
            return

        if checklist_type == "open":
            await callback.message.edit_text(checklist_text)
            await callback.answer("Чек-лист завершён.")
            await state.set_state(OpenShiftStates.waiting_meat_start)
            await callback.message.answer("Чек-лист завершён.\nСколько мяса сейчас (кг)?")
            return

        if checklist_type == "close":
            await state.set_state(CloseShiftStates.waiting_revenue)
            close_ready_text = (
                f"{checklist_text}\n\n"
                "Чек-лист завершён.\n"
                "Введите выручку за смену (₽) следующим сообщением."
            )
            await callback.message.edit_text(close_ready_text)
            await callback.answer("Чек-лист завершён.")
            return

        await callback.message.edit_text(checklist_text)
        await callback.answer("Чек-лист завершён.")
        return

    await callback.message.edit_text(
        checklist_text,
        reply_markup=build_checklist_keyboard(checklist_type, completed, active_section),
    )
    await callback.answer()


@shift_router.message(CloseShiftStates.waiting_residual_value)
async def save_close_residual_value(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    """Сохраняет введённый остаток закрытия и обновляет чек-лист.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.
        db: Экземпляр базы данных.
        settings: Настройки приложения.

    Returns:
        None.
    """
    if not message.text or not message.from_user:
        return

    state_data = await state.get_data()
    pending = state_data.get(CLOSE_RESIDUAL_PENDING_KEY)
    if not isinstance(pending, dict):
        await state.set_state(None)
        await message.answer("Не удалось определить пункт. Нажмите его в чек-листе ещё раз.")
        return

    item_text = str(pending.get("item_text", "")).strip()
    item_key = str(pending.get("item_key", "")).strip()
    unit = str(pending.get("unit", "")).strip()
    value = parse_close_residual_value(message.text, item_key)
    if value is None:
        if item_key == "sauce":
            await message.answer("Введите остаток соуса как число или дробь: 1, 1/2, 1/3")
        else:
            await message.answer("Введите корректное число, например: 12.5")
        return

    item_index_raw = pending.get("item_index")
    try:
        item_index = int(item_index_raw)
    except (TypeError, ValueError):
        await state.set_state(None)
        await message.answer("Не удалось определить пункт. Нажмите его в чек-листе ещё раз.")
        return

    shift_id_raw = state_data.get("checklist_close_shift_id")
    try:
        shift_id = int(shift_id_raw)
    except (TypeError, ValueError):
        shift_id = None

    if shift_id is None:
        active_shift = await db.get_active_shift(message.from_user.id)
        if active_shift:
            shift_id = int(active_shift["id"])

    if shift_id is None:
        await state.set_state(None)
        await message.answer("Не удалось определить смену. Откройте чек-лист заново: /close")
        return

    now = now_local(settings)
    employee = employee_name(message)
    await db.upsert_close_residual(
        shift_id=shift_id,
        item_key=item_key,
        item_label=item_text,
        quantity=value,
        unit=unit or "шт",
        residual_date=now.date().isoformat(),
        residual_time=now.time().replace(microsecond=0).isoformat(),
        employee=employee,
        employee_id=message.from_user.id,
    )

    completed = _restore_completed_indexes(
        state_data,
        "checklist_close_done",
        None,
    )
    completed.add(item_index)

    active_section = checklist_section_for_item("close", item_index)
    completed_sorted = sorted(completed)
    await state.update_data(
        {
            "checklist_close_done": completed_sorted,
            "checklist_close_section": active_section,
            "checklist_close_shift_id": shift_id,
            CLOSE_RESIDUAL_PENDING_KEY: None,
        }
    )
    await db.upsert_checklist_state(
        shift_id=shift_id,
        checklist_type="close",
        completed=completed_sorted,
        active_section=active_section,
    )

    checklist_text = build_checklist_text("close", completed, active_section)
    checklist_len = checklist_total_items("close")
    checklist_chat_id_raw = state_data.get("checklist_close_message_chat_id")
    checklist_message_id_raw = state_data.get("checklist_close_message_id")
    try:
        checklist_chat_id = int(checklist_chat_id_raw)
        checklist_message_id = int(checklist_message_id_raw)
    except (TypeError, ValueError):
        checklist_chat_id = None
        checklist_message_id = None

    if len(completed) == checklist_len:
        await state.set_state(CloseShiftStates.waiting_revenue)
        close_ready_text = (
            f"{checklist_text}\n\n"
            "Чек-лист завершён.\n"
            "Введите выручку за смену (₽) следующим сообщением."
        )
        if checklist_chat_id is not None and checklist_message_id is not None:
            try:
                await message.bot.edit_message_text(
                    text=close_ready_text,
                    chat_id=checklist_chat_id,
                    message_id=checklist_message_id,
                )
            except Exception:
                logger.exception("Failed to update close checklist message after residual input")
                await message.answer(close_ready_text)
        else:
            await message.answer(close_ready_text)
        return

    await state.set_state(None)
    if checklist_chat_id is not None and checklist_message_id is not None:
        try:
            await message.bot.edit_message_text(
                text=checklist_text,
                chat_id=checklist_chat_id,
                message_id=checklist_message_id,
                reply_markup=build_checklist_keyboard("close", completed, active_section),
            )
            return
        except Exception:
            logger.exception("Failed to update close checklist message after residual input")

    await message.answer(
        checklist_text,
        reply_markup=build_checklist_keyboard("close", completed, active_section),
    )


@shift_router.message(OpenShiftStates.waiting_meat_start)
async def save_open_meat_start(
    message: Message,
    state: FSMContext,
    db: Database,
) -> None:
    """Сохраняет стартовый остаток мяса после открытия смены.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.
        db: Экземпляр базы данных.

    Returns:
        None.
    """
    if not message.from_user or not message.text:
        return

    meat_start = parse_non_negative_number(message.text)
    if meat_start is None:
        await message.answer("Введите корректное число (кг), например: 12.5")
        return

    active_shift = await db.get_active_shift(message.from_user.id)
    if not active_shift:
        await state.clear()
        await message.answer("Не удалось найти открытую смену. Откройте смену снова командой /open.")
        return

    await db.set_shift_meat_start(int(active_shift["id"]), meat_start)
    await state.clear()
    await message.answer("Смена открыта. Отличного дня :)")


@shift_router.message(CloseShiftStates.waiting_revenue)
async def close_revenue(
    message: Message,
    state: FSMContext,
) -> None:
    """Сохраняет выручку и переводит сценарий к ожиданию фото кухни.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.

    Returns:
        None.
    """
    if not message.text:
        return

    revenue = parse_non_negative_number(message.text)
    if revenue is None:
        await message.answer("Введите выручку числом, например: 25430.5")
        return

    await state.update_data(close_revenue=revenue)
    await state.set_state(CloseShiftStates.waiting_photo)
    await message.answer("Отправьте фото кухни.")


@shift_router.message(CloseShiftStates.waiting_photo, F.photo)
async def close_photo_ok(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
    bot: Bot,
) -> None:
    """Закрывает смену после получения фото и обязательных остатков.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.
        db: Экземпляр базы данных.
        settings: Настройки приложения.
        bot: Экземпляр Telegram-бота.

    Returns:
        None.
    """
    if not message.photo or not message.from_user:
        return

    photo_file_id = message.photo[-1].file_id
    await state.update_data(close_photo=photo_file_id)

    shift = await db.get_active_shift(message.from_user.id)
    if not shift:
        await state.clear()
        await message.answer("Открытая смена не найдена. Начните снова с /open.")
        return

    shift_id = int(shift["id"])
    residuals = await db.get_close_residuals(shift_id)
    missing_keys = [key for key in CLOSE_REQUIRED_RESIDUAL_KEYS if key not in residuals]
    if missing_keys:
        missing_labels = [CLOSE_RESIDUAL_LABELS.get(key, key) for key in missing_keys]
        await message.answer(
            "Не заполнены остатки в чек-листе:\n"
            + "\n".join(f"• {label}" for label in missing_labels)
            + "\n\nВернитесь в /close и заполните их."
        )
        return

    data = await state.get_data()
    revenue = float(data.get("close_revenue", 0))
    now = now_local(settings)

    marinated_chicken = float(residuals["marinated_chicken"]["quantity"])
    fried_chicken = float(residuals["fried_chicken"]["quantity"])
    lavash = float(residuals["lavash"]["quantity"])
    soup = float(residuals["soup"]["quantity"])
    sauce = float(residuals["sauce"]["quantity"])

    meat_used = await db.close_shift(
        shift_id=shift_id,
        close_time=now.isoformat(timespec="minutes"),
        revenue=revenue,
        photo=photo_file_id,
        meat_end=marinated_chicken,
        lavash_end=lavash,
    )

    employee = employee_name(message)
    stock_date = now.date().isoformat()
    stock_time = now.time().replace(microsecond=0).isoformat()
    await db.save_stock(
        item="мясо",
        quantity=marinated_chicken,
        stock_date=stock_date,
        employee=employee,
        employee_id=message.from_user.id,
        stock_time=stock_time,
    )
    await db.save_stock(
        item="лаваш",
        quantity=lavash,
        stock_date=stock_date,
        employee=employee,
        employee_id=message.from_user.id,
        stock_time=stock_time,
    )

    await notify_owner(
        bot,
        settings,
        (
            "Смена закрыта\n"
            f"Сотрудник: {employee}\n"
            f"Выручка: {revenue}\n"
            f"Расход мяса: {meat_used if meat_used is not None else 'н/д'}\n"
            f"Остаток маринованной курицы: {marinated_chicken} кг\n"
            f"Остаток жареной курицы: {fried_chicken} кг\n"
            f"Остаток лаваша: {lavash} шт\n"
            f"Остаток супа: {fmt_number(soup)} порц\n"
            f"Остаток соуса: {fmt_number(sauce)} гастроёмк."
        ),
    )

    close_report_text = (
        "📊 Закрытие смены\n\n"
        f"Дата: {now.date().isoformat()}\n"
        f"Время: {now.strftime('%H:%M')}\n\n"
        "Остатки:\n\n"
        f"Маринованная курица — {fmt_number(marinated_chicken)} кг\n"
        f"Жареная курица — {fmt_number(fried_chicken)} кг\n"
        f"Лаваш — {fmt_number(lavash)} шт\n"
        f"Суп — {fmt_number(soup)} порций\n"
        f"Соус — {fmt_number(sauce)}"
    )
    await notify_work_chat(bot, settings, close_report_text)

    logger.info("Смена закрыта shift_id=%s employee_id=%s", shift_id, message.from_user.id)
    await state.clear()
    await message.answer("Смена закрыта. Данные сохранены.")


@shift_router.message(CloseShiftStates.waiting_photo)
async def close_photo_invalid(
    message: Message,
) -> None:
    """Обрабатывает ввод, когда ожидается фото кухни при закрытии смены.

    Args:
        message: Входящее сообщение Telegram.

    Returns:
        None.
    """
    await message.answer("Нужно отправить фото кухни.")
