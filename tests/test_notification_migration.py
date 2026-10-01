"""Offline migration contract; all databases and source rows are synthetic."""
import copy
import hashlib
import importlib
import importlib.util
import json
import sqlite3

import pytest

from backend.native_notifications import NotificationOutbox, canonical


def migration():
    assert importlib.util.find_spec('deploy.notification_migration') is not None, 'offline migration helper missing'
    return importlib.import_module('deploy.notification_migration')


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@pytest.fixture
def reviewed(tmp_path):
    home, state = tmp_path / 'home', tmp_path / 'bridge'
    home.mkdir()
    state.mkdir(mode=0o700)
    with sqlite3.connect(state / 'auth.sqlite') as db:
        db.execute('CREATE TABLE users(id,role,profile,status)')
        db.execute("INSERT INTO users VALUES('owner','owner','default','ready')")
    with sqlite3.connect(state / 'runs.sqlite') as db:
        db.execute('CREATE TABLE runs(id,user_id,profile,session_id,upstream_id)')
        db.execute("INSERT INTO runs VALUES('run','owner','default','session_a','run_native')")
        db.execute('CREATE TABLE session_deletions(user_id,profile,session_id)')
    with sqlite3.connect(home / 'state.db') as db:
        db.execute('CREATE TABLE sessions(id,parent_session_id,source,profile_name,model_config,end_reason)')
        db.execute("INSERT INTO sessions VALUES('session_a',NULL,'api_server','default','{}',NULL)")
        db.execute('CREATE TABLE async_delegations(delegation_id PRIMARY KEY,event_json,result_json,delivery_state,delivery_claim)')
    records = []
    for n in range(80):
        event = dict(type='async_delegation', delegation_id=f'deleg_{n}',
                     session_key='run_native', origin_session_id='session_a', summary=f'private summary {n}')
        result = {'full': [n, 'private untruncated result' * 100], 'nested': {'unicode': '保留'}}
        records.append(dict(event=event, result=result, source='archived' if n < 31 else 'current'))
    sources = {name: digest([r for r in records if r['source'] == name]) for name in ('current', 'archived')}
    proof = dict(version=1, profile='default', home=str(home), state_dir=str(state),
                 owner_user_id='owner', expected_count=len(records), sources=sources,
                 records=[dict(event_id='async:' + r['event']['delegation_id'],
                               payload_sha256=digest(r['event']), result_sha256=digest(r['result']),
                               source=r['source']) for r in records])
    return home, state, records, proof


def subset(reviewed, size):
    home, state, records, proof = reviewed
    proof = {**proof, 'expected_count': size, 'records': proof['records'][:size]}
    return home, state, records[:size], proof


def seed_source(home, records):
    with sqlite3.connect(home / 'state.db') as db:
        db.executemany('INSERT INTO async_delegations VALUES(?,?,?,?,NULL)',
                       [(r['event']['delegation_id'], canonical(r['event']), canonical(r['result']),
                         'dropped' if r['source'] == 'archived' else 'pending') for r in records])


def source_bytes(home, state):
    return {str(p): p.read_bytes() for p in (home / 'state.db', state / 'auth.sqlite', state / 'runs.sqlite')}


def test_held_dropped_record_is_privately_durable_historical_pending(reviewed):
    home, state, records, proof = subset(reviewed, 1)
    seed_source(home, records)
    before = source_bytes(home, state)
    original = copy.deepcopy((records, proof))
    result = migration().migrate_records(state, home, records, provenance=proof)
    assert result['complete'] is True
    assert result['captured_count'] == result['expected_count'] == 1
    assert result['records'] == [{**proof['records'][0], 'source_sha256': proof['sources']['archived']}]
    assert result['sources'] == proof['sources']
    box = NotificationOutbox(state / 'native-notifications.sqlite')
    saved = box.record('async:deleg_0')
    assert saved['event'] == records[0]['event']
    assert saved['result'] == records[0]['result']
    assert saved['historical'] == 1
    assert saved['state'] == 'pending'
    assert saved['receipt_id'] is None
    assert saved['source_state'] == 'unexamined'
    assert json.loads(saved['provenance']) == {
        **{k: proof[k] for k in ('version', 'profile', 'home', 'state_dir', 'owner_user_id')},
        **result['records'][0],
    }
    assert source_bytes(home, state) == before
    assert (records, proof) == original
    assert (state / 'native-notifications.sqlite').stat().st_mode & 0o077 == 0


