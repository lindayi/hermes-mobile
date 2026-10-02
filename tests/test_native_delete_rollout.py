"""Offline rollout contracts; never acquire live gates or operate live services."""
import hashlib
from pathlib import Path
import shutil

import pytest
from backend import model_controls as controls
from deploy import native_controls_release as release


def test_deletion_storage_implementation_is_an_attested_dependency():
    storage = Path('/usr/local/lib/hermes-agent/hermes_state.py')
    supported = '70c69963f39902bad1b3ed1b943aaa1dc195b986ebd267fe1b0ce49a0c6d6723'
    assert hashlib.sha256(storage.read_bytes()).hexdigest() == supported
    assert release.NATIVE_DEPENDENCIES.get(storage) == supported


@pytest.fixture
def versions(tmp_path, monkeypatch):
    old = {'backend/native_controls_service.py': b'old entry',
           'backend/native_run_controls.py': b'run', 'backend/native_api_service.py': b'api',
           'backend/native_maintenance.py': b'old maintenance'}
    new = {**old, 'backend/native_controls_service.py': b'new entry',
           'backend/native_maintenance.py': b'new maintenance',
           'backend/native_session_deletion.py': b'delete',
           'backend/native_notifications.py': b'notifications'}
    roots, maps = [], []
    for label, files in [('previous', old), ('candidate', new)]:
        root = tmp_path / label
        (root / 'backend').mkdir(parents=True)
        for name, content in files.items():
            (root / name).write_bytes(content)
        roots.append(root)
        maps.append({name: hashlib.sha256(content).hexdigest() for name, content in files.items()})
    monkeypatch.setattr(controls, '_PREVIOUS_CONTROL_HASHES', maps[0])
    monkeypatch.setattr(controls, '_CONTROL_HASHES', maps[1])
    monkeypatch.setattr(release, 'PREVIOUS_CONTROL_HASHES', maps[0])
    monkeypatch.setattr(release, 'APPROVED_CONTROL_HASHES', maps[1])
    for root in roots:
        (root / 'backend/model_controls.py').write_text(
            '_CONTROL_HASHES = ' + repr(maps[1])
            + '\n_PRE_ROUTING_CONTROL_HASHES = ' + repr(release.PRE_ROUTING_CONTROL_HASHES)
            + '\n_PREVIOUS_CONTROL_HASHES = ' + repr(maps[0])
            + '\n_TIMEOUT_BASELINE_CONTROL_HASHES = ' + repr(release.TIMEOUT_BASELINE_CONTROL_HASHES))
    return (*roots, *maps)


def test_whole_previous_sources_remain_attested_for_continued_controls(versions):
    old, new, previous, candidate = versions
    assert controls._control_sources_match(old)
    assert controls._control_sources_match(new)
    assert release.attested_controls(old) == previous
    assert release.attested_controls(new) == candidate
    assert release.approved_controls(new) == candidate
    with pytest.raises(RuntimeError, match='approved'):
        release.approved_controls(old)


@pytest.mark.parametrize('change', ['old-helper', 'old-dangling-helper', 'mixed', 'missing', 'aliased'])
def test_unknown_or_mixed_sources_are_not_a_trusted_version(versions, change):
    old, new, previous, candidate = versions
    root = old if change.startswith('old-') else new
    helper = root / 'backend/native_session_deletion.py'
    if change == 'old-helper':
        helper.write_bytes(b'delete')
    elif change == 'old-dangling-helper':
        helper.symlink_to(root / 'missing')
    elif change == 'mixed':
        shutil.copyfile(old / 'backend/native_maintenance.py', new / 'backend/native_maintenance.py')
    elif change == 'missing':
        helper.unlink()
    else:
        helper.unlink()
        helper.symlink_to(old / 'backend/native_api_service.py')
    assert not controls._control_sources_match(root)
    with pytest.raises(RuntimeError, match='approved'):
        release.attested_controls(root)


@pytest.mark.parametrize('constant', ['_CONTROL_HASHES', '_PRE_ROUTING_CONTROL_HASHES',
                                      '_PREVIOUS_CONTROL_HASHES',
                                      '_TIMEOUT_BASELINE_CONTROL_HASHES'])
def test_candidate_requires_all_staged_literal_maps(versions, constant):
    old, new, previous, candidate = versions
    path = new / 'backend/model_controls.py'
    path.write_text(path.read_text() + '\n' + constant + ' = {}\n')
    with pytest.raises(RuntimeError, match='approved'):
        release.approved_controls(new)


