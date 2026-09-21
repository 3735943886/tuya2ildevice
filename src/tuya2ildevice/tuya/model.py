"""Inputs and dp resolution (spec sections 1.1, 1.2, 2). Sans-I/O, dependency-free."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Literal

# --- 1.1 types --------------------------------------------------------------
BITMAP, BOOLEAN, ENUM, INTEGER, JSON, RAW, STRING = "Bitmap", "Boolean", "Enum", "Integer", "Json", "Raw", "String"
# core const.py:44-63 -- exact names plus lower-case aliases; NOT "Bool"/"Value"->? see below
_TYPES = {
    "Bitmap": BITMAP, "bitmap": BITMAP,
    "Boolean": BOOLEAN, "bool": BOOLEAN,
    "Enum": ENUM, "enum": ENUM,
    "Integer": INTEGER, "value": INTEGER,
    "Json": JSON, "json": JSON,
    "Raw": RAW, "raw": RAW,
    "String": STRING, "string": STRING,
}


def normalize_type(name: Any) -> str | None:
    return _TYPES.get(name) if isinstance(name, str) else None


class SchemaError(ValueError):
    """Malformed dp spec (parity tag P-13: core raises; the engine reports per device)."""


@dataclass(frozen=True)
class SpecInteger:
    min: int
    max: int
    scale: int
    step: int
    unit: str | None = None
    inverted: bool = False   # quirk TypeOverride `invert_int_max` (spec 1.3)


@dataclass(frozen=True)
class SpecEnum:
    range: tuple[str, ...]


@dataclass(frozen=True)
class SpecBitmap:
    label: tuple[str, ...]


@dataclass(frozen=True)
class SpecJson:
    raw: dict


@dataclass(frozen=True)
class DpSpec:
    code: str
    type: str
    values: Any = None           # dict | JSON str, verbatim
    report_type: str | None = None

    def _values(self) -> dict | None:
        v = self.values
        if isinstance(v, str):
            v = json.loads(v) if v else None
        return v or None

    def parse(self):
        """Typed spec, or None when core's find_dpcode would find nothing (falsy values)."""
        kind = normalize_type(self.type)
        if kind in (BOOLEAN, RAW, STRING):
            return object()
        v = self._values()
        if kind == JSON:
            return SpecJson(v or {})
        if not v:
            return None
        try:
            if kind == INTEGER:
                return SpecInteger(int(v["min"]), int(v["max"]), int(v["scale"]), int(v["step"]), v.get("unit"))
            if kind == ENUM:
                return SpecEnum(tuple(v["range"]))
            if kind == BITMAP:
                return SpecBitmap(tuple(v["label"]))
        except (KeyError, TypeError, ValueError) as e:
            raise SchemaError(f"{self.code}: {e!r}") from e
        return None


@dataclass
class DeviceSchema:
    id: str
    category: str
    product_id: str = ""
    name: str = ""
    product_name: str = ""
    online: bool = True
    function: dict[str, DpSpec] = field(default_factory=dict)
    status_range: dict[str, DpSpec] = field(default_factory=dict)
    status: dict[str, Any] = field(default_factory=dict)
    type_overrides: dict[str, str] = field(default_factory=dict)
    dpmap: dict[int, str] = field(default_factory=dict)


# --- 2 resolution -----------------------------------------------------------
@dataclass(frozen=True)
class DpRef:
    candidates: tuple[str, ...]
    types: tuple[str, ...]
    source: Literal["status_range_first", "function_first"] = "status_range_first"


@dataclass(frozen=True)
class ResolvedDp:
    code: str
    spec: Any
    kind: str
    report_type: str | None


def resolve(schema: DeviceSchema, ref: DpRef) -> ResolvedDp | None:
    """TypeInformation.find_dpcode: code-major; per candidate try sources in order;
    accept iff entry exists AND normalized type in ref.types AND spec parses."""
    order = (
        (schema.status_range, schema.function)
        if ref.source == "status_range_first"
        else (schema.function, schema.status_range)
    )
    for code in ref.candidates:
        for src in order:
            entry = src.get(code)
            if entry is None:
                continue
            kind = normalize_type(entry.type)
            if kind not in ref.types:
                continue
            spec = entry.parse()
            if spec is None:
                continue
            if kind == INTEGER and schema.type_overrides.get(code) == "invert_int_max":
                spec = replace(spec, inverted=True)
            sr = schema.status_range.get(code)
            return ResolvedDp(code, spec, kind, sr.report_type if sr else None)
    return None
