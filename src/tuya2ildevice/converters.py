"""Code converters, sans-IO: per-device objects that derive extra ildevice properties from the device's dp values.

This is what rustuya-homeassistant's ``custom_converters/*.py`` did (derive a value from several dps, keep state
between packets). A converter is created per device, sees the driver's full dp-code state on every packet, and
returns property values. It never reads a clock or does I/O: when it needs to be woken later it asks for a timer
and the host's `Timer` input calls it back (il.md R-6).

    class MyConverter(Converter):
        def props(self):                     # extra properties (read only), may carry a role
            return {"motion": {"type": "select", "role": "motion", "options": ["opening", "closing", "stopped"]}}
        def update(self, now, codes, changed, active):
            return {"motion": "stopped"}     # or Result(values={...}, timers={"settle": 5})

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
        """Extra properties, as ildevice property definitions. Read only (no `rw`)."""
        return {}

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


# --- built-in: cover motion ----------------------------------------------------------------------------
class CoverMotion(Converter):
    """`motion` (opening / closing / stopped) of a curtain or blind that reports no motion of its own.

    Ported from rustuya-homeassistant's ``00_curtain.py``. Config (all optional):
    ``command`` (default ``control``), ``set_position`` (``percent_control``), ``position`` (``percent_state``): dp codes;
    ``words``: ``{"open": "open", "close": "close", "stop": "stop"}``, what the control dp carries;
    ``invert``: which raw end is closed; default follows the engine's own position (reversed unless
    ``control_back_mode`` is ``back``), so `motion` and `position` always agree;
    ``settle``: seconds without a position report after which motion counts as stopped (default 0 = never).

    A command word, or a set-position that differs from the current position, starts motion; a stop word, reaching
    the target or an end, and any readback / snapshot settle it. A snapshot never starts motion (its dps describe the
    last command, not a move in progress).
    """
    OPTIONS = ("opening", "closing", "stopped")

    def __init__(self, config: dict | None = None):
        c = config or {}
        unknown = set(c) - {"command", "set_position", "position", "words", "invert", "settle"}
        if unknown:
            raise ValueError(f"cover_motion: unknown config {sorted(unknown)}")
        self.command = c.get("command", "control")
        self.set_position = c.get("set_position", "percent_control")
        self.position = c.get("position", "percent_state")
        self.words = {"open": "open", "close": "close", "stop": "stop", **(c.get("words") or {})}
        self.invert = c.get("invert")
        self.settle = float(c.get("settle") or 0)
        self.reset()

    def reset(self) -> None:
        self.state: str | None = None
        self.target: float | None = None           # where the current move should end (0 closed .. 100 open)

    def props(self) -> dict[str, dict]:
        return {"motion": {"type": "select", "role": "motion", "options": list(self.OPTIONS)}}

    def _open_pct(self, codes: dict, raw: Any) -> float | None:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        inv = self.invert if self.invert is not None else codes.get("control_back_mode") != "back"
        return 100 - raw if inv else float(raw)

    def _out(self, state: str | None, timers: dict | None = None) -> Result:
        if state is not None:
            self.state = state
            if state == "stopped":
                self.target = None
        return Result({"motion": state} if state is not None else {}, timers or {})

    def update(self, now, codes, changed, active) -> Result:
        pos = self._open_pct(codes, codes.get(self.position))
        state: str | None = None
        timers: dict[str, float | None] = {}
        if not active:
            if pos is not None and any(c in changed for c in (self.position, self.command, self.set_position)):
                state = "stopped"
        else:
            if self.command in changed:
                word = codes.get(self.command)
                if word == self.words["stop"]:
                    state = "stopped"
                elif word == self.words["open"]:
                    state, self.target = "opening", 100.0
                elif word == self.words["close"]:
                    state, self.target = "closing", 0.0
            elif self.set_position in changed and pos is not None:
                tgt = self._open_pct(codes, codes.get(self.set_position))
                if tgt is not None:
                    self.target = tgt
                    state = "stopped" if tgt == pos else "opening" if tgt > pos else "closing"
            elif self.position in changed and pos is not None and self.state in ("opening", "closing"):
                arrived = self.target is not None and (pos >= self.target if self.state == "opening" else pos <= self.target)
                state = "stopped" if arrived else None
            elif self.position in changed and pos is not None:
                state = "stopped"
            if pos is not None and ((pos <= 0 and (state or self.state) == "closing") or (pos >= 100 and (state or self.state) == "opening")):
                state = "stopped"                   # at a hard end the motor cannot go further
        moving = (state or self.state) in ("opening", "closing") and state != "stopped"
        if self.settle:
            timers["settle"] = self.settle if moving else None
        return self._out(state, timers)

    def timer(self, now, name, codes) -> Result:
        return self._out("stopped") if name == "settle" and self.state in ("opening", "closing") else Result()


BUILTIN: dict[str, Callable[[dict], Converter]] = {"cover_motion": CoverMotion}
