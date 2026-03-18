from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.checklist_data import CLOSE_CHECKLIST, CLOSE_RESIDUAL_INPUTS
from app.units_config import UNIT_TYPE_BASE_UNITS, normalize_measurement_value, restore_measurement_value


CloseItemType = Literal["input", "check"]


@dataclass(frozen=True)
class CloseSectionMeta:
    """Метаданные секции мастера закрытия смены.

    Args:
        emoji: Emoji секции.
        Нет дополнительных полей.
    """

    emoji: str


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


SECTION_META_BY_TITLE: dict[str, CloseSectionMeta] = {
    "Передача заготовок на следующую смену": CloseSectionMeta(emoji="🤝"),
    "Остатки продуктов": CloseSectionMeta(emoji="🥩"),
    "Продукты": CloseSectionMeta(emoji="📦"),
    "Рабочая зона": CloseSectionMeta(emoji="🧼"),
    "Фритюр": CloseSectionMeta(emoji="🍟"),
    "Расходники": CloseSectionMeta(emoji="🧴"),
    "Зал": CloseSectionMeta(emoji="🧹"),
    "Выключение оборудования": CloseSectionMeta(emoji="🔌"),
}

INPUT_RULES: dict[str, CloseInputRule] = {
    "marinated_chicken": CloseInputRule(
        prompt="Введите остаток маринованной курицы (г)",
        display_unit="г",
        unit_type="weight_g",
        max_value=50000.0,
        only_integer=True,
        step=1.0,
    ),
    "fried_chicken": CloseInputRule(
        prompt="Введите остаток жареной курицы (г)",
        display_unit="г",
        unit_type="weight_g",
        max_value=50000.0,
        only_integer=True,
        step=1.0,
    ),
    "lavash": CloseInputRule(
        prompt="Введите количество оставшегося лаваша (шт)",
        display_unit="шт",
        unit_type="piece",
        max_value=2000.0,
        only_integer=True,
        step=1.0,
    ),
    "fried_lavash": CloseInputRule(
        prompt="Введите количество оставшегося жареного лаваша (шт)",
        display_unit="шт",
        unit_type="piece",
        max_value=2000.0,
        only_integer=True,
        step=1.0,
    ),
    "soup": CloseInputRule(
        prompt="Введите остаток супа (г)",
        display_unit="г",
        unit_type="weight_g",
        max_value=50000.0,
        only_integer=True,
        step=1.0,
    ),
    "sauce": CloseInputRule(
        prompt="Выберите остаток соуса кнопкой или введите вручную",
        display_unit="гастроёмк",
        unit_type="gastro_unit",
        max_value=100.0,
        quick_buttons=(("0", "0"), ("1/2", "1/2"), ("1", "1"), ("1+1/2", "1+1/2")),
        step=0.5,
    ),
}


def _build_items() -> tuple[CloseWizardItem, ...]:
    """Строит линейный список пунктов мастера закрытия.

    Args:
        Нет параметров.

    Returns:
        Кортеж пунктов мастера.
    """
    items: list[CloseWizardItem] = []
    cursor = 0
    for section_index, section in enumerate(CLOSE_CHECKLIST):
        section_title = str(section["title"]).strip()
        meta = SECTION_META_BY_TITLE.get(
            section_title,
            CloseSectionMeta(emoji="▫️"),
        )
        for item_text_raw in section["items"]:
            item_text = str(item_text_raw).strip()
            residual_config = CLOSE_RESIDUAL_INPUTS.get(item_text)
            if residual_config:
                residual_key = str(residual_config["key"])
                input_rule = INPUT_RULES[residual_key]
                items.append(
                    CloseWizardItem(
                        index=cursor,
                        section_index=section_index,
                        section_title=section_title,
                        section_emoji=meta.emoji,
                        text=item_text,
                        item_type="input",
                        residual_key=residual_key,
                        storage_unit=str(residual_config["unit"]),
                        input_rule=input_rule,
                    )
                )
            else:
                items.append(
                    CloseWizardItem(
                        index=cursor,
                        section_index=section_index,
                        section_title=section_title,
                        section_emoji=meta.emoji,
                        text=item_text,
                        item_type="check",
                    )
                )
            cursor += 1
    return tuple(items)


CLOSE_WIZARD_ITEMS = _build_items()
CLOSE_WIZARD_TOTAL = len(CLOSE_WIZARD_ITEMS)
CLOSE_WIZARD_STEPS_TOTAL = len(CLOSE_CHECKLIST)

# Префикс callback_data для мастера закрытия смены.
CLOSE_WIZARD_CALLBACK_PREFIX = "closewiz"


def _fmt_number(value: float) -> str:
    """Форматирует число без лишних нулей.

    Args:
        value: Число для отображения.

    Returns:
        Отформатированная строка.
    """
    return f"{value:.3f}".rstrip("0").rstrip(".")


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
    """Возвращает общее количество пунктов мастера.

    Args:
        Нет параметров.

    Returns:
        Число пунктов.
    """
    return CLOSE_WIZARD_TOTAL


def close_wizard_first_incomplete_index(
    completed: set[int],
) -> int:
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


def close_wizard_normalized_unit(
    item: CloseWizardItem,
) -> str | None:
    """Возвращает базовую единицу нормализации для пункта мастера.

    Args:
        item: Пункт мастера.

    Returns:
        Базовая единица (например, `г`, `мл`) или None.
    """
    if not item.input_rule:
        return None
    return UNIT_TYPE_BASE_UNITS.get(item.input_rule.unit_type)


def _progress_line(
    done: int,
    total: int,
) -> tuple[str, int]:
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


def _as_question_text(
    item_text: str,
) -> str:
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
    bar, percent = _progress_line(done, total)

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
                f"Сейчас: {_fmt_number(values[item.residual_key])} {item.input_rule.display_unit}"
            )
    else:
        lines.append(_as_question_text(item.text))

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
