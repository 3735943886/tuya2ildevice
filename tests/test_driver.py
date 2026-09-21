import json
import os
import pathlib

import pytest

from helpers import curtain, fn, light, strat
from tuya2ildevice import (Absent, Command, Connected, Disconnected, Event, Message, Reject, SendMessage, TuyaDriver,
                           Value, check_command, Rejected)

ILDEVICE = pathlib.Path(os.environ.get("ILDEVICE", pathlib.Path(__file__).resolve().parents[2] / "ildevice"))   # the spec repo
needs_spec = pytest.mark.skipif(not ILDEVICE.is_dir(), reason=f"no ildevice checkout at {ILDEVICE} (set ILDEVICE)")


def test_light_realtime_and_commands():
    d = TuyaDriver(light())
    props = d.descriptor["props"]
    assert d.descriptor["kind"] == "light"
    assert props["switch_led"]["role"] == "on" and props["brightness"]["max"] == 100
    outs = d.handle(0, Connected())
    assert outs[0].desc is d.descriptor
    outs = d.handle(1, Message("state", {"dps": {"20": True, "22": 1000, "23": 0}}))
    vals = {o.prop: o.value for o in outs if isinstance(o, Value)}
    assert vals["available"] is True and vals["switch_led"] is True and vals["brightness"] == 100
    # active delta wrapped as the bridge does; only the changed prop is emitted
    assert d.handle(2, Message("active", {"data": {"dps": {"22": 505}}})) == [Value("brightness", 50)]
    assert d.handle(3, Message("passive", {"dps": {"22": 505}})) == []          # no change, no repeat
    (out,) = d.handle(4, Command("brightness", 50))
    assert isinstance(out, SendMessage) and out.json["dps"]["22"] == 507 and out.json["dps"]["20"] is True   # 50% -> 128/255 -> 507
    assert d.handle(5, Command("brightness", 101))[0].code == "out_of_range"
    assert d.handle(5, Command("switch_led", "maybe"))[0].code == "invalid_value"


def test_cover_position_and_triggers():
    d = TuyaDriver(curtain())
    p = d.descriptor["props"]
    assert d.descriptor["kind"] == "cover" and p["position"]["rw"] and {"open", "close", "stop"} <= set(p)
    d.handle(0, Connected())
    # like core, position is inverted unless control_back_mode == "back": raw 30 is 70% open
    outs = d.handle(1, Message("active", {"3": 30}))
    assert Value("position", 70) in outs
    (out,) = d.handle(2, Command("open"  , None))
    assert out.json == {"dps": {"2": 0}}           # set_position wins over the instruction dp, reversed, as in core
    assert d.handle(3, Command("position", 70))[0].json == {"dps": {"2": 30}}


def test_unavailable_when_disconnected_and_absent_on_drop():
    d = TuyaDriver(curtain())
    assert d.handle(0, Command("position", 10))[0] == Reject("position", "unavailable", "device link is down")
    d.handle(0, Connected())
    d.handle(1, Message("state", {"3": 30}))
    outs = d.handle(2, Disconnected())
    assert Absent("position") in outs and Value("available", False) in outs


def test_command_checks_before_link():
    d = TuyaDriver(curtain())
    assert d.handle(0, Command("nope", 1))[0].code == "unknown_property"
    assert d.handle(0, Command("available", True))[0].code == "read_only"


def test_delta_and_event_only_on_active():
    f = {"add_ele": {"code": "add_ele", "type": "Integer", "values": json.dumps({"min": 0, "max": 50000, "scale": 3, "step": 1}), "report_type": "sum"}}
    dev = {"id": "m", "category": "cz", "product_id": "p", "name": "Plug", "function": {}, "status_range": f, "status": {},
           "local_strategy": strat({"17": ("add_ele", "Integer")})}
    d = TuyaDriver(dev)
    assert d.descriptor["props"]["add_ele"]["series"] == "counter"
    d.handle(0, Connected())
    assert Value("add_ele", 0) in d.handle(1, Message("state", {"17": 84}))          # readback never accumulates
    assert d.handle(2, Message("active", {"17": 5, "t": 1})) == [Value("add_ele", 5)]
    assert d.handle(3, Message("active", {"17": 7, "t": 2})) == [Value("add_ele", 12)]


