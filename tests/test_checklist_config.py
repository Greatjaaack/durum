"""Тесты конфигурации чек-листов: YAML загружается, структура ожидаема,
поддерживаются inline-опции пунктов (``requires_photo``)."""
from __future__ import annotations

from app.checklist.data import (
    CHECKLISTS,
    CLOSE_CHECKLIST,
    CLOSE_RESIDUAL_INPUTS,
    CLOSE_RESIDUAL_INPUTS_BY_SECTION_AND_ITEM,
    OPEN_CHECKLIST,
    checklist_item_options,
    checklist_item_requires_photo,
    flat_checklist_items,
)


def test_checklists_loaded() -> None:
    assert OPEN_CHECKLIST, "open_checklist пустой"
    assert CLOSE_CHECKLIST, "close_checklist пустой"
    for sec in OPEN_CHECKLIST + CLOSE_CHECKLIST:
        assert isinstance(sec.get("title"), str) and sec["title"].strip()
        assert isinstance(sec.get("items"), list)
        assert isinstance(sec.get("item_options"), dict)


def test_uniform_section_is_first_in_open() -> None:
    """Секция «Униформа и внешний вид» — первая в открытии смены."""
    assert OPEN_CHECKLIST[0]["title"] == "Униформа и внешний вид"
    items = OPEN_CHECKLIST[0]["items"]
    assert any("фартук" in it.lower() for it in items)


def test_close_residuals_are_section_scoped() -> None:
    """Каждый close-остаток имеет section_title, и матчится по паре (section, text)."""
    for label, cfg in CLOSE_RESIDUAL_INPUTS.items():
        assert cfg.get("section_title"), f"{label}: section_title обязателен"
        key = (cfg["section_title"], cfg["checklist_item"])
        assert key in CLOSE_RESIDUAL_INPUTS_BY_SECTION_AND_ITEM, key


def test_no_duplicate_residual_inputs_in_wizard() -> None:
    """Сборка close-wizard не задваивает residual_key."""
    from app.handlers.shift import CLOSE_WIZARD_ITEMS

    keys = [it.residual_key for it in CLOSE_WIZARD_ITEMS if it.item_type == "input"]
    assert len(keys) == len(set(keys)), f"duplicate residual keys: {keys}"


def test_required_residual_keys_are_all_covered() -> None:
    """Все CLOSE_REQUIRED_RESIDUAL_KEYS присутствуют в wizard ровно один раз."""
    from app.handlers.constants import CLOSE_REQUIRED_RESIDUAL_KEYS
    from app.handlers.shift import CLOSE_WIZARD_ITEMS

    required = set(CLOSE_REQUIRED_RESIDUAL_KEYS)
    in_wizard = {
        it.residual_key
        for it in CLOSE_WIZARD_ITEMS
        if it.item_type == "input" and it.residual_key
    }
    assert required == in_wizard, f"mismatch required={required} wizard={in_wizard}"


def test_requires_photo_flag_picked_up() -> None:
    """Пункты с requires_photo=true получают флаг и срабатывает helper."""
    from app.handlers.shift import CLOSE_WIZARD_ITEMS, _close_wizard_item_requires_photo

    photo_items = [it for it in CLOSE_WIZARD_ITEMS if _close_wizard_item_requires_photo(it)]
    photo_texts = {it.text for it in photo_items}
    assert "Сделано фото корзины" in photo_texts
    assert "Сделано фото масла" in photo_texts
    assert "Сделано фото чистого гриля" in photo_texts


def test_open_photo_item_yaml_flag() -> None:
    """Пункт «фото холодильника» в открытии теперь помечен через YAML."""
    open_items = flat_checklist_items("open")
    fridge_idx = next(
        i for i, t in enumerate(open_items) if "фото холодильника" in t.lower()
    )
    assert checklist_item_requires_photo("open", fridge_idx) is True
    options = checklist_item_options("open", fridge_idx)
    assert options.get("requires_photo") is True


def test_checklists_dict_has_three_types() -> None:
    assert set(CHECKLISTS.keys()) == {"open", "mid", "close"}
