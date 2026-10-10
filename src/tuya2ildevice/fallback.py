"""Thin binding for Rust's unused-DP planning."""
from dataclasses import asdict

from .native import call
from .tuya.runtime import from_native


def unused_plans(schema, entries, consumed):
    raw = call('unused', device=asdict(schema), entries=entries, consumed=sorted(consumed))
    return [from_native(p) for p in raw]