def test_event_fires_on_active_only():
    f = {"switch_mode1": fn("switch_mode1", "Enum", range=["click", "double_click", "press"])}
    dev = {"id": "b", "category": "wxkg", "product_id": "p", "name": "Btn", "function": {}, "status_range": f, "status": {},
           "local_strategy": strat({"1": ("switch_mode1", "Enum")})}
    d = TuyaDriver(dev)
    assert d.descriptor["props"]["switch_mode1"] == {"type": "event", "options": ["click", "double_click", "press"],
                                                     "src": "switch_mode1", "class": "button"}
    d.handle(0, Connected())
    assert d.handle(1, Message("state", {"1": "click"})) == [Value("available", True)]      # a replay is no occurrence
    assert d.handle(2, Message("active", {"1": "click"})) == [Event("switch_mode1", "click")]
    assert d.handle(3, Message("active", {"1": "click"})) == [Event("switch_mode1", "click")]   # same kind twice: two events


# --- il.md section 5 vectors ------------------------------------------------------------------
VECTORS = ILDEVICE / "vectors" / "commands.json"


@needs_spec
def test_il_command_vectors():
    v = json.loads(VECTORS.read_text())
    for c in v["cases"]:
        try:
            got = {"accept": check_command(v["descriptor"], c.get("state", {}), c["prop"], c.get("value"))}
        except Rejected as e:
            got = {"reject": e.code}
        assert got == c["expect"], c["name"]


# --- descriptors of the real devices validate against the IL schema ------------------------------
SCHEMA = ILDEVICE / "schema" / "descriptor.schema.json"


@needs_spec
def test_every_core_fixture_yields_a_valid_descriptor():
    """All 324 Home Assistant core tuya fixtures: descriptor validates, and carries no secret (D-3)."""
    import fixtures
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text())
    for code in fixtures.all_codes():
        d = TuyaDriver(fixtures.load(code))
        jsonschema.validate(d.descriptor, schema)
        assert "local_key" not in json.dumps(d.descriptor)


def test_unused_dps_get_properties_like_v1():
    f = {"switch_1": fn("switch_1", "Boolean"), "countdown_1": fn("countdown_1", "Integer", unit="s", min=0, max=86400, scale=0, step=1),
         "mystery": fn("mystery", "Enum", range=["a", "b"]), "cycle_time": fn("cycle_time", "String")}
    sr = {**f, "fault": fn("fault", "Bitmap", label=["e1", "e2"]), "temp_x": fn("temp_x", "Integer", unit="", min=0, max=100, scale=1, step=1)}
    dev = {"id": "x", "category": "zz", "product_id": "p", "name": "X", "function": f, "status_range": sr, "status": {},
           "local_strategy": strat({"1": ("switch_1", "Boolean"), "7": ("countdown_1", "Integer"), "9": ("mystery", "Enum"),
                                    "10": ("cycle_time", "String"), "11": ("fault", "Bitmap"), "12": ("temp_x", "Integer")})}
    assert "mystery" not in TuyaDriver(dev).descriptor["props"]           # off by default: core creates no entity for it
    d = TuyaDriver(dev, expose_unused=True)
    p = d.descriptor["props"]
    assert p["mystery"] == {"type": "select", "rw": True, "options": ["a", "b"], "label": "Mystery", "category": "config", "src": "mystery"}
    assert p["countdown_1"]["step"] == 1 and isinstance(p["countdown_1"]["step"], int) and p["countdown_1"]["unit"] == "s"
    assert "rw" not in p["cycle_time"] and p["cycle_time"]["type"] == "text" and p["cycle_time"]["category"] == "diagnostic"
    assert p["fault"]["type"] == "number" and p["temp_x"]["category"] == "diagnostic" and "rw" not in p["temp_x"]
    d.handle(0, Connected())
    outs = d.handle(1, Message("state", {"9": "b", "12": 55, "11": 2, "10": "abc"}))
    vals = {o.prop: o.value for o in outs if isinstance(o, Value)}
    assert vals["mystery"] == "b" and vals["temp_x"] == 5.5 and vals["fault"] == 2 and vals["cycle_time"] == "abc"
    assert d.handle(2, Command("mystery", "a"))[0].json == {"dps": {"9": "a"}}
    assert d.handle(3, Command("temp_x", 1))[0].code == "read_only"
    

