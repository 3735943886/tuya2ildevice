# Phase 4 — golden test foundation (status)

Tooling: core-analysis/golden/  (ambr.py, build_golden.py, fixtures.py, baseline_v1.py, quirk_oracle.py; oracle-venv/ = handlers 0.0.29 + sdk 0.2.15)

1. golden.json — 285 fixtures / 1205 entities (17 platforms) parsed from core's syrupy snapshots
   (registry entry + state: key, translation_key, device_class, category, unit, features, capabilities, state, attributes).
   IMPORTANT: core snapshot tests run with `no_quirk` -> golden baseline = quirk OFF.
   Not included: 22 `test_us_customary_system` variants (orphans, add as a units-policy suite), init/config_flow/diagnostics/services.
   39 fixtures produce no entity on any platform (expected-empty is part of the golden).
2. fixtures.py — replicates tests/components/tuya/__init__.py::create_device (compact json values, Json status re-stringified, REDACTED -> "").
3. baseline_v1.py — current tuya2ha v1 vs golden by per-platform COUNT only: 548/1205; 2 fixtures raise. (Floor, not a real parity number.)
4. quirk_oracle.py + quirk_golden.json — handlers' post-quirk schema for fixtures with a quirk: only 7 of 32 quirks have a real fixture
   -> the other 25 need synthetic devices (built from each quirk's declared dps) in Phase 4b.
Open in 4b: golden comparison contract per IL (which fields are asserted per platform), dispatch-behaviour goldens (service calls / state updates)
are NOT in snapshots (they live in core's test_*.py parametrized tests) -> extract those separately.
Location decision pending: eventual home rustuya-homeassistant/tests/golden/.

## Phase 4b additions (2026-09-20)
- extract_actions.py -> actions_golden.json: 61 write-side cases (service call -> commands) via AST from core's parametrized tests
  (cover 9, fan 9, alarm 8, climate 7, light 7, vacuum 7, humidifier 3, camera/siren/switch/valve 2 each, button/select/number 1 each).
  HA constants stay symbolic ("$SERVICE_TURN_ON"); the replay harness must map them.
- quirk_oracle_synthetic.py -> quirk_synthetic_golden.json: all 32 quirks applied to an EMPTY schema (patch-only oracle).
  8 quirks (68nvbio9, b9oa3zocv4qq47iy, cf1sl3tj, csgb8eqhczvjaetl, dune79w7bsu6dg3e, hw50w7qvxluhslkk, nfq1essvr99qsvvd,
  xyakonle1azq2xgn) yield an empty patch there (they depend on existing dps / apply_when / type overrides / category) ->
  they still need base schemas (no real fixture) — known coverage gap.
- Not yet extracted: error-path goldens (e.g. humidifier action_dpcode_not_found, select not_valid_option -> P-13 family),
  state-after-update cases (test_percent_state_on_cover style), unit-conversion cases (number/sensor unit tests, us_customary).
- Run oracle scripts with the venv that has tuya-device-handlers==0.0.29 + tuya-device-sharing-sdk==0.2.15 (+ cryptography, paho-mqtt, requests).

## Phase 5 progress (IL v2 in src/rustuya_ha/tuya2ha/v2/)
- `python tests/golden/test_golden_v2.py [platform...]` compares classify() with golden.json (+ write replay for implemented platforms).
  It asserts 324 fixtures are present (a vacuous-pass bug was caught this way: a broken fixture path had made the first run compare nothing).
- core_fixtures/ = copy of HA core tests/components/tuya/fixtures (Apache-2.0).
- Golden limitation: button/binary_sensor/sensor snapshots were taken with entity_registry_enabled_by_default, so
  enabled_default is not golden-checked for them (value comes straight from the generated table).
- scripts/gen_core_tables.py regenerates v2/tables/*.json (L1) from a core checkout.
