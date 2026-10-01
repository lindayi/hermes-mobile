"""Offline only: temporary releases/webroots; no system service or live config access."""
from pathlib import Path
import importlib
import json
import stat
import subprocess
import sys

import pytest


def controller():
    return importlib.import_module('deploy.self_deploy')


def source_fixture(tmp_path):
    source = tmp_path / 'source'
    for relative, content in {
        'backend/__init__.py': '', 'backend/serve.py': 'VERSION = "new"\n',
        'frontend/index.html': '<h1>new</h1>', 'frontend/app.js': '/* new */',
        'tests/test_fixture.py': 'def test_ok(): assert True\n',
        'requirements.lock': 'fixture==1\n', 'patches/native.patch': 'native',
        'config.json': 'SECRET', 'state/auth.sqlite': 'SECRET', '.venv/token': 'SECRET',
    }.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return source


def test_stage_is_private_frozen_source_with_test_support_not_private_state(tmp_path):
    source = source_fixture(tmp_path)
    stage = controller().stage_release(source, tmp_path / 'private' / 'releases' / 'one')
    assert (stage / 'backend/serve.py').read_text() == 'VERSION = "new"\n'
    assert (stage / 'tests/test_fixture.py').exists()
    assert (stage / 'requirements.lock').exists()
    assert not (stage / 'config.json').exists()
    assert not (stage / 'state').exists()
    assert not (stage / '.venv').exists()
    assert stat.S_IMODE(stage.stat().st_mode) == 0o700
    assert (stage / 'backend/serve.py').read_text() == 'VERSION = "new"\n'


@pytest.mark.parametrize('relative', ['backend/link.py', 'frontend/link.js', 'tests/link.py'])
def test_stage_rejects_symlinks_in_source(tmp_path, relative):
    source = source_fixture(tmp_path)
    (source / relative).symlink_to(source / 'config.json')
    with pytest.raises(ValueError, match='symlink'):
        controller().stage_release(source, tmp_path / 'release')


def deploy_fixture(tmp_path):
    module = controller()
    source = source_fixture(tmp_path)
    paths = module.Paths(source=source, state=tmp_path / 'private',
                         webroot=tmp_path / 'web', database=tmp_path / 'live/runs.sqlite',
                         dropin=tmp_path / 'systemd/override.conf')
    paths.webroot.mkdir()
    (paths.webroot / 'index.html').write_text('<h1>old</h1>')
    return module, paths


@pytest.mark.parametrize('entry',['releases','backups','deploy.lock','status.json'])
def test_internal_symlinks_rejected_before_writes_or_checks(tmp_path, entry):
    module,paths=deploy_fixture(tmp_path)
    paths.state.mkdir()
    outside=tmp_path/'outside'
    if entry.endswith(('.lock','.json')):outside.write_text('unchanged')
    else:outside.mkdir()
    (paths.state/entry).symlink_to(outside,target_is_directory=outside.is_dir())
    with pytest.raises(ValueError,match='Symlink'):
        module.deploy(paths,frontend_only=True,checks=lambda _:pytest.fail('must reject before checks'),verify=lambda *_:None)
    if outside.is_dir():assert not list(outside.iterdir())
    else:assert outside.read_text()=='unchanged'
    assert (paths.webroot/'index.html').read_text()=='<h1>old</h1>'


@pytest.mark.parametrize('kind',['external','dangling','directory'])
def test_current_pointer_only_accepts_a_real_private_release(tmp_path,kind):
    module,paths=deploy_fixture(tmp_path)
    paths.state.mkdir()
    if kind=='directory':(paths.state/'current').mkdir()
    else:
        target=tmp_path/'outside'
        if kind=='external':target.mkdir()
        (paths.state/'current').symlink_to(target,target_is_directory=True)
    with pytest.raises(ValueError,match='current|release'):
        module.deploy(paths,frontend_only=True,checks=lambda _:pytest.fail('no checks'),verify=lambda *_:None)
    assert not (paths.state/'releases').exists()


