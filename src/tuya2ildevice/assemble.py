"""Compatibility assembly DTOs; property construction executes in Rust."""
from dataclasses import asdict, dataclass, field
from typing import Any

from .native import call
from .tuya.runtime import StateSlot

IL_VERSION = 0
UNSUPPORTED = {'camera'}


@dataclass
class Binding:
    prop: str
    plan: Any
    read: Any = None
    write: Any = None
    event: bool = False
    slot: StateSlot | None = None


@dataclass
class CoverPart:
    plan: Any
    prefix: str
    group: str | None
    hazardous: bool


@dataclass
class Assembly:
    descriptor: dict
    bindings: dict
    unsupported: list = field(default_factory=list)
    covers: list = field(default_factory=list)


def assemble(schema, plans, device_info, *, allow_hazardous=False):
    raw = call('assemble', device=asdict(schema), plans=[p._native for p in plans],
               info=device_info, allow_hazardous=allow_hazardous)
    bindings = {}
    for name, binding in raw['bindings'].items():
        plan = plans[binding['plan']]
        def read(status, slot=None, b=binding, p=plan, name=name):
            b = {**b, 'total': slot.total if slot else 0}
            return call('binding_read', binding=b, plan=p._native, status=status,
                        definition=raw['descriptor']['props'][name])
        def write(value, status, b=binding, p=plan):
            return call('binding_write', binding=b, plan=p._native, value=value, status=status)
        bindings[name] = Binding(name, plan, read if binding['read'] else None, write if binding['action'] else None,
                                 plan.slot_kind == 'event', StateSlot() if plan.slot_kind else None)
    covers = [CoverPart(plans[i], prefix, group, hazardous) for i, prefix, group, hazardous in raw['covers']]
    return Assembly(raw['descriptor'], bindings, raw['unsupported'], covers)
