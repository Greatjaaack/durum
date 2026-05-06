"""Чистый data + render слой мастера закрытия смены.

Назначение модуля — собрать в одном месте всё, что не зависит от
``aiogram.Bot``, ``Database`` и ``FSMContext``: dataclass-описания
пунктов мастера, билдеры из YAML, утилиты доступа, конвертация значений,
хелперы рендеринга экранов и парсинг пользовательского ввода.

FSM-обработчики (``@shift_router``), обращения к БД, отправка сообщений
и финализация закрытия по-прежнему живут в ``app/handlers/shift.py`` и
импортируют отсюда нужные имена. Это даёт явный шов и позволяет тестам
работать без mock-ов Telegram/SQLite.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.checklist.data import (
    CHECKLISTS,
    CLOSE_CHECKLIST,
    CLOSE_RESIDUAL_INPUTS,
    CLOSE_RESIDUAL_INPUTS_BY_CHECKLIST_ITEM,
    CLOSE_RESIDUAL_INPUTS_BY_SECTION_AND_ITEM,
    CLOSE_SECTION_EMOJI_BY_TITLE,
)
from app.checklist.ui import build_checklist_text
from app.handlers.utils import fmt_number, parse_close_residual_value
from app.units_config import (
    UNIT_TYPE_BASE_UNITS,
    normalize_measurement_value,
    restore_measurement_value,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Структуры данных и константы
# ---------------------------------------------------------------------------

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
        requires_photo: Требуется ли фото для отметки чекбокса.
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
    requires_photo: bool = False


# Префиксы callback_data для inline-клавиатур мастера.
CLOSE_WIZARD_CALLBACK_PREFIX = "closewiz"
CLOSE_DONE_CALLBACK_PREFIX = "closedone"

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

# Резервный whitelist пунктов с обязательным фото.
# Используется, если в YAML не выставлен флаг requires_photo
# (например, при откате конфига на старую версию без флагов).
CLOSE_WIZARD_PHOTO_REQUIRED_ITEMS = frozenset(
    {
        "сделать фото корзины фритюра",
        "сделать фото масла во фритюре",
        "сделать фото корзины",
        "сделать фото масла",
    }
)


# ---------------------------------------------------------------------------
# Билдеры из YAML
# ---------------------------------------------------------------------------


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

    Returns:
        Кортеж пунктов мастера.
    """
    items: list[CloseWizardItem] = []
    cursor = 0
    for section_index, section in enumerate(CLOSE_CHECKLIST):
        section_title = str(section["title"]).strip()
        section_emoji = CLOSE_SECTION_EMOJI_BY_TITLE.get(section_title, "▫️")
        section_options = section.get("item_options", {}) or {}
        for item_text_raw in section["items"]:
            item_text = str(item_text_raw).strip()
            item_options = section_options.get(item_text, {}) or {}
            requires_photo_flag = bool(item_options.get("requires_photo", False))
            # Сначала ищем остаток с привязкой к конкретной секции, чтобы
            # одноимённые пункты в разных секциях (например, «Сроки годности»
            # и «Убрать продукты») не задваивали ввод остатков.
            residual_config = CLOSE_RESIDUAL_INPUTS_BY_SECTION_AND_ITEM.get(
                (section_title, item_text)
            )
            if residual_config is None:
                # Фолбэк для обратной совместимости с конфигами без section_title:
                # привязываем остаток только если его текст уникален во всём чек-листе.
                candidate = CLOSE_RESIDUAL_INPUTS_BY_CHECKLIST_ITEM.get(item_text)
                if candidate is not None and not str(
                    candidate.get("section_title") or ""
                ).strip():
                    residual_config = candidate
            if residual_config is None:
                fallback = CLOSE_RESIDUAL_INPUTS.get(item_text)
                if fallback is not None and not str(
                    fallback.get("section_title") or ""
                ).strip():
                    residual_config = fallback
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
                        requires_photo=requires_photo_flag,
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
                        requires_photo=requires_photo_flag,
                    )
                )
            cursor += 1
    return tuple(items)


CLOSE_WIZARD_ITEMS = _build_close_wizard_items()
CLOSE_WIZARD_TOTAL = len(CLOSE_WIZARD_ITEMS)
CLOSE_WIZARD_STEPS_TOTAL = len(CLOSE_CHECKLIST)


def _build_close_wizard_section_items() -> dict[int, tuple[CloseWizardItem, ...]]:
    """Группирует пункты закрытия по секциям.

    Returns:
        Словарь: индекс секции -> кортеж пунктов.
    """
    grouped: dict[int, list[CloseWizardItem]] = {}
    for item in CLOSE_WIZARD_ITEMS:
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


# ---------------------------------------------------------------------------
# Утилиты доступа и конвертации
# ---------------------------------------------------------------------------


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
    """Возвращает общее количество пунктов мастера."""
    return CLOSE_WIZARD_TOTAL


def close_wizard_first_incomplete_index(completed: set[int]) -> int:
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


def close_wizard_normalized_unit(item: CloseWizardItem) -> str | None:
    """Возвращает базовую единицу нормализации для пункта мастера.

    Args:
        item: Пункт мастера.

    Returns:
        Базовая единица (например, ``г``, ``мл``) или None.
    """
    if not item.input_rule:
        return None
    return UNIT_TYPE_BASE_UNITS.get(item.input_rule.unit_type)


