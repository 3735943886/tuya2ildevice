"""Entities for DPs that no platform table claimed (what rustuya-homeassistant v1 called "individual" DPs).

Every dp of the device's `local_strategy` that no assembled plan depends on becomes a plain property chosen by its
Tuya type: Boolean -> binary, Integer -> number, Enum -> select; String, Raw, Json and Bitmap -> a read-only value (text or
number; they are mostly opaque schedules, so never written). Boolean, Integer and Enum are writable only if the dp
is in `function` (the cloud says it accepts writes). Writable ones are
`category: config`, read-only ones `diagnostic`. The value logic is the engine's own (`ops`), so scaling and range
checks match the classified entities.
"""
from __future__ import annotations

from typing import Any

from .tuya import ops
from .tuya.model import BITMAP, BOOLEAN, ENUM, INTEGER, JSON, RAW, STRING, DeviceSchema, DpSpec, ResolvedDp, SchemaError, normalize_type
from .tuya.runtime import EntityPlan

MAX_TEXT = 1024                                    # il-messages.md W-13


def _text(raw: Any) -> str | None:
    return raw if isinstance(raw, str) and raw and len(raw) <= MAX_TEXT else None


def _label(code: str) -> str:
    return code.replace("_", " ").capitalize()


def _spec_of(schema: DeviceSchema, code: str, ci: dict) -> DpSpec:
    return schema.status_range.get(code) or schema.function.get(code) or DpSpec(
        code, ci.get("valueType") or STRING, ci.get("valueDesc") or None)


def unused_plans(schema: DeviceSchema, entries: dict[str, tuple[str, str, dict]], consumed: set[str]) -> list[EntityPlan]:
    """`entries`: the adapter's ``dp id -> (code, strategy, config_item)``; `consumed`: codes already used."""
    plans: list[EntityPlan] = []
    seen: set[str] = set()
    for dpid in sorted(entries, key=lambda d: (not d.isdigit(), int(d) if d.isdigit() else 0, d)):
        code, _, ci = entries[dpid]
        if code in consumed or code in seen:
            continue
        seen.add(code)
        spec = _spec_of(schema, code, ci)
        kind = normalize_type(spec.type)
        try:
            parsed = spec.parse()
        except SchemaError:
            continue
        if kind is None or parsed is None:
            continue
        rw = code in schema.function
        r = ResolvedDp(code, parsed, kind, spec.report_type)
        ident: dict[str, Any] = {"label": _label(code), "entity_category": "config" if rw else "diagnostic"}
        plan = _plan(code, kind, parsed, rw, r, ident)
        if plan is not None:
            plans.append(plan)
    return plans


def _plan(code: str, kind: str, spec: Any, rw: bool, r: ResolvedDp, ident: dict) -> EntityPlan | None:
    def mk(platform, read=None, write=None, slot_kind=None):
        return EntityPlan(platform, code, ident, {"main": r}, (code,), read, write, slot_kind=slot_kind)

    if kind == BOOLEAN:
        read = lambda st: {"is_on": ops.validate_bool_read(st.get(code))}
        if rw:
            return mk("switch", read, lambda a, args, st: [{"code": code, "value": a == "turn_on"}])
        return mk("binary_sensor", read)
    if kind == INTEGER:
        ident["native_unit"] = spec.unit or None
        if rw:
            ident.update(native_min_value=ops.scale_value(spec, spec.min), native_max_value=ops.scale_value(spec, spec.max),
                         native_step=ops.scale_value(spec, spec.step))
            return mk("number", lambda st: {"native_value": ops.validate_int_read(spec, st.get(code))},
                      lambda a, args, st: [{"code": code, "value": ops.validate_int_write(spec, args["value"])}])
        if r.report_type == "sum":                                   # an increment per report, not a total
            ident.update(kind="delta", state_class="total_increasing")
            return mk("sensor", lambda st, slot=None: {"native_value": slot.total if slot else 0.0}, slot_kind="delta")
        ident["kind"] = "integer"
        return mk("sensor", lambda st: {"native_value": ops.validate_int_read(spec, st.get(code))})
    if kind == ENUM:
        if rw:
            return mk("select", lambda st: {"current_option": ops.validate_enum_read(spec, st.get(code))},
                      lambda a, args, st: [{"code": code, "value": ops.validate_enum_write(spec, args["option"])}])
        ident.update(kind="enum", options=list(spec.range))
        return mk("sensor", lambda st: {"native_value": ops.validate_enum_read(spec, st.get(code))})
    if kind == STRING:
        ident.update(kind="text", entity_category="diagnostic")   # opaque (schedules, blobs): never writable
        return mk("sensor", lambda st: {"native_value": _text(st.get(code))})
    if kind == BITMAP:
        ident.update(kind="integer", entity_category="diagnostic")
        return mk("sensor", lambda st: {"native_value": st.get(code) if isinstance(st.get(code), int) else None})
    if kind in (RAW, JSON):
        ident.update(kind="text", entity_category="diagnostic")
        return mk("sensor", lambda st: {"native_value": _text(st.get(code))})
    return None
