"""The host around the Hub: Runner on in-process transports, the device file, the bridge's own config, and paho against a
real broker (mosquitto, skipped when it is not installed)."""
import asyncio
import json
import shutil
import socket
import subprocess
import time

import pytest
from helpers import curtain, light

from tuya2ildevice import BridgeTopics, Hub, IlTopics
from tuya2ildevice.host import DeviceWatcher, InProcessTransport, Runner, parse_devices, read_bridge_config
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
    bridge, il = InProcessTransport(), InProcessTransport()
    hub = Hub([lamp("lamp1")], bridge=BridgeTopics("rustuya"), il=IlTopics("il", "tuya"))
    runner = Runner(hub, bridge, il)
    await runner.start()
    await runner.drain()
    yield bridge, il, runner, hub
    await runner.stop()


async def test_start_publishes_presence_and_descriptors_and_values_flow_both_ways(running):
    bridge, il, runner, hub = running
    assert il.retained["il/_producer/tuya"].payload == "online"
    assert json.loads(il.retained["il/lamp1"].payload)["kind"] == "light"

    await bridge.publish("rustuya/error/lamp1", '{"errorCode":0,"errorMsg":"Connection Successful"}', 0, True)
    await bridge.publish("rustuya/event/state/lamp1", '{"20":true,"22":1000,"23":0}', 0, True)
    await runner.drain()
    assert il.retained["il/lamp1/brightness"].payload == "100"
    assert ("rustuya/command", '{"action": "get", "id": "lamp1"}') in [p[:2] for p in bridge.published]

    bridge.published.clear()
    await il.publish("il/lamp1/brightness/set", "50", 1)
    await runner.drain()
    (_, payload, *_), = [p for p in bridge.published if p[0] == "rustuya/command"]
    assert json.loads(payload) == {"action": "set", "id": "lamp1", "dps": {"20": True, "22": 507}}

    await il.publish("il/lamp1/brightness/set", "500", 1)
    await runner.drain()
    assert json.loads(il.published[-1][1])["code"] == "out_of_range" and il.published[-1][0] == "il/lamp1/reject"


async def test_stop_is_idempotent_and_publishes_offline(running):
    _, il, runner, _ = running
    await runner.stop()
    await runner.stop()
    assert il.published[-1][:2] == ("il/_producer/tuya", "offline")


async def test_sync_adds_changes_and_removes_and_clears_before_removing(running):
    bridge, il, runner, hub = running
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


# ---- the device file and the bridge's config -------------------------------------------------------------------------
def test_a_list_or_a_dict_of_records_and_the_ones_that_cannot_be_driven():
    records = [lamp("a"), {"id": "nocat", "name": "x"}, {"name": "noid"}, "junk"]
    for raw in (json.dumps(records), json.dumps({str(i): r for i, r in enumerate(records)})):
        usable, skipped = parse_devices(raw)
        assert [r["id"] for r in usable] == ["a"] and skipped == ["nocat"]
    with pytest.raises(ValueError):
        parse_devices('"nope"')


async def test_the_watcher_follows_the_file_and_keeps_devices_when_it_cannot_be_read(running, tmp_path):
    _, _, runner, hub = running
    path = tmp_path / "tuyadevices.json"
    path.write_text(json.dumps([lamp("lamp1")]))
    watcher = DeviceWatcher(path, runner, interval=0.01)
    assert watcher.check() and set(hub.records) == {"lamp1"}
    assert not watcher.check()
    path.write_text("{ half written")
    assert watcher.check() and set(hub.records) == {"lamp1"}
    path.write_text(json.dumps([lamp("lamp1"), lamp("late")]))
    assert watcher.check() and set(hub.records) == {"lamp1", "late"}


