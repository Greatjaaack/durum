from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.checklist.callbacks import build_checklist_callback
from app.checklist.data import CHECKLISTS, CHECKLIST_TITLES

# Максимальная длина текста inline-кнопки Telegram.
BUTTON_TEXT_LIMIT = 64

# Эмодзи заголовка чек-листа по типу.
CHECKLIST_TITLE_EMOJI = {
    "open": "🍳",
    "mid": "📝",
    "close": "🔒",
}


def _clamp_section_index(checklist_type: str, section_index: int) -> int:
    """Ограничивает индекс секции допустимым диапазоном.

    Args:
        checklist_type: Тип чек-листа.
        section_index: Искомый индекс секции.

    Returns:
        Корректный индекс секции.
    """
    sections_count = len(CHECKLISTS[checklist_type])
    if sections_count == 0:
        return 0
    return max(0, min(section_index, sections_count - 1))


def _wrap_label(text: str, max_len: int = 20) -> str:
    """Переносит длинный текст кнопки на вторую строку по пробелу.

    Args:
        text: Исходный текст.
        max_len: Максимальная длина первой строки.

    Returns:
        Текст с переносом или без изменений.
    """
    if len(text) <= max_len:
        return text
    # Ищем последний пробел до позиции max_len
    cut = text.rfind(" ", 0, max_len)
    if cut <= 0:
        # Нет пробела — разбиваем по max_len
        return text[:max_len] + "\n" + text[max_len:]
    return text[:cut] + "\n" + text[cut + 1:]


def _item_button_text(
    item_text: str,
    mark: str,
    limit: int = BUTTON_TEXT_LIMIT,
) -> str:
    """Формирует компактный текст кнопки пункта с чекбоксом.

    Args:
        item_text: Название пункта.
        mark: Символ чекбокса.
        limit: Ограничение длины кнопки Telegram.

    Returns:
        Строка вида `Пункт … ☐`.
    """
    suffix = f" {mark}"
    item_limit = max(4, limit - len(suffix))
    compact = item_text.strip()
    if len(compact) > item_limit:
        if item_limit <= 3:
            compact = compact[:item_limit]
        else:
            compact = compact[: item_limit - 3].rstrip() + "..."
    return f"{compact}{suffix}"


def _section_start_index(checklist_type: str, section_index: int) -> int:
    """Возвращает глобальный индекс начала секции.

    Args:
        checklist_type: Тип чек-листа.
        section_index: Индекс секции.

    Returns:
        Индекс первого пункта секции в плоском списке.
    """
    section_index = _clamp_section_index(checklist_type, section_index)
    return sum(len(section["items"]) for section in CHECKLISTS[checklist_type][:section_index])


def _checklist_title_with_emoji(
    checklist_type: str,
) -> str:
    """Возвращает заголовок чек-листа с эмодзи.

    Args:
        checklist_type: Тип чек-листа.

    Returns:
        Заголовок вида `🍳 Открытие смены`.
    """
    title = CHECKLIST_TITLES[checklist_type]
    emoji = CHECKLIST_TITLE_EMOJI.get(checklist_type, "📋")
    return f"{emoji} {title}"


def _progress_percent(
    done: int,
    total: int,
) -> int:
    """Считает целочисленный процент прогресса.

    Args:
        done: Выполненные пункты.
        total: Общее число пунктов.

    Returns:
        Процент выполнения от 0 до 100.
    """
    if total <= 0:
        return 0
    return int(round((done / total) * 100))


def _progress_bar(
    done: int,
    total: int,
    width: int = 10,
) -> str:
    """Строит компактный прогресс-бар.

    Args:
        done: Выполненные пункты.
        total: Общее число пунктов.
        width: Ширина прогресс-бара.

    Returns:
        Строка вида `████░░░░░░`.
    """
    if total <= 0 or width <= 0:
        return "░" * max(width, 0)
    filled = int(round((done / total) * width))
    filled = max(0, min(filled, width))
    return ("█" * filled) + ("░" * (width - filled))


