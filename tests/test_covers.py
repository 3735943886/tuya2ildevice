"""A cover's settings (`cover` block, covers.py): the device's switches, what each does, `CoverMotion` on top, and the
host saving what is written through IL (`SaveSettings` -> `OverrideWatcher.save_settings` -> `zz_settings.json`)."""
import json
import logging

import pytest
from helpers import curtain, fn, strat

from tuya2ildevice import (
    BridgeCommand,
    Command,
    Connected,
    Disconnected,
    Hub,
    IlTopics,
    Message,
    OverrideError,
    Publish,
    SaveSettings,
    SendMessage,
    SettingsChanged,
    TuyaDriver,
    Value,
)
from tuya2ildevice.host import (
    SETTINGS_FILE,
    InProcessTransport,
    OverrideWatcher,
    Runner,
    load_overrides,
)
from tuya2ildevice.host.pack import sync

PCT = {"unit": "%", "min": 0, "max": 100, "scale": 0, "step": 1}
SWITCH_KEYS = ("cover_invert_position", "cover_invert_set_position", "cover_invert_control", "cover_infer_motion")


def cover(dev_id="c1", category="cl", fns=(), status=(), pid="p", dps=None):
    """A cover of `fns` (writable) and `status` (read only) dps: [(code, type, values)]; dp ids in order from 1."""
    f = {c: fn(c, t, **v) for c, t, v in fns}
    sr = {**f, **{c: fn(c, t, **v) for c, t, v in status}}
    ids = dps or {str(i + 1): (c, fn_["type"]) for i, (c, fn_) in enumerate(sr.items())}
    return {"id": dev_id, "category": category, "product_id": pid, "name": "Cover", "product_name": "Cover",
            "function": f, "status_range": sr, "status": {}, "local_strategy": strat(ids)}


CONTROL = ("control", "Enum", {"range": ["open", "stop", "close"]})
ONE_DP = cover(fns=[CONTROL, ("percent_control", "Integer", PCT)])                         # 1 control, 2 position
READ_ONLY = cover(fns=[CONTROL], status=[("percent_state", "Integer", PCT)])                 # 1 control, 2 position
CONTROL_ONLY = cover(fns=[CONTROL])


def switches(desc):
    return {k for k in desc["props"] if "cover_" in k and k.endswith(SWITCH_KEYS)}


def values(outs):
    return {o.prop: o.value for o in outs if isinstance(o, Value)}


def sent(outs):
    return next(o.json["dps"] for o in outs if isinstance(o, SendMessage))


def drive(device, block=None, **kw):
    d = TuyaDriver(device, overrides={device["id"]: {"cover": block}} if block is not None else None,
                   device_settings=True, **kw)
    d.handle(0, Connected())
    return d


# ---- which switches a cover gets ---------------------------------------------------------------------------------
@pytest.mark.parametrize("device, expected", [
    (curtain(), {"cover_invert_position", "cover_invert_set_position", "cover_infer_motion"}),   # reported + target
    (ONE_DP, {"cover_invert_position", "cover_infer_motion"}),                                    # one dp for both
    (READ_ONLY, {"cover_invert_position", "cover_invert_control", "cover_infer_motion"}),         # position + control
    (CONTROL_ONLY, {"cover_invert_control"}),
])
def test_a_switch_is_offered_only_where_it_changes_something(device, expected):
    desc = TuyaDriver(device, device_settings=True).descriptor
    assert switches(desc) == expected
    for k in expected:
        assert desc["props"][k]["type"] == "binary" and desc["props"][k]["rw"] and desc["props"][k]["category"] == "config"
    assert desc["props"]["cover_invert_position"]["label"] == "Invert current position" if "cover_invert_position" in expected else True
    assert ("cover_state" in desc["props"]) == ("cover_infer_motion" in expected)          # inferred by default


