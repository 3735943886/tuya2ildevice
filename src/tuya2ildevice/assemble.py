"""Assemble an ildevice descriptor and its property bindings from Tuya engine entity plans.

An `EntityPlan` is what Home Assistant core's tuya integration would create (platform, key, identity, read, write).
Each plan becomes one or more IL properties; a `Binding` says how to read the property's value from the device's
dp-code state and how to turn a written value into `[{code, value}]` commands. Composites (light, cover, fan, siren,
valve, humidifier, climate) get IL roles; the first one is the device's `kind`, later ones become kinded groups (il.md section 10).

Not assembled (listed in `Assembly.unsupported`): camera (a stream is outside the IL).
"""
from __future__ import annotations

import colorsys
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .checks import integral
from .tuya import ops
from .tuya.platforms import COVER_FEATURES, VAC_FEATURES
from .tuya.runtime import EntityPlan, StateSlot, WriteRejected

IL_VERSION = 0
UNSUPPORTED = {"camera"}
KINDS = {"alarm_control_panel": "alarm"}                          # engine platform -> IL kind, where they differ
HAZARDOUS_COVERS = {"garage", "gate", "door", "damper"}          # il.md S-1: motion of a hazardous device
BUTTON_CLASSES = {"restart", "identify", "update"}
CATEGORIES = ("config", "diagnostic")
_HEX_COLOUR = re.compile(r"#[0-9a-fA-F]{6}")
# (property, IL role, engine action) of a vacuum's triggers, each offered when the plan's features have it
VACUUM_ACTIONS = (("start", "start", "start"), ("pause", "pause", "pause"),
                  ("return_home", "return_home", "return_to_base"), ("locate", "locate", "locate"),
                  ("stop", None, "stop"))


@dataclass
class Binding:
    prop: str
    plan: EntityPlan
    read: Callable[[dict, StateSlot | None], Any] | None = None      # (codes, slot) -> IL value | None
    write: Callable[[Any, dict], list[dict]] | None = None           # (canonical value, codes) -> commands
    event: bool = False
    slot: StateSlot | None = None


@dataclass
class CoverPart:
    """A cover of the device: its plan, the prefix of its property names ("" for the device's own kind, else its
    group's name and "_"), its group (None for the device's own kind) and whether its motion is withheld (S-1)."""
    plan: EntityPlan
    prefix: str
    group: str | None
    hazardous: bool


@dataclass
class Assembly:
    descriptor: dict
    bindings: dict[str, Binding]
    unsupported: list[str] = field(default_factory=list)
    covers: list[CoverPart] = field(default_factory=list)


def _unit(ident: dict) -> str | None:
    # Bindings publish native_value; a suggested display unit does not convert its magnitude.
    u = ident.get("native_unit")
    return u.replace("µ", "μ") if u else None          # U-2


def _percent(lo: float = 0, hi: float = 100, **fields: Any) -> dict:
    """A number property in % (`fields`, e.g. rw and role, come before the unit and the range)."""
    return {"type": "number", **fields, "unit": "%", "min": lo, "max": hi, "step": 1}


def _category(ident: dict) -> dict:
    return {"category": ident["entity_category"]} if ident.get("entity_category") in CATEGORIES else {}


def _common(ident: dict) -> dict:
    out: dict[str, Any] = {}
    if ident.get("label"):
        out["label"] = ident["label"]
    if ident.get("device_class"):
        out["class"] = ident["device_class"]
    return {**out, **_category(ident)}


def _view(plan: EntityPlan, key: str, conv: Callable[[Any], Any] = lambda x: x):
    def read(st, slot):
        v = (plan.read(st, slot) if plan.slot_kind == "delta" else plan.read(st))[key]
        return None if v is None else conv(v)
    return read


def _call(plan: EntityPlan, action: str, arg: str | None = None):
    """A write that runs the plan's `action`, with the written value as its argument `arg` (none: a trigger)."""
    return lambda v, st: plan.write(action, {arg: v} if arg else {}, st)


def _switch(plan: EntityPlan, on: str = "turn_on", off: str = "turn_off"):
    """A binary write: `on` for true, `off` for false."""
    return lambda v, st: plan.write(on if v else off, {}, st)


def _to_hex(hs) -> str:
    # a device can report beyond its scale (colour_data `s: 1000` read on the 0..255 scale): clamp, or the rgb goes negative
    r, g, b = colorsys.hsv_to_rgb(hs[0] % 360 / 360, min(100, max(0, hs[1])) / 100, 1)
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


