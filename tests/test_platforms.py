"""Regression tests for platform-builder bugs found auditing docs/engine-spec.md against the code."""
from tuya2ildevice.tuya import platforms  # noqa: F401  (registers builders)
from tuya2ildevice.tuya.model import DeviceSchema, DpSpec
from tuya2ildevice.tuya.runtime import HostEnv, classify, on_update


def _entity(category, platform, key, **status):
    spec = DpSpec(key, "String", "")
    s = DeviceSchema("d", category, status_range={key: spec}, function={}, status=status)
    (p,) = [e for e in classify(s, HostEnv(), (platform,)).entities if e.key == key]
    return p, s


def test_event_with_undecodable_payload_reads_as_unknown_not_a_crash():
    """P-14 (deviate): core raises on an undecodable event payload; the engine must yield UNKNOWN, like the
    alarm platform's message decode already does -- not raise AttributeError/UnicodeDecodeError."""
    p, s = _entity("sp", "event", "alarm_message", alarm_message="not-base64!!")
    assert p.read(s.status)["event"] == ("triggered", {"message": None})

    p, s = _entity("sp", "event", "alarm_message", alarm_message="/w==")   # valid b64, not valid utf-8
    assert p.read(s.status)["event"] == ("triggered", {"message": None})


def test_camera_writes_state_on_any_update_not_just_its_own_dps():
    """core's TuyaCameraEntity has no `_process_device_update` override, so (per
    docs/analysis/catalog/F_core_crosscutting.md) it writes state on every update, whatever dp changed --
    it must be `update_all`, not `own_dps`."""
    s = DeviceSchema("d", "sp")
    (p,) = [e for e in classify(s, HostEnv(), ("camera",)).entities if e.platform == "camera"]
    assert on_update(p, None, ["unrelated_dp"], None, {}).write_state