@pytest.mark.parametrize('workers,expected', [(0, True), (1, False)])
def test_candidate_readiness_accounts_for_deletion_receipt_workers(workers, expected):
    from deploy.native_readiness import require_native_readiness
    from test_native_readiness import evidence
    health = evidence()
    health['native_maintenance']['work']['session_deletion_workers'] = workers
    assert require_native_readiness(health, expected_pid=123, expected_start_ticks=456) is expected


@pytest.mark.parametrize('workers', [None, False, -1, 0.0, '0'])
def test_candidate_readiness_rejects_unknown_deletion_workers(workers):
    from deploy.native_readiness import require_native_readiness
    from test_native_readiness import evidence
    health = evidence()
    health['native_maintenance']['work']['session_deletion_workers'] = workers
    with pytest.raises(RuntimeError, match='native maintenance'):
        require_native_readiness(health, expected_pid=123, expected_start_ticks=456)


@pytest.mark.parametrize('version', [0, 1])
def test_readiness_never_drops_unknown_work_fields(version):
    from deploy.native_readiness import require_native_readiness
    from test_native_readiness import evidence
    health = evidence()
    if version:
        health['native_maintenance']['work']['session_deletion_workers'] = 0
    health['native_maintenance']['work']['unknown_worker'] = 0
    with pytest.raises(RuntimeError, match='native maintenance'):
        require_native_readiness(health, expected_pid=123, expected_start_ticks=456,
                                 session_delete_version=version)


def test_previous_readiness_requires_explicit_attested_version():
    from deploy.native_readiness import require_native_readiness
    from test_native_readiness import evidence
    assert require_native_readiness(evidence(), expected_pid=123, expected_start_ticks=456,
                                    session_delete_version=0)
    with pytest.raises(RuntimeError, match='native maintenance'):
        require_native_readiness(evidence(), expected_pid=123, expected_start_ticks=456)


@pytest.fixture
def bound_native(versions, tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from urllib.error import HTTPError
    from test_native_readiness import evidence, legacy
    old, new, previous, candidate = versions
    releases = tmp_path / 'releases'
    native_root = releases / ('a' * 32)
    bridge_root = releases / ('b' * 32)
    candidate_root = releases / ('c' * 32)
    shutil.copytree(old, native_root)
    shutil.copytree(old, bridge_root)
    shutil.copytree(new, candidate_root)
    monkeypatch.setattr(controls, '_OWNER_RELEASES', releases)
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'upstream_url': 'http://127.0.0.1:18642', 'upstream_token': 'x' * 32}))
    config.chmod(0o600)
    installed = tmp_path / 'installed.py'
    installed.write_bytes(b'installed')
    monkeypatch.setattr(release, 'NATIVE_DEPENDENCIES', {})
    monkeypatch.setattr(release, 'NATIVE_API', installed)
    monkeypatch.setattr(release, 'INSTALLED_API', hashlib.sha256(installed.read_bytes()).hexdigest())
    proc = tmp_path / 'proc'
    for pid, root in [(123, native_root), (789, bridge_root)]:
        directory = proc / str(pid)
        directory.mkdir(parents=True)
        (directory / 'cwd').symlink_to(root)
        (directory / 'stat').write_text(str(pid) + ' (native worker) S ' + ' '.join(['0'] * 18 + ['456']))
    directory = proc / '123'
    (directory / 'cmdline').write_bytes((release.NATIVE_PYTHON + '\0' + str(native_root / 'backend/native_controls_service.py') + '\0').encode())
    (directory / 'environ').write_bytes(('HERMES_MOBILE_CONFIG=' + str(config) + '\0HERMES_HOME=/home/lindayi/.hermes\0').encode())
    (directory / 'fd').mkdir()
    (directory / 'fd/1').symlink_to('socket:[99]')
    (proc / 'net').mkdir()
    (proc / 'net/tcp').write_text('header\n0: 0100007F:48D2 remote 0A x x x x x 99\n')
    (proc / 'net/tcp6').write_text('header\n')
    monkeypatch.setattr(release, 'PROC_ROOT', proc)
    def run(command, **kw):
        assert command[:3] == ['systemctl', '--user', 'show']
        return SimpleNamespace(stdout='789' if command[3] == 'hermes-mobile.service' else '123')
    probe = release.NativeProbe(new, config=config, run=run)
    health = {**legacy(), **evidence(), 'status': 'ok'}
    caps = {'mobile_run_controls': dict(version=1, steering=True, live_commentary=True),
            'mobile_native_maintenance': dict(version=1, scope='dedicated-listener', atomic_drain=False)}
    def request(path, *, authenticated=True):
        if not authenticated:
            raise HTTPError('private', 401, 'Unauthorized', {}, None)
        return health if path == '/health/detailed' else caps
    probe.request = request
    monkeypatch.setattr(release.time, 'sleep', lambda _: None)
    return probe, native_root, bridge_root, candidate_root, health, caps, proc


