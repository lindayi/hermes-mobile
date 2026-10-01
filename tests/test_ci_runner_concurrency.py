"""Concurrency controls use the real managed lifecycle with no child processes."""
from pathlib import Path
import sys

import pytest

from deploy import test_workspace


@pytest.fixture
def source(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    (source / 'tests/browser').mkdir(parents=True)
    (source / 'frontend').mkdir()
    (source / 'tests/browser/ui.spec.mjs').write_text('')
    (source / 'tests/browser/unit.test.mjs').write_text('')
    # Injected command callbacks spawn no children; ignore unrelated /proc churn.
    monkeypatch.setattr(test_workspace, '_workspace_processes', lambda *a, **k: ({}, False))
    return source


@pytest.mark.parametrize('suite', ['browser', 'js', 'all'])
@pytest.mark.parametrize('concurrency', [None, 1, 2, 3, 4])
def test_node_concurrency_reaches_command_without_changing_defaults(tmp_path, source, suite, concurrency):
    calls = []
    root = tmp_path / 'managed'
    options = {} if concurrency is None else {'node_concurrency': concurrency}
    test_workspace.run_suite(
        source, python=sys.executable, node='/synthetic/node', suite=suite,
        root=root, run=lambda command, **kwargs: calls.append((command, kwargs)), **options,
    )
    selected = {
        'browser': ['tests/browser/ui.spec.mjs'],
        'js': ['tests/browser/unit.test.mjs'],
        'all': ['tests/browser/ui.spec.mjs', 'tests/browser/unit.test.mjs'],
    }[suite]
    expected = 2 if concurrency is None else concurrency
    assert len(calls) == (2 if suite == 'all' else 1)
    assert calls[-1][0] == ['/synthetic/node', '--test', f'--test-concurrency={expected}', *selected]
    if suite == 'all':
        command, kwargs = calls[0]
        workspace = Path(kwargs['env']['HOME']).parent
        assert command == [
            sys.executable, '-c', test_workspace.PYTEST_BOOTSTRAP, 'tests', '-q',
            '-p', 'no:cacheprovider', '--basetemp', str(workspace / 'pytest'),
        ]
    assert all(kwargs['cwd'] == source and kwargs['check'] is True for _, kwargs in calls)
    assert not list(root.glob('r-*'))


class IntSubclass(int):
    pass


@pytest.mark.parametrize('suite', ['browser', 'python'])
@pytest.mark.parametrize('concurrency', [
    0, -1, 5, True, False, 1.0, '1', None, [], {}, b'1',
    pytest.param(IntSubclass(2), id='int-subclass'),
])
def test_invalid_node_concurrency_fails_before_workspace_allocation(tmp_path, monkeypatch, suite, concurrency):
    root = tmp_path / 'managed'
    allocations = []

    def allocate(*args, **kwargs):
        allocations.append(args)
        pytest.fail('invalid concurrency reached managed root allocation')

    monkeypatch.setattr(test_workspace, '_root', allocate)
    with pytest.raises(ValueError, match='node_concurrency'):
        test_workspace.run_suite(
            tmp_path, python=sys.executable, node='/synthetic/node', suite=suite,
            root=root, node_concurrency=concurrency,
            run=lambda *a, **k: pytest.fail('invalid concurrency executed a command'),
        )
    assert allocations == []
    assert not root.exists()
