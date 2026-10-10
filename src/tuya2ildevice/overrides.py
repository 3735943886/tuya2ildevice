"""Override-data utilities and validation bindings; Tuya patches execute in Rust."""
from .native import call, rules

COVER_DEFAULTS = rules()['cover_defaults']
DELTA_DEFAULTS = {'accept_passive': False}


class OverrideError(ValueError):
    pass


def deep_merge(dst, src):
    result = merge_all([dst, src])
    dst.clear()
    dst.update(result)
    return dst


def merge_all(mappings):
    return call('merge', mappings=list(mappings))


def validate(block, where='override', converter_types=None):
    try:
        return call('override_validate', block=block, converter_types=list(converter_types or ()))
    except ValueError as error:
        raise OverrideError(f'{where}: {error}') from error


def find(mapping, device, converter_types=None):
    return merge_all([validate(mapping[k], f'override[{k}]', converter_types)
                      for k in (device.get('product_id'), device.get('id')) if k and k in (mapping or {})])