def close_wizard_item_index_by_residual_key(residual_key: str) -> int | None:
    """Возвращает индекс пункта мастера по ключу остатка.

    Args:
        residual_key: Ключ остатка.

    Returns:
        Индекс пункта или None.
    """
    return CLOSE_WIZARD_RESIDUAL_INDEX.get(residual_key)


def close_wizard_item_requires_photo(item: CloseWizardItem) -> bool:
    """Проверяет, требуется ли фото для выполнения пункта мастера.

    Источник истины — флаг ``requires_photo`` из YAML (см. ``item_options``
    в ``app/checklist/config.yaml``). Старый whitelist/эвристика сохранены
    как фолбэк, чтобы не потерять обязательность фото, если YAML временно
    отстаёт от кода.

    Args:
        item: Пункт мастера.

    Returns:
        True, если для пункта обязательно фото.
    """
    if item.item_type != "check":
        return False
    if item.requires_photo:
        return True
    normalized_text = item.text.strip().lower()
    if normalized_text in CLOSE_WIZARD_PHOTO_REQUIRED_ITEMS:
        return True
    if "фото" in normalized_text and "корзин" in normalized_text:
        return True
    if "фото" in normalized_text and "масл" in normalized_text:
        return True
    return False


# ---------------------------------------------------------------------------
# Восстановление состояния из FSM/БД
# ---------------------------------------------------------------------------


def close_wizard_restore_completed(
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


def close_wizard_restore_values(
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


def close_wizard_items_for_section(section_index: int) -> list[CloseWizardItem]:
    """Возвращает пункты выбранного блока закрытия.

    Args:
        section_index: Индекс блока.

    Returns:
        Список пунктов блока.
    """
    return list(CLOSE_WIZARD_SECTION_ITEMS.get(section_index, ()))


def close_wizard_next_section_after_completion(
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
    if not all(item.index in completed for item in current_items):
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


def close_wizard_parse_context(
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
    values = close_wizard_restore_values(state_data)
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


# ---------------------------------------------------------------------------
# Числовой ввод: валидация
# ---------------------------------------------------------------------------


def close_wizard_parse_numeric_input(
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


# ---------------------------------------------------------------------------
# Чтение остатков (БД-агностичные хелперы)
# ---------------------------------------------------------------------------


def close_residual_normalized(
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


def close_residual_display(
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
    return close_residual_normalized(residuals, item_key)


def close_residual_display_unit(
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


# ---------------------------------------------------------------------------
# Форматирование длительности и времени
# ---------------------------------------------------------------------------


def close_duration_label(close_duration_sec: int | None) -> str:
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


def format_hhmm(raw_value: str | None) -> str:
    """Форматирует дату/время в строку HH:MM.

    Args:
        raw_value: ISO-строка даты/времени.

    Returns:
        Строка HH:MM или ``--:--``.
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


# ---------------------------------------------------------------------------
# Рендер: текст + клавиатуры одного вопроса
# ---------------------------------------------------------------------------


def _close_wizard_progress_line(done: int, total: int) -> tuple[str, int]:
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


def _close_wizard_as_question_text(item_text: str) -> str:
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
        if close_wizard_item_requires_photo(item):
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


# ---------------------------------------------------------------------------
# Рендер: блок-экран, экран предупреждения, агрегатный билдер
# ---------------------------------------------------------------------------


def _close_wizard_short_button_text(text: str, limit: int = 64) -> str:
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
        Строка вида ``Пункт … ☐``.
    """
    suffix = f" {mark}"
    item_limit = max(4, limit - len(suffix))
    compact = _close_wizard_short_button_text(item_text, limit=item_limit)
    return f"{compact}{suffix}"


def close_wizard_missing_items(completed: set[int]) -> list[CloseWizardItem]:
    """Возвращает список незавершённых пунктов мастера.

    Args:
        completed: Выполненные пункты.

    Returns:
        Список незавершённых пунктов в порядке чек-листа.
    """
    return [item for item in CLOSE_WIZARD_ITEMS if item.index not in completed]


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
    items = close_wizard_items_for_section(section_index)
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
    text = f"⚠️ Не все пункты выполнены\n\nВыполнено: {done} из {total}"
    missing_items = close_wizard_missing_items(completed)
    if missing_items:
        by_section: dict[int, list[str]] = {}
        for item in missing_items:
            by_section.setdefault(item.section_index, []).append(item.text)
        lines = ["\n\nПропущено:"]
        for sec_idx, item_texts in sorted(by_section.items()):
            sec_title = (
                CLOSE_CHECKLIST[sec_idx]["title"]
                if sec_idx < len(CLOSE_CHECKLIST)
                else f"Блок {sec_idx + 1}"
            )
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


def build_close_wizard_screen(
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
    if item.item_type == "check" and not close_wizard_item_requires_photo(item):
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


# ---------------------------------------------------------------------------
# Рендер: экраны успешного закрытия
# ---------------------------------------------------------------------------


def build_close_done_summary_text(
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
    status_line = (
        "✅ Всё заполнено" if all_items_completed else "⚠ Есть незаполненные пункты"
    )
    return (
        "✅ Смена закрыта\n\n"
        f"Член команды: {employee}\n"
        f"Время: {closed_at_hhmm}\n"
        f"Длительность: {duration_label}\n\n"
        f"Статус:\n{status_line}\n\n"
        "Спасибо за смену 🙌"
    )


def build_close_done_summary_keyboard(shift_id: int) -> InlineKeyboardMarkup:
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


def build_close_done_details_text(
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


def build_close_done_details_keyboard(shift_id: int) -> InlineKeyboardMarkup:
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
