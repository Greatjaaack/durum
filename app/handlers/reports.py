from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.checklist.data import CLOSE_RESIDUAL_INPUTS
from app.checklist.ui import checklist_total_items
from app.db import Database
from app.handlers.constants import MENU_REPORTS


report_router = Router()

# Типы интерактивных отчётов.
REPORT_TYPES = {
    "shifts": "Отчёт по сменам",
    "residuals": "Отчёт по остаткам",
    "checklists": "Отчёт по чек-листам",
}

# Порядок отображения чек-листов в отчётах.
CHECKLIST_SEQUENCE = ("open", "mid", "close")

# Подписи чек-листов для отчётов.
CHECKLIST_LABELS = {
    "open": "Открытие",
    "mid": "Ведение",
    "close": "Закрытие",
}

# Порядок отображения остатков в детальном отчёте смены.
RESIDUAL_KEY_ORDER = tuple(config["key"] for config in CLOSE_RESIDUAL_INPUTS.values())

# Максимальная длина текста inline-кнопки Telegram.
BUTTON_TEXT_LIMIT = 64


def _short_button_text(text: str, limit: int = BUTTON_TEXT_LIMIT) -> str:
    """Ограничивает длину текста inline-кнопки.

    Args:
        text: Исходный текст.
        limit: Максимальная длина.

    Returns:
        Сокращённый текст.
    """
    compact = text.strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1].rstrip()}…"


def _date_label(date_text: str) -> str:
    """Преобразует дату YYYY-MM-DD в DD.MM.YYYY.

    Args:
        date_text: Дата в формате YYYY-MM-DD.

    Returns:
        Строка даты для интерфейса.
    """
    try:
        value = datetime.strptime(date_text, "%Y-%m-%d")
        return value.strftime("%d.%m.%Y")
    except ValueError:
        return date_text


def _time_label(raw_value: str | None) -> str:
    """Форматирует время из ISO-строки в HH:MM.

    Args:
        raw_value: Время/дата-время в виде строки.

    Returns:
        Строка времени HH:MM или «—».
    """
    if not raw_value:
        return "—"

    value = str(raw_value).strip()
    if not value:
        return "—"

    try:
        return datetime.fromisoformat(value).strftime("%H:%M")
    except ValueError:
        pass

    if "T" in value:
        tail = value.split("T", maxsplit=1)[1]
        if len(tail) >= 5 and tail[2] == ":":
            return tail[:5]
    if len(value) >= 5 and value[2] == ":":
        return value[:5]
    return value


