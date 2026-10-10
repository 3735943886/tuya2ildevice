"""Freeze numeric conformance vectors from the existing Python implementation."""
import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests/reference"))

from tuya2ildevice_reference.tuya.model import SpecInteger
from tuya2ildevice_reference.tuya.ops import validate_int_read, validate_int_write
from tuya2ildevice_reference.tuya.runtime import WriteRejected

cases = []
for scale in (0, 1, 3):
    for inverted in (False, True):
        spec = SpecInteger(-100, 1000, scale, 1, inverted=inverted)
        for direction, values in (
            ("read", [None, True, False, -101, -100, 0, 1, 999, 1000, 1001, 1.0, "1"]),
            ("write", [None, True, False, -101, -0.5, 0, 0.0005, 0.0015, 1.5, 2.5, 1001, "1"]),
        ):
            ops = []
            if direction == "read":
                ops.extend([{"op": "validate_integer", "min": spec.min, "max": spec.max},
                            {"op": "scale", "scale": scale}])
            else:
                ops.append({"op": "validate_number"})
            if inverted:
                ops.append({"op": "invert", "max": spec.max / 10**scale})
            if direction == "write":
                ops.extend([{"op": "unscale", "scale": scale}, {"op": "round_half_even"},
                            {"op": "validate_range", "min": spec.min, "max": spec.max}])
            for value in values:
                try:
                    expected = {"ok": True, "value": (validate_int_read if direction == "read" else validate_int_write)(spec, value)}
                except WriteRejected:
                    expected = {"ok": False}
                cases.append({"name": f"{direction}/{scale}/{inverted}/{value!r}",
                              "request": {"version": 1, "value": value, "ops": ops},
                              "expected": expected})

target = Path(__file__).resolve().parents[2] / "rust/engine/tests/numeric_vectors.json"
target.write_text(json.dumps(cases, indent=2) + "\n")
print(f"Exported {len(cases)} Python oracle vectors to {target}")
