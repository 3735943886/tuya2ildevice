# PHASE 3: adversarial coverage review of IL_SPEC.md (DRAFT 1)

Reviewed: IL_SPEC.md against PHASE2_GAP_ANALYSIS.md (106 REQ, S1-S12, A1-A20), catalogs A-F, and core/handlers source (spot-checked: light.py, fan.py, climate.py + handlers climate/fan/cover/light/alarm/vacuum/sensor/binary_sensor/extended wrappers, definitions, util.py, registry.py, const.py).

Legend: C = COVERED (a concrete spec construct can express it; "C*" = constructs exist but a semantic detail is unspecified), P = PARTIAL, U = UNCOVERED, O = OUT-OF-SCOPE-BY-DECISION. Section refs are IL_SPEC.md sections.

## 0. Headline

- REQ (106): COVERED 74, PARTIAL 26, UNCOVERED 2, OUT-OF-SCOPE-BY-DECISION 4.
- S1-S12: COVERED 9, PARTIAL 3. Axes A1-A20: answered 17, partly open 3.
- 8 blocking issues (section 6). Most "covered" rows are covered only at the level of *names in a closed registry*; the expression grammar (section 3) is too thin to actually write several of the platform recipes the spec claims.
- The biggest structural hole: `Role` has ONE `dpref` and ONE `read` pipeline, but core resolves *type-dispatched alternatives* (fan speed Integer|Enum, cover instruction Enum|Boolean, sensor Integer(sum)->Delta | Integer | Enum, electricity Raw|Json, colour Json|String, binary Boolean|InSet). Read pipeline, write pipeline, options and units all depend on which alternative matched.

## 1. REQ coverage table (all 106)

