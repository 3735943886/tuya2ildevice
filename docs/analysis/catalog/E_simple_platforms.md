# Catalog E: simple-entity platforms (switch, sensor, binary_sensor, number, select, button, event, camera)

Scope: HA core `tuya` integration + `tuya-device-handlers` 0.0.29. Requirements-analysis input for a sans-I/O IL.
Everything below is derived from reading source; statements marked **[unverified]** are inferences.

Path abbreviations (all under `.../scratchpad/core-analysis/`):
- `C/`  = `core/homeassistant/components/tuya/`
- `H/`  = `handlers-dl/tuya_device_handlers-0.0.29/src/tuya_device_handlers/`
- `T/`  = `core/tests/components/tuya/` (snapshots in `T/snapshots/*.ambr`, device fixtures in `T/fixtures/*.json`)

Counting method: descriptions counted by AST over the module (`TuyaXxxEntityDescription(...)` calls) and cross-checked by hand. "Expanded" = after `*SHARED_TUPLE` / helper-function expansion.

---------------------------------------------------------------------------------------------------

## 0. Cross-cutting architecture (applies to all 8 platforms)

### 0.1 Data flow: description table -> definition -> wrapper -> entity
1. Each platform has a static dict `DeviceCategory -> tuple[Description, ...]` (e.g. `C/switch.py:35`, `C/sensor.py:182`, `C/binary_sensor.py:55`, `C/number.py:40`, `C/select.py:30`, `C/button.py:32`, `C/event.py:42`, `C/camera.py:32`).
2. On device add, `async_discover_device` looks up `device.category` (`C/switch.py:971-975`; `C/sensor.py:1820`; `C/binary_sensor.py:462`; `C/number.py:541`; `C/select.py:406`; `C/button.py:103`; `C/event.py:130`; `C/camera.py:52`), then for each description calls `get_default_definition(device, <dpcode>, ...)` from handlers. Truthy result => entity is created; `None` => silently skipped (the "conditional creation" mechanism).
3. The handler returns a `*Definition` dataclass holding one or more `DeviceWrapper` objects (`H/definition/switch.py:12-15`, `sensor.py:20-23`, `binary_sensor.py:20-23`, `number.py`, `select.py`, `button.py`, `event.py:20-23`, `camera.py:19-23`). The wrapper owns: read conversion (`read_device_status`), write conversion (`get_update_commands`), `skip_update` (which DPs changed => should state be written), plus metadata (`native_unit`, `suggested_unit`, `min_value`, `max_value`, `value_step`, `options` — `H/device_wrapper/base.py:8-17`).
4. The entity delegates: state = `wrapper.read_device_status(device)` (`C/entity.py:101-105`); write = `manager.send_commands(device.id, wrapper.get_update_commands(device, value))` (`C/entity.py:92-115`).

### 0.2 Quirk layer (handlers) — relevance to this slice
- Every platform has `<Platform>Quirk(BaseEntityQuirk)` with a `definition_fn` (`H/definition/switch.py:18-26`, `sensor.py:26-33`, `binary_sensor.py:26-33`, `number.py:22-29`, `select.py`, `button.py`, `event.py`, `camera.py`), but **nothing in core or in any of the 32 shipped device quirks uses these classes**: grep for `definition_fn` / `*Quirk` in `C/*.py` returns nothing, and in `H/devices/**` only `DeviceQuirk` (the builder) is used. `get_default_definition` is called directly by core with the description key. => Per-device *entity-definition* overrides do not exist in 0.0.29; only per-device *DP-metadata* overrides do (below).
- Device-level quirks (`H/builder/device_quirk.py`) are keyed by `product_id` (`H/registry.py`, `get_quirk_for_device`) and applied once at device add (`C/coordinator.py:150` `initialise_device_quirk`). Builder operations and usage across the 32 shipped quirks (grep count): `add_dpid_integer` 26, `add_dpid_enum` 19, `override_dpid_type_information_cls` 8 (all `InvertedIntegerTypeInformationEx`, `H/type_information_ex.py:16-47`, on cover/curtain products), `remove_dpid` 4, `set_dpid_strategy_to_enum` 2, `override_category` 2, `map_dpid_initial_status_values` 2, `add_dpid_boolean` 1, `add_dpid_bitmap` 1, `map_feeder_schedules_wrapper` 1. Each entry supports `apply_when: Callable[[CustomerDevice], bool]` (`H/builder/device_quirk.py:36-45`) — a *code predicate* (used by `kt_hw50w7qvxluhslkk.py:26-46` to detect a Fahrenheit variant from `status["temp_set"] >= 450`).
- Quirks that matter to *my* platforms: (a) they inject missing DPs (id, code, type, range, unit, scale) so a description can find them, e.g. `wsdcg_m7kacaxrxbxeegfs.py` adds `ext_temp` (Integer, unit ℃, -200..1000, scale 1) used by `C/sensor.py:1514`; `dgnbj_qajfz5x1lqej5xxw.py` adds `ph_current` (Integer, unit "ph", 0..1400, scale 2) used by `C/sensor.py:556`; `cs_ma3oq4onxxwg91ky.py:12-17` adds Bitmap `fault` labels `["E1","E2","tankfull"]` used by binary_sensor CS `tankfull` (`C/binary_sensor.py:86`); (b) `report_type="sum"` on `feed_report`/`add_ele` (`cwwsq_wfkzyy0evslzsmoi.py:34`, `cz_wifvoilfrqeo6hvu.py:35`) which flips a sensor into delta-accumulation mode (see 2.2 pattern S-DELTA); (c) `map_feeder_schedules_wrapper` for the feeder service (section 9).
- **Consequence for IL:** the "device knowledge" is two-tiered: (1) category-keyed description tables (pure data), (2) product_id-keyed DP metadata patches (data, but with optional predicate `apply_when`).

### 0.3 DP metadata model that everything depends on (`H/type_information.py`)
The wrappers are driven by cloud-provided per-DP metadata: `device.function[code]` (writable DPs) and `device.status_range[code]` (readable DPs), each `{type, values(JSON string)}`; `status_range` additionally has `report_type` (`H/type_information.py:88-92`).
- DP types (`H/const.py:33-51`): `Bitmap, Boolean, Enum, Integer, Json, Raw, String`; lenient parse maps `bitmap,bool,enum,json,raw,string,value(->Integer)` (`H/const.py:53-63`).
- Integer metadata: `min,max,scale,step,unit` (`H/type_information.py:278-315`). Scaled value = raw / 10**scale (`:289-291`); write raw = `round(value*10**scale)` (`:293-295`).
- Enum metadata: `range: list[str]` (`:216-241`). Bitmap: `label: list[str]` (`:133-158`). Boolean/Json/Raw/String: no metadata used.
- `find_dpcode` (`:66-118`): iterates given dpcode(s), for each looks in `(function, status_range)` if `prefer_function` else `(status_range, function)`; **succeeds only if DP is present in that dict AND its declared type parses to the wrapper's expected `_DPTYPE`**; then `_from_json` may return None if the JSON `values` is empty/falsy (`Enum`/`Bitmap`/`Integer` only, `:151,234,303`). A quirk may substitute the TypeInformation class per dpcode (`:86,95-98`).
- Read validation (returns None + once-per-device warning `_should_log_warning` `:28-40`): Boolean: `raw in (True, False)` (`:198`) — note 0/1 also pass and are returned as-is; Enum: `raw in range` (`:258`); Integer: `isinstance(raw,int) and min<=raw<=max` (`:336`) — **a float raw or an out-of-range int => None**; Bitmap: must be int (`:164`); Json: `json.loads` else None (`:368-381`); Raw: `base64.b64decode` else None (`:394-407`); String: passthrough (`:418`).
- Write validation (`prepare_set_value`): Boolean must be `bool` (`:187-192`); Enum must be `str` and in `range` (`:243-251`); Integer must be int/float, scaled back with `round`, then `min<=raw<=max` else error (`:317-329`) — **step is NOT enforced on write**. Errors are `PrepareSetValueError` -> re-raised as `SetValueOutOfRangeError(ValueError)` (`H/device_wrapper/common.py:122-129`, `exception.py`); core has no handler for `SetValueOutOfRangeError` (grep of `core/homeassistant` finds none) so it propagates as a ValueError.
- **Implication for a local-MQTT/bridge integration:** the bridge gives dp *ids* and raw values; the cloud-derived `code -> (id, type, values, report_type, function/status membership)` metadata must come from somewhere else (or be encoded in the IL). Test fixtures show the cloud shape: `{"category","product_id","function":{code:{type,value}},"status_range":{code:{type,value,[report_type]}},"status":{code:value}}` (e.g. `T/fixtures/wxkg_l8yaz4um5b3pwyvf.json`, `T/fixtures/cz_guitoc9iylae4axs.json` with `"report_type":"sum"`).

