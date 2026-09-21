"""The Tuya DP engine: data + closed algorithms that reproduce Home Assistant core's `tuya` integration.

Sans-IO and dependency-free. `classify(schema)` turns a device's function / status_range / status into entity plans
(read, write, identity); `adapter.Adapter` converts raw LAN dps to cloud-style values and back; `quirks` patches
schemas; `tables/` and `quirks/` are generated from HA core and tuya-device-handlers (see scripts/). Behaviour is
specified in docs/engine-spec.md and pinned by the golden tests. `tuya2ildevice.assemble` turns plans into ildevice.
"""
ENGINE_VERSION = 2