def test_the_bridges_own_topic_templates_win_over_the_default_root():
    t = BridgeTopics.from_config({"mqtt_root_topic": "tuya", "mqtt_event_topic": "{root}/ev/{type}/{id}",
                                  "mqtt_command_topic": "{root}/cmd"}, "rustuya")
    assert (t.root, t.event, t.command) == ("tuya", "tuya/ev/{type}/{id}", "tuya/cmd")
    d = BridgeTopics.from_config({}, "rustuya")
    assert (d.root, d.event, d.command) == ("rustuya", "rustuya/event/{type}/{id}", "rustuya/command")


async def test_the_bridge_config_is_read_once_published_and_absence_times_out():
    bus = InProcessTransport()
    assert await read_bridge_config(bus, "rustuya", timeout=0.05) is None
    await bus.publish("rustuya/bridge/config", '{"mqtt_root_topic": "rustuya", "version": "0.4"}', 1, True)
    assert (await read_bridge_config(bus, "rustuya", timeout=1))["version"] == "0.4"
    await bus.publish("other/bridge/config", "not json", 1, True)
    assert await read_bridge_config(bus, "other", timeout=0.05) is None


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
    hub = Hub([lamp("lamp1")], bridge=BridgeTopics("rustuya"), il=IlTopics("il", "tuya"))
    will = hub.presence(False)
    bridge_side = MqttTransport("127.0.0.1", broker, client_id="t-bridge")
    il_side = MqttTransport("127.0.0.1", broker, client_id="t-il", will=(will.topic, will.payload, will.qos, will.retain))
    sim = MqttTransport("127.0.0.1", broker, client_id="t-sim")
    for t in (bridge_side, il_side, sim):
        await t.connect()
    seen, commands = {}, []
    await sim.subscribe("il/#", lambda m: seen.__setitem__(m.topic, m.payload.decode()))
    await sim.subscribe("rustuya/command", lambda m: commands.append(json.loads(m.payload)))
    runner = Runner(hub, bridge_side, il_side)
    await runner.start()
    try:
        await until(lambda: seen.get("il/_producer/tuya") == "online" and "il/lamp1" in seen)
        await sim.publish("rustuya/error/lamp1", '{"errorCode":0,"errorMsg":"Connection Successful"}', 1, True)
        await until(lambda: {"action": "get", "id": "lamp1"} in commands)
        await sim.publish("rustuya/event/state/lamp1", '{"20":true,"22":1000,"23":0}', 1, True)
        await until(lambda: seen.get("il/lamp1/brightness") == "100")
        await sim.publish("il/lamp1/brightness/set", "50", 1)
        await until(lambda: any(c.get("action") == "set" for c in commands))
        assert [c for c in commands if c.get("action") == "set"] == [
            {"action": "set", "id": "lamp1", "dps": {"20": True, "22": 507}}]

        seen.pop("il/_producer/tuya")
        il_side._client._sock.close()                       # an unclean drop: the broker publishes the Last Will
        await until(lambda: seen.get("il/_producer/tuya") == "offline")
        await until(lambda: seen.get("il/_producer/tuya") == "online")      # paho reconnects; the runner republishes
    finally:
        await runner.stop()
        for t in (bridge_side, il_side):
            await t.close()
        for topic in list(seen) + ["rustuya/error/lamp1", "rustuya/event/state/lamp1"]:
            await sim.publish(topic, "", 1, True)
        await asyncio.sleep(0.1)
        await sim.close()


async def test_a_failed_connect_stops_its_own_thread():
    """A timed-out connect() must not leave the paho loop thread running (the caller may just drop the object)."""
    import threading

    from tuya2ildevice.host import MqttTransport

    before = {t.name for t in threading.enumerate()}
    t = MqttTransport("127.0.0.1", 1, client_id="dead-end")   # nothing listens on port 1
    with pytest.raises((TimeoutError, asyncio.CancelledError)):
        await t.connect(timeout=0.3)
    after = {t.name for t in threading.enumerate()}
    assert not (after - before), f"leaked thread(s): {after - before}"
