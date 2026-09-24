"""User overrides for non-standard devices: dp renames, `remap` (alias / invert), per-device `expose_unused`, named
converter types, the built-in curated set and the migration of rustuya-homeassistant v1 custom_converters."""
import json
import pathlib

import pytest

from helpers import curtain, fn, strat
from test_driver import SCHEMA, needs_spec
from tuya2ildevice import Command, Connected, Converter, Message, SendMessage, TuyaDriver, Value
from tuya2ildevice.overrides import BUILTIN, OverrideError, from_v1, is_v1

HERE = pathlib.Path(__file__).parent
PRODUCTS = json.loads((HERE / "v1_default_products.json").read_text())      # cloud schemas of the curated products

# rustuya-homeassistant's custom_converters/00_default.json, as it shipped
V1_DEFAULT = {
    "5rta89nj": {"model": "Sliding Window Opener",
                 "dp_meta": {"104": {"code": "percent_control", "type": "Integer", "unit": "%", "min": 0, "max": 100,
                                     "step": 1}}},
    "f6jujmx0is5td50x": {"discovery_overrides": {"cover": {
        "position_dp": "2", "state_opening": "open", "state_closing": "close", "state_stopped": "stop",
        "payload_open": "open", "payload_close": "close", "payload_stop": "stop"}}},
    "h2wipnagcunsar5r": {"discovery_overrides": {"cover": {
        "state_dp": "99", "state_stream": "derived", "payload_open": "open", "payload_close": "close",
        "payload_stop": "stop"}}},
    "3i3exuay": {"discovery_overrides": {"cover": {
        "state_dp": "99", "state_stream": "derived", "payload_open": "open", "payload_close": "close",
        "payload_stop": "stop"}}},
}


def _values(outs):
    return {o.prop: o.value for o in outs if isinstance(o, Value)}


def _sent(outs):
    return [o.json["dps"] for o in outs if isinstance(o, SendMessage)]


def _odd_curtain():
    """A curtain whose control dp speaks on/off/pause and whose position runs the other way."""
    d = curtain()
    for t in (d["function"], d["status_range"]):
        t["control"] = fn("control", "Enum", range=["on", "pause", "off"])
    return d


# --- rename ------------------------------------------------------------------------------------
def test_a_code_only_dp_entry_renames_and_keeps_the_type():
    dev = curtain()
    dev["local_strategy"] = strat({"1": ("mach_ctrl", "Enum"), "2": ("percent_control", "Integer"),
                                   "3": ("percent_state", "Integer")})
    for t in (dev["function"], dev["status_range"]):
        t["mach_ctrl"] = t.pop("control")
        t["mach_ctrl"]["code"] = "mach_ctrl"
    assert "open" not in TuyaDriver(dev).descriptor["props"]                   # the tables do not know mach_ctrl
    d = TuyaDriver(dev, overrides={"p": {"dp": {"1": {"code": "control"}}}})
    assert d.descriptor["props"]["open"]["role"] == "open"
    d.handle(0, Connected())
    assert _sent(d.handle(1, Command("stop", None))) == [{"1": "stop"}]


def test_a_rename_of_a_dp_the_device_lacks_is_an_error():
    with pytest.raises(OverrideError, match="no dp 42"):
        TuyaDriver(curtain(), overrides={"p": {"dp": {"42": {"code": "control"}}}})
    with pytest.raises(OverrideError, match="only a `code`"):
        TuyaDriver(curtain(), overrides={"p": {"dp": {"1": {"code": "control", "mode": "R"}}}})


# --- remap -------------------------------------------------------------------------------------
def test_alias_translates_both_ways_and_the_enum_range():
    ov = {"p": {"remap": {"control": {"alias": {"on": "open", "off": "close", "pause": "stop"}}}}}
    assert "open" not in TuyaDriver(_odd_curtain()).descriptor["props"]    # no standard word: no open/close/stop
    d = TuyaDriver(_odd_curtain(), overrides=ov)
    assert {"open", "close", "stop"} <= set(d.descriptor["props"])
    d.handle(0, Connected())
    assert _sent(d.handle(1, Command("stop", None))) == [{"1": "pause"}]
    d = TuyaDriver(_odd_curtain(), overrides={"p": {**ov["p"], "remove": ["percent_control"]}})
    d.handle(0, Connected())                                # no set-position dp: open/close go to the control dp
    assert _sent(d.handle(2, Command("close", None))) == [{"1": "off"}]


def test_alias_is_applied_before_the_converters_see_the_value():
    ov = {"p": {"remap": {"control": {"alias": {"on": "open", "off": "close", "pause": "stop"}}},
                "converters": {"cover_motion": {"invert": False}}}}
    d = TuyaDriver(_odd_curtain(), overrides=ov)
    d.handle(0, Connected())
    d.handle(1, Message("state", {"3": 50}))
    assert _values(d.handle(2, Message("active", {"1": "on"})))["motion"] == "opening"


def test_invert_mirrors_an_integer_and_negates_a_boolean():
    ov = {"p": {"remap": {"percent_state": {"invert": True}, "percent_control": {"invert": True}}}}
    plain, inv = TuyaDriver(curtain()), TuyaDriver(curtain(), overrides=ov)
    for d in (plain, inv):
        d.handle(0, Connected())
    assert _values(inv.handle(1, Message("active", {"3": 30})))["position"] == \
        100 - _values(plain.handle(1, Message("active", {"3": 30})))["position"]
    assert _sent(inv.handle(2, Command("position", 80))) == [{"2": 100 - _sent(plain.handle(2, Command("position", 80)))[0]["2"]}]

    from test_driver import _kg
    d = TuyaDriver(_kg(), overrides={"prod1": {"remap": {"switch_1": {"invert": True}}}})
    d.handle(0, Connected())
    assert _values(d.handle(1, Message("active", {"1": True})))["switch_1"] is False
    assert _sent(d.handle(2, Command("switch_1", True))) == [{"1": False}]


