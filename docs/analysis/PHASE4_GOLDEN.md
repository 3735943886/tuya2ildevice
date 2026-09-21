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
