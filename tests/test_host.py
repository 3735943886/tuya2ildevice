"""The host around the Hub: Runner on the IL transport, the device file, and paho against a real broker (mosquitto,
skipped when it is not installed). Runner no longer touches rustuya-bridge's own MQTT wire — that's the host's job
now (a `pyrustuyabridge`-based bridge client, e.g. in rustuya-local); here it's simulated with `runner.on_bridge_message`
and `BridgeCommand` outputs are captured via the injected `on_bridge_command` callback."""
import asyncio
import json
import shutil
import socket
import subprocess
import time

import pytest
from helpers import curtain, light

from tuya2ildevice import BridgeCommand, Connected, Hub, IlTopics, Message
from tuya2ildevice.host import DeviceWatcher, InProcessTransport, OverrideWatcher, Runner, load_overrides, parse_devices
from tuya2ildevice.host.memory import matches

def lamp(dev_id):
    d = light()
    d["id"] = dev_id
    return d


# ---- transports ------------------------------------------------------------------------------------------------------
def test_topic_filters_are_mqtt_filters():
    assert matches("il/+", "il/d1") and not matches("il/+", "il/d1/x") and matches("il/#", "il/d1/x")
    assert not matches("il/+/x", "il/d1") and matches("a/b", "a/b") and not matches("a/b", "a/c")


async def test_retained_messages_replay_to_a_late_subscriber_and_empty_clears():
    bus, seen = InProcessTransport(), []
    await bus.publish("a/b", "1", 1, True)
    await bus.subscribe("a/+", lambda m: seen.append((m.topic, m.payload, m.retain)))
    await bus.publish("a/c", "2", 1, False)
    assert seen == [("a/b", "1", True), ("a/c", "2", False)]
    await bus.publish("a/b", "", 1, True)
    assert "a/b" not in bus.retained


# ---- the runner ------------------------------------------------------------------------------------------------------
@pytest.fixture
async def running():
    il = InProcessTransport()
    hub = Hub([lamp("lamp1")], il=IlTopics("il", "tuya"))
    commands: list[BridgeCommand] = []
    runner = Runner(hub, il, on_bridge_command=commands.append)
    await runner.start()
    await runner.drain()
    yield il, runner, hub, commands
    await runner.stop()


async def test_start_publishes_presence_and_descriptors_and_values_flow_both_ways(running):
    il, runner, hub, commands = running
    assert il.retained["il/_producer/tuya"].payload == "online"
    assert json.loads(il.retained["il/lamp1"].payload)["kind"] == "light"

    runner.on_bridge_message("lamp1", Connected())
    runner.on_bridge_message("lamp1", Message("state", {"20": True, "22": 1000, "23": 0}))
    await runner.drain()
    assert il.retained["il/lamp1/brightness"].payload == "100"
    assert BridgeCommand("lamp1", "get") in commands

    commands.clear()
    await il.publish("il/lamp1/brightness/set", "50", 1)
    await runner.drain()
    assert BridgeCommand("lamp1", "set", {"20": True, "22": 507}) in commands

    await il.publish("il/lamp1/brightness/set", "500", 1)
    await runner.drain()
    assert json.loads(il.published[-1][1])["code"] == "out_of_range" and il.published[-1][0] == "il/lamp1/reject"


async def test_stop_is_idempotent_and_publishes_offline(running):
    il, runner, _, _ = running
    await runner.stop()
    await runner.stop()
    assert il.published[-1][:2] == ("il/_producer/tuya", "offline")


async def test_sync_adds_changes_and_removes_and_clears_before_removing(running):
    il, runner, hub, _ = running
    runner.set_device(lamp("gone"))
    runner.set_device(lamp("edit"))
    await runner.drain()
    edited = lamp("edit")
    edited["name"] = "Renamed"
    done = runner.sync_devices([lamp("lamp1"), edited, lamp("new")])
    assert done == {"added": ["new"], "changed": ["edit"], "removed": ["gone"], "failed": []}
    await runner.drain()
    assert set(hub.records) == {"lamp1", "edit", "new"} and not [t for t in il.retained if t.startswith("il/gone")]
    assert json.loads(il.retained["il/edit"].payload)["label"] == "Renamed"
    bad = lamp("bad")
    bad["id"] = "_reserved"
    assert runner.sync_devices([lamp("lamp1"), bad])["failed"] == ["_reserved"]


