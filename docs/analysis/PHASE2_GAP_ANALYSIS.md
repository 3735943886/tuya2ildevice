# Phase 2: master IL requirements and gap analysis vs current tuya2ha

Target (decided): exact reproduction of HA core `tuya` + tuya-device-handlers 0.0.29 behavior, including core quirks.
Catalog abbreviations: A=A_value_transform, B=B_quirks, C=C_light_climate_fan_humidifier, D=D_cover_vacuum_valve_siren_alarm, E=E_simple_platforms, F=F_core_crosscutting. Catalog references like "C R-L5" are the catalog's own requirement IDs (A and B have no "Requirements" section; A's is the section-4 transform table T1..T25, B's is section 4 "What the IL must carry" plus K1..K14). `H/`=handlers, `CORE/`=core tuya component.
Current-code references: `il`=tuya2ha/il.py, `cls`=classify.py, `dpm`=dp_mapping.py, `conv`=converter.py, `rm`=render_mqtt.py, `eng`=rustuya-local entity_engine.py.
Coverage grades: FULL = current IL/classifier expresses it with equivalent semantics; PARTIAL = some mechanism exists but semantics differ or a piece is missing; NONE = no mechanism. Items marked (UNCERTAIN) rest on unverified points.

## 0. What the current tuya2ha IL is (baseline for the grades)

- `EntityIL` = component, key, grouped flag, display metadata (name/device_class/unit/icon/options/min/max/step/force_update), and `roles: {name: DpRole}` (`il.py:36-58`).
- `DpRole` = a **dp_id** string + readable/writable + write `kind` (bool/number/enum/string/raw) + integer `scale` + optional `val_map` + `stream` state|active (`il.py:18-33`). No transform ops, no validation, no conditions, no literals, no multi-command writes.
- Classification (`cls.py:84-118`) is a pure function of one device dict (no status, no host env). DPs come from `local_strategy || mapping || function` (`cls:124`), keyed by dp_id with the code as an attribute; roles are found by first-matching **regex over dict order** with no type check (`cls:170-173`). Grouped classifiers (cover/climate/fan/light, `cls:195-280`) are hard-coded Python that gate on category via `CATEGORY_MAP` OR a "signature" of code regexes (`dpm:489-526`). Everything else falls to a per-DP "individual" entity chosen by code maps (`cls:283-332`, `dpm:366-421`), with a generic switch/number/select/text/sensor fallback for unknown codes (`cls:291-295`) - i.e. it creates entities core would NOT create.
- Quirks = `Converter.find(product_id)` returning `dp_meta` (add/override dp code/type/meta/ent_info) merged **before** classification (`cls:102-107,146-165`) plus `discovery_overrides` that patch the rendered MQTT payload (`cls:89-93`, `rm:266+`) - the latter is outside core semantics.
- Engine (`eng:97-155`) maps role dp_id -> code, filters updates by "any role code in updated set" (`eng:143-146`), sends single `[{"code","value"}]` (`eng:148-151`), unique_id `rustuya_local.{device.id}.{il.key}` (`eng:129`); MQTT renderer uses `{dev_id}_{key}` (`rm:187`).
- Existing tests: own discovery goldens (`tests/golden`, `test_discovery_golden.py`), not core fixtures.

---------------------------------------------------------------------------------------------------

## 1. Master requirement list

Format per row: ID | requirement | evidence (catalog: file:line / cat ID) | coverage | reason (current code). Where catalogs duplicate a requirement, all source IDs are listed in one row.

### 1.1 INP - Input model

| ID | Requirement | Evidence | Cov | Current reason |
|---|---|---|---|---|
| REQ-INP-01 | Input = `{id,name,category,product_id,product_name,online, function{code:{type,values}}, status_range{code:{type,values,report_type?}}, status{code:value}}`; optional local_strategy/support_local | F R1 (F:§7); D R1 (H/type_information.py:67-118); E R3; C Q4(§6) | PARTIAL | `cls:124` reads only one of local_strategy/mapping/function; `status_range` and `status` never read |
| REQ-INP-02 | Schema keyed by dp CODE; separate dpid<->code map for the local bridge; IL must express "code known" and "dpid only" | F R2; E R3, E open Q6; A open Q12; B §4.3 | PARTIAL | `cls:123-143` keys by dp_id with code as attribute; roles carry `dp_id` only (`il.py:24`); eng translates back via device_manager.code_for_dp (`eng:97-105`) |
| REQ-INP-03 | DP type-name normalisation (Integer/value, Boolean/bool, case-insensitive), `values` as dict OR JSON string | A T1 (const.py:44-63); F R3; C Q3 (type_information.py:151-158) | PARTIAL | JSON-string `valueDesc` handled (`cls:132-135`) but type names compared verbatim `'Boolean'/'Integer'/'Enum'` (`cls:293-294`) |
| REQ-INP-04 | Full DP type model: bool/enum/int/bitmap(labels)/json/raw/string; int min/max/scale/step/unit; enum range; bitmap label; report_type | E R3; A T4,T23; B §4.3 | PARTIAL | meta bag carries scale/min/max/step/options/val_map (`cls:130-140,316-326`); no bitmap labels, no report_type, no json/raw type handling |
| REQ-INP-05 | Readable vs writable / function-vs-status_range distinction retained (drives lookup preference and min/max source) | E R4; D R1; B K6 | PARTIAL | only heuristic `code in functions` (`cls:292`) and comp-based writability (`cls:306`) |
| REQ-INP-06 | Host environment inputs to classification/read/write (HA temperature unit system; `temp_unit_convert` status) | C R-C4, C global Q5; F R11 | NONE | `classify_device(device, converter)` has no env param (`cls:84-86`) |
| REQ-INP-07 | Live `status` values available to classification (existence probing of JSON/parsed attrs, apply_when, temp unit detection at discovery) | A §5.3 (extended.py:118-140,168-194); B K14; F R11 | NONE | classification input has no status (`cls:84-118`) |
| REQ-INP-08 | Read dps that are NOT in the schema (`control_back_mode`, `master_state`, `alarm_msg`) - arbitrary dp reference | D R11 (H/device_wrapper/cover.py:12-24; alarm_control_panel.py:52-71) | NONE | roles must resolve to a schema DP; `eng:97-105` drops the entity if a role dp is missing |
| REQ-INP-09 | Availability = device `online` only; unknown value -> `unknown` state; no per-DP availability | D R14, Q14; E R16; C R-L16; F R9 | FULL | renderer uses single device availability topic (`rm:34`); no per-dp availability in IL |
| REQ-INP-10 | SDK local value-convert strategies (24) may need porting to interpret bridge values (core assumes cloud-converted values) | F Q2, PHASE1 D3; B open Q6-7 | NONE (UNCERTAIN: bridge value form not verified) | no representation of raw-local vs cloud value forms; only `enumMappingMap`->val_map (`cls:136-140`) |

