"""Closed registry of value ops (spec section 5). Each op: read(raw)->value|UNKNOWN, write(value)->raw|raises."""
from __future__ import annotations

from typing import Any

from .model import SpecEnum, SpecInteger
from .runtime import WriteRejected


def validate_bool_read(raw: Any) -> Any:
    return raw if raw is not None and raw in (True, False) else None   # 0/1 pass as-is (P-12)


def validate_bool_write(value: Any) -> bool:
    if not isinstance(value, bool):
        raise WriteRejected(f"Invalid boolean value `{value}` ({type(value).__name__})")
    return value


def validate_enum_read(spec: SpecEnum, raw: Any) -> Any:
    return raw if raw is not None and raw in spec.range else None


def validate_enum_write(spec: SpecEnum, value: Any) -> str:
    if not isinstance(value, str) or value not in spec.range:
        raise WriteRejected(f"Invalid enum value `{value}`")
    return value


def scale_value(spec: SpecInteger, v: int) -> float:
    return v / (10 ** spec.scale)


def validate_int_read(spec: SpecInteger, raw: Any) -> float | None:
    # bool is an int subclass: passes (core parity); floats are rejected
    if isinstance(raw, int) and spec.min <= raw <= spec.max:
        v = scale_value(spec, raw)
        return scale_value(spec, spec.max) - v if spec.inverted else v    # InvertedIntegerTypeInformationEx
    return None


def validate_int_write(spec: SpecInteger, value: Any) -> int:
    if not isinstance(value, (int, float)):
        raise WriteRejected(f"Invalid numeric value `{value}` ({type(value).__name__})")
    if spec.inverted:
        value = scale_value(spec, spec.max) - value
    raw = round(value * (10 ** spec.scale))   # banker's rounding, like core
    if not (spec.min <= raw <= spec.max):
        raise WriteRejected(f"Value `{raw}` out of range: ({spec.min}-{spec.max})")
    return raw


def remap(value: float, from_min: float, from_max: float, to_min: float, to_max: float, reverse: bool = False) -> float:
    """RemapHelper.remap_value: linear map, `reverse` flips the SOURCE about its range first."""
    if reverse:
        value = from_max - value + from_min
    return ((value - from_min) / (from_max - from_min)) * (to_max - to_min) + to_min


def remap_read(spec: SpecInteger, raw: Any, target_min: float, target_max: float, reverse: bool = False) -> int | None:
    """Percentage wrappers: validate_int -> remap(scaled range -> target) -> round (banker's)."""
    v = validate_int_read(spec, raw)
    if v is None:
        return None
    return round(remap(v, scale_value(spec, spec.min), scale_value(spec, spec.max), target_min, target_max, reverse))


def remap_write(spec: SpecInteger, value: Any, target_min: float, target_max: float, reverse: bool = False) -> int:
    """Inverse: remap(target -> scaled range) then validate_int write."""
    return validate_int_write(
        spec, remap(value, target_min, target_max, scale_value(spec, spec.min), scale_value(spec, spec.max), reverse))
