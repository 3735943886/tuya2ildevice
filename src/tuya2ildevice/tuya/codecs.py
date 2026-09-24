"""Payload codecs (named ops `parsed_attr`; spec 5): electricity Raw/Hex, wind direction table."""
from __future__ import annotations

import base64
import binascii
import json
import struct
from dataclasses import dataclass
from typing import Any


@dataclass
class ElectricityData:
    current: float
    power: float
    voltage: float
    # None = the payload layout does not carry the attribute (legacy 8-byte layout)
    reactive_power: float | None = None
    apparent_power: float | None = None
    power_factor: float | None = None


def electricity_from_bytes(raw: bytes) -> ElectricityData | None:
    is_v1 = len(raw) == 17 and raw[0:2] == b"\x01\x0f"
    is_v2 = len(raw) == 18 and raw[0:2] == b"\x02\x0f"
    if is_v1 or is_v2:
        d = raw[2:17]
        voltage = struct.unpack(">H", d[0:2])[0] / 10.0
        current = struct.unpack(">L", b"\x00" + d[2:5])[0]
        power = struct.unpack(">L", b"\x00" + d[5:8])[0]
        reactive = struct.unpack(">L", b"\x00" + d[8:11])[0]
        apparent = struct.unpack(">L", b"\x00" + d[11:14])[0]
        pf = d[14] / 100.0
        if is_v2:
            sign = raw[17]
            if sign & 0x01:
                current = -current
            if sign & 0x02:
                power = -power
            if sign & 0x04:
                reactive = -reactive
            if sign & 0x08:
                pf = -pf
        return ElectricityData(current, power, voltage, reactive, apparent, pf)
    if len(raw) >= 8:
        return ElectricityData(
            struct.unpack(">L", b"\x00" + raw[2:5])[0],
            struct.unpack(">L", b"\x00" + raw[5:8])[0],
            struct.unpack(">H", raw[0:2])[0] / 10.0,
        )
    return None


def electricity_from_hex(raw: str) -> ElectricityData | None:
    try:
        return electricity_from_bytes(bytes.fromhex(raw))
    except ValueError:
        return None


def b64_decode(raw: Any) -> bytes | None:
    """Non-strict b64decode; failure -> UNKNOWN (P-14)."""
    if raw is None:
        return None
    try:
        return base64.b64decode(raw)
    except (binascii.Error, TypeError, ValueError):
        return None


def json_loads(raw: Any) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


WIND_DIRECTIONS = {
    "north": 0.0, "north_north_east": 22.5, "north_east": 45.0, "east_north_east": 67.5,
    "east": 90.0, "east_south_east": 112.5, "south_east": 135.0, "south_south_east": 157.5,
    "south": 180.0, "south_south_west": 202.5, "south_west": 225.0, "west_south_west": 247.5,
    "west": 270.0, "west_north_west": 292.5, "north_west": 315.0, "north_north_west": 337.5,
}

# wrapper-name suffix -> (electricity attribute in ElectricityData, native_unit, suggested_unit)  [Raw/Hex]
ELEC_RAW = {
    "Current": ("current", "mA", "A"), "Power": ("power", "W", "kW"), "Voltage": ("voltage", "V", None),
    "ReactivePower": ("reactive_power", "var", "kvar"), "ApparentPower": ("apparent_power", "VA", "kVA"),
    "PowerFactor": ("power_factor", None, None),
}
# ... and the Json variants: (json key, native_unit)
ELEC_JSON = {
    "Current": ("electricCurrent", "A"), "Power": ("power", "kW"), "Voltage": ("voltage", "V"),
    "ReactivePower": ("reactivePower", "kvar"), "ApparentPower": ("apparentPower", "kVA"),
    "PowerFactor": ("powerFactor", None),
}


def hsv_hex_decode(raw: Any) -> tuple[int, int, int] | None:
    """`HHHHSSSSVVVV` (four hex digits each); anything else -> UNKNOWN."""
    if not isinstance(raw, str) or len(raw) != 12:
        return None
    try:
        return int(raw[0:4], 16), int(raw[4:8], 16), int(raw[8:12], 16)
    except ValueError:
        return None