def test_staging_rejects_destination_alias_before_copy(tmp_path):
    source=source_fixture(tmp_path);outside=tmp_path/'outside';outside.mkdir()
    link=tmp_path/'alias';link.symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='Symlink'):
        controller().stage_release(source,link/'snapshot')
    assert not list(outside.iterdir())


def test_release_checks_and_publishes_versioned_assets_not_mutable_source_urls(tmp_path):
    module, paths = deploy_fixture(tmp_path)
    (paths.source/'frontend/index.html').write_text('<script src="./app.js"></script>')
    def checks(stage):
        public=stage/'public'
        assert public.is_dir(), 'the exact generated assets must exist before checks'
        assert './app.js' not in (public/'index.html').read_text()
        assert (stage/'frontend/app.js').exists(), 'source remains intact for unit tests'
    def verify(stage,backend):
        assert not backend
        assert (paths.webroot/'index.html').read_bytes()==(stage/'public/index.html').read_bytes()
        assert not (paths.webroot/'app.js').exists()
        assert list(paths.webroot.glob('app.*.js'))
    module.deploy(paths,frontend_only=True,checks=checks,verify=verify,
                  run=lambda *a,**kw:pytest.fail('frontend does not restart services'))


def test_frontend_release_runs_real_fixture_check_and_publisher_without_services(tmp_path):
    module, paths = deploy_fixture(tmp_path)
    events = []
    def checks(stage):
        assert (paths.webroot / 'index.html').read_text() == '<h1>old</h1>'
        subprocess.run([sys.executable, '-m', 'pytest', 'tests', '-q'], cwd=stage, check=True)
        events.append('checked')
    def verify(stage, backend):
        assert not backend
        assert (paths.webroot / 'index.html').read_bytes() == (stage / 'frontend/index.html').read_bytes()
        events.append('verified')
    module.deploy(paths, frontend_only=True, checks=checks, verify=verify,
                  run=lambda *a, **k: pytest.fail('frontend must not invoke systemd'))
    assert events == ['checked', 'verified']
    assert not paths.database.exists()
    assert not paths.dropin.exists()
    assert json.loads((paths.state / 'status.json').read_text())['status'] == 'succeeded'


def test_frontend_verification_failure_restores_exact_previous_assets(tmp_path):
    module, paths = deploy_fixture(tmp_path)
    def verify(stage, backend, *, assets=None):
        if assets is None:
            raise RuntimeError('fixture unhealthy')
    with pytest.raises(RuntimeError, match='fixture unhealthy'):
        module.deploy(paths, frontend_only=True, checks=lambda stage: None, verify=verify)
    assert (paths.webroot / 'index.html').read_text() == '<h1>old</h1>'
    assert not (paths.webroot / 'app.js').exists()
    assert (paths.source / 'frontend/index.html').read_text() == '<h1>new</h1>'
    assert json.loads((paths.state / 'status.json').read_text())['status'] == 'rolled_back'


def test_bootstrap_then_routine_bridge_release_uses_private_current_and_gate(tmp_path):
    from backend.runs import RunJournal, RunConflict
    module, paths = deploy_fixture(tmp_path)
    journal = RunJournal(paths.database)
    commands = []
    def run(command, **kwargs):
        commands.append(command)
    def checks(stage):
        assert journal.submit('u', 'default', 's', 'probe', stage.name)[1]
        row = journal.connect().execute('SELECT id FROM runs WHERE status="queued"').fetchone()
        journal.finish('u', row['id'], 'completed')
    def verify(stage, backend):
        assert backend
        assert (paths.state / 'current').resolve() == stage
        assert (paths.webroot / 'index.html').read_bytes() == (stage / 'frontend/index.html').read_bytes()
        with pytest.raises(RunConflict, match='deploy'):
            journal.submit('u', 'default', 'blocked', 'x', 'blocked')
    old = module.deploy(paths, bootstrap=True, checks=checks, verify=verify, run=run)
    (paths.source / 'backend/serve.py').write_text('VERSION="newer"\n')
    new = module.deploy(paths, checks=checks, verify=verify, run=run)
    assert old != new
    assert (old / 'backend/serve.py').read_text() == 'VERSION = "new"\n'
    assert str(paths.state / 'current') in paths.dropin.read_text()
    assert 'NoNewPrivileges=yes' in paths.dropin.read_text()
    assert all('hermes-mobile-api' not in ' '.join(c) for c in commands)
    assert sum(c == ['systemctl', '--user', 'restart', 'hermes-mobile.service'] for c in commands) == 2
    assert journal.submit('u', 'default', 'unblocked', 'x', 'unblocked')[1]



