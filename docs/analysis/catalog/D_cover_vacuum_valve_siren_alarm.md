# D. cover / vacuum / valve / siren / alarm_control_panel (+ lock / media_player / water_heater audit)

Abbreviations for citations (all paths relative to the scratchpad `core-analysis/`):
- `core/X.py` = `core/homeassistant/components/tuya/X.py`
- `H/definition/X.py`, `H/device_wrapper/X.py`, `H/type_information.py`, `H/utils.py`, `H/registry.py`, `H/builder/device_quirk.py`, `H/devices/...`
  = `handlers-dl/tuya_device_handlers-0.0.29/src/tuya_device_handlers/...`
- `T/` = `core/tests/components/tuya/` (tests, `fixtures/*.json`, `snapshots/*.ambr`)

Versions: HA core manifest pins `tuya-device-handlers==0.0.29`, `tuya-device-sharing-sdk==0.2.15` (core/manifest.json). Line numbers below are per-file.

---------------------------------------------------------------------------------------------------
## 0. Shared machinery (needed to understand every platform)

### 0.1 Data model the platforms read
- Every platform reads a `CustomerDevice` from the Tuya sharing SDK with: `category`, `product_id`, `online`, `function` (dict dpcode -> {type, values}; the WRITABLE dps), `status_range` (dict dpcode -> {type, values, report_type}; the READABLE dps), `status` (dict dpcode -> current raw value), `local_strategy`, `support_local`. (H/type_information.py:81-86 uses `device.function`/`device.status_range`; H/builder/device_quirk.py:230-262 touches all of them.)
- Everything is keyed by DP *code* (string such as `percent_control`), never by numeric dp id, in the platform code. Numeric dp ids appear only in quirks (`dpid=`), where they are used to key removal/local strategy and to label type-information overrides (H/builder/device_quirk.py:397-407).
- DP type schema (type + JSON `values`) comes from the cloud (`function`/`status_range`). Type names: `Bitmap Boolean Enum Integer Json Raw String`, with a normalisation table for ill-formed cloud strings (`bool`, `enum`, `value`->Integer, ...) (H/const.py:33-62).
- Integer schema: `min, max, scale, step, unit` (H/type_information.py:278-315). Enum schema: `range` list (H/type_information.py:217-241).

### 0.2 DP lookup semantic `TypeInformation.find_dpcode(device, dpcodes, prefer_function)` (H/type_information.py:67-118)
- `dpcodes` may be one code or a TUPLE in priority order; None => not found.
- Lookup order is dpcode-major then source: for each dpcode in tuple order, search `(status_range, function)` (default) or `(function, status_range)` (`prefer_function=True`); the first source that has the dpcode AND whose declared type equals the wrapper's expected type (`DPType.try_parse(type) is cls._DPTYPE`) AND parses (`_from_json`; Enum/Integer/Bitmap return None if `values` JSON is empty/falsy) wins. A dp present with the wrong type is silently skipped and the next dpcode / source is tried (lines 88-118).
- A quirk can substitute the TypeInformation class per dpcode (`quirk.get_type_information_cls(dpcode=...)`, lines 86-98, H/builder/device_quirk.py:584-591). NOTE: the override key is `(dpid, dpcode)` but lookup matches on dpcode ONLY (device_quirk.py:588-590), and applies to every product with that quirk (registry keyed by product_id, H/registry.py:82-86).
- Reading validates against the schema: Enum value not in `range` -> None (+ once-per-device warning) (type_information.py:253-274); Integer must be an `int` within [min,max] else None, then divided by 10**scale (331-353); Boolean must be True/False (`raw in (True, False)`, so 0/1 pass) else None (194-213); Raw is base64-decoded; Json is `json.loads`; String is passthrough.
- Writing validates: Enum value must be `str` in `range` else `PrepareSetValueError`->`SetValueOutOfRangeError` (ValueError subclass, H/device_wrapper/exception.py); Integer: numeric, `round(value*10**scale)` must be in [min,max] (type_information.py:293-329) — NOTE range-checked but NOT step-snapped; Boolean must be a real `bool` (187-192).
- `DPCodeWrapper.get_update_commands(device, value)` always returns ONE command `[{"code": dpcode, "value": raw}]` (H/device_wrapper/common.py:70-84 region). Multi-command writes exist only where a wrapper overrides it (none in this slice).

