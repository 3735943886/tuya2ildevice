# F. Core cross-cutting catalog (HA core `tuya` + `tuya-device-handlers` 0.0.29)

Path abbreviations (all paths below relative to these roots):
- `CORE/` = `.../core-analysis/core/homeassistant/components/tuya/`
- `TESTS/` = `.../core-analysis/core/tests/components/tuya/`
- `H/` = `.../core-analysis/handlers-dl/tuya_device_handlers-0.0.29/src/tuya_device_handlers/`
- `SDK/` = `.../core-analysis/sdk/x/tuya_sharing/` (tuya-device-sharing-sdk 0.2.15, downloaded by me because core delegates all wire behaviour to it; NOT in the original brief but essential, see 3.5)

Line numbers are real file lines (verified). "Unknown" = not determinable from the sources I read.

---
## 1. entity.py: the base entity contract (`CORE/entity.py`, 115 lines)

### 1.1 Construction
- `TuyaEntityDescription(EntityDescription)`, frozen dataclass with **no extra fields** (entity.py:14-17). Each platform subclasses it.
- `TuyaEntity(Entity)`: `_attr_has_entity_name = True`, `_attr_should_poll = False` (entity.py:23-26). So entity friendly name = device name + entity name, push-only.
- `__init__(device, device_manager, description)` (entity.py:28-41):
  - `device_info = DeviceInfo(identifiers={(DOMAIN, device.id)})` (l.35): only the identifier; full info (manufacturer/model/name) comes from the device-registry write in `util.get_device_info` (see 1.5). **No `via_device`, no connections, no sw_version, no area/suggested_area.**
  - `unique_id = f"tuya.{device.id}{description.key}"` (l.36). Plain string concat, **no separator** between device id and description key (ambiguity risk; e.g. test id `tuya.pykascx9yfqrxtbgzcchild_lock` in `TESTS/snapshots/test_switch.ambr`). The `tuya.` prefix is intentionally kept for legacy reasons (pylint disable comment). `description.key` is normally a DPCode string, but some descriptions use a synthetic key (e.g. `child_lock`, `switch_1`) - see platform analyses.
  - `device.set_up = True` (l.39): side effect on the SDK model; "TuyaEntity initialize mq can subscribe". The SDK only subscribes to MQ topics for devices with `set_up` truthy (`SDK/manager.py:87-91`). Cloud-only concept.
  - Stores `self.device`, `self.device_manager` (l.40-41).

### 1.2 Fields of CustomerDevice read by entity.py itself
Only: `device.id` (unique_id, dispatcher signal, logs), `device.online` (availability), `device.set_up` (write). Everything else is read by wrappers/handlers (see 1.7).

### 1.3 Availability
`available -> self.device.online` (entity.py:44-47). Online/offline is a plain bool mutated by the SDK (`SDK/manager.py:245-250`). No per-DP availability, no staleness/timeout logic in core.

### 1.4 Live updates
- `async_added_to_hass` connects dispatcher signal `f"{TUYA_HA_SIGNAL_UPDATE_ENTITY}_{device.id}"` (entity.py:49-58), constant `TUYA_HA_SIGNAL_UPDATE_ENTITY = "tuya_entry_update"` (`CORE/const.py:38`). Signal is **per device**, all entities of the device receive every update.
- Handler `_handle_state_update(updated_status_properties: list[str]|None, dp_timestamps: dict[str,int]|None)` (entity.py:60-78):
  - `updated_status_properties is None` => always `async_write_ha_state()` (online/offline/name update - "nothing to process", comment l.68-73).
  - else `await self._process_device_update(props, ts)` decides; default returns True (entity.py:80-91). Platform entities that use a wrapper override it with `return not wrapper.skip_update(device, props, ts)` (switch.py:1010-1018, select.py:447-455, number.py:641-649, event.py:171-179, binary_sensor.py:511-519, valve.py:149-157, siren.py:120-128, sensor.py:1945-1957). Platforms not in that list (climate, cover, fan, light, humidifier, vacuum, alarm, camera, button) use the default => write state on any signal, whatever DP changed.
- `updated_status_properties` = list of **DP codes (strings)** that changed, `dp_timestamps` = `{dpcode: epoch_ms}` (only populated on the local-strategy path, `SDK/manager.py:211-212`; empty dict `{}` on the cloud-code path, l.172,221). The state values themselves are NOT in the signal: the SDK has already mutated `device.status[code] = value` (SDK/manager.py:209, 218) before dispatching; entities re-read `device.status`.
- Default `DeviceWrapper.skip_update` returns True (base.py:27-37) but `DPCodeWrapper.skip_update` returns `dpcode not in updated_status_properties` (common.py:34-45). `DeltaIntegerWrapper.skip_update` (sensor.py:62-89 in `H/device_wrapper/`) is **stateful**: accumulates `_accumulated_value += float(raw)` when `dp_timestamps[dpcode]` is new (dedupes on `_last_dp_timestamp`), and `read_device_status` returns the accumulator (used for DPs whose `status_range.report_type == "sum"`; see `TypeInformation.report_type`, type_information.py:44-54). So dp_timestamps and report_type carry real semantics: incremental energy counters.
- `wrapper.initialize(device)` exists on the base class (base.py:20-25) with docstring "Called when the entity is added" but **nothing in core or handlers calls it** (grep for `.initialize(` in CORE and H found nothing except SDK-unrelated). Treat as dead API in 0.0.29.

### 1.5 Device registry info (`CORE/util.py:66-86`, called from coordinator.py:152-155)
`get_device_info(device)`: manufacturer `"Tuya"`, `model=device.product_name`, `model_id=device.product_id`, `name=device.name`, identifiers `{(tuya, device.id)}`. If a quirk is registered for `device.product_id` **and `quirk.manufacturer` is truthy**, manufacturer/model/model_id are replaced by the quirk's values (util.py:72-79; "If the manufacturer is not set, we cannot trust the model/model_id"). Test `TESTS/test_init.py:210-246`.