def test_persistent_home_binding_rejects_other_home(reviewed, tmp_path):
    home, state, records, proof = subset(reviewed, 1)
    migration().migrate_records(state, home, records, provenance=proof)
    box = NotificationOutbox(state / 'native-notifications.sqlite')
    with box.transaction() as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert 'notification_binding' in tables, 'offline preseed must bind native home'
        assert db.execute('SELECT home FROM notification_binding').fetchone()[0] == str(home)
    other = tmp_path / 'other-home'
    other.mkdir()
    (other / 'state.db').write_bytes((home / 'state.db').read_bytes())
    changed = {**proof, 'home': str(other)}
    before = box.record('async:deleg_0')
    with pytest.raises(ValueError, match='home binding'):
        migration().migrate_records(state, other, records, provenance=changed)
    assert box.record('async:deleg_0') == before


@pytest.mark.parametrize('conflict', ['event', 'result', 'provenance', 'historical', 'missing_result', 'state'])
def test_conflict_preserves_existing_row_reports_partial_handoff(reviewed, conflict):
    home, state, records, proof = subset(reviewed, 2)
    m = migration()
    single = {**proof, 'expected_count': 1, 'records': proof['records'][1:]}
    m.migrate_records(state, home, records[1:], provenance=single)
    box = NotificationOutbox(state / 'native-notifications.sqlite')
    # A pre-existing row from another import cannot be overwritten or relabelled.
    with box.transaction() as db:
        if conflict == 'event':
            changed = {**records[1]['event'], 'summary': 'different private event'}
            db.execute('UPDATE notification_outbox SET event_json=?,payload_sha256=?', (canonical(changed), digest(changed)))
        elif conflict == 'result':
            db.execute('UPDATE notification_outbox SET result_json=?', (canonical({'private': 'different result'}),))
        elif conflict == 'provenance':
            db.execute("UPDATE notification_outbox SET provenance='another-source'")
        elif conflict == 'missing_result':
            db.execute('UPDATE notification_outbox SET result_json=NULL')
        elif conflict == 'state':
            db.execute("UPDATE notification_outbox SET state='quarantined'")
        else:
            db.execute('UPDATE notification_outbox SET historical=0')
    before = box.record('async:deleg_1')
    with pytest.raises(Exception) as caught:
        m.migrate_records(state, home, records, provenance=proof)
    assert hasattr(caught.value, 'manifest'), 'failure must report conservative durable partial progress'
    manifest = caught.value.manifest
    assert manifest['complete'] is False
    assert manifest['expected_count'] == 2
    assert manifest['captured_count'] == 1
    assert [entry['event_id'] for entry in manifest['records']] == ['async:deleg_0']
    assert manifest['failed_event_id'] == 'async:deleg_1'
    assert 'private' not in str(caught.value)
    assert box.record('async:deleg_1') == before
    assert box.record('async:deleg_0')['state'] == 'pending'
    if conflict in ('event', 'result'):
        assert box.status()['conflicts'] == 1


def test_existing_conflict_blocks_even_exact_retry(reviewed):
    from backend.native_notifications import NotificationConflict
    home, state, records, proof = subset(reviewed, 1)
    m = migration()
    m.migrate_records(state, home, records, provenance=proof)
    box = NotificationOutbox(state / 'native-notifications.sqlite')
    with pytest.raises(NotificationConflict):
        box.capture({**records[0]['event'], 'summary': 'conflicting'}, records[0]['result'], route='owned')
    with pytest.raises(m.MigrationIncomplete) as error:
        m.migrate_records(state, home, records, provenance=proof)
    assert error.value.manifest['complete'] is False
    assert error.value.manifest['captured_count'] == 0
    assert box.claim() == []


