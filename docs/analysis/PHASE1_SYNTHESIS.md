# Phase 1 synthesis: what HA core's tuya integration actually does

Sources: HA core dev @ 949a484 (2026-09-19), tuya-device-handlers 0.0.29,
tuya-device-sharing-sdk 0.2.15. Per-area catalogs (with file:line evidence) are
catalog/A..F. Headline claims were spot-checked against source by the
coordinator (PLATFORMS list, siren features, local_strategy usage, quirk count).

## 1. Shape of the system

    CustomerDevice (cloud schema: function / status_range / status)
      -> quirk stage (by product_id; mutates schema/status/category)     [handlers]
      -> category -> per-platform description tables                    [core]
      -> definition (resolves dp codes + types into wrappers)           [handlers]
      -> wrapper: read(status)->value ; write(value)->[{code,value}]    [handlers]
      -> HA entity (thin)                                               [core]

Handlers (7.1k lines) has NO homeassistant imports. Core (9.7k lines) holds the
tables + thin entities. 18 platforms; lock / media_player / water_heater are NOT
among them (lock-category devices register but create zero entities).

## 2. Numbers

- DeviceCategory 139 members (98 referenced; categories are open strings in fixtures)
- DPCode 415 members
- 617 literal entity descriptions (834 expanded); sensor alone 250/450
- 32 quirk files (14 override kinds; 30 pure data, 3 code hooks)
- 324 test fixtures, 285 produce entities (1205 total, quirks disabled), 39 produce none
- only 16/324 fixtures carry local_strategy (numeric dp ids)

## 3. Facts that constrain the IL

1. Everything is keyed by dp CODE, never dp id. Schema = function + status_range
   (type, range, scale, step, enum values). local_strategy is touched only by quirks.
2. Entity existence = category table AND required dp codes present with required
   type (tuple-of-candidates fallback, prefer_function toggle). No product_id
   selection outside quirks. Some platforms have degenerate rules (climate: every
   dbl/kt/qn/rs/wk/wkf device gets one even with no dps; vacuum: always for `sd`).
3. Reads are pure; invalid raw values become None (unknown), not errors. One
   stateful reader (delta accumulator, needs dp_timestamps).
4. Writes are pure: value -> list of {code,value}; validated against schema.
   Multi-dp writes exist only in light (ordered color+mode) and a few climate paths.
5. Cross-dp rules exist: cover inversion depends on control_back_mode; alarm
   TRIGGERED depends on master_state + decoded alarm_msg; light color mode depends
   on work_mode; climate hvac_mode from switch+mode. Some read dps NOT in schema.
6. Named fixed transforms needed: linear remap (with inversion), mired/Kelvin,
   HSV json/hex codecs, unit conversion, electricity raw/json parsers, wind lookup,
   bitmap bit/in-set, base64 utf8/raw, enum<->HA maps, many-to-one status maps.
7. No optimistic state, no debounce; dp_timestamps only for the delta accumulator.
   Availability = device online flag only.
8. Quirks: keyed product_id; run before classification; add/replace/remove dp,
   retype/rescale, category override, initial status map, TypeInformation class swap
   per dpcode, apply_when predicate (code), feeder wrapper (code). Custom user
   quirks are arbitrary Python files (out of scope for a data IL).
9. Hard-coded literals live in code ("Sensor Low Battery", FZ/ZZ/STOP, chargego,
   back) -- IL must carry them as data.
10. Documented quirks of core itself: climate hvac_mode reads an unfiltered map (can
    report a mode not in hvac_modes); cover open/close with a position dp sends only
    position; unverified paths: cover tilt, mach_operate (no fixtures).

## 4. Fit with our situation

- Our registration cache already stores local_strategy + function + status_range
  (const.py CONF_LOCAL_STRATEGY/FUNCTION/STATUS_RANGE), so core's actual required
  input (function/status_range) IS available to us; tuya2ha today classifies from
  local_strategy alone and ignores function/status_range types.
- We need dp code <-> dp id translation on top (bridge speaks numeric dp ids);
  the SDK also has 24 value-convert strategies (local raw -> cloud value) that
  core assumes already applied. Whether the bridge's values need those is UNKNOWN
  (SDK strategy implementations were not read).
- Lock is outside "reuse of core knowledge": core has none. Needs another source.
- Removable from IL scope: cloud auth/MQ, scenes, camera streaming, feeder
  services, user-quirk plugins, optimistic state, debounce.

## 5. Decisions the IL design needs from the user

D1. Fidelity target: reproduce core exactly (including its quirks like the climate
    unfiltered map) vs. reproduce intended behavior.
D2. Schema source contract: require function/status_range (like core) with
    local_strategy for dp-id mapping, vs. keep tuya2ha's local_strategy-only mode too.
D3. Do SDK value-convert strategies need porting (blocked on live verification).
D4. Quirk scope: data-only quirks in IL (30/32) + small declarative predicate
    language, or also code hooks.
D5. Lock/media_player/etc: out of scope, or source knowledge elsewhere (tuya-local
    yaml corpus / own design).
D6. Golden tests: use core's snapshots as-is (quirks off) and add quirk-on cases.
