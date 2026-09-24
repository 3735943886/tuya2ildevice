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
      "remap":  {"control": {"alias": {"on": "open", "off": "close", "pause": "stop"}},
                 "percent_state": {"invert": true}},
      "converters": {"cover_motion": {"settle": 5}},   # code converters by name (converters.py, or the host's)
      "expose_unused": true                            # this device only: every dp no table claims gets a property
    }

`dp`, `remove` and `category` patch the schema before classification, so a defined dp is classified like any other.
A `dp` entry with only a `code` renames a dp the device already has (its Tuya type, range and value strategy stay):
the usual fix for a dp the cloud gave a non-standard code. `remap` fixes the values of a dp (by its code, after any
rename) that uses other words or the other direction than the tables expect: `alias` maps the device's value to the
standard one (both ways; an Enum's range is translated too), `invert` negates a Boolean or mirrors an Integer in its
range.
`props` and `device` patch the finished descriptor; `props` keys are property names as `descriptor_of` shows them.
A null value clears a field. Unknown keys raise `OverrideError` rather than being ignored.

`BUILTIN` (``overrides.json`` next to this module) is the curated set shipped with the package, for devices whose
cloud schema is known to be wrong; `find` puts it under the user's mapping (a user block wins, key by key).
"""
from __future__ import annotations

import copy
import json
from importlib import resources

from .tuya.adapter import Remap
from .tuya.model import DeviceSchema, DpSpec, normalize_type
from .tuya.quirks import apply_quirk

_BLOCK = {"dp", "remove", "category", "props", "device", "converters", "remap", "expose_unused"}
_DP = {"code", "type", "values", "mode", "report_type"}
_REMAP = {"alias", "invert"}
_PROP = {"label", "class", "category", "unit", "series", "rw", "hide"}
_DEVICE = {"kind", "class", "label", "vendor", "model"}
_RW_ROLES = {"on", "mode", "fan_speed", "target_humidity", "target_temperature", "swing_vertical",
             "swing_horizontal", "brightness", "color_temperature", "color"}    # il.md section 9: never read only


class OverrideError(ValueError):
    pass


def _load_builtin() -> dict:
    try:
        return json.loads(resources.files(__package__).joinpath("overrides.json").read_text("utf-8"))
    except FileNotFoundError:
        return {}


BUILTIN: dict = _load_builtin()


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
    if not isinstance(block.get("expose_unused", False), bool):
        raise OverrideError(f"{where}.expose_unused: true or false")
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
    from .converters import BUILTIN as BUILTIN_CONVERTERS
    known = set(BUILTIN_CONVERTERS) | set(converter_types or ())
    for name, cfg in (block.get("converters") or {}).items():
        if name not in known:
            raise OverrideError(f"{where}.converters.{name}: unknown converter (known: {sorted(known)})")
        if not isinstance(cfg, dict):
            raise OverrideError(f"{where}.converters.{name}: the config must be an object")
    return block


def find(mapping: dict | None, device: dict, converter_types=None, builtin: bool = True) -> dict:
    """The merged, validated block for a device: the built-in block for its product (unless `builtin` is off), then
    the user's block for its product, then the user's block for the device itself."""
    out: dict = {}
    layers = ([(BUILTIN, "builtin")] if builtin else []) + [(mapping or {}, "override")]
    for table, name in layers:
        for key in (device.get("product_id"), device.get("id")):
            if key and key in table:
                deep_merge(out, validate(copy.deepcopy(table[key]), f"{name}[{key}]", converter_types))
    return out


# --- schema layer ----------------------------------------------------------------------------------
def patch_schema(schema: DeviceSchema, block: dict, dpcodes: dict[str, str] | None = None
                 ) -> tuple[DeviceSchema, set[str]]:
    """Apply `dp`, `category`, `remove` and the Enum side of `remap`. `dpcodes`: the device's ``{dp id: code}`` before
    the patch (what a rename starts from). Returns the patched schema and the codes that were removed."""
    ops: list[dict] = []
    renamed: list[tuple[str, str]] = []
    if block.get("category"):
        ops.append({"op": "SetCategory", "category": block["category"]})
    for dpid, d in (block.get("dp") or {}).items():
        if "type" not in d:
            old = (dpcodes or {}).get(str(dpid))
            if old is None:
                raise OverrideError(f"dp.{dpid}: the device has no dp {dpid} to rename; give its `type` to define it")
            renamed.append((old, d["code"]))
            continue
        ops.append({"op": "DefineDp", "dpid": int(dpid) if str(dpid).isdigit() else dpid, "code": d["code"],
                    "type": d["type"], "mode": d.get("mode", "RW"), "values": d.get("values") or {},
                    "report_type": d.get("report_type")})
    if renamed:
        schema = copy.deepcopy(schema)
        for old, new in renamed:
            for table in (schema.function, schema.status_range):
                if old in table:
                    spec = table.pop(old)
                    table[new] = DpSpec(new, spec.type, spec.values, spec.report_type)
            if old in schema.status:
                schema.status[new] = schema.status.pop(old)
            schema.dpmap = {k: (new if v == old else v) for k, v in schema.dpmap.items()}
    if ops:
        schema = apply_quirk(schema, {"ops": ops})
    aliased = {code: r["alias"] for code, r in (block.get("remap") or {}).items() if r.get("alias")}
    if aliased:
        schema = copy.deepcopy(schema)
        for code, alias in aliased.items():
            for table in (schema.function, schema.status_range):
                spec = table.get(code)
                if spec is not None and normalize_type(spec.type) == "Enum" and spec._values():
                    v = dict(spec._values())
                    v["range"] = [alias.get(x, x) for x in v.get("range", [])]
                    table[code] = DpSpec(code, spec.type, v, spec.report_type)
            if isinstance(schema.status.get(code), str):
                schema.status[code] = alias.get(schema.status[code], schema.status[code])
    removed = set(block.get("remove") or [])
    if removed:
        schema = copy.deepcopy(schema)
        for code in removed:
            schema.function.pop(code, None)
            schema.status_range.pop(code, None)
            schema.status.pop(code, None)
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
        alias = dict(r.get("alias") or {})
        bounds = None
        spec = schema and (schema.status_range.get(code) or schema.function.get(code))
        if spec is not None and normalize_type(spec.type) == "Integer":
            v = spec._values() or {}
            if "min" in v and "max" in v:
                bounds = (v["min"], v["max"])
        adapter.remaps[code] = Remap(alias, bool(r.get("invert")), bounds)


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
_V1_LAYOUT = {"command_dp": ("1", "control"), "set_position_dp": ("2", "percent_control"),
              "position_dp": ("3", "percent_state")}       # the common Tuya curtain layout v1 assumed as well
_V1_WORDS = {"payload_open": "open", "payload_close": "close", "payload_stop": "stop"}


def _v1_cover(key: str, c: dict, block: dict, warn: list[str]) -> None:
    """`discovery_overrides.cover` -> dp renames / `remove` / `remap` / `cover_motion`, assuming the standard layout
    (control 1, percent_control 2, percent_state 3) wherever the v1 block did not name a dp."""
    dp_of = {k: str(c[k]) if c.get(k) is not None else d for k, (d, _) in _V1_LAYOUT.items()}
    for k, (default, code) in _V1_LAYOUT.items():
        if k == "position_dp" or k not in c or c[k] is None or str(c[k]) == default:
            continue
        block.setdefault("dp", {}).setdefault(str(c[k]), {"code": code})
    pos_code = "percent_state"
    if "position_dp" in c:
        if c["position_dp"] is None or dp_of["position_dp"] == dp_of["set_position_dp"]:
            block.setdefault("remove", []).append("percent_state")      # position read back from the target
            pos_code = "percent_control"
            if c["position_dp"] is None:
                warn.append(f"{key}.discovery_overrides.cover.position_dp: null -> percent_state removed")
        elif dp_of["position_dp"] != "3":
            block.setdefault("dp", {}).setdefault(dp_of["position_dp"], {"code": "percent_state"})
    alias = {str(c[k]): std for k, std in _V1_WORDS.items() if k in c and str(c[k]) != std}
    if alias:
        block.setdefault("remap", {}).setdefault("control", {})["alias"] = alias
    if c.get("invert_position"):
        block.setdefault("remap", {}).setdefault(pos_code, {})["invert"] = True
    if c.get("invert_set_position") and pos_code != "percent_control":
        block.setdefault("remap", {}).setdefault("percent_control", {})["invert"] = True
    states = {std: str(c[f"state_{w}"]) for std, w in (("open", "opening"), ("close", "closing"), ("stop", "stopped"))
              if f"state_{w}" in c}
    if c.get("state_stream") == "derived" or (states and c.get("state_dp") is None):
        # v1 derived the motion in 00_curtain.py, or read it straight off the control dp's words: both are cover_motion
        words = {std: alias.get(w, w) for std, w in states.items() if alias.get(w, w) != std}
        block.setdefault("converters", {})["cover_motion"] = {"words": words} if words else {}
    done = (set(_V1_LAYOUT) | set(_V1_WORDS) | {"invert_position", "invert_set_position", "state_stream", "state_dp"}
            | {"state_opening", "state_closing", "state_stopped"})
    if c.get("state_dp") is not None and c.get("state_stream") != "derived":
        warn.append(f"{key}.discovery_overrides.cover.state_dp: dropped (a state dp of its own is not supported)")
    for k in sorted(set(c) - done):
        warn.append(f"{key}.discovery_overrides.cover.{k}: dropped (Home Assistant MQTT payload field)")


def is_v1(mapping: dict) -> bool:
    """A rustuya-homeassistant custom_converters mapping (by its keys), as opposed to this module's format."""
    return any(isinstance(v, dict) and ({"dp_meta", "discovery_overrides", "model"} & set(v))
               for v in (mapping or {}).values())


def from_v1(mapping: dict) -> tuple[dict, list[str]]:
    """Convert rustuya-homeassistant custom_converters (`dp_meta`, `model`, `discovery_overrides.cover`) to this format.

    Other `discovery_overrides` patch Home Assistant MQTT payload fields and have no counterpart here; they are dropped
    and reported in the returned warnings, as is anything else that is not understood."""
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
            block["expose_unused"] = True          # v1 gave every dp an entity; a dp defined here was meant to show
            for k in set(m) - {"code", "type", "unit", "min", "max", "step", "scale", "active", "options", "range"}:
                warn.append(f"{key}.dp_meta.{dpid}.{k}: dropped")
        disc = v.get("discovery_overrides") or {}
        if isinstance(disc.get("cover"), dict):
            _v1_cover(key, disc["cover"], block, warn)
        for comp in sorted(set(disc) - {"cover"}):
            warn.append(f"{key}.discovery_overrides.{comp}: dropped (Home Assistant MQTT payload fields)")
        for k in set(v) - {"model", "dp_meta", "discovery_overrides"}:
            warn.append(f"{key}.{k}: dropped")
        if block:
            out[key] = block
    return out, warn