def _from_hex(s: str) -> tuple[float, float]:
    if not _HEX_COLOUR.fullmatch(s):
        raise WriteRejected(f"not a #rrggbb colour: {s!r}")
    h, sat, _ = colorsys.rgb_to_hsv(*(int(s[i:i + 2], 16) / 255 for i in (1, 3, 5)))
    return h * 360, sat * 100


class _Builder:
    def __init__(self, allow_hazardous: bool):
        self.props: dict[str, dict] = {}
        self.bindings: dict[str, Binding] = {}
        self.groups: dict[str, dict] = {}
        self.kind: str | None = None
        self.klass: str | None = None
        self.unsupported: list[str] = []
        self.covers: list[CoverPart] = []
        self.allow_hazardous = allow_hazardous

    def name(self, base: str) -> str:
        base = re.sub(r"[^a-z0-9_]", "_", base.lower()) or "prop"
        n, i = base, 2
        while n in self.props or n == "available":
            n, i = f"{base}_{i}", i + 1
        return n

    def add(self, base: str, definition: dict, plan: EntityPlan, **kw) -> str:
        name = self.name(base)
        definition.setdefault("src", plan.depends_on[0] if plan.depends_on else plan.key)
        self.props[name] = definition
        self.bindings[name] = Binding(name, plan, **kw)
        return name

    def composite(self, plan: EntityPlan) -> tuple[str, dict]:
        """(name prefix, extra prop fields) for a composite: the first is the device's kind, later ones are groups."""
        kind, ident = KINDS.get(plan.platform, plan.platform), plan.identity
        cat = _category(ident)
        if self.kind is None:
            self.kind = kind
            self.klass = ident.get("device_class")
            return "", cat
        g = self.name(plan.key)
        self.groups[g] = {"kind": kind, **({"class": ident["device_class"]} if ident.get("device_class") else {})}
        return g + "_", {"group": g, **cat}


def _simple(b: _Builder, plan: EntityPlan) -> None:
    i, p = plan.identity, plan.platform
    if p == "switch":
        b.add(plan.key, {"type": "binary", "rw": True, **_common(i)}, plan, read=_view(plan, "is_on"), write=_switch(plan))
    elif p == "button":
        d = {"type": "trigger", **_common(i)}
        if d.get("class") not in BUTTON_CLASSES:
            d.pop("class", None)
        b.add(plan.key, d, plan, write=_call(plan, "press"))
    elif p == "select":
        opts = list(plan.roles["main"].spec.range)
        b.add(plan.key, {"type": "select", "rw": True, "options": opts, **_common(i)}, plan,
              read=_view(plan, "current_option"), write=_call(plan, "select_option", "option"))
    elif p == "number":
        d = {"type": "number", "rw": True, "min": integral(i["native_min_value"]),
             "max": integral(i["native_max_value"]), "step": integral(i["native_step"]), **_common(i)}
        if u := _unit(i):
            d["unit"] = u
        b.add(plan.key, d, plan, read=_view(plan, "native_value", integral), write=_call(plan, "set_value", "value"))
    elif p == "binary_sensor":
        b.add(plan.key, {"type": "binary", **_common(i)}, plan, read=_view(plan, "is_on"))
    elif p == "sensor":
        d = _common(i)
        if i.get("kind") == "enum":
            opts = list(i.get("options") or plan.roles["main"].spec.range)
            d.update(type="select", options=opts)
            def conv(x, o=opts):
                return x if x in o else None       # V-3
        elif i.get("kind") == "text":
            d["type"] = "text"
            def conv(x):
                return x
        else:
            d["type"] = "number"
            if u := _unit(i):
                d["unit"] = u
            sc = i.get("state_class")
            if sc in ("total_increasing", "total"):
                d["series"] = "counter"
            elif sc == "measurement":
                d["series"] = "gauge"
            conv = integral
        b.add(plan.key, d, plan, read=_view(plan, "native_value", conv))
    elif p == "event":
        b.add(plan.key, {"type": "event", "options": list(i["event_types"]), **_common(i)}, plan, event=True)


