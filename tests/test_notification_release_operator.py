"""Synthetic-only private operator tests; never construct a live NativeProbe."""
import hashlib
import importlib.util
import os
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def sha(data):
    return hashlib.sha256(data).hexdigest()


def private_json(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return sha(path.read_bytes())


def bind_synthetic_approval(op, tmp_path, monkeypatch):
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'backend').mkdir()
    (root / 'backend/app.py').write_text('synthetic = True\n')
    assets = tmp_path / 'assets'
    assets.mkdir()
    (assets / 'index.html').write_text('synthetic assets')
    runtime = tmp_path / 'runtime'
    runtime.write_text('synthetic runtime')
    manifest = tmp_path / 'manifest.json'
    provenance = tmp_path / 'owner.json'
    monkeypatch.setattr(op, 'ROOT', root)
    monkeypatch.setattr(op, 'BASE_ASSETS', assets)
    monkeypatch.setattr(op, 'RUNTIME_REQUIRED', (runtime,))
    monkeypatch.setattr(op, 'PUBLIC_PINS', {runtime: sha(runtime.read_bytes())})
    monkeypatch.setattr(op, 'APPROVED_SOURCE', {'backend/app.py': sha((root / 'backend/app.py').read_bytes())})
    monkeypatch.setattr(op, 'APPROVED_RUNTIME', {str(runtime): sha(runtime.read_bytes())})
    monkeypatch.setattr(op, 'APPROVED_BASE_ASSETS', {'index.html': sha((assets / 'index.html').read_bytes())})
    monkeypatch.setattr(op, 'APPROVED_OPERATOR_SHA256', op.operator_digest())
    monkeypatch.setattr(op, 'APPROVED_MANIFEST_PATH', manifest)
    monkeypatch.setattr(op, 'APPROVED_MANIFEST_SHA256', private_json(manifest, {
        'complete': True, 'expected_count': 1, 'captured_count': 1, 'records': [{'event_id': 'synthetic'}], 'sources': []}))
    monkeypatch.setattr(op, 'APPROVED_PROVENANCE_PATH', provenance)
    monkeypatch.setattr(op, 'APPROVED_PROVENANCE_SHA256', private_json(provenance, {
        'owner_user_id': 'synthetic-owner', 'scope': '["default","/home/lindayi/.hermes"]'}))
    bundle = tmp_path / 'bundle.json'
    monkeypatch.setattr(op, 'APPROVED_BUNDLE_PATH', bundle)
    monkeypatch.setattr(op, 'APPROVED_BUNDLE_SHA256', private_json(bundle, {
        'records': [{'event': {}, 'result': {}, 'source': 'synthetic'}],
        'provenance': {'owner_user_id': 'synthetic-owner', 'home': str(op.HOME),
                       'state_dir': str(op.LIVE), 'expected_count': 1,
                       'records': [{'event_id': 'synthetic'}], 'sources': []}}))
    return root, manifest


def test_frozen_approval_rejects_source_manifest_runtime_and_provenance_drift(tmp_path, monkeypatch):
    op = load_operator()
    root, manifest = bind_synthetic_approval(op, tmp_path, monkeypatch)
    result = op.validate_approval()
    assert result['owner_id'] == 'synthetic-owner'
    for path in (root / 'backend/app.py', manifest, tmp_path / 'runtime', tmp_path / 'owner.json', tmp_path / 'bundle.json'):
        before = path.read_bytes()
        path.write_bytes(before + b' ')
        with pytest.raises(RuntimeError, match='approval'):
            op.validate_approval()
        path.write_bytes(before)
    (root / 'backend/unreviewed.py').write_text('new source')
    with pytest.raises(RuntimeError, match='approval'):
        op.validate_approval()

OPERATOR = Path('/home/lindayi/.local/share/hermes-mobile-notification-repair/notification-release-operator.py')


