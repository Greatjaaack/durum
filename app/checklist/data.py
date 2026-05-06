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

    Каждый пункт можно задать строкой или мапой ``{text, requires_photo}``.
    Опции пунктов сохраняются в ``item_options`` (карта по тексту пункта),
    а ``items`` остаётся плоским списком строк для обратной совместимости.

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

        items: list[str] = []
        item_options: dict[str, dict[str, object]] = {}
        for item_index, item_raw in enumerate(items_raw):
            if isinstance(item_raw, str):
                text = item_raw.strip()
                options: dict[str, object] = {}
            elif isinstance(item_raw, dict):
                text = str(item_raw.get("text", "")).strip()
                if not text:
                    raise ValueError(
                        f"{field_name}[{index}].items[{item_index}].text is required"
                    )
                options = {}
                requires_photo = item_raw.get("requires_photo")
                if requires_photo is not None:
                    options["requires_photo"] = bool(requires_photo)
            else:
                raise ValueError(
                    f"{field_name}[{index}].items[{item_index}] must be a string or mapping"
                )
            if not text:
                continue
            items.append(text)
            if options:
                item_options[text] = options
        sections.append({"title": title, "items": items, "item_options": item_options})
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
        section_title_raw = config_raw.get("section_title")
        section_title = (
            str(section_title_raw).strip()
            if isinstance(section_title_raw, str)
            else ""
        )
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
            "section_title": section_title,
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

MID_NUMERIC_INPUTS: dict[str, dict[str, object]] = _normalize_residual_inputs(
    _RAW_CONFIG.get("mid_numeric_inputs") or {}
)
MID_NUMERIC_INPUTS_BY_ITEM_TEXT: dict[str, dict[str, object]] = {
    str(cfg.get("checklist_item") or label): cfg
    for label, cfg in MID_NUMERIC_INPUTS.items()
}

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

# Словарь остатков, индексированный парой (секция, текст пункта).
# Используется при сборке мастера закрытия, чтобы один и тот же текст пункта
# в разных секциях (например, «Сроки годности» vs «Убрать продукты»)
# не приводил к задвоению ввода остатков.
CLOSE_RESIDUAL_INPUTS_BY_SECTION_AND_ITEM: dict[tuple[str, str], dict[str, object]] = {}
for _item_label, _config in CLOSE_RESIDUAL_INPUTS.items():
    _checklist_item = str(_config.get("checklist_item") or _item_label).strip()
    _section_title = str(_config.get("section_title") or "").strip()
    if not _checklist_item or not _section_title:
        continue
    CLOSE_RESIDUAL_INPUTS_BY_SECTION_AND_ITEM[(_section_title, _checklist_item)] = _config

CHECKLISTS = {
    "open": OPEN_CHECKLIST,
    "mid": MID_CHECKLIST,
    "close": CLOSE_CHECKLIST,
}


def checklist_item_options(
    checklist_type: str,
    item_index: int,
) -> dict[str, object]:
    """Возвращает опции пункта чек-листа по его глобальному индексу.

    Args:
        checklist_type: Тип чек-листа ('open', 'mid', 'close').
        item_index: Глобальный индекс пункта в плоском списке.

    Returns:
        Словарь опций (например, ``requires_photo``); пустой словарь если опций нет.
    """
    sections = CHECKLISTS.get(checklist_type, [])
    cursor = 0
    for section in sections:
        items = section.get("items", []) or []
        section_len = len(items)
        if cursor <= item_index < cursor + section_len:
            local_index = item_index - cursor
            text = str(items[local_index])
            options_map = section.get("item_options", {}) or {}
            options = options_map.get(text, {})
            return dict(options) if options else {}
        cursor += section_len
    return {}


def checklist_item_requires_photo(checklist_type: str, item_index: int) -> bool:
    """Возвращает True, если для пункта обязательно фото.

    Args:
        checklist_type: Тип чек-листа.
        item_index: Глобальный индекс пункта.

    Returns:
        Признак обязательного фото.
    """
    return bool(checklist_item_options(checklist_type, item_index).get("requires_photo", False))


def flat_checklist_items(checklist_type: str) -> list[str]:
    """Возвращает плоский список текстов всех пунктов чек-листа.

    Args:
        checklist_type: Тип чек-листа.

    Returns:
        Список текстов пунктов по порядку.
    """
    sections = CHECKLISTS.get(checklist_type, [])
    return [str(item) for section in sections for item in section["items"]]

CHECKLIST_TITLES = _normalize_titles(_RAW_CONFIG.get("checklist_titles"))

PERIODIC_RESIDUAL_INPUTS: dict[str, dict[str, object]] = _normalize_residual_inputs(
    _RAW_CONFIG.get("periodic_residual_inputs") or {}
)

# Список позиций для последовательного опроса (сохраняет порядок YAML).
PERIODIC_RESIDUAL_INPUTS_LIST: list[dict[str, object]] = list(PERIODIC_RESIDUAL_INPUTS.values())

CLOSE_RESIDUAL_LABELS_BY_KEY = {
    str(config["key"]): str(config.get("checklist_item") or item_label)
    for item_label, config in CLOSE_RESIDUAL_INPUTS.items()
}
CLOSE_RESIDUAL_UNITS_BY_KEY = {
    str(config["key"]): str(config["unit"])
    for config in CLOSE_RESIDUAL_INPUTS.values()
}
