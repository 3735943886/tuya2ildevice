"""Exercise the actual shared library and Python wrapper against frozen vectors."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import os
sys.path.insert(0, str(ROOT / 'src'))
from tuya2ildevice.native import _call

os.environ['TUYA_ENGINE_LIBRARY'] = sys.argv[1]
cases = json.loads((ROOT / "rust/engine/tests/numeric_vectors.json").read_text())
for case in cases:
    try:
        value = _call(case["request"])
    except ValueError:
        assert not case["expected"]["ok"], case["name"]
    else:
        assert case["expected"]["ok"], case["name"]
        assert value == case["expected"]["value"], case["name"]
print(f"FFI passed {len(cases)} Python oracle vectors")
