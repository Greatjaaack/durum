from __future__ import annotations

from dataclasses import dataclass


CHECKLIST_CALLBACK_PREFIX = "checklist"
CHECKLIST_CALLBACK_ACTIONS = frozenset({"item", "section"})


@dataclass(slots=True, frozen=True)
class ChecklistCallbackPayload:
    """Нормализованный payload callback-кнопки чек-листа.

    Args:
        checklist_type: Тип чек-листа.
        action: Тип действия callback (`item` или `section`).
        value: Индекс пункта или секции.
        shift_id: Идентификатор смены или None.
    """

    checklist_type: str
    action: str
    value: int
    shift_id: int | None = None


def build_checklist_callback(
    *,
    checklist_type: str,
    action: str,
    value: int,
    shift_id: int | None = None,
) -> str:
    """Формирует callback_data для inline-кнопки чек-листа.

    Args:
        checklist_type: Тип чек-листа.
        action: Действие (`item` или `section`).
        value: Индекс пункта или секции.
        shift_id: ID смены или None.

    Returns:
        Строка callback_data.
    """
    if action not in CHECKLIST_CALLBACK_ACTIONS:
        raise ValueError(f"Unsupported checklist callback action: {action}")

    base = f"{CHECKLIST_CALLBACK_PREFIX}:{checklist_type}:{action}:{value}"
    if shift_id is None:
        return base
    return f"{base}:{shift_id}"


def parse_checklist_callback(
    callback_data: str,
) -> ChecklistCallbackPayload | None:
    """Парсит callback_data чек-листа в типизированный payload.

    Поддерживает форматы:
    - `checklist:<type>:<action>:<value>`
    - `checklist:<type>:<action>:<value>:<shift_id>`
    - legacy `checklist:<type>:<value>` (считается `action=item`)

    Args:
        callback_data: Строка callback_data.

    Returns:
        Распарсенный payload или None при некорректном формате.
    """
    parts = callback_data.split(":")
    shift_id: int | None = None

    if len(parts) == 5:
        prefix, checklist_type, action, value_raw, shift_id_raw = parts
    elif len(parts) == 4:
        prefix, checklist_type, action, value_raw = parts
        shift_id_raw = None
    elif len(parts) == 3:
        prefix, checklist_type, value_raw = parts
        action = "item"
        shift_id_raw = None
    else:
        return None

    if prefix != CHECKLIST_CALLBACK_PREFIX:
        return None
    if action not in CHECKLIST_CALLBACK_ACTIONS:
        return None

    try:
        value = int(value_raw)
    except ValueError:
        return None

    if shift_id_raw is not None:
        try:
            shift_id = int(shift_id_raw)
        except ValueError:
            return None

    return ChecklistCallbackPayload(
        checklist_type=checklist_type,
        action=action,
        value=value,
        shift_id=shift_id,
    )