def test_no_switch_without_device_settings_and_read_only_for_a_hazardous_cover():
    props = TuyaDriver(curtain()).descriptor["props"]
    assert not [p for p in props if p.startswith("cover_")]              # no motion inferred unless a block asks
    assert "cover_state" in TuyaDriver(curtain(), overrides={"p": {"cover": {"infer_motion": True}}}).descriptor["props"]
    garage = cover(category="ckmkzq", fns=[("switch_1", "Boolean", {})], status=[("doorcontact_state", "Boolean", {})])
    props = TuyaDriver(garage, device_settings=True).descriptor["props"]
    assert "cover_invert_control" in props and "rw" not in props["cover_invert_control"]
    d = drive(garage)
    assert d.handle(1, Command("cover_invert_control", True))[0].code == "read_only"
    assert TuyaDriver(garage, device_settings=True, allow_hazardous=True).descriptor["props"]["cover_invert_control"]["rw"]


def test_the_switches_show_the_settings_in_effect_even_offline():
    d = TuyaDriver(curtain(), overrides={"p": {"cover": {"invert_position": True, "infer_motion": False}}},
                   device_settings=True)
    outs = values(d.describe())
    assert outs["cover_invert_position"] is True and outs["cover_invert_set_position"] is False
    assert outs["available"] is False and "cover_infer_motion" in d.descriptor["props"]
    d.handle(0, Connected())
    d.handle(1, Message("state", {"3": 30}))
    outs = d.handle(2, Disconnected())
    assert Value("available", False) in outs and not any(getattr(o, "prop", "").startswith("cover_") for o in outs)


# ---- what each does --------------------------------------------------------------------------------------------------
def test_invert_position_reads_the_reported_dp_the_other_way():
    d = drive(curtain(), {"invert_position": True})
    assert values(d.handle(1, Message("state", {"3": 30})))["position"] == 70
    assert sent(d.handle(2, Command("position", 70))) == {"2": 70}              # the target is a dp of its own


def test_with_one_dp_for_both_invert_position_turns_reads_and_writes():
    d = drive(ONE_DP, {"invert_position": True})
    assert values(d.handle(1, Message("state", {"2": 30})))["position"] == 70
    assert sent(d.handle(2, Command("position", 70))) == {"2": 30}
    assert sent(d.handle(3, Command("open", None))) == {"2": 0}


def test_invert_set_position_writes_and_reads_the_target_the_other_way():
    d = drive(curtain(), {"invert_set_position": True})
    assert sent(d.handle(1, Command("position", 70))) == {"2": 30}
    assert sent(d.handle(2, Command("open", None))) == {"2": 0}
    assert values(d.handle(3, Message("state", {"3": 30})))["position"] == 30     # the reported one stays


@pytest.mark.parametrize("device, open_, close, stop", [
    (CONTROL_ONLY, "close", "open", "stop"),
    (cover(fns=[("mach_operate", "Enum", {"range": ["FZ", "ZZ", "STOP"]})]), "ZZ", "FZ", "STOP"),     # the special one
    (cover(fns=[("switch_1", "Boolean", {})]), False, True, None),                                    # a Boolean
])
def test_invert_control_swaps_open_and_close(device, open_, close, stop):
    d = drive(device, {"invert_control": True})
    (dp,) = device["local_strategy"]
    assert sent(d.handle(1, Command("open", None))) == {dp: open_}
    assert sent(d.handle(2, Command("close", None))) == {dp: close}
    if stop is not None:
        assert sent(d.handle(3, Command("stop", None))) == {dp: stop}


def test_invert_control_composes_with_the_users_alias():
    device = cover(fns=[("control", "Enum", {"range": ["on", "off", "pause"]})])     # device_settings off: as a file
    block = {"remap": {"control": {"alias": {"on": "open", "off": "close", "pause": "stop"}}}}
    plain = TuyaDriver(device, overrides={"c1": block})
    plain.handle(0, Connected())
    assert sent(plain.handle(1, Command("open", None))) == {"1": "on"}
    d = TuyaDriver(device, overrides={"c1": {**block, "cover": {"invert_control": True}}})
    d.handle(0, Connected())
    assert sent(d.handle(1, Command("open", None))) == {"1": "off"}
    assert sent(d.handle(2, Command("close", None))) == {"1": "on"}
    assert sent(d.handle(3, Command("stop", None))) == {"1": "pause"}