### 1.2 SEL - Entity selection / existence

| ID | Requirement | Evidence | Cov | Current reason |
|---|---|---|---|---|
| REQ-SEL-01 | Category -> per-platform description tables; multiple entities per category; alias/include between tables (cz/pc=kg, dghsxj=sp, tdq=tgq); categories are open strings | E R1,R4; C R-L3,R-L4; D R5 | PARTIAL | `CATEGORY_MAP` category->one component (`dpm:489`), `GENERIC_MAP` small (`dpm:528`); no description tables/aliasing |
| REQ-SEL-02 | Existence = required dp codes present WITH required type; tuple-of-candidates fallback (dpcode-major); `prefer_function` toggle; silently omit otherwise | F R4; A T19 (type_information.py:66-118); E R5; C R-L1,R-L2,R-C2; D R1,R2 | PARTIAL | `_find_dp` first regex match in dict order, no type filter, no ordering semantics (`cls:170-173`) |
| REQ-SEL-03 | Per-platform existence policies incl. degenerate: climate unconditional (no required dp), vacuum always for `sd`, camera always for sp/dghsxj, cover key dp only (zero features ok), fan/humidifier any-of-dpcodes untyped, binary_sensor existence-only untyped, valve/siren boolean key dp, alarm category+enum | D R4; C R-C1,R-F1,R-H1; E R5, R17; (H/definition/*.py) | NONE | climate needs both temp dps (`cls:223`), fan needs speed dp (`cls:247`); one rule shape per classifier |
| REQ-SEL-04 | Existence extras: bitmap label presence, JSON/parsed attribute presence (electricity) | E R5, R12; A §5.3 | NONE | no bitmap/json existence probes |
| REQ-SEL-05 | Every present alternate DP creates its own entity (no dedup/priority among `_F`, `VA_`, `_VALUE` variants) | E R14 | PARTIAL | individual pass creates one entity per DP (`cls:285`), but selection is by code table not core descriptions |
| REQ-SEL-06 | One DP -> N entities (per enum `on_value`, per bitmap label, e.g. ZD shock_state, CS fault x12) | E R13 | NONE | one role/one entity per DP (`cls:305-313`) |
| REQ-SEL-07 | Same DP may back several entities (humidifier+fan share `switch`; `switch_led` + light) | C 3.6, C 4.5 | NONE | `consumed` set prevents reuse (`cls:110-116,283-287`) |
| REQ-SEL-08 | Fixed multi-key entity lists (switch_1..8, control_2/3), indexed names/translation placeholders | D R5 | PARTIAL | per-DP individual naturally yields switch_N (`dpm:369-372`), no placeholders |
| REQ-SEL-09 | Platform coverage: humidifier, vacuum, valve, siren, alarm_control_panel, camera (+ lock/media_player/water_heater explicitly NOT produced) | D §6, D R3-R5, E R17, C §4; PHASE1 §1 | NONE | components limited to cover/climate/fan/light + switch/number/select/text/sensor/binary_sensor/event/button (`cls:80,283-332`); none for the six |
| REQ-SEL-10 | Do NOT emit entities core would not (unknown category -> no entities; no generic fallback sensors/switches/numbers) | E R4 ("no product_id selection", categories open); PHASE1 §3.2 (39/324 fixtures produce none) | NONE | generic fallback creates switch/number/select/text/sensor for any unmapped DP (`cls:291-295`), and category-independent "signature" grouping (`cls:196,218,243,263`) |

### 1.3 LKP - DP lookup / role resolution

| ID | Requirement | Evidence | Cov | Current reason |
|---|---|---|---|---|
| REQ-LKP-01 | Role-based ordered alternatives per role; typed lookup; two-pass typed preference across whole list (fan speed Integer over Enum) | C R-L2,R-C2,R-F2; D R2 (core/cover.py:86-87) | NONE | regex alternation, first in dict order (`cls:170`) |
| REQ-LKP-02 | Per-role function-vs-status_range precedence (mixed within one entity, e.g. humidifier current_humidity status-first, others function-first; vacuum pause status-first) | E R4; C 4.5; D Q6 | NONE | no source distinction |
| REQ-LKP-03 | Absent optional roles legal; features derived from which roles resolved | D R3; C R-C11,R-F9,R-L10 | PARTIAL | optional roles exist in `roles` dict (`cls:231-237,253-257`), but feature derivation is in the renderer/engine, not in IL |
| REQ-LKP-04 | Features derived from enum-range intersection (cover open/close/stop, alarm ARM_HOME/AWAY, vacuum features) | D R3,R7 | NONE | no enum-range-derived capability concept |
| REQ-LKP-05 | Two enum sources vs bool fallback for cover instruction (`{open,close,stop}` vs `{FZ,ZZ,STOP}` vs boolean) | D R7 | NONE | cover command role is `kind=raw` (`cls:204`) |
| REQ-LKP-06 | Multiple dpids may carry the same dpcode (tdq_xeagimantb7d7apb) - later replaces earlier in cloud mode | B §3 note, B open Q5 | PARTIAL | dp_id-keyed so both survive, but `_find_dp` takes first (`cls:170`); core semantics unclear (UNCERTAIN) |

### 1.4 RD - Read transforms (device value -> entity value)

| ID | Requirement | Evidence | Cov | Current reason |
|---|---|---|---|---|
| REQ-RD-01 | Integer scale `raw/10^scale`; validated against [min,max] and must be int; out-of-range/wrong type -> unknown | A T2,T3 (type_information.py:289-295,336); D R17; C R-L15,R-C13 | PARTIAL | `scale` present (`il.py:28`); no min/max validation, no type check |
| REQ-RD-02 | Enum membership validation on read (outside range -> unknown) | A T5 (type_information.py:248-250) | NONE | `val_map.get(raw, str(raw))` passes unknown values through (`eng:234`) |
| REQ-RD-03 | Boolean read accepts True/False/0/1, else unknown | A T6 (:198-199) | NONE | no validation concept |
| REQ-RD-04 | Universal "invalid raw value -> None/unknown, never error" | D R17; E R6; C R-L15 | NONE | no invalid-value semantics in `DpRole` |
| REQ-RD-05 | Boolean inversion (`doorcontact_state`) | A T7 (extended.py:97-106); D R6 | NONE | no invert op in IL |
| REQ-RD-06 | Integer inversion about max (quirk K9), composing with wrapper inversion | A T8 (type_information_ex.py:34-47); B K9; D R6 | NONE | only a rendered-payload `invert` override (`rm:237,292`), not in IL |
| REQ-RD-07 | Linear range remap [raw_min..raw_max]/10^scale <-> target (0..100, 1..100, 0..255), optional reverse, round-half-even on read | A T9,T10 (utils.py:72-87; extended.py:31-73); C R-L5,R-F3; D R6 | NONE | scale only |
| REQ-RD-08 | Round scaled float to int (humidity etc.) | A T10; C R-C10,R-H2 | NONE | |
| REQ-RD-09 | Colour temperature: mired-linear over fixed 2000..6500K, reversed direction | C R-L7 | NONE | color_temp role is plain scaled number (`cls:277`) |
| REQ-RD-10 | Colour codecs: JSON `{h,s,v}` (written as JSON string) and 12-hex `HHHHSSSSVVVV`, per-channel ranges, V1/V2 range derivation rules, `colour_data_hsv` alt | C R-L8 | NONE | no colour role |
| REQ-RD-11 | JSON string parse + dict attribute extraction (with support probing) | A T12,T13; E R6,R12 | NONE | |
| REQ-RD-12 | Base64 -> bytes/text (utf8 / UTF-16BE) | A T14; E R6; D R10 | NONE | |
| REQ-RD-13 | Binary struct codec `electricity_v1v2` (versioned by header+len, sign bitmap, scales), hex-string form, JSON form; field-presence rules | A T15-T17 (raw_data_model.py:45-92); E R12 | NONE | |
| REQ-RD-14 | Bitmap bit test / in-set (scalar|set) comparison / enum-to-number lookup (wind) | E R6; A T11 | NONE | |
| REQ-RD-15 | Enum <-> HA-value maps: forward many-to-one tables, unknown -> None (cover closed map, vacuum 23-entry activity map, alarm mode map, hvac map) | D R7,R8; C R-C5 | PARTIAL | `val_map` dict is many-to-one capable (`il.py:29`) but has no "unmapped -> None", one map per role, no per-value HA semantics |
| REQ-RD-16 | Fan enum speed <-> percentage by ordered position (floor formula, "first bound >= p" write); speed_count | C R-F3,R-F4 | NONE | |
| REQ-RD-17 | Climate hvac_mode: ambiguity elimination (duplicates -> presets), read map NOT filtered against hvac_modes (core quirk), OFF handling, preset residual enum | C R-C5,R-C6,R-C9; C 2.9 | NONE | climate `mode` role is raw val_map (`cls:232-234`) |
| REQ-RD-18 | Composite swing: 3 booleans -> 5-value enum | C R-C8 | NONE | |
| REQ-RD-19 | Runtime temperature unit selection/conversion (dual C/F dps, unit alias sets), applied on read and write; min/max NOT converted (core quirk) | C R-C4, C 2.9; F R11 | NONE | |
| REQ-RD-20 | Dynamic brightness limits from two other integer DPs (only when both exist), read and write | C R-L6 (device_wrapper/light.py:20-95) | NONE | |
| REQ-RD-21 | Light color_mode derived from work_mode ("white" vs other = HS; missing work_mode = white); supported-mode set (ONOFF/BRIGHTNESS/HS/COLOR_TEMP/WHITE) | C R-L9,R-L10 | NONE | |
| REQ-RD-22 | Delta accumulator reader (per-entity running sum, timestamp de-dup, not persisted) | E R8; A T23; F R7 | PARTIAL | `stream="active"`+`force_update` treats `add_ele` as an event-like value (`il.py:30-34`, `cls:301`, `dpm:440`); no accumulation - different semantics from core |
| REQ-RD-23 | Sensor unit/device_class reconciliation: alias table (~35 rows, canonicalisation only), temp_unit_convert fallback, fallback-to-description-unit, drop-device_class branches, suggested_unit | E R11 (C/const.py:1034-1229; T/test_sensor.py:236-326); A T18,T24 | PARTIAL | own `UNIT_NORM_MAP`/`DEFAULT_UNITS_BY_CLASS` (`dpm:334-363`, `cls:185-190,303`) - different table and rules |
| REQ-RD-24 | Event payload attribute extraction (`message` from base64 string/raw) | E R9 | NONE | |

### 1.5 WR - Write transforms and command emission

| ID | Requirement | Evidence | Cov | Current reason |
|---|---|---|---|---|
| REQ-WR-01 | Write = ordered list of `{code,value}`; empty = no-op; code->dpid translation at the edge | F R8; E R7; D R12; A T22 | PARTIAL | engine emits a single command (`eng:148-151`); IL has no command-list notion |
| REQ-WR-02 | Scaled-int encode `round(v*10^scale)` (banker's), range-checked, step not enforced | A T2,T3 (:323-328) | PARTIAL | `_unscale` rounds (`eng:93-94`); no range check |
| REQ-WR-03 | Validation on write: reject (raise SetValueOutOfRange), never clamp; enum membership; real bool | A T3,T5,T6; C R-L15,R-C13; D R12 | NONE | |
| REQ-WR-04 | Inverse of every read transform (remap, inversion, mired, colour codecs, enum maps, position/percentage) | A T7-T9; C R-L7,R-L8; D R6 | NONE | |
| REQ-WR-05 | Ordered multi-command writes: light turn_on (switch, work_mode, color_temp, color_data\|brightness), hvac set (switch then mode), swing (on_off, vertical, horizontal), fan turn_on (switch, speed, preset) | C R-L11,R-C7,R-C8,R-F8; F R8 | NONE | |
| REQ-WR-06 | Write recipes conditional on entity's current derived state (light HS/V read-modify-write; ATTR_WHITE; percentage==0 -> switch off; turn_on with no switch = no-op) | C R-L11,R-L12,R-F8 | NONE | |
| REQ-WR-07 | Constant / literal writes (button True; `"white"`/`"colour"`; FZ/ZZ/STOP; turn_off = switch false only) | E R7; C R-L9,R-L13; D R19 | NONE | write kind only (`il.py:27`); no constants |
| REQ-WR-08 | Ordered fallback on write (cover open/close sends only position if a position dp exists; vacuum return_to_base prefers switch_charge over mode=chargego) | D R9, Q4 | NONE | |
| REQ-WR-09 | Cross-dp conditional inversion on write (cover `control_back_mode != "back"`) | D R6 | NONE | |
| REQ-WR-10 | Distinct error semantics: missing control DP at action time raises (humidifier) vs silent no-op (modes) | C R-H4 | NONE | |
| REQ-WR-11 | Free-form passthrough `send_command(code,value)` and device-level services (feeder meal_plan, response-returning) | D R12; F R13; B K12 | NONE | (services are likely out of scope; PHASE1 §4) |

### 1.6 XDP - Cross-dp rules and state derivation

| ID | Requirement | Evidence | Cov | Current reason |
|---|---|---|---|---|
| REQ-XDP-01 | Transform conditional on another dp's live value (cover inversion, evaluated at read and write) | D R6,R11 | NONE | |
| REQ-XDP-02 | Cross-dp state override with content inspection (alarm TRIGGERED if master_state=="alarm" unless decoded alarm_msg contains "Sensor Low Battery"; changed_by from gated Raw dp) | D R10 | NONE | |
| REQ-XDP-03 | Ordered precedence/fallback among dps on read (cover is_closed: position==0 else state wrapper; vacuum activity: status > pause > ...) | D R9 | NONE | cover `state`/`position` are independent roles (`cls:205-211`) |
| REQ-XDP-04 | hvac state from switch+mode (switch False -> OFF; switch-only fallback) | C R-C7 | NONE | climate `power` role is `readable=False` (`cls:236`); no derivation |
| REQ-XDP-05 | Small predicate language over dp values / dp presence (`>=`, ==, substring, in-set) usable for quirk apply_when, cross-dp conditions and state derivation | B K14; C R-C12; F R5; E R18 | NONE | |
| REQ-XDP-06 | Many-to-one derived state (vacuum status->activity 23 entries, alarm mode->state) with "unknown" default | D R8 | PARTIAL | see REQ-RD-15 (single-dp val_map only) |

### 1.7 ENT - Entity metadata and capability data

| ID | Requirement | Evidence | Cov | Current reason |
|---|---|---|---|---|
| REQ-ENT-01 | Descriptor fields: platform, key (unique-id suffix), translation_key, translation_placeholders, entity_category, device_class, state_class, icon, name (None => device name / literal), entity_registry_enabled_default, unit, suggested_unit | F R6; E R15; D R5 | PARTIAL | has name/device_class/unit/icon/key (`il.py:46-56`); lacks translation_key/placeholders/entity_category/state_class/enabled_default/suggested_unit |
| REQ-ENT-02 | Names without HA i18n need fallback English strings (strings.json content not in handlers) | E R15, E Q9 | PARTIAL (UNCERTAIN: target consumer) | auto-name from code (`cls:312`) |
| REQ-ENT-03 | Numeric limits exported: number min/max/step (scaled), climate temp min/max/step, humidifier min/max (else 0-100), fan speed_count | A T4; C R-C3,R-H2,R-F4 | PARTIAL | number min/max/step only, `cls:315-322`; scaled |
| REQ-ENT-04 | Option lists: select options from enum range; climate preset/fan modes raw enum strings; fan direction restricted to {forward, reverse}; humidifier modes | C R-C6,R-C9,R-F5,R-F7,R-H3; E (select) | PARTIAL | `options` for select/event (`cls:323-330`) |
| REQ-ENT-05 | Event entity: on-report trigger only (not on snapshot), fires even on repeated value, event_types from enum or fixed `["triggered"]`, optional attribute | E R9 | PARTIAL | `stream="active"` for events (`cls:301-309`); event_types get extra hard-coded click types (`cls:330`) not from core |
| REQ-ENT-06 | Device info: manufacturer default "Tuya", model/model_id from product; quirk override only when quirk manufacturer set (model/model_id become None otherwise - core quirk) | F R12; B K13, B Q2 | PARTIAL | `model` from user_info or product_name (`cls:100,105`) |
| REQ-ENT-07 | Camera: `{motion_detection_dp?, recording_dp?, stream provider out-of-band}` | E R17 | NONE | |
| REQ-ENT-08 | Hard-coded literals as data ("Sensor Low Battery", FZ/ZZ/STOP, chargego, back, white/colour) | D R19 | NONE | literals only in table code (`dpm`) not as transform arguments |
| REQ-ENT-09 | Platform-specific fixed semantics as data: fan oscillation = first of horizontal/vertical (quirk selectable), vacuum feature set, siren co2bj entity_category CONFIG, cover tilt unverified path (angle_horizontal/vertical) | C R-F6; D R3,R5, D Q2 | NONE (tilt UNCERTAIN, no fixtures) | oscillation only `fan_horizontal` (`cls:246`) |

### 1.8 LIF - Lifecycle and update semantics

| ID | Requirement | Evidence | Cov | Current reason |
|---|---|---|---|---|
| REQ-LIF-01 | Per-entity update-relevance set (dp codes an entity depends on; composite = source dps; cross-dp inputs included); update with `None` (online/name only) always refreshes | E R10; D R13 (valve/siren filter, cover/vacuum/alarm always rewrite); A T21; F R7,R9 | PARTIAL | eng filters on role codes (`eng:143-146`) - derived from roles, ignores cross-dp deps and the always-rewrite platforms; not an IL field |
| REQ-LIF-02 | Update contract `(device_id, updated_codes\|None, dp_timestamps\|None)`; status mutated before signal | F R9; F R7 | PARTIAL | eng signature has both args (`eng:143-145`), timestamps unused (`_dp_timestamps`); not specified in IL |
| REQ-LIF-03 | Per-entity persistent reader state (delta accumulator; not persisted across restart) | E R8; F R7; A §5.6 | NONE | IL is stateless |
| REQ-LIF-04 | Per-DP timestamps from transport (needed by delta de-dup) | E R8, E Q5 (UNCERTAIN whether bridge has them) | NONE | |
| REQ-LIF-05 | Lifecycle events: add (re-classify, drop stale registry entries), remove, rename, online/offline, schema-change re-classify (added beyond core) | F R10 | PARTIAL | classification is a pure re-runnable function (`cls:84`); no diff/stale-entity API |
| REQ-LIF-06 | No optimistic/assumed state, no debounce | D R15; F cross-cutting | FULL | none in IL/engine |

### 1.9 QRK - Quirks

| ID | Requirement | Evidence | Cov | Current reason |
|---|---|---|---|---|
| REQ-QRK-01 | Keyed by exact (case-sensitive) product_id, one quirk per id, run before classification and device-info, entries applied in order (later replaces earlier) | B §1,§4.10, B Q9-10; F R5 | PARTIAL | `converter.find(product_id)` then merge before classify (`cls:102-107`); no order/replace semantics beyond deep_merge (`conv:40-49`) |
| REQ-QRK-02 | Add/replace Integer DP (range/scale/unit/step/report_type) - K1 (14 files) | B K1 | FULL | `_merge_user_dp` overrides type/meta (`cls:146-162`) (report_type has no slot: PARTIAL for that field) |
| REQ-QRK-03 | Add/replace Enum DP (option range) - K2 (13 files) | B K2 | FULL | meta `range`/`options`/`val_map` (`cls:162,324`) |
| REQ-QRK-04 | Declare a DP absent from the cloud model (dpid+code+type) - K3 | B K3 | FULL | injection branch (`cls:148-154`) |
| REQ-QRK-05 | Add Bitmap (K4) / Boolean (K5) DP | B K4,K5 | PARTIAL | boolean fine; bitmap has no consumer/labels |
| REQ-QRK-06 | dpmode R/W/RW controls function/status_range membership (K6) | B K6 | NONE | no readable/writable slot in dp_meta; writability derived from component (`cls:306`) |
| REQ-QRK-07 | Category override (K7) | B K7 | NONE | category read from device (`cls:95`) and not patched by user_info |
| REQ-QRK-08 | Remove DP incl. status (K8) so defaults fall back | B K8 | NONE | `_merge_user_dp` only adds/overrides |
| REQ-QRK-09 | Integer invert flag (K9; TypeInformation class swap) as closed named transform | B K9, B §4 | NONE | see REQ-RD-06 |
| REQ-QRK-10 | Initial-status value mapping (K10) and local enum value-convert map (K11) | B K10,K11 | PARTIAL | `enumMappingMap`->val_map read side (`cls:136-140`); no status rewrite |
| REQ-QRK-11 | Conditional entries: `apply_when` variant predicate (K14) | B K14; C R-C12; F R5 | NONE | |
| REQ-QRK-12 | Device-info override (manufacturer/model/model_id) - K13, 15 files | B K13 | PARTIAL | `model` only (`cls:105`) |
| REQ-QRK-13 | Feeder-schedule wrapper binding (K12) | B K12 | NONE (likely out of scope, PHASE1 §4) | |
| REQ-QRK-14 | Same dpcode on multiple dpids in one quirk | B §3 | PARTIAL | see REQ-LKP-06 |
| REQ-QRK-15 | Code escape hatch / user Python quirks (custom_quirks dir, unused `definition_fn`) | B Q11; C global Q2; E Q4; D Q11 | NONE (decision D4 pending; IL should reserve extension point) | `discovery_overrides`/`custom_converters` are JSON only (`cls:89-93`) and patch rendered MQTT, non-core |
| REQ-QRK-16 | Quirks may select platform sub-behavior (fan oscillation dp choice, hvac fixes, cover position invert intent) | C R-F6,R-F10; B K9 | NONE | |

### 1.10 UID - Unique-id compatibility

| ID | Requirement | Evidence | Cov | Current reason |
|---|---|---|---|---|
| REQ-UID-01 | Entity identity = `tuya.{device_id}{key}` (no separator) if migration from core is wanted (open decision) | F R16; D R18 (entity.py:35); E R2, E Q7 | NONE | `eng:129` uses `rustuya_local.{id}.{key}`; `rm:187` `{dev_id}_{key}` |
| REQ-UID-02 | `key` explicit and independent of the actual dpcode, incl. computed keys (`{dpcode}electriccurrent`, `fault_{label}`, `shock_state_{value}`, `total_power`+`power`, empty for camera), stable when dpcode alternatives resolve differently (`switch` vs `switch_spray`) | E R2 (C/sensor.py:70,81,1673-1675; C/binary_sensor.py:78-86); C R-H5 | PARTIAL | `key` field exists (`il.py:47`) but individual key = dp code (`cls:310`), grouped keys are fixed slugs (`motor/thermostat/fan/led`) unrelated to core keys |
| REQ-UID-03 | Multiple platforms/entities may share a dp with distinct unique_ids (humidifier `tuya.<id>switch` vs fan `tuya.<id>`) | C 4.5 | NONE | see REQ-SEL-07 |

### 1.11 TST - Testing

| ID | Requirement | Evidence | Cov | Current reason |
|---|---|---|---|---|
| REQ-TST-01 | Golden tests from core's 324 fixtures with quirks off (1205 entities; 285 fixtures produce entities, 39 none) | F R15; PHASE1 §2, D6 | NONE | own goldens (`tests/golden`, `test_discovery_golden.py`) only |
| REQ-TST-02 | Compare unique_id suffixes (fixture device id reversed) and registry properties from snapshots | F R15 | NONE | |
| REQ-TST-03 | Fixture DP values as dict OR JSON string; only 16/324 fixtures carry local_strategy (need dp-id synthesis for the rest) | C global Q3; F R15,Q1 | NONE | |
| REQ-TST-04 | Quirk-on cases for the 32 quirk files added on top | PHASE1 D6; B | NONE | |
| REQ-TST-05 | State/command behavior tests (snapshots hold state; read/write pure functions testable) | F §6; A (pure read/write) | NONE (UNCERTAIN: test tooling not examined) | |

---------------------------------------------------------------------------------------------------

## 2. Summary counts

| Theme | Total | FULL | PARTIAL | NONE |
|---|---|---|---|---|
| INP | 10 | 1 | 5 | 4 |
| SEL | 10 | 0 | 4 | 6 |
| LKP | 6 | 0 | 2 | 4 |
| RD | 24 | 0 | 4 | 20 |
| WR | 11 | 0 | 2 | 9 |
| XDP | 6 | 0 | 1 | 5 |
| ENT | 9 | 0 | 6 | 3 |
| LIF | 6 | 1 | 2 | 3 |
| QRK | 16 | 3 | 5 | 8 |
| UID | 3 | 0 | 1 | 2 |
| TST | 5 | 0 | 0 | 5 |
| **All** | **106** | **5** | **32** | **69** |

Note: rows graded 'NONE (UNCERTAIN...)' or 'PARTIAL (UNCERTAIN...)' are counted under the leading grade.

---------------------------------------------------------------------------------------------------

## 3. Structural vs incremental gaps

### 3.1 Structural gaps (force an IL data-model change, not a new field)

S1. **Schema/DP addressing model.** IL identifies DPs by numeric `dp_id` inside roles (`il.py:24`) and classifier keys by dp_id with code as attribute (`cls:123-143`). Core identifies by code, with a typed schema (type/range/scale/step/enum/labels, function vs status_range) and existence depends on it. IL needs a code-keyed typed schema plus a code<->dpid map as first-class inputs. (REQ-INP-01/02/04/05, REQ-SEL-02, REQ-LKP-01/02, REQ-QRK-06)
S2. **Role = single dp + scale + val_map is too small.** Needs a role that is a *pipeline* (ordered read ops, inverse write ops, validation) over one or several dps, including composite readers (swing 3 bools, hvac from switch+mode, colour, electricity). (REQ-RD-*, REQ-WR-04)
S3. **Cross-dp references and conditions** (inversion by another dp, alarm override, cover precedence, brightness limits, predicate language) - needs dependency edges between roles and a condition expression type, plus references to non-schema dps. (REQ-XDP-*, REQ-INP-08, REQ-WR-09)
S4. **Writes as ordered command programs**, possibly conditional on derived entity state, rather than `kind` encoding of one dp. (REQ-WR-05/06/07/08)
S5. **Entity existence rules as data with per-platform policies incl. degenerate/unconditional/any-of/per-value fan-out and shared-dp entities.** Current: grouped Python classifiers + `consumed` set that forbids sharing. (REQ-SEL-02..07, REQ-SEL-10)
S6. **Description-table selection model** (category -> ordered list of descriptions, alias/include, one description -> N entities) replacing category->one component + regex DP tables. (REQ-SEL-01/05/06/08)
S7. **Classification input must include live status and host environment** (unit system, `temp_unit_convert`, probe-existence, apply_when). Today classification is a pure function of the schema. (REQ-INP-06/07, REQ-RD-19, REQ-QRK-11)
S8. **Stateful readers + timestamps in the update contract** (delta accumulator; per-entity state slot; update event carrying dp_timestamps). IL currently stateless, `stream` flag is a substitute with different semantics. (REQ-RD-22, REQ-LIF-02/03/04)
S9. **Update-relevance/dependency sets and per-platform "rewrite on any update" vs "only if own dp changed"** (must be computed from the role graph, including cross-dp inputs). (REQ-LIF-01)
S10. **Quirk model:** category override, DP removal, dpmode R/W, status rewrite, predicate-guarded entries, ordered replace semantics operate on the *schema* before classification (patch layer), whereas current quirks merge into a flat per-dp meta bag and `discovery_overrides` patch rendered payloads. (REQ-QRK-06/07/08/10/11, REQ-QRK-01)
S11. **Entity identity/key model** decoupled from dpcode, from grouped slugs, and format-compatible with core (`tuya.{id}{key}`) if migration is required. (REQ-UID-01/02/03)
S12. **Six missing platform shapes** (humidifier, vacuum, valve, siren, alarm_control_panel, camera) each needing derived-feature capability sets (from enum-range intersection). Whether this is structural depends on S2/S3/S5 choices; capabilities-from-schema (REQ-LKP-04) is itself structural.

### 3.2 Incremental gaps (new field, new named op, or new table on an adequate model)

- New named read/write ops once a pipeline exists: bool invert, int invert-about-max, linear remap, round, mired/kelvin, HSV json/hex, JSON attribute, base64/utf16 text, electricity codec, bitmap test, in-set, enum-to-number, enum-position percentage (REQ-RD-05..14,16,18).
- Validation-on-read/write flags (REQ-RD-01..04, REQ-WR-03).
- Extra metadata fields: translation_key, placeholders, entity_category, state_class, enabled_default, suggested_unit (REQ-ENT-01); climate/humidifier/fan limits (REQ-ENT-03); fan direction restriction (REQ-ENT-04).
- Type-name normalisation, report_type/bitmap-label slots (REQ-INP-03/04 partially).
- Device-info override rule (REQ-ENT-06, REQ-QRK-12), event_types from core (REQ-ENT-05).
- Core unit alias table replacing `UNIT_NORM_MAP` (REQ-RD-23).
- Literal carrying, once ops take arguments (REQ-ENT-08, REQ-WR-07).
- Golden/test infrastructure (REQ-TST-*).
- Decision-dependent: SDK value-convert strategies (REQ-INP-10, UNCERTAIN), user-Python quirk hatch (REQ-QRK-15), services/feeder (REQ-WR-11, REQ-QRK-13), camera stream (REQ-ENT-07).

### 3.3 Divergences from core in the CURRENT design (behavior to remove/replace, not just extend)

- Generic fallback entities for unknown DPs and unmapped categories (`cls:291-295`); signature-based grouping regardless of category (`cls:196,218,243,263`); both create entities core would not (REQ-SEL-10).
- `add_ele` treated as active-only event-like sensor rather than delta accumulator (REQ-RD-22).
- Cover `state` and `position` read independently; cover command is raw; climate power not readable (REQ-XDP-03/04).
- `discovery_overrides` patches rendered MQTT (`cls:89-93`) - no analogue in core.
- Event types get hard-coded click types appended (`cls:330`) - not evidenced in core catalog (UNCERTAIN whether core does this).

---------------------------------------------------------------------------------------------------

## 4. Design axes / questions for the Phase 3 spec

Each axis lists the requirements it must satisfy and the option space.

A1. **Schema input model.** Code-keyed typed schema (`function`, `status_range`, `status`, `category`, `product_id`, `online`) + dpid<->code table + optional host env; how bridge-only devices (no cloud schema) are synthesized (from local_strategy, static per-product tables, or category assumptions); representation of `values` as dict vs JSON string; type-name normalisation at the boundary. Must satisfy REQ-INP-01..07, REQ-TST-03. Open: does the bridge supply function/status_range? (E Q5, D Q1: UNCERTAIN).
A2. **dp-code vs dp-id.** Recommended shape to decide: IL references codes (like core); a separate adapter resolves code->dpid at the edge (writes) and dpid->code (reads). Must satisfy REQ-INP-02, REQ-WR-01, REQ-LKP-06 (dup codes on several dpids), REQ-QRK-14, REQ-INP-08 (non-schema codes).
A3. **Transform representation: named presets vs composable ops.** Core has ~15 fixed algorithms (A §5, C §5, E R19: "~15 named readers"); composition occurs mainly as inversion-on-top-of-remap and scale+round. Choose closed set of named codecs with parameters vs small op pipeline with automatic inverse. Every read op needs a defined inverse or `write: none`. Must satisfy REQ-RD-01..24, REQ-WR-02..04, REQ-QRK-09. Also decide the closed-set boundary vs a code escape hatch (REQ-QRK-15).
A4. **Validation semantics.** Read: invalid -> unknown (with numeric types strict: ints only, bool accepted as int, floats rejected - A Q2); write: reject not clamp; where the reject error surfaces. Must satisfy REQ-RD-01..04, REQ-WR-03, REQ-WR-10; core quirks to keep or not (A Q1 bool leaks int, A Q2, A Q3 KeyError on missing step).
A5. **Cross-dp condition language.** Expression over dp values (incl. non-schema dps): ==, in, >=, contains, decoded-substring, presence; usable in (a) transform selection (cover inversion), (b) state override (alarm), (c) quirk apply_when, (d) existence gating. Decide one language for all four uses. Must satisfy REQ-XDP-01..05, REQ-QRK-11, REQ-INP-07/08.
A6. **State derivation model.** How an entity state is a function of several dps: ordered fallback list, override rules, many-to-one tables with unknown default, derived color_mode/hvac_mode/is_closed/activity. Decide "state expression" vs per-platform derived fields with fixed algorithms. Must satisfy REQ-XDP-03/04/06, REQ-RD-15,17,21, REQ-LKP-03/04.
A7. **Write program model.** Command list ordering (atomic batch vs sequence), conditional steps, read-modify-write against derived entity state, constants, ordered fallback (position vs instruction), no-op on empty, fan turn_on semantics. Must satisfy REQ-WR-01,05..10, REQ-RD-19 (unit conversion on write). Include how per-service inputs (turn_on kwargs) bind to steps.
A8. **Entity existence rules.** Rule kinds: typed-required, any-of untyped, existence-only, always/unconditional (degenerate), bitmap-label, json-attribute presence, category-only, per-enum-value fan-out; lookup source order (function/status_range) and typed multi-pass. How degenerate entities (zero features) are expressed and rendered. How shared dps are allowed. Must satisfy REQ-SEL-01..10, REQ-LKP-01..05, REQ-UID-03.
A9. **Description tables and category aliasing.** Data format for ~617 literal descriptions (834 expanded), 98 referenced categories, alias/include, tuple-expanded variants (`f"{dpcode}_{n}"`); whether unknown categories yield nothing (core) or a fallback (current). Must satisfy REQ-SEL-01/05/08/10, REQ-ENT-01.
A10. **Quirk patch model.** Patch applied to the schema (add/replace/remove dp with dpid, type, values, mode, report_type; category override; status initial mapping; ordered; later replaces earlier; predicate-guarded) before classification; how it carries the closed-set `invert` flag; device-info override; whether custom user quirks (JSON only) are kept; interplay with existing `custom_converters`/`discovery_overrides`. Must satisfy REQ-QRK-01..16, REQ-INP-07 (predicate needs status).
A11. **Update-relevance sets.** Explicit per-entity `depends_on` (codes) computed from role graph incl. cross-dp inputs; None-updated => always refresh; per-platform rewrite policy (valve/siren filter; cover/vacuum/alarm always). Must satisfy REQ-LIF-01/02, REQ-XDP-01..03.
A12. **Per-entity state for the delta accumulator.** Where state lives (IL declares slot, host holds value), timestamp input contract, restart behavior (starts at 0), what happens if bridge lacks per-DP timestamps (UNCERTAIN). Must satisfy REQ-RD-22, REQ-LIF-03/04; contrast with existing `stream` active/state semantics (REQ-ENT-05, `il.py:30`).
A13. **Literal carrying.** All protocol/UI literals (FZ/ZZ/STOP, chargego, back, "Sensor Low Battery", white/colour, on/off strings, timezone-free names) as IL data arguments to ops, not renderer code. Must satisfy REQ-ENT-08, REQ-WR-07, REQ-XDP-02, REQ-RD-21.
A14. **Entity identity.** Explicit `key` vs dpcode; core-compatible unique_id format or a mapping; computed keys; per-description keys; behavior on duplicates. Decision needed: is registry continuity with core `tuya` a goal? Must satisfy REQ-UID-01..03, REQ-SEL-06/07.
A15. **Capability/feature derivation.** Supported features (cover OPEN/CLOSE/STOP/POSITION/TILT, vacuum, alarm, climate, fan, light color modes) computed from resolved roles and enum ranges - in IL (data) or by renderer. Must satisfy REQ-LKP-03/04/05, REQ-RD-21, REQ-ENT-09. Interaction with core's reproduced quirks (climate unfiltered read map, C 2.9; cover open/close sends only position, D Q4; hvac modes not in hvac_modes).
A16. **Host environment & availability.** Units, `online` bit, name updates; whether env is part of classification input or of read/write context; behavior when unit changes after discovery (core does not track). Must satisfy REQ-INP-06/09, REQ-RD-19.
A17. **Metadata split.** What is IL (device_class, unit, limits, options, translation keys, entity_category, enabled_default, state_class, icon) vs renderer (topics/templates). Fallback names for non-HA-i18n consumers. Must satisfy REQ-ENT-01..06.
A18. **Extension/escape hatch and scope boundary.** Decisions D3 (SDK value-convert strategies), D4 (data-only vs code hooks; feeder/services), D5 (lock etc.). Must satisfy REQ-INP-10, REQ-QRK-13/15, REQ-WR-11, REQ-ENT-07.
A19. **Testing strategy.** Fixture ingestion (dict/JSON-string values, dpid synthesis for 308 fixtures), unique_id-suffix comparison, quirk-on/off matrix, pure read/write assertions, snapshot-derived state expectations. Must satisfy REQ-TST-01..05.
A20. **Reproduced core quirks list.** Since target is exact reproduction, spec must enumerate quirks to preserve (each is a requirement with test): unfiltered hvac read map (C 2.9), hvac_mode not in hvac_modes, cover open/close position-only (D Q4), min/max not unit-converted (C 2.9), model/model_id None when quirk sets only manufacturer (B Q2), inversion about max ignoring min (A Q4/B K9), banker's rounding lossy round-trip (A Q11), light brightness maps DP min as 0 (C 1.9), missing-work_mode => white (C 1.9), temp unit read once at discovery (C 2.9), integer read rejects floats (A Q2), bool read leaks 0/1 (A Q1). Tag each as reproduce/fix.

---------------------------------------------------------------------------------------------------

## 5. Caveats

- Requirement list is derived from catalog "Requirements for the IL" sections C, D, E, F and, for A and B (which have no such section), from A section 4 (T1-T25 + section 5) and B section 4 + K1..K14. Catalog claims were not re-verified against core source in this phase, except that current-code coverage grades were made from reading tuya2ha (`il.py`, `classify.py`, `dp_mapping.py`, `converter.py`), the `render_mqtt.py` function outline and selected lines of `entity_engine.py`.
- Grades reflect the IL/classifier only; renderer/engine capabilities are cited only when they show what the IL would have to carry. `render_mqtt.py` templates were not read in full, so any transform implemented purely inside Jinja templates (e.g. position invert, `rm:237,292`) is graded as not in the IL.
- Items marked UNCERTAIN depend on unverified facts: bridge-supplied schema/timestamps/value forms, SDK value-convert strategies, cover tilt and `mach_operate` paths (no fixtures), event_types hard-coding.
