"""Offline source-attested notification rollout; no live service/state access."""
import copy
import json

import pytest

from deploy import native_controls_release as release
from test_native_delete_rollout import versions, bound_native, activate_candidate


def test_new_source_capture_idle_and_verify_allow_preserved_backlog_before_delivery(bound_native):
    probe, root, health, caps = activate_candidate(bound_native)
    notices = health['native_maintenance']['notifications']
    notices.update(backlog=80, durable_retained=80, unpreserved=0,
                   web_pending=78, quarantined=2, foreign_retained=3, web_delivered=0)
    baseline = probe.capture(bound_native[2], False)
    assert baseline['source_hashes'] == release.APPROVED_CONTROL_HASHES
    assert probe.idle(None, baseline)
    probe.verify(root)
    health['native_maintenance']['work']['notification_workers'] = 1
    assert not probe.idle(None, baseline)
    with pytest.raises(RuntimeError, match='verification failed'):
        probe.verify(root)


def test_new_source_cannot_downgrade_to_old_notification_payload(bound_native):
    probe, root, health, caps = activate_candidate(bound_native)
    del caps['mobile_notifications']
    del health['native_maintenance']['work']['notification_workers']
    for key in ('web_pending', 'quarantined', 'foreign_retained', 'web_delivered'):
        del health['native_maintenance']['notifications'][key]
    with pytest.raises(RuntimeError, match='capabilities|maintenance'):
        probe.capture(bound_native[2], False)
    with pytest.raises(RuntimeError, match='verification failed'):
        probe.verify(root)


@pytest.mark.parametrize('change', ['missing-notifications', 'extra-candidate', 'missing-previous', 'extra-previous'])
def test_even_matching_approval_maps_must_have_exact_frozen_source_sets(versions, monkeypatch, change):
    from backend import model_controls
    old, new, previous, candidate = versions
    previous, candidate = dict(previous), dict(candidate)
    if change == 'missing-notifications':
        del candidate['backend/native_notifications.py']
    elif change == 'extra-candidate':
        candidate['backend/unchecked.py'] = candidate['backend/native_notifications.py']
        (new / 'backend/unchecked.py').write_bytes(b'notifications')
    elif change == 'missing-previous':
        del previous['backend/native_maintenance.py']
        (old / 'backend/native_maintenance.py').unlink()
    else:
        previous['backend/unchecked.py'] = candidate['backend/native_notifications.py']
        (old / 'backend/unchecked.py').write_bytes(b'notifications')
    monkeypatch.setattr(model_controls, '_CONTROL_HASHES', candidate)
    monkeypatch.setattr(model_controls, '_PREVIOUS_CONTROL_HASHES', previous)
    monkeypatch.setattr(release, 'APPROVED_CONTROL_HASHES', candidate)
    monkeypatch.setattr(release, 'PREVIOUS_CONTROL_HASHES', previous)
    root = old if 'previous' in change else new
    with pytest.raises(RuntimeError, match='approved'):
        release.attested_controls(root)


@pytest.mark.parametrize('change', [None, 'worker', 'unpreserved', 'missing-capability', 'missing-counter'])
def test_initial_startup_waits_for_preservation_not_unstarted_bridge_delivery(bound_native, change):
    probe, root, health, caps = activate_candidate(bound_native)
    health['native_maintenance']['notifications'].update(
        backlog=80, durable_retained=80, unpreserved=0, web_pending=80)
    if change == 'worker':
        health['native_maintenance']['work']['notification_workers'] = 1
    elif change == 'unpreserved':
        health['native_maintenance']['notifications'].update(durable_retained=79, unpreserved=1, web_pending=79)
    elif change == 'missing-capability':
        del caps['mobile_notifications']
    elif change == 'missing-counter':
        del health['native_maintenance']['notifications']['web_delivered']
    if change is None:
        probe.verify_initial(root)
    else:
        with pytest.raises(RuntimeError, match='verification failed'):
            probe.verify_initial(root)