def test_gate_is_closed_before_drain_and_unknown_blocks_without_restart(tmp_path):
    from backend.runs import RunJournal, RunConflict
    module, paths = deploy_fixture(tmp_path)
    journal = RunJournal(paths.database)
    module.deploy(paths, bootstrap=True, checks=lambda stage: None, verify=lambda *args: None,
                  run=lambda *args, **kw: None)
    run_record, _ = journal.submit('u', 'p', 's', 'hello', '1')
    sleeps = []
    def sleep(seconds):
        with pytest.raises(RunConflict, match='deploy'):
            journal.submit('u', 'p', 'new', 'hello', '2')
        sleeps.append(seconds)
        journal.finish('u', run_record['id'], 'completed')
    module.deploy(paths, checks=lambda stage: None, verify=lambda *args: None,
                  run=lambda *args, **kw: None, sleep=sleep)
    assert sleeps
    unresolved, _ = journal.submit('u', 'p', 's', 'hello', '3')
    journal.finish('u', unresolved['id'], 'unknown')
    with pytest.raises(RuntimeError, match='unknown'):
        module.deploy(paths, checks=lambda stage: None, verify=lambda *args: None,
                      run=lambda *args, **kw: pytest.fail('must not restart'))
    assert journal.submit('u', 'p', 'different', 'hello', '4')[1]


def test_bootstrap_refuses_active_old_bridge_without_stopping(tmp_path):
    from backend.runs import RunJournal
    module, paths = deploy_fixture(tmp_path)
    RunJournal(paths.database).submit('u', 'p', 's', 'hello', '1')
    with pytest.raises(RuntimeError, match='idle'):
        module.deploy(paths, bootstrap=True, checks=lambda stage: None, verify=lambda *args: None,
                      run=lambda *args, **kw: pytest.fail('must not stop active old bridge'))



@pytest.mark.parametrize('failure', ['verify', 'restart', 'daemon-reload'])
def test_failed_bridge_restores_pointer_assets_and_checks_old_health(tmp_path, failure):
    from backend.runs import RunJournal
    module, paths = deploy_fixture(tmp_path)
    journal = RunJournal(paths.database)
    old = module.deploy(paths, bootstrap=True, checks=lambda stage: None,
                        verify=lambda *args: None, run=lambda *args, **kw: None)
    (paths.source / 'frontend/index.html').write_text('newer')
    commands, verified = [], []
    failed = False
    def run(command, **kwargs):
        nonlocal failed
        commands.append(command)
        if failure in command and not failed:
            failed = True
            raise RuntimeError('launch fixture failure')
    def verify(stage, backend, *, assets=None):
        verified.append(stage)
        if stage != old:
            raise RuntimeError('verify fixture failure')
        assert (paths.state / 'current').resolve() == old
        assert (paths.webroot / 'index.html').read_text() == '<h1>new</h1>'
    with pytest.raises(RuntimeError, match='fixture failure'):
        module.deploy(paths, checks=lambda stage: None, verify=verify, run=run)
    assert (paths.state / 'current').resolve() == old
    assert verified[-1] == old
    assert (paths.source / 'frontend/index.html').read_text() == 'newer'
    assert journal.submit('u', 'p', 's', 'ok', '1')[1]


