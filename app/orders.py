from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


PRODUCT_ITEMS = {
    "meat": "Мясо",
    "lavash": "Лаваш",
    "vegetables": "Овощи",
    "sauces": "Соусы",
    "ayran": "Айран",
    "potato": "Картофель",
}

SUPPLY_ITEMS = {
    "gloves": "Перчатки",
    "napkins": "Салфетки",
    "bags": "Пакеты",
    "containers": "Контейнеры",
    "cleaner": "Моющее средство",
    "paper_towels": "Бумажные полотенца",
}

ORDER_CATALOG = {
    "products": PRODUCT_ITEMS,
    "supplies": SUPPLY_ITEMS,
}


def build_order_keyboard(order_type: str) -> InlineKeyboardMarkup:
    items = ORDER_CATALOG[order_type]
    rows: list[list[InlineKeyboardButton]] = []

    for key, title in items.items():
        rows.append(
            [
                InlineKeyboardButton(
                    text=title,
                    callback_data=f"order:{order_type}:{key}",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def resolve_order_item(order_type: str, item_key: str) -> str | None:
    return ORDER_CATALOG.get(order_type, {}).get(item_key)