def test_a_setting_xors_with_an_old_remap_invert_which_warns(caplog):
    caplog.set_level(logging.WARNING)
    block = {"remap": {"percent_state": {"invert": True}}, "cover": {"invert_position": True}}
    d = TuyaDriver(curtain() | {"id": "warn1"}, overrides={"warn1": block})
    d.handle(0, Connected())
    assert values(d.handle(1, Message("state", {"3": 30})))["position"] == 30      # turned twice
    assert "remap.percent_state.invert on a cover is deprecated" in caplog.text
    caplog.clear()
    TuyaDriver(curtain() | {"id": "warn2"}, overrides={"warn2": {"converters": {"cover_motion": {}}}})
    assert "converters.cover_motion is deprecated" in caplog.text
    caplog.clear()
    lamp = cover(dev_id="warn3", category="kg", fns=[("switch_1", "Boolean", {})])
    TuyaDriver(lamp, overrides={"warn3": {"remap": {"switch_1": {"invert": True}}}})   # not a cover: as it was
    assert "deprecated" not in caplog.text


def test_position_from_target_and_invert_tilt():
    d = drive(curtain(), {"position_from_target": True})
    assert values(d.handle(1, Message("state", {"2": 40, "3": 10})))["position"] == 40
    assert "cover_invert_set_position" not in d.descriptor["props"]           # one dp for both now
    tilted = cover(fns=[CONTROL, ("percent_control", "Integer", PCT), ("angle_horizontal", "Integer", PCT)])
    d = drive(tilted, {"invert_tilt": True})
    assert values(d.handle(1, Message("state", {"3": 25})))["tilt"] == 75


def test_bad_cover_blocks_are_refused():
    for bad in ({"invert_positon": True}, {"invert_position": "yes"}, {"settle": -1}, {"control_2": {"x": True}},
                {"control_2": {"control_3": {}}}):
        with pytest.raises(OverrideError):
            TuyaDriver(curtain(), overrides={"cur1": {"cover": bad}})
    with pytest.raises(OverrideError, match="no cover in a group"):
        TuyaDriver(curtain(), overrides={"cur1": {"cover": {"control_2": {"invert_position": True}}}})


# ---- CoverMotion on top ----------------------------------------------------------------------------------------------
def motion(outs, prop="cover_state"):
    return [o.value for o in outs if isinstance(o, Value) and o.prop == prop]


def test_motion_is_inferred_after_the_inversion():
    d = drive(curtain(), {"invert_position": True, "invert_set_position": True})
    assert motion(d.handle(1, Message("state", {"2": 70, "3": 70}))) == ["stopped"]      # 30%
    assert motion(d.handle(2, Message("active", {"2": 0}))) == ["opening"]               # target 100%
    assert motion(d.handle(3, Message("active", {"3": 0}))) == ["open"]


def test_a_command_word_starts_a_move_and_the_position_decides_its_way():
    d = drive(READ_ONLY)
    d.handle(1, Message("state", {"1": "stop", "2": 50}))
    assert motion(d.handle(2, Message("active", {"1": "open"}))) == ["opening"]          # the device says open...
    assert motion(d.handle(3, Message("active", {"2": 40}))) == ["closing"]              # ...and closes
    assert motion(d.handle(4, Message("active", {"2": 20}))) == []
    assert motion(d.handle(5, Message("active", {"2": 0}))) == ["closed"]
    assert motion(d.handle(6, Message("active", {"1": "close"}))) == []                  # at the end already


def test_infer_motion_off_leaves_no_cover_state_and_settle_is_passed_on():
    assert "cover_state" not in drive(curtain(), {"infer_motion": False}).descriptor["props"]
    d = drive(curtain(), {"settle": 4})
    d.handle(1, Message("state", {"3": 50}))
    outs = d.handle(2, Message("active", {"1": "close"}))
    assert motion(outs) == ["closing"] and any(getattr(o, "after", None) == 4 for o in outs)


def test_a_users_cover_motion_wins_over_the_setting():
    d = TuyaDriver(curtain(), overrides={"cur1": {"converters": {"cover_motion": {"invert": True}}}},
                   device_settings=True)
    assert "cover_infer_motion" not in d.descriptor["props"]                        # it would change nothing
    d.handle(0, Connected())
    assert motion(d.handle(1, Message("state", {"3": 100}))) == ["closed"]          # the user's own config