def load_operator():
    assert OPERATOR.is_file(), 'private notification release operator is missing'
    spec = importlib.util.spec_from_file_location('notification_release_operator', OPERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Operational approval changes must never turn synthetic tests into a release.
    for name in vars(module):
        if name.startswith('APPROVED_'):
            setattr(module,name,None)
    return module


def test_loader_clears_operational_approvals_even_after_operator_is_bound(monkeypatch):
    original=importlib.util.spec_from_file_location
    def approved_spec(*args,**kwargs):
        spec=original(*args,**kwargs);execute=spec.loader.exec_module
        def load_approved(module):
            execute(module)
            for name in vars(module):
                if name.startswith('APPROVED_'):setattr(module,name,'synthetic-bound-approval')
        spec.loader.exec_module=load_approved
        return spec
    monkeypatch.setattr(importlib.util,'spec_from_file_location',approved_spec)
    op=load_operator()
    assert all(value is None for name,value in vars(op).items() if name.startswith('APPROVED_'))
    monkeypatch.setattr(op,'create_operator',lambda:pytest.fail('test reached operational factory'))
    with pytest.raises(RuntimeError,match='Frozen approvals are unset'):op.main(['deploy'])


def test_cli_defaults_to_no_action_and_unset_approval_blocks_modes(monkeypatch):
    op = load_operator()
    assert op.main([]) == 0
    for mode in ('check', 'deploy', 'verify'):
        with pytest.raises(RuntimeError, match='Frozen approvals are unset'):
            op.main([mode])
    with pytest.raises(SystemExit):
        op.main(['deploy', '--manifest', '/tmp/not-an-override'])


class Clock:
    def __init__(self):
        self.value = 0
    def __call__(self):
        return self.value
    def sleep(self, seconds):
        assert seconds > 0
        self.value += seconds


def test_public_verifier_uses_pinned_frontend_for_old_bridge_and_honors_rollback(tmp_path):
    op = load_operator()
    calls = []
    paths = SimpleNamespace(source=tmp_path)
    strict = lambda *args, **kwargs: calls.append((args, kwargs)) or {'verified': True}
    ready = lambda: True
    for stage, assets, expected in (
            (op.BASE, None, op.BASE_ASSETS),
            (tmp_path / 'new', None, None),
            (op.BASE, tmp_path / 'backup/files', tmp_path / 'backup/files')):
        op.public_verify(paths, stage, True, assets=assets, verifier=strict, ready=ready)
        assert calls[-1] == ((paths, stage, True), {'assets': expected})
    assert len(calls) == 3


def test_public_readiness_timeout_never_calls_strict_verifier():
    op = load_operator()
    clock = Clock()
    def forbidden(*args, **kwargs):
        pytest.fail('unready bridge must not reach public verifier')
    with pytest.raises(RuntimeError, match='readiness timeout'):
        op.public_verify(None, op.BASE, True, verifier=forbidden, ready=lambda: False,
                         clock=clock, sleep=clock.sleep, readiness_timeout=2)
    assert clock.value == 2


def test_snapshot_reads_only_named_tables_and_fresh_ready_owner(tmp_path, monkeypatch):
    import sqlite3
    op = load_operator()
    for name, sql in (
        ('native-notifications.sqlite', "CREATE TABLE notification_outbox(event_json TEXT, result_json TEXT); INSERT INTO notification_outbox VALUES('{}','{}');"),
        ('notifications.sqlite', "CREATE TABLE background_receipts(event_json TEXT); INSERT INTO background_receipts VALUES('{}'); CREATE TABLE inbox(body TEXT); INSERT INTO inbox VALUES('synthetic');"),
        ('auth.sqlite', "CREATE TABLE users(id TEXT, role TEXT, profile TEXT, status TEXT); INSERT INTO users VALUES('owner','owner','default','ready');")):
        path = tmp_path / name
        with sqlite3.connect(path) as db:
            db.executescript(sql)
        path.chmod(0o600)
    before = {p.name: sha(p.read_bytes()) for p in tmp_path.iterdir()}
    connect = sqlite3.connect
    statements = []
    def readonly_connect(database_uri, **kwargs):
        assert database_uri.endswith('?mode=ro') and kwargs['uri'] is True
        db = connect(database_uri, **kwargs)
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr(sqlite3, 'connect', readonly_connect)
    snapshot = op.read_snapshot(tmp_path)
    assert snapshot == {'native_records': [{'event_json': '{}', 'result_json': '{}'}],
                        'receipts': [{'event_json': '{}'}], 'inbox': [{'body': 'synthetic'}]}
    assert op.ready_owner_ids(tmp_path) == ['owner']
    assert all(s.startswith(('PRAGMA query_only=ON', 'BEGIN', 'SELECT')) for s in statements)
    assert sum(s == 'BEGIN' for s in statements) == 3
    assert before == {p.name: sha(p.read_bytes()) for p in tmp_path.iterdir()}
    with connect(tmp_path / 'auth.sqlite') as db:
        db.execute("UPDATE users SET status='disabled'")
    assert op.ready_owner_ids(tmp_path) == []
    (tmp_path / 'native-notifications.sqlite').unlink()
    with pytest.raises(OSError):
        op.read_snapshot(tmp_path)
    assert not (tmp_path / 'native-notifications.sqlite').exists()


def delivery_fixture():
    event = dict(type='async_delegation', delegation_id='synthetic', origin_session_id='chat', summary='Synthetic result')
    result = {'summary': 'Synthetic result'}
    canonical = lambda v: json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    event_hash = sha(canonical(event).encode())
    key = 'async:synthetic'
    return dict(
        manifest=dict(complete=True, expected_count=1, captured_count=1, records=[dict(
            event_id=key, payload_sha256=event_hash, result_sha256=sha(canonical(result).encode()))]),
        native_status=dict(pending=0, quarantined=0, conflicts=0, active_workers=0, shutdown_publications=0, foreign=0, foreign_retained=0, delivered=1),
        native_records=[dict(event_id=key, event_json=canonical(event), result_json=canonical(result),
                             payload_sha256=event_hash, historical=1, route='owned', state='delivered', receipt_id='receipt')],
        receipts=[dict(event_id=key, scope='scope', digest=event_hash, event_json=canonical(event), user_id='owner',
                       origin='chat', inbox_id='receipt', acknowledged=1)],
        inbox=[dict(id='receipt', user_id='owner', title='Background result', body='Synthetic result')])


class FakeNative:
    def __init__(self, status):
        self.status = status
        self.pid = 123
        self.started = 456
        self.requests = []
        self.verified = []
    def request(self, path, *, authenticated=True):
        assert authenticated is True
        self.requests.append(path)
        return self.status() if callable(self.status) else self.status
    def attest(self, stage):
        return self.pid
    def _start_ticks(self, pid):
        assert pid == self.pid
        return self.started
    def verify(self, stage):
        self.verified.append(stage)


def proof_case(monkeypatch):
    monkeypatch.syspath_prepend(str(OPERATOR.parent.parent / 'hermes-mobile-notification-ready'))
    from deploy.notification_delivery_proof import verify_delivery
    data = delivery_fixture()
    return data, verify_delivery


def test_pending_status_never_promotes_even_with_complete_persisted_receipts(monkeypatch):
    op = load_operator()
    data, proof = proof_case(monkeypatch)
    native = FakeNative({**data['native_status'], 'pending': 1})
    clock = Clock()
    with pytest.raises(RuntimeError, match='Delivery proof deadline'):
        op.await_delivery(Path('/synthetic-stage'), native=native, manifest=data['manifest'],
                          owner_id='owner', scope='scope', proof=proof,
                          snapshot=lambda: {k: data[k] for k in ('native_records', 'receipts', 'inbox')},
                          owners=lambda: ['owner'], clock=clock, sleep=clock.sleep, timeout=3, interval=1)
    assert clock.value == 3
    assert native.requests == ['/v1/mobile/notifications/status'] * 3
    assert native.verified == []


def test_delivery_polls_until_proof_then_reverifies_native_and_fresh_owner(monkeypatch):
    op = load_operator()
    data, proof = proof_case(monkeypatch)
    clock = Clock()
    native = FakeNative(lambda: {**data['native_status'], 'pending': int(clock.value < 2)})
    owner_reads = []
    def owners():
        owner_reads.append(clock.value)
        return ['owner']
    result = op.await_delivery(Path('/synthetic-stage'), native=native, manifest=data['manifest'],
                              owner_id='owner', scope='scope', proof=proof,
                              snapshot=lambda: {k: data[k] for k in ('native_records', 'receipts', 'inbox')},
                              owners=owners, clock=clock, sleep=clock.sleep, timeout=3, interval=1)
    assert result == {'status': 'delivered', 'verified_count': 1, 'pid': 123, 'start_ticks': 456}
    assert native.verified == [Path('/synthetic-stage')]
    assert owner_reads == [0, 1, 2, 2]


@pytest.mark.parametrize('failure', ['owner-changed', 'pid-changed', 'start-changed', 'slow-proof', 'slow-verify'])
def test_delivery_cannot_promote_changed_identity_or_expired_evidence(monkeypatch, failure):
    op = load_operator()
    data, real_proof = proof_case(monkeypatch)
    native = FakeNative(data['native_status'])
    clock = Clock()
    reads = []
    def owners():
        reads.append(1)
        return ['other'] if failure == 'owner-changed' and len(reads) > 1 else ['owner']
    def proof(**kwargs):
        result = real_proof(**kwargs)
        if failure == 'pid-changed':
            native.pid += 1
        if failure == 'start-changed':
            native.started += 1
        if failure == 'slow-proof':
            clock.value += 4
        return result
    if failure == 'slow-verify':
        native.verify = lambda stage: setattr(clock, 'value', clock.value + 4)
    with pytest.raises(RuntimeError, match='changed|deadline'):
        op.await_delivery(Path('/synthetic-stage'), native=native, manifest=data['manifest'],
                          owner_id='owner', scope='scope', proof=proof,
                          snapshot=lambda: {k: data[k] for k in ('native_records', 'receipts', 'inbox')},
                          owners=owners, clock=clock, sleep=clock.sleep, timeout=3)


@pytest.mark.parametrize('failure', ['503', 'missing-table', 'missing-file'])
def test_transient_unavailability_defers_only_to_bounded_deadline(monkeypatch, failure):
    from urllib.error import HTTPError
    import sqlite3
    op = load_operator()
    data, proof = proof_case(monkeypatch)
    native = FakeNative(data['native_status'])
    clock = Clock()
    def unavailable():
        if failure == '503':
            raise HTTPError('http://synthetic', 503, 'unavailable', {}, None)
        if failure == 'missing-file':
            raise FileNotFoundError('synthetic')
        raise sqlite3.OperationalError('no such table: background_receipts')
    if failure == '503':
        native.status = unavailable
    with pytest.raises(RuntimeError, match='Delivery proof deadline'):
        op.await_delivery(Path('/synthetic-stage'), native=native, manifest=data['manifest'],
                          owner_id='owner', scope='scope', proof=proof, snapshot=unavailable,
                          owners=lambda: ['owner'], clock=clock, sleep=clock.sleep, timeout=2)
    assert clock.value == 2
    assert native.verified == []


def synthetic_operator(op, tmp_path, monkeypatch, *, mutate_checks=False):
    import shutil
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'backend').mkdir()
    (source / 'backend/app.py').write_text('synthetic = True')
    (source / 'frontend').mkdir()
    (source / 'frontend/index.html').write_text('synthetic UI')
    state = tmp_path / 'state'
    (state / 'releases/old').mkdir(parents=True)
    (state / 'current').symlink_to(state / 'releases/old')
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    monkeypatch.setattr(op, 'BASE', state / 'releases/old')
    monkeypatch.setattr(op, 'NATIVE_BASE', state / 'releases/native-old')
    identities = {'bridge': {'pid': 1, 'start_ticks': 10, 'cwd': str(op.BASE)},
                  'native': {'pid': 2, 'start_ticks': 20, 'cwd': str(op.NATIVE_BASE)},
                  'gateway': {'pid': 3, 'start_ticks': 30, 'cwd': '/synthetic-gateway'}}
    events = []
    def stage_release(root, stage):
        events.append('stage')
        shutil.copytree(root, stage)
        return stage
    def build(frontend, public):
        events.append('build')
        shutil.copytree(frontend, public)
    def checks(paths, stage, *, run):
        scratch = Path(os.environ['TMPDIR'])
        assert scratch.is_dir()
        assert run == op.checked_runner
        events.append(('checks', scratch))
        if mutate_checks:
            (stage / 'backend/app.py').write_text('drift')
    bridge = SimpleNamespace(stage_release=stage_release, fingerprints=op.inventory,
                             run_checks=checks, SOURCE_TREES=op.SOURCE_NAMES, SOURCE_FILES=())
    manifest = dict(complete=True, expected_count=1, captured_count=1, records=[], sources={})
    approval = dict(binding={'synthetic': 'frozen'}, owner_id='owner', scope='scope', manifest=manifest,
                    bundle={'records': ['synthetic-record'], 'provenance': {'synthetic': 'reviewed'}})
    native = FakeNative({})
    migration_calls = []
    def migration(state_dir, home, records, *, provenance):
        migration_calls.append((state_dir, home, records, provenance))
        return manifest
    paths = SimpleNamespace(source=source, state=state)
    operator = op.Operator(paths, bridge=bridge, build=build, native=native,
                           verifier=lambda *a, **kw: None, migration=migration, proof=lambda **kw: None,
                           approval=lambda: approval, identities=lambda: json.loads(json.dumps(identities)),
                           private=private, state_dir=tmp_path / 'live', home=tmp_path / 'home')
    return operator, events, identities, approval, migration_calls


