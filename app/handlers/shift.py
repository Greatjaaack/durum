from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.checklist.data import (
    CHECKLISTS,
    CLOSE_CHECKLIST,
    CLOSE_RESIDUAL_INPUTS,
    CLOSE_RESIDUAL_INPUTS_BY_CHECKLIST_ITEM,
    CLOSE_SECTION_EMOJI_BY_TITLE,
    MID_NUMERIC_INPUTS_BY_ITEM_TEXT,
    PERIODIC_RESIDUAL_INPUTS_LIST,
    flat_checklist_items,
)
from app.checklist.ui import (
    build_checklist_keyboard,
    build_checklist_text,
    checklist_total_items,
    normalize_checklist_section,
)
from app.config import Settings
from app.db import Database
from app.handlers.constants import (
    CLOSE_RESIDUAL_LABELS,
    CLOSE_REQUIRED_RESIDUAL_KEYS,
    MENU_CLOSE_SHIFT,
    MENU_MID_SHIFT,
    MENU_OPEN_SHIFT,
    MENU_RESIDUALS,
    MENU_SHIFT_PHOTOS,
)
from app.handlers.shift_checklist import _build_open_notification_text, checklist_state_keys, restore_completed_indexes
from app.handlers.states import CloseShiftStates, MidShiftStates, OpenShiftStates, PeriodicResidualStates
from app.handlers.utils import (
    build_shift_menu_keyboard,
    employee_name,
    fmt_number,
    notify_owner,
    notify_work_chat,
    now_local,
    parse_close_residual_value,
    safe_answer_callback,
    safe_delete_message,
    safe_edit_text,
)
from app.units_config import (
    UNIT_TYPE_BASE_UNITS,
    normalize_measurement_value,
    restore_measurement_value,
)


logger = logging.getLogger(__name__)
shift_router = Router()

CloseItemType = Literal["input", "check"]


@dataclass(frozen=True)
class CloseInputRule:
    """Правило ввода числового остатка.

    Args:
        prompt: Текст подсказки для ввода.
        display_unit: Единица измерения в интерфейсе.
        unit_type: Тип единицы для нормализации.
        max_value: Верхняя граница допустимого значения.
        quick_buttons: Кнопки быстрого ввода в формате (текст, значение).
        only_integer: Признак, что ввод должен быть целым.
        step: Допустимый шаг ввода.
    """

    prompt: str
    display_unit: str
    unit_type: str
    max_value: float
    quick_buttons: tuple[tuple[str, str], ...] = ()
    only_integer: bool = False
    step: float | None = None


@dataclass(frozen=True)
class CloseWizardItem:
    """Описание одного пункта мастера закрытия смены.

    Args:
        index: Сквозной индекс пункта.
        section_index: Индекс секции пункта.
        section_title: Заголовок секции.
        section_emoji: Emoji секции.
        text: Текст пункта.
        item_type: Тип пункта (чекбокс или ввод).
        residual_key: Ключ остатка для сохранения в БД.
        storage_unit: Единица измерения в БД.
        input_rule: Настройки ввода для числовых пунктов.
    """

    index: int
    section_index: int
    section_title: str
    section_emoji: str
    text: str
    item_type: CloseItemType
    residual_key: str | None = None
    storage_unit: str | None = None
    input_rule: CloseInputRule | None = None


def _normalize_quick_buttons(raw_value: object) -> tuple[tuple[str, str], ...]:
    """Нормализует quick-кнопки из конфигурации.

    Args:
        raw_value: Сырые данные кнопок.

    Returns:
        Кортеж кнопок в формате (label, value).
    """
    if not isinstance(raw_value, (list, tuple)):
        return ()
    result: list[tuple[str, str]] = []
    for button in raw_value:
        if isinstance(button, (list, tuple)) and len(button) == 2:
            label = str(button[0]).strip()
            value = str(button[1]).strip()
        elif isinstance(button, dict):
            label = str(button.get("label", "")).strip()
            value = str(button.get("value", "")).strip()
        else:
            continue
        if label and value:
            result.append((label, value))
    return tuple(result)


def _build_close_input_rules() -> dict[str, CloseInputRule]:
    """Строит правила ввода остатков из общей конфигурации.

    Args:
        Нет параметров.

    Returns:
        Словарь правил ввода по residual_key.
    """
    rules: dict[str, CloseInputRule] = {}
    for config in CLOSE_RESIDUAL_INPUTS.values():
        key = str(config.get("key", "")).strip()
        prompt = str(config.get("prompt", "")).strip()
        display_unit = str(config.get("unit", "")).strip()
        unit_type = str(config.get("unit_type", "")).strip()
        if not key or not prompt or not display_unit or not unit_type:
            continue

        max_value_raw = config.get("max_value", 50000.0)
        try:
            max_value = float(max_value_raw)
        except (TypeError, ValueError):
            max_value = 50000.0

        step_value: float | None = None
        step_raw = config.get("step")
        if step_raw is not None:
            try:
                step_value = float(step_raw)
            except (TypeError, ValueError):
                step_value = None

        rules[key] = CloseInputRule(
            prompt=prompt,
            display_unit=display_unit,
            unit_type=unit_type,
            max_value=max_value,
            quick_buttons=_normalize_quick_buttons(config.get("quick_buttons")),
            only_integer=bool(config.get("only_integer", False)),
            step=step_value,
        )
    return rules


INPUT_RULES = _build_close_input_rules()


def _build_close_wizard_items() -> tuple[CloseWizardItem, ...]:
    """Строит линейный список пунктов мастера закрытия.

    Args:
        Нет параметров.

    Returns:
        Кортеж пунктов мастера.
    """
    items: list[CloseWizardItem] = []
    cursor = 0
    for section_index, section in enumerate(CLOSE_CHECKLIST):
        section_title = str(section["title"]).strip()
        section_emoji = CLOSE_SECTION_EMOJI_BY_TITLE.get(section_title, "▫️")
        for item_text_raw in section["items"]:
            item_text = str(item_text_raw).strip()
            residual_config = CLOSE_RESIDUAL_INPUTS_BY_CHECKLIST_ITEM.get(item_text)
            if residual_config is None:
                residual_config = CLOSE_RESIDUAL_INPUTS.get(item_text)
            if residual_config:
                residual_key = str(residual_config["key"])
                input_rule = INPUT_RULES.get(residual_key)
                if not input_rule:
                    fallback_step: float | None = None
                    fallback_step_raw = residual_config.get("step")
                    if fallback_step_raw is not None:
                        try:
                            fallback_step = float(fallback_step_raw)
                        except (TypeError, ValueError):
                            fallback_step = None
                    input_rule = CloseInputRule(
                        prompt=str(residual_config.get("prompt", "Введите значение")),
                        display_unit=str(residual_config.get("unit", "шт")),
                        unit_type=str(residual_config.get("unit_type", "piece")),
                        max_value=float(residual_config.get("max_value", 50000.0)),
                        quick_buttons=_normalize_quick_buttons(
                            residual_config.get("quick_buttons")
                        ),
                        only_integer=bool(residual_config.get("only_integer", False)),
                        step=fallback_step,
                    )
                items.append(
                    CloseWizardItem(
                        index=cursor,
                        section_index=section_index,
                        section_title=section_title,
                        section_emoji=section_emoji,
                        text=item_text,
                        item_type="input",
                        residual_key=residual_key,
                        storage_unit=str(residual_config["unit"]),
                        input_rule=input_rule,
                    )
                )
            else:
                items.append(
                    CloseWizardItem(
                        index=cursor,
                        section_index=section_index,
                        section_title=section_title,
                        section_emoji=section_emoji,
                        text=item_text,
                        item_type="check",
                    )
                )
            cursor += 1
    return tuple(items)


CLOSE_WIZARD_ITEMS = _build_close_wizard_items()
CLOSE_WIZARD_TOTAL = len(CLOSE_WIZARD_ITEMS)
CLOSE_WIZARD_STEPS_TOTAL = len(CLOSE_CHECKLIST)

# Префикс callback_data для мастера закрытия смены.
CLOSE_WIZARD_CALLBACK_PREFIX = "closewiz"


async def _download_media_to_disk(
    bot: Bot,
    file_id: str,
    shift_id: int,
    item_index: int,
    mime_type: str | None,
    media_type: str,
) -> str | None:
    """Скачивает файл из Telegram и сохраняет на диск.

    Args:
        bot: Экземпляр Telegram-бота.
        file_id: Telegram file_id.
        shift_id: ID смены.
        item_index: Индекс пункта чек-листа.
        mime_type: MIME-тип файла.
        media_type: Тип медиа ('open' или 'close').

    Returns:
        Путь к сохранённому файлу или None при ошибке.
    """
    media_root = Path(os.getenv("MEDIA_DIR", "data/media")) / media_type
    media_root.mkdir(parents=True, exist_ok=True)
    ext = ".jpg"
    if mime_type:
        if "png" in mime_type:
            ext = ".png"
        elif "gif" in mime_type:
            ext = ".gif"
        elif "pdf" in mime_type:
            ext = ".pdf"
    dest = media_root / f"{shift_id}_{item_index}{ext}"
    try:
        await bot.download(file_id, destination=str(dest))
        return str(dest)
    except Exception:
        logger.exception("Failed to save media to disk file_id=%s shift_id=%s", file_id, shift_id)
        return None



def close_wizard_item_by_index(index: int) -> CloseWizardItem | None:
    """Возвращает пункт мастера по индексу.

    Args:
        index: Индекс пункта.

    Returns:
        Пункт мастера или None.
    """
    if index < 0 or index >= CLOSE_WIZARD_TOTAL:
        return None
    return CLOSE_WIZARD_ITEMS[index]


def close_wizard_total_items() -> int:
    """Возвращает общее количество пунктов мастера.

    Args:
        Нет параметров.

    Returns:
        Число пунктов.
    """
    return CLOSE_WIZARD_TOTAL


def close_wizard_first_incomplete_index(
    completed: set[int],
) -> int:
    """Возвращает индекс первого незавершённого пункта.

    Args:
        completed: Множество выполненных индексов.

    Returns:
        Индекс пункта или длину списка, если всё завершено.
    """
    for item in CLOSE_WIZARD_ITEMS:
        if item.index not in completed:
            return item.index
    return CLOSE_WIZARD_TOTAL


def close_wizard_section_for_index(index: int) -> int:
    """Возвращает индекс секции для пункта мастера.

    Args:
        index: Индекс пункта.

    Returns:
        Индекс секции.
    """
    if index < 0:
        return 0
    item = close_wizard_item_by_index(min(index, CLOSE_WIZARD_TOTAL - 1))
    return item.section_index if item else 0


def close_wizard_to_storage_value(
    item: CloseWizardItem,
    display_value: float,
) -> float:
    """Переводит значение из интерфейса в единицу хранения БД.

    Args:
        item: Пункт мастера.
        display_value: Значение, введённое пользователем.

    Returns:
        Нормализованное значение для хранения в БД.
    """
    if not item.input_rule:
        return display_value
    normalized = normalize_measurement_value(
        display_value,
        item.input_rule.unit_type,
    )
    if not normalized:
        return display_value
    return normalized.normalized


def close_wizard_restore_display_value(
    item: CloseWizardItem,
    storage_value: float,
) -> float:
    """Переводит значение из БД в формат интерфейса.

    Args:
        item: Пункт мастера.
        storage_value: Значение из БД.

    Returns:
        Значение для отображения в UI.
    """
    if not item.input_rule:
        return storage_value
    restored = restore_measurement_value(
        storage_value,
        item.input_rule.unit_type,
    )
    if restored is None:
        return storage_value
    return restored


def close_wizard_normalized_unit(
    item: CloseWizardItem,
) -> str | None:
    """Возвращает базовую единицу нормализации для пункта мастера.

    Args:
        item: Пункт мастера.

    Returns:
        Базовая единица (например, `г`, `мл`) или None.
    """
    if not item.input_rule:
        return None
    return UNIT_TYPE_BASE_UNITS.get(item.input_rule.unit_type)


