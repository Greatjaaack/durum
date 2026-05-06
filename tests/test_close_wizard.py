"""Тесты модуля app/handlers/close_wizard.py.

Проверяют чистый data-слой мастера закрытия: сборку пунктов из YAML,
утилиты доступа, валидацию числового ввода, рендер экранов и
форматирование длительности.
"""
from __future__ import annotations

import pytest

from app.handlers.close_wizard import (
    CLOSE_DONE_CALLBACK_PREFIX,
    CLOSE_WIZARD_CALLBACK_PREFIX,
    CLOSE_WIZARD_ITEMS,
    CLOSE_WIZARD_RESIDUAL_INDEX,
    CLOSE_WIZARD_TOTAL,
    CloseInputRule,
    CloseWizardItem,
    build_close_done_summary_keyboard,
    build_close_done_summary_text,
    build_close_wizard_screen,
    close_duration_label,
    close_residual_normalized,
    close_wizard_first_incomplete_index,
    close_wizard_item_by_index,
    close_wizard_item_index_by_residual_key,
    close_wizard_item_requires_photo,
    close_wizard_missing_items,
    close_wizard_next_section_after_completion,
    close_wizard_parse_numeric_input,
    close_wizard_section_for_index,
    close_wizard_total_items,
    format_hhmm,
)


def test_total_matches_items_length() -> None:
    assert close_wizard_total_items() == CLOSE_WIZARD_TOTAL == len(CLOSE_WIZARD_ITEMS)


def test_residual_index_unique_and_complete() -> None:
    keys = [it.residual_key for it in CLOSE_WIZARD_ITEMS if it.residual_key]
    assert len(keys) == len(set(keys))
    for key in keys:
        idx = close_wizard_item_index_by_residual_key(key)
        assert idx is not None
        item = close_wizard_item_by_index(idx)
        assert item is not None and item.residual_key == key


def test_first_incomplete_index_walks_through() -> None:
    completed: set[int] = set()
    assert close_wizard_first_incomplete_index(completed) == 0
    completed = {0}
    assert close_wizard_first_incomplete_index(completed) == 1
    completed = set(range(CLOSE_WIZARD_TOTAL))
    assert close_wizard_first_incomplete_index(completed) == CLOSE_WIZARD_TOTAL


def test_section_for_index_clamps_into_range() -> None:
    assert close_wizard_section_for_index(-5) == 0
    assert close_wizard_section_for_index(0) == CLOSE_WIZARD_ITEMS[0].section_index
    big = close_wizard_section_for_index(CLOSE_WIZARD_TOTAL + 100)
    assert big == CLOSE_WIZARD_ITEMS[-1].section_index


def test_next_section_after_completion_advances_when_done() -> None:
    # Берём первую секцию и помечаем все её пункты как выполненные.
    first_section = CLOSE_WIZARD_ITEMS[0].section_index
    completed = {
        it.index for it in CLOSE_WIZARD_ITEMS if it.section_index == first_section
    }
    next_sec = close_wizard_next_section_after_completion(
        current_section=first_section,
        completed=completed,
    )
    assert next_sec != first_section


def test_next_section_stays_when_section_incomplete() -> None:
    section = CLOSE_WIZARD_ITEMS[0].section_index
    next_sec = close_wizard_next_section_after_completion(
        current_section=section,
        completed=set(),
    )
    assert next_sec == section


@pytest.mark.parametrize(
    "raw,expected_error_substr",
    [
        ("abc", "число"),
        # Минус отбраковывается ещё на этапе parse_close_residual_value,
        # поэтому юзер видит более общую ошибку «Введите число».
        ("-5", "число"),
        ("9999999", "слишком"),
    ],
)
def test_numeric_input_validation_errors(raw: str, expected_error_substr: str) -> None:
    item = next(it for it in CLOSE_WIZARD_ITEMS if it.item_type == "input")
    value, err = close_wizard_parse_numeric_input(item=item, raw_value=raw)
    assert value is None
    assert err is not None
    assert expected_error_substr.lower() in err.lower()


def test_numeric_input_accepts_valid_integer() -> None:
    item = next(
        it for it in CLOSE_WIZARD_ITEMS
        if it.item_type == "input" and it.input_rule and it.input_rule.only_integer
    )
    value, err = close_wizard_parse_numeric_input(item=item, raw_value="1000")
    assert err is None
    assert value == 1000


def test_check_item_rejects_numeric() -> None:
    item = next(it for it in CLOSE_WIZARD_ITEMS if it.item_type == "check")
    value, err = close_wizard_parse_numeric_input(item=item, raw_value="10")
    assert value is None
    assert err and "галк" in err.lower()


def test_requires_photo_only_for_check_items_with_flag() -> None:
    photo_items = [it for it in CLOSE_WIZARD_ITEMS if close_wizard_item_requires_photo(it)]
    assert all(it.item_type == "check" for it in photo_items)
    # input-пункты не должны просить фото
    for it in CLOSE_WIZARD_ITEMS:
        if it.item_type == "input":
            assert not close_wizard_item_requires_photo(it)


def test_missing_items_count_consistent() -> None:
    completed = {it.index for it in CLOSE_WIZARD_ITEMS[:3]}
    missing = close_wizard_missing_items(completed)
    assert len(missing) == CLOSE_WIZARD_TOTAL - 3


def test_format_hhmm_handles_iso_and_garbage() -> None:
    assert format_hhmm("2026-05-06T11:30:00+03:00") == "11:30"
    assert format_hhmm("11:30") == "11:30"
    assert format_hhmm("") == "--:--"
    assert format_hhmm(None) == "--:--"
    assert format_hhmm("garbage") == "--:--"


def test_close_duration_label() -> None:
    assert close_duration_label(None) == "н/д"
    assert "мин" in close_duration_label(180)


def test_close_residual_normalized_legacy_kg_for_chicken() -> None:
    residuals = {"marinated_chicken": {"quantity": 2.0, "unit_type": ""}}
    # Исторический формат: quantity в кг → нормализуем в граммы.
    assert close_residual_normalized(residuals, "marinated_chicken") == 2000.0


def test_close_residual_normalized_uses_normalized_quantity_when_present() -> None:
    residuals = {"soup": {"quantity": 1.0, "normalized_quantity": 1500.0}}
    assert close_residual_normalized(residuals, "soup") == 1500.0


def test_build_screen_returns_text_and_keyboard() -> None:
    text, keyboard = build_close_wizard_screen(
        active_section=0,
        selected_item_index=None,
        finish_confirm=False,
        completed=set(),
        values={},
    )
    assert isinstance(text, str) and text.strip()
    assert keyboard.inline_keyboard


def test_build_done_summary_text_and_keyboard() -> None:
    text = build_close_done_summary_text(
        employee="@emp",
        closed_at_hhmm="22:15",
        duration_label="13 мин",
        all_items_completed=True,
    )
    assert "Смена закрыта" in text
    assert "@emp" in text
    keyboard = build_close_done_summary_keyboard(shift_id=42)
    button = keyboard.inline_keyboard[0][0]
    assert CLOSE_DONE_CALLBACK_PREFIX in button.callback_data
    assert "42" in button.callback_data


def test_callback_prefixes_are_distinct() -> None:
    assert CLOSE_WIZARD_CALLBACK_PREFIX != CLOSE_DONE_CALLBACK_PREFIX