def test_check_uses_private_short_scratch_for_chromium_socket_limit(tmp_path, monkeypatch):
    op=load_operator()
    operator,events,identities,approval,migration_calls=synthetic_operator(op,tmp_path,monkeypatch)
    operator.private=tmp_path/('long-private-evidence-'*6)
    operator.private.mkdir(mode=0o700)
    original=operator.bridge.run_checks
    def short_checks(paths,stage,**kwargs):
        scratch=Path(os.environ['TMPDIR'])
        assert len(str(scratch/'com.google.Chrome.abcdef'/'SingletonSocket').encode())<108
        assert scratch.stat().st_mode & 0o777==0o700
        return original(paths,stage,**kwargs)
    operator.bridge.run_checks=short_checks
    operator.check()
    assert not events[2][1].exists()


def test_check_builds_fresh_stage_freezes_tests_and_writes_private_receipt(tmp_path, monkeypatch):
    op = load_operator()
    operator, events, identities, approval, migration_calls = synthetic_operator(op, tmp_path, monkeypatch)
    monkeypatch.setenv('TMPDIR', '/synthetic-prior-tmp')
    receipt = operator.check()
    assert events[:2] == ['stage', 'build']
    scratch = events[2][1]
    assert not scratch.exists()
    assert os.environ['TMPDIR'] == '/synthetic-prior-tmp'
    stage = Path(receipt['stage'])
    assert stage.parent == operator.paths.state / 'releases'
    assert 'public/index.html' in receipt['fingerprints']
    assert receipt['approval'] == approval['binding']
    assert receipt['processes'] == identities
    assert operator.receipt.stat().st_mode & 0o777 == 0o600
    assert json.loads(operator.receipt.read_text()) == receipt
    assert migration_calls == []