def _build_report_types_keyboard() -> InlineKeyboardMarkup:
    """Строит клавиатуру выбора типа отчёта.

    Args:
        Нет параметров.

    Returns:
        Inline-клавиатура типов отчётов.
    """
    rows = [
        [
            InlineKeyboardButton(
                text=title,
                callback_data=f"reports:type:{report_type}",
            )
        ]
        for report_type, title in REPORT_TYPES.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_dates_keyboard(
    report_type: str,
    dates: list[str],
) -> InlineKeyboardMarkup:
    """Строит клавиатуру выбора даты для отчёта.

    Args:
        report_type: Тип отчёта.
        dates: Список дат в формате YYYY-MM-DD.

    Returns:
        Inline-клавиатура выбора даты.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for date_text in dates:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_date_label(date_text),
                    callback_data=f"reports:date:{report_type}:{date_text}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ К типам отчётов", callback_data="reports:types")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_shifts_keyboard(
    report_date: str,
    shifts: list[dict[str, object]],
) -> InlineKeyboardMarkup:
    """Строит клавиатуру выбора конкретной смены.

    Args:
        report_date: Дата отчёта.
        shifts: Список смен за дату.

    Returns:
        Inline-клавиатура выбора смены.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for shift in shifts:
        shift_id = int(shift["id"])
        opened_at = _time_label(
            str(shift.get("opened_at") or shift.get("open_time") or ""),
        )
        closed_at = _time_label(
            str(shift.get("closed_at") or shift.get("close_time") or ""),
        )
        employee = str(shift.get("opened_by") or shift.get("employee") or "Сотрудник")
        rows.append(
            [
                InlineKeyboardButton(
                    text=_short_button_text(f"#{shift_id} {opened_at}–{closed_at} {employee}"),
                    callback_data=f"reports:shift:{shift_id}:{report_date}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ К датам", callback_data="reports:type:shifts")])
    rows.append([InlineKeyboardButton(text="⬅️ К типам отчётов", callback_data="reports:types")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _build_shift_detail_text(
    db: Database,
    shift_id: int,
) -> str:
    """Формирует детальный текст отчёта по смене.

    Args:
        db: Экземпляр базы данных.
        shift_id: Идентификатор смены.

    Returns:
        Текст детального отчёта.
    """
    shift = await db.get_shift_by_id(shift_id)
    if not shift:
        return "Смена не найдена."

    residuals = await db.get_close_residuals(shift_id)
    checklists = await db.get_checklists_completion_by_shift(shift_id)
    report_date = str(shift.get("date") or "—")
    opened_at = _time_label(str(shift.get("opened_at") or shift.get("open_time") or ""))
    closed_at = _time_label(str(shift.get("closed_at") or shift.get("close_time") or ""))
    opened_by = str(shift.get("opened_by") or shift.get("employee") or "—")
    status = str(shift.get("status") or "—")
    revenue_value = shift.get("revenue")
    if revenue_value is None:
        revenue_label = "—"
    else:
        revenue_label = f"{float(revenue_value):.2f}".rstrip("0").rstrip(".")

    lines = [
        "📊 Смена",
        "",
        f"ID: {shift_id}",
        f"Дата: {_date_label(report_date)}",
        f"Открыта: {opened_at}",
        f"Закрыта: {closed_at}",
        f"Открыл: {opened_by}",
        f"Статус: {status}",
        f"Выручка: {revenue_label}",
        "",
        "Остатки:",
    ]

    if residuals:
        ordered_keys = [key for key in RESIDUAL_KEY_ORDER if key in residuals]
        extra_keys = sorted(key for key in residuals.keys() if key not in RESIDUAL_KEY_ORDER)
        for item_key in ordered_keys + extra_keys:
            residual = residuals[item_key]
            label = str(residual.get("item_label", "Позиция"))
            quantity = float(residual.get("quantity") or 0)
            unit = str(residual.get("unit") or "").strip()
            quantity_text = f"{quantity:.3f}".rstrip("0").rstrip(".")
            suffix = f" {unit}" if unit else ""
            lines.append(f"{label} — {quantity_text}{suffix}")
    else:
        lines.append("Нет данных")

    lines.extend(["", "Чек-листы:"])
    for checklist_type in CHECKLIST_SEQUENCE:
        done = int(checklists.get(checklist_type, 0))
        total = checklist_total_items(checklist_type)
        title = CHECKLIST_LABELS[checklist_type]
        lines.append(f"{title}: {done}/{total}")

    return "\n".join(lines)


@report_router.message(Command("reports"))
@report_router.message(F.text == MENU_REPORTS)
async def reports_start(
    message: Message,
) -> None:
    """Запускает интерактивное меню отчётов.

    Args:
        message: Входящее сообщение Telegram.

    Returns:
        None.
    """
    await message.answer(
        "Выберите тип отчёта:",
        reply_markup=_build_report_types_keyboard(),
    )


@report_router.callback_query(F.data == "reports:types")
async def reports_types_callback(
    callback: CallbackQuery,
) -> None:
    """Возвращает пользователя к выбору типа отчёта.

    Args:
        callback: Callback-запрос Telegram.

    Returns:
        None.
    """
    if not callback.message:
        await callback.answer()
        return
    await callback.message.edit_text(
        "Выберите тип отчёта:",
        reply_markup=_build_report_types_keyboard(),
    )
    await callback.answer()


@report_router.callback_query(F.data.startswith("reports:type:"))
async def reports_type_callback(
    callback: CallbackQuery,
    db: Database,
) -> None:
    """Показывает выбор даты для выбранного типа отчёта.

    Args:
        callback: Callback-запрос Telegram.
        db: Экземпляр базы данных.

    Returns:
        None.
    """
    if not callback.data or not callback.message:
        await callback.answer()
        return

    _, _, report_type = callback.data.split(":", maxsplit=2)
    if report_type not in REPORT_TYPES:
        await callback.answer("Неизвестный тип отчёта", show_alert=True)
        return

    dates = await db.get_shift_dates(limit=14)
    if not dates:
        await callback.message.edit_text(
            "Нет данных по сменам.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ К типам отчётов", callback_data="reports:types")]
                ]
            ),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        f"{REPORT_TYPES[report_type]}\nВыберите дату:",
        reply_markup=_build_dates_keyboard(report_type, dates),
    )
    await callback.answer()


