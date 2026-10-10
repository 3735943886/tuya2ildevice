"""Compatibility DTOs and thin calls to Rust's bridge value adapter."""
from dataclasses import asdict, dataclass, field

from ..native import call, rules


class NoWritePath(Exception):
    pass


def strategy_code(meta):
    return call('strategy_code', meta=meta) or None


def _strategy(name, direction):
    def convert(value, config):
        try:
            return call('strategy_' + direction, name=name, value=value, config=config)
        except ValueError as error:
            if direction == 'write' and str(error).startswith('unsupported:'):
                raise NoWritePath(str(error)) from error
            raise
    return convert


STRATEGIES = {name: (_strategy(name, 'read'), _strategy(name, 'write')) for name in rules()['strategies']}
_r_color, _w_color = STRATEGIES['dj_v2_color_alg']
_r_contr, _w_contr = STRATEGIES['dj_v2_contr_alg']
_r_scene, _w_scene = STRATEGIES['dj_v2_scene_alg']


@dataclass
class Remap:
    alias: dict = field(default_factory=dict)
    invert: bool = False
    bounds: tuple | None = None

    def read(self, value):
        return call('remap', remap=asdict(self), direction='read', value=value)

    def write(self, value):
        return call('remap', remap=asdict(self), direction='write', value=value)


@dataclass
class Adapter:
    entries: dict = field(default_factory=dict)
    enum_ranges: dict = field(default_factory=dict)
    unsupported: dict = field(default_factory=dict)
    remaps: dict = field(default_factory=dict)

    @classmethod
    def from_local_strategy(cls, local_strategy, status_range=None):
        device = {'local_strategy': local_strategy or {}, 'status_range':
                  {k: asdict(v) for k, v in (status_range or {}).items()}}
        raw = call('adapter_create', device=device, dpmap={})
        return cls({k: tuple(v) for k, v in raw['entries'].items()}, raw['enum_ranges'], raw['unsupported'])

    def _data(self):
        return asdict(self)

    def codes(self):
        return {dp: entry[0] for dp, entry in self.entries.items()}

    def code_for_dp(self, dpid):
        entry = self.entries.get(str(dpid))
        return entry[0] if entry else None

    def dp_for_code(self, code):
        return next((dp for dp, entry in self.entries.items() if entry[0] == code), None)

    def read(self, dps):
        return call('adapter_read', adapter=self._data(), dps={str(k): v for k, v in dps.items()})

    def write(self, commands):
        try:
            dps, missing = call('adapter_write', adapter=self._data(), commands=commands)
        except ValueError as error:
            if str(error).startswith('unsupported:'):
                raise NoWritePath(str(error)) from error
            raise
        return dps, missing
