"""User overrides, sans-IO: plain dicts in, no files read. The host loads and merges them and passes the mapping.

A mapping is ``{key: block}`` where `key` is a Tuya `product_id` or a device `id` (a device block wins over its
product's, and both win over the built-in quirks). A block:

    {
      "dp":     {"104": {"code": "percent_control", "type": "Integer", "values": {...}, "mode": "RW"}},
      "remove": ["cycle_time"],                        # dp codes to drop: no classified entity, no fallback property
      "category": "cl",                                # replace the Tuya category the tables are chosen by
      "props":  {"countdown_1": {"label": "Timer", "class": "duration", "category": "config", "unit": "s",
                                 "series": "gauge", "rw": false, "hide": false, "role": null},
                 "position": {"src": "percent_control", "role": "position", "unit": "%"},    # defined: see below
                 "open": {"src": "percent_control", "type": "trigger", "role": "open", "send": 100}},
      "device": {"kind": "cover", "class": "window", "label": "...", "vendor": "...", "model": "..."},
      "remap":  {"control": {"alias": {"on": "open", "off": "close", "pause": "stop"}},
                 "percent_state": {"invert": true}},
      "converters": {"cover_motion": {"settle": 5}},   # code converters by name (converters.py, or the host's)
      "expose_unused": true,                           # this device only: every dp no table claims gets a property
      "auto": false                                    # no property from the tables: only what `props` defines
    }

`dp`, `remove` and `category` patch the schema before classification, so a defined dp is classified like any other.
A `dp` entry with only a `code` renames a dp the device already has (its Tuya type, range and value strategy stay):
the usual fix for a dp the cloud gave a non-standard code. `remap` fixes the values of a dp (by its code, after any
rename) that uses other words or the other direction than the tables expect: `alias` maps the device's value to the
standard one (both ways; an Enum's range is translated too), `invert` negates a Boolean or mirrors an Integer in its
range.
`props` and `device` patch the finished descriptor; `props` keys are property names as `descriptor_of` shows them.
A `props` entry with a `src` (a dp code, after any rename) defines the property instead, replacing one of that name:
its `type` follows the dp's (Boolean binary, Integer number, Enum select, others text) unless given, `min`/`max`/`step`,
`unit` and `options` come from the dp unless given, and it is writable (`rw`) when the dp is. A `trigger` writes its
`send` value to the dp. `role`, `label`, `class`, `category` and `series` are as in a patch. With `device.kind` this
builds a composite the tables do not know, e.g. a cover from a position dp alone.
A null value clears a field. Unknown keys raise `OverrideError` rather than being ignored.

The package itself carries no block for any product or device: a fix for a model lives in a converters file (the
user's own, or the override pack copied there).
"""
from __future__ import annotations

import copy
import json

from .assemble import Binding
from .checks import integral
from .converters import BUILTIN as BUILTIN_CONVERTERS
from .tuya import ops
from .tuya.adapter import Remap
from .tuya.model import (
    BOOLEAN,
    ENUM,
    INTEGER,
    DeviceSchema,
    DpSpec,
    SchemaError,
    normalize_type,
)
from .tuya.quirks import apply_quirk
from .tuya.runtime import EntityPlan

_BLOCK = {"dp", "remove", "category", "props", "device", "converters", "remap", "expose_unused", "auto"}
_DP = {"code", "type", "values", "mode", "report_type"}
_REMAP = {"alias", "invert"}
_PATCH_FIELDS = ("label", "class", "category", "unit", "series", "role")     # copied as given; null clears
_PROP = {*_PATCH_FIELDS, "rw", "hide"}
_PROP_DEF = {"src", "type", "label", "class", "category", "unit", "series", "rw", "role", "min", "max", "step",
             "options", "send"}
_DEF_TYPES = {"binary", "number", "select", "text", "trigger"}
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