def test_failed_or_mutating_checks_never_write_passing_receipt(tmp_path, monkeypatch):
    op = load_operator()
    operator, *_ = synthetic_operator(op, tmp_path, monkeypatch, mutate_checks=True)
    with pytest.raises(RuntimeError, match='changed during checks'):
        operator.check()
    assert not operator.receipt.exists()


def test_check_runner_preserves_candidate_python_and_adds_provider_path(monkeypatch):
    op = load_operator()
    calls = []
    monkeypatch.setattr(op.subprocess, 'run', lambda command, **kw: calls.append((command, kw)))
    op.checked_runner(['/synthetic/.venv/bin/python', '-m', 'pytest', 'tests', '-q'], check=True)
    command, kwargs = calls[0]
    assert command[:2] == ['/synthetic/.venv/bin/python', '-c']
    assert '/usr/local/lib/hermes-agent/venv/lib/python3.11/site-packages' in command[2]
    assert command[3:] == ['tests', '-q']
    assert kwargs == {'check': True}



def install_fake_controller(op, operator, identities, monkeypatch, *, drift=None):
    calls = []
    gate = {'held': False}
    original_migration = operator.migration
    def migration(*args, **kwargs):
        assert gate['held'], 'migration outside controller lock/gate'
        calls.append('migration')
        return original_migration(*args, **kwargs)
    operator.migration = migration
    monkeypatch.setattr(op, 'ready_owner_ids', lambda state: ['owner'])
    def delivery(stage, **kwargs):
        assert gate['held']
        assert kwargs['manifest'] == operator.approval()['manifest']
        calls.append('delivery')
        return dict(status='delivered', verified_count=1, pid=123, start_ticks=456)
    monkeypatch.setattr(op, 'await_delivery', delivery)
    def controller(paths, *, checks, verify, native, probe, handoff):
        calls.append('controller')
        assert paths is operator.paths and native is operator.native
        stage = operator.bridge.stage_release(paths.source, paths.state / 'releases/new')
        operator.build(stage / 'frontend', stage / 'public')
        if drift == 'stage':
            (stage / 'public/index.html').write_text('not-tested')
        checks(stage)
        gate['held'] = True
        try:
            if drift == 'approval':
                operator.approval()['binding']['late-drift'] = True
            handoff(stage)
            calls.append('restart')
            identities['bridge'].update(cwd=str(stage), pid=11)
            identities['native'].update(cwd=str(stage), pid=123, start_ticks=456)
            if drift == 'gateway':
                identities['gateway']['pid'] += 1
            probe(stage)
            (paths.state / 'current').unlink()
            (paths.state / 'current').symlink_to(stage)
            return stage
        finally:
            gate['held'] = False
    operator.controller = controller
    return calls