### 0.4 Entity base behaviors (`C/entity.py`)
- `TuyaEntityDescription(EntityDescription)` is an **empty** subclass (`:16-18`): the base HA `EntityDescription` fields are the whole vocabulary (`key, name, translation_key, translation_placeholders, icon, entity_category, device_class, entity_registry_enabled_default, ...`).
- `unique_id = f"tuya.{device.id}{description.key}"` (`:35`) — device id and key are concatenated with no separator (snapshot: `tuya.6tbtkuv3tal1aesfjxqwind_direct`, `T/snapshots/test_sensor.ambr:4176-4180`; `tuya.qi94v9dmdx4fkpncqldphase_aelectriccurrent`, `test_sensor.ambr:6439-6475`). **The `key` is therefore an identity contract for registry continuity** and for computed sensors is a synthetic string (`f"{dpcode}electriccurrent"`, `C/sensor.py:81`).
- `has_entity_name=True`, `should_poll=False` (`:24-25`). No polling: entity state only changes via push (`update_device` -> dispatcher, `C/coordinator.py:97-118`).
- **Availability = `device.online` only** (`:42-46`). No per-DP availability: a DP with no value yields state `unknown` (None), not `unavailable`.
- Update filtering (`:59-90`): if `updated_status_properties is None` (online/offline transitions) always write state; else write only if `_process_device_update` returns True. Platform entities implement it as `not wrapper.skip_update(device, updated_props, dp_timestamps)`; default `DPCodeWrapper.skip_update` = `dpcode not in updated_status_properties` (`H/device_wrapper/common.py:34-45`). => Entities only refresh when *their* DP was in the update. (Test: `T/test_sensor.py:70-95`.)
- Writes: `_async_send_commands` no-ops on an empty list (`:95-96`); command shape is `[{"code": <dpcode>, "value": <raw>}]` (`H/device_wrapper/common.py:70-83`). One command per action for all 8 platforms in this slice (single-DP). Cloud API takes DP **codes**, not ids.
- `device.set_up = True` at entity init "so MQ can subscribe" (`:37-38`) — cloud-MQ specific, irrelevant to IL.

### 0.5 What is "code" in this slice (summary; details in per-platform sections)
Only these non-data items exist for the 8 platforms: (a) about 12 sensor wrapper classes (wind direction, delta accumulator, electricity json/raw/hex extractors), (b) 3 binary-sensor wrappers (boolean, bitmap-bit, in-set), (c) 3 event wrappers (enum, base64-string, base64-raw), (d) the unit/device-class reconciliation logic in `TuyaSensorEntity`/`TuyaNumberEntity`, (e) camera stream/image (cloud API + ffmpeg), (f) feeder-schedule codec (service, not entity), (g) quirk `apply_when` predicates. Everything else is declarative.

---------------------------------------------------------------------------------------------------

## 1. SWITCH (`C/switch.py`, 1029 lines)

### 1.1 Description shape
`TuyaSwitchEntityDescription(TuyaEntityDescription, SwitchEntityDescription)` with **no added fields** (`C/switch.py:30-32`). Wrapper class is not configurable; always `DPCodeBooleanWrapper` (`H/definition/switch.py:33-41`). The DP looked up is `description.key` itself (`C/switch.py:971-975` passes `description.key`).

Structure: `SWITCHES: dict[DeviceCategory, tuple[...]]` (`:35-947`), 51 category keys, **157 descriptions**, plus 2 aliases: `SWITCHES[CZ]=SWITCHES[PC]` (`:950`), `SWITCHES[DGHSXJ]=SWITCHES[SP]` (`:953`) => 53 lookup keys.
Categories: BH,BZYD,CJKG,CL,CN,CS,CWJWQ,CWWSQ,CWYSJ,DJ,DLQ,DR,FS,FSD,GGQ,HXD,JSQ,KG,KJ,KT,KS,MAL,MSP,MZJ,PC,QCCDZ,QJDCZ,QN,QXJ,SD,SGBJ,SJZ,SP,SZ,SZJQR,TDQ,TYNDJ,WG2,WK,WKCZ,WKF,WNYKQ,WSDCG,XDD,XNYJCN,XXJ,YWBJ,ZNDB,ZNJDQ,ZNJXS,ZNRB.

Field frequency (of 157):
| field | count | values / notes |
|---|---|---|
| key | 157 | always a `DPCode` member (71 distinct DP codes) |
| translation_key | 149 | e.g. `child_lock`, `indexed_switch`, `switch`, `power`, `socket`, `indexed_usb`, ... |
| entity_category | 77 | always `CONFIG` |
| translation_placeholders | 51 | always `{"index": "N"}` (or none) — indexed channels (`SWITCH_1..8`, `SWITCH_USB1..6`, alarms) |
| device_class | 32 | `OUTLET` 26; `SWITCH` 6 (all in category DR, `:195-230`) |
| icon | 14 | mdi:account-lock 4, mdi:radiator 3, others 1 each |
| name | 8 | `name=None` 2 (`BZYD` main switch `:48-51`, `WNYKQ` `:862`), hard-coded English strings 6 (DR `:195-230`: "Power","Side A Power","Side B Power","Preheat","Side A Preheat","Side B Preheat" — these DR entries have no translation_key) |
Not used: entity_registry_enabled_default, entity_registry_visible_default, any `dpcode` alias.

### 1.2 Patterns (all are straight bool DP -> switch)
- **SW-PLAIN**: single Boolean DP, 1 description = 1 entity (majority).
- **SW-INDEXED**: `SWITCH_1..N`/`SWITCH_USB1..6` with `translation_key=indexed_*` + `{"index": "N"}` (CJKG `:71-92`, GGQ `:271-312`, KG `:364-452`, PC `:536-613`, TDQ `:754-796`, HXD `:313-346`, WKCZ `:835-848`).
- **SW-CONFIG**: `entity_category=CONFIG` toggles (child lock, anion, mute, ...).
- **SW-SAME-KEY-DIFFERENT-CATEGORY**: e.g. `KG` main `SWITCH` has `device_class=OUTLET` (`:448`), `DLQ` has none (`:189`), `DJ` has comment: RGB-light sockets advertise `dj` but expose an extra plug switch (`:174-182`).
- **SW-ALIAS**: category alias tables (above).
No computed/multi-DP/inverted/JSON switches exist. (`DPCodeInvertedBooleanWrapper` exists in `H/device_wrapper/extended.py:94-106` but is not referenced by `C/switch.py` or `H/definition/switch.py`; probably used by another platform — **[unverified]** outside my slice.)

### 1.3 Read / availability / conditional creation
- Created iff `DPCodeBooleanWrapper.find_dpcode(device, key, prefer_function=True)` succeeds: DP `key` exists in `function` (checked first) or `status_range` with type Boolean (`H/definition/switch.py:33-41`; `H/type_information.py:66-118`). **Does not require the DP to be writable** — presence in `status_range` alone suffices, and the write would be attempted anyway.
- State: `is_on = wrapper.read_device_status` -> bool | None (`C/switch.py:1002-1005`). Invalid value (e.g. string) -> `unknown` (test `T/test_switch.py:139-167`, params `(True,on),(False,off),(None,unknown),("some string",unknown)`). 0/1 ints pass `raw in (True, False)` and return the int as-is (`H/type_information.py:198-199`) [HA treats truthiness; **[unverified]** for exact state].
- Enabled by default always; no registry-disabled switches.

### 1.4 Write
`turn_on` -> `[{"code": key, "value": True}]`, `turn_off` -> `[{"code": key, "value": False}]` (`C/switch.py:1022-1029`; test `T/test_switch.py:95-136`). Boolean type check only (`H/type_information.py:187-192`).

### 1.5 Data vs code
100% data: `(category, dpcode/key, translation_key, placeholders{index}, entity_category, device_class, icon, name)`.

---------------------------------------------------------------------------------------------------

## 2. SENSOR (`C/sensor.py`, 1957 lines)

### 2.1 Description shape
`TuyaSensorEntityDescription(TuyaEntityDescription, SensorEntityDescription)` adds exactly two fields (`C/sensor.py:66-71`):
- `dpcode: DPCode | None = None` — DP to read if different from `key` (lookup uses `description.dpcode or description.key`, `:1827`).
- `wrapper_class: tuple[type[DPCodeTypeInformationWrapper], ...] | None = None` — ordered candidate wrapper classes; **first whose `find_dpcode` succeeds wins**; if tuple given and none match => no entity (`H/definition/sensor.py:43-48`).

Table: `SENSORS` (`:182-1796`), 61 category keys, + aliases `SENSORS[DGHSXJ]=SENSORS[SP]` (`:1800`) and `SENSORS[PC]=SENSORS[KG]` (`:1803`) => 63 lookup keys. **250 literal `TuyaSensorEntityDescription(...)` calls; 450 expanded descriptions**:
- 239 literal in the category table,
- 5 in `BATTERY_SENSORS` (`:141-177`) which is spliced (`*BATTERY_SENSORS` or bare `BATTERY_SENSORS`) into **35 categories** => 175 expanded,
- 6 templates in `_electricity_data(dpcode)` (`:74-137`) instantiated 6 times (`PHASE_A/B/C` in `DLQ` `:609-611` and `ZNDB` `:1687-1689`) => 36 expanded.
(Check: 239+175+36 = 450.) ~136 distinct DP codes referenced by key/dpcode (`DPCode.X` literals).

