"""Platform builders (L2, hand-authored, reviewed against tuya-device-handlers definition/*.py)."""
from __future__ import annotations

from typing import Any

from . import codecs, ops
from .model import BITMAP, BOOLEAN, ENUM, INTEGER, JSON, RAW, STRING, DeviceSchema, DpRef, resolve
from .runtime import ActionDPCodeNotFound, EntityPlan, HostEnv, WriteRejected, builder, identity
from .units import resolve_unit


def _find(schema: DeviceSchema, desc: dict[str, Any], kind: str):
    """get_default_definition: `<Kind>Wrapper.find_dpcode(device, key, prefer_function=True)`."""
    return resolve(schema, DpRef((desc["key"],), (kind,), "function_first"))


@builder("switch", "SWITCHES")
def switch(schema: DeviceSchema, env: HostEnv, desc: dict[str, Any]) -> EntityPlan | None:
    r = _find(schema, desc, BOOLEAN)
    if r is None:
        return None
    code = r.code
    read = lambda st: {"is_on": ops.validate_bool_read(st.get(code))}
    write = lambda action, args, st: [{"code": code, "value": ops.validate_bool_write({"turn_on": True, "turn_off": False}[action])}]
    return EntityPlan("switch", desc["key"], identity(desc), {"main": r}, (code,), read, write)


@builder("button", "BUTTONS")
def button(schema: DeviceSchema, env: HostEnv, desc: dict[str, Any]) -> EntityPlan | None:
    r = _find(schema, desc, BOOLEAN)
    if r is None:
        return None
    code = r.code
    write = lambda action, args, st: [{"code": code, "value": ops.validate_bool_write(True)}]   # press
    return EntityPlan("button", desc["key"], identity(desc), {"main": r}, (code,), None, write)


@builder("select", "SELECTS")
def select(schema: DeviceSchema, env: HostEnv, desc: dict[str, Any]) -> EntityPlan | None:
    r = _find(schema, desc, ENUM)
    if r is None:
        return None
    code, spec = r.code, r.spec
    read = lambda st: {"current_option": ops.validate_enum_read(spec, st.get(code)), "options": list(spec.range)}
    write = lambda action, args, st: [{"code": code, "value": ops.validate_enum_write(spec, args["option"])}]
    return EntityPlan("select", desc["key"], identity(desc), {"main": r}, (code,), read, write)


@builder("number", "NUMBERS")
def number(schema: DeviceSchema, env: HostEnv, desc: dict[str, Any]) -> EntityPlan | None:
    r = _find(schema, desc, INTEGER)
    if r is None:
        return None
    code, spec = r.code, r.spec
    ident = identity(desc)
    ur = resolve_unit("number", desc.get("device_class"), spec.unit, desc.get("native_unit_of_measurement"),
                      desc.get("suggested_unit_of_measurement"), schema.status.get("temp_unit_convert"), env.allowed_units)
    ident.update(device_class=ur.device_class, native_unit=ur.native_unit, suggested_unit=ur.suggested_unit)
    ident.update(native_min_value=ops.scale_value(spec, spec.min), native_max_value=ops.scale_value(spec, spec.max),
                 native_step=ops.scale_value(spec, spec.step))
    read = lambda st: {"native_value": ops.validate_int_read(spec, st.get(code))}
    write = lambda action, args, st: [{"code": code, "value": ops.validate_int_write(spec, args["value"])}]
    return EntityPlan("number", desc["key"], ident, {"main": r}, (code,), read, write)


@builder("binary_sensor", "BINARY_SENSORS")
def binary_sensor(schema: DeviceSchema, env: HostEnv, desc: dict[str, Any]) -> EntityPlan | None:
    """Exclusive branches (first wins): bitmap_key => bitmap only; Boolean; legacy in-set."""
    dpcode = desc.get("dpcode") or desc["key"]
    ident = identity(desc)
    bitmap_key = desc.get("bitmap_key")
    if bitmap_key is not None:
        r = resolve(schema, DpRef((dpcode,), (BITMAP,)))
        if r is None or bitmap_key not in r.spec.label:
            return None
        mask = r.spec.label.index(bitmap_key)
        def read(st):
            raw = st.get(r.code)
            return {"is_on": None if raw is None else (raw & (1 << mask)) != 0}
        return EntityPlan("binary_sensor", desc["key"], ident, {"main": r}, (r.code,), read)
    r = resolve(schema, DpRef((dpcode,), (BOOLEAN,)))
    if r is not None:
        return EntityPlan("binary_sensor", desc["key"], ident, {"main": r}, (r.code,),
                          lambda st: {"is_on": ops.validate_bool_read(st.get(r.code))})
    if not (dpcode in schema.function or dpcode in schema.status or dpcode in schema.status_range):
        return None
    on = desc.get("on_value", True)
    valid = set(on["$set"]) if isinstance(on, dict) else {on}
    def read_set(st):
        raw = st.get(dpcode)
        return {"is_on": None if raw is None else raw in valid}
    return EntityPlan("binary_sensor", desc["key"], ident, {}, (dpcode,), read_set)


@builder("sensor", "SENSORS")
def sensor(schema: DeviceSchema, env: HostEnv, desc: dict[str, Any]) -> EntityPlan | None:
    dpcode = desc.get("dpcode") or desc["key"]
    ident = identity(desc)
    if desc.get("wrapper_class"):
        return _wrapped_sensor(schema, env, desc, dpcode, ident)
    ri = resolve(schema, DpRef((dpcode,), (INTEGER,)))
    if ri is not None:
        spec, code = ri.spec, ri.code
        delta = ri.report_type == "sum"
        if delta:
            ident.setdefault("state_class", "total_increasing")
        ident["kind"] = "delta" if delta else "integer"
        ur = resolve_unit("sensor", desc.get("device_class"), spec.unit, desc.get("native_unit_of_measurement"),
                          desc.get("suggested_unit_of_measurement"), schema.status.get("temp_unit_convert"), env.allowed_units)
        ident.update(device_class=ur.device_class, native_unit=ur.native_unit, suggested_unit=ur.suggested_unit)
        if delta:
            read = lambda st, slot=None: {"native_value": slot.total if slot else 0.0}
            return EntityPlan("sensor", desc["key"], ident, {"main": ri}, (code,), read, slot_kind="delta")
        read = lambda st, slot=None: {"native_value": ops.validate_int_read(spec, st.get(code))}
        return EntityPlan("sensor", desc["key"], ident, {"main": ri}, (code,), read)
    re_ = resolve(schema, DpRef((dpcode,), (ENUM,)))
    if re_ is not None:
        spec, code = re_.spec, re_.code
        dc = desc.get("device_class")
        if dc is None:
            dc = "enum"
            ident["options"] = list(spec.range)
        ident["kind"] = "enum"
        ur = resolve_unit("sensor", dc, None, desc.get("native_unit_of_measurement"),
                          desc.get("suggested_unit_of_measurement"), schema.status.get("temp_unit_convert"), env.allowed_units)
        ident.update(device_class=ur.device_class, native_unit=ur.native_unit, suggested_unit=ur.suggested_unit)
        read = lambda st: {"native_value": ops.validate_enum_read(spec, st.get(code))}
        return EntityPlan("sensor", desc["key"], ident, {"main": re_}, (code,), read)
    return None


