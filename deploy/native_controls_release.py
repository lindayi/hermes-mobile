"""Authorized dedicated-owner listener maintenance; never the shared gateway.

Safety depends on trusted OS/owner and exclusive bridge ingress, not on an
atomic native drain API or protection against another local bearer holder.
"""
import fcntl
import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid
from contextlib import closing
from . import self_deploy as bridge

NATIVE_DROPIN = Path('/home/lindayi/.config/systemd/user/hermes-mobile-api.service.d/60-native-controls.conf')
NATIVE_PYTHON = '/usr/local/lib/hermes-agent/venv/bin/python'
NATIVE_API = Path('/usr/local/lib/hermes-agent/gateway/platforms/api_server.py')
CONFIG = Path('/home/lindayi/.local/share/hermes-mobile-live/config.json')
PROC_ROOT = Path('/proc')
LEGACY_LAUNCHER = 'a3a28cf5d83688e69e335c816febfe11acfdd72631fff14f4203d97b81e77c22'
INSTALLED_API = '187c92509b3769c04756f0dc800d3597ea891ea21262e8a32ceaf3972ac95300'
NATIVE_DEPENDENCIES = {
    Path('/usr/local/lib/hermes-agent/run_agent.py'): 'fb58e81ac57c0f49370d72146d21250d1cfeaa0b964c9996e972954bbc343609',
    Path('/usr/local/lib/hermes-agent/agent/conversation_loop.py'): '9904134bef009978bf95477a7ba8663421448e3e7735e0ba041ca672dfd33b45',
    Path('/usr/local/lib/hermes-agent/agent/turn_context.py'): '4a65e543a0795c341fd7598ac42a40eb2d87da6637a3521ecc3a00096eb56896',
    Path('/usr/local/lib/hermes-agent/agent/turn_finalizer.py'): '8c6157b55abf936d6f413c3722c919e2a8a0f02439b3c99ba13f6b22063cab7f',
    Path('/usr/local/lib/hermes-agent/hermes_cli/lifecycle.py'): 'e820ed8114d7062d53223113cc053c737d4ced6b624ce1fbe88f79ad8b2a26bd',
    Path('/usr/local/lib/hermes-agent/agent/agent_runtime_helpers.py'): '6634b15aa5ab3d73a0f17e1d77adccbd605e3a3944b0c60adb6d84dedca9ea05',
    Path('/usr/local/lib/hermes-agent/hermes_state.py'): '70c69963f39902bad1b3ed1b943aaa1dc195b986ebd267fe1b0ce49a0c6d6723',
    Path('/usr/local/lib/hermes-agent/tools/process_registry.py'): '5eace294ba298a08ff2a5e0503a75720f5eb2bf9c3117e2588746f207ab915a9',
    Path('/usr/local/lib/hermes-agent/tools/async_delegation.py'): 'eadedad768e3b327bfa9755daf8b0ee00f330ce3f787336986d8b50dad5f9391',
    Path('/usr/local/lib/hermes-agent/tools/daemon_pool.py'): '148a18c801a28f4fb5a96eb20399052245599413a349d7df2fd0b4643ae910dc',
    Path('/usr/local/lib/hermes-agent/tools/approval.py'): '266ce183b1adced097b29df7b2c4a15bde0f86c9a24f6d887339675f4011b903',
    Path('/usr/local/lib/hermes-agent/tools/delegate_tool.py'): '9559ddd8d407cf8d321b8751c714a9f221dd8bd7f09cd016274c5b830940f385',
    Path('/usr/local/lib/hermes-agent/tools/clarify_tool.py'): 'bbd37f211baa1a1fd1e79cf7ce9a553cbdc92459f5436d37ed04ea8d232dbcd4',
}
TERMINAL = {'completed', 'failed', 'cancelled'}


def _full_sha(value):
    return (type(value) is str and len(value) == 40
            and all(char in '0123456789abcdef' for char in value))


APPROVED_CONTROL_HASHES = {
    'backend/native_controls_service.py': 'f0b27766bb923976cc97dccacd54005989f74e026a6ecc2f167817a248ee24ab',
    'backend/native_run_controls.py': 'c904731785c54f79eb9cb2490df96b8aa4dc33704858c50a9c6d16b2b8928456',
    'backend/native_api_service.py':
        'a3a28cf5d83688e69e335c816febfe11acfdd72631fff14f4203d97b81e77c22',
    'backend/native_maintenance.py': 'e79cfee2bf8d32c3f51dd3ee9247e23029376e10c5e55ea21aa373d6d72535c5',
    'backend/native_session_deletion.py': '182246c696c5f409f9d6feafedbcd10278c938ad9b3bc858804ef3d49d15e0f6',
    'backend/native_notifications.py': '230ab537cda34e2f8f497ce92a435b393a2cfc270638f1417213c6bc0a466610',
}

# Exact pre-photo source set, retained for drain/rollback.
PRE_PHOTO_CONTROL_HASHES = {
    'backend/native_controls_service.py': 'f0b27766bb923976cc97dccacd54005989f74e026a6ecc2f167817a248ee24ab',
    'backend/native_run_controls.py': '564f0ab912a1d138f2ac5b1271935ef5aa2c99cdd2ab48d0527363f1a031e65c',
    'backend/native_api_service.py':
        'a3a28cf5d83688e69e335c816febfe11acfdd72631fff14f4203d97b81e77c22',
    'backend/native_maintenance.py': 'e79cfee2bf8d32c3f51dd3ee9247e23029376e10c5e55ea21aa373d6d72535c5',
    'backend/native_session_deletion.py': '182246c696c5f409f9d6feafedbcd10278c938ad9b3bc858804ef3d49d15e0f6',
    'backend/native_notifications.py': '230ab537cda34e2f8f497ce92a435b393a2cfc270638f1417213c6bc0a466610',
}

# Exact pre-clarification source set, retained for drain/rollback.
PRE_CLARIFICATION_CONTROL_HASHES = {
    'backend/native_controls_service.py': 'f0b27766bb923976cc97dccacd54005989f74e026a6ecc2f167817a248ee24ab',
    'backend/native_run_controls.py': '6e3a8796028925ea771bf90b2b97ba1fd7a47cbbd979a71e9be7fb525c129b16',
    'backend/native_api_service.py':
        'a3a28cf5d83688e69e335c816febfe11acfdd72631fff14f4203d97b81e77c22',
    'backend/native_maintenance.py': 'e083b0941b2b849559cd685d946ed10fb14a87128cf8ea2d125f77cb38ce434b',
    'backend/native_session_deletion.py': '182246c696c5f409f9d6feafedbcd10278c938ad9b3bc858804ef3d49d15e0f6',
    'backend/native_notifications.py': '230ab537cda34e2f8f497ce92a435b393a2cfc270638f1417213c6bc0a466610',
}