Field frequency (250 literal calls; shared tuples counted once):
| field | count | values / notes |
|---|---|---|
| key | 250 | `DPCode.X`, or f-string for computed sensors (`:81..127`, `:1674`) |
| translation_key | 246 | 4 without: `EC_CURRENT` (`:519-523`), `PH_CURRENT` (`:556`), `WINDSPEED_AVG` (`:1118-1122`), `PRESSURE_VALUE`(`YLCG` `:1618-1624`, name=None) |
| state_class | 229 | `MEASUREMENT` 196, `TOTAL_INCREASING` 33; never `TOTAL` |
| device_class | 200 | TEMPERATURE 45, ENERGY 26, HUMIDITY 25, POWER 21, CURRENT 11, VOLTAGE 11, CO2 8, BATTERY 7, VOC 7, PM25 7, DURATION 6, ILLUMINANCE 5, PM10 3, CO 2, FREQUENCY 2, PRESSURE 2, and 1 each: REACTIVE_POWER, APPARENT_POWER, POWER_FACTOR, CONDUCTIVITY, PH, WEIGHT, PM1, WIND_SPEED, PRECIPITATION, PRECIPITATION_INTENSITY, WIND_DIRECTION, DISTANCE |
| suggested_unit_of_measurement | 49 | A 10, V 10, ppm 10, µg/m3 10, kWh 8, W 1 |
| entity_category | 24 | always `DIAGNOSTIC` |
| translation_placeholders | 21 | `{"index": "N"}` (indexed sensors: CZ `:384-470`, QXJ `:1046-1105`, SWTZ `:1289`, XNYJCN pv channel `:1561-1574`) |
| entity_registry_enabled_default | 14 | always `False` (disabled by default): AQCZ 3 (`:184-206`), DLQ 3 (`:612-634`), WKCZ 3 (`:1442-1465`), WNYKQ 3 (`:1480-1505`), CWYSJ 2 (`uv_runtime :319`, `water_time :338`) |
| native_unit_of_measurement | 8 | kWh 4 (CZ `:370`, DLQ `:577`, KG `:790`, ZNJDQ `:1712`), `%` 2 (battery), minutes 1 (`MZJ REMAIN_TIME :927`), W 1 (`ZNNBQ :1728`) |
| dpcode | 7 | 6 = electricity templates, 1 = `ZNDB TOTAL_POWER` (`:1673-1679`) |
| wrapper_class | 7 | 6 = electricity templates, 1 = `WindDirectionEnumWrapper` (`QXJ :1140-1146`) |
| name | 3 | `None` 2 (`RQBJ` gas `:1173-1179`, `YLCG` pressure `:1618-1624`), `"Methane"` 1 (`DGNBJ CH4_SENSOR_VALUE :486-491`) |
| suggested_display_precision | 1 | `0` (`ZNNBQ POWER_TOTAL :1728-1736`) |
Not used: icon, options, `entity_registry_visible_default`, `last_reset`, `native_precision`.

### 2.2 Exhaustive pattern list
Read default (no `wrapper_class`): `get_default_definition` (`H/definition/sensor.py:36-64`):
1. `IntegerTypeInformation.find_dpcode(device, dpcode)` (status_range first, then function). If found and `report_type == "sum"` -> `DeltaIntegerWrapper` (pattern S-DELTA); else `DPCodeIntegerWrapper` (S-INT).
2. Else `DPCodeEnumWrapper.find_dpcode` -> enum sensor (S-ENUM).
3. Else None: **String/Raw/Json/Bitmap/Boolean DPs never become sensors unless an explicit `wrapper_class` is given** (only the electricity and wind wrappers do so).

Patterns (with counts and examples):

**S-INT (plain scaled integer, ~220 of 250 literals + all `BATTERY_SENSORS`)** — DP Integer -> `raw/10^scale`, unit from DP metadata `unit`. Example `C/sensor.py:209-214` (`TEMP_CURRENT`, temperature, measurement). Unit comes from the *device* (`IntegerTypeInformation.unit`, `H/device_wrapper/common.py:171-182` sets `native_unit`), reconciled with device_class by S-UNIT below.

**S-ENUM (auto ENUM sensor)** — a description with `device_class is None` whose DP is an Enum becomes `SensorDeviceClass.ENUM` with `options = enum.range` (`C/sensor.py:1862-1868`), unit forced None (`:1884-1888`). 20 literal descriptions have neither device_class nor state_class and are the intended enum/text sensors: `BATTERY_STATE :158`, `BH STATUS :221`, `CL TIME_TOTAL :227` (integer, plain), `CWJWQ WORK_STATE_E :304`, `CWYSJ WATER_LEVEL :345`, `CZ DEVICE_STATE1/2 :384,:389`, `DGNBJ BRIGHT_STATE :529`, `HJJCY AIR_QUALITY_INDEX :646`, `JSQ LEVEL_CURRENT :755`, `KJ FILTER :806`, `KJ AIR_QUALITY :855`, `LDCG BRIGHT_STATE :861`, `MSP EXCRETION_TIMES_DAY :907`, `MSP STATUS :911`, `MZJ STATUS :923`, `MZJ REMAIN_TIME :927`, `QCCDZ WORK_STATE :989`, `SFKZQ WORK_STATE :1251`, `YWCGQ LIQUID_STATE :1637`. Same description works as a plain numeric sensor if the DP is Integer (e.g. `KJ FILTER` "filter_utilization" is numeric %, `MZJ REMAIN_TIME`). **=> the same description resolves to different entity kinds depending on DP type.** State strings are the raw enum values; HA translations for 10 sensor keys live in `C/strings.json` (`entity.sensor.<translation_key>.state`): air_quality, air_quality_index, cat_litter_box_status, charger_status, indexed_meter_status, irrigation_status, liquid_state, odor_elimination_status, sous_vide_status, water_level_state. (Translations are UI text, not device logic.)
   Edge: descriptions with `state_class` but no `device_class` (e.g. `CH2O_VALUE :253`, `GAS_SENSOR_VALUE :481`) whose DP turned out to be Enum would also become ENUM+state_class sensors — HA-side behavior **[unverified]**.

**S-DELTA (report_type == "sum" accumulator)** — `DeltaIntegerWrapper` (`H/device_wrapper/sensor.py:51-98`). Chosen purely from DP metadata `report_type=="sum"` (not from the description). Behavior: state = local running total, starts at 0 on every HA start (no persistence) (test expects "0" initially `T/test_sensor.py:98-116`); on each update that includes this DP **and** `dp_timestamps[dpcode]` exists **and** differs from the last seen timestamp **and** value not None: `total += float(raw_scaled)` and state is written (`:62-94`); otherwise skip. `read_device_status` returns the accumulator irrespective of current status (`:96-98`). Forces `state_class=TOTAL_INCREASING` if description has none (`C/sensor.py:1869-1875`). Needs **per-DP timestamps** from the transport (`dp_timestamps`), and mutable per-entity state. Only two shipped quirks set `report_type="sum"`: `cwwsq_wfkzyy0evslzsmoi.py:34` (feed_report), `cz_wifvoilfrqeo6hvu.py:35` (add_ele); cloud fixture `T/fixtures/cz_guitoc9iylae4axs.json` (add_ele scale 3, sum) also does. Test sequence with expected totals: `T/test_sensor.py:120-233` (0 -> +200 -> +300 -> duplicate timestamp ignored -> None ignored -> missing timestamps ignored; state 0.2, 0.5, 0.5, 0.6).

**S-ELEC-RAW / S-ELEC-JSON (composite: 6 attributes from one DP)** — `_electricity_data(dpcode)` (`C/sensor.py:74-137`) yields 6 sensors per source DP (`phase_a`, `phase_b`, `phase_c`; used by `DLQ` and `ZNDB` only). Each has `key=f"{dpcode}electriccurrent|power|voltage|reactivepower|apparentpower|powerfactor"`, `dpcode=<phase DP>`, `translation_key=f"{dpcode}_current"` etc., device_class CURRENT/POWER/VOLTAGE/REACTIVE_POWER/APPARENT_POWER/POWER_FACTOR, `state_class=MEASUREMENT`, and `wrapper_class=(<X>RawWrapper, <X>JsonWrapper)` (Raw tried first, then Json).
  - Raw path (`H/device_wrapper/sensor.py:142-211`, `H/raw_data_model.py:8-100`): DP type **Raw** (base64 in cloud, decoded by `RawTypeInformation.read_device_value` `H/type_information.py:390-407`). Parsed by `ElectricityData.from_bytes`: 
    - v1: `len==17` and `raw[0:2]==01 0f`; v2: `len==18` and `raw[0:2]==02 0f`; data = `raw[2:17]`; big-endian: voltage `>H` /10 (V), current 3 bytes (mA), power 3 bytes (W), reactive 3 bytes (var), apparent 3 bytes (VA), power_factor 1 byte /100; v2 adds `raw[17]` sign bitmap (bit0 current, bit1 power, bit2 reactive, bit3 power_factor negative; apparent has no sign bit).
    - legacy: `len>=8`: voltage `>H`/10, current 3B, power 3B; **no** reactive/apparent/pf (None).
    - else -> None.
  Units: current native `mA` suggested `A`; power native `W` suggested `kW`; voltage `V`; reactive `var`->`kvar`; apparent `VA`->`kVA`; power factor unitless (`H/device_wrapper/sensor.py:168-211`). Snapshot confirms HA unit conversion result `A` and `suggested_display_precision: 2` (`T/snapshots/test_sensor.ambr:6439-6475`); the precision is HA-derived from unit conversion, not set by tuya.
  - Json path (`:101-139`): DP type **Json**; value is a JSON object read via `json.loads`; attribute names `electricCurrent`(A), `power`(kW), `voltage`(V), `reactivePower`(kvar), `apparentPower`(kVA), `powerFactor` — no scaling applied, unit declared by the wrapper.
  - Conditional creation: `DPCodeParsedAttributeWrapper.find_dpcode` (`H/device_wrapper/extended.py:169-194`) creates the sensor if the DP exists with correct type AND (no payload yet, or parsed payload has non-None attribute). So legacy 8-byte payloads create only current/power/voltage sensors; v1/v2 create all 6. Json variant: `DPCodeJsonDictAttributeWrapper.find_dpcode` (`:119-140`) same logic keyed on attribute name presence in the dict.
  - Read: `getattr(parsed, attr)` (`:196-203`) — **payload is re-parsed on every read**.
  - Also present but **unused by core 0.0.29**: `Electricity*HexStringWrapper` (`H/device_wrapper/sensor.py:155-165,213-259`, String DP holding a hex frame, `ElectricityData.from_hex`). Not imported by `C/sensor.py:14-29` (**[unverified]** whether another slice uses them; grep shows none in core).
  - `ZNDB` also has a *non-composite* `TOTAL_POWER` sensor using `dpcode=TOTAL_POWER` with a synthetic key `f"{TOTAL_POWER}power"` and no wrapper_class (`C/sensor.py:1673-1679`) => default S-INT/S-ENUM path on DP `total_power` (key differs from dpcode purely to keep a unique_id).