### 1.6 Sending commands
- `_async_send_commands(commands: list[dict])` (entity.py:93-99): logs, **no-ops on empty list**, then `hass.async_add_executor_job(device_manager.send_commands, device.id, commands)`. Command format = list of `{"code": <dpcode>, "value": <raw value>}` (common.py:65-80 in `H/device_wrapper/`). The SDK posts these to the cloud `/v1.1/m/thing/{id}/commands` (SDK/device.py:211-215) and **rate-limits/dedupes**: identical command list for the same device within 10 s is silently dropped (`Filter`, SDK/device.py:227-261). No optimistic state update in core: entity state only changes when an MQ report comes back.
- `_read_wrapper(wrapper)` (entity.py:101-105): `wrapper.read_device_status(device) if wrapper else None`.
- `_async_send_wrapper_updates(wrapper, value)` (entity.py:107-115): `wrapper.get_update_commands(device, value)` -> `_async_send_commands`. `None` wrapper => silent no-op.
- Wrapper protocol (`H/device_wrapper/base.py:8-47`): `DeviceWrapper[T]` with class attrs `native_unit`, `suggested_unit`, `max_value`, `min_value`, `value_step`, `options`; methods `initialize`, `skip_update`, `read_device_status(device)->T|None`, `get_update_commands(device, value)->list[dict]`. Wrappers are **pure functions of (CustomerDevice, value)** except stateful ones (Delta accumulator; possibly others - not audited in my slice).

### 1.7 Name / translation
- Names are `_attr_has_entity_name=True`; per-entity `name`/`translation_key` come from the platform description (e.g. number.py:44-91). Main-device entities set `_attr_name = None` (e.g. alarm_control_panel.py:97, scene.py:32).
- `strings.json` `entity.<platform>.<translation_key>.name` (+ `state`/`state_attributes` for select/sensor enums) holds the English text; counts of translation keys per platform: binary_sensor 23, button 8, cover 4, event 3, light 4, number 44, select 41, sensor 119, siren 1, switch 60, valve 2 (measured from `CORE/strings.json`). `icons.json` has entries only for binary_sensor 6, button 6, number 18, select 21, sensor 27, switch 45 (keyed by the same translation_key; measured). `strings.json` top-level keys: config, entity, exceptions, selector, services. **Implication: entity metadata is keyed by `(platform, translation_key)`; the IL should carry translation_key (and device_class / entity_category / unit) rather than English names.**
- `ActionDPCodeNotFoundError` (util.py:40-63) formats "expected one of {expected} in {available}" where available=`sorted(device.function.keys())`.

### 1.8 Non-device-backed entity
`scene.py`: `TuyaSceneEntity` does NOT derive from `TuyaEntity`. `unique_id = f"tys{scene.scene_id}"` (l.37), device_info entry_type SERVICE with model "Tuya Scene" (l.43-51), `available = scene.enabled`, activation via `manager.trigger_scene(home_id, scene_id)` (l.60-62); list obtained via `manager.query_scenes` (l.23). **Cloud-only.**

---
## 2. const.py (`CORE/const.py`, 1229 lines)

| Item | Where | Count / content |
|---|---|---|
| `DOMAIN="tuya"`, `LOGGER` | :26-27 | |
| Config keys `CONF_ENDPOINT/TERMINAL_ID/TOKEN_INFO/USER_CODE` | :29-32 | cloud auth |
| `TUYA_CLIENT_ID="HA_3y9q4ak7g4ephrvke"`, `TUYA_SCHEMA="haauthorize"` | :34-35 | cloud OAuth-ish (QR) |
| `TUYA_DISCOVERY_NEW="tuya_discovery_new"`, `TUYA_HA_SIGNAL_UPDATE_ENTITY="tuya_entry_update"` | :37-38 | dispatcher signals |
| `TUYA_RESPONSE_*` | :40-44 | cloud login response keys |
| `CELSIUS_ALIASES={"°c","c","celsius","℃"}`, `FAHRENHEIT_ALIASES` | :46-47 | duplicated in `H/const.py:78-79` |
| `PLATFORMS` | :49-68 | **18 platforms**: alarm_control_panel, binary_sensor, button, camera, climate, cover, event, fan, humidifier, light, number, scene, select, sensor, siren, switch, vacuum, valve |
| `WorkMode(StrEnum)` | :71-77 | 4: colour, music, scene, white |
| `DeviceCategory(StrEnum)` | :80-586 | **139 members, 139 distinct values** (AST-counted) |
| `DPCode(StrEnum)` | :587-1018 | **415 members, 414 distinct values** (one duplicate value: `filter`); values are plain lowercase strings, many with comments (typos "atmospheric_pressture" preserved from Tuya API, :609) |
| `UnitOfMeasurement` dataclass (`unit`, `device_classes:set`, `aliases:set`) | :1021-1028 | |
| `UNITS` tuple | :1034-~1215 | table of unit + aliases + which SensorDeviceClasses accept it (e.g. "%" aliases `pct`,`percent`,`% RH`). |
| `DEVICE_CLASS_UNITS: dict[device_class, dict[unit_or_alias, UnitOfMeasurement]]` | :1224-1229 | derived at import from UNITS |

DeviceCategory values (139): amy bgl bh bx bxx cjkg ckmkzq ckqdkg cl clkg cn co2bj cobj cs cwtswsq cwwqfsq cwwsq cwysj cz dbl dc dcl dd dgnbj dj dlq dr ds fs fsd fwd ggq gyd gyms hotelms hps js jsq jtmsbh jtmspro jwbj kfj kg kj kqzg kt ktkzq ldcg liliao lyj mal mb mc mcs mg mjj mk ms ms_category msp mzj nnq ntq pc photolock pir pm2.5 qn rqbj rs sb sd sf sgbj sj sos sp sz tgkg tgq tnq tracker ts tyndj tyy tzc1 videolock wk wsdcg xdd xfj xxj xy yb yg ykq ylcg ywbj zd zndb znfh znsb znyh aqcz bzyd cwjwq dghsxj dsd fskg hcdd hjjcy hxd jdcljqr jqbj ks mbd qccdz qjdcz qxj sfkzq sjz szjcy szjqr swtz tdq tyd voc wg2 wkcz wkf wnykq wxkg xnyjcn ywcgq znnbq znjdq zwjcy znjxs znrb.

