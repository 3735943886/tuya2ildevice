# Portable Tuya rule engine

The complete production `TuyaDriver` executes in Rust. Python transports JSON
through a two-function C ABI and dispatches explicitly registered Python plugins.
There is no Python conversion fallback and no Python interpreter in the Rust library.
The original implementation is frozen under `tests/reference/` for oracle tooling.

## Rules and execution

`rules/default.json` is the canonical version-1 rule pack. It contains category
classification tables, ordered DP role candidates, schema quirks, units, mappings,
feature bits, IL property layouts, motion defaults and setting definitions.

A property layout declares its creation condition, descriptor fields, read
selector, transformation and write action. `$identity.*`, `$config.*` and
`$roles.<name>.range` resolve against the device's classified plan. `$key` selects
the plan key; other property names receive the composite group's prefix.

Rust supplies the closed operation registry: typed validation, arithmetic,
scaling, rounding, mappings, codecs and platform algorithms. Device-specific
knowledge belongs in the rule pack or override JSON. New rules using supported
operations need no rebuild; new primitives require an engine change and a
compatible rule-language version. Binary64 arithmetic follows the reference's
numeric conversion paths; arbitrary-precision Python numbers are outside the
JSON ABI contract.

The native boundary uses explicit JSON driver state:

```
create(device, options, rules) -> driver
handle(driver, input, now, rules) -> {driver, outputs, callbacks}
```

The driver holds accumulated DP values, published values, delta totals and last
report stamps, cover motion, declarative state slots, settings and timer names.
No sockets or clocks are read. Serialize the returned driver to continue in
another process using the same engine and rule pack. Internal driver state is
version-specific; it is not a durable storage format across engine upgrades.

## JSON converters

A product/device override may declare `converters.declarative`:

```json
{
  "my_product": {
    "converters": {
      "declarative": {
        "props": {
          "filter_low": {"type": "binary", "class": "problem"},
          "updates": {"type": "number"}
        },
        "initial_state": {"count": 0},
        "update": [
          {
            "set": ["state", "count"],
            "value": {
              "op": "add",
              "args": [{"op": "get", "path": ["state", "count"]}, 1]
            }
          },
          {
            "emit": "updates",
            "value": {"op": "get", "path": ["state", "count"]}
          },
          {
            "emit": "filter_low",
            "value": {
              "op": "lt",
              "args": [{"op": "get", "path": ["codes", "filter_life"], "default": 100}, 10]
            }
          }
        ]
      }
    }
  }
}
```

Expression objects use `op`; other JSON values are literals. `literal` explicitly
quotes an object containing an `op` field. The expression vocabulary is:

| Expression | Fields / behavior |
| --- | --- |
| `get` | `path` array, optional literal `default`; missing yields null |
| `literal` | `value` |
| `if` | `condition`, `then`, `else`; evaluates only the chosen branch |
| `coalesce` | first non-null item in `args` |
| `eq`, `ne`, `lt`, `le`, `gt`, `ge` | two `args`; ordered comparisons require numbers |
| `not`, `and`, `or` | `args`, JSON truth semantics |
| `contains` | array membership, object key or string substring |
| `is_number`, `is_null` | one item in `args` |
| `add`, `sub`, `mul`, `div`, `min`, `max` | two numeric `args`; unknown/non-finite results yield null |
| `round_half_even` | one numeric item in `args` |
| `lookup` | mapping and key in `args`, optional `default` |

Programs are statement arrays:

| Statement | Fields |
| --- | --- |
| assign state | `set`: path beginning with `state`, `value`: expression |
| publish | `emit`: property name, `value`: expression; null clears it |
| request/cancel timer | `timer`: local name, `after`: expression; seconds or null |
| send DP | `send`: `{code: expression, value: expression}` |
| conditional | `if`: expression, `then` / optional `else`: statement arrays |

`update`, `timer` and `reset` programs receive their corresponding inputs;
`write` maps property names to programs and receives `prop`, `value`, `codes`
and `now`. A writable/trigger property must have a write program. Context also
contains `state` and the converter's `config`. Updates receive `codes`, `changed`,
`active`, `now`; timer programs receive `name`, `codes`, `now`.

Only state slots can be assigned. Programs have no code imports or I/O and a
10,000-statement invocation limit. Timer names use the existing `c<index>:<name>`
namespace, preserving host scheduling behavior. Keep persistent device settings
through `SettingsChanged` and `carry` as before. Use `reset` to define which
custom transient state is cleared on disconnect.

## C ABI

See `rust/engine/tuya_engine.h`:

```c
char *tuya_engine_eval(const char *request_json);
void tuya_engine_free(char *response_json);
```

Input is borrowed UTF-8 with a terminating NUL, valid during the call. Output is
owned by Rust and must be released exactly once with this library's free
function. Null input returns an error; freeing null is allowed. Invalid pointers
are outside the ABI contract. The Python wrapper releases output in `finally`.