@pytest.fixture
def release_transaction(tmp_path, monkeypatch):
    from test_native_controls_release import approved_fixture_hashes, fixture
    # Reuse the real staging/journal/rollback transaction with only its OS
    # service and source bytes supplied by the existing synthetic fixture.
    approved_fixture_hashes.__wrapped__(monkeypatch)
    return fixture(tmp_path)


def test_notification_helper_is_allowed_only_inside_attested_native_release(release_transaction, monkeypatch):
    paths, old, journal, dropin, events, args = release_transaction
    monkeypatch.setattr(release.bridge, 'PROTECTED', (*release.bridge.PROTECTED, 'backend/native_notifications.py'))
    stage = release.deploy(paths, **args)
    assert (stage / 'backend/native_notifications.py').read_bytes() == b'notifications'
    assert (paths.state / 'current').resolve() == stage
    assert ('native', stage) in events


@pytest.mark.parametrize('probe', [None, False, 'not-callable'])
def test_notification_publication_requires_explicit_receipt_postverification(release_transaction, probe):
    paths, old, journal, dropin, events, args = release_transaction
    args['probe'] = probe
    with pytest.raises(RuntimeError, match='receipt verification'):
        release.deploy(paths, **args)
    assert (paths.state / 'current').resolve() == old
    assert not any(event[0] in ('capture', 'command') for event in events)
    assert not events
    with journal.connect() as db:
        assert not db.execute('SELECT 1 FROM deployment_gate').fetchall()


def test_notification_publication_requires_explicit_handoff_before_checks(release_transaction):
    paths, old, journal, dropin, events, args = release_transaction
    args.pop('handoff', None)
    with pytest.raises(RuntimeError, match='handoff'):
        release.deploy(paths, **args)
    assert (paths.state / 'current').resolve() == old
    assert not events  # No checks, capture, public verification or service command.
    assert not dropin.exists()
    assert (paths.webroot / 'index.html').read_bytes() == (old / 'public/index.html').read_bytes()
    with journal.connect() as db:
        assert not db.execute('SELECT 1 FROM deployment_gate').fetchall()


