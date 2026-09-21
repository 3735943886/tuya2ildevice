# Catalog C: light, climate, fan, humidifier (HA core `tuya` + `tuya-device-handlers` 0.0.29)

Path abbreviations used for every citation below:

- `CORE/` = `core/homeassistant/components/tuya/`
- `HDL/` = `tuya_device_handlers/` (package root of 0.0.29, under `src/`)
- `TESTS/` = `core/tests/components/tuya/` (snapshots in `TESTS/snapshots/`, fixtures in `TESTS/fixtures/*.json`)
- `light.py:N` means `CORE/light.py` line N. `HDL/definition/light.py:N` is written in full where it is not obvious.

Line numbers for `HDL/device_wrapper/light.py`, `HDL/device_wrapper/fan.py` and `HDL/definition/humidifier.py` were derived from a concatenated `cat -n` and re-based to per-file numbers (accurate to about +/-1).

---------------------------------------------------------------------------
## 0. Shared machinery (applies to all four platforms)

### 0.1 Architecture in 0.0.29
- Core owns only two things per platform: (a) a **category -> description table** (which entity/entities per `device.category`) and (b) a thin HA entity class. All DP reading, enum/range parsing, remapping and command building lives in `tuya_device_handlers` "wrappers" (`DeviceWrapper` subclasses) assembled by a `get_default_definition(device, ...)` function per platform.
- `*Quirk` dataclasses with `definition_fn` exist in `HDL/definition/{light,fan,...}.py` (e.g. `HDL/definition/light.py:43-50`, `fan.py:34-41`, `climate.py:48-55`, `humidifier.py:215-222`) but **nothing in core or handlers 0.0.29 uses them** (grep for `definition_fn|LightQuirk|ClimateQuirk|FanQuirk|HumidifierQuirk` hits only the class definitions). So per-device definition overrides do not exist yet; the only per-device customisation is the DP-metadata quirk (0.3).
- Entities are `should_poll = False`, `has_entity_name = True` (`CORE/entity.py:24-25`). `unique_id = f"tuya.{device.id}{description.key}"` (`entity.py:35`). Climate/fan use `key=""` so unique_id is `tuya.<id>`; humidifier uses `key=DPCode.SWITCH` so unique_id is `tuya.<id>switch` even if the switch dpcode found is `switch_spray` (`humidifier.py:43,50`). Light unique_id suffix is the switch dpcode key (e.g. `tuya.<id>switch_led`, `tuya.<id>light`; snapshot `TESTS/snapshots/test_light.ambr:42,1695`).
- `available` = `device.online` (`entity.py:42-46`). Nothing else.

### 0.2 DP lookup semantics (`find_dpcode`)
`HDL/type_information.py:66-118`:
- Input `dpcodes`: `None` -> not found; a str or an **ordered tuple** (first dpcode in the tuple that yields a match wins; the outer loop is over dpcodes, inner over the two spec dicts).
- For each dpcode it looks in `(device.function, device.status_range)` if `prefer_function=True`, else `(device.status_range, device.function)`. The definition must have a `type` that parses (`DPType.try_parse`) to the wrapper's expected type (Boolean/Enum/Integer/Json/String/Bitmap/Raw) and `_from_json` must succeed. Note this means a DP with the wrong declared type is silently "not found".
- `DPType.try_parse` also tolerates lowercase/legacy type names: `bitmap,bool,enum,json,raw,string,value` (`HDL/const.py:44-63`).
- A quirk can replace the TypeInformation class per dpcode (`get_type_information_cls`, `HDL/builder/device_quirk.py:481-488`; used in `type_information.py:86-98`). Only in-repo class is `InvertedIntegerTypeInformationEx` (`HDL/type_information_ex.py:16-47`), not used by any of my four platforms' quirks (0.3).
- Enum: `range` list required, `_from_json` returns None if `values` JSON falsy (`type_information.py:224-241`). Integer requires keys min,max,scale,step, optional unit (`:297-315`).

### 0.3 Read validation (all "value" reads)
All reads go through `TypeInformation.read_device_value` (`HDL/type_information.py`):
- Boolean: only `True/False` accepted (`raw in (True, False)`, so `1`/`0` also pass, but strings `"true"` do not -> None + one-time warning) (`:194-213`).
- Enum: value must be in `range` else None (+ warn once) (`:253-274`). This means an out-of-range/typed-mismatched status (e.g. int `1` vs range `["1","2"]`) yields None.
- Integer: must be `int` (not float) and `min<=v<=max`, returned **scaled** `v/10**scale`; out-of-range -> None (`:331-353`).
- Json: `json.loads(status)`; error -> None (`:362-381`). String: raw pass-through (`:416-418`). Note: JSON DP status in the *fixtures* is already a dict (e.g. `dj_mki13ie507rlry4r` `colour_data_v2` = `{"h":243,...}`) but production `device.status` is a string; the handlers call `json.loads` (an implementation concern for the IL: decode JSON strings).
- Write validation (`prepare_set_value`): Boolean must be a real `bool`; Enum must be a `str` and in `range`; Integer must be int/float, converted with `round(value*10**scale)` and must lie in `[min,max]` (raw), else `SetValueOutOfRangeError` (`type_information.py:187-192,243-251,317-329`; `common.py:122-129`; `exception.py`). No clamping, only rejection.

### 0.4 Command format
`DPCodeWrapper.get_update_commands` returns `[{"code": <dpcode>, "value": <raw>}]` (`HDL/device_wrapper/common.py:70-82`). Commands are by **dpcode name**, not dp id. Core sends a whole list in one call: `manager.send_commands(device.id, commands)` executed in an executor (`CORE/entity.py:92-99`). Empty list -> nothing sent. Order in list = order of the commands.

### 0.5 Runtime state update behaviour
- On any device update, `TuyaEntity._handle_state_update` calls `async_write_ha_state()` when `updated_status_properties is None` (online/offline notice) or when `_process_device_update` returns True (`entity.py:59-78`). **None of light/climate/fan/humidifier override `_process_device_update`** (grep: only binary_sensor, event, sensor, siren do), so they re-write state for every update of the device, regardless of which dpcode changed. `DeviceWrapper.skip_update` / `dp_timestamps` are unused for these four platforms (they are only consumed by binary_sensor/event/sensor/siren).
- No optimistic state, no debounce, no polling, no command echo handling in these four platforms: after a service call the entity value changes only when the next status update arrives from the device (`entity.py` has no optimistic hooks). All properties are pure functions of `device.status` (+ `device.status_range`/`function`, + `hass.config.units` for climate).
- `DeviceWrapper.initialize()` hook exists (`HDL/device_wrapper/base.py:20-25`) but is not called by these platforms' entities.

### 0.6 Quirks (DP metadata patches) relevant to these four platforms
Quirks are keyed by `product_id` (`HDL/registry.py:74-86`) and applied at device registration (`CORE/coordinator.py:146-155` -> `initialise_device`), i.e. **before** platform discovery. They mutate `device.function/status_range/status/local_strategy/category` (`HDL/builder/device_quirk.py:230-245`). Entry kinds: add DP (enum/integer/boolean/bitmap) with READ and/or WRITE mode (`:130-187`), remove DP (`:96-105`), map initial status values (`:108-127`), local-strategy overrides (only when `device.support_local`) (`:52-93`), override category (`:265-268`), override type-information class (`:397-406`), and an optional `apply_when(device)` predicate per entry (`:35-45`) - i.e. **quirks can contain arbitrary Python predicates** (see fahrenheit detection below).