def _finish_sensor(schema, env, desc, ident, dc, dp_unit, wrapper_suggested):
    ur = resolve_unit("sensor", dc, dp_unit, desc.get("native_unit_of_measurement"),
                      desc.get("suggested_unit_of_measurement") or wrapper_suggested,
                      schema.status.get("temp_unit_convert"), env.allowed_units)
    ident.update(device_class=ur.device_class, native_unit=ur.native_unit, suggested_unit=ur.suggested_unit)


def _wrapped_sensor(schema, env, desc, dpcode, ident):
    """First wrapper class (in table order) whose find_dpcode succeeds wins."""
    import re
    for wname in desc["wrapper_class"]:
        w = wname.lstrip("$")
        if w == "WindDirectionEnumWrapper":
            r = resolve(schema, DpRef((dpcode,), (ENUM,)))
            if r is None:
                continue
            code = r.code
            _finish_sensor(schema, env, desc, ident, desc.get("device_class"), None, None)
            ident["kind"] = "wind_direction"
            read = lambda st: {"native_value": None if st.get(code) is None else codecs.WIND_DIRECTIONS.get(st.get(code))}
            return EntityPlan("sensor", desc["key"], ident, {"main": r}, (code,), read)
        m = re.fullmatch(r"Electricity(\w+?)(Raw|Json|HexString)Wrapper", w)
        if not m:
            raise NotImplementedError(w)
        what, form = m.groups()
        if form == "Json":
            key, unit = codecs.ELEC_JSON[what]
            r = resolve(schema, DpRef((dpcode,), (JSON,)))
            if r is None:
                continue
            payload = codecs.json_loads(schema.status.get(r.code))
            if payload is not None and key not in payload:
                continue
            code = r.code
            def read(st, code=code, key=key):
                p = codecs.json_loads(st.get(code))
                return {"native_value": None if p is None else p.get(key)}
            sug = None
        else:
            attr, unit, sug = codecs.ELEC_RAW[what]
            r = resolve(schema, DpRef((dpcode,), (RAW if form == "Raw" else STRING,)))
            if r is None:
                continue
            code = r.code
            def parse(v, form=form):
                return codecs.electricity_from_bytes(codecs.b64_decode(v)) if form == "Raw" and codecs.b64_decode(v) is not None \
                    else (codecs.electricity_from_hex(v) if form != "Raw" else None)
            first = schema.status.get(code)
            if first:
                pv = parse(first)
                if pv is None or getattr(pv, attr) is None:
                    continue
            def read(st, code=code, attr=attr, parse=parse):
                v = st.get(code)
                if v is None:
                    return {"native_value": None}
                pv = parse(v)
                return {"native_value": None if pv is None else getattr(pv, attr)}
        _finish_sensor(schema, env, desc, ident, desc.get("device_class"), unit, sug)
        ident["kind"] = f"electricity_{form.lower()}"
        return EntityPlan("sensor", desc["key"], ident, {"main": r}, (code,), read)
    return None


def _boolean_plan(platform: str, schema, desc, read_key: str, invert: bool = False):
    r = _find(schema, desc, BOOLEAN)
    if r is None:
        return None
    code = r.code
    def read(st):
        v = ops.validate_bool_read(st.get(code))
        return {read_key: v if (v is None or not invert) else not v}
    return EntityPlan(platform, desc["key"], identity(desc), {"main": r}, (code,), read)


@builder("siren", "SIRENS")
def siren(schema, env, desc):
    plan = _boolean_plan("siren", schema, desc, "is_on")
    if plan:
        code = plan.depends_on[0]
        plan.write = lambda action, args, st: [{"code": code, "value": {"turn_on": True, "turn_off": False}[action]}]
    return plan


@builder("valve", "VALVES")
def valve(schema, env, desc):
    plan = _boolean_plan("valve", schema, desc, "is_closed", invert=True)   # DP true == open
    if plan:
        code = plan.depends_on[0]
        plan.write = lambda action, args, st: [{"code": code, "value": {"open": True, "close": False}[action]}]
    return plan


@builder("camera", "CAMERAS")
def camera(schema, env, desc):
    """Existence Always; roles motion_switch (function_first) and record_switch are both optional (spec 6.5)."""
    m = resolve(schema, DpRef(("motion_switch",), (BOOLEAN,), "function_first"))
    rec = resolve(schema, DpRef(("record_switch",), (BOOLEAN,)))
    roles = {k: v for k, v in (("motion_switch", m), ("record_switch", rec)) if v}
    def read(st):
        rv = ops.validate_bool_read(st.get(rec.code)) if rec else None
        mv = ops.validate_bool_read(st.get(m.code)) if m else None
        return {"is_recording": rv if rv is not None else False,
                "motion_detection_enabled": mv if mv else False}
    def write(action, args, st):
        if m is None:
            raise WriteRejected("motion_switch not available")   # TODO parity: core raises ActionDPCodeNotFound?
        return [{"code": m.code, "value": action == "enable_motion_detection"}]
    return EntityPlan("camera", desc["key"], identity(desc), roles, tuple(v.code for v in roles.values()), read, write)


@builder("event", "EVENTS")
def event(schema, env, desc):
    dpcode = desc["key"]
    w = (desc.get("wrapper_class") or "$SimpleEventEnumWrapper").lstrip("$")
    ident = identity(desc)
    if w == "SimpleEventEnumWrapper":
        r = resolve(schema, DpRef((dpcode,), (ENUM,)))
        if r is None:
            return None
        ident["event_types"] = list(r.spec.range)
        code = r.code
        read = lambda st: {"event": None if st.get(code) is None else (st.get(code), None)}
    else:
        kind = STRING if w == "Base64Utf8StringEventWrapper" else RAW
        r = resolve(schema, DpRef((dpcode,), (kind,)))
        if r is None:
            return None
        ident["event_types"] = ["triggered"]
        code = r.code
        def read(st):
            raw = st.get(code)
            if raw is None:
                return {"event": None}
            return {"event": ("triggered", {"message": codecs.b64_decode(raw).decode("utf-8")})}
    return EntityPlan("event", desc["key"], ident, {"main": r}, (code,), read, slot_kind="event")


