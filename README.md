# tuya2ildevice

The one place that interprets Tuya. Sans-IO Python: takes a Tuya device as [rustuya](../rustuya) /
[rustuya-bridge](../rustuya-bridge) know it and produces an [ildevice](../ildevice): descriptor, live values and
events from dps packets, and dps for ildevice commands. It opens no sockets and reads no clock. Other projects
(rustuya-homeassistant, il-ha hosts, rustuya-manager) import this instead of carrying their own Tuya knowledge.

```
pip install .              # no dependencies;  .[host] adds paho-mqtt for `tuya2ildevice.host`,  .[test] for the tests
```

```python
from tuya2ildevice import TuyaDriver, Connected, Message, Command, descriptor_of

descriptor_of(device)                 # just the ildevice descriptor of a tuyadevices.json entry
d = TuyaDriver(device)                # the state machine (il.md section 8)
d.handle(now, Connected())            # -> [Descriptor]
d.handle(now, Message("active", {"data": {"dps": {"1": True}}}))   # -> [Value(...), Event(...)]
d.handle(now, Command("switch_1", "off"))    # -> [SendMessage("set", {"dps": {"1": False}})]  or  [Reject(...)]
```

## Packets and commands

- `Message(channel, json)`: `active` (device push: fires events, accumulates `add_ele`-style deltas) or `passive` /
  `state` (readback, snapshot: values only). Payload: `{"dps":{}}`, `{"data":{"dps":{}}}` or bare `{"1":true}`.
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

## rustuya-bridge <-> tuya2ildevice <-> il-ha over MQTT

`Hub` ([mqtt.py](src/tuya2ildevice/mqtt.py)) owns one driver per device and maps both sides. Its methods take what was
received and return `Publish(side, topic, payload, retain, qos)`; the host only executes them.
`tuya2ildevice.host` is the host: `Runner` drives a `Hub` on two transports (`MqttTransport` over paho, or the in-process
`InProcessTransport`), keeps the Last Will presence (M-12), reconnects, adds/removes devices while running
(`set_device`, `remove_device`, `sync_devices`) and follows rustuya-manager's `tuyadevices.json` (`DeviceWatcher`).
`read_bridge_config` + `BridgeTopics.from_config` take the topic layout the bridge announces on `{root}/bridge/config`.

| direction | topic |
|---|---|
| bridge -> hub | `rustuya/event/{active,passive,state}/<id>` (dps JSON, or single-DP), `rustuya/error/<id>` (`errorCode` 0 = connected) |
| hub -> bridge | `rustuya/command` `{"action":"set"/"get",...}` (a `get` after each connect) |
| hub -> il-ha | il-mqtt.md: retained `il/<id>` and `il/<id>/<prop>`; events and `il/<id>/reject` not retained; `il/_producer/tuya` presence |
| il-ha -> hub | `il/<id>/<prop>/set` (retained writes ignored) |

Run the bridge with `mqtt_retain: true` and register the devices in it yourself (`add`); the hub never touches keys.

## Layout

```
src/tuya2ildevice/
  driver.py    TuyaDriver: packets/commands <-> outputs          io.py      inputs and outputs as data
  assemble.py  engine entity plans -> ildevice props/roles       checks.py  il.md section 5 command checks
  mqtt.py      Hub, BridgeTopics, IlTopics                       tuya/      the DP engine (see below)
tuya/          classify + platforms, adapter (raw dps <-> values), quirks, ops, codecs, units
tuya/tables/   per-platform description tables, generated from HA core     tuya/quirks/   from tuya-device-handlers
tuya/data/     HA's allowed units per device class
scripts/       generators for those data files, and golden/ (builds the golden data; needs HA core + oracle venvs)
docs/          engine-spec.md (the engine's behaviour), analysis/ (how it was derived from HA core)
tests/         golden/ = 324 HA core fixtures + core's own snapshots; spec/ = schema and vectors copied from ildevice
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
is installed. `tests/spec/` is a copy of ildevice's `schema/` and `vectors/`; refresh it when the spec changes.

## Tests

`pytest`. The spec repository (`../ildevice`, or `$ILDEVICE`) supplies the schema and the language-neutral vectors
(commands, wire values, topics); those tests are skipped if it is not there. `tests/chain/` compares every Home Assistant
core tuya fixture with core's entity snapshots through il-ha's HA-free planner (needs `il-ha`; not collected without it).
