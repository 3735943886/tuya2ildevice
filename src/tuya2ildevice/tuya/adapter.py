"""Bridge value adapter (spec section 10): raw LAN dps <-> core-style cloud values, driven by `local_strategy`.

The engine assumes core-style, already-converted values. rustuya-bridge emits RAW LAN dps keyed by numeric dp id, so this
adapter reproduces tuya_sharing's `Manager._on_device_report` (READ) and adds the inverse (WRITE) the SDK never had.
Sans-I/O; `read`/`write` are pure. Ported strategies: default, enum, dj_v2_{color,contr,music,scene}_alg.
Any other strategy is passed through unchanged and listed in `Adapter.unsupported` (host may make it read-only).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


def strategy_code(meta: Any) -> str | None:
    """The DPCode a strategy entry's value is stored under: the LAST key of `config_item.statusFormat`
    (SDK: json.loads(...).popitem()), NOT `status_code`. statusFormat is a dict (cloud cache) or a JSON string."""
    if not isinstance(meta, dict):
        return None
    fmt = (meta.get("config_item") or {}).get("statusFormat")
    if isinstance(fmt, str):
        try:
            fmt = json.loads(fmt)
        except ValueError:
            fmt = None
    if isinstance(fmt, dict) and fmt:
        return str(next(reversed(fmt)))
    return meta.get("status_code") or None


def _hex(s: str) -> int:
    s = (s or "").lstrip("0")
    return int(s, 16) if s else 0


def _default_value(ci: dict) -> Any:
    t = (ci.get("valueType") or "").capitalize()
    if t == "Boolean":
        return False
    if t == "Integer":
        return json.loads(ci["valueDesc"]).get("min")
    if t == "Enum":
        return json.loads(ci["valueDesc"]).get("range")[0]
    return ""


# --- read strategies: (raw, config_item) -> value ------------------------------------------
def _r_default(raw, ci):
    return raw if raw is not None and raw != "" else _default_value(ci)


def _r_enum(raw, ci):
    m = ci["enumMappingMap"]
    k = str(raw)
    for key in (k, k.lower()):
        if key in m and "value" in m[key]:
            return m[key]["value"]
    return _default_value(ci)


def _r_color(raw, ci):
    return None if raw is None else json.dumps({"h": _hex(raw[0:4]), "s": _hex(raw[4:8]), "v": _hex(raw[8:12])})


def _r_contr(raw, ci):
    if raw is None:
        return None
    if not raw:
        return ""
    flag = _hex(raw[:1])
    mode = "direct" if flag == 0 else "gradient" if flag == 1 else ""
    return json.dumps({"change_mode": mode, "h": _hex(raw[1:5]), "s": _hex(raw[5:9]), "v": _hex(raw[9:13]),
                       "bright": _hex(raw[13:17]), "temperature": _hex(raw[17:])})


_SCENE_MODES = {0: "static", 1: "jump", 2: "gradient"}


def _r_scene(raw, ci):
    if raw is None:
        return None
    units = []
    body = raw[2:]
    for i in range(len(body) // 26):
        it = body[i * 26:(i + 1) * 26]
        units.append({"unit_switch_duration": _hex(it[:2]), "unit_gradient_duration": _hex(it[2:4]),
                      "unit_change_mode": _SCENE_MODES.get(_hex(it[4:6]), ""), "h": _hex(it[6:10]), "s": _hex(it[10:14]),
                      "v": _hex(it[14:18]), "bright": _hex(it[18:22]), "temperature": _hex(it[22:])})
    return json.dumps({"scene_num": 1 + _hex(raw[:2]), "scene_units": units})


# --- write strategies: (value, config_item) -> raw ---------------------------------------
def _w_pass(v, ci):
    return v


def _w_enum(v, ci):
    """Invert enumMappingMap: prefer a numeric-string key, else the first key mapping to `v` (wire form LIVE-VERIFY)."""
    m = ci["enumMappingMap"]
    keys = [k for k, e in m.items() if isinstance(e, dict) and e.get("value") == v]
    if not keys:
        return v
    return next((k for k in keys if k.isdigit()), keys[0])


def _obj(v):
    return json.loads(v) if isinstance(v, str) else v


def _w_color(v, ci):
    o = _obj(v)
    return f"{o['h']:04x}{o['s']:04x}{o['v']:04x}"


def _w_contr(v, ci):
    o = _obj(v)
    flag = {"direct": 0, "gradient": 1}.get(o.get("change_mode"), 0)
    return f"{flag:x}{o['h']:04x}{o['s']:04x}{o['v']:04x}{o['bright']:04x}{o['temperature']:04x}"


def _w_scene(v, ci):
    o = _obj(v)
    rev = {"static": 0, "jump": 1, "gradient": 2}
    out = f"{o['scene_num'] - 1:02x}"
    for u in o["scene_units"]:
        out += (f"{u['unit_switch_duration']:02x}{u['unit_gradient_duration']:02x}{rev.get(u['unit_change_mode'], 0):02x}"
                f"{u['h']:04x}{u['s']:04x}{u['v']:04x}{u['bright']:04x}{u['temperature']:04x}")
    return out


STRATEGIES = {
    "default": (_r_default, _w_pass),
    "enum": (_r_enum, _w_enum),
    "dj_v2_color_alg": (_r_color, _w_color),
    "dj_v2_contr_alg": (_r_contr, _w_contr),
    "dj_v2_music_alg": (_r_contr, _w_contr),
    "dj_v2_scene_alg": (_r_scene, _w_scene),
}


@dataclass
class Remap:
    """A user's fix for a dp that speaks a different vocabulary than the tables expect (overrides `remap`), applied
    after the read strategy and before the write strategy. `alias`: device value -> standard value (a bijection;
    values not listed pass through). `invert`: a Boolean is negated, an Integer mirrored inside `[lo, hi]`."""
    alias: dict = field(default_factory=dict)
    invert: bool = False
    bounds: tuple[float, float] | None = None       # Integer range, raw units, for `invert`

    def _flip(self, v: Any) -> Any:
        if not self.invert:
            return v
        if isinstance(v, bool):
            return not v
        if isinstance(v, (int, float)) and self.bounds is not None:
            lo, hi = self.bounds
            out = lo + hi - v
            return int(out) if isinstance(v, int) and float(out).is_integer() else out
        return v

    def read(self, v: Any) -> Any:
        if isinstance(v, str) and v in self.alias:
            v = self.alias[v]
        return self._flip(v)

    def write(self, v: Any) -> Any:
        v = self._flip(v)
        return next((dev for dev, std in self.alias.items() if std == v), v)


@dataclass
class Adapter:
    """dp-id <-> code translation + value conversion for one device."""
    entries: dict[str, tuple[str, str, dict]] = field(default_factory=dict)   # dpid -> (code, strategy, config_item)
    enum_ranges: dict[str, list] = field(default_factory=dict)                # code -> range (Enum guard)
    unsupported: dict[str, str] = field(default_factory=dict)                 # dpid -> strategy name (passthrough)
    remaps: dict[str, Remap] = field(default_factory=dict)                  # code -> user value fix (overrides `remap`)

    @classmethod
    def from_local_strategy(cls, local_strategy: dict, status_range: dict[str, Any] | None = None) -> Adapter:
        a = cls()
        for dpid, meta in (local_strategy or {}).items():
            code = strategy_code(meta)
            if not code:
                continue
            name = meta.get("value_convert") or "default"
            ci = dict(meta.get("config_item") or {})
            if name not in STRATEGIES:
                a.unsupported[str(dpid)] = name
                name = "default"
            a.entries[str(dpid)] = (code, name, ci)
        for code, spec in (status_range or {}).items():
            if getattr(spec, "type", None) == "Enum":
                try:
                    v = spec.values if isinstance(spec.values, dict) else json.loads(spec.values)
                    a.enum_ranges[code] = list(v.get("range", []))
                except (ValueError, TypeError, AttributeError):
                    pass
        return a

    def codes(self) -> dict[str, str]:
        """{dp id: code}."""
        return {d: e[0] for d, e in self.entries.items()}

    def code_for_dp(self, dpid) -> str | None:
        e = self.entries.get(str(dpid))
        return e[0] if e else None

    def dp_for_code(self, code: str) -> str | None:
        return next((d for d, e in self.entries.items() if e[0] == code), None)

    def read(self, dps: dict[str, Any]) -> dict[str, Any]:
        """{dpid: raw} -> {code: value}. Unknown dp ids are skipped; an Enum value outside its range is DROPPED
        (Manager._on_device_report parity). A malformed raw value (strategy raises) is dropped too."""
        out: dict[str, Any] = {}
        for dpid, raw in dps.items():
            e = self.entries.get(str(dpid))
            if e is None:
                continue
            code, name, ci = e
            try:
                value = STRATEGIES[name][0](raw, ci)
            except (ValueError, TypeError, KeyError, IndexError):
                continue
            if code in self.remaps:
                value = self.remaps[code].read(value)
            rng = self.enum_ranges.get(code)
            if rng is not None and value not in rng:
                continue
            out[code] = value
        return out

    def write(self, commands: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
        """[{code, value}] -> ({dpid: raw}, unmapped codes)."""
        dps: dict[str, Any] = {}
        missing: list[str] = []
        for c in commands:
            dpid = self.dp_for_code(c["code"])
            if dpid is None:
                missing.append(c["code"])
                continue
            code, name, ci = self.entries[dpid]
            value = self.remaps[code].write(c["value"]) if code in self.remaps else c["value"]
            dps[dpid] = STRATEGIES[name][1](value, ci)
        return dps, missing
