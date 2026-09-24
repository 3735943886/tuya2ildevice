"""`TuyaDriver`: the sans-IO state machine for one Tuya device (il.md section 8).

    driver = TuyaDriver(device)              # a tuyadevices.json / Tuya cloud device dict
    driver.descriptor                        # the ildevice descriptor
    outs = driver.handle(now, Connected())
    outs = driver.handle(now, Message("active", {"1": True}))            # -> [Value("switch_1", True)]
    outs = driver.handle(now, Command("switch_1", "off"))                # -> [SendMessage("set", {"dps": {"1": False}})]

It reads no clock and does no I/O; the host feeds it already-decoded dp:value maps and carries out what it returns.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any

from .tuya import platforms  # noqa: F401  (registers the platform builders)
from .tuya.adapter import Adapter
from .tuya.model import DeviceSchema, DpSpec, SchemaError
from .tuya.quirks import apply_quirk, apply_status_quirk, device_info, quirk_for
from .tuya.runtime import ActionDPCodeNotFound, HostEnv, WriteRejected, classify, on_update

from .assemble import UNSUPPORTED, Assembly, assemble
from . import overrides as ov
from .converters import BUILTIN, Converter, as_result
from .fallback import unused_plans
from .checks import Rejected, check_command
from .io import (Absent, Command, Connected, Descriptor, Disconnected, Event, Message, Reject, SendMessage, SetTimer,
                 CancelTimer, Timer, Value)

_HOST_UNITS = json.loads((pathlib.Path(__file__).parent / "tuya" / "data" / "host_units.json").read_text())


def default_env() -> HostEnv:
    """Home Assistant's per-device-class allowed units, which the v2 unit policy needs (spec 5.3)."""
    return HostEnv(allowed_units=_HOST_UNITS)


def schema_of(device: dict) -> DeviceSchema:
    def specs(m: dict | None, with_report: bool) -> dict[str, DpSpec]:
        return {k: DpSpec(k, v["type"], v.get("values"), v.get("report_type") if with_report else None)
                for k, v in (m or {}).items()}
    return DeviceSchema(device["id"], device.get("category", ""), device.get("product_id", ""), device.get("name", ""),
                        device.get("product_name", ""), bool(device.get("online", True)),
                        specs(device.get("function"), False), specs(device.get("status_range"), True),
                        dict(device.get("status") or {}))


def _dps_of(payload: Any) -> tuple[dict[str, Any], Any]:
    """(dps, t): `payload` is already a flat `{dp: value}` map (the host decoded whatever envelope the bridge's
    wire payload used); `t` is Tuya's own event timestamp, mixed into that same flat map, not a wrapper key."""
    if not isinstance(payload, dict):
        return {}, None
    return {str(k): v for k, v in payload.items() if k != "t"}, payload.get("t")


def descriptor_of(device: dict, **kwargs: Any) -> dict:
    """The ildevice descriptor of a Tuya device record (what `TuyaDriver(device).descriptor` is)."""
    return TuyaDriver(device, **kwargs).descriptor