Facts:
- Enum is documentation-ish: platform tables use `DeviceCategory.X` as dict keys, looked up with `device.category` (a plain str; StrEnum hashes equal). **Categories not in the enum still work if they appear as str keys, but none is used** - the enum is not enforced (`.get(device.category)`).
- Categories referenced by at least one platform table: **98 of 139**. Unreferenced (41): AMY BGL BX BXX CKQDKG CWTSWSQ CWWQFSQ DCL DS GYMS HOTELMS JS JTMSBH JTMSPRO KQZG KTKZQ LILIAO LYJ MB MG MJJ MS MS_CATEGORY NNQ NTQ PHOTOLOCK SB SF TNQ TRACKER TS TYY TZC1 VIDEOLOCK XFJ XY YB YG ZNFH ZNSB ZNYH (regex-derived from `DeviceCategory\.X` references in all non-const files).
- Per-platform count of distinct categories referenced: alarm 2, binary_sensor 28, button 4, camera 2, climate 6, cover 4, event 2, fan 6, humidifier 2, light 34, number 29, select 28, sensor 63, siren 5, switch 53, vacuum 1, valve 1.
- 11 categories occur in test fixtures but are **absent from the enum**: zjq ygsb ydkt xktyd wxnbq wsdykq wfcon(x3) qt infrared_tv(x2) infrared_ac(x2) hwsb. They yield no entities except when reached via quirk `override_category` (only 2 quirks do that, see 3.3). 63 enum categories have no fixture.
- There is **no dp-code alias/grouping structure in const.py** beyond the temperature/unit alias sets. DP-code fallback lists ("first present of (A, B, C)") live in platform description tables as `tuple[DPCode,...]` and are resolved by `TypeInformation.find_dpcode` (`H/type_information.py:67-118`): iterates the tuple in order, looks in `status_range` then `function` (or reverse with `prefer_function`), accepts the first whose `DPType.try_parse(type)` matches the wrapper's expected type.
- Other tables the classifier needs (owned by other slices but they are the actual "data"): per-platform `dict[DeviceCategory -> description(s)]`: `ALARM`, `BINARY_SENSORS`, `BUTTONS`, `CAMERAS`, `CLIMATE_DESCRIPTIONS`, `COVERS`, `EVENTS`, `FANS`, `HUMIDIFIERS`, `LIGHTS`, `NUMBERS`, `SELECTS`, `SENSORS`, `SIRENS`, `SWITCHES`, `VACUUMS`, `VALVES` (found by grep of module-level dict assignments), plus mapping dicts `_TUYA_TO_HA_STATE_MAPPINGS` (alarm), `_TUYA_TO_HA_HVACMODE_MAPPINGS`/`_HA_TO_TUYA_HVACMODE_MAPPINGS`/`_TUYA_TO_HA_SWING_MAPPINGS`/`_HA_TO_TUYA_SWING_MAPPINGS`/`_HA_TO_TUYA_TEMPERATURE` (climate), `_TUYA_TO_HA_DIRECTION_MAPPINGS`/`_HA_TO_TUYA_DIRECTION_MAPPINGS` (fan), `_TUYA_TO_HA_ACTIVITY_MAPPINGS` (vacuum), `_REDACTED_DPCODES` (diagnostics.py:16-21: ALARM_MESSAGE, ALARM_MSG, DOORBELL_PIC, MOVEMENT_DETECT_PIC).
- Data types (`H/const.py:99-129`): `DPType` = Bitmap, Boolean, Enum, Integer, Json, Raw, String; `try_parse` tolerates lower/UPPER and cloud aliases (`value`->Integer, `bool`->Boolean etc., l.110-129). `DPMode` IntFlag READ=1, WRITE=2 (l.92-96). `ColorTempScale` mired|kelvin (l.82-89). `DEVICE_WARNINGS: dict[device_id, set[str]]` global log-dedupe (l.75), included in diagnostics.
- Fixtures show cloud type strings with inconsistent case: F:`STRING` 52, F:`ENUM` 8, S:`ENUM` 6, S:`BOOLEAN` 2, S:`raw` 3, F:`raw` 2, S:`bitmap` 1 (script output, section 6). The IL parser must normalize case.

---
## 3. Lifecycle: `__init__.py`, `coordinator.py`

### 3.1 Setup order (`CORE/__init__.py:37-68`)
1. `DeviceListener(hass, entry)`, `listener.initialize` in executor (l.39-40). Inside (`coordinator.py:52-95`): 
   a. `register_tuya_quirks(config_dir/"tuya_quirks")` (l.64) - purges stale custom quirks, imports every module under `H/devices/` (registering quirks), then loads user `.py` files from `<config>/tuya_quirks` (`H/devices/__init__.py:16-66`); logs a warning telling users to upstream custom quirks.
   b. `Manager(client_id, user_code, terminal_id, endpoint, token_info, token_listener)` (l.70-77).
   c. `manager.add_device_listener(self)` (l.79).
   d. `manager.update_device_cache()` (l.83): blocking cloud fetch of homes -> devices (+ per-device specification, strategy info, report types; SDK/device.py:117-133).
   e. `requests.ConnectionError` -> `ConfigEntryNotReady`; exception text containing "sign invalid" -> `ConfigEntryAuthFailed` (l.84-92).
2. `entry.runtime_data = listener` (l.43).
3. `cleanup_device_registry` (l.47, 71-82): delete registry devices of this entry whose id is no longer in `manager.device_map`.
4. For every device in `device_map`: debug-log, then `listener.async_register_device(registry, device)` (l.50-61).
5. `async_forward_entry_setups(entry, PLATFORMS)` (l.63) -> each platform runs `async_discover_device([*manager.device_map])` immediately and subscribes to `TUYA_DISCOVERY_NEW` (see 5).
6. `manager.refresh_mq()` in executor (l.67) **after** entities exist so only devices with `set_up=True` (i.e. that produced >=1 entity) get MQ subscriptions.

