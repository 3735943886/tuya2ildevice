"""Chain check: every Home Assistant core tuya fixture through this package's descriptor and il-ha's HA-free planner, compared
with core's own entity snapshots. Needs `il-ha` (its `il_ha.core`); without it these tests are not collected."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

try:
    import il_ha.core  # noqa: F401
except ImportError:
    collect_ignore_glob = ["test_*.py"]