# --- user overrides ----------------------------------------------------------------------------
from tuya2ildevice import OverrideError, from_v1, merge_all   # noqa: E402


def _kg():
    f = {"switch_1": fn("switch_1", "Boolean"), "countdown_1": fn("countdown_1", "Integer", unit="s", min=0, max=86400, scale=0, step=1),
         "cycle_time": fn("cycle_time", "String")}
    return {"id": "d1", "category": "kg", "product_id": "prod1", "name": "Sw", "function": f, "status_range": f, "status": {},
            "local_strategy": strat({"1": ("switch_1", "Boolean"), "7": ("countdown_1", "Integer"), "10": ("cycle_time", "String")})}


def test_override_props_device_and_precedence():
    ovr = {"prod1": {"props": {"countdown_1": {"label": "Timer", "category": None, "unit": "min"}, "cycle_time": {"hide": True}},
                     "device": {"model": "X1"}},
           "d1": {"props": {"switch_1": {"rw": False, "class": "switch"}}}}
    d = TuyaDriver(_kg(), overrides=ovr, expose_unused=True)
    p = d.descriptor["props"]
    assert p["countdown_1"]["label"] == "Timer" and p["countdown_1"]["unit"] == "min" and "category" not in p["countdown_1"]
    assert "cycle_time" not in p and d.descriptor["model"] == "X1"
    assert "rw" not in p["switch_1"] and p["switch_1"]["class"] == "switch"
    d.handle(0, Connected())
    assert d.handle(1, Command("switch_1", True))[0].code == "read_only"
    outs = d.handle(2, Message("state", {"10": "x"}))
    assert all(getattr(o, "prop", "") != "cycle_time" for o in outs)              # hidden: no value either


def test_override_dp_definition_is_classified_and_remove_hides_fallback():
    dev = _kg()
    ovr = {"prod1": {"dp": {"104": {"code": "percent_control", "type": "Integer",
                                    "values": {"min": 0, "max": 100, "scale": 0, "step": 1, "unit": "%"}}},
                     "remove": ["cycle_time"]}}
    d = TuyaDriver(dev, overrides=ovr, expose_unused=True)
    assert "cycle_time" not in d.descriptor["props"] and "percent_control" in d.descriptor["props"]
    d.handle(0, Connected())
    outs = d.handle(1, Message("state", {"104": 40}))
    assert Value("percent_control", 40) in outs
    assert d.handle(2, Command("percent_control", 60))[0].json == {"dps": {"104": 60}}
    # redefining an existing dp id points it at the new code
    d2 = TuyaDriver(dev, expose_unused=True, overrides={"d1": {"dp": {"7": {"code": "renamed", "type": "Integer", "values": {"min": 0, "max": 9, "scale": 0, "step": 1}}}}})
    assert "renamed" in d2.descriptor["props"] and "countdown_1" not in d2.descriptor["props"]


def test_override_errors_are_loud():
    for bad in ({"prod1": {"prop": {}}}, {"prod1": {"props": {"nope": {"label": "x"}}}},
                {"prod1": {"props": {"switch_1": {"unit": "s"}}}}, {"prod1": {"props": {"switch_1": {"rw": True}}}},
                {"prod1": {"dp": {"9": {"code": "a", "type": "Nonsense"}}}}):
        with pytest.raises(OverrideError):
            TuyaDriver(_kg(), overrides=bad)