# --- cover -------------------------------------------------------------------
COVER_FEATURES = {"OPEN": 1, "CLOSE": 2, "SET_POSITION": 4, "STOP": 8, "SET_TILT_POSITION": 128}
_COVER_ENUM = {"open": "open", "close": "close", "stop": "stop"}
_COVER_ENUM_SPECIAL = {"open": "FZ", "close": "ZZ", "stop": "STOP"}
_CLOSED_ENUM = {"close": True, "fully_close": True, "open": False, "fully_open": False}


def _codes(v) -> tuple[str, ...]:
    return () if v is None else ((v,) if isinstance(v, str) else tuple(v))


@builder("cover", "COVERS")
def cover(schema: DeviceSchema, env: HostEnv, desc: dict[str, Any]) -> EntityPlan | None:
    ikey = desc["key"]
    if not (ikey in schema.function or ikey in schema.status_range):        # KeyPresent
        return None
    pos_w = (desc.get("position_wrapper") or "$DPCodeInvertedPercentageWrapper").lstrip("$")
    inverted = None if pos_w == "ControlBackModePercentageMappingWrapper" else True   # None => by control_back_mode

    def reverse(st):
        return (st.get("control_back_mode") != "back") if inverted is None else inverted

    def int_role(codes, source="status_range_first"):
        return resolve(schema, DpRef(_codes(codes), (INTEGER,), source)) if codes else None

    cur = int_role(desc.get("current_position"))
    setp = int_role(desc.get("set_position"), "function_first")
    tilt = resolve(schema, DpRef(("angle_horizontal", "angle_vertical"), (INTEGER,), "function_first"))

    # current_state: Enum(closed map) by default, or an inverted Boolean
    cs_w = (desc.get("current_state_wrapper") or "$CoverClosedEnumWrapper").lstrip("$")
    cs = None
    if desc.get("current_state"):
        cs = resolve(schema, DpRef(_codes(desc["current_state"]), (BOOLEAN if cs_w == "DPCodeInvertedBooleanWrapper" else ENUM,)))

    # instruction: (description's enum wrapper) or Boolean(open/close)
    special = (desc.get("instruction_wrapper") or "").lstrip("$") == "CoverInstructionSpecialEnumWrapper"
    table = _COVER_ENUM_SPECIAL if special else _COVER_ENUM
    ins = resolve(schema, DpRef((ikey,), (ENUM,), "function_first"))
    ins_kind = "enum"
    if ins is not None:
        options = [a for a, raw in table.items() if raw in ins.spec.range]
    else:
        ins = resolve(schema, DpRef((ikey,), (BOOLEAN,), "function_first"))
        ins_kind, options = "boolean", ["open", "close"]

    features = 0
    for a in options:
        features |= COVER_FEATURES[a.upper()]
    if setp:
        features |= COVER_FEATURES["SET_POSITION"]
    if tilt:
        features |= COVER_FEATURES["SET_TILT_POSITION"]
    ident = identity(desc)
    ident["supported_features"] = features

    pos_src = cur or setp     # current_position_wrapper or set_position_wrapper

    def pos_read(role, st):
        return None if role is None else ops.remap_read(role.spec, st.get(role.code), 0, 100, reverse(st))

    def read(st):
        position = pos_read(pos_src, st)
        if position is not None:
            closed = position == 0
        elif cs is None:
            closed = None
        elif cs.kind == BOOLEAN:
            v = ops.validate_bool_read(st.get(cs.code))
            closed = None if v is None else not v
        else:
            v = ops.validate_enum_read(cs.spec, st.get(cs.code))
            closed = None if v is None else _CLOSED_ENUM.get(v)
        return {"is_closed": closed, "current_position": position, "current_tilt_position": pos_read(tilt, st)}

    def instr(action, st):
        if ins is None or action not in options:
            return []
        if ins_kind == "boolean":
            raw = {"open": True, "close": False}[action]
        else:
            raw = table[action]
        return [{"code": ins.code, "value": raw}]

    def write_pos(role, value, st):
        return [{"code": role.code, "value": ops.remap_write(role.spec, value, 0, 100, reverse(st))}]

    def write(action, args, st):
        if action == "open_cover":
            return write_pos(setp, 100, st) if setp else instr("open", st)
        if action == "close_cover":
            return write_pos(setp, 0, st) if setp else instr("close", st)
        if action == "stop_cover":
            return instr("stop", st)
        if action == "set_cover_position":
            return write_pos(setp, args["position"], st)
        if action == "set_cover_tilt_position":
            return write_pos(tilt, args["tilt_position"], st)
        raise KeyError(action)

    roles = {k: v for k, v in (("instruction", ins), ("current_position", cur), ("set_position", setp),
                               ("current_state", cs), ("tilt", tilt)) if v}
    deps = tuple(dict.fromkeys(v.code for v in roles.values()))
    return EntityPlan("cover", desc["key"], ident, roles, deps + ("control_back_mode",), read, write, update_all=True)


# --- fan ---------------------------------------------------------------------
FAN_FEATURES = {"SET_SPEED": 1, "OSCILLATE": 2, "DIRECTION": 4, "PRESET_MODE": 8, "TURN_OFF": 16, "TURN_ON": 32}
_FAN_DIRECTION = ("forward", "reverse")
_FAN_SPEED = ("fan_speed_percent", "fan_speed", "speed", "fan_speed_enum")
_FAN_SWITCH = ("switch_fan", "fan_switch", "switch")
_FAN_OSC = ("switch_horizontal", "switch_vertical")
_FAN_MODE = ("fan_mode", "mode")


