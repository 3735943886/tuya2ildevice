"""Delta accumulator (spec 7.3), keeps raw totals and publishes scaled values."""
import json

import pytest

from tuya2ildevice.fallback import unused_plans
from tuya2ildevice.tuya import platforms  # noqa: F401
from tuya2ildevice.tuya.model import DeviceSchema, DpSpec
from tuya2ildevice.tuya.runtime import HostEnv, classify, new_slot, on_update


def _plan():
    spec = DpSpec("add_ele", "Integer", '{"min":0,"max":50000,"scale":3,"step":1}', "sum")
    s = DeviceSchema("d", "cz", status_range={"add_ele": spec}, function={}, status={"add_ele": 84})
    (p,) = [e for e in classify(s, HostEnv(), ("sensor",)).entities if e.key == "add_ele"]
    return p, s


def test_delta_accumulates_once_per_timestamp():
    p, s = _plan()
    slot = new_slot(p)
    assert p.read(s.status, slot)["native_value"] == 0.0            # starts at 0 even if status has a value
    assert on_update(p, slot, ["add_ele"], {"add_ele": 1}, {"add_ele": 5}).write_state
    assert p.read({}, slot)["native_value"] == 0.005                  # scaled to the native unit
    assert not on_update(p, slot, ["add_ele"], {"add_ele": 1}, {"add_ele": 5}).write_state   # same ts
    assert on_update(p, slot, ["add_ele"], {"add_ele": 2}, {"add_ele": 7}).write_state
    assert p.read({}, slot)["native_value"] == 0.012
    assert not on_update(p, slot, ["add_ele"], None, {"add_ele": 7}).write_state               # no timestamps
    assert not on_update(p, slot, ["other"], {"add_ele": 3}, {"add_ele": 7}).write_state       # not own dp
    assert on_update(p, slot, None, None, {}).write_state                                     # online/offline


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("scale", [0, 1, 3])
def test_delta_native_unit_and_scale_with_total_above_dp_max(fallback, scale):
    spec = DpSpec("add_ele", "Integer", json.dumps(
        {"min": 0, "max": 10, "scale": scale, "step": 1, "unit": "kWh"}), "sum")
    schema = DeviceSchema("d", "cz", status_range={"add_ele": spec}, function={}, status={})
    if fallback:
        (plan,) = unused_plans(schema, {"17": ("add_ele", "default", {})}, set())
    else:
        (plan,) = [p for p in classify(schema, HostEnv(), ("sensor",)).entities if p.key == "add_ele"]
    assert plan.identity["native_unit"] == "kWh"
    slot = new_slot(plan)
    assert plan.read({}, slot)["native_value"] == 0
    for ts in (1, 2):
        assert on_update(plan, slot, ["add_ele"], {"add_ele": ts}, {"add_ele": 7}).write_state
    assert slot.total == 14  # raw total is kept across reloads; only each increment has the DP's range
    assert plan.read({}, slot)["native_value"] == 14 / (10 ** scale)
