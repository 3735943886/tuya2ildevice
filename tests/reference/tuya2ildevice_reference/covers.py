"""Cover settings and configuration entities, applied after DP role classification.

Position choice is retained from the original classification. Inversions compose
with adapter remaps; direct report inversion affects only CoverReport output.
state_source overrides the legacy infer_motion Boolean. SettingsChanged lets the
host persist settings and rebuild the driver without sending motor commands.
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any

from .assemble import Assembly, Binding, CoverPart
from .converters import CoverMotion, CoverReport
from .overrides import COVER_DEFAULTS, OverrideError, int_bounds
from .tuya.adapter import Adapter, Remap
from .tuya.model import BOOLEAN, ENUM, DeviceSchema, ResolvedDp
from .tuya.runtime import EntityPlan

_LOGGER = logging.getLogger(__name__)
_WARNED: set[tuple[str, str]] = set()

# the switches, in the order they appear, with their labels
SWITCHES = {"invert_position": "Invert current position", "invert_set_position": "Invert target position",
            "invert_control": "Invert control", "position_from_target": "Use target as current position",
            "state_source": "Motion state source", "invert_reported_motion": "Invert reported motion"}
_NEVER = object()                                                            # a word no dp carries
_SWAPS = ({"open": "close", "close": "open"}, {"FZ": "ZZ", "ZZ": "FZ"})      # a command dp's open / close words


@dataclass
class Cover:
    part: CoverPart
    defaults: dict[str, Any]
    settings: dict[str, Any]                          # in effect: the defaults, then the block's
    cur: ResolvedDp | None = None
    setp: ResolvedDp | None = None
    ins: ResolvedDp | None = None
    tilt: ResolvedDp | None = None
    switches: list[str] = field(default_factory=list)

    position_choice: bool = False

    @property
    def state_source(self) -> str:
        return self.settings["state_source"] or ("inferred" if self.settings["infer_motion"] else "none")

    def value(self, key: str) -> Any:
        """A setting as its switch shows it."""
        return self.state_source if key == "state_source" else self.settings[key]

    @property
    def separate_target(self) -> bool:
        return self.setp is not None and (self.cur is None or self.setp.code != self.cur.code)

    def applies(self, key: str) -> bool:
        """Does the setting change anything for this cover?"""
        if key == "position_from_target":
            return self.position_choice
        if key == "state_source":
            return self.cur is not None or self.ins is not None
        if key == "invert_reported_motion":
            return self.ins is not None and self.state_source == "control"
        if key == "invert_position":
            return self.cur is not None
        if key == "invert_set_position":
            return self.separate_target
        if key == "invert_control":
            return self.ins is not None and self.setp is None
        if key == "infer_motion":
            return self.cur is not None and (self.separate_target or self.ins is not None)
        return True


def _own(block: dict, group: str | None) -> dict:
    """A cover's own settings in a `cover` block: the top level for the device's own kind, else under its group."""
    c = block.get("cover") or {}
    if group is not None:
        c = c.get(group) or {}
    return {k: v for k, v in c.items() if k in COVER_DEFAULTS}


def covers(assembly: Assembly, block: dict, *, infer_motion: bool = True) -> list[Cover]:
    """The device's covers with the settings `block` gives them (`infer_motion`: that setting's default). Raises
    `OverrideError` for a group the device has no cover in."""
    defaults = {**COVER_DEFAULTS, "infer_motion": infer_motion}
    out = []
    for part in assembly.covers:
        r = part.plan.roles
        out.append(Cover(part, defaults, {**defaults, **_own(block, part.group)}, r.get("current_position") or r.get("set_position"),
                         r.get("set_position"), r.get("instruction"), r.get("tilt")))
    for c in out:
        c.position_choice = c.cur is not None and c.separate_target
    groups = {c.part.group for c in out}
    for k, v in (block.get("cover") or {}).items():
        if isinstance(v, dict) and k not in groups:
            raise OverrideError(f"cover.{k}: the device has no cover in a group of that name "
                                f"(has {sorted(g for g in groups if g)})")
    return out


def dropped(found: list[Cover]) -> set[str]:
    """The reported position dps that `position_from_target` leaves out (the device is classified again without)."""
    return {c.cur.code for c in found
            if c.settings["position_from_target"] and c.cur is not None and c.separate_target}


def without(schema: DeviceSchema, codes: set[str]) -> DeviceSchema:
    schema = copy.deepcopy(schema)
    for code in codes:
        schema.function.pop(code, None)
        schema.status_range.pop(code, None)
        schema.status.pop(code, None)
    return schema


def _invert(adapter: Adapter, schema: DeviceSchema, code: str) -> None:
    r = adapter.remaps.get(code) or Remap()
    adapter.remaps[code] = Remap(r.alias, not r.invert, r.bounds or int_bounds(schema, code))


def _swap(adapter: Adapter, schema: DeviceSchema, ins: ResolvedDp) -> None:
    if ins.kind == BOOLEAN:
        _invert(adapter, schema, ins.code)
        return
    if ins.kind != ENUM:
        return
    rng = set(ins.spec.range)                          # already in the standard words (after any alias)
    swap = next((s for s in _SWAPS if set(s) <= rng), None)
    if swap is None:
        return
    r = adapter.remaps.get(ins.code) or Remap()
    alias: dict = {}
    for dev in set(r.alias) | (set(swap) - set(r.alias.values())):
        std = r.alias.get(dev, dev)
        if (new := swap.get(std, std)) != dev:
            alias[dev] = new
    adapter.remaps[ins.code] = Remap(alias, r.invert, r.bounds)


def apply(found: list[Cover], adapter: Adapter, schema: DeviceSchema) -> None:
    """Lay the settings over the dps' value fixes."""
    for c in found:
        s = c.settings
        if s["invert_position"] and c.cur is not None:
            _invert(adapter, schema, c.cur.code)          # a target that is the same dp turns with it
        if s["invert_set_position"] and c.separate_target:
            _invert(adapter, schema, c.setp.code)
        if s["invert_control"] and c.applies("invert_control"):
            _swap(adapter, schema, c.ins)
        if s["invert_tilt"] and c.tilt is not None:
            _invert(adapter, schema, c.tilt.code)


