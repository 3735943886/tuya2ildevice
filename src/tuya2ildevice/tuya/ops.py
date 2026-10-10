"""Compatibility signatures for the Rust value-operation registry."""
from dataclasses import asdict

from ..native import call
from .runtime import WriteRejected


def _op(name, value=None, spec=None, **kwargs):
    try:
        return call('value_op', name=name, value=value, spec=asdict(spec) if spec is not None else {}, **kwargs)
    except ValueError as error:
        raise WriteRejected(str(error)) from error


def validate_bool_read(raw): return _op('validate_bool_read', raw)
def validate_bool_write(value): return _op('validate_bool_write', value)
def validate_enum_read(spec, raw): return _op('validate_enum_read', raw, spec)
def validate_enum_write(spec, value): return _op('validate_enum_write', value, spec)
def scale_value(spec, value): return _op('scale_value', value, spec)
def scaled_range(spec): return tuple(_op('scaled_range', spec=spec))
def validate_int_read(spec, raw): return _op('validate_int_read', raw, spec)
def validate_int_write(spec, value): return _op('validate_int_write', value, spec)
def remap(value, from_min, from_max, to_min, to_max, reverse=False):
    return _op('remap', value, range=[from_min, from_max, to_min, to_max], reverse=reverse)
def remap_read(spec, raw, target_min, target_max, reverse=False):
    return _op('remap_read', raw, spec, target=[target_min, target_max], reverse=reverse)
def remap_write(spec, value, target_min, target_max, reverse=False):
    return _op('remap_write', value, spec, target=[target_min, target_max], reverse=reverse)