def test_failed_rollback_keeps_gate_closed(tmp_path):
    from backend.runs import RunJournal, RunConflict
    module, paths = deploy_fixture(tmp_path)
    journal = RunJournal(paths.database)
    module.deploy(paths, bootstrap=True, checks=lambda stage: None,
                  verify=lambda *args: None, run=lambda *args, **kw: None)
    def unhealthy(*args, **kwargs):
        raise RuntimeError('unhealthy')
    with pytest.raises(RuntimeError, match='rollback'):
        module.deploy(paths, checks=lambda stage: None, verify=unhealthy, run=lambda *args, **kw: None)
    with pytest.raises(RunConflict, match='deploy'):
        journal.submit('u', 'p', 's', 'ok', '1')
    assert json.loads((paths.state / 'status.json').read_text())['status'] == 'rollback_failed'



@pytest.mark.parametrize('relative', ['requirements.lock', 'patches/native.patch', 'backend/native_api_service.py', 'hermes-plugin/plugin.yaml'])
def test_routine_release_refuses_dependency_native_or_plugin_changes(tmp_path, relative):
    from backend.runs import RunJournal
    module, paths = deploy_fixture(tmp_path)
    RunJournal(paths.database)
    old = module.deploy(paths, bootstrap=True, checks=lambda stage: None,
                        verify=lambda *args: None, run=lambda *args, **kw: None)
    changed = paths.source / relative
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text('changed external dependency')
    with pytest.raises(RuntimeError, match='dependency|native'):
        module.deploy(paths, checks=lambda stage: pytest.fail('refuse before tests'),
                      verify=lambda *args: None, run=lambda *args, **kw: pytest.fail('no restart'))
    assert (paths.state / 'current').resolve() == old


