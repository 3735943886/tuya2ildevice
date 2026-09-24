"""apply_quirk == tuya-device-handlers' post-quirk schema (oracle dumps: quirk_golden.json real fixtures,
quirk_synthetic_golden.json empty-schema patches)."""
import json
import pathlib
import fixtures
from tuya2ildevice.tuya.model import DeviceSchema, DpSpec
from tuya2ildevice.tuya.quirks import apply_quirk, apply_status_quirk, device_info, load_quirks, quirk_for

here = pathlib.Path(__file__).parent / "golden"


def norm_values(v):
    if isinstance(v, str):
        try:
            return json.loads(v) if v else None
        except ValueError:
            return v
    return v


def dump(s):
    return {"category": s.category,
            "function": {k: (v.type, norm_values(v.values)) for k, v in s.function.items()},
            "status_range": {k: (v.type, norm_values(v.values), v.report_type) for k, v in s.status_range.items()}}


def oracle(o):
    return {"category": o["category"],
            "function": {k: (v["type"], norm_values(v["values"])) for k, v in o["function"].items()},
            "status_range": {k: (v["type"], norm_values(v["values"]), v.get("report_type")) for k, v in o["status_range"].items()}}


def test_real_fixtures():
    gold = json.loads((here / "quirk_golden.json").read_text())
    assert gold, "no quirk fixtures"
    for code, g in gold.items():
        d = fixtures.load(code)
        def mk(m):
            return {k: DpSpec(k, v["type"], v["values"], v.get("report_type")) for k, v in m.items()}
        s = DeviceSchema(d["id"], d["category"], d["product_id"], d["name"], d["product_name"], d["online"],
                         mk(d["function"]), mk(d["status_range"]), d["status"])
        got = dump(apply_quirk(s))
        want = oracle(g["after"])
        assert got == want, code
        assert dump(s) == oracle(g["before"]), "apply_quirk must not mutate its input"


def test_synthetic_empty_schema_patches():
    gold = json.loads((here / "quirk_synthetic_golden.json").read_text())
    assert len(gold) == 32 == len(load_quirks())
    for pid, g in gold.items():
        s = DeviceSchema("x", "", pid, status={"temp_set": 100})
        got = dump(apply_quirk(s))
        want = oracle(g)
        assert got == want, pid


def test_when_guard_and_status_quirk_and_device_info():
    assert quirk_for("hw50w7qvxluhslkk") is not None
    for raw, applies in ((600, True), (300, False), ("600", False), (None, False)):
        s = apply_quirk(DeviceSchema("x", "kt", "hw50w7qvxluhslkk", status={"temp_set": raw} if raw is not None else {}))
        assert ("temp_set" in s.status_range) == applies, raw
    assert apply_status_quirk({"ops": [{"op": "MapInitialStatus", "code": "a", "mapping": [["true", True]]}]}, {"a": "true"}) == {"a": True}
    assert device_info(DeviceSchema("x", "c", "nothing", product_name="P"))["manufacturer"] == "Tuya"
    di = device_info(DeviceSchema("x", "c", "avriaapskyik4eaa", product_name="P"))
    assert di["manufacturer"] == "Konyks" and di["model_id"] is None


def test_invert_int_max_reaches_cover():
    """Quirk TypeOverride: the inverted TypeInformation cancels the cover wrapper's own inversion."""
    from tuya2ildevice.tuya import platforms  # noqa: F401
    from tuya2ildevice.tuya.runtime import classify
    rng = '{"unit":"%","min":0,"max":100,"scale":0,"step":1}'
    def plan(pid):
        sch = DeviceSchema("x", "cl", pid,
                           function={"control": DpSpec("control", "Enum", '{"range":["open","close","stop"]}'),
                                     "percent_control": DpSpec("percent_control", "Integer", rng)},
                           status_range={"percent_state": DpSpec("percent_state", "Integer", rng)},
                           status={"percent_state": 30})
        sch = apply_quirk(sch)
        return next(e for e in classify(sch, platforms=("cover",)).entities if e.key == "control")
    plain, inv = plan("unmapped_pid"), plan("68nvbio9")
    st = {"percent_state": 30}
    assert plain.read(st)["current_position"] == 70            # core default: percentage inverted
    assert inv.read(st)["current_position"] == 30              # quirk pre-inverts, wrapper's inversion cancels
    assert plain.write("set_cover_position", {"position": 25}, st) == [{"code": "percent_control", "value": 75}]
    assert inv.write("set_cover_position", {"position": 25}, st) == [{"code": "percent_control", "value": 25}]


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