# ---- the device file --------------------------------------------------------------------------------------------------
def test_a_list_or_a_dict_of_records_and_the_ones_that_cannot_be_driven():
    records = [lamp("a"), {"id": "nocat", "name": "x"}, {"name": "noid"}, "junk"]
    for raw in (json.dumps(records), json.dumps({str(i): r for i, r in enumerate(records)})):
        usable, skipped = parse_devices(raw)
        assert [r["id"] for r in usable] == ["a"] and skipped == ["nocat"]
    with pytest.raises(ValueError):
        parse_devices('"nope"')


async def test_the_watcher_follows_the_file_and_keeps_devices_when_it_cannot_be_read(running, tmp_path):
    _, runner, hub, _ = running
    path = tmp_path / "tuyadevices.json"
    path.write_text(json.dumps([lamp("lamp1")]))
    watcher = DeviceWatcher(path, runner, interval=0.01)
    assert watcher.check() and set(hub.records) == {"lamp1"}
    assert not watcher.check()
    path.write_text("{ half written")
    assert watcher.check() and set(hub.records) == {"lamp1"}
    path.write_text(json.dumps([lamp("lamp1"), lamp("late")]))
    assert watcher.check() and set(hub.records) == {"lamp1", "late"}


CONVERTER_PY = """
from tuya2ildevice import Converter

class Lit(Converter):
    def __init__(self, cfg):
        self.word = cfg.get("word", "on")

    def props(self):
        return {"lit": {"type": "text"}}

    def update(self, now, codes, changed, active):
        return {"lit": self.word if codes.get("switch_led") else "off"}

CONVERTERS = {"lit": Lit}
"""


def test_an_overrides_directory_loads_json_v1_json_and_py(tmp_path):
    (tmp_path / "10_base.json").write_text(json.dumps({"p": {"device": {"model": "A"}, "converters": {"lit": {}}}}))
    (tmp_path / "20_v1.json").write_text(json.dumps({"q": {"model": "Old", "discovery_overrides": {"light": {}}}}))
    (tmp_path / "99_local.json").write_text(json.dumps({"p": {"device": {"model": "B"}}}))
    (tmp_path / "lit.py").write_text(CONVERTER_PY)
    (tmp_path / "old_curtain.py").write_text("def setup(api):\n    pass\n")
    (tmp_path / "broken.py").write_text("raise RuntimeError('boom')\n")
    (tmp_path / "manifest.json").write_text("not even json")
    loaded = load_overrides(tmp_path)
    assert loaded.overrides == {"p": {"device": {"model": "B"}, "converters": {"lit": {}}},
                                "q": {"device": {"model": "Old"}}}
    assert set(loaded.converter_types) == {"lit"}
    warned = " ".join(loaded.warnings)
    assert "old_curtain.py" in warned and "v1 plugin" in warned and "broken.py: RuntimeError" in warned
    assert "20_v1.json" in warned and "manifest" not in warned
    assert load_overrides(tmp_path / "missing").overrides == {}