### 3.2 Quirk application order (per device)
`async_register_device` (coordinator.py:145-155): `TUYA_QUIRKS_REGISTRY.initialise_device_quirk(device)` **first** (mutates the device in place), THEN device-registry create (`get_device_info`, which itself consults the quirk). Platforms later read the already-mutated device. `initialise_device` (H/builder/device_quirk.py:230-245): snapshots `original_category/function/local_strategy/status_range`, applies `override_category`, then applies entries **in builder-call order**. Quirk lookup key is **`device.product_id` only** (`H/registry.py:82-91`; `register` = dict assignment so a later quirk for the same product_id silently replaces an earlier one, l.74-80). `apply_when(device)->bool` callables allow per-variant conditions (e.g. Fahrenheit variant, `devices/kt/kt_hw50w7qvxluhslkk.py:41-46`).
- Not idempotent: re-running `initialise_device_quirk` on a device already mutated overwrites `original_*` with the mutated state (device_quirk.py:232-235). Core calls it once per `async_register_device`, but note `async_add_device` may re-run it on a fresh object (bind-user path re-queries the device, SDK/manager.py:232).

### 3.3 Quirk operation catalogue (`H/builder/device_quirk.py`), as used by the 32 registered quirks in 0.0.29
Counts of builder calls across `H/devices/**` (grep): `applies_to` 32; `add_dpid_integer` 26; `add_dpid_enum` 19; `add_dpid_boolean` 1; `add_dpid_bitmap` 1; `remove_dpid` 4; `set_dpid_strategy_to_enum` 2; `map_dpid_initial_status_values` 2; `override_dpid_type_information_cls` 8; `override_category` 2 (tdq_gk0d4i8g5akryd9d -> "pir", tdq_p6sqiuesvhmhvv4f -> "mcs"); `map_feeder_schedules_wrapper` 1; `remove_dpid_strategy` 0. (48 .py files under devices/, 16 category sub-dirs: bh cl clkg cs cwwsq cz dgnbj fs kt pc qn tdq wk wsdcg znnbq.)
Entry semantics:
- `_DatapointDefinition` (dpid, dpcode, dpmode READ/WRITE, dptype, values-json, report_type): READ => writes `status_range[code]`, else pops it; WRITE => writes `function[code]`, else pops; if `device.support_local` writes `local_strategy[dpid]` with `value_convert="default"` else pops it (l.130-187).
- `_DatapointRemoval`: pops dpcode from function/status/status_range and dpid from local_strategy (l.96-105).
- `_LocalConvertStrategy` (enum mapping, `value_convert="enum"`) and `_LocalStrategyRemoval` only apply if `device.support_local` (`requires_local_support`, l.35-45, 52-93).
- `_InitialStatusValueMapping`: rewrites an initial `status[code]` value (e.g. cloud returns "true" string) (l.108-127).
- `override_dpid_type_information_cls((dpid, dpcode) -> TypeInformation subclass)` consulted by `find_dpcode` **by dpcode only** (dpid ignored, device_quirk.py:481-488).
- `map_feeder_schedules_wrapper`: per-product feeder schedule wrapper for the services (3.6/4).
- Quirks carry metadata `manufacturer/model/model_id` (applies_to, l.247-263).
- **Entity-level quirks exist as dataclasses but are unused**: `H/definition/*.py` define `BaseEntityQuirk(key)` and per-platform `*Quirk(definition_fn)` (e.g. definition/binary_sensor.py:18-29, definition/valve.py:22-28) but grep finds no registry, no builder method and no core reference to them in 0.0.29. Scaffolding only.
- **Important for us**: the quirk engine is the ONLY place in core+handlers with a static DP-id -> DP-code mapping (`dpid` in add_dpid_*), and it only writes `local_strategy` when `support_local`.

### 3.4 Dynamic add/remove/update (coordinator.py)
- `add_device` (SDK callback, from MQ thread, l.120-130) -> `hass.add_job(async_add_device)`. `async_add_device` (l.132-143): **remove any stale registry entry** for the device id (which also removes its entities), `async_register_device` (quirk + registry), then `async_dispatcher_send(TUYA_DISCOVERY_NEW, [device.id])` -> every platform re-runs its discover callback for just that id. There is no other "schema changed" path.
- **Schema change handling**: none. `update_device` (l.97-118) never re-derives function/status_range; it only forwards `(updated_status_properties, dp_timestamps)`. A changed spec only reaches the integration via SDK `bindUser` bizCode (device re-queried + `add_device`, SDK/manager.py:227-238) or HA reload (full `update_device_cache`). Entities are never re-classified in place.
- `remove_device` (l.157-163) -> `async_remove_device` (l.165-170): removes registry device (entities go with it). Tested `TESTS/test_init.py:300-332`.
- Duplicate discovery is not deduped by core; the HA entity registry rejects duplicate unique_ids.
- `unload`: unload platforms, `manager.mq.stop()` if mq, `manager.remove_device_listener` (`__init__.py:85-93`). `async_remove_entry` builds a fresh Manager and calls `manager.unload` (= `user_repository.unload(terminal_id)`) to revoke the cloud terminal (l.96-108).
- `_TokenListener.update_token` persists refreshed OAuth tokens into the config entry (coordinator.py:173-203).

### 3.5 Online/offline, MQ, and SDK behaviour (SDK, not core)
- Online/offline arrive as MQ bizCode `online`/`offline` (protocol 20); SDK sets `device.online` then calls `update_device(device)` with **`updated_status_properties=None`** (SDK/manager.py:245-250, 160-164), which the entity base treats as "always write state".
- Status reports (protocol 4): two modes chosen by `device.support_local` (SDK/manager.py:166-221):
  * **local-strategy mode** (`support_local` True): items are `{dpId, value, t}`; SDK looks up `device.local_strategy[dpId]` -> `{value_convert, status_code, config_item}` and runs a registered convert strategy by name (`strategy.convert(name, (status_code, raw), config_item)`), returning `(code, value)`. **Unknown dpIds are dropped** (l.178-182). For Enum status_ranges, values not in `range` are **dropped** (l.192-205). Sets `device.status[code]`, appends to `updated_status_properties`, and records `dp_timestamps[code]=t`.
  * **cloud-code mode**: items are `{code, value}` applied directly; `dp_timestamps={}`.
  * Convert strategy names registered in `SDK/strategy_repo/`: default, enum, sd_clean_record, hb_range_v1, hb_range_v2, hb_jsq_lightv1, hb_djv1_color, voice_atm_color, dj_v1_hsv_alg, dj_v1_scene_alg, dj_v2_color_alg, dj_v2_contr_alg, dj_v2_music_alg, dj_v2_scene_alg, ms_dp_syn_alg, db_v1_{alarm,daily,data,frozen,month,params,tariff}, cz_timer1_alg, cz_timer2_alg (24 total). These implement e.g. light colour HSV<->Tuya-hex, scene strings, energy-meter blobs, socket timers. **This is DP-value decoding logic that lives in the SDK, not in core/handlers**, and is what a local integration talking raw DP ids would have to replicate (or bypass) for lights (`dj_*` strategies) and possibly meters (`db_v1_*`) and socket timers (`cz_timer*`); category attribution is inferred from strategy names, not verified. In fixtures with local_strategy the counts of `value_convert` are: default 149, enum 4, dj_v2_color_alg 3, dj_v2_scene_alg 3, dj_v2_music_alg 3, dj_v2_contr_alg 3.
