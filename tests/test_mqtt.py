import json

import pytest

from helpers import curtain, light
from tuya2ildevice import BridgeTopics, Hub, Publish


def topics(pubs, side=None):
    return {p.topic: p for p in pubs if side is None or p.side == side}


def test_bridge_to_il_and_back():
    hub = Hub([light(), curtain()])
    start = topics(hub.start(), "il")
    assert start["il/_producer/tuya"].payload == "online" and start["il/_producer/tuya"].retain
    assert json.loads(start["il/lamp1"].payload)["kind"] == "light" and start["il/lamp1"].retain
    assert start["il/lamp1/available"].payload == "false"

    # bridge says connected -> a `get` goes back to the bridge
    out = hub.on_bridge(0, "rustuya/error/lamp1", '{"errorCode":0,"errorMsg":"Connection Successful"}', True)
    assert Publish("bridge", "rustuya/command", '{"action": "get", "id": "lamp1"}') in out

    # retained full snapshot, then a live delta
    out = topics(hub.on_bridge(1, "rustuya/event/state/lamp1", '{"20":true,"22":1000,"23":0}', True))
    assert out["il/lamp1/switch_led"].payload == "true" and out["il/lamp1/brightness"].payload == "100"
    assert out["il/lamp1/available"].payload == "true"
    out = hub.on_bridge(2, "rustuya/event/active/lamp1", '{"data":{"dps":{"22":505}}}')
    assert out == [Publish("il", "il/lamp1/brightness", "50", True, 1)]
    assert hub.on_bridge(3, "rustuya/event/active/lamp1", '{"22":100}', retained=True) == []   # replayed delta

    # il-ha writes a value -> a `set` for the bridge; a bad one -> a reject for il-ha
    (cmd,) = hub.on_il(4, "il/lamp1/brightness/set", "50")
    assert cmd.side == "bridge" and json.loads(cmd.payload) == {"action": "set", "id": "lamp1", "dps": {"20": True, "22": 507}}
    (rej,) = hub.on_il(5, "il/lamp1/brightness/set", "500")
    assert rej.topic == "il/lamp1/reject" and not rej.retain and json.loads(rej.payload)["code"] == "out_of_range"
    assert hub.on_il(6, "il/lamp1/brightness/set", "50", retained=True) == []                 # M-9

    # trigger with an empty payload, and a link drop
    (cmd,) = hub.on_il(7, "il/lamp1/switch_led/set", "off")
    assert json.loads(cmd.payload)["dps"] == {"20": False}
    out = topics(hub.on_bridge(8, "rustuya/error/lamp1", '{"errorCode":914}'))
    assert out["il/lamp1/brightness"].payload == "" and out["il/lamp1/available"].payload == "false"


def test_single_dp_mode_and_custom_root():
    b = BridgeTopics("myhome/tuya", event="{root}/{id}/{dp}/{type}")
    hub = Hub([light()], bridge=b)
    hub.start()
    out = topics(hub.on_bridge(0, "myhome/tuya/lamp1/20/active", "true"))
    assert out["il/lamp1/switch_led"].payload == "true"
    assert b.subscriptions()[0] == "myhome/tuya/+/+/+"


def test_bad_ids_refused():
    import pytest
    d = light()
    d["id"] = "a/b"
    with pytest.raises(ValueError):
        Hub([d])


def test_reload_overrides_republishes_and_clears_removed_props():
    from tuya2ildevice import OverrideError
    import pytest
    hub = Hub([curtain()])
    hub.start()
    hub.on_bridge(0, "rustuya/error/cur1", '{"errorCode":0}')
    hub.on_bridge(1, "rustuya/event/state/cur1", '{"3":30}')
    assert hub.reload({}) == []                                                    # nothing changed
    pubs = hub.reload({"cur1": {"props": {"stop": {"hide": True}}, "device": {"label": "Bedroom"}}})
    by = topics(pubs)
    assert by["il/cur1/stop"].payload == "" and by["il/cur1/stop"].retain          # M-11
    assert json.loads(by["il/cur1"].payload)["label"] == "Bedroom" and "stop" not in json.loads(by["il/cur1"].payload)["props"]
    assert Publish("bridge", "rustuya/command", '{"action": "get", "id": "cur1"}') in pubs   # state again
    with pytest.raises(OverrideError):
        hub.reload({"cur1": {"props": {"missing": {"hide": True}}}})
    assert json.loads(topics(hub.on_il(2, "il/cur1/position/set", "70"))["rustuya/command"].payload)["dps"]           # still works


def test_timers_surface_as_schedule_and_on_timer():
    from tuya2ildevice import Schedule, Unschedule
    hub = Hub([curtain()], overrides={"cur1": {"converters": {"cover_motion": {"invert": False, "settle": 5}}}})
    hub.start()
    hub.on_bridge(0, "rustuya/error/cur1", '{"errorCode":0}')
    hub.on_bridge(1, "rustuya/event/state/cur1", '{"3":50}')
    out = hub.on_bridge(2, "rustuya/event/active/cur1", '{"1":"close"}')
    assert Schedule("cur1", "c0:settle", 5.0) in out and Publish("il", "il/cur1/motion", "closing", True, 1) in out
    assert hub.on_timer(3, "cur1", "c0:settle") == [Publish("il", "il/cur1/motion", "stopped", True, 1)]
    hub.on_bridge(4, "rustuya/event/active/cur1", '{"1":"open"}')
    assert Unschedule("cur1", "c0:settle") in hub.on_bridge(5, "rustuya/error/cur1", '{"errorCode":914}')


def test_set_device_adds_replaces_and_remove_clears_first():
    hub = Hub([light()])
    hub.start()
    cid = curtain()["id"]
    added = topics(hub.set_device(curtain()))                      # runtime add: descriptor out, retained
    assert added[f"il/{cid}"].retain and json.loads(added[f"il/{cid}"].payload)["kind"] == "cover"
    assert cid in hub.drivers

    # replacing a known device that lost a property clears that property before the new descriptor (M-11)
    small = light()
    del small["function"]["temp_value_v2"]                         # function and status_range are one dict in helpers.light
    small["local_strategy"].pop("23")
    out = hub.set_device(small)
    order = [p.topic for p in out]
    assert order == ["il/lamp1/color_temperature", "il/lamp1", "il/lamp1/available"]
    assert out[0].payload == "" and out[0].retain

    # removal: every retained value cleared, then the descriptor last, and the device is gone
    out = hub.remove_device("lamp1")
    assert out[-1].topic == "il/lamp1" and out[-1].payload == "" and out[-1].retain
    assert all(p.payload == "" and p.retain for p in out) and len(out) > 2
    assert hub.on_bridge(2, "rustuya/event/state/lamp1", '{"20":true}', True) == [] and hub.remove_device("lamp1") == []

    bad = light()
    bad["id"] = "_x"
    with pytest.raises(ValueError):
        hub.set_device(bad)
    assert "_x" not in hub.drivers