# ---- several covers ------------------------------------------------------------------------------------------------
TWO = cover(fns=[CONTROL, ("percent_control", "Integer", PCT), ("control_2", "Enum", {"range": ["open", "stop", "close"]}),
                 ("percent_control_2", "Integer", PCT)],
            status=[("percent_state", "Integer", PCT), ("percent_state_2", "Integer", PCT)])


def test_each_cover_has_its_switches_and_its_block():
    d = TuyaDriver(TWO, overrides={"c1": {"cover": {"control_2": {"invert_position": True}}}}, device_settings=True)
    props = d.descriptor["props"]
    assert props["control_2_cover_invert_position"]["group"] == "control_2" and "cover_invert_position" in props
    assert props["control_2_cover_state"]["group"] == "control_2"
    assert values(d.handle(0, Connected()))["control_2_cover_invert_position"] is True
    v = values(d.handle(1, Message("state", {"5": 30, "6": 30})))
    assert v["position"] == 30 and v["control_2_position"] == 70
    outs = d.handle(2, Command("cover_invert_set_position", True))
    assert SettingsChanged({"cover": {"invert_set_position": True, "control_2": {"invert_position": True}}}) in outs


# ---- writing a switch ------------------------------------------------------------------------------------------------
def test_writing_a_switch_sends_nothing_and_reports_the_block():
    d = drive(curtain())
    assert d.handle(1, Command("cover_invert_position", True)) == [
        Value("cover_invert_position", True), SettingsChanged({"cover": {"invert_position": True}})]
    assert d.handle(2, Command("cover_invert_position", False)) == [
        Value("cover_invert_position", False), SettingsChanged({})]              # back where it started: no block
    d.handle(3, Disconnected())
    assert d.handle(4, Command("cover_infer_motion", False))[-1] == SettingsChanged({"cover": {"infer_motion": False}})


def test_a_switch_keeps_what_differs_from_the_products_block_and_what_the_device_had():
    ov = {"p": {"cover": {"invert_position": True}}, "cur1": {"cover": {"settle": 3}}}
    d = TuyaDriver(curtain(), overrides=ov, device_settings=True)
    assert d.handle(0, Command("cover_invert_position", False))[-1] == \
        SettingsChanged({"cover": {"invert_position": False, "settle": 3}})


def test_the_hub_saves_and_a_reload_shows_the_setting_at_once():
    hub = Hub([curtain()], device_settings=True)
    hub.start()
    hub.on_bridge_message(0, "cur1", Connected())
    hub.on_bridge_message(1, "cur1", Message("state", {"3": 30}))
    pubs = hub.on_il(2, "il/cur1/cover_invert_position/set", "true")
    assert pubs == [Publish("il", "il/cur1/cover_invert_position", "true", True, 1),
                    SaveSettings("cur1", {"cover": {"invert_position": True}})]
    pubs = hub.reload({"cur1": {"cover": {"invert_position": True}}})
    assert Publish("il", "il/cur1/position", "70", True, 1) in pubs
    assert not [p for p in pubs if isinstance(p, BridgeCommand)]


# ---- the host: Runner + OverrideWatcher ------------------------------------------------------------------------------
async def start(conv, **kw):
    il = InProcessTransport()
    watcher = OverrideWatcher(conv, None, interval=60)
    loaded = await watcher.load_initial()
    hub = Hub([curtain()], il=IlTopics("il", "tuya"), overrides=loaded.overrides, device_settings=True)
    commands = []
    runner = Runner(hub, il, on_bridge_command=commands.append, on_settings=watcher.save_settings, **kw)
    watcher.runner = runner
    await runner.start()
    runner.on_bridge_message("cur1", Connected())
    runner.on_bridge_message("cur1", Message("state", {"3": 30}))
    await runner.drain()
    return il, runner, commands


