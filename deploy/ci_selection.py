"""Complete, deterministic test partition for hosted CI and local compatibility."""
import json
from pathlib import Path


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate host test: {key}')
        result[key] = value
    return result


def select_tests(source, suite, *, shard=0, shards=1):
    source = Path(source).absolute()
    if suite not in {'python', 'host', 'js', 'browser'}:
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
    manifest = source / '.github/host-tests.json'
    if manifest.is_symlink():
        raise ValueError('Host manifest must not be a symlink')
    host = json.loads(manifest.read_text(), object_pairs_hook=_unique_object)
    if not isinstance(host, dict) or any(
            key not in python or not isinstance(reason, str) or not reason.strip()
            for key, reason in host.items()):
        raise ValueError('Invalid or stale host test manifest')
    groups = {'python': python - host.keys(), 'host': set(host),
              'browser': {p.relative_to(source).as_posix() for p in browser},
              'js': {p.relative_to(source).as_posix() for p in js}}
    selected = sorted(groups[suite])[shard::shards]
    if not selected:
        raise ValueError('Empty CI shard')
    return selected