@builder("fan", "FANS")
def fan(schema: DeviceSchema, env: HostEnv, desc: dict[str, Any]) -> EntityPlan | None:
    props = (*_FAN_SWITCH, *_FAN_SPEED, *_FAN_OSC, "fan_direction")
    if not any(c in schema.function or c in schema.status or c in schema.status_range for c in props):
        return None
    F = "function_first"
    direction = resolve(schema, DpRef(("fan_direction",), (ENUM,), F))
    dir_options = [d for d in _FAN_DIRECTION if direction and d in direction.spec.range]
    mode = resolve(schema, DpRef(_FAN_MODE, (ENUM,), F))
    osc = resolve(schema, DpRef(_FAN_OSC, (BOOLEAN,), F))
    speed = resolve(schema, DpRef(_FAN_SPEED, (INTEGER,), F)) or resolve(schema, DpRef(_FAN_SPEED, (ENUM,), F))
    switch = resolve(schema, DpRef(_FAN_SWITCH, (BOOLEAN,), F))

    ident = identity(desc)
    feats = 0
    if mode:
        feats |= FAN_FEATURES["PRESET_MODE"]; ident["preset_modes"] = list(mode.spec.range)
    if speed:
        feats |= FAN_FEATURES["SET_SPEED"]
        ident["speed_count"] = len(speed.spec.range) if speed.kind == ENUM else 100
    if osc:
        feats |= FAN_FEATURES["OSCILLATE"]
    if direction:
        feats |= FAN_FEATURES["DIRECTION"]
    if switch:
        feats |= FAN_FEATURES["TURN_ON"] | FAN_FEATURES["TURN_OFF"]
    ident["supported_features"] = feats

    def speed_read(st):
        if speed is None:
            return None
        raw = st.get(speed.code)
        if speed.kind == INTEGER:
            return ops.remap_read(speed.spec, raw, 1, 100)
        raw = ops.validate_enum_read(speed.spec, raw)          # out-of-range -> UNKNOWN before the position math
        if raw is None:
            return None
        opts = speed.spec.range
        return ((opts.index(raw) + 1) * 100) // len(opts)

    def speed_write(p):
        if speed.kind == INTEGER:
            return ops.remap_write(speed.spec, p, 1, 100)
        opts = speed.spec.range
        for i, s in enumerate(opts):
            if p <= ((i + 1) * 100) // len(opts):
                return s
        return opts[-1]

    def read(st):
        d = None
        if direction:
            raw = st.get(direction.code)
            d = raw if raw and raw in dir_options else None
        return {"is_on": ops.validate_bool_read(st.get(switch.code)) if switch else None,
                "percentage": speed_read(st), "direction": d,
                "oscillating": ops.validate_bool_read(st.get(osc.code)) if osc else None,
                "preset_mode": ops.validate_enum_read(mode.spec, st.get(mode.code)) if mode else None}

    def write(action, args, st):
        if action == "turn_off":
            return [{"code": switch.code, "value": False}]
        if action == "turn_on":
            if switch is None:
                return []
            cmds = [{"code": switch.code, "value": True}]
            if args.get("percentage") is not None and speed:
                cmds.append({"code": speed.code, "value": speed_write(args["percentage"])})
            if args.get("preset_mode") is not None and mode:
                cmds.append({"code": mode.code, "value": ops.validate_enum_write(mode.spec, args["preset_mode"])})
            return cmds
        if action == "set_percentage":
            if args["percentage"] == 0 and switch is not None:      # speed wrappers have no off position
                return [{"code": switch.code, "value": False}]
            return [{"code": speed.code, "value": speed_write(args["percentage"])}]
        if action == "set_preset_mode":
            return [{"code": mode.code, "value": ops.validate_enum_write(mode.spec, args["preset_mode"])}]
        if action == "oscillate":
            return [{"code": osc.code, "value": ops.validate_bool_write(args["oscillating"])}]
        if action == "set_direction":
            if args["direction"] not in _FAN_DIRECTION:
                return []
            return [{"code": direction.code, "value": ops.validate_enum_write(direction.spec, args["direction"])}]
        raise KeyError(action)

    roles = {k: v for k, v in (("switch", switch), ("speed", speed), ("mode", mode), ("oscillate", osc),
                               ("direction", direction)) if v}
    return EntityPlan("fan", desc["key"], ident, roles, tuple(dict.fromkeys(v.code for v in roles.values())),
                      read, write, update_all=True)


# --- humidifier ----------------------------------------------------------------
@builder("humidifier", "HUMIDIFIERS")
def humidifier(schema: DeviceSchema, env: HostEnv, desc: dict[str, Any]) -> EntityPlan | None:
    sw_codes = _codes(desc.get("dpcode") or desc["key"])
    cur_code, hum_code = desc.get("current_humidity"), desc.get("humidity")
    probe = {*sw_codes, cur_code, hum_code} - {None}
    if not any(c in schema.function or c in schema.status or c in schema.status_range for c in probe):
        return None
    F = "function_first"
    cur = resolve(schema, DpRef(_codes(cur_code), (INTEGER,))) if cur_code else None
    mode = resolve(schema, DpRef(("mode",), (ENUM,), F))
    switch = resolve(schema, DpRef(sw_codes, (BOOLEAN,), F))
    target = resolve(schema, DpRef(_codes(hum_code), (INTEGER,), F)) if hum_code else None
    ident = identity(desc)
    ident["min_humidity"] = round(ops.scale_value(target.spec, target.spec.min)) if target else 0
    ident["max_humidity"] = round(ops.scale_value(target.spec, target.spec.max)) if target else 100
    ident["supported_features"] = 1 if mode else 0
    if mode:
        ident["available_modes"] = list(mode.spec.range)

    def rounded(role, st):
        v = None if role is None else ops.validate_int_read(role.spec, st.get(role.code))
        return None if v is None else round(v)

    def read(st):
        return {"is_on": ops.validate_bool_read(st.get(switch.code)) if switch else None,
                "mode": ops.validate_enum_read(mode.spec, st.get(mode.code)) if mode else None,
                "target_humidity": rounded(target, st), "current_humidity": rounded(cur, st)}

    def write(action, args, st):
        if action in ("turn_on", "turn_off"):
            if switch is None:
                raise ActionDPCodeNotFound(desc.get("dpcode") or desc["key"])
            return [{"code": switch.code, "value": action == "turn_on"}]
        if action == "set_humidity":
            if target is None:
                raise ActionDPCodeNotFound(hum_code)
            return [{"code": target.code, "value": ops.validate_int_write(target.spec, args["humidity"])}]
        if action == "set_mode":
            return [{"code": mode.code, "value": ops.validate_enum_write(mode.spec, args["mode"])}]
        raise KeyError(action)

    roles = {k: v for k, v in (("switch", switch), ("current_humidity", cur), ("target_humidity", target), ("mode", mode)) if v}
    return EntityPlan("humidifier", desc["key"], ident, roles, tuple(dict.fromkeys(v.code for v in roles.values())),
                      read, write, update_all=True)


# --- alarm_control_panel ---------------------------------------------------------
_ALARM_STATE = {"disarmed": "disarmed", "arm": "armed_away", "home": "armed_home", "sos": "triggered"}
_ALARM_ACTION = {"arm_home": "home", "arm_away": "arm", "disarm": "disarmed", "trigger": "sos"}
_ALARM_FEATURE = {"arm_home": 1, "arm_away": 2, "trigger": 8}


