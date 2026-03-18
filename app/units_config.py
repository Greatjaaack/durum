from __future__ import annotations

from dataclasses import dataclass
import math


# Базовая единица нормализации для каждого unit_type.
UNIT_TYPE_BASE_UNITS: dict[str, str] = {
    "weight_g": "г",
    "piece": "шт",
    "portion": "порц",
    "liter": "л",
    "gastro_unit": "гастроёмк",
    "sauce_gastro": "гастроёмк",
    "legacy_ml": "мл",
}


@dataclass(slots=True, frozen=True)
class NormalizedMeasurement:
    """Результат нормализации пользовательского ввода.

    Args:
        value: Значение в интерфейсной единице.
        unit_type: Тип единицы (например, sauce_gastro).
        normalized: Нормализованное значение в базовой единице.
        normalized_unit: Базовая единица нормализации.
    """

    value: float
    unit_type: str
    normalized: float
    normalized_unit: str


def parse_mixed_number(raw_value: str) -> float | None:
    """Парсит число в формате `1`, `1/2`, `1+1/2`.

    Args:
        raw_value: Строка ввода пользователя.

    Returns:
        Распарсенное число или None.
    """
    text = raw_value.strip().replace(",", ".").replace(" ", "")
    if not text:
        return None

    terms = text.split("+")
    if any(term == "" for term in terms):
        return None

    total = 0.0
    for term in terms:
        if "/" in term:
            fraction_parts = term.split("/")
            if len(fraction_parts) != 2:
                return None
            numerator_text, denominator_text = fraction_parts
            try:
                numerator = float(numerator_text)
                denominator = float(denominator_text)
            except ValueError:
                return None
            if (
                not math.isfinite(numerator)
                or not math.isfinite(denominator)
                or numerator < 0
                or denominator <= 0
            ):
                return None
            total += numerator / denominator
        else:
            try:
                value = float(term)
            except ValueError:
                return None
            if not math.isfinite(value) or value < 0:
                return None
            total += value
    if not math.isfinite(total):
        return None
    return total


def normalize_measurement_value(
    value: float,
    unit_type: str,
) -> NormalizedMeasurement | None:
    """Нормализует ввод пользователя в базовую единицу.

    Args:
        value: Значение в интерфейсной единице.
        unit_type: Тип интерфейсной единицы.

    Returns:
        Нормализованное значение или None.
    """
    if not math.isfinite(value) or value < 0:
        return None

    normalized_unit = UNIT_TYPE_BASE_UNITS.get(unit_type)
    if not normalized_unit:
        return None

    return NormalizedMeasurement(
        value=value,
        unit_type=unit_type,
        normalized=value,
        normalized_unit=normalized_unit,
    )


def restore_measurement_value(
    normalized_value: float,
    unit_type: str,
) -> float | None:
    """Переводит нормализованное значение обратно в интерфейсную единицу.

    Args:
        normalized_value: Значение в базовой единице.
        unit_type: Целевой интерфейсный тип единицы.

    Returns:
        Значение в интерфейсной единице или None.
    """
    if not math.isfinite(normalized_value) or normalized_value < 0:
        return None

    if unit_type in UNIT_TYPE_BASE_UNITS:
        return normalized_value
    return None