Relevant quirk files under `HDL/devices/`:
| product_id | file | category | effect on these platforms |
|---|---|---|---|
| dune79w7bsu6dg3e (Duux Whisper Flex) | `fs/fs_dune79w7bsu6dg3e.py:11-40` | fs | initial status of `switch_horizontal`(dp4)/`switch_vertical`(dp5) `"false"/"true"` -> `False/True`; local strategy enum map `{0:False,1:True}` (affects fan oscillate, climate swing) |
| xwv3jifdbhbolgh3 (Comfort Zone tower fan) | `fs/fs_xwv3jifdbhbolgh3.py:8-46` | fs | redefines dp2 `mode` enum = `["normal","nature","sleep"]` (R/W) (fan preset modes); dp22 `countdown_set` enum |
| crh9iaqFowdJX5UY (Kogan portable AC) | `kt/kt_crh9iaqfowdjx5uy.py:7-37` | kt | adds dp2 `temp_set` Integer ℃ 16..30 scale0 step1 R/W; dp3 `temp_current` ℃ -7..98 R; dp4 `mode` enum `["cold","hot","wet","wind"]` R/W |
| hw50w7qvxluhslkk (Della mini-split etc.) | `kt/kt_hw50w7qvxluhslkk.py:23-49` | kt | **conditional** (`apply_when=_is_fahrenheit_variant`: `isinstance(status["temp_set"],int) and >=450`): redefine dp2 `temp_set` as unit `℉` 160..880 scale1 step5 R/W, and remove dp136 `temp_set_f` |
| cpmgn2cf (TRV) | `wk/wk_cpmgn2cf.py:14-32` | wk | dp4 `mode` enum `["holiday","auto","manual","comfort","eco","BOOST","temp_auto"]` R/W (climate preset list) |
| if6pqia2gbtvqa6l (R11 thermostat) | `wk/wk_if6pqia2gbtvqa6l.py:10-20` | wk | dp2 `mode` enum `["auto","home"]` |
| ucf09xuve67adcp4 (Warmtec T510) | `wk/wk_ucf09xuve67adcp4.py:10-25` | wk | dp2 `mode` enum `["auto","comfort","eco","holiday"]` |
| tjvnxyobs3upidjo (heater) | `qn/qn_tjvnxyobs3upidjo.py:6-20` | qn | dp4 `mode` enum `["eco","off"]` (so device can be turned off) |
| ma3oq4onxxwg91ky, uhtamgih7kkdcqtx | `cs/cs_*.py` | cs | add `fault` bitmap / `humidity_indoor` (dp3, %, 0..100) + `temp_indoor` (dp103) R only; `humidity_indoor` feeds the humidifier `current_humidity` for uhtamgih7kkdcqtx |
| datzwoplui1zao16, x3o8epevyeo3z3oa, xeagimantb7d7apb (tdq) | `tdq/*` | tdq | add `temp_unit_convert`, `temp_current`, `humidity_value` (sensor-oriented; note LIGHTS[TDQ]=LIGHTS[TGQ] so tdq still gets dimmer-light candidates, but they only apply if the DPs exist) |
No quirk in 0.0.29 targets the light platform's DPs. There is no light/fan/humidifier "definition" quirk.

Tests bypass quirks with the `no_quirk` fixture for the snapshot tests (`TESTS/conftest.py:146-152`; each `test_platform_setup_and_discovery` is `@pytest.mark.usefixtures("no_quirk")`).

---------------------------------------------------------------------------
## 1. LIGHT

Sources: `CORE/light.py`, `HDL/definition/light.py`, `HDL/device_wrapper/light.py`, `TESTS/test_light.py`, `TESTS/snapshots/test_light.ambr`.

### 1.1 Entity decision
`async_discover_device` (`light.py:389-414`): for each device, `LIGHTS.get(device.category)` gives a tuple of descriptions; for each description a `LightDefinition` is built by `get_default_definition(...)`; **an entity is created iff the definition is not None**, which happens iff the description's `key` (the "switch dpcode") is found as a **Boolean** DP (`HDL/definition/light.py:75-80`, `DPCodeBooleanWrapper.find_dpcode(device, switch_dpcode, prefer_function=True)`). Every other DP (brightness, color_data, color_mode, color_temp) is optional; missing ones just disable that capability. A category not in `LIGHTS` -> no light entities. A device may yield several light entities (one per description whose switch DP exists).

`TuyaLightEntityDescription` fields (`light.py:35-45`): `key` (switch dpcode), `brightness` (dpcode or ordered tuple), `brightness_max`, `brightness_min`, `color_data` (dpcode or tuple), `color_mode` (dpcode, work-mode enum), `color_temp` (dpcode or tuple), `fallback_color_data_mode` (`V1` default | `V2`), plus HA `name`/`translation_key`/`translation_placeholders`/`entity_category`.

Entity naming: `name=None` => device name; `translation_key` `"light"` -> "Light", `"backlight"` -> "Backlight", `"indexed_light"` -> "Light {index}", `"night_light"` -> "Night light" (`CORE/strings.json` entity.light). Literal names "Floodlight"/"Indicator light" for `sp` (`light.py:260,264`). `entity_category=CONFIG` where marked.

### 1.2 The complete category table (`light.py:48-378`)
Columns: switch key | brightness | color_temp | color_data | color_mode | other. All `prefer_function=True` for all lookups.

