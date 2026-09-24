"""Quirks = schema patches (spec 1.3). Pure functions; data in quirks/quirks.json (generated, L3)."""
from __future__ import annotations

import copy
import json
import pathlib
from typing import Any

from .model import DeviceSchema, DpSpec

_QUIRKS: dict[str, dict] | None = None


def load_quirks() -> dict[str, dict]:
    global _QUIRKS
    if _QUIRKS is None:
        _QUIRKS = json.loads((pathlib.Path(__file__).parent / "quirks" / "quirks.json").read_text())["quirks"]
    return _QUIRKS


def quirk_for(product_id: str) -> dict | None:
    """ONE quirk per product_id, exact and case-sensitive (registry.get_quirk_for_device)."""
    return load_quirks().get(product_id)


def _ev(c: Any, status: dict[str, Any]) -> Any:
    """Cond/Expr subset legal in a quirk `when` (spec 3): status, is_int, ge, and/or/not, lit."""
    if isinstance(c, dict):
        (k, v), = c.items()
        if k == "status":
            return status.get(v)
        if k == "is_int":
            x = _ev(v, status)
            return isinstance(x, int)
        if k == "ge":
            a, b = (_ev(x, status) for x in v)
            return isinstance(a, (int, float)) and isinstance(b, (int, float)) and a >= b
        if k == "and":
            return all(_ev(x, status) for x in v)
        if k == "or":
            return any(_ev(x, status) for x in v)
        if k == "not":
            return not _ev(v, status)
        raise ValueError(f"unknown when op {k}")
    return c


def apply_quirk(schema: DeviceSchema, quirk: dict | None = None) -> DeviceSchema:
    """Ops in order, each guarded by its own `when` (evaluated against status BEFORE any op mutates it)."""
    quirk = quirk if quirk is not None else quirk_for(schema.product_id)
    if not quirk:
        return schema
    s = copy.deepcopy(schema)
    for op in quirk["ops"]:
        if "when" in op and not _ev(op["when"], s.status):
            continue
        kind, code = op["op"], op.get("code")
        if kind == "SetCategory":
            s.category = op["category"]
        elif kind == "DefineDp":
            if "R" in op["mode"]:
                s.status_range[code] = DpSpec(code, op["type"], op["values"], op["report_type"])
            else:
                s.status_range.pop(code, None)
            if "W" in op["mode"]:
                s.function[code] = DpSpec(code, op["type"], op["values"])
            else:
                s.function.pop(code, None)
            s.dpmap[op["dpid"]] = code
        elif kind == "RemoveDp":
            s.function.pop(code, None)
            s.status_range.pop(code, None)
            s.status.pop(code, None)
            s.dpmap.pop(op["dpid"], None)
        elif kind == "TypeOverride":
            s.type_overrides[code] = op["as"]       # matched by dpcode only, dpid ignored (core parity)
        # MapInitialStatus: apply_status_quirk; LocalConvert: adapter hint (not interpreted here)
    return s


def apply_status_quirk(quirk: dict | None, status: dict[str, Any]) -> dict[str, Any]:
    """Host calls this on the INITIAL status only (core applies it once at init)."""
    out = dict(status)
    for op in (quirk or {}).get("ops", []):
        if op["op"] != "MapInitialStatus":
            continue
        mapping = {k: v for k, v in op["mapping"]}
        raw = out.get(op["code"])
        try:
            if raw in mapping:                 # dict membership: hash equality, as core
                out[op["code"]] = mapping[raw]
        except TypeError:                      # unhashable raw value
            pass
    return out


def device_info(schema: DeviceSchema, quirk: dict | None = None) -> dict[str, Any]:
    """util.py:65-87: a quirk with a truthy manufacturer supplies ALL THREE fields (P-11)."""
    quirk = quirk if quirk is not None else quirk_for(schema.product_id)
    meta = (quirk or {}).get("meta") or {}
    if meta.get("manufacturer"):
        return {"manufacturer": meta["manufacturer"], "model": meta.get("model"), "model_id": meta.get("model_id")}
    return {"manufacturer": "Tuya", "model": schema.product_name, "model_id": schema.product_id}
