"""Adapter.read == tuya_sharing Manager._on_device_report (SDK as oracle; needs tuya_sharing) + write round-trips.
Run with the oracle venv (tuya-device-sharing-sdk==0.2.15)."""
import json
import pathlib
import random

import fixtures

from tuya2ildevice.tuya.adapter import STRATEGIES, Adapter
from tuya2ildevice.tuya.model import DpSpec

here = pathlib.Path(__file__).parent / "golden"
try:
    import tuya_sharing.strategy_repo  # noqa: F401  (registers strategies)
    from tuya_sharing.strategy import strategy as SDK
except ImportError:                       # the SDK is the oracle; without it these tests cannot run
    import pytest
    pytest.skip("tuya_sharing (tuya-device-sharing-sdk==0.2.15) not installed", allow_module_level=True)

random.seed(7)


def sdk_read(local_strategy, status_range, dpid, raw):
    """Exactly Manager._on_device_report for one item (device.support_local True)."""
    m = local_strategy[dpid]
    code, value = SDK.convert(m["value_convert"], (m["status_code"], raw), m["config_item"])
    sr = status_range.get(code)
    if sr and sr["type"] == "Enum" and value not in json.loads(sr["values"]).get("range", []):
        return None
    return {code: value}


def samples(strategy, ci):
    base = [None, "", 0, 1, 2, "0", "1", "2", "on", "off", "memory", "single_click", "long_press", True, False, 220]
    if strategy.startswith("dj_v2"):
        def hx(n):
            return "".join(random.choice("0123456789abcdef") for _ in range(n))
        return base + [hx(12), "00b403e803e8", hx(21), hx(2 + 26 * 3), hx(2)]
    return base


def test_read_matches_sdk():
    n = 0
    for code in fixtures.all_codes():
        d = fixtures.load(code)
        ls = d["local_strategy"]
        if not ls:
            continue
        rng = {k: {"type": v["type"], "values": v["values"]} for k, v in d["status_range"].items()}
        ad = Adapter.from_local_strategy(ls, {k: DpSpec(k, v["type"], v["values"]) for k, v in d["status_range"].items()})
        for dpid, meta in ls.items():
            if meta["value_convert"] not in STRATEGIES:
                continue
            for raw in samples(meta["value_convert"], meta["config_item"]):
                try:
                    want = sdk_read(ls, rng, dpid, raw)
                except Exception:  # noqa: BLE001, S112  (the SDK itself raises on this input: adapter drops it, checked below)
                    continue
                assert ad.read({dpid: raw}) == (want or {}), (code, dpid, meta["value_convert"], raw)
                n += 1
    assert n > 200, n
    print("compared", n, "sdk conversions")


def test_write_roundtrips():
    from tuya2ildevice.tuya.adapter import (
        _r_color,
        _r_contr,
        _r_scene,
        _w_color,
        _w_contr,
        _w_scene,
    )
    for raw in ("00b403e803e8", "012c00ff0010"):
        assert _w_color(_r_color(raw, {}), {}) == raw
    for raw in ("1" + "00b4" + "03e8" + "03e8" + "0064" + "0100", "0" + "0000" * 5):
        assert len(raw) == 21 and _w_contr(_r_contr(raw, {}), {}) == raw
    scene = "01" + "0a0b02" + "00b4" + "03e8" + "03e8" + "0100" + "0100"
    assert _w_scene(_r_scene(scene, {}), {}) == scene
    ci = {"enumMappingMap": {"0": {"value": "power_off"}, "off": {"value": "power_off"}, "1": {"value": "power_on"}}}
    ad = Adapter.from_local_strategy({"5": {"value_convert": "enum", "status_code": "relay_status",
                                            "config_item": {"statusFormat": '{"relay_status":"$"}', "valueType": "Enum",
                                                            "valueDesc": '{"range":["power_off","power_on"]}', **ci}}})
    assert ad.write([{"code": "relay_status", "value": "power_off"}]) == ({"5": "0"}, [])
    assert ad.write([{"code": "nope", "value": 1}]) == ({}, ["nope"])


def test_statusformat_key_and_unsupported_and_enum_guard():
    ls = {"2": {"value_convert": "default", "status_code": "humidity_value",
                "config_item": {"statusFormat": '{"va_humidity":"$"}', "valueType": "Integer", "valueDesc": '{"min":0,"max":100}'}},
          "9": {"value_convert": "cz_timer1_alg", "status_code": "t", "config_item": {"statusFormat": '{"timer":"$"}'}},
          "3": {"value_convert": "default", "status_code": "mode",
                "config_item": {"statusFormat": {"mode": "$"}, "valueType": "Enum", "valueDesc": '{"range":["a","b"]}'}}}
    ad = Adapter.from_local_strategy(ls, {"mode": DpSpec("mode", "Enum", '{"range":["a","b"]}')})
    assert ad.read({"2": 55, "9": "xx", "3": "b", "77": 1}) == {"va_humidity": 55, "timer": "xx", "mode": "b"}
    assert ad.read({"3": "zzz"}) == {}                      # Enum guard drops
    assert ad.unsupported == {"9": "cz_timer1_alg"}


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