def motion(c: Cover) -> CoverMotion | None:
    """One state producer per cover: direct reports, inference, or position at rest."""
    cfg = {"command": c.ins.code if c.ins is not None else None,
           "set_position": c.setp.code if c.separate_target else None,
           "position": c.cur.code if c.cur is not None else None, "settle": c.settings["settle"]}
    if c.ins is not None and c.ins.kind == BOOLEAN:
        cfg["words"] = {"open": True, "close": False, "stop": _NEVER}
    elif c.ins is not None and c.ins.kind == ENUM and set(_SWAPS[1]) <= set(c.ins.spec.range):
        cfg["words"] = {"open": "FZ", "close": "ZZ", "stop": "STOP"}
    kwargs = {"prop": c.part.prefix + "cover_state", "group": c.part.group}
    if c.state_source == "control" and c.ins is not None:
        return CoverReport(cfg, invert_report=c.settings["invert_reported_motion"], **kwargs)
    if c.state_source == "inferred" and c.applies("infer_motion"):
        return CoverMotion(cfg, **kwargs)
    if c.cur is not None and c.settings["state_source"] == "none":
        return CoverReport(cfg, enabled=False, **kwargs)
    return None


def switches(assembly: Assembly, found: list[Cover], *, motion_taken: set[str]) -> dict[str, tuple[Cover, str]]:
    """Add each cover's switches that change something to the descriptor (read only for a hazardous cover); returns
    ``{property: (cover, setting)}``. `motion_taken`: a converter of the user's owns the motion, so no switch for it."""
    props, out = assembly.descriptor["props"], {}
    for c in found:
        for key, label in SWITCHES.items():
            if not c.applies(key) or (key in ("state_source", "invert_reported_motion") and c.part.prefix + "cover_state" in motion_taken):
                continue
            name = f"{c.part.prefix}cover_{key}"
            if name in props:
                continue
            d = {"type": "binary", **({} if c.part.hazardous else {"rw": True}), "category": "config", "label": label}
            if key == "state_source":
                d.update(type="select", options=["control", "inferred", "none"] if c.ins is not None else ["inferred", "none"])
            props[name] = {**d, "group": c.part.group} if c.part.group else d
            assembly.bindings[name] = Binding(name, EntityPlan("setting", name, {}, {}, ()))
            c.switches.append(key)
            out[name] = (c, key)
    return out


def block(found: list[Cover], product: dict, device: dict) -> dict:
    """The device's settings block, as the switches leave it: each setting that differs from where the device would
    start without a block of its own (the defaults, then its product's `cover`), and each its own block already had.
    `product` / `device`: the product's and the device's own override blocks."""
    out: dict = {}
    for c in found:
        base = {**c.defaults, **_own(product, c.part.group)}
        had = _own(device, c.part.group)
        vals = {k: v for k, v in c.settings.items() if v != base[k] or k in had}
        if c.part.group is None:
            out.update(vals)
        elif vals:
            out[c.part.group] = vals
    return {"cover": out} if out else {}


def deprecated(block: dict, found: list[Cover]) -> list[str]:
    """What the block does the old way, for the host to warn about."""
    out = []
    codes = {r.code for c in found for r in (c.cur, c.setp, c.ins) if r is not None}
    for code, r in (block.get("remap") or {}).items():
        if r.get("invert") and code in codes:
            out.append(f"remap.{code}.invert on a cover is deprecated: move it to the cover settings "
                       "(`cover.invert_position` / `invert_set_position` / `invert_control`, or the device's switches)")
    if "cover_motion" in (block.get("converters") or {}):
        out.append("converters.cover_motion is deprecated: move it to the cover settings "
                   "(`cover.infer_motion`, `cover.settle`); it replaces the cover's own motion until then")
    return out


def warn(device_id: str, messages: list[str]) -> None:
    for m in messages:
        if (device_id, m) not in _WARNED:
            _WARNED.add((device_id, m))
            _LOGGER.warning("device %s: %s", device_id, m)
