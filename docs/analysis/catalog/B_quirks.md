# B. Per-device quirk system (tuya-device-handlers 0.0.29 + core consumption)

Path abbreviations: `H/` = `tuya_device_handlers-0.0.29/src/tuya_device_handlers/`; `C/` = `core/homeassistant/components/tuya/`; `D/` = `H/devices/`.
Note: there are **32** quirk files (not ~40): bh1, cl5, clkg1, cs2, cwwsq1, cz5, dgnbj1, fs2, kt2, pc1, qn1, tdq5, wk3, wsdcg1, znnbq1.

## 1. How it works end to end

**Registration.** A quirk file is a bare expression: `DeviceQuirk().applies_to(...)...register(TUYA_QUIRKS_REGISTRY)` executed at import time (e.g. `D/bh/bh_dft4ebatvon3ha5s.py:15-30`). `register_tuya_quirks()` (`D/__init__.py:13-52`) walks all sub-packages of `devices` with `pkgutil.walk_packages` and imports each one (`D/__init__.py:25-31`); it also loads user-provided custom quirk modules from `<config>/tuya_quirks` by exec_module (`D/__init__.py:42-60`), first purging previously loaded custom quirks (`D/__init__.py:22-23`, `H/registry.py:93-103`), then logs a warning asking to upstream them. Core calls it in `C/coordinator.py:64` (`register_tuya_quirks(str(Path(hass.config.config_dir, "tuya_quirks")))`, executor thread).

**Registry.** `QuirksRegistry` is a process singleton (`H/registry.py:57-72`) holding `dict[str, DeviceQuirkProtocol]` (`H/registry.py:61`). **Lookup key = `device.product_id` only** (`H/registry.py:86, 90`). Category is NOT part of the key (the file-layout `devices/<category>/` is only organisational, and directory `__init__.py` files hold Tuya doc URLs, e.g. `D/cl/__init__.py`). `register()` is a plain dict assignment (`H/registry.py:80`) => a second quirk for the same product_id silently overwrites the first; exactly one quirk per product_id, no merge. Keys are case-sensitive and several are mixed-case (`eyEYwtdx9VhexxLW` `D/cz/cz_eyeywtdx9vhexxlw.py:21`, `QH3oyDNHKw9c1irH` `D/cz/cz_qh3oydnhkw9c1irh.py:17`, `crh9iaqFowdJX5UY` `D/kt/kt_crh9iaqfowdjx5uy.py:9`), so the IL match must be exact, case-sensitive product_id.

**Metadata match fields.** `applies_to(product_id, manufacturer=None, model=None, model_id=None)` (`H/builder/device_quirk.py:253-268`) - only `product_id` participates in matching; manufacturer/model/model_id are display metadata. May be called once only (`:261-263` raises). `register()` raises if `applies_to` missing (`:275-277`).

**When applied.** In core, `TUYA_QUIRKS_REGISTRY.initialise_device_quirk(device)` is called inside `DeviceListener.async_register_device` (`C/coordinator.py:143-150`), which runs (a) for every device at entry setup **before** platforms are forwarded (`C/__init__.py:51-63`, comment "Register quirk, and add device to the device registry" at `:60`; `async_forward_entry_setups` at `:65`) and (b) on the device-added event before dispatching `TUYA_DISCOVERY_NEW` (`C/coordinator.py:134-141`). So quirks always mutate the `CustomerDevice` before any entity/platform discovery. Platforms then read `device.category` for table lookup (e.g. `C/cover.py:170`, `C/binary_sensor.py:462`, `C/sensor.py:1820`; all 16 platforms do `TABLE.get(device.category)`).

