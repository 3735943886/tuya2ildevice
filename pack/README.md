# The override pack

Fixes for non-standard devices that reach users without a release: hosts running `tuya2ildevice.host.pack.sync`
(rustuya-local, by default) copy the files listed in `manifest.json` into their `custom_converters/` directory.

- Files are override mappings (`*.json`) or code converters (`*.py`), in the format of `custom_converters/`. Name them
  `00_pack_<what>.<ext>` so a user's own files (loaded later, by name) refine them.
- After adding, changing or removing a file, run `python scripts/build_pack.py`; the tests check the manifest.
- A file that needs something a tuya2ildevice release added gets `"requires": <LEVEL of that release>` in its manifest
  entry. When a release takes a fix into `overrides.json`, bump `LEVEL` (`host/pack.py`) in it and give the file
  `"until": <that LEVEL>`; older installations keep it, newer ones remove it. Drop the file once nobody needs it.
- Keep it small: a fix belongs in `src/tuya2ildevice/overrides.json` with the next release; the pack only bridges the
  time until then.