def test_owner_proof_lost_after_preflight_does_not_capture_quarantined_row(reviewed, monkeypatch):
    home, state, records, proof = subset(reviewed, 1)
    m = migration()
    original = NotificationOutbox.import_records

    def change_owner_before_capture(self, *args, **kwargs):
        with sqlite3.connect(state / 'runs.sqlite') as db:
            db.execute("UPDATE runs SET user_id='other'")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(NotificationOutbox, 'import_records', change_owner_before_capture)
    with pytest.raises(m.MigrationIncomplete) as error:
        m.migrate_records(state, home, records, provenance=proof)
    assert error.value.manifest['captured_count'] == 0
    assert NotificationOutbox(state / 'native-notifications.sqlite').record('async:deleg_0') is None


def test_all_80_survive_source_retention_with_quiet_claims_until_receipt(reviewed, monkeypatch, capsys):
    import builtins
    from backend.native_notifications import NotificationCapture
    home, state, records, proof = reviewed
    seed_source(home, records)
    before = source_bytes(home, state)
    original_import = builtins.__import__

    def no_sdk_import(name, *args, **kwargs):
        assert name.split('.')[0] not in ('tools', 'gateway', 'run_agent'), 'must not initialize SDK'
        return original_import(name, *args, **kwargs)

    def no_live_operation(*args, **kwargs):
        pytest.fail('offline preseed must not install hooks, touch queues, claim SDK delivery, or ACK')

    monkeypatch.setattr(builtins, '__import__', no_sdk_import)
    for name in ('install', 'transfer', 'claim', 'reconcile_queue'):
        monkeypatch.setattr(NotificationCapture, name, no_live_operation)
    m = migration()
    manifest = m.migrate_records(state, home, records, provenance=proof)
    assert manifest['complete'] is True
    assert manifest['captured_count'] == 80
    assert source_bytes(home, state) == before
    assert m.migrate_records(state, home, records, provenance=proof) == manifest
    # Simulate the SDK's 50-row retention, then total source loss. No SDK imports.
    with sqlite3.connect(home / 'state.db') as db:
        assert db.execute("SELECT COUNT(*) FROM async_delegations WHERE delivery_state='dropped'").fetchone()[0] == 31
        assert db.execute("SELECT COUNT(*) FROM async_delegations WHERE delivery_state='pending'").fetchone()[0] == 49
        db.execute('DELETE FROM async_delegations WHERE rowid NOT IN (SELECT rowid FROM async_delegations ORDER BY rowid DESC LIMIT 50)')
        assert db.execute('SELECT COUNT(*) FROM async_delegations').fetchone()[0] == 50
    box = NotificationOutbox(state / 'native-notifications.sqlite')
    assert box.status()['pending'] == 80
    with sqlite3.connect(home / 'state.db') as db:
        db.execute('DELETE FROM async_delegations')
    box = NotificationOutbox(box.path)
    for record, entry in zip(records, manifest['records']):
        saved = box.record(entry['event_id'])
        assert saved['event'] == record['event']
        assert saved['result'] == record['result']
        assert saved['payload_sha256'] == entry['payload_sha256']
        assert saved['state'] == 'pending'
        assert saved['receipt_id'] is None
        assert json.loads(saved['provenance'])['source_sha256'] == proof['sources'][record['source']]
    first, second = box.claim(50), box.claim(50)
    assert len(first) == 50 and len(second) == 30
    assert len({entry['event_id'] for entry in first + second}) == 80
    assert all(entry['historical'] is True for entry in first + second)
    assert all(set(entry) == {'event_id', 'payload_sha256', 'event', 'historical', 'lease_token'} for entry in first + second)
    assert box.status()['pending'] == 80 and box.status()['delivered'] == 0
    lease = first[0]
    box.ack(**{k: lease[k] for k in ('event_id', 'payload_sha256', 'lease_token')}, receipt_id='durable-web-receipt')
    assert m.migrate_records(state, home, records, provenance=proof) == manifest
    assert box.status()['pending'] == 79 and box.status()['delivered'] == 1
    assert box.record(lease['event_id'])['receipt_id'] == 'durable-web-receipt'
    assert 'private' not in canonical(manifest)
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('after_commit', [False, True])
def test_interrupted_capture_reports_partial_and_exact_retry_is_idempotent(reviewed, monkeypatch, after_commit):
    home, state, records, proof = subset(reviewed, 3)
    m = migration()
    original = NotificationOutbox.capture

    def interrupted(self, event, *args, **kwargs):
        if event['delegation_id'] == 'deleg_1':
            if after_commit:
                original(self, event, *args, **kwargs)
            raise sqlite3.OperationalError('private failure details must not be printed')
        return original(self, event, *args, **kwargs)

    monkeypatch.setattr(NotificationOutbox, 'capture', interrupted)
    with pytest.raises(m.MigrationIncomplete) as error:
        m.migrate_records(state, home, records, provenance=proof)
    assert error.value.manifest['complete'] is False
    assert error.value.manifest['captured_count'] == 1
    assert error.value.manifest['failed_event_id'] == 'async:deleg_1'
    assert 'private' not in str(error.value)
    monkeypatch.setattr(NotificationOutbox, 'capture', original)
    result = m.migrate_records(state, home, records, provenance=proof)
    assert result['complete'] is True and result['captured_count'] == 3
    assert NotificationOutbox(state / 'native-notifications.sqlite').status()['pending'] == 3


