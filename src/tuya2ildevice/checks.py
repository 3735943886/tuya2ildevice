"""il.md section 5: check a write against the descriptor. Pure; no Tuya knowledge."""
from __future__ import annotations

import math
from typing import Any

_ON = {"on": True, "true": True, "1": True, "off": False, "false": False, "0": False}


class Rejected(Exception):
    def __init__(self, code: str, reason: str = ""):
        super().__init__(code, reason)
        self.code, self.reason = code, reason


def integral(v: float) -> float | int:
    """An integral number as an int (C-2 / V-5): `20.0` is `20`."""
    return int(v) if float(v).is_integer() else v


def _number(v: Any) -> float | int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise Rejected("invalid_value", f"not a finite number: {v!r}")
    return integral(v)


def _requires_ok(req: Any, state: dict[str, Any]) -> bool:
    if isinstance(req, str):
        return state.get(req) is True
    return state.get(req["prop"]) in req["in"]


def check_command(desc: dict, state: dict, prop: str, value):
    """The command-check contract runs in the shared Rust engine."""
    from .native import call
    if isinstance(value, float) and not math.isfinite(value):
        value = None  # JSON has no non-finite numbers; both representations are invalid writes.
    result = call("check", descriptor=desc, state=state, prop=prop, value=value)
    if "reject" in result:
        raise Rejected(result["reject"], result["reason"])
    return result["accept"]
