from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.checklist.callbacks import CHECKLIST_CALLBACK_PREFIX, parse_checklist_callback
from app.checklist.data import CHECKLISTS
from app.checklist.ui import (
    build_checklist_keyboard,
    build_checklist_text,
    checklist_section_for_item,
    checklist_total_items,
    normalize_checklist_section,
)
from app.db import Database
from app.handlers.utils import build_shift_menu_keyboard, safe_answer_callback, safe_edit_text


shift_checklist_router = Router()


def checklist_state_keys(
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


def restore_completed_indexes(
    state_data: dict[str, object],
    done_key: str,
    saved_state: dict[str, object] | None,
) -> set[int]:
    """Извлекает выполненные пункты из FSM-данных или состояния БД.

    Args:
        state_data: Данные FSM пользователя.
        done_key: Ключ массива выполненных пунктов.
        saved_state: Сохранённое состояние чек-листа.

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


@shift_checklist_router.callback_query(F.data.startswith(f"{CHECKLIST_CALLBACK_PREFIX}:"))
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
    if not callback.data or not callback.message or not callback.from_user:
        return

    async def _answer(text: str | None = None, *, show_alert: bool = False) -> None:
        await safe_answer_callback(
            callback,
            text,
            show_alert=show_alert,
            log_context="shift checklist",
        )

    payload = parse_checklist_callback(callback.data)
    if payload is None:
        await _answer()
        return

    checklist_type = payload.checklist_type
    action = payload.action

    if checklist_type not in CHECKLISTS:
        await _answer("Неизвестный чек-лист", show_alert=True)
        return
    if checklist_type == "close":
        await _answer(
            "Закрытие смены работает в отдельном сценарии. Используйте /close.",
            show_alert=True,
        )
        return

    keys = checklist_state_keys(checklist_type)
    state_data = await state.get_data()

    shift_id_raw = state_data.get(keys["shift_key"])
    try:
        shift_id = int(shift_id_raw)
    except (TypeError, ValueError):
        shift_id = None

    if payload.shift_id is not None:
        shift_id = payload.shift_id

    if shift_id is None:
        await _answer(
            "Этот чек-лист устарел. Откройте актуальный через /open или /mid.",
            show_alert=True,
        )
        return

    saved_state = await db.get_checklist_state(
        shift_id=shift_id,
        checklist_type=checklist_type,
    )
    completed = restore_completed_indexes(
        state_data,
        keys["done_key"],
        saved_state,
    )
    previous_completed_count = len(completed)

    section_default_raw = saved_state.get("active_section", 0) if saved_state else 0
    section_raw = state_data.get(keys["section_key"], section_default_raw)
    try:
        section_index = int(section_raw)
    except (TypeError, ValueError):
        section_index = int(section_default_raw)
    active_section = normalize_checklist_section(checklist_type, section_index)

    toggled_to_done = False
    if action == "section":
        active_section = normalize_checklist_section(checklist_type, payload.value)
    elif action == "item":
        index = payload.value

        checklist_len = checklist_total_items(checklist_type)
        if index < 0 or index >= checklist_len:
            await _answer("Некорректный пункт", show_alert=True)
            return

        if index in completed:
            completed.remove(index)
        else:
            completed.add(index)
            toggled_to_done = True
        active_section = checklist_section_for_item(checklist_type, index)

        # Автопереход к следующему блоку при закрытии текущего.
        if toggled_to_done and len(completed) < checklist_len:
            section_start = sum(
                len(section["items"])
                for section in CHECKLISTS[checklist_type][:active_section]
            )
            section_total = len(CHECKLISTS[checklist_type][active_section]["items"])
            section_done = sum(
                1
                for item_idx in range(section_start, section_start + section_total)
                if item_idx in completed
            )
            if (
                section_total > 0
                and section_done == section_total
                and active_section < len(CHECKLISTS[checklist_type]) - 1
            ):
                active_section = normalize_checklist_section(
                    checklist_type,
                    active_section + 1,
                )
    else:
        await _answer()
        return

    completed_sorted = sorted(completed)
    await state.update_data(
        {
            keys["done_key"]: completed_sorted,
            keys["section_key"]: active_section,
            keys["shift_key"]: shift_id,
            keys["message_chat_key"]: callback.message.chat.id,
            keys["message_id_key"]: callback.message.message_id,
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
    just_completed = (
        action == "item"
        and toggled_to_done
        and previous_completed_count < checklist_len
        and len(completed) == checklist_len
    )
    if len(completed) == checklist_len:
        if checklist_type == "mid":
            await safe_edit_text(
                callback.message,
                checklist_text,
                reply_markup=build_checklist_keyboard(
                    checklist_type,
                    completed,
                    active_section,
                    shift_id=shift_id,
                ),
                log_context="shift checklist",
            )
            await _answer("Чек-лист завершён.")
            if just_completed:
                await state.clear()
                await callback.message.answer("✅ Чек-лист ведения смены завершён.")
            return

        if checklist_type == "open":
            await safe_edit_text(
                callback.message,
                checklist_text,
                log_context="shift checklist",
            )
            await _answer("Чек-лист завершён.")
            await state.clear()
            await callback.message.answer(
                "Смена открыта ✅",
                reply_markup=build_shift_menu_keyboard(is_shift_open=True),
            )
            return

        await safe_edit_text(
            callback.message,
            checklist_text,
            log_context="shift checklist",
        )
        await _answer("Чек-лист завершён.")
        return

    await safe_edit_text(
        callback.message,
        checklist_text,
        reply_markup=build_checklist_keyboard(
            checklist_type,
            completed,
            active_section,
            shift_id=shift_id,
        ),
        log_context="shift checklist",
    )
    await _answer()