**S-WIND (enum -> number via lookup table)** — `WindDirectionEnumWrapper` (`H/device_wrapper/sensor.py:22-48`): DP Enum with 16 compass strings mapped to degrees (`north=0, north_north_east=22.5, ... north_north_west=337.5`, step 22.5); unknown enum member -> None (`.get`). Used once, `QXJ WIND_DIRECT` (`C/sensor.py:1140-1146`), `device_class=WIND_DIRECTION`, `state_class=MEASUREMENT`. **Observed defect/behavior:** the wrapper does not set `native_unit` (inherits `None` from `DPCodeEnumWrapper`), so `_validate_device_class_unit(None)` finds `None` not among allowed units, has no fallback unit, and executes the "device class ignored" branch (`C/sensor.py:1927-1936`); the snapshot shows `original_device_class: None` and `unit_of_measurement: None` for this entity (`T/snapshots/test_sensor.ambr:4137-4175`). Whether HA's WIND_DIRECTION class expects `°` — **[unverified]** (HA core source for `sensor` not in the download); snapshot proves outcome only.

**S-BATTERY (shared block)** — 5 descriptions (`C/sensor.py:141-177`) all `entity_category=DIAGNOSTIC`: `battery_percentage`, `battery` (comment: "non-standard contact sensor implementations"), `battery_value`, `va_battery` (device_class BATTERY, measurement), `battery_state` (enum, no device class, `:158`). Two of them carry `native_unit_of_measurement=%`. Included in 35 categories. All 5 attempt creation; each is created only if its DP exists as Integer (or Enum for `battery_state`), so a device may end up with several battery entities.

**S-UNIT (unit reconciliation, executed at entity construction)** — `TuyaSensorEntity._validate_device_class_unit` (`C/sensor.py:1879-1936`), inputs: DP-declared unit (`wrapper.native_unit`), description.device_class/native_unit, device `temp_unit_convert` status:
  1. device_class ENUM -> unit None.
  2. device_class None, or DP unit is in HA's allowed units for that class (`SENSOR_DEVICE_CLASS_UNITS`) -> use DP unit verbatim.
  3. device_class TEMPERATURE, DP unit empty, and `status["temp_unit_convert"]` in {"c","f"} -> °C/°F (`C/util.py:12-37`; tests `T/test_sensor.py:288-305`).
  4. Else look up alias table `C/const.py:1034-1229` (`UNITS`, `DEVICE_CLASS_UNITS` built at `:1224-1229`) by DP unit, exact then lowercased (`:1910`) -> replaced by canonical unit string. **Rename only, no numeric conversion.** Alias examples: `pct/percent/% RH -> %`; `a -> A`; `ma -> mA`; `wh -> Wh`; `kwh/kilowatt-hour/kW·h/kW.h -> kWh`; `m3 -> m³`(gas); `mm -> mm/h`(precip. intensity); `lux -> lx`; `ug/m3` variants (with U+00B5 / U+03BC) -> µg/m³; `mg/m3`; `watt`, `kilowatt`; `hpa`, `millibar`, `inhg`; `db`, `dbm`; temperature aliases `{"°c","c","celsius","℃"}` / `{"°f","f","fahrenheit","℉"}` (`H/const.py:14-15`); `volt`, `mv`, `millivolt`; `ph` -> "" (PH); `us` -> µS/cm.
  5. Else if description has `native_unit_of_measurement` -> keep it, log debug (fallback unit; `:1915-1926`). Test: `T/test_sensor.py:236-285` (DLQ `add_ele` with unit `invalid_uom` -> kWh from description).
  6. Else unit = DP unit, **device_class and suggested_unit dropped** (`:1927-1936`). Test: `hjjcy` temperature with bad unit => no device class (`T/test_sensor.py:255-266`).
  Also: `suggested_unit_of_measurement` from description wins; else from wrapper (`suggested_unit`, only electricity Raw/Hex wrappers set it) (`:1858-1861`).
  Note (HA behavior, **[unverified]** here): assigning `_attr_native_unit_of_measurement` in step 2 takes precedence over the description's `native_unit_of_measurement`, so the description's unit is effectively only a fallback for steps 5, i.e. the 8 `native_unit_of_measurement` fields are "fallback units", not overrides. The CZ/KG/ZNJDQ `add_ele` kWh example sets *both* native and suggested kWh.

**S-REGISTRY-DISABLED** — 14 descriptions with `entity_registry_enabled_default=False` (list above) — mostly `cur_current/cur_power/cur_voltage` diagnostic-ish channels.

**S-MULTIKEY-SAME-QUANTITY** — several categories list alternate DP codes for the same logical quantity, all instantiated when present: temperature `TEMP_CURRENT`+`TEMP_CURRENT_F` (BH `:209-220`, JSQ `:743-754`), `VA_TEMPERATURE`+`TEMP_CURRENT` (QXJ `:1028-1039`, TDQ `:1354-1365`, WNYKQ `:1468`, WSDCG `:1508`), humidity `HUMIDITY_VALUE`+`VA_HUMIDITY` (QXJ `:1067-1078`, TDQ `:1366-1377`, WSDCG `:1526-1537`). Same `translation_key`, different `key` (unique_id differs), so a device with both DPs gets duplicate-named entities. There is no "prefer X over Y" mechanism.

**S-ALIAS / S-SHARED** — table sharing: `DGHSXJ<-SP`, `PC<-KG`, `BATTERY_SENSORS` spliced in 35 categories.

**S-NUMBER-LIKE-ENERGY** — energy DPs use `TOTAL_INCREASING` + `ENERGY` w/o native unit (unit from DP metadata, alias-normalized). Several descriptions reuse `translation_key="total_energy"` for different DP codes in one category (DLQ: `TOTAL_FORWARD_ENERGY :571`, `ADD_ELE :577`, `FORWARD_ENERGY_TOTAL :584`) — dedup by DP presence only. **DLQ `CUR_NEUTRAL` is mapped to `translation_key=total_production`, ENERGY, TOTAL_INCREASING (`:596-601`)** — semantic oddity, preserved as data.

### 2.3 Multi-DP / computed sensors — exact logic
There are no sensors that read *two* DPs. Computed sensors are strictly *single-DP-derived*: (1) electricity attributes (1 DP -> 6 sensors, decoded per attribute), (2) wind enum lookup, (3) delta accumulation (stateful, uses timestamps), (4) enum->ENUM class promotion. Everything else is `raw/10^scale` with unit reconciliation. No expressions, no templates, no cross-DP math anywhere in the sensor table.

### 2.4 Availability & conditional creation (sensor)
- Created iff wrapper found: default path needs DP present in `status_range`/`function` as Integer or Enum (function checked second since `prefer_function=False`, `H/type_information.py:81-85`); explicit `wrapper_class` path needs a class-specific type (Raw/Json/String) + attribute presence.
- Value `None`/invalid -> state `unknown` (+ one-time warning per device/DP/value).
- Entity `available` = `device.online`.
- 14 sensors are created but disabled in the entity registry.
- Sensors never take `options` from a description; ENUM options come from DP `range`.

### 2.5 Write side
None (read-only). `native_value = wrapper.read_device_status` (`C/sensor.py:1938-1942`).

### 2.6 Data vs code (sensor)
- Pure data: ~236 of 250 literal descriptions (category, key, translation_key, device_class, state_class, suggested unit, native unit fallback, entity_category, enabled_default, placeholders, name, precision).
- Needs a *named reader* (code today, could be an IL `reader` enum): `electricity_raw.{current,power,voltage,reactive_power,apparent_power,power_factor}`, `electricity_json.{6}`, (unused `electricity_hex.{6}`), `wind_direction_enum`, `delta_sum`.
- Needs *metadata-driven runtime logic*: type dispatch (Integer vs Enum), report_type=="sum", unit-alias table (`C/const.py:1034-1229` ~35 alias rows), temp_unit_convert lookup, "incompatible unit" fallback rules.

---------------------------------------------------------------------------------------------------

## 3. BINARY_SENSOR (`C/binary_sensor.py`, 520 lines)

### 3.1 Description shape
`TuyaBinarySensorEntityDescription` (`C/binary_sensor.py:25-37`) adds:
- `dpcode: DPCode | None = None` (DP to read, else `key`),
- `on_value: bool | float | int | str | set[...] = True`,
- `bitmap_key: str | None = None` (label name inside a Bitmap DP).
Wrapper is chosen in handler, not in the description (`H/definition/binary_sensor.py:36-64`).

