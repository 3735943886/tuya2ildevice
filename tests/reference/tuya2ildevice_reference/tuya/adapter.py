"""Bridge value adapter (spec section 10): raw LAN dps <-> core-style cloud values, driven by `local_strategy`.

The engine assumes core-style, already-converted values. rustuya-bridge emits RAW LAN dps keyed by numeric dp id, so this
adapter reproduces tuya_sharing's `Manager._on_device_report` (READ) and adds the inverse (WRITE) the SDK never had.
Sans-I/O; `read`/`write` are pure. Ported: default, enum, dj_v2_{color,contr,music,scene}_alg, dj_v1_{hsv,scene}_alg,
voice_atm_color, cz_timer{1,2}_alg, hb_{djv1_color,jsq_lightv1,range_v1,range_v2}, ms_dp_syn_alg, sd_clean_record,
db_v1_{params,daily,month,frozen,alarm}. Where the SDK has no inverse and none is exact (meter data, lossy ranges, scenes,
lock bits) the write side raises `NoWritePath`. Not ported: db_v1_data and db_v1_tariff (the SDK's own parsers are wrong
on real payloads, see docs/analysis/SDK_CONVERT_INVESTIGATION.md); they, and any unknown strategy, are passed through
unchanged and listed in `Adapter.unsupported`.
"""
from __future__ import annotations

import base64
import colorsys
import json
import math
from dataclasses import dataclass, field
from typing import Any


class NoWritePath(Exception):
    """The dp's strategy has no inverse: its property cannot be written."""


def strategy_code(meta: Any) -> str | None:
    """The DPCode a strategy entry's value is stored under: the LAST key of `config_item.statusFormat`
    (SDK: json.loads(...).popitem()), NOT `status_code`. statusFormat is a dict (cloud cache) or a JSON string."""
    if not isinstance(meta, dict):
        return None
    fmt = (meta.get("config_item") or {}).get("statusFormat")
    if isinstance(fmt, str):
        try:
            fmt = json.loads(fmt)
        except ValueError:
            fmt = None
    if isinstance(fmt, dict) and fmt:
        return str(next(reversed(fmt)))
    return meta.get("status_code") or None


def _hex(s: str) -> int:
    s = (s or "").lstrip("0")
    return int(s, 16) if s else 0


def _default_value(ci: dict) -> Any:
    t = (ci.get("valueType") or "").capitalize()
    if t == "Boolean":
        return False
    if t == "Integer":
        return json.loads(ci["valueDesc"]).get("min")
    if t == "Enum":
        return json.loads(ci["valueDesc"]).get("range")[0]
    return ""


# --- read strategies: (raw, config_item) -> value ------------------------------------------
def _r_default(raw, ci):
    return raw if raw is not None and raw != "" else _default_value(ci)


def _r_enum(raw, ci):
    m = ci["enumMappingMap"]
    k = str(raw)
    for key in (k, k.lower()):
        if key in m and "value" in m[key]:
            return m[key]["value"]
    return _default_value(ci)


def _r_color(raw, ci):
    return None if raw is None else json.dumps({"h": _hex(raw[0:4]), "s": _hex(raw[4:8]), "v": _hex(raw[8:12])})


def _r_contr(raw, ci):
    if raw is None:
        return None
    if not raw:
        return ""
    flag = _hex(raw[:1])
    mode = "direct" if flag == 0 else "gradient" if flag == 1 else ""
    return json.dumps({"change_mode": mode, "h": _hex(raw[1:5]), "s": _hex(raw[5:9]), "v": _hex(raw[9:13]),
                       "bright": _hex(raw[13:17]), "temperature": _hex(raw[17:])})


_SCENE_MODES = {0: "static", 1: "jump", 2: "gradient"}