def _close_wizard_progress_line(
    done: int,
    total: int,
) -> tuple[str, int]:
    """Формирует визуальную строку прогресса.

    Args:
        done: Выполненные пункты.
        total: Все пункты.

    Returns:
        Кортеж из progress-bar и процента.
    """
    if total <= 0:
        return "░░░░░░░░░░░░", 0

    width = 12
    ratio = done / total
    filled = min(width, max(0, round(ratio * width)))
    percent = round(ratio * 100)
    bar = f"{'█' * filled}{'░' * (width - filled)}"
    return bar, percent


def _close_wizard_as_question_text(
    item_text: str,
) -> str:
    """Преобразует текст пункта в короткий вопрос.

    Args:
        item_text: Текст пункта чек-листа.

    Returns:
        Короткий вопрос с вопросительным знаком.
    """
    text = item_text.strip().rstrip(".?!")
    if not text:
        return "Готово?"
    return f"{text}?"


def build_close_wizard_question_text(
    *,
    item: CloseWizardItem,
    completed: set[int],
    values: dict[str, float],
    error_text: str | None = None,
) -> str:
    """Формирует экран с одним вопросом мастера.

    Args:
        item: Текущий пункт мастера.
        completed: Множество выполненных пунктов.
        values: Введённые значения остатков.
        error_text: Текст ошибки валидации.

    Returns:
        Текст сообщения Telegram.
    """
    done = len(completed)
    total = CLOSE_WIZARD_TOTAL
    bar, percent = _close_wizard_progress_line(done, total)

    lines = [
        f"{item.section_emoji} {item.section_title}",
        f"Шаг {item.section_index + 1} из {CLOSE_WIZARD_STEPS_TOTAL}",
        "",
        f"Прогресс: {bar} {percent}%",
        "",
    ]

    if item.item_type == "input" and item.input_rule:
        lines.append(item.input_rule.prompt)
        if item.residual_key and item.residual_key in values:
            lines.append(
                f"Сейчас: {fmt_number(values[item.residual_key])} {item.input_rule.display_unit}"
            )
    else:
        lines.append(_close_wizard_as_question_text(item.text))
        if _close_wizard_item_requires_photo(item):
            lines.append("Отправьте фото (камера или галерея).")

    if error_text:
        lines.append("")
        lines.append(f"⚠ {error_text}")

    return "\n".join(lines)