| category | entity | switch key (required Bool) | brightness | brightness_max/min | color_temp | color_data | color_mode (work_mode) | other |
|---|---|---|---|---|---|---|---|---|
| bzyd | main (name None) | switch_led | - | | - | colour_data | work_mode | |
| clkg | "backlight", CONFIG | switch_backlight | - | | | | | |
| cwwsq | "light", CONFIG | light | - | | | | | |
| dc | main | switch_led | bright_value | | temp_value | colour_data | work_mode | |
| dd | main | switch_led | bright_value | | temp_value | colour_data | work_mode | fallback_color_data_mode=V2 |
| dj | main | switch_led | (bright_value_v2, bright_value) | | (temp_value_v2, temp_value) | (colour_data_v2, colour_data) | work_mode | |
| dj | "indexed_light" index=1 | switch_1 | bright_value_1 | | - | | | "manufacturer customized Dimmer 2 switches" comment `:101-102` |
| dsd | main | switch_led | bright_value | | - | - | work_mode | |
| fs | main | light | bright_value | | temp_value | - | work_mode | |
| fs | "indexed_light" index=2 | switch_led | bright_value_1 | | | | | |
| fsd | main | switch_led | bright_value | | temp_value | colour_data | work_mode | |
| fsd | main (2nd, name None) | light | - | | | | | "Some ceiling fan lights use LIGHT instead of SWITCH_LED" (both have name=None; both may exist) |
| fwd | main | switch_led | bright_value | | temp_value | colour_data | work_mode | |
| gyd | main | switch_led | bright_value | | temp_value | colour_data | work_mode | |
| hcdd | main | switch_led | bright_value | | temp_value | colour_data | work_mode | |
| hxd | "light" | switch_led | (bright_value_v2, bright_value) | brightness_max_1 / brightness_min_1 | - | - | - | |
| jsq | main | switch_led | bright_value | | - | colour_data_hsv | work_mode | (humidifier's light) |
| kg | "backlight", CONFIG | switch_backlight | | | | | | |
| kj | "backlight", CONFIG | light | | | | | | |
| kt | "backlight", CONFIG | light | | | | | | |
| ks | "backlight", CONFIG | light | | | | | | |
| mbd | main | switch_led | bright_value | | - | colour_data | work_mode | |
| msp | "light", CONFIG | light | | | | | | |
| qjdcz | main | switch_led | bright_value | | - | colour_data | work_mode | |
| qn | "backlight", CONFIG | light | | | | | | |
| sp | "Floodlight" | floodlight_switch | floodlight_lightness | | | | | |
| sp | "Indicator light", CONFIG | basic_indicator | | | | | | |
| sz | "light" | light | bright_value | | | | | |
| tgkg | indexed 1 | switch_led_1 | bright_value_1 | brightness_max_1/min_1 | | | | |
| tgkg | indexed 2 | switch_led_2 | bright_value_2 | brightness_max_2/min_2 | | | | |
| tgkg | indexed 3 | switch_led_3 | bright_value_3 | brightness_max_3/min_3 | | | | |
| tgq | "light" | switch_led | (bright_value_v2, bright_value) | brightness_max_1/min_1 | | | | |
| tgq | indexed 1 | switch_led_1 | bright_value_1 | | | | | |
| tgq | indexed 2 | switch_led_2 | bright_value_2 | | | | | |
| tyd | main | switch_led | bright_value | | temp_value | colour_data | work_mode | |
| tyndj | main | switch_led | bright_value | | temp_value | colour_data | work_mode | |
| xdd | main | switch_led | bright_value | | temp_value | colour_data | work_mode | |
| xdd | "night_light" | switch_night_light | - | | | | | |
| ykq | main | switch_controller | bright_controller | | temp_controller | - | work_mode | |
Aliases assigned after table (`light.py:367-378`): `cz`=`kg` table, `pc`=`kg` table, `dghsxj`=`sp` table, `tdq`=`tgq` table (same tuple objects).

### 1.3 Definition (wrapper) construction (`HDL/definition/light.py:62-238`)
- `switch_wrapper`: `DPCodeBooleanWrapper` on `key`, required.
- `brightness_wrapper`: `BrightnessWrapper.find_dpcode(device, brightness_dpcode)` (Integer DP; first hit of the tuple). If found, optionally attach `brightness_max`/`brightness_min` `DPCodeIntegerWrapper`s (also `prefer_function`) with `RemapHelper.from_type_information(type_info, 0, 255)` each (`:105-136`). **Note:** min and max are only used if BOTH exist (see 1.4).
- `color_mode_wrapper`: `DPCodeEnumWrapper.find_dpcode(device, color_mode_dpcode)` (work_mode enum).
- `color_temp_wrapper`: `ColorTempWrapper.find_dpcode(...)` (Integer DP; tuple order).
- `color_data_wrapper`: `_get_color_data_wrapper` (`:139-160`): try **Json** DP first (`ColorDataJsonWrapper`), else **String** DP (`ColorDataStringWrapper`). Ranges:
  - JSON with non-empty `function_data` (`type_data` JSON dict truthy): `h_type = RemapHelper.from_function_data(h or {"min":0,"max":360}, 0, 360)`, `s_type = ...(s or {"min":0,"max":255}, 0, 100)`, `v_type = ...(v or {"min":0,"max":255}, 0, 255)` (`:170-192`). i.e. source range comes straight from the DP's own h/s/v min/max, targets HA H 0-360, S 0-100, V 0-255.
  - JSON with empty spec, or String DP (hex): `_apply_fallback_ranges` (`:224-238`): if `fallback_color_data_mode==V2` **or** `color_data_wrapper.dpcode=="colour_data_v2"` **or** (brightness wrapper exists and `brightness_wrapper.max_value > 255`) then V2 remaps (`DEFAULT_H_TYPE_V2 = (1..360)->(0..360)`, `S_V2 = (1..1000)->(0..100)`, `V_V2 = (1..1000)->(0..255)`; `HDL/device_wrapper/light.py:~229-237`), else class defaults V1 (`H (1..360)->(0..360)`, `S (1..255)->(0..100)`, `V (1..255)->(0..255)`; `:~218-227`).
  - Because fallback ranges have **source_min=1** but function-data ranges use the DP's own min (usually 0), the H/S/V remap is not uniform across DPs; the IL must carry (source_min,source_max,target_min,target_max) per channel.

### 1.4 Value transforms
`RemapHelper.remap_value(v, from_min, from_max, to_min, to_max, reverse=False)` = `((v-from_min)/(from_max-from_min))*(to_max-to_min)+to_min`; `reverse` first flips `v=from_max-v+from_min` (`HDL/utils.py:72-87`). Linear, no clamping. Rounding is applied at the end where stated below.

**Brightness** (`BrightnessWrapper`, `device_wrapper/light.py:~20-95`):
- Read: raw scaled value -> `remap(raw, dp_min..dp_max -> 0..255)` (scaled min/max, `RemapHelper.from_type_information(ti,0,255)`). If BOTH `brightness_max` and `brightness_min` DPs exist and both currently report values: convert min/max DP values to 0..255 with their own remaps, then re-remap brightness from `[min_255, max_255]` to `[0,255]` (limits are dynamic device-reported values read from status at read time). Result `round(...)`.
- Write: inverse: if min/max DPs present & readable, first map HA 0..255 -> `[min_255, max_255]`, then `remap_value_from` to raw range, then `prepare_set_value` (integer round + range check; raises if outside `[min,max]`).
- E.g. test: `bright_value_v2` range 10..1000: HA 150 -> 592, HA 255 -> 1000 (`TESTS/test_light.py:76-80,116-120`).

**Color temperature** (`ColorTempWrapper`, `:~139-215`): Kelvin range **fixed** 2000..6500 K (`MIN_KELVIN=2000` = 500 mired, `MAX_KELVIN=6500` = 153 mired) as class defaults "the Tuya cloud never reports it" (`:~146-159`). Default scale `ColorTempScale.MIRED` (linear in mireds); a subclass could select KELVIN scale, but **no subclass exists in 0.0.29** and core uses the class constants except `test_color_temp_range_override` patches them.
- Read: raw scaled -> `remap_value_to(raw, reverse=True)` with target range [153.85 mired, 500 mired] (i.e. raw_min <-> 500 mired? see below), -> `mired_to_kelvin = round(1e6/mired)`. The remap uses `RemapHelper.from_type_information(ti, min_mireds=1e6/6500, max_mireds=1e6/2000)` and **reverse=True**, i.e. raw_min maps to max_mireds (warmest 2000K) and raw_max to min_mireds (coolest 6500K). So raw low = warm, raw high = cool.
- Write: `kelvin_to_mired`, `remap_value_from(mired, reverse=True)`, then `prepare_set_value`. Test: 5000 K on `temp_value` 0..255 -> 221 (`TESTS/test_light.py:135-141`).
- Entity min/max kelvin come from the wrapper's `min_kelvin/max_kelvin` (`light.py:430-431,457-462`), default 2000/6500.

**Color data (HS + V)**:
- JSON read: `(h_remap(status["h"]), s_remap(status["s"]), v_remap(status["v"]))` -> tuple (H 0..360, S 0..100, V 0..255) unrounded floats (`device_wrapper/light.py:~258-268`).
- JSON write: `json.dumps({"h": round(h_type.remap_value_from(h)), "s": round(...s), "v": round(...v)})` -> **a JSON string** value (compact separators with ", " and ": " as produced by `json.dumps`) (`:~270-281`). Test: HS (10.1,20.2), brightness 255 -> `'{"h": 10, "s": 202, "v": 1000}'` (`test_light.py:119`).
- String read: 12 hex chars `HHHHSSSSVVVV` (three 4-digit hex numbers); any other length/type or non-hex -> None. Then remaps (`:~295-313`).
- String write: `f"{round(h):04x}{round(s):04x}{round(v):04x}"` (`:~315-323`). No uppercase, no other formats (e.g. no 14-char HSV+alpha forms).
- V (brightness within colour mode) is `v_type.remap_value_to` so HA brightness 0..255 <-> device V range.

### 1.5 HA features exposed
- `supported_features` = 0 always (no effects/transition), confirmed in snapshots (`test_light.ambr:1703-1712` `LightEntityFeature: 0`, entity registry `supported_features: 0` at `:1691`). **No light effects, no scenes, no music mode, no transition, no white-flag other than color mode WHITE**.
- `supported_color_modes` (`light.py:448-476`): start `{ONOFF}`; `+BRIGHTNESS` if a brightness wrapper exists; `+HS` if a color_data wrapper exists; `+COLOR_TEMP` if a color_temp wrapper exists; `elif` (no color_temp) and `color_supported(modes)` (i.e. HS present) and a color_mode wrapper exists and `"white"` in its enum options -> `+WHITE` and `_white_color_mode = WHITE`. Then HA's `filter_supported_color_modes` prunes redundant modes (ONOFF removed if any richer mode, BRIGHTNESS removed if HS/COLOR_TEMP/etc.). Observed combos in snapshots (counts over `test_light.ambr` entries): {COLOR_TEMP,HS} x25, {HS,WHITE} x5, {BRIGHTNESS} x3, {HS} x3, {ONOFF}+CONFIG x11, {COLOR_TEMP} x17, {ONOFF} x1.
- `_fixed_color_mode` set when exactly one supported mode remains (`light.py:473-476`).
- `color_mode` (`light.py:578-594`): fixed mode if fixed; else if a color_mode (work_mode) wrapper exists and its value `!= "white"` (**anything else incl. None/unknown/scene/music counts as HS**) -> `HS`; otherwise `_white_color_mode` (COLOR_TEMP or WHITE). **If no work_mode wrapper exists but multiple modes are supported, color_mode is always the white mode** (never HS).
- `is_on` = switch bool (`light.py:478-482`).
- `brightness` (`:552-562`): in HS color mode -> `round(hsv[2])` from color_data (V); otherwise brightness wrapper value (0..255). Returns None if wrapper missing/None.
- `hs_color` (`:570-577`): `(H,S)` from color_data if wrapper exists, regardless of mode (snapshot shows `hs_color: None` in WHITE mode only because the value is None/blocked by HA color mode logic; HA core light hides hs_color unless color_mode==HS).
- `color_temp_kelvin` (`:564-568`): color_temp wrapper value.
- `min/max_color_temp_kelvin` (2000/6500 default).

### 1.6 turn_on / turn_off write logic (`light.py:484-550`) - multi-DP, ordered
Given HA `kwargs`:
1. `commands = [switch <- True]` (always first, always sent on every turn_on, even when only changing brightness).
2. If color_mode wrapper exists and (`ATTR_WHITE` or `ATTR_COLOR_TEMP_KELVIN` in kwargs): append `work_mode <- "white"` (via enum validation; raises if "white" is not in enum range).
3. If color_temp wrapper and `ATTR_COLOR_TEMP_KELVIN` in kwargs: append `color_temp DP <- kelvin` (converted).
4. If color_data wrapper and (`ATTR_HS_COLOR` in kwargs OR (`ATTR_BRIGHTNESS` in kwargs and current `self.color_mode == HS` and no WHITE and no COLOR_TEMP)):
   - append `work_mode <- "colour"` if color_mode wrapper exists;
   - brightness = kwargs brightness else (`self.brightness or 0`); color = kwargs hs else (`self.hs_color or (0,0)`);
   - append `color_data <- (h, s, v=brightness)` single DP write (V carries brightness).
   - Falls through **without** running the brightness branch (`elif`).
5. `elif` brightness wrapper and (`ATTR_BRIGHTNESS` or `ATTR_WHITE`): append `brightness DP <- (kwargs[BRIGHTNESS] if present else kwargs[WHITE])`. So brightness after a work_mode white/colour-temp change is sent separately.
6. All commands sent in **one** `send_commands` call in that order.
Tests confirm orders: `[switch_led True, work_mode white, bright_value_v2 546]` (white=True; HA supplies current brightness), `[switch_led, bright_value_v2 592]` (brightness only), `[switch_led, work_mode colour, colour_data_v2 {...}]`, `[switch_led, temp_value 221, bright_value 255]` for device without work_mode (`test_light.py:56-142`).
`turn_off`: `[switch <- False]` only (`:547-550`). No transition/flash/effect support.
Cross-dp dependencies: (a) `color_mode` property depends on `work_mode` status; (b) writes to `work_mode` are derived from which HA attrs are present; (c) `brightness` read depends on color_mode (HS -> colour_data.v, else brightness DP); (d) brightness write in HS mode depends on the current `hs_color` (read-modify-write of a combined JSON/hex field: current H,S from status are re-sent when only brightness changes); (e) brightness scaling depends on live `brightness_max/min` DP values; (f) color-data range mode depends on the brightness DP's max (`max_value>255` -> V2 ranges) and the dpcode name `colour_data_v2`.

### 1.7 Pure data vs code
Pure data (tables): category -> ordered list of descriptions (key, dpcode lists, names, category, index placeholders), fallback color mode V1/V2. Data-driven but algorithmic (needs a fixed set of named transforms): linear remap of int DPs to 0..255, dynamic min/max brightness limits, mired-linear kelvin mapping with fixed 2000-6500 K range, JSON `{h,s,v}` codec with per-channel ranges, hex `HHHHSSSSVVVV` codec, V1/V2 range selection rule, first-match dpcode tuple. Arbitrary code: the color_mode / turn_on decision logic in `light.py` (the `if/elif` tree above) - it is ordinary code but small and fully described by the rules in 1.5-1.6 (a declarative "capabilities + write recipe" is feasible).

### 1.8 Requirements for the IL (light)
- R-L1: Per entity, a **required boolean switch DP** with lookup semantics (function-first then status_range; type must be Boolean); entity absent if missing (`HDL/definition/light.py:75-80`).
- R-L2: Ordered **dpcode alternatives** per role (`bright_value_v2` before `bright_value`, etc.) resolved against the actual device DP set (`light.py:97-99`; `type_information.py:88-116`).
- R-L3: Multiple entities per category/device, keyed by switch dpcode, with names/translation keys/entity category/placeholders (`light.py:103-108,126-131,275-321`).
- R-L4: Category-level table reuse/aliasing (cz/pc=kg, dghsxj=sp, tdq=tgq) (`light.py:367-378`).
- R-L5: Integer range remap with DP-declared scale, min, max, and rounding, into HA 0..255 (`device_wrapper/light.py`).
- R-L6: Dynamic brightness limits from two other integer DPs, applied only when both exist and are readable, both on read and write (`:20-95`).
- R-L7: Color temperature: linear-in-mired mapping over fixed 2000..6500 K, reversed direction (raw low=warm), integer write range-check; optional per-device Kelvin range/scale override is a capability of the wrapper (`:139-215`) even though no in-tree device uses it.
- R-L8: Color data codecs: JSON object `{"h","s","v"}` (written as JSON **string**) and 12-char hex string (`HHHHSSSSVVVV`), with per-channel (src_min, src_max, dst_min, dst_max) ranges; range derivation rules (function data h/s/v min/max; else V1/V2 fallback; V2 triggered by category flag, dpcode `colour_data_v2`, or brightness DP max>255) (`HDL/definition/light.py:163-238`). Plus the alternative `colour_data_hsv` code for `jsq` (`light.py:193`) which goes through the same String/JSON wrapper search (no special handling).
- R-L9: Work mode enum DP: entity color_mode derived = HS unless value=="white"; `"white"`/`"colour"` literals written; requirement that `"white"` be in enum range for WHITE color mode when no color_temp (`light.py:463-471`).
- R-L10: Supported color mode set computation (ONOFF/BRIGHTNESS/HS/COLOR_TEMP/WHITE) + HA's filtering (`light.py:448-476`).
- R-L11: Ordered multi-command turn_on recipe (switch, work_mode, color_temp, color_data|brightness) in a single batch (`light.py:484-545`), including read-modify-write of HS/V using the entity's current derived state.
- R-L12: HA-level ATTR_WHITE semantics (white value used as brightness; WHITE mode -> `work_mode="white"`) (`light.py:489-495,533-539`).
- R-L13: turn_off = switch false only.
- R-L14: No transition, effect, or scene support is needed (supported_features 0).
- R-L15: Read validation semantics (out-of-range int/enum/bool -> None/unknown, one-time warn) and write validation (reject rather than clamp).
- R-L16: Availability = device online.

### 1.9 Open questions / surprises (light)
- `brightness_max/min` DPs are read from live status at each read; with only one of the pair present the limits are silently ignored (`device_wrapper/light.py:~50-60`).
- `hs_color` is returned regardless of mode; HA core hides it in non-HS modes (evidence: `light.garage_light` state attributes `hs_color: None`, color_mode WHITE at `test_light.ambr:1699-1720`, but device status has a `colour_data_v2` of h 243 -> so HA core does the filtering; IL should not assume the integration does).
- `color_mode` treats any non-"white" work_mode (music/scene/unknown/None) as HS, and treats a missing work_mode DP as white mode (never HS) when multiple modes exist.
- Two `fsd` descriptions both have `name=None`: both switch_led and light lights could exist on one device -> duplicate entity name; unique_ids differ (`light.py:133-147`).
- `sp` (`dghsxj`) `Floodlight` etc. use literal English names, not translation keys.
- `brightness` may exceed HA range if device brightness DP has a min>0: the mapping treats the DP min as 0 (HA 0 -> DP min), so HA brightness 0 sends DP min not off (as seen 150 -> 592 with min 10).
- The string-hex form's `prepare` is not validated against the DP's declared `maxlen`.

---------------------------------------------------------------------------
## 2. CLIMATE

Sources: `CORE/climate.py`, `CORE/util.py`, `HDL/definition/climate.py`, `HDL/device_wrapper/climate.py`, `HDL/device_wrapper/extended.py`, `HDL/const.py`, `TESTS/test_climate.py`, `TESTS/snapshots/test_climate.ambr`.

### 2.1 Entity decision
- Table `CLIMATE_DESCRIPTIONS` (`climate.py:72-97`), exactly **one description per category**, with `key=""` and `switch_only_hvac_mode`:
  | category | switch_only_hvac_mode |
  |---|---|
  | dbl (electric fireplace) | HEAT |
  | kt (air conditioner) | COOL |
  | qn (heater) | HEAT |
  | rs (water heater) | HEAT |
  | wk (thermostat) | HEAT_COOL |
  | wkf (TRV) | HEAT |
- **`get_default_definition` never returns None** (`HDL/definition/climate.py:180-212`): so **every device in those six categories gets a climate entity, even with zero relevant DPs** (evidence: `climate.mr_pure` and `climate.geti_solar_pv_water_heater` snapshots: `supported_features: 0`, `hvac_modes: []`, state `unknown`, `TESTS/snapshots/test_climate.ambr:1013-1030,649-680`). No DP is "required".
- Other categories (e.g. `cs`, `jsq`, `kj`) never produce climate entities.

### 2.2 Wrappers built (`HDL/definition/climate.py:180-212`)
| role | dpcodes (order) | wrapper | prefer_function |
|---|---|---|---|
| current temperature | `("temp_current","upper_temp")` (Integer) and F-variant `("temp_current_f","upper_temp_f")` | `DPCodeIntegerWrapper` (scaled) | no (status_range first) (`:146-151`) |
| set temperature | `temp_set`, `temp_set_f` | `DPCodeIntegerWrapper` | yes (`:152-157`) |
| unit-convert enum | `temp_unit_convert` | `DPCodeEnumWrapper` | no (`:160-164`) |
| current humidity | `humidity_current` | `DPCodeRoundedIntegerWrapper` (`round(scaled)`) | no |
| target humidity | `humidity_set` | `DPCodeRoundedIntegerWrapper` | yes |
| fan mode | `("fan_speed_enum","level","windspeed")` Enum | `DPCodeEnumWrapper` | yes |
| hvac mode | `mode` Enum | `DefaultHVACModeWrapper` | yes |
| preset | `mode` Enum (**same DP**) | `DefaultPresetModeWrapper` | yes |
| swing | see 2.5 | `SwingModeCompositeWrapper` | yes |
| switch | `("switch","power_switch")` Boolean | `DPCodeBooleanWrapper` | yes |

### 2.3 Temperature unit selection (`HDL/definition/climate.py:58-177`, `CORE/climate.py:114-121,163-182`)
System unit passed in = `hass.config.units.temperature_unit` mapped to `TuyaUnitOfTemperature` ("°C"/"°F"), default Celsius (`climate.py:59-62,117-120`).
1. If a `temp_unit_convert` enum DP exists: for each of the four temp wrappers with an **empty unit**, set `wrapper.type_information.unit = temp_unit_convert.read_device_status(device)` (the raw enum status, e.g. `"c"`/`"f"`, read **once at discovery time** - the unit is not re-evaluated when the status changes) (`:160-169`).
2. `get_temperature_wrappers`: classify each wrapper by unit (`unit.lower()` in `CELSIUS_ALIASES={"°c","c","celsius","℃"}` / `FAHRENHEIT_ALIASES={"°f","f","fahrenheit","℉"}`, `HDL/const.py:12-13`); the C/F candidates for current = `[temp_current, temp_current_f]` (C) and `[temp_current_f, temp_current]` (F); set likewise. Choose:
   - if system unit is Fahrenheit and ((cur_F and set_F) or (cur_F and not set_C) or (set_F and not cur_C)) -> use F wrappers, entity unit F;
   - elif ((cur_C and set_C) or (cur_C and not set_F) or (set_C and not cur_F)) -> C wrappers, entity unit C;
   - else fallback: system unit F -> `(temp_current_f or temp_current, temp_set_f or temp_set, F)`; else `(temp_current or temp_current_f, temp_set or temp_set_f, C)`.
   Units that match neither alias set (e.g. `"℃ ℉"`, `"℃/F"`) fall into the fallback and are assumed to be in the system unit (docstring of `TESTS/test_climate.py:323-328`).
3. Entity: `_attr_temperature_unit = definition.temperature_unit` (`climate.py:163`); `_current_temp_unit` / `_set_temp_unit` = `get_temperature_unit(device, wrapper.native_unit)` (`util.py:18-30`): unit string -> C/F via aliases; **if the DP unit is empty -> `device.status["temp_unit_convert"]` mapped {"c":C,"f":F} (dynamic read at init only)**; unrecognised -> None (no conversion) (`climate.py:165-172`).
4. Reads: `current_temperature`/`target_temperature` value; if native unit known and != entity unit -> `TemperatureConverter.convert` (`climate.py:292-326`). Write: `async_set_temperature` converts HA temperature (entity unit) into the DP's native unit before writing (`climate.py:282-290`). Test matrix: `test_temperature_unit_conversion` (`TESTS/test_climate.py:243-355`).
5. `min_temp/max_temp/target_temperature_step` = `set_wrapper.min_value/max_value/value_step` = **scaled DP min/max/step in the DP's native unit, NOT converted** to the entity unit (`climate.py:174-182`) - so if native != entity unit the limits are in the wrong unit (inference from code; the test `metric-both-fahrenheit` does not assert min/max).
6. `target_temperature_step` default `1.0` (`climate.py:140`), else DP step scaled (e.g. step 5 scale 1 -> 0.5, `wk_6kijc7nd` -> 0.5).

Quirk interplay: `kt/kt_hw50w7qvxluhslkk.py` fixes F variants (0.6). Fixture `kt_ibmmirhhq62mmf1g` has both `temp_set` (℃, 160..880 scale 1) and `temp_set_f` (℉ 61..88); snapshot picks C wrapper under metric (min 16.0 max 88.0 step 0.5, temperature 75.0, `test_climate.ambr` climate.master_bedroom_ac).

### 2.4 HVAC modes / presets / switch (core `climate.py:184-208,241-259,334-351`; handlers `device_wrapper/climate.py:18-217`)
Default mode map `_DEFAULT_DEVICE_MODE_TO_HVACMODE` (`device_wrapper/climate.py:18-28`):
```
"auto"   -> HEAT_COOL
"cold"   -> COOL
"freeze" -> COOL
"heat"   -> HEAT
"hot"    -> HEAT
"manual" -> HEAT_COOL
"off"    -> OFF
"wet"    -> DRY
"wind"   -> FAN_ONLY
```
(TuyaClimateHVACMode values off/heat/cool/heat_cool/auto/dry/fan_only, `HDL/helpers/homeassistant.py:134-157`; core maps 1:1 to HA `HVACMode`, `climate.py:39-48`.) Note `HVACMode.AUTO` is in the enum but **no device string maps to it**.
`_filter_hvac_mode_mappings(range)` (`:134-151`): map each enum string via the table (unknown strings -> None); if two+ strings map to the **same** HA mode, **all of them are set to None** ("avoid ambiguity").
- `DefaultHVACModeWrapper.options` = the non-None mapped HA modes of the filtered map (includes OFF if `"off"` in range) (`:160-170`). `hvac_modes` in core: if hvac wrapper exists: `[OFF] + [each option except OFF]`, else if only a switch: `[OFF, switch_only_hvac_mode]`, else `[]` (`climate.py:184-201`).
- `DefaultPresetModeWrapper.options` = enum strings whose filtered mapping is None, i.e. **unmapped strings AND all strings involved in an ambiguity** (e.g. `auto`+`manual` both HEAT_COOL -> both presets; `cold`+`freeze` -> both presets, `heat`+`hot` -> both presets) (`:195-217`). Presets are raw enum strings. Read: raw value if in `options` else None.
- Presets: if `preset_wrapper.options` non-empty -> `PRESET_MODE` feature, `preset_modes = options`, **and `switch_only_hvac_mode` is appended to `hvac_modes` if absent** (`climate.py:203-208`).
- `hvac_mode` (state) (`climate.py:334-351`): if switch status `is False` -> OFF (even if mode says otherwise); if no hvac wrapper: switch True -> `switch_only_hvac_mode`, else None; with a wrapper: `_DEFAULT_DEVICE_MODE_TO_HVACMODE` looked up on the raw mode **unfiltered** (`read_device_status`, `:172-180`) -> so ambiguous strings *do* yield an HVAC mode on read (e.g. mode `manual` -> HEAT_COOL) even though they are absent from the settable `hvac_modes`. If mode is not in the map -> None (`unknown` state when the switch is True/None and mode is a preset-only value such as `holiday`/`program`). Note: raw `"off"` mode with switch True reads OFF.
  - Snapshot evidence: `climate.empore` state `heat_cool` with `hvac_modes [off, heat]`, `preset_mode manual`, presets `[auto, manual]` (`test_climate.ambr:501-527`); `climate.kabinet` heat_cool with preset_modes `['program']` and preset_mode None.
- `async_set_hvac_mode(hvac_mode)` (`climate.py:241-259`): commands list, in order: (1) if switch wrapper: `switch <- (hvac_mode != OFF)`; (2) if hvac wrapper and `HA->Tuya map[hvac_mode]` exists and is in `hvac_wrapper.options`: `mode <- first raw enum string whose filtered mapping == that Tuya mode` (`_convert_value_to_raw_value`, `:182-192`). So HVAC OFF sends `[switch false]` plus `[mode "off"]` iff `"off"` is in the enum. Setting HEAT on a wk device whose HEAT is not a mode option sends only `switch true`. Tests: COOL -> `[{"code":"switch","value":True},{"code":"mode","value":"cold"}]` (`TESTS/test_climate.py:129-135`).
- `async_set_preset_mode(preset)`: `mode <- preset` (validated against the enum) (`climate.py:261-264`; test `wk_gc1bxoq2hafxpa35` -> `[{"code":"mode","value":"holiday"}]`, `:136-142`). Note it does **not** touch the switch, and it writes the same DP as hvac mode (last-writer semantic).
- `turn_on/turn_off`: `switch <- True/False` only (`climate.py:372-380`); features `TURN_ON|TURN_OFF` iff switch wrapper (`:236-239`).

### 2.5 Fan mode, swing, humidity
- Fan: `FAN_MODE` feature iff enum `("fan_speed_enum","level","windspeed")` found (function-first); `fan_modes` = raw enum range strings (e.g. `['1','2']`, `['low','middle','high','auto']`); read/write raw strings (`climate.py:220-223,266-269`; test `windspeed` `"2"`, `TESTS/test_climate.py:108-114`). Enum values are strings; HA passes `2` (number in test) -> `"2"`? The test sends `ATTR_FAN_MODE: 2` and expects `"2"` (HA coerces to str before the entity).
- Swing (`SwingModeCompositeWrapper`, `device_wrapper/climate.py:31-131`): finds Boolean DPs `on_off=("swing","shake")`, `horizontal="switch_horizontal"`, `vertical="switch_vertical"` (function-first); exists if any of them exists; `options=[OFF] + [ON if on_off] + [HORIZONTAL if h] + [VERTICAL if v]` (**BOTH is never listed** in options even though read/write support it) (`:45-71`). Core maps to HA `SWING_*` strings; only options present in `_TUYA_TO_HA_SWING_MAPPINGS` (all 5) are exposed (`climate.py:50-56,225-234`).
  - Read: on_off True -> ON; else h&v -> BOTH; h -> HORIZONTAL; v -> VERTICAL; else OFF (`:73-95`).
  - Write value X -> commands in this fixed order: `on_off <- (X==ON)`, `vertical <- (X in {BOTH,VERTICAL})`, `horizontal <- (X in {BOTH,HORIZONTAL})` (one command per existing DP; so one HA call -> up to 3 DP writes) (`:97-131`). Test: horizontal -> `[{"code":"switch_horizontal","value":True}]` for a device with only that DP (`TESTS/test_climate.py:143-149`).
  - `SWING_MODE` feature iff wrapper found.
- Humidity: `TARGET_HUMIDITY` feature iff `humidity_set` Integer found; `min_humidity/max_humidity = round(min/max scaled)`; set writes int. `current_humidity` from `humidity_current` (status-first) rounded (`climate.py:210-218,307-311,328-332`). `async_set_humidity` writes wrapper (no-op if wrapper None, HA blocks with `ServiceNotSupported` since the feature bit is absent, `TESTS/test_climate.py:181-227`).
- `TARGET_TEMPERATURE` feature iff set-temperature wrapper found (`climate.py:176-182`). No `TARGET_TEMPERATURE_RANGE`, no `AUX_HEAT`. `supported_features` observed: 0,1,17,385,393,401,417 (`test_climate.ambr` states; bit values: 1 target temp, 8 fan, 16 preset, 32 swing, 128 turn_off, 256 turn_on).
- Attribute `current_temperature` etc. `_attr_name=None` (entity named after device).

### 2.6 Write path for temperature
`async_set_temperature(**kwargs)`: uses only `ATTR_TEMPERATURE` (`kwargs[ATTR_TEMPERATURE]`, KeyError if HA passes only low/high - not applicable, no range feature); converts unit if needed; `IntegerTypeInformation.prepare_set_value` rounds `round(value*10**scale)`: 22.7 with scale 0 -> 23 (`TESTS/test_climate.py:104-107`); out of range -> `SetValueOutOfRangeError`.

### 2.7 Pure data vs code
Pure data: category table (6 rows: switch_only mode), dpcode tuples per role, default device-mode->HVAC map, swing DP names, swing options rule, unit alias sets. Algorithmic but fixed: (a) unit selection algorithm (with `temp_unit_convert` injection at discovery), (b) mode-map ambiguity filter (duplicate detection) producing hvac options vs preset options, (c) hvac_mode derivation, (d) composite swing read/write, (e) unit conversion (C<->F; `TemperatureConverter`), (f) hvac write (switch + mode). Arbitrary code: quirk predicates like `_is_fahrenheit_variant` (`kt_hw50w7qvxluhslkk.py:23-26`).

### 2.8 Requirements for the IL (climate)
- R-C1: Entity per (category, table row) unconditional on DPs (no required DP), with per-category `switch_only_hvac_mode` (`climate.py:72-97`; snapshots with empty features).
- R-C2: Role-based dpcode search with ordered alternatives and function-vs-status preference per role (`HDL/definition/climate.py:146-211`).
- R-C3: Scaled integer temperature DPs (scale, min, max, step, unit), rounding on write; min/max/step exported as entity limits (`climate.py:174-182`).
- R-C4: Dual C/F DP selection rule + unit inference (unit string aliases; `temp_unit_convert` enum status read at init; fallback to system unit) + runtime unit conversion on both read and write, with the HA system unit as an input to the definition (`HDL/definition/climate.py:74-134`; `CORE/util.py:18-37`; `CORE/climate.py:283-326`). The IL needs a "host temperature unit" parameter.
- R-C5: Enum -> HVAC mode mapping table with ambiguity elimination (duplicates become presets) and a **read map that is not filtered** (`device_wrapper/climate.py:18-28,134-180`), plus OFF handling (`"off"` in enum) and `hvac_modes` = OFF + options.
- R-C6: Preset modes = residual enum strings (raw) with PRESET_MODE feature and forced `switch_only_hvac_mode` inclusion (`climate.py:203-208`).
- R-C7: HVAC mode set = ordered command list `[switch(bool != OFF), mode(enum)]`; state derivation switch-False -> OFF, switch-only fallback (`climate.py:241-259,334-351`).
- R-C8: Composite swing: three booleans (`swing|shake`, `switch_horizontal`, `switch_vertical`) combined into a 5-value enum with fixed write ordering (on_off, vertical, horizontal) (`device_wrapper/climate.py:31-131`).
- R-C9: Raw enum strings used as fan mode / preset names (no HA-constant mapping) (`climate.py:220-223`).
- R-C10: Optional humidity (current & target) integer DPs with rounding (`extended.py:21-28`).
- R-C11: Feature-flag computation from wrapper presence (`TARGET_TEMPERATURE`, `PRESET_MODE`, `TARGET_HUMIDITY`, `FAN_MODE`, `SWING_MODE`, `TURN_ON|TURN_OFF`).
- R-C12: Quirk-level DP metadata overrides (add enum/integer DPs, conditional on device status) since several products need patched enums/units (0.6). The predicate is arbitrary code (`apply_when`) -> IL needs either a small condition language ("status value >= N") or a per-product override hook.
- R-C13: Read validation to None for out-of-range values (unknown state) and no clamping on write.

### 2.9 Open questions / surprises (climate)
- `hvac_mode` may return a mode not in `hvac_modes` (`empore`: state `heat_cool`, hvac_modes `[off, heat]`) - HA core log-warns; parity with core requires reproducing this, but a rewritten integration may prefer to fix it. Flagged for design decision.
- `temp_unit_convert` unit injection uses the value at discovery; later unit changes on the device are not tracked (`HDL/definition/climate.py:160-169`; `CORE/climate.py:165-172`).
- Min/max not unit-converted (see 2.3(5)) - likely latent bug; not test-covered.
- `HVACMode.AUTO` and `TuyaClimateHVACMode.AUTO` exist but nothing maps to them; `auto` dp string maps to HEAT_COOL.
- `switch_only_hvac_mode` when preset exists is appended even if `hvac_wrapper` already offers other modes.
- `preset_mode`/`hvac_mode` share DP `mode`; selecting a preset does not touch `switch`.
- `TuyaClimateEntity.async_set_hvac_mode` mode write when `hvac_mode==OFF` and `"off"` in enum: both switch False and mode "off" are sent.
- `async_turn_on` only sets switch; no "retain HVAC" logic beyond that (docstring claims retaining).
- Fan-mode values are strings; enum `windspeed` `["1","2"]` etc.

---------------------------------------------------------------------------
## 3. FAN

Sources: `CORE/fan.py`, `HDL/definition/fan.py`, `HDL/device_wrapper/fan.py`, `HDL/device_wrapper/extended.py`, `TESTS/test_fan.py`, `TESTS/snapshots/test_fan.ambr`.

### 3.1 Entity decision
- Table `FANS` (`fan.py:31-38`): categories `cs` (dehumidifier), `fs` (fan), `fsd` (ceiling fan light), `fskg` (fan wall switch), `kj` (air purifier), `ks` (tower fan). One description each, `key=""`, no per-category variation (all identical).
- Entity created iff `get_default_definition` returns non-None: **at least one** of these dpcodes exists in `device.function`, `device.status`, **or** `device.status_range` (`HDL/definition/fan.py:53-69`):
  `switch_fan, fan_switch, switch, fan_speed_percent, fan_speed, speed, fan_speed_enum, switch_horizontal, switch_vertical, fan_direction`. **`mode`/`fan_mode` alone do not create an entity** (they are not in the check set). This check is by name only (any type).
- Wrappers (all `prefer_function=True`):
  | role | dpcodes (order) | wrapper |
  |---|---|---|
  | switch | `("switch_fan","fan_switch","switch")` Boolean | `DPCodeBooleanWrapper` |
  | speed | `("fan_speed_percent","fan_speed","speed","fan_speed_enum")` | `FanSpeedIntegerWrapper` (Integer) **or, if none is Integer**, `FanSpeedEnumWrapper` (Enum) (`definition/fan.py:80-85`) |
  | mode (preset) | `("fan_mode","mode")` Enum | `DPCodeEnumWrapper` (raw strings) |
  | oscillate | `("switch_horizontal","switch_vertical")` Boolean | `DPCodeBooleanWrapper` (only the **first found** DP; never both) |
  | direction | `("fan_direction",)` Enum | `FanDirectionEnumWrapper` |
  Note the speed search is two-pass: first look for an Integer among all four codes, only then for an Enum among all four (so `fan_speed` Integer beats `fan_speed_enum`).

### 3.2 Features and attribute derivation (`fan.py:96-115,172-201`)
- `PRESET_MODE`: iff mode wrapper; `preset_modes` = raw enum range (`fan.py:96-98`), read raw string, write validated enum string.
- `SET_SPEED`: iff speed wrapper; `speed_count = len(options)` if the wrapper has `options` (enum) else HA default 100 (`fan.py:100-105`). Snapshots: enum with 6 options -> `percentage_step 16.67`; int -> `1.0`.
- `OSCILLATE`: iff oscillate wrapper. `DIRECTION`: iff direction wrapper. `TURN_ON|TURN_OFF`: iff switch wrapper. Observed `supported_features` 48 (TURN_ON|TURN_OFF), 49, 53, 56, 59, 61 (`test_fan.ambr`).
- `is_on` = switch bool; `oscillating` = bool DP; `preset_mode` = raw enum; `current_direction` via map `forward/reverse` -> HA `DIRECTION_*` (`fan.py:40-46,180-183`); `percentage` = speed wrapper value.
- Fan **without a switch** DP: `is_on` None, no turn_on/off features; `async_turn_on` returns immediately doing nothing (`fan.py:151-152`) - so `percentage`/`preset_mode` in turn_on are ignored unless there is a switch.

### 3.3 Transforms
- `FanSpeedIntegerWrapper` (`HDL/device_wrapper/fan.py:~47-57`, on `DPCodeRemappedIntegerWrapper` `extended.py:31-73`): raw scaled value -> `round(remap(raw, dp_min..dp_max -> 1..100))`; write `remap_value_from(percent)` then integer round/range check. **Range starts at 1, not 0** ("Contrary to the standard DPCodePercentageWrapper we start the range at 1"). E.g. `fan_speed` 1..6 raw 2 -> `round((2-1)/5*99+1)=21` (snapshot `fan.ceiling_fan_light_v2` percentage 21).
- `FanSpeedEnumWrapper` (`~:60-84`): options = enum range in declared order. Read: `position=index(value)+1; percent=(position*100)//len(options)` (integer floor). Write: percentage p -> first option (in order) whose `upper_bound=(pos*100)//len` satisfies `p <= upper_bound`, else last option. Test: 6 options, 50% -> `"3"` (`TESTS/test_fan.py:118-124`). No "off" option is assumed: enum values are all treated as speeds, including any like "off"/"auto" (if the device lists them).
- Direction: enum strings `"forward"`/`"reverse"` only; `options` = those present in the DP range; other strings read as None and cannot be written (`device_wrapper/fan.py:~17-44`; core `async_set_direction` writes the Tuya direction string if HA direction is known, `fan.py:122-126`).

### 3.4 Write logic
- `async_set_percentage(p)`: **if p==0 and a switch exists -> `turn_off` (`switch <- False`) and return; else speed DP write** (`fan.py:128-136`; tests `[{"code":"switch","value":False}]` for percentage 0, `test_fan.py:126-140`). If no switch and p==0 the value 0 is remapped and (for enum) selects the first option; for integer it falls below the DP min and raises out-of-range (inference).
- `async_turn_on(percentage, preset_mode)`: `[switch True]` + (speed write if `percentage is not None` and speed wrapper) + (mode write if `preset_mode is not None` and mode wrapper), single batch; test `[{"code":"switch","value":True},{"code":"mode","value":"sleep"}]` (`fan.py:143-165`; `test_fan.py:110-116`). Note percentage==0 in turn_on is not special-cased.
- `async_turn_off`: `[switch False]`; `async_oscillate(bool)`: DP write; `async_set_preset_mode`: mode DP write; `async_set_direction`.
- No speed/mode mutual exclusion logic; no "reset to last speed".

### 3.5 Pure data vs code
Data: category set, dpcode tuples per role, direction map, entity-existence DP set. Fixed algorithms: two-pass speed wrapper choice (int then enum), 1..100 int remap, enum position<->percent (floor and ceiling-ish bounds), percentage 0 -> switch off, turn_on batch ordering. Quirk (data + tiny transform): Duux status string->bool mapping (`fs_dune79w7bsu6dg3e.py`) and enum range patch (`fs_xwv3jifdbhbolgh3.py`).

### 3.5b Requirements for the IL (fan)
- R-F1: Entity existence predicate = "any of a given set of dpcodes appears in function|status|status_range" (`definition/fan.py:53-69`) - distinct from typed lookup (mode alone -> no entity).
- R-F2: Roles with ordered dpcode alternatives incl. a typed two-pass choice for speed (Integer preferred over Enum across the whole list) (`definition/fan.py:80-85`).
- R-F3: Integer speed remap raw[min..max] -> 1..100 (round) and inverse; enum speed <-> percentage by ordered position with the floor formula and "first bound >= p" write rule (`device_wrapper/fan.py`).
- R-F4: `speed_count` = number of enum options for enum speed; default 100 for integer.
- R-F5: Raw-string preset modes (whole enum range).
- R-F6: Oscillation from a single boolean DP (first of horizontal/vertical); the IL must allow a quirk to set which.
- R-F7: Direction enum restricted to {forward, reverse}.
- R-F8: Turn-on as an ordered batch (switch, speed, preset), turn-off, `percentage==0 -> switch off` rule, and "turn_on without switch is a no-op" (`fan.py:128-165`).
- R-F9: Feature flags derived from wrapper presence.
- R-F10: Quirk support: initial-status string->bool mapping ("true"/"false"), enum range redefinition (`fs_dune79w7bsu6dg3e.py`, `fs_xwv3jifdbhbolgh3.py`).

### 3.6 Open questions / surprises (fan)
- The `cs` (dehumidifier) and `kj` (purifier) categories also produce fan entities (e.g. `fan.dehumidifier` with features 48; `fan.d825a_i` with speed from `fan_speed_enum`); a dehumidifier device can therefore have humidifier + fan (+ climate no) entities.
- Snapshot `fan.ceiling_fan_with_light` shows `percentage: None` with state `on` although the fixture `fs_g0ewlb1vmwqljzji` reports `fan_speed`=`1` and range `["1".."6"]` (`test_fan.ambr:214-232`; fixture status): likely a type mismatch (int status vs string enum range -> read validation returns None, `type_information.py:253-274`). Not verified against the fixture's raw JSON type; flagging as probable.
- `oscillate_wrapper` picks only `switch_horizontal` if present, ignoring `switch_vertical` (`definition/fan.py:77-79`), while climate handles both (composite).
- In `fs` fixture the switch DP is named `switch`; `fsd` uses `fan_switch`.
- Speed=0 handling depends on presence of the switch DP.

---------------------------------------------------------------------------
## 4. HUMIDIFIER

Sources: `CORE/humidifier.py`, `HDL/definition/humidifier.py`, `TESTS/test_humidifier.py`, `TESTS/snapshots/test_humidifier.ambr`.

### 4.1 Entity decision
- Table `HUMIDIFIERS` (`humidifier.py:41-56`), one description per category:
  | category | key (unique_id suffix) | switch dpcode(s) | current_humidity | target humidity | device_class |
  |---|---|---|---|---|---|
  | cs | `switch` | `(switch, switch_spray)` | `humidity_indoor` | `dehumidify_set_value` | DEHUMIDIFIER |
  | jsq | `switch` | `(switch, switch_spray)` | `humidity_current` | `humidity_set` | HUMIDIFIER |
- Entity created iff **any** of {switch, switch_spray, current_humidity code, target humidity code} is present in `function|status|status_range` (name-only check, any type) (`HDL/definition/humidifier.py:48-64`; core passes `switch_dpcode=description.dpcode or description.key`, `humidifier.py:73-79`). Otherwise none.
- Wrappers: switch `DPCodeBooleanWrapper` on tuple (prefer_function); `current_humidity` `DPCodeRoundedIntegerWrapper` (status-first, no prefer_function) on `current_humidity_dpcode`; `target_humidity` `DPCodeRoundedIntegerWrapper` (prefer_function); `mode` `DPCodeEnumWrapper` on `"mode"` (prefer_function) (`:70-79`).
- `switch_spray` is the fallback dpcode; `switch` is tried first.

### 4.2 Features / attributes (`humidifier.py:99-150`)
- `min_humidity/max_humidity` = `round(target_wrapper.min_value/max_value)` if wrapper else HA defaults (0/100) (snapshots: `arida_stavern` 0/100 no target; `dehumidifier` 35/70) (`humidifier.py:114-121`).
- `MODES` feature iff `mode` enum exists; `available_modes` = raw enum range (`:123-126`). (No snapshot fixture currently exposes a `mode`; all snapshots show `supported_features: 0`.)
- `is_on` switch; `mode`; `target_humidity`; `current_humidity` from wrappers. `device_class` from table. No `HumidifierAction` (action) reported.
- `_attr_name=None`.

### 4.3 Writes
- `turn_on/off`: `[switch <- True/False]`; if there is no switch wrapper raise `ActionDPCodeNotFoundError` (ServiceValidationError, translation key `action_dpcode_not_found`, placeholders `expected=sorted(dpcodes)`, `available=sorted(device.function.keys())`) (`humidifier.py:152-170`; `CORE/util.py:40-63`; test `TESTS/test_humidifier.py:116-161`).
- `set_humidity(h)`: write target wrapper (integer; `round` rounding via scale rules; range-check against DP min/max) else raise the same error with `expected=[humidity dpcode]` (`:172-180`).
- `set_mode(mode)`: `_async_send_wrapper_updates(mode_wrapper, mode)` -> silently no-op if no wrapper.
- Test: `switch` True, `dehumidify_set_value` 50 (`test_humidifier.py:62-93`).

### 4.4 Requirements for the IL (humidifier)
- R-H1: Category-keyed description with device_class and dpcode alternatives for switch, current humidity, target humidity; entity-existence predicate = any-of dpcode names present (untyped) (`definition/humidifier.py:48-64`).
- R-H2: Rounded integer humidity read/write; entity min/max from target DP (rounded) else 0-100.
- R-H3: Optional mode enum (raw strings) -> MODES feature.
- R-H4: Distinct error semantic when a control DP is missing at action time (raise, with expected/available dpcode names) vs silent no-op for modes (`humidifier.py:152-185`).
- R-H5: Unique-id key can differ from actual dpcode (`switch` vs `switch_spray`) - keep stable IDs.
- R-H6: Quirk-added DPs (e.g. `humidity_indoor` for `uhtamgih7kkdcqtx`, `cs_uhtamgih7kkdcqtx.py`).

### 4.5 Open questions / surprises (humidifier)
- `current_humidity` uses status-first lookup, others use function-first; if a DP is in both with different types/ranges the two searches can differ (`definition/humidifier.py:70-79`).
- Entity is created even if only a `humidity_indoor` sensor DP exists (no switch, no target): then it exists but has no working controls; snapshot `humidifier.arida_stavern` (state `on`? `is_on` from absent switch is None; the snapshot shows state `on`, meaning the fixture does have a switch) - not verified for a device with no switch; the unsupported-action test removes switch and target while `humidity_indoor` remains (`test_humidifier.py:116-161`).
- Same `cs` device also spawns a `fan` entity when it exposes `fan_speed_enum`/`switch` (see 3.6): two entities share the same `switch` DP (unique_ids `tuya.<id>switch` for humidifier vs `tuya.<id>` for fan).
- `HumidifierEntity.action` not implemented.

---------------------------------------------------------------------------
## 5. Cross-cutting: pure-data vs code summary (for the IL boundary)

Pure data (declarable):
- Category -> entity descriptions tables (light 34 category rows incl. aliases; climate 6; fan 6; humidifier 2), with dpcode alternatives, names, translation keys, entity category, device class, placeholders, switch-only HVAC mode.
- Default device-mode -> HVAC mode table; climate temperature-unit alias sets; swing DP names; direction map; work-mode literals `white/colour/music/scene` (`CORE/const.py:71-77`, only `white` and `colour` are used by light).
- Per-product quirks that redefine/add/remove DPs (enum ranges, integer ranges/units, bitmaps) and initial-status mappings; local-strategy enum maps.

Fixed named algorithms (need built-in transforms, no arbitrary code): linear remap w/ rounding & reversal; mired<->kelvin; HSV JSON/hex codecs; enum-position percentage; ambiguity-filtered enum mapping; composite boolean swing; unit selection & conversion; brightness min/max dynamic scaling; first-match ordered dpcode search with function-vs-status precedence and type filter; validation-on-read (None) / rejection-on-write.

Arbitrary/imperative in current implementation: `light.async_turn_on` decision tree, `climate.async_set_hvac_mode`, `fan.async_turn_on/percentage`, quirk `apply_when` lambdas (`_is_fahrenheit_variant`); all are small enough to be expressed as declarative "write recipes" plus a conditional on device status.

## 6. Global open questions
1. The handlers package has `DeviceWrapper.skip_update` and `dp_timestamps` plumbing but none of these four platforms use them; confirm the IL need not model partial-update filtering for them.
2. `*Quirk.definition_fn` hooks exist but are unused in 0.0.29; future versions may make definitions per-product code. The IL may need an escape hatch.
3. Fixtures store DP `value` specs as dicts (or JSON strings in some), while the real SDK supplies JSON **strings** in `DeviceFunction.values`; the IL's DP-metadata ingestion must handle the string form (handlers call `json.loads`, `type_information.py:151-158,234,303`).
4. In a local-MQTT bridge context, DP metadata (`function`/`status_range` with types and ranges, and category) is normally obtained from the Tuya cloud; whether the bridge supplies category and type/range info is outside this slice but is a hard prerequisite (entity selection depends on `device.category`, on DP types, and on min/max/scale/step/unit/enum range).
5. `device.online` availability and the temp-unit conversion depend on host state (`hass.config.units`) - IL must accept host unit system as an input.
