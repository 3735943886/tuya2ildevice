# A. Value-transform / type layer of tuya-device-handlers 0.0.29

Path prefix `H/` = `.../handlers-dl/tuya_device_handlers-0.0.29/src/tuya_device_handlers/`.
All line numbers are from files read in full. `CustomerDevice` = `tuya_sharing.device` (SimpleNamespace subclass; `DeviceFunction{code,desc,name,type,values(json str)}`, `DeviceStatusRange{code,type,values(json str),report_type}` at tuya_sharing/device.py:12-44).

Scope: type_information.py, type_information_ex.py, raw_data_model.py, utils.py, const.py, helpers/*.py, device_wrapper/{__init__,base,common,exception,extended}.py. Other wrappers (sensor.py etc.) were only peeked at where they instantiate the base classes (marked "peek").

## 0. Fundamental architecture (summary)

- Two layers:
  1. **TypeInformation** (type_information.py): parsed schema of one DP (from `device.status_range` / `device.function`) + how to read the current value out of `device.status[dpcode]` and how to validate/serialize an outgoing value.
  2. **DeviceWrapper** (device_wrapper/*): what an entity holds; exposes `read_device_status(device)` and `get_update_commands(device, value) -> list[{"code","value"}]`.
- Everything is keyed by **dpcode (string name)**, not dpid, at runtime. dpid only appears in quirk builder (`override_dpid_type_information_cls(dpid=, dpcode=, ...)` builder/device_quirk.py:397-406), where the dpid is stored as key but lookups (`get_type_information_cls`, builder/device_quirk.py:481-487) match **on dpcode only** (dpid ignored, first match wins).
- Values are read from `device.status: dict[str, Any]` (a dict code -> raw value; cloud/sharing already gives Python bool/int/str; RAW arrives as base64 str; JSON arrives as JSON str). Commands are dicts `{"code": <dpcode>, "value": <raw>}`.
- Nothing in this slice does I/O; all is pure functions over (device.status, device.function, device.status_range) except (a) module-level warning-dedup state `DEVICE_WARNINGS` (const.py:9) and (b) global `TUYA_QUIRKS_REGISTRY` lookup inside `find_dpcode` (type_information.py:86).
- Imports: NO `homeassistant.*` import anywhere in this slice (grep of whole package: only helpers/homeassistant.py is "Copy of Home Assistant constants" — header docstring helpers/homeassistant.py:1 — it is a self-contained StrEnum copy, no HA import, lines 3 `from enum import StrEnum`). Only external dependency is `tuya_sharing.CustomerDevice` (type_information.py:11, base.py:5, common.py:5, extended.py:5, type_information_ex.py:11, diagnostics.py:6) and stdlib.

## 1. Type classes (type_information.py)

### 1.0 DPType enum and type-name normalisation (const.py:33-63)
- `DPType` StrEnum: Bitmap, Boolean, Enum, Integer, Json, Raw, String (const.py:36-42).
- `DPType.try_parse(str)` (const.py:44-52): exact-match, else fallback map `_DPTYPE_MAPPING` (const.py:55-63): `bitmap, bool, enum, json, raw, string, value` (lowercase) -> canonical; `"value"` -> INTEGER. Comment: "ill-formed DPTypes from the cloud". Unknown -> None (so never matches). Note: `"Bool"`/`"Value"` capitalised variants are NOT mapped (only lowercase keys + exact canonical names).
- `DPMode` IntFlag READ=1 WRITE=2 (const.py:26-30) — defined but not used in this slice.
- `ColorTempScale` StrEnum MIRED/KELVIN (const.py:16-23) — not used in this slice (used by light quirks/wrappers elsewhere).
- `CELSIUS_ALIASES`, `FAHRENHEIT_ALIASES` sets (const.py:12-13) — lower-case unit spellings incl. `℃`, `℉`; not used in this slice (consumed elsewhere).

### 1.1 Base `TypeInformation[T]` (type_information.py:43-130) - `@dataclass(kw_only=True)`, ABC
- Fields: `dpcode: str`, `type_data: str` (raw JSON string of `values`, kept verbatim), `report_type: str|None` (from status_range only). Class var `_DPTYPE: DPType` (l.50).
- `_from_json(dpcode, type_data, *, report_type)` (55-64): default builds without parsing `type_data` (so Boolean/Json/Raw/String never inspect `values`). Subclasses may return None (=> "not found").
- `find_dpcode(device, dpcodes, *, prefer_function=False)` (66-118): see 2.2 below. Classmethod on the type class.
- `read_device_value(device) -> T|None` abstract (120-122): reads `device.status[self.dpcode]`, validates, converts.
- `prepare_set_value(device, value) -> Any` (124-130): base raises `NotImplementedError` (so only Boolean/Enum/Integer are writable through TypeInformation; Bitmap, Json, Raw, String have NO write serializer).
- `PrepareSetValueError(ValueError)` (24-25) is the error type for write validation.
- Warning dedup `_should_log_warning(device_id, key)` (28-40): per-device set in global `DEVICE_WARNINGS`; log once per (device, "kind|dpcode|value"). Behavioural side effect only (logging), not part of value semantics.

### 1.2 Per-type classes

| Class (line) | Tuya type | Extra fields | read (raw status -> value) | write (`prepare_set_value`) |
|---|---|---|---|---|
| `BitmapTypeInformation` (133-178), `_DPTYPE=BITMAP` | Bitmap | `label: list[str]` from `values` JSON key `"label"` (l.139,157). `_from_json` returns None if parsed JSON falsy (`{}`/`null`) (151-152); KeyError if "label" missing when non-empty (uncaught). | None if missing; `isinstance(raw,int)` -> return as-is (raw int, NOT decoded into bits/labels) (162-165); else warn-once, None (166-178). NB: `bool` is an int subclass so True/False pass. | not implemented (raises NotImplementedError from base) |
| `BooleanTypeInformation` (181-213) | Boolean | none (`values` ignored) | None if missing; `raw in (True, False)` -> return raw unchanged (198-199) — so `0`/`1` ints (and `0.0/1.0`) pass and are returned as-is, NOT coerced to bool (Python `1 == True`); anything else (e.g. "true", 2) warn-once + None (201-213). | requires `isinstance(value,bool)` else `PrepareSetValueError` (187-192); returns value. (int 1 rejected.) |
| `EnumTypeInformation` (216-274) | Enum | `range: list[str]` from `values` JSON `"range"` (222,240). `_from_json` None if JSON falsy (234-235); KeyError if "range" missing. | None if missing; `raw in self.range` -> raw (258-259) else warn-once + None (261-274). Case-sensitive exact string match. | must be `str` (245-247) and `in self.range` (248-250) else `PrepareSetValueError`; returns as-is. |
| `IntegerTypeInformation` (277-353), `_DPTYPE=INTEGER`, `T=float` | Integer (Tuya "value") | `min,max,scale,step: int`, `unit: str|None=None` (283-287). `_from_json` (297-315): None if JSON falsy; `int(parsed["min"])` etc. for min,max,scale,step — all four REQUIRED else KeyError; `int()` truncates float strings/numbers; `unit=parsed.get("unit")` (313). | None if missing; requires `isinstance(raw,int)` (bool passes; float/str do NOT) AND `min <= raw <= max` (336) -> `scale_value(raw) = raw / 10**scale` (289-291; always float, true division). Otherwise warn-once, None (338-353). Range check is on RAW (unscaled) value. `step` is NOT enforced on read. | must be `int|float` (319-321; bool passes); `scale_value_back(v) = round(v * 10**scale)` (293-295; Python banker's rounding -> int); then `min<=new<=max` else `PrepareSetValueError` (323-328); returns int. Step NOT enforced on write either. |
| `JsonTypeInformation` (356-381) | Json | none | None if missing; `json.loads(raw)` (369) -> dict (typed dict but any JSON value returned); `JSONDecodeError` -> warn-once, None (370-381). NB: `TypeError` (raw already dict/non-str) is NOT caught. | not implemented |
| `RawTypeInformation` (384-407) | Raw | none | None if missing; `base64.b64decode(raw)` -> `bytes` (395) (non-strict: default `validate=False` discards non-alphabet chars); catches `binascii.Error, TypeError` -> warn-once, None (396-407). | not implemented |
| `StringTypeInformation` (410-418) | String | none | returns `device.status.get(dpcode)` verbatim, no type check (418) | not implemented |

### 1.3 Edge-case notes
- Float rounding on read: none; `scale_value` returns e.g. `raw/10**scale` -> may give 0.30000000000000004-type artefacts? `int/int` is correctly-rounded division, so `3/10 = 0.3`; safe. Downstream wrappers may `round()` (extended.py:28, 59).
- Scale is never negative in practice; a negative `scale` would give `10**negative` float (int**negative-int -> float) — untested/unknown.
- Out-of-range read -> value treated as unavailable (None), not clamped (336-353). Same for invalid enum (258-274), invalid bool (198-213), invalid bitmap (164-178).
- Integer `raw` arriving as float (e.g. 23.0) is rejected as invalid (336) — Important for a bridge that may decode DP values via JSON numbers; ints only.
- Enum/Integer schema `type_data` is parsed at every `find_dpcode` call (no cache) (109-113).
- `IntegerTypeInformation._from_json` etc. do not catch `json.JSONDecodeError`/`KeyError`; malformed schemas raise out of `find_dpcode` (surprising; see section 6).

### 1.4 Extended type information (type_information_ex.py)
- `InvertedIntegerTypeInformationEx(IntegerTypeInformation)` (16-47), used by quirks via `override_dpid_type_information_cls` (docstring 24-29; used in devices/cl/*.py, devices/clkg/*.py).
  - read: `scale_value(self.max) - value` (34-39) — inverts about `0..max` NOT `min..max` (ignores `min`; correct only when min == 0). 
  - write: if value is int/float: `super().prepare_set_value(device, scale_value(max) - value)` else falls to super (41-47) which raises for non-numeric.
  - Purpose: pre-inverts so an outer wrapper's inversion cancels (docstring 28-31).

### 1.5 RAW payload parsers (raw_data_model.py) — `ElectricityData` dataclass (8-19)
Fields: `current, power, voltage: float`, optional `reactive_power, apparent_power, power_factor` (None = layout doesn't carry it, comment 16-18).
- `from_bytes(raw: bytes) -> ElectricityData|None` (23-83), big-endian, three layouts:
  - **v1**: `len==17 and raw[0:2]==b"\x01\x0f"` (45); **v2**: `len==18 and raw[0:2]==b"\x02\x0f"` (46). data = `raw[2:17]` (48).
    - voltage `>H` (2B) `/10.0` (50) -> V.
    - current 3B `>L` of `b"\x00"+data[2:5]` (51) — raw integer, unit mA (comment 32; peek sensor.py native_unit "mA", suggested "A").
    - power 3B (52) -> W (peek: native "W", suggested kW). reactive 3B (53), apparent 3B (54) — raw ints (peek units "var"/"VA").
    - power_factor 1B `/100.0` (55).
    - v2 only: sign bitmap byte `raw[17]` (58): bit0 (0x01) negate current, 0x02 power, 0x04 reactive, 0x08 power_factor (59-66); apparent power has no sign bit (comments 42-43).
  - **legacy**: `len(raw) >= 8` (77): voltage `>H raw[0:2]/10.0`, current 3B `raw[2:5]`, power 3B `raw[5:8]` (78-81); optional fields left None. NB: any other length >=8 (including malformed 17/18-byte with a wrong header, or 16 bytes) falls into legacy parse. `<8` bytes -> None (83).
- `from_hex(str)` (85-92): `bytes.fromhex` (ValueError -> None) then `from_bytes` — used for string DPs (`phase_s`, comment 86; peek sensor.py `_ElectricityHexStringWrapper`).
- Units: parser returns raw magnitudes; unit conversion is delegated to wrapper `native_unit`/`suggested_unit` (peek sensor.py:143-215). Note the comment says power "0.001 kW (i.e. W)": returned number is W.

### 1.6 Generic remap helper (utils.py)
`RemapHelper(source_min, source_max, target_min, target_max)` dataclass (9-16):
- `from_type_information(ti, tmin, tmax)` (18-36): source = **scaled** `scale_value(ti.min)` / `scale_value(ti.max)`.
- `from_function_data(function_data: dict, tmin, tmax)` (38-48): source = `function_data["min"/"max"]` (unscaled, from function `values` JSON dict).
- `remap_value(value, from_min, from_max, to_min, to_max, *, reverse=False)` (72-87): if reverse `value = from_max - value + from_min`; result `((value-from_min)/(from_max-from_min))*(to_max-to_min)+to_min`. Linear, no clamping, ZeroDivisionError if from_max==from_min. Float result, caller rounds.
- `remap_value_to(v, reverse)` source->target (50-59); `remap_value_from(v, reverse)` target->source (61-70): NB reverse in `remap_value_from` reverses within *target* range (from_min/from_max = target) — symmetric so consistent inverse.

## 2. DeviceWrapper classes

### 2.1 `DeviceWrapper[T]` base (device_wrapper/base.py:8-47)
Class-level metadata attributes (declared, some without defaults): `native_unit=None`, `suggested_unit=None` (11-12); `max_value, min_value, value_step: float` (14-16, no default: only meaningful for number-ish wrappers); `options: list[str]` (18, no default; select-ish wrappers).
Methods:
- `initialize(device) -> None` (20-25): hook "called when the entity is added to Home Assistant"; default no-op. (Lets wrappers do one-time stateful init from device.)
- `skip_update(device, updated_status_properties: list[str], dp_timestamps: dict[str,int]|None=None) -> bool` (27-37): default `True` (always skip) unless overridden. `dp_timestamps` = per-dp timestamp map (units not defined here; type `int`). Core entities call `not wrapper.skip_update(...)` in their `_handle_state_update` (core binary_sensor.py:511-519, sensor.py:1948-1955, switch.py:1010-1018, number.py:641-649, select.py:447-455, event.py:171-180, siren.py:120-127).
- `read_device_status(device) -> T|None` (39-41): NotImplementedError. Contract: convert device status into HA-domain value; None == unknown/unavailable.
- `get_update_commands(device, value: T) -> list[dict[str, Any]]` (43-47): NotImplementedError. Contract: return a LIST of command dicts (`{"code": str, "value": raw}`), so one write can produce several dp writes. Core entities concatenate lists from several wrappers (core climate.py:247-257, cover.py:264-281, fan.py:154-163, light.py:487-542, services.py:100-128).

### 2.2 `DPCodeWrapper[T]` (common.py:23-82) — single dpcode
- `__init__(dpcode)` (30-32).
- `skip_update` override (34-45): `dpcode not in updated_status_properties` (so an update is processed only if this dp was among the changed props; `None` list would raise TypeError — the docstring says "if not given" but code does `in None` -> TypeError; **surprising**, unless callers always pass a list; `dp_timestamps` unused).
- `read_device_status` -> `_read_dpcode_value(device)` (47-49); `_read_dpcode_value` base returns raw `device.status.get(dpcode)` (51-58).
- `_convert_value_to_raw_value(device, value)` NotImplementedError (60-68).
- `get_update_commands` (70-82): exactly one command `[{"code": dpcode, "value": _convert_value_to_raw_value(...)}]`. Subclasses override to emit multiple.

### 2.3 `DPCodeTypeInformationWrapper[TypeInformationT, UnderlyingT, T]` (common.py:85-129)
- Holds `type_information` (98); class var `_DPTYPE: type[TypeInformationT]` (92) selects which TypeInformation class to look up.
- `find_dpcode(device, dpcodes, *, prefer_function=False) -> Self|None` (100-116): delegates to `cls._DPTYPE.find_dpcode(...)` and, if found, builds wrapper with `dpcode=type_information.dpcode` (i.e. the **first candidate code that resolved**, common.py:112-115).
- `_read_dpcode_value` -> `type_information.read_device_value(device)` (118-120): validated+scaled value (UnderlyingT).
- `_convert_value_to_raw_value` -> `type_information.prepare_set_value`, translating `PrepareSetValueError` to `SetValueOutOfRangeError` (122-129; exception.py:4-5, subclass of ValueError).
- T (HA-side type) may differ from UnderlyingT (type param design): subclasses map underlying <-> HA.

**dpcode alternatives resolution (`TypeInformation.find_dpcode`, type_information.py:66-118):**
1. `dpcodes` None -> None; a str is wrapped into 1-tuple (75-79).
2. Lookup order of the two schema dicts: `(status_range, function)` by default, `(function, status_range)` if `prefer_function` (81-85). "status_range" first = the *read* schema, "function" = the *write* schema.
3. Quirk fetched once per call: `TUYA_QUIRKS_REGISTRY.get_quirk_for_device(device)` (86). 
4. For each candidate dpcode **in tuple order** (88): `report_type` taken from `device.status_range[dpcode].report_type` only (89-93; None if dpcode not in status_range, even if found in function); quirk may swap the type class for that dpcode (`quirk.get_type_information_cls(dpcode=...)` 95-98); then for each schema dict in lookup order, accept if entry exists AND `DPType.try_parse(entry.type) is type_cls._DPTYPE` (103-105) AND `_from_json(...)` returns non-None (107-115). First success returns.
5. Precedence: candidate code order beats dict order (i.e. code1 in function only wins over code2 in status_range).
6. Type mismatch (e.g. dpcode is Integer but wrapper wants Enum) => silently skipped, tries next candidate; whole thing returns None if none matches.
7. **Only `device.status_range` and `device.function` are consulted** for schema; `device.status` only for values; `device.local_strategy` is NOT consulted anywhere in this slice (only dumped in helpers/diagnostics.py:60, 77-78). No dpid <-> dpcode mapping consulted (no `local_strategy[dpid]` lookup) in this slice.
- **Availability**: no explicit "available" concept; `read_*` returning None = unknown. Wrapper *existence* (find_dpcode not None) determines entity creation. `device.online` is not consulted here.

### 2.4 Simple typed wrappers (common.py:132-205)
| Class | `_DPTYPE` | Extra behaviour |
|---|---|---|
| `DPCodeBitmapWrapper[T=int]` (132-137) | Bitmap | none |
| `DPCodeBooleanWrapper[T=bool]` (140-145) | Boolean | none |
| `DPCodeEnumWrapper[T=str]` (148-161) | Enum | `options = type_information.range` (161) (same list object, not copied) |
| `DPCodeIntegerWrapper[T=float]` (164-181) | Integer | sets `native_unit = ti.unit` (176), `min_value/max_value = scale_value(min/max)` (177-178), `value_step = scale_value(step)` (179-181) (step scaled, not a multiple relative to min) |
| `DPCodeJsonWrapper[T=dict]` (184-189) | Json | none |
| `DPCodeRawWrapper[T=bytes]` (192-197) | Raw | none |
| `DPCodeStringWrapper[T=str]` (200-205) | String | none |
Read = validated value from TypeInformation; write = TypeInformation.prepare_set_value (so Bitmap/Json/Raw/String wrappers raise NotImplementedError on write unless overridden).

### 2.5 Extended wrappers (device_wrapper/extended.py)
- `DPCodeRoundedIntegerWrapper` (21-28): read = `round(scaled_value)` -> int (26-28). Write inherited (Integer: `round(v*10**scale)` then range check).
- `DPCodeRemappedIntegerWrapper` (31-73): ctor takes `target_min,target_max` (kw) and builds `RemapHelper.from_type_information` (46-48) — uses scaled ranges. Hook `_remap_inverted(device)->bool` (default False) (50-52) — takes `device` so an override could choose inversion per device. read: `round(remap_value_to(value, reverse=inv))` (54-63). write: `super()._convert_value_to_raw_value(device, remap_value_from(value, reverse=inv))` (65-73) i.e. HA value -> unscaled-float in source range -> Integer.prepare_set_value (scale back, round, range check -> `SetValueOutOfRangeError`).
- `DPCodePercentageWrapper` (76-83): target 0..100.
- `DPCodeInvertedPercentageWrapper` (86-91): `_remap_inverted -> True` (89-91) => raw min <-> 100, raw max <-> 0.
- `DPCodeInvertedBooleanWrapper` (94-106): read `not value` (99-101); write `not super()._convert_value_to_raw_value(...)` (103-106; validation runs on the HA value before inversion, requires bool).
- `DPCodeJsonDictAttributeWrapper[T=float]` (109-146): reads one key `_ATTRIBUTE_NAME` from a Json DP (`status.get(name)`) (142-146). Custom `find_dpcode` (118-140): wrapper found only if the current status is None (not reported yet -> assume supported) or the attribute key is in the current dict (131-140). **Discovery depends on live status**. Write: not implemented (inherits Json; NotImplemented).
- `DPCodeParsedAttributeWrapper[UnderlyingT, TypeInformationT, ParsedT, T=float]` (149-202): reads one attribute of a parsed payload: `_parse(raw_value)` classmethod (NotImplemented default) (163-166); read `getattr(parsed, _ATTRIBUTE_NAME)` (196-202); `find_dpcode` (168-194): found if no payload yet (`not raw_value`, covers None/empty bytes/empty str, l.188-189), or parse OK and attribute not None (190-194). Write: not implemented.
  Concrete uses (peek device_wrapper/sensor.py:101-215): six JSON attrs (`electricCurrent` A, `power` kW, `voltage` V, `reactivePower` kvar, `apparentPower` kVA, `powerFactor`), six RAW (`current` mA->A, `power` W->kW, `voltage` V, `reactive_power` var->kvar, `apparent_power` VA->kVA, `power_factor`), and hex-string variants.

### 2.6 Read-from-one-write-to-many
- Contract-level: `get_update_commands` returns a `list` (base.py:43-47) so a single wrapper *can* emit many commands and can address dpcodes other than the one it reads; the shipped `DPCodeWrapper.get_update_commands` emits exactly one, on the same dpcode (common.py:77-82). Multi-dp writes in the wrappers of this slice: none. In core, multi-dp writes are composed at the entity level by concatenating several wrappers' command lists (see refs at 2.1). Other-slice wrappers (climate.py, cover.py, light.py, etc. in device_wrapper/) not read by me — may implement multi-dp writes; I did not verify.
- Read side asymmetry: a wrapper may read one dp (`_read_dpcode_value`) while `skip_update` (default) only looks at its own dpcode. Wrappers with state (peek: sensor.py accumulator with `read_device_status` returning `_accumulated_value`, ~l.95-98) override `skip_update`/`initialize`.

## 3. Helpers
- helpers/utils.py:6-17 `parse_enum(enum_class, value)` -> member or None (None/invalid -> None). HA-independent.
- helpers/homeassistant.py — "Copy of Home Assistant constants" (l.1): pure `StrEnum` copies with NO homeassistant import. Classes: `TuyaEntityCategory` (6; config/diagnostic), `TuyaAlarmControlPanelState` (22), `TuyaAlarmControlPanelAction` (37), `TuyaBinarySensorDeviceClass` (46), `TuyaClimateHVACMode` (134), `TuyaClimateSwingMode` (160), `TuyaCoverAction` (170), `TuyaCoverDeviceClass` (178), `TuyaEventDeviceClass` (194), `TuyaFanDirection` (202), `TuyaHumidifierDeviceClass` (209), `TuyaNumberDeviceClass` (216, large), `TuyaSensorDeviceClass` (622, large), `TuyaSensorStateClass` (1051), `TuyaSwitchDeviceClass` (1071), `TuyaUnitOfTemperature` (1078), `TuyaVacuumActivity` (1085), `TuyaVacuumAction` (1096), `TuyaValveDeviceClass` (1106). These are the HA-vocabulary the definitions refer to; a rustuya IL must carry the same vocabulary (or a mapping to it).
- helpers/__init__.py:3-20 re-exports 6 of them + `parse_enum`.
- helpers/diagnostics.py:37-84 `customer_device_as_dict(device)`: dumps id,name,category,product_id,product_name,online,sub,time_zone, active/create/update_time (as ISO from epoch seconds, `dt.datetime.fromtimestamp(..., tz=UTC)` 50-58), `function` and `status_range` in normalised form, `local_strategy`, `status`, `set_up`, `support_local`, the matching quirk `file:line`, `DEVICE_WARNINGS`, and quirk `original_category/function/local_strategy/status_range` if present (70-82). Depends on `TUYA_QUIRKS_REGISTRY` (globals), `tuya_sharing` types. Also reveals which CustomerDevice fields matter: id, name, category, product_id, product_name, online, sub, time_zone, active_time, create_time, update_time, function, local_strategy, status_range, status, set_up, support_local.
- `__init__.py:9` `TUYA_QUIRKS_REGISTRY = QuirksRegistry()` global singleton (registry.py `__new__` singleton, ~l.60).

## 4. Transform kinds table (what an IL must express declaratively)

| # | Transform | Direction | Evidence |
|---|---|---|---|
| T1 | DP type-name normalisation (`Integer`/`value`, `Boolean`/`bool`, lowercase aliases) | schema | const.py:44-63 |
| T2 | Integer scale: `raw / 10**scale` read; `round(v*10**scale)` write (banker's rounding) | R/W | type_information.py:289-295 |
| T3 | Integer raw range validation [min,max] on read (out-of-range/non-int -> None) and on write (error) | R/W | type_information.py:336, 323-328 |
| T4 | Integer metadata -> unit, scaled min/max/step | schema | type_information.py:283-287; common.py:176-181 |
| T5 | Enum membership validation (read -> None if outside range; write error) | R/W | type_information.py:248-250, 258-259 |
| T6 | Boolean validation (read accepts True/False/0/1; write requires bool) | R/W | type_information.py:198-199, 189-192 |
| T7 | Boolean inversion (`not`) | R/W | extended.py:97-106 |
| T8 | Integer inversion within `0..max` (raw-level pre-inversion; ignores min) | R/W | type_information_ex.py:34-47 |
| T9 | Linear range remap source(min,max scaled) <-> target (e.g. 0..100), optional reverse, round to int on read | R/W | utils.py:72-87; extended.py:31-73, 76-91 |
| T10 | Round scaled float to int on read | R | extended.py:21-28 |
| T11 | Bitmap raw int passthrough (label list kept; bit->label decode is not here) | R | type_information.py:139-165 |
| T12 | JSON string -> dict (`json.loads`) | R | type_information.py:369 |
| T13 | JSON dict key extraction (attribute), with support probing at discovery | R | extended.py:109-146 |
| T14 | Base64 -> bytes | R | type_information.py:395 |
| T15 | Binary struct decode: big-endian uint16/uint24/uint8 fields, scale 0.1/0.01, signed via sign-bitmap, versioned by header bytes and length (electricity) | R | raw_data_model.py:45-83 |
| T16 | Hex string -> bytes -> same binary decode | R | raw_data_model.py:85-92 |
| T17 | Parsed-payload attribute extraction (`getattr(parsed, name)`), attribute-may-be-None support probing | R | extended.py:149-202 |
| T18 | Native unit + suggested unit declaration (mA->A, W->kW, var->kvar, VA->kVA, plus Integer.unit) | meta | common.py:176; peek sensor.py:145-215 |
| T19 | dpcode candidate tuple, first-match across (status_range,function) with type check; `prefer_function` toggles source-of-schema order | discovery | type_information.py:66-118 |
| T20 | Per-device TypeInformation class override by dpcode (quirk) | discovery | type_information.py:95-98; builder/device_quirk.py:397-406, 481-487 |
| T21 | Update-skipping predicate: only handle when this dpcode changed | events | common.py:34-45; base.py:27-37 |
| T22 | Write => list of `{"code","value"}` commands; validation error -> `SetValueOutOfRangeError` | W | base.py:43-47; common.py:70-82,122-129 |
| T23 | report_type (from status_range) captured for delta/sum handling | schema | type_information.py:53,89-93 (consumer: sensor accumulation, peek sensor.py ~95-98, not verified in detail) |
| T24 | Unit / temp aliasing sets | meta | const.py:12-13 (consumer elsewhere) |
| T25 | Timestamps: `dp_timestamps` passed to skip_update but unused in this slice (default impls ignore) | events | base.py:30-37; common.py:34-45 |

Not present in this slice (so NOT evidenced here): unit conversion arithmetic (no unit math), timestamp parsing, string-to-number parsing, color conversion (HSV/RGB), bit-testing of bitmap labels, enum<->HA-string mapping (these enum maps live in definition/* and other wrappers, other slices).

## 5. Things that are code, not data

1. `TypeInformation.find_dpcode` is procedural: tuple iteration, dict-order switch, type-class swap by quirk, per-call side lookup in a global registry (type_information.py:66-118). Expressible as data (candidate list + prefer_function + type), except the quirk override hook.
2. Quirk-supplied *classes* as overrides (`override_dpid_type_information_cls(type_information_cls=<Python class>)`, builder/device_quirk.py:397-406; get_type_information_cls 481-487): arbitrary subclass of TypeInformation with custom `read_device_value`/`prepare_set_value` (e.g. InvertedIntegerTypeInformationEx type_information_ex.py:17-47). In the IL this must become a closed set of named transforms ("invert-within-range" etc.); any quirk with a novel subclass is code.
3. `DPCodeJsonDictAttributeWrapper.find_dpcode` / `DPCodeParsedAttributeWrapper.find_dpcode`: entity *existence* depends on live device status contents (extended.py:118-140, 168-194). Requires runtime probing in the IL ("present-if-key-in-current-payload-or-no-payload-yet").
4. `ElectricityData.from_bytes`: length/header-sniffing multi-format binary parser with sign bitmap (raw_data_model.py:45-83). A declarative binary-layout DSL with (version selector by header+len, fallback layout by min length, sign bitmask) would be needed; otherwise keep as named built-in parser.
5. `_remap_inverted(device)` hook takes `device` (extended.py:50-52), theoretically allowing per-device dynamic inversion; the shipped subclasses return constants (89-91). Other wrappers (not in slice) might use device state.
6. Stateful wrappers: `initialize(device)` hook (base.py:20-25) and custom `skip_update` w/ `dp_timestamps` (base.py:27-37); peek sensor.py accumulator holds `_accumulated_value` across updates (delta report_type). State across events = code, not pure function of status.
7. Per-device warn-once logging with global state (type_information.py:28-40; const.py:9) — non-semantic, can be dropped.
8. Quirk-provided `initialise_device(device)` mutating the CustomerDevice (registry.py DeviceQuirkProtocol ~l.40-44; diagnostics expects `original_*` attrs, helpers/diagnostics.py:70-82) — quirks rewrite `function`/`status_range`/`local_strategy`/`category` on the device model before wrappers see it. So "IL value semantics" are always relative to a possibly-patched schema (only the interface is seen from this slice; builder internals outside my slice).
9. Error semantics via exceptions (PrepareSetValueError -> SetValueOutOfRangeError, common.py:126-129) vs None returns on the read side.

## 6. Open questions / surprises

1. **Boolean read returns raw non-bool** for 0/1/0.0/1.0 (`raw in (True, False)` then `return raw`, type_information.py:198-199); `not value` in inverted wrapper then yields proper bool, but non-inverted paths may leak int 0/1. Probably not intended to matter for HA.
2. **Integer read rejects floats** (`isinstance(raw,int)`, l.336) — a bridge that yields float for integer DP would be flagged invalid; but accepts `bool` (l.336) as int.
3. **Integer write range check happens after scaling but `step` is never enforced** (323-328); `_from_json` requires all of min,max,scale,step (309-312) and would KeyError if a cloud schema omits `step`. Not caught (no try/except) -> exception propagates out of `find_dpcode`. Same for Enum "range"/Bitmap "label" KeyError and invalid JSON (`json.loads` uncaught, e.g. empty string `values`).
4. `InvertedIntegerTypeInformationEx` inverts against `0..max` (type_information_ex.py:39, 46), not `min..max`; wrong if min != 0.
5. `DPCodeWrapper.skip_update` docstring says "skip if updated_status_properties is not given", but code `self.dpcode not in updated_status_properties` (common.py:45) would raise TypeError on None — depends on caller; core always passes a list (core coordinator.py:101-117 passes through; I didn't verify list-ness of every caller).
6. `JsonTypeInformation.read_device_value` only catches JSONDecodeError; if the bridge supplies an already-decoded dict, `json.loads(dict)` raises TypeError (uncaught) (369-370). `RawTypeInformation` accepts `TypeError` (396) but the JSON one does not. Also declared return type is `dict` but JSON arrays/scalars would pass through.
7. `RawTypeInformation` uses non-strict `b64decode` (395): junk characters silently dropped; a local (non-cloud) bridge that supplies raw hex/bytes rather than base64 would be mis-decoded or errors.
8. Quirk override matches by dpcode only, dpid ignored, first match (builder/device_quirk.py:481-487) — two overrides with same dpcode but different dpid collide silently (probably fine).
9. `report_type` only from `status_range` (type_information.py:89-93), even when schema found via `function`; a dpcode that is function-only gets `report_type=None`. Consumer semantic (`sum`/`minux`/`un_known` per tuya_sharing docstring device.py:36-37 — "minux" typo is upstream) not verified in this slice.
10. `step` scaling: `value_step = scale_value(step)` (common.py:179-181) — HA number step; no relation to `min` origin.
11. `DPCodeRemappedIntegerWrapper` computes float remap then Integer.prepare_set_value rounds via `round()` (banker's rounding), and read side rounds to int -> round-trip lossy for coarse ranges; e.g. 0..100 percentage over raw 0..1000 loses resolution (extended.py:54-73).
12. `local_strategy` (dpid->dpcode, `dp_id`, `config_item` incl. `statusFormat`, `valueDesc`, `valueType`, `pid`) is never read here: this layer is dpcode/cloud-schema centric. For a local-bridge integration the IL will need its own dpid<->dpcode map and its own schema source, since `function`/`status_range` come from the cloud. (Field contents of local_strategy in tuya_sharing not verified in this slice.)
13. Wrapper `T` type params vs `UnderlyingT`: HA-side enum/str mapping of enum values (e.g. Tuya enum string -> HA hvac mode) is NOT in the base/common/extended wrappers of this slice — it is in per-platform wrappers (climate.py, cover.py, ...), belonging to other catalog slices.
14. No handling of: Bitmap bit->label decoding (label list is stored but unused in this slice: type_information.py:139), Json/Raw/String writes, timestamps, string-to-number, or multi-dpcode reads within a single base wrapper.