def build_close_wizard_question_keyboard(
    item: CloseWizardItem,
) -> InlineKeyboardMarkup:
    """Строит клавиатуру для экрана одного вопроса.

    Args:
        item: Текущий пункт мастера.

    Returns:
        Inline-клавиатура.
    """
    rows: list[list[InlineKeyboardButton]] = []
    if item.item_type != "input" or not item.input_rule:
        rows.append(
            [
                InlineKeyboardButton(
                    text="← Назад",
                    callback_data=f"{CLOSE_WIZARD_CALLBACK_PREFIX}:back",
                )
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    if item.input_rule.quick_buttons:
        quick_row: list[InlineKeyboardButton] = []
        for label, raw_value in item.input_rule.quick_buttons:
            quick_row.append(
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{CLOSE_WIZARD_CALLBACK_PREFIX}:quick:{raw_value}",
                )
            )
            if len(quick_row) == 2:
                rows.append(quick_row)
                quick_row = []
        if quick_row:
            rows.append(quick_row)

    nav_row: list[InlineKeyboardButton] = [
        InlineKeyboardButton(
            text="⏭ Пропустить",
            callback_data=f"{CLOSE_WIZARD_CALLBACK_PREFIX}:skip",
        )
    ]
    nav_row.append(
        InlineKeyboardButton(
            text="← Назад",
            callback_data=f"{CLOSE_WIZARD_CALLBACK_PREFIX}:back",
        )
    )
    rows.append(nav_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Ключи FSM-данных для блокового сценария закрытия смены.
CLOSE_WIZARD_SHIFT_KEY = "close_wizard_shift_id"
CLOSE_WIZARD_DONE_KEY = "close_wizard_done"
CLOSE_WIZARD_INDEX_KEY = "close_wizard_item_index"
CLOSE_WIZARD_SECTION_KEY = "close_wizard_section"
CLOSE_WIZARD_VALUES_KEY = "close_wizard_values"
CLOSE_WIZARD_MESSAGE_CHAT_KEY = "close_wizard_message_chat_id"
CLOSE_WIZARD_MESSAGE_ID_KEY = "close_wizard_message_id"
CLOSE_WIZARD_STARTED_AT_KEY = "close_wizard_started_at"
CLOSE_WIZARD_FINISH_CONFIRM_KEY = "close_wizard_finish_confirm"
CLOSE_DONE_CALLBACK_PREFIX = "closedone"


def _build_close_wizard_section_items() -> dict[int, tuple[CloseWizardItem, ...]]:
    """Группирует пункты закрытия по секциям.

    Args:
        Нет параметров.

    Returns:
        Словарь: индекс секции -> кортеж пунктов.
    """
    grouped: dict[int, list[CloseWizardItem]] = {}
    for index in range(close_wizard_total_items()):
        item = close_wizard_item_by_index(index)
        if not item:
            continue
        grouped.setdefault(item.section_index, []).append(item)
    return {
        section_index: tuple(items)
        for section_index, items in grouped.items()
    }


CLOSE_WIZARD_SECTION_ITEMS = _build_close_wizard_section_items()
CLOSE_WIZARD_RESIDUAL_INDEX = {
    item.residual_key: item.index
    for items in CLOSE_WIZARD_SECTION_ITEMS.values()
    for item in items
    if item.residual_key
}
# Пункты закрытия, которые можно отметить только после отправки фото.
CLOSE_WIZARD_PHOTO_REQUIRED_ITEMS = frozenset(
    {
        "сделать фото корзины фритюра",
        "сделать фото масла во фритюре",
        "сделать фото корзины",
        "сделать фото масла",
    }
)


def _close_wizard_item_requires_photo(item: CloseWizardItem) -> bool:
    """Проверяет, требуется ли фото для выполнения пункта мастера.

    Args:
        item: Пункт мастера.

    Returns:
        True, если для пункта обязательно фото.
    """
    if item.item_type != "check":
        return False
    normalized_text = item.text.strip().lower()
    if normalized_text in CLOSE_WIZARD_PHOTO_REQUIRED_ITEMS:
        return True
    if "фото" in normalized_text and "корзин" in normalized_text:
        return True
    if "фото" in normalized_text and "масл" in normalized_text:
        return True
    return False


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
    keys = checklist_state_keys(checklist_type)
    saved_state = await db.get_checklist_state(
        shift_id=shift_id,
        checklist_type=checklist_type,
    )
    completed = restore_completed_indexes({}, keys["done_key"], saved_state)
    if saved_state:
        active_section = normalize_checklist_section(
            checklist_type,
            int(saved_state.get("active_section", 0)),
        )
    else:
        active_section = 0

    checklist_text = build_checklist_text(checklist_type, completed, active_section)
    checklist_message = await message.answer(
        checklist_text,
        reply_markup=build_checklist_keyboard(
            checklist_type,
            completed,
            active_section,
            shift_id=shift_id,
        ),
    )

    await state.update_data(
        {
            keys["done_key"]: sorted(completed),
            keys["section_key"]: active_section,
            keys["shift_key"]: shift_id,
            keys["message_chat_key"]: checklist_message.chat.id,
            keys["message_id_key"]: checklist_message.message_id,
        }
    )


@shift_router.message(Command("open"))
@shift_router.message(F.text == MENU_OPEN_SHIFT)
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

    active_shift = await db.get_active_shift()
    if active_shift:
        shift_id = int(active_shift["id"])
        open_state = await db.get_checklist_state(
            shift_id=shift_id,
            checklist_type="open",
        )
        open_done = len(open_state.get("completed", [])) if open_state else 0
        open_total = checklist_total_items("open")
        if open_done < open_total:
            await state.clear()
            await message.answer("Смена уже открыта. Продолжим чек-лист открытия.")
            await _start_checklist(message, state, db, "open", shift_id)
            return

        opener = str(active_shift.get("opened_by") or active_shift.get("employee") or "")
        opener_label = f" сотрудником {opener}" if opener else ""
        await message.answer(
            f"Смена на сегодня уже открыта{opener_label}.",
            reply_markup=build_shift_menu_keyboard(is_shift_open=True),
        )
        return

    await state.clear()
    now = now_local(settings)
    shift_id = await db.create_shift(
        employee=employee_name(message),
        employee_id=message.from_user.id,
        shift_date=now.date().isoformat(),
        open_time=now.isoformat(timespec="minutes"),
    )
    latest_meat_stock = await db.get_latest_stock_item_quantity("мясо")
    if latest_meat_stock is not None:
        await db.set_shift_meat_start(shift_id, latest_meat_stock)

    logger.info("Открыта смена shift_id=%s employee_id=%s", shift_id, message.from_user.id)
    await _start_checklist(message, state, db, "open", shift_id)


@shift_router.message(OpenShiftStates.waiting_photo, (F.photo | F.document))
async def open_checklist_photo_input(
    message: Message,
    state: FSMContext,
    db: Database,
    bot: Bot,
    settings: Settings,
) -> None:
    """Принимает обязательное фото холодильника при открытии смены.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.
        db: Экземпляр базы данных.

    Returns:
        None.
    """
    if not message.from_user:
        return

    state_data = await state.get_data()
    item_index: int | None = None
    shift_id: int | None = None
    try:
        item_index = int(state_data["open_photo_item_index"])
        shift_id = int(state_data["open_photo_shift_id"])
    except (KeyError, TypeError, ValueError):
        await state.clear()
        await message.answer("Что-то пошло не так. Повторите открытие через /open.")
        return

    if message.document:
        mime = str(message.document.mime_type or "").lower()
        if not mime.startswith("image/"):
            await message.answer("Нужно отправить именно фото, а не файл.")
            return

    media_file_id: str | None = None
    media_file_unique_id: str | None = None
    media_mime_type: str | None = None
    if message.photo:
        largest = message.photo[-1]
        media_file_id = str(largest.file_id)
        media_file_unique_id = str(largest.file_unique_id)
        media_mime_type = "image/jpeg"
    elif message.document:
        media_file_id = str(message.document.file_id)
        media_file_unique_id = str(message.document.file_unique_id)
        media_mime_type = str(message.document.mime_type or "").strip() or None

    if not media_file_id:
        await message.answer("Не удалось получить фото. Попробуйте ещё раз.")
        return

    created_at = (
        message.date.isoformat()
        if message.date is not None
        else datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )

    open_items = flat_checklist_items("open")
    item_label = open_items[item_index] if item_index < len(open_items) else "Фото холодильника"

    local_path = await _download_media_to_disk(
        bot=bot,
        file_id=media_file_id,
        shift_id=shift_id,
        item_index=item_index,
        mime_type=media_mime_type,
        media_type="open",
    )
    await db.upsert_open_checklist_media(
        shift_id=shift_id,
        item_index=item_index,
        item_label=item_label,
        file_id=media_file_id,
        file_unique_id=media_file_unique_id,
        mime_type=media_mime_type,
        created_at=created_at,
        local_path=local_path,
    )

    # Отмечаем пункт как выполненный
    saved_state = await db.get_checklist_state(shift_id=shift_id, checklist_type="open")
    keys = checklist_state_keys("open")
    completed = restore_completed_indexes({}, keys["done_key"], saved_state)
    completed.add(item_index)
    completed_sorted = sorted(completed)

    open_total = checklist_total_items("open")
    active_section_raw = saved_state.get("active_section", 0) if saved_state else 0
    try:
        active_section = int(active_section_raw)
    except (TypeError, ValueError):
        active_section = 0
    active_section = normalize_checklist_section("open", active_section)

    await db.upsert_checklist_state(
        shift_id=shift_id,
        checklist_type="open",
        completed=completed_sorted,
        active_section=active_section,
    )
    await state.clear()

    await safe_delete_message(message, log_context="open checklist photo input")

    if len(completed) >= open_total:
        await message.answer(
            "Смена открыта ✅",
            reply_markup=build_shift_menu_keyboard(is_shift_open=True),
        )
        display_name = await db.get_employee_display_name(message.from_user.id)
        name = display_name or employee_name(message)
        open_time = now_local(settings).strftime("%H:%M")
        await notify_work_chat(
            bot,
            settings,
            _build_open_notification_text(name, open_time, completed),
        )
    else:
        await message.answer("✅ Фото сохранено. Продолжайте чек-лист.")
        await _start_checklist(message, state, db, "open", shift_id)


@shift_router.message(MidShiftStates.waiting_numeric, F.text)
async def mid_checklist_numeric_input(
    message: Message,
    state: FSMContext,
    db: Database,
) -> None:
    """Принимает числовой ввод для пункта ведения смены.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.
        db: Экземпляр базы данных.

    Returns:
        None.
    """
    if not message.from_user or not message.text:
        return

    state_data = await state.get_data()
    item_index: int | None = None
    shift_id: int | None = None
    try:
        item_index = int(state_data["mid_numeric_item_index"])
        shift_id = int(state_data["mid_numeric_shift_id"])
    except (KeyError, TypeError, ValueError):
        await state.clear()
        await message.answer("Что-то пошло не так. Запустите /mid заново.")
        return

    item_text = str(state_data.get("mid_numeric_item_text", ""))
    cfg: dict[str, object] = MID_NUMERIC_INPUTS_BY_ITEM_TEXT.get(item_text, {})

    raw_text = message.text.strip().replace(",", ".")
    try:
        value = float(raw_text)
    except ValueError:
        await message.answer("Пожалуйста, введите число (например, 500).")
        return

    if not math.isfinite(value) or value < 0:
        await message.answer("Введите корректное неотрицательное число.")
        return

    max_value = cfg.get("max_value")
    if max_value is not None and value > float(max_value):
        await message.answer(f"Слишком большое значение. Максимум: {int(max_value)}.")
        return

    only_integer = bool(cfg.get("only_integer", False))
    if only_integer and value != int(value):
        await message.answer("Значение должно быть целым числом.")
        return

    unit = str(cfg.get("unit", ""))
    key = str(cfg.get("key", ""))
    created_at = (
        message.date.isoformat()
        if message.date is not None
        else datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )

    await db.upsert_mid_checklist_data(
        shift_id=shift_id,
        key=key,
        value=value,
        unit=unit,
        created_at=created_at,
    )

    # Отмечаем пункт как выполненный
    saved_state = await db.get_checklist_state(shift_id=shift_id, checklist_type="mid")
    keys = checklist_state_keys("mid")
    completed = restore_completed_indexes({}, keys["done_key"], saved_state)
    completed.add(item_index)
    completed_sorted = sorted(completed)

    mid_total = checklist_total_items("mid")
    active_section_raw = saved_state.get("active_section", 0) if saved_state else 0
    try:
        active_section = int(active_section_raw)
    except (TypeError, ValueError):
        active_section = 0
    active_section = normalize_checklist_section("mid", active_section)

    await db.upsert_checklist_state(
        shift_id=shift_id,
        checklist_type="mid",
        completed=completed_sorted,
        active_section=active_section,
    )
    await state.clear()

    display_value = int(value) if only_integer else value
    await message.answer(f"✅ Записано: {display_value} {unit}")

    if len(completed) >= mid_total:
        await db.update_shift_last_mid(shift_id, datetime.now(timezone.utc).isoformat())
        await message.answer(
            "✅ Чек-лист ведения смены завершён.",
            reply_markup=build_shift_menu_keyboard(is_shift_open=True),
        )
    else:
        await _start_checklist(message, state, db, "mid", shift_id)


@shift_router.message(Command("mid"))
@shift_router.message(F.text == MENU_MID_SHIFT)
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

    active_shift = await db.get_active_shift()
    if not active_shift:
        await message.answer(
            "Сначала откройте смену.",
            reply_markup=build_shift_menu_keyboard(is_shift_open=False),
        )
        return

    shift_id = int(active_shift["id"])
    await db.update_shift_mid_started_at(shift_id, datetime.now(timezone.utc).isoformat())
    await state.clear()
    await _start_checklist(message, state, db, "mid", shift_id)


@shift_router.message(Command("close"))
@shift_router.message(F.text == MENU_CLOSE_SHIFT)
async def close_shift_start(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    """Запускает блоковый сценарий закрытия активной смены.

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

    active_shift = await db.get_active_shift()
    if not active_shift:
        await message.answer(
            "Нет открытой смены.",
            reply_markup=build_shift_menu_keyboard(is_shift_open=False),
        )
        return

    shift_id = int(active_shift["id"])
    open_state = await db.get_checklist_state(
        shift_id=shift_id,
        checklist_type="open",
    )
    open_done = len(open_state.get("completed", [])) if open_state else 0
    open_total = checklist_total_items("open")
    if open_done < open_total:
        remaining = max(0, open_total - open_done)
        await state.clear()
        await message.answer(
            "Нельзя закрыть смену, пока не завершён чек-лист открытия.\n"
            f"Осталось: {remaining} {_close_wizard_count_suffix(remaining)}."
        )
        await _start_checklist(message, state, db, "open", shift_id)
        return

    now = now_local(settings)
    started_at_raw = str(active_shift.get("close_started_at") or "").strip()
    started_at = started_at_raw or now.isoformat(timespec="seconds")
    await db.mark_close_flow_started(
        shift_id=shift_id,
        started_at=started_at,
    )

    await state.clear()
    await _start_close_wizard(
        message=message,
        state=state,
        db=db,
        shift_id=shift_id,
        started_at=started_at,
    )


def _close_wizard_restore_completed(
    saved_state: dict[str, object] | None,
) -> set[int]:
    """Восстанавливает выполненные пункты мастера закрытия из БД.

    Args:
        saved_state: Состояние чек-листа из БД.

    Returns:
        Множество индексов выполненных пунктов.
    """
    completed: set[int] = set()
    if not saved_state:
        return completed

    raw_completed = saved_state.get("completed", [])
    if not isinstance(raw_completed, list):
        return completed

    for value in raw_completed:
        try:
            completed.add(int(value))
        except (TypeError, ValueError):
            continue
    return completed


def _close_wizard_restore_values(
    state_data: dict[str, object],
) -> dict[str, float]:
    """Восстанавливает введённые значения остатков из FSM.

    Args:
        state_data: Данные FSM пользователя.

    Returns:
        Словарь значений остатков.
    """
    values: dict[str, float] = {}
    raw = state_data.get(CLOSE_WIZARD_VALUES_KEY)
    if not isinstance(raw, dict):
        return values

    for key, value in raw.items():
        key_text = str(key).strip()
        if not key_text:
            continue
        try:
            values[key_text] = float(value)
        except (TypeError, ValueError):
            continue
    return values


def _close_wizard_items_for_section(
    section_index: int,
) -> list[CloseWizardItem]:
    """Возвращает пункты выбранного блока закрытия.

    Args:
        section_index: Индекс блока.

    Returns:
        Список пунктов блока.
    """
    return list(CLOSE_WIZARD_SECTION_ITEMS.get(section_index, ()))


def _close_wizard_next_section_after_completion(
    *,
    current_section: int,
    completed: set[int],
) -> int:
    """Возвращает блок для автоперехода после завершения текущего.

    Args:
        current_section: Индекс текущего блока.
        completed: Множество выполненных пунктов.

    Returns:
        Индекс блока, который нужно показать пользователю.
    """
    if current_section < 0 or current_section >= len(CHECKLISTS["close"]):
        return 0

    current_items = CLOSE_WIZARD_SECTION_ITEMS.get(current_section, ())
    if not current_items:
        return current_section

    current_done = all(item.index in completed for item in current_items)
    if not current_done:
        return current_section

    for section_index in range(current_section + 1, len(CHECKLISTS["close"])):
        section_items = CLOSE_WIZARD_SECTION_ITEMS.get(section_index, ())
        if not section_items:
            continue
        if any(item.index not in completed for item in section_items):
            return section_index

    for section_index in range(0, current_section):
        section_items = CLOSE_WIZARD_SECTION_ITEMS.get(section_index, ())
        if not section_items:
            continue
        if any(item.index not in completed for item in section_items):
            return section_index

    return current_section


def _close_wizard_short_button_text(
    text: str,
    limit: int = 64,
) -> str:
    """Ограничивает текст кнопки до безопасной длины.

    Args:
        text: Исходный текст.
        limit: Максимальная длина.

    Returns:
        Укороченный текст.
    """
    value = text.strip()
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3].rstrip() + "..."


def _close_wizard_item_button_text(
    item_text: str,
    mark: str,
    limit: int = 64,
) -> str:
    """Собирает компактный текст кнопки пункта с чекбоксом.

    Args:
        item_text: Название пункта.
        mark: Символ чекбокса.
        limit: Ограничение длины inline-кнопки Telegram.

    Returns:
        Строка вида `Пункт … ☐`.
    """
    suffix = f" {mark}"
    item_limit = max(4, limit - len(suffix))
    compact = _close_wizard_short_button_text(item_text, limit=item_limit)
    return f"{compact}{suffix}"


def _close_wizard_count_suffix(
    value: int,
) -> str:
    """Возвращает правильное склонение слова «пункт».

    Args:
        value: Количество.

    Returns:
        Строка со склонением.
    """
    mod_10 = value % 10
    mod_100 = value % 100
    if mod_10 == 1 and mod_100 != 11:
        return "пункт"
    if mod_10 in (2, 3, 4) and mod_100 not in (12, 13, 14):
        return "пункта"
    return "пунктов"


def _close_wizard_missing_items(
    completed: set[int],
) -> list[CloseWizardItem]:
    """Возвращает список незавершённых пунктов мастера.

    Args:
        completed: Выполненные пункты.

    Returns:
        Список незавершённых пунктов в порядке чек-листа.
    """
    return [item for item in CLOSE_WIZARD_ITEMS if item.index not in completed]


def _close_wizard_missing_items_preview(
    completed: set[int],
    *,
    limit: int = 5,
) -> str:
    """Строит краткую подсказку по пропущенным пунктам.

    Args:
        completed: Выполненные пункты.
        limit: Максимум отображаемых пунктов.

    Returns:
        Текст-подсказка со списком пропусков.
    """
    missing_items = _close_wizard_missing_items(completed)
    if not missing_items:
        return ""

    lines = ["Нужно выполнить:"]
    for item in missing_items[: max(1, limit)]:
        lines.append(f"• Блок {item.section_index + 1}: {item.text}")

    remaining = len(missing_items) - max(1, limit)
    if remaining > 0:
        lines.append(f"И ещё {remaining} {_close_wizard_count_suffix(remaining)}.")
    return "\n".join(lines)


def _close_wizard_block_screen(
    *,
    section_index: int,
    completed: set[int],
) -> tuple[str, InlineKeyboardMarkup]:
    """Строит экран списка пунктов выбранного блока.

    Args:
        section_index: Индекс блока.
        completed: Выполненные пункты.

    Returns:
        Текст и клавиатура блока.
    """
    items = _close_wizard_items_for_section(section_index)
    if not items:
        return (
            "Блок не найден.",
            InlineKeyboardMarkup(inline_keyboard=[]),
        )

    header_text = build_checklist_text("close", completed, section_index)
    lines = [
        header_text,
        "",
        "Выберите пункт:",
    ]

    rows: list[list[InlineKeyboardButton]] = []
    for item in items:
        mark = "☑" if item.index in completed else "☐"
        item_callback = f"{CLOSE_WIZARD_CALLBACK_PREFIX}:pick:{item.index}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=_close_wizard_item_button_text(item.text, mark, limit=64),
                    callback_data=item_callback,
                ),
            ]
        )

    section_nav_row: list[InlineKeyboardButton] = []
    if section_index > 0:
        section_nav_row.append(
            InlineKeyboardButton(
                text="← Назад",
                callback_data=f"{CLOSE_WIZARD_CALLBACK_PREFIX}:section:{section_index - 1}",
            )
        )
    if section_index < len(CHECKLISTS["close"]) - 1:
        section_nav_row.append(
            InlineKeyboardButton(
                text="➡",
                callback_data=f"{CLOSE_WIZARD_CALLBACK_PREFIX}:section:{section_index + 1}",
            )
        )
    if section_nav_row:
        rows.append(section_nav_row)

    rows.append(
        [
            InlineKeyboardButton(
                text="✅ Завершить смену",
                callback_data=f"{CLOSE_WIZARD_CALLBACK_PREFIX}:finish",
            )
        ]
    )
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _close_wizard_finish_warning_screen(
    completed: set[int],
) -> tuple[str, InlineKeyboardMarkup]:
    """Строит экран предупреждения о незавершённых пунктах.

    Args:
        completed: Выполненные пункты.

    Returns:
        Текст и клавиатура предупреждения.
    """
    total = close_wizard_total_items()
    done = len(completed)
    remaining = max(0, total - done)
    text = (
        f"⚠️ Не все пункты выполнены\n\nВыполнено: {done} из {total}"
    )
    missing_items = _close_wizard_missing_items(completed)
    if missing_items:
        by_section: dict[int, list[str]] = {}
        for item in missing_items:
            by_section.setdefault(item.section_index, []).append(item.text)
        lines = ["\n\nПропущено:"]
        for sec_idx, item_texts in sorted(by_section.items()):
            sec_title = CLOSE_CHECKLIST[sec_idx]["title"] if sec_idx < len(CLOSE_CHECKLIST) else f"Блок {sec_idx + 1}"
            lines.append(f"\n{sec_title}:")
            for t in item_texts:
                lines.append(f"• {t}")
        text += "\n".join(lines)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="К пропущенным пунктам",
                    callback_data=f"{CLOSE_WIZARD_CALLBACK_PREFIX}:finish_return",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Закрыть всё равно",
                    callback_data=f"{CLOSE_WIZARD_CALLBACK_PREFIX}:finish_force",
                ),
            ],
        ]
    )
    return text, keyboard


def _close_wizard_build_screen(
    *,
    active_section: int,
    selected_item_index: int | None,
    finish_confirm: bool,
    completed: set[int],
    values: dict[str, float],
    error_text: str | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    """Формирует экран блока/пункта/подтверждения завершения.

    Args:
        active_section: Текущий блок.
        selected_item_index: Выбранный пункт или None.
        finish_confirm: Флаг экрана подтверждения завершения.
        completed: Выполненные пункты.
        values: Значения вводимых пунктов.
        error_text: Текст ошибки.

    Returns:
        Текст и inline-клавиатура.
    """
    if finish_confirm:
        return _close_wizard_finish_warning_screen(completed)

    if selected_item_index is None:
        return _close_wizard_block_screen(
            section_index=active_section,
            completed=completed,
        )

    item = close_wizard_item_by_index(selected_item_index)
    if not item:
        return _close_wizard_block_screen(
            section_index=active_section,
            completed=completed,
        )
    if item.item_type == "check" and not _close_wizard_item_requires_photo(item):
        return _close_wizard_block_screen(
            section_index=item.section_index,
            completed=completed,
        )

    return (
        build_close_wizard_question_text(
            item=item,
            completed=completed,
            values=values,
            error_text=error_text,
        ),
        build_close_wizard_question_keyboard(item),
    )


async def _render_close_wizard_screen(
    source_message: Message,
    state: FSMContext,
    *,
    active_section: int,
    selected_item_index: int | None,
    finish_confirm: bool,
    completed: set[int],
    values: dict[str, float],
    error_text: str | None = None,
) -> None:
    """Обновляет экран мастера закрытия в одном сообщении.

    Args:
        source_message: Источник обновления.
        state: FSM-контекст.
        active_section: Текущий блок.
        selected_item_index: Выбранный пункт или None.
        finish_confirm: Флаг подтверждения завершения.
        completed: Выполненные пункты.
        values: Значения вводимых пунктов.
        error_text: Текст ошибки.

    Returns:
        None.
    """
    text, keyboard = _close_wizard_build_screen(
        active_section=active_section,
        selected_item_index=selected_item_index,
        finish_confirm=finish_confirm,
        completed=completed,
        values=values,
        error_text=error_text,
    )
    state_data = await state.get_data()

    chat_id_raw = state_data.get(CLOSE_WIZARD_MESSAGE_CHAT_KEY)
    message_id_raw = state_data.get(CLOSE_WIZARD_MESSAGE_ID_KEY)
    try:
        chat_id = int(chat_id_raw)
        message_id = int(message_id_raw)
    except (TypeError, ValueError):
        chat_id = None
        message_id = None

    if chat_id is not None and message_id is not None:
        try:
            await source_message.bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=keyboard,
            )
            return
        except TelegramBadRequest as error:
            error_text_lc = str(error).lower()
            if "message is not modified" in error_text_lc:
                return
            if "message to edit not found" not in error_text_lc and "message can't be edited" not in error_text_lc:
                raise
            logger.warning("Сообщение мастера закрытия не найдено, отправляем новое")

    sent = await source_message.answer(text, reply_markup=keyboard)
    await state.update_data(
        {
            CLOSE_WIZARD_MESSAGE_CHAT_KEY: sent.chat.id,
            CLOSE_WIZARD_MESSAGE_ID_KEY: sent.message_id,
        }
    )


async def _persist_close_wizard_progress(
    *,
    state: FSMContext,
    db: Database,
    shift_id: int,
    completed: set[int],
    active_section: int,
    selected_item_index: int | None,
    finish_confirm: bool,
    values: dict[str, float],
    started_at: str,
) -> None:
    """Сохраняет прогресс мастера в FSM и БД.

    Args:
        state: FSM-контекст.
        db: База данных.
        shift_id: ID смены.
        completed: Выполненные пункты.
        active_section: Текущий блок.
        selected_item_index: Выбранный пункт или None.
        finish_confirm: Флаг подтверждения завершения.
        values: Значения вводимых пунктов.
        started_at: Время старта закрытия.

    Returns:
        None.
    """
    completed_sorted = sorted(completed)
    await state.update_data(
        {
            CLOSE_WIZARD_SHIFT_KEY: shift_id,
            CLOSE_WIZARD_DONE_KEY: completed_sorted,
            CLOSE_WIZARD_SECTION_KEY: active_section,
            CLOSE_WIZARD_INDEX_KEY: selected_item_index,
            CLOSE_WIZARD_FINISH_CONFIRM_KEY: finish_confirm,
            CLOSE_WIZARD_VALUES_KEY: values,
            CLOSE_WIZARD_STARTED_AT_KEY: started_at,
        }
    )
    await db.upsert_checklist_state(
        shift_id=shift_id,
        checklist_type="close",
        completed=completed_sorted,
        active_section=active_section,
    )


async def _start_close_wizard(
    *,
    message: Message,
    state: FSMContext,
    db: Database,
    shift_id: int,
    started_at: str,
) -> None:
    """Инициализирует блоковый сценарий закрытия смены.

    Args:
        message: Входящее сообщение.
        state: FSM-контекст.
        db: База данных.
        shift_id: ID активной смены.
        started_at: Время старта сценария.

    Returns:
        None.
    """
    saved_state = await db.get_checklist_state(
        shift_id=shift_id,
        checklist_type="close",
    )
    completed = _close_wizard_restore_completed(saved_state)

    values: dict[str, float] = {}
    residuals = await db.get_close_residuals(shift_id)
    for index in range(close_wizard_total_items()):
        item = close_wizard_item_by_index(index)
        if not item or item.item_type != "input" or not item.residual_key:
            continue
        residual_data = residuals.get(item.residual_key)
        if not residual_data:
            continue
        input_value_raw = residual_data.get("input_value")
        normalized_value_raw = residual_data.get("normalized_quantity")
        quantity_raw = residual_data.get("quantity")

        if input_value_raw is not None:
            values[item.residual_key] = float(input_value_raw)
            continue

        if normalized_value_raw is not None:
            values[item.residual_key] = close_wizard_restore_display_value(
                item,
                float(normalized_value_raw),
            )
            continue

        if quantity_raw is None:
            continue

        legacy_value = float(quantity_raw)
        if item.residual_key in {"marinated_chicken", "fried_chicken"}:
            legacy_value *= 1000.0
        values[item.residual_key] = legacy_value

    active_section = 0
    if saved_state:
        try:
            active_section = int(saved_state.get("active_section", 0))
        except (TypeError, ValueError):
            active_section = 0
    if active_section < 0 or active_section >= len(CHECKLISTS["close"]):
        first_missing = close_wizard_first_incomplete_index(completed)
        active_section = close_wizard_section_for_index(first_missing)

    await state.set_state(CloseShiftStates.wizard)
    await _persist_close_wizard_progress(
        state=state,
        db=db,
        shift_id=shift_id,
        completed=completed,
        active_section=active_section,
        selected_item_index=None,
        finish_confirm=False,
        values=values,
        started_at=started_at,
    )

    text, keyboard = _close_wizard_build_screen(
        active_section=active_section,
        selected_item_index=None,
        finish_confirm=False,
        completed=completed,
        values=values,
    )
    wizard_message = await message.answer(text, reply_markup=keyboard)
    await state.update_data(
        {
            CLOSE_WIZARD_MESSAGE_CHAT_KEY: wizard_message.chat.id,
            CLOSE_WIZARD_MESSAGE_ID_KEY: wizard_message.message_id,
        }
    )


def _close_wizard_parse_context(
    state_data: dict[str, object],
) -> tuple[int | None, set[int], int, int | None, bool, dict[str, float], str]:
    """Парсит контекст мастера закрытия из FSM-данных.

    Args:
        state_data: Данные FSM пользователя.

    Returns:
        Кортеж: shift_id, completed, active_section, selected_item_index,
        finish_confirm, values, started_at.
    """
    shift_id_raw = state_data.get(CLOSE_WIZARD_SHIFT_KEY)
    try:
        shift_id = int(shift_id_raw)
    except (TypeError, ValueError):
        shift_id = None

    completed: set[int] = set()
    completed_raw = state_data.get(CLOSE_WIZARD_DONE_KEY)
    if isinstance(completed_raw, list):
        for value in completed_raw:
            try:
                completed.add(int(value))
            except (TypeError, ValueError):
                continue

    section_raw = state_data.get(CLOSE_WIZARD_SECTION_KEY)
    try:
        active_section = int(section_raw)
    except (TypeError, ValueError):
        active_section = close_wizard_section_for_index(
            close_wizard_first_incomplete_index(completed)
        )
    if active_section < 0 or active_section >= len(CHECKLISTS["close"]):
        active_section = close_wizard_section_for_index(
            close_wizard_first_incomplete_index(completed)
        )

    selected_item_index: int | None
    index_raw = state_data.get(CLOSE_WIZARD_INDEX_KEY)
    try:
        parsed_index = int(index_raw)
    except (TypeError, ValueError):
        parsed_index = None
    if parsed_index is None:
        selected_item_index = None
    else:
        selected_item_index = (
            parsed_index
            if 0 <= parsed_index < close_wizard_total_items()
            else None
        )

    finish_confirm = bool(state_data.get(CLOSE_WIZARD_FINISH_CONFIRM_KEY))

    values = _close_wizard_restore_values(state_data)
    started_at = str(state_data.get(CLOSE_WIZARD_STARTED_AT_KEY) or "")
    return (
        shift_id,
        completed,
        active_section,
        selected_item_index,
        finish_confirm,
        values,
        started_at,
    )


def _close_wizard_item_index_by_residual_key(
    residual_key: str,
) -> int | None:
    """Возвращает индекс пункта мастера по ключу остатка.

    Args:
        residual_key: Ключ остатка.

    Returns:
        Индекс пункта или None.
    """
    return CLOSE_WIZARD_RESIDUAL_INDEX.get(residual_key)


def _close_wizard_parse_numeric_input(
    *,
    item: CloseWizardItem,
    raw_value: str,
) -> tuple[float | None, str | None]:
    """Валидирует числовой ввод по пункту мастера закрытия.

    Args:
        item: Текущий пункт мастера.
        raw_value: Введённая строка.

    Returns:
        Кортеж из значения и текста ошибки.
    """
    if item.item_type != "input" or not item.input_rule:
        return None, "Этот пункт отмечается галкой в списке."

    parsed = parse_close_residual_value(raw_value, item.residual_key or "")
    if parsed is None:
        return None, "Введите число"
    if not math.isfinite(parsed):
        return None, "Введите корректное число"
    if parsed < 0:
        return None, "Значение должно быть не меньше 0"
    if parsed > item.input_rule.max_value:
        return None, "Значение слишком большое"
    if item.input_rule.only_integer and not float(parsed).is_integer():
        return None, "Введите целое значение"
    if item.input_rule.step is not None and item.input_rule.step > 0:
        step = item.input_rule.step
        scaled = parsed / step
        if abs(scaled - round(scaled)) > 1e-9:
            step_label = fmt_number(step)
            return None, f"Допустимы значения с шагом {step_label}"
    return parsed, None


async def _close_wizard_store_input(
    *,
    db: Database,
    settings: Settings,
    employee: str,
    employee_id: int,
    shift_id: int,
    item: CloseWizardItem,
    display_value: float,
) -> None:
    """Сохраняет введённое значение остатка в БД.

    Args:
        db: Экземпляр базы данных.
        settings: Настройки приложения.
        employee: Имя сотрудника.
        employee_id: Telegram ID сотрудника.
        shift_id: Идентификатор смены.
        item: Пункт мастера.
        display_value: Значение в интерфейсной единице.

    Returns:
        None.
    """
    if not item.residual_key:
        return

    now = now_local(settings)
    normalized_quantity = close_wizard_to_storage_value(item, display_value)
    unit_type = item.input_rule.unit_type if item.input_rule else None
    normalized_unit = close_wizard_normalized_unit(item)
    await db.upsert_close_residual(
        shift_id=shift_id,
        item_key=item.residual_key,
        item_label=item.text,
        quantity=display_value,
        unit=item.input_rule.display_unit if item.input_rule else (item.storage_unit or "шт"),
        input_value=display_value,
        unit_type=unit_type,
        normalized_quantity=normalized_quantity,
        normalized_unit=normalized_unit,
        residual_date=now.date().isoformat(),
        residual_time=now.time().replace(microsecond=0).isoformat(),
        employee=employee,
        employee_id=employee_id,
    )
    logger.debug(
        "Residual saved: shift_id=%s key=%s value=%s normalized=%s unit=%s",
        shift_id,
        item.residual_key,
        display_value,
        normalized_quantity,
        normalized_unit,
    )


def _get_normalized_residual(
    residuals: dict[str, dict[str, object]],
    item_key: str,
) -> float:
    """Возвращает нормализованное значение остатка.

    Args:
        residuals: Словарь остатков смены.
        item_key: Ключ остатка.

    Returns:
        Нормализованное значение в базовой единице.
    """
    row = residuals[item_key]
    normalized_raw = row.get("normalized_quantity")
    if normalized_raw is not None:
        return float(normalized_raw)

    raw_quantity = float(row.get("quantity") or 0.0)
    unit_type = str(row.get("unit_type") or "").strip()

    if unit_type in {"gastro_unit", "legacy_ml", "sauce_gastro"}:
        return raw_quantity
    if item_key in {"marinated_chicken", "fried_chicken"}:
        # Исторический формат: quantity хранилось в кг.
        return raw_quantity * 1000.0
    return raw_quantity


def _get_display_residual(
    residuals: dict[str, dict[str, object]],
    item_key: str,
) -> float:
    """Возвращает значение остатка в интерфейсной единице.

    Args:
        residuals: Словарь остатков смены.
        item_key: Ключ остатка.

    Returns:
        Значение в пользовательской единице.
    """
    row = residuals[item_key]
    input_value_raw = row.get("input_value")
    if input_value_raw is not None:
        return float(input_value_raw)

    normalized = _get_normalized_residual(residuals, item_key)
    return normalized


def _get_residual_display_unit(
    residuals: dict[str, dict[str, object]],
    item_key: str,
    default_unit: str,
) -> str:
    """Возвращает единицу отображения остатка.

    Args:
        residuals: Словарь остатков смены.
        item_key: Ключ остатка.
        default_unit: Единица по умолчанию.

    Returns:
        Единица отображения.
    """
    row = residuals.get(item_key, {})
    unit_raw = row.get("unit") if isinstance(row, dict) else None
    unit = str(unit_raw or "").strip()
    return unit or default_unit


def _close_duration_label(
    close_duration_sec: int | None,
) -> str:
    """Возвращает человекочитаемую длительность закрытия.

    Args:
        close_duration_sec: Длительность в секундах.

    Returns:
        Текст длительности в минутах.
    """
    if close_duration_sec is None:
        return "н/д"
    duration_min = round(close_duration_sec / 60, 1)
    return f"{fmt_number(duration_min)} мин"


def _format_hhmm(
    raw_value: str | None,
) -> str:
    """Форматирует дату/время в строку HH:MM.

    Args:
        raw_value: ISO-строка даты/времени.

    Returns:
        Строка HH:MM или `--:--`.
    """
    if not raw_value:
        return "--:--"
    text = str(raw_value).strip()
    if not text:
        return "--:--"
    try:
        return datetime.fromisoformat(text).strftime("%H:%M")
    except ValueError:
        if len(text) >= 5 and text[2] == ":":
            return text[:5]
        return "--:--"


def _build_close_done_summary_text(
    *,
    employee: str,
    closed_at_hhmm: str,
    duration_label: str,
    all_items_completed: bool,
) -> str:
    """Строит основной экран успешного закрытия смены.

    Args:
        employee: Член команды.
        closed_at_hhmm: Время закрытия в формате HH:MM.
        duration_label: Текст длительности.
        all_items_completed: Признак полного заполнения.

    Returns:
        Текст экрана завершения.
    """
    status_line = "✅ Всё заполнено" if all_items_completed else "⚠ Есть незаполненные пункты"
    return (
        "✅ Смена закрыта\n\n"
        f"Член команды: {employee}\n"
        f"Время: {closed_at_hhmm}\n"
        f"Длительность: {duration_label}\n\n"
        f"Статус:\n{status_line}\n\n"
        "Спасибо за смену 🙌"
    )


def _build_close_done_summary_keyboard(
    shift_id: int,
) -> InlineKeyboardMarkup:
    """Строит клавиатуру основного экрана закрытия.

    Args:
        shift_id: Идентификатор смены.

    Returns:
        Inline-клавиатура.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 Показать детали",
                    callback_data=f"{CLOSE_DONE_CALLBACK_PREFIX}:details:{shift_id}",
                )
            ]
        ]
    )


