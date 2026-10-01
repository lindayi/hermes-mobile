import hashlib
import json
import sqlite3
from pathlib import Path
import pytest
try:
    from deploy import notice_recovery
except ImportError:
    notice_recovery = None

SCHEMA = '''CREATE TABLE async_delegations(
 delegation_id TEXT PRIMARY KEY, origin_session TEXT NOT NULL,
 origin_ui_session_id TEXT NOT NULL DEFAULT '', parent_session_id TEXT,
 state TEXT NOT NULL, dispatched_at REAL NOT NULL, completed_at REAL,
 updated_at REAL NOT NULL, event_json TEXT, result_json TEXT,
 delivery_state TEXT NOT NULL DEFAULT 'pending', delivery_attempts INTEGER NOT NULL DEFAULT 0,
 delivered_at REAL, owner_pid INTEGER, owner_started_at INTEGER, task_json TEXT,
 delivery_claim TEXT, delivery_claimed_at REAL, origin_session_id TEXT NOT NULL DEFAULT '')'''

def row(key='deleg_a', **overrides):
    event={'type':'async_delegation','delegation_id':key,'session_key':'run_original','origin_session_id':'api_original','summary':'Recorded completed report'}
    return dict(delegation_id=key,origin_session='run_original',origin_ui_session_id='',parent_session_id='api_original',state='completed',dispatched_at=1.,completed_at=2.,updated_at=2.,event_json=json.dumps(event),result_json=json.dumps({'results':[{'summary':'Recorded completed report'}]}),delivery_state='pending',delivery_attempts=2,delivered_at=None,owner_pid=1234,owner_started_at=12,task_json='{}',delivery_claim=None,delivery_claimed_at=None,origin_session_id='api_original') | overrides

def make_db(path, rows=()):
    with sqlite3.connect(path) as db:
        db.execute(SCHEMA)
        db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY,content TEXT)')
        db.execute("INSERT INTO messages VALUES(1,'Real unrelated conversation unchanged')")
        for r in rows:
            db.execute('INSERT INTO async_delegations('+','.join(r)+') VALUES('+','.join('?' for _ in r)+')',tuple(r.values()))
    return path

def read_rows(path):
    with sqlite3.connect(path) as db:
        db.row_factory=sqlite3.Row
        return {r['delegation_id']:dict(r) for r in db.execute('SELECT * FROM async_delegations')}

def restore(live,archive,ids,**kw):
    assert notice_recovery is not None,'notice recovery helper is implemented'
    return notice_recovery.restore_missing(live,archive,ids,archive_sha256=kw.pop('archive_sha256',hashlib.sha256(archive.read_bytes()).hexdigest()),owner_pid=kw.pop('owner_pid',1234),**kw)

def test_restore_exact_missing_result_without_replay_or_history_rewind(tmp_path):
    original=row();other=row('deleg_other');other['delivery_state']='delivered'
    archive=make_db(tmp_path/'archive.sqlite',[original,other])
    live=make_db(tmp_path/'live.sqlite',[other])
    before=read_rows(live);archive_bytes=archive.read_bytes()
    result=restore(live,archive,['deleg_a'])
    after=read_rows(live)
    assert result['inserted']==['deleg_a']
    assert after['deleg_other']==before['deleg_other']
    assert after['deleg_a']=={**original,'delivery_state':'dropped','delivery_claim':None,'delivery_claimed_at':None}
    assert after['deleg_a']['event_json']==original['event_json']
    assert after['deleg_a']['result_json']==original['result_json']
    assert archive.read_bytes()==archive_bytes
    with sqlite3.connect(live) as db:
        assert db.execute('SELECT * FROM messages').fetchall()==[(1,'Real unrelated conversation unchanged')]
        assert db.execute("SELECT COUNT(*) FROM async_delegations WHERE delivery_state='pending'").fetchone()[0]==0
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


@pytest.mark.parametrize('change',[
 {'owner_pid':999}, {'state':'running'}, {'event_json':'not json'},
 {'event_json':'[]'}, {'result_json':'[]'}, {'result_json':'{"n":NaN}'},
 {'event_json':json.dumps({'type':'completion','delegation_id':'deleg_b'})},
 {'origin_session':'different-run'}, {'origin_session_id':'different-session'},
 {'event_json':json.dumps({'type':'async_delegation','delegation_id':'wrong','session_key':'run_original','origin_session_id':'api_original'})},
])
def test_invalid_source_record_aborts_entire_batch_before_inserting(tmp_path,change):
    archive=make_db(tmp_path/'archive.sqlite',[row('deleg_a'),row('deleg_b',**change)])
    live=make_db(tmp_path/'live.sqlite')
    with pytest.raises(ValueError):restore(live,archive,['deleg_a','deleg_b'])
    assert read_rows(live)=={}

@pytest.mark.parametrize('ids',[[],['deleg_a','deleg_a'],['missing'],['../deleg_a']])
def test_invalid_allowlist_fails_closed(tmp_path,ids):
    archive=make_db(tmp_path/'archive.sqlite',[row()]);live=make_db(tmp_path/'live.sqlite')
    with pytest.raises(ValueError):restore(live,archive,ids)
    assert read_rows(live)=={}

