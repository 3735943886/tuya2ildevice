"""classify / read / write (spec section 7). Platform builders register in `BUILDERS`."""
from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import PACKAGE_DIR
from .model import DeviceSchema, ResolvedDp

TABLES = PACKAGE_DIR / "tables"
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
    from dataclasses import asdict

    from ..native import call
    result = call("on_update", plan=plan._native, slot=asdict(slot) if slot else None,
                  changed=changed, timestamps=dp_timestamps, status=status)
    if slot and result["slot"] is not None:
        slot.total, slot.last_ts = result["slot"]["total"], result["slot"]["last_ts"]
    return UpdateResult(result["write_state"], tuple(result["fire"]) if result["fire"] else None)


@dataclass
class Plan:
    entities: list[EntityPlan]


@functools.cache
def load_table(platform: str) -> dict:
    from ..native import rules
    group = next(g for g in rules()["platforms"] if g["platform"] == platform)
    return {group["table"]: group["categories"]}


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
    from dataclasses import asdict

    from ..native import call
    raw = call("classify", device=asdict(schema), env=asdict(env or HostEnv()), platforms=platforms)
    return Plan([from_native(p) for p in raw])


def from_native(raw):
    from ..native import call
    from .model import DpSpec, ResolvedDp
    roles = {name: ResolvedDp(r["code"], DpSpec(r["code"], r["kind"], r["spec"]).parse(), r["kind"], r["report_type"])
             for name, r in raw["roles"].items()}
    def read(status, slot=None):
        result = call("read", plan=raw, status=status, total=slot.total if slot else 0)
        for name in ("hs_color", "event"):
            if isinstance(result.get(name), list):
                result[name] = tuple(result[name])
        return result
    def write(action, args, status):
        try:
            return call("write", plan=raw, action=action, args=args, status=status)
        except ValueError as error:
            raise WriteRejected(str(error)) from error
    plan = EntityPlan(raw["platform"], raw["key"], raw["identity"], roles, tuple(raw["depends_on"]),
                      None if raw["platform"] == "button" else read,
                      None if raw["platform"] in ("sensor", "binary_sensor", "event") else write,
                      raw["slot_kind"], raw["update_all"])
    plan._native = raw
    return plan
