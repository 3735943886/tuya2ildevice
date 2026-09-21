"""User overrides, sans-IO: plain dicts in, no files read. The host loads and merges them and passes the mapping.

A mapping is ``{key: block}`` where `key` is a Tuya `product_id` or a device `id` (a device block wins over its
product's, and both win over the built-in quirks). A block:

    {
      "dp":     {"104": {"code": "percent_control", "type": "Integer", "values": {...}, "mode": "RW"}},
      "remove": ["cycle_time"],                        # dp codes to drop: no classified entity, no fallback property
      "category": "cl",                                # replace the Tuya category the tables are chosen by
      "props":  {"countdown_1": {"label": "Timer", "class": "duration", "category": "config", "unit": "s",
                                 "series": "gauge", "rw": false, "hide": false}},
      "device": {"kind": "cover", "class": "window", "label": "...", "vendor": "...", "model": "..."},
      "converters": {"cover_motion": {"settle": 5}}    # built-in code converters (converters.py)
    }

`dp`, `remove` and `category` patch the schema before classification, so a defined dp is classified like any other.
`props` and `device` patch the finished descriptor; `props` keys are property names as `descriptor_of` shows them.
A null value clears a field. Unknown keys raise `OverrideError` rather than being ignored.
"""
from __future__ import annotations

import copy
from typing import Any

from .tuya.model import DeviceSchema, normalize_type
from .tuya.quirks import apply_quirk

_BLOCK = {"dp", "remove", "category", "props", "device", "converters"}
_DP = {"code", "type", "values", "mode", "report_type"}
_PROP = {"label", "class", "category", "unit", "series", "rw", "hide"}
_DEVICE = {"kind", "class", "label", "vendor", "model"}
_RW_ROLES = {"on", "mode", "fan_speed", "target_humidity", "target_temperature", "swing_vertical",
             "swing_horizontal", "brightness", "color_temperature", "color"}    # il.md section 9: never read only


class OverrideError(ValueError):
    pass


def deep_merge(dst: dict, src: dict) -> dict:
    """Recursively merge `src` into `dst` (a later leaf wins; nested dicts combine). Mutates and returns `dst`."""
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)
    return dst


def merge_all(mappings) -> dict:
    """Merge several override mappings (e.g. the files of a directory, in filename order)."""
    out: dict = {}
    for m in mappings:
        deep_merge(out, m)
    return out


def _unknown(where: str, got: dict, allowed: set) -> None:
    if extra := set(got) - allowed:
        raise OverrideError(f"{where}: unknown key(s) {sorted(extra)}; allowed {sorted(allowed)}")


def validate(block: dict, where: str = "override") -> dict:
    if not isinstance(block, dict):
        raise OverrideError(f"{where}: a block must be an object")
    _unknown(where, block, _BLOCK)
    for dpid, d in (block.get("dp") or {}).items():
        _unknown(f"{where}.dp.{dpid}", d, _DP)
        if not d.get("code") or normalize_type(d.get("type")) is None:
            raise OverrideError(f"{where}.dp.{dpid}: needs a `code` and a valid Tuya `type`")
        if d.get("mode", "RW") not in ("R", "W", "RW"):
            raise OverrideError(f"{where}.dp.{dpid}: mode must be R, W or RW")
    if not all(isinstance(c, str) for c in block.get("remove") or []):
        raise OverrideError(f"{where}.remove: a list of dp codes")
    for prop, p in (block.get("props") or {}).items():
        _unknown(f"{where}.props.{prop}", p, _PROP)
        if p.get("category") not in (None, "diagnostic", "config"):
            raise OverrideError(f"{where}.props.{prop}: category is diagnostic, config or null")
        if p.get("series") not in (None, "gauge", "counter"):
            raise OverrideError(f"{where}.props.{prop}: series is gauge, counter or null")
        if "rw" in p and p["rw"] is not False:
            raise OverrideError(f"{where}.props.{prop}: `rw` can only be false (make read only)")
    _unknown(f"{where}.device", block.get("device") or {}, _DEVICE)
    from .converters import BUILTIN
    for name, cfg in (block.get("converters") or {}).items():
        if name not in BUILTIN:
            raise OverrideError(f"{where}.converters.{name}: unknown converter (built in: {sorted(BUILTIN)})")
        if not isinstance(cfg, dict):
            raise OverrideError(f"{where}.converters.{name}: the config must be an object")
    return block


def find(mapping: dict | None, device: dict) -> dict:
    """The merged, validated block for a device: its product's block, then its own."""
    out: dict = {}
    for key in (device.get("product_id"), device.get("id")):
        if key and key in (mapping or {}):
            deep_merge(out, validate(mapping[key], f"override[{key}]"))
    return out