Table: `BINARY_SENSORS` (`:55-445`), 28 categories, **56 literal descriptions, 73 expanded** (shared `TAMPER_BINARY_SENSOR` `:40-45` — `key=TEMPER_ALARM, name="Tamper", device_class=TAMPER, entity_category=DIAGNOSTIC` — is spliced into 17 categories). No alias assignments.
Categories: CO2BJ,COBJ,CS,CWWSQ,DGNBJ,HPS,JQBJ,JWBJ,LDCG,MC,MCS,MK,MSP,PIR,PM2_5,QXJ,RQBJ,SGBJ,SJ,SOS,VOC,WG2,WK,WKF,WSDCG,YLCG,YWBJ,ZD.

Field frequency (56 literals):
| field | count | notes |
|---|---|---|
| key | 56 | DPCode or f-string/literal for multi-entity DPs |
| device_class | 50 | PROBLEM 15, SAFETY 12, GAS 5, DOOR 4, BATTERY_CHARGING 3, SMOKE 3, MOISTURE 2, TAMPER 1, OCCUPANCY 1, LOCK 1, MOTION 1, WINDOW 1, VIBRATION 1 (6 have none) |
| on_value | 34 | see below |
| translation_key | 25 | 23 in strings.json `entity.binary_sensor` |
| entity_category | 18 | always DIAGNOSTIC (17 via tamper + others) |
| dpcode | 17 | 14 bitmap + 3 shock-state |
| bitmap_key | 14 | CS 12, MSP 2 |
| name | 1 | "Tamper" (hard-coded English) |
Not used: `entity_registry_enabled_default`, icon, inverted flag.

### 3.2 Patterns
- **B-BOOL (22 literals without on_value)**: Boolean DP, `on_value` ignored (`H/definition/binary_sensor.py:53-54`: if a Boolean-typed DP is found, `DPCodeBooleanWrapper` is used regardless of `on_value`). E.g. `CWWSQ CHARGE_STATE :181`, `SOS SOS_STATE :363`, `TAMPER`.
- **B-INSET-STR (on_value scalar string)**: DP is Enum/String (any type that is not Boolean, incl. even Raw/Integer); state = `raw_value in {on_value}` (`H/device_wrapper/binary_sensor.py:50-66`). Distribution of literals: `'alarm'` 20; `'1'` 2 (`COBJ CO_STATE :65`, `RQBJ GAS_SENSOR_STATE :340`); `'feeding'`, `'pir'`, `'open'`(WK valve_state), `'opened'` (WKF window_state), `'vibration'`, `'drop'`, `'tilt'` 1 each. Note `on_value='1'` compares the *string* "1".
- **B-INSET-SET (on_value is a set, 5)**: `HPS PRESENCE_STATE {'presence','small_move','large_move','peaceful'}` (`:251`), `MC STATUS {'open','opened'}` (`:275`), `MK CLOSED_OPENED_KIT {'AQAB'}` (`:293`, DP is a base64 Raw — compared against the *raw base64 string* because `DPCodeInSetWrapper` uses base `_read_dpcode_value` = `device.status.get` (`H/device_wrapper/common.py:51-58`), no decode), `SJ WATERSENSOR_STATE {'1','alarm'}` (`:355`), `YWBJ SMOKE_SENSOR_STATE {'1','alarm'}` (`:418`).
- **B-BITMAP (14)**: DP type Bitmap; `bitmap_key` names a label; bit index = `label.index(key)`; state = `(raw & (1 << idx)) != 0` (`H/device_wrapper/binary_sensor.py:12-47`). Creation requires `bitmap_key in type_information.label` (`:38-47`) — so alternative label spellings can be listed as separate descriptions and only the ones the device declares get created. `CS` (dehumidifier) has 12 such (`:78-172`) all on `dpcode=FAULT`: `water_full`, `tankfull`, `FULL`, `defrost`, `COIL`, `wet`, `Cleaning`, `E1`, `CL`, `CH`, `LO`, `MOTOR`; all PROBLEM/DIAGNOSTIC. Three of them share `translation_key='tankfull'` (`water_full`,`tankfull`,`FULL`) — alternates for the same concept. `MSP`: `full_fault`, `box_out` (`:300-315`). The synthetic key is `f"{FAULT}_{label}"` or the bare label (`'tankfull'`, `'defrost'`, `'wet'`) — inconsistent, preserved for unique_id stability. Test: `T/test_binary_sensor.py:96-128` (0x1->tankfull, 0x2->defrost, 0x80->wet).
- **B-MULTI-ENTITY-FROM-ONE-ENUM (3)**: `ZD SHOCK_STATE` yields 3 entities via `dpcode=SHOCK_STATE`, `on_value` = `'vibration'|'drop'|'tilt'` (`:426-444`), keys `f"{SHOCK_STATE}_vibration|_drop|_tilt"`. (Same pattern for bitmap CS.)
- **B-SHARED**: `TAMPER_BINARY_SENSOR` in 17 categories (`:62,75,248,263,271,273,290,323,331,333,345,352,360,367,375,404,410,423`, i.e. every listed category that has tamper).
- **Legacy/compat branch**: if none of Boolean/Bitmap match, entity is still created when `dpcode in device.function or in device.status or in device.status_range` (`H/definition/binary_sensor.py:56-63`) — i.e. **existence in `status` (a live value) suffices, no type check**, unlike other platforms.
No inverted binary sensor exists in core (no `DPCodeInvertedBooleanWrapper` use here).

### 3.3 Availability / conditional creation
Created iff (bitmap: Bitmap DP & label present) | (Boolean DP found via status_range-then-function) | (legacy: dp appears in function/status/status_range). `bitmap_key` set but DP not Bitmap or label absent -> None, *no fallback to boolean/in-set* (`:44-48`). Unknown/None -> `unknown`. `device.online` availability.

### 3.4 Write
None.

### 3.5 Data vs code
All data: `(category, key, dpcode, on_value scalar|set, bitmap_key, device_class, entity_category, translation_key, name)`. Runtime rule: 4-way reader selection (bitmap / boolean / in-set) plus existence-only fallback.

---------------------------------------------------------------------------------------------------

## 4. NUMBER (`C/number.py`, 655 lines)

### 4.1 Description shape
`TuyaNumberEntityDescription(TuyaEntityDescription, NumberEntityDescription)` adds **no fields** (`C/number.py:31-33`). DP = `description.key` (`:545`). Wrapper = `DPCodeIntegerWrapper.find_dpcode(device, key, prefer_function=True)` (`H/definition/number.py:31-39`). Number `min/max/step` are **not** in descriptions; they come from the DP's Integer metadata (`C/number.py:571-573`; `H/device_wrapper/common.py:171-182`).

Table `NUMBERS` (`:40-521`): 28 categories, **73 descriptions**, alias `NUMBERS[DGHSXJ]=NUMBERS[SP]` (`:524`) => 29 keys.
Categories: BH,BZYD,CO2BJ,CWWSQ,CZ,DGNBJ,FS,HPS,JSQ,KFJ,MAL,MSP,MZJ,QCCDZ,SWTZ,SD,SFKZQ,SGBJ,SP,SZJQR,TGKG,TGQ,WG2,WK,XNYJCN,YWCGQ,ZD,ZNRB.

Field frequency (73):
| field | count | notes |
|---|---|---|
| key | 73 | |
| translation_key | 73 | all have one |
| entity_category | 65 | always CONFIG (8 without: `CWWSQ MANUAL_FEED/VOICE_TIMES`, `FS TEMP`, `HPS TARGET_DIS_CLOSEST`, `JSQ TEMP_SET/_F`, `QCCDZ CHARGE_CUR_SET`, `ZNRB TEMP_SET`) |
| device_class | 35 | DURATION 17, TEMPERATURE 9, DISTANCE 5, POWER 3, CURRENT 1 |
| translation_placeholders | 21 | `{"index": N}` (SFKZQ countdown 1-8 `:265-321`, TGKG/TGQ min/max brightness, CZ warn power 1/2, SWTZ cook temp 2) |
| native_unit_of_measurement | 4 | `%` 2 (SZJQR `:359,:365`), seconds 1 (CO2BJ alarm_time `:80`), minutes 1 (MZJ cook_time `:224`) — fallback only (see S-UNIT analogue) |
Not used: `mode`, `native_min/max/step`, `icon`, `entity_registry_enabled_default`.

### 4.2 Patterns
- **N-PLAIN**: Integer DP -> slider/box; range/step from DP metadata.
- **N-ALT-KEYS-SAME-CONCEPT**: temperature setpoint `TEMP_SET` + `TEMP_SET_F` (BH `:42-53`, JSQ `:154-164`), `TEMP_BOILING_C/_F` (BH `:54-65`); both created if both DPs exist.
- **N-INDEXED**: `COUNTDOWN_1..8` etc.
- Unit reconciliation: same algorithm as sensors, using NumberDeviceClass allowed units (`C/number.py:577-629`); TEMP_UNIT_CONVERT fallback (`:591-597`); alias table; fallback to description unit; else drop device class (`:622-629`). Tests: `T/test_number.py:188-251` (znrb `temp_set` state `28.0`/`82.4`/`-2.2`; invalid `temp_unit_convert="k"` -> device_class None, unit `""`).
- The `sfkzq` `COUNTDOWN` comment: "Controls the irrigation duration for indexed water valves" (`:263-270`).