class TuyaDriver:
    """`device`: id, category, product_id, name, function, status_range, local_strategy, status (Tuya cloud record).
    `dpmap`: extra ``{dp id: code}`` for a device that has no `local_strategy`.
    `overrides`: user overrides (see `overrides.py`), a ``{product_id or device id: block}`` mapping.
    `expose_unused`: give every dp no platform table claimed a property of its own (as rustuya-homeassistant v1 did); off by default, which is what Home Assistant core's tuya integration does.
    `allow_hazardous`: also offer writes for a garage door or gate cover (il.md S-1); off by default.
    `converter_types`: extra named converters (``{name: factory(config) -> Converter}``, e.g. from a user's `.py`
    files) that an override block can name in `converters`, next to the built-in ones.
    `use_quirks`: the built-in quirks and the built-in overrides (`overrides.BUILTIN`); on by default."""

    def __init__(self, device: dict, *, env: HostEnv | None = None, use_quirks: bool = True,
                 dpmap: dict[str, str] | None = None, allow_hazardous: bool = False,
                 expose_unused: bool = False, overrides: dict | None = None,
                 converters: dict | None = None, converter_types: dict | None = None):
        schema = schema_of(device)
        quirk = quirk_for(schema.product_id) if use_quirks else None
        if quirk:
            schema = apply_quirk(schema, quirk)
            schema.status = apply_status_quirk(quirk, schema.status)
        types = {**BUILTIN, **(converter_types or {})}
        block = ov.find(overrides, device, types, builtin=use_quirks)
        dpcodes = {**{str(k): v for k, v in schema.dpmap.items()}, **{str(k): v for k, v in (dpmap or {}).items()},
                   **Adapter.from_local_strategy(device.get("local_strategy") or {}).codes()}
        schema, removed = ov.patch_schema(schema, block, dpcodes)
        self.adapter = Adapter.from_local_strategy(device.get("local_strategy") or {}, schema.status_range)
        for dpid, code in {**{str(k): v for k, v in schema.dpmap.items()}, **(dpmap or {})}.items():
            self.adapter.entries.setdefault(str(dpid), (code, "default", {}))
        ov.patch_adapter(self.adapter, block, removed, schema)
        plans = classify(schema, env or default_env()).entities
        if block.get("expose_unused", expose_unused):
            used = {c for p in plans if p.platform not in UNSUPPORTED for c in p.depends_on}
            plans = plans + unused_plans(schema, self.adapter.entries, used)
        self.assembly: Assembly = assemble(schema, plans, device_info(schema, quirk), allow_hazardous=allow_hazardous)
        self.converters: list[Converter] = self._make_converters(device, block, converters, types)
        for conv in self.converters:
            for name, definition in conv.props().items():
                if name in self.assembly.descriptor["props"] or "rw" in definition:
                    raise ov.OverrideError(f"converter property {name!r}: taken, or not read only")
                self.assembly.descriptor["props"][name] = definition
        ov.patch_descriptor(self.assembly, block)
        self.descriptor: dict = self.assembly.descriptor
        self.unsupported: list[str] = self.assembly.unsupported
        self.timers: set[str] = set()                # names of the timers the converters have set
        self._codes: dict[str, Any] = {}
        self._values: dict[str, Any] = {}
        self._described = False
        self._linked = False
        self._synced = False
        self._seq = 0

    # -- the driver interface -------------------------------------------------
    def handle(self, now: float, inp: Any) -> list:
        if isinstance(inp, Connected):
            self._linked = True
            return self._describe()
        if isinstance(inp, Disconnected):
            return self._disconnect()
        if isinstance(inp, Message):
            return self._message(now, inp)
        if isinstance(inp, Command):
            return self._command(inp)
        if isinstance(inp, Timer):
            return self._timer(now, inp.name)
        return []

    # -- internals ------------------------------------------------------------
    @staticmethod
    def _make_converters(device: dict, block: dict, registered: dict | None, types: dict) -> list[Converter]:
        out: list[Converter] = []
        for key in (device.get("product_id"), device.get("id")):
            fs = (registered or {}).get(key) if key else None
            for f in (fs if isinstance(fs, (list, tuple)) else [fs] if fs else []):
                out.append(f(device))
        for name, cfg in (block.get("converters") or {}).items():
            try:
                out.append(types[name](cfg))
            except ValueError as e:
                raise ov.OverrideError(str(e)) from e
        return out

    @property
    def linked(self) -> bool:
        return self._linked

    def describe(self) -> list:
        """The descriptor and `available: false`, for a host that publishes them before the device link is up."""
        outs = self._describe()
        self._set("available", False, outs)
        return outs

    def _describe(self) -> list:
        if self._described:
            return []
        self._described = True
        return [Descriptor(self.descriptor)]

    def _disconnect(self) -> list:
        self._linked = self._synced = False
        self._codes.clear()
        outs: list = []
        for prop in list(self._values):
            if prop != "available":
                outs.append(Absent(prop))
        outs.append(Value("available", False))
        self._values = {"available": False}
        for conv in self.converters:
            conv.reset()
        outs += [CancelTimer(n) for n in sorted(self.timers)]
        self.timers.clear()
        return outs

    def _set(self, prop: str, value: Any, outs: list) -> None:
        if value is None:
            if prop in self._values:
                del self._values[prop]
                outs.append(Absent(prop))
        elif self._values.get(prop, _MISSING) != value:
            self._values[prop] = value
            outs.append(Value(prop, value))

    def _message(self, now: float, msg: Message) -> list:
        dps, t = _dps_of(msg.json)
        outs = self._describe()                     # R-2/R-3: a descriptor precedes the first value
        if not dps:
            return outs
        active = msg.channel == "active"
        new = self.adapter.read(dps)
        self._seq += 1
        ts = t if isinstance(t, int) else self._seq
        changed = list(new)
        self._codes.update(new)
        first = not self._synced
        if first:
            self._synced = True
            self._linked = True
            self._set("available", True, outs)      # A-2: true once the first state has arrived
        for idx, conv in enumerate(self.converters):
            self._converted(idx, conv.update(now, self._codes, changed, active), outs)
        for name, b in self.assembly.bindings.items():
            deps = b.plan.depends_on
            if b.event:
                if active and deps[0] in changed:
                    ev = on_update(b.plan, b.slot, changed, {c: ts for c in changed}, self._codes)
                    if ev.fire and ev.fire[0] in self.descriptor["props"][name]["options"]:
                        outs.append(Event(name, ev.fire[0]))
                continue
            if b.read is None:
                continue
            if b.plan.slot_kind == "delta":
                if active and deps[0] in changed:
                    res = on_update(b.plan, b.slot, changed, {c: ts for c in changed}, self._codes)
                    if res.write_state:
                        self._set(name, b.read(self._codes, b.slot), outs)
                elif first:
                    self._set(name, b.read(self._codes, b.slot), outs)
                continue
            if first or b.plan.update_all or any(c in changed for c in deps):
                self._set(name, b.read(self._codes, b.slot), outs)
        return outs

    def _converted(self, idx: int, result: Any, outs: list) -> None:
        r = as_result(result)
        for prop, value in r.values.items():
            if prop in self.descriptor["props"]:            # an override may have hidden it
                self._set(prop, value, outs)
        for name, after in r.timers.items():
            full = f"c{idx}:{name}"
            if after is None:
                if full in self.timers:
                    self.timers.discard(full)
                    outs.append(CancelTimer(full))
            else:
                self.timers.add(full)
                outs.append(SetTimer(full, float(after)))

    def _timer(self, now: float, name: str) -> list:
        outs: list = []
        head, _, short = name.partition(":")
        if head[:1] == "c" and head[1:].isdigit() and int(head[1:]) < len(self.converters):
            self.timers.discard(name)
            self._converted(int(head[1:]), self.converters[int(head[1:])].timer(now, short, self._codes), outs)
        return outs

    def _command(self, cmd: Command) -> list:
        try:
            value = check_command(self.descriptor, self._values, cmd.prop, cmd.value)
        except Rejected as e:
            return [Reject(cmd.prop, e.code, e.reason)]
        b = self.assembly.bindings.get(cmd.prop)
        if b is None or b.write is None:
            return [Reject(cmd.prop, "unsupported", "no write path")]
        if not self._linked:
            return [Reject(cmd.prop, "unavailable", "device link is down")]
        try:
            commands = b.write(value, self._codes)
            dps, missing = self.adapter.write(commands)
        except ActionDPCodeNotFound as e:
            return [Reject(cmd.prop, "unsupported", str(e))]
        except (WriteRejected, SchemaError, KeyError, TypeError) as e:
            return [Reject(cmd.prop, "invalid_value", str(e))]
        if missing or not dps:
            return [Reject(cmd.prop, "unsupported", f"no dp for {missing or 'command'}")]
        return [SendMessage("set", {"dps": dps})]


_MISSING = object()