def test_merge_and_v1_migration():
    assert merge_all([{"a": {"props": {"x": {"label": "1", "class": "c"}}}}, {"a": {"props": {"x": {"label": "2"}}}}]) == \
        {"a": {"props": {"x": {"label": "2", "class": "c"}}}}
    new, warn = from_v1({"5rta89nj": {"model": "Opener", "dp_meta": {"104": {"code": "percent_control", "type": "Integer", "unit": "%", "min": 0, "max": 100, "step": 1, "comp": "cover"}},
                                      "discovery_overrides": {"cover": {}}}})
    assert new["5rta89nj"]["dp"]["104"]["values"]["unit"] == "%" and new["5rta89nj"]["device"] == {"model": "Opener"}
    assert any("discovery_overrides" in w for w in warn) and any("comp" in w for w in warn)
    dev = _kg(); dev["product_id"] = "5rta89nj"
    assert "percent_control" in TuyaDriver(dev, overrides=new, expose_unused=True).descriptor["props"]


# --- code converters ---------------------------------------------------------------------------
from tuya2ildevice import Converter, Result   # noqa: E402
from tuya2ildevice.io import CancelTimer, SetTimer, Timer   # noqa: E402


def _motion(outs):
    return [o.value for o in outs if isinstance(o, Value) and o.prop == "motion"]


def test_cover_motion_builtin():
    cfg = {"cur1": {"converters": {"cover_motion": {"invert": False, "settle": 5}}}}
    d = TuyaDriver(curtain(), overrides=cfg)
    assert d.descriptor["props"]["motion"] == {"type": "select", "role": "motion", "options": ["opening", "closing", "stopped"]}
    d.handle(0, Connected())
    assert _motion(d.handle(1, Message("state", {"3": 0}))) == ["stopped"]         # a snapshot never starts motion
    outs = d.handle(2, Message("active", {"1": "open"}))
    assert _motion(outs) == ["opening"] and SetTimer("c0:settle", 5.0) in outs
    assert _motion(d.handle(3, Message("active", {"3": 50}))) == []                # still moving
    outs = d.handle(4, Message("active", {"3": 100}))
    assert _motion(outs) == ["stopped"] and CancelTimer("c0:settle") in outs       # arrived at the end
    assert _motion(d.handle(5, Message("active", {"2": 20}))) == ["closing"]       # set-position below the current one
    assert _motion(d.handle(6, Message("active", {"3": 20}))) == ["stopped"]       # reached the target
    d.handle(7, Message("active", {"1": "close"}))
    assert _motion(d.handle(8, Timer("c0:settle"))) == ["stopped"]                 # no position report: settle timer
    d.handle(9, Message("active", {"1": "open"}))
    outs = d.handle(10, Disconnected())
    assert Absent("motion") in outs and CancelTimer("c0:settle") in outs


def test_cover_motion_follows_engine_position_by_default():
    d = TuyaDriver(curtain(), overrides={"cur1": {"converters": {"cover_motion": {}}}})
    d.handle(0, Connected())
    d.handle(1, Message("state", {"3": 100}))                       # raw 100 = closed (engine reverses without control_back_mode)
    outs = d.handle(2, Message("active", {"2": 0}))                 # raw target 0 = fully open, from closed
    assert _motion(outs) == ["opening"]


