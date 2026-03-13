from __future__ import annotations

from app.checklist_data import CLOSE_RESIDUAL_INPUTS

# Эталонные уровни остатков для ключевых позиций склада.
STOCK_REFERENCE_LEVELS = {
    "мясо": 20.0,
    "лаваш": 200.0,
    "картофель": 30.0,
}

# Порог, ниже которого остаток считается потенциально критичным.
STOCK_ALERT_THRESHOLD = 0.30

# Ключ в FSM-данных для временного состояния ввода остатка при закрытии.
CLOSE_RESIDUAL_PENDING_KEY = "close_residual_pending"

# Обязательные ключи остатков, без которых закрытие смены блокируется.
CLOSE_REQUIRED_RESIDUAL_KEYS = (
    "marinated_chicken",
    "fried_chicken",
    "lavash",
    "soup",
    "sauce",
)

# Отображаемые подписи обязательных остатков по их внутреннему ключу.
CLOSE_RESIDUAL_LABELS = {
    config["key"]: item_label for item_label, config in CLOSE_RESIDUAL_INPUTS.items()
}

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
