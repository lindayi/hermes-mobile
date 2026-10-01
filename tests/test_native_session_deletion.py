"""Native storage is imported ONLY in temp-HERMES_HOME subprocesses."""
import os
from pathlib import Path
import subprocess
import textwrap
import pytest

ROOT = Path(__file__).resolve().parents[1]
NATIVE_PYTHON = '/usr/local/lib/hermes-agent/venv/bin/python'


def probe(tmp_path, code):
    home = tmp_path / 'isolated-home'
    home.mkdir()
    env = {**os.environ, 'HOME': str(home), 'HERMES_HOME': str(home),
           'PYTHONPATH': f'{ROOT}:/usr/local/lib/hermes-agent'}
    result = subprocess.run([NATIVE_PYTHON, '-c', textwrap.dedent('''
import importlib.util
from pathlib import Path
import os
assert importlib.util.find_spec('backend.native_session_deletion'), 'native deletion guard not implemented'
from backend.native_session_deletion import DeletionGuard, DeleteRefused
from hermes_state import SessionDB
home = Path(os.environ['HERMES_HOME'])
db = SessionDB(home / 'state.db')
def make(sid='root', **kwargs):
    db.create_session(sid, 'api_server', **kwargs)
    db.append_message(sid, 'user', 'PRIVATE transcript')
    return sid
guard = DeletionGuard(db, home, quiescent=lambda: True)
''') + textwrap.dedent(code)], env=env, cwd=ROOT, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


def test_exact_delete_and_persistent_receipt(tmp_path):
    probe(tmp_path, '''
make()
make('unrelated')
op = 'a' * 32
receipt = guard.delete('root', op)
assert receipt['deleted'] is True and receipt['id'] == 'root'
assert receipt['operation_id'] == op and receipt['files_deleted'] is False
assert db.get_session('root') is None and db.get_session('unrelated')
assert db._conn.execute("SELECT count(*) FROM messages WHERE session_id='root'").fetchone()[0] == 0
again = DeletionGuard(db, home, quiescent=lambda: True)
assert again.receipt(op) == receipt
make()  # An outside client may recreate; same operation MUST NOT delete again.
assert again.delete('root', op) == receipt
assert db.get_session('root')
assert (home/'mobile-delete-records').stat().st_mode & 0o777 == 0o700
assert (home/'mobile-delete-records'/'receipts.sqlite').stat().st_mode & 0o777 == 0o600
''')


@pytest.mark.parametrize('setup', [
    "db.try_acquire_session_turn_lease('root', 'foreign')",
    "db.try_acquire_compression_lock('root', 'foreign')",
    "guard.quiescent = lambda: False",
    "guard.quiescent = lambda: None",
])
def test_busy_or_unknown_refuses_and_preserves_foreign_locks(tmp_path, setup):
    probe(tmp_path, f'''
make()
{setup}
try:
    guard.delete('root', 'b'*32)
except DeleteRefused:
    pass
else:
    raise AssertionError('busy/unknown deletion admitted')
assert db.get_session('root')
for table in ('session_turn_leases', 'compression_locks'):
    rows = db._conn.execute('SELECT holder FROM '+table).fetchall()
    assert all(r[0] == 'foreign' for r in rows)
''')


@pytest.mark.parametrize('setup', [
    "make('branch', parent_session_id='root')",
    "make('parent'); db._execute_write(lambda c: c.execute(\"UPDATE sessions SET parent_session_id='parent' WHERE id='root'\"))",
    "db._execute_write(lambda c: c.execute(\"UPDATE sessions SET end_reason='compression' WHERE id='root'\"))",
    "db._execute_write(lambda c: c.execute(\"UPDATE sessions SET session_key='route' WHERE id='root'\"))",
    "db._execute_write(lambda c: c.execute(\"UPDATE sessions SET profile_name='member_a' WHERE id='root'\"))",
    "db._execute_write(lambda c: c.execute(\"UPDATE sessions SET source='whatsapp' WHERE id='root'\"))",
    "db._execute_write(lambda c: c.execute(\"UPDATE sessions SET model_config='invalid json' WHERE id='root'\"))",
    "db._execute_write(lambda c: c.execute(\"INSERT INTO gateway_routing VALUES ('', 'route', '{\\\"session_id\\\":\\\"root\\\"}', 1)\"))",
    "db._execute_write(lambda c: c.execute(\"INSERT INTO async_delegations (delegation_id,origin_session,state,dispatched_at,updated_at) VALUES ('d','root','completed',1,1)\"))",
])
def test_unsafe_identity_relations_refused(tmp_path, setup):
    probe(tmp_path, f'''
make()
{setup}
try:
    guard.delete('root', 'c'*32)
except DeleteRefused:
    pass
else:
    raise AssertionError('unsafe identity accepted')
assert db.get_session('root')
''')


def test_completed_internal_delegate_cascade(tmp_path):
    probe(tmp_path, '''
make()
make('worker', parent_session_id='root', model_config={'_delegate_from':'root'})
make('grandchild', model_config={'_delegate_from':'worker'})
db.end_session('worker', 'agent_close')
db.end_session('grandchild', 'agent_close')
db._execute_write(lambda c: c.execute("UPDATE sessions SET source='subagent' WHERE id != 'root'"))
r = guard.delete('root', 'd'*32)
assert r['deleted'] and set(r['deleted_ids']) == {'root','worker','grandchild'}
assert all(db.get_session(s) is None for s in r['deleted_ids'])
assert not db._conn.execute('SELECT * FROM session_turn_leases').fetchall()
assert not db._conn.execute('SELECT * FROM compression_locks').fetchall()
''')



@pytest.mark.parametrize('sid,operation', [('../root', 'e'*32), ('root*', 'e'*32),
    ('root/other', 'e'*32), ('root', 'not-a-uuid'), ('root', True)])
def test_invalid_ids_never_claim_or_delete(tmp_path, sid, operation):
    probe(tmp_path, f"""
make({sid!r})
try:
    guard.delete({sid!r}, {operation!r})
except DeleteRefused as exc:
    assert exc.status == 400
else:
    raise AssertionError('invalid identity admitted')
assert db.get_session({sid!r})
""")


def test_cross_home_and_unsafe_receipt_storage_refused(tmp_path):
    probe(tmp_path, """
other = home/'other'; other.mkdir()
try:
    DeletionGuard(db, other, quiescent=lambda: True)
except DeleteRefused:
    pass
else:
    raise AssertionError('cross-home native DB admitted')
(guard.records).chmod(0o644)
try:
    DeletionGuard(db, home, quiescent=lambda: True)
except DeleteRefused:
    pass
else:
    raise AssertionError('public receipt storage admitted')
""")


def test_detached_branch_marker_refused(tmp_path):
    probe(tmp_path, """
make(model_config={'_branched_from': 'old-parent'})
try:
    guard.delete('root', 'e'*32)
except DeleteRefused:
    pass
else:
    raise AssertionError('branch marker accepted')
assert db.get_session('root')
""")


def test_same_native_root_cannot_get_another_operation(tmp_path):
    probe(tmp_path, """
make()
r = guard.delete('root', 'a'*32)
make()
try:
    guard.delete('root', 'b'*32)
except DeleteRefused:
    pass
else:
    raise AssertionError('receipt session binding bypassed')
assert db.get_session('root')
""")



HTTP_SETUP = r"""
import asyncio, json
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from types import SimpleNamespace
import backend.native_session_deletion as deletion
assert hasattr(deletion, 'session_deletion_adapter'), 'native HTTP adapter missing'
class Base:
    def __init__(self):
        self.evidence = {'status':'ok', 'work':{'agent_workers':0},
                         'notifications':{'status':'ok','backlog':0}}
    def _check_auth(self, request):
        if request.headers.get('Authorization') != 'Bearer test-owner':
            return web.json_response({'error':'unauthorized'}, status=401)
    async def _read_json_body(self, request):
        return await request.json(), None
    def _ensure_session_db(self):
        return db
    async def _ensure_session_db_async(self):
        return db
    async def _handle_capabilities(self, request):
        return web.json_response({'features':{}})
    async def mutate(self, request):
        return web.json_response({'mutated':True})
    def _http_route_table(self):
        return [('GET','/v1/capabilities',self._handle_capabilities),
                ('POST','/mutate',self.mutate)]
Adapter = deletion.session_deletion_adapter(Base, home,
    evidence=lambda adapter: adapter.evidence)
adapter = Adapter()
async def client_for():
    app = web.Application()
    for method, path, handler in adapter._http_route_table():
        app.router.add_route(method, path, handler)
        app.router.add_route(method, '/p/{profile}'+path, handler)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client
headers = {'Authorization':'Bearer test-owner'}
"""


def test_authenticated_http_contract_and_owner_profile(tmp_path):
    probe(tmp_path, HTTP_SETUP + r"""
make()
async def test():
    client = await client_for()
    try:
        r = await client.get('/v1/capabilities')
        assert r.status == 401
        r = await client.get('/v1/capabilities', headers=headers)
        assert (await r.json())['features']['mobile_session_delete_version'] == 1
        body = {'confirm':True, 'operation_id':'f'*32}
        r = await client.delete('/api/mobile/sessions/root', json=body)
        assert r.status == 401 and db.get_session('root')
        for bad in ({'confirm':1,'operation_id':'f'*32}, {**body,'profile':'default'}, {}, []):
            r = await client.delete('/api/mobile/sessions/root', json=bad, headers=headers)
            assert r.status == 400 and db.get_session('root')
        r = await client.delete('/p/default/api/mobile/sessions/root',json=body,headers=headers)
        assert r.status == 403 and db.get_session('root')
        r = await client.delete('/api/mobile/sessions/root', json=body, headers=headers)
        data = await r.json()
        assert r.status == 200 and data['deleted'] is True
        r = await client.get('/api/mobile/session-deletions/'+body['operation_id'],headers=headers)
        assert r.status == 200 and await r.json() == data
        r = await client.get('/api/mobile/session-deletions/'+body['operation_id'])
        assert r.status == 401
    finally:
        await client.close()
asyncio.run(test())
""")


@pytest.mark.parametrize('evidence', [
    {'status':'unknown'},
    {'status':'ok','work':{'agent_workers':1},'notifications':{'status':'ok','backlog':0}},
    {'status':'ok','work':{'agent_workers':0},'notifications':{'status':'ok','backlog':1}},
])
def test_http_unknown_and_terminal_but_live_workers_refused(tmp_path, evidence):
    probe(tmp_path, HTTP_SETUP + f"""
make()
adapter.evidence = {evidence!r}
async def test():
    client = await client_for()
    try:
        r = await client.delete('/api/mobile/sessions/root',json={{'confirm':True,'operation_id':'f'*32}},headers=headers)
        assert r.status == 409 and db.get_session('root')
    finally:
        await client.close()
asyncio.run(test())
""")




def test_definitive_refusal_receipt_releases_only_new_operation(tmp_path):
    probe(tmp_path, """
make()
db.try_acquire_session_turn_lease('root', 'foreign')
try:
    guard.delete('root', 'a'*32)
except DeleteRefused:
    pass
r = guard.receipt('a'*32)
assert r['status'] == 'refused' and r['deleted'] is False
assert r['id'] == 'root' and r['operation_id'] == 'a'*32
db.release_session_turn_lease('root', 'foreign')
assert guard.delete('root', 'a'*32) == r  # never replay the refused operation
assert db.get_session('root')
assert guard.delete('root', 'b'*32)['deleted'] is True
""")


def test_commit_receipt_gap_remains_unknown_forever(tmp_path):
    probe(tmp_path, """
make()
guard._save = lambda receipt: (_ for _ in ()).throw(OSError('receipt disk failure'))
try:
    guard.delete('root', 'a'*32)
except DeleteRefused:
    pass
assert db.get_session('root') is None
fresh = DeletionGuard(db, home, quiescent=lambda: True)
r = fresh.receipt('a'*32)
assert r['status'] == 'unknown' and r['deleted'] is False
assert fresh.delete('root', 'a'*32) == r
assert fresh.receipt('a'*32) == r
""")




def test_owner_entrypoint_composes_deletion_but_member_does_not(tmp_path):
    probe(tmp_path, """
from backend.native_controls_service import listener_adapter
class Empty: pass
owner = listener_adapter(Empty, home, member=False, state_dir=home/'app-state')
member = listener_adapter(Empty, home, member=True)
assert hasattr(owner, '_handle_mobile_delete'), 'owner composition missing'
assert not hasattr(member, '_handle_mobile_delete')
""")


def test_cancelled_request_keeps_worker_and_ingress_fence_until_exit(tmp_path):
    probe(tmp_path, HTTP_SETUP + r"""
import threading, queue
from backend.native_maintenance import maintenance_snapshot
adapter._active_run_tasks = {}; adapter._run_statuses = {}
adapter._active_run_agents = {}; adapter._shutdown_interruptible_agents = {}
adapter._stopping_run_ids = set(); adapter._pending_agent_requests = 0
adapter._inflight_agent_runs = 0; adapter._maintenance_workers = 0
adapter._maintenance_uncertain = False
adapter._maintenance_delegations = SimpleNamespace(count=lambda: 0)
registry = SimpleNamespace(_lock=threading.Lock(), _running={}, completion_queue=queue.Queue())
delegations = SimpleNamespace(_records_lock=threading.Lock(), _records={})
entered, release = threading.Event(), threading.Event()
def work():
    entered.set()
    assert release.wait(5)
async def test():
    client = await client_for()
    task = None
    try:
        adapter._mobile_deletion_busy = True
        task = asyncio.create_task(adapter._deletion_job(work, exclusive=True))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        snapshot = maintenance_snapshot(adapter, registry, delegations, home/'state.db')
        assert snapshot['status'] == 'ok'
        assert snapshot['work'].get('session_deletion_workers') == 1, snapshot
        r = await client.post('/mutate', headers=headers)
        assert r.status == 409
        release.set()
        await asyncio.gather(*adapter._mobile_deletion_jobs)
        await asyncio.sleep(0)
        assert not adapter._mobile_deletion_busy
        snapshot = maintenance_snapshot(adapter, registry, delegations, home/'state.db')
        assert snapshot['work']['session_deletion_workers'] == 0
        r = await client.post('/mutate', headers=headers)
        assert r.status == 200
    finally:
        release.set()
        await client.close()
asyncio.run(test())
""")




@pytest.mark.parametrize('change', [
    "make('branch',parent_session_id='root')",
    "make('detached',model_config={'_branched_from':'root'})",
    "make('lateworker',model_config={'_delegate_from':'root'}); db.end_session('lateworker','agent_close')",
    "db._execute_write(lambda c: c.execute(\"INSERT INTO gateway_routing VALUES ('','key','{\\\"session_id\\\":\\\"root\\\"}',1)\"))",
    "db._execute_write(lambda c: c.execute(\"UPDATE compression_locks SET expires_at=0\"))",
    "db._execute_write(lambda c: c.execute(\"UPDATE session_turn_leases SET holder='successor'\"))",
])
def test_atomic_final_recheck_catches_racing_metadata_and_lease_loss(tmp_path, change):
    probe(tmp_path, f"""
make()
original = db._execute_write
count = 0
def racing(fn, patience_s=None):
    global count
    count += 1
    if count == 3:  # after two native lock acquisitions, before deletion BEGIN
        db._execute_write = original
        {change}
    return original(fn, patience_s=patience_s)
db._execute_write = racing
try:
    guard.delete('root','a'*32)
except DeleteRefused:
    pass
else:
    raise AssertionError('racing mutation admitted')
assert db.get_session('root')
assert guard.receipt('a'*32)['status'] == 'refused'
rows = db._conn.execute('SELECT holder FROM session_turn_leases').fetchall()
assert all(row[0] == 'successor' for row in rows)
""")


@pytest.mark.parametrize('state', ['active', 'leased', 'branch', 'foreign_parent'])
def test_unsafe_delegate_cascade_is_not_deleted(tmp_path, state):
    changes = {
        'active':'',
        'leased':"db.end_session('worker','agent_close'); db.try_acquire_session_turn_lease('worker','foreign')",
        'branch':"db.end_session('worker','agent_close'); make('branch',parent_session_id='worker')",
        'foreign_parent':"db.end_session('worker','agent_close'); make('foreign'); db._execute_write(lambda c:c.execute(\"UPDATE sessions SET parent_session_id='foreign' WHERE id='worker'\"))",
    }
    probe(tmp_path, f"""
make(); make('worker',model_config={{'_delegate_from':'root'}})
{changes[state]}
try:
    guard.delete('root','a'*32)
except DeleteRefused:
    pass
else:
    raise AssertionError('unsafe delegate admitted')
assert db.get_session('root') and db.get_session('worker')
""")


def test_in_place_archived_messages_deleted_but_files_retained(tmp_path):
    probe(tmp_path, """
make()
db._execute_write(lambda c:c.execute("UPDATE messages SET active=0, compacted=1 WHERE session_id='root'"))
transcripts = home/'sessions'; transcripts.mkdir(exist_ok=True)
f = transcripts/'root.jsonl'; f.write_text('external copy')
r = guard.delete('root','a'*32)
assert r['deleted'] is True and r['files_deleted'] is False
assert not db._conn.execute('SELECT * FROM messages').fetchall()
assert f.read_text() == 'external copy'
assert b'PRIVATE transcript' not in guard.records.read_bytes()
""")




def test_real_installed_owner_adapter_storage_and_maintenance(tmp_path):
    probe(tmp_path, r"""
import asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from backend.native_controls_service import listener_adapter
from backend.native_session_deletion import _native_evidence
adapter = listener_adapter(APIServerAdapter, home, member=False, state_dir=home/'app-state')(
    PlatformConfig(enabled=True, extra={'key':'isolated-test-key-not-live-0123456789', 'host':'127.0.0.1','port':0}))
adapter._session_db = db
# Native registry initialization is permitted only in this isolated subprocess.
import tools.process_registry
import tools.async_delegation
make()
async def test():
    app = web.Application()
    for method,path,handler in adapter._http_route_table():
        app.router.add_route(method,path,handler)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        evidence = _native_evidence(adapter)
        assert evidence['status'] == 'ok', evidence
        assert evidence['work']['session_deletion_workers'] == 0
        headers = {'Authorization':'Bearer isolated-test-key-not-live-0123456789'}
        r = await client.delete('/api/mobile/sessions/root',headers=headers,
            json={'confirm':True,'operation_id':'a'*32})
        data = await r.json()
        assert r.status == 200 and data['deleted'] is True, data
        assert db.get_session('root') is None
        assert _native_evidence(adapter)['work']['session_deletion_workers'] == 0
    finally:
        await client.close()
asyncio.run(test())
""")




def test_native_callback_exception_is_unknown_not_refused(tmp_path):
    probe(tmp_path, r"""
make()
db._execute_write(lambda c: c.execute("CREATE TRIGGER test_abort BEFORE DELETE ON messages BEGIN SELECT RAISE(ABORT, 'injected failure'); END"))
try:
    guard.delete('root','a'*32)
except DeleteRefused:
    pass
else:
    raise AssertionError('native error reported as deletion')
assert db.get_session('root')
r = guard.receipt('a'*32)
assert r['status'] == 'unknown' and r['deleted'] is False
assert guard.delete('root','a'*32) == r
""")


def test_native_validation_and_mutation_share_real_sqlite_writer_lock(tmp_path):
    probe(tmp_path, r"""
import sqlite3
make()
other = SessionDB(home/'state.db')
checks = 0
def quiescent():
    global checks
    checks += 1
    if checks == 2:
        assert db._conn.in_transaction
        try:
            other._execute_write(lambda c:c.execute("INSERT INTO sessions(id,source,started_at,parent_session_id) VALUES ('racing','api_server',1,'root')"), patience_s=0)
        except sqlite3.OperationalError as exc:
            assert 'locked' in str(exc)
        else:
            raise AssertionError('second writer entered before native deletion')
    return True
guard.quiescent = quiescent
assert guard.delete('root','a'*32)['deleted']
assert checks == 2
assert not db.get_session('racing')
other.close()
""")




def test_lazy_native_db_initialization_is_inside_tracked_worker(tmp_path):
    probe(tmp_path, HTTP_SETUP + r"""
import threading
make()
loop_thread = threading.get_ident()
observed = []
def sync_db():
    assert adapter._session_deletion_workers == 1
    assert threading.get_ident() != loop_thread
    observed.append('tracked')
    return db
async def async_db():
    assert adapter._session_deletion_workers == 1, 'DB initialization untracked'
    return db
adapter._ensure_session_db = sync_db
adapter._ensure_session_db_async = async_db
async def test():
    client = await client_for()
    try:
        r = await client.delete('/api/mobile/sessions/root',headers=headers,
            json={'confirm':True,'operation_id':'a'*32})
        assert r.status == 200, await r.json()
        r = await client.get('/api/mobile/session-deletions/'+'a'*32,headers=headers)
        assert r.status == 200
        assert observed == ['tracked','tracked']
    finally:
        await client.close()
asyncio.run(test())
""")
