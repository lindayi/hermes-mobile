"""Coverage partition must fail closed, never silently omit tests."""
import json
from pathlib import Path

import pytest


def fixture(tmp_path):
    (tmp_path / '.github').mkdir()
    (tmp_path / 'tests/browser').mkdir(parents=True)
    for name in ('alpha', 'beta', 'native', 'new'):
        (tmp_path / f'tests/test_{name}.py').write_text('')
    for name in ('a', 'b', 'c', 'd'):
        (tmp_path / f'tests/browser/{name}.spec.mjs').write_text('')
        (tmp_path / f'tests/browser/{name}.test.mjs').write_text('')
    manifest = tmp_path / '.github/host-tests.json'
    manifest.write_text(json.dumps({'tests/test_native.py': 'Requires installed patched native runtime'}))
    return manifest


def test_python_partition_is_complete_disjoint_and_new_files_are_hosted(tmp_path):
    from deploy.ci_selection import select_tests
    fixture(tmp_path)
    hosted = set(select_tests(tmp_path, 'python'))
    host = set(select_tests(tmp_path, 'host'))
    assert not hosted & host
    assert hosted | host == {f'tests/test_{name}.py' for name in ('alpha','beta','native','new')}
    assert 'tests/test_new.py' in hosted
    assert host == {'tests/test_native.py'}


@pytest.mark.parametrize('suite', ['python', 'browser', 'js'])
def test_shards_are_exact_disjoint_union(tmp_path, suite):
    from deploy.ci_selection import select_tests
    fixture(tmp_path)
    whole = set(select_tests(tmp_path, suite))
    shards = [set(select_tests(tmp_path, suite, shard=i, shards=2)) for i in range(2)]
    assert shards[0] | shards[1] == whole
    assert not shards[0] & shards[1]


@pytest.mark.parametrize('shard,shards', [(-1,2),(2,2),(0,0),(True,2),(0,False),(0,17)])
def test_bad_shards_fail(tmp_path, shard, shards):
    from deploy.ci_selection import select_tests
    fixture(tmp_path)
    with pytest.raises(ValueError):
        select_tests(tmp_path, 'python', shard=shard, shards=shards)


@pytest.mark.parametrize('value', [
    {'tests/test_missing.py':'stale'}, {'../test_escape.py':'escape'},
    {'tests/test_native.py':''}, ['tests/test_native.py'],
    {'tests/browser/a.spec.mjs':'wrong suite'},
])
def test_bad_manifest_fails_even_for_browser(tmp_path, value):
    from deploy.ci_selection import select_tests
    manifest = fixture(tmp_path)
    manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        select_tests(tmp_path, 'browser')


def test_symlink_test_is_not_accepted(tmp_path):
    from deploy.ci_selection import select_tests
    fixture(tmp_path)
    target = tmp_path / 'outside.py'
    target.write_text('')
    (tmp_path / 'tests/test_link.py').symlink_to(target)
    with pytest.raises(ValueError):
        select_tests(tmp_path, 'python')


def test_empty_or_unknown_selection_fails(tmp_path):
    from deploy.ci_selection import select_tests
    fixture(tmp_path)
    with pytest.raises(ValueError):
        select_tests(tmp_path, 'all')
    with pytest.raises(ValueError):
        select_tests(tmp_path, 'python', shard=15, shards=16)


def test_duplicate_manifest_key_is_rejected(tmp_path):
    from deploy.ci_selection import select_tests
    manifest = fixture(tmp_path)
    manifest.write_text('{"tests/test_native.py":"a", "tests/test_native.py":"b"}')
    with pytest.raises(ValueError):
        select_tests(tmp_path, 'python')
