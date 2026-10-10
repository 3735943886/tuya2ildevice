"""Compatibility payload DTOs; codecs and lookup data live in the Rust rule engine."""
from dataclasses import dataclass

from ..native import call, rules


@dataclass
class ElectricityData:
    current: float
    power: float
    voltage: float
    reactive_power: float | None = None
    apparent_power: float | None = None
    power_factor: float | None = None


def electricity_from_bytes(raw):
    value = call('codec', name='electricity_bytes', value=list(raw))
    return ElectricityData(**value) if value else None


def electricity_from_hex(raw):
    value = call('codec', name='electricity_hex', value=raw)
    return ElectricityData(**value) if value else None


def b64_decode(raw):
    value = call('codec', name='b64_decode', value=raw)
    return bytes(value) if value is not None else None


def json_loads(raw):
    return call('codec', name='json_loads', value=raw)


def hsv_hex_decode(raw):
    value = call('codec', name='hsv_hex_decode', value=raw)
    return tuple(value) if value is not None else None


WIND_DIRECTIONS = rules()['wind']
ELEC_RAW = {k: tuple(v) for k, v in rules()['electricity']['raw'].items()}
ELEC_JSON = {k: tuple(v) for k, v in rules()['electricity']['json'].items()}