def _light(b: _Builder, plan: EntityPlan) -> None:
    pre, g = b.composite(plan)
    st, r = plan.identity, plan.roles
    kmin, kmax = st["min_color_temp_kelvin"], st["max_color_temp_kelvin"]
    b.add(plan.key, {"type": "binary", "rw": True, "role": "on", **g}, plan, read=_view(plan, "is_on"),
          write=_switch(plan))
    if "brightness" in r or "color_data" in r:
        b.add(pre + "brightness", {**_percent(1, rw=True, role="brightness"), **g}, plan,
              read=_view(plan, "brightness", lambda x: min(100, max(1, round(x * 100 / 255)))),
              write=lambda v, s: plan.write("turn_on", {"brightness": max(1, round(v * 255 / 100))}, s))
    if "color_temp" in r:
        b.add(pre + "color_temperature", {"type": "number", "rw": True, "role": "color_temperature", "unit": "K",
                                          "min": kmin, "max": kmax, "step": 1, **g}, plan,
              read=_view(plan, "color_temp_kelvin", lambda x: min(kmax, max(kmin, x))),
              write=_call(plan, "turn_on", "color_temp_kelvin"))
    if "color_data" in r:
        b.add(pre + "color", {"type": "text", "rw": True, "role": "color", **g}, plan,
              read=_view(plan, "hs_color", _to_hex),
              write=lambda v, s: plan.write("turn_on", {"hs_color": _from_hex(v)}, s))
        if "color_temp" in r or "white" in st["supported_color_modes"]:
            modes = {"hs": "color", "color_temp": "white", "white": "white"}
            b.add(pre + "color_mode", {"type": "select", "role": "color_mode", "options": ["white", "color"], **g},
                  plan, read=_view(plan, "color_mode", modes.get))


def _cover(b: _Builder, plan: EntityPlan) -> None:
    hazardous = plan.identity.get("device_class") in HAZARDOUS_COVERS and not b.allow_hazardous
    pre, g = b.composite(plan)
    b.covers.append(CoverPart(plan, pre, g.get("group"), hazardous))
    feats, r = plan.identity["supported_features"], plan.roles
    if "set_position" in r or "current_position" in r:
        rw = "set_position" in r and not hazardous
        b.add(pre + "position", {**_percent(role="position"), **({"rw": True} if rw else {}), **g}, plan,
              read=_view(plan, "current_position"),
              write=_call(plan, "set_cover_position", "position") if rw else None)
    if "tilt" in r:
        b.add(pre + "tilt", {**_percent(role="tilt"), "rw": not hazardous, **g}, plan,
              read=_view(plan, "current_tilt_position"), write=_call(plan, "set_cover_tilt_position", "tilt_position"))
    if not hazardous:
        for act in ("open", "close", "stop"):
            if feats & COVER_FEATURES[act.upper()]:
                b.add(pre + act, {"type": "trigger", "role": act, **g}, plan, write=_call(plan, f"{act}_cover"))


def _alarm(b: _Builder, plan: EntityPlan) -> None:
    pre, g = b.composite(plan)
    rng = plan.roles["master_mode"].spec.range
    b.add(pre + "alarm_state", {"type": "select", "role": "alarm_state",
                                "options": ["disarmed", "armed_home", "armed_away", "triggered"], **g}, plan,
          read=_view(plan, "alarm_state"))
    for act, raw in (("arm_home", "home"), ("arm_away", "arm"), ("disarm", "disarmed")):
        if raw not in rng or (act == "disarm" and not b.allow_hazardous):      # S-1: disarm stays out unless allowed
            continue
        b.add(pre + act, {"type": "trigger", "role": act, **g}, plan, write=_call(plan, act))


def _vacuum(b: _Builder, plan: EntityPlan) -> None:
    pre, g = b.composite(plan)
    feats = plan.identity["supported_features"]
    b.add(pre + "vacuum_state", {"type": "select", "role": "vacuum_state",
                                 "options": ["cleaning", "docked", "paused", "returning", "idle", "error"], **g}, plan,
          read=_view(plan, "activity"))
    for name, role, act in VACUUM_ACTIONS:
        if feats & VAC_FEATURES[name.upper()]:
            b.add(pre + name, {"type": "trigger", **({"role": role} if role else {}), **g}, plan, write=_call(plan, act))
    if plan.identity["fan_speed_list"]:
        b.add(pre + "fan_speed", {"type": "select", "rw": True, "role": "fan_speed",
                                  "options": list(plan.identity["fan_speed_list"]), **g}, plan,
              read=_view(plan, "fan_speed"), write=_call(plan, "set_fan_speed", "fan_speed"))