def test_capture_attests_actual_immutable_native_root_not_bridge_root(bound_native):
    probe, native_root, bridge_root, _, health, caps, _ = bound_native
    captured = probe.capture(bridge_root, False)
    assert captured == dict(root=str(native_root), pid=123, start_ticks=456, legacy=False,
                           source_hashes=release.PREVIOUS_CONTROL_HASHES, caps=caps,
                           bridge_root=str(bridge_root), bridge_pid=789, bootstrap=False)


def test_previous_baseline_readiness_is_selected_by_attested_source_map(bound_native):
    probe, native_root, bridge_root, _, health, caps, _ = bound_native
    baseline = probe.capture(bridge_root, False)
    assert probe.idle(None, baseline)
    health['native_maintenance']['work']['session_deletion_workers'] = 0
    with pytest.raises(RuntimeError, match='maintenance'):
        probe.idle(None, baseline)


def activate_candidate(bound_native):
    probe, native_root, bridge_root, candidate_root, health, caps, proc = bound_native
    (proc / '123/cwd').unlink()
    (proc / '123/cwd').symlink_to(candidate_root)
    (proc / '123/cmdline').write_bytes((release.NATIVE_PYTHON + '\0' + str(candidate_root / 'backend/native_controls_service.py') + '\0').encode())
    health['native_maintenance']['work']['session_deletion_workers'] = 0
    health['native_maintenance']['work']['notification_workers'] = 0
    health['native_maintenance']['work']['notification_lifecycle_uncertain'] = 0
    health['native_maintenance']['notifications'].update(
        web_pending=0, quarantined=0, foreign_retained=0, web_delivered=0, shutdown_publications=0)
    caps['features'] = {'mobile_session_delete_version': 1}
    caps['mobile_notifications'] = dict(version=1, delivery='durable-inbox', automatic_model_wake=False)
    return probe, candidate_root, health, caps


@pytest.mark.parametrize('version', [None, True, 1.0, '1', 2])
def test_candidate_activation_requires_exact_integer_deletion_version(bound_native, version):
    probe, root, health, caps = activate_candidate(bound_native)
    caps['features']['mobile_session_delete_version'] = version
    with pytest.raises(RuntimeError, match='verification failed'):
        probe.verify(root)


def test_candidate_activation_and_previous_rollback_use_distinct_contracts(bound_native):
    probe, native_root, bridge_root, _, health, caps, _ = bound_native
    baseline = probe.capture(bridge_root, False)
    probe.verify(native_root, baseline=baseline)
    probe, root, health, caps = activate_candidate(bound_native)
    probe.verify(root)


@pytest.mark.parametrize('change', ['outside-release', 'noncanonical-release', 'source-root'])
def test_capture_rejects_unapproved_runtime_roots(bound_native, tmp_path, change):
    probe, native_root, bridge_root, _, health, caps, proc = bound_native
    root = (tmp_path / 'outside' if change == 'outside-release' else
            native_root.parent / 'arbitrary' if change == 'noncanonical-release' else probe.source)
    if root != probe.source:
        shutil.copytree(native_root, root)
    else:
        for name in release.APPROVED_CONTROL_HASHES:
            (root / name).unlink(missing_ok=True)
        for name in release.PREVIOUS_CONTROL_HASHES:
            shutil.copyfile(native_root / name, root / name)
    (proc / '123/cwd').unlink()
    (proc / '123/cwd').symlink_to(root)
    (proc / '123/cmdline').write_bytes((release.NATIVE_PYTHON + '\0' + str(root / 'backend/native_controls_service.py') + '\0').encode())
    with pytest.raises(RuntimeError, match='root'):
        probe.capture(bridge_root, False)


