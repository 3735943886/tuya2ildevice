"""Delta settings: which reports count an increment of a delta dp (`report_type: sum`, e.g. a plug's `add_ele`).

By default only an `active` push counts: a passive report is a readback of a value the device already pushed, and
counting it too would count the increment twice. Some devices never push it actively and report it passively
only; `accept_passive` counts their live passive reports as well (the host drops retained ones), each one, even one
that repeats the last value. A readback the host asks for (a `get`, on a reconnect) then counts once more: turn it on
only for a device that does not push.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .assemble import Assembly, Binding
from .overrides import DELTA_DEFAULTS
from .tuya.runtime import EntityPlan

# the switches, with their labels
SWITCHES = {"accept_passive": "Count passive reports"}


@dataclass
class Delta:
    settings: dict[str, Any]                          # in effect: the defaults, then the block's

    def value(self, key: str) -> Any:
        return self.settings[key]


def delta(assembly: Assembly, block: dict) -> Delta | None:
    """The device's delta settings, or None when it has no delta property."""
    if not any(b.plan.slot_kind == "delta" for b in assembly.bindings.values()):
        return None
    return Delta({**DELTA_DEFAULTS, **(block.get("delta") or {})})


def switches(assembly: Assembly, d: Delta | None) -> dict[str, tuple[Delta, str]]:
    """Add the switches to the descriptor; returns ``{property: (delta, setting)}``."""
    if d is None:
        return {}
    props, out = assembly.descriptor["props"], {}
    for key, label in SWITCHES.items():
        name = f"delta_{key}"
        if name in props:
            continue
        props[name] = {"type": "binary", "rw": True, "category": "config", "label": label}
        assembly.bindings[name] = Binding(name, EntityPlan("setting", name, {}, {}, ()))
        out[name] = (d, key)
    return out


def block(d: Delta | None, product: dict, device: dict) -> dict:
    """The device's `delta` settings block, as the switches leave it: each setting that differs from where the device
    would start without a block of its own (the defaults, then its product's), and each its own block already had."""
    if d is None:
        return {}
    base = {**DELTA_DEFAULTS, **(product.get("delta") or {})}
    had = device.get("delta") or {}
    vals = {k: v for k, v in d.settings.items() if v != base[k] or k in had}
    return {"delta": vals} if vals else {}