# --- schema layer ----------------------------------------------------------------------------------
def patch_schema(schema: DeviceSchema, block: dict) -> tuple[DeviceSchema, set[str]]:
    """Apply `dp`, `category` and `remove`. Returns the patched schema and the codes that were removed."""
    ops: list[dict] = []
    if block.get("category"):
        ops.append({"op": "SetCategory", "category": block["category"]})
    for dpid, d in (block.get("dp") or {}).items():
        ops.append({"op": "DefineDp", "dpid": int(dpid) if str(dpid).isdigit() else dpid, "code": d["code"],
                    "type": d["type"], "mode": d.get("mode", "RW"), "values": d.get("values") or {},
                    "report_type": d.get("report_type")})
    if ops:
        schema = apply_quirk(schema, {"ops": ops})
    removed = set(block.get("remove") or [])
    if removed:
        schema = copy.deepcopy(schema)
        for code in removed:
            schema.function.pop(code, None)
            schema.status_range.pop(code, None)
            schema.status.pop(code, None)
        schema.dpmap = {k: v for k, v in schema.dpmap.items() if v not in removed}
    return schema, removed


def patch_adapter(adapter, block: dict, removed: set[str]) -> None:
    """Make the dp-id table follow the schema patch: a redefined dp id points at its new code (default strategy)."""
    for dpid, d in (block.get("dp") or {}).items():
        cur = adapter.entries.get(str(dpid))
        if cur is None or cur[0] != d["code"]:
            adapter.entries[str(dpid)] = (d["code"], "default", {})
    for dpid in [k for k, e in adapter.entries.items() if e[0] in removed]:
        del adapter.entries[dpid]


# --- descriptor layer ------------------------------------------------------------------------------
def patch_descriptor(assembly, block: dict) -> None:
    """Apply `props` and `device` to an `Assembly` in place (descriptor and bindings stay consistent)."""
    desc, bindings = assembly.descriptor, assembly.bindings
    for prop, p in (block.get("props") or {}).items():
        d = desc["props"].get(prop)
        if d is None:
            raise OverrideError(f"props.{prop}: the device has no such property "
                                f"(has {sorted(k for k in desc['props'] if k != 'available')})")
        if prop == "available":
            raise OverrideError("props.available cannot be overridden")
        if p.get("hide"):
            del desc["props"][prop]
            bindings.pop(prop, None)
            continue
        for field in ("label", "class", "category", "unit", "series"):
            if field in p:
                if p[field] is None:
                    d.pop(field, None)
                elif field in ("unit", "series") and d["type"] != "number":
                    raise OverrideError(f"props.{prop}: `{field}` only applies to a number")
                else:
                    d[field] = p[field]
        if p.get("rw") is False:
            if d["type"] in ("trigger", "event"):
                raise OverrideError(f"props.{prop}: a {d['type']} cannot be made read only (use hide)")
            d.pop("rw", None)
            if d.get("role") in _RW_ROLES:
                del d["role"]                                   # O-6 style: a role that must be writable is dropped
            if prop in bindings:
                bindings[prop].write = None
    for field, v in (block.get("device") or {}).items():
        if v is None:
            desc.pop(field, None)
        else:
            desc[field] = v
    groups = desc.get("groups")
    if groups:                                                  # a group left with no property is dropped
        used = {d.get("group") for d in desc["props"].values()}
        for g in [g for g in groups if g not in used]:
            del groups[g]
        if not groups:
            del desc["groups"]


# --- migration -------------------------------------------------------------------------------------
def from_v1(mapping: dict) -> tuple[dict, list[str]]:
    """Convert rustuya-homeassistant custom_converters (`dp_meta`, `model`) to this format.

    `discovery_overrides` patch Home Assistant MQTT payload fields and have no counterpart here; they are dropped and
    reported in the returned warnings, as is anything else that is not understood."""
    out: dict = {}
    warn: list[str] = []
    for key, v in (mapping or {}).items():
        block: dict = {}
        if v.get("model"):
            block["device"] = {"model": v["model"]}
        for dpid, m in (v.get("dp_meta") or {}).items():
            vtype = m.get("type", "Integer")
            values = {k: m[k] for k in ("unit", "min", "max", "step", "scale") if k in m}
            if vtype == "Integer":
                values = {"unit": "", "min": 0, "max": 100, "scale": 0, "step": 1, **values}
            elif m.get("options") or m.get("range"):
                values = {"range": m.get("options") or m.get("range")}
            d = {"code": m.get("code", str(dpid)), "type": vtype, "values": values, "mode": "RW"}
            if m.get("active"):
                d["report_type"] = "sum"
            block.setdefault("dp", {})[str(dpid)] = d
            for k in set(m) - {"code", "type", "unit", "min", "max", "step", "scale", "active", "options", "range"}:
                warn.append(f"{key}.dp_meta.{dpid}.{k}: dropped")
        for k in set(v) - {"model", "dp_meta"}:
            warn.append(f"{key}.{k}: dropped" + (" (Home Assistant MQTT payload fields)" if k == "discovery_overrides" else ""))
        if block:
            out[key] = block
    return out, warn