@pytest.mark.parametrize('change', ['start', 'caps', 'source-map'])
def test_idle_rechecks_exact_captured_baseline(bound_native, change):
    import copy
    probe, native_root, bridge_root, _, health, caps, proc = bound_native
    baseline = copy.deepcopy(probe.capture(bridge_root, False))
    if change == 'start':
        (proc / '123/stat').write_text('123 (native worker) S ' + ' '.join(['0'] * 18 + ['999']))
        health['native_maintenance']['start_ticks'] = 999
    elif change == 'caps':
        caps['mobile_run_controls']['version'] = True
    else:
        baseline['source_hashes'] = release.APPROVED_CONTROL_HASHES
    with pytest.raises(RuntimeError):
        probe.idle(None, baseline)


@pytest.mark.parametrize('cap,key,value', [
    ('mobile_run_controls', 'version', True), ('mobile_run_controls', 'steering', 1),
    ('mobile_native_maintenance', 'version', True), ('mobile_native_maintenance', 'scope', 'shared'),
    ('mobile_native_maintenance', 'atomic_drain', 0)])
def test_candidate_activation_retains_typed_steering_and_maintenance_contract(bound_native, cap, key, value):
    probe, root, health, caps = activate_candidate(bound_native)
    caps[cap][key] = value
    with pytest.raises(RuntimeError, match='verification failed'):
        probe.verify(root)


@pytest.mark.parametrize('change', ['caps-type', 'source-map', 'start-missing', 'work-unknown'])
def test_baseline_abort_or_rollback_cannot_relax_saved_identity(bound_native, change):
    import copy
    probe, native_root, bridge_root, _, health, caps, _ = bound_native
    baseline = copy.deepcopy(probe.capture(bridge_root, False))
    if change == 'caps-type':
        caps['mobile_run_controls']['version'] = True
        with pytest.raises(RuntimeError, match='verification failed'):
            probe.verify(native_root, baseline=baseline)
    else:
        if change == 'source-map':
            baseline['source_hashes'] = release.APPROVED_CONTROL_HASHES
        elif change == 'start-missing':
            del baseline['start_ticks']
        else:
            health['native_maintenance']['work']['unknown_worker'] = 0
        with pytest.raises(RuntimeError):
            probe.verify_unchanged(native_root, baseline=baseline)


@pytest.mark.parametrize('change', ['previous-source', 'anonymous', 'changed-start'])
def test_candidate_verification_rechecks_source_auth_and_process(bound_native, change):
    probe, native_root, bridge_root, _, health, caps, proc = bound_native
    if change == 'previous-source':
        root = native_root
        health['native_maintenance']['work']['session_deletion_workers'] = 0
        caps['features'] = {'mobile_session_delete_version': 1}
    else:
        probe, root, health, caps = activate_candidate(bound_native)
        original = probe.request
        calls = []
        def request(path, *, authenticated=True):
            if change == 'anonymous' and not authenticated:
                return health
            if change == 'changed-start' and path == '/v1/capabilities':
                calls.append(True)
                ticks = 456 + len(calls)
                (proc / '123/stat').write_text('123 (native worker) S ' + ' '.join(['0'] * 18 + [str(ticks)]))
                health['native_maintenance']['start_ticks'] = ticks
            return original(path, authenticated=authenticated)
        probe.request = request
    with pytest.raises(RuntimeError, match='verification failed'):
        probe.verify(root)


def test_rollback_requires_saved_native_root_even_with_identical_approved_sources(bound_native):
    probe, native_root, bridge_root, _, health, caps, proc = bound_native
    baseline = probe.capture(bridge_root, False)
    (proc / '123/cwd').unlink()
    (proc / '123/cwd').symlink_to(bridge_root)
    (proc / '123/cmdline').write_bytes((release.NATIVE_PYTHON + '\0' + str(bridge_root / 'backend/native_controls_service.py') + '\0').encode())
    with pytest.raises(RuntimeError, match='verification failed'):
        probe.verify(bridge_root, baseline=baseline)


@pytest.mark.parametrize('change', ['unknown-work', 'old-feature', 'capture-start-race'])
def test_capture_validates_previous_schema_and_identity(bound_native, change):
    probe, native_root, bridge_root, _, health, caps, proc = bound_native
    if change == 'unknown-work':
        health['native_maintenance']['work']['unknown'] = 0
    elif change == 'old-feature':
        caps['features'] = {'mobile_session_delete_version': 1}
    else:
        original = probe.request
        def request(path, **kw):
            if path == '/v1/capabilities':
                (proc / '123/stat').write_text('123 (native worker) S ' + ' '.join(['0'] * 18 + ['999']))
            return original(path, **kw)
        probe.request = request
    with pytest.raises(RuntimeError):
        probe.capture(bridge_root, False)
