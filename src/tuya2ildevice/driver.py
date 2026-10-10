"""Thin Python facade over the Rust sans-IO driver and explicit plugin callbacks."""
from __future__ import annotations

import logging
from dataclasses import asdict
from types import SimpleNamespace

from . import io
from .converters import Converter, as_result
from .native import _call, register_rules
from .native import rules as default_rules
from .overrides import OverrideError, merge_all
from .tuya.model import DeviceSchema, DpSpec
from .tuya.runtime import HostEnv


def schema_of(device):
    """Compatibility DTO constructor; native conversion takes the raw device JSON."""
    def specs(table, report):
        return {k: DpSpec(k, v['type'], v.get('values'), v.get('report_type') if report else None)
                for k, v in (table or {}).items()}
    return DeviceSchema(device['id'], device.get('category', ''), device.get('product_id', ''),
                        device.get('name', ''), device.get('product_name', ''), bool(device.get('online', True)),
                        specs(device.get('function'), False), specs(device.get('status_range'), True),
                        dict(device.get('status') or {}))


def default_env():
    from .native import rules
    return HostEnv(allowed_units=rules()['host_units'])


def descriptor_of(device, **kwargs):
    return TuyaDriver(device, **kwargs).descriptor


_TYPES = {name.lower().replace('_', ''): getattr(io, name) for name in
          ('Descriptor', 'Value', 'Absent', 'Event', 'SendMessage', 'SetTimer', 'CancelTimer', 'Reject', 'SettingsChanged')}
_INPUT_TYPES = {'Connected': 'connected', 'Disconnected': 'disconnected', 'Message': 'message', 'Command': 'command', 'Timer': 'timer'}


def _input(inp):
    kind = _INPUT_TYPES.get(type(inp).__name__)
    return {'type': kind, **asdict(inp)} if kind else {'type': 'unknown'}


def _namespace(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{k: _namespace(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_namespace(v) for v in value]
    return value


class TuyaDriver:
    def __init__(self, device, *, env=None, use_quirks=True, dpmap=None, allow_hazardous=False,
                 expose_unused=False, overrides=None, converters=None, converter_types=None, device_settings=False, rules=None):
        pack = rules if rules is not None else default_rules()
        self._rules_id = register_rules(pack)
        overrides = merge_all([pack.get("overrides", {}), overrides or {}])
        self.converters = []
        self._factories = []
        external = []
        block = merge_all([(overrides or {}).get(k) or {} for k in (device.get('product_id'), device.get('id'))])
        registrations = []
        for key in (device.get('product_id'), device.get('id')):
            fs = (converters or {}).get(key)
            registrations.extend((f, device, None) for f in fs if f) if isinstance(fs, (list, tuple)) else registrations.extend([(fs, device, None)] if fs else [])
        for name, cfg in (block.get('converters') or {}).items():
            if name not in ('cover_motion', 'declarative'):
                factory = (converter_types or {}).get(name)
                if factory is None:
                    raise OverrideError(f'unknown converter {name}')
                registrations.append((factory, cfg, name))
        for factory, config, name in registrations:
            self._factories.append(factory)
            try:
                conv = factory(config)
            except ValueError as error:
                raise OverrideError(str(error)) from error
            props = conv.props()
            if any((d.get('rw') or d.get('type') == 'trigger') for d in props.values()) and type(conv).write is Converter.write:
                raise OverrideError('writable converter has no write()')
            self.converters.append(conv)
            external.append({'props': props, 'name': name})
        try:
            result = self._call('create', device=device, options={'env': asdict(env) if env is not None else asdict(HostEnv(allowed_units=pack["host_units"])),
                          'use_quirks': use_quirks, 'dpmap': {str(k): v for k, v in (dpmap or {}).items()},
                          'allow_hazardous': allow_hazardous, 'expose_unused': expose_unused, 'overrides': overrides or {},
                          'device_settings': device_settings, 'external': external})
        except ValueError as error:
            if str(error).startswith('override:'):
                raise OverrideError(str(error)) from error
            raise
        for warning in result.get('warnings', []):
            logging.getLogger(__name__).warning('device %s: %s', device['id'], warning)
        self._state = result['driver']
        self.descriptor = self._state['assembly']['descriptor']

    def _call(self, op, **payload):
        return _call({'op': op, 'rules_id': self._rules_id, **payload})

    def _outputs(self, outputs):
        out = []
        for output in outputs:
            fields = dict(output)
            kind = fields.pop('type').replace('_', '')
            if kind == 'descriptor':
                fields['desc'] = self.descriptor
            out.append(_TYPES[kind](**fields))
        return out

    def _run(self, inp, now=0):
        result = self._call('handle', driver=self._state, input=inp, now=now)
        self._state = result['driver']
        out = self._outputs(result['outputs'])
        added = 0
        for cb in result.get('callbacks', []):
            conv = self.converters[cb['index']]
            method = cb['method']
            if method == 'reset':
                conv.reset()
                continue
            if method == 'write':
                commands = conv.write(cb['prop'], cb['value'], cb['codes'])
                produced = self._outputs(self._call('write_callback', driver=self._state, prop=cb['prop'], commands=commands)['outputs'])
                at = cb.get('at', len(out)) + added
                out[at:at] = produced
                added += len(produced)
                continue
            value = conv.update(cb['now'], cb['codes'], cb['changed'], cb['active']) if method == 'update' else conv.timer(cb['now'], cb['name'], cb['codes'])
            converted = self._call('converted', driver=self._state, index=cb['index'], result=asdict(as_result(value)))
            self._state = converted['driver']
            produced = self._outputs(converted['outputs'])
            at = cb.get('at', len(out)) + added
            out[at:at] = produced
            added += len(produced)
        return out

    def handle(self, now, inp):
        return self._run(_input(inp), now)

    def describe(self, seed=(), now=0):
        return self._run({'type': 'describe', 'seed': [_input(inp) for inp in seed]}, now)

    def carry(self, old):
        self._state = self._call('carry', driver=self._state, old=old._state)['driver']

    def settings_block(self):
        return self._call('settings', driver=self._state)

    @property
    def linked(self): return self._state['linked']
    @property
    def synced(self): return self._state['synced']
    @property
    def timers(self): return set(self._state['timers'])
    @property
    def dps(self): return dict(self._state['dps'])
    @property
    def block(self): return self._state['block']
    @property
    def unsupported(self): return self._state['assembly']['unsupported']
    @property
    def _values(self): return self._state['values']
    @property
    def fingerprint(self): return self._rules_id, self.block, self._factories, [c['settings'] for c in self._state['covers']], self._state['delta']
    @property
    def covers(self):
        return [_namespace({**c, 'state_source': c['settings']['state_source'] or ('inferred' if c['settings']['infer_motion'] else 'none')}) for c in self._state['covers']]
    @property
    def assembly(self):
        plans = self._state['plans']
        bindings = {name: SimpleNamespace(plan=SimpleNamespace(**plans[b['plan']]) if b['plan'] < len(plans) else SimpleNamespace(platform='converter')) for name, b in self._state['assembly']['bindings'].items()}
        return SimpleNamespace(bindings=bindings, descriptor=self.descriptor)