- MQ subscription topic differs by mode: `.../pen` if support_local else `.../sta` (SDK/mq.py:106-112).
- `support_local` derivation (SDK/device.py:153-190): False if any `dpStatusRelationDTOS` item has `supportLocal` false, or if the cloud "custom-type" flag is on; `local_strategy` only populated when True. Otherwise `local_strategy` stays `{}`.
- Cloud-only concepts a local integration cannot/need not replicate: OAuth-QR login & token refresh (`TUYA_CLIENT_ID`, user_code, terminal_id, endpoint, `SharingTokenListener`); MQ push (`SharingMQ`, `refresh_mq`, `set_up`); cloud command POST + 10 s dedupe filter; homes/rooms/scenes (`query_homes`, `query_scenes`, `trigger_scene`, `query_room_by_device`); `get_device_stream_allocate` for camera RTSP (`camera.py:105-112`); dp-report-types call that fills `report_type` (SDK/device.py:192-209); cloud `specifications` endpoint that fills `function`/`status_range`; `time_zone`, `active_time`, `create_time`, `update_time`, `local_key`, `ip`, `uuid`, `asset_id`, `icon` fields; `dhcp` matchers & `iot_class: cloud_push` (manifest.json); the `ffmpeg` dependency (manifest `dependencies`). `bindUser`/`delete` MQ events for add/remove.
- **Sub-devices / gateways: not modelled.** `CustomerDevice.sub` (bool) is only exported in diagnostics (H/helpers/diagnostics.py:48); no `via_device`, no gateway-id field in the model (SDK/device.py:47-95), no core logic branches on `sub` (grep `\.sub\b` in CORE = none). Sub-devices are ordinary devices in `device_map`. 3 fixtures lack the key; 60 have `sub: true`.

### 3.6 diagnostics.py (CORE/diagnostics.py:1-138)
Dumps `customer_device_as_dict` (H/helpers/diagnostics.py:36-84) => keys: id, name, category, product_id, product_name, online, sub, time_zone, active_time, create_time, update_time (ISO), function `{code:{type,value}}`, local_strategy, status_range `{code:{type,value,report_type}}`, status, set_up, support_local, quirk (file:line), warnings, plus `original_category/function/local_strategy/status_range` when a quirk applied. Redacts status of `_REDACTED_DPCODES`. This diagnostics JSON is exactly the fixture format (6.1).

### 3.7 manifest.json (skim)
domain tuya; `config_flow`; `dependencies:["ffmpeg"]`; `integration_type: hub`; `iot_class: cloud_push`; `requirements: tuya-device-handlers==0.0.29, tuya-device-sharing-sdk==0.2.15`; 11 DHCP OUI prefixes; loggers `tuya_sharing`.

---
## 4. services.py (`CORE/services.py`, 161 lines; `services.yaml`)
Two services (`Service` StrEnum, services.py:32-35):
1. `tuya.get_feeder_meal_plan` (device_id: HA device registry id; `SupportsResponse.ONLY`), registered l.132-143. Flow: `_get_tuya_device` (l.39-82) resolves HA registry id -> tuya id via identifier `(tuya, id)` -> looks through all loaded config entries' `runtime_data.manager.device_map`; errors `device_not_found`, `device_not_tuya_device`. Then `get_feeder_schedule_wrapper(device)` (H/device_wrapper/service_feeder_schedule.py, 241 lines) must return a wrapper (per-quirk override via `quirk.get_feeder_schedules_wrapper`, else default), else `device_not_support_meal_plan_status`; returns `{"meal_plan": wrapper.read_device_status(device)}` or `invalid_meal_plan_data`.
2. `tuya.set_feeder_meal_plan` (device_id, `meal_plan`: list of `{days?: [monday..sunday], time: str, portion: int, enabled: bool}`, schema services.py:22-29, 145-161). Sends `wrapper.get_update_commands(device, meal_plan)` through `manager.send_commands` directly (l.126-129), bypassing `_async_send_commands`.
`services.yaml`: `time` selector, portion number 0-100 unit "g".
Needs for a local integration: a device->wrapper resolution by device model (feeder = DPCode-based schedule blob, handled in service_feeder_schedule.py - not audited in depth here), a response-returning service, and a command send path. `icons.json` also has both services.

---
## 5. Platform-table selection logic