def validate(block: dict, where: str = "override", converter_types=None) -> dict:
    if not isinstance(block, dict):
        raise OverrideError(f"{where}: a block must be an object")
    _unknown(where, block, _BLOCK)
    for dpid, d in (block.get("dp") or {}).items():
        _unknown(f"{where}.dp.{dpid}", d, _DP)
        if not d.get("code"):
            raise OverrideError(f"{where}.dp.{dpid}: needs a `code`")
        if "type" not in d:
            if set(d) - {"code"}:
                raise OverrideError(f"{where}.dp.{dpid}: a rename (no `type`) takes only a `code`")
        elif normalize_type(d.get("type")) is None:
            raise OverrideError(f"{where}.dp.{dpid}: `type` is not a Tuya type")
        if d.get("mode", "RW") not in ("R", "W", "RW"):
            raise OverrideError(f"{where}.dp.{dpid}: mode must be R, W or RW")
    for k in ("expose_unused", "auto"):
        if not isinstance(block.get(k, False), bool):
            raise OverrideError(f"{where}.{k}: true or false")
    if not all(isinstance(c, str) for c in block.get("remove") or []):
        raise OverrideError(f"{where}.remove: a list of dp codes")
    for prop, p in (block.get("props") or {}).items():
        if not isinstance(p, dict):
            raise OverrideError(f"{where}.props.{prop}: must be an object")
        if p.get("role") is not None and not isinstance(p["role"], str):
            raise OverrideError(f"{where}.props.{prop}.role: a role name or null")
        if "src" in p:
            _unknown(f"{where}.props.{prop}", p, _PROP_DEF)
            if not isinstance(p["src"], str) or not p["src"]:
                raise OverrideError(f"{where}.props.{prop}.src: a dp code")
            if p.get("type", "binary") not in _DEF_TYPES:
                raise OverrideError(f"{where}.props.{prop}.type: one of {sorted(_DEF_TYPES)}")
            if (p.get("type") == "trigger") != ("send" in p):
                raise OverrideError(f"{where}.props.{prop}: a trigger needs `send`, and only a trigger takes it")
            if not isinstance(p.get("rw", False), bool):
                raise OverrideError(f"{where}.props.{prop}.rw: true or false")
            continue
        _unknown(f"{where}.props.{prop}", p, _PROP)
        if p.get("category") not in (None, "diagnostic", "config"):
            raise OverrideError(f"{where}.props.{prop}: category is diagnostic, config or null")
        if p.get("series") not in (None, "gauge", "counter"):
            raise OverrideError(f"{where}.props.{prop}: series is gauge, counter or null")
        if "rw" in p and p["rw"] is not False:
            raise OverrideError(f"{where}.props.{prop}: `rw` can only be false (make read only)")
    _unknown(f"{where}.device", block.get("device") or {}, _DEVICE)
    for code, r in (block.get("remap") or {}).items():
        if not isinstance(r, dict):
            raise OverrideError(f"{where}.remap.{code}: must be an object")
        _unknown(f"{where}.remap.{code}", r, _REMAP)
        alias = r.get("alias") or {}
        if not isinstance(alias, dict) or not all(isinstance(k, str) for k in alias):
            raise OverrideError(f"{where}.remap.{code}.alias: an object of device value -> standard value")
        if len({json.dumps(v, sort_keys=True) for v in alias.values()}) != len(alias):
            raise OverrideError(f"{where}.remap.{code}.alias: two device values map to the same standard value")
        if not isinstance(r.get("invert", False), bool):
            raise OverrideError(f"{where}.remap.{code}.invert: true or false")
    known = set(BUILTIN_CONVERTERS) | set(converter_types or ())
    for name, cfg in (block.get("converters") or {}).items():
        if name not in known:
            raise OverrideError(f"{where}.converters.{name}: unknown converter (known: {sorted(known)})")
        if not isinstance(cfg, dict):
            raise OverrideError(f"{where}.converters.{name}: the config must be an object")
    return block


def find(mapping: dict | None, device: dict, converter_types=None) -> dict:
    """The merged, validated block for a device: the block for its product, then the block for the device itself."""
    out: dict = {}
    for key in (device.get("product_id"), device.get("id")):
        if key and key in (mapping or {}):
            deep_merge(out, validate(copy.deepcopy(mapping[key]), f"override[{key}]", converter_types))
    return out


