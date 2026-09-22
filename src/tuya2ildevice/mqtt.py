"""Sans-IO MQTT mapping for  rustuya-bridge <-> tuya2ildevice <-> il-ha.

    bridge broker                          this module                          il broker
    rustuya/event/{type}/{id}   -->  Hub.on_bridge()  -->  TuyaDriver  -->  il/<id>, il/<id>/<prop>, ...
    rustuya/command  <--  set/get    Hub.on_il()      <--  il/<id>/<prop>/set

`Hub` owns one `TuyaDriver` per device and speaks the bridge's topics on one side and il-mqtt.md on the other.
It performs no I/O: every method takes what was received and returns `Publish`es. Each `Publish` and subscription
names its `side` ("bridge" or "il"), so the host may use two connections or one broker for both.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .driver import TuyaDriver
from .io import (Absent, CancelTimer, Command, Connected, Descriptor, Disconnected, Event, Message, Reject, SendMessage,
                 SetTimer, Timer, Value)

BRIDGE, IL = "bridge", "il"


@dataclass(frozen=True)
class Publish:
    side: str
    topic: str
    payload: str
    retain: bool = False
    qos: int = 1


@dataclass(frozen=True)
class Subscribe:
    side: str
    topic: str
    qos: int = 1


@dataclass(frozen=True)
class Schedule:
    """Ask the host to call `Hub.on_timer(now, device_id, name)` after `after` seconds (replacing an equal name)."""
    device_id: str
    name: str
    after: float


@dataclass(frozen=True)
class Unschedule:
    device_id: str
    name: str


# --- rustuya-bridge side ---------------------------------------------------------------------------
class BridgeTopics:
    """Topic layout of rustuya-bridge (defaults; pass the bridge's own templates if they were changed).

    `event`: ``{root}/event/{type}/{id}`` (a ``{dp}`` in it means single-DP mode: the payload is one value).
    `message`: ``{root}/{level}/{id}``; ``error`` carries ``{"errorCode": 0}`` for *connected*, non-zero for a fault.
    """

    def __init__(self, root: str = "rustuya", event: str | None = None, message: str | None = None,
                 command: str | None = None):
        self.root = root
        self.event = (event or "{root}/event/{type}/{id}").replace("{root}", root)
        self.message = (message or "{root}/{level}/{id}").replace("{root}", root)
        self.command = (command or "{root}/command").replace("{root}", root)
        self._event_re = self._regex(self.event, {"type": r"(?P<type>active|passive|state)"})
        self._error_re = self._regex(self.message.replace("{level}", "error"), {})

    @classmethod
    def from_config(cls, config: dict, root: str = "rustuya") -> "BridgeTopics":
        """The layout a running bridge announces (its retained ``{root}/bridge/config``: ``mqtt_root_topic``,
        ``mqtt_event_topic``, ``mqtt_message_topic``, ``mqtt_command_topic``). What the bridge says wins over `root`."""
        return cls(config.get("mqtt_root_topic") or root, event=config.get("mqtt_event_topic"),
                   message=config.get("mqtt_message_topic"), command=config.get("mqtt_command_topic"))

    @staticmethod
    def _regex(template: str, special: dict[str, str]) -> re.Pattern:
        out, pos = "", 0
        for m in re.finditer(r"\{(\w+)\}", template):
            out += re.escape(template[pos:m.start()])
            name = m.group(1)
            out += special.get(name, f"(?P<{name}>[^/]+)")
            pos = m.end()
        return re.compile(out + re.escape(template[pos:]) + r"\Z")

    def subscriptions(self) -> list[str]:
        wild = lambda t: re.sub(r"\{\w+\}", "+", t)
        return [wild(self.event), wild(self.message.replace("{level}", "error"))]

    def parse(self, topic: str, payload: bytes | str, retained: bool) -> tuple[str, Any] | None:
        """(device id, driver input) for an event or an error message, else None."""
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", "replace")
        if m := self._event_re.match(topic):
            g = m.groupdict()
            if g["type"] != "state" and retained:
                return None                                   # a replayed delta is not a new event
            try:
                body: Any = json.loads(payload)
            except ValueError:
                body = payload
            if g.get("dp"):                                   # single-DP mode
                body = {"dps": {g["dp"]: body}}
            return g["id"], Message(g["type"], body)
        if m := self._error_re.match(topic):
            try:
                code = json.loads(payload).get("errorCode")
            except (ValueError, AttributeError):
                return None
            return m.group("id"), (Connected() if code == 0 else Disconnected())
        return None

    def set(self, device_id: str, dps: dict) -> Publish:
        return Publish(BRIDGE, self.command, json.dumps({"action": "set", "id": device_id, "dps": dps}), False, 1)

    def get(self, device_id: str) -> Publish:
        return Publish(BRIDGE, self.command, json.dumps({"action": "get", "id": device_id}), False, 1)


# --- il-mqtt.md side -------------------------------------------------------------------------------
class IlTopics:
    def __init__(self, prefix: str = "il", source: str = "tuya"):
        self.prefix, self.source = prefix, source

    def descriptor(self, id: str) -> str:
        return f"{self.prefix}/{id}"

    def state(self, id: str, prop: str) -> str:
        return f"{self.prefix}/{id}/{prop}"

    def reject(self, id: str) -> str:
        return f"{self.prefix}/{id}/reject"

    @property
    def presence(self) -> str:
        return f"{self.prefix}/_producer/{self.source}"

    def set_subscription(self) -> str:
        return f"{self.prefix}/+/+/set"

    def parse_set(self, topic: str) -> tuple[str, str] | None:
        head = self.prefix + "/"
        if not topic.startswith(head):
            return None
        parts = topic[len(head):].split("/")
        if len(parts) == 3 and parts[2] == "set" and not parts[0].startswith("_"):
            return parts[0], parts[1]
        return None


def encode_value(value: Any) -> str:
    """il-mqtt.md section 3: no JSON quoting of strings."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = int(value)                                    # an integral value has no fraction
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return str(value)


def decode_write(ptype: str, payload: str) -> Any:
    """A `set` payload -> a value for `Command`. The driver's checks refuse what does not fit."""
    if ptype == "trigger":
        return None
    if ptype == "number":
        try:
            return json.loads(payload)
        except ValueError:
            return payload
    return payload


# --- the hub ---------------------------------------------------------------------------------------
class Hub:
    """One driver per Tuya device id. `devices`: tuyadevices.json entries. Hosts call `start()` once, then feed
    `on_bridge` / `on_il` and execute the returned `Publish`es. `now` is whatever clock the host reads."""

    def __init__(self, devices: list[dict], *, bridge: BridgeTopics | None = None, il: IlTopics | None = None,
                 **driver_kwargs: Any):
        self.bridge = bridge or BridgeTopics()
        self.il = il or IlTopics()
        self._devices = {d["id"]: d for d in devices}
        self._kw = driver_kwargs
        self.drivers = {i: TuyaDriver(d, **driver_kwargs) for i, d in self._devices.items()}
        for i in self.drivers:
            self._check_id(i)

    @property
    def records(self) -> dict[str, dict]:
        """The device records the Hub drives now, by id (a copy of the mapping; the records are shared)."""
        return dict(self._devices)

    def _check_id(self, i: str) -> None:
        if not i or i[0] == "_" or any(c in i for c in "/+#\0"):
            raise ValueError(f"device id {i!r} is not usable as an il-mqtt topic level (M-2)")

    def set_device(self, device: dict) -> list:
        """Add a device, or replace the record of one that is known (a new schema, a new local key): the descriptor
        is published (properties it no longer has are cleared first, M-11) and, if the bridge link was up, the state is
        asked for again. Raises before changing anything if the record cannot be driven."""
        i = device["id"]
        self._check_id(i)
        new = TuyaDriver(device, **self._kw)
        old = self.drivers.get(i)
        pubs: list = []
        if old is not None:
            pubs += [Unschedule(i, n) for n in sorted(old.timers)]
            for prop in old.descriptor["props"].keys() - new.descriptor["props"].keys():
                pubs.append(Publish(IL, self.il.state(i, prop), "", True, 1))
        pubs += self._il_pubs(i, new.describe())
        if old is not None and old.linked:
            new.handle(0, Connected())
            pubs.append(self.bridge.get(i))
        self._devices[i] = device
        self.drivers[i] = new
        return pubs

    def remove_device(self, device_id: str) -> list:
        """M-11: clear every retained value, then the descriptor (`remove`). Unknown ids do nothing."""
        old = self.drivers.pop(device_id, None)
        self._devices.pop(device_id, None)
        if old is None:
            return []
        pubs: list = [Unschedule(device_id, n) for n in sorted(old.timers)]
        pubs += [Publish(IL, self.il.state(device_id, prop), "", True, 1) for prop in old.descriptor["props"]]
        pubs.append(Publish(IL, self.il.descriptor(device_id), "", True, 1))
        return pubs

    def reload(self, overrides: dict | None, converters: dict | None = None) -> list:
        """Apply new user overrides (see overrides.py). A device whose descriptor changed gets a fresh driver: its
        removed properties are cleared (M-11), the new descriptor is published, and the bridge is asked for its
        state again. Raises `OverrideError` (changing nothing) if the overrides are invalid."""
        kw = {**self._kw, "overrides": overrides}
        if converters is not None:
            kw["converters"] = converters
        fresh = {i: TuyaDriver(d, **kw) for i, d in self._devices.items()}          # all or nothing
        pubs: list = []
        for i, new in fresh.items():
            old = self.drivers[i]
            if new.descriptor == old.descriptor and not (old.timers or new.converters):
                continue
            pubs += [Unschedule(i, n) for n in sorted(old.timers)]
            for prop in old.descriptor["props"].keys() - new.descriptor["props"].keys():
                pubs.append(Publish(IL, self.il.state(i, prop), "", True, 1))
            pubs += self._il_pubs(i, new.describe())
            if old.linked:
                new.handle(0, Connected())
                pubs.append(self.bridge.get(i))
            self.drivers[i] = new
        self._kw = kw
        return pubs

    def subscriptions(self) -> list[Subscribe]:
        return [*(Subscribe(BRIDGE, t) for t in self.bridge.subscriptions()),
                Subscribe(IL, self.il.set_subscription())]

    def presence(self, online: bool) -> Publish:
        """M-12. Register `presence(False)` as the Last Will on the il connection."""
        return Publish(IL, self.il.presence, "online" if online else "offline", True, 1)

    def start(self) -> list[Publish]:
        """Presence, then every device's descriptor and `available: false` (retained), so il-ha sees them at once."""
        pubs = [self.presence(True)]
        for i, d in self.drivers.items():
            pubs += self._il_pubs(i, d.describe())
        return pubs

    def stop(self) -> list[Publish]:
        return [self.presence(False)]

    def on_bridge(self, now: float, topic: str, payload: bytes | str, retained: bool = False) -> list:
        parsed = self.bridge.parse(topic, payload, retained)
        if parsed is None or parsed[0] not in self.drivers:
            return []
        i, inp = parsed
        pubs = self._il_pubs(i, self.drivers[i].handle(now, inp))
        if isinstance(inp, Connected):
            pubs.append(self.bridge.get(i))                      # ask for the full state right after connecting
        return pubs

    def on_timer(self, now: float, device_id: str, name: str) -> list:
        d = self.drivers.get(device_id)
        return self._il_pubs(device_id, d.handle(now, Timer(name))) if d else []

    def on_il(self, now: float, topic: str, payload: bytes | str, retained: bool = False) -> list:
        loc = self.il.parse_set(topic)
        if loc is None or retained:                              # M-9: never replay an old write
            return []
        i, prop = loc
        d = self.drivers.get(i)
        if d is None:
            return []
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", "replace")
        ptype = d.descriptor["props"].get(prop, {}).get("type")
        return self._il_pubs(i, d.handle(now, Command(prop, decode_write(ptype, payload))))

    def _il_pubs(self, id: str, outs: list) -> list:
        pubs: list = []
        for o in outs:
            if isinstance(o, Descriptor):
                pubs.append(Publish(IL, self.il.descriptor(id), json.dumps(o.desc, ensure_ascii=False), True, 1))
            elif isinstance(o, Value):
                pubs.append(Publish(IL, self.il.state(id, o.prop), encode_value(o.value), True, 1))
            elif isinstance(o, Absent):
                pubs.append(Publish(IL, self.il.state(id, o.prop), "", True, 1))
            elif isinstance(o, Event):
                pubs.append(Publish(IL, self.il.state(id, o.prop), o.kind, False, 1))
            elif isinstance(o, Reject):
                pubs.append(Publish(IL, self.il.reject(id), json.dumps({"prop": o.prop, "code": o.code,
                                                                        "reason": o.reason}), False, 1))
            elif isinstance(o, SetTimer):
                pubs.append(Schedule(id, o.name, o.after))
            elif isinstance(o, CancelTimer):
                pubs.append(Unschedule(id, o.name))
            elif isinstance(o, SendMessage) and o.channel == "set":
                pubs.append(self.bridge.set(id, o.json["dps"]))
        return pubs
