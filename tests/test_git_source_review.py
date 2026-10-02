"""Offline regressions for the independent provenance review; no live services."""
import json
import subprocess

import pytest

from deploy import self_deploy
from test_git_source import repository, git, guard_for, isolated_paths
from test_self_deploy import deploy_fixture


def test_unmerged_replacement_bytes_are_rejected(repository, monkeypatch, tmp_path):
    source, origin = repository
    guard = guard_for(monkeypatch, origin)
    main_sha = git(source, 'rev-parse', 'HEAD')
    (source / 'frontend/index.html').write_text('LOCAL UNMERGED REPLACEMENT')
    git(source, 'add', '.')
    git(source, 'commit', '-m', 'unmerged replacement')
    other_sha = git(source, 'rev-parse', 'HEAD')
    git(source, 'reset', '--hard', main_sha)
    git(source, 'replace', main_sha, other_sha)
    git(source, 'reset', '--hard', main_sha)
    assert git(source, 'status', '--porcelain') == ''
    assert git(source, 'rev-parse', 'HEAD') == git(origin, 'rev-parse', 'main') == main_sha
    with pytest.raises(RuntimeError, match='Git source|Git stage'):
        sha = guard.validate_source(source)
        self_deploy.stage_release(source, tmp_path / 'stage', git_sha=sha)


@pytest.mark.parametrize('variable', ['GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR',
                                      'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY'])
def test_git_ignores_inherited_repository_selection(repository, monkeypatch, tmp_path, variable):
    source, origin = repository
    guard_for(monkeypatch, origin)
    monkeypatch.setenv(variable, str(tmp_path / 'foreign-repository'))
    stage = self_deploy.stage_release(source, tmp_path / 'stage')
    assert (stage / 'frontend/index.html').read_text() == '<h1>tracked</h1>'


def test_git_ignores_inherited_config_overrides(repository, monkeypatch, tmp_path):
    source, origin = repository
    guard_for(monkeypatch, origin)
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0', 'remote.origin.url')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', str(tmp_path / 'foreign-origin'))
    self_deploy.stage_release(source, tmp_path / 'stage')


@pytest.mark.parametrize('entry', ['deploy', 'schedule'])
@pytest.mark.parametrize('explicit', [False, True])
def test_non_git_default_commands_rejected_before_side_effects(tmp_path, monkeypatch, entry, explicit):
    from backend.runs import RunJournal
    module, paths = deploy_fixture(tmp_path)
    RunJournal(paths.database)
    database = paths.database.read_bytes()
    monkeypatch.setattr(module.os, 'geteuid', lambda: 1000)
    commands = []

    def forbidden_popen(command, *args, **kwargs):
        commands.append(command)
        raise AssertionError('default subprocess.run reached a real command')

    # Intercept below run(): exercise the actual bound default, not a fake injected run.
    monkeypatch.setattr(subprocess, 'Popen', forbidden_popen)
    kwargs = {'run': subprocess.run} if explicit else {}
    with pytest.raises(RuntimeError, match='Git source'):
        if entry == 'deploy':
            module.deploy(paths, bootstrap=True, checks=lambda _: None,
                          verify=lambda *a, **k: None, **kwargs)
        else:
            module.main([], paths=paths, **kwargs)
    assert commands == []
    assert not paths.state.exists()
    assert not paths.dropin.exists()
    assert paths.database.read_bytes() == database
    assert (paths.webroot / 'index.html').read_text() == '<h1>old</h1>'


@pytest.mark.parametrize('explicit', [False, True])
def test_native_non_git_default_commands_rejected_before_side_effects(tmp_path, monkeypatch, explicit):
    from deploy import native_controls_release as native_release
    from test_native_controls_release import fixture
    paths, old, journal, dropin, events, args = fixture(tmp_path)
    args.pop('run')
    if explicit:
        args['run'] = subprocess.run
    monkeypatch.setattr(native_release, 'approved_controls', lambda stage: {})
    before = {str(p.relative_to(tmp_path)): p.read_bytes()
              for p in tmp_path.rglob('*') if p.is_file()}
    commands = []

    def forbidden_popen(command, *a, **kw):
        commands.append(command)
        raise AssertionError('native default subprocess.run reached a real command')

    monkeypatch.setattr(subprocess, 'Popen', forbidden_popen)
    with pytest.raises(RuntimeError, match='Git source'):
        native_release.deploy(paths, **args)
    assert not commands
    assert not events
    assert (paths.state / 'current').resolve() == old
    assert {str(p.relative_to(tmp_path)): p.read_bytes()
            for p in tmp_path.rglob('*') if p.is_file()} == before


