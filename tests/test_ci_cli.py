import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


def load():
    path = Path(__file__).resolve().parents[1] / 'scripts/ci_tests.py'
    spec = importlib.util.spec_from_file_location('ci_test_cli', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('suite,managed', [
    ('host','python'),('native','python'),('python','python'),('js','js'),('browser','browser')
])
def test_wrapper_uses_managed_selection_and_serial_browser(monkeypatch, tmp_path, suite, managed):
    m = load()
    monkeypatch.setattr(m, 'select_tests', lambda source, kind, **kw: ['tests/example'])
    monkeypatch.setattr(m.shutil, 'which', lambda value: sys.executable)
    monkeypatch.setenv('HERMES_TEST_PYTHON', sys.executable)
    calls = []
    assert m.main(['--assets',str(tmp_path), '--shard','1','--shards','2',suite],
                  run=lambda *a, **kw: calls.append((a,kw))) == 0
    args, kw = calls[0]
    assert kw['suite'] == managed
    assert kw['node_concurrency'] == 1
    assert kw['extra_args'] == ['tests/example']
    assert kw['assets'] == tmp_path


def test_host_wrapper_falls_back_to_installed_node_when_path_has_none(monkeypatch):
    m = load()
    monkeypatch.setattr(m, 'select_tests', lambda *a, **kw: ['tests/example'])
    monkeypatch.setattr(m.shutil, 'which', lambda value: None)
    monkeypatch.setattr(m.os, 'access', lambda path, mode: str(path) in {
        sys.executable, '/home/lindayi/.hermes/node/bin/node'})
    monkeypatch.setenv('HERMES_TEST_PYTHON', sys.executable)
    calls = []
    assert m.main(['host'], run=lambda *a, **kw: calls.append(kw)) == 0
    assert calls[0]['node'] == '/home/lindayi/.hermes/node/bin/node'


def test_wrapper_preserves_test_failure(monkeypatch):
    m = load()
    monkeypatch.setattr(m, 'select_tests', lambda *a, **kw: ['tests/example'])
    monkeypatch.setattr(m.shutil, 'which', lambda value: sys.executable)
    monkeypatch.setenv('HERMES_TEST_PYTHON', sys.executable)
    def failed(*a, **kw):
        raise subprocess.CalledProcessError(7, ['test'])
    assert m.main(['python'],run=failed) == 7


def test_invalid_selection_does_not_run(monkeypatch):
    m = load()
    def invalid(*a,**kw):
        raise ValueError('invalid partition')
    monkeypatch.setattr(m, 'select_tests', invalid)
    assert m.main(['python'],run=lambda *a, **kw: pytest.fail('ran')) == 1