# --- schema layer ----------------------------------------------------------------------------------
def patch_schema(schema: DeviceSchema, block: dict, dpcodes: dict[str, str] | None = None
                 ) -> tuple[DeviceSchema, set[str]]:
    """Apply `dp`, `category`, `remove` and the Enum side of `remap`. `dpcodes`: the device's ``{dp id: code}`` before
    the patch (what a rename starts from). Returns the patched schema and the codes that were removed."""
    quirk_ops: list[dict] = []
    renamed: list[tuple[str, str]] = []
    if block.get("category"):
        quirk_ops.append({"op": "SetCategory", "category": block["category"]})
    for dpid, d in (block.get("dp") or {}).items():
        if "type" not in d:
            old = (dpcodes or {}).get(str(dpid))
            if old is None:
                raise OverrideError(f"dp.{dpid}: the device has no dp {dpid} to rename; give its `type` to define it")
            renamed.append((old, d["code"]))
            continue
        quirk_ops.append({"op": "DefineDp", "dpid": int(dpid) if str(dpid).isdigit() else dpid, "code": d["code"],
                    "type": d["type"], "mode": d.get("mode", "RW"), "values": d.get("values") or {},
                    "report_type": d.get("report_type")})
    aliased = {code: r["alias"] for code, r in (block.get("remap") or {}).items() if r.get("alias")}
    removed = set(block.get("remove") or [])
    if renamed or aliased or removed:
        schema = copy.deepcopy(schema)
    for old, new in renamed:
        for table in (schema.function, schema.status_range):
            if old in table:
                spec = table.pop(old)
                table[new] = DpSpec(new, spec.type, spec.values, spec.report_type)
        if old in schema.status:
            schema.status[new] = schema.status.pop(old)
        schema.dpmap = {k: (new if v == old else v) for k, v in schema.dpmap.items()}
    if quirk_ops:
        schema = apply_quirk(schema, {"ops": quirk_ops})
    for code, alias in aliased.items():
        for table in (schema.function, schema.status_range):
            spec = table.get(code)
            if spec is not None and normalize_type(spec.type) == ENUM and (v := spec.value_map()):
                v = {**v, "range": [alias.get(x, x) for x in v.get("range", [])]}
                table[code] = DpSpec(code, spec.type, v, spec.report_type)
        if isinstance(schema.status.get(code), str):
            schema.status[code] = alias.get(schema.status[code], schema.status[code])
    for code in removed:
        schema.function.pop(code, None)
        schema.status_range.pop(code, None)
        schema.status.pop(code, None)
    if removed:
        schema.dpmap = {k: v for k, v in schema.dpmap.items() if v not in removed}
    return schema, removed


def patch_adapter(adapter, block: dict, removed: set[str], schema: DeviceSchema | None = None) -> None:
    """Make the dp-id table follow the schema patch: a redefined dp id points at its new code (default strategy), a
    renamed one keeps its strategy; then attach the `remap` value fixes (`schema`: the patched one, for ranges; the
    adapter's Enum guard ranges come from it, so they are already in the standard words)."""
    for dpid, d in (block.get("dp") or {}).items():
        cur = adapter.entries.get(str(dpid))
        if "type" not in d and cur is not None:
            adapter.entries[str(dpid)] = (d["code"], cur[1], cur[2])
        elif cur is None or cur[0] != d["code"]:
            adapter.entries[str(dpid)] = (d["code"], "default", {})
    for dpid in [k for k, e in adapter.entries.items() if e[0] in removed]:
        del adapter.entries[dpid]
    for code, r in (block.get("remap") or {}).items():
        bounds = None
        spec = schema.spec(code) if schema else None
        if spec is not None and normalize_type(spec.type) == INTEGER:
            v = spec.value_map() or {}
            if "min" in v and "max" in v:
                bounds = (v["min"], v["max"])
        adapter.remaps[code] = Remap(dict(r.get("alias") or {}), bool(r.get("invert")), bounds)


