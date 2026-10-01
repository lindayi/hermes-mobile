"""Synthetic frozen releases only; no installed-runtime tests or real services."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deploy import self_deploy as controller


def fixture(tmp_path, monkeypatch):
    from deploy import git_source, release_artifact
    from backend.runs import RunJournal
    source = tmp_path / 'source'
    (source / 'frontend').mkdir(parents=True)
    (source / 'frontend/index.html').write_text('<h1>raw</h1>')
    (source / 'backend').mkdir()
    (source / 'backend/main.py').write_text('VERSION = 1')
    (source / '.github').mkdir()
    (source / '.github/host-tests.json').write_text('{}')
    paths = controller.Paths(source, tmp_path / 'state', tmp_path / 'web',
                             tmp_path / 'live/runs.sqlite', tmp_path / 'systemd/dropin')
    paths.webroot.mkdir()
    (paths.webroot / 'index.html').write_text('old')
    previous = controller.stage_release(source, paths.state / 'releases/previous')
    (previous / 'public').mkdir()
    (previous / 'public/index.html').write_text('old')
    (paths.state / 'current').symlink_to(previous)
    paths.database.parent.mkdir()
    journal = RunJournal(paths.database)
    monkeypatch.setattr(git_source, 'preflight', lambda *args, **kw: 'a' * 40)
    monkeypatch.setattr(git_source, 'verify_stage', lambda *args: None)
    events = []

    def acquire(source, run_id, destination, *, run):
        assert source == paths.source and run_id == 51
        events.append('acquire')
        public = destination / 'public'
        public.mkdir(parents=True)
        (public / 'index.html').write_text('<h1>hosted bytes</h1>')
        import hashlib
        return release_artifact.VerifiedBundle('a' * 40, 51, 2, 61, 'b' * 64,
            release_artifact.source_mapping(source),
            {'index.html': hashlib.sha256(b'<h1>hosted bytes</h1>').hexdigest()}, public)

    monkeypatch.setattr(release_artifact, 'acquire_verified_bundle', acquire)
    def run(command, **kwargs):
        events.append(command)
    return paths, events, run, journal


def test_hosted_controller_installs_exact_bytes_and_runs_only_internal_residual(tmp_path, monkeypatch):
    paths, events, run, journal = fixture(tmp_path, monkeypatch)
    from deploy import frontend_release
    monkeypatch.setattr(frontend_release, 'build_frontend', lambda *a: pytest.fail('must never rebuild hosted assets'))
    def host_checks(paths, stage, *, run):
        assert events == ['acquire']
        assert (stage / 'public/index.html').read_text() == '<h1>hosted bytes</h1>'
        assert (stage / '.github/host-tests.json').exists(), 'full host partition is staged'
        events.append('host')
    monkeypatch.setattr(controller, 'run_host_checks', host_checks, raising=False)
    def verify(stage, backend):
        assert backend is True
        assert (paths.webroot / 'index.html').read_bytes() == (stage / 'public/index.html').read_bytes()
        events.append('verify')
    stage = controller.deploy(paths, hosted_run_id=51, run=run,
        checks=lambda _: pytest.fail('untrusted callback cannot bypass residual checks'), verify=verify)
    assert (paths.state / 'current').resolve() == stage
    assert events[-1] == 'verify'
    status = json.loads((paths.state / 'status.json').read_text())
    assert status['hosted_run_id'] == 51 and status['hosted_run_attempt'] == 2
    with journal.connect() as connection:
        assert connection.execute('SELECT * FROM deployment_gate').fetchall() == []



def test_residual_runner_selects_entire_frozen_host_partition(tmp_path, monkeypatch):
    import sys
    from deploy import test_workspace
    calls = []
    selected = ['tests/test_host_a.py', 'tests/test_host_b.py']
    stage = tmp_path / 'frozen'
    paths = controller.Paths(source=tmp_path / 'canonical')
    def select_tests(source, suite):
        assert source == stage and suite == 'host'
        return selected
    # PR4 owns this module. This seam tests controller routing, not its partitioner.
    monkeypatch.setitem(sys.modules, 'deploy.ci_selection', SimpleNamespace(select_tests=select_tests))
    monkeypatch.setattr(test_workspace, 'run_suite', lambda *a, **kw: calls.append((a, kw)))
    controller.run_host_checks(paths, stage, run=lambda *a, **kw: None)
    args, options = calls[0]
    assert args == (stage,)
    assert options['suite'] == 'python' and options['extra_args'] == selected
    assert options['assets'] == stage / 'public'
    assert options['python'] == str(paths.source / '.venv/bin/python')



@pytest.mark.parametrize('worker', [False, True])
def test_hosted_cli_runs_in_existing_systemd_worker_without_rescheduling(tmp_path, monkeypatch, worker):
    calls = []
    monkeypatch.setattr(controller.os, 'geteuid', lambda: 1000)
    monkeypatch.setenv('INVOCATION_ID', 'synthetic-systemd-context')
    monkeypatch.setattr(controller, 'deploy', lambda paths, **kw: calls.append(kw))
    args = ['--hosted-run-id', '51'] + (['--worker'] if worker else [])
    assert controller.main(args, paths=controller.Paths(source=tmp_path),
                           run=lambda *a, **kw: pytest.fail('no rescheduling')) == 0
    assert calls[0]['hosted_run_id'] == 51


def test_hosted_cli_rejects_non_worker_context(tmp_path, monkeypatch):
    monkeypatch.setattr(controller.os, 'geteuid', lambda: 1000)
    monkeypatch.delenv('INVOCATION_ID', raising=False)
    monkeypatch.setattr(controller, 'deploy', lambda *a, **kw: pytest.fail('no deployment'))
    with pytest.raises(RuntimeError, match='systemd'):
        controller.main(['--hosted-run-id', '51'], paths=controller.Paths(source=tmp_path))


@pytest.mark.parametrize('flag', ['--frontend-only', '--bootstrap', '--status'])
def test_hosted_cli_cannot_mix_maintenance_or_status_modes(tmp_path, monkeypatch, flag):
    monkeypatch.setattr(controller.os, 'geteuid', lambda: 1000)
    monkeypatch.setenv('INVOCATION_ID', 'synthetic')
    with pytest.raises(SystemExit):
        controller.main([flag, '--hosted-run-id', '51'], paths=controller.Paths(source=tmp_path))


@pytest.mark.parametrize('option,value', [('frontend_only', True), ('bootstrap', True), ('hosted_run_id', 0),
                                         ('hosted_run_id', {'verified': True})])
def test_hosted_controller_rejects_bypass_inputs_before_preflight(tmp_path, monkeypatch, option, value):
    from deploy import git_source
    monkeypatch.setattr(git_source, 'preflight', lambda *a, **kw: pytest.fail('invalid options rejected first'))
    options = {'hosted_run_id': 51, option: value}
    with pytest.raises(ValueError):
        controller.deploy(controller.Paths(source=tmp_path), checks=lambda *_: None,
                          verify=lambda *_: None, **options)



@pytest.mark.parametrize('failure', ['network', 'source-map', 'source-sha', 'public-bytes', 'native-change', 'host-failed', 'mutated'])
def test_hosted_failures_never_publish_restart_or_fall_back(tmp_path, monkeypatch, failure):
    from dataclasses import replace
    from deploy import release_artifact
    paths, events, run, journal = fixture(tmp_path, monkeypatch)
    previous = (paths.state / 'current').resolve()
    original = release_artifact.acquire_verified_bundle
    def acquire(*args, **kwargs):
        if failure == 'network': raise RuntimeError('network denied')
        evidence = original(*args, **kwargs)
        if failure == 'source-map': evidence = replace(evidence, source_files={})
        if failure == 'source-sha': evidence = replace(evidence, source_sha='c' * 40)
        if failure == 'public-bytes': (evidence.public_path / 'index.html').write_text('tampered')
        return evidence
    monkeypatch.setattr(release_artifact, 'acquire_verified_bundle', acquire)
    def host_checks(paths, stage, *, run):
        if failure == 'host-failed': raise RuntimeError('host failure')
        if failure == 'mutated': (stage / 'public/index.html').write_text('changed in checks')
    monkeypatch.setattr(controller, 'run_host_checks', host_checks)
    if failure == 'native-change': (paths.source / 'requirements.lock').write_text('new dependency')
    with pytest.raises(RuntimeError):
        controller.deploy(paths, hosted_run_id=51, run=run,
            checks=lambda _: pytest.fail('no fallback'), verify=lambda *a: pytest.fail('never published'))
    assert (paths.webroot / 'index.html').read_text() == 'old'
    assert (paths.state / 'current').resolve() == previous
    assert not any(isinstance(e, list) for e in events), 'never invoke a service command'
    assert not list(paths.state.glob('verified-*')), 'temporary bundle cleaned'
    with journal.connect() as connection:
        assert connection.execute('SELECT * FROM deployment_gate').fetchall() == []


def test_hosted_health_failure_uses_existing_rollback_and_reopens_gate(tmp_path, monkeypatch):
    paths, events, run, journal = fixture(tmp_path, monkeypatch)
    previous = (paths.state / 'current').resolve()
    monkeypatch.setattr(controller, 'run_host_checks', lambda *a, **kw: None)
    def verify(stage, backend, *, assets=None):
        if assets is None: raise RuntimeError('unhealthy candidate')
        assert stage == previous
        assert (paths.webroot / 'index.html').read_text() == 'old'
    with pytest.raises(RuntimeError, match='unhealthy'):
        controller.deploy(paths, hosted_run_id=51, run=run, checks=lambda _: None, verify=verify)
    assert (paths.state / 'current').resolve() == previous
    assert json.loads((paths.state / 'status.json').read_text())['status'] == 'rolled_back'
    with journal.connect() as connection:
        assert connection.execute('SELECT * FROM deployment_gate').fetchall() == []


def test_hosted_busy_run_cannot_restart_or_publish(tmp_path, monkeypatch):
    paths, events, run, journal = fixture(tmp_path, monkeypatch)
    previous = (paths.state / 'current').resolve()
    journal.submit('fixture-owner', 'default', 'session', 'synthetic', 'nonce')
    monkeypatch.setattr(controller, 'run_host_checks', lambda *a, **kw: None)
    with pytest.raises(RuntimeError, match='idle'):
        controller.deploy(paths, hosted_run_id=51, run=run, idle_timeout=0,
                          checks=lambda _: None, verify=lambda *a: None)
    assert (paths.state / 'current').resolve() == previous
    assert (paths.webroot / 'index.html').read_text() == 'old'
    assert not any(isinstance(e, list) for e in events)