def test_non_git_native_default_dropin_is_not_an_isolated_fixture(tmp_path, monkeypatch):
    from deploy import assets, native_controls_release as native_release
    from test_native_controls_release import fixture
    paths, old, journal, dropin, events, args = fixture(tmp_path)
    args.pop('native_dropin')  # Keep the harmless run, but the real native target.
    checked_path = assets.checked_path

    def no_production_access(path):
        if path == native_release.NATIVE_DROPIN:
            pytest.fail('real native dropin reached before Git provenance guard')
        return checked_path(path)

    monkeypatch.setattr(assets, 'checked_path', no_production_access)
    with pytest.raises(RuntimeError, match='Git source'):
        native_release.deploy(paths, **args)
    assert not events
    assert not (paths.state / 'status.json').exists()
    assert (paths.state / 'current').resolve() == old


@pytest.mark.parametrize('outcome', ['failed', 'rolled_back'])
@pytest.mark.parametrize('frontend_only', [True, False])
def test_active_release_provenance_survives_next_attempt(repository, monkeypatch, tmp_path,
                                                       outcome, frontend_only):
    from backend.runs import RunJournal
    source, origin = repository
    guard_for(monkeypatch, origin)
    paths = isolated_paths(tmp_path, source)
    paths.webroot.mkdir()
    (paths.webroot / 'index.html').write_text('old')
    RunJournal(paths.database)
    prior_sha = git(source, 'rev-parse', 'HEAD')
    args = dict(frontend_only=frontend_only, checks=lambda _: None,
                verify=lambda *a, **kw: None, run=lambda *a, **kw: None)
    prior_stage = self_deploy.deploy(paths, bootstrap=True, **args)
    (source / 'frontend/index.html').write_text('NEXT MAIN')
    git(source, 'add', '.')
    git(source, 'commit', '-m', 'next main')
    git(source, 'push', 'origin', 'main')
    next_sha = git(source, 'rev-parse', 'HEAD')

    def fail_checks(stage):
        raise RuntimeError('review simulated checks failure')

    def fail_verify(stage, backend, *, assets=None):
        if assets is None:
            raise RuntimeError('review simulated verification failure')

    args['checks' if outcome == 'failed' else 'verify'] = (
        fail_checks if outcome == 'failed' else fail_verify)
    with pytest.raises(RuntimeError, match='review simulated'):
        self_deploy.deploy(paths, **args)
    assert (paths.webroot / 'index.html').read_text() == '<h1>tracked</h1>'
    if not frontend_only:
        assert (paths.state / 'current').resolve() == prior_stage
    status = json.loads((paths.state / 'status.json').read_text())
    assert status['status'] == outcome
    assert status['git_sha'] == next_sha
    records = [p for p in prior_stage.glob('*.json') if prior_sha.encode() in p.read_bytes()]
    assert records, 'still-deployed release must retain its own Git SHA, not only the attempt SHA'
    assert json.loads(records[0].read_text())['git_sha'] == prior_sha


def test_legacy_native_stage_call_persists_provenance(repository, monkeypatch, tmp_path):
    source, origin = repository
    guard_for(monkeypatch, origin)
    sha = git(source, 'rev-parse', 'HEAD')
    # Native controller deliberately uses the two-positional-argument Path API.
    stage = self_deploy.stage_release(source, tmp_path / 'native-stage')
    records = [p for p in stage.glob('*.json') if sha.encode() in p.read_bytes()]
    assert records, 'stage_release must not discard the SHA when its caller only receives a Path'
    assert json.loads(records[0].read_text())['git_sha'] == sha


@pytest.mark.parametrize('explicit', [False, True])
def test_native_schedule_non_git_default_commands_rejected_before_side_effects(tmp_path, monkeypatch, explicit):
    from deploy import native_controls_release as native_release
    _, paths = deploy_fixture(tmp_path)
    monkeypatch.setattr(native_release.os, 'geteuid', lambda: 1000)
    before = {str(p.relative_to(tmp_path)): p.read_bytes()
              for p in tmp_path.rglob('*') if p.is_file()}
    commands = []

    def forbidden_popen(command, *args, **kwargs):
        commands.append(command)
        raise AssertionError('native scheduler default subprocess.run reached a real command')

    # Preserve the actual run identity; never launch services even on the RED path.
    monkeypatch.setattr(subprocess, 'Popen', forbidden_popen)
    kwargs = {'run': subprocess.run} if explicit else {}
    with pytest.raises(RuntimeError, match='Git source'):
        native_release.main(['--schedule', '--local-full-checks'], paths=paths, **kwargs)
    assert commands == []
    assert not paths.state.exists()
    assert not paths.dropin.exists()
    assert not paths.database.exists()
    assert {str(p.relative_to(tmp_path)): p.read_bytes()
            for p in tmp_path.rglob('*') if p.is_file()} == before