def test_release_migrates_only_in_controller_handoff_then_proves_before_return(tmp_path, monkeypatch):
    op = load_operator()
    operator, _, identities, approval, migrations = synthetic_operator(op, tmp_path, monkeypatch)
    operator.check()
    calls = install_fake_controller(op, operator, identities, monkeypatch)
    stage = operator.release()
    assert calls == ['controller', 'migration', 'restart', 'delivery']
    assert migrations == [(operator.state_dir, operator.home, approval['bundle']['records'], approval['bundle']['provenance'])]
    handoff = operator.private / ('notification-handoff-' + stage.name + '.json')
    saved = json.loads(handoff.read_text())
    assert saved['manifest'] == approval['manifest']
    assert saved['approval'] == approval['binding']
    assert handoff.stat().st_mode & 0o777 == 0o600
    assert (operator.private / ('notification-delivery-' + stage.name + '.json')).is_file()


@pytest.mark.parametrize('drift', ['source', 'manifest', 'baseline', 'stage', 'approval', 'gateway'])
def test_release_drift_never_promotes(tmp_path, monkeypatch, drift):
    op = load_operator()
    operator, _, identities, approval, migrations = synthetic_operator(op, tmp_path, monkeypatch)
    operator.check()
    calls = install_fake_controller(op, operator, identities, monkeypatch, drift=drift)
    if drift == 'source':
        (operator.paths.source / 'backend/app.py').write_text('unreviewed')
    if drift == 'manifest':
        def changed():
            raise RuntimeError('Frozen approval manifest mismatch')
        operator.approval = changed
    if drift == 'baseline':
        identities['native']['pid'] += 1
    with pytest.raises(RuntimeError):
        operator.release()
    assert (operator.paths.state / 'current').resolve() == op.BASE
    if drift in ('source', 'manifest', 'baseline'):
        assert calls == []
    if drift != 'gateway':
        assert migrations == []
    assert 'delivery' not in calls



