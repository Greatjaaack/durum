from __future__ import annotations

from app.checklist.data import CLOSE_RESIDUAL_INPUTS, CLOSE_RESIDUAL_LABELS_BY_KEY

# Эталонные уровни остатков для ключевых позиций склада.
STOCK_REFERENCE_LEVELS = {
    "мясо": 20.0,
    "лаваш": 200.0,
    "картофель": 30.0,
}

# Порог, ниже которого остаток считается потенциально критичным.
STOCK_ALERT_THRESHOLD = 0.30

# Обязательные ключи остатков, без которых закрытие смены блокируется.
CLOSE_REQUIRED_RESIDUAL_KEYS = tuple(
    str(config["key"])
    for config in CLOSE_RESIDUAL_INPUTS.values()
)

# Отображаемые подписи обязательных остатков по их внутреннему ключу.
CLOSE_RESIDUAL_LABELS = dict(CLOSE_RESIDUAL_LABELS_BY_KEY)

# Ключ FSM-данных: временно выбранный пункт заказа для ввода количества.
ORDER_PENDING_ITEM_KEY = "order_pending_item"

# Ключ FSM-данных: список выбранных индексов пунктов заказа.
ORDER_SELECTED_KEY = "order_selected"

# Ключ FSM-данных: активная секция интерфейса заказа.
ORDER_SECTION_KEY = "order_section"

# Ключ FSM-данных: словарь количеств выбранных позиций.
ORDER_QUANTITIES_KEY = "order_quantities"

# Ключ FSM-данных: chat_id сообщения с чек-листом заказа.
ORDER_MESSAGE_CHAT_KEY = "order_message_chat_id"

# Ключ FSM-данных: message_id сообщения с чек-листом заказа.
ORDER_MESSAGE_ID_KEY = "order_message_id"

# Ключ FSM-данных: тип текущего заказа.
ORDER_TYPE_KEY = "order_type"

# Тексты кнопок главного меню (reply keyboard).
MENU_OPEN_SHIFT = "▶ Открыть смену"
MENU_MID_SHIFT = "📝 Ведение смены"
MENU_CLOSE_SHIFT = "🔒 Закрыть смену"
MENU_MORE = "📦 Дополнительно"
MENU_BACK = "⬅ Назад"
MENU_ORDER_PRODUCTS = "📦 Заказ продуктов"
MENU_ORDER_SUPPLIES = "🧴 Заказ хозтоваров"
MENU_STOCK = "📊 Остатки"
MENU_PROBLEM = "⚠ Проблема"
MENU_REPORT_BY_DATE = "Отчёт за дату"
MENU_REPORTS = "Отчёты"
MENU_FACT = "🍔 Факт"
MENU_AI_ENABLE = "AI режим"
MENU_AI_DISABLE = "Отключить AI"
MENU_CANCEL = "Отмена"