def test_controller_serializes_frontend_and_backend_publishers(tmp_path):
    import fcntl
    module, paths = deploy_fixture(tmp_path)
    paths.state.mkdir()
    with (paths.state / 'deploy.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match='already'):
            module.deploy(paths, frontend_only=True, checks=lambda stage: pytest.fail('busy'), verify=lambda *args: None)


def test_backend_requires_existing_bootstrap_and_existing_run_database(tmp_path):
    module, paths = deploy_fixture(tmp_path)
    with pytest.raises(RuntimeError, match='bootstrap'):
        module.deploy(paths, checks=lambda stage: None, verify=lambda *args: None,
                      run=lambda *args, **kw: pytest.fail('bootstrap required'))
    with pytest.raises(RuntimeError, match='database'):
        module.deploy(paths, bootstrap=True, checks=lambda stage: None, verify=lambda *args: None,
                      run=lambda *args, **kw: pytest.fail('missing db must not mean idle'))



def test_default_cli_schedules_separate_user_unit_without_restarting_current_request(tmp_path, monkeypatch):
    module, paths = deploy_fixture(tmp_path)
    calls = []
    monkeypatch.setattr(module.os, 'geteuid', lambda: 1000)
    assert module.main([], paths=paths, run=lambda cmd, **kw: calls.append(cmd)) == 0
    command, = calls
    assert command[:3] == ['systemd-run', '--user', '--collect']
    assert '--on-active=5s' in command
    assert '--property=NoNewPrivileges=yes' in command
    assert '--worker' in command
    assert command[-4:] == [str(paths.source / '.venv/bin/python'), '-m', 'deploy.self_deploy', '--worker']
    assert not paths.database.exists()


def test_cli_status_is_read_only_and_worker_refuses_interactive_execution(tmp_path, monkeypatch, capsys):
    module, paths = deploy_fixture(tmp_path)
    monkeypatch.setattr(module.os, 'geteuid', lambda: 1000)
    monkeypatch.delenv('INVOCATION_ID', raising=False)
    assert module.main(['--status'], paths=paths) == 0
    assert 'not_deployed' in capsys.readouterr().out
    assert not paths.state.exists()
    with pytest.raises(RuntimeError, match='transient'):
        module.main(['--worker'], paths=paths)


def test_cli_rejects_root_even_for_bootstrap(tmp_path, monkeypatch):
    module, paths = deploy_fixture(tmp_path)
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    with pytest.raises(RuntimeError, match='unprivileged'):
        module.main(['--bootstrap'], paths=paths)



def test_checks_remove_disposable_outputs_without_touching_staged_source(tmp_path, monkeypatch):
    module, paths = deploy_fixture(tmp_path)
    stage = module.stage_release(paths.source, tmp_path / 'stage')
    (stage / 'tests/browser').mkdir()
    (stage / 'tests/browser/example.test.mjs').write_text('')
    (stage / 'tests/browser/example.spec.mjs').write_text('')
    sentinel = stage / 'keep.txt'
    sentinel.write_text('source is not disposable')
    monkeypatch.setenv('HERMES_MOBILE_CONFIG', '/private/live.json')
    monkeypatch.setenv('PYTHONPATH', '/private/native')
    original_environment = dict(__import__('os').environ)
    outputs = []

    def execute(command, **kwargs):
        environment = kwargs['env']
        assert 'HERMES_TEST_ARTIFACT_DIR' in environment
        assert 'HERMES_MOBILE_CONFIG' not in environment
        assert 'PYTHONPATH' not in environment
        assert environment['PYTHONDONTWRITEBYTECODE'] == '1'
        for key in ('HOME', 'HERMES_HOME', 'TMPDIR', 'XDG_CACHE_HOME', 'HERMES_TEST_ARTIFACT_DIR'):
            target = Path(environment[key]) / ('fixture-' + key)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('disposable')
            outputs.append(target)
        return subprocess.CompletedProcess(command, 0)

    module.run_checks(paths, stage, run=execute)
    assert outputs and all(not path.exists() for path in outputs)
    assert sentinel.read_text() == 'source is not disposable'
    assert dict(__import__('os').environ) == original_environment


def test_checks_execute_full_suites_on_stage_with_fixed_venv_and_no_live_config(tmp_path, monkeypatch):
    module, paths = deploy_fixture(tmp_path)
    stage = module.stage_release(paths.source, tmp_path / 'stage')
    (stage / 'tests/browser').mkdir()
    (stage / 'tests/browser/example.test.mjs').write_text('')
    (stage / 'tests/browser/mobile.spec.mjs').write_text('')
    monkeypatch.setenv('HERMES_MOBILE_CONFIG', '/private/live.json')
    calls = []
    module.run_checks(paths, stage, run=lambda cmd, **kw: calls.append((cmd, kw)))
    assert len(calls) == 2
    assert calls[0][0][0] == str(paths.source / '.venv/bin/python')
    assert calls[0][0][1] == '-c' and 'pytest.main' in calls[0][0][2]
    assert 'tests' in calls[0][0][3:] and '-q' in calls[0][0][3:]
    assert '--test' in calls[1][0]
    assert [arg for arg in calls[1][0] if arg.endswith('.mjs')] == [
        'tests/browser/example.test.mjs', 'tests/browser/mobile.spec.mjs']
    for cmd, kw in calls:
        assert kw['cwd'] == stage and kw['check']
        assert kw['umask'] == 0o077
        assert 'HERMES_MOBILE_CONFIG' not in kw['env']
        assert kw['env']['HERMES_TEST_PYTHON'] == str(paths.source / '.venv/bin/python')


@pytest.mark.parametrize('inherited_path', ['/usr/bin:/bin', '', None])
def test_checks_python_children_resolve_fixed_node_without_interactive_path(tmp_path, monkeypatch, inherited_path):
    import os
    module, paths = deploy_fixture(tmp_path)
    python = paths.source / '.venv/bin/python'
    (paths.source / '.venv/token').unlink()
    (paths.source / '.venv').rmdir()
    (paths.source / '.venv').symlink_to(sys.prefix, target_is_directory=True)
    (paths.source / 'tests/test_fixture.py').write_text('''import json, os, subprocess
from pathlib import Path

def test_python_child_executes_fixed_node():
    actual = subprocess.check_output(['node', '-p', 'process.execPath'], text=True).strip()
    assert Path(actual).resolve() == Path('/home/lindayi/.hermes/node/bin/node').resolve()
    Path('child-environment.json').write_text(json.dumps({'node': actual, 'path': os.environ['PATH']}))
''')
    stage = module.stage_release(paths.source, tmp_path / 'stage')
    (stage / 'tests/browser').mkdir()
    (stage / 'tests/browser/example.test.mjs').write_text('import "node:test";\n')
    (stage / 'public').mkdir()
    if inherited_path is None:
        monkeypatch.delenv('PATH', raising=False)
    else:
        monkeypatch.setenv('PATH', inherited_path)
    monkeypatch.setenv('HERMES_MOBILE_CONFIG', '/private/live.json')
    monkeypatch.setenv('PYTHONPATH', '/private/native')
    original = dict(os.environ)
    calls = []

    def recording_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.run(command, **kwargs)

    module.run_checks(paths, stage, run=recording_run)
    evidence = json.loads((stage / 'child-environment.json').read_text())
    node = Path('/home/lindayi/.hermes/node/bin/node')
    assert Path(evidence['node']).resolve() == node.resolve()
    expected_path = os.pathsep.join(filter(None, (str(node.parent), inherited_path if inherited_path is not None else os.defpath)))
    assert evidence['path'] == expected_path
    assert len(calls) == 2
    assert calls[0][0][0] == str(python)
    assert calls[0][0][1] == '-c' and 'pytest.main' in calls[0][0][2]
    assert 'tests' in calls[0][0][3:] and '-q' in calls[0][0][3:]
    assert calls[1][0][0] == str(node)
    for command, kwargs in calls:
        assert kwargs['env']['PATH'] == expected_path
        assert kwargs['env']['HERMES_TEST_PYTHON'] == str(python)
        assert kwargs['env']['HERMES_FRONTEND_DIR'] == str(stage / 'public')
        assert 'HERMES_MOBILE_CONFIG' not in kwargs['env']
        assert 'PYTHONPATH' not in kwargs['env']
        assert kwargs['cwd'] == stage and kwargs['check']
        assert kwargs['umask'] == 0o077
    assert dict(os.environ) == original


def test_production_verifier_uses_real_fixture_http_not_live_services(tmp_path, monkeypatch):
    from functools import partial
    from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
    from threading import Thread
    import os
    module, paths = deploy_fixture(tmp_path)
    stage = module.stage_release(paths.source, tmp_path / 'stage')
    monkeypatch.chdir(stage)
    import shutil
    shutil.copytree(stage / 'frontend', paths.webroot, dirs_exist_ok=True)
    (paths.webroot / 'health.json').write_text('{"status":"ok"}')
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=str(paths.webroot)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = 'http://127.0.0.1:' + str(server.server_port) + '/'
    # Health JSON is not a public frontend asset: serve it in a sibling directory.
    health = paths.webroot / 'health.json'
    health.unlink()
    requests = []
    server.auth_status = 401
    server.landing_status = 200
    class HealthHandler(SimpleHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path.split('?',1)[0]=='/' and server.landing_status!=200:
                self.send_response(server.landing_status)
                self.end_headers()
            elif self.path.startswith('/sessions'):
                self.send_response(server.auth_status)
                self.end_headers()
            elif self.path.startswith('/health'):
                body = b'{"status":"ok"}'
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)
            else:
                super().do_GET()
    server.RequestHandlerClass = partial(HealthHandler, directory=str(paths.webroot))
    import urllib.request
    real_opener = urllib.request.build_opener
    proxy_handlers = []
    def build_opener(*handlers):
        proxy_handlers.extend(h for h in handlers if isinstance(h, urllib.request.ProxyHandler))
        return real_opener(*handlers)
    monkeypatch.setattr(urllib.request, 'build_opener', build_opener)
    try:
        module.verify_release(paths, stage, True, public_url=url, health_url=url + 'health',
                              run=lambda *a, **k: subprocess.CompletedProcess(a, 0, str(os.getpid())))
        assert '/sessions' in requests
        assert proxy_handlers and proxy_handlers[-1].proxies == {}
        server.landing_status = 403
        with pytest.raises(RuntimeError, match='Public HTTP 403: /'):
            module.verify_release(paths, stage, False, public_url=url)
        server.landing_status = 200
        server.auth_status = 200
        with pytest.raises(RuntimeError, match='401'):
            module.verify_release(paths, stage, True, public_url=url, health_url=url + 'health',
                                  run=lambda *a, **k: subprocess.CompletedProcess(a, 0, str(os.getpid())))
        server.auth_status = 401
        with pytest.raises(RuntimeError, match='release'):
            module.verify_release(paths, paths.source, True, public_url=url, health_url=url + 'health',
                                  run=lambda *a, **k: subprocess.CompletedProcess(a, 0, str(os.getpid())))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()



def test_failed_checks_never_touch_gate_or_assets_and_are_reported(tmp_path):
    from backend.runs import RunJournal
    module, paths = deploy_fixture(tmp_path)
    journal = RunJournal(paths.database)
    def checks(stage):
        raise RuntimeError('tests failed')
    with pytest.raises(RuntimeError, match='tests failed'):
        module.deploy(paths, bootstrap=True, checks=checks, verify=lambda *args: None,
                      run=lambda *a, **k: pytest.fail('tests failed: no service calls'))
    assert (paths.webroot / 'index.html').read_text() == '<h1>old</h1>'
    assert journal.submit('u', 'p', 's', 'x', '1')[1]
    assert json.loads((paths.state / 'status.json').read_text())['status'] == 'failed'


def test_staged_code_mutated_during_checks_is_not_published(tmp_path):
    module, paths = deploy_fixture(tmp_path)
    def checks(stage):
        (stage / 'frontend/index.html').write_text('untested')
    with pytest.raises(RuntimeError, match='changed'):
        module.deploy(paths, frontend_only=True, checks=checks, verify=lambda *args: None)
    assert (paths.webroot / 'index.html').read_text() == '<h1>old</h1>'


def test_private_state_symlink_is_refused_without_copying_secrets(tmp_path):
    from dataclasses import replace
    module, paths = deploy_fixture(tmp_path)
    external = tmp_path / 'external'
    external.mkdir()
    paths.state.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match='Symlink'):
        module.deploy(paths, frontend_only=True, checks=lambda stage: None, verify=lambda *args: None)
    assert not list(external.iterdir())