def _utf16(raw: bytes | None) -> str | None:
    """P-14 (deviate): core raises on undecodable payloads; the engine yields UNKNOWN."""
    try:
        return None if raw is None else raw.decode("utf-16be")
    except UnicodeDecodeError:
        return None


@builder("alarm_control_panel", "ALARM")
def alarm(schema: DeviceSchema, env: HostEnv, desc: dict[str, Any]) -> EntityPlan | None:
    mm = resolve(schema, DpRef(("master_mode",), (ENUM,), "function_first"))
    if mm is None:
        return None
    msg = resolve(schema, DpRef(("alarm_msg",), (RAW,)))
    options = [a for a, raw in _ALARM_ACTION.items() if raw in mm.spec.range]
    ident = identity(desc)
    ident["supported_features"] = sum(_ALARM_FEATURE[a] for a in options if a in _ALARM_FEATURE)

    def read(st):
        state = None
        decided = False
        if st.get("master_state") == "alarm":
            enc = st.get("alarm_msg")
            decoded = _utf16(codecs.b64_decode(enc)) if enc else None
            if not (enc and decoded and "Sensor Low Battery" in decoded):
                state, decided = "triggered", True
        if not decided:
            v = ops.validate_enum_read(mm.spec, st.get(mm.code))
            state = None if v is None else _ALARM_STATE.get(v)
        changed_by = None
        if msg is not None and st.get("master_state") == "alarm":
            changed_by = _utf16(codecs.b64_decode(st.get(msg.code)))
        return {"alarm_state": state, "changed_by": changed_by}

    def write(action, args, st):
        if action in options:
            return [{"code": mm.code, "value": _ALARM_ACTION[action]}]
        raise ValueError(f"Unsupported value {action} for {mm.code}")

    roles = {"master_mode": mm, **({"alarm_msg": msg} if msg else {})}
    return EntityPlan("alarm_control_panel", desc["key"], ident, roles,
                      tuple(dict.fromkeys([mm.code, "master_state", "alarm_msg"])), read, write, update_all=True)


# --- vacuum ---------------------------------------------------------------------
_VAC = {"charge_done": "docked", "chargecompleted": "docked", "chargego": "docked", "charging": "docked",
        "cleaning": "cleaning", "docking": "returning", "goto_charge": "returning", "goto_pos": "cleaning",
        "mop_clean": "cleaning", "part_clean": "cleaning", "paused": "paused", "pick_zone_clean": "cleaning",
        "pos_arrived": "cleaning", "pos_unarrive": "cleaning", "random": "cleaning", "sleep": "idle",
        "smart_clean": "cleaning", "smart": "cleaning", "spot_clean": "cleaning", "standby": "idle",
        "wall_clean": "cleaning", "wall_follow": "cleaning", "zone_clean": "cleaning"}
VAC_FEATURES = {"PAUSE": 4, "STOP": 8, "RETURN_HOME": 16, "FAN_SPEED": 32, "SEND_COMMAND": 256, "LOCATE": 512,
                "STATE": 4096, "START": 8192}


@builder("vacuum", "VACUUMS")
def vacuum(schema: DeviceSchema, env: HostEnv, desc: dict[str, Any]) -> EntityPlan | None:
    F = "function_first"
    charge = resolve(schema, DpRef(("switch_charge",), (BOOLEAN,), F))
    locate = resolve(schema, DpRef(("seek",), (BOOLEAN,), F))
    mode = resolve(schema, DpRef(("mode",), (ENUM,), F))
    pause = resolve(schema, DpRef(("pause",), (BOOLEAN,)))
    power = resolve(schema, DpRef(("power_go",), (BOOLEAN,), F))
    status = resolve(schema, DpRef(("status",), (ENUM,)))
    suction = resolve(schema, DpRef(("suction",), (ENUM,), F))
    activity_found = bool(pause or status)

    opts = []
    if charge or (mode and "chargego" in mode.spec.range):
        opts.append("return_to_base")
    if locate:
        opts.append("locate")
    if pause:
        opts.append("pause")
    if power:
        opts += ["start", "stop"]
    feats = VAC_FEATURES["SEND_COMMAND"]
    for o, f in (("pause", "PAUSE"), ("return_to_base", "RETURN_HOME"), ("locate", "LOCATE"), ("start", "START"), ("stop", "STOP")):
        if o in opts:
            feats |= VAC_FEATURES[f]
    if activity_found:
        feats |= VAC_FEATURES["STATE"]
    ident = identity(desc)
    ident["fan_speed_list"] = list(suction.spec.range) if suction else []
    if suction:
        feats |= VAC_FEATURES["FAN_SPEED"]
    ident["supported_features"] = feats

    def read(st):
        act = None
        sv = ops.validate_enum_read(status.spec, st.get(status.code)) if status else None
        if sv is not None:                       # present-but-unmapped => UNKNOWN; pause NOT consulted
            act = _VAC.get(sv)
        elif pause and ops.validate_bool_read(st.get(pause.code)):
            act = "paused"
        return {"activity": act if activity_found else None,
                "fan_speed": ops.validate_enum_read(suction.spec, st.get(suction.code)) if suction else None}

    def write(action, args, st):
        if action == "locate" and locate:
            return [{"code": locate.code, "value": True}]
        if action == "pause" and pause:
            return [{"code": pause.code, "value": True}]
        if action == "return_to_base":
            if charge:
                return [{"code": charge.code, "value": True}]
            if mode:
                return [{"code": mode.code, "value": ops.validate_enum_write(mode.spec, "chargego")}]
        if action == "start" and power:
            return [{"code": power.code, "value": True}]
        if action == "stop" and power:
            return [{"code": power.code, "value": False}]
        if action == "set_fan_speed":
            return [{"code": suction.code, "value": ops.validate_enum_write(suction.spec, args["fan_speed"])}]
        if action == "send_command":
            params = args.get("params")
            if not params:
                raise ValueError("Params cannot be omitted for Tuya vacuum commands")
            if not isinstance(params, list):
                raise TypeError("Params must be a list for Tuya vacuum commands")
            return [{"code": args["command"], "value": params[0]}]
        return []

    roles = {k: v for k, v in (("switch_charge", charge), ("seek", locate), ("mode", mode), ("pause", pause),
                               ("power_go", power), ("status", status), ("suction", suction)) if v}
    return EntityPlan("vacuum", desc["key"], ident, roles, tuple(dict.fromkeys(v.code for v in roles.values())),
                      read, write, update_all=True)


