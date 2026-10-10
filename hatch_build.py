"""Bundle the C ABI library and portable rule pack into platform-specific wheels."""
import os
import subprocess
import sys
import sysconfig
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if self.target_name != 'wheel':
            return
        root = Path(self.root)
        manifest = root / 'rust/Cargo.toml'
        subprocess.run(['cargo', 'build', '--locked', '--release', '--manifest-path', str(manifest)], check=True)
        target = Path(os.environ.get('CARGO_TARGET_DIR', root / 'rust/target'))
        if not target.is_absolute():
            target = root / target
        name = 'tuya_rule_engine.dll' if sys.platform == 'win32' else 'libtuya_rule_engine.dylib' if sys.platform == 'darwin' else 'libtuya_rule_engine.so'
        build_data['pure_python'] = False
        platform = sysconfig.get_platform().replace('-', '_').replace('.', '_')
        build_data['tag'] = 'py3-none-' + platform
        build_data['force_include'][str(target / 'release' / name)] = 'tuya2ildevice/' + name
        build_data['force_include'][str(root / 'rules')] = 'tuya2ildevice/rules'