async def test_the_override_watcher_reloads_the_hub(running, tmp_path):
    il, runner, hub, _ = running
    watcher = OverrideWatcher(tmp_path, runner, interval=0.01, base={"lamp1": {"device": {"label": "Desk"}}})
    (tmp_path / "lit.py").write_text(CONVERTER_PY)
    (tmp_path / "a.json").write_text(json.dumps({"p": {"converters": {"lit": {"word": "bright"}}}}))
    assert watcher.check()
    await runner.drain()
    desc = json.loads(il.retained["il/lamp1"].payload)
    assert "lit" in desc["props"] and desc["label"] == "Desk"
    runner.on_bridge_message("lamp1", Connected())
    runner.on_bridge_message("lamp1", Message("state", {"20": True}))
    await runner.drain()
    assert il.retained["il/lamp1/lit"].payload == "bright"
    assert not watcher.check()
    (tmp_path / "a.json").write_text(json.dumps({"p": {"converters": {"nope": {}}}}))      # refused: the old one stays
    assert watcher.check()
    await runner.drain()
    assert "lit" in json.loads(il.retained["il/lamp1"].payload)["props"]
    (tmp_path / "a.json").unlink()
    assert watcher.check()
    await runner.drain()
    assert "lit" not in json.loads(il.retained["il/lamp1"].payload)["props"]


# ---- paho against a real broker --------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def broker():
    exe = shutil.which("mosquitto")
    pytest.importorskip("paho.mqtt.client")
    if exe is None:
        pytest.skip("mosquitto is not installed")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([exe, "-p", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield port
    proc.terminate()
    proc.wait(5)


async def until(cond, timeout=5.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        assert asyncio.get_running_loop().time() < end, "condition not reached in time"
        await asyncio.sleep(0.02)


async def test_paho_transport_runs_the_runner_survives_a_dropped_connection_and_fires_the_last_will(broker):
    from tuya2ildevice.host import MqttTransport
    hub = Hub([lamp("lamp1")], il=IlTopics("il", "tuya"))
    will = hub.presence(False)
    il_side = MqttTransport("127.0.0.1", broker, client_id="t-il", will=(will.topic, will.payload, will.qos, will.retain))
    sim = MqttTransport("127.0.0.1", broker, client_id="t-sim")
    for t in (il_side, sim):
        await t.connect()
    seen: dict[str, str] = {}
    commands: list[BridgeCommand] = []
    await sim.subscribe("il/#", lambda m: seen.__setitem__(m.topic, m.payload.decode()))
    runner = Runner(hub, il_side, on_bridge_command=commands.append)
    await runner.start()
    try:
        await until(lambda: seen.get("il/_producer/tuya") == "online" and "il/lamp1" in seen)
        runner.on_bridge_message("lamp1", Connected())
        await until(lambda: BridgeCommand("lamp1", "get") in commands)
        runner.on_bridge_message("lamp1", Message("state", {"20": True, "22": 1000, "23": 0}))
        await until(lambda: seen.get("il/lamp1/brightness") == "100")
        await sim.publish("il/lamp1/brightness/set", "50", 1)
        await until(lambda: any(c.action == "set" for c in commands))
        assert [c for c in commands if c.action == "set"] == [BridgeCommand("lamp1", "set", {"20": True, "22": 507})]

        seen.pop("il/_producer/tuya")
        il_side._client._sock.close()                       # an unclean drop: the broker publishes the Last Will
        await until(lambda: seen.get("il/_producer/tuya") == "offline")
        await until(lambda: seen.get("il/_producer/tuya") == "online")      # paho reconnects; the runner republishes
    finally:
        await runner.stop()
        await il_side.close()
        for topic in list(seen):
            await sim.publish(topic, "", 1, True)
        await asyncio.sleep(0.1)
        await sim.close()


async def test_a_failed_connect_stops_its_own_thread():
    """A timed-out connect() must not leave the paho loop thread running (the caller may just drop the object)."""
    import threading

    from tuya2ildevice.host import MqttTransport

    before = {t.name for t in threading.enumerate()}
    t = MqttTransport("127.0.0.1", 1, client_id="dead-end")   # nothing listens on port 1
    with pytest.raises((asyncio.TimeoutError, asyncio.CancelledError)):   # asyncio.TimeoutError != TimeoutError before 3.11
        await t.connect(timeout=0.3)
    after = {t.name for t in threading.enumerate()}
    assert not (after - before), f"leaked thread(s): {after - before}"