# --- climate -----------------------------------------------------------------------
import collections  # noqa: E402
import json  # noqa: E402

_C_ALIASES = {"°c", "c", "celsius", "℃"}
_F_ALIASES = {"°f", "f", "fahrenheit", "℉"}
_MODE_TO_HVAC = {"auto": "heat_cool", "cold": "cool", "freeze": "cool", "heat": "heat", "hot": "heat",
                 "manual": "heat_cool", "off": "off", "wet": "dry", "wind": "fan_only"}
CLIMATE_FEATURES = {"TARGET_TEMPERATURE": 1, "TARGET_HUMIDITY": 4, "FAN_MODE": 8, "PRESET_MODE": 16,
                    "SWING_MODE": 32, "TURN_OFF": 128, "TURN_ON": 256}


def _filter_hvac(tuya_range):
    """Modes sharing an HA mode ALL become unmapped (None) — spec `enum_map_filtered`."""
    m = {t: _MODE_TO_HVAC.get(t) for t in tuya_range}
    occ = collections.Counter(m.values())
    return {k: (None if v is not None and occ[v] > 1 else v) for k, v in m.items()}


def _temp_convert(v, frm, to):
    """HA TemperatureConverter: C<->F."""
    if frm == to:
        return v
    return v * 1.8 + 32 if (frm, to) == ("°C", "°F") else (v - 32) / 1.8


@builder("climate", "CLIMATE_DESCRIPTIONS")
def climate(schema: DeviceSchema, env: HostEnv, desc: dict[str, Any]) -> EntityPlan | None:
    F = "function_first"
    sys_unit = "°F" if env.temperature_unit == "F" else "°C"
    cur_c = resolve(schema, DpRef(("temp_current", "upper_temp"), (INTEGER,)))
    cur_f = resolve(schema, DpRef(("temp_current_f", "upper_temp_f"), (INTEGER,)))
    set_c = resolve(schema, DpRef(("temp_set",), (INTEGER,), F))
    set_f = resolve(schema, DpRef(("temp_set_f",), (INTEGER,), F))
    tuc = resolve(schema, DpRef(("temp_unit_convert",), (ENUM,)))

    # native unit per role (mutable copy; spec 6.4 step 1: empty unit <- temp_unit_convert raw, read ONCE)
    units = {id(r): r.spec.unit for r in (cur_c, cur_f, set_c, set_f) if r}
    if tuc is not None:
        tv = ops.validate_enum_read(tuc.spec, schema.status.get(tuc.code))
        for r in (cur_c, cur_f, set_c, set_f):
            if r is not None and not units[id(r)]:
                units[id(r)] = tv

    def pick(roles, aliases):
        return next((r for r in roles if r is not None and (u := units[id(r)]) and u.lower() in aliases), None)

    cc, cf = pick([cur_c, cur_f], _C_ALIASES), pick([cur_f, cur_c], _F_ALIASES)
    sc, sf = pick([set_c, set_f], _C_ALIASES), pick([set_f, set_c], _F_ALIASES)
    if sys_unit == "°F" and ((cf and sf) or (cf and not sc) or (sf and not cc)):
        cur, setp, tunit = cf, sf, "°F"
    elif (cc and sc) or (cc and not sf) or (sc and not cf):
        cur, setp, tunit = cc, sc, "°C"
    elif sys_unit == "°F":
        cur, setp, tunit = cur_f or cur_c, set_f or set_c, "°F"
    else:
        cur, setp, tunit = cur_c or cur_f, set_c or set_f, "°C"

    def native_temp_unit(r):
        """util.get_temperature_unit: unit alias, else live temp_unit_convert status (once, at classify)."""
        if r is None:
            return None
        u = units[id(r)]
        if not u:
            tv = schema.status.get("temp_unit_convert")
            return {"c": "°C", "f": "°F"}.get(tv) if tv else None
        u = u.lower()
        return "°C" if u in _C_ALIASES else "°F" if u in _F_ALIASES else None

    cur_unit, set_unit = native_temp_unit(cur), native_temp_unit(setp)
    hum_cur = resolve(schema, DpRef(("humidity_current",), (INTEGER,)))
    fan_mode = resolve(schema, DpRef(("fan_speed_enum", "level", "windspeed"), (ENUM,), F))
    mode = resolve(schema, DpRef(("mode",), (ENUM,), F))
    switch = resolve(schema, DpRef(("switch", "power_switch"), (BOOLEAN,), F))
    hum_set = resolve(schema, DpRef(("humidity_set",), (INTEGER,), F))
    sw_on = resolve(schema, DpRef(("swing", "shake"), (BOOLEAN,), F))
    sw_h = resolve(schema, DpRef(("switch_horizontal",), (BOOLEAN,), F))
    sw_v = resolve(schema, DpRef(("switch_vertical",), (BOOLEAN,), F))

    switch_only = desc["switch_only_hvac_mode"]
    filt = _filter_hvac(mode.spec.range) if mode else {}
    hvac_opts = [v for v in filt.values() if v is not None]
    preset_opts = [t for t, v in filt.items() if v is None]

    ident = identity(desc)
    feats = 0
    ident["temperature_unit"] = tunit
    ident["target_temperature_step"] = 1.0
    if setp:
        feats |= CLIMATE_FEATURES["TARGET_TEMPERATURE"]
        s = setp.spec
        ident.update(max_temp=ops.scale_value(s, s.max), min_temp=ops.scale_value(s, s.min),
                     target_temperature_step=ops.scale_value(s, s.step))
    hvac_modes: list[str] = []
    if mode:
        hvac_modes = ["off"] + [m for m in hvac_opts if m != "off"]
    elif switch:
        hvac_modes = ["off", switch_only]
    if mode and preset_opts:
        ident["preset_modes"] = list(preset_opts)
        feats |= CLIMATE_FEATURES["PRESET_MODE"]
        if switch_only not in hvac_modes:
            hvac_modes.append(switch_only)
    ident["hvac_modes"] = hvac_modes
    if hum_set:
        feats |= CLIMATE_FEATURES["TARGET_HUMIDITY"]
        ident["min_humidity"] = round(ops.scale_value(hum_set.spec, hum_set.spec.min))
        ident["max_humidity"] = round(ops.scale_value(hum_set.spec, hum_set.spec.max))
    if fan_mode:
        feats |= CLIMATE_FEATURES["FAN_MODE"]
        ident["fan_modes"] = list(fan_mode.spec.range)
    if sw_on or sw_h or sw_v:
        feats |= CLIMATE_FEATURES["SWING_MODE"]
        ident["swing_modes"] = ["off"] + (["on"] if sw_on else []) + (["horizontal"] if sw_h else []) + (["vertical"] if sw_v else [])
    if switch:
        feats |= CLIMATE_FEATURES["TURN_OFF"] | CLIMATE_FEATURES["TURN_ON"]
    ident["supported_features"] = feats

    def temp(role, unit, st):
        v = None if role is None else ops.validate_int_read(role.spec, st.get(role.code))
        if v is not None and unit and unit != tunit:
            v = _temp_convert(v, unit, tunit)
        return v

    def rounded(role, st):
        v = None if role is None else ops.validate_int_read(role.spec, st.get(role.code))
        return None if v is None else round(v)

    def read(st):
        sw = ops.validate_bool_read(st.get(switch.code)) if switch else None
        if sw is False:
            hvac = "off"
        elif mode is None:
            hvac = switch_only if sw is True else None
        else:
            raw = ops.validate_enum_read(mode.spec, st.get(mode.code))
            hvac = _MODE_TO_HVAC.get(raw) if raw else None      # DefaultHVACModeWrapper: unfiltered map (P-02)
        pr = ops.validate_enum_read(mode.spec, st.get(mode.code)) if mode else None
        if pr not in preset_opts:
            pr = None
        swing = None
        if sw_on or sw_h or sw_v:
            def b(r): return ops.validate_bool_read(st.get(r.code)) if r else None
            if sw_on and b(sw_on):
                swing = "on"
            else:
                h, v = b(sw_h), b(sw_v)
                swing = "both" if (h and v) else "horizontal" if h else "vertical" if v else "off"
        return {"hvac_mode": hvac, "current_temperature": temp(cur, cur_unit, st), "temperature": temp(setp, set_unit, st),
                "current_humidity": rounded(hum_cur, st), "target_humidity": rounded(hum_set, st),
                "fan_mode": ops.validate_enum_read(fan_mode.spec, st.get(fan_mode.code)) if fan_mode else None,
                "preset_mode": pr, "swing_mode": swing}

    def write(action, args, st):
        if action == "set_hvac_mode":
            hv, cmds = args["hvac_mode"], []
            if switch:
                cmds.append({"code": switch.code, "value": hv != "off"})
            if mode and hv in hvac_opts:      # HA mode -> abstract Tuya mode (same value); must be an offered option
                cmds.append({"code": mode.code, "value": next(t for t, m in filt.items() if m == hv)})
            return cmds
        if action == "set_preset_mode":
            return [{"code": mode.code, "value": ops.validate_enum_write(mode.spec, args["preset_mode"])}]
        if action == "set_fan_mode":
            return [{"code": fan_mode.code, "value": ops.validate_enum_write(fan_mode.spec, args["fan_mode"])}]
        if action == "set_humidity":
            return [{"code": hum_set.code, "value": ops.validate_int_write(hum_set.spec, args["humidity"])}]
        if action == "set_temperature":
            v = args["temperature"]
            if set_unit and set_unit != tunit:
                v = _temp_convert(v, tunit, set_unit)
            return [{"code": setp.code, "value": ops.validate_int_write(setp.spec, v)}]
        if action == "set_swing_mode":
            m = args["swing_mode"]
            cmds = []
            if sw_on:
                cmds.append({"code": sw_on.code, "value": m == "on"})
            if sw_v:
                cmds.append({"code": sw_v.code, "value": m in ("both", "vertical")})
            if sw_h:
                cmds.append({"code": sw_h.code, "value": m in ("both", "horizontal")})
            return cmds
        if action in ("turn_on", "turn_off"):
            return [{"code": switch.code, "value": action == "turn_on"}]
        raise KeyError(action)

    roles = {k: v for k, v in (("cur_temp_c", cur_c), ("cur_temp_f", cur_f), ("set_temp_c", set_c), ("set_temp_f", set_f),
                               ("unit_convert", tuc), ("cur_hum", hum_cur), ("fan", fan_mode), ("hvac_mode", mode),
                               ("switch", switch), ("set_hum", hum_set), ("swing_on_off", sw_on), ("swing_h", sw_h),
                               ("swing_v", sw_v)) if v}
    return EntityPlan("climate", desc["key"], ident, roles, tuple(dict.fromkeys(v.code for v in roles.values())),
                      read, write, update_all=True)