def _build_close_done_details_text(
    *,
    marinated_chicken_kg: float,
    fried_chicken_kg: float,
    lavash: float,
    fried_lavash: float,
    soup: float,
    soup_unit: str,
    sauce: float,
    sauce_unit: str,
) -> str:
    """Строит экран деталей закрытой смены.

    Args:
        marinated_chicken_kg: Остаток маринованной курицы (кг).
        fried_chicken_kg: Остаток жареной курицы (кг).
        lavash: Остаток лаваша (шт).
        fried_lavash: Остаток жареного лаваша (шт).
        soup: Остаток супа в отображаемой единице.
        soup_unit: Единица измерения супа.
        sauce: Остаток соуса в отображаемой единице.
        sauce_unit: Единица измерения соуса.

    Returns:
        Текст экрана деталей.
    """
    return (
        "📊 Детали смены\n\n"
        "Остатки:\n\n"
        f"🥩 Маринованная курица — {fmt_number(marinated_chicken_kg)} кг\n"
        f"🍗 Жареная курица — {fmt_number(fried_chicken_kg)} кг\n"
        f"🌯 Лаваш — {fmt_number(lavash)} шт\n"
        f"🥙 Жареный лаваш — {fmt_number(fried_lavash)} шт\n"
        f"🍲 Суп — {fmt_number(soup)} {soup_unit}\n"
        f"🧴 Соус — {fmt_number(sauce)} {sauce_unit}"
    )


