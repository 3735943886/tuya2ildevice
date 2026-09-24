"""classify / read / write (spec section 7). Platform builders register in `BUILDERS`."""
from __future__ import annotations

import json
import pathlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import standard
from .model import DeviceSchema, ResolvedDp

TABLES = pathlib.Path(__file__).parent / "tables"
UNKNOWN = None  # R0.8: UNKNOWN == None


class WriteRejected(ValueError):
    """A write value the DP's type rejects (never clamped)."""


class ActionDPCodeNotFound(WriteRejected):
    """core's ActionDPCodeNotFoundError: the action needs a dp this device lacks."""

    def __init__(self, expected):
        super().__init__(f"action dpcode not found: {expected}")
        self.expected = expected


@dataclass
class HostEnv:
    temperature_unit: str = "C"
    allowed_units: dict[str, dict[str, list[str]]] = field(default_factory=dict)


@dataclass
class StateSlot:
    """The only mutable per-entity state (R0.3): delta accumulator (spec 7.3), not persisted."""
    total: float = 0.0
    last_ts: int | None = None


@dataclass
class UpdateResult:
    write_state: bool
    fire: tuple[str, dict | None] | None = None   # event platform: (event_type, attributes)


@dataclass
class EntityPlan:
    platform: str
    key: str
    identity: dict[str, Any]
    roles: dict[str, ResolvedDp]
    depends_on: tuple[str, ...] = ()
    read: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    write: Callable[[str, dict[str, Any], dict[str, Any]], list[dict[str, Any]]] | None = None
    slot_kind: str | None = None   # None | "delta" | "event"
    update_all: bool = False       # any_update platforms (spec 7): every update writes state


def new_slot(plan: EntityPlan) -> StateSlot | None:
    return StateSlot() if plan.slot_kind else None


def on_update(plan: EntityPlan, slot: StateSlot | None, changed: list[str] | None,
              dp_timestamps: dict[str, int] | None, status: dict[str, Any]) -> UpdateResult:
    """Spec 7: `changed is None` (online/offline) always writes state; own_dps platforms write iff a
    dependency changed; delta accumulates first (core DeltaIntegerWrapper.skip_update)."""
    if changed is None:
        return UpdateResult(True)
    if plan.slot_kind == "event":
        # fires iff own code changed and the event reads truthy (repeats fire; initial status never fires)
        code = plan.depends_on[0]
        ev = plan.read(status)["event"] if code in changed else None
        return UpdateResult(True, ev) if ev else UpdateResult(False)
    if plan.slot_kind == "delta":
        code = plan.depends_on[0]
        if code not in changed or dp_timestamps is None or (ts := dp_timestamps.get(code)) is None \
                or ts == slot.last_ts or (raw := status.get(code)) is None:
            return UpdateResult(False)
        slot.total += float(raw)   # core adds the RAW (unscaled) value, not the scaled one
        slot.last_ts = ts
        return UpdateResult(True)
    return UpdateResult(True if plan.update_all else any(c in changed for c in plan.depends_on))


@dataclass
class Plan:
    entities: list[EntityPlan]


_TABLE_CACHE: dict[str, dict] = {}


def load_table(platform: str) -> dict:
    if platform not in _TABLE_CACHE:
        tables = json.loads((TABLES / f"{platform}.json").read_text())["tables"]
        _TABLE_CACHE[platform] = standard.apply(platform, tables)
    return _TABLE_CACHE[platform]


def preload_tables() -> None:
    """Read every platform table now (see `tuya2ildevice.preload`)."""
    for path in sorted(TABLES.glob("*.json")):
        if not path.stem.startswith("_"):
            load_table(path.stem)


def identity(desc: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in desc.items() if not k.startswith("$")}
    out.setdefault("entity_registry_enabled_default", True)
    return out


Builder = Callable[[DeviceSchema, HostEnv, dict[str, Any]], "EntityPlan | None"]
BUILDERS: dict[str, tuple[str, Builder]] = {}  # platform -> (table name, builder)


def builder(platform: str, table: str):
    def deco(fn: Builder) -> Builder:
        BUILDERS[platform] = (table, fn)
        return fn
    return deco


def classify(schema: DeviceSchema, env: HostEnv | None = None, platforms: tuple[str, ...] | None = None) -> Plan:
    env = env or HostEnv()
    ents: list[EntityPlan] = []
    for platform, (table, fn) in BUILDERS.items():
        if platforms and platform not in platforms:
            continue
        descs = load_table(platform).get(table, {}).get(schema.category, [])
        for desc in ([descs] if isinstance(descs, dict) else descs):
            plan = fn(schema, env, desc)
            if plan is not None:
                ents.append(plan)
    return Plan(ents)