# --- light -------------------------------------------------------------------------
import json  # noqa: E402

_COLOR_MODES_COLOR = {"hs", "rgb", "rgbw", "rgbww", "xy"}
MIN_KELVIN, MAX_KELVIN = 2000, 6500
# (source_min, source_max, target_min, target_max) for H, S, V
_HSV_V1 = ((1, 360, 0, 360), (1, 255, 0, 100), (1, 255, 0, 255))
_HSV_V2 = ((1, 360, 0, 360), (1, 1000, 0, 100), (1, 1000, 0, 255))


def _filter_color_modes(modes: set[str]) -> set[str]:
    """HA light.filter_supported_color_modes."""
    modes = set(modes)
    if "onoff" in modes and len(modes) > 1:
        modes.remove("onoff")
    if "brightness" in modes and len(modes) > 1:
        modes.remove("brightness")
    return modes


@builder("light", "LIGHTS")
def light(schema: DeviceSchema, env: HostEnv, desc: dict[str, Any]) -> EntityPlan | None:
    F = "function_first"
    switch = resolve(schema, DpRef((desc["key"],), (BOOLEAN,), F))
    if switch is None:
        return None
    bri = resolve(schema, DpRef(_codes(desc.get("brightness")), (INTEGER,), F)) if desc.get("brightness") else None
    bmax = bmin = None
    if bri is not None:
        bmax = resolve(schema, DpRef(_codes(desc.get("brightness_max")), (INTEGER,), F)) if desc.get("brightness_max") else None
        bmin = resolve(schema, DpRef(_codes(desc.get("brightness_min")), (INTEGER,), F)) if desc.get("brightness_min") else None

    def sc(spec, v):
        return ops.scale_value(spec, v)

    def to255(role, st):
        """A limit dp's scaled value remapped onto 0..255, or None when unreadable."""
        v = ops.validate_int_read(role.spec, st.get(role.code))
        return None if v is None else ops.remap(v, sc(role.spec, role.spec.min), sc(role.spec, role.spec.max), 0, 255)

    def limits(st):
        if bri is None or bmax is None or bmin is None:
            return None
        hi, lo = to255(bmax, st), to255(bmin, st)
        return None if hi is None or lo is None else (lo, hi)

    # colour data: JSON first, then hex String
    v2_mode = str(desc.get("fallback_color_data_mode", "")).endswith("V2")
    cd = resolve(schema, DpRef(_codes(desc.get("color_data")), (JSON,), F)) if desc.get("color_data") else None
    cd_kind = "json" if cd else None
    hsv = None
    if cd is not None:
        d = cd.spec.raw
        if d:
            hd, sd, vd = d.get("h", {"min": 0, "max": 360}), d.get("s", {"min": 0, "max": 255}), d.get("v", {"min": 0, "max": 255})
            hsv = ((hd["min"], hd["max"], 0, 360), (sd["min"], sd["max"], 0, 100), (vd["min"], vd["max"], 0, 255))
    else:
        cd = resolve(schema, DpRef(_codes(desc.get("color_data")), (STRING,), F)) if desc.get("color_data") else None
        cd_kind = "hex" if cd else None
    if cd is not None and hsv is None:
        big = bri is not None and sc(bri.spec, bri.spec.max) > 255
        hsv = _HSV_V2 if (v2_mode or cd.code == "colour_data_v2" or big) else _HSV_V1

    cm = resolve(schema, DpRef((desc["color_mode"],), (ENUM,), F)) if desc.get("color_mode") else None
    ct = resolve(schema, DpRef(_codes(desc.get("color_temp")), (INTEGER,), F)) if desc.get("color_temp") else None

    modes = {"onoff"}
    if bri:
        modes.add("brightness")
    if cd:
        modes.add("hs")
    white_mode = "color_temp"
    if ct:
        modes.add("color_temp")
    elif (modes & _COLOR_MODES_COLOR) and cm is not None and "white" in cm.spec.range:
        modes.add("white")
        white_mode = "white"
    supported = _filter_color_modes(modes)
    fixed = next(iter(supported)) if len(supported) == 1 else None

    ident = identity(desc)
    ident["supported_color_modes"] = sorted(supported)
    ident["min_color_temp_kelvin"] = MIN_KELVIN
    ident["max_color_temp_kelvin"] = MAX_KELVIN

    def bri_read(st):
        if bri is None:
            return None
        v = ops.validate_int_read(bri.spec, st.get(bri.code))
        if v is None:
            return None
        b = ops.remap(v, sc(bri.spec, bri.spec.min), sc(bri.spec, bri.spec.max), 0, 255)
        lim = limits(st)
        if lim is not None:
            b = ops.remap(b, lim[0], lim[1], 0, 255)
        return round(b)

    def hsv_read(st):
        if cd is None:
            return None
        raw = st.get(cd.code)
        if cd_kind == "json":
            p = codecs.json_loads(raw)
            if not isinstance(p, dict) or not all(k in p for k in "hsv"):     # P-22 (deviate): core KeyError -> UNKNOWN
                return None
            trip = (p["h"], p["s"], p["v"])
        else:
            trip = codecs.hsv_hex_decode(raw)
            if trip is None:
                return None
        return tuple(ops.remap(x, *r) for x, r in zip(trip, hsv))

    def ct_read(st):
        if ct is None:
            return None
        v = ops.validate_int_read(ct.spec, st.get(ct.code))
        if v is None:
            return None
        m = ops.remap(v, sc(ct.spec, ct.spec.min), sc(ct.spec, ct.spec.max), 1e6 / MAX_KELVIN, 1e6 / MIN_KELVIN, reverse=True)
        return round(1e6 / m)

    def color_mode(st):
        if fixed:
            return fixed
        if cm is not None and ops.validate_enum_read(cm.spec, st.get(cm.code)) != "white":
            return "hs"
        return white_mode

    def read(st):
        mode = color_mode(st)
        h = hsv_read(st)
        b = (None if h is None else round(h[2])) if (mode == "hs" and cd) else bri_read(st)
        return {"is_on": ops.validate_bool_read(st.get(switch.code)), "color_mode": mode, "brightness": b,
                "color_temp_kelvin": ct_read(st), "hs_color": None if h is None else (h[0], h[1])}

    def bri_write(value, st):
        lim = limits(st)
        if lim is not None:
            value = ops.remap(value, 0, 255, lim[0], lim[1])
        return ops.validate_int_write(bri.spec, ops.remap(value, 0, 255, sc(bri.spec, bri.spec.min), sc(bri.spec, bri.spec.max)))

    def hsv_write(hh, ss, vv):
        raw = [round(ops.remap(x, t0, t1, s0, s1)) for x, (s0, s1, t0, t1) in zip((hh, ss, vv), hsv)]
        if cd_kind == "json":
            return json.dumps({"h": raw[0], "s": raw[1], "v": raw[2]})
        return f"{raw[0]:04x}{raw[1]:04x}{raw[2]:04x}"

    def write(action, args, st):
        if action == "turn_off":
            return [{"code": switch.code, "value": False}]
        cmds = [{"code": switch.code, "value": True}]
        if cm is not None and ("white" in args or "color_temp_kelvin" in args):
            cmds.append({"code": cm.code, "value": ops.validate_enum_write(cm.spec, "white")})
        if ct is not None and "color_temp_kelvin" in args:
            k = args["color_temp_kelvin"]
            cmds.append({"code": ct.code, "value": ops.validate_int_write(
                ct.spec, ops.remap(1e6 / k, 1e6 / MAX_KELVIN, 1e6 / MIN_KELVIN, sc(ct.spec, ct.spec.min), sc(ct.spec, ct.spec.max), True))})
        cur = read(st)
        if cd is not None and ("hs_color" in args or ("brightness" in args and cur["color_mode"] == "hs"
                                                       and "white" not in args and "color_temp_kelvin" not in args)):
            if cm is not None:
                cmds.append({"code": cm.code, "value": ops.validate_enum_write(cm.spec, "colour")})
            b = args.get("brightness") or cur["brightness"] or 0
            c = args.get("hs_color") or cur["hs_color"] or (0, 0)
            cmds.append({"code": cd.code, "value": hsv_write(c[0], c[1], b)})
        elif bri is not None and ("brightness" in args or "white" in args):
            b = args["brightness"] if "brightness" in args else args["white"]
            cmds.append({"code": bri.code, "value": bri_write(b, st)})
        return cmds

    roles = {k: v for k, v in (("switch", switch), ("brightness", bri), ("brightness_max", bmax), ("brightness_min", bmin),
                               ("color_data", cd), ("work_mode", cm), ("color_temp", ct)) if v}
    return EntityPlan("light", desc["key"], ident, roles, tuple(dict.fromkeys(v.code for v in roles.values())),
                      read, write, update_all=True)
