from __future__ import annotations

from pathlib import Path

import yaml


_CONFIG_PATH = Path(__file__).resolve().with_name("config.yaml")


def _load_config() -> dict[str, object]:
    """Загружает YAML-конфиг чек-листов.

    Args:
        Нет параметров.

    Returns:
        Словарь конфигурации.
    """
    with _CONFIG_PATH.open("r", encoding="utf-8") as file:
        payload = yaml.safe_load(file) or {}
    if not isinstance(payload, dict):
        raise ValueError("Checklist config must be a mapping")
    return payload


def _normalize_sections(raw: object, field_name: str) -> list[dict[str, object]]:
    """Приводит список секций к ожидаемому формату.

    Args:
        raw: Сырые данные секций.
        field_name: Имя поля для сообщения об ошибке.

    Returns:
        Нормализованный список секций.
    """
    if not isinstance(raw, list):
        raise ValueError(f"{field_name} must be a list")

    sections: list[dict[str, object]] = []
    for index, section in enumerate(raw):
        if not isinstance(section, dict):
            raise ValueError(f"{field_name}[{index}] must be a mapping")
        title = str(section.get("title", "")).strip()
        items_raw = section.get("items", [])
        if not title:
            raise ValueError(f"{field_name}[{index}] title is required")
        if not isinstance(items_raw, list):
            raise ValueError(f"{field_name}[{index}].items must be a list")
        items = [str(item).strip() for item in items_raw if str(item).strip()]
        sections.append({"title": title, "items": items})
    return sections


def _normalize_titles(raw: object) -> dict[str, str]:
    """Приводит карту заголовков чек-листов.

    Args:
        raw: Сырые данные заголовков.

    Returns:
        Словарь titles по типу чек-листа.
    """
    if not isinstance(raw, dict):
        raise ValueError("checklist_titles must be a mapping")
    result: dict[str, str] = {}
    for key, value in raw.items():
        normalized_key = str(key).strip()
        normalized_value = str(value).strip()
        if normalized_key and normalized_value:
            result[normalized_key] = normalized_value
    return result


def _normalize_emoji_map(raw: object) -> dict[str, str]:
    """Приводит карту эмодзи секций закрытия.

    Args:
        raw: Сырые данные.

    Returns:
        Словарь title -> emoji.
    """
    if not isinstance(raw, dict):
        raise ValueError("close_section_emoji_by_title must be a mapping")
    result: dict[str, str] = {}
    for key, value in raw.items():
        title = str(key).strip()
        emoji = str(value).strip()
        if title and emoji:
            result[title] = emoji
    return result


def _normalize_quick_buttons(raw: object) -> tuple[tuple[str, str], ...]:
    """Нормализует quick-кнопки из YAML.

    Args:
        raw: Сырые кнопки.

    Returns:
        Кортеж кнопок в формате (label, value).
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("quick_buttons must be a list")
    result: list[tuple[str, str]] = []
    for index, button in enumerate(raw):
        if not isinstance(button, dict):
            raise ValueError(f"quick_buttons[{index}] must be a mapping")
        label = str(button.get("label", "")).strip()
        value = str(button.get("value", "")).strip()
        if label and value:
            result.append((label, value))
    return tuple(result)


def _normalize_residual_inputs(raw: object) -> dict[str, dict[str, object]]:
    """Приводит настройки обязательных остатков закрытия.

    Args:
        raw: Сырые данные настроек.

    Returns:
        Нормализованный словарь по тексту пункта.
    """
    if not isinstance(raw, dict):
        raise ValueError("close_residual_inputs must be a mapping")

    result: dict[str, dict[str, object]] = {}
    for item_label_raw, config_raw in raw.items():
        item_label = str(item_label_raw).strip()
        if not item_label:
            continue
        if not isinstance(config_raw, dict):
            raise ValueError(f"close_residual_inputs[{item_label}] must be a mapping")

        key = str(config_raw.get("key", "")).strip()
        prompt = str(config_raw.get("prompt", "")).strip()
        unit = str(config_raw.get("unit", "")).strip()
        checklist_item_raw = config_raw.get("checklist_item")
        checklist_item = (
            str(checklist_item_raw).strip()
            if isinstance(checklist_item_raw, str)
            else ""
        )
        if not checklist_item:
            checklist_item = item_label
        stock_item_raw = config_raw.get("stock_item")
        stock_item = str(stock_item_raw).strip() if isinstance(stock_item_raw, str) else None
        stock_item = stock_item or None
        if not key:
            raise ValueError(f"close_residual_inputs[{item_label}].key is required")
        if not prompt:
            raise ValueError(f"close_residual_inputs[{item_label}].prompt is required")
        if not unit:
            raise ValueError(f"close_residual_inputs[{item_label}].unit is required")

        normalized: dict[str, object] = {
            "key": key,
            "prompt": prompt,
            "unit": unit,
            "checklist_item": checklist_item,
            "stock_item": stock_item,
        }

        unit_type = str(config_raw.get("unit_type", "")).strip()
        if unit_type:
            normalized["unit_type"] = unit_type
        max_value = config_raw.get("max_value")
        if max_value is not None:
            normalized["max_value"] = float(max_value)
        only_integer = config_raw.get("only_integer")
        if only_integer is not None:
            normalized["only_integer"] = bool(only_integer)
        step = config_raw.get("step")
        if step is not None:
            normalized["step"] = float(step)
        normalized["quick_buttons"] = _normalize_quick_buttons(config_raw.get("quick_buttons"))

        result[item_label] = normalized
    return result


_RAW_CONFIG = _load_config()

OPEN_CHECKLIST = _normalize_sections(_RAW_CONFIG.get("open_checklist"), "open_checklist")
MID_CHECKLIST = _normalize_sections(_RAW_CONFIG.get("mid_checklist"), "mid_checklist")
CLOSE_CHECKLIST = _normalize_sections(_RAW_CONFIG.get("close_checklist"), "close_checklist")

CLOSE_SECTION_EMOJI_BY_TITLE = _normalize_emoji_map(
    _RAW_CONFIG.get("close_section_emoji_by_title"),
)
CLOSE_RESIDUAL_INPUTS = _normalize_residual_inputs(
    _RAW_CONFIG.get("close_residual_inputs"),
)
CLOSE_RESIDUAL_INPUTS_BY_CHECKLIST_ITEM = {
    checklist_item: config
    for item_label, config in CLOSE_RESIDUAL_INPUTS.items()
    if (
        checklist_item := str(config.get("checklist_item") or item_label).strip()
    )
}

CHECKLISTS = {
    "open": OPEN_CHECKLIST,
    "mid": MID_CHECKLIST,
    "close": CLOSE_CHECKLIST,
}

CHECKLIST_TITLES = _normalize_titles(_RAW_CONFIG.get("checklist_titles"))

CLOSE_RESIDUAL_LABELS_BY_KEY = {
    str(config["key"]): str(config.get("checklist_item") or item_label)
    for item_label, config in CLOSE_RESIDUAL_INPUTS.items()
}
CLOSE_RESIDUAL_UNITS_BY_KEY = {
    str(config["key"]): str(config["unit"])
    for config in CLOSE_RESIDUAL_INPUTS.values()
}