# Exact immediately prior version, retained for rollback.
PRE_ROUTING_CONTROL_HASHES = {
    'backend/native_controls_service.py': 'f0b27766bb923976cc97dccacd54005989f74e026a6ecc2f167817a248ee24ab',
    'backend/native_run_controls.py': '6e3a8796028925ea771bf90b2b97ba1fd7a47cbbd979a71e9be7fb525c129b16',
    'backend/native_api_service.py':
        'a3a28cf5d83688e69e335c816febfe11acfdd72631fff14f4203d97b81e77c22',
    'backend/native_maintenance.py': 'e083b0941b2b849559cd685d946ed10fb14a87128cf8ea2d125f77cb38ce434b',
    'backend/native_session_deletion.py': '182246c696c5f409f9d6feafedbcd10278c938ad9b3bc858804ef3d49d15e0f6',
    'backend/native_notifications.py': '0159fbdd02705469853f51be7bb32479ea9e2fa0d6fdbc6789253d3b3c1c85fe',
}

# Exact immediate pre-timeout-fix source set, retained for drain/rollback.
TIMEOUT_BASELINE_CONTROL_HASHES = {
    'backend/native_controls_service.py': 'f0b27766bb923976cc97dccacd54005989f74e026a6ecc2f167817a248ee24ab',
    'backend/native_run_controls.py': '5107e54ed631fe2579efe2fb50c6a8ba1e9e3616c4fcd1d0e8ead2f7f29445d9',
    'backend/native_api_service.py':
        'a3a28cf5d83688e69e335c816febfe11acfdd72631fff14f4203d97b81e77c22',
    'backend/native_maintenance.py': 'e083b0941b2b849559cd685d946ed10fb14a87128cf8ea2d125f77cb38ce434b',
    'backend/native_session_deletion.py': '182246c696c5f409f9d6feafedbcd10278c938ad9b3bc858804ef3d49d15e0f6',
    'backend/native_notifications.py': '0159fbdd02705469853f51be7bb32479ea9e2fa0d6fdbc6789253d3b3c1c85fe',
}

PREVIOUS_CONTROL_HASHES = {
    'backend/native_controls_service.py': '1d9a23a567c8896cd1f2c69f9e111e9c9be6297f5b7bfe773426891bd1354969',
    'backend/native_run_controls.py': '5107e54ed631fe2579efe2fb50c6a8ba1e9e3616c4fcd1d0e8ead2f7f29445d9',
    'backend/native_api_service.py':
        'a3a28cf5d83688e69e335c816febfe11acfdd72631fff14f4203d97b81e77c22',
    'backend/native_maintenance.py': '94feb8767f7bbbe5a641ad835712f0468b53e019a478d4887f18f0cfb0ed437a',
}


def _clarification_version(source_hashes):
    for family, version in (
            (APPROVED_CONTROL_HASHES, 1),
            (PRE_PHOTO_CONTROL_HASHES, 1),
            (PRE_CLARIFICATION_CONTROL_HASHES, 0),
            (PRE_ROUTING_CONTROL_HASHES, 0),
            (TIMEOUT_BASELINE_CONTROL_HASHES, 0),
            (PREVIOUS_CONTROL_HASHES, 0)):
        if source_hashes == family:
            return version
    raise RuntimeError('Unknown native control source-version baseline')


def attested_controls(root):
    """Runtime/rollback approval accepts only one complete known source set."""
    from backend.model_controls import (
        _CONTROL_HASHES, _PRE_PHOTO_CONTROL_HASHES, _PRE_CLARIFICATION_CONTROL_HASHES, _PRE_ROUTING_CONTROL_HASHES, _PREVIOUS_CONTROL_HASHES,
        _TIMEOUT_BASELINE_CONTROL_HASHES, _control_source_hashes)
    actual = _control_source_hashes(root)
    if (set(APPROVED_CONTROL_HASHES) != {
                'backend/native_controls_service.py', 'backend/native_run_controls.py',
                'backend/native_api_service.py', 'backend/native_maintenance.py',
                'backend/native_session_deletion.py', 'backend/native_notifications.py'}
            or set(PRE_ROUTING_CONTROL_HASHES) != set(APPROVED_CONTROL_HASHES)
            or set(PRE_CLARIFICATION_CONTROL_HASHES) != set(APPROVED_CONTROL_HASHES)
            or set(PREVIOUS_CONTROL_HASHES) != {
                'backend/native_controls_service.py', 'backend/native_run_controls.py',
                'backend/native_api_service.py', 'backend/native_maintenance.py'}
            or _CONTROL_HASHES != APPROVED_CONTROL_HASHES
            or _PRE_PHOTO_CONTROL_HASHES != PRE_PHOTO_CONTROL_HASHES
            or set(PRE_PHOTO_CONTROL_HASHES) != set(APPROVED_CONTROL_HASHES)
            or _PRE_ROUTING_CONTROL_HASHES != PRE_ROUTING_CONTROL_HASHES
            or _PRE_CLARIFICATION_CONTROL_HASHES != PRE_CLARIFICATION_CONTROL_HASHES
            or _PREVIOUS_CONTROL_HASHES != PREVIOUS_CONTROL_HASHES
            or set(TIMEOUT_BASELINE_CONTROL_HASHES) != set(APPROVED_CONTROL_HASHES)
            or _TIMEOUT_BASELINE_CONTROL_HASHES != TIMEOUT_BASELINE_CONTROL_HASHES
            or actual not in (APPROVED_CONTROL_HASHES, PRE_PHOTO_CONTROL_HASHES, PRE_CLARIFICATION_CONTROL_HASHES,
                              PRE_ROUTING_CONTROL_HASHES,
                              TIMEOUT_BASELINE_CONTROL_HASHES, PREVIOUS_CONTROL_HASHES)):
        raise RuntimeError('Native controls do not match approved source version')
    return actual


