"""Actual worker entrypoint, default-shaped paths, exclusively synthetic state."""
from dataclasses import replace
import json

import pytest

from deploy import native_controls_release as release
from test_native_notification_release_callbacks import setup_controller_release


@pytest.fixture
def worker(tmp_path, monkeypatch):
    from backend import native_api_service
    import test_native_controls_release
    from test_self_deploy import source_fixture

    home = tmp_path / 'synthetic-home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('INVOCATION_ID', 'synthetic-worker')
    monkeypatch.setattr(release.os, 'geteuid', lambda: 1000)
    # Translate the real Paths defaults, not the erroneous co-located fixture.
    defaults = release.bridge.Paths()
    owner_home = defaults.state.parents[2]
    paths = replace(defaults, **{
        name: home / getattr(defaults, name).relative_to(owner_home)
        for name in ('source', 'state', 'database', 'dropin')
    }, webroot=home / 'public-webroot')
    assert paths.state == home / '.local/share/hermes-mobile-deploy'
    assert paths.database == home / '.local/share/hermes-mobile-live/runs.sqlite'
    source = source_fixture(home)
    paths.source.parent.mkdir(parents=True)
    source.rename(paths.source)
    paths.webroot.mkdir()
    (paths.webroot / 'index.html').write_text('<h1>old</h1>')
    monkeypatch.setattr(test_native_controls_release, 'deploy_fixture',
                        lambda _: (release.bridge, paths))
    app, outbox, native, paths, _, args, old, event = setup_controller_release(
        home, monkeypatch, wire_callbacks=False)
    monkeypatch.setattr(native_api_service, 'OWNER_HOME', app.catalog.profiles['default'])

    def native_probe(source, **kwargs):
        assert source == paths.source
        assert kwargs['run'] is args['run']
        return native  # Only process/service observation is synthetic.

    monkeypatch.setattr(release, 'NativeProbe', native_probe)
    monkeypatch.setattr(release.bridge, 'run_checks',
                        lambda paths, stage, **kw: args['checks'](stage))
    monkeypatch.setattr(release.bridge, 'verify_release',
                        lambda paths, stage, backend, **kw: args['verify'](stage, backend))
    real_deploy = release.deploy
    observed = []

    def deploy(paths, **kwargs):
        observed.append(kwargs['handoff'])
        # Redirect the OS-native dropin default too; run the real transaction.
        return real_deploy(paths, native_dropin=args['native_dropin'], **kwargs)

    monkeypatch.setattr(release, 'deploy', deploy)
    return paths, args, native, old, observed


@pytest.mark.parametrize('default_paths', [True, False])
def test_worker_default_two_root_layout_reaches_real_proofs(worker, monkeypatch, default_paths):
    paths, args, native, old, observed = worker
    if default_paths:
        real_paths = release.bridge.Paths

        def defaults():
            # Only translate main's default selection. Leave production-boundary
            # detection in git_source using its original protected defaults.
            monkeypatch.setattr(release.bridge, 'Paths', real_paths)
            return paths

        monkeypatch.setattr(release.bridge, 'Paths', defaults)
    assert release.main(['--worker'], run=args['run'],
                        **({} if default_paths else {'paths': paths})) == 0
    assert len(observed) == 1
    callbacks = observed[0]
    assert callbacks.runs == paths.database
    assert callbacks.auth == paths.database.parent / 'auth.sqlite'
    assert callbacks.inbox == paths.database.parent / 'notifications.sqlite'
    assert callbacks.outbox == paths.database.parent / 'native-notifications.sqlite'
    assert callbacks.bridge_root == old
    assert callbacks.initial_records and callbacks.handoff_records
    assert callbacks.initial_receipts
    assert json.loads((paths.state / 'status.json').read_text())['status'] == 'succeeded'
    assert (paths.state / 'current').resolve() == native.active_root != old
    assert (paths.state / 'deploy.lock').is_file()
    assert not (paths.database.parent / 'current').exists()
    assert not (paths.database.parent / 'deploy.lock').exists()
    for name in ('runs.sqlite', 'auth.sqlite', 'notifications.sqlite', 'native-notifications.sqlite'):
        assert not (paths.state / name).exists()


@pytest.mark.parametrize('bad_binding', [
    'foreign-journal', 'wrong-journal-name', 'controller-config', 'missing-config',
    'relative-config', 'alias-config', 'controller-symlink', 'journal-symlink',
    'journal-parent-symlink', 'journal-traversal',
])
def test_worker_rejects_unbound_roots_before_deployment(worker, monkeypatch, bad_binding):
    paths, args, native, _, _ = worker
    live = paths.database.parent
    foreign = live.parent / 'foreign'
    foreign.mkdir()
    (foreign / 'runs.sqlite').write_bytes(paths.database.read_bytes())
    if bad_binding == 'foreign-journal':
        paths = replace(paths, database=foreign / 'runs.sqlite')
    elif bad_binding == 'wrong-journal-name':
        paths = replace(paths, database=live / 'foreign.sqlite')
    elif bad_binding == 'controller-config':
        native.config_bytes = json.dumps({'state_dir': str(paths.state)}).encode()
    elif bad_binding == 'missing-config':
        native.config_bytes = b'{}'
    elif bad_binding == 'relative-config':
        native.config_bytes = json.dumps({'state_dir': live.name}).encode()
    elif bad_binding == 'alias-config':
        native.config_bytes = json.dumps({'state_dir': str(live) + '/.'}).encode()
    elif bad_binding == 'controller-symlink':
        alias = paths.state.with_name('controller-alias')
        alias.symlink_to(paths.state, target_is_directory=True)
        paths = replace(paths, state=alias)
    elif bad_binding == 'journal-symlink':
        paths.database.unlink()
        paths.database.symlink_to(foreign / 'runs.sqlite')
    elif bad_binding == 'journal-parent-symlink':
        alias = live.with_name('live-alias')
        alias.symlink_to(live, target_is_directory=True)
        paths = replace(paths, database=alias / 'runs.sqlite')
    else:
        paths = replace(paths, database=live / '..' / live.name / 'runs.sqlite')
    calls = []
    monkeypatch.setattr(release, 'deploy', lambda *a, **kw: calls.append('deployment'))
    with pytest.raises(RuntimeError, match='binding|outbox path'):
        release.main(['--worker'], paths=paths, run=args['run'])
    assert calls == []
