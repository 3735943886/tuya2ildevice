"""What the drivers publish fits their descriptors, over every core fixture: names are real names, and each value a
fixture's own state produces is within its property's range, one of its options, or a colour."""
import re

import fixtures
import pytest

from tuya2ildevice import Connected, IlTopics, Message, TuyaDriver, Value
from tuya2ildevice.mqtt import Hub, Publish

WIRE = {"Boolean", "Integer", "Enum", "String", "Raw", "Json", "Bitmap"}


def record(code):
    """The fixture with dps numbered in status order and a local_strategy, as a real device record has one."""
    d = fixtures.load(code)
    codes = list(d["status"])
    types = {}
    for c in codes:
        spec = d["status_range"].get(c) or d["function"].get(c)
        v = d["status"][c]
        types[c] = spec["type"] if spec and spec["type"] in WIRE else (
            "Boolean" if isinstance(v, bool) else "Integer" if isinstance(v, int) else "String")
    d["local_strategy"] = {str(i + 1): {"value_convert": "default", "status_code": c,
                                        "config_item": {"statusFormat": {c: "$"}, "valueType": types[c],
                                                        "valueDesc": {}, "enumMappingMap": {}}}
                           for i, c in enumerate(codes)}
    return d, {str(i + 1): d["status"][c] for i, c in enumerate(codes)}


def fits(spec, value):
    t = spec["type"]
    if t == "binary":
        return isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and spec.get("min", value) <= value <= spec.get("max", value)
    if t == "select":
        return value in spec["options"]
    if spec.get("role") == "color":
        return re.fullmatch(r"#[0-9a-f]{6}", value) is not None
    return True


@pytest.mark.parametrize("code", fixtures.all_codes())
def test_a_fixture_state_publishes_values_that_fit(code):
    rec, dps = record(code)
    d = TuyaDriver(rec, allow_hazardous=True)
    props = d.descriptor["props"]
    assert not [p for p in props if p == "prop" or p.startswith("prop_")]       # a composite without a core key
    out = d.handle(0, Connected()) + d.handle(1, Message("state", dps))
    bad = [(o.prop, o.value) for o in out if isinstance(o, Value) and not fits(props[o.prop], o.value)]
    assert not bad


def test_a_fan_and_a_climate_power_are_named_after_their_dp():
    fan, _ = record("fs_g0ewlb1vmwqljzji")
    assert TuyaDriver(fan).descriptor["props"]["switch"]["role"] == "on"
    ac, _ = record("cs_b9oyi2yofflroq1g")
    assert TuyaDriver(ac).descriptor["props"]["switch"]["role"] == "on"


def test_the_descriptor_names_the_hubs_source():
    rec, _ = record("fs_g0ewlb1vmwqljzji")
    hub = Hub([rec], il=IlTopics("il", "tuya-house"))
    pubs = [p for p in hub.start() if isinstance(p, Publish)]
    import json
    desc = next(json.loads(p.payload) for p in pubs if p.topic == f"il/{rec['id']}")
    assert desc["source"] == "tuya-house" and pubs[0].topic == "il/_producer/tuya-house"
