"""il.md section 5: check a write against the descriptor. Pure; no Tuya knowledge."""
from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

_ON = {"on": True, "true": True, "1": True, "off": False, "false": False, "0": False}


class Rejected(Exception):
    def __init__(self, code: str, reason: str = ""):
        super().__init__(code, reason)
        self.code, self.reason = code, reason


def _number(v: Any) -> float | int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise Rejected("invalid_value", f"not a finite number: {v!r}")
    return int(v) if float(v).is_integer() else v      # C-2 / V-5


def _requires_ok(req: Any, state: dict[str, Any]) -> bool:
    if isinstance(req, str):
        return state.get(req) is True
    return state.get(req["prop"]) in req["in"]


def check_command(desc: dict, state: dict[str, Any], prop: str, value: Any) -> Any:
    """Return the canonical value (None for a trigger) or raise `Rejected` with the il.md step's code.

    `state` maps property name -> the value the producer currently holds (absent = not reported)."""
    p = desc["props"].get(prop)
    if p is None:
        raise Rejected("unknown_property", prop)
    t = p["type"]
    if t == "event" or not (p.get("rw") or t == "trigger"):
        raise Rejected("read_only", prop)
    if "requires" in p and not _requires_ok(p["requires"], state):
        raise Rejected("requires_unmet", f"{prop} requires {p['requires']}")
    if t == "trigger":
        return None
    if t == "binary":
        if isinstance(value, bool):
            return value
        key = str(value).lower() if isinstance(value, (str, int)) and not isinstance(value, float) else None
        if key not in _ON:
            raise Rejected("invalid_value", f"not a boolean: {value!r}")
        return _ON[key]
    if t == "number":
        v = _number(value)
        lo, hi, step = p.get("min"), p.get("max"), p.get("step")
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            raise Rejected("out_of_range", f"{lo}..{hi}")
        if step:
            base = Decimal(str(lo if lo is not None else 0))
            q = (Decimal(str(v)) - base) / Decimal(str(step))
            if abs(q - q.to_integral_value()) * Decimal(str(step)) > Decimal("1e-9"):
                raise Rejected("bad_step", f"step {step} from {base}")
        return v
    if t == "select":
        if value not in p["options"]:
            raise Rejected("invalid_value", f"not one of {p['options']}")
        return value
    if t == "text":
        if not isinstance(value, str) or value == "":
            raise Rejected("invalid_value", "non-empty string required")
        return value
    raise Rejected("unsupported", f"type {t}")