def _build_close_done_details_keyboard(
    shift_id: int,
) -> InlineKeyboardMarkup:
    """Строит клавиатуру экрана деталей закрытой смены.

    Args:
        shift_id: Идентификатор смены.

    Returns:
        Inline-клавиатура.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅ Назад",
                    callback_data=f"{CLOSE_DONE_CALLBACK_PREFIX}:back:{shift_id}",
                )
            ]
        ]
    )


async def _edit_close_wizard_message(
    *,
    source_message: Message,
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    log_context: str,
) -> None:
    """Редактирует сохранённое сообщение мастера закрытия или отправляет новое.

    Args:
        source_message: Текущее сообщение-источник события.
        state: FSM-контекст пользователя.
        text: Новый текст.
        reply_markup: Inline-клавиатура или None.
        log_context: Метка контекста для логов.

    Returns:
        None.
    """
    state_data = await state.get_data()
    chat_id_raw = state_data.get(CLOSE_WIZARD_MESSAGE_CHAT_KEY)
    message_id_raw = state_data.get(CLOSE_WIZARD_MESSAGE_ID_KEY)
    try:
        chat_id = int(chat_id_raw)
        message_id = int(message_id_raw)
    except (TypeError, ValueError):
        chat_id = None
        message_id = None

    if chat_id is not None and message_id is not None:
        try:
            await source_message.bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=reply_markup,
            )
            return
        except TelegramBadRequest as error:
            error_text_lc = str(error).lower()
            if "message is not modified" in error_text_lc:
                logger.info("Skipped %s edit: no changes", log_context)
                return
            if "message to edit not found" not in error_text_lc and "message can't be edited" not in error_text_lc:
                raise
            logger.warning("Сообщение мастера закрытия не найдено, отправляем новое")

    sent = await source_message.answer(text, reply_markup=reply_markup)
    await state.update_data(
        {
            CLOSE_WIZARD_MESSAGE_CHAT_KEY: sent.chat.id,
            CLOSE_WIZARD_MESSAGE_ID_KEY: sent.message_id,
        }
    )


async def _close_wizard_finalize(
    state: FSMContext,
    db: Database,
    settings: Settings,
    bot: Bot,
    *,
    callback: CallbackQuery | None = None,
    source_message: Message | None = None,
    actor_id: int | None = None,
    actor_username: str | None = None,
    actor_full_name: str | None = None,
    force: bool = False,
) -> bool:
    """Финализирует закрытие смены из мастера.

    Args:
        state: FSM-контекст пользователя.
        db: Экземпляр базы данных.
        settings: Настройки приложения.
        bot: Экземпляр Telegram-бота.
        callback: Callback-запрос Telegram (если финализация вызвана из кнопки).
        source_message: Сообщение-источник (если финализация вызвана не из callback).
        actor_id: Telegram ID сотрудника.
        actor_username: Username сотрудника.
        actor_full_name: Полное имя сотрудника.

    Returns:
        True, если смена успешно закрыта.
    """
    if callback is not None:
        if not callback.message or not callback.from_user:
            return False
        source_message = callback.message
        actor_id = callback.from_user.id
        actor_username = callback.from_user.username
        actor_full_name = callback.from_user.full_name

    if source_message is None or actor_id is None:
        return False
    actor_name = str(actor_full_name or "").strip() or "Сотрудник"
    employee = f"@{actor_username}" if actor_username else actor_name

    async def _respond(
        text: str | None = None,
        *,
        show_alert: bool = False,
    ) -> None:
        if callback is not None:
            await safe_answer_callback(
                callback,
                text,
                show_alert=show_alert,
                log_context="close wizard finalize",
            )
            return
        if text:
            prefix = "⚠️ " if show_alert else ""
            await source_message.answer(prefix + text)

    state_data = await state.get_data()
    (
        shift_id,
        completed,
        active_section,
        selected_item_index,
        _finish_confirm,
        values,
        started_at,
    ) = _close_wizard_parse_context(state_data)
    if shift_id is None:
        await _respond("Сценарий закрытия потерян. Запустите /close заново.", show_alert=True)
        return False

    total = close_wizard_total_items()
    if not force and len(completed) < total:
        first_missing = close_wizard_first_incomplete_index(completed)
        if first_missing < total:
            active_section = close_wizard_section_for_index(first_missing)
        await _persist_close_wizard_progress(
            state=state,
            db=db,
            shift_id=shift_id,
            completed=completed,
            active_section=active_section,
            selected_item_index=None,
            finish_confirm=True,
            values=values,
            started_at=started_at,
        )
        await _render_close_wizard_screen(
            source_message=source_message,
            state=state,
            active_section=active_section,
            selected_item_index=None,
            finish_confirm=True,
            completed=completed,
            values=values,
        )
        await _respond(
            "Нельзя закрыть смену, пока не завершены все пункты чек-листа.",
            show_alert=True,
        )
        return False

    residuals = await db.get_close_residuals(shift_id)
    missing_residual_keys = [key for key in CLOSE_REQUIRED_RESIDUAL_KEYS if key not in residuals]
    if missing_residual_keys:
        missing_label = CLOSE_RESIDUAL_LABELS.get(missing_residual_keys[0], missing_residual_keys[0])
        target_index = _close_wizard_item_index_by_residual_key(missing_residual_keys[0])
        if target_index is not None:
            active_section = close_wizard_section_for_index(target_index)
            selected_item_index = target_index
        else:
            selected_item_index = None
        await _persist_close_wizard_progress(
            state=state,
            db=db,
            shift_id=shift_id,
            completed=completed,
            active_section=active_section,
            selected_item_index=selected_item_index,
            finish_confirm=False,
            values=values,
            started_at=started_at,
        )
        await _render_close_wizard_screen(
            source_message=source_message,
            state=state,
            active_section=active_section,
            selected_item_index=selected_item_index,
            finish_confirm=False,
            completed=completed,
            values=values,
            error_text=f"Вы не заполнили остаток: {missing_label}",
        )
        await _respond(f"Вы не заполнили остаток: {missing_label}", show_alert=True)
        return False

    try:
        return await _close_wizard_finalize_inner(
            state=state,
            db=db,
            settings=settings,
            bot=bot,
            source_message=source_message,
            actor_id=actor_id,
            employee=employee,
            shift_id=shift_id,
            completed=completed,
            active_section=active_section,
            selected_item_index=selected_item_index,
            values=values,
            started_at=started_at,
            residuals=residuals,
            _respond=_respond,
        )
    except Exception:
        logger.exception(
            "Unexpected error during shift close finalization shift_id=%s actor_id=%s",
            shift_id,
            actor_id,
        )
        await _respond(
            "Произошла ошибка при закрытии смены. Попробуйте ещё раз или обратитесь к администратору.",
            show_alert=True,
        )
        return False


async def _close_wizard_finalize_inner(
    state: FSMContext,
    db: Database,
    settings: Settings,
    bot: Bot,
    *,
    source_message: Message,
    actor_id: int,
    employee: str,
    shift_id: int,
    completed: set[int],
    active_section: int,
    selected_item_index: int | None,
    values: dict[str, object],
    started_at: str | None,
    residuals: dict,
    _respond,
) -> bool:
    """Выполняет фактическую финализацию закрытия смены.

    Вызывается из _close_wizard_finalize после извлечения контекста и валидации.
    """
    now = now_local(settings)
    shift = await db.get_shift_by_id(shift_id)
    started_at_value = (
        started_at
        or str((shift or {}).get("close_started_at") or (shift or {}).get("open_time") or "")
    )
    started_dt: datetime | None
    if not started_at_value:
        started_dt = None
    else:
        try:
            started_dt = datetime.fromisoformat(started_at_value)
        except ValueError:
            logger.warning(
                "Invalid close_started_at for shift_id=%s: %r — duration will not be recorded",
                shift_id,
                started_at_value,
            )
            started_dt = None

    if started_dt and started_dt.tzinfo is None:
        started_dt = started_dt.replace(tzinfo=now.tzinfo)

    close_duration_sec: int | None = None
    if started_dt:
        close_duration_sec = max(0, int((now - started_dt).total_seconds()))

    marinated_chicken_g = (
        _get_normalized_residual(residuals, "marinated_chicken")
        if "marinated_chicken" in residuals
        else 0.0
    )
    fried_chicken_g = (
        _get_normalized_residual(residuals, "fried_chicken")
        if "fried_chicken" in residuals
        else 0.0
    )
    lavash = _get_normalized_residual(residuals, "lavash") if "lavash" in residuals else 0.0
    fried_lavash = (
        _get_normalized_residual(residuals, "fried_lavash")
        if "fried_lavash" in residuals
        else 0.0
    )
    soup_l = _get_display_residual(residuals, "soup") if "soup" in residuals else 0.0
    sauce_l = _get_display_residual(residuals, "sauce") if "sauce" in residuals else 0.0

    marinated_chicken = marinated_chicken_g / 1000.0
    fried_chicken = fried_chicken_g / 1000.0

    meat_used = await db.close_shift(
        shift_id=shift_id,
        close_time=now.isoformat(timespec="minutes"),
        revenue=0.0,
        photo=None,
        meat_end=marinated_chicken,
        lavash_end=lavash,
        closed_by_id=actor_id,
        closed_by_name=employee,
        close_duration_sec=close_duration_sec,
    )
    logger.info(
        "Shift record closed: shift_id=%s meat_end=%.3f lavash_end=%.1f meat_used=%s duration_sec=%s",
        shift_id,
        marinated_chicken,
        lavash,
        meat_used,
        close_duration_sec,
    )

    stock_date = now.date().isoformat()
    stock_time = now.time().replace(microsecond=0).isoformat()
    await db.save_stock(
        item="мясо",
        quantity=marinated_chicken,
        stock_date=stock_date,
        employee=employee,
        employee_id=actor_id,
        stock_time=stock_time,
    )
    await db.save_stock(
        item="лаваш",
        quantity=lavash,
        stock_date=stock_date,
        employee=employee,
        employee_id=actor_id,
        stock_time=stock_time,
    )
    logger.debug(
        "Stock saved: shift_id=%s мясо=%.3f лаваш=%.1f",
        shift_id,
        marinated_chicken,
        lavash,
    )

    duration_text = _close_duration_label(close_duration_sec)
    all_items_completed = len(completed) == close_wizard_total_items()

    owner_close_text = (
        "Смена закрыта\n"
        f"Сотрудник: {employee}\n"
        f"Время: {now.strftime('%H:%M')}\n"
        f"Длительность закрытия: {duration_text}\n"
        f"Расход мяса: {meat_used if meat_used is not None else 'н/д'}\n"
        f"Остаток маринованной курицы: {fmt_number(marinated_chicken)} кг\n"
        f"Остаток жареной курицы: {fmt_number(fried_chicken)} кг\n"
        f"Остаток лаваша: {fmt_number(lavash)} шт\n"
        f"Остаток жареного лаваша: {fmt_number(fried_lavash)} шт\n"
        f"Остаток супа: {fmt_number(soup_l)} л\n"
        f"Остаток соуса: {fmt_number(sauce_l)} л"
    )

    close_report_text = (
        "📊 Закрытие смены\n\n"
        f"Дата: {now.date().isoformat()}\n"
        f"Время: {now.strftime('%H:%M')}\n"
        f"Член команды: {employee}\n"
        f"Длительность: {duration_text}\n\n"
        "Остатки:\n\n"
        f"Маринованная курица — {fmt_number(marinated_chicken)} кг\n"
        f"Жареная курица — {fmt_number(fried_chicken)} кг\n"
        f"Лаваш — {fmt_number(lavash)} шт\n"
        f"Жареный лаваш — {fmt_number(fried_lavash)} шт\n"
        f"Суп — {fmt_number(soup_l)} л\n"
        f"Соус — {fmt_number(sauce_l)} л"
    )
    if not all_items_completed:
        missing_close = _close_wizard_missing_items(completed)
        by_sec: dict[int, list[str]] = {}
        for item in missing_close:
            by_sec.setdefault(item.section_index, []).append(item.text)
        skip_lines = ["\n\nПропущенные пункты чек-листа:"]
        for sec_idx, item_texts in sorted(by_sec.items()):
            sec_title = CLOSE_CHECKLIST[sec_idx]["title"] if sec_idx < len(CLOSE_CHECKLIST) else f"Блок {sec_idx + 1}"
            skip_lines.append(f"\n{sec_title}:")
            for t in item_texts:
                skip_lines.append(f"• {t}")
        skipped_block = "\n".join(skip_lines)
        owner_close_text += skipped_block
        close_report_text += skipped_block

    work_chat_status = await notify_work_chat(bot, settings, close_report_text)
    if (
        settings.owner_id != settings.work_chat_id
        and work_chat_status != "owner_fallback"
    ):
        await notify_owner(bot, settings, owner_close_text)

    confirmation_text = _build_close_done_summary_text(
        employee=employee,
        closed_at_hhmm=now.strftime("%H:%M"),
        duration_label=duration_text,
        all_items_completed=all_items_completed,
    )
    await _edit_close_wizard_message(
        source_message=source_message,
        state=state,
        text=confirmation_text,
        reply_markup=_build_close_done_summary_keyboard(shift_id),
        log_context="close done summary",
    )
    await _respond("Смена закрыта")
    await state.clear()
    await source_message.answer(
        "Главное меню:",
        reply_markup=build_shift_menu_keyboard(is_shift_open=False),
    )
    logger.info(
        "Смена закрыта shift_id=%s employee_id=%s duration_sec=%s",
        shift_id,
        actor_id,
        close_duration_sec,
    )
    return True


@shift_router.callback_query(F.data.startswith(f"{CLOSE_DONE_CALLBACK_PREFIX}:"))
async def close_done_callback(
    callback: CallbackQuery,
    db: Database,
) -> None:
    """Показывает основной экран/детали после закрытия смены.

    Args:
        callback: Callback-запрос Telegram.
        db: Экземпляр базы данных.

    Returns:
        None.
    """
    if not callback.data or not callback.message:
        return

    async def _answer(text: str | None = None, *, show_alert: bool = False) -> None:
        await safe_answer_callback(
            callback,
            text,
            show_alert=show_alert,
            log_context="close done callback",
        )

    parts = callback.data.split(":")
    if len(parts) != 3:
        await _answer()
        return

    _, action, shift_id_raw = parts
    try:
        shift_id = int(shift_id_raw)
    except ValueError:
        await _answer("Некорректный идентификатор смены", show_alert=True)
        return

    shift = await db.get_shift_by_id(shift_id)
    if not shift:
        await _answer("Смена не найдена", show_alert=True)
        return

    residuals = await db.get_close_residuals(shift_id)

    marinated_chicken_g = (
        _get_normalized_residual(residuals, "marinated_chicken")
        if "marinated_chicken" in residuals
        else 0.0
    )
    fried_chicken_g = (
        _get_normalized_residual(residuals, "fried_chicken")
        if "fried_chicken" in residuals
        else 0.0
    )
    lavash = _get_normalized_residual(residuals, "lavash") if "lavash" in residuals else 0.0
    fried_lavash = (
        _get_normalized_residual(residuals, "fried_lavash")
        if "fried_lavash" in residuals
        else 0.0
    )
    soup = _get_display_residual(residuals, "soup") if "soup" in residuals else 0.0
    sauce = _get_display_residual(residuals, "sauce") if "sauce" in residuals else 0.0
    soup_unit = _get_residual_display_unit(residuals, "soup", "л")
    sauce_unit = _get_residual_display_unit(residuals, "sauce", "л")

    marinated_chicken_kg = marinated_chicken_g / 1000.0
    fried_chicken_kg = fried_chicken_g / 1000.0

    if action == "details":
        details_text = _build_close_done_details_text(
            marinated_chicken_kg=marinated_chicken_kg,
            fried_chicken_kg=fried_chicken_kg,
            lavash=lavash,
            fried_lavash=fried_lavash,
            soup=soup,
            soup_unit=soup_unit,
            sauce=sauce,
            sauce_unit=sauce_unit,
        )
        await safe_edit_text(
            callback.message,
            details_text,
            reply_markup=_build_close_done_details_keyboard(shift_id),
            log_context="close done details",
        )
        await _answer()
        return

    if action == "back":
        close_state = await db.get_checklist_state(
            shift_id=shift_id,
            checklist_type="close",
        )
        completed_items = len(close_state.get("completed", [])) if close_state else 0
        all_items_completed = completed_items >= close_wizard_total_items()

        employee = str(
            shift.get("closed_by_name")
            or shift.get("opened_by")
            or shift.get("employee")
            or "Сотрудник"
        )
        summary_text = _build_close_done_summary_text(
            employee=employee,
            closed_at_hhmm=_format_hhmm(str(shift.get("closed_at") or shift.get("close_time") or "")),
            duration_label=_close_duration_label(
                int(shift["close_duration_sec"]) if shift.get("close_duration_sec") is not None else None
            ),
            all_items_completed=all_items_completed,
        )
        await safe_edit_text(
            callback.message,
            summary_text,
            reply_markup=_build_close_done_summary_keyboard(shift_id),
            log_context="close done summary",
        )
        await _answer()
        return

    await _answer()


@shift_router.callback_query(F.data.startswith(f"{CLOSE_WIZARD_CALLBACK_PREFIX}:"))
async def close_wizard_callback(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    settings: Settings,
    bot: Bot,
) -> None:
    """Обрабатывает inline-действия мастера закрытия смены.

    Args:
        callback: Callback-запрос Telegram.
        state: FSM-контекст пользователя.
        db: Экземпляр базы данных.
        settings: Настройки приложения.
        bot: Экземпляр Telegram-бота.

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
            log_context="close wizard callback",
        )

    if await state.get_state() != CloseShiftStates.wizard.state:
        await _answer("Сценарий неактивен. Запустите /close заново.", show_alert=True)
        return

    parts = callback.data.split(":", 2)
    if len(parts) < 2:
        await _answer()
        return
    action = parts[1]
    action_value = parts[2] if len(parts) == 3 else ""

    state_data = await state.get_data()
    (
        shift_id,
        completed,
        active_section,
        selected_item_index,
        finish_confirm,
        values,
        started_at,
    ) = _close_wizard_parse_context(state_data)
    if shift_id is None:
        await _answer("Не удалось определить смену. Запустите /close заново.", show_alert=True)
        return

    if action == "finish":
        if len(completed) < close_wizard_total_items():
            target_section = close_wizard_section_for_index(
                close_wizard_first_incomplete_index(completed)
            )
            await _persist_close_wizard_progress(
                state=state,
                db=db,
                shift_id=shift_id,
                completed=completed,
                active_section=target_section,
                selected_item_index=None,
                finish_confirm=True,
                values=values,
                started_at=started_at,
            )
            await _render_close_wizard_screen(
                source_message=callback.message,
                state=state,
                active_section=target_section,
                selected_item_index=None,
                finish_confirm=True,
                completed=completed,
                values=values,
            )
            await _answer(
                "Нельзя закрыть смену: выполните все пропущенные пункты.",
                show_alert=True,
            )
            return
        await _close_wizard_finalize(
            callback=callback,
            state=state,
            db=db,
            settings=settings,
            bot=bot,
        )
        return

    if action in {"finish_return", "return"}:
        target_section = active_section
        if len(completed) < close_wizard_total_items():
            target_section = close_wizard_section_for_index(
                close_wizard_first_incomplete_index(completed)
            )
        await _persist_close_wizard_progress(
            state=state,
            db=db,
            shift_id=shift_id,
            completed=completed,
            active_section=target_section,
            selected_item_index=None,
            finish_confirm=False,
            values=values,
            started_at=started_at,
        )
        await _render_close_wizard_screen(
            source_message=callback.message,
            state=state,
            active_section=target_section,
            selected_item_index=None,
            finish_confirm=False,
            completed=completed,
            values=values,
        )
        await _answer()
        return

    if action == "finish_force":
        await _close_wizard_finalize(
            callback=callback,
            state=state,
            db=db,
            settings=settings,
            bot=bot,
            force=True,
        )
        return

    if action == "section":
        try:
            requested_section = int(action_value)
        except ValueError:
            await _answer("Некорректный блок", show_alert=True)
            return
        if requested_section < 0 or requested_section >= len(CHECKLISTS["close"]):
            await _answer("Некорректный блок", show_alert=True)
            return
        await _persist_close_wizard_progress(
            state=state,
            db=db,
            shift_id=shift_id,
            completed=completed,
            active_section=requested_section,
            selected_item_index=None,
            finish_confirm=False,
            values=values,
            started_at=started_at,
        )
        await _render_close_wizard_screen(
            source_message=callback.message,
            state=state,
            active_section=requested_section,
            selected_item_index=None,
            finish_confirm=False,
            completed=completed,
            values=values,
        )
        await _answer()
        return

    if action == "pick":
        try:
            picked_index = int(action_value)
        except ValueError:
            await _answer("Некорректный пункт", show_alert=True)
            return
        item = close_wizard_item_by_index(picked_index)
        if not item:
            await _answer("Некорректный пункт", show_alert=True)
            return
        if item.item_type == "check":
            if _close_wizard_item_requires_photo(item):
                if item.index in completed:
                    completed.remove(item.index)
                    await _persist_close_wizard_progress(
                        state=state,
                        db=db,
                        shift_id=shift_id,
                        completed=completed,
                        active_section=item.section_index,
                        selected_item_index=None,
                        finish_confirm=False,
                        values=values,
                        started_at=started_at,
                    )
                    await _render_close_wizard_screen(
                        source_message=callback.message,
                        state=state,
                        active_section=item.section_index,
                        selected_item_index=None,
                        finish_confirm=False,
                        completed=completed,
                        values=values,
                    )
                    await _answer()
                    return

                await _persist_close_wizard_progress(
                    state=state,
                    db=db,
                    shift_id=shift_id,
                    completed=completed,
                    active_section=item.section_index,
                    selected_item_index=item.index,
                    finish_confirm=False,
                    values=values,
                    started_at=started_at,
                )
                await _render_close_wizard_screen(
                    source_message=callback.message,
                    state=state,
                    active_section=item.section_index,
                    selected_item_index=item.index,
                    finish_confirm=False,
                    completed=completed,
                    values=values,
                    error_text="Отправьте фото (камера или галерея)",
                )
                await _answer("Жду фото")
                return

            toggled_to_done = False
            if item.index in completed:
                completed.remove(item.index)
            else:
                completed.add(item.index)
                toggled_to_done = True
            target_section = item.section_index
            if toggled_to_done:
                target_section = _close_wizard_next_section_after_completion(
                    current_section=item.section_index,
                    completed=completed,
                )
            await _persist_close_wizard_progress(
                state=state,
                db=db,
                shift_id=shift_id,
                completed=completed,
                active_section=target_section,
                selected_item_index=None,
                finish_confirm=False,
                values=values,
                started_at=started_at,
            )
            if toggled_to_done and len(completed) == close_wizard_total_items():
                await _close_wizard_finalize(
                    callback=callback,
                    state=state,
                    db=db,
                    settings=settings,
                    bot=bot,
                )
                return
            await _render_close_wizard_screen(
                source_message=callback.message,
                state=state,
                active_section=target_section,
                selected_item_index=None,
                finish_confirm=False,
                completed=completed,
                values=values,
            )
            await _answer()
            return
        await _persist_close_wizard_progress(
            state=state,
            db=db,
            shift_id=shift_id,
            completed=completed,
            active_section=item.section_index,
            selected_item_index=item.index,
            finish_confirm=False,
            values=values,
            started_at=started_at,
        )
        await _render_close_wizard_screen(
            source_message=callback.message,
            state=state,
            active_section=item.section_index,
            selected_item_index=item.index,
            finish_confirm=False,
            completed=completed,
            values=values,
        )
        await _answer("Введите значение")
        return

    if action in {"back", "skip"}:
        await _persist_close_wizard_progress(
            state=state,
            db=db,
            shift_id=shift_id,
            completed=completed,
            active_section=active_section,
            selected_item_index=None,
            finish_confirm=False,
            values=values,
            started_at=started_at,
        )
        await _render_close_wizard_screen(
            source_message=callback.message,
            state=state,
            active_section=active_section,
            selected_item_index=None,
            finish_confirm=False,
            completed=completed,
            values=values,
        )
        await _answer()
        return

    if finish_confirm:
        await _render_close_wizard_screen(
            source_message=callback.message,
            state=state,
            active_section=active_section,
            selected_item_index=None,
            finish_confirm=True,
            completed=completed,
            values=values,
        )
        await _answer()
        return

    if selected_item_index is None:
        await _answer("Сначала выберите пункт", show_alert=True)
        return

    item = close_wizard_item_by_index(selected_item_index)
    if not item:
        await _answer("Пункт не найден. Запустите /close заново.", show_alert=True)
        return

    if action == "done":
        if item.item_type != "check":
            await _answer("Введите число текстом.", show_alert=True)
            return
        completed.add(item.index)
        target_section = _close_wizard_next_section_after_completion(
            current_section=item.section_index,
            completed=completed,
        )
        await _persist_close_wizard_progress(
            state=state,
            db=db,
            shift_id=shift_id,
            completed=completed,
            active_section=target_section,
            selected_item_index=None,
            finish_confirm=False,
            values=values,
            started_at=started_at,
        )
        if len(completed) == close_wizard_total_items():
            await _close_wizard_finalize(
                callback=callback,
                state=state,
                db=db,
                settings=settings,
                bot=bot,
            )
            return
        await _render_close_wizard_screen(
            source_message=callback.message,
            state=state,
            active_section=target_section,
            selected_item_index=None,
            finish_confirm=False,
            completed=completed,
            values=values,
        )
        await _answer()
        return

    if action == "quick":
        parsed_value, error_text = _close_wizard_parse_numeric_input(
            item=item,
            raw_value=action_value,
        )
        if parsed_value is None:
            await _render_close_wizard_screen(
                source_message=callback.message,
                state=state,
                active_section=item.section_index,
                selected_item_index=item.index,
                finish_confirm=False,
                completed=completed,
                values=values,
                error_text=error_text,
            )
            await _answer(error_text or "Ошибка ввода", show_alert=True)
            return

        await _close_wizard_store_input(
            db=db,
            settings=settings,
            employee=f"@{callback.from_user.username}"
            if callback.from_user.username
            else (callback.from_user.full_name or "—"),
            employee_id=callback.from_user.id,
            shift_id=shift_id,
            item=item,
            display_value=parsed_value,
        )
        if item.residual_key:
            values[item.residual_key] = parsed_value
        completed.add(item.index)
        target_section = _close_wizard_next_section_after_completion(
            current_section=item.section_index,
            completed=completed,
        )
        await _persist_close_wizard_progress(
            state=state,
            db=db,
            shift_id=shift_id,
            completed=completed,
            active_section=target_section,
            selected_item_index=None,
            finish_confirm=False,
            values=values,
            started_at=started_at,
        )
        if len(completed) == close_wizard_total_items():
            await _close_wizard_finalize(
                callback=callback,
                state=state,
                db=db,
                settings=settings,
                bot=bot,
            )
            return
        await _render_close_wizard_screen(
            source_message=callback.message,
            state=state,
            active_section=target_section,
            selected_item_index=None,
            finish_confirm=False,
            completed=completed,
            values=values,
        )
        await _answer()
        return

    await _answer()


