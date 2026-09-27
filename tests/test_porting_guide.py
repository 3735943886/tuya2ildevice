"""docs/porting-v1-converters.md's example, taken from the document itself: the ported converter file and the override
that turns it on, loaded as a custom_converters directory and driving a device."""
import pathlib
import re

from helpers import fn, strat

from tuya2ildevice import Connected, Message, TuyaDriver, Value
from tuya2ildevice.host import load_overrides

GUIDE = pathlib.Path(__file__).resolve().parents[1] / "docs" / "porting-v1-converters.md"


def purifier():
    life = fn("filter_life", "Integer", unit="%", min=0, max=100, scale=0, step=1)
    f = {"switch": fn("switch", "Boolean"), "filter_life": life}
    return {"id": "pur1", "category": "kj", "product_id": "abc123", "name": "Purifier", "product_name": "Purifier",
            "function": {"switch": f["switch"]}, "status_range": f, "status": {},
            "local_strategy": strat({"1": ("switch", "Boolean"), "5": ("filter_life", "Integer")})}


def test_the_guides_example_ports_the_v1_plugin(tmp_path):
    text = GUIDE.read_text()
    code = re.search(r"in a `\.py` file of the `custom_converters/` directory:\n\n```python\n(.*?)```", text, re.DOTALL).group(1)
    block = re.search(r"```json\n(.*?)```", text, re.DOTALL).group(1)
    (tmp_path / "filter_low.py").write_text(code)
    (tmp_path / "10_filter.json").write_text(block)
    loaded = load_overrides(tmp_path)
    assert loaded.warnings == []

    d = TuyaDriver(purifier(), overrides=loaded.overrides, converter_types=loaded.converter_types)
    assert d.descriptor["props"]["filter_low"]["class"] == "problem"
    d.handle(0, Connected())
    values = {o.prop: o.value for o in d.handle(1, Message("state", {"1": True, "5": 12})) if isinstance(o, Value)}
    assert values["filter_low"] is True                                     # 12 % < the configured 15
    values = {o.prop: o.value for o in d.handle(2, Message("active", {"5": 80})) if isinstance(o, Value)}
    assert values["filter_low"] is False
