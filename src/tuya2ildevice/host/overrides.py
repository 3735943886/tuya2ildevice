"""User overrides as files: a `custom_converters/` directory (or one `.json` file), loaded and followed as it changes.

- `*.json`: override mappings (see `tuya2ildevice.overrides`), deep-merged in filename order, so a later file (say
  `99_local.json`) refines an earlier one. A file in rustuya-homeassistant v1's format (`dp_meta`,
  `discovery_overrides`, `model`) is converted with `from_v1`; what it cannot carry is reported.
- `*.py`: code converters. A file defines ``CONVERTERS = {"name": factory}`` (`factory(config) -> Converter`, a
  `Converter` subclass taking its config works) and an override block turns one on for a product or a device with
  ``{"converters": {"name": {...config...}}}``. The code runs in-process: trust it like any plugin. A v1 file (it
  defines `setup(api)` for rustuya-manager's plugin runtime) is not loaded; it is reported instead.

`manifest.json` and files starting with `.` or `_` are skipped. Nothing here raises for a bad file: a file that cannot
be read, parsed or imported is left out and reported in `OverrideSet.warnings`, and the rest still loads.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..overrides import OverrideError, from_v1, is_v1, merge_all
from .runner import Runner

_LOGGER = logging.getLogger(__name__)
_SKIP = {"manifest.json"}


@dataclass
class OverrideSet:
    overrides: dict = field(default_factory=dict)
    converter_types: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    return sorted(p for p in path.iterdir()
                  if p.is_file() and p.suffix in (".json", ".py") and p.name not in _SKIP and p.name[0] not in "._")


def _load_py(p: Path) -> tuple[dict, str | None]:
    """({name: factory}, warning). Each load imports the file afresh under its own module name, so an edited file
    takes effect without a restart."""
    raw = p.read_bytes()
    name = f"tuya2ildevice_user_{p.stem}_{hashlib.sha1(raw).hexdigest()[:10]}"
    spec = importlib.util.spec_from_file_location(name, p)
    if spec is None or spec.loader is None:
        return {}, f"{p.name}: cannot be imported"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module                   # dataclasses and pickling look the module up by name
    try:
        spec.loader.exec_module(module)
    except Exception as e:                       # a user's file: anything can go wrong in it
        sys.modules.pop(name, None)
        return {}, f"{p.name}: {type(e).__name__}: {e}"
    types = getattr(module, "CONVERTERS", None)
    if types is None:
        if callable(getattr(module, "setup", None)):
            return {}, (f"{p.name}: a rustuya-homeassistant v1 plugin (setup(api)); not loaded. Port it to a "
                        "tuya2ildevice Converter and list it in CONVERTERS")
        return {}, f"{p.name}: defines no CONVERTERS"
    if not isinstance(types, dict) or not all(isinstance(k, str) and callable(v) for k, v in types.items()):
        return {}, f"{p.name}: CONVERTERS must map names to factories"
    return dict(types), None


def load_overrides(path: str | Path) -> OverrideSet:
    """Read a directory (or one `.json` file) of overrides and converters. Never raises for a bad file."""
    out = OverrideSet()
    mappings: list[dict] = []
    for p in _files(Path(path)):
        try:
            if p.suffix == ".json":
                data = json.loads(p.read_text("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("expected an object keyed by product or device id")
                if is_v1(data):
                    data, warn = from_v1(data)
                    out.warnings += [f"{p.name}: {w}" for w in warn]
                mappings.append(data)
            else:
                types, warn = _load_py(p)
                if warn:
                    out.warnings.append(warn)
                for name in types.keys() & out.converter_types.keys():
                    out.warnings.append(f"{p.name}: converter {name!r} replaces one from an earlier file")
                out.converter_types.update(types)
        except (OSError, ValueError) as e:
            out.warnings.append(f"{p.name}: {e}")
    out.overrides = merge_all(mappings)
    return out


class OverrideWatcher:
    """Follow an overrides directory: when a file is added, changed or removed, load it all again and reload the
    runner's Hub with it (`base`, e.g. overrides from the host's own configuration, is merged on top). Overrides the
    Hub refuses (`OverrideError`) are reported and the ones in effect stay."""

    def __init__(self, path: str | Path, runner: Runner, interval: float = 5.0, base: dict | None = None) -> None:
        self.path = Path(path)
        self.runner = runner
        self.interval = interval
        self.base = base or {}
        self._stamp: Any = None
        self._task: asyncio.Task | None = None

    def load(self) -> OverrideSet:
        loaded = load_overrides(self.path)
        for w in loaded.warnings:
            _LOGGER.warning("overrides: %s", w)
        loaded.overrides = merge_all([loaded.overrides, self.base])
        return loaded

    def start(self) -> None:
        """Watch from now on (the state at this point counts as already applied)."""
        self._stamp = self._stat()
        self.watch()

    async def load_initial(self) -> OverrideSet:
        """Load what is there now, off the event loop, and remember it as applied: pass the result to the Hub you
        create, then `watch()`."""
        def work() -> OverrideSet:
            self._stamp = self._stat()
            return self.load()
        return await asyncio.to_thread(work)

    def watch(self) -> None:
        """Start following the directory from the state last loaded."""
        if self._task is None:
            self._task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    def _stat(self) -> Any:
        stamp = []
        for p in _files(self.path):
            try:
                st = p.stat()
            except OSError:
                continue
            stamp.append((p.name, st.st_mtime_ns, st.st_size))
        return tuple(stamp)

    def check(self) -> bool:
        """Reload once if anything changed since the last look; True if it did."""
        stamp = self._stat()
        if stamp == self._stamp:
            return False
        self._stamp = stamp
        self._apply(self.load())
        return True

    def _apply(self, loaded: OverrideSet) -> None:
        try:
            self.runner.reload(loaded.overrides, None, loaded.converter_types)
        except OverrideError as e:
            _LOGGER.warning("overrides in %s not applied: %s", self.path, e)

    async def _loop(self) -> None:
        # the file work (stat, read, importing a user's .py) runs in a worker thread: a host such as Home Assistant
        # must not block its event loop on it; only the reload itself runs on the loop
        while True:
            await asyncio.sleep(self.interval)
            stamp = await asyncio.to_thread(self._stat)
            if stamp == self._stamp:
                continue
            self._stamp = stamp
            self._apply(await asyncio.to_thread(self.load))
