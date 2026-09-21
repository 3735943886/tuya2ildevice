"""Driver inputs and outputs (il.md section 8) as plain data. No behaviour, no I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# --- inputs -------------------------------------------------------------------
@dataclass(frozen=True)
class Connected:
    """The device link came up."""


@dataclass(frozen=True)
class Disconnected:
    """The device link dropped."""


@dataclass(frozen=True)
class Message:
    """A structured packet from the device. For Tuya, `channel` is one of

    * ``"active"``: device-initiated push (button, toggle, add_ele delta, event). Only this channel fires events
      and accumulates deltas.
    * ``"passive"`` / ``"state"``: readback, periodic report or full snapshot. Updates values only.

    `json` is what rustuya-bridge publishes: ``{"dps": {...}}``, ``{"data": {"dps": {...}}}`` (optionally with
    ``"t"``) or the bare ``{"1": true}``.
    """
    channel: str
    json: Any


@dataclass(frozen=True)
class Command:
    """A consumer wants to write a property. The driver runs the il.md section 5 checks itself."""
    prop: str
    value: Any = None


@dataclass(frozen=True)
class Timer:
    name: str


# --- outputs ------------------------------------------------------------------
@dataclass(frozen=True)
class Descriptor:
    desc: dict


@dataclass(frozen=True)
class Value:
    prop: str
    value: Any


@dataclass(frozen=True)
class Absent:
    prop: str


@dataclass(frozen=True)
class Event:
    prop: str
    kind: str


@dataclass(frozen=True)
class SendMessage:
    """Write to the device. Channel ``"set"``: json ``{"dps": {"<dp id>": raw, ...}}``; the host adds the device."""
    channel: str
    json: Any


@dataclass(frozen=True)
class SetTimer:
    """Call the driver back with ``Timer(name)`` after `after` seconds (replaces a timer of the same name)."""
    name: str
    after: float


@dataclass(frozen=True)
class CancelTimer:
    name: str


@dataclass(frozen=True)
class Reject:
    prop: str
    code: str
    reason: str = ""


Input = Connected | Disconnected | Message | Command | Timer
Output = Descriptor | Value | Absent | Event | SendMessage | SetTimer | CancelTimer | Reject