def test_worker_cli_wires_guarded_notification_callbacks(release_transaction, monkeypatch):
    paths, _, _, _, _, _ = release_transaction
    from deploy import native_notification_release
    monkeypatch.setattr(release.os, 'geteuid', lambda: 1000)
    monkeypatch.setenv('INVOCATION_ID', 'synthetic-invocation')
    monkeypatch.setattr(release, 'NativeProbe', lambda *a, **kw: object())
    def callbacks(stage):
        return None
    callbacks.probe = lambda stage: None
    monkeypatch.setattr(native_notification_release, 'NativeNotificationCallbacks',
                        lambda *a, **kw: callbacks)
    captured = {}

    def deploy(_paths, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(release, 'deploy', deploy)
    assert release.main(['--worker'], paths=paths) == 0
    assert callable(captured.get('handoff'))
    assert callable(captured.get('probe'))


def test_notification_baseline_capture_runs_under_owned_gate_before_drain(release_transaction):
    import fcntl
    paths, old, journal, dropin, events, args = release_transaction

    class Handoff:
        def capture(self, baseline):
            assert baseline['gate_owner']
            with (paths.state / 'deploy.lock').open('a') as lock:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with journal.connect() as db:
                assert [tuple(row) for row in db.execute(
                    'SELECT singleton,owner FROM deployment_gate')] == [(1, baseline['gate_owner'])]
            events.append(('notification-capture', old))

        def __call__(self, stage):
            events.append(('handoff', stage))

    native = args['native']
    original_idle = native.idle

    def idle(*a, **kw):
        events.append(('idle', old))
        return original_idle(*a, **kw)

    native.idle = idle
    args.update(handoff=Handoff(), probe=lambda stage: None)
    release.deploy(paths, **args)
    assert events.index(('notification-capture', old)) < events.index(('idle', old))
    handoff_index = next(i for i, event in enumerate(events) if event[0] == 'handoff')
    assert events.index(('idle', old)) < handoff_index


@pytest.mark.parametrize('callback', ['handoff', 'probe'])
def test_false_notification_callback_result_aborts_release(release_transaction, callback):
    paths, old, journal, _, _, args = release_transaction
    args[callback] = lambda stage: False
    error = ('handoff did not verify' if callback == 'handoff'
             else 'receipt verification did not pass')
    with pytest.raises(RuntimeError, match=error):
        release.deploy(paths, **args)
    assert (paths.state / 'current').resolve() == old
    assert json.loads((paths.state / 'status.json').read_text())['status'] == 'rolled_back'
    with journal.connect() as db:
        assert not db.execute('SELECT 1 FROM deployment_gate').fetchall()


@pytest.mark.parametrize('handoff', [None, False, 'not-callable'])
def test_notification_handoff_must_be_callable_before_checks(release_transaction, handoff):
    paths, old, journal, dropin, events, args = release_transaction
    args['handoff'] = handoff
    with pytest.raises(RuntimeError, match='handoff'):
        release.deploy(paths, **args)
    assert not events
    assert (paths.state / 'current').resolve() == old
    with journal.connect() as db:
        assert not db.execute('SELECT 1 FROM deployment_gate').fetchall()


def test_handoff_runs_once_after_drain_under_owned_gate_before_publication(release_transaction, monkeypatch):
    import fcntl
    import json
    from backend.runs import RunConflict
    from deploy import assets
    paths, old, journal, dropin, events, args = release_transaction
    old_assets = assets._assets(paths.webroot)
    old_dropin = paths.dropin.read_bytes()
    parent, _ = journal.submit('owner', 'default', 'handoff-drain', 'parent', 'handoff-drain')
    with journal.connect() as db:
        db.execute('UPDATE runs SET upstream_id=? WHERE id=?', ('native-parent', parent['id']))
    idle_calls = []
    def idle(observed_journal, baseline, *, required_ids=()):
        assert observed_journal.path == journal.path
        assert required_ids == ('native-parent',)
        assert baseline['root'] == str(old)
        idle_calls.append(baseline['gate_owner'])
        events.append(('idle', old))
        return len(idle_calls) > 1
    args['native'].idle = idle
    def sleep(_):
        assert not any(e[0] in ('handoff', 'publish', 'command') for e in events)
        events.append(('drain', old))
        journal.finish('owner', parent['id'], 'completed')
    def handoff(stage):
        assert len(idle_calls) == 2
        assert (paths.state / 'current').resolve() == old
        assert paths.dropin.read_bytes() == old_dropin
        assert not dropin.exists()
        assert assets._assets(paths.webroot) == old_assets
        assert not any(e[0] in ('publish', 'command') for e in events)
        snapshot = json.loads((paths.state / 'recovery' / stage.name / 'native-rollback.json').read_text())
        assert snapshot['current'] == str(old)
        assert snapshot['native']['gate_owner'] == stage.name
        assert idle_calls == [stage.name, stage.name]
        with (paths.state / 'deploy.lock').open('a') as lock:
            with pytest.raises(BlockingIOError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with journal.connect() as db:
            assert [tuple(row) for row in db.execute('SELECT singleton, owner FROM deployment_gate')] == [(1, stage.name)]
        with pytest.raises(RunConflict):
            journal.submit('owner', 'default', 'handoff-blocked', 'blocked', 'handoff-blocked')
        events.append(('handoff', stage))
    args['handoff'] = handoff
    original_publish = assets.publish_assets
    def publish(*a):
        assert len(idle_calls) == 3
        events.append(('publish', a[0].parent))
        return original_publish(*a)
    monkeypatch.setattr(assets, 'publish_assets', publish)
    stage = release.deploy(paths, sleep=sleep, **args)
    assert events.count(('handoff', stage)) == 1
    assert events.index(('handoff', stage)) < events.index(('publish', stage))
    assert events.index(('publish', stage)) < next(i for i, e in enumerate(events) if e[0] == 'command')
    assert events.index(('native', stage)) < events.index(('receipt-probe', stage))
    with journal.connect() as db:
        assert not db.execute('SELECT 1 FROM deployment_gate').fetchall()


@pytest.mark.parametrize('activity', ['busy', 'identity-drift'])
def test_new_native_activity_after_handoff_aborts_before_publication(release_transaction, monkeypatch, activity):
    import json
    from deploy import assets
    paths, old, journal, dropin, events, args = release_transaction
    saved_assets = assets._assets(paths.webroot)
    saved_dropin = paths.dropin.read_bytes()
    observed = []
    def handoff(stage):
        observed.append(stage)
    def idle(observed_journal, baseline, *, required_ids=()):
        events.append(('idle', old))
        if not observed:
            return True
        assert (paths.state / 'current').resolve() == old
        if activity == 'identity-drift':
            raise RuntimeError('native identity drift after handoff')
        return False
    args.update(handoff=handoff)
    args['native'].idle = idle
    original_publish = assets.publish_assets
    def publish(*a):
        events.append(('publish', a[0].parent))
        return original_publish(*a)
    monkeypatch.setattr(assets, 'publish_assets', publish)
    with pytest.raises(RuntimeError, match='after handoff'):
        release.deploy(paths, **args)
    assert len(observed) == 1
    assert events.count(('idle', old)) == 2
    assert not any(e[0] in ('publish', 'command', 'receipt-probe', 'native') for e in events)
    assert events.count(('unchanged-native', old)) == 1
    assert events.count(('bridge', old)) == 2
    assert (paths.state / 'current').resolve() == old
    assert paths.dropin.read_bytes() == saved_dropin
    assert not dropin.exists()
    assert assets._assets(paths.webroot) == saved_assets
    assert json.loads((paths.state / 'status.json').read_text())['status'] == 'rolled_back'
    with journal.connect() as db:
        assert not db.execute('SELECT 1 FROM deployment_gate').fetchall()


@pytest.mark.parametrize('drift', [None, 'foreign-gate', 'assets', 'native'])
def test_partial_handoff_failure_preserves_baseline_and_only_clears_owned_gate(release_transaction, monkeypatch, drift):
    import json
    import sqlite3
    from deploy import assets
    paths, old, journal, dropin, events, args = release_transaction
    dropin.write_bytes(b'old native dropin\r\n')
    saved_dropins = {path: path.read_bytes() for path in (paths.dropin, dropin)}
    saved_assets = assets._assets(paths.webroot)
    outbox = paths.state / 'private-outbox.sqlite'
    observed = []
    def handoff(stage):
        observed.append(stage)
        # A committed partial private copy must not be discarded on abort.
        with sqlite3.connect(outbox) as db:
            db.execute('CREATE TABLE copied (id INTEGER PRIMARY KEY)')
            db.execute('INSERT INTO copied VALUES (1)')
        raise RuntimeError('partial handoff failed')
    args['handoff'] = handoff
    original_verify = args['verify']
    def verify(*a, **kw):
        original_verify(*a, **kw)
        if not observed:
            return
        if drift == 'foreign-gate':
            with journal.connect() as db:
                db.execute("UPDATE deployment_gate SET owner='foreign'")
        elif drift == 'assets':
            (paths.webroot / 'index.html').write_text('baseline drift')
    args['verify'] = verify
    original_unchanged = args['native'].verify_unchanged
    def unchanged(*a, **kw):
        original_unchanged(*a, **kw)
        if drift == 'native':
            raise RuntimeError('baseline native identity drift')
    args['native'].verify_unchanged = unchanged
    original_publish = assets.publish_assets
    def publish(*a):
        events.append(('publish', a[0].parent))
        return original_publish(*a)
    monkeypatch.setattr(assets, 'publish_assets', publish)
    with pytest.raises(RuntimeError, match='rollback failed' if drift else 'partial handoff failed'):
        release.deploy(paths, **args)
    assert len(observed) == 1
    assert (paths.state / 'current').resolve() == old
    assert {path: path.read_bytes() for path in saved_dropins} == saved_dropins
    if drift != 'assets':
        assert assets._assets(paths.webroot) == saved_assets
    assert not any(e[0] in ('publish', 'command', 'receipt-probe', 'native') for e in events)
    assert events.count(('bridge', old)) == 2
    if drift in (None, 'native'):
        assert events.count(('unchanged-native', old)) == 1
    with sqlite3.connect(outbox) as db:
        assert db.execute('SELECT id FROM copied').fetchall() == [(1,)]
    status = json.loads((paths.state / 'status.json').read_text())
    assert status['status'] == ('rollback_failed' if drift else 'rolled_back')
    assert status['error'] == 'partial handoff failed'
    with journal.connect() as db:
        assert [tuple(row) for row in db.execute('SELECT singleton, owner FROM deployment_gate')] == (
            [(1, 'foreign' if drift == 'foreign-gate' else observed[0].name)] if drift else [])


def test_handoff_source_mutation_is_rejected_immediately_before_idle_recheck(release_transaction, monkeypatch):
    from deploy import assets
    paths, old, journal, dropin, events, args = release_transaction
    observed = []
    saved_assets = assets._assets(paths.webroot)
    saved_dropin = paths.dropin.read_bytes()
    def handoff(stage):
        observed.append(stage)
        (stage / 'backend/native_notifications.py').write_bytes(b'tampered')
    def idle(*a, **kw):
        assert not observed, 'Fingerprint check must precede post-handoff idle check'
        return True
    args.update(handoff=handoff)
    args['native'].idle = idle
    original_publish = assets.publish_assets
    def publish(*a):
        events.append(('publish', a[0].parent))
        return original_publish(*a)
    monkeypatch.setattr(assets, 'publish_assets', publish)
    with pytest.raises(RuntimeError, match='Staged source changed after checks'):
        release.deploy(paths, **args)
    assert len(observed) == 1
    assert (paths.state / 'current').resolve() == old
    assert paths.dropin.read_bytes() == saved_dropin
    assert not dropin.exists()
    assert assets._assets(paths.webroot) == saved_assets
    assert not any(e[0] in ('publish', 'command', 'receipt-probe', 'native') for e in events)
    assert events.count(('unchanged-native', old)) == 1
    with journal.connect() as db:
        assert not db.execute('SELECT 1 FROM deployment_gate').fetchall()


@pytest.mark.parametrize('key', ['notification_workers', 'notification_lifecycle_uncertain', 'web_pending', 'quarantined', 'foreign_retained', 'web_delivered', 'shutdown_publications'])
@pytest.mark.parametrize('value', [None, False, -1, 0.0, '0', 'missing'])
def test_source_selected_readiness_requires_every_typed_notification_counter(bound_native, key, value):
    probe, root, health, caps = activate_candidate(bound_native)
    baseline = copy.deepcopy(probe.capture(bound_native[2], False))
    section = health['native_maintenance']['work' if key in ('notification_workers','notification_lifecycle_uncertain') else 'notifications']
    if value == 'missing':
        del section[key]
    else:
        section[key] = value
    with pytest.raises(RuntimeError, match='maintenance'):
        probe.idle(None, baseline)


@pytest.mark.parametrize('location', ['top', 'features'])
def test_previous_source_capture_rejects_notification_advertisement(bound_native, location):
    probe, root, bridge_root, _, health, caps, _ = bound_native
    target = caps if location == 'top' else caps.setdefault('features', {})
    target['mobile_notifications'] = dict(NOTIFICATIONS)
    with pytest.raises(RuntimeError, match='capabilities'):
        probe.capture(bridge_root, False)


@pytest.mark.parametrize('failed', [False, True])
def test_receipt_verification_runs_after_bridge_activation_under_owned_gate(release_transaction, failed):
    import fcntl
    from backend.runs import RunConflict
    paths, old, journal, dropin, events, args = release_transaction
    observed = []
    def receipts(stage):
        assert (paths.state / 'current').resolve() == stage
        assert ('command', ['systemctl', '--user', 'restart', 'hermes-mobile.service']) in events
        assert ('native', stage) in events and ('bridge', stage) in events
        with (paths.state / 'deploy.lock').open('a') as lock:
            with pytest.raises(BlockingIOError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with journal.connect() as db:
            assert [tuple(row) for row in db.execute('SELECT singleton, owner FROM deployment_gate')] == [(1, stage.name)]
        with pytest.raises(RunConflict):
            journal.submit('owner', 'default', 'notification-gate', 'blocked', 'notification-gate')
        observed.append(stage)
        if failed:
            raise RuntimeError('real receipt proof unavailable')
    args['probe'] = receipts
    if failed:
        with pytest.raises(RuntimeError, match='real receipt proof unavailable'):
            release.deploy(paths, **args)
        assert (paths.state / 'current').resolve() == old
        assert ('native', old) in events and ('bridge', old) in events
    else:
        assert release.deploy(paths, **args) == observed[0]
    assert len(observed) == 1
    with journal.connect() as db:
        assert not db.execute('SELECT 1 FROM deployment_gate').fetchall()


NOTIFICATIONS = dict(version=1, delivery='durable-inbox', automatic_model_wake=False)


def capabilities(notification_version=1, session_delete_version=1):
    caps = {
        'mobile_run_controls': dict(version=1, steering=True, live_commentary=True),
        'mobile_native_maintenance': dict(version=1, scope='dedicated-listener', atomic_drain=False),
        'features': {'mobile_session_delete_version': 1} if session_delete_version else {},
    }
    if notification_version:
        caps['mobile_notifications'] = dict(NOTIFICATIONS)
    return caps


def test_notification_capability_requires_exact_source_selected_contract():
    release.require_controls_capabilities(capabilities(), session_delete_version=1,
                                         notification_version=1)
    for bad in [None, {}, {**NOTIFICATIONS, 'version': True},
                {**NOTIFICATIONS, 'version': 1.0},
                {**NOTIFICATIONS, 'delivery': 'memory'},
                {**NOTIFICATIONS, 'automatic_model_wake': 0},
                {**NOTIFICATIONS, 'extra': True}]:
        caps = capabilities()
        caps['mobile_notifications'] = bad
        with pytest.raises(RuntimeError, match='capabilities'):
            release.require_controls_capabilities(caps, session_delete_version=1,
                                                 notification_version=1)
    caps = capabilities(0)
    caps['features']['mobile_notifications'] = dict(NOTIFICATIONS)
    with pytest.raises(RuntimeError, match='capabilities'):
        release.require_controls_capabilities(caps, session_delete_version=1,
                                             notification_version=1)


def test_previous_sources_reject_unexpected_notification_capability():
    release.require_controls_capabilities(capabilities(0, 0), session_delete_version=0,
                                         notification_version=0)
    for caps in [capabilities(1, 0), capabilities(0, 0)]:
        if 'mobile_notifications' not in caps:
            caps['features']['mobile_notifications'] = dict(NOTIFICATIONS)
        with pytest.raises(RuntimeError, match='capabilities'):
            release.require_controls_capabilities(caps, session_delete_version=0,
                                                 notification_version=0)


@pytest.mark.parametrize('version', [None, True, 1.0, '1', 2, -1])
def test_notification_version_cannot_be_coerced(version):
    with pytest.raises(RuntimeError, match='capabilities'):
        release.require_controls_capabilities(capabilities(), session_delete_version=1,
                                             notification_version=version)
