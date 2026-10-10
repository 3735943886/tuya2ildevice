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


# --- built-in: cover motion ----------------------------------------------------------------------------
class CoverMotion(Converter):
    """`cover_state` (il.md: open / closed / opening / closing / stopped) of a curtain or blind that reports no motion
    of its own. At rest it is `closed` or `open` at an end and `stopped` part way (or with no position known).

    The driver turns one on for every cover with a reported position and a separate target or a command
    (``"cover": {"state_source": "inferred"}``, or legacy ``infer_motion: true``), on the codes the cover
    actually has. It sees the dps after the cover's settings and any `remap`, as the engine's `position` does, so
    `cover_state` and `position` always agree.

    A move starts on a command word or on a target that differs from the current position; a stop word, reaching the
    target or an end, and any readback / snapshot settle it. A snapshot never starts motion (its dps describe the last
    command, not a move in progress). A command word only says that a move started: the position reports that follow
    decide its direction, so a device that reports the opposite word still shows the way it really moves. A move
    started by a target keeps comparing the target with the position.

    Naming it in an override block (``{"converters": {"cover_motion": {...}}}``, deprecated: use the `cover`
    settings) replaces the driver's own. Config (all optional):
    ``command`` (default ``control``), ``set_position`` (``percent_control``), ``position`` (``percent_state``): dp codes
    (null: the cover has none);
    ``words``: ``{"open": "open", "close": "close", "stop": "stop"}``, what the control dp carries;
    ``invert``: count the position the other way (default false);
    ``settle``: seconds without a position report after which motion counts as stopped (default 0 = never).
    """
    OPTIONS = ("open", "closed", "opening", "closing", "stopped")
    MOVING = ("opening", "closing")
    CONFIG = frozenset({"command", "set_position", "position", "words", "invert", "settle"})

    def __init__(self, config: dict | None = None, *, prop: str = "cover_state", group: str | None = None):
        c = config or {}
        if unknown := set(c) - self.CONFIG:
            raise ValueError(f"cover_motion: unknown config {sorted(unknown)}")
        self.command = c.get("command", "control")
        self.set_position = c.get("set_position", "percent_control")
        self.position = c.get("position", "percent_state")
        self.words = {"open": "open", "close": "close", "stop": "stop", **(c.get("words") or {})}
        self.invert = bool(c.get("invert"))
        self.settle = float(c.get("settle") or 0)
        self.prop, self.group = prop, group
        self.reset()

    def reset(self) -> None:
        self.state: str | None = None
        self.target: float | None = None           # where the current move should end (0 closed .. 100 open)
        self.by_word = False                       # the move started on a command word: its direction is a guess
        self.last: float | None = None             # the position last seen

    def props(self) -> dict[str, dict]:
        d = {"type": "select", "role": "cover_state", "options": list(self.OPTIONS)}
        return {self.prop: {**d, "group": self.group} if self.group else d}

    def _open_pct(self, raw: Any) -> float | None:
        """A position dp's value as 0 (closed) .. 100 (open), or None when it is not a number."""
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        return float(100 - raw if self.invert else raw)

    def _out(self, codes: dict, state: str | None, timers: dict | None = None) -> Result:
        """`state` is the motion (opening / closing / stopped); at rest the value says which end, if any."""
        if state is None:
            return Result({}, timers or {})
        self.state = state
        if state == "stopped":
            self.target = None
            self.by_word = False
            pos = self._open_pct(codes.get(self.position))
            state = "stopped" if pos is None else "closed" if pos <= 0 else "open" if pos >= 100 else "stopped"
        return Result({self.prop: state}, timers or {})

    def update(self, now, codes, changed, active) -> Result:
        pos = self._open_pct(codes.get(self.position))
        last = self.last
        if pos is not None:
            self.last = pos
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
                    state, self.target, self.by_word = "opening", 100.0, True
                elif word == self.words["close"]:
                    state, self.target, self.by_word = "closing", 0.0, True
            elif self.set_position in changed and pos is not None:
                tgt = self._open_pct(codes.get(self.set_position))
                if tgt is not None:
                    self.target, self.by_word = tgt, False
                    state = "stopped" if tgt == pos else "opening" if tgt > pos else "closing"
            elif self.position in changed and pos is not None and self.state in self.MOVING:
                if self.by_word and last is not None and pos != last:
                    heading = "opening" if pos > last else "closing"       # where it really goes
                    if heading != self.state:
                        state, self.target = heading, 100.0 if heading == "opening" else 0.0
                moving = state or self.state
                arrived = self.target is not None and (pos >= self.target if moving == "opening" else pos <= self.target)
                if arrived:
                    state = "stopped"
            elif self.position in changed and pos is not None:
                state = "stopped"
            if pos is not None and ((pos <= 0 and (state or self.state) == "closing") or (pos >= 100 and (state or self.state) == "opening")):
                state = "stopped"                   # at a hard end the motor cannot go further
        moving = (state or self.state) in self.MOVING and state != "stopped"
        if self.settle:
            timers["settle"] = self.settle if moving else None
        return self._out(codes, state, timers)

    def timer(self, now, name, codes) -> Result:
        return self._out(codes, "stopped") if name == "settle" and self.state in self.MOVING else Result()


class CoverReport(CoverMotion):
    """Direct control reports, independent of target comparison and motion timers.

    Snapshots never assert movement. With no position they clear the state; with
    position they publish only its resting state. Live stop remains stopped,
    including at endpoints. Position-only updates cannot override live motion.
    """

    def __init__(self, config=None, *, invert_report=False, enabled=True, **kwargs):
        super().__init__(config, **kwargs)
        self.invert_report = invert_report
        self.enabled = enabled

    def update(self, now, codes, changed, active):
        if not any(c in changed for c in (self.command, self.position)):
            return Result()
        if active and self.enabled and self.command in changed:
            word = codes.get(self.command)
            state = next((state for key, state in (("open", "opening"), ("close", "closing"),
                                                   ("stop", "stopped")) if word == self.words[key]), None)
            if state is not None:
                if self.invert_report:
                    state = {"opening": "closing", "closing": "opening"}.get(state, state)
                self.state = state
                return Result({self.prop: state})
        if active and self.enabled and self.state in self.MOVING:
            return Result()
        self.state = None
        pos = self._open_pct(codes.get(self.position))
        return Result({self.prop: None if pos is None else "closed" if pos <= 0 else "open" if pos >= 100 else "stopped"})

    def timer(self, now, name, codes):
        return Result()


BUILTIN: dict[str, Callable[[dict], Converter]] = {"cover_motion": CoverMotion}