def _r_scene(raw, ci):
    if raw is None:
        return None
    units = []
    body = raw[2:]
    for i in range(len(body) // 26):
        it = body[i * 26:(i + 1) * 26]
        units.append({"unit_switch_duration": _hex(it[:2]), "unit_gradient_duration": _hex(it[2:4]),
                      "unit_change_mode": _SCENE_MODES.get(_hex(it[4:6]), ""), "h": _hex(it[6:10]), "s": _hex(it[10:14]),
                      "v": _hex(it[14:18]), "bright": _hex(it[18:22]), "temperature": _hex(it[22:])})
    return json.dumps({"scene_num": 1 + _hex(raw[:2]), "scene_units": units})


def _b64_hex(raw: str) -> str:
    return base64.b64decode(raw).hex()


def _hsv_from_hex_rgb(rgb: str) -> dict:
    """`RRGGBB` -> `{"h": 0..360, "s", "v": 0..255}`."""
    h, s, v = colorsys.rgb_to_hsv(*(int(rgb[i:i + 2], 16) / 255 for i in (0, 2, 4)))
    return {"h": round(h * 360, 1), "s": round(s * 255, 1), "v": round(v * 255, 1)}


def _dj_v1_hsv(raw, digits):
    """`RRGGBB` + `HHHH` + `SS` + `VV` (dj_v1_hsv_alg rounds s/v to 3 digits of the 0..1 share, voice_atm_color to 4);
    the tail `0168ffff` means "take the RGB"."""
    if raw is None:
        return None
    if raw[6:] == "0168ffff":
        return json.dumps(_hsv_from_hex_rgb(raw[:6]))
    s = round(int(raw[10:12], 16) / 255, digits)
    v = round(int(raw[12:], 16) / 255, digits)
    return json.dumps({"h": int(raw[6:10], 16) / 1.0, "s": round(s * 255, 1), "v": round(v * 255, 1)})


def _r_dj_v1_scene(raw, ci):
    if raw is None:
        return None
    if not raw:
        return ""
    body = raw[8:]
    hsv = [_hsv_from_hex_rgb(body[i:i + 6]) for i in range(0, len(body), 6)]
    return json.dumps({"frequency": int(raw[4:6], 16), "bright": int(raw[0:2], 16), "temperature": int(raw[2:4], 16),
                       "hsv": hsv})


def _hhmm(hi: int, lo: int) -> str:
    n = hi * 256 + lo
    return f"{n // 60:02d}:{n % 60:02d}"


def _timers(raw, width):
    if raw is None:
        return None
    b = base64.b64decode(raw)
    out = []
    for i in range(0, len(b), width):
        t = b[i:i + width]
        item = {"timer_switch": t[0] > 0, "week_day": [d for d in range(8) if t[1] >> d & 1],
                "start_time": _hhmm(t[2], t[3]), "end_time": _hhmm(t[4], t[5])}
        if width == 10:
            item.update(open_time=_hhmm(t[6], t[7]), close_time=_hhmm(t[8], t[9]))
        out.append(item)
    return json.dumps(out)


def _r_hb_djv1_color(raw, ci):
    if raw is None:
        return None
    o = json.loads(raw)
    r, g, b = colorsys.hsv_to_rgb(float(o["h"]) / 360, float(o["s"]) / 255, float(o["v"]) / 255)
    return f"{int(r * 255)}|{int(g * 255)}|{int(b * 255)}"


def _r_hb_jsq(raw, ci):
    return "" if raw is None else raw + "|0|0"


def _hb_range(raw, t_min, t_max, i_min, i_max):
    if raw is None:
        return ""
    x = min(i_max, max(i_min, int(raw)))
    return min(t_max, max(t_min, math.floor((t_max - t_min) / (i_max - i_min) * (x - i_min) + t_min)))


def _r_ms_syn(raw, ci):
    """The unlocked indices. The SDK returns a `set`; a sorted list is the same data in a JSON-safe form."""
    if raw is None:
        return None
    if not raw:
        return []
    try:
        b = base64.b64decode(raw)
        out = {((b[i] & 0x7F) - 1) * 8 + bit for i in range(0, len(b), 2) for bit in range(8) if b[i + 1] >> bit & 1}
    except (ValueError, IndexError):
        out = set()
    return sorted(out)


def _r_sd_clean(raw, ci):
    if raw is None:
        return None
    n = len(raw)
    if n == 6:
        rec, t, a, m = "", raw[:3], raw[3:6], ""
    elif n == 11:
        rec, t, a, m = "", raw[:3], raw[3:6], raw[6:11]
    elif n == 18:
        rec, t, a, m = raw[:12], raw[12:15], raw[15:18], ""
    else:
        rec, t, a, m = raw[:12], raw[12:15], raw[15:18], raw[18:23]
    return json.dumps({"record_time": rec, "clean_time": int(t), "clean_area": int(a), "map_id": m})


def _r_db_params(raw, ci):
    if raw is None:
        return None
    s = _b64_hex(raw)
    o: dict[str, Any] = {}
    if (len(s) >= 34 and s.startswith("010f")) or (len(s) >= 36 and s.startswith("020f")):
        o = {"voltage": _hex(s[4:8]) / 10.0, "electricCurrent": _hex(s[8:14]) / 1000.0, "power": _hex(s[14:20]) / 1000.0,
             "reactivePower": _hex(s[20:26]) / 1000.0, "apparentPower": _hex(s[26:32]) / 1000.0,
             "powerFactor": _hex(s[32:34]) / 100.0}
        if s.startswith("020f"):
            sign = _hex(s[34:36])
            for bit, k in ((1, "electricCurrent"), (2, "power"), (4, "reactivePower"), (8, "powerFactor")):
                if sign & bit:
                    o[k] = -o[k]
    else:
        o = {"voltage": _hex(s[0:4]) / 10.0, "electricCurrent": _hex(s[4:10]) / 1000.0, "power": _hex(s[10:16]) / 1000.0}
    return json.dumps(o)


def _r_db_daily(raw, ci):
    if raw is None:
        return None
    s = _b64_hex(raw)
    return json.dumps({"startMonth": _hex(s[0:2]), "startDay": _hex(s[2:4]), "endMonth": _hex(s[4:6]),
                       "endDay": _hex(s[6:8]), "electricTotal": _hex(s[8:16]) / 100.0})


def _r_db_month(raw, ci):
    if raw is None:
        return None
    s = _b64_hex(raw)
    return json.dumps({"startYear": _hex(s[:2]), "startMonth": _hex(s[2:4]), "endYear": _hex(s[4:6]),
                       "endMonth": _hex(s[6:8]), "electricTotal": _hex(s[8:16]) / 100.0})


def _r_db_frozen(raw, ci):
    if raw is None:
        return None
    s = _b64_hex(raw)
    return json.dumps({"day": _hex(s[:2]), "hour": _hex(s[2:4])})


_ALARMS = {1: ("overcurrent", 0), 2: ("three_phase_current_imbalance", 0), 3: ("ammeter_overvoltage", 0),
           4: ("under_voltage", 0), 5: ("three_phase_current_loss", None), 6: ("power_failure", None),
           7: ("magnetic", None), 8: ("insufficient_balance", 0), 9: ("arrears", None), 10: ("battery_overvoltage", 2),
           11: ("cover_open", None), 12: ("meter_cover_open", None), 13: ("fault", None)}


def _r_db_alarm(raw, ci):
    if raw is None:
        return None
    s = _b64_hex(raw)
    out = []
    for i in range(0, len(s), 8):
        rec = s[i:i + 8]
        known = _ALARMS.get(_hex(rec[0:2]))
        if known is None:
            continue
        name, scale = known
        item: dict[str, Any] = {"alarmCode": name, "doAction": _hex(rec[2:4]) == 1}
        if scale is not None:
            t = _hex(rec[4:8]) / math.pow(10, scale)
            item["threshold"] = str(t) if scale > 0 else str(int(t))
        out.append(item)
    return json.dumps(out)


# --- write strategies: (value, config_item) -> raw ---------------------------------------
def _w_pass(v, ci):
    return v


def _w_enum(v, ci):
    """Invert enumMappingMap: prefer a numeric-string key, else the first key mapping to `v` (wire form LIVE-VERIFY)."""
    m = ci["enumMappingMap"]
    keys = [k for k, e in m.items() if isinstance(e, dict) and e.get("value") == v]
    if not keys:
        return v
    return next((k for k in keys if k.isdigit()), keys[0])


def _obj(v):
    return json.loads(v) if isinstance(v, str) else v


def _w_color(v, ci):
    o = _obj(v)
    return f"{o['h']:04x}{o['s']:04x}{o['v']:04x}"


def _w_contr(v, ci):
    o = _obj(v)
    flag = {"direct": 0, "gradient": 1}.get(o.get("change_mode"), 0)
    return f"{flag:x}{o['h']:04x}{o['s']:04x}{o['v']:04x}{o['bright']:04x}{o['temperature']:04x}"


def _w_scene(v, ci):
    o = _obj(v)
    rev = {"static": 0, "jump": 1, "gradient": 2}
    out = f"{o['scene_num'] - 1:02x}"
    for u in o["scene_units"]:
        out += (f"{u['unit_switch_duration']:02x}{u['unit_gradient_duration']:02x}{rev.get(u['unit_change_mode'], 0):02x}"
                f"{u['h']:04x}{u['s']:04x}{u['v']:04x}{u['bright']:04x}{u['temperature']:04x}")
    return out


def _w_readonly(v, ci):
    raise NoWritePath("this dp's value conversion has no inverse")


def _w_dj_v1_hsv(v, ci):
    """`RRGGBB` (the colour at full value, as the device shows it) + `HHHH` `SS` `VV`: `{"h": 0..360, "s", "v": 0..255}`."""
    o = _obj(v)
    h, s, val = round(float(o["h"])), round(float(o["s"])), round(float(o["v"]))
    r, g, b = colorsys.hsv_to_rgb(h / 360, s / 255, val / 255)
    return f"{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}{h:04x}{s:02x}{val:02x}"


def _hhmm_bytes(t: str) -> bytes:
    hh, mm = (int(x) for x in t.split(":"))
    n = hh * 60 + mm
    return bytes((n >> 8, n & 0xFF))


def _w_timers(v, width):
    out = b""
    for t in _obj(v):
        out += bytes((1 if t["timer_switch"] else 0, sum(1 << d for d in t["week_day"])))
        out += _hhmm_bytes(t["start_time"]) + _hhmm_bytes(t["end_time"])
        if width == 10:
            out += _hhmm_bytes(t["open_time"]) + _hhmm_bytes(t["close_time"])
    return base64.b64encode(out).decode()


def _w_hb_jsq(v, ci):
    return v[:-4] if isinstance(v, str) and v.endswith("|0|0") else v


STRATEGIES = {
    "default": (_r_default, _w_pass),
    "enum": (_r_enum, _w_enum),
    "dj_v2_color_alg": (_r_color, _w_color),
    "dj_v2_contr_alg": (_r_contr, _w_contr),
    "dj_v2_music_alg": (_r_contr, _w_contr),
    "dj_v2_scene_alg": (_r_scene, _w_scene),
    "dj_v1_hsv_alg": (lambda raw, ci: _dj_v1_hsv(raw, 3), _w_dj_v1_hsv),
    "voice_atm_color": (lambda raw, ci: _dj_v1_hsv(raw, 4), _w_dj_v1_hsv),
    "dj_v1_scene_alg": (_r_dj_v1_scene, _w_readonly),
    "cz_timer1_alg": (lambda raw, ci: _timers(raw, 6), lambda v, ci: _w_timers(v, 6)),
    "cz_timer2_alg": (lambda raw, ci: _timers(raw, 10), lambda v, ci: _w_timers(v, 10)),
    "hb_djv1_color": (_r_hb_djv1_color, _w_readonly),
    "hb_jsq_lightv1": (_r_hb_jsq, _w_hb_jsq),
    "hb_range_v1": (lambda raw, ci: _hb_range(raw, 0, 100, 25, 255), _w_readonly),
    "hb_range_v2": (lambda raw, ci: _hb_range(raw, 1000, 12000, 0, 255), _w_readonly),
    "ms_dp_syn_alg": (_r_ms_syn, _w_readonly),
    "sd_clean_record": (_r_sd_clean, _w_readonly),
    "db_v1_params": (_r_db_params, _w_readonly),
    "db_v1_daily": (_r_db_daily, _w_readonly),
    "db_v1_month": (_r_db_month, _w_readonly),
    "db_v1_frozen": (_r_db_frozen, _w_readonly),
    "db_v1_alarm": (_r_db_alarm, _w_readonly),
}


@dataclass
class Remap:
    """A user's fix for a dp that speaks a different vocabulary than the tables expect (overrides `remap`), applied
    after the read strategy and before the write strategy. `alias`: device value -> standard value (a bijection;
    values not listed pass through). `invert`: a Boolean is negated, an Integer mirrored inside `[lo, hi]`."""
    alias: dict = field(default_factory=dict)
    invert: bool = False
    bounds: tuple[float, float] | None = None       # Integer range, raw units, for `invert`

    def _flip(self, v: Any) -> Any:
        if not self.invert:
            return v
        if isinstance(v, bool):
            return not v
        if isinstance(v, (int, float)) and self.bounds is not None:
            lo, hi = self.bounds
            out = lo + hi - v
            return int(out) if isinstance(v, int) and float(out).is_integer() else out
        return v

    def read(self, v: Any) -> Any:
        if isinstance(v, str) and v in self.alias:
            v = self.alias[v]
        return self._flip(v)

    def write(self, v: Any) -> Any:
        v = self._flip(v)
        return next((dev for dev, std in self.alias.items() if std == v), v)


@dataclass
class Adapter:
    """dp-id <-> code translation + value conversion for one device."""
    entries: dict[str, tuple[str, str, dict]] = field(default_factory=dict)   # dpid -> (code, strategy, config_item)
    enum_ranges: dict[str, list] = field(default_factory=dict)                # code -> range (Enum guard)
    unsupported: dict[str, str] = field(default_factory=dict)                 # dpid -> strategy name (passthrough)
    remaps: dict[str, Remap] = field(default_factory=dict)                  # code -> user value fix (overrides `remap`)

    @classmethod
    def from_local_strategy(cls, local_strategy: dict, status_range: dict[str, Any] | None = None) -> Adapter:
        a = cls()
        for dpid, meta in (local_strategy or {}).items():
            code = strategy_code(meta)
            if not code:
                continue
            name = meta.get("value_convert") or "default"
            ci = dict(meta.get("config_item") or {})
            if name not in STRATEGIES:
                a.unsupported[str(dpid)] = name
                name = "default"
            a.entries[str(dpid)] = (code, name, ci)
        for code, spec in (status_range or {}).items():
            if getattr(spec, "type", None) == "Enum":
                try:
                    v = spec.values if isinstance(spec.values, dict) else json.loads(spec.values)
                    a.enum_ranges[code] = list(v.get("range", []))
                except (ValueError, TypeError, AttributeError):
                    pass
        return a

    def codes(self) -> dict[str, str]:
        """{dp id: code}."""
        return {d: e[0] for d, e in self.entries.items()}

    def code_for_dp(self, dpid) -> str | None:
        e = self.entries.get(str(dpid))
        return e[0] if e else None

    def dp_for_code(self, code: str) -> str | None:
        return next((d for d, e in self.entries.items() if e[0] == code), None)

    def read(self, dps: dict[str, Any]) -> dict[str, Any]:
        """{dpid: raw} -> {code: value}. Unknown dp ids are skipped; an Enum value outside its range is DROPPED
        (Manager._on_device_report parity). A malformed raw value (strategy raises) is dropped too."""
        out: dict[str, Any] = {}
        for dpid, raw in dps.items():
            e = self.entries.get(str(dpid))
            if e is None:
                continue
            code, name, ci = e
            try:
                value = STRATEGIES[name][0](raw, ci)
            except (ValueError, TypeError, KeyError, IndexError):
                continue
            if code in self.remaps:
                value = self.remaps[code].read(value)
            rng = self.enum_ranges.get(code)
            if rng is not None and value not in rng:
                continue
            out[code] = value
        return out

    def write(self, commands: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
        """[{code, value}] -> ({dpid: raw}, unmapped codes)."""
        dps: dict[str, Any] = {}
        missing: list[str] = []
        for c in commands:
            dpid = self.dp_for_code(c["code"])
            if dpid is None:
                missing.append(c["code"])
                continue
            code, name, ci = self.entries[dpid]
            value = self.remaps[code].write(c["value"]) if code in self.remaps else c["value"]
            dps[dpid] = STRATEGIES[name][1](value, ci)
        return dps, missing