# --- descriptor layer ------------------------------------------------------------------------------
def patch_descriptor(assembly, block: dict, schema: DeviceSchema | None = None) -> None:
    """Apply `props` and `device` to an `Assembly` in place (descriptor and bindings stay consistent). `schema` (the
    patched one) is needed for a property `props` defines from a dp."""
    desc, bindings = assembly.descriptor, assembly.bindings
    for prop, p in (block.get("props") or {}).items():
        if prop == "available":
            raise OverrideError("props.available cannot be overridden")
        if "src" in p:
            desc["props"][prop], bindings[prop] = _define(prop, p, schema)
            continue
        d = desc["props"].get(prop)
        if d is None:
            raise OverrideError(f"props.{prop}: the device has no such property "
                                f"(has {sorted(k for k in desc['props'] if k != 'available')})")
        if p.get("hide"):
            del desc["props"][prop]
            bindings.pop(prop, None)
            continue
        for field in _PATCH_FIELDS:
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
    if groups := desc.get("groups"):                            # a group left with no property is dropped
        used = {d.get("group") for d in desc["props"].values()}
        desc["groups"] = {g: v for g, v in groups.items() if g in used}
        if not desc["groups"]:
            del desc["groups"]


def _define(prop: str, p: dict, schema: DeviceSchema | None):
    """(definition, binding) of a property `props` defines from the dp `p["src"]`."""
    where, code = f"props.{prop}", p["src"]
    spec = schema.spec(code) if schema else None
    if spec is None:
        raise OverrideError(f"{where}.src: the device has no dp {code!r} (define it in `dp`)")
    kind = normalize_type(spec.type)
    try:
        parsed = spec.parse()
    except SchemaError as e:
        raise OverrideError(f"{where}.src: {e}") from e
    natural = {BOOLEAN: "binary", INTEGER: "number", ENUM: "select"}.get(kind, "text")
    t = p.get("type", natural)
    if t not in ("trigger", "text", natural):
        raise OverrideError(f"{where}.type: {t} does not fit a {kind} dp (use {natural}, text or trigger)")
    writable = code in schema.function

    def to_raw(v):
        if kind == INTEGER:
            return ops.validate_int_write(parsed, v)
        if kind == ENUM:
            return ops.validate_enum_write(parsed, v)
        return ops.validate_bool_write(v) if kind == BOOLEAN else v

    def read(codes, slot=None):
        raw = codes.get(code)
        if t == "binary":
            return ops.validate_bool_read(raw)
        if t == "number":
            v = ops.validate_int_read(parsed, raw)
            return None if v is None else integral(v)
        if t == "select":
            v = ops.validate_enum_read(parsed, raw)
            return v if v in d["options"] else None
        return raw if isinstance(raw, str) and raw else None if raw is None else str(raw)

    d: dict = {"type": t}
    if t == "number":
        for k, v in (("min", parsed.min), ("max", parsed.max), ("step", parsed.step)):
            d[k] = integral(p.get(k, ops.scale_value(parsed, v)))
        if unit := p.get("unit", parsed.unit):
            d["unit"] = unit
    elif t == "select":
        d["options"] = list(p.get("options") or parsed.range)
    rw = t not in ("trigger", "text") and p.get("rw", writable)
    if rw and not writable:
        raise OverrideError(f"{where}.rw: dp {code!r} is not writable (give it mode RW in `dp`)")
    if t == "trigger" and not writable:
        raise OverrideError(f"{where}: dp {code!r} is not writable, so it cannot be a trigger")
    if rw:
        d["rw"] = True
    for k in ("role", "label", "class", "category", "series"):
        if p.get(k) is not None:
            d[k] = p[k]
    d["src"] = code
    plan = EntityPlan("override", code, {}, {}, (code,))
    if t == "trigger":
        return d, Binding(prop, plan, write=lambda v, codes: [{"code": code, "value": to_raw(p["send"])}])
    return d, Binding(prop, plan, read=read, write=(lambda v, codes: [{"code": code, "value": to_raw(v)}]) if rw else None)
