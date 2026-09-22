# tuya2ildevice

The one place that interprets Tuya. Sans-IO Python: takes a Tuya device as [rustuya](https://github.com/3735943886/rustuya) /
[rustuya-bridge](https://github.com/3735943886/rustuya-bridge) know it and produces an
[ildevice](https://github.com/3735943886/ildevice): descriptor, live values and events from dps packets, and dps for
ildevice commands. It opens no sockets and reads no clock. Other projects
([rustuya-homeassistant](https://github.com/3735943886/rustuya-homeassistant), ildevice hosts,
[rustuya-manager](https://github.com/3735943886/rustuya-manager)) import this instead of carrying their own Tuya knowledge.

```
pip install tuya2ildevice              # no dependencies;  tuya2ildevice[host] adds paho-mqtt for `tuya2ildevice.host`,  [test] for the tests
```

```python
from tuya2ildevice import TuyaDriver, Connected, Message, Command, descriptor_of

descriptor_of(device)                 # just the ildevice descriptor of a tuyadevices.json entry
d = TuyaDriver(device)                # the state machine (il.md section 8)
d.handle(now, Connected())            # -> [Descriptor]
d.handle(now, Message("active", {"1": True}))   # -> [Value(...), Event(...)]
d.handle(now, Command("switch_1", "off"))    # -> [SendMessage("set", {"dps": {"1": False}})]  or  [Reject(...)]
```

## Packets and commands

- `Message(channel, json)`: `active` (device push: fires events, accumulates `add_ele`-style deltas) or `passive` /
  `state` (readback, snapshot: values only). `json` is a flat `{dp: value}` map (the host has already decoded whatever
  the bridge's own wire payload looked like — see `Hub.on_bridge_message` below).
- `Command(prop, value)` is checked as il.md section 5 says before anything is sent; failures come back as `Reject`.
  Values convert as in Home Assistant core (brightness goes through 0..255; cover position is reversed unless
  `control_back_mode` is `back`), so a written value can differ slightly from what is read back.
- Covers of class garage/gate are read only unless `TuyaDriver(..., allow_hazardous=True)` (il.md S-1).
- A dp no platform table claims gets no property, as in Home Assistant core; `TuyaDriver(..., expose_unused=True)` gives
  each one a property chosen by its Tuya type (rustuya-homeassistant v1 did that). Integer, Enum and Boolean are writable only if the dp is
  in `function`; String, Raw, Json and Bitmap are read-only. `config` if writable, else `diagnostic`.
- Assembled: switch, button, select, number, sensor, binary_sensor, event, light, cover, fan, siren, valve,
  humidifier, climate, alarm (kind `alarm`), vacuum. Not assembled: camera (a stream is outside the IL; `driver.unsupported` lists it).
  A doorbell `alarm_message` event loses its message text (an ildevice event has only a kind).

## User overrides

`TuyaDriver(device, overrides=mapping)` / `Hub(devices, overrides=mapping)`; `mapping` is `{product_id or device id: block}`,
already loaded (the host reads files; `merge_all([...])` merges several, later wins). Details and the block format are in
[overrides.py](src/tuya2ildevice/overrides.py):

```json
{ "5rta89nj": {
    "dp":     {"104": {"code": "percent_control", "type": "Integer", "values": {"min": 0, "max": 100, "scale": 0, "step": 1}}},
    "remove": ["cycle_time"],
    "props":  {"countdown_1": {"label": "Timer", "category": "config", "rw": false}},
    "device": {"model": "Sliding Window Opener"} } }
```

`dp`, `remove`, `category` patch the schema before classification (a defined dp is classified like any other);
`props` (by property name) and `device` patch the descriptor. Unknown keys raise `OverrideError`.
`Hub.reload(mapping)` applies new overrides live: republishes changed descriptors and clears removed properties.
`from_v1(custom_converters)` converts rustuya-homeassistant `dp_meta`; `discovery_overrides` (HA payload fields) are
dropped with a warning.

## Code converters

The equivalent of v1's `custom_converters/*.py`: a per-device `Converter` object sees the driver's dp state on every
packet and returns derived property values (and timer requests, since it cannot read a clock). See
[converters.py](src/tuya2ildevice/converters.py).

```python
class MyConverter(Converter):
    def props(self):  return {"motion": {"type": "select", "role": "motion", "options": ["opening", "closing", "stopped"]}}
    def update(self, now, codes, changed, active):  return {"motion": "stopped"}      # or Result(values=..., timers=...)

TuyaDriver(device, converters={"<product_id or device id>": [lambda device: MyConverter()]})
```

Built-in ones are named in an override block: `{"<product_id>": {"converters": {"cover_motion": {"settle": 5}}}}`.
`cover_motion` (ported from v1's `00_curtain.py`) derives the cover's `motion` role (opening / closing / stopped) from the
control, set-position and position dps; a snapshot never starts motion. Timers come out as `SetTimer` (driver) or
`Schedule` (`Hub`); the host calls back with `Timer` / `hub.on_timer(now, id, name)`. Loading a user's `.py` file is the
host's job; the code runs in-process.

## tuya2ildevice <-> an IL host over MQTT

`Hub` ([mqtt.py](src/tuya2ildevice/mqtt.py)) owns one driver per device and maps il-mqtt.md on the IL side. On the
bridge side it takes already-decoded input, not a raw topic/payload: `on_bridge_message(now, device_id, inp,
retained=False)` where `inp` is `Connected()` / `Disconnected()` / `Message(channel, dps)`, and its writes/reads to
the bridge come out as abstract `BridgeCommand(device_id, action, dps)` — Hub has no idea rustuya-bridge's own MQTT
topics exist, let alone that they're configurable. Rendering `BridgeCommand` into a real topic+payload, and turning
a real bridge MQTT message into `Connected`/`Disconnected`/`Message`, is the **host's** job — correctly, that means
using [pyrustuyabridge](https://github.com/3735943886/rustuya-bridge)'s bindings (`match_topic`, `render_template`,
`tpl_to_wildcard`, `parse_seed_dps`), which mirror the real bridge's own template/payload parsing, not a hand-rolled
one. [rustuya-local](https://github.com/3735943886/rustuya-local) is that host for a real rustuya-bridge; its
`bridge_client` module is the reference implementation.

`tuya2ildevice.host` is the *IL-side* host: `Runner` drives a `Hub` on one IL transport (`MqttTransport` over paho, or
the in-process `InProcessTransport`), keeps the Last Will presence (M-12), reconnects, adds/removes devices while
running (`set_device`, `remove_device`, `sync_devices`), follows rustuya-manager's `tuyadevices.json`
(`DeviceWatcher`), and routes every `BridgeCommand` Hub produces through an injected `on_bridge_command` callback —
supplied by whatever owns the real bridge connection — plus a matching `runner.on_bridge_message(device_id, inp,
retained=False)` entry point for feeding decoded bridge input back in.

| direction | shape |
|---|---|
| bridge -> hub | `runner.on_bridge_message(device_id, Connected() / Disconnected() / Message(channel, {dp: value}))` |
| hub -> bridge | `BridgeCommand(device_id, "set"/"get", dps)` via `on_bridge_command` (a `get` after each connect) |
| hub -> IL host | il-mqtt.md: retained `il/<id>` and `il/<id>/<prop>`; events and `il/<id>/reject` not retained; `il/_producer/tuya` presence |
| IL host -> hub | `il/<id>/<prop>/set` (retained writes ignored) |

Register the devices on the bridge yourself (`add`); the hub never touches keys.

## Layout

```
src/tuya2ildevice/
  driver.py    TuyaDriver: packets/commands <-> outputs          io.py      inputs and outputs as data
  assemble.py  engine entity plans -> ildevice props/roles       checks.py  il.md section 5 command checks
  mqtt.py      Hub, IlTopics                                     tuya/      the DP engine (see below)
tuya/          classify + platforms, adapter (raw dps <-> values), quirks, ops, codecs, units
tuya/tables/   per-platform description tables, generated from HA core     tuya/quirks/   from tuya-device-handlers
tuya/data/     HA's allowed units per device class
scripts/       generators for those data files, and golden/ (builds the golden data; needs HA core + oracle venvs)
docs/          engine-spec.md (the engine's behaviour), analysis/ (how it was derived from HA core)
tests/         golden/ = 324 HA core fixtures + core's own snapshots; chain/ = the same through an IL host's planner
```

The engine reproduces Home Assistant core's `tuya` integration exactly, including its quirks, and the golden tests
pin it: `tests/golden/golden.json` is core's own entity snapshots for every fixture. To follow a new HA core /
tuya-device-handlers release, run the generators in `scripts/` against it, then the tests; a difference is either
a real change to adopt or a regression.

## Tests

```
pip install -e .[test] && python -m pytest
```

`tests/test_adapter.py` compares the raw-dps adapter with the Tuya SDK and is skipped unless `tuya-device-sharing-sdk==0.2.15`
is installed. The spec repository (`../ildevice`, or `$ILDEVICE`) supplies the schema and the language-neutral vectors
(commands, wire values, topics) read directly from its checkout; those tests are skipped if it is not there. `tests/chain/`
compares every Home Assistant core tuya fixture with core's entity snapshots through an IL host's HA-free planner
(needs that host's `ildevice.core` on `sys.path`; not collected without it).