- **Only key: `device.category`** (str). Every platform's `async_discover_device` does `TABLE.get(device.category)` (grep list in section 2; e.g. switch.py:970, sensor.py:1820, climate.py:114, camera.py:52, vacuum.py:64). `product_id` is never used in platform files (grep `product_id` in CORE excluding coordinator/util/diagnostics: no hits). Product-specific behaviour exists **only** via the quirk registry (keyed by product_id), which can (a) override `device.category` (2 quirks), (b) add/remove/retype DPs, (c) override type-information class per dpcode, (d) set device info.
- **Second gate: function presence.** A description in the category's list only becomes an entity if `get_default_definition(device, <dpcode(s)>, ...)` returns non-None, i.e. the required DP code(s) exist with the right `DPType` in `status_range`/`function` (e.g. button.py:107, switch.py:974, select.py:410, number.py:545, binary_sensor.py:466-473, sensor.py:1825-1830). Single-entity platforms (alarm, camera, climate, fan, humidifier, vacuum) use `TABLE.get(category)` (a single description) AND a definition (alarm_control_panel.py:79-84, climate.py:114-122, fan.py:63-65, humidifier.py:73-80); camera (camera.py:52-57) and vacuum (vacuum.py:64-69) create the entity unconditionally when the category matches (definition may hold `None` wrappers).
- Multi-entity platforms iterate ALL descriptions for the category: one device can yield many entities per platform. A device may appear in several platforms (a `cz` socket => switch + sensor + number ...).
- Category-specific hidden coupling: climate passes `hass.config.units.temperature_unit` (climate.py:117-119) into `get_default_definition` => **HA unit-system dependence** during classification (choose Celsius or Fahrenheit DP). Also `util.get_temperature_unit` uses `device.status[TEMP_UNIT_CONVERT]` ("c"/"f") at runtime (util.py:18-35).
- Scene platform is unrelated to devices (cloud scenes).
- `PLATFORMS` list is also monkey-patched in tests to single platforms (`patch("homeassistant.components.tuya.PLATFORMS", [...])`).
- Cross-check against fixtures: 285 of 324 fixtures produce >=1 entity; 39 produce none (list in 6.4).

---
## 6. Test fixtures and snapshots (`TESTS/`)

### 6.1 Fixture format (`TESTS/fixtures/*.json`, 324 files; filename = `{category}_{product_id}.json`, enforced by `test_fixtures_valid`, test_init.py:335-362)
They are **diagnostics dumps** (section 3.6) with sensitive/irrelevant keys stripped (`home_assistant`, `id`, `terminal_id` forbidden, test_init.py:341-355; redacted DPs must be `"**REDACTED**"`). Field presence over all 324 (my script):

| field | present |
|---|---|
| name, category, product_id, product_name, online, function, status_range, status | 324/324 |
| sub | 321 |
| time_zone, active_time, create_time, update_time | 319 |
| endpoint, mqtt_connected, disabled_by, disabled_polling | 313 (entry-level diag keys, ignored by loader) |
| set_up, support_local | 297 |
| **local_strategy** | **25 keys present, only 16 non-empty** (9 present-but-empty) |
| auth_type, country_code, app_type, model | 21 (config-entry-ish leftovers) |
| warnings | 8 |
| quirk | 3 (values present but no matching original_* keys; **no fixture has `original_*`**) |
Other stats: `support_local` True 284 / False 13 / absent 27; `online` True 266 / False 58; `sub` True 60 / False 261 / absent 3; 87 distinct categories, 324 distinct product_ids (1 fixture per product); 29 have empty `status`, 56 empty `function`, 27 empty `status_range`.
Category histogram (top): cz 53, dj 42, wsdcg 13, wk 12, cl 11, tdq 11, cs 9, sp 9, dlq 8, sfkzq 7, wg2 7, zndb 7, mcs 6, pir 5, wnykq 5, ywbj 5 ... (87 total; full list from script).

Entry shapes:
- `function[code] = {"type": <DPType str, case-variable>, "value": <dict | JSON str | int>}` (1678 entries; keys are only `type`,`value`; 3 entries additionally carry a `code` key). `status_range[code] = {"type","value"[,"report_type"]}` (2438 entries; `report_type` on 119). `value` is a JSON **object** in 3898 of 4116 entries, a str in 198, an int in 20; the test loader re-serialises non-str to JSON (`json_dumps`) to imitate the SDK's JSON-string `values` (TESTS/__init__.py:125-149). Type counts: S:Integer 1005, S:Boolean 545, F:Boolean 529, F:Integer 439, S:Enum 434, F:Enum 323, S:Raw 150, S:String 124, S:Json 122, F:Json 120, F:Raw 111, F:String 94, S:Bitmap 46, plus odd-case variants (F:STRING 52, F:ENUM 8, S:ENUM 6, S:BOOLEAN 2, S:raw 3, F:raw 2, S:bitmap 1).
- `status = {code: value}` current values. Loader special cases (TESTS/__init__.py:152-160): if the DP type is Json (in status_range or function) the dict value is re-serialised to a JSON string; `"**REDACTED**"` -> `""`.
- Consistency: 13 status codes not in status_range (all in function); 99 function codes have no status_range (write-only DPs, e.g. buttons).
- `local_strategy` (16 fixtures): `{ "<dpid>": {"value_convert","status_code","config_item":{"statusFormat":{code:"$"},"valueDesc":{...},"valueType","enumMappingMap","pid"}} }`; `valueDesc` is a parsed dict here (a JSON string in the live SDK). Loader: `device.local_strategy = details.get("local_strategy") or {}` (TESTS/__init__.py:122); test JSON keys are strings (live SDK ints). Fixtures with non-empty local_strategy: dj_k3okx0w3bsgmindp dj_oj4fqh3fo3obgu6a dj_p06rbu0a9jp37ixo dlq_fygozcnralhwbauo hjjcy_9f8pjxsmaqnk2tzr pir_o1l76njefmksbgkk tdq_1ctrc5jx88mtdh9w tdq_emb5khohohihmbxc tdq_gdknjvdpiwoq6smx tzc1_5vlawhjm wsdcg_vlzqwckk wsdcg_xflodz7oja0pndk3 zndb_z95s7p3z54xbsjnl znjdq_au6dqazvkxqnpaak znrb_8ln34bg8u4y6rdda znrb_gpzittzfnzhduquz. Distinct dpids seen: 1..47, 115, 209, 210 ... (script; union of keys).
- **Conclusion: fixtures do NOT contain DP ids for ~95% of devices (308/324 lack a usable local_strategy).** They are code-keyed only. A schema-only classifier driven by these fixtures must be keyed by DP **code**; numeric-dpid classification cannot be golden-tested from these fixtures (only 16). Also core never reads `local_strategy` for classification: grep of CORE+H shows `local_strategy` is touched only by quirks (write), diagnostics, and the SDK's MQ decoder.