def test_stage_never_copies_nested_runtime_secrets(tmp_path):
    source = source_fixture(tmp_path)
    (source / 'backend/config.json').write_text('SECRET')
    (source / 'backend/.env').write_text('SECRET')
    (source / 'backend/auth.sqlite').write_text('SECRET')
    (source / 'backend/state').mkdir()
    (source / 'backend/state/secret.py').write_text('SECRET')
    stage = controller().stage_release(source, tmp_path / 'release')
    assert not (stage / 'backend/config.json').exists()
    assert not (stage / 'backend/.env').exists()
    assert not (stage / 'backend/auth.sqlite').exists()
    assert not (stage / 'backend/state').exists()



def test_verifier_rejects_self_consistent_wrong_published_bytes_before_http(tmp_path):
    module, paths = deploy_fixture(tmp_path)
    stage = module.stage_release(paths.source, tmp_path / 'stage')
    with pytest.raises(RuntimeError, match='assets'):
        module.verify_release(paths, stage, False, public_url='http://127.0.0.1:1/')


def test_frontend_rollback_verifies_the_backup_assets_without_service_calls(tmp_path):
    module, paths = deploy_fixture(tmp_path)
    verified = []
    def verify(stage, backend, *, assets=None):
        assert not backend
        verified.append(assets)
        if assets is None:
            raise RuntimeError('new public response failed')
        assert (assets / 'index.html').read_text() == '<h1>old</h1>'
        assert (paths.webroot / 'index.html').read_text() == '<h1>old</h1>'
    with pytest.raises(RuntimeError, match='new public response failed'):
        module.deploy(paths, frontend_only=True, checks=lambda stage: None, verify=verify,
                      run=lambda *a, **k: pytest.fail('no systemd'))
    assert len(verified) == 2