| REQ | Verdict | Spec section / construct, or what is missing |
|---|---|---|
| REQ-INP-01 | C | 1.2 `DeviceSchema` (function/status_range/status/online/category/product_id). local_strategy/support_local intentionally absent (adapter). |
| REQ-INP-02 | C | 1.2 `dpmap`, R0.2; codes everywhere, dpids only in `DefineDp`/`DpMapEntry`. |
| REQ-INP-03 | C | 1.1 `normalize_type` (exact + lowercase aliases). Verified correct vs const.py (uppercase `ENUM`/`STRING` are NOT parsed by core; catalog F sec.2 claim "tolerates UPPER" is wrong, the spec is right). |
| REQ-INP-04 | C | 1.1 `DpType`, `SpecInteger/Enum/Bitmap`, `report_type`. (No `SpecJson`; see RD-10.) |
| REQ-INP-05 | C | 1.2 separate `function`/`status_range` + 2 `DpRef.source`. |
| REQ-INP-06 | C | 1.2 `HostEnv.temperature_unit`; `temp_unit_convert` reachable via `status`. (HA allowed-unit sets not an input: see RD-23.) |
| REQ-INP-07 | C | 1.2 `status` "needed by classify". |
| REQ-INP-08 | C | 3 `Status(code)` "may name codes NOT in schema". |
| REQ-INP-09 | C | 7 `State.available = schema.online`. |
| REQ-INP-10 | O | R0.2 / 10: SDK value-convert in adapter (decision D-adapter). Justified. |
| REQ-SEL-01 | C | 4 `tables[platform][category]`, aliases as data, open strings. (Aliases are per platform, see factual errors.) |
| REQ-SEL-02 | C | 2 `DpRef` + `resolve`, 4 `TypedDp`. |
| REQ-SEL-03 | C | 4 `Always`, `AnyName`, `KeyPresent`, `KeyTyped+Enum`. |
| REQ-SEL-04 | C | 4 `BitmapLabel`, `ParsedAttr`. |
| REQ-SEL-05 | C | 4 ordered `EntityDef` list, "no `consumed` set". |
| REQ-SEL-06 | C | 4 `expand` (or simply N literal EntityDefs, which is how core has it). `expand` args are undefined but not needed. |
| REQ-SEL-07 | C | 4 last paragraph. |
| REQ-SEL-08 | C | 6 `identity.naming{translation_key, placeholders, name}`. |
| REQ-SEL-09 | P | Humidifier/vacuum/valve/siren/alarm in 8. **Camera missing** from 8 and from non-goals (non-goal says only "camera streaming"). |
| REQ-SEL-10 | C | 4 "Unknown category => nothing"; Q2 (opt-in residual layer) left open but default is parity. |
| REQ-LKP-01 | P | 2 `candidates` + `types` + `multi_pass:bool`. Pass order for multi_pass unspecified (order of `types`?), and the matched *kind* cannot select a different pipeline (Role has one `read`). |
| REQ-LKP-02 | C | 2 `DpRef.source` per role. |
| REQ-LKP-03 | P | `Role.required` + 6.3 prose. Cond (3) has no `Found(role)` primitive; behavior of a Step on an unresolved optional role (skip) is not stated. |
| REQ-LKP-04 | P | 3 `RangeContains(code, lit)` is keyed by *code*, not by resolved role; cannot express cover Boolean-fallback options ["open","close"] or the resolved enum range of the alt that actually matched. |
| REQ-LKP-05 | P | Three instruction sources (open/close/stop enum, FZ/ZZ/STOP enum, Boolean) need alternatives with different action maps; only `FirstApplicable` on *writes* exists, nothing on reads/features. |
| REQ-LKP-06 | C | 1.3 note (dpmap keeps both dpids, function/status_range last-writer-wins). |
| REQ-RD-01 | C | 5 `validate_int`. |
| REQ-RD-02 | C | 5 `validate_enum`. |
| REQ-RD-03 | C | 5 `validate_bool`. |
| REQ-RD-04 | C | R0.4, UNKNOWN short-circuit. (Contradicted by raising core paths, see contradictions C2.) |
| REQ-RD-05 | C | 5 `invert_bool`. |
| REQ-RD-06 | P | `invert_int_max` op exists, but the quirk `DpTypeOverride(code, flag)` has nowhere to live: `DeviceSchema` has no override slot, and how `resolve()` (dpcode-only, any role) inserts the op into pipelines is unspecified. Flag name inconsistent (`invert_int` in 1.3 vs `invert_int_max` in 5/P-06). |
| REQ-RD-07 | C | 5 `remap` (scaled src, `reverse: Cond`, banker's round). |
| REQ-RD-08 | C | 5 `round_int`. |
| REQ-RD-09 | C | 5 `mired_kelvin`. Kelvin min/max exported via 6.3. |
| REQ-RD-10 | P | `hsv_json/hsv_hex(ranges)` named, but range derivation is absent: function-data h/s/v min/max (needs a `SpecJson` accessor), V1/V2 fallback triggered by 3 conditions (description flag, dpcode == `colour_data_v2`, brightness role max > 255), Json-then-String type alternatives, `colour_data_hsv`. Only promised in 13. |
| REQ-RD-11 | C | 5 `parse_json`, `json_attr`; 4 `ParsedAttr` covers json-key probe. |
| REQ-RD-12 | C | 5 `b64_decode`, `decode`. |
| REQ-RD-13 | C | 5 `parsed_attr(codec)` named op (codec byte layout is not in the spec; must be inlined or cited as normative). |
| REQ-RD-14 | C | 5 `bit_test`, `in_set`, `map_table` (wind). |
| REQ-RD-15 | C | 5 `map_table`, 6.1 `Table`. |
| REQ-RD-16 | C | 5 `enum_position_percent`; int path via `remap(1..100)`. |
| REQ-RD-17 | P | `enum_filtered_map` + P-02 contradict each other on which map hvac read uses; derived list construction (`hvac_modes` = OFF + non-OFF options, presets = unmapped strings, `switch_only_hvac_mode` append-if-absent when presets exist) has no expression construct (StateExpr has no list ops). |
| REQ-RD-18 | P | `Compose` is named but never defined; swing read (Override chain) is writable, but `options` list building and skip-absent-role write steps are not. |
| REQ-RD-19 | P | `HostEnv`, `temp_convert`, P-04/P-05 exist. The selection algorithm among (temp_current, temp_current_f, temp_set, temp_set_f) by unit alias sets, the `temp_unit_convert` injection into empty units, and the fallback tuple are entirely absent. |
| REQ-RD-20 | P | `bmin/bmax` roles + `companions`; no op/expr for "remap using LIVE values of two other roles, only when both resolved and both non-UNKNOWN, read and write". `remap` endpoints are "spec's scaled [min,max] or explicit" (static). |
| REQ-RD-21 | P | P-08 states the rule; missing: `_white_color_mode` defaults to COLOR_TEMP (WHITE only via the no-color-temp branch), HA `filter_supported_color_modes`/`color_supported` pruning, `_fixed_color_mode` when one mode remains. |
| REQ-RD-22 | C | 7.3 + `state_slot: delta`. (API split with `relevant`, see contradictions.) |
| REQ-RD-23 | P | `units.py` alias table + `native_unit_fallback`/`suggested_unit` fields only. The 6-step reconciliation algorithm (S-UNIT), HA's allowed-units-per-device-class tables, device_class dropping, ENUM promotion, TOTAL_INCREASING promotion are not specified. |
| REQ-RD-24 | C | 8 `event` role `main:(enum|b64str|b64raw)`, `last_event(type,attrs)`; `decode(utf8)`. |
| REQ-WR-01 | C | 6.2 ordered command list, empty = no-op; 7.4. |
| REQ-WR-02 | C | 5 `validate_int` write. |
| REQ-WR-03 | C | R0.4 `WriteRejected`; ops write column. |
| REQ-WR-04 | C | 5 write column per op. |
| REQ-WR-05 | P | `WriteProgram` ordered steps with `when`. But `Cond`/`Expr` (3) have no `Arg(name)`, `ArgPresent`, `Derived(field)`, tuple constructor: light `ATTR_WHITE in kwargs`, fan `percentage is not None` cannot be written. |
| REQ-WR-06 | P | Read-modify-write "value expr reads Resolved(color_data)" but needs `Derived(color_mode)`, `Derived(brightness)`, falsy-fallback (`kwargs.get(B)` falsy -> current, `or 0`, `or (0,0)`) - not in grammar. |
| REQ-WR-07 | C | 6.2 const `ValueExpr`; R0.5. |
| REQ-WR-08 | C | 6.2 `FirstApplicable` (cover open/close, vacuum return). "Applicable" is not defined (roles resolved? guards true?), see behavior check 13. |
| REQ-WR-09 | C | 5 `remap(reverse: Cond)` applied both directions. |
| REQ-WR-10 | U | No construct for "raise `ActionDPCodeNotFound(expected, available)` when a control role is unresolved at action time" vs silent skip (humidifier on/off/set_humidity vs set_mode). |
| REQ-WR-11 | P | vacuum `send_command` mentioned as pass-through (8 note) but `Step.role: RoleName` cannot address a dynamic code from an argument (needs `RawStep(code_expr, value_expr)`, plus the `params` non-empty ValueError). Feeder services are a non-goal (fine). |
| REQ-XDP-01 | C | 5 `remap(reverse: Cond)`, 3 `Status`. |
| REQ-XDP-02 | C* | 6.1 `Override` + `Decode` + `Contains` + `Eq(Status("master_state"),"alarm")`. Unspecified: `Decode(UNKNOWN/None)` and `Contains(UNKNOWN)` must evaluate False (core: `encoded and decoded and "Sensor Low Battery" in decoded` -> TRIGGERED otherwise) - three-valued semantics not defined. |
| REQ-XDP-03 | P | `FirstOf` is "UNKNOWN-skipping" (value fallback). Core needs two different fallbacks: role-PRESENCE (`_current_position = current_position_wrapper or set_position_wrapper`) and precedence on the validated *raw* value BEFORE mapping (vacuum: status present but unmapped -> None, does NOT fall to pause). FirstOf over mapped values gives the wrong answer for the vacuum. |
| REQ-XDP-04 | P | `Override` covers switch False -> OFF and switch-only fallback, but core uses `is False`/`is True` (identity); with P-12 leak (0/1 pass as-is) `Eq(x, False)` mis-evaluates 0. Needs `Is`. |
| REQ-XDP-05 | C | 3 `Expr`/`Cond`, one language for 4 uses. |
| REQ-XDP-06 | C | 5 `map_table`, 6.1 `Table`, unmapped -> UNKNOWN. |
| REQ-ENT-01 | C | 6 `identity{...}` lists all fields. |
| REQ-ENT-02 | O | 10 (translations/icons in adapter), non-goal "host-specific rendering". Justified, but the adapter then needs `strings.json` as an explicit input; say so. |
| REQ-ENT-03 | P | "Also computed: min/max/step" (6.3) but `StateExpr` has no access to `SpecInteger`/`SpecEnum` fields (no `SpecOf(role, field)`), so number/climate limits, humidifier default 0/100 and `round(min)` cannot be expressed. |
| REQ-ENT-04 | P | Same: option lists (select options, fan direction options, preset lists, humidifier modes) need `SpecOf(role,'range')` + list filter ops. |
| REQ-ENT-05 | P | `event_types` feature and P-19 exist; no update-time trigger API (nothing returns "fire event type/attrs"); `relevant()` returns bool only. Also core requires the decoded value to be truthy. |
| REQ-ENT-06 | C | 1.3 `device_info` (verified vs util.py:66-87). |
| REQ-ENT-07 | U | Camera has no platform row in 8, no roles (`motion_switch` function-first, `record_switch` status-first), no derived (`is_recording`/`motion_detection_enabled` = value or False), no actions; entity always exists for sp/dghsxj. 9 camera entities are in the 1205 oracle. |
| REQ-ENT-08 | C | R0.5 literals as op/expr args. |
| REQ-ENT-09 | C | P-09 (oscillation first-found via `DpRef`), P-20, FeatureRule, `identity.entity_category`. Entity-level quirks do not exist in 0.0.29. |
| REQ-LIF-01 | C | 7 `relevant` + classify-time `depends_on`. |
| REQ-LIF-02 | P | `relevant(plan, changed|None)` and, separately, 7.3 `on_update(slot, changed, dp_timestamps, status)`; no single update contract returning {write_state, event}. |
| REQ-LIF-03 | C | 6 `state_slot`, 7.3 `StateSlot`. |
| REQ-LIF-04 | C | 7.3 + Q4. |
| REQ-LIF-05 | P | `reclassify(schema')` only; no Plan diff (added/removed/unchanged unique keys) to drive "drop stale registry entries". |
| REQ-LIF-06 | C | R0.3 pure functions; no optimistic construct exists. (State it explicitly as a non-feature.) |
| REQ-QRK-01 | C | 1.3 `QuirkSet` exact product_id, one per id, ordered, before classify/device_info (registry.py:80 verified). |
| REQ-QRK-02 | C | `DefineDp(type Integer, values, report_type)`. |
| REQ-QRK-03 | C | `DefineDp(Enum)`. |
| REQ-QRK-04 | C | `DefineDp` creates dp + dpmap. |
| REQ-QRK-05 | C | `DefineDp(Bitmap/Boolean)`. |
| REQ-QRK-06 | C | `DefineDp.mode`. |
| REQ-QRK-07 | C | `SetCategory`. |
| REQ-QRK-08 | C | `RemoveDp`. |
| REQ-QRK-09 | P | See RD-06 (no storage slot, naming mismatch). |
| REQ-QRK-10 | C | `MapInitialStatus`, `DpValueMapEnum` (adapter hint). Caveat: see contradiction C6 (purity). |
| REQ-QRK-11 | C | `QuirkOp.when: Expr`; kt_hw50w7qvxluhslkk verified (`isinstance(int) and >= 450`). Core uses `status.get("temp_set")` so a missing key is False (spec `Status` of a missing code must also be False, not an error). |
| REQ-QRK-12 | C | `device_info`. |
| REQ-QRK-13 | O | Non-goal "feeder services". Justified. |
| REQ-QRK-14 | C | 1.3 tdq_xeagimantb7d7apb note. |
| REQ-QRK-15 | O | R0.1 "no code hooks", non-goal "user Python quirks". Justified. |
| REQ-QRK-16 | P | K9 covered; entity-level quirk selection (`*Quirk.definition_fn`) is unused in 0.0.29 and the spec is silent - add a one-line non-goal. |
| REQ-UID-01 | P | Only "keys are core-verbatim, host prefix decides" (Q5 still an open question, not a decision). `tuya.{id}{key}` (no separator) is not stated. |
| REQ-UID-02 | C | `EntityDef.key` verbatim + `expand.key_suffix`. |
| REQ-UID-03 | C | 4 shared-dp entities with distinct keys. |
| REQ-TST-01 | C | 11 T1. |
| REQ-TST-02 | C | 11 T1 "unique_id suffix" (mention the reversed-id harness detail). |
| REQ-TST-03 | C | 11 T5 + 1.1 dict-or-string `values`. (Loader special cases: Json dict re-serialised, "**REDACTED**"->"" - add.) |
| REQ-TST-04 | C | 11 T2. |
| REQ-TST-05 | C | 11 T3/T4. |

Totals: COVERED 74 (INP 9, SEL 9, LKP 2, RD 16, WR 7, XDP 4, ENT 4, LIF 4, QRK 12, UID 2, TST 5); PARTIAL 26; UNCOVERED 2 (WR-10, ENT-07); OUT-OF-SCOPE-BY-DECISION 4 (INP-10, ENT-02, QRK-13, QRK-15).

## 2. Structural gaps S1-S12

| Gap | Verdict | Where / what |
|---|---|---|
| S1 schema/dp addressing | C | 1.1-1.2, 2, R0.2. |
| S2 role = pipeline, composite readers | P | 5/6 pipelines exist; `Compose` undefined; no kind-dispatched alternatives per role (blocking B1). |
| S3 cross-dp refs and conditions | C | 3 (Status non-schema, Cond); vocabulary incomplete (B4). |
| S4 write programs | P | 6.2 exists; `Cond`/`Expr` lack Arg/Derived/Tuple/falsy-fallback/`RawStep`/action-time raise. |
| S5 existence rules as data | C | 4. |
| S6 description-table selection | C | 4 + 9 (generation feasibility concern, B7). |
| S7 classify input incl. status/env | C | 1.2 (missing: HA allowed-unit tables for RD-23). |
| S8 stateful readers + timestamps | C | 7.3. |
| S9 update relevance | C | 7 `relevant` (event/delta gating not in it). |
| S10 quirk model on schema | C | 1.3. |
| S11 identity/key | C | 6 `key`; format left to host (Q5 open). |
| S12 six missing platform shapes | P | 5 of 6 in 8; camera absent. Feature derivation prose only (no `Found(role)`). |

## 3. Design axes A1-A20

| Axis | Answered? | Where / what is left open |
|---|---|---|
| A1 schema input model | Partly open | 1.2 answers shape, dict/str `values`, type normalisation. **Open**: where function/status_range come from for bridge-only devices (10 lists DpMap only; no schema-synthesis duty/decision). |
| A2 code vs id | Answered | R0.2, 1.2, 10. |
| A3 transform representation | Answered | 5 closed named ops with automatic inverse (composition order rules for quirk flag + remap not stated). |
| A4 validation semantics | Answered | R0.4, 5, P-12/P-15; Q3 still open and R0.4 contradicts core raise paths (B8). |
| A5 condition language | Answered | 3 (one language, 4 uses); vocabulary gaps (B4). |
| A6 state derivation | Answered | 6.1 `StateExpr` (hybrid with named ops); insufficient for list-valued fields. |
| A7 write program | Answered | 6.2 (ordered list, atomicity is host's). |
| A8 existence rules | Answered | 4 (degenerate rendering of zero-feature entities not described). |
| A9 tables/alias | Answered | 4, 9 (feasibility problem, B7). |
| A10 quirk patch model | Answered | 1.3; legacy `custom_converters`/`discovery_overrides` deferred to Q1. |
| A11 update relevance | Answered | 7. |
| A12 state slot | Answered | 7.3 + Q4 (timestamps from bridge UNCERTAIN). |
| A13 literals | Answered | R0.5. |
| A14 identity | Partly open | key verbatim; unique_id format and duplicate behavior undecided (Q5). |
| A15 capability derivation | Answered | 6.3 FeatureRule (primitives missing: `Found(role)`, role-scoped range). |
| A16 host env/availability | Answered | 1.2 HostEnv, 7 `available`, P-05. |
| A17 metadata split | Partly open | identity fields IL; translations/icons adapter; fallback English names unresolved. |
| A18 escape hatch/scope | Answered | R0.1, non-goals, D-adapter. |
| A19 testing | Answered | 11. |
| A20 quirks list | Answered | 12 P-01..P-20 covers all 12 A20 items; P-13/P-14 are not tagged reproduce/fix; many more core quirks missing (section 5). |

## 4. Red team

### 4a. Internal contradictions

- C1 R0.2 "IL never sees numeric ids" vs 1.2 `dpmap` + `DefineDp(dpid,...)`. Acknowledged in the text; reword to "runtime read/write never sees ids".
- C2 R0.4 "Reads never raise" vs core paths that raise (unguarded `alarm_msg` decode, `json.loads(dict)` TypeError, ColorDataJsonWrapper `status["h"]` KeyError, event base64/utf-8 decode, malformed `values`). 1.1 says P-13 is a "parity decision" (SchemaError) while 14 Q3 still asks the user; 3 says Decode failure -> UNKNOWN "(parity P-14: see 12)" and 12 says "DECISION NEEDED". 12's title is "Core behaviors reproduced deliberately" but P-13/P-14 are deviations. Must be resolved and every raise path tagged.
- C3 5 `enum_filtered_map`: read column says "filtered map" (ambiguous targets unmapped), P-02 says hvac read uses the UNFILTERED map. The op only fits preset options and the write inverse; hvac read needs plain `map_table`. Also preset read (`raw if raw in options else None`) is a third behavior not in the op.
- C4 6 `update: own_dps` + 7 `relevant` ("any dependency") vs 7.3 `on_update` (delta) vs event: three overlapping entry points with different gating (delta needs timestamps+dedupe; event needs truthy value). `relevant` alone would let delta entities refresh without accumulating.
- C5 4 `Any/All` for binary_sensor implies fall-through, but core is exclusive: `bitmap_key` set -> bitmap only (no fallback), else Boolean-typed wins over `on_value`, else legacy in-set. Which reader/pipeline applies depends on the branch that matched (no way to express, B1).
- C6 1.3 `MapInitialStatus` "rewrites status once at init" vs 0.3 purity + `read(status)` taking a fresh status each call: the rewrite is lost unless the host re-applies it. Specify `apply_status_quirk(status) -> status` or move it to the adapter (core applies it only to the initial cloud status; later updates already arrive converted via the local strategy).
- C7 1.3 `DefineDp` "also creates dpmap[dpid]" unconditionally; core creates `local_strategy[dpid]` only when `support_local`. Acceptable for a local-only IL but unstated.
- C8 9 "Tables are generated from core by a script (AST of TuyaXxxEntityDescription) ... Hand edits forbidden" vs climate/fan/humidifier/vacuum/alarm/light definitions living in handlers `definition/*.py` and wrapper classes (per-description `wrapper_class`, `position_wrapper`, `current_state_wrapper`) which are code, not description literals. Cannot be generated from core's AST alone.
- C9 R0.3 `read(entity,status,...)`/`write(...)` vs 7 `read(EntityPlan,status,env,slot)`, `classify(schema, env)` vs `classify(schema, env, tables, quirks)`; 8 says full field lists live in `il/platforms.py` (nonexistent) and 13 promises worked examples that are not appended.
- C10 1.3 flag name `invert_int` vs 5/P-06 `invert_int_max`.

### 4b. Behaviors checked against catalogs and core source (>= 12)

| # | Behavior | Result |
|---|---|---|
| 1 | Light turn_on (light.py:484-545) | Order/branches match 6.2 in prose, but NOT writable: needs `ArgPresent(WHITE/COLOR_TEMP_KELVIN/HS_COLOR/BRIGHTNESS)`, `Derived(color_mode)==HS`, `Not(ArgPresent(...))`, falsy fallbacks (`kwargs.get(B)` falsy -> `self.brightness or 0`; `hs or (0,0)`), tuple `(h,s,brightness)` value, `elif` exclusivity between colour branch and brightness branch. None exist in 3/6.2. Also `WorkMode.WHITE` written via enum validation (raises if "white" not in range). FAIL as specified. |
| 2 | Climate unit selection (definition/climate.py:58-177) | Algorithm (alias-set classification of 4 wrappers; F branch guard `(cF&sF)|(cF&!sC)|(sF&!cC)`; C branch; fallback tuples; `temp_unit_convert` raw enum injected into EMPTY units before classification; entity unit; `get_temperature_unit` at entity init) is not in the spec at all. FAIL. |
| 3 | Climate filtered map/presets (device_wrapper/climate.py:134-217, climate.py:184-208) | Filter (Counter>1 -> None), options, preset = unmapped, hvac_modes = [OFF]+non-OFF, append `switch_only_hvac_mode` when presets non-empty, unfiltered read. Only the filter op is specified; list construction and append rule are not expressible (no list ops, no `SpecOf`). PARTIAL. |
| 4 | Cover `control_back_mode` inversion | `remap(reverse=Ne(Status("control_back_mode"),"back"))` expresses it, read and write; requires `Ne(None,"back")` True (missing status = inverted). PASS with UNKNOWN-semantics note. |
| 5 | Cover open/close position-only + `_current_position = current or set_position` | Write: `FirstApplicable` OK (define "applicable"). Read fallback is role-PRESENCE, but `FirstOf` is value-skipping: a present-but-UNKNOWN current_position would fall through to set_position in the spec, but core returns None. PARTIAL (B5). |
| 6 | Alarm override (alarm wrapper) | Constructible: `Override(Eq(Status(master_state),"alarm") AND Not(Contains(Decode(Decode(Status(alarm_msg),b64),utf16be),"Sensor Low Battery")), TRIGGERED, Table(master_mode))`; needs Decode/Contains of missing/empty -> False. changed_by via `Override(Ne(master_state,"alarm"),UNKNOWN,Decode(Resolved(alarm_msg),utf16be))`. PASS with caveat. Note state reads `alarm_msg` from raw status of ANY type while changed_by requires a resolved Raw role. |
| 7 | Sensor unit reconciliation (sensor.py:1879-1936) | Six-step algorithm, HA `SENSOR_DEVICE_CLASS_UNITS` membership test, temp_unit_convert branch, alias lookup exact then lower, fallback-to-description-unit, drop device_class + suggested unit, ENUM promotion (device_class None + Enum), TOTAL_INCREASING promotion (Delta), suggested_unit from wrapper (electricity). Spec: one line about `units.py`. FAIL. |
| 8 | Electricity parsed_attr probe (extended.py:149-202) | `ParsedAttr` matches ("no payload yet" = falsy decoded value; unparsable -> not found; attr None -> not found; Json: status None or key in dict). But (a) wrapper_class tuple order Raw then Json = ordered alternatives with different pipelines (B1); (b) wrapper-level `native_unit` (mA/W/V/var/VA, A/kW/kvar/kVA) and `suggested_unit` (A/kW/kvar/kVA) live on the op, not in `identity`, and are not in the op table. PARTIAL. |
| 9 | Delta accumulator (sensor.py wrapper) | 7.3 matches (own code in changed, timestamps not None, current != last, value not None after validation). Selection requires `report_type=="sum"` from status_range to pick Delta vs plain Integer (B1). `None` changed-list bypasses skip_update (no accumulate) - not stated. Uses `relevant` + `on_update` split (C4). PASS/PARTIAL. |
| 10 | Binary bitmap x12 | `BitmapLabel` + `bit_test(label)` bound at classify (label index = first occurrence). Read validates `isinstance(int)` first. 12 literal descriptions or `expand`. Keys inconsistent (`fault_water_full`... vs bare `tankfull`) preserved verbatim. PASS. |
| 11 | Fan two-pass speed + turn_on (definition/fan.py, fan.py) | `multi_pass` gives resolution; pipeline differs by kind (int -> `remap(min..max -> 1..100)`, enum -> `enum_position_percent`), `speed_count` only for enum. Role has one pipeline (B1). turn_on: `[switch True]` + speed if `percentage is not None` and role + preset likewise; no-switch = no-op; `percentage==0` and switch -> `[switch False]` (in set_percentage, not turn_on). Needs Arg vocabulary (B4). PARTIAL. |
| 12 | Vacuum status/pause precedence (vacuum.py:65-76) | Core: validated status not None -> `map.get(status)` (may be None, pause NOT consulted); else pause truthy -> PAUSED. `FirstOf([map_table(status), pause->PAUSED])` (UNKNOWN-skipping) would return PAUSED when status is present but unmapped. Must be `Override(Not(IsNone(Resolved(status_raw))), Table(...), ...)` with the map applied in `derived`, not in the role pipeline. Spec does not say so. PARTIAL. |
| 13 | Humidifier existence (definition/humidifier.py:48-64) | `AnyName([switch, switch_spray, cur_h, target_h])` any type in function/status/status_range; key fixed `switch` vs found code `switch_spray`; mode role status/function precedence per role. PASS (data). Action-time raise absent (WR-10). |
| 14 | Swing composite | Read is an `Override` chain: on_off truthy -> ON; h&v -> BOTH; h; v; else OFF. Write: three steps in order on_off, vertical, horizontal each `arg in {...}`, each skipped if role absent. Options `[OFF, ON?, HORIZONTAL?, VERTICAL?]` (never BOTH). Expressible only with absent-role skip + list constructor (both unspecified). PARTIAL. |
| 15 | Event on-update semantics (event.py:167-185) | P-19 states the rule; no API returns a fire instruction; value must also be truthy (`0`/empty string never fire). PARTIAL. |
| 16 | Quirk `kt_hw50w7qvxluhslkk` | `when: And(IsInt(Status(temp_set)), Ge(Status(temp_set),450))` on both `DefineDp(2)` and `RemoveDp(136)`; matches code (`isinstance(int) and >= 450`; bool would pass `IsInt`). PASS. |
| 17 | Cover tilt (P-20) | `tilt` role with `("angle_horizontal","angle_vertical")` function-first, using the description's position pipeline (incl. dynamic `control_back_mode` inversion for clkg). Expressible per description. PASS. |

### 4c. Factual claims checked against core source (>= 10)

| # | Spec claim | Verdict |
|---|---|---|
| 1 | 1.1 type normalisation: exact names + lowercase aliases, not "Bool"/"Value" | Correct (const.py:33-63). Note fixtures contain `ENUM`/`STRING`/`BOOLEAN`/`raw` upper-case forms; `raw` lower works, upper-case do not, so those DPs are unresolved in core; the oracle must treat them the same. Catalog F sec.2 saying try_parse "tolerates UPPER" is wrong. |
| 2 | 1.3 device_info: manufacturer-only quirk => model/model_id None (P-11) | Correct (util.py:66-87). |
| 3 | 1.3 one quirk per product_id, later replaces earlier | Correct (registry.py:80 dict assignment). |
| 4 | 5 `validate_int`: bool accepted, float rejected, step not enforced, banker's on write | Correct (type_information.py). |
| 5 | 5 `hsv_json` write "`{"h":..,"s":..,"v":..}` (json.dumps separators)" | Ambiguous/misleading: `json.dumps` default separators give `{"h": 10, "s": 202, "v": 1000}` (with spaces). Compact form would break golden command tests. |
| 6 | 5 `invert_int_max`: `scale(max) - v` read and write, ignores min | Correct (type_information_ex.py). Composes AFTER read validation and BEFORE the wrapper's remap; order not stated. |
| 7 | 4 aliases "cz=kg, pc=kg, dghsxj=sp, tdq=tgq" | Overgeneralized: aliases are per platform. Light: cz=kg, pc=kg, dghsxj=sp, tdq=tgq. Switch: CZ=PC, DGHSXJ=SP. Sensor: DGHSXJ=SP, PC=KG. Select: CZ=KG, PC=KG, DGHSXJ=SP. Number: DGHSXJ=SP. Siren: DGHSXJ=SP. |
| 8 | P-18 "binary_sensor legacy existence = name present in `status`" | Imprecise: core checks `function` OR `status` OR `status_range` (definition/binary_sensor.py:56-63). Same as `AnyName(where=any)`; a write-only function DP creates the entity. |
| 9 | 4 `DefaultKinds(dpref)  # sensor/binary default: Integer(+report_type) else Enum` | Sensor only. Binary default is Boolean -> legacy in-set (never Integer/Enum kinds). |
| 10 | P-08 light colour mode | Incomplete: HS only when `_fixed_color_mode` is None AND work_mode role exists AND value != "white"; the "white" mode is COLOR_TEMP by default (`_white_color_mode = COLOR_TEMP`) and WHITE only via the elif branch (no color_temp, HS present, "white" in work_mode range). Fixed mode via HA `filter_supported_color_modes` (external algorithm). |
| 11 | P-17 fan | Correct incl. `percentage==0` handled in `set_percentage` not `turn_on`, and `turn_on` without switch returns immediately. Also: `set_direction` silently ignores unknown HA directions; direction read requires truthy raw. |
| 12 | P-09 swing/oscillate | Correct (SwingModeComposite options never BOTH; oscillate = first Boolean of `switch_horizontal`, `switch_vertical`). |
| 13 | P-16 alarm | Correct (changed_by skips low-battery exclusion; DISARM raises ValueError if "disarmed" not in range). |
| 14 | P-05 temp unit read ONCE | Correct, but the value injected into empty DP units is the raw enum string (e.g. "c"/"f"), not a normalized unit; `get_temperature_unit` at entity init reads `temp_unit_convert` again when the unit is still empty. |
| 15 | 6 `update:` policy list | Correct for valve/siren/binary/sensor/number/select/switch/event (own) and cover/vacuum/alarm/light/climate/fan/humidifier (any); camera, button omitted. |
| 16 | 7.3 delta accumulator conditions | Correct (sensor.py:62-98) incl. `float(scaled)`; also `dp_timestamps is None` -> skip. |
| 17 | 1.3 `DefineDp` "creates dpmap[dpid]" | Core creates `local_strategy[dpid]` only if `support_local`. Acceptable deviation, undeclared. |

### 4d. Important core behaviors omitted entirely by the spec

1. Sensor/number unit reconciliation and HA allowed-unit tables (RD-23), ENUM promotion, TOTAL_INCREASING promotion, wrapper `native_unit`/`suggested_unit` on parsed-attr/json-attr ops, WIND_DIRECTION class dropped by the algorithm (snapshot shows unit None, class None). Number has the same algorithm with `NUMBER_DEVICE_CLASS_UNITS`.
2. Camera entity (always for sp/dghsxj; motion_switch / record_switch).
3. Light: HA `filter_supported_color_modes`/`color_supported`; `_white_color_mode` default; colour DP Json-else-String alternatives; 12-char-only hex read; V1/V2 range triggers; `ColorDataJsonWrapper` raises KeyError on missing h/s/v; dynamic brightness limits require both live values; kwargs falsy semantics.
4. Climate: unit selection/injection; list construction of hvac_modes/presets; `HVACMode.OFF` first ordering; humidity min/max rounding; default step 1.0; swing options filtered through HA mapping; `async_set_temperature` uses only `ATTR_TEMPERATURE`; `Is False` identity in hvac_mode.
5. Humidifier action-time `ActionDPCodeNotFoundError(expected, available)`; humidifier min/max default 0/100.
6. Vacuum `send_command` validation (`params` non-empty, only `params[0]` used), fan speed list from raw range, `pause` write only True.
7. Fan direction map + silent ignore, speed range starts at 1.
8. Event fire semantics (truthy value, `updated_status_properties None` never fires).
9. Delta: `updated_status_properties None` bypasses skip_update (no accumulation); accumulator not persisted, starts at 0 even when status None.
10. Stale registry cleanup on add/remove (LIF-05 diff).
11. Fixture-loader normalisations (Json dict re-serialised, "**REDACTED**" -> "") needed for T1 parity.
12. `Enum`/`Integer` `values` parse details: `int()` truncation, whole-device failure on malformed schema (only P-13 mentions), `values` falsy -> not found.

## 5. Recommended amendments (prioritized)

### BLOCKING

B1 (2, 6, 4; REQ LKP-01/05, RD-*, S2, C5). Add typed alternatives to `Role`.
Replace `Role{dpref, read, write}` by
`Role{alts: [Alt{dpref: DpRef, exists: Existence|None, read: Pipeline, write: Pipeline|None, meta: {native_unit, suggested_unit, options_from...}}], required}`; resolution = first alt whose `dpref`+`exists` matches, in list order; `ResolvedRole{alt_index, kind, spec}`; add Cond `Kind(role, DpType)` and `AltIs(role, i)`. Delete `DpRef.multi_pass` (two-pass = two alts). This expresses fan speed (Integer then Enum), cover instruction (enum map | special enum map | boolean), sensor (Integer+sum -> delta | Integer | Enum), electricity (raw | json), colour (Json | String), binary (bitmap | Boolean | legacy in-set).

B2 (new 6.4 "Climate temperature selection"; RD-19). Add a classify-time named op `select_temp_roles(candidates, alias_sets, host_unit)` with the algorithm from catalog C 2.3 verbatim (C/F classification by alias sets on resolved units; F guard; C guard; fallback tuples), preceded by rule "`temp_unit_convert` raw status assigned to any resolved temp role whose unit is empty", output = chosen (current, set, entity_unit) + per-role native unit for `temp_convert`. State that limits stay native (P-04).

B3 (new 5.x "UnitPolicy"; RD-23). Specify the 6-step reconciliation as a named classify-time op with inputs {dp_unit, description.device_class, description.native_unit_fallback, suggested_unit, status[temp_unit_convert]} and external table `HostEnv.allowed_units[platform][device_class]` (HA `SENSOR_DEVICE_CLASS_UNITS`/`NUMBER_DEVICE_CLASS_UNITS`), plus promotions: `device_class None + Enum alt -> ENUM (options = range)`, `Delta alt -> state_class TOTAL_INCREASING if unset`, wrapper suggested_unit fallback. Outputs `{native_unit, device_class|None, suggested_unit|None}`.

B4 (3). Complete the Expr/Cond vocabulary and define semantics.
Add Expr: `Arg(name)`, `Derived(field)`, `SpecOf(role, min|max|step|scale|unit|range|labels)`, `Tuple(...)`, `OrElse(a, b)` (falsy fallback), `Round(expr)`, `ListOf/Filter/Append` (or named ops `hvac_modes_of(...)`, `preset_options_of(...)`). Add Cond: `ArgPresent(name)`, `Found(role)`, `Kind(role, T)`, `Is(expr, lit)` (identity; needed for `switch is False`), `Truthy(expr)`, `InRange(role, lit)`. Add a normative paragraph: comparisons follow Python semantics on `None` (`Ne(None,"back")` True; `Contains(None,x)` False; `Decode(None)` -> None); `UNKNOWN` == `None`.

B5 (6.1). Split fallback constructs: `FirstOf` = value fallback (skip UNKNOWN); add `PreferRole(a, b)` = resolution-time presence fallback (cover `current_position or set_position`). Rewrite the vacuum example as an `Override` on the validated RAW status role, map applied in `derived`. Add a test per construct.

B6 (8, 4, 12; SEL-09, ENT-07, S12). Add `camera` row: roles `motion_switch` (bool, function-first, rw), `record_switch` (bool, status-first, r); `existence: Always` for sp/dghsxj; derived `is_recording`, `motion_detection_enabled` = value `or False`; actions `enable/disable_motion_detection`; stream via adapter. Update non-goals wording ("camera stream/image only").

B7 (10 hsv/brightness; RD-10, RD-20, RD-21). Light: (a) add `SpecJson{h:{min,max},s:{..},v:{..}}` accessor and `hsv_json(ranges = FromSpec | V1 | V2, v2_when = Or(desc_flag, DpcodeIs("colour_data_v2"), Gt(SpecOf(brightness,max),255)))`; (b) `remap` endpoints may be `Expr` so brightness limits = "if Found(bmin)&Found(bmax) and both values non-UNKNOWN then remap over [remap255(bmin), remap255(bmax)] else plain"; (c) add named op `filter_color_modes(set)` (HA ONOFF/BRIGHTNESS pruning) + default `white_mode = COLOR_TEMP` rule + fixed-mode rule; (d) fix `hsv_json` write example to default `json.dumps` separators.

B8 (12, R0.4, 1.1, 14 Q3; C2). Decide and tag every raise path. Proposed: R0.4 reworded "Reads never raise EXCEPT tagged parity raises" and a table `P-xx | core behavior | IL behavior | tag reproduce|deviate`. Tag deviations for: malformed `values` (P-13 SchemaError), alarm decode, `json.loads(dict)`, ColorData KeyError, event decode. Remove P-13/P-14 from the "reproduced" list or relabel as DEVIATIONS.

### IMPORTANT

I1 (1.2/1.3; RD-06, QRK-09). Add `DeviceSchema.type_overrides: dict[code, "invert_int_max"]`; `apply_quirk` sets it; `resolve()` consults it by dpcode only (core ignores dpid) and inserts `invert_int_max` between `validate_int` and any remap for EVERY role reading that code. Use one name (`invert_int_max`) everywhere.

I2 (7, 7.3; LIF-02, ENT-05, C4). Replace `relevant()` for stateful/event entities with one `on_update(plan, slot, changed|None, dp_timestamps|None, status) -> UpdateResult{write_state: bool, fire: {type, attrs}|None}` (keep `relevant` as its pure part for others). Document: `changed is None` always writes and never accumulates/fires; event needs own code in changed AND truthy value; delta as 7.3.

I3 (6.2; WR-10, WR-11, LKP-03). Add `Step.if_missing: "skip" | Raise(ActionDPCodeNotFound, expected=[...])` (default skip); add `RawStep(code: Expr, value: Expr)` for vacuum `send_command` (+ `params` non-empty precondition); define `FirstApplicable` applicability = all required step roles resolved and all guards true.

I4 (5 ops; RD-17, RD-18, C3). Replace `enum_filtered_map` by three ops: `enum_map_filtered` (write inverse: first raw whose filtered target == x), `enum_map_unfiltered` (hvac read, P-02), `enum_unmapped_options` (presets; read = raw if in options). Add list-building rule for `hvac_modes`, `preset_modes`, `switch_only_hvac_mode` append, swing options; define `Compose` or delete it.

I5 (9; C8). Split data layers: (1) generated description tables from core (`TuyaXxxEntityDescription` literals); (2) hand-authored, reviewed "platform definitions" mirroring handlers `definition/*.py` (roles, dpcode tuples, wrapper-class-to-pipeline mapping such as `CoverClosedEnumWrapper` -> `map_table`). Limit "Hand edits forbidden" to layer 1; add a wrapper-class -> pipeline table the generator must resolve; else generation cannot be done.

I6 (1.3; C6). `MapInitialStatus`: define as `apply_status_quirk(quirk, status) -> status` to be called by the host on the initial status (or move to adapter); do not imply persistence.

I7 (12). Add missing parity items: hvac `is False`/`is True` identity; vacuum status-present-unmapped no pause fallback; cover position fallback by presence; light turn_on falsy fallbacks; ColorData KeyError; hex read requires len 12; fan direction unknown write ignored; humidifier action-time raise; delta `None`-changed bypass; binary_sensor legacy existence is function|status|status_range (fix P-18); uppercase type strings unparsed (fixtures `ENUM`, `STRING`, `BOOLEAN`).

I8 (4, 9; SEL-01). Make aliases per-platform explicit and list them (light, switch, sensor, select, number, siren, camera, etc.); note shared tuples (`BATTERY_SENSORS` x35, `TAMPER` x17) are generator expansions.

I9 (1.1, 1.2; A1). Add a decision paragraph on schema synthesis for bridge-only devices (source of function/status_range: local_strategy, static per-product tables, or category defaults) and who owns it; otherwise classify cannot run.

I10 (11; TST-03). Add fixture loader rules (Json dict re-serialised, "**REDACTED**" -> "", device id = reversed `{category}{product_id}` stripped of `_`), and a test asserting uppercase-type DPs resolve to nothing.

### MINOR

M1 (5) `hsv_json` example text; M2 (4) `DefaultKinds` comment (sensor only) and P-18 wording; M3 (R0.2/R0.3/7/8/13) reconcile signatures, remove "il/platforms.py" reference or create it, append the worked examples 13 promised; M4 (1.3) note `DefineDp` dpmap creation is unconditional (core: only with support_local); M5 (6) mention camera/button in update-policy list; M6 (1.1) note `int()` truncation on Integer `values`; M7 (3) quirk `when` `IsInt` semantic (bool passes) and missing-key behaviour; M8 (UID-01) state `tuya.{device_id}{key}` no separator as the reference format, keep prefix host-side; M9 (P list) add explicit non-goal "entity-level `*Quirk.definition_fn` (unused in 0.0.29)"; M10 (ENT-02) declare `strings.json` as adapter input for names and state labels.

## 6. Blocking issue count

8 blocking (B1-B8), 10 important (I1-I10), 10 minor (M1-M10).

Top 3: (1) Role has a single dpref/pipeline, so type-dispatched alternatives (fan speed, cover instruction, sensor kinds/delta, electricity, colour, binary) are inexpressible; (2) Expr/Cond and StateExpr vocabulary is too thin (no args, derived fields, spec metadata, identity, list ops, UNKNOWN semantics), so light/fan/climate write recipes and option/limit lists cannot be written; (3) climate unit selection and sensor/number unit reconciliation (with HA allowed-unit tables) are entirely unspecified.
