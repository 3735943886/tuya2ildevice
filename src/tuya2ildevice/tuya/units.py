"""Unit-policy DTO and thin Rust binding."""
from dataclasses import dataclass

from ..native import call

TEMP_CONVERT = {'c': '°C', 'f': '°F'}


@dataclass(frozen=True)
class UnitResult:
    native_unit: str | None
    device_class: str | None
    suggested_unit: str | None


def resolve_unit(platform, device_class, dp_unit, fallback_unit, suggested_unit, temp_unit_convert, allowed_units):
    result = call('unit_policy', platform=platform, device_class=device_class, dp_unit=dp_unit,
                  fallback_unit=fallback_unit, suggested_unit=suggested_unit,
                  temp_unit_convert=temp_unit_convert, allowed_units=allowed_units)
    return UnitResult(result['native_unit'], result['device_class'], result['suggested_unit'])
