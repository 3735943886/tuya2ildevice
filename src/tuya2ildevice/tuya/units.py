"""UnitPolicy (spec 5.3): sensor.py `_validate_device_class_unit` / number.py equivalent, as a pure function."""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from typing import Any

_ALIASES: dict[str, dict[str, str]] = json.loads((pathlib.Path(__file__).parent / "tables" / "_units.json").read_text())
_TEMP_CONVERT = {"c": "°C", "f": "°F"}


@dataclass(frozen=True)
class UnitResult:
    native_unit: str | None
    device_class: str | None
    suggested_unit: str | None


def resolve_unit(
    platform: str,
    device_class: str | None,
    dp_unit: str | None,
    fallback_unit: str | None,
    suggested_unit: str | None,
    temp_unit_convert: Any,
    allowed_units: dict[str, dict[str, list]],
) -> UnitResult:
    if device_class == "enum":
        return UnitResult(None, device_class, suggested_unit)
    allowed = allowed_units.get(platform, {}).get(device_class, ())
    if device_class is None or dp_unit in allowed:
        return UnitResult(dp_unit, device_class, suggested_unit)
    if device_class == "temperature" and not dp_unit and temp_unit_convert in _TEMP_CONVERT:
        return UnitResult(_TEMP_CONVERT[temp_unit_convert], device_class, suggested_unit)
    if dp_unit is not None:
        table = _ALIASES.get(device_class)
        if table and (uom := table.get(dp_unit) or table.get(dp_unit.lower())) is not None:
            return UnitResult(uom, device_class, suggested_unit)
    if fallback_unit is not None:
        return UnitResult(fallback_unit, device_class, suggested_unit)
    return UnitResult(dp_unit, None, None)
