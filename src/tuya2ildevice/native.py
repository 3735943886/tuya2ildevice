"""C ABI transport and cached rule-pack loading; contains no conversion rules."""
from __future__ import annotations

import ctypes
import functools
import hashlib
import json
import os
import sys
from pathlib import Path


@functools.cache
def library():
    suffix = '.dll' if sys.platform == 'win32' else '.dylib' if sys.platform == 'darwin' else '.so'
    filename = ('tuya_rule_engine' if sys.platform == 'win32' else 'libtuya_rule_engine') + suffix
    bundled = Path(__file__).parent / filename
    target = Path(__file__).resolve().parents[2] / 'rust/target'
    checkout = target / 'debug' / filename
    if not checkout.exists():
        checkout = target / 'release' / filename
    path = Path(os.environ['TUYA_ENGINE_LIBRARY']) if 'TUYA_ENGINE_LIBRARY' in os.environ else bundled if bundled.exists() else checkout
    lib = ctypes.CDLL(str(path))
    lib.tuya_engine_eval.argtypes = [ctypes.c_char_p]
    lib.tuya_engine_eval.restype = ctypes.c_void_p
    lib.tuya_engine_free.argtypes = [ctypes.c_void_p]
    lib.tuya_engine_free.restype = None
    return lib


def load_rules(paths=(), *, default_path=None):
    """Load the bundled default and user files through the native host loader."""
    bundled = Path(__file__).parent / 'rules/default.json'
    checkout = Path(__file__).resolve().parents[2] / 'rules/default.json'
    return _call({'op': 'load_rules', 'default_path': str(default_path or (bundled if bundled.exists() else checkout)),
                  'paths': [str(p) for p in paths]})['rules']


@functools.cache
def rules():
    paths = [p for p in os.environ.get('TUYA_ENGINE_RULES', '').split(os.pathsep) if p]
    return load_rules(paths)


def _call(request):
    lib = library()
    pointer = lib.tuya_engine_eval(json.dumps(request, ensure_ascii=False, allow_nan=False).encode())
    if not pointer:
        raise RuntimeError('Rust engine returned null')
    try:
        response = json.loads(ctypes.string_at(pointer))
    finally:
        lib.tuya_engine_free(pointer)
    if not response['ok']:
        raise ValueError(response['error'])
    return response['value']


def register_rules(pack):
    identifier = hashlib.sha256(json.dumps(pack, sort_keys=True).encode()).hexdigest()
    _call({'op': 'install_rules', 'rules_id': identifier, 'rules': pack})
    return identifier


@functools.cache
def rule_id():
    return register_rules(rules())


def call(op, **payload):
    return _call({'op': op, 'rules_id': rule_id(), **payload})


def preload():
    library()
    rule_id()
