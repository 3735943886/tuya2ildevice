"""Code converters, sans-IO: per-device objects that derive extra ildevice properties from the device's dp values.

A converter derives a value from several dps and keeps state between packets. It is created per device, sees the driver's full dp-code state on every packet, and
returns property values. It never reads a clock or does I/O: when it needs to be woken later it asks for a timer
and the host's `Timer` input calls it back (il.md R-6).

    class MyConverter(Converter):
        def props(self):                     # extra properties (read only), may carry a role
            return {"filter_low": {"type": "binary", "class": "problem"}}
        def update(self, now, codes, changed, active):
            return {"filter_low": codes.get("filter_life", 100) < 10}     # or Result(values={...}, timers={"t": 5})

Register with ``TuyaDriver(device, converters={product_id_or_device_id: factory})`` (`factory(device) -> Converter`, or a
list of factories), or by name in an override block: ``{"converters": {"cover_motion": {...config...}}}``.
Loading a user's ``.py`` file is the host's job; the code runs in-process, so trust it like any plugin.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Result:
    """`values`: property -> value (None = absent). `timers`: name -> seconds from now (None cancels)."""
    values: dict[str, Any] = field(default_factory=dict)
    timers: dict[str, float | None] = field(default_factory=dict)


class Converter:
    def props(self) -> dict[str, dict]:
        """Properties, as ildevice property definitions. One with a name the tables already gave replaces it. One
        with `rw: true` (or a `trigger`) is written through `write`."""
        return {}

    def write(self, prop: str, value: Any, codes: dict[str, Any]) -> dict[str, Any] | list[dict] | None:
        """A command for one of this converter's writable properties (already checked against its definition; None
        for a trigger): the dps to send, as ``{code: value}`` (values as the device's codes read them, before the
        dp's own encoding) or ``[{"code", "value"}]``."""
        raise NotImplementedError(prop)

    def update(self, now: float, codes: dict[str, Any], changed: list[str], active: bool) -> Result | dict | None:
        """A packet arrived. `codes`: every dp value the driver holds (already updated); `changed`: the codes in this
        packet; `active`: it was pushed by the device (True) or is a readback / snapshot (False)."""
        return None

    def timer(self, now: float, name: str, codes: dict[str, Any]) -> Result | dict | None:
        return None

    def reset(self) -> None:
        """The device link dropped: forget anything that is not a value."""


def as_result(r: Result | dict | None) -> Result:
    return r if isinstance(r, Result) else Result(values=dict(r or {}))


class CoverMotion(Converter):
    """Compatibility facade for the Rust cover-motion state machine."""
    def __init__(self, config=None, *, prop="cover_state", group=None, mode="inferred", invert_report=False):
        from .native import call
        result = call("motion_create", config=config or {}, prop=prop, group=group, mode=mode, invert_report=invert_report)
        self._motion, self._props = result["motion"], result["props"]
        self.prop = prop

    def props(self):
        return self._props

    def update(self, now, codes, changed, active):
        from .native import call
        result = call("motion_update", motion=self._motion, codes=codes, changed=changed, active=active)
        self._motion = result["motion"]
        return Result(**result["result"])

    def timer(self, now, name, codes):
        from .native import call
        if name != "settle":
            return Result()
        result = call("motion_update", motion=self._motion, codes=codes, changed=[], active=False, timer=True)
        self._motion = result["motion"]
        return Result(**result["result"])

    def reset(self):
        from .native import call
        self._motion = call("motion_reset", motion=self._motion)


class CoverReport(CoverMotion):
    def __init__(self, config=None, *, invert_report=False, enabled=True, **kwargs):
        super().__init__(config, mode="control" if enabled else "none", invert_report=invert_report, **kwargs)


BUILTIN: dict[str, Callable[[dict], Converter]] = {"cover_motion": CoverMotion}
