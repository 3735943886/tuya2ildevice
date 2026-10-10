# tuya2ildevice

The one place that interprets Tuya. Sans-IO Python: takes a Tuya device as [rustuya](https://github.com/3735943886/rustuya) /
[rustuya-bridge](https://github.com/3735943886/rustuya-bridge) know it and produces an
[ildevice](https://github.com/3735943886/ildevice): descriptor, live values and events from dps packets, and dps for
ildevice commands. It opens no sockets and reads no clock. Other projects
([rustuya-local](https://github.com/3735943886/rustuya-local), ildevice hosts,
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

- Numeric properties use the native unit of their values. For example, a plug reporting `500 mA` publishes
  `500` with unit `mA`; a suggested display unit such as `A` does not change the wire unit or magnitude.
- `Message(channel, json)`: `active` (device push: fires events, accumulates `add_ele`-style deltas) or `passive` /
  `state` (readback, snapshot: values only; with `delta.accept_passive`, a passive report adds its delta too, see
  [Delta settings](#delta-settings)). `json` is a flat `{dp: value}` map (the host has already decoded whatever
  the bridge's own wire payload looked like — see `Hub.on_bridge_message` below).
- `Command(prop, value)` is checked as il.md section 5 says before anything is sent; failures come back as `Reject`.
  Values convert as in Home Assistant core (brightness goes through 0..255), so a written value can differ slightly
  from what is read back. A cover's position is the device's own number, never mirrored (core mirrors it); a device
  that counts the other way is set so by its cover settings (below).
- A `passive` report (a live one: the host drops retained ones) that changes a value is the device's own push, like
  `active`: converters see it as live; one that changes nothing is a readback. Events fire only from `active`. `state`, the bridge's
  merged view of what `active` / `passive` carried, is a snapshot: the first one after a connect sets everything, a
  later one only what it alone brings, and never as a push. An increment (`report_type: sum`) is added only from
  `active`.
- Covers of class garage/gate are read only unless `TuyaDriver(..., allow_hazardous=True)` (il.md S-1).
- A dp no platform table claims gets no property, as in Home Assistant core; `TuyaDriver(..., expose_unused=True)` gives
  each one a property chosen by its Tuya type. Integer, Enum and Boolean are writable only if the dp is
  in `function`; String, Raw, Json and Bitmap are read-only. `config` if writable, else `diagnostic`.
- Assembled: switch, button, select, number, sensor, binary_sensor, event, light, cover, fan, siren, valve,
  humidifier, climate, alarm (kind `alarm`), vacuum. Not assembled: camera (a stream is outside the IL; `driver.unsupported` lists it).
  A doorbell `alarm_message` event loses its message text (an ildevice event has only a kind).

## User overrides

Tuya devices are fragmented: a cloud schema can give a dp a non-standard code, leave a dp out, speak other words
(`on`/`off`/`pause` instead of `open`/`close`/`stop`) or run a position the other way. Overrides fix that in Tuya's own
terms, before classification, so the fixed device goes through the same tables as any other and every IL consumer
(il-ha, a discovery publisher, ...) sees the fix.

`TuyaDriver(device, overrides=mapping)` / `Hub(devices, overrides=mapping)`; `mapping` is `{product_id or device id: block}`,
already loaded (`merge_all([...])` merges several, later wins; `tuya2ildevice.host.load_overrides` reads a directory).
Details and the block format are in [overrides.py](src/tuya2ildevice/overrides.py):

```json
{ "<product_id>": {
    "dp":     {"104": {"code": "percent_control", "type": "Integer", "values": {"min": 0, "max": 100, "scale": 0, "step": 1}},
               "101": {"code": "control"}},
    "remove": ["percent_state"],
    "category": "cl",
    "remap":  {"control": {"alias": {"on": "open", "off": "close", "pause": "stop"}}},
    "cover":  {"invert_position": true, "settle": 5},
    "delta":  {"accept_passive": true},
    "props":  {"countdown_1": {"label": "Timer", "category": "config", "rw": false}},
    "device": {"model": "Sliding Window Opener"},
    "converters": {"glow": {"on": "bright"}},
    "expose_unused": true } }
```

| key | fixes |
|---|---|
| `dp` with a `type` | a dp the schema lacks or describes wrongly (defined like a quirk's `DefineDp`) |
| `dp` with only a `code` | a dp with a non-standard code: renamed, its type, range and value strategy kept |
| `remove` | a dp that should not be used (the tables then fall back, e.g. position read from the target) |
| `category` | the Tuya category the tables are chosen by |
| `remap.<code>.alias` | other words: device value -> standard value, both ways (an Enum's range is translated too) |
| `remap.<code>.invert` | the other direction: a Boolean negated, an Integer mirrored in its range (not for a cover's position or command: use `cover`) |
| `cover` | a cover's direction and motion (below) |
| `props`, `device` | the finished descriptor: label, class, category, unit, role, read only, hidden; device kind/class/label/model |
| `props.<name>` with a `src` | a property defined from a dp (below), replacing one of that name |
| `converters` | code converters by name (below) |
| `expose_unused` | this device only: every dp no table claims gets a property of its own |
| `auto: false` | no property from the tables at all: only what `props` defines and the converters give |

A property defined from a dp builds what the tables cannot, such as a cover from a position dp alone (a window opener
whose category has no cover table):

```json
{ "<product_id>": {
    "dp":     {"104": {"code": "percent_control", "type": "Integer", "mode": "RW",
                       "values": {"unit": "%", "min": 0, "max": 100, "scale": 0, "step": 1}}},
    "device": {"kind": "cover", "class": "window"},
    "props":  {"position": {"src": "percent_control", "role": "position"},
               "open":     {"src": "percent_control", "type": "trigger", "role": "open", "send": 100},
               "close":    {"src": "percent_control", "type": "trigger", "role": "close", "send": 0},
               "battery":  {"src": "residual_electricity", "role": "battery", "category": "diagnostic"}} } }
```

Its `type` follows the dp's (Boolean `binary`, Integer `number`, Enum `select`, others `text`) unless given; `min`,
`max`, `step`, `unit` and `options` come from the dp unless given; it is writable when the dp is in `function` (`rw:
false` makes it read only); a `trigger` writes its `send` value. Values go through `remap` like any other.

Unknown keys raise `OverrideError`. `Hub.reload(mapping)` applies new overrides live: republishes changed descriptors,
clears removed properties, and works a changed device's values out again from the dps it last had.

### Cover settings

Which way a cover's position and commands run, and whether its motion is inferred, are the cover's settings, not
`remap`: a curtain whose direction is wrong is fixed from its own configuration switches. `Hub(devices,
device_settings=True)` (a host with somewhere to keep them) offers each switch only where it changes something:

| switch (property) | label | offered when the cover has | does |
|---|---|---|---|
| `cover_invert_position` | Invert current position | a reported position (`current_position`) | reads it the other way, 0..100; with one dp for position and target, writes it the other way too |
| `cover_invert_set_position` | Invert target position | a target (`set_position`) that is another dp | writes the target, and reads it back, the other way |
| `cover_invert_control` | Invert control | a command dp (`control`) and no target | swaps open and close (`FZ` / `ZZ` on the special command; a Boolean negated) |
| `cover_position_from_target` | Use target as current position | two distinct position sources before selection | ON uses the target DP for display; OFF restores the original feedback DP; the switch stays available |
| `cover_state_source` | Motion state source | a position or control DP, with no user converter owning this cover state | select `control` (control reports), `inferred` (position-based motion inference), or `none` (no motion generation); `control` requires a control DP |
| `cover_invert_reported_motion` | Invert reported motion | control report mode | swaps only the reported opening/closing meaning, leaving stop unchanged |

A cover with a target opens and closes by writing it (100 / 0), and its command dp only stops it: it has no *Invert
control*. The switches are `binary`, the state source is `select`; all are `rw`, `category: config`, and show the settings in effect; a hazardous cover's
(garage, gate, ...) are read only unless `allow_hazardous`. A later cover of the device (`control_2`, ...) has its own,
named by its group (`control_2_cover_invert_position`).

Writing a switch sends nothing to the device: the driver answers with the value and `SettingsChanged(block)`, and the
Hub with `SaveSettings(device_id, block)`, the device's whole settings block (e.g. `{"cover": {"invert_position":
true}}`) for the host to keep. `tuya2ildevice.host` keeps it in `zz_settings.json` (`host.SETTINGS_FILE`) in the
overrides directory (`Runner(..., on_settings=watcher.save_settings)`), which then reloads at once.

The same settings are a `cover` block in any override file, where a product's block is the starting point for its
devices and a device's (the switches write the device's) wins:

| key | default | |
|---|---|---|
| `invert_position` | `false` | as the switch |
| `invert_set_position` | `false` | as the switch |
| `invert_control` | `false` | as the switch |
| `infer_motion` | `device_settings` | legacy Boolean, used only when `state_source` is absent; true maps to `inferred`, false to `none` |
| `state_source` | legacy `infer_motion` | `control`, `inferred`, or `none`; an explicit value takes precedence over the legacy Boolean |
| `invert_reported_motion` | `false` | invert only direct control reports |
| `settle` | `0` | seconds without a position report after which a move counts as stopped (0: never) |
| `invert_tilt` | `false` | the tilt the other way |
| `position_from_target` | `false` | the reported position dp is left out and the target read as the position |

A later cover's keys go in an object under its group's name: `{"cover": {"invert_position": true, "control_2":
{"invert_position": true}}}`. An inversion is laid over the dp's `remap` (a `remap.invert` still there cancels it), and
the swap is composed with the dp's `remap.alias`, so a device that says `on` / `off` / `pause` keeps its alias.
`remap.<code>.invert` on a cover's position or command dp and `converters.cover_motion` still work, and log that they
are deprecated: move them to `cover`.

`control` maps live open/close/stop reports directly to opening/closing/stopped, even without
position feedback. Boolean true/false and FZ/ZZ/STOP use the same control vocabulary as inference.
The order on input is `remap.alias`, existing `invert_control` (where applicable), then
`invert_reported_motion`. The last setting swaps only opening/closing from control reports;
it never changes sent commands, positions, inferred direction, or endpoint states. Live stop
is always stopped. Position updates alone do not settle a directly reported movement.

### Delta settings

A delta dp (`report_type: sum`, a plug's `add_ele`) reports the energy used since its last report, not a meter
reading: its property is the running total tuya2ildevice adds up (`series: counter`), so a repeated increment (`5`,
then `5` again) adds twice. The total is published in the DP’s native unit with its `scale` applied: for
`scale: 3` and unit `kWh`, a raw increment of `5` adds `0.005 kWh`. By default only an `active` push adds: a passive report is a readback of what the device
already pushed. A device that never pushes it, and reports it passively only, needs passive reports counted too:

| switch (property) | label | offered when the device has | does |
|---|---|---|---|
| `delta_accept_passive` | Count passive reports | a delta dp | a live passive report that carries the dp adds it as well, each one, even one that repeats the last value |

It is the `delta` block's `accept_passive` (default `false`) in an override file, and saves as the cover switches do
(`{"delta": {"accept_passive": true}}`). One report seen twice with the same Tuya `t` adds once; a readback the host
asks for (a `get`, on a reconnect) carries no new increment but adds once more, so turn it on only for a device that
does not push. A reload (an override or a setting changed) keeps the totals.

Retained snapshots/readbacks (`state`, and non-live passive reports) never start motion: their
control may be the last command. A relevant snapshot reports only the position's resting state
(closed at 0, open at 100, stopped between), or no state without position. Disconnect clears
motion; reconnect/reload seeds only resting state and waits for a live report. Duplicate live
reports do not republish unchanged state. Direct report mode has no settle timer.

`inferred` preserves CoverMotion: a control word can start motion; subsequent position feedback
corrects its direction, while an independent target can be compared with current position.
A shared current/target DP is never compared against itself. Without independent feedback,
command direction and arrival cannot be verified; a target-only update does not prove movement.
`none` generates only resting state from position and never movement. Legacy `infer_motion:false`
keeps its previous behavior of omitting the separate cover_state property (position remains
available); explicitly selecting `none` also provides the resting state property.

The old Infer motion switch is replaced by the state source select, not exposed alongside it.
Existing JSON and saved `zz_settings.json` Booleans remain readable; the select shows their mapped
mode and subsequent selections save `state_source`. Explicit state_source wins even if an older
infer_motion entry remains. A user converter owning a cover's `cover_state` retains ownership:
that cover gets neither the built-in state producer nor its source/direction controls. Other
covers remain independent, including when legacy `converters.cover_motion` owns the first one.

Settings remain saved while their controls are hidden: target inversion is dormant while current
and target share a DP, and reported motion inversion is dormant outside control mode. Switching
back restores them. Current-position inversion follows the selected display source; when that
source is also the target, it inverts both reads and writes. Property IDs and cover group IDs
stay stable through source changes. Host reload cancels old timers and clears the old cover state
before publishing a fresh snapshot. A bare sans-IO caller must save SettingsChanged and reload,
just as for the existing inversion switches.

For **f6jujmx0is5td50x**, remove its old `remove`, `remap` and `converters.cover_motion` JSON
entries, then configure the device (no product ID is hardcoded):

1. **Use target as current position**: ON.
2. **Invert current position**: ON.
3. **Motion state source**: `control`.
4. **Invert reported motion**: ON.

This selects percent_control (DP 2 in the referenced schema), inverts its reads and writes, and
reverses only the reported motion. The DP name does not establish that it reports physical position:
if firmware reports only a target, the displayed position is only that target; intermediate travel,
arrival and physical resting position cannot be determined from it.

The previous rustuya-homeassistant JSON selected DP 2, inverted position reads and target writes,
and mapped control directly; it was not enrolled in the Python converter because it had no
`state_stream: "derived"`. Its MQTT open/close commands used control open/close. This package
continues to send target 100/0 when set_position exists (after position inversion), so its command
path is **different from that earlier implementation**. Report inversion does not change that path.

The package carries no block for any product or device. A fix for a model goes in a converters file: the user's own,
or the [override pack](pack/README.md), which hosts copy into that directory.

## Code converters

A per-device `Converter` object sees the driver's dp state (after `remap`) on every packet and returns property values
(and timer requests, since it cannot read a clock). Its properties may replace the tables' ones of the same name, and
one with `rw: true` (or a trigger) is written through its `write(prop, value, codes)`, which returns the dps to send as
`{code: value}`. See [converters.py](src/tuya2ildevice/converters.py).

```python
class MyConverter(Converter):
    def __init__(self, config): ...
    def props(self):  return {"filter_low": {"type": "binary", "class": "problem"}}
    def update(self, now, codes, changed, active):  return {"filter_low": codes.get("filter_life", 100) < 10}  # or Result(...)
    def write(self, prop, value, codes):  return {"<code>": value}        # only for rw / trigger properties

TuyaDriver(device, converters={"<product_id or device id>": [lambda device: MyConverter({})]})
TuyaDriver(device, converter_types={"my": MyConverter}, overrides={"<product_id>": {"converters": {"my": {}}}})
```

The built-in `CoverMotion` gives a cover its `cover_state` role (open / closed / opening / closing / stopped); the
cover settings turn it on (`state_source: "inferred"`, or legacy `infer_motion`) on the dps the cover actually has, after its inversions. A command word
only says a move started, the position reports after it decide which way; a snapshot never starts motion. Naming it
in a block (`{"converters": {"cover_motion": {...}}}`) still works, deprecated. Timers come out as `SetTimer` (driver)
or `Schedule` (`Hub`); the host calls back with `Timer` / `hub.on_timer(now, id, name)`.

## Overrides as files

`tuya2ildevice.host.load_overrides(path)` reads a `custom_converters/` directory (or one `.json` file) and
`OverrideWatcher(path, runner, base=...)` follows it, reloading the Hub when a file changes:

- `*.json`: override mappings, deep-merged in filename order (`99_local.json` refines `10_base.json`).
- `*.py`: define `CONVERTERS = {"name": factory}`; an override block turns one on by name. The code runs in-process.
- A bad file is reported and left out; the rest still loads. Overrides the Hub refuses leave the ones in effect.
- `zz_settings.json` (`SETTINGS_FILE`, loaded last) holds the settings written through IL, a block per device:
  `await watcher.save_settings(device_id, block)` replaces the device's (an empty block removes it), writes the file
  atomically and reloads at once. Keep your own overrides in other files; with one `.json` file instead of a directory
  nothing is saved.

### The override pack

Fixes for non-standard devices can reach users before the next release: [pack/](pack/) on `master` holds override files
and `tuya2ildevice.host.pack.sync(directory)` copies them into a host's `custom_converters/` directory, where the watcher
loads them like the user's own (rustuya-local does this at start and daily, unless turned off). Every file is checked
against the manifest's SHA-256, and `sync` only writes or removes the files it put there, as recorded in
`.tuya2ildevice_pack.json`. A user's file with the same name, or a pack file the user has edited, is left alone. A
manifest entry can be limited to a range of tuya2ildevice versions (`pack.LEVEL`). `.py` pack files run in the host's
process, like the user's own files. The pack never writes `zz_settings.json`.

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
retained=False)` entry point for feeding decoded bridge input back in. A host that already heard a device before it
drives it (a restart: the bridge's retained link state and `state`) hands that over as `seed` (`set_device(device,
seed)`, `sync_devices(records, seed=...)`), so the device goes out as it was — `available: true` with its values, never
`false` in between — and `stop(offline=False)` keeps the presence `online` for a host that starts again at once: IL
consumers see no gap.

One producer per IL prefix and source: `await producer_running(il, hub.il.presence)` before starting says whether
another one is serving it. It returns `True` when a running `Runner` answers a probe on `<presence>/probe` (the answer
comes on `<presence>/alive`; neither is retained, and IL consumers, subscribed to `_producer/+`, do not see them). It
returns `False` when presence is not `online`. It returns `None` when presence is `online` but nothing answers: a
stale presence whose Last Will never reached the broker, or a producer on tuya2ildevice before 0.3.5.

| direction | shape |
|---|---|
| bridge -> hub | `runner.on_bridge_message(device_id, Connected() / Disconnected() / Message(channel, {dp: value}))` |
| hub -> bridge | `BridgeCommand(device_id, "set"/"get", dps)` via `on_bridge_command` (a `get` after a live connect, or for a device with no state yet; none when its retained snapshot is there) |
| hub -> host | `SaveSettings(device_id, block)` via `on_settings` (a sync function, or a coroutine function run as a task that `drain()` waits for) |
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

One deliberate exception: where Tuya's own category list (`tuya/tables/_tuya_categories.json`, generated by
`scripts/gen_tuya_categories.py` from Tuya's standard instruction set page) disagrees with core, Tuya's list wins.
The decisions are data too (`_tuya_standard_rules.json`, applied by `tuya/standard.py`): a `kg` ("Switch") category's
switches are `switch`, not core's `outlet` plug icon; `cz` ("Socket") and `pc` ("Power strip") stay `outlet`. The golden
tests apply the same file to core's expectations, and each such difference is a tagged `deviate` row in
`docs/engine-spec.md` (P-28).

A second one: a cover's position is not mirrored. The bridge shows the device's number and so does the IL; an
installation inverts a device that counts the other way itself (its cover settings). The golden tests mirror core's
expected position and position writes where core mirrored them (`core_reverses` in `tests/test_golden.py`).

## Tests

```
pip install -e .[test] && python -m pytest
```

`tests/test_adapter.py` compares the raw-dps adapter with the Tuya SDK and is skipped unless `tuya-device-sharing-sdk==0.2.15`
is installed. The spec repository (`../ildevice`, or `$ILDEVICE`) supplies the schema and the language-neutral vectors
(commands, wire values, topics) read directly from its checkout; those tests are skipped if it is not there. `tests/chain/`
compares every Home Assistant core tuya fixture with core's entity snapshots through an IL host's HA-free planner
(needs that host's `ildevice.core` on `sys.path`; not collected without it).