@shift_router.message(CloseShiftStates.wizard, F.text)
async def close_wizard_text_input(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
    bot: Bot,
) -> None:
    """Обрабатывает текстовый ввод числовых пунктов мастера закрытия.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.
        db: Экземпляр базы данных.
        settings: Настройки приложения.
        bot: Экземпляр Telegram-бота.

    Returns:
        None.
    """
    if not message.text or not message.from_user:
        return
    if message.text.startswith("/"):
        return
    await safe_delete_message(message, log_context="close wizard text input")

    state_data = await state.get_data()
    (
        shift_id,
        completed,
        active_section,
        selected_item_index,
        finish_confirm,
        values,
        started_at,
    ) = _close_wizard_parse_context(state_data)
    if shift_id is None:
        await message.answer("Сценарий закрытия потерян. Запустите /close заново.")
        return

    if finish_confirm:
        await _render_close_wizard_screen(
            source_message=message,
            state=state,
            active_section=active_section,
            selected_item_index=None,
            finish_confirm=True,
            completed=completed,
            values=values,
        )
        return

    if selected_item_index is None:
        await _render_close_wizard_screen(
            source_message=message,
            state=state,
            active_section=active_section,
            selected_item_index=None,
            finish_confirm=False,
            completed=completed,
            values=values,
            error_text="Выберите пункт кнопкой ниже",
        )
        return

    item = close_wizard_item_by_index(selected_item_index)
    if not item:
        await message.answer("Пункт не найден. Запустите /close заново.")
        return
    if item.item_type != "input":
        if _close_wizard_item_requires_photo(item):
            await _render_close_wizard_screen(
                source_message=message,
                state=state,
                active_section=item.section_index,
                selected_item_index=item.index,
                finish_confirm=False,
                completed=completed,
                values=values,
                error_text="Нужно отправить фото",
            )
            return

        await _persist_close_wizard_progress(
            state=state,
            db=db,
            shift_id=shift_id,
            completed=completed,
            active_section=item.section_index,
            selected_item_index=None,
            finish_confirm=False,
            values=values,
            started_at=started_at,
        )
        await _render_close_wizard_screen(
            source_message=message,
            state=state,
            active_section=item.section_index,
            selected_item_index=None,
            finish_confirm=False,
            completed=completed,
            values=values,
            error_text="Этот пункт отмечается галкой в списке",
        )
        return

    parsed_value, error_text = _close_wizard_parse_numeric_input(
        item=item,
        raw_value=message.text,
    )
    if parsed_value is None:
        await _render_close_wizard_screen(
            source_message=message,
            state=state,
            active_section=item.section_index,
            selected_item_index=item.index,
            finish_confirm=False,
            completed=completed,
            values=values,
            error_text=error_text,
        )
        return

    await _close_wizard_store_input(
        db=db,
        settings=settings,
        employee=employee_name(message),
        employee_id=message.from_user.id,
        shift_id=shift_id,
        item=item,
        display_value=parsed_value,
    )
    if item.residual_key:
        values[item.residual_key] = parsed_value
    completed.add(item.index)
    target_section = _close_wizard_next_section_after_completion(
        current_section=item.section_index,
        completed=completed,
    )
    await _persist_close_wizard_progress(
        state=state,
        db=db,
        shift_id=shift_id,
        completed=completed,
        active_section=target_section,
        selected_item_index=None,
        finish_confirm=False,
        values=values,
        started_at=started_at,
    )
    if len(completed) == close_wizard_total_items():
        await _close_wizard_finalize(
            state=state,
            db=db,
            settings=settings,
            bot=bot,
            source_message=message,
            actor_id=message.from_user.id,
            actor_username=message.from_user.username,
            actor_full_name=message.from_user.full_name,
        )
        return
    await _render_close_wizard_screen(
        source_message=message,
        state=state,
        active_section=target_section,
        selected_item_index=None,
        finish_confirm=False,
        completed=completed,
        values=values,
    )