### 6.2 Test harness (`TESTS/conftest.py`, `TESTS/__init__.py`)
- `create_device` builds `MagicMock(spec=CustomerDevice)`; **`device.id = mock_device_code.replace("_","")[::-1]`** (reverse of "{category}_{product_id}" with underscores stripped) (TESTS/__init__.py:102-103) => unique_ids in snapshots derive from this. Time fields parsed to int epoch; `mqtt_connected` copied.
- `create_manager` -> `MagicMock(spec=Manager)` with `device_map`, `device_listeners`, mq mocks (l.163-190). `TuyaNotificationHelper` drives `update_device/add_device/remove_device/online/offline` on registered listeners (l.32-92), `async_send_device_update(device, {code: value}, dp_timestamps)` mutates `device.status` and passes property list (l.75-92) - i.e. exactly the SDK contract in 1.4.
- Config entry data is fake cloud creds (conftest.py:24-37).
- `no_quirk` fixture clears the quirk registry and patches `register_tuya_quirks` (conftest.py:145-152). **All 17 `test_platform_setup_and_discovery` snapshot tests and `test_device_registry` use `no_quirk`** (grep list: binary_sensor, camera, fan, init, number, sensor, siren, vacuum, alarm_control_panel, button, climate, cover, event, humidifier, light, select, switch, valve). => **Golden snapshots represent the pure category+schema classification WITHOUT quirks**; quirk behaviour is only tested in separate targeted tests (test_device_registry_with_quirk, test_dynamic_add_device). Note: fixtures with a quirk (3 with `quirk` key) are also classified without it.
- `entity_registry_enabled_by_default` fixture used only for binary_sensor, sensor, button (14 sensor descriptions and 1 button set `entity_registry_enabled_default=False`, grep count). `snapshot_platform` asserts all entities enabled and each has a state (tests/common.py:1972-1989).
- Extra tests: services (7), diagnostics (2), init (7: registry cleanup x2, device registry, with_quirk, dynamic add/remove, fixtures_valid, network error retry), per-platform behaviour tests (parametrised by `mock_device_code`; e.g. sensor/number tests also assert a log message advising "use a quirk" when unit/device-class is unsupported, test_number.py:148, test_sensor.py:209).

### 6.3 Snapshot format (`TESTS/snapshots/*.ambr`, syrupy)
Per entity two blocks: `# name: test_platform_setup_and_discovery[<entity_id>-entry]` with an `EntityRegistryEntrySnapshot` (fields: domain, entity_id, unique_id `tuya.<rev-id><key>`, translation_key, original_name, original_device_class, entity_category, supported_features, capabilities (e.g. select options, number min/max/step, climate hvac modes/min/max temp), unit_of_measurement, disabled_by, icon...) and `...-state]` with `StateSnapshot` (state string + attributes). `test_init.ambr` holds device-registry snapshots (9721 lines, one per device: manufacturer/model/model_id/name/identifiers). `test_diagnostics.ambr` (749 lines) and `test_services.ambr` cover those features. 21 .ambr files total, ~79.7k lines.

### 6.4 Expected entity counts (parsed from `-entry]` blocks of `test_platform_setup_and_discovery`; entity `unique_id` prefix-matched back to fixture via reversed ids, 0 unmatched)
Total **1205 entities** over 285 fixtures (39 fixtures produce 0):

| platform | entities | distinct fixtures |
|---|---|---|
| sensor | 532 | 146 |
| switch | 257 | 138 |
| select | 118 | 73 |
| light | 65 | 64 |
| number | 62 | 37 |
| binary_sensor | 57 | 40 |
| climate | 20 | 20 |
| cover | 16 | 16 |
| event | 14 | 9 |
| valve | 14 | 7 |
| fan | 13 | 13 |
| button | 12 | 6 |
| camera | 9 | 9 |
| humidifier | 7 | 7 |
| siren | 5 | 5 |
| alarm_control_panel | 2 | 2 |
| vacuum | 2 | 2 |
| scene | 0 (cloud-only, not snapshotted) | - |

Also: entity_category None 754 / config 328 / diagnostic 123; `disabled_by` always None (enforced); 245 distinct (platform, translation_key) pairs; 158 entities have `translation_key None` (main device entity or device-class-named). Fixtures with zero entities: cz_79a7z01v3n35kytb cz_dhto3y4uachr1wll dd_gaobbrxqiblcng2p fs_ibytpo6fpnugft1c ggq_7ytb3h8u hwsb_ircs2n82vgrozoew infrared_ac_47peys infrared_ac_qzktzhehinzsz2je infrared_tv_47pew0 infrared_tv_lplun31mo1xaonwz jtmspro_xqeob8h6 ktkzq_urzivdhumrwfakie mzj_jlapoy5liocmtdvd ntq_9mqdhwklpvnnvb7t pir_j5jgnjvdaczeb6dc qt_TtXKwTMwiPpURWLJ sgbj_DYgId0sz6zWlmmYu tdq_p6sqiuesvhmhvv4f tdq_uoa3mayicscacseb tdq_x3o8epevyeo3z3oa tzc1_5vlawhjm wfcon_b25mh8sxawsgndck wfcon_lieerjyy6l4ykjor wfcon_plp0gnfcacdeqk5o wg2_tmwhss6ntjfc7prs wg2_v7owd9tzcaninc36 wnykq_kzwdw5bpxlbs9h9g wnykq_npbbca46yiug8ysk wnykq_om518smspsaltzdi wnykq_rqhxdyusjrwxyff6 wnykq_x0lyfgjuguuh1vof wsdykq_ay30hrndaogxclh0 wxnbq_5l1ht8jygsyr1wn1 xfj_pjabraecffsfrmxz xktyd_3djw12ln4xtvv8eq ydkt_jevroj5aguwdbs2e ygsb_l6ax0u6jwbz82atk zjq_nkkl7uzv zndb_gqmmtjclqb7reg5p (several of these are the quirk-target products, e.g. tdq_p6sqiuesvhmhvv4f -> override to "mcs").
Golden-test suitability: entries give `(unique_id suffix = description.key, platform, entity_category, device_class, translation_key, unit, capabilities)` per (fixture, entity). State snapshots additionally capture read-conversion output (value scaling, enum mapping) - usable to test `status -> entity state`. They do NOT capture commands sent (needs the per-platform behaviour tests) and do not capture quirk behaviour (no_quirk).

---
## 7. Requirements for the IL (derived only from the above)

