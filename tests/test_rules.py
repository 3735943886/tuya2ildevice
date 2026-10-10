"""The single rule format, native loading, and host reload boundary."""
import copy
import json

import pytest

from tuya2ildevice import Hub, TuyaDriver
from tuya2ildevice.host import OverrideWatcher, load_overrides
from tuya2ildevice.native import _call, load_rules, rules

DEVICE = {'id': 'd', 'product_id': 'p', 'category': 'kg',
          'function': {'switch_1': {'type': 'Boolean', 'values': {}}}}


def write(path, value):
    path.write_text(json.dumps(value))


def test_native_default_and_ordered_user_layers(tmp_path):
    write(tmp_path / '20_user.json', {'overrides': {'p': {'device': {'label': 'later'}}}})
    write(tmp_path / '10_user.json', {'overrides': {'p': {'device': {'label': 'earlier', 'model': 'retained'}}}})
    (tmp_path / '.ignored.json').write_text('broken')
    (tmp_path / '_ignored.json').write_text('broken')
    (tmp_path / 'schema.json').write_text('broken')
    (tmp_path / 'nested').mkdir()
    (tmp_path / 'nested' / '30.json').write_text('broken')
    result = _call({'op': 'load_rules', 'paths': [str(tmp_path)]})
    assert [p.rsplit('/', 1)[-1] for p in result['sources']] == ['10_user.json', '20_user.json']
    driver = TuyaDriver(DEVICE, rules=result['rules'])
    assert driver.descriptor['label'] == 'later'
    assert driver.block['device']['model'] == 'retained'
    assert result['rules']['platforms']


def test_array_replacement_member_deletion_and_null():
    base = copy.deepcopy(rules())
    base['maps']['custom'] = {'remove': 1, 'keep': 2, 'array': [1, 2]}
    merged = _call({'op': 'merge_rules', 'base': base, 'layers': [
        {'maps': {'custom': {'remove': {'$delete': True}, 'array': [3], 'keep': None}}}]})
    assert merged['maps']['custom'] == {'keep': None, 'array': [3]}
    assert base['maps']['custom']['remove'] == 1


def test_saved_settings_win_across_paths_and_do_not_get_written(tmp_path):
    first, second = tmp_path / 'a', tmp_path / 'b'
    first.mkdir()
    second.mkdir()
    settings = first / 'zz_settings.json'
    write(settings, {'overrides': {'p': {'cover': {'settle': 9}}}})
    before = settings.read_bytes()
    write(second / 'zzz_user.json', {'overrides': {'p': {'cover': {'settle': 2}}}})
    assert load_rules([first, second])['overrides']['p']['cover']['settle'] == 9
    assert settings.read_bytes() == before


@pytest.mark.parametrize('value', [{'version': 2}, {'layouts': []}, {'unknown': {}},
                                 {'platforms': [{'platform': 'switch'}]}, {'version': {'$delete': True}}])
def test_invalid_rule_sets_are_rejected(tmp_path, value):
    write(tmp_path / '10_bad.json', value)
    with pytest.raises(ValueError, match='10_bad.json'):
        load_rules([tmp_path])


def test_host_unifies_legacy_files_and_new_rule_files(tmp_path):
    write(tmp_path / '10_legacy.json', {'p': {'device': {'label': 'legacy'}}})
    write(tmp_path / '20_rules.json', {'overrides': {'p': {'device': {'label': 'unified'}}}})
    loaded = load_overrides(tmp_path)
    assert not loaded.warnings
    assert loaded.overrides == loaded.rules['overrides']
    assert TuyaDriver(DEVICE, rules=loaded.rules).descriptor['label'] == 'unified'


def test_rule_reload_detects_changes_without_descriptor_changes(tmp_path):
    hub = Hub([DEVICE])
    old = hub.drivers['d']
    write(tmp_path / '10_rule.json', {'maps': {'custom': {'a': 1}}})
    custom = load_rules([tmp_path])
    hub.reload({}, rules=custom)
    assert hub.drivers['d'] is not old
    assert hub.drivers['d'].descriptor == old.descriptor
    stable = hub.drivers['d']
    hub.reload({}, rules=custom)
    assert hub.drivers['d'] is stable


def test_watcher_reloads_global_rules_and_restores_defaults_after_removal(tmp_path):
    class Runner:
        def __init__(self):
            self.hub = Hub([DEVICE])

        def reload(self, overrides, converters, converter_types, *, rules):
            self.hub.reload(overrides, converters, converter_types, rules=rules)

    runner = Runner()
    watcher = OverrideWatcher(tmp_path, runner)
    path = tmp_path / '10_rules.json'
    layouts = copy.deepcopy(rules()['layouts']['switch'])
    layouts[0]['definition']['label'] = 'User switch'
    write(path, {'layouts': {'switch': layouts}})
    assert watcher.check()
    assert runner.hub.drivers['d'].descriptor['props']['switch_1']['label'] == 'User switch'
    path.unlink()
    assert watcher.check()
    assert runner.hub.drivers['d'].descriptor['props']['switch_1'].get('label') != 'User switch'