def checklist_total_items(checklist_type: str) -> int:
    """Считает общее число пунктов в чек-листе.

    Args:
        checklist_type: Тип чек-листа.

    Returns:
        Общее число пунктов.
    """
    return sum(len(section["items"]) for section in CHECKLISTS[checklist_type])


def checklist_section_for_item(checklist_type: str, item_index: int) -> int:
    """Определяет секцию для указанного пункта.

    Args:
        checklist_type: Тип чек-листа.
        item_index: Индекс пункта.

    Returns:
        Индекс секции.
    """
    cursor = 0
    for section_index, section in enumerate(CHECKLISTS[checklist_type]):
        next_cursor = cursor + len(section["items"])
        if item_index < next_cursor:
            return section_index
        cursor = next_cursor
    return _clamp_section_index(checklist_type, len(CHECKLISTS[checklist_type]) - 1)


def normalize_checklist_section(checklist_type: str, section_index: int) -> int:
    """Нормализует индекс секции для чек-листа.

    Args:
        checklist_type: Тип чек-листа.
        section_index: Индекс секции.

    Returns:
        Корректный индекс секции.
    """
    return _clamp_section_index(checklist_type, section_index)


def build_checklist_text(checklist_type: str, completed: set[int], active_section: int) -> str:
    """Формирует текст сообщения чек-листа.

    Args:
        checklist_type: Тип чек-листа.
        completed: Множество выполненных пунктов.
        active_section: Активная секция.

    Returns:
        Текст для сообщения Telegram.
    """
    sections = CHECKLISTS[checklist_type]
    active_section = _clamp_section_index(checklist_type, active_section)
    section = sections[active_section]

    done = len(completed)
    total = checklist_total_items(checklist_type)
    progress_pct = _progress_percent(done, total)
    progress_bar = _progress_bar(done, total)

    sections_count = len(sections)
    return "\n".join(
        [
            _checklist_title_with_emoji(checklist_type),
            f"Блок {active_section + 1} из {sections_count} — {section['title']}",
            f"Прогресс: {done} / {total}",
            f"{progress_bar} {progress_pct}%",
        ]
    )


def build_checklist_keyboard(
    checklist_type: str,
    completed: set[int],
    active_section: int,
    shift_id: int | None = None,
) -> InlineKeyboardMarkup:
    """Строит inline-клавиатуру чек-листа.

    Args:
        checklist_type: Тип чек-листа.
        completed: Множество выполненных пунктов.
        active_section: Активная секция.
        shift_id: Идентификатор смены для привязки callback-кнопок.

    Returns:
        Готовая inline-клавиатура.
    """
    sections = CHECKLISTS[checklist_type]
    active_section = _clamp_section_index(checklist_type, active_section)
    rows: list[list[InlineKeyboardButton]] = []

    start_index = _section_start_index(checklist_type, active_section)
    active_items = sections[active_section]["items"]
    for local_index, item in enumerate(active_items):
        item_index = start_index + local_index
        mark = "☑" if item_index in completed else "☐"
        item_callback = build_checklist_callback(
            checklist_type=checklist_type,
            action="item",
            value=item_index,
            shift_id=shift_id,
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=_item_button_text(item, mark),
                    callback_data=item_callback,
                ),
            ]
        )

    nav_row: list[InlineKeyboardButton] = []
    if active_section > 0:
        back_callback = build_checklist_callback(
            checklist_type=checklist_type,
            action="section",
            value=active_section - 1,
            shift_id=shift_id,
        )
        nav_row.append(
            InlineKeyboardButton(
                text="← Назад",
                callback_data=back_callback,
            )
        )
    if active_section < len(sections) - 1:
        next_callback = build_checklist_callback(
            checklist_type=checklist_type,
            action="section",
            value=active_section + 1,
            shift_id=shift_id,
        )
        nav_row.append(
            InlineKeyboardButton(
                text="Далее →",
                callback_data=next_callback,
            )
        )
    if nav_row:
        rows.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)