def approved_controls(root):
    try:
        tree = ast.parse((root / 'backend/model_controls.py').read_bytes())
        for name, expected in (('_CONTROL_HASHES', APPROVED_CONTROL_HASHES),
                               ('_PRE_PHOTO_CONTROL_HASHES', PRE_PHOTO_CONTROL_HASHES),
                               ('_PRE_CLARIFICATION_CONTROL_HASHES', PRE_CLARIFICATION_CONTROL_HASHES),
                               ('_PRE_ROUTING_CONTROL_HASHES', PRE_ROUTING_CONTROL_HASHES),
                               ('_PREVIOUS_CONTROL_HASHES', PREVIOUS_CONTROL_HASHES),
                               ('_TIMEOUT_BASELINE_CONTROL_HASHES', TIMEOUT_BASELINE_CONTROL_HASHES)):
            constants = [ast.literal_eval(node.value) for node in tree.body
                         if isinstance(node, ast.Assign) and any(
                             isinstance(t, ast.Name) and t.id == name for t in node.targets)]
            if constants != [expected]:
                raise ValueError()
        actual = attested_controls(root)
        if actual != APPROVED_CONTROL_HASHES:
            raise ValueError()
    except (OSError, SyntaxError, ValueError, TypeError):
        raise RuntimeError('Native controls do not match approved hashes/constants') from None
    return actual


def require_controls_capabilities(caps, *, session_delete_version, notification_version=0,
                                  clarification_version=0):
    """Capabilities must agree with the independently attested source version."""
    try:
        if (type(notification_version) is not int or notification_version not in (0, 1)
                or type(clarification_version) is not int or clarification_version not in (0, 1)):
            raise ValueError()
        if notification_version == 0:
            if 'mobile_notifications' in caps:
                raise ValueError()
        elif json.dumps(caps['mobile_notifications'], sort_keys=True, allow_nan=False) != json.dumps(
                dict(version=1, delivery='durable-inbox', automatic_model_wake=False), sort_keys=True):
            raise ValueError()
        run_controls = dict(version=1, steering=True, live_commentary=True)
        if clarification_version:
            run_controls['clarifications'] = True
        for name, expected in (
                ('mobile_run_controls', run_controls),
                ('mobile_native_maintenance', dict(version=1, scope='dedicated-listener', atomic_drain=False))):
            if json.dumps(caps[name], sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True):
                raise ValueError()
        features = caps.get('features', {})
        if not isinstance(features, dict) or 'mobile_notifications' in features:
            raise ValueError()
        if session_delete_version == 0:
            if 'mobile_session_delete_version' in features:
                raise ValueError()
        else:
            version = features.get('mobile_session_delete_version')
            if type(version) is not int or version != 1:
                raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise RuntimeError('Native controls capabilities do not match source version') from None


