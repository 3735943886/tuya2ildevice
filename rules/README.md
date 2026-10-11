# Rules

`00-default.json` ships with the package and wheel. It contains the base
conversion rules and the `overrides` section for product/device-specific fixes.
`schema.json` defines the complete format; partial user files are validated after
merging with the defaults.

Put user files in a separate directory and set `TUYA_ENGINE_RULES` to that directory,
or use `load_rules([directory])` and pass the result as `TuyaDriver(..., rules=...)`.
The native loader collects visible `*.json` files from all supplied locations and applies them in
ascending filename byte order (ASCII ascending for ASCII names). Equal filenames
preserve the supplied location order. `00-default.json` has no special handling. Loading is non-recursive. Hidden files,
`schema.json` and the obsolete `manifest.json` are ignored. Saved settings in
`zz_settings.json` follow the same filename order. Later filenames can override them.

Objects merge recursively. Arrays and scalar values replace earlier values.
`{"$delete": true}` deletes an object member; `null` is a literal value.

For example, `10_curtain.json` can contain:

```json
{
  "overrides": {
    "my_product_id": {
      "cover": {"invert_position": true}
    }
  }
}
```

The host's `load_overrides` and `OverrideWatcher` also read this format. They
accept old unwrapped product/device mappings for compatibility and load optional
Python plugins separately. No remote pack download or manifest is needed.

See [the engine documentation](../docs/rust-engine.md) for declarative JSON
converters, native loading operations and the C ABI.
