from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Emoji-иконки для маркировки блоков заказа в интерфейсе.
SECTION_BADGES = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

# Максимальная длина текста inline-кнопки Telegram.
BUTTON_TEXT_LIMIT = 64

# Отображаемые заголовки разных типов заказа.
ORDER_TITLES = {
    "products": "Заказ продуктов",
    "supplies": "Заказ хозтоваров",
}

# Секции и позиции для заказа продуктов.
PRODUCT_ORDER_SECTIONS = [
    {
        "title": "Основные продукты",
        "items": [
            {"key": "chicken", "title": "Курица", "unit": "кг"},
            {"key": "vegetables", "title": "Овощи", "unit": "кг"},
            {"key": "fries", "title": "Картофель фри", "unit": "кг"},
        ],
    },
    {
        "title": "Соусы",
        "items": [
            {"key": "mayo", "title": "Майонез", "unit": "кг"},
            {"key": "ketchup", "title": "Кетчуп", "unit": "кг"},
            {"key": "tomato_paste", "title": "Томатная паста", "unit": "кг"},
        ],
    },
    {
        "title": "Прочее",
        "items": [
            {"key": "condensed_milk", "title": "Сгущёнка", "unit": "шт"},
        ],
    },
]

# Секции и позиции для заказа хозтоваров.
SUPPLY_ORDER_SECTIONS = [
    {
        "title": "Расходники",
        "items": [
            {"key": "napkins", "title": "Салфетки"},
            {"key": "wet_napkins", "title": "Влажные салфетки"},
            {"key": "bags", "title": "Пакеты"},
            {"key": "gloves", "title": "Перчатки"},
            {"key": "foil", "title": "Фольга"},
            {"key": "straws", "title": "Трубочки"},
        ],
    },
    {
        "title": "Упаковка",
        "items": [
            {"key": "tea_cups", "title": "Стаканы для чая"},
            {"key": "tea_lids", "title": "Крышки для чая"},
            {"key": "soup_cups", "title": "Стаканы для супа"},
            {"key": "soup_lids", "title": "Крышки для супа"},
            {"key": "fries_cups", "title": "Стаканы для фри"},
            {"key": "fries_lids", "title": "Крышки для фри"},
            {"key": "sauce_cups", "title": "Соусники"},
            {"key": "durum_pack", "title": "Упаковка для дюрюма"},
        ],
    },
    {
        "title": "Прочее",
        "items": [
            {"key": "sticks", "title": "Палочки"},
            {"key": "toothpicks", "title": "Зубочистки"},
            {"key": "hand_towels", "title": "Полотенца для рук"},
            {"key": "disinfectant", "title": "Дезинфицирующее средство"},
            {"key": "receipt_tape", "title": "Чековая лента"},
        ],
    },
]

# Унифицированная карта чек-листов заказа по типу.
ORDER_CHECKLISTS = {
    "products": PRODUCT_ORDER_SECTIONS,
    "supplies": SUPPLY_ORDER_SECTIONS,
}


def _section_badge(section_index: int) -> str:
    """Возвращает emoji-номер блока заказа.

    Args:
        section_index: Индекс секции.

    Returns:
        Текстовый бейдж секции.
    """
    if 0 <= section_index < len(SECTION_BADGES):
        return SECTION_BADGES[section_index]
    return f"{section_index + 1}."


def _shorten_button_text(text: str, limit: int = BUTTON_TEXT_LIMIT) -> str:
    """Обрезает слишком длинный текст кнопки.

    Args:
        text: Исходный текст.
        limit: Максимальная длина текста.

    Returns:
        Сокращённый текст.
    """
    compact = text.strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1].rstrip()}…"


def _clamp_section_index(order_type: str, section_index: int) -> int:
    """Ограничивает индекс секции допустимыми границами.

    Args:
        order_type: Тип заказа.
        section_index: Индекс секции.

    Returns:
        Валидный индекс секции.
    """
    sections_count = len(ORDER_CHECKLISTS[order_type])
    if sections_count == 0:
        return 0
    return max(0, min(section_index, sections_count - 1))


def normalize_order_section(order_type: str, section_index: int) -> int:
    """Нормализует индекс секции для типа заказа.

    Args:
        order_type: Тип заказа.
        section_index: Индекс секции.

    Returns:
        Валидный индекс секции.
    """
    return _clamp_section_index(order_type, section_index)


def _section_start_index(order_type: str, section_index: int) -> int:
    """Возвращает глобальный индекс начала секции.

    Args:
        order_type: Тип заказа.
        section_index: Индекс секции.

    Returns:
        Индекс первого пункта секции.
    """
    section_index = _clamp_section_index(order_type, section_index)
    return sum(len(section["items"]) for section in ORDER_CHECKLISTS[order_type][:section_index])


def order_total_items(order_type: str) -> int:
    """Подсчитывает общее число пунктов заказа.

    Args:
        order_type: Тип заказа.

    Returns:
        Общее количество пунктов.
    """
    return sum(len(section["items"]) for section in ORDER_CHECKLISTS[order_type])