@pytest.mark.parametrize('bad', [
    'short', 'extra', 'duplicate', 'count', 'bool_count', 'empty', 'payload', 'result_digest',
    'missing_result', 'missing_event', 'source', 'source_hash', 'source_mismatch',
    'event_id', 'non_async', 'no_id', 'nan', 'no_proof',
])
def test_invalid_reviewed_batch_fails_before_outbox_creation(reviewed, bad):
    home, state, records, proof = subset(reviewed, 2)
    if bad == 'short': records.pop()
    elif bad == 'extra': records.append(copy.deepcopy(records[0]))
    elif bad == 'duplicate':
        records[1] = copy.deepcopy(records[0])
        proof['records'][1] = copy.deepcopy(proof['records'][0])
    elif bad == 'count': proof['expected_count'] = 3
    elif bad == 'bool_count': proof['expected_count'] = True
    elif bad == 'empty': records.clear(); proof.update(expected_count=0, records=[])
    elif bad == 'payload': records[1]['event']['summary'] = 'changed private payload'
    elif bad == 'result_digest': records[1]['result']['full'] = ['changed private full result']
    elif bad == 'missing_result': records[1].pop('result')
    elif bad == 'missing_event': records[1].pop('event')
    elif bad == 'source': records[1]['source'] = 'unreviewed'
    elif bad == 'source_hash': proof['sources']['archived'] = 'not-a-hash'
    elif bad == 'source_mismatch': proof['records'][1]['source'] = 'current'
    elif bad == 'event_id': proof['records'][1]['event_id'] = 'async:unapproved'
    elif bad == 'non_async': records[1]['event']['type'] = 'watch_match'
    elif bad == 'no_id': records[1]['event'].pop('delegation_id')
    elif bad == 'nan': records[1]['result']['bad'] = float('nan')
    elif bad == 'no_proof': proof = None
    before = source_bytes(home, state)
    with pytest.raises(ValueError, match='review') as error:
        migration().migrate_records(state, home, records, provenance=proof)
    assert 'private' not in str(error.value)
    assert not (state / 'native-notifications.sqlite').exists()
    assert source_bytes(home, state) == before


@pytest.mark.parametrize('bad', ['profile', 'version', 'home', 'state_dir', 'owner_user_id',
                                 'native_profile', 'upstream', 'user', 'deletion', 'foreign', 'missing_binding'])
def test_owner_proof_required_for_every_record_before_any_capture(reviewed, bad):
    home, state, records, proof = subset(reviewed, 2)
    if bad in ('profile', 'home', 'state_dir', 'owner_user_id'):
        proof[bad] = 'wrong-binding'
    elif bad == 'version': proof['version'] = 2
    elif bad == 'missing_binding': proof.pop('owner_user_id')
    elif bad == 'native_profile':
        with sqlite3.connect(home / 'state.db') as db:
            db.execute("UPDATE sessions SET profile_name='other'")
    elif bad == 'user':
        with sqlite3.connect(state / 'runs.sqlite') as db:
            db.execute("UPDATE runs SET user_id='someone-else'")
    elif bad == 'deletion':
        with sqlite3.connect(state / 'runs.sqlite') as db:
            db.execute("INSERT INTO session_deletions VALUES('owner','default','session_a')")
    else:
        records[1]['event']['session_key'] = 'unknown' if bad == 'upstream' else 'agent:whatsapp:foreign'
        proof['records'][1]['payload_sha256'] = digest(records[1]['event'])
    before = source_bytes(home, state)
    with pytest.raises(ValueError, match='binding|owner'):
        migration().migrate_records(state, home, records, provenance=proof)
    assert not (state / 'native-notifications.sqlite').exists()
    assert source_bytes(home, state) == before


