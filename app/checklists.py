from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


OPEN_CHECKLIST = [
    "Проверить холодильники",
    "Проверить гриль",
    "Подготовить мясо",
    "Нарезать овощи",
    "Проверить кассу",
    "Подготовить размен",
    "Проверить запас лаваша",
]

MID_CHECKLIST = [
    "Проверить состояние мяса",
    "Обновить соусы",
    "Проверить чистоту кухни",
    "Проверить остаток лаваша",
    "Проверить очередь",
]

CLOSE_CHECKLIST = [
    "Выключить гриль",
    "Убрать кухню",
    "Вынести мусор",
    "Убрать продукты",
    "Снять Z-отчёт",
]

CHECKLISTS = {
    "open": OPEN_CHECKLIST,
    "mid": MID_CHECKLIST,
    "close": CLOSE_CHECKLIST,
}

CHECKLIST_TITLES = {
    "open": "Чек-лист открытия смены",
    "mid": "Чек-лист в течение смены",
    "close": "Чек-лист закрытия смены",
}


def build_checklist_text(checklist_type: str, completed: set[int]) -> str:
    items = CHECKLISTS[checklist_type]
    title = CHECKLIST_TITLES[checklist_type]

    lines = [title]
    for idx, item in enumerate(items):
        mark = "✅" if idx in completed else "⬜"
        lines.append(f"{mark} {item}")
    return "\n".join(lines)


def build_checklist_keyboard(checklist_type: str, completed: set[int]) -> InlineKeyboardMarkup:
    items = CHECKLISTS[checklist_type]
    rows: list[list[InlineKeyboardButton]] = []

    for idx, item in enumerate(items):
        mark = "✅" if idx in completed else "⬜"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {item}",
                    callback_data=f"checklist:{checklist_type}:{idx}",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)