def test_verify_rechecks_published_stage_native_public_and_delivery_without_migration(tmp_path, monkeypatch):
    op = load_operator()
    operator, _, identities, approval, migrations = synthetic_operator(op, tmp_path, monkeypatch)
    operator.check()
    install_fake_controller(op, operator, identities, monkeypatch)
    stage = operator.release()
    calls = []
    operator.public = lambda root, backend: calls.append(('public', root, backend))
    def delivery(root, **kwargs):
        calls.append(('delivery', root))
        return dict(status='delivered', verified_count=1, pid=123, start_ticks=456)
    monkeypatch.setattr(op, 'await_delivery', delivery)
    result = operator.verify()
    assert result['verified_count'] == 1
    assert calls == [('public', stage, True), ('delivery', stage)]
    assert operator.native.verified == [stage]
    assert len(migrations) == 1
    identities['gateway']['cwd'] = '/changed'
    with pytest.raises(RuntimeError, match='gateway|identity'):
        operator.verify()


def test_prerequisites_require_unprivileged_systemd_and_exact_interpreter(tmp_path, monkeypatch):
    op = load_operator()
    bind_synthetic_approval(op, tmp_path, monkeypatch)
    monkeypatch.setattr(op.os, 'geteuid', lambda: 1000)
    monkeypatch.delenv('INVOCATION_ID', raising=False)
    with pytest.raises(RuntimeError, match='Separate unprivileged'):
        op.prerequisites()
    monkeypatch.setenv('INVOCATION_ID', 'a' * 32)
    with pytest.raises(RuntimeError, match='candidate interpreter'):
        op.prerequisites()
    monkeypatch.setattr(op.sys, 'executable', str(op.ROOT / '.venv/bin/python'))
    assert op.prerequisites()['owner_id'] == 'synthetic-owner'
    monkeypatch.setattr(op.os, 'geteuid', lambda: 0)
    with pytest.raises(RuntimeError, match='Separate unprivileged'):
        op.prerequisites()


