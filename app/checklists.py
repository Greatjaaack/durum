from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.checklist_data import CHECKLISTS, CHECKLIST_TITLES

# Emoji-иконки для маркировки секций чек-листа в интерфейсе.
SECTION_BADGES = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

# Максимальная длина текста inline-кнопки Telegram.
BUTTON_TEXT_LIMIT = 64


def _section_badge(section_index: int) -> str:
    """Возвращает emoji-номер секции.

    Args:
        section_index: Индекс секции.

    Returns:
        Текстовый бейдж секции.
    """
    if 0 <= section_index < len(SECTION_BADGES):
        return SECTION_BADGES[section_index]
    return f"{section_index + 1}."


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


def _shorten_button_text(text: str, limit: int = BUTTON_TEXT_LIMIT) -> str:
    """Сокращает текст кнопки до безопасной длины.

    Args:
        text: Исходный текст кнопки.
        limit: Максимальная длина текста.

    Returns:
        Сокращённый текст.
    """
    compact = text.strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1].rstrip()}…"


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


def checklist_total_items(checklist_type: str) -> int:
    """Считает общее число пунктов в чек-листе.

    Args:
        checklist_type: Тип чек-листа.

    Returns:
        Общее число пунктов.
    """
    return sum(len(section["items"]) for section in CHECKLISTS[checklist_type])


def checklist_item_text(checklist_type: str, item_index: int) -> str | None:
    """Возвращает текст пункта по его глобальному индексу.

    Args:
        checklist_type: Тип чек-листа.
        item_index: Глобальный индекс пункта.

    Returns:
        Текст пункта или None, если индекс вне диапазона.
    """
    if item_index < 0:
        return None

    cursor = 0
    for section in CHECKLISTS[checklist_type]:
        items = section["items"]
        next_cursor = cursor + len(items)
        if item_index < next_cursor:
            return str(items[item_index - cursor]).strip()
        cursor = next_cursor
    return None


def checklist_section_done_count(checklist_type: str, completed: set[int], section_index: int) -> int:
    """Считает количество выполненных пунктов в секции.

    Args:
        checklist_type: Тип чек-листа.
        completed: Множество выполненных индексов.
        section_index: Индекс секции.

    Returns:
        Количество выполненных пунктов секции.
    """
    section_index = _clamp_section_index(checklist_type, section_index)
    section_items = CHECKLISTS[checklist_type][section_index]["items"]
    start_index = _section_start_index(checklist_type, section_index)
    return sum(1 for idx in range(start_index, start_index + len(section_items)) if idx in completed)


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
    title = CHECKLIST_TITLES[checklist_type]
    done = len(completed)
    total = checklist_total_items(checklist_type)
    section = sections[active_section]
    section_done = checklist_section_done_count(checklist_type, completed, active_section)
    section_total = len(section["items"])

    lines = [title, f"Прогресс: {done} / {total}", ""]
    lines.append(f"Блок: {_section_badge(active_section)} {section['title']} ({section_done}/{section_total})")
    if done < total:
        lines.append("Выберите блок или отметьте пункт кнопками ниже.")
    else:
        lines.append("Все пункты отмечены.")
    return "\n".join(lines)


def build_checklist_keyboard(
    checklist_type: str,
    completed: set[int],
    active_section: int,
) -> InlineKeyboardMarkup:
    """Строит inline-клавиатуру чек-листа.

    Args:
        checklist_type: Тип чек-листа.
        completed: Множество выполненных пунктов.
        active_section: Активная секция.

    Returns:
        Готовая inline-клавиатура.
    """
    sections = CHECKLISTS[checklist_type]
    active_section = _clamp_section_index(checklist_type, active_section)
    rows: list[list[InlineKeyboardButton]] = []

    for section_index, section in enumerate(sections):
        section_done = checklist_section_done_count(checklist_type, completed, section_index)
        section_total = len(section["items"])
        marker = "▶️" if section_index == active_section else "▫️"
        rows.append(
            [
                InlineKeyboardButton(
                    text=_shorten_button_text(
                        f"{marker} {_section_badge(section_index)} {section['title']} {section_done}/{section_total}"
                    ),
                    callback_data=f"checklist:{checklist_type}:section:{section_index}",
                )
            ]
        )

    start_index = _section_start_index(checklist_type, active_section)
    active_items = sections[active_section]["items"]
    for local_index, item in enumerate(active_items):
        item_index = start_index + local_index
        mark = "☑" if item_index in completed else "⬜"
        rows.append(
            [
                InlineKeyboardButton(
                    text=_shorten_button_text(f"{mark} {item.strip()}"),
                    callback_data=f"checklist:{checklist_type}:item:{item_index}",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)