def deploy(paths, *, checks, verify, native, native_dropin=NATIVE_DROPIN,
           run=subprocess.run, sleep=time.sleep, idle_timeout=1800,
           bootstrap_dedicated_native=False, probe=None, handoff=None,
           rollback_verify=None, hosted_run_id=None, local_full_checks=False,
           expected_source_sha=None):
    """One lock and one candidate, with owner-gated verified rollback.

    Notification candidates require synchronous callbacks:
    handoff(stage) proves durable retention in the existing private outbox after
    drain and before publication/restarts; it never mutates SDK source or registry.
    probe(stage) proves delivery receipts after activation.
    rollback_verify(old, baseline) proves captured notification evidence before
    either abort path reopens admission.
    All callbacks run under the same deployment lock and owned admission gate.
    Capture (when supplied), handoff, probe and rollback verification must return
    literal True only after completing their proof; no-op/truthy values fail closed.
    """
    if (type(local_full_checks) is not bool
            or (hosted_run_id is None) == (not local_full_checks)
            or (hosted_run_id is not None
                and (type(hosted_run_id) is not int or hosted_run_id <= 0))
            or (expected_source_sha is not None and not _full_sha(expected_source_sha))
            or (hosted_run_id is not None and expected_source_sha is None)
            or (local_full_checks and not callable(checks))):
        raise ValueError('Select exactly one valid native validation mode')
    from .git_source import preflight
    git_sha = preflight(paths, service_run=run, extra_paths=(native_dropin,))
    if expected_source_sha is not None and git_sha != expected_source_sha:
        raise RuntimeError('Source no longer matches scheduled source SHA')
    from .assets import checked_path, publish_assets, restore_assets, _assets
    from backend.runs import RunJournal, RunConflict
    for path in (paths.source, paths.state, paths.webroot, paths.database, paths.dropin,
                 native_dropin, *(paths.state / n for n in
                 ('deploy.lock', 'status.json', 'releases', 'backups', 'recovery'))):
        checked_path(path)
    current = paths.state / 'current'
    if not current.is_symlink():
        raise RuntimeError('Existing immutable bridge baseline required')
    old = current.resolve(strict=True)
    if old.parent != paths.state / 'releases' or not old.is_dir():
        raise RuntimeError('Invalid current release binding')
    checked_path(old)
    if not paths.database.is_file():
        raise RuntimeError('Existing journal database required')
    paths.state.mkdir(parents=True, exist_ok=True, mode=0o700)
    paths.state.chmod(0o700)
    with (paths.state / 'deploy.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('A deployment is already running') from None
        status = paths.state / 'status.json'
        prior = json.loads(status.read_text()) if status.exists() else {}
        if prior.get('status') == 'rollback_failed':
            raise RuntimeError('Failed rollback requires operator recovery; gate remains closed')
        release_id = uuid.uuid4().hex
        hosted_status = {}
        validation_mode = 'hosted-artifact' if hosted_run_id is not None else 'local-full-checks'
        def report(state, **extra):
            record = dict(status=state, release=release_id, git_sha=git_sha,
                          validation_mode=validation_mode, **hosted_status)
            record.update(extra)
            bridge.atomic_write(status, json.dumps(record))
        report('running')
        try:
            stage = bridge.stage_release(paths.source, paths.state / 'releases' / release_id,
                                         git_sha=git_sha)
            candidate_hashes = approved_controls(stage)
            if 'backend/native_notifications.py' in candidate_hashes and not callable(probe):
                raise RuntimeError('Notification release requires operator delivery receipt verification')
            if 'backend/native_notifications.py' in candidate_hashes and not callable(handoff):
                raise RuntimeError('Notification release requires operator pre-restart handoff')
            if 'backend/native_notifications.py' in candidate_hashes and not callable(rollback_verify):
                raise RuntimeError('Notification release requires rollback preservation verification')
            protected = tuple(n for n in bridge.PROTECTED if n not in
                              ('backend/native_controls_service.py', 'backend/native_run_controls.py',
                               'backend/native_maintenance.py', 'backend/native_session_deletion.py',
                               'backend/native_notifications.py'))
            if bridge.fingerprints(stage, protected) != bridge.fingerprints(old, protected):
                raise RuntimeError('Unsupported protected dependency/native change')
            for name in ('backend/native_controls_service.py', 'backend/native_run_controls.py'):
                if not (stage / name).is_file():
                    raise RuntimeError('Native controls candidate incomplete')
            if hosted_run_id is None:
                from .frontend_release import build_frontend
                build_frontend(stage / 'frontend', stage / 'public')
            else:
                import tempfile
                from .release_artifact import acquire_verified_bundle
                with tempfile.TemporaryDirectory(prefix='verified-', dir=paths.state) as temporary:
                    evidence = acquire_verified_bundle(
                        paths.source, hosted_run_id, Path(temporary) / 'bundle', run=run)
                    hosted_status = bridge.stage_verified_bundle(
                        stage, evidence, git_sha, hosted_run_id)
                report('running')
            names = (*bridge.SOURCE_TREES, *bridge.SOURCE_FILES, 'public')
            frozen = bridge.fingerprints(stage, names)
            if hosted_run_id is None:
                checks(stage)
            else:
                bridge.run_host_checks(paths, stage, run=run)
            if bridge.fingerprints(stage, names) != frozen:
                raise RuntimeError('Staged source changed during checks')
            # Strict existing public verifier BEFORE admission or service mutation.
            saved_assets = _assets(paths.webroot)
            verify(old, True)
            baseline = native.capture(old, bootstrap_dedicated_native)
            baseline['gate_owner'] = release_id
            saved = {str(p): p.read_bytes() if p.exists() else None for p in (paths.dropin, native_dropin)}
            for content in saved.values():
                if content is not None:
                    content.decode('utf-8')  # Reject invalid systemd text before mutation.
            recovery = checked_path(paths.state / 'recovery' / release_id)
            recovery.parent.mkdir(exist_ok=True, mode=0o700)
            recovery.parent.chmod(0o700)
            recovery.mkdir(mode=0o700)
            snapshot = checked_path(recovery / 'native-rollback.json')
            bridge.atomic_write(snapshot, json.dumps(
                dict(current=str(old), dropins={name: None if data is None else
                     base64.b64encode(data).decode('ascii') for name, data in saved.items()},
                     dropin_encoding='base64', native=baseline)))
            snapshot.chmod(0o600)
            journal = RunJournal(paths.database)
            with closing(journal.connect()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT 1 FROM deployment_gate').fetchone():
                    raise RunConflict('A deployment is already in progress')
                db.execute('INSERT INTO deployment_gate VALUES(1,?)', (release_id,))
                draining = tuple(row[0] for row in db.execute(
                    "SELECT id FROM runs WHERE profile='default' AND status NOT IN ('completed','failed','cancelled')"))
        except Exception as error:
            report('failed', error=str(error))
            raise
        backup = paths.state / 'backups' / release_id
        switched = False
        try:
            prepare_handoff = getattr(handoff, 'capture', None)
            if prepare_handoff is not None:
                if not callable(prepare_handoff):
                    raise RuntimeError('Invalid native notification handoff preparation')
                if prepare_handoff(baseline) is not True:
                    raise RuntimeError('Native notification capture did not verify')
            bridge.wait_idle(journal, timeout=idle_timeout, sleep=sleep)
            with closing(journal.connect()) as db:
                required_ids = tuple(dict.fromkeys(
                    row[0] for rid in draining for row in db.execute(
                        'SELECT upstream_id FROM runs WHERE id=? AND upstream_id IS NOT NULL', (rid,))))
            deadline = time.monotonic() + idle_timeout
            while not native.idle(journal, baseline, required_ids=required_ids):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Native idle wait timed out')
                sleep(1)
            if handoff is not None:
                if handoff(stage) is not True:
                    raise RuntimeError('Native notification handoff did not verify')
            if bridge.fingerprints(stage, names) != frozen:
                raise RuntimeError('Staged source changed after checks')
            if handoff is not None and not native.idle(journal, baseline, required_ids=required_ids):
                # Do not wait/retry the copy if work or identity changed after it.
                raise RuntimeError('Native activity changed after handoff')
            publish_assets(stage / 'public', paths.webroot, backup)
            switched = True  # Includes partial pointer/dropin write failures.
            bridge.point_current(current, stage)
            bridge.atomic_write(paths.dropin, '[Service]\nWorkingDirectory=' + str(stage) + '\nNoNewPrivileges=yes\n')
            bridge.atomic_write(native_dropin, '[Service]\nWorkingDirectory=' + str(stage) +
                                '\nExecStart=\nExecStart=' + NATIVE_PYTHON + ' ' +
                                str(stage / 'backend/native_controls_service.py') + '\nNoNewPrivileges=yes\n')
            run(['systemctl', '--user', 'daemon-reload'], check=True)
            for service in ('hermes-mobile-api.service', 'hermes-mobile.service'):
                run(['systemctl', '--user', 'restart', service], check=True)
            native.verify(stage)
            verify(stage, True)
            if probe is not None:
                # Notification candidates require this operator-supplied proof:
                # drain the old backlog to real web receipts before reopening
                # deletion/admission. Native readiness proves retention only.
                if probe(stage) is not True:
                    raise RuntimeError('Native notification receipt verification did not pass')
            if bridge.fingerprints(stage, names) != frozen:
                raise RuntimeError('Staged source changed during activation')
        except Exception as error:
            try:
                if (backup / 'manifest.json').exists():
                    restore_assets(paths.webroot, backup)
                if switched:
                    bridge.point_current(current, old)
                    for name, content in saved.items():
                        if content is None:
                            Path(name).unlink(missing_ok=True)
                        else:
                            target = checked_path(Path(name))
                            temporary = target.with_name(target.name + '.tmp-' + uuid.uuid4().hex)
                            temporary.write_bytes(content)
                            os.replace(temporary, target)
                    run(['systemctl', '--user', 'daemon-reload'], check=True)
                    for service in ('hermes-mobile-api.service', 'hermes-mobile.service'):
                        run(['systemctl', '--user', 'restart', service], check=True)
                if switched:
                    native.verify(Path(baseline['root']), baseline=baseline)
                verify(old, True, **({'assets': backup / 'files'} if (backup / 'manifest.json').exists() else {}))
                if not switched:
                    # Public proof is outside the write transaction. Under the
                    # deployment lock, recheck its exact local asset preimage,
                    # bindings and live native boundary before reopening admission.
                    def check_abort(db):
                        if (not current.is_symlink() or current.resolve(strict=True) != old or any(
                                (Path(name).read_bytes() if checked_path(Path(name)).exists()
                                 else None) != content for name, content in saved.items())
                                or _assets(paths.webroot) != saved_assets):
                            raise RuntimeError('Pre-mutation baseline changed')
                        if [tuple(row) for row in db.execute(
                                'SELECT singleton, owner FROM deployment_gate')] != [(1, release_id)]:
                            raise RuntimeError('Pre-mutation gate ownership changed')
                    with closing(journal.connect()) as db, db:
                        db.execute('BEGIN IMMEDIATE')
                        check_abort(db)
                        # No restart: busy work/notifications are allowed, but
                        # identity, health, capabilities and auth must be unchanged.
                        native.verify_unchanged(Path(baseline['root']), baseline=baseline)
                        check_abort(db)
                        if callable(rollback_verify) and rollback_verify(old, baseline) is not True:
                            raise RuntimeError('Native notification rollback preservation was not verified')
                        check_abort(db)
                        changed = db.execute('DELETE FROM deployment_gate WHERE singleton=1 AND owner=?', (release_id,))
                        if changed.rowcount != 1:
                            raise RuntimeError('Owned pre-mutation gate was not cleared')
                else:
                    if callable(rollback_verify) and rollback_verify(old, baseline) is not True:
                        raise RuntimeError('Native notification rollback preservation was not verified')
                    journal.clear_deployment_gate(release_id)
            except Exception as rollback_error:
                report('rollback_failed', error=str(error), rollback_error=str(rollback_error))
                raise RuntimeError('Native rollback failed; admission gate remains closed') from rollback_error
            report('rolled_back', error=str(error))
            raise
        journal.clear_deployment_gate(release_id)
        report('succeeded')
        return stage



def require_native_quiescence(evidence):
    """Positive, typed known counters; plain health is never idle evidence."""
    try:
        counts = evidence['readiness']['checks']['background_queues']
        values = [counts[k] for k in ('active_api_runs', 'process_completions', 'active_delegations')]
        if counts['status'] != 'ok' or any(type(v) is not int or v < 0 for v in values):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise RuntimeError('Unknown native readiness counts') from None
    return all(v == 0 for v in values)


OPERATIONAL_UNSAFE_WORK = (
    'shutdown_agents', 'stopping_runs', 'cancellation_uncertain',
    'session_deletion_workers', 'notification_lifecycle_uncertain',
)


class NativeProbe:
    """Read-only private native boundary; fixed owner endpoint, no model calls."""
    def __init__(self, source, *, config=CONFIG, run=subprocess.run, legacy_notice_approval=None):
        from backend.native_api_service import _private_file
        self.source, self.config, self.run = source, _private_file(config), run
        self.config_bytes = self.config.read_bytes()
        settings = json.loads(self.config_bytes)
        if (settings.get('upstream_url') != 'http://127.0.0.1:18642'
                or 'hermes_home' in settings or 'port' in settings
                or not isinstance(settings.get('upstream_token'), str)
                or len(settings['upstream_token']) < 32):
            raise RuntimeError('Dedicated private default owner endpoint required')
        self.endpoint = settings['upstream_url']
        self.token = settings['upstream_token']
        self.pins = {}
        self.legacy_notice_approval = legacy_notice_approval

    def request(self, path, *, authenticated=True):
        from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        headers = {'Authorization': 'Bearer ' + self.token} if authenticated else {}
        with build_opener(ProxyHandler({}), NoRedirect()).open(
                Request(self.endpoint + path, headers=headers), timeout=5) as response:
            return json.load(response)

    def attest(self, root, *, legacy=False):
        """OS/service manager trust; not a claim about in-memory Python bytes."""
        if self.config.read_bytes() != self.config_bytes:
            raise RuntimeError('Private native configuration changed')
        if any(hashlib.sha256(path.read_bytes()).hexdigest() != digest for path, digest in NATIVE_DEPENDENCIES.items()):
            raise RuntimeError('Unknown installed native dependency')
        if hashlib.sha256(NATIVE_API.read_bytes()).hexdigest() != INSTALLED_API:
            raise RuntimeError('Unknown installed native implementation')
        entry = 'native_api_service.py' if legacy else 'native_controls_service.py'
        launcher = root / 'backend' / entry
        if legacy:
            if hashlib.sha256(launcher.read_bytes()).hexdigest() != LEGACY_LAUNCHER:
                raise RuntimeError('Unknown legacy launcher')
        else:
            from backend.model_controls import _runtime_root_allowed, _OWNER_RELEASES
            if root.parent != _OWNER_RELEASES or not _runtime_root_allowed(root):
                raise RuntimeError('Unknown immutable native release root')
            attested_controls(root)
        pid = self.run(['systemctl', '--user', 'show', 'hermes-mobile-api.service',
                        '--property=MainPID', '--value'], check=True, capture_output=True, text=True).stdout.strip()
        if not pid.isdigit() or int(pid) <= 0:
            raise RuntimeError('Unknown native PID')
        proc = PROC_ROOT / pid
        if (proc.stat().st_uid != os.getuid() or (proc / 'cwd').resolve() != root
                or (proc / 'cmdline').read_bytes().split(b'\0')[:-1] !=
                [NATIVE_PYTHON.encode(), str(launcher).encode()]):
            raise RuntimeError('Unknown native process binding')
        env = dict(item.split(b'=', 1) for item in (proc / 'environ').read_bytes().split(b'\0') if b'=' in item)
        if (env.get(b'HERMES_MOBILE_CONFIG') != str(self.config).encode()
                or env.get(b'HERMES_HOME') != b'/home/lindayi/.hermes'):
            raise RuntimeError('Unknown native owner/config binding')
        sockets = {os.readlink(p) for p in (proc / 'fd').iterdir()}
        listeners = []
        for table in ('tcp', 'tcp6'):
            for line in (PROC_ROOT / 'net' / table).read_text().splitlines()[1:]:
                fields = line.split()
                if fields[3] == '0A' and fields[1].endswith(':48D2'):
                    listeners.append((table, fields[1], 'socket:[' + fields[9] + ']' in sockets))
        if listeners != [('tcp', '0100007F:48D2', True)]:
            raise RuntimeError('Unknown/private native socket binding')
        return int(pid)

    @staticmethod
    def _start_ticks(pid):
        value = int((PROC_ROOT / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[19])
        if value <= 0:
            raise RuntimeError('Unknown native process start identity')
        return value

    def _bridge_pid(self, root):
        pid = self.run(['systemctl', '--user', 'show', 'hermes-mobile.service',
                        '--property=MainPID', '--value'], check=True, capture_output=True,
                       text=True, timeout=10).stdout.strip()
        if (not pid.isdigit() or int(pid) <= 0 or (PROC_ROOT / pid).stat().st_uid != os.getuid()
                or (PROC_ROOT / pid / 'cwd').resolve() != Path(root)):
            raise RuntimeError('Unknown bridge ingress process binding')
        return int(pid)

    def _ready(self, health, *, baseline=None, journal=None, root=None):
        from .native_readiness import require_native_readiness, require_legacy_bootstrap, observe_process
        pid = health.get('pid')
        if baseline is None or not baseline['legacy']:
            if baseline is None and root is None:
                raise RuntimeError('Native readiness requires attested source root')
            hashes = attested_controls(Path(baseline['root']) if baseline is not None else root)
            if baseline is not None and hashes != baseline.get('source_hashes'):
                raise RuntimeError('Native baseline source identity changed')
            return require_native_readiness(health, expected_pid=pid,
                expected_start_ticks=self._start_ticks(pid),
                session_delete_version=int('backend/native_session_deletion.py' in hashes),
                notification_version=int('backend/native_notifications.py' in hashes))
        if journal is None:
            raise RuntimeError('Legacy bootstrap requires an owned admission gate')
        with closing(journal.connect()) as db:
            gates = db.execute('SELECT owner FROM deployment_gate').fetchall()
            nonterminal = db.execute("SELECT count(*) FROM runs WHERE status IS NULL OR status NOT IN ('completed','failed','cancelled')").fetchone()[0]
        if len(gates) != 1 or gates[0][0] != baseline['gate_owner']:
            raise RuntimeError('Legacy bootstrap gate ownership changed')
        peer = self._bridge_pid(baseline['bridge_root'])
        if peer != baseline['bridge_pid']:
            raise RuntimeError('Legacy bridge ingress PID changed')
        process = observe_process(pid, trusted_peer_pid=peer, allow_closed_tcp=True)
        accepted = None
        approval = getattr(self, 'legacy_notice_approval', None)
        if approval is not None:
            if approval['pid'] != pid or approval['start_ticks'] != baseline['start_ticks']:
                raise RuntimeError('Legacy restart approval process identity changed')
            accepted = approval['backlog']
        return require_legacy_bootstrap(health, expected_pid=baseline['pid'],
            expected_start_ticks=baseline['start_ticks'], exclusive_ingress=baseline['bootstrap'],
            gate_owner=gates[0][0], expected_gate_owner=baseline['gate_owner'],
            local_nonterminal_runs=nonterminal, required_statuses=[],
            implementation_attested=True, process=process, accepted_backlog=accepted)

    def capture(self, baseline, bootstrap):
        from urllib.error import HTTPError
        observed = self.run(['systemctl', '--user', 'show', 'hermes-mobile-api.service',
                             '--property=MainPID', '--value'], check=True, capture_output=True,
                            text=True, timeout=10).stdout.strip()
        if not observed.isdigit() or int(observed) <= 0:
            raise RuntimeError('Unknown native PID')
        root = (PROC_ROOT / observed / 'cwd').resolve(strict=True)
        command = (PROC_ROOT / observed / 'cmdline').read_bytes().split(b'\0')[:-1]
        legacy = command == [NATIVE_PYTHON.encode(),
                             str(self.source / 'backend/native_api_service.py').encode()]
        if legacy and not bootstrap:
            raise RuntimeError('Explicit --bootstrap-dedicated-native required')
        if legacy and hashlib.sha256((baseline / 'backend/native_api_service.py').read_bytes()).hexdigest() != LEGACY_LAUNCHER:
            raise RuntimeError('Unknown immutable legacy launcher baseline')
        pid = self.attest(root, legacy=legacy)
        if pid != int(observed):
            raise RuntimeError('Native PID changed during capture')
        started = self._start_ticks(pid)
        source_hashes = ({'backend/native_api_service.py': LEGACY_LAUNCHER} if legacy
                         else attested_controls(root))
        try:
            self.request('/health/detailed', authenticated=False)
        except HTTPError as error:
            if error.code != 401:
                raise RuntimeError('Native anonymous boundary failed') from error
        else:
            raise RuntimeError('Native endpoint must reject anonymous requests')
        health = self.request('/health/detailed')
        require_native_quiescence(health)  # Busy is allowed until the gate/drain.
        if health.get('pid') != pid:
            raise RuntimeError('Native health PID mismatch')
        caps = self.request('/v1/capabilities')
        if not isinstance(caps, dict) or (legacy and 'mobile_run_controls' in caps):
            raise RuntimeError('Unknown legacy capabilities')
        if not legacy:
            require_controls_capabilities(caps,
                session_delete_version=int('backend/native_session_deletion.py' in source_hashes),
                notification_version=int('backend/native_notifications.py' in source_hashes),
                clarification_version=_clarification_version(source_hashes))
        captured = dict(root=str(root), pid=pid, legacy=legacy, caps=caps, source_hashes=source_hashes,
                        start_ticks=started, bootstrap=bootstrap,
                        bridge_root=str(baseline), bridge_pid=self._bridge_pid(baseline))
        self.verify_unchanged(root, baseline=captured)
        return captured

    def idle(self, journal, baseline, *, required_ids=()):
        from urllib.parse import quote
        if not baseline['legacy']:
            self.verify_unchanged(Path(baseline['root']), baseline=baseline)
        pid = self.attest(Path(baseline['root']), legacy=baseline['legacy'])
        if pid != baseline['pid']:
            raise RuntimeError('Native PID changed during drain')
        evidence = self.request('/health/detailed')
        if evidence.get('pid') != pid:
            raise RuntimeError('Native health PID mismatch')
        idle = self._ready(evidence, baseline=baseline, journal=journal)
        # Historical terminal journal entries outlive native's bounded cache.
        # Only runs active at gate acquisition require a fresh terminal response.
        for run_id in required_ids:
            status = self.request('/v1/runs/' + quote(run_id, safe='')).get('status')
            if status not in TERMINAL | {
                    'queued', 'running', 'stopping', 'waiting_for_approval',
                    'waiting_for_clarification'}:
                raise RuntimeError('Unknown native journal run status')
            idle = status in TERMINAL and idle
        return idle

    @staticmethod
    def _require_operational_activity(health):
        work = health['native_maintenance']['work']
        notifications = health['native_maintenance']['notifications']
        if notifications['unpreserved'] != 0:
            raise RuntimeError('Native notifications are not fully preserved')
        if any(work[name] != 0 for name in OPERATIONAL_UNSAFE_WORK):
            raise RuntimeError('Native lifecycle is not safe for observation')

    def verify_operational(self, root):
        """Verify a healthy running release without requiring maintenance idle."""
        pid = self.attest(root)
        source_hashes = approved_controls(root)
        started = self._start_ticks(pid)
        health = self.request('/health/detailed')
        if (health.get('status') != 'ok' or type(health.get('pid')) is not int
                or health['pid'] != pid):
            raise RuntimeError('Native operational health or PID mismatch')
        self._ready(health, root=root)
        self._require_operational_activity(health)
        caps = self.request('/v1/capabilities')
        require_controls_capabilities(
            caps, session_delete_version=int('backend/native_session_deletion.py' in source_hashes),
            notification_version=int('backend/native_notifications.py' in source_hashes),
            clarification_version=_clarification_version(source_hashes))
        self.verify_unchanged(root, baseline=dict(
            root=str(root), legacy=False, caps=caps, source_hashes=source_hashes,
            pid=pid, start_ticks=started), operational=True)

    def verify_unchanged(self, root, *, baseline, operational=False):
        """Abort before restart: identity/health/auth, not a drain of live work."""
        from urllib.error import HTTPError
        pid = self.attest(root, legacy=baseline['legacy'])
        if not baseline['legacy']:
            if root != Path(baseline['root']):
                raise RuntimeError('Native baseline root changed')
            if 'start_ticks' not in baseline:
                raise RuntimeError('Missing native process start identity')
        def check_start():
            # Older fixtures/snapshots predate this field. New capture() always
            # includes it; a present but malformed identity is never ignored.
            if 'start_ticks' in baseline:
                expected = baseline['start_ticks']
                actual = self._start_ticks(pid)
                if (type(expected) is not int or expected <= 0
                        or type(actual) is not int or actual != expected):
                    raise RuntimeError('Unchanged native process start identity mismatch')
        check_start()
        health = self.request('/health/detailed')
        if (type(pid) is not int or type(baseline['pid']) is not int
                or pid != baseline['pid'] or type(health.get('pid')) is not int
                or health.get('pid') != pid):
            raise RuntimeError('Unchanged native PID mismatch')
        if health.get('status') != 'ok':
            raise RuntimeError('Unchanged native health is not healthy')
        require_native_quiescence(health)  # Validate typed known evidence, permit busy.
        caps = self.request('/v1/capabilities')
        if not baseline['legacy']:
            self._ready(health, baseline=baseline)  # Validate schema/source, permit busy.
            source_hashes = baseline.get('source_hashes')
            if not isinstance(source_hashes, dict):
                raise RuntimeError('Missing native source-version baseline')
            require_controls_capabilities(
                caps,
                session_delete_version=int('backend/native_session_deletion.py' in source_hashes),
                notification_version=int('backend/native_notifications.py' in source_hashes),
                clarification_version=_clarification_version(source_hashes))
            if operational:
                self._require_operational_activity(health)
        if json.dumps(caps, sort_keys=True, allow_nan=False) != json.dumps(
                baseline['caps'], sort_keys=True, allow_nan=False):
            raise RuntimeError('Unchanged native capabilities differ')
        try:
            self.request('/health/detailed', authenticated=False)
        except HTTPError as error:
            if error.code != 401:
                raise RuntimeError('Native anonymous boundary failed') from error
        else:
            raise RuntimeError('Native endpoint must reject anonymous requests')
        check_start()

    def verify_initial(self, root):
        """Native startup before bridge activation: preservation, not web drain.

        The old bridge has no consumer. Requiring web_pending == 0 here would
        deadlock the upgrade. Neither this nor verify() authorizes publication
        of deletion: the owned deployment gate must remain held through the
        operator's post-bridge backlog/real-receipt verification.
        """
        return self.verify(root)

    def verify(self, root, *, baseline=None):
        legacy = baseline is not None and baseline['legacy']
        last = None
        for attempt in range(30):
            try:
                pid = self.attest(root, legacy=legacy)
                if baseline is None:
                    source_hashes = approved_controls(root)
                started = self._start_ticks(pid)
                health = self.request('/health/detailed')
                if health.get('pid') != pid or (not legacy and not self._ready(health, baseline=baseline, root=root)):
                    raise RuntimeError('Native verification not idle or PID mismatch')
                if legacy:
                    # Restoring the old service is not another drain/restart.
                    self.verify_unchanged(root, baseline={**baseline, 'pid': pid,
                                                         'start_ticks': started})
                    counts = health['readiness']['checks']['background_queues']
                    if counts['active_api_runs'] or counts['active_delegations']:
                        raise RuntimeError('Restored legacy native has active work')
                caps = self.request('/v1/capabilities')
                if baseline is not None:
                    if not legacy:
                        source_hashes = baseline.get('source_hashes')
                        if not isinstance(source_hashes, dict):
                            raise RuntimeError('Missing native source-version baseline')
                        require_controls_capabilities(
                            caps,
                            session_delete_version=int(
                                'backend/native_session_deletion.py' in source_hashes),
                            notification_version=int(
                                'backend/native_notifications.py' in source_hashes),
                            clarification_version=_clarification_version(source_hashes))
                    if json.dumps(caps, sort_keys=True, allow_nan=False) != json.dumps(
                            baseline['caps'], sort_keys=True, allow_nan=False):
                        raise RuntimeError('Restored native capabilities differ')
                else:
                    require_controls_capabilities(caps,
                        session_delete_version=int('backend/native_session_deletion.py' in source_hashes),
                        notification_version=int('backend/native_notifications.py' in source_hashes),
                        clarification_version=_clarification_version(source_hashes))
                if not legacy:
                    expected = baseline if baseline is not None else dict(
                        root=str(root), legacy=False, caps=caps, source_hashes=APPROVED_CONTROL_HASHES)
                    self.verify_unchanged(root, baseline={**expected, 'pid': pid, 'start_ticks': started})
                return
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                last = error
                if attempt < 29:
                    time.sleep(1)
        raise RuntimeError('Native verification failed') from last


def main(argv=None, *, paths=None, run=subprocess.run):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--schedule', action='store_true')
    modes.add_argument('--worker', action='store_true')
    validation = parser.add_mutually_exclusive_group(required=True)
    validation.add_argument('--hosted-run-id', type=int, action='append',
                            help='Verify and deploy the exact current-main hosted release bundle')
    validation.add_argument('--local-full-checks', action='store_true',
                            help='Explicit diagnostic mode: rebuild assets and run the complete local suite')
    parser.add_argument('--expected-source-sha', action='append',
                        help='Worker-only source SHA captured by the scheduler')
    parser.add_argument('--bootstrap-dedicated-native', action='store_true',
                        help='Authorized exclusive-bridge-ingress maintenance bootstrap')
    parser.add_argument('--legacy-restart-approval', type=Path,
                        help='Private PID/count/backup-bound explicit notice-loss approval')
    args = parser.parse_args(argv)
    if args.hosted_run_id is not None:
        if len(args.hosted_run_id) != 1 or args.hosted_run_id[0] <= 0:
            parser.error('--hosted-run-id must be supplied once as a positive integer')
        args.hosted_run_id = args.hosted_run_id[0]
    if args.expected_source_sha is not None:
        if len(args.expected_source_sha) != 1 or not _full_sha(args.expected_source_sha[0]):
            parser.error('--expected-source-sha must be supplied once as a full lowercase Git SHA')
        args.expected_source_sha = args.expected_source_sha[0]
    if args.schedule and args.expected_source_sha is not None:
        parser.error('--expected-source-sha is captured by --schedule, not supplied')
    if args.worker and args.hosted_run_id is not None and args.expected_source_sha is None:
        parser.error('Hosted workers require the source SHA captured by --schedule')
    if args.legacy_restart_approval and not args.bootstrap_dedicated_native:
        parser.error('Legacy notice-loss approval requires explicit bootstrap')
    paths = paths or bridge.Paths()
    if os.geteuid() == 0:
        raise RuntimeError('Run as application owner, never root')
    if args.schedule:
        from .git_source import preflight
        expected_source_sha = preflight(paths, service_run=run)
        if args.hosted_run_id is not None and not _full_sha(expected_source_sha):
            raise RuntimeError('Hosted scheduling requires a verified full source SHA')
        from .assets import checked_path
        checked_path(paths.state)
        paths.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        paths.state.chmod(0o700)
        unit = 'hermes-native-controls-' + uuid.uuid4().hex
        expected = paths.state / (unit + '-expected.json')
        names = (*bridge.SOURCE_TREES, *bridge.SOURCE_FILES)
        bridge.atomic_write(expected, json.dumps(bridge.fingerprints(paths.source, names)))
        since = str(time.time_ns())
        common = ['systemd-run', '--user', '--collect', '--property=NoNewPrivileges=yes',
                  '--property=UMask=0077', '--property=WorkingDirectory=' + str(paths.source)]
        python = str(paths.source / '.venv/bin/python')
        # Durable observer exists before the worker can change admission.
        run([*common, '--unit=' + unit + '-observer', '--property=RuntimeMaxSec=5700',
             python, '-m', 'deploy.observe_release', '--since-ns', since,
             '--expected', str(expected), '--unit', unit, '--timeout', '5640', '--watch-worker', '--native', '--notify-owner'], check=True)
        run([*common, '--unit=' + unit, '--on-active=5s', '--property=RuntimeMaxSec=5400',
             python, '-m', 'deploy.native_controls_release', '--worker',
             *(('--hosted-run-id', str(args.hosted_run_id)) if args.hosted_run_id is not None
               else ('--local-full-checks',)),
             *(('--expected-source-sha', expected_source_sha)
               if _full_sha(expected_source_sha) else ()),
             *(['--bootstrap-dedicated-native'] if args.bootstrap_dedicated_native else []),
             *(['--legacy-restart-approval', str(args.legacy_restart_approval)] if args.legacy_restart_approval else [])], check=True)
        print('Scheduled ' + unit + '; journalctl --user -f -u ' + unit + '.service; observer: ' + unit + '-observer')
        return 0
    if not os.environ.get('INVOCATION_ID'):
        raise RuntimeError('Worker requires a separate systemd invocation')
    from .native_readiness import load_legacy_notice_approval
    approval = load_legacy_notice_approval(args.legacy_restart_approval) if args.legacy_restart_approval else None
    native = NativeProbe(paths.source, run=run, legacy_notice_approval=approval)
    from backend.native_api_service import OWNER_HOME
    from .native_notification_release import NativeNotificationCallbacks
    # The private scratch proof lives exactly as long as this deploy, including
    # its rollback verification, and is deleted on every exit path.
    with NativeNotificationCallbacks(paths, native, home=OWNER_HOME) as notifications:
        deploy(paths, checks=lambda stage: bridge.run_checks(paths, stage, run=run),
               verify=lambda stage, backend, **kw: bridge.verify_release(paths, stage, backend, run=run, **kw),
               native=native, run=run, bootstrap_dedicated_native=args.bootstrap_dedicated_native,
               handoff=notifications, probe=notifications.probe,
               rollback_verify=notifications.verify_rollback,
               hosted_run_id=args.hosted_run_id, local_full_checks=args.local_full_checks,
               expected_source_sha=args.expected_source_sha)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