@report_router.callback_query(F.data.startswith("reports:date:"))
async def reports_date_callback(
    callback: CallbackQuery,
    db: Database,
) -> None:
    """Формирует отчёт выбранного типа за указанную дату.

    Args:
        callback: Callback-запрос Telegram.
        db: Экземпляр базы данных.

    Returns:
        None.
    """
    if not callback.data or not callback.message:
        await callback.answer()
        return

    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return

    _, _, report_type, report_date = parts
    if report_type not in REPORT_TYPES:
        await callback.answer("Неизвестный тип отчёта", show_alert=True)
        return

    if report_type == "shifts":
        shifts = await db.get_shifts_by_date(report_date)
        if not shifts:
            await callback.message.edit_text(
                f"Смены за {_date_label(report_date)} не найдены.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="⬅️ К датам",
                                callback_data="reports:type:shifts",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="⬅️ К типам отчётов",
                                callback_data="reports:types",
                            )
                        ],
                    ]
                ),
            )
            await callback.answer()
            return

        await callback.message.edit_text(
            f"📅 Смены за {_date_label(report_date)}\nВыберите смену:",
            reply_markup=_build_shifts_keyboard(report_date, shifts),
        )
        await callback.answer()
        return

    if report_type == "residuals":
        rows = await db.get_close_residuals_by_date(report_date)
        totals: dict[tuple[str, str], float] = defaultdict(float)
        for row in rows:
            label = str(row.get("item_label") or "Позиция")
            unit = str(row.get("unit") or "").strip()
            quantity = float(row.get("quantity") or 0)
            totals[(label, unit)] += quantity

        lines = [f"🧾 Остатки за {_date_label(report_date)}", ""]
        if totals:
            for (label, unit), quantity in sorted(totals.items(), key=lambda item: item[0][0]):
                quantity_text = f"{quantity:.3f}".rstrip("0").rstrip(".")
                suffix = f" {unit}" if unit else ""
                lines.append(f"{label} — {quantity_text}{suffix}")
        else:
            lines.append("Нет данных по остаткам.")

        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ К датам", callback_data="reports:type:residuals")],
                    [InlineKeyboardButton(text="⬅️ К типам отчётов", callback_data="reports:types")],
                ]
            ),
        )
        await callback.answer()
        return

    shifts = await db.get_shifts_by_date(report_date)
    lines = [f"✅ Чек-листы за {_date_label(report_date)}", ""]
    if not shifts:
        lines.append("Смен за эту дату нет.")
    else:
        for shift in shifts:
            shift_id = int(shift["id"])
            employee = str(shift.get("opened_by") or shift.get("employee") or "Сотрудник")
            opened_at = _time_label(str(shift.get("opened_at") or shift.get("open_time") or ""))
            closed_at = _time_label(str(shift.get("closed_at") or shift.get("close_time") or ""))
            completion = await db.get_checklists_completion_by_shift(shift_id)
            lines.append(f"Смена #{shift_id} ({employee}, {opened_at}–{closed_at})")
            for checklist_type in CHECKLIST_SEQUENCE:
                done = int(completion.get(checklist_type, 0))
                total = checklist_total_items(checklist_type)
                lines.append(f"{CHECKLIST_LABELS[checklist_type]}: {done}/{total}")
            lines.append("")
        if lines[-1] == "":
            lines.pop()

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ К датам", callback_data="reports:type:checklists")],
                [InlineKeyboardButton(text="⬅️ К типам отчётов", callback_data="reports:types")],
            ]
        ),
    )
    await callback.answer()


@report_router.callback_query(F.data.startswith("reports:shift:"))
async def reports_shift_callback(
    callback: CallbackQuery,
    db: Database,
) -> None:
    """Показывает детальный отчёт по конкретной смене.

    Args:
        callback: Callback-запрос Telegram.
        db: Экземпляр базы данных.

    Returns:
        None.
    """
    if not callback.data or not callback.message:
        await callback.answer()
        return

    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return

    _, _, shift_id_raw, report_date = parts
    try:
        shift_id = int(shift_id_raw)
    except ValueError:
        await callback.answer("Некорректная смена", show_alert=True)
        return

    detail_text = await _build_shift_detail_text(db, shift_id)
    await callback.message.edit_text(
        detail_text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ К сменам",
                        callback_data=f"reports:date:shifts:{report_date}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ К типам отчётов",
                        callback_data="reports:types",
                    )
                ],
            ]
        ),
    )
    await callback.answer()
