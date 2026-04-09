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

# Тексты кнопок главного меню (reply keyboard).
MENU_OPEN_SHIFT = "▶ Открыть смену"
MENU_MID_SHIFT = "📝 Ведение смены"
MENU_CLOSE_SHIFT = "🔒 Закрыть смену"
MENU_STOCK = "📊 Остатки"
MENU_PROBLEM = "⚠ Проблема"
MENU_REPORT_BY_DATE = "Отчёт за дату"
MENU_REPORTS = "Отчёты"
MENU_CANCEL = "Отмена"
MENU_SHIFT_PHOTOS = "📷 Фото смены"
MENU_RESIDUALS = "📋 Остатки смены"