@shift_router.message(CloseShiftStates.wizard, (F.photo | F.document))
async def close_wizard_media_input(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
    bot: Bot,
) -> None:
    """Принимает обязательные фото для пунктов мастера закрытия.

    Args:
        message: Входящее сообщение Telegram.
        state: FSM-контекст пользователя.
        db: Экземпляр базы данных.
        settings: Настройки приложения.
        bot: Экземпляр Telegram-бота.

    Returns:
        None.
    """
    if not message.from_user:
        return
    await safe_delete_message(message, log_context="close wizard media input")

    state_data = await state.get_data()
    (
        shift_id,
        completed,
        active_section,
        selected_item_index,
        finish_confirm,
        values,
        started_at,
    ) = _close_wizard_parse_context(state_data)
    if shift_id is None:
        await message.answer("Сценарий закрытия потерян. Запустите /close заново.")
        return
    if finish_confirm:
        await _render_close_wizard_screen(
            source_message=message,
            state=state,
            active_section=active_section,
            selected_item_index=None,
            finish_confirm=True,
            completed=completed,
            values=values,
        )
        return
    if selected_item_index is None:
        await _render_close_wizard_screen(
            source_message=message,
            state=state,
            active_section=active_section,
            selected_item_index=None,
            finish_confirm=False,
            completed=completed,
            values=values,
            error_text="Сначала выберите пункт, где требуется фото",
        )
        return

    item = close_wizard_item_by_index(selected_item_index)
    if not item or not _close_wizard_item_requires_photo(item):
        await _render_close_wizard_screen(
            source_message=message,
            state=state,
            active_section=active_section,
            selected_item_index=None,
            finish_confirm=False,
            completed=completed,
            values=values,
            error_text="Для этого шага фото не требуется",
        )
        return

    if message.document:
        mime = str(message.document.mime_type or "").lower()
        if not mime.startswith("image/"):
            await _render_close_wizard_screen(
                source_message=message,
                state=state,
                active_section=item.section_index,
                selected_item_index=item.index,
                finish_confirm=False,
                completed=completed,
                values=values,
                error_text="Нужно отправить именно фото",
            )
            return

    media_file_id: str | None = None
    media_file_unique_id: str | None = None
    media_mime_type: str | None = None
    if message.photo:
        largest = message.photo[-1]
        media_file_id = str(largest.file_id)
        media_file_unique_id = str(largest.file_unique_id)
        media_mime_type = "image/jpeg"
    elif message.document:
        media_file_id = str(message.document.file_id)
        media_file_unique_id = str(message.document.file_unique_id)
        media_mime_type = str(message.document.mime_type or "").strip() or None

    if media_file_id:
        created_at = (
            message.date.isoformat()
            if message.date is not None
            else datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        )
        local_path = await _download_media_to_disk(
            bot=bot,
            file_id=media_file_id,
            shift_id=shift_id,
            item_index=item.index,
            mime_type=media_mime_type,
            media_type="close",
        )
        await db.upsert_close_checklist_media(
            shift_id=shift_id,
            item_index=item.index,
            item_label=item.text,
            file_id=media_file_id,
            file_unique_id=media_file_unique_id,
            mime_type=media_mime_type,
            created_at=created_at,
            local_path=local_path,
        )
        logger.debug(
            "Checklist media saved: shift_id=%s item_index=%s item=%r",
            shift_id,
            item.index,
            item.text,
        )

    completed.add(item.index)
    target_section = _close_wizard_next_section_after_completion(
        current_section=item.section_index,
        completed=completed,
    )
    await _persist_close_wizard_progress(
        state=state,
        db=db,
        shift_id=shift_id,
        completed=completed,
        active_section=target_section,
        selected_item_index=None,
        finish_confirm=False,
        values=values,
        started_at=started_at,
    )
    if len(completed) == close_wizard_total_items():
        await _close_wizard_finalize(
            state=state,
            db=db,
            settings=settings,
            bot=bot,
            source_message=message,
            actor_id=message.from_user.id,
            actor_username=message.from_user.username,
            actor_full_name=message.from_user.full_name,
        )
        return
    await _render_close_wizard_screen(
        source_message=message,
        state=state,
        active_section=target_section,
        selected_item_index=None,
        finish_confirm=False,
        completed=completed,
        values=values,
    )


