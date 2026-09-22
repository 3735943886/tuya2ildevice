"""Run a `tuya2ildevice.Hub` on the IL transport (il-ha or any IL consumer). The Hub is sans-IO; this is where its
`Publish`, `Schedule` and `Unschedule` outputs happen. It knows nothing about rustuya-bridge's own MQTT wire, either
— Hub's bridge-facing `BridgeCommand` outputs (asking the bridge to read/write a device) are handed to
`on_bridge_command`, supplied by whatever owns the real bridge connection (e.g. rustuya-local's bridge client)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from .transport import Message, Transport, Unsubscribe
from ..io import Connected, Disconnected
from ..io import Message as DriverInput
from ..mqtt import BridgeCommand, Hub, Publish, Schedule, Unschedule

_LOGGER = logging.getLogger(__name__)


class Runner:
    def __init__(self, hub: Hub, il: Transport, clock: Callable[[], float] = time.time,
                 on_bridge_command: Callable[[BridgeCommand], None] | None = None) -> None:
        self.hub = hub
        self.il = il
        self.clock = clock
        self._on_bridge_command = on_bridge_command
        self._unsubs: list[Unsubscribe] = []
        self._timers: dict[tuple[str, str], asyncio.TimerHandle] = {}
        self._out: asyncio.Queue = asyncio.Queue()
        self._worker: asyncio.Task | None = None

    async def start(self) -> None:
        """Subscribe on the IL side, publish presence and every descriptor (retained)."""
        self._worker = asyncio.ensure_future(self._publish_loop())
        for sub in self.hub.subscriptions():
            self._unsubs.append(await self.il.subscribe(sub.topic, self._on_il, qos=sub.qos))
        hooks = getattr(self.il, "on_connect", None)
        if hooks is not None:
            hooks.append(self._reconnected)
        self.run(self.hub.start())

    async def stop(self) -> None:
        """Presence goes offline, the queue drains, then subscriptions and timers are released."""
        if self._worker is None:                   # not started, or already stopped
            return
        self.run(self.hub.stop())
        await self.drain()
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        for handle in self._timers.values():
            handle.cancel()
        self._timers.clear()
        if self._worker:
            self._worker.cancel()
            self._worker = None

    async def drain(self) -> None:
        await self._out.join()

    # ---- runtime device changes (each returns nothing; the Hub's outputs are executed) --------------

    def set_device(self, device: dict) -> None:
        self.run(self.hub.set_device(device))

    def remove_device(self, device_id: str) -> None:
        self.run(self.hub.remove_device(device_id))

    def sync_devices(self, records: list[dict]) -> dict[str, list[str]]:
        """Make the driven devices exactly `records`: add the new, replace the changed, remove the gone. A record the
        Hub cannot drive is reported and skipped, the others go on. Returns what was done, by kind."""
        wanted = {r["id"]: r for r in records}
        have = self.hub.records
        done: dict[str, list[str]] = {"added": [], "changed": [], "removed": [], "failed": []}
        for device_id in have.keys() - wanted.keys():
            self.remove_device(device_id)
            done["removed"].append(device_id)
        for device_id, record in wanted.items():
            if have.get(device_id) == record:
                continue
            try:
                self.set_device(record)
            except Exception:
                _LOGGER.exception("cannot drive device %s", device_id)
                done["failed"].append(device_id)
                continue
            done["changed" if device_id in have else "added"].append(device_id)
        return done

    def reload(self, overrides: dict | None, converters: dict | None = None) -> None:
        self.run(self.hub.reload(overrides, converters))

    # ---- plumbing ----------------------------------------------------------------------

    def run(self, outs: list) -> None:
        for p in outs:
            if isinstance(p, Schedule):
                self._cancel(p.device_id, p.name)
                self._timers[(p.device_id, p.name)] = asyncio.get_running_loop().call_later(
                    p.after, self._fire, p.device_id, p.name)
            elif isinstance(p, Unschedule):
                self._cancel(p.device_id, p.name)
            elif isinstance(p, BridgeCommand):
                if self._on_bridge_command is not None:
                    self._on_bridge_command(p)
            else:
                self._out.put_nowait(p)

    def _cancel(self, device_id: str, name: str) -> None:
        if handle := self._timers.pop((device_id, name), None):
            handle.cancel()

    def _fire(self, device_id: str, name: str) -> None:
        self._timers.pop((device_id, name), None)
        self._guard(lambda: self.hub.on_timer(self.clock(), device_id, name))

    def _on_il(self, msg: Message) -> None:
        self._guard(lambda: self.hub.on_il(self.clock(), msg.topic, msg.payload, msg.retain))

    def on_bridge_message(self, device_id: str, inp: Connected | Disconnected | DriverInput,
                           retained: bool = False) -> None:
        """The bridge client (whoever decoded a real rustuya-bridge message into one of these — e.g. rustuya-local's
        `pyrustuyabridge`-based bridge client) calls this instead of touching `hub`/`run` directly, so a bad message
        is isolated the same way `_on_il` isolates one (see `_guard`)."""
        self._guard(lambda: self.hub.on_bridge_message(self.clock(), device_id, inp, retained))

    def _guard(self, produce: Callable[[], list]) -> None:
        try:
            self.run(produce())
        except Exception:  # a bad message must not stop the runner
            _LOGGER.exception("could not handle a message")

    def _reconnected(self) -> None:
        self.run(self.hub.start())                 # a Last Will took the presence away; descriptors are retained anyway

    async def _publish_loop(self) -> None:
        while True:
            p: Publish = await self._out.get()
            try:
                await self.il.publish(p.topic, p.payload, p.qos, p.retain)
            except Exception:
                _LOGGER.exception("publish to %s failed", p.topic)
            finally:
                self._out.task_done()
