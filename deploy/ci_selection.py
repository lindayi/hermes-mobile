"""Complete, deterministic test selection for hosted CI and local compatibility."""
import json
from pathlib import Path


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate test manifest entry: {key}')
        result[key] = value
    return result


def _load_manifest(source, filename, python):
    path = source / '.github' / filename
    label = filename.removesuffix('.json').removesuffix('-tests')
    if path.is_symlink():
        raise ValueError(f'{label.capitalize()} manifest must not be a symlink')
    manifest = json.loads(path.read_text(), object_pairs_hook=_unique_object)
    if not isinstance(manifest, dict) or any(
            key not in python or not isinstance(reason, str) or not reason.strip()
            for key, reason in manifest.items()):
        raise ValueError(f'Invalid or stale {label} test manifest')
    return manifest


def select_tests(source, suite, *, shard=0, shards=1):
    source = Path(source).absolute()
    if suite not in {'python', 'host', 'native', 'js', 'browser'}:
        raise ValueError('Unknown CI suite')
    if type(shard) is not int or type(shards) is not int or not 1 <= shards <= 16 or not 0 <= shard < shards:
        raise ValueError('Invalid shard range')
    root = source / 'tests'
    python = set(root.rglob('test_*.py')) | set(root.rglob('*_test.py'))
    browser = set((root / 'browser').glob('*.spec.mjs'))
    js = set((root / 'browser').glob('*.test.mjs'))
    for path in python | browser | js:
        if not path.is_file() or path.resolve() != path or not path.is_relative_to(root):
            raise ValueError(f'Unsafe test path: {path}')
    python = {p.relative_to(source).as_posix() for p in python}
    host = _load_manifest(source, 'host-tests.json', python)
    native = _load_manifest(source, 'native-tests.json', python)
    if not native.keys() <= host.keys():
        raise ValueError('Invalid native test manifest: entries must be a subset of host tests')
    host_files, native_files = set(host), set(native)
    portable = python - host_files
    if portable & host_files or portable | host_files != python:
        raise ValueError('Python test partition is incomplete or overlapping')
    groups = {'python': portable, 'host': host_files, 'native': native_files,
              'browser': {p.relative_to(source).as_posix() for p in browser},
              'js': {p.relative_to(source).as_posix() for p in js}}
    selected = sorted(groups[suite])[shard::shards]
    if not selected:
        raise ValueError('Empty CI shard')
    return selected