def test_remap_errors_are_loud():
    for bad in ({"p": {"remap": {"control": {"alias": {"a": "open", "b": "open"}}}}},
                {"p": {"remap": {"control": {"invert": "yes"}}}},
                {"p": {"remap": {"control": {"words": {}}}}},
                {"p": {"expose_unused": "yes"}}):
        with pytest.raises(OverrideError):
            TuyaDriver(curtain(), overrides=bad)


# --- per-device expose_unused, named converter types ----------------------------------------------
def test_expose_unused_per_device():
    from test_driver import _kg
    names = set(TuyaDriver(_kg(), overrides={"prod1": {"expose_unused": True}}).descriptor["props"])
    assert names == set(TuyaDriver(_kg(), expose_unused=True).descriptor["props"])
    assert "cycle_time" in names
    assert "cycle_time" not in TuyaDriver(_kg(), expose_unused=True,
                                          overrides={"prod1": {"expose_unused": False}}).descriptor["props"]


class _Doubler(Converter):
    def __init__(self, cfg):
        self.factor = cfg.get("factor", 2)

    def props(self):
        return {"double": {"type": "number"}}

    def update(self, now, codes, changed, active):
        return {"double": codes.get("percent_state", 0) * self.factor}


def test_named_converter_types_from_the_host():
    ov = {"p": {"converters": {"doubler": {"factor": 3}}}}
    with pytest.raises(OverrideError, match="unknown converter"):
        TuyaDriver(curtain(), overrides=ov)
    d = TuyaDriver(curtain(), overrides=ov, converter_types={"doubler": _Doubler})
    d.handle(0, Connected())
    assert _values(d.handle(1, Message("active", {"3": 10})))["double"] == 30


# --- built-in set and v1 migration -----------------------------------------------------------------
def test_builtin_overrides_apply_by_default_and_follow_use_quirks():
    opener = PRODUCTS["5rta89nj"]
    assert set(TuyaDriver(opener, use_quirks=False).descriptor["props"]) == {"available"}
    props = TuyaDriver(opener).descriptor["props"]
    assert props["percent_control"]["label"] == "Opening" and props["percent_control"].get("rw")
    assert props["residual_electricity"]["class"] == "battery"
    for pid in ("f6jujmx0is5td50x", "h2wipnagcunsar5r", "3i3exuay"):
        d = TuyaDriver(PRODUCTS[pid])
        assert d.descriptor["kind"] == "cover" and "motion" in d.descriptor["props"], pid
        assert "motion" not in TuyaDriver(PRODUCTS[pid], use_quirks=False).descriptor["props"]


def test_a_user_block_wins_over_the_builtin_one():
    d = TuyaDriver(PRODUCTS["5rta89nj"], overrides={"5rta89nj": {"props": {"percent_control": {"label": "Window"}}}})
    assert d.descriptor["props"]["percent_control"]["label"] == "Window"


def test_position_reads_back_from_the_target_where_the_builtin_says_so():
    d = TuyaDriver(PRODUCTS["f6jujmx0is5td50x"])
    d.handle(0, Connected())
    plain = TuyaDriver(PRODUCTS["f6jujmx0is5td50x"], use_quirks=False)
    plain.handle(0, Connected())
    assert "position" not in _values(plain.handle(1, Message("active", {"2": 30})))     # read from percent_state
    assert "position" in _values(d.handle(1, Message("active", {"2": 30})))


@needs_spec
def test_builtin_descriptors_validate():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text())
    for dev in PRODUCTS.values():
        jsonschema.validate(TuyaDriver(dev).descriptor, schema)


def test_v1_default_converts_to_the_builtin_set():
    assert is_v1(V1_DEFAULT) and not is_v1(BUILTIN)
    new, warn = from_v1(V1_DEFAULT)
    assert warn == []
    for pid in ("f6jujmx0is5td50x", "h2wipnagcunsar5r", "3i3exuay"):
        assert new[pid] == BUILTIN[pid], pid
    opener = {k: v for k, v in BUILTIN["5rta89nj"].items() if k != "props"}      # labels are the built-in's own
    new_opener = dict(new["5rta89nj"])
    new_opener["dp"] = {k: {kk: vv for kk, vv in d.items() if kk != "mode"} for k, d in new_opener["dp"].items()}
    assert new_opener == opener


def test_v1_cover_roles_words_and_inversion():
    new, warn = from_v1({"x": {"discovery_overrides": {"cover": {
        "command_dp": 101, "set_position_dp": "2", "position_dp": "5", "invert_position": True,
        "payload_open": "on", "payload_close": "off", "payload_stop": "stop", "json_attributes_topic": "t"},
        "climate": {"a": 1}}}})
    b = new["x"]
    assert b["dp"] == {"101": {"code": "control"}, "5": {"code": "percent_state"}}
    assert b["remap"] == {"control": {"alias": {"on": "open", "off": "close"}}, "percent_state": {"invert": True}}
    assert "converters" not in b
    assert any("json_attributes_topic" in w for w in warn) and any("discovery_overrides.climate" in w for w in warn)
