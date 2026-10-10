"""Freeze complete-driver traces from the Python reference, not the Rust engine."""
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests'), str(ROOT / 'tests/golden'), str(ROOT / 'tests/reference')]
import fixtures
from tuya2ildevice_reference.driver import TuyaDriver
from tuya2ildevice_reference.io import Connected, Disconnected, Message, Command


def data(value):
    name = type(value).__name__
    # Explicit wire tags; Python class names are not part of the portable protocol.
    tags = {'Connected': 'connected', 'Disconnected': 'disconnected', 'Message': 'message', 'Command': 'command',
            'Descriptor': 'descriptor', 'Value': 'value', 'Absent': 'absent', 'Event': 'event',
            'SendMessage': 'send_message', 'Reject': 'reject', 'SettingsChanged': 'settings_changed',
            'SetTimer': 'set_timer', 'CancelTimer': 'cancel_timer'}
    return {'type': tags[name], **asdict(value)}


cases = []
for code in fixtures.all_codes():
    device = fixtures.load(code)
    # Cloud fixture values are already decoded: an identity strategy represents this boundary.
    dpmap = {str(i + 1): c for i, c in enumerate(device['status'])}
    dps = {dp: device['status'][c] for dp, c in dpmap.items()}
    options = {'dpmap': dpmap, 'allow_hazardous': True}
    driver = TuyaDriver(device, **options)
    inputs = [Connected(), Message('state', dps), Message('active', dps), Message('passive', dps)]
    for name, spec in driver.descriptor['props'].items():
        if spec.get('rw') or spec['type'] == 'trigger':
            ty = spec['type']
            value = {'binary': True, 'trigger': None, 'text': '#12abef' if spec.get('role') == 'color' else 'example'}.get(ty)
            if ty == 'number': value = spec.get('min', 1)
            if ty == 'select': value = spec['options'][0] if spec['options'] else None
            inputs.append(Command(name, value))
    inputs.extend([Command('missing_property', 1), Disconnected(), Connected(), Message('state', dps)])
    steps = []
    for now, inp in enumerate(inputs):
        outputs = [data(o) for o in driver.handle(now, inp)]
        steps.append({'now': now, 'input': data(inp), 'outputs': outputs})
    cases.append({'name': code, 'device': device, 'options': options, 'descriptor': driver.descriptor, 'steps': steps})
path = ROOT / 'rust/engine/tests/driver_vectors.json'
path.write_text(json.dumps(cases, ensure_ascii=False, separators=(',', ':')) + '\n')
print(f'Exported {len(cases)} full-driver reference traces ({sum(len(c["steps"]) for c in cases)} steps)')