### 4.3 Read / conditional creation
Created iff the DP is an Integer in `function` (preferred) or `status_range` (`H/definition/number.py`). Value = scaled float or None (invalid/out-of-range -> None + warning). Min/max/step attributes are taken from whichever dict supplied the metadata first — i.e. from `function` (write range) if present, else `status_range`. `device.online` availability.

### 4.4 Write
`set_native_value(v)` -> `[{"code": key, "value": round(v * 10**scale)}]` after checking `min <= raw <= max` (`H/type_information.py:317-329`); step not checked; no unit conversion (HA converts displayed unit for temperature classes before calling, **[unverified]** because HA core `number` isn't in the download, but test `T/test_number.py:101-128` shows `18 -> {"code":"delay_set","value":18}` with scale 0). No rounding to step.

### 4.5 Data vs code
Data: key, translation, category, device_class, index placeholder, fallback unit. Everything else is metadata-driven.

---------------------------------------------------------------------------------------------------

## 5. SELECT (`C/select.py`, 461 lines)

### 5.1 Description shape
`TuyaSelectEntityDescription(TuyaEntityDescription, SelectEntityDescription)` — **no added fields** (`C/select.py:23-26`). DP = `description.key`; wrapper = `DPCodeEnumWrapper.find_dpcode(device, key, prefer_function=True)` (`H/definition/select.py:31-39`). `options` are taken straight from the DP's Enum `range` (`C/select.py:435`; `H/device_wrapper/common.py:148-161`). **No option remapping, no option filtering, no label maps in code.**

Table `SELECTS` (`:30-380`): 25 categories, **59 descriptions**, aliases `CZ<-KG` (`:383`), `DGHSXJ<-SP` (`:386`), `PC<-KG` (`:389`) => 28 keys.
Categories: BH,CL,CO2BJ,CS,CWJWQ,DGNBJ,DR,FS,JSQ,KFJ,KG,KJ,QCCDZ,QN,SD,SFKZQ,SGBJ,SJZ,SP,SZJQR,TDQ,TGKG,TGQ,XNYJCN,ZNJDQ.

Field frequency (59): key 59; translation_key 59; entity_category 52 (all CONFIG); translation_placeholders 7 (`{"index": N}` — DR blanket levels, TGKG/TGQ led type); icon 3 (`mdi:thermometer-lines`, DR `:89-105`). Not used: device_class, options, `entity_registry_enabled_default`.

### 5.2 Patterns
- **SEL-PLAIN** only. Displayed labels are provided by HA translation files: `C/strings.json entity.select.<translation_key>.state.<raw_enum_value>` — 33 of 41 select translation keys have `state` maps (list in section 10). The mapping *raw enum value -> UI label* is thus outside of both core .py and handlers.
- **SEL-ALT-KEYS**: `COUNTDOWN` and `COUNTDOWN_SET` both mapped to `translation_key='countdown'` (FS `:118-127`, JSQ `:145-154`, KJ `:189-198`, CS `:63`) — DP name varies across products, first/second/both created.
- Quirks extend enum ranges (e.g. `fs_xwv3jifdbhbolgh3.py` widens `mode` and `countdown_set` ranges via `add_dpid_enum`), demonstrating that select options are entirely device-metadata driven.

### 5.3 Read / creation / write
- Created iff Enum DP found (function first, then status_range). `current_option` = raw enum str if in range else None(+warning).
- Write: `select_option(o)` -> `[{"code": key, "value": o}]`; `o` must be `str` and `in range` (`H/type_information.py:243-251`). HA's `SelectEntity` rejects unknown options before that with translation_key `not_valid_option` (`T/test_select.py:126-152`).

### 5.4 Data vs code
100% data + DP-metadata dependency.

---------------------------------------------------------------------------------------------------

## 6. BUTTON (`C/button.py`, 136 lines)

- Description: `TuyaButtonEntityDescription` no added fields (`:27-29`). DP = key; wrapper = `DPCodeBooleanWrapper.find_dpcode(..., prefer_function=True)` (`H/definition/button.py:31-39`).
- Table `BUTTONS` (`:32-86`): 4 categories (HXD, MSP, SD, SP), **9 descriptions**: `HXD SWITCH_USB6 (snooze) :35`, `MSP FACTORY_RESET :41` (`entity_category=DIAGNOSTIC`, `entity_registry_enabled_default=False`), `MSP MANUAL_CLEAN :47`, `SD RESET_DUSTER_CLOTH/EDGE_BRUSH/FILTER/MAP/ROLL_BRUSH :54-74`, `SP DEVICE_RESTART :81` (`device_class=RESTART`, no translation_key).
- Fields: key 9, translation_key 8, entity_category 8 (CONFIG 7, DIAGNOSTIC 1), entity_registry_enabled_default 1 (False), device_class 1.
- Write: `press` -> `[{"code": key, "value": True}]` (`C/button.py:133-136`; `T/test_button.py:45-67`). Stateless: no read/state, `skip_update` irrelevant.
- Created iff Boolean DP found. Note the dp is a Boolean *momentary* trigger; nothing resets it.

---------------------------------------------------------------------------------------------------

## 7. EVENT (`C/event.py`, 185 lines)

### 7.1 Description shape
`TuyaEventEntityDescription(TuyaEntityDescription, EventEntityDescription)` adds `wrapper_class: type[DPCodeTypeInformationWrapper] = SimpleEventEnumWrapper` (`C/event.py:32-36`) (single class, not tuple). DP = `description.key` (`:135-137`).
Table `EVENTS` (`:42-113`): 2 categories, **11 descriptions**.
- `WXKG` (wireless switch): `SWITCH_MODE1..9` (`:57-112`) each `device_class=EventDeviceClass.BUTTON`, `translation_key="numbered_button"`, `translation_placeholders={"button_number": "N"}`; default wrapper.
- `SP`: `ALARM_MESSAGE` (`translation_key doorbell_message`, `wrapper_class=Base64Utf8StringEventWrapper` `:46-50`) and `DOORBELL_PIC` (`doorbell_picture`, `Base64Utf8RawEventWrapper` `:51-55`). Comment `:44-45`: neither means "doorbell rung", so no DOORBELL device class.
Field frequency (11): key 11, translation_key 11, device_class 9, translation_placeholders 9, wrapper_class 2.

### 7.2 Wrappers (`H/device_wrapper/event.py`)
- `SimpleEventEnumWrapper` (`:11-23`): Enum DP; event type = the enum value; attributes None; `event_types` = the DP's enum `range`. (HA `EventEntity` event_types are the *device-declared enum members*; snapshot for wxkg has `click`, `press` — `T/fixtures/wxkg_l8yaz4um5b3pwyvf.json`; strings.json translates `click/double_click/press` `C/strings.json entity.event.numbered_button.state_attributes.event_type.state`.)
- `Base64Utf8StringEventWrapper` (`:26-48`): **String** DP whose value is base64 of UTF-8 text; fires event type `"triggered"` with attribute `{"message": b64decode(v).decode("utf-8")}`; `options=["triggered"]`. No error handling for invalid base64/UTF-8 (raises).
- `Base64Utf8RawEventWrapper` (`:51-68`): **Raw** DP (already b64-decoded by `RawTypeInformation`), fires `"triggered"` with `{"message": bytes.decode("utf-8")}`. In test `doorbell_pic` decodes to a URL (`T/test_event.py:69-96`).

### 7.3 Behavior
`TuyaEventEntity._process_device_update` (`C/event.py:167-185`): if wrapper `skip_update` (DP not in `updated_status_properties`) or read returns falsy -> no state write; else `self._trigger_event(type, attrs)` then write. => **events fire only on update messages that include the DP; identical repeated values still fire**; initial/cached status does not fire (state starts `unknown`; on online/offline transition state is restored without a new event — test `T/test_event.py:98-144`). No read-on-demand. The event platform's `available`= `device.online` (`:44`).
Creation: needs matching DP type (Enum for simple, String for base64 string, Raw for base64 raw) via `wrapper_class.find_dpcode`.

### 7.4 Data vs code
Data: (category, key, translation_key, device_class, placeholders, wrapper enum: {enum, b64_string, b64_raw}). Semantics "fire on report" is a platform-level rule, not per description.

---------------------------------------------------------------------------------------------------

## 8. CAMERA (`C/camera.py`, 137 lines)

- Description `TuyaCameraEntityDescription(TuyaEntityDescription, CameraEntityDescription)` no fields (`:27-29`); `CAMERAS` is **not a tuple table** but `dict[DeviceCategory, Description]` with 2 entries `DGHSXJ` and `SP`, both `key=""` (`:32-35`). => unique_id `tuya.{device_id}` (empty key). Always created for these categories (no DP requirement) (`:46-57`), unlike other platforms; `get_default_definition(device)` always returns a definition (`H/definition/camera.py:32-41`).
- Definition: two *optional* wrappers: `motion_detection_switch = DPCodeBooleanWrapper.find_dpcode(device,"motion_switch", prefer_function=True)`, `recording_status = DPCodeBooleanWrapper.find_dpcode(device,"record_switch")`.
- Entity (`C/camera.py:68-137`): `supported_features=STREAM`, `brand="Tuya"`, `name=None`, `model=device.product_name`. `is_recording` = `record_switch` value or False; `motion_detection_enabled` = `motion_switch` value or False; `enable/disable_motion_detection` writes `[{"code":"motion_switch","value":True/False}]` (`test_camera.py:59-100`). No `_process_device_update` override => every update re-writes state.
- **Stream**: `stream_source` -> `manager.get_device_stream_allocate(device.id, "rtsp")` via executor (`:105-112`) — **Tuya cloud API call**, returns an RTSP URL. `async_camera_image` = `ffmpeg.async_get_image(hass, stream_source, width, height)` (`:114-127`). Manifest depends on `ffmpeg` (`C/manifest.json`). Not reproducible over local MQTT; would require a different source (local RTSP / bridge).
- Note the SP category also gets switches (`motion_switch` as `motion_alarm` switch `C/switch.py:722`, `record_switch` as `video_recording` `:687`), numbers, selects, sensors, buttons, events (all keyed `SP`, copied to `DGHSXJ`).

---------------------------------------------------------------------------------------------------

## 9. Feeder schedule service (`H/device_wrapper/service_feeder_schedule.py`, used by `C/services.py`)

Not an entity; a `DeviceWrapper[list[FeederSchedule]]` accessed via the quirk registry: `get_feeder_schedule_wrapper(device)` -> `TUYA_QUIRKS_REGISTRY.get_quirk_for_device(device).get_feeder_schedules_wrapper(device)` (`:72-85`). So availability is product_id-specific (only `cwwsq_wfkzyy0evslzsmoi` maps it: `lambda device: DefaultFeederScheduleWrapper.find_dpcode(device,"meal_plan",prefer_function=True)`).
- Services: `tuya.get_feeder_meal_plan` (response only) and `tuya.set_feeder_meal_plan(device_id, meal_plan[])` (`C/services.py:35-36,85-160`). Entry schema: `days: list[str]`, `time: "hh:mm"`, `portion: int`, `enabled: bool` (`C/services.py:25-27`, `H/...:15-25`).
- Codec (Raw DP `meal_plan`, base64 of hex string chunks): each entry is 5 fields x 2 hex chars = 5 bytes: `days` (bitmask, bit0=Mon..bit6=Sun; `_DaysOfWeek` `:91-100`; day mapping identity `(i,i)` `:41`), `hour`, `minute`, `portion`, `enabled` (`:33-46`, `:195-245`). Decode: b64 -> bytes -> lowercase hex -> chunk by 10 chars -> parse. Encode: inverse, then `base64.b64encode(...).decode()` (`:60-70`). Write command shape: `[{"code":"meal_plan","value":"<base64 str>"}]`.
- Requires bespoke codec code: a "record list in raw bytes" layout description (field widths, bit-mask <-> day names, hour/minute split). If in scope for IL: fixed-width hex record codec (`template=[(name,width)...]`, transformer for days) is expressible as data + 3 named transforms.

---------------------------------------------------------------------------------------------------

## 10. Frequency tables and IL-relevant summary

### 10.1 Totals
| platform | categories (+aliases) | literal descs | expanded descs | lookup by | wrapper family |
|---|---|---|---|---|---|
| switch | 51 (+2) | 157 | 157 | key | Boolean |
| sensor | 61 (+2) | 250 | 450 | dpcode or key | Integer / Delta / Enum / wind / electricity Raw|Json |
| binary_sensor | 28 (+0) | 56 | 73 | dpcode or key | Boolean / BitmapBit / InSet |
| number | 28 (+1) | 73 | 73 | key | Integer |
| select | 25 (+3) | 59 | 59 | key | Enum |
| button | 4 | 9 | 9 | key | Boolean |
| event | 2 | 11 | 11 | key | EnumEvent / B64-String / B64-Raw |
| camera | 2 (dict, single desc) | 2 | 2 | (none) | Boolean x2 optional |
| **total** | | **617** | **834** | | |
Distinct `DPCode` keys across the seven table-driven platforms: 340 (switch 71, sensor 136, binary_sensor 28, number 55, select 36, button 9, event 11; overlaps between platforms).
Snapshot entity counts under all fixtures (each entity has -entry and -state): switch 257, sensor 532, binary_sensor 57, number 62, select 118, button 12, event 14, camera 9 (`grep -c "test_platform_setup_and_discovery\["` in `T/snapshots/*.ambr` halved). Fixture count: 324 device JSONs.

### 10.2 EntityDescription field usage (all 8 platforms; count = literals using it)
| field | switch | sensor | binary | number | select | button | event | camera | comment |
|---|---|---|---|---|---|---|---|---|---|
| key | 157 | 250 | 56 | 73 | 59 | 9 | 11 | 2 | 100%; also unique_id suffix |
| translation_key | 149 | 246 | 25 | 73 | 59 | 8 | 11 | 0 | HA i18n key; name text lives in strings.json |
| translation_placeholders | 51 | 21 | 0 | 21 | 7 | 0 | 9 | 0 | only `{"index":N}` / `{"button_number":N}` |
| entity_category | 77 CONFIG | 24 DIAG | 18 DIAG | 65 CONFIG | 52 CONFIG | 8 (7C,1D) | 0 | 0 | |
| device_class | 32 | 200 | 50 | 35 | 0 | 1 | 9 | 0 | enum from HA per platform |
| state_class | - | 229 | - | - | - | - | - | - | MEASUREMENT 196 / TOTAL_INCREASING 33 |
| suggested_unit_of_measurement | - | 49 | - | - | - | - | - | - | |
| native_unit_of_measurement | - | 8 | - | 4 | - | - | - | - | fallback semantics only |
| suggested_display_precision | - | 1 | - | - | - | - | - | - | |
| entity_registry_enabled_default | 0 | 14 (=False) | 0 | 0 | 0 | 1 (=False) | 0 | 0 | |
| icon | 14 | 0 | 0 | 0 | 3 | 0 | 0 | 0 | |
| name | 8 (2 None, 6 str) | 3 (2 None, 1 str) | 1 str | 0 | 0 | 0 | 0 | 0 (attr `_attr_name=None`) | |
| dpcode (alias of key) | - | 7 | 17 | - | - | - | - | - | |
| wrapper_class | - | 7 | - | - | - | - | 2 | - | |
| on_value | - | - | 34 | - | - | - | - | - | |
| bitmap_key | - | - | 14 | - | - | - | - | - | |
Never used anywhere in slice: `entity_registry_visible_default`, `options` (description), `mode`, `native_min/max/step` (description), `last_reset`, `unit_of_measurement` conversions, `force_update`, `has_entity_name` override.

### 10.3 Pattern catalog (need first-class IL support vs. leave as code)
| pattern | where | count | recommendation |
|---|---|---|---|
| plain dp -> bool switch (write True/False) | switch | 157 | data |
| plain scaled integer sensor/number (scale from DP meta) | sensor/number | ~220 sensors + 73 numbers | data + DP-type metadata (min,max,scale,step,unit) |
| enum sensor (auto ENUM class, options=DP range) | sensor | ~20 literal (+ battery_state) | data + type-dispatch |
| enum select (options=DP range) | select | 59 | data |
| button (momentary Boolean write True) | button | 9 | data |
| bool binary sensor | binary | 22 | data |
| in-set binary sensor (string/int/enum compare) | binary | 34 | data (`on_value` scalar or set) |
| bitmap-bit binary sensor | binary | 14 | data (`bitmap_key` + label list) |
| multi-entity from one DP (enum members / bitmap labels) | binary | 3 shock + 14 bitmap | data (fan-out by value/label) |
| shared tuples / category aliases | all | BATTERY x35, TAMPER x17, aliases 9 | data-level include/alias |
| alternate DP names for same concept (create all present) | sensor/number/select | many | data; needs "create all present" semantics |
| `entity_registry_enabled_default=False` | sensor 14, button 1 | 15 | data flag |
| electricity attribute extraction (raw v1/v2/legacy blob; json dict) | sensor | 36 expanded | code (named reader) or codec spec |
| wind enum -> degrees table | sensor | 1 | data table reader |
| delta accumulator (`report_type=="sum"`) | sensor | dynamic (2 quirk products + fixture) | code + needs timestamps + state |
| event enum / b64-string / b64-raw | event | 11 | named reader enum |
| unit reconciliation & alias table & temp_unit_convert | sensor/number | all numeric | data table (`UNITS` 35 rows) + small algorithm |
| camera: optional switch DPs + cloud RTSP stream | camera | 2 | out of scope / special |
| feeder schedule codec | service | 1 product | optional bespoke |
| quirk `apply_when` predicate | quirks | 5 entries in 1 quirk (kt_hw50w7qvxluhslkk) | code hook or declarative "when dp X >= N" |

### 10.4 Complete list of HA i18n state maps needed (device value -> label), for completeness
Select `state` maps exist for: basic_anti_flicker, basic_nightvision, blanket_level, charger_work_mode, countdown, curtain_mode, curtain_motor_mode, decibel_sensitivity, desk_level, desk_up_down, fingerbot_mode, horizontal_fan_angle, humidifier_level, humidifier_moodlighting, humidifier_spray_mode, indexed_blanket_level, indexed_led_type, inverter_work_mode, ipc_work_mode, kettle_work_mode, led_type, light_mode, motion_sensitivity, odor_elimination_mode, quick_heat_temperature, record_mode, relay_status, siren_mode, vacuum_cistern, vacuum_collection, vacuum_mode, vertical_fan_angle, weather_delay. Sensor `state` maps: air_quality, air_quality_index, cat_litter_box_status, charger_status, indexed_meter_status, irrigation_status, liquid_state, odor_elimination_status, sous_vide_status, water_level_state. Event: numbered_button event_type click/double_click/press. These sit in `C/strings.json`, not in Python; entity display names for all 8 platforms also come from `strings.json` `entity.<platform>.<translation_key>.name`.

---------------------------------------------------------------------------------------------------

## 11. Requirements for the IL (with evidence)

R1. **Two-level knowledge model.** (a) Category-keyed entity tables; (b) product_id-keyed DP-metadata patches with optional predicate. Evidence: `C/*.py` tables keyed by `device.category`; `H/registry.py`/`builder/device_quirk.py`. The IL must represent both, and allow a category table to alias/include another (9 alias assignments, 2 shared tuples).

R2. **Entity identity = (device_id, key)** with `key` a free string that may differ from the dpcode (computed keys `f"{dpcode}electriccurrent"`, `f"{FAULT}_{label}"`, `f"{SHOCK_STATE}_{value}"`, `f"{TOTAL_POWER}power"`, empty string for camera). Need explicit `key` and separate `dpcode` (C/entity.py:35; C/sensor.py:70,81,1673-1675; C/binary_sensor.py:78-86). Registry-continuity with existing HA tuya installs may matter — **open Q**.

R3. **DP addressing by code plus type metadata.** Every wrapper first resolves `(dpcode, declared type, range/scale/unit/labels)` from device-provided metadata; entity existence depends on it (H/type_information.py:66-118). The IL needs a DP schema (`code`, `type in {bool,enum,int,bitmap,json,raw,string}`, `min,max,scale,step,unit`, `range[]`, `label[]`, `report_type`, `readable`, `writable`) and an explicit `dpcode -> dp id` map for the local protocol (cloud API is code-based; local bridge is id-based). Quirks show that unknown DPs are added with dp id (`add_dpid_*`) — IL should treat "dp id + code + type" as one declaration.

R4. **Readable vs writable distinction & lookup preference.** `prefer_function=True` (switch/number/select/button/motion_switch) vs default status-first (sensor/binary/event/record_switch). No platform enforces writability. IL should record per-DP `readable`/`writable` and per-entity lookup preference, or normalize away (note that min/max for numbers may differ between function and status_range dicts) — **open Q**.

R5. **Conditional creation as first-class semantics**: entity is emitted iff its DP(s) exist with expected type (and label/attribute present); silently omitted otherwise. Includes: type match, bitmap label presence, parsed-attribute presence (electricity), existence-only fallback for binary_sensor (function/status/status_range with no type check, H/definition/binary_sensor.py:56-63), sensors "try wrapper list in order". IL needs `requires` predicates: `{dp_type, has_label, has_json_attribute, has_parsed_field}`.

R6. **Value model for reads**: bool, scaled number (`raw/10^scale`, validated against min..max, must be `int`), enum-in-range str, bitmap bit, in-set(scalar|set) compare, json-attribute extractor, raw-blob field extractor, enum-to-number lookup (wind), delta accumulator, base64 text decode. Invalid values => None/unknown, not error.

R7. **Write model**: only single-command `[{"code": dpcode, "value": raw}]`: bool (switch on/off, button True), int (round(v*10^scale), range-check only), str enum (must be in range). IL must express `write = {dp, encode: bool|scaled_int|enum|const(true)}` and validation rules; and keep a command shape convertible to (dp id, value) for the local bridge.

R8. **Stateful sensors**: `DeltaIntegerWrapper` needs per-entity accumulator, per-DP timestamp from transport, `skip_update` semantics. IL needs a `delta_accumulate` reader and an input event carrying `dp_timestamps`. If the bridge has no per-DP timestamps, this pattern cannot work as implemented (only ordering/dedup via timestamp equality) — **open Q**. Also state is not persisted across restarts (starts at 0).

R9. **Event semantics**: events fire only on *update messages that contain the DP*, not on state polls/initial status; must trigger even when value repeats. IL needs an `event` entity with an "on-report" trigger and `event_types` (from enum range, or fixed `["triggered"]`), with optional attribute extraction (`message` from base64 string/raw).

R10. **Update-filter semantics**: entity refreshes only when its DP is in the update set (`skip_update`), and always on online/offline transitions (`updated_status_properties is None`). IL should model per-entity dependency sets (`depends_on: [dpcodes]`), incl. composite sensors (dependency = source DP).

R11. **Unit/device-class reconciliation** (sensor/number): needs the alias table (`C/const.py:1034-1229`, ~35 alias rows: canonicalization only, no value conversion), the temperature `temp_unit_convert` fallback (`C/util.py`), the "fallback to description unit" and "drop device_class" branches. Tests lock these behaviors (`T/test_sensor.py:236-326`, `T/test_number.py:188-251`). Make this a documented algorithm in the IL spec + data table, not per-entity code. Also `suggested_unit_of_measurement` (49) as data and `native_unit` semantic as "fallback".

R12. **Composite/decoder registry** (raw-blob and JSON attribute extractors): specify the `ElectricityData` layout (v1/v2/legacy, sign bitmap, scales) as a named codec `electricity_v1v2` with 6 output fields, plus JSON codec with 6 attribute names & units, plus (unused by core but shipped) hex-string form. Presence-of-field rule: legacy blobs lack reactive/apparent/pf (H/raw_data_model.py:44-100).

R13. **Fan-out entities**: one DP -> N entities by enum value (`on_value` per description: ZD `shock_state`) or bitmap label (CS fault, 12 entities). IL entity table should allow multiple entities per DP with distinct key/on_value/bitmap_key.

R14. **Alternate/duplicate DP names**: many tables list several DP codes for the same concept (temp `_F`, `VA_`, `_VALUE`, `COUNTDOWN`/`COUNTDOWN_SET`); all present ones become entities. No priority/dedup rule exists — reproducing HA behavior requires exactly this "create for each present".

R15. **Metadata attributes (pure data)**: `translation_key`, `translation_placeholders`, `entity_category`, `device_class`, `state_class`, `icon`, `name` (None => device name; string literal), `entity_registry_enabled_default`. Names ultimately come from `strings.json`; an IL consumer without HA i18n needs fallback English names — **open Q** (translation content is outside code).

R16. **Availability**: single rule `device.online` (C/entity.py:42-46); value None -> `unknown`. IL needs device-level availability signal (from bridge) but no per-DP availability.

R17. **Camera**: entity always created for `sp`/`dghsxj`; two optional boolean DPs (`motion_switch` write+read, `record_switch` read); stream is a cloud RTSP allocation (`get_device_stream_allocate`) — IL should model camera as `{motion_detection_dp?, recording_dp?, stream: <provider>}` with stream provider out-of-band.

R18. **Quirk predicates**: `apply_when` (device -> bool) is code; used once for a status-value heuristic. IL may want a tiny predicate language (`status[dp] >= N`) or keep as an extension point.

R19. **Data sizes**: ~834 expanded descriptions, ~340 distinct DP codes; description records are tiny, so data-driven encoding (JSON/TOML) is practical; the code surface is ~15 named readers.

---------------------------------------------------------------------------------------------------

## 12. Open questions / things not verified

1. HA-core internals not in the download (`homeassistant/components/{sensor,number,select,...}/__init__.py`): the exact precedence of `_attr_native_unit_of_measurement` vs `entity_description.native_unit_of_measurement`, HA's numeric unit conversion for `suggested_unit_of_measurement`, and allowed-unit sets per device class (`SENSOR_DEVICE_CLASS_UNITS`, `NUMBER_DEVICE_CLASS_UNITS`). Behavior inferred only from tests/snapshots.
2. WIND_DIRECTION class dropped in snapshot: root cause inferred (wrapper has no unit), not proven against HA source.
3. Whether `DPCodeInvertedBooleanWrapper`, `DPCodeRoundedIntegerWrapper`, `DPCodePercentageWrapper`, `Electricity*HexStringWrapper` are used by other platforms (cover/light/fan/etc.) — outside my slice; in my 8 platforms they are unused.
4. Quirk `*Quirk.definition_fn` classes: defined for every platform but unused in 0.0.29 — planned extension point? Not confirmed. If a future version uses them, per-device entity definitions (data + code) would appear.
5. Bridge transport: does it deliver per-DP timestamps (needed by delta sensors), does it deliver dp *type/range* metadata (needed by `find_dpcode`), and how does it differentiate `function` vs `status_range`? Cloud device JSON provides all of these; local protocol normally provides only `dps` id->value.
6. How to map dp *codes* (used by every description) to dp *ids*: not in core; cloud provides `local_strategy[dpid] = {status_code, config_item{valueDesc,valueType,enumMappingMap,statusFormat,pid}, value_convert}` (`H/builder/device_quirk.py:53-82,166-178`). Local-strategy `value_convert=="enum"` + `enumMappingMap` is a value mapping layer applied *below* these wrappers (used by 2 quirks via `set_dpid_strategy_to_enum`); its exact semantics are in the tuya_sharing SDK (not downloaded).
7. Whether IL must preserve HA `unique_id` format `tuya.{device_id}{key}` for migration.
8. Interaction of `entity_category`/`enabled_default` with a non-HA consumer (IL may only need booleans for "hidden by default").
9. Translation catalogs (names/state labels) are needed to render entities; they live in `C/strings.json` (119 sensor, 60 switch, 44 number, 41 select, 23 binary_sensor, 8 button, 3 event translation entries) and are not part of `tuya-device-handlers`.
10. An ENUM-promoted sensor whose description also has `state_class` (numeric intent) — resulting HA behavior unverified.