def test_conflicting_existing_identity_cannot_be_silently_accepted(tmp_path):
    archive=make_db(tmp_path/'archive.sqlite',[row('deleg_a'),row('deleg_b')])
    live=make_db(tmp_path/'live.sqlite',[row('deleg_b',origin_session='other')]);before=read_rows(live)
    with pytest.raises(ValueError):restore(live,archive,['deleg_a','deleg_b'])
    assert read_rows(live)==before

def test_recovery_retry_preserves_existing_acknowledgement(tmp_path):
    archive=make_db(tmp_path/'archive.sqlite',[row()]);live=make_db(tmp_path/'live.sqlite')
    restore(live,archive,['deleg_a'])
    with sqlite3.connect(live) as db:db.execute("UPDATE async_delegations SET delivery_state='delivered',delivered_at=9")
    before=read_rows(live);assert restore(live,archive,['deleg_a'])['inserted']==[]
    assert read_rows(live)==before

def test_wrong_hash_and_absent_live_database_do_not_write(tmp_path):
    archive=make_db(tmp_path/'archive.sqlite',[row()]);live=make_db(tmp_path/'live.sqlite')
    with pytest.raises(ValueError):restore(live,archive,['deleg_a'],archive_sha256='0'*64)
    assert read_rows(live)=={}
    missing=tmp_path/'missing.sqlite'
    with pytest.raises((ValueError,sqlite3.OperationalError,FileNotFoundError)):restore(missing,archive,['deleg_a'])
    assert not missing.exists()

@pytest.mark.parametrize('alias',['archive','live','same'])
def test_alias_paths_are_rejected(tmp_path,alias):
    archive=make_db(tmp_path/'archive.sqlite',[row()]);live=make_db(tmp_path/'live.sqlite')
    if alias=='same':live=archive
    else:
        link=tmp_path/'link';link.symlink_to(archive if alias=='archive' else live)
        if alias=='archive':archive=link
        else:live=link
    with pytest.raises(ValueError):restore(live,archive,['deleg_a'])

def test_schema_mismatch_fails_before_any_insertion(tmp_path):
    archive=make_db(tmp_path/'archive.sqlite',[row()]);live=make_db(tmp_path/'live.sqlite')
    with sqlite3.connect(live) as db:db.execute('ALTER TABLE async_delegations ADD COLUMN surprise TEXT')
    with pytest.raises(ValueError):restore(live,archive,['deleg_a'])
    assert read_rows(live)=={}


@pytest.mark.parametrize('replacement', ['symlink', 'file', 'ancestor', 'aba'])
def test_target_replaced_at_sqlite_open_never_writes_either_database(tmp_path, monkeypatch, replacement):
    archive = make_db(tmp_path/'archive.sqlite', [row()])
    directory = tmp_path/'target'; directory.mkdir()
    live = make_db(directory/'live.sqlite')
    unintended = make_db(tmp_path/'unintended.sqlite')
    moved = tmp_path/'original.sqlite'
    connect = sqlite3.connect
    switched = False

    def raced_connect(database, *args, **kwargs):
        nonlocal switched, moved
        if database != ':memory:' and not switched:
            switched = True
            if replacement == 'ancestor':
                moved_dir = tmp_path/'original-directory'
                directory.rename(moved_dir)
                moved = moved_dir/'live.sqlite'
                directory.mkdir()
                make_db(live)
            else:
                live.rename(moved)
                if replacement == 'file':
                    live.write_bytes(unintended.read_bytes())
                else:
                    live.symlink_to(unintended)
            connection = connect(database, *args, **kwargs)
            if replacement == 'aba':
                live.unlink()
                moved.rename(live)
                moved = live
            return connection
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', raced_connect)
    with pytest.raises((ValueError, sqlite3.DatabaseError, OSError)):
        restore(live, archive, ['deleg_a'])
    assert switched
    assert read_rows(moved) == {}
    assert read_rows(unintended) == {}
    assert read_rows(live) == {}