Every response is `{ "ok": true, "value": ... }` or
`{ "ok": false, "error": "..." }`. Errors have diagnostic text; output `Reject`
codes are the stable command-rejection contract. No exception crosses the C ABI.

For standalone hosts, supply `rules` with each request, or register an immutable
pack once with `{op: "install_rules", rules_id: "your-content-id", rules: pack}`.
Subsequent requests use `rules_id`. IDs cannot be reused for different contents.
The registry caches immutable rules, not device state. Separate hosts need only
JSON serialization and these two C functions.

The primary operations are:

| `op` | Request fields | Result |
| --- | --- | --- |
| `create` | `device`, `options` | `{driver, warnings}` |
| `handle` | `driver`, `input`, `now` | `{driver, outputs, callbacks}` |
| `carry` | `driver`, `old` | `{driver}` with compatible delta totals |
| `settings` | `driver` | settings block |
| `converted` | `driver`, `index`, `result` | apply external plugin values/timers |
| `write_callback` | `driver`, `prop`, `commands` | encode external plugin commands |
| `classify`, `read`, `write` | schema/plan inputs | low-level compatibility operations |

Input/output tags use snake_case: `connected`, `disconnected`, `message`,
`command`, `timer`; outputs include `descriptor`, `value`, `absent`, `event`,
`send_message`, `reject`, `set_timer`, `cancel_timer`, `settings_changed`.
`message.json` is the flat DP map with optional `t`, matching the existing API.
`callbacks` are empty for JSON-only configurations. Python converter plugins
require a host that implements that plugin language; they are not portable rules.

`TUYA_ENGINE_RULES` and `TUYA_ENGINE_LIBRARY` select user rule paths and the library path. `TUYA_ENGINE_RULES` supplies additional file/directory paths
(separated with the OS path separator), over the bundled `default.json`. Environment-selected
rules are cached until restart. Explicit `load_rules(paths)` returns an independent set for
`TuyaDriver(..., rules=...)`; `OverrideWatcher` reloads complete sets when files change.

`load_rules` is a host-side native operation that reads files. The evaluation operations remain
sans-I/O. Its request accepts `default_path` (optional; otherwise embedded defaults), `paths`
(array), and returns `{rules, sources, warnings}`. `merge_rules` accepts `base` and ordered
`layers` for hosts that supply already decoded data. Both use the same recursive merge and
bundled schema validation. Arrays replace; `{"$delete": true}` deletes a member. User directories
are non-recursive, sorted by filename; hidden files and `schema.json` are excluded and
`zz_settings.json` is always last. Product/device fixes are under `overrides` in the same format.
There is no remote pack sync. Default rules are included in the library and wheel.

## Build, packaging and validation

Rust 1.88+ and Cargo are needed to build from source. Installed wheels include
the platform library and JSON pack; they need no Rust toolchain or Python
runtime dependencies. The host extra still supplies paho-mqtt.

```sh
cargo test --locked --manifest-path rust/Cargo.toml
cargo clippy --locked --manifest-path rust/Cargo.toml --all-targets -- -D warnings
pip install -e '.[test]'
python -m pytest -q
python -m build
```

The custom Hatch hook builds the release library and produces a `py3-none`
platform wheel. Release CI builds 13 wheels: Linux glibc and musl each on ARMv7,
ARM64, x86 and x64; macOS on ARM64 and x64; Windows on ARM64, x86 and x64.
Linux libraries are cross-compiled with cargo-zigbuild, then bundled and repaired
inside manylinux/musllinux containers. ARMv7 container tests use QEMU. Windows
and macOS build for an explicit Rust target so the library matches the wheel.
Each installed wheel runs the native driver and rule-loader tests; the separate
source test job runs the full suite. A source distribution is built once.

Push a version tag such as `v0.4.0` to build, test and publish all artifacts to PyPI
using the `pypi` Trusted Publishing environment. Pull requests affecting the
release inputs and `workflow_dispatch` build and test without publishing.
PyPI's Trusted Publisher must reference this repository, `release.yml` and the
`pypi` environment. No release tag is created by the workflow.

`TUYA_ENGINE_BUNDLED_LIBRARY` lets the build hook package a prebuilt library
(relative paths resolve against the project root). Without it Cargo builds the
library; `TUYA_ENGINE_TARGET` or `CARGO_BUILD_TARGET` selects an explicit target. Local Linux wheels
are tagged for their local platform; manylinux repair happens in release CI.

Existing HA core/quirk/action golden tests exercise the Rust-backed APIs. An
additional frozen oracle contains 324 complete driver traces / 3,421 steps,
covering descriptors, snapshots, active/passive packets, writes, disconnect and
reconnect. The Rust test runner consumes them without Python. The numeric corpus
contains 144 cases; the adapter tests compare Rust with Tuya SDK 0.2.15.

Oracle tooling under `scripts/rust/` imports the frozen reference only to export
vectors. Do not regenerate expected data to conceal a failing Rust test. For
new intended behavior, review the rule change and update independent goldens.
