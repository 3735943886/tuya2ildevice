"""Dump HA's per-device-class allowed units -> src/tuya2ildevice/tuya/data/host_units.json (HostEnv.allowed_units, spec 5.3). Needs `homeassistant`.

Usage: python gen_host_units.py <out.json>"""
import json, sys, enum
from homeassistant.components.sensor.const import DEVICE_CLASS_UNITS as S
from homeassistant.components.number.const import DEVICE_CLASS_UNITS as N
def conv(d):
    out = {}
    for dc, units in d.items():
        vals = []
        for u in units:
            if isinstance(u, type) and issubclass(u, enum.Enum): vals += [m.value for m in u]
            else: vals.append(u)
        out[dc.value] = sorted(vals, key=lambda x: (x is None, str(x)))
    return out
json.dump({"sensor": conv(S), "number": conv(N)}, open(sys.argv[1], "w"), indent=1, ensure_ascii=False)
