from __future__ import annotations

from dataclasses import dataclass


# Объёмы/вес одной условной единицы хранения.
# Для гастроёмкостей и тубусов значение задаётся в базовой единице продукта.
UNITS_CONFIG: dict[str, float] = {
    "sauce_gastro": 800.0,  # мл
    "tomato_gastro": 1200.0,  # г
    "cucumber_gastro": 1000.0,  # г
    "meat_gastro": 2000.0,  # г
    "tube": 500.0,  # г
}

# Базовая единица нормализации для каждого unit_type.
UNIT_TYPE_BASE_UNITS: dict[str, str] = {
    "weight_g": "г",
    "piece": "шт",
    "portion": "порц",
    "sauce_gastro": "мл",
    "gastro_unit": "гастроёмк",
    "tomato_gastro": "г",
    "cucumber_gastro": "г",
    "meat_gastro": "г",
    "tube": "г",
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
            if numerator < 0 or denominator <= 0:
                return None
            total += numerator / denominator
        else:
            try:
                value = float(term)
            except ValueError:
                return None
            if value < 0:
                return None
            total += value
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
    if value < 0:
        return None

    normalized_unit = UNIT_TYPE_BASE_UNITS.get(unit_type)
    if not normalized_unit:
        return None

    if unit_type in UNITS_CONFIG:
        normalized = value * UNITS_CONFIG[unit_type]
    else:
        normalized = value

    return NormalizedMeasurement(
        value=value,
        unit_type=unit_type,
        normalized=normalized,
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
    if normalized_value < 0:
        return None

    if unit_type in UNITS_CONFIG:
        unit_volume = UNITS_CONFIG[unit_type]
        if unit_volume <= 0:
            return None
        return normalized_value / unit_volume
    if unit_type in UNIT_TYPE_BASE_UNITS:
        return normalized_value
    return None