def order_item_meta(order_type: str, item_index: int) -> dict[str, str] | None:
    """Возвращает метаданные пункта заказа по индексу.

    Args:
        order_type: Тип заказа.
        item_index: Индекс пункта.

    Returns:
        Метаданные пункта либо None.
    """
    if item_index < 0:
        return None
    cursor = 0
    for section in ORDER_CHECKLISTS[order_type]:
        items = section["items"]
        next_cursor = cursor + len(items)
        if item_index < next_cursor:
            return dict(items[item_index - cursor])
        cursor = next_cursor
    return None


def order_section_for_item(order_type: str, item_index: int) -> int:
    """Определяет секцию для указанного пункта заказа.

    Args:
        order_type: Тип заказа.
        item_index: Индекс пункта.

    Returns:
        Индекс секции.
    """
    cursor = 0
    for section_index, section in enumerate(ORDER_CHECKLISTS[order_type]):
        next_cursor = cursor + len(section["items"])
        if item_index < next_cursor:
            return section_index
        cursor = next_cursor
    return _clamp_section_index(order_type, len(ORDER_CHECKLISTS[order_type]) - 1)


def _order_section_done_count(order_type: str, selected: set[int], section_index: int) -> int:
    """Считает число отмеченных пунктов в секции.

    Args:
        order_type: Тип заказа.
        selected: Индексы отмеченных пунктов.
        section_index: Индекс секции.

    Returns:
        Количество отмеченных пунктов в секции.
    """
    section_index = _clamp_section_index(order_type, section_index)
    section_items = ORDER_CHECKLISTS[order_type][section_index]["items"]
    start_index = _section_start_index(order_type, section_index)
    return sum(1 for idx in range(start_index, start_index + len(section_items)) if idx in selected)


def _format_quantity(value: float | str, unit: str | None) -> str:
    """Форматирует количество с единицей измерения.

    Args:
        value: Количество.
        unit: Единица измерения.

    Returns:
        Отформатированная строка количества.
    """
    if isinstance(value, str):
        number = value.strip()
    else:
        number = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return f"{number} {unit}".strip() if unit else number


def build_order_text(order_type: str, selected: set[int], active_section: int) -> str:
    """Формирует текст интерфейса заказа.

    Args:
        order_type: Тип заказа.
        selected: Отмеченные пункты.
        active_section: Текущая секция.

    Returns:
        Текст сообщения Telegram.
    """
    active_section = _clamp_section_index(order_type, active_section)
    sections = ORDER_CHECKLISTS[order_type]
    title = ORDER_TITLES[order_type]
    done = len(selected)
    total = order_total_items(order_type)
    section = sections[active_section]
    section_done = _order_section_done_count(order_type, selected, active_section)
    section_total = len(section["items"])

    lines = [title, f"Прогресс: {done} / {total}", ""]
    lines.append(f"Блок: {_section_badge(active_section)} {section['title']} ({section_done}/{section_total})")
    if order_type == "products":
        lines.append("Нажмите на позицию, чтобы ввести или обновить количество.")
    else:
        lines.append("Отметьте нужные позиции и отправьте заказ.")
    return "\n".join(lines)


def build_order_keyboard(
    order_type: str,
    selected: set[int],
    active_section: int,
    quantities: dict[str, float | str],
) -> InlineKeyboardMarkup:
    """Строит inline-клавиатуру для заказа.

    Args:
        order_type: Тип заказа.
        selected: Отмеченные пункты.
        active_section: Активная секция.
        quantities: Количества для пунктов заказа продуктов.

    Returns:
        Готовая inline-клавиатура.
    """
    sections = ORDER_CHECKLISTS[order_type]
    active_section = _clamp_section_index(order_type, active_section)
    rows: list[list[InlineKeyboardButton]] = []

    for section_index, section in enumerate(sections):
        section_done = _order_section_done_count(order_type, selected, section_index)
        section_total = len(section["items"])
        marker = "▶️" if section_index == active_section else "▫️"
        rows.append(
            [
                InlineKeyboardButton(
                    text=_shorten_button_text(
                        f"{marker} {_section_badge(section_index)} {section['title']} {section_done}/{section_total}"
                    ),
                    callback_data=f"orderlist:{order_type}:section:{section_index}",
                )
            ]
        )

    start_index = _section_start_index(order_type, active_section)
    active_items = sections[active_section]["items"]
    for local_index, item in enumerate(active_items):
        item_index = start_index + local_index
        mark = "☑" if item_index in selected else "⬜"
        title = str(item["title"]).strip()
        key = str(item["key"])
        unit = item.get("unit")

        if order_type == "products" and item_index in selected and key in quantities:
            quantity_text = _format_quantity(quantities[key], unit)
            button_text = f"{mark} {title} — {quantity_text}"
        else:
            button_text = f"{mark} {title}"

        rows.append(
            [
                InlineKeyboardButton(
                    text=_shorten_button_text(button_text),
                    callback_data=f"orderlist:{order_type}:item:{item_index}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="📤 Отправить заказ",
                callback_data=f"orderlist:{order_type}:submit",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)