def test_custom_python_converter_and_registration():
    class Counter(Converter):
        def props(self):
            return {"updates": {"type": "number"}}

        def reset(self):
            self.n = 0

        def __init__(self, device):
            self.n = 0
            self.device = device

        def update(self, now, codes, changed, active):
            self.n += 1
            return Result({"updates": self.n}, {"tick": 10})

        def timer(self, now, name, codes):
            return {"updates": 100}

    d = TuyaDriver(curtain(), converters={"p": [Counter]})              # product_id of the curtain() helper
    d.handle(0, Connected())
    outs = d.handle(1, Message("state", {"3": 10}))
    assert Value("updates", 1) in outs and SetTimer("c0:tick", 10.0) in outs
    assert d.handle(2, Timer("c0:tick")) == [Value("updates", 100)]
    assert d.handle(3, Timer("c9:x")) == [] and d.handle(3, Timer("junk")) == []
    with pytest.raises(OverrideError):
        TuyaDriver(curtain(), overrides={"cur1": {"converters": {"nope": {}}}})
    with pytest.raises(OverrideError):                                  # a converter property may not clash or be writable
        TuyaDriver(curtain(), converters={"p": [type("Bad", (Converter,), {"__init__": lambda s, dev: None,
                                                                            "props": lambda s: {"position": {"type": "number"}}})]})
    hidden = TuyaDriver(curtain(), converters={"p": [Counter]}, overrides={"cur1": {"props": {"updates": {"hide": True}}}})
    hidden.handle(0, Connected())
    assert Value("updates", 1) not in hidden.handle(1, Message("state", {"3": 10}))


# --- alarm panel and robot vacuum ----------------------------------------------------------------
def _with_dpmap(code, **kw):
    """A core fixture has no local_strategy; number its dps by status order."""
    import fixtures
    dev = fixtures.load(code)
    dpmap = {str(i + 1): c for i, c in enumerate(dev["status"])}
    d = TuyaDriver(dev, dpmap=dpmap, **kw)
    d.handle(0, Connected())
    return d, {c: i for i, c in dpmap.items()}


def _sent(d, now, prop, value=None):
    (out,) = d.handle(now, Command(prop, value))
    return out.json["dps"]


def test_alarm_panel_states_and_arming():
    d, ids = _with_dpmap("mal_gyitctrjj1kefxp2", allow_hazardous=True)
    assert d.descriptor["kind"] == "alarm" and d.descriptor["props"]["alarm_state"]["role"] == "alarm_state"
    m = ids["master_mode"]
    assert _sent(d, 1, "arm_home") == {m: "home"} and _sent(d, 2, "arm_away") == {m: "arm"}
    assert _sent(d, 3, "disarm") == {m: "disarmed"}
    vals = lambda outs: {o.prop: o.value for o in outs if isinstance(o, Value)}
    assert vals(d.handle(4, Message("state", {m: "home"})))["alarm_state"] == "armed_home"
    assert vals(d.handle(5, Message("active", {ids["master_state"]: "alarm", ids["alarm_msg"]: ""})))["alarm_state"] == "triggered"
    import fixtures
    assert "disarm" not in TuyaDriver(fixtures.load("mal_gyitctrjj1kefxp2")).descriptor["props"]         # S-1


def test_robot_vacuum_state_and_commands():
    d, ids = _with_dpmap("sd_lr33znaodtyarrrz")
    p = d.descriptor["props"]
    assert d.descriptor["kind"] == "vacuum" and p["vacuum_state"]["role"] == "vacuum_state"
    assert [n for n in ("start", "pause", "return_home", "locate") if p[n]["role"] == n] == ["start", "pause", "return_home", "locate"]
    assert _sent(d, 1, "start") == {ids["power_go"]: True} and _sent(d, 2, "stop") == {ids["power_go"]: False}
    assert _sent(d, 3, "pause") == {ids["pause"]: True} and _sent(d, 4, "return_home") == {ids["switch_charge"]: True}
    assert _sent(d, 5, "locate") == {ids["seek"]: True} and _sent(d, 6, "fan_speed", "gentle") == {ids["suction"]: "gentle"}
    vals = {o.prop: o.value for o in d.handle(7, Message("state", {ids["status"]: "cleaning", ids["suction"]: "strong"}))
            if isinstance(o, Value)}
    assert vals["vacuum_state"] == "cleaning" and vals["fan_speed"] == "strong"