@pytest.mark.parametrize('database,alias', [
    (database, alias)
    for database in ('native', 'auth', 'runs', 'outbox')
    for alias in ('symlink', 'hardlink', 'dangling_symlink', 'directory')
] + [('outbox', 'hardlink-' + source) for source in ('native', 'auth', 'runs')])
def test_database_alias_rejected_before_outbox_constructor_preserves_sources(
        reviewed, tmp_path, monkeypatch, database, alias):
    home, state, records, proof = subset(reviewed, 1)
    seed_source(home, records)
    paths = dict(native=home / 'state.db', auth=state / 'auth.sqlite',
                 runs=state / 'runs.sqlite', outbox=state / 'native-notifications.sqlite')
    originals = {name: path for name, path in paths.items() if name != 'outbox'}
    path = paths[database]
    unexpected = tmp_path / 'unexpected-home'
    unexpected.mkdir()
    backup = unexpected / path.name
    if alias.startswith('hardlink-'):
        source = paths[alias.removeprefix('hardlink-')]
        source.chmod(0o600)  # Private mode must not hide the writable-source bug.
        path.hardlink_to(source)
    else:
        if database == 'outbox':
            NotificationOutbox(path)
        if alias == 'hardlink':
            backup.hardlink_to(path)
        else:
            path.rename(backup)
            if database != 'outbox':
                originals[database] = backup
            if alias == 'symlink':
                path.symlink_to(backup)
            elif alias == 'dangling_symlink':
                path.symlink_to(unexpected / 'missing.sqlite')
            else:
                path.mkdir()
    watched = set(originals.values())
    if backup.is_file():
        watched.add(backup)
    before = {p: p.read_bytes() for p in watched}
    constructors = []
    m = migration()

    def track_constructor(*args, **kwargs):
        constructors.append(args)
        return NotificationOutbox(*args, **kwargs)

    monkeypatch.setattr(m, 'NotificationOutbox', track_constructor)
    try:
        with pytest.raises(ValueError, match='database'):
            m.migrate_records(state, home, records, provenance=proof)
    finally:
        assert {p: p.read_bytes() for p in watched} == before
    assert constructors == [], 'invalid database binding must fail before outbox construction'
    if database != 'outbox':
        assert not paths['outbox'].exists()
    assert not (unexpected / 'missing.sqlite').exists()


@pytest.mark.parametrize('database', ['native', 'auth', 'runs'])
def test_missing_required_database_fails_without_creating_files(reviewed, database):
    home, state, records, proof = subset(reviewed, 1)
    paths = dict(native=home / 'state.db', auth=state / 'auth.sqlite', runs=state / 'runs.sqlite')
    missing = paths.pop(database)
    missing.unlink()
    before = {p: p.read_bytes() for p in paths.values()}
    with pytest.raises(ValueError):
        migration().migrate_records(state, home, records, provenance=proof)
    assert not missing.exists()
    assert not (state / 'native-notifications.sqlite').exists()
    assert {p: p.read_bytes() for p in paths.values()} == before


@pytest.mark.parametrize('canonical_binding', [False, True])
def test_home_directory_alias_requires_canonical_manifest_binding(reviewed, tmp_path, canonical_binding):
    home, state, records, proof = subset(reviewed, 1)
    alias = tmp_path / 'home-alias'
    alias.symlink_to(home, target_is_directory=True)
    before = source_bytes(home, state)
    if canonical_binding:
        assert migration().migrate_records(state, alias, records, provenance=proof)['complete'] is True
    else:
        proof['home'] = str(alias)
        with pytest.raises(ValueError, match='binding'):
            migration().migrate_records(state, alias, records, provenance=proof)
        assert not (state / 'native-notifications.sqlite').exists()
    assert source_bytes(home, state) == before