R1. **Input model** = `{id, name, category, product_id, product_name, online, sub?, function{code:{type,values}}, status_range{code:{type,values,report_type?}}, status{code:value}}`; optional `local_strategy{dpid:{status_code,value_convert,config_item}}`, `support_local`. Time/cloud fields (`time_zone, active_time, create_time, update_time, local_key, ip, uuid, icon`) are diagnostic-only and unneeded for entities.
R2. **Code-keyed schema**. All classification/decoding in core+handlers is by DP code, never dpid. A local (bridge) integration must supply a dpid->code(+type) map from somewhere (cloud spec, `local_strategy`, or static quirk `dpid` tables). The IL must be able to express both "code known" and "dpid only".
R3. **Case-insensitive/aliased DPType normalisation** (`value`, `bool`, `ENUM`, `raw`...), and `values` given as dict or JSON str, numeric scale/step semantics (`IntegerTypeInformation.scale_value`), Enum `range`, Bitmap `label`.
R4. **Selector**: `category -> per-platform list of descriptions`, each description yielding an entity iff required DP codes exist with required DPType (tuple-of-codes fallback, `prefer_function` toggle, `status_range` vs `function` lookup order). No product_id selection; product-specific behaviour via quirks. Categories are open strings (enum incomplete; fixtures include categories not in the enum).
R5. **Quirk stage runs before classification and before device-info**, ordered, keyed by `product_id`, supports: category override, add/replace/remove DP (with dpid), retype/rescale (unit/min/max/scale/step/report_type), initial-status value mapping, type-information class override by dpcode, per-variant `apply_when(device)` predicates (arbitrary code!; an IL needs a declarative predicate language, e.g. on status value or DP presence - see `kt_hw50w7qvxluhslkk.py`), manufacturer/model/model_id metadata, feeder wrapper override. Custom user quirks are Python files in `<config>/tuya_quirks` (not declarative). Decide whether the IL must also express user quirks.
R6. **Entity descriptor output** must include: platform, unique-id suffix (`description.key`), translation_key, device_class, entity_category, entity_registry_enabled_default, unit/suggested unit, state_class (sensor), icon (icons.json keyed by translation_key), plus platform capabilities (options, min/max/step, hvac modes, etc.). Names come from translations; not literal.
R7. **Read path**: pure `read(device.status, schema) -> value` per wrapper; some wrappers are stateful (Delta accumulator with timestamp de-dup keyed by `dp_timestamps`); `skip_update(props, ts)` gates state writes (per-DP relevance filter). IL needs (a) update-relevance = set of DP codes an entity depends on and (b) optional per-entity persistent state.
R8. **Write path**: pure `write(value) -> list[{code, value}]`; validation/scaling raises out-of-range errors; empty list = no-op; must allow multi-command results (e.g. light colour + mode). A local bridge would translate code->dpid (and reverse value encodings the SDK strategies would otherwise do, such as HSV colour and scene strings; see 3.5).
R9. **Update contract**: `(device_id, updated_codes: list|None, timestamps: dict|None)` where `None` means "availability/name changed only". Status is mutated before the signal. Availability = single `online` bool.
R10. **Lifecycle events**: add_device (re-register, drop stale registry entry, re-classify), remove_device, name update, online/offline. No in-place schema-change event exists in core; IL should offer one anyway ("re-classify on schema change") since local schemas may arrive late.
R11. **HA-environment inputs to classification**: at least the HA temperature unit system (climate) and `device.status[temp_unit_convert]` at runtime. Any other env inputs should be modelled explicitly.
R12. **Device info**: manufacturer default "Tuya", model/model_id from product; quirk override only when quirk manufacturer is set. No via_device/gateway/sub-device linkage exists in core; the IL should add optional parent linkage if the bridge exposes gateways.
R13. **Services**: entity-independent, device-scoped, may return a response (feeder meal plan). IL needs "device-level actions with schema".
R14. **Non-representable in a local integration** (drop or replace): OAuth/QR login, token refresh, MQ subscription/refresh, cloud commands rate filter, scenes, camera stream allocation, home/room, `report_type` fetch (fixtures carry it, live local path would not have it unless provided), `specifications` fetch.
R15. **Golden tests**: use `fixtures/*.json` (324) with `no_quirk` semantic => 1205 expected entities; snapshots give registry-level properties and state; no dpid data for 308 fixtures. Device-id used for unique_id in tests is the reversed fixture code, so compare `unique_id` suffixes (strip the id prefix) or reproduce the reversal.
R16. **Unique-id compatibility**: `tuya.{device_id}{key}`; keeping it enables migration of existing user entities; note no separator.

## 8. Open questions / not verified
1. Where does a local (bridge) integration get code<->dpid mapping and full `function/status_range` for devices whose cloud schema is unavailable? Only 16/324 fixtures contain local_strategy; rustuya bridge output format not examined by me.
2. Are the SDK convert strategies (24 names, SDK/strategy_repo) required to reproduce entity states for local-only data? They convert raw local DP values to cloud-code values for dj/dlq(db_v1_*)/cz timers etc. Core/handlers assume the value is already converted. I did not read the strategy implementations.
3. How many of the 415 DPCodes / 98 categories / 17 platform tables are exercised by the 324 fixtures vs. only present in code (fixture coverage gap; not computed here).
4. `service_feeder_schedule.py` default wrapper and per-platform wrapper classes (`H/device_wrapper/*`, `H/definition/*`, `H/helpers/homeassistant.py`, 1111 lines) were only spot-checked; deeper analysis belongs to other slices.
5. `BaseEntityQuirk`/`*Quirk` in `H/definition` appear unused in 0.0.29: confirm with upstream whether they are intended to become a per-entity quirk registry (would affect IL design).
6. Whether platform snapshot tests represent `disabled_by=None` for entities with `entity_registry_enabled_default=False` only because of the `entity_registry_enabled_by_default` fixture (used for sensor/binary_sensor/button only). Other platforms have no such descriptions per grep (only sensor.py:14, button.py:1 use the flag), so consistent, but not independently verified.
7. `update_device` may be invoked from an SDK thread; core hands off via `dispatcher_send` (thread-safe). Ordering guarantees between add_device and status updates are not documented.
8. Behaviour when two devices share a `product_id` but differing firmware: quirks apply per product_id only; only `apply_when` distinguishes. No firmware/version fields exist in the model.