def _fan(b: _Builder, plan: EntityPlan) -> None:
    pre, g = b.composite(plan)
    r = plan.roles
    if "switch" in r:
        # core's fan entity has no key: name the power after its dp, as a light's is (`switch_led`)
        b.add(plan.key or r["switch"].code, {"type": "binary", "rw": True, "role": "on", **g}, plan,
              read=_view(plan, "is_on"), write=_switch(plan))
    if "speed" in r:
        b.add(pre + "speed", {**_percent(1, rw=True, role="speed"), **g}, plan,
              read=_view(plan, "percentage"), write=_call(plan, "set_percentage", "percentage"))
    if "mode" in r:
        b.add(pre + "mode", {"type": "select", "rw": True, "role": "mode", "options": list(r["mode"].spec.range),
                             **g}, plan, read=_view(plan, "preset_mode"), write=_call(plan, "set_preset_mode", "preset_mode"))
    if "oscillate" in r:
        b.add(pre + "oscillate", {"type": "binary", "rw": True, "role": "oscillate", **g}, plan,
              read=_view(plan, "oscillating"), write=_call(plan, "oscillate", "oscillating"))
    if "direction" in r:
        b.add(pre + "direction", {"type": "select", "rw": True, "role": "direction", "options": ["forward", "reverse"],
                                  **g}, plan, read=_view(plan, "direction"), write=_call(plan, "set_direction", "direction"))


def _siren(b: _Builder, plan: EntityPlan) -> None:
    _, g = b.composite(plan)
    b.add(plan.key, {"type": "binary", "rw": True, "role": "on", **g}, plan, read=_view(plan, "is_on"),
          write=_switch(plan))


def _valve(b: _Builder, plan: EntityPlan) -> None:
    _, g = b.composite(plan)
    b.add(plan.key, {"type": "binary", "rw": True, "role": "opened", **g}, plan,
          read=_view(plan, "is_closed", lambda x: not x), write=_switch(plan, "open", "close"))


def _humidifier(b: _Builder, plan: EntityPlan) -> None:
    pre, g = b.composite(plan)
    i, r = plan.identity, plan.roles
    if "switch" in r:
        b.add(plan.key, {"type": "binary", "rw": True, "role": "on", **g}, plan, read=_view(plan, "is_on"),
              write=_switch(plan))
    if "target_humidity" in r:
        b.add(pre + "target_humidity", {**_percent(i["min_humidity"], i["max_humidity"], rw=True,
                                                 role="target_humidity"), **g}, plan,
              read=_view(plan, "target_humidity"), write=_call(plan, "set_humidity", "humidity"))
    if "current_humidity" in r:
        b.add(pre + "humidity", {"type": "number", "role": "current_humidity", "unit": "%", **g}, plan,
              read=_view(plan, "current_humidity"))
    if "mode" in r:
        b.add(pre + "mode", {"type": "select", "rw": True, "role": "mode", "options": list(r["mode"].spec.range), **g},
              plan, read=_view(plan, "mode"), write=_call(plan, "set_mode", "mode"))


