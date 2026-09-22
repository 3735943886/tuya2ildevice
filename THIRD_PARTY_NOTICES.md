# Third-party notices

## home-assistant/core (`tuya/`) and tuya-device-handlers

`src/tuya2ildevice/tuya/` is a sans-I/O, data-driven reproduction of
[home-assistant/core](https://github.com/home-assistant/core)'s official `tuya` integration (Apache-2.0) and of
[tuya-device-handlers](https://github.com/make-all/tuya-local) (Apache-2.0). Its category/platform tables
(`tuya/tables/*.json`) and quirk registry (`tuya/quirks/quirks.json`) are *generated* from those sources by
`scripts/gen_core_tables.py` and `scripts/gen_quirks.py` against a pinned checkout of each, not hand-copied;
`tuya/platforms.py` is hand-authored but reviewed against tuya-device-handlers' `definition/*.py`. Behaviour is
pinned by the golden tests in `scripts/golden/` (built from core's own test fixtures and snapshots).

Same author as this project (MIT); the generated data derived from Apache-2.0 sources keeps that notice.

## tuya-device-sharing-sdk

`tuya/adapter.py` reproduces (does not vendor) the `default`, `enum` and `dj_v2_{color,contr,music,scene}_alg`
`value_convert` strategies of [tuya-device-sharing-sdk](https://github.com/tuya/tuya-device-sharing-sdk)
(`strategy_repo/`, Apache-2.0) — specifically `Manager._on_device_report`'s read direction — plus the inverse write
direction the SDK does not have. It is tested against the SDK's own strategies.

## History

This package is the successor of `rustuya-homeassistant`'s `tuya2ha.v2` (later `rustuya_ha.tuya2ha.v2`), split out
as its own sans-I/O engine so it could target ildevice's IL instead of Home Assistant entities directly; the
generation/attribution setup above carries over unchanged from that project. See that repository's history for the
earlier, HA-entity-shaped designs (a vendored `vendor/ha_core/` copy, then a heuristic `tuya2ha` v1 classifier) that
`tuya2ha.v2` itself replaced.
