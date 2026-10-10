"""Quirk data and thin Rust schema-patch bindings."""
import functools
from dataclasses import asdict

from ..native import call, rules
from .model import DeviceSchema, DpSpec


@functools.cache
def load_quirks():
    return rules()['quirks']


def quirk_for(product_id):
    return load_quirks().get(product_id)


def apply_quirk(schema, quirk=None):
    data = call('quirk_schema', device=asdict(schema), quirk=quirk)
    for table in ('function', 'status_range'):
        data[table] = {k: DpSpec(k, v['type'], v.get('values'), v.get('report_type')) for k, v in data[table].items()}
    data['dpmap'] = {int(k) if k.isdigit() else k: v for k, v in data.get('dpmap', {}).items()}
    return DeviceSchema(**{k: v for k, v in data.items() if k in DeviceSchema.__dataclass_fields__})


def apply_status_quirk(quirk, status):
    return call('status_quirk', quirk=quirk or {}, status=status)


def device_info(schema, quirk=None):
    return call('device_info', device=asdict(schema), quirk=quirk)
