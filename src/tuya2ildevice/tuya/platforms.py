"""Compatibility platform builders. All classification/read/write runs in Rust."""
from dataclasses import asdict

from ..native import call, rules
from .runtime import builder, from_native

COVER_FEATURES = rules()['features']['cover']
VAC_FEATURES = rules()['features']['vacuum']
FAN_FEATURES = rules()['features']['fan']
CLIMATE_FEATURES = rules()['features']['climate']


def _factory(platform):
    def build(schema, env, desc):
        result = call('build', platform=platform, device=asdict(schema), env=asdict(env), description=desc)
        return from_native(result) if result is not None else None
    return build


for _group in rules()['platforms']:
    _name = _group['platform']
    globals()[_name] = builder(_name, _group['table'])(_factory(_name))