def _climate(b: _Builder, plan: EntityPlan) -> None:
    pre, g = b.composite(plan)
    i, r = plan.identity, plan.roles
    unit = i["temperature_unit"]
    celsius = unit == "°C"                                    # the IL roles are in °C

    def role(x):
        return {"role": x} if celsius else {}

    switch, mode = r.get("switch"), r.get("hvac_mode")
    if switch is not None:
        b.add(plan.key or switch.code, {"type": "binary", "rw": True, **role("on"), **g}, plan,
              read=lambda st, slot: (None if (h := plan.read(st)["hvac_mode"]) is None else h != "off"),
              write=_switch(plan))
    if mode is not None and (opts := [m for m in i["hvac_modes"] if m != "off"]):
        # K-5: the wire's mode is shown while the device is off, so read it as if the switch were on
        def mode_read(st, slot):
            st = {**st, switch.code: True} if switch is not None else st
            v = plan.read(st)["hvac_mode"]
            return v if v in opts else None
        b.add(pre + "mode", {"type": "select", "rw": True, **role("mode"), "options": opts, **g}, plan,
              read=mode_read, write=_call(plan, "set_hvac_mode", "hvac_mode"))
    if i.get("preset_modes"):
        b.add(pre + "preset", {"type": "select", "rw": True, "options": list(i["preset_modes"]), **g}, plan,
              read=_view(plan, "preset_mode"), write=_call(plan, "set_preset_mode", "preset_mode"))
    if "set_temp_c" in r or "set_temp_f" in r:
        b.add(pre + "target_temperature", {"type": "number", "rw": True, **role("target_temperature"), "unit": unit,
                                           "min": integral(i["min_temp"]), "max": integral(i["max_temp"]),
                                           "step": integral(i["target_temperature_step"]), **g},
              plan, read=_view(plan, "temperature", integral), write=_call(plan, "set_temperature", "temperature"))
    if "cur_temp_c" in r or "cur_temp_f" in r:
        b.add(pre + "current_temperature", {"type": "number", **role("current_temperature"), "unit": unit, **g}, plan,
              read=_view(plan, "current_temperature", integral))
    if "cur_hum" in r:
        b.add(pre + "current_humidity", {"type": "number", "role": "current_humidity", "unit": "%", **g}, plan,
              read=_view(plan, "current_humidity"))
    if "set_hum" in r:
        b.add(pre + "target_humidity", {**_percent(i["min_humidity"], i["max_humidity"], rw=True), **g},
              plan, read=_view(plan, "target_humidity"), write=_call(plan, "set_humidity", "humidity"))
    if "fan" in r:
        b.add(pre + "fan_speed", {"type": "select", "rw": True, "role": "fan_speed", "options": list(i["fan_modes"]),
                                  **g}, plan, read=_view(plan, "fan_mode"), write=_call(plan, "set_fan_mode", "fan_mode"))
    _swing(b, plan, pre, g)


def _swing(b: _Builder, plan: EntityPlan, pre: str, g: dict) -> None:
    """A climate's swing roles as binaries; a vertical or horizontal write keeps the other axis as it is."""
    r = plan.roles
    axes = {"swing_on_off": ("swing", None), "swing_v": ("swing_vertical", "vertical"),
            "swing_h": ("swing_horizontal", "horizontal")}
    for rk, (name, axis) in axes.items():
        if rk not in r:
            continue
        code = r[rk].code

        def swing_write(v, s, axis=axis):
            def cur(k):
                return bool(k in r and ops.validate_bool_read(s.get(r[k].code)))
            if axis is None:
                mode = "on" if v else "off"
            else:
                h, vv = (v, cur("swing_v")) if axis == "horizontal" else (cur("swing_h"), v)
                mode = "both" if (h and vv) else "horizontal" if h else "vertical" if vv else "off"
            return plan.write("set_swing_mode", {"swing_mode": mode}, s)
        b.add(pre + name, {"type": "binary", "rw": True, **({"role": name} if axis else {}), **g}, plan,
              read=lambda st, slot, code=code: ops.validate_bool_read(st.get(code)), write=swing_write)


_COMPOSITE = {"humidifier": _humidifier, "climate": _climate, "light": _light, "cover": _cover, "fan": _fan,
              "siren": _siren, "valve": _valve, "alarm_control_panel": _alarm, "vacuum": _vacuum}


def assemble(schema, plans: list[EntityPlan], device_info: dict[str, Any], *, allow_hazardous: bool = False) -> Assembly:
    """`schema` is the (post-quirk) `DeviceSchema`; `device_info` the `quirks.device_info` dict."""
    b = _Builder(allow_hazardous)
    for plan in plans:
        if plan.platform in UNSUPPORTED:
            b.unsupported.append(f"{plan.platform}:{plan.key}")
        else:
            _COMPOSITE.get(plan.platform, _simple)(b, plan)
    for bd in b.bindings.values():
        if bd.plan.slot_kind in ("delta", "event"):
            bd.slot = StateSlot()
    props = {"available": {"type": "binary", "role": "available"}, **b.props}
    desc: dict[str, Any] = {"il": IL_VERSION, "id": schema.id, "source": "tuya", "props": props}
    for key, value in (("kind", b.kind), ("class", b.klass), ("label", schema.name),
                       ("vendor", device_info.get("manufacturer")), ("model", device_info.get("model"))):
        if value:
            desc[key] = value
    desc["identifiers"] = {"tuya_id": schema.id, **({"tuya_product_id": schema.product_id} if schema.product_id else {})}
    if b.groups:
        desc["groups"] = b.groups
    return Assembly(desc, b.bindings, b.unsupported, b.covers)