# ---------------------------------------------------------------------------
# Просмотр фото открытия смены
# ---------------------------------------------------------------------------


@shift_router.message(F.text == MENU_SHIFT_PHOTOS)
async def shift_photos(
    message: Message,
    db: Database,
    bot: Bot,
) -> None:
    """Отправляет фотографии, сделанные при открытии активной смены."""
    if not message.from_user:
        return

    active_shift = await db.get_active_shift()
    if not active_shift:
        await message.answer("Нет открытой смены.")
        return

    shift_id = int(active_shift["id"])
    media_list = await db.get_all_open_checklist_media(shift_id)
    if not media_list:
        await message.answer("Фотографии ещё не добавлены.")
        return

    for media in media_list:
        file_id = str(media.get("file_id", ""))
        caption = str(media.get("item_label", ""))
        if not file_id:
            continue
        try:
            await bot.send_photo(message.chat.id, file_id, caption=caption)
        except Exception:
            logger.exception(
                "Не удалось отправить фото open_checklist_media id=%s", media.get("id")
            )


# ---------------------------------------------------------------------------
# Периодические остатки (каждые 2 часа)
# ---------------------------------------------------------------------------

_PERIODIC_FSM_SHIFT_KEY = "periodic_shift_id"
_PERIODIC_FSM_INDEX_KEY = "periodic_item_index"
_PERIODIC_FSM_VALUES_KEY = "periodic_values"


@shift_router.message(Command("residuals"))
@shift_router.message(F.text == MENU_RESIDUALS)
async def periodic_residuals_start(
    message: Message,
    state: FSMContext,
    db: Database,
) -> None:
    """Запускает сбор периодических остатков."""
    if not message.from_user:
        return

    active_shift = await db.get_active_shift()
    if not active_shift:
        await message.answer(
            "Нет открытой смены.",
            reply_markup=build_shift_menu_keyboard(is_shift_open=False),
        )
        return

    if not PERIODIC_RESIDUAL_INPUTS_LIST:
        await message.answer("Список позиций для остатков не настроен.")
        return

    await state.clear()
    await state.set_state(PeriodicResidualStates.collecting)
    await state.update_data(
        **{
            _PERIODIC_FSM_SHIFT_KEY: int(active_shift["id"]),
            _PERIODIC_FSM_INDEX_KEY: 0,
            _PERIODIC_FSM_VALUES_KEY: {},
        }
    )

    first_item = PERIODIC_RESIDUAL_INPUTS_LIST[0]
    await message.answer(
        f"📋 Запись остатков (1/{len(PERIODIC_RESIDUAL_INPUTS_LIST)})\n\n"
        f"{first_item['prompt']}"
    )


@shift_router.message(PeriodicResidualStates.collecting, F.text)
async def periodic_residuals_collect(
    message: Message,
    state: FSMContext,
    db: Database,
) -> None:
    """Принимает ввод текущей позиции периодических остатков."""
    if not message.from_user or not message.text:
        return

    fsm_data = await state.get_data()
    shift_id: int = int(fsm_data.get(_PERIODIC_FSM_SHIFT_KEY, 0))
    item_index: int = int(fsm_data.get(_PERIODIC_FSM_INDEX_KEY, 0))
    collected: dict[str, object] = dict(fsm_data.get(_PERIODIC_FSM_VALUES_KEY, {}))

    if item_index >= len(PERIODIC_RESIDUAL_INPUTS_LIST):
        await state.clear()
        return

    item_cfg = PERIODIC_RESIDUAL_INPUTS_LIST[item_index]
    raw = message.text.strip().replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        await message.answer("Введите число.")
        return

    if not math.isfinite(value) or value < 0:
        await message.answer("Введите корректное неотрицательное число.")
        return

    max_value = float(item_cfg.get("max_value", 1_000_000))
    if value > max_value:
        await message.answer(f"Слишком большое значение (максимум {max_value:.0f}).")
        return

    only_integer = bool(item_cfg.get("only_integer", False))
    if only_integer and value != int(value):
        await message.answer("Введите целое число.")
        return

    unit = str(item_cfg.get("unit", ""))
    key = str(item_cfg.get("key", ""))
    recorded_at = datetime.now(timezone.utc).isoformat()

    await db.insert_periodic_residual(
        shift_id=shift_id,
        key=key,
        value=value,
        unit=unit,
        recorded_at=recorded_at,
    )

    collected[key] = value
    next_index = item_index + 1
    await state.update_data(
        **{
            _PERIODIC_FSM_INDEX_KEY: next_index,
            _PERIODIC_FSM_VALUES_KEY: collected,
        }
    )

    if next_index < len(PERIODIC_RESIDUAL_INPUTS_LIST):
        next_item = PERIODIC_RESIDUAL_INPUTS_LIST[next_index]
        await message.answer(
            f"📋 Запись остатков ({next_index + 1}/{len(PERIODIC_RESIDUAL_INPUTS_LIST)})\n\n"
            f"{next_item['prompt']}"
        )
        return

    # Все позиции собраны — показываем итог
    await state.clear()
    lines = ["✅ Остатки записаны:\n"]
    for cfg in PERIODIC_RESIDUAL_INPUTS_LIST:
        k = str(cfg.get("key", ""))
        label = str(cfg.get("prompt", k))
        u = str(cfg.get("unit", ""))
        val = collected.get(k)
        if val is not None:
            val_f = float(val)
            val_str = str(int(val_f)) if val_f == int(val_f) else str(val_f)
            lines.append(f"• {label}: {val_str} {u}")
    await message.answer(
        "\n".join(lines),
        reply_markup=build_shift_menu_keyboard(is_shift_open=True),
    )
