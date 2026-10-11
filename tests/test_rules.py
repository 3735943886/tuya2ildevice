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
    from pathlib import Path
    bundled = Path(__file__).resolve().parents[1] / 'rules'
    result = _call({'op': 'load_rules', 'paths': [str(tmp_path), str(bundled)]})
    assert [p.rsplit('/', 1)[-1] for p in result['sources']] == ['00-default.json', '10_user.json', '20_user.json']
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


def test_settings_follow_filename_order_without_special_priority(tmp_path):
    first, second = tmp_path / 'a', tmp_path / 'b'
    first.mkdir()
    second.mkdir()
    settings = first / 'zz_settings.json'
    write(settings, {'overrides': {'p': {'cover': {'settle': 9}}}})
    before = settings.read_bytes()
    write(second / 'zzz_user.json', {'overrides': {'p': {'cover': {'settle': 2}}}})
    assert load_rules([first, second])['overrides']['p']['cover']['settle'] == 2
    assert settings.read_bytes() == before


@pytest.mark.parametrize('value', [{'version': 2}, {'layouts': []}, {'unknown': {}},
                                 {'platforms': [{'platform': 'switch'}]}, {'version': {'$delete': True}}])
def test_invalid_rule_sets_are_rejected(tmp_path, value):
    write(tmp_path / '10_bad.json', value)
    with pytest.raises(ValueError):
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


def test_no_implicit_default_or_default_filename_requirement(tmp_path):
    from pathlib import Path
    write(tmp_path / '50_complete.json', rules())
    result = _call({'op': 'load_rules', 'paths': [str(tmp_path)]})
    assert result['rules'] == rules()
    assert [Path(p).name for p in result['sources']] == ['50_complete.json']
    with pytest.raises(ValueError):
        _call({'op': 'load_rules', 'paths': []})


def test_sort_across_locations_and_validate_only_after_all_layers(tmp_path):
    first, second = tmp_path / 'a', tmp_path / 'b'
    first.mkdir()
    second.mkdir()
    base = copy.deepcopy(rules())
    platforms = base.pop('platforms')
    write(second / '00_base.json', base)
    write(first / '10_platforms.json', {'platforms': platforms})
    write(second / '30_label.json', {'overrides': {'p': {'device': {'label': 'last'}}}})
    write(first / '20_label.json', {'overrides': {'p': {'device': {'label': 'first'}}}})
    result = _call({'op': 'load_rules', 'paths': [str(first), str(second)]})
    assert TuyaDriver(DEVICE, rules=result['rules']).descriptor['label'] == 'last'


def test_host_uses_the_same_global_order_as_the_native_loader(tmp_path):
    write(tmp_path / '000_before_base.json', {'maps': {'switch_on': {'probe': True}}})
    write(tmp_path / '10_after_base.json', {'overrides': {'p': {'device': {'label': 'user'}}}})
    loaded = load_overrides(tmp_path)
    assert not loaded.warnings
    assert loaded.rules == load_rules([tmp_path])