async def test_a_switch_written_through_il_is_saved_applied_and_kept(tmp_path):
    conv = tmp_path / "conv"
    conv.mkdir()
    (conv / "10_mine.json").write_text(json.dumps({"other": {"device": {"label": "x"}}}))
    il, runner, commands = await start(conv)
    assert il.retained["il/cur1/position"].payload == "30"
    commands.clear()
    await il.publish("il/cur1/cover_invert_position/set", "true", 1, False)
    await runner.drain()
    assert json.loads((conv / SETTINGS_FILE).read_text()) == {"cur1": {"cover": {"invert_position": True}}}
    assert not [c for c in commands if c.action == "set"]                     # nothing to the device
    assert il.retained["il/cur1/position"].payload == "70"                    # at once, from the last state
    assert il.retained["il/cur1/cover_invert_position"].payload == "true"
    runner.on_bridge_message("cur1", Message("state", {"3": 20}))
    await runner.drain()
    assert il.retained["il/cur1/position"].payload == "80"
    await runner.stop()

    il, runner, _ = await start(conv)                                          # a restart: still inverted
    assert il.retained["il/cur1/position"].payload == "70" and il.retained["il/cur1/cover_invert_position"].payload == "true"
    await il.publish("il/cur1/cover_invert_position/set", "false", 1, False)
    await runner.drain()
    assert json.loads((conv / SETTINGS_FILE).read_text()) == {"cur1": {"cover": {"invert_position": False}}}
    assert il.retained["il/cur1/position"].payload == "30"
    await runner.stop()


async def test_a_sync_callback_and_a_failing_one(tmp_path, caplog):
    saved = []
    il = InProcessTransport()
    runner = Runner(Hub([curtain()], device_settings=True), il, on_settings=lambda i, b: saved.append((i, b)))
    await runner.start()
    await il.publish("il/cur1/cover_infer_motion/set", "false", 1, False)
    await runner.drain()
    assert saved == [("cur1", {"cover": {"infer_motion": False}})]
    await runner.stop()

    async def broken(i, b):
        raise OSError("disk full")
    runner = Runner(Hub([curtain()], device_settings=True), il, on_settings=broken)
    await runner.start()
    await il.publish("il/cur1/cover_infer_motion/set", "false", 1, False)
    await runner.drain()
    assert "could not save the settings of cur1" in caplog.text
    await runner.stop()


async def test_save_settings_removes_an_empty_block_and_leaves_a_single_file_alone(tmp_path, caplog):
    watcher = OverrideWatcher(tmp_path, None)
    await watcher.save_settings("a", {"cover": {"invert_control": True}})
    await watcher.save_settings("b", {"cover": {"settle": 2}})
    await watcher.save_settings("a", {})
    assert json.loads((tmp_path / SETTINGS_FILE).read_text()) == {"b": {"cover": {"settle": 2}}}
    assert load_overrides(tmp_path).overrides == {"b": {"cover": {"settle": 2}}}
    assert [p.name for p in tmp_path.iterdir()] == [SETTINGS_FILE]            # no temporary file left
    (tmp_path / SETTINGS_FILE).write_text("{broken")
    await watcher.save_settings("a", {"cover": {"invert_control": True}})       # logged, the file left to its owner
    assert (tmp_path / SETTINGS_FILE).read_text() == "{broken" and "could not save" in caplog.text
    one = tmp_path / "one.json"
    one.write_text("{}")
    await OverrideWatcher(one, None).save_settings("a", {"cover": {"invert_control": True}})
    assert one.read_text() == "{}" and not (tmp_path / "one.json" / SETTINGS_FILE).exists()


def test_the_pack_never_touches_the_settings_file(tmp_path):
    served = tmp_path / "served"
    served.mkdir()
    (served / SETTINGS_FILE).write_text("{}")
    import hashlib
    manifest = {"version": 1, "files": [{"name": SETTINGS_FILE, "sha256": hashlib.sha256(b"{}").hexdigest()}]}
    dest = tmp_path / "conv"
    dest.mkdir()
    (dest / SETTINGS_FILE).write_text('{"cur1": {"cover": {"invert_position": true}}}')
    r = sync(dest, base_url=served.as_uri() + "/", manifest=manifest)
    assert not r.changed and json.loads((dest / SETTINGS_FILE).read_text())["cur1"]["cover"]["invert_position"]