def test_main_dispatches_only_after_prerequisites(monkeypatch, capsys):
    op = load_operator()
    calls = []
    monkeypatch.setattr(op, 'prerequisites', lambda: calls.append('approved'))
    fake = SimpleNamespace(check=lambda: calls.append('check') or {'fingerprints': {}},
                           release=lambda: calls.append('deploy') or Path('/synthetic/release'),
                           verify=lambda: calls.append('verify') or {'verified_count': 1, 'pid': 123})
    monkeypatch.setattr(op, 'create_operator', lambda: calls.append('constructed') or fake)
    for mode in ('check', 'deploy', 'verify'):
        assert op.main([mode]) == 0
    assert calls == ['approved', 'constructed', 'check', 'approved', 'constructed', 'deploy',
                     'approved', 'constructed', 'verify']
    assert 'synthetic-owner' not in capsys.readouterr().out



def test_factory_explicitly_binds_candidate_paths_and_native_probe(monkeypatch):
    import importlib
    op = load_operator()
    calls = []
    bridge = SimpleNamespace(__file__=str(op.ROOT / 'deploy/self_deploy.py'),
                             SOURCE_TREES=op.SOURCE_NAMES, SOURCE_FILES=(), SKIP=op.SKIP,
                             Paths=lambda **kw: calls.append(('paths', kw)) or SimpleNamespace(**kw))
    native = SimpleNamespace(__file__=str(op.ROOT / 'deploy/native_controls_release.py'),
                             NativeProbe=lambda source: calls.append(('native', source)) or FakeNative({}),
                             deploy=lambda *a, **kw: None)
    modules = {'deploy.self_deploy': bridge, 'deploy.native_controls_release': native}
    for name, attribute in (('deploy.frontend_release', 'build_frontend'),
                            ('deploy.notification_migration', 'migrate_records'),
                            ('deploy.notification_delivery_proof', 'verify_delivery')):
        modules[name] = SimpleNamespace(__file__=str(op.ROOT / (name.replace('.', '/') + '.py')),
                                        **{attribute: lambda *a, **kw: None})
    modules['operator_browser_verify'] = SimpleNamespace(__file__=str(op.STATE / 'operator_browser_verify.py'), verify=lambda *a, **kw: None)
    monkeypatch.setattr(importlib, 'import_module', lambda name: modules[name])
    monkeypatch.setattr(op.sys, 'path', op.sys.path[:])
    operator = op.create_operator()
    assert calls == [('paths', {'source': op.ROOT}), ('native', op.ROOT)]
    assert operator.paths.source == op.ROOT
    assert operator.controller is native.deploy