### 0.3 Entity plumbing common to all five platforms (core/entity.py)
- `_attr_should_poll=False` (entity.py:25). Push only: state changes come via `update_device(device, updated_status_properties, dp_timestamps)` -> dispatcher -> `_handle_state_update` (coordinator.py:97-118; entity.py:59-78).
- `available` == `device.online` (entity.py:44-46). No per-DP availability anywhere in this slice.
- `unique_id = f"tuya.{device.id}{description.key}"` (entity.py:35). `device.set_up = True` on entity init (entity.py:38; SDK flag so MQ subscribes).
- State write policy: if `updated_status_properties is None` (online/offline events) -> always write state; else `_process_device_update()` decides; base default returns True (entity.py:80-90). Only VALVE and SIREN override it, using `wrapper.skip_update(...)`, which for a `DPCodeWrapper` is `self.dpcode not in updated_status_properties` (H/device_wrapper/common.py:34-42). COVER, VACUUM, ALARM do NOT override -> they rewrite state on every update event for the device. `dp_timestamps` is passed through but NOT used by any wrapper in this slice (only `H/device_wrapper/sensor.py:62-78` uses it).
- Commands: `_async_send_commands` no-ops on an empty list, otherwise `manager.send_commands(device.id, commands)` in executor (entity.py:92-99). No optimistic state, no read-back, no ordering/sequencing/delay logic: after a command HA state changes only when the cloud pushes a status update.
- Discovery: each platform's `async_discover_device` runs on the initial device_map and on `TUYA_DISCOVERY_NEW` (per-platform files, e.g. core/cover.py:156-194). Quirks are applied to the device (`TUYA_QUIRKS_REGISTRY.initialise_device_quirk`) BEFORE any platform sees it (core/coordinator.py:132-155; core/__init__.py:50-63), so quirk-mutated `category/function/status_range/status` are what platform tables and `find_dpcode` see. `DeviceQuirk.initialise_device` snapshots originals then can override category and apply entries in declaration order (H/builder/device_quirk.py:230-262).
- Platforms enabled by core: `PLATFORMS = [ALARM_CONTROL_PANEL, BINARY_SENSOR, BUTTON, CAMERA, CLIMATE, COVER, EVENT, FAN, HUMIDIFIER, LIGHT, NUMBER, SCENE, SELECT, SENSOR, SIREN, SWITCH, VACUUM, VALVE]` (core/const.py:49-68). No LOCK, MEDIA_PLAYER, WATER_HEATER (see section 6).
- Names: `_attr_has_entity_name=True`; names come from `translation_key` (or `name=` for alarm, `name=None` for vacuum/alarm-entity/SGBJ siren = device name).
- Structural pattern in handlers 0.0.29: each `H/definition/<platform>.py` exposes `get_default_definition(device, ...)` returning a `*Definition` dataclass of wrappers (or None = no entity) plus an (unused in this package/core) `*Quirk(definition_fn=...)` dataclass. Verified unused: `grep definition_fn|CoverQuirk|VacuumQuirk|SirenQuirk|ValveQuirk|AlarmControlPanelQuirk` matches only the definition modules themselves (H/definition/*.py), not core nor any device quirk. So entity-level custom definitions per product are a declared extension point but NOT exercised in 0.0.29.

### 0.4 Wrapper contract
`DeviceWrapper[T]` (H/device_wrapper/base.py): `read_device_status(device)->T|None`, `get_update_commands(device, value)->list[dict]`, `skip_update(...)`, `initialize(device)` (defined, never called in this slice), plus optional attributes `options`, `min_value/max_value/value_step`, `native_unit`. The platform entity only uses `.options`, `read_device_status`, `get_update_commands`, `skip_update`.

---------------------------------------------------------------------------------------------------
## 1. COVER

Sources: core/cover.py, H/definition/cover.py, H/device_wrapper/cover.py, H/device_wrapper/extended.py, H/device_wrapper/common.py, H/type_information.py, H/utils.py, H/type_information_ex.py, H/devices/{cl,clkg}/*.py.

### 1.1 Entity decision
Table `COVERS: dict[DeviceCategory, tuple[TuyaCoverEntityDescription,...]]` (core/cover.py:55-153). For each device whose `category` is a key, for each description (one potential entity each), `get_default_definition(...)` is called (core/cover.py:170-186). The entity is created iff `get_default_definition` returns non-None, i.e. iff the description's KEY dp code is in `device.function` OR `device.status_range` (any dp type) (H/definition/cover.py:64-68). NOTE: once the key dp exists, an entity is ALWAYS created, even if every wrapper resolves to None (the definition dataclass has all-Optional wrappers; core/cover.py:229-232 then yields `supported_features=0`, unknown state). So "key present but wrong type" (e.g. key is Integer) => entity with zero features. If no wrapper can act, it still appears.
No entity: category not in COVERS, or key dp absent from both dicts.

Description fields (core/cover.py:41-52): `key` (instruction dp), `current_state` (dpcode or tuple), `current_state_wrapper` class (default `CoverClosedEnumWrapper`), `current_position` (dpcode or tuple), `instruction_wrapper` class (default `CoverInstructionEnumWrapper`), `position_wrapper` class (default `DPCodeInvertedPercentageWrapper`), `set_position` (dpcode), plus device_class, translation_key, placeholders.

Per-category tables (every description; `key` is also the unique_id suffix):

| Category | key (instruction dp) | translation | current_state dp(s) / wrapper | current_position | set_position | position_wrapper | instruction_wrapper | device_class |
|---|---|---|---|---|---|---|---|---|
| `ckmkzq` (garage door opener) core/cover.py:56-81 | `switch_1` | indexed_door {index:1} | `doorcontact_state` / DPCodeInvertedBooleanWrapper | - | - | default | default | garage |
| | `switch_2` | indexed_door {2} | `doorcontact_state_2` / InvertedBool | - | - | | | garage |
| | `switch_3` | indexed_door {3} | `doorcontact_state_3` / InvertedBool | - | - | | | garage |
| `cl` (curtain) core/cover.py:82-124 | `control` | curtain | `(situation_set, control)` / CoverClosedEnumWrapper | `(percent_state, percent_control)` | `percent_control` | DPCodeInvertedPercentageWrapper | default | curtain |
| | `control_2` | indexed_curtain {2} | - | `percent_state_2` | `percent_control_2` | inverted % | default | curtain |
| | `control_3` | indexed_curtain {3} | - | `percent_state_3` | `percent_control_3` | inverted % | default | curtain |
| | `mach_operate` | curtain | - | `position` | `position` | inverted % | CoverInstructionSpecialEnumWrapper (FZ/ZZ/STOP) | curtain |
| | `switch_1` (comment: undocumented, behaves like control; Kogan Smart Blinds Driver) | blind | - | `percent_control` | `percent_control` | inverted % | default | blind |
| `clkg` (curtain switch) core/cover.py:125-143 | `control` | curtain | - | `percent_control` | `percent_control` | ControlBackModePercentageMappingWrapper | default | curtain |
| | `control_2` | indexed_curtain {2} | - | `percent_control_2` | `percent_control_2` | ControlBackMode... | default | curtain |
| `jdcljqr` core/cover.py:144-152 | `control` | curtain | - | `percent_state` | `percent_control` | inverted % | default | curtain |

Note `DeviceCategory.CLKG` and `JDCLJQR`, `CKMKZQ` values: "clkg", "jdcljqr", "ckmkzq" (core/const.py:101,113,503). Other categories (e.g. `mc`, `mcs`, `sfkzq`) never make covers.

Resolution of wrappers (H/definition/cover.py:70-89):
- `current_position_wrapper = position_wrapper.find_dpcode(device, current_position)` (default `prefer_function=False`, so status_range first per dpcode, then function). For a tuple, first dpcode that is an Integer wins (see 0.2). E.g. CL `control`: `percent_state` if it exists as Integer in either dict, else `percent_control`.
- `set_position_wrapper = position_wrapper.find_dpcode(device, set_position, prefer_function=True)`.
- `tilt_position_wrapper = position_wrapper.find_dpcode(device, ("angle_horizontal","angle_vertical"), prefer_function=True)` (hard-coded strings, line 86-88). Computed for EVERY description, including `control_2`, `control_3`, `mach_operate`, `ckmkzq` doors. Uses the description's `position_wrapper` class, so tilt also gets inversion/remap of that class (inverted by default!). No test fixture/snapshot exercises tilt (grep of tests shows only `test_set_tilt_position_not_supported` asserting a device without angle dps rejects the service, T/test_cover.py:182-207).
- `current_state_wrapper = current_state_wrapper_cls.find_dpcode(device, current_state)` (status_range first). For `DPCodeInvertedBooleanWrapper` requires Boolean type; for `CoverClosedEnumWrapper` requires Enum type.
- `instruction_wrapper = instruction_wrapper_cls.find_dpcode(device, key, prefer_function=True) or CoverInstructionBooleanWrapper.find_dpcode(device, key, prefer_function=True)`: i.e. Enum-typed key -> enum wrapper; else Boolean-typed key -> boolean wrapper (open=True/close=False, no stop); else None.

### 1.2 Exposed HA features/attributes and transforms
`supported_features` (core/cover.py:211-232):
- OPEN if `TuyaCoverAction.OPEN in instruction_wrapper.options`; CLOSE if CLOSE in options; STOP if STOP in options.
  - Enum wrapper `options` = subset of [open, close, stop] whose Tuya string is in the dp's enum `range` (H/device_wrapper/cover.py:55-60). Default mapping open->"open", close->"close", stop->"stop" (lines 45-49). Special (mach_operate) mapping open->"FZ", close->"ZZ", stop->"STOP" (68-74). Note the enum range in fixtures may also include `continue` (cl_zah67ekd control range `['open','stop','close','continue']`) which is never used.
  - Boolean wrapper `options=["open","close"]` (line 30).
- SET_POSITION if `set_position_wrapper` present; SET_TILT_POSITION if `tilt_position_wrapper` present. (No OPEN_TILT/CLOSE_TILT/STOP_TILT features.)
- Snapshot evidence: cl_zah67ekd => 15 (OPEN|CLOSE|SET_POSITION|STOP); cl_n3xgr5pdmpinictg (control enum only) => 11 (OPEN|CLOSE|STOP); ckmkzq garage => 3 (OPEN|CLOSE) (T/snapshots/test_cover.ambr lines around 33, 195, 302).

Attributes / state:
- `current_cover_position` = `read(current_position_wrapper or set_position_wrapper)` (core/cover.py:213-215,234-238).
  - Read transform for percentage wrappers (H/device_wrapper/extended.py:31-73): `raw` is validated by IntegerTypeInformation (int and within min..max else None), scaled by 10**scale; then `RemapHelper.remap_value_to(value, reverse=inverted)` from [min,max] (scaled) to [0,100]; `round()` (Python banker's rounding). Formula (H/utils.py:73-84): `if reverse: v = from_max - v + from_min; out = (v-from_min)/(from_max-from_min)*(to_max-to_min)+to_min`.
  - `DPCodeInvertedPercentageWrapper`: always reverse (extended.py:86-91) => HA 100 = raw min, HA 0 = raw max ("Tuya 0=open,100=closed" convention; test comment T/test_cover.py:171: `percent_state = 100 - percent_state`).
  - `ControlBackModePercentageMappingWrapper` (H/device_wrapper/cover.py:12-24): non-inverted ONLY if the live `device.status["control_back_mode"] == "back"`; otherwise (value "forward" or missing/None or any other) inverted. Evaluated at every read AND every write from `device.status` (dynamic cross-dp dependency, not fixed at setup). NOTE `control_back_mode` is a raw hard-coded string, not required to be in function/status_range.
  - Read source dp differs from write dp in some categories (CL: read percent_state (status_range) / write percent_control (function); JDCLJQR same); each has its own IntegerTypeInformation (own min/max/scale), so the remap uses each dp's own range.
- `current_cover_tilt_position` = `read(tilt_position_wrapper)` (same transform, through the description's position wrapper class).
- `is_closed` (core/cover.py:249-257): if `current_cover_position is not None` -> `position == 0`; else `read(current_state_wrapper)`.
  - `CoverClosedEnumWrapper` mapping (H/device_wrapper/cover.py:81-86): `{"close": True, "fully_close": True, "open": False, "fully_open": False}`; anything else (e.g. `stop`, `continue`) -> None -> HA "unknown" (test: control "stop" => `unknown`, T/test_cover.py:317-344).
  - `DPCodeInvertedBooleanWrapper` (extended.py:94-106): closed = `not raw_bool` (i.e. `doorcontact_state` True means door OPEN -> is_closed False). Snapshot: ckmkzq fixture doorcontact_state True => `open`. (Fixture T/fixtures/ckmkzq_1yyqfw4djv9eii3q.json: status `{'switch_1': False, 'countdown_1': 0, 'doorcontact_state': True}`.)
  - `CL/control` current_state tuple `(situation_set, control)`: first Enum-typed dp found (status_range first) — `situation_set` range in fixtures is `['fully_open','fully_close']`; if absent falls back to the `control` enum (values open/close/stop/continue).
  - Note the position-over-state precedence means cl_zah67ekd `situation_set: fully_open` is ignored because position exists (state derived from position 48 => open).
- No `assumed_state`, no `is_opening/is_closing` (so the `work_state: closing` dp in cl_zah67ekd is not used by the cover; grep of core/cover.py shows no work_state).
- `device_class` per table above (`garage`, `curtain`, `blind`).

Commands (core/cover.py:259-316):
- `open_cover`: IF `set_position_wrapper` exists -> send ONLY `set_position_wrapper.get_update_commands(device, 100)`; the instruction dp is NOT sent. ELSE if instruction wrapper has OPEN -> send instruction (`open`/`FZ`/`True`). (Lines 262-274.)
- `close_cover`: same with 0 / CLOSE (277-291).
- `set_cover_position(pos)`: `[{code: set_dp, value: remap_from(pos, reverse)}]` (294-298). Position is remapped from [0,100] to raw [min,max] (`remap_value_from`, extended.py:65-73), reverse for inverted wrappers, then `IntegerTypeInformation.prepare_set_value` rounds `raw*10**scale` and range-checks (raises SetValueOutOfRangeError on failure). NOTE: `remap_value_from` yields float; `scale_value_back` rounds to int.
  - Test evidence: cl_zah67ekd (inverted, percent_control 0..100): open -> `{"percent_control": 0}`, close -> 100, pos 25 -> 75 (T/test_cover.py:62-90). clkg_wltqkykhni0papzj with control_back_mode "back": open -> 100, pos 25 -> 25, close -> 0 (T/test_cover.py:250-310).
- `stop_cover`: only if instruction wrapper has STOP: sends `{key: "stop"}` (or `"STOP"` for mach_operate) (300-309). There is NO stop implemented via position; a device with only position (no enum key) has no STOP.
- `set_cover_tilt_position(pos)`: via tilt wrapper (311-316).
- Multi-dp writes: none. Each service call = at most ONE command. Ordering concerns: none.
- Because open/close use position when available, `open`/`close` OPEN/CLOSE features are advertised from the instruction wrapper only (mismatch possible: SET_POSITION-only cover without an instruction enum shows no open/close services). Also a cover whose instruction enum lacks all of open/close/stop but has position gets only SET_POSITION.
- Stale doc in H/devices/cl/cl_nfq1essvr99qsvvd.py:10-19 says open sends `control: "open"` AND a position command; core/cover.py:262-274 (current) sends ONLY position. (Doc drift; trust code.)

### 1.3 Quirks that change cover behavior (H/devices)
Quirks are per product_id, applied at `initialise_device_quirk` (registry.py:88-91):
- `InvertedIntegerTypeInformationEx` (H/type_information_ex.py:21-52): read returns `scale_value(max) - value`, write sends `scale_value(max) - value`. Note it inverts about `max` only, i.e. assumes min==0. Registered via `override_dpid_type_information_cls(dpid, dpcode, cls)` for: cl `68nvbio9` (dp2 percent_control, dp3 percent_state), cl `cf1sl3tj` (same), cl `nfq1essvr99qsvvd` (manufacturer Canisteo), clkg `csgb8eqhczvjaetl` (dp2, dp3). Purpose: cancel the wrapper's inversion for devices that already report HA convention. NOTE for clkg the quirk composes with `ControlBackModePercentageMappingWrapper`'s dynamic inversion (which depends on control_back_mode) -> net direction depends on both.
- `remove_dpid` for cl `b9oa3zocv4qq47iy` (`percent_state` dp3, so the position falls back to `percent_control` in the CL tuple) and cl `xyakonle1azq2xgn` (removes `percent_control` dp9 and `percent_state` dp8 -> cover falls back to open/close/stop enum only) (device_quirk.py:408-419 removes from function, status_range, status, local_strategy).
- Quirk mechanism also offers (not used by cover quirks in this package version): `override_category`, `add_dpid_*`, `map_dpid_initial_status_values`, local-strategy edits (`set_dpid_strategy_to_enum`, `remove_dpid_strategy`).

### 1.4 Runtime behaviors
- No optimistic state; no `_process_device_update` override => every push update rewrites cover state.
- Availability: `device.online`.
- Out-of-range/invalid raw dp value => position None (falls to state wrapper) with a per-device de-duplicated log warning (H/type_information.py:28-40, 331-353).
- `control_back_mode` change does not itself need special handling since all updates rewrite state and the wrapper reads live status.

### 1.5 Pure data vs code
Pure data (table-expressible): category -> [ {key, state dp list, position dp list, set-position dp, device_class, name}], instruction word map (open/close/stop -> strings; standard vs FZ/ZZ/STOP), closed-state enum map, inversion boolean per description, tilt dp candidates.
Needs (small, closed set of) code/primitives: (a) tuple-priority typed DP lookup with status/function ordering; (b) linear remap with optional inversion and rounding; (c) inversion condition depending on ANOTHER dp's live value (`control_back_mode != "back"`); (d) inverted boolean; (e) per-product type-info overrides ("invert about max"); (f) position-over-state precedence and open/close-via-position precedence rule. All are declarative-able with a small expression vocabulary; none requires arbitrary Python.

---------------------------------------------------------------------------------------------------
## 2. VACUUM

Sources: core/vacuum.py, H/definition/vacuum.py, H/device_wrapper/vacuum.py.

### 2.1 Entity decision
- `VACUUMS = {DeviceCategory.SD: TuyaVacuumEntityDescription(key="")}` (core/vacuum.py:45-47). Only category `sd` (robot vacuum). One entity per device, unique_id `tuya.<device.id>` (empty key), name None (device name) (core/vacuum.py:82).
- `get_default_definition(device)` never returns None (H/definition/vacuum.py:41-49). Therefore EVERY `sd` device creates a vacuum entity even if no wrappers resolve (then features = SEND_COMMAND only, state unknown). Evidence: sd_i6hyjg3af7doaswm (dps power/mode/power_go/seek only) => features 8984, state `unknown` (T/snapshots/test_vacuum.ambr).
- Required DPs: none. Optional: `status`(enum), `pause`(bool), `suction`(enum), `switch_charge`(bool), `seek`(bool), `mode`(enum), `power_go`(bool).

### 2.2 Wrappers / DP selection (H/device_wrapper/vacuum.py, H/definition/vacuum.py:41-49)
- `activity_wrapper = VacuumActivityWrapper.find_dpcode(device)`: builds from `pause` = Boolean (status_range first) and `status` = Enum (status_range first). Returned only if at least one of them exists (vacuum.py:54-63).
- `action_wrapper = VacuumActionWrapper.find_dpcode(device)` (always an object, maybe with empty `options`): `switch_charge` Boolean (prefer_function), `seek` Boolean (prefer_function), `mode` Enum (prefer_function), `pause` Boolean (NOT prefer_function -> status_range first), `power_go` Boolean (prefer_function) (vacuum.py:116-133).
- `fan_speed_wrapper = DPCodeEnumWrapper.find_dpcode(device, "suction", prefer_function=True)` (definition/vacuum.py:44-48). `fan_speed_list` = the raw enum `range`, unmapped (core/vacuum.py:115-117). Snapshot: v20 range `['gentle','normal','strong']`.

### 2.3 Features (core/vacuum.py:98-117)
- Always: SEND_COMMAND.
- Action options built in `VacuumActionWrapper.__init__` (vacuum.py:101-114): RETURN_TO_BASE if `switch_charge` exists OR (`mode` exists AND "chargego" in mode.range); LOCATE if `seek`; PAUSE if `pause`; START and STOP both if `power_go`. => features RETURN_HOME, LOCATE, PAUSE, START, STOP.
- STATE if activity wrapper; FAN_SPEED if `suction` enum. NOT exposed: CLEAN_SPOT, MAP, BATTERY (electricity_left is a separate `sensor`), CLEAN_AREA/ROOM, `mode` as select (separate `select` platform DPCode.MODE "vacuum_mode", core/select.py:224-226 region).
- Snapshot check: sd_lr33znaodtyarrrz => 13116 = START|STATE|LOCATE|SEND_COMMAND|FAN_SPEED|RETURN_HOME|STOP|PAUSE; sd_i6hyjg3af7doaswm => 8984 = START|LOCATE|SEND_COMMAND|RETURN_HOME|STOP (no STATE, no PAUSE, no FAN_SPEED).

### 2.4 State derivation (activity)
`VacuumActivityWrapper.read_device_status` (vacuum.py:65-76):
1. If `status` enum wrapper exists and its validated value is not None -> return `_TUYA_STATUS_TO_HA.get(status)` (None if unmapped; the pause dp is then NOT consulted).
2. Else if `pause` wrapper exists and pause is truthy -> `PAUSED`.
3. Else None.
Then core maps Tuya-activity -> HA VacuumActivity via a same-name 6-entry table (core/vacuum.py:30-37) and `activity()` returns None when the wrapper value is falsy (core/vacuum.py:127-130).

Mapping table `_TUYA_STATUS_TO_HA` (H/device_wrapper/vacuum.py:19-43), quote:
```
charge_done -> DOCKED      chargecompleted -> DOCKED   chargego -> DOCKED     charging -> DOCKED
cleaning -> CLEANING       docking -> RETURNING        goto_charge -> RETURNING
goto_pos -> CLEANING       mop_clean -> CLEANING       part_clean -> CLEANING
paused -> PAUSED           pick_zone_clean -> CLEANING pos_arrived -> CLEANING
pos_unarrive -> CLEANING   random -> CLEANING          sleep -> IDLE
smart_clean -> CLEANING    smart -> CLEANING           spot_clean -> CLEANING
standby -> IDLE            wall_clean -> CLEANING      wall_follow -> CLEANING
zone_clean -> CLEANING
```
`TuyaVacuumActivity.ERROR` exists (helpers/homeassistant.py:1085-1093) and is mapped in core, but NOTHING in 0.0.29 produces it (the `fault` bitmap dp is unused by vacuum). Statuses not in the table (e.g. `error`?) yield None => HA `unknown`. Note: `status` values must ALSO be in the dp's enum `range` (validated) — e.g. fixture sd_lr33znaodtyarrrz range `['standby','zone_clean','part_clean','cleaning','paused','goto_pos','pos_arrived','pos_unarrive','goto_charge','charging','charge_done','sleep']` with status `charge_done` => `docked` (snapshot).
Mode `chargego` is a value of the `mode` dp (not `status`), but the same string is in the status table (some devices report it in `status`).

### 2.5 Commands (H/device_wrapper/vacuum.py:135-152 get_update_commands; core/vacuum.py:132-184)
| HA service | dp write |
|---|---|
| start | `power_go = True` |
| stop | `power_go = False` |
| pause | `pause = True` (no resume; START uses power_go) |
| return_to_base | if `switch_charge` exists: `switch_charge = True`; elif `mode` exists: `mode = "chargego"` (enum-validated: raises if not in range) |
| locate | `seek = True` |
| set_fan_speed(x) | `suction = x` (validated against the enum range; ValueError subclass otherwise) |
| send_command(command, params) | requires `params` non-empty list else `ValueError`/`TypeError`; sends `[{"code": command, "value": params[0]}]` raw, no schema validation, only first param used (core/vacuum.py:173-184) |
Unavailable action (e.g. start without power_go) => `get_update_commands` returns `[]` -> `_async_send_commands` no-ops silently (vacuum.py:135-152; entity.py:95). Test evidence: T/test_vacuum.py:47-100.
No multi-dp writes, no ordering.

### 2.6 Runtime behaviors
Same as generic: no optimistic state, no `_process_device_update` override (every update rewrites state), availability from `online`. `fan_speed` = validated `suction` enum value or None.

### 2.7 Pure data vs code
Data: status->activity map, dp-name roles (status/pause/suction/switch_charge/seek/mode/power_go), action->(dp,value) table with fallback order (switch_charge preferred over mode=chargego), feature derivation from availability. Code-ish: "status wins over pause" precedence (expressible as ordered fallback), `fan_speed_list` from enum range; `send_command` passthrough.

---------------------------------------------------------------------------------------------------
## 3. VALVE

Sources: core/valve.py, H/definition/valve.py.

### 3.1 Entity decision
`VALVES = {DeviceCategory.SFKZQ: (switch, switch_1, ..., switch_8)}` (core/valve.py:32-88). Category `sfkzq` (smart water valve controller, core/const.py:529). One potential entity per description:
- `switch` (translation "valve", name "Valve"), `switch_1`..`switch_8` (translation "indexed_valve", index 1-8), all `device_class=WATER`.
Entity created iff `DPCodeBooleanWrapper.find_dpcode(device, key, prefer_function=True)` finds the dp as Boolean type in function (preferred) or status_range (H/definition/valve.py:31-39). Required DP = the Boolean switch dp itself; nothing optional. E.g. sfkzq_ed7frwissyqrejic has switch_1..8 => 8 valves; sfkzq_nxquc5lb has `switch` => 1.

### 3.2 Features / attributes
- `_attr_supported_features = OPEN | CLOSE` (core/valve.py:124). No SET_POSITION/STOP; `reports_position` not set (default False), so state is closed/open only.
- `is_closed`: `raw = read(wrapper)`; None -> None; else `not raw` (True raw = open) (core/valve.py:137-143). Invalid values (non-bool such as "some string", or None) => `unknown` (T/test_valve.py:139-167).
- Commands: open -> `{dp: True}`, close -> `{dp: False}` (core/valve.py:160-168). No transitional states (opening/closing) although `work_state`/`battery_percentage` etc. exist in fixtures — those are handled in sensor/number/select platforms only (core/number.py "irrigation_duration" on `countdown`; select weather_delay; sensor).

### 3.3 Runtime
- Selective update: `_process_device_update` returns `not wrapper.skip_update(...)` => state rewritten only if the valve's own dp code is among the updated properties (`updated_status_properties is None` events still write) (core/valve.py:145-158; H/device_wrapper/common.py:34-42). Test: update of `battery_percentage` alone leaves `last_reported` unchanged; update including `switch_1` writes (T/test_valve.py:44-90).
- No optimistic state, availability = device.online.
### 3.4 Data vs code: fully data (category -> list of boolean dp codes + names + device_class). Semantics: on=open.

---------------------------------------------------------------------------------------------------
## 4. SIREN

Sources: core/siren.py, H/definition/siren.py.

### 4.1 Entity decision
`SIRENS` (core/siren.py:32-61):
| Category | key dp | entity_category | name/translation |
|---|---|---|---|
| `co2bj` (CO2 detector) | `alarm_switch` | CONFIG | translation "siren" |
| `dgnbj` (multi-function alarm host) | `alarm_switch` | - | "siren" |
| `sgbj` (siren alarm) | `alarm_switch` | - | name=None (device name) |
| `sp` (camera) | `siren_switch` | - | "siren" |
| `dghsxj` (camera, low power) | same object as `sp` (`SIRENS[DGHSXJ] = SIRENS[SP]`, line 61) | | |
Entity created iff Boolean dp `key` exists (function preferred, else status_range) (H/definition/siren.py:31-39). Required: that boolean dp. Otherwise no entity. Fixture evidence: sirens come from co2bj_yrr3eiyiacm31ski, sgbj_ulv4nnue7gqp0rjk, sp_* fixtures having `alarm_switch`/`siren_switch`; fixtures sgbj_DYgId0sz6zWlmmYu, co2bj_yakol79dibtswovc, dgnbj_layxxij0sdbrfmrf have no such dp => no siren entity from them (checked their status/function/status_range for `alarm_switch`: None).
(An `swtz_3rzngbyy` fixture also carries such a dp but category `swtz` is not in SIRENS — I did not verify its behavior beyond that; likely no siren.)

### 4.2 Exposed features
- `_attr_supported_features = TURN_ON | TURN_OFF` ONLY (core/siren.py:97). There is NO tone, duration, or volume support in the siren entity (no `TONES`, `DURATION`, `VOLUME_SET` features; `turn_on(**kwargs)` ignores kwargs, core/siren.py:117-123 region `async_turn_on`).
- `is_on` = validated bool of the dp (core/siren.py:110-113). Non-bool => None.
- `turn_on` -> `{key: True}`; `turn_off` -> `{key: False}` (core/siren.py:138-146 region).
- Tone/duration/volume-like dps are exposed by OTHER platforms: `number` ALARM_TIME for co2bj/dgnbj/mal/sgbj/wg2; `select` ALARM_VOLUME for co2bj/dgnbj/sgbj and ALARM_STATE ("siren_mode") for sgbj; `switch` SWITCH_ALARM_SOUND/SWITCH_ALARM_LIGHT for mal (cross-ref only; found via grep of core/number.py, select.py, switch.py).

### 4.3 Runtime
Selective update via `skip_update` on the siren dp (core/siren.py:123-131; test T/test_siren.py:46-83). No optimistic, availability = online. entity_category CONFIG only for co2bj.
### 4.4 Data vs code: fully data.

---------------------------------------------------------------------------------------------------
## 5. ALARM_CONTROL_PANEL

Sources: core/alarm_control_panel.py, H/definition/alarm_control_panel.py, H/device_wrapper/alarm_control_panel.py.

### 5.1 Entity decision
`ALARM = {MAL: desc(key=master_mode, name="Alarm"), WG2: desc(key=master_mode, name="Alarm")}` (core/alarm_control_panel.py:38-47). Categories `mal` (multifunction alarm host) and `wg2` (core/const.py:273,545 — note const comment: "Documented, but not in official list"). Entity created iff category in ALARM AND `EnumTypeInformation.find_dpcode(device, "master_mode", prefer_function=True)` succeeds (H/definition/alarm_control_panel.py:42-58). Required: `master_mode` enum dp. Optional: `alarm_msg` (Raw type; status_range first). `master_state` is consulted from raw `device.status` (no type lookup).
Evidence: of 7 wg2 fixtures only `wg2_pkhw2vbphv4csrir` has `master_mode` => only it yields an entity ("C30"); `mal_gyitctrjj1kefxp2` (Multifunction alarm) too (T/snapshots/test_alarm_control_panel.ambr: 2 entities). The description uses `name="Alarm"` but the entity sets `_attr_name = None` (core/alarm_control_panel.py:97) -> device name is the entity friendly_name ("C30", "Multifunction alarm").
The entity constructor raises if `definition.action_wrapper` missing? No — `action_wrapper` is always set when definition exists.

### 5.2 Features / attributes (core/alarm_control_panel.py:94-132)
- `_attr_code_arm_required=False`; `code_format` None (snapshot).
- `supported_features` from `action_wrapper.options` = subset of {ARM_HOME->"home", ARM_AWAY->"arm", DISARM->"disarmed", TRIGGER->"sos"} whose Tuya string is in `master_mode.range` (H/device_wrapper/alarm_control_panel.py:74-93). HA features set: ARM_HOME if "home" in range; ARM_AWAY if "arm"; TRIGGER if "sos". DISARM has no HA feature flag (disarm always available). Fixtures: range `['disarmed','arm','home','sos']` => 11 (ARM_HOME|ARM_AWAY|TRIGGER). No ARM_NIGHT/VACATION/CUSTOM_BYPASS support.
- `alarm_state`: `AlarmStateWrapper.read_device_status` (alarm_control_panel.py:33-71):
  1. If raw `device.status["master_state"] == "alarm"` (hard-coded key/value, status only): decode `device.status["alarm_msg"]` (raw base64 string from `status`, `base64.b64decode(...).decode("utf-16be")`); if there is NO alarm_msg / empty decode, OR the decoded text does NOT contain the substring `"Sensor Low Battery"` -> return TRIGGERED. If it contains "Sensor Low Battery" (a battery warning) -> fall through to step 2.
  2. Else read `master_mode` (enum validated); mapping:
```
"disarmed" -> DISARMED, "arm" -> ARMED_AWAY, "home" -> ARMED_HOME, "sos" -> TRIGGERED
```
     Unknown enum string -> None (unknown). (`_STATE_MAPPINGS`, lines 41-46.)
  3. Core then maps Tuya alarm state -> HA state using a 10-entry identity table (core/alarm_control_panel.py:49-62), returning None if falsy. Only DISARMED/ARMED_HOME/ARMED_AWAY/TRIGGERED are ever produced; PENDING/ARMING/DISARMING/ARMED_NIGHT/ARMED_VACATION/ARMED_CUSTOM_BYPASS are declared in `TuyaAlarmControlPanelState` (helpers/homeassistant.py:22-33) but never produced in 0.0.29.
  Test matrix (T/test_alarm_control_panel.py:87-156): master_mode home + master_state alarm + alarm_msg "Test Sensor" (UTF-16BE base64) => triggered; with alarm_msg "Sensor Low Battery Test Sensor" => armed_home.
- `changed_by`: `AlarmChangedByWrapper` (Raw dp `alarm_msg`): returns None unless `device.status["master_state"] == "alarm"`, else `b64decode(raw).decode("utf-16be")` (lines 17-30). NOTE it does not apply the low-battery exclusion (returns the low-battery message even when state isn't triggered). `alarm_msg` dp existence required at setup for the wrapper (Raw type); the fixture's `alarm_msg` is redacted in the mal fixture.
- Robustness note: `AlarmStateWrapper` decodes `alarm_msg` with plain `base64.b64decode` and `.decode("utf-16be")` without try/except (invalid data would raise inside the state property).

### 5.3 Commands
`arm_home` -> `master_mode="home"`; `arm_away` -> `"arm"`; `disarm` -> `"disarmed"`; `trigger` -> `"sos"` (H/device_wrapper/alarm_control_panel.py:77-102; core/alarm_control_panel.py:134-160). A value not present in `options` (range lacks it) raises `ValueError("Unsupported value ...")` (line 102-103) rather than being blocked at the feature level (disarm has no feature flag). The `code` argument is ignored. Single-dp writes. Tests T/test_alarm_control_panel.py:47-84.

### 5.4 Runtime
No `_process_device_update` override => any update rewrites (so changes in `master_state`/`alarm_msg` alone refresh state). No optimistic state. Availability = online.
Other related dps handled in other platforms (not alarm entity): `alarm_time`, `delay_set`, `alarm_delay_time` (number), `switch_alarm_sound`/`switch_alarm_light`/`muffling`/`switch_kb_sound` (switch), for mal/wg2 (core/number.py, core/switch.py greps).

### 5.5 Data vs code
Data: category->key, action<->enum-string map, state map. Needs small code: (a) "override state to TRIGGERED when another dp (`master_state`) == alarm" with content-based exception (substring check on decoded UTF-16BE of a base64 raw dp); (b) base64+utf16be decode transform. Both are expressible as: cross-dp condition + `decode(base64, utf-16be)` + `contains` predicate, but the "Sensor Low Battery" English literal is embedded.

---------------------------------------------------------------------------------------------------
## 6. lock / media_player / water_heater audit (definitive)

Method: `grep -rniE "media_player|water_heater"` over core/homeassistant/components/tuya (all files incl. json/yaml) and over the whole handlers package + dist: ZERO matches. `Platform.LOCK` / `"lock"` platform: PLATFORMS list (core/const.py:49-68) contains no LOCK/MEDIA_PLAYER/WATER_HEATER; there are no `lock.py`, `media_player.py`, `water_heater.py` in core/homeassistant/components/tuya (directory listing: alarm_control_panel, binary_sensor, button, camera, climate, config_flow, const, coordinator, cover, diagnostics, entity, event, fan, humidifier, light, number, scene, select, sensor, siren, switch, vacuum, valve, ...). => Core creates NO lock, media_player or water_heater entities. Handlers has no `definition/lock.py|media_player.py|water_heater.py` (definition/ files: alarm_control_panel, base, binary_sensor, button, camera, climate, cover, event, fan, humidifier, light, number, select, sensor, siren, switch, valve, vacuum).

What "lock"-ish strings do appear:
- `DPCode.CHILD_LOCK="child_lock"` (core/const.py:647), `DPCode.LOCK="lock"` ("Lock / Child lock", const.py:786), `WIRELESS_BATTERYLOCK` (const.py:1011): used only by `switch.py` as `child_lock`/`battery_lock` SWITCH entities (e.g. switch.py:53,123,185,259,366,466,492,538,633,666,673,792,825,851,923,937).
- `BinarySensorDeviceClass.LOCK` for `DeviceCategory.MK` with `CLOSED_OPENED_KIT` on_value `{"AQAB"}` (core/binary_sensor.py:292-297): a binary_sensor, not a lock entity.
- `DeviceCategory` enum lists lock-ish categories: `MS` "Residential lock" (const.py:299), `MS_CATEGORY` "Lock accessories", `PHOTOLOCK` (322), `VIDEOLOCK` (408). Grep for their use: `DeviceCategory.MS\b`, `MS_CATEGORY`, `PHOTOLOCK`, `VIDEOLOCK` occur ONLY in the enum definition; no platform table keys them (grep over core/*.py and handlers). No test fixtures for `ms`, `photolock`, `videolock` (fixtures list has msp/mc/bh only).
- Consequence for lock-category devices (ms etc.): the device IS still registered in the device registry (`async_register_device` for every device in `manager.device_map`, core/__init__.py:50-63, coordinator.py:145-155) and quirks are applied, but no platform emits any entity (they can still be subscribed for MQ because `refresh_mq` is called, core/__init__.py:63-68). Their dps (unlock_*, etc.) are silently dropped by the integration. The generic `switch` platform's `child_lock` tables are keyed on other categories (not ms). `mc` (door/window contact? core/binary_sensor.py:274, sensor.py:892) yields sensors only.
- `msp` (cat toilet) has button/number/switch/light/sensor/binary_sensor tables (not lock).

---------------------------------------------------------------------------------------------------
## 7. Requirements for the IL (each with evidence)

R1. Device-schema-driven gating. Entity existence depends on dp presence AND declared dp type (Boolean/Enum/Integer/Raw/...) AND, for enums, declared range; sources are two distinct sets: writable `function` vs readable `status_range`, with per-call ordering preference. (H/type_information.py:67-118; H/definition/cover.py:64-68; valve.py:31-39.) The IL must model (a) dp code, (b) dp type, (c) enum range / int range+scale+step, (d) readable/writable flags, or provide a way to derive them from the local bridge. Open question: local devices don't provide this schema (see Q1).

R2. Prioritized dp candidate lists (tuple) with type filter, dpcode-major search (`current_position=(percent_state, percent_control)`, `current_state=(situation_set, control)`, tilt `(angle_horizontal, angle_vertical)`). (core/cover.py:86-87; H/type_information.py:88-118.)

R3. "Any wrapper missing" is legal; features are derived from what resolved: cover OPEN/CLOSE/STOP from enum range intersection; SET_POSITION/SET_TILT from position dps; vacuum RETURN_HOME/LOCATE/PAUSE/START/STOP/STATE/FAN_SPEED from dp availability; alarm ARM_HOME/ARM_AWAY/TRIGGER from enum range. (core/cover.py:221-232; core/vacuum.py:98-117; core/alarm_control_panel.py:113-119.)

R4. Entity creation policies differ: cover: key dp presence only (entity may have zero features); vacuum: always for category `sd`; valve/siren: only if boolean key dp found; alarm: category + enum found. IL needs per-entity-kind "required dps" rules incl. the degenerate "no requirement". (H/definition/*.py.)

R5. Category tables incl. multi-entity-per-category with fixed key lists and indexed names (switch_1..8, control_2/3), aliasing (`dghsxj` = `sp`), translation keys + placeholders, device_class, entity_category (siren co2bj CONFIG), name override (None => device name). (core/valve.py:32-88; core/siren.py:32-61; core/cover.py:55-153.)

R6. Value transforms (read and inverse write):
  - linear remap [raw_min..raw_max]/10^scale <-> [0..100] with optional inversion, round-half-even on read, round on write, range check (H/utils.py:73-84; extended.py:54-73; type_information.py:293-329);
  - inversion that is conditional on another dp's live value (`control_back_mode != "back"`) (H/device_wrapper/cover.py:12-24) — needs cross-dp reference evaluated at read and write time;
  - inverted boolean (`doorcontact_state`) (extended.py:94-106);
  - per-product "invert about max" override (type_information_ex.py) applied to both read and write, composed with wrapper inversion.

R7. Enum <-> HA action maps with range-intersection for feature detection: cover {open,close,stop}->{"open","close","stop"} or {"FZ","ZZ","STOP"}; boolean fallback (open=True/close=False, no stop); alarm {home,arm,disarmed,sos}. (H/device_wrapper/cover.py:27-75; alarm_control_panel.py:74-103.)

R8. Many-to-one state mapping tables: cover closed enum map (`close/fully_close->True`, `open/fully_open->False`, others None); vacuum 23-entry status->activity; alarm mode->state; unknown value => unknown (None) not error. (cited above.)

R9. Precedence/fallback logic among dps: cover `is_closed` = position==0 else state-wrapper; cover open/close prefer position write over instruction; vacuum activity: `status` overrides `pause`, `pause` True alone => PAUSED; vacuum return_to_base prefers `switch_charge` over `mode=chargego`. (core/cover.py:249-291; vacuum.py:65-76,135-152.) IL needs ordered-fallback constructs on both read and write.

R10. Cross-dp state override with content inspection: alarm TRIGGERED if `master_state=="alarm"` unless decoded `alarm_msg` contains "Sensor Low Battery" (H/device_wrapper/alarm_control_panel.py:52-71). IL needs conditional on other dps, base64/UTF-16BE decode, substring predicate, and a `changed_by` string sourced from a Raw dp gated on another dp.

R11. Raw-status access to dps NOT in the schema (`control_back_mode`, `master_state`, `alarm_msg` read from `device.status` directly). IL must allow referencing arbitrary dp codes, not only schema-declared ones.

R12. Command emission: single `{code,value}` per action; value validated against declared type/range (enum membership, integer range, real bool); errors are ValueError-derived; no-op when no command; free-form passthrough (`send_command` uses any dp code and value=params[0]). Multi-dp/ordered/atomic writes are NOT used in this slice (so IL need not require them for these platforms, but I recorded that none exist here).

R13. Update-notification semantics: valve/siren write state only if own dp in changed set (unless notification lacks the changed set, e.g. online/offline); cover/vacuum/alarm rewrite on any update. IL needs a per-entity "state dependencies" notion or must mirror both behaviours. `dp_timestamps` unused here.

R14. Availability = device online flag only. No per-entity availability_dp.

R15. Optimistic/assumed state: none. State is device-authoritative only (command sends have no local echo).

R16. Quirk layer keyed by product_id (manufacturer/model/model_id metadata): operations relevant here = remove dp, override TypeInformation class per dpcode, (also available: add dp definitions with type+range+mode, override category, initial status value mapping, local strategy edit). Applied before entity discovery and may change category/schema/status. (H/builder/device_quirk.py:125-259, 397-419; H/devices/cl/*, clkg/*.)

R17. Diagnostics of invalid data: out-of-range/wrong-typed values are treated as absent (None) with deduplicated warnings per device+key (H/type_information.py:28-40, 194-274, 331-353). IL semantics for "invalid raw value" must be "unknown".

R18. Unique id = category-independent `tuya.<device_id><key>`; multiple entities per device share device id. (entity.py:35.)

R19. Hard-coded English/protocol literals live in code: `"Sensor Low Battery"`, `FZ/ZZ/STOP`, `chargego`, `back`. IL must carry literals as data.

---------------------------------------------------------------------------------------------------
## 8. Open questions / surprises

Q1. Schema availability locally: core relies on cloud `function`/`status_range` (types + enum ranges + int ranges/scale) for gating, feature detection and value scaling. A local MQTT bridge may only see dp ids and raw values. The IL must decide where types/ranges come from (bridge-provided schema, static per-product tables from the cloud, or per-category assumptions). Not determinable from these sources.

Q2. Cover tilt is wired to EVERY cover description and uses the description's (inverted) position wrapper; no fixture/snapshot covers it — behavior for real tilt devices (angle_horizontal/vertical) is unverified by tests. Also whether inversion of tilt is intended is unclear.

Q3. `CoverInstructionSpecialEnumWrapper` (mach_operate FZ/ZZ/STOP with `position`) has no fixture in tests (grep for `mach_operate` in fixtures: none). Behavior derived from code only.

Q4. Open/close on a cover with a set-position DP sends only the position, never the instruction enum; conversely the OPEN/CLOSE features are tied to the instruction wrapper. A cover with position but no instruction enum gets no open/close services. Reproducing exactly requires keeping both rules.

Q5. Vacuum: `TuyaVacuumActivity.ERROR` exists but is never produced; `fault` bitmap unused. HA's `VacuumActivity` extras (e.g. cleaning modes/rooms) are not implemented; `mode` is a separate select entity; `electricity_left` is a sensor.

Q6. Vacuum `pause` is looked up WITHOUT prefer_function (status_range first) whereas other action dps prefer function; also `pause=True` is used as both a state (activity fallback) and a command (write True). No resume semantics beyond `power_go`.

Q7. Alarm: `AlarmStateWrapper` decodes `alarm_msg` from raw `device.status` unguarded; could raise on malformed data. `changed_by` ignores the low-battery filter. DISARM has no HA feature flag but raises ValueError if "disarmed" not in the enum range.

Q8. Alarm categories: `wg2` marked "Documented, but not in official list" (core/const.py:545); most wg2 fixtures lack `master_mode` so yield no alarm entity.

Q9. `override_dpid_type_information_cls` matches by dpcode only, not dpid (device_quirk.py:588-590), and `InvertedIntegerTypeInformationEx` inverts about `max` (assumes min=0) (type_information_ex.py:34-52).

Q10. Doc drift: comment in H/devices/cl/cl_nfq1essvr99qsvvd.py:10-19 claims open sends `control: "open"` plus position; current core/cover.py:262-274 only sends position.

Q11. Custom quirk files can be loaded from `<config>/tuya_quirks` at runtime (H/devices/__init__.py:16-66; core/coordinator.py:64) — arbitrary Python, i.e. a plug-in escape hatch beyond the data model; the built-in package's own quirks are all declarative builder calls (no arbitrary functions in cl/clkg/dgnbj quirks I read). `_QuirkEntry.apply_when` callables and `map_feeder_schedules_wrapper` are function-valued hooks (H/builder/device_quirk.py:142-148, 562-582), not used by the cover/vacuum/valve/siren/alarm quirks I inspected.

Q12. `swtz_3rzngbyy` fixture contains a siren dp but its category is not in SIRENS; I did not examine why that fixture has one, no conclusion.

Q13. Lock category (`ms`) devices produce no entities in core (section 6); if the custom integration wants locks it is outside "reuse of core knowledge".

Q14. Cover/vacuum/alarm state rewrites happen on every push; `available` toggles only via device.online. If the bridge has different online semantics, entity availability semantics must be defined by the IL.
