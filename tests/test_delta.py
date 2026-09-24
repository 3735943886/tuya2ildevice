"""Delta accumulator (spec 7.3), mirrors core DeltaIntegerWrapper semantics."""
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
    assert p.read({}, slot)["native_value"] == 5.0                    # raw (unscaled) delta, as core
    assert not on_update(p, slot, ["add_ele"], {"add_ele": 1}, {"add_ele": 5}).write_state   # same ts
    assert on_update(p, slot, ["add_ele"], {"add_ele": 2}, {"add_ele": 7}).write_state
    assert p.read({}, slot)["native_value"] == 12.0
    assert not on_update(p, slot, ["add_ele"], None, {"add_ele": 7}).write_state               # no timestamps
    assert not on_update(p, slot, ["other"], {"add_ele": 3}, {"add_ele": 7}).write_state       # not own dp
    assert on_update(p, slot, None, None, {}).write_state                                     # online/offline