def test_process_identity_reads_only_named_services_and_pid_cwd_start(tmp_path, monkeypatch):
    op = load_operator()
    working = tmp_path / 'working'
    working.mkdir()
    for pid in (1, 2, 3):
        proc = tmp_path / str(pid)
        proc.mkdir()
        (proc / 'cwd').symlink_to(working)
        (proc / 'stat').write_text(str(pid) + ' (synthetic) ' + ' '.join(['S'] + ['0'] * 18 + [str(pid * 10)]))
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert command[:3] == ['systemctl', '--user', 'show']
        assert kwargs['timeout'] == 10
        return SimpleNamespace(stdout=str(len(calls)))
    result = op.process_identities(run=run, proc_root=tmp_path)
    assert result == {key: {'pid': i, 'start_ticks': i * 10, 'cwd': str(working)}
                      for i, key in enumerate(('bridge', 'native', 'gateway'), 1)}
    assert [c[3] for c in calls] == ['hermes-mobile.service', 'hermes-mobile-api.service', 'hermes-gateway.service']



@pytest.mark.parametrize('change', ['owner', 'identity', 'result'])
def test_promotion_final_boundary_rechecks_owner_and_proof_identity(tmp_path, monkeypatch, change):
    op = load_operator()
    operator, _, identities, _, _ = synthetic_operator(op, tmp_path, monkeypatch)
    operator.check()
    install_fake_controller(op, operator, identities, monkeypatch)
    def delivery(stage, **kwargs):
        if change == 'owner':
            monkeypatch.setattr(op, 'ready_owner_ids', lambda state: [])
        if change == 'identity':
            identities['native']['start_ticks'] += 1
        return dict(status='failed' if change == 'result' else 'delivered', verified_count=1, pid=123, start_ticks=456)
    monkeypatch.setattr(op, 'await_delivery', delivery)
    with pytest.raises(RuntimeError, match='owner|identity|proof'):
        operator.release()
    assert not list(operator.private.glob('notification-delivery-*.json'))


def test_entrypoint_never_prints_exception_payload(monkeypatch, capsys):
    op = load_operator()
    def fail():
        raise RuntimeError('PRIVATE_SYNTHETIC_PAYLOAD')
    monkeypatch.setattr(op, 'main', fail)
    assert op.entrypoint() == 1
    output = capsys.readouterr()
    assert output.out == ''
    assert output.err == 'NOTIFICATION_OPERATOR_FAILED\n'
