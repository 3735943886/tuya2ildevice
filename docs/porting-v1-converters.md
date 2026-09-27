# Porting a rustuya-homeassistant v1 `.py` converter

rustuya-homeassistant's code converters were `setup(api)` plugins for rustuya-manager's runtime: async handlers
that received a device's dps by number and published *derived* dps, which a JSON `dp_meta` entry then turned into a
Home Assistant entity. tuya2ildevice's `Converter` does the same job without I/O: one object per device sees the
device's dp values by code on every packet and returns ildevice property values. `load_overrides` reports a v1 file
(`a rustuya-homeassistant v1 plugin (setup(api)); not loaded`) instead of running it; port it as below.

JSON files need no porting: `from_v1` converts `dp_meta`, `model` and `discovery_overrides.cover` on load.

## The API, side by side

| v1 (`setup(api)`) | `Converter` |
|---|---|
| `@api.on_product(pid)` / `@api.on_device(id)` | turn the converter on for that product or device in an override block: `{"<pid or id>": {"converters": {"<name>": {...config...}}}}` |
| `handler(device_id, dps, origin)`, dps by number | `update(now, codes, changed, active)`: `codes` is every dp value by **code** (after `remap`), `changed` the codes in this packet |
| `@api.on_dp(id, dp)` | check `"<code>" in changed` in `update` |
| `origin == "retained"` (a snapshot replay) | `active is False` (a readback or snapshot, not a push from the device) |
| `await api.derive(id, dp, value)` + a `dp_meta` entry for that dp | declare the property in `props()` and return `{"<property>": value}` from `update`; `None` makes it absent |
| `await api.clear(id, dp)` | return `{"<property>": None}` |
| `api.current_dps(id)` to seed state | not needed: `codes` always holds everything the driver knows |
| keeping state between packets | attributes on the converter (one object per device); `reset()` is called when the link drops |
| `asyncio.sleep` / a delay | ask for a timer: `Result(values=..., timers={"name": seconds})`, then `timer(now, name, codes)` is called |
| `await api.set_dp(id, dp, value)` | not in a converter: converter properties are read only. Writes go through the device's own properties (use `remap` or a `dp` override to shape them) |
| `api.service(...)` | no equivalent: run a long-lived task in the host, outside the converter |

## An example

A v1 plugin that flags a filter for replacement from dp 5 (`filter_life`, a percentage) as a derived dp 99:

```python
def setup(api):
    @api.on_product("abc123")
    async def _(device_id, dps, origin):
        life = dps.get("5")
        if life is not None:
            await api.derive(device_id, "99", life < 10)
```

The same as a `Converter`, in a `.py` file of the `custom_converters/` directory:

```python
from tuya2ildevice import Converter


class FilterLow(Converter):
    def __init__(self, config):
        self.code = config.get("code", "filter_life")
        self.below = config.get("below", 10)

    def props(self):
        return {"filter_low": {"type": "binary", "class": "problem", "label": "Filter"}}

    def update(self, now, codes, changed, active):
        life = codes.get(self.code)
        return {"filter_low": None if life is None else life < self.below}


CONVERTERS = {"filter_low": FilterLow}
```

and turned on for the product in a `.json` file of the same directory (this also replaces the v1 `dp_meta` entry for
dp 99, which no longer exists):

```json
{"abc123": {"converters": {"filter_low": {"below": 15}}}}
```

`tests/test_porting_guide.py` loads exactly these two files and drives a device with them.

## Things that differ

- **Codes, not numbers.** A converter never sees dp numbers. If the schema lacks the dp you need, define it in the
  override block first (`"dp": {"105": {"code": "my_code", "type": "Integer", "values": {...}}}`), then read
  `codes["my_code"]`.
- **Declared up front.** `props()` is read when the driver is made; a converter cannot add properties later.
- **No I/O, no clock.** `update` and `timer` must return quickly and must not await anything. Use `now` for time.
- **Reloading.** Unlike v1, `.py` files are re-imported when the directory changes (`OverrideWatcher`); no restart.
