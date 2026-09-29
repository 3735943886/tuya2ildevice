# The override pack

Fixes for non-standard devices. The package itself never carries a block for a product or device; this is where
those fixes live, and they reach users without a release: hosts running `tuya2ildevice.host.pack.sync`
(rustuya-local, by default) copy the files listed in `manifest.json` into their `custom_converters/` directory.

- A cover that runs the other way is set by a product block's `cover` settings (`invert_position`,
  `invert_set_position`, `invert_control`, `infer_motion`, `settle`, ...), not by `remap.invert` or
  `converters.cover_motion`: it is the starting point, and the device's own switches (saved in the host's
  `zz_settings.json`, which the pack never writes) win. Keep `remap` for other words and `dp` / `props` / code converters
  for the rest.
- Files are override mappings (`*.json`) or code converters (`*.py`), in the format of `custom_converters/`. Name them
  `00_pack_<what>.<ext>` so a user's own files (loaded later, by name) refine them.
- After adding, changing or removing a file, run `python scripts/build_pack.py`; the tests check the manifest.
- A file that needs something a tuya2ildevice release added gets `"requires": <LEVEL of that release>` in its manifest
  entry: a file with a `cover` block needs `"requires": 2` (0.3.15). When a release makes a file unnecessary (the engine now handles such devices generically), bump `LEVEL`
  (`host/pack.py`) in it and give the file `"until": <that LEVEL>`; older installations keep it, newer ones remove it.