**What `initialise_device` mutates** (`H/builder/device_quirk.py:230-247`):
1. Snapshots `original_category`, `original_function`, `original_local_strategy`, `original_status_range` onto the quirk instance (`:231-234`; used only by diagnostics `H/helpers/diagnostics.py:71-82`). Caveat: snapshot lives on the shared per-product quirk object, so with multiple devices of same product it is overwritten by the last one.
2. `device.category = override` if set (`:236-237`).
3. Iterates `_quirk_entries` in call order, applying each whose `applies_to_device` is true (`:239-247`): entries mutate `device.function`, `device.status_range`, `device.local_strategy`, `device.status` (see 2). Order matters (comment `:239-241`: a local-strategy entry reads `status_range`, so must come after the DP definition).
4. NOT mutated by any code path: `product_id`, `name`, `model`/`manufacturer` on the device object itself (manufacturer/model/model_id are only exposed through the quirk object and used by core `get_device_info`, `C/util.py:65-87`: only when `quirk.manufacturer` is truthy are manufacturer/model/model_id replaced - `C/util.py:72-78`; otherwise manufacturer "Tuya", model=product_name, model_id=product_id).

**Two non-mutating hooks (consumed lazily, not in initialise_device):**
- `get_type_information_cls(dpcode=...)`: consulted in `TypeInformation.find_dpcode` (`H/type_information.py:86, 92-98`) for every dpcode lookup; if quirk provides a class it replaces the class used for parsing (and the DPType check uses that class's `_DPTYPE`, `:100-104`). Implementation `H/builder/device_quirk.py:481-488` matches on **dpcode only** (dpid ignored; iteration over dict keyed `(dpid, dpcode)`).
- `get_feeder_schedules_wrapper(device)`: `H/builder/device_quirk.py:471-479`; called via `get_feeder_schedule_wrapper` (`H/device_wrapper/service_feeder_schedule.py:70-83`) from core services `get_feeder_meal_plan`/`set_feeder_meal_plan` (`C/services.py:91, 114`). Returns None (service unavailable) if no quirk/wrapper.

**Builder API surface** (`H/builder/device_quirk.py`; all fluent, return Self):
| method | line | effect |
|---|---|---|
| `applies_to(product_id, manufacturer, model, model_id)` | 253 | key + metadata |
| `override_category(str)` | 270 | set `device.category` |
| `register(registry)` | 274 | store by product_id |
| `add_dpid_bitmap(dpid,dpcode,dpmode,label_range,apply_when)` | 279 | define/replace DP, type Bitmap, values `{"label":[...]}` (:291) |
| `add_dpid_boolean(dpid,dpcode,dpmode,apply_when)` | 300 | Boolean, values `"{}"` (:319) |
| `add_dpid_enum(dpid,dpcode,dpmode,enum_range,apply_when)` | 322 | Enum, `{"range":[...]}` (:339) |
| `add_dpid_integer(dpid,dpcode,dpmode,unit,min,max,scale,step,report_type,apply_when)` | 342 | Integer, `{"unit","min","max","scale","step"}` (:367-374), optional `report_type` (e.g. "sum") |
| `map_dpid_initial_status_values(dpid,dpcode,status_mapping,apply_when)` | 379 | rewrite initial `device.status[dpcode]` via dict |
| `override_dpid_type_information_cls(dpid,dpcode,type_information_cls)` | 396 | swap TypeInformation class (code) |
| `remove_dpid(dpid,dpcode,apply_when)` | 409 | drop DP from function/local_strategy/status/status_range |
| `set_dpid_strategy_to_enum(dpid,dpcode,enum_mapping_map,apply_when)` | 423 | set local_strategy[dpid] with `value_convert:"enum"` and `enumMappingMap {str(k):{"value":v}}` (:436-440); only if `device.support_local` |
| `remove_dpid_strategy(dpid,dpcode,apply_when)` | 446 | pop `local_strategy[dpid]`; only if support_local. **Unused by any shipped quirk.** |
| `map_feeder_schedules_wrapper(wrapper_function)` | 460 | register callable device->wrapper (code) |

`_DatapointDefinition.apply` semantics (`:139-158`): dpmode has READ => `status_range[dpcode]=DeviceStatusRange(code,type,values,report_type)` else pop it; dpmode has WRITE => `function[dpcode]=DeviceFunction(code,type,values)` else pop it; if `support_local` => `local_strategy[dpid]` = `{"value_convert":"default","status_code":dpcode,"config_item":{"statusFormat":json.dumps({dpcode:"$"}),"valueDesc":values,"valueType":type,"enumMappingMap":{},"pid":product_id}}` else pop local_strategy[dpid] (:150-158, :174-186). i.e. a definition is a full **replace** (not merge) of that dpcode's range/function definition, and it also creates the dpid<->dpcode mapping for local (LAN) mode. `_LocalConvertStrategy`/`_LocalStrategyRemoval` have `requires_local_support = True` (:80-88, `:63-64` in base: skipped when `not device.support_local`); `_DatapointRemoval` and `_InitialStatusValueMapping` do not.
`apply_when: Callable[[CustomerDevice], bool]` per-entry predicate (:36-52) - arbitrary Python; only used once (kt_hw50w7qvxluhslkk).

`DPMode` = IntFlag READ=1, WRITE=2 (`H/const.py:33-37`); `DPType` = Bitmap, Boolean, Enum, Integer, Json, Raw, String with lax aliases `bool,enum,json,raw,string,value,bitmap` (`H/const.py:40-63`). Quirk builder can only *define* Bitmap/Boolean/Enum/Integer (no Json/Raw/String builders).

## 2. Override-kind classification

Counts are of quirk files (32 total).

| # | Kind | Builder | Description | Files | Examples |
|---|---|---|---|---|---|
| K1 | **Add / replace Integer DP definition (range/scale/unit/step/report_type)** | `add_dpid_integer` | Overrides cloud numeric spec: fixes wrong scale (deci-W/V), unit, max; or declares DP the cloud omitted | 14 | Fix scale 0->1 + widen max: `D/cz/cz_eyeywtdx9vhexxlw.py:26-45` (cur_power dp5 scale1; cur_voltage dp6 max 3000); unit/scale kWh: `D/cz/cz_wifvoilfrqeo6hvu.py:26-35` (add_ele scale3 unit kWh report_type sum); unit fix kW->W: `D/znnbq/znnbq_7bqwya0ydtz4q3ss.py:28-37` (power_total); new DP not in cloud: `D/dgnbj/dgnbj_qajfz5x1lqej5xxw.py:13-22` (ph_current dp102), `D/wsdcg/wsdcg_m7kacaxrxbxeegfs.py:10-19` (ext_temp dp101); Fahrenheit override: `D/kt/kt_hw50w7qvxluhslkk.py:34-42` (temp_set unit ℉ min160 max880 scale1 step5, conditional) |
| K2 | **Add / replace Enum DP definition (option list)** | `add_dpid_enum` | Cloud enum range incomplete; supply full range (also invents DPs) | 13 | add "on": `D/cz/cz_ndvina39gbq8x0jk.py:22-27`, `D/pc/pc_avriaapskyik4eaa.py:22-27` (light_mode dp40); add "off": `D/qn/qn_tjvnxyobs3upidjo.py:19-24`; add home: `D/wk/wk_if6pqia2gbtvqa6l.py`; add comfort/holiday: `D/wk/wk_ucf09xuve67adcp4.py`; expanded temp presets `D/bh/bh_dft4ebatvon3ha5s.py:28-33`; 13-value countdown `D/fs/fs_xwv3jifdbhbolgh3.py:23-50`; brand-new DPs on empty product `D/tdq/tdq_gk0d4i8g5akryd9d.py:19-24` |
| K3 | **Declare DP absent from cloud model ("fake"/missing DP)** | any add_dpid_* for a dpcode not in cloud data | Sub-case of K1/K2/K4/K5: dpid+dpcode+type invented because cloud model lacks it, typically from Tuya dev portal/property query. Not a separate API. | 10 | `D/tdq/tdq_datzwoplui1zao16.py` (temp_unit_convert dp20, temp_current dp27, humidity_value dp46), `D/tdq/tdq_x3o8epevyeo3z3oa.py`, `D/tdq/tdq_xeagimantb7d7apb.py`, `D/tdq/tdq_gk0d4i8g5akryd9d.py`, `D/tdq/tdq_p6sqiuesvhmhvv4f.py`, `D/wk/wk_cpmgn2cf.py` (docstring says valve dp109 missing but code only sets `mode`, see open Q), `D/cs/cs_ma3oq4onxxwg91ky.py:12-18`, `D/cs/cs_uhtamgih7kkdcqtx.py:15-36`, `D/dgnbj/...`, `D/wsdcg/...`, `D/kt/kt_crh9iaqfowdjx5uy.py` (temp_set/temp_current/mode) |
| K4 | **Add Bitmap DP** | `add_dpid_bitmap` | declare fault bitmap with labels | 1 | `D/cs/cs_ma3oq4onxxwg91ky.py:12-18` (fault dp19 labels E1,E2,tankfull) |
| K5 | **Add Boolean DP** | `add_dpid_boolean` | | 1 | `D/tdq/tdq_p6sqiuesvhmhvv4f.py:14-18` (doorcontact_state dp101) |
| K6 | **Change dpmode (read-only vs read/write)** | `dpmode` param on every add_* | Determines whether DP is placed in `status_range` (READ), `function` (WRITE), or both; also removes it from the other map. Used both to declare writable settings (READ|WRITE) and read-only sensors (READ). | all files that use K1-K5 (22) | `DPMode.READ|WRITE` `D/fs/fs_xwv3jifdbhbolgh3.py:14`; `DPMode.READ` `D/cz/cz_eyeywtdx9vhexxlw.py:19` |
| K7 | **Override category** | `override_category` | Change `device.category` so a different core platform table applies | 2 | `D/tdq/tdq_gk0d4i8g5akryd9d.py:13` -> "pir"; `D/tdq/tdq_p6sqiuesvhmhvv4f.py:13` -> "mcs" |
| K8 | **Remove DP** | `remove_dpid` | Delete a DP entirely (function/status/status_range/local_strategy) so core default mapping falls back or doesn't create the entity | 3 | `D/cl/cl_b9oa3zocv4qq47iy.py:24` (drop percent_state so default falls back to percent_control); `D/cl/cl_xyakonle1azq2xgn.py:15-16` (drop both percent DPs); `D/kt/kt_hw50w7qvxluhslkk.py:43-46` (drop temp_set_f dp136, conditional) |
| K9 | **Swap TypeInformation class (value inversion)** | `override_dpid_type_information_cls` | Replace parser class for a dpcode; the only class used is `InvertedIntegerTypeInformationEx` (`H/type_information_ex.py:14-47`) which inverts on read (`scale_value(max) - value`) and write to cancel the cover wrapper's own inversion | 4 | `D/cl/cl_68nvbio9.py:25-34`, `D/cl/cl_cf1sl3tj.py:25-34`, `D/cl/cl_nfq1essvr99qsvvd.py:37-46`, `D/clkg/clkg_csgb8eqhczvjaetl.py:25-34` (each on percent_control dp2 + percent_state dp3) |
| K10 | **Initial-status value mapping** | `map_dpid_initial_status_values` | Rewrite cached cloud status (e.g. "true"/"false" strings -> bool) at init | 1 | `D/fs/fs_dune79w7bsu6dg3e.py:19-23, 29-33` (switch_horizontal dp4, switch_vertical dp5) |
| K11 | **Local (LAN) value-convert strategy = enum mapping** | `set_dpid_strategy_to_enum` | Set local_strategy for a DP with enumMappingMap `{0:False,1:True}` so local reads convert to bool. Local-mode only | 1 | `D/fs/fs_dune79w7bsu6dg3e.py:24-28, 34-38` |
| K12 | **Feeder-schedule wrapper** | `map_feeder_schedules_wrapper` | Bind a wrapper (code) to `feeder_schedules` service; lambda finds `meal_plan` DP with `prefer_function=True` and builds `DefaultFeederScheduleWrapper` | 1 | `D/cwwsq/cwwsq_wfkzyy0evslzsmoi.py:18-24` |
| K13 | **Set manufacturer/model/model_id** (device-registry info) | kwargs of `applies_to` | Only if manufacturer given, core replaces model/model_id too | 15 | `D/bh/bh_dft4ebatvon3ha5s.py:19-22`, `D/cz/cz_eyeywtdx9vhexxlw.py:20-25`, `D/cl/cl_nfq1essvr99qsvvd.py:36` (manufacturer only => model/model_id become None in core, see open Q) |
| K14 | **Conditional application (`apply_when`)** | kwarg on entries | Predicate on device state to handle variants sharing product_id | 1 | `D/kt/kt_hw50w7qvxluhslkk.py:26-28, 41, 46` (`_is_fahrenheit_variant`: `isinstance(status["temp_set"], int) and >= 450`) |
| - | remove local strategy (`remove_dpid_strategy`), add_dpid_json/raw/string, rename dpcode, change dp type of an existing dpcode independently, remove function-only / status-only | | 0 | not present. "Change DP type" only happens via re-declaring with a different builder (nothing in shipped quirks changes type: every replaced dpcode keeps its type as far as the quirk shows; cloud-side original type not verified) |

Rename/remap of dpcode: **not supported** by the builder. (Quirk `tdq_xeagimantb7d7apb` declares the same dpcode on two different dpids: `temp_current` on dp27 and dp102, `humidity_value` on 46 and 103, `battery_state` on 101 and 104 - `D/tdq/tdq_xeagimantb7d7apb.py:22-73`; because `status_range`/`function` are keyed by dpcode, the later entry replaces the earlier in status_range, while `local_strategy` is keyed by dpid so both dpids remain mapped to the same dpcode. Effectively "alias multiple dpids to one dpcode" for local mode; behavior in cloud mode is last-writer-wins.)

## 3. Per-file table

Legend: K1 int-def, K2 enum-def, K3 fake/missing DP, K4 bitmap, K5 bool, K7 category, K8 remove, K9 TI class swap, K10 init-status map, K11 local enum strategy, K12 feeder, K13 metadata, K14 apply_when. K6 (dpmode) omitted, present wherever an add_* exists.

| file | category (dir) | product_id | manufacturer / model / model_id | kinds |
|---|---|---|---|---|
| D/bh/bh_dft4ebatvon3ha5s.py | bh | dft4ebatvon3ha5s | Anko / Smart kettle / LD-K3068 | K2 (temp_setting_quick_c dp4), K13 |
| D/cl/cl_68nvbio9.py | cl | 68nvbio9 | - | K9 (dp2 percent_control, dp3 percent_state) |
| D/cl/cl_b9oa3zocv4qq47iy.py | cl | b9oa3zocv4qq47iy | A-OK / Tubular motor / AM45 Plus Wi-Fi | K8 (percent_state dp3), K13 |
| D/cl/cl_cf1sl3tj.py | cl | cf1sl3tj | - | K9 (dp2, dp3) |
| D/cl/cl_nfq1essvr99qsvvd.py | cl | nfq1essvr99qsvvd | Canisteo / - / - | K9 (dp2, dp3), K13 (manufacturer only) |
| D/cl/cl_xyakonle1azq2xgn.py | cl | xyakonle1azq2xgn | - | K8 (percent_control dp9, percent_state dp8) |
| D/clkg/clkg_csgb8eqhczvjaetl.py | clkg | csgb8eqhczvjaetl | - | K9 (dp2, dp3) |
| D/cs/cs_ma3oq4onxxwg91ky.py | cs | ma3oq4onxxwg91ky | - | K4, K3 (fault dp19) |
| D/cs/cs_uhtamgih7kkdcqtx.py | cs | uhtamgih7kkdcqtx | - | K1, K3 (humidity_indoor dp3, temp_indoor dp103) |
| D/cwwsq/cwwsq_wfkzyy0evslzsmoi.py | cwwsq | wfkzyy0evslzsmoi | Cleverio / Automatic pet feeder / PF100 | K12, K1 (feed_report dp15, report_type sum), K13 |
| D/cz/cz_eyeywtdx9vhexxlw.py | cz | eyEYwtdx9VhexxLW | Gosund / Smart Socket / SP111 | K1 (cur_power dp5, cur_voltage dp6), K13 |
| D/cz/cz_ndvina39gbq8x0jk.py | cz | ndvina39gbq8x0jk | Konyks / Priska Max 3 FR / - | K2 (light_mode dp40), K13 |
| D/cz/cz_qh3oydnhkw9c1irh.py | cz | QH3oyDNHKw9c1irH | - | K1 (cur_power dp5, cur_voltage dp6) |
| D/cz/cz_qxjsytletx5wrza9.py | cz | qxJSyTLEtX5WrzA9 | GHome / Mini Smart Plug / WP3 | K1 (dp5, dp6), K13 |
| D/cz/cz_wifvoilfrqeo6hvu.py | cz | wifvoilfrqeo6hvu | Gosund / Smart socket / EP2 | K1 (add_ele dp3 sum, cur_power dp5, cur_voltage dp6), K13 |
| D/dgnbj/dgnbj_qajfz5x1lqej5xxw.py | dgnbj | qajfz5x1lqej5xxw | - | K1, K3 (ph_current dp102) |
| D/fs/fs_dune79w7bsu6dg3e.py | fs | dune79w7bsu6dg3e | Duux / Whisper Flex / DXCF10 | K10 x2, K11 x2, K13 |
| D/fs/fs_xwv3jifdbhbolgh3.py | fs | xwv3jifdbhbolgh3 | Comfort Zone / Tower Fan / CZTF423S | K2 x2 (mode dp2, countdown_set dp22), K13 |
| D/kt/kt_crh9iaqfowdjx5uy.py | kt | crh9iaqFowdJX5UY | - | K1 x2 (temp_set dp2 R/W, temp_current dp3), K2 (mode dp4) |
| D/kt/kt_hw50w7qvxluhslkk.py | kt | hw50w7qvxluhslkk | - | K1 (temp_set dp2), K8 (temp_set_f dp136), K14 |
| D/pc/pc_avriaapskyik4eaa.py | pc | avriaapskyik4eaa | Konyks / Priska Duo FR / - | K2 (light_mode dp40), K13 |
| D/qn/qn_tjvnxyobs3upidjo.py | qn | tjvnxyobs3upidjo | - | K2 (mode dp4: eco, off) |
| D/tdq/tdq_datzwoplui1zao16.py | tdq | datzwoplui1zao16 | - | K2, K1 x2, K3 (dp20, dp27, dp46) |
| D/tdq/tdq_gk0d4i8g5akryd9d.py | tdq | gk0d4i8g5akryd9d | - | K7 (->pir), K2 x2, K3 (pir dp101, battery_state dp102) |
| D/tdq/tdq_p6sqiuesvhmhvv4f.py | tdq | p6sqiuesvhmhvv4f | - | K7 (->mcs), K5, K2, K3 (doorcontact_state dp101, battery_state dp102) |
| D/tdq/tdq_x3o8epevyeo3z3oa.py | tdq | x3o8epevyeo3z3oa | - | K2 x2, K1 x2, K3 (dp20, 27, 46, 101) |
| D/tdq/tdq_xeagimantb7d7apb.py | tdq | xeagimantb7d7apb | - | K2 x3, K1 x4, K3; duplicate dpcodes on 2 dpids (dp27/102, 46/103, 101/104) |
| D/wk/wk_cpmgn2cf.py | wk | cpmgn2cf | - | K2 (mode dp4, 7 values incl "BOOST") |
| D/wk/wk_if6pqia2gbtvqa6l.py | wk | if6pqia2gbtvqa6l | - | K2 (mode dp2: auto, home) |
| D/wk/wk_ucf09xuve67adcp4.py | wk | ucf09xuve67adcp4 | Warmtec / Thermostat / T510 | K2 (mode dp2), K13 |
| D/wsdcg/wsdcg_m7kacaxrxbxeegfs.py | wsdcg | m7kacaxrxbxeegfs | - | K1, K3 (ext_temp dp101) |
| D/znnbq/znnbq_7bqwya0ydtz4q3ss.py | znnbq | 7bqwya0ydtz4q3ss | WVC / Micro inverter / WVC-800W | K1 (power_total dp10 unit W scale3), K13 |

Totals by kind (files): K1=14, K2=13, K3=10 (subset overlap), K4=1, K5=1, K7=2, K8=3, K9=4, K10=1, K11=1, K12=1, K13=15, K14=1. Every quirk file is keyed on the product_id; category dir name is not used by code.

## 4. Declarative vs code

**Pure data (expressible as JSON/YAML):** K1, K2, K4, K5, K6 (dpmode), K3, K7 (category string), K8, K10 (dict mapping raw value -> value), K11 (enum map), K13 (strings). Covers 30 of 32 files completely.

**Needs code today:**
- K9 `type_information_cls`: a Python class. Only one class is used (`InvertedIntegerTypeInformationEx`), semantics fully describable as data: `invert: true` on an Integer DP (read `scale_value(max) - v`, write likewise, `H/type_information_ex.py:33-47`). Note the inversion is a *pre-inversion that cancels a downstream wrapper's inversion* (cover's `DPCodeInvertedPercentageWrapper`, see docstring `D/cl/cl_68nvbio9.py` header) so the data representation should express intent as "this device's position is NOT inverted vs HA convention" (a cover-semantic flag), rather than "invert TypeInformation"; otherwise the IL couples to core's wrapper quirks.
- K12 feeder wrapper: lambda choosing DP code `meal_plan` (prefer function map) plus the `DefaultFeederScheduleWrapper` binary codec (template of 5 two-hex-digit fields days/hour/minute/portion/enabled, base64-encoded Raw DP: `H/device_wrapper/service_feeder_schedule.py:31-69`). Data form: `feeder_schedule: {dpcode: "meal_plan", codec: "default"}`; codec itself is code (or a named built-in codec).
- K14 `apply_when`: predicate (`temp_set` status >= 450 => Fahrenheit variant, `D/kt/kt_hw50w7qvxluhslkk.py:35-38`). Data form: a small condition DSL over current DP values (`{dpcode, op, value}`) or IL-provided variant discriminator. Only one instance; DSL could be minimal (int compare on a DP's current value).

**What the IL must carry to make quirks data:**
1. Match: `product_id` (case-sensitive exact). Optional: display metadata `{manufacturer, model, model_id}` (+ rule that model only replaces if manufacturer set, `C/util.py:72-78`).
2. Device-level patches: `category` override.
3. DP map: for each `(dpid, dpcode)`: `type` in {Bitmap,Boolean,Enum,Integer} (ideally also Json/Raw/String), `mode` {R,W,RW}, `values` payload (enum range, integer min/max/scale/step/unit, bitmap labels), `report_type` ("sum"), replace semantics ("define this DP fully; replaces what cloud said"). The dpid is essential because the local bridge speaks dpids not dpcodes (local_strategy is generated from dpid).
4. DP removals `(dpid,dpcode)`; removal also drops status.
5. Initial-value mapping table per DP; local-mode value-convert strategy (`enum` with map) per DP - relevant to the IL since a bridge speaking dpid/raw values sees the raw local representation.
6. Integer "invert" / "position not inverted" flag per DP or semantic flag on cover position.
7. Optional predicate (variant discriminator) on each entry.
8. Feeder schedule binding.
9. Multiple dpids -> same dpcode (aliasing), as in tdq_xeagimantb7d7apb.
10. Ordering guarantee: entries apply in declared order; later replaces earlier.

## 5. Interaction with core platform tables

- Core selects the entity table by `device.category` in every platform (`C/binary_sensor.py:462`, `C/cover.py:170`, `C/sensor.py:1820`, `C/switch.py:970`, `C/climate.py:114`, etc.). Because quirks run before platform setup, `override_category` re-routes the device to another category's tables. Two cases: `gk0d4i8g5akryd9d` (tdq, "temperature/humidity? socket-like" per docstring "Tuya does not advertise any datapoints") -> `pir` (core binary_sensor table for PIR: `C/binary_sensor.py:317-319` keyed on `DPCode.PIR`; battery via `C/sensor.py:933` `DeviceCategory.PIR: BATTERY_SENSORS`), and `p6sqiuesvhmhvv4f` -> `mcs` (`C/binary_sensor.py:281`, battery `C/sensor.py:893`). The quirk adds the DPs (`pir`, `doorcontact_state`, `battery_state`) those tables expect (`D/tdq/tdq_gk0d4i8g5akryd9d.py:19-24`, `D/tdq/tdq_p6sqiuesvhmhvv4f.py:14-24`). => The IL cannot assume category is immutable; category is a per-device output of the quirk stage, and platform tables key on the *post-quirk* category.
- Other quirks keep the category but add/replace DP definitions so existing category tables (e.g. `tdq` sensors for temp_current/humidity_value, `cz` power sensors, `wk` climate modes, `kt` climate) pick them up. Table lookups use `find_dpcode`/`function`/`status_range`; therefore a quirk's READ flag decides which map the DP is in (sensors read status_range, controls/selects read function, `prefer_function` variants exist: `H/type_information.py:77-83`).
- `remove_dpid` (cl) works by making the default CL mapping (which prefers `percent_state`, falling back to `percent_control`) skip a DP (`D/cl/cl_b9oa3zocv4qq47iy.py` header). Type-info override (K9) works by interplay with cover's inversion wrapper (`DPCodeInvertedPercentageWrapper`) - core behaviour, not in this slice.
- Sensor/number entities also look at `temp_unit_convert` (`C/util.py get_device_temp_unit_convert`, `C/sensor.py:63`, `C/number.py:594`); quirks tdq_* add `temp_unit_convert` dp20 enum c/f (`D/tdq/tdq_datzwoplui1zao16.py`), so unit handling downstream depends on quirk output.
- Device-registry naming: `C/util.py:65-87`.
- Diagnostics expose quirk file:line and originals (`H/helpers/diagnostics.py:71-82`).
- Core has warnings that mention quirks as the recommended remedy for wrong scale/units (`C/sensor.py:1918`, `C/number.py:611`): "use a quirk" - confirms K1 is the sanctioned data-fix route.

## 6. Open questions / surprises

1. `wk_cpmgn2cf.py` docstring says the missing DP is `valve` (dpid 109) but the code only redefines `mode` (dp4) with 7 values incl. "BOOST"; no valve DP is declared. Either docstring stale or code incomplete. Not verified against upstream issue #207.
2. `cl_nfq1essvr99qsvvd.py` sets manufacturer only ("Canisteo"); per `C/util.py:72-78` core then sets `model=quirk.model` (None) and `model_id=quirk.model_id` (None), i.e. loses product_name/product_id in the registry. Looks like a side-effect, not intent.
3. Quirk state (`original_*`) is stored on the shared quirk object (`H/builder/device_quirk.py:231-234`), not per device; and `initialise_device` is invoked each time a device is (re)added (`C/coordinator.py:143-150`), so a re-init snapshot would capture already-quirked state on second run (function is copied AFTER first mutation). Also the mutation is not idempotent-aware (re-run is safe only because entries overwrite).
4. `get_type_information_cls` ignores dpid (`:481-488`); `dpid` in `override_dpid_type_information_cls` is effectively documentation.
5. `tdq_xeagimantb7d7apb.py` defines same dpcodes on two dpids (see section 2 note); in cloud-mode status_range keyed by dpcode, so only one survives; the intent (multi-channel sensor, dp27/102 etc.) is unclear from the quirk. Requires checking how tuya_sharing maps dpid<->code (not in this slice).
6. `support_local` gating: definitions create local_strategy entries only when `device.support_local`; otherwise they pop them (`:150-158`). In the IL, all devices are "local" (via bridge), so the local_strategy portion (K11, and dpid<->dpcode map) is the primary path and the cloud path is irrelevant; conversely, K10/K11 exist because of cloud-vs-local representation differences (cloud reports strings "true"/"false", local reports 0/1); a bridge may deliver yet another representation.
7. `value_convert` supports only `"default"` and `"enum"` via the builder; other strategies present in tuya_sharing local_strategy (e.g. bitmap/other) are not exercised here; unknown.
8. `_DatapointDefinition` for Boolean writes `values="{}"` and Enum `{"range":...}`; whether tuya_sharing/local parsing needs additional keys (e.g. `maxlen`) is unknown (not in this slice).
9. Case-sensitive mixed-case product_ids (see section 1) - verify the bridge reports product_id with original casing.
10. Registry allows only one quirk per product_id; there is no category or model qualifier, and `applies_to` metadata is not matched. Product IDs shared between regional variants must be handled via `apply_when` (kt_hw50w7qvxluhslkk header lines).
11. Custom quirks are arbitrary Python from `/config/tuya_quirks` (`D/__init__.py:42-60`) - an IL replacing this must decide whether to keep a code escape hatch.
12. The quirk stage only touches `function`, `status_range`, `local_strategy`, `status`, `category`. `status` (values) modification exists only for K8 pop and K10 mapping. No quirk sets `device.name/model/etc.`.