def test_source_replaced_after_validation_fails_even_with_identical_bytes(tmp_path, monkeypatch):
    archive = make_db(tmp_path/'archive.sqlite', [row()])
    live = make_db(tmp_path/'live.sqlite')
    replacement = tmp_path/'replacement.sqlite'
    replacement.write_bytes(archive.read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    real_sha256 = hashlib.sha256
    switched = False

    def raced_hash(raw):
        nonlocal switched
        if not switched:
            switched = True
            archive.rename(tmp_path/'original.sqlite')
            replacement.rename(archive)
        return real_sha256(raw)

    monkeypatch.setattr(hashlib, 'sha256', raced_hash)
    with pytest.raises(ValueError):
        notice_recovery.restore_missing(live, archive, ['deleg_a'],
                                       archive_sha256=digest, owner_pid=1234)
    assert switched
    assert read_rows(live) == {}


@pytest.mark.parametrize('drift', ['trigger', 'constraint', 'index'])
def test_full_schema_drift_rejected_without_history_mutation(tmp_path, drift):
    archive = make_db(tmp_path/'archive.sqlite', [row()])
    live = make_db(tmp_path/'live.sqlite')
    with sqlite3.connect(live) as db:
        if drift == 'trigger':
            db.execute("CREATE TRIGGER corrupt_history AFTER INSERT ON async_delegations "
                       "BEGIN UPDATE messages SET content='CORRUPTED'; END")
        elif drift == 'constraint':
            db.execute('DROP TABLE async_delegations')
            db.execute(SCHEMA[:-1] + ", CHECK (delivery_state != 'forbidden'))")
        else:
            db.execute('CREATE INDEX unexpected ON async_delegations(owner_pid)')
    before = live.read_bytes()
    with pytest.raises(ValueError, match='schema'):
        restore(live, archive, ['deleg_a'])
    assert live.read_bytes() == before
    assert read_rows(live) == {}


def test_even_hash_pinned_trigger_cannot_modify_history(tmp_path):
    archive = make_db(tmp_path/'archive.sqlite', [row()])
    live = make_db(tmp_path/'live.sqlite')
    for path in (archive, live):
        with sqlite3.connect(path) as db:
            db.execute("CREATE TRIGGER corrupt_history AFTER INSERT ON async_delegations "
                       "BEGIN UPDATE messages SET content='CORRUPTED'; END")
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        restore(live, archive, ['deleg_a'])
    assert read_rows(live) == {}
    with sqlite3.connect(live) as db:
        assert db.execute('SELECT content FROM messages').fetchone()[0] == 'Real unrelated conversation unchanged'


def test_second_insert_failure_rolls_back_first_insert(tmp_path, monkeypatch):
    archive = make_db(tmp_path/'archive.sqlite', [row(), row('deleg_b')])
    live = make_db(tmp_path/'live.sqlite')
    connect = sqlite3.connect
    inserted = []

    class FailingSecondInsert(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith('INSERT INTO async_delegations'):
                inserted.append(parameters[0])
                if len(inserted) == 2:
                    raise sqlite3.OperationalError('injected second insert failure')
            return super().execute(sql, parameters)

    def connect_target(database, *args, **kwargs):
        if database != ':memory:':
            kwargs['factory'] = FailingSecondInsert
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', connect_target)
    with pytest.raises(sqlite3.OperationalError, match='second insert'):
        restore(live, archive, ['deleg_a', 'deleg_b'])
    assert inserted == ['deleg_a', 'deleg_b']
    assert read_rows(live) == {}
    with connect(live) as db:
        assert db.execute('SELECT content FROM messages').fetchone()[0] == 'Real unrelated conversation unchanged'


@pytest.mark.parametrize('existing', ['file', 'symlink', 'absent'])
def test_plan_publication_is_private_atomic_and_never_overwrites(tmp_path, monkeypatch, existing):
    import os
    import stat
    plan = tmp_path/'plan.json'
    previous = tmp_path/'previous.json'; previous.write_text('previous protected evidence')
    if existing == 'file':
        plan.write_text('old plan')
    elif existing == 'symlink':
        plan.symlink_to(previous)
    writer = getattr(notice_recovery, 'write_private_plan', None)
    assert callable(writer), 'exclusive atomic private plan writer is implemented'
    link = os.link
    observed = []

    def inspect_publication(source, destination, *args, **kwargs):
        info = os.stat(source, dir_fd=kwargs.get('src_dir_fd'), follow_symlinks=False)
        observed.append(stat.S_IMODE(info.st_mode))
        return link(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, 'link', inspect_publication)
    if existing != 'absent':
        with pytest.raises(FileExistsError):
            writer(plan, {'proof': 'new'})
        assert plan.read_text() == ('old plan' if existing == 'file' else 'previous protected evidence')
    else:
        writer(plan, {'proof': 'new'})
        assert json.loads(plan.read_text()) == {'proof': 'new'}
        assert stat.S_IMODE(plan.stat().st_mode) == 0o600
    assert previous.read_text() == 'previous protected evidence'
    assert observed == [0o600]
    assert not list(tmp_path.glob('.recovery-plan-*'))


@pytest.mark.parametrize('special', ['archive', 'live'])
def test_special_file_is_rejected_without_waiting_for_a_writer(tmp_path, special):
    import os
    import signal
    archive = make_db(tmp_path/'archive.sqlite', [row()])
    live = make_db(tmp_path/'live.sqlite')
    path = archive if special == 'archive' else live
    path.unlink(); os.mkfifo(path)
    def timed_out(*args):
        raise TimeoutError('recovery blocked opening a non-regular file')
    previous = signal.signal(signal.SIGALRM, timed_out)
    signal.setitimer(signal.ITIMER_REAL, 0.2)
    try:
        with pytest.raises(ValueError, match='regular'):
            notice_recovery.restore_missing(live, archive, ['deleg_a'],
                                           archive_sha256='0'*64, owner_pid=1234)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
