"""Review regressions: actual native imports/storage only in isolated subprocesses."""
import pytest

from test_native_session_deletion import HTTP_SETUP, probe


@pytest.mark.parametrize('drift', ['extra_delete', 'bypass_transaction', 'same_bytes_wrong_origin'])
def test_unsupported_storage_never_advertised_or_used(tmp_path, drift):
    probe(tmp_path, HTTP_SETUP + r'''
import hashlib, importlib.util, sys
api = Path('/usr/local/lib/hermes-agent/gateway/platforms/api_server.py')
api_hash = hashlib.sha256(api.read_bytes()).hexdigest()
source = Path('/usr/local/lib/hermes-agent/hermes_state.py').read_text()
''' + f"drift = {drift!r}\n" + r'''
needle = '            removed_delegate_ids.extend(_delete_delegate_children(conn, [session_id]))'
assert source.count(needle) == 1
if drift == 'extra_delete':
    source = source.replace(needle,
        "            conn.execute(\"DELETE FROM messages WHERE session_id='unrelated'\")\n"
        "            conn.execute(\"DELETE FROM sessions WHERE id='unrelated'\")\n" + needle)
elif drift == 'bypass_transaction':
    needle = '    def delete_session(\n        self,\n        session_id: str,\n        sessions_dir: Optional[Path] = None,\n        expected_delete_ids: Optional[List[str]] = None,\n    ) -> bool:'
    assert source.count(needle) == 1
    source = source.replace(needle, needle + '\n        return True\n', 1)
copy = home / 'unsupported_hermes_state.py'
copy.write_text(source)
spec = importlib.util.spec_from_file_location('hermes_state', copy)
replacement = importlib.util.module_from_spec(spec)
sys.modules['hermes_state'] = replacement
spec.loader.exec_module(replacement)
db.close()
db = replacement.SessionDB(home / 'state.db')
make(); make('unrelated')
async def test():
    request = SimpleNamespace(headers=headers, match_info={}, path='/v1/capabilities')
    response = await adapter._handle_capabilities(request)
    assert json.loads(response.text)['features'].get('mobile_session_delete_version') != 1, 'unsupported storage advertised'
    request = SimpleNamespace(headers=headers, match_info={'session_id':'root'},
                              path='/api/mobile/sessions/root')
    async def body(request):
        return {'confirm':True, 'operation_id':'a'*32}, None
    adapter._read_json_body = body
    response = await adapter._handle_mobile_delete(request)
    assert response.status == 503, response.text
    assert db.get_session('root') and db.get_session('unrelated')
    assert guard.receipt('a'*32) is None
asyncio.run(test())
assert hashlib.sha256(api.read_bytes()).hexdigest() == api_hash
''')


def test_storage_digest_rechecked_before_existing_guard_mutation(tmp_path):
    probe(tmp_path, HTTP_SETUP + r'''
import importlib.util, sys
source = Path('/usr/local/lib/hermes-agent/hermes_state.py').read_bytes()
copy = home / 'hermes_state.py'
copy.write_bytes(source)
spec = importlib.util.spec_from_file_location('hermes_state', copy)
replacement = importlib.util.module_from_spec(spec)
sys.modules['hermes_state'] = replacement
spec.loader.exec_module(replacement)
# Simulate an installation at a disposable path. Do not change the digest pin.
deletion.SUPPORTED_STORAGE_PATH = copy
db.close()
db = replacement.SessionDB(home / 'state.db')
make(); make('unrelated')
guard = DeletionGuard(db, home, quiescent=lambda: True)
async def test():
    request = SimpleNamespace(headers=headers, match_info={}, path='/v1/capabilities')
    response = await adapter._handle_capabilities(request)
    assert json.loads(response.text)['features']['mobile_session_delete_version'] == 1
    copy.write_bytes(source + b'\n# unsupported storage update\n')
    response = await adapter._handle_capabilities(request)
    assert json.loads(response.text)['features'].get('mobile_session_delete_version') != 1
asyncio.run(test())
try:
    guard.delete('root', 'a'*32)
except DeleteRefused as exc:
    assert exc.code == 'unsupported_native_storage'
else:
    raise AssertionError('existing guard used changed storage identity')
assert db.get_session('root') and db.get_session('unrelated')
assert guard.receipt('a'*32) is None
''')


def test_cached_unknown_storage_class_never_advertised_or_used(tmp_path):
    probe(tmp_path, HTTP_SETUP + r'''
class UnknownStorage(SessionDB):
    pass
db.__class__ = UnknownStorage
adapter._session_db = db
make()
async def test():
    request = SimpleNamespace(headers=headers, match_info={}, path='/v1/capabilities')
    response = await adapter._handle_capabilities(request)
    assert json.loads(response.text)['features'].get('mobile_session_delete_version') != 1, 'unknown cached class advertised'
asyncio.run(test())
try:
    guard.delete('root', 'a'*32)
except DeleteRefused as exc:
    assert exc.code == 'unsupported_native_storage' and exc.status == 503
else:
    raise AssertionError('unknown storage class used')
assert db.get_session('root') and guard.receipt('a'*32) is None
''')


@pytest.mark.parametrize('cancel_shutdown', [False, True])
def test_actual_native_shutdown_closes_only_after_admitted_receipt_exit(tmp_path, cancel_shutdown):
    probe(tmp_path, r'''
import asyncio, threading
from types import SimpleNamespace
from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from backend.native_controls_service import listener_adapter
import backend.native_session_deletion as deletion
adapter = listener_adapter(APIServerAdapter, home, member=False, state_dir=home/'app-state')(PlatformConfig(
    enabled=True, extra={'key':'isolated-review-key-not-live-0123456789',
                         'host':'127.0.0.1', 'port':0}))
cached = adapter._ensure_session_db()
assert cached is not db and not adapter._session_db_cache_closed
entered_a, release_a, entered_b, release_b = [threading.Event() for _ in range(4)]
def blocked(entered, release):
    entered.set(); assert release.wait(5)
original = deletion.DeletionGuard.receipt
def receipt(self, op):
    blocked(entered_b, release_b)
    assert cached._conn is not None, 'native DB closed underneath admitted receipt'
    return original(self, op)
deletion.DeletionGuard.receipt = receipt
request = SimpleNamespace(
    headers={'Authorization':'Bearer isolated-review-key-not-live-0123456789'},
    match_info={'operation_id':'a'*32}, path='/api/mobile/session-deletions/'+'a'*32)
''' + f'cancel_shutdown = {cancel_shutdown!r}\n' + r'''
async def test():
    a = asyncio.create_task(adapter._deletion_job(lambda: blocked(entered_a, release_a)))
    b = asyncio.create_task(adapter._handle_mobile_delete_receipt(request))
    try:
        assert await asyncio.to_thread(entered_a.wait, 2)
        assert await asyncio.to_thread(entered_b.wait, 2)
        b.cancel()
        try: await b
        except asyncio.CancelledError: pass
        stop = asyncio.create_task(adapter.disconnect()); await asyncio.sleep(0)
        response = await adapter._handle_mobile_delete_receipt(request)
        assert response.status == 503, 'late receipt admitted after drain boundary'
        assert adapter._session_deletion_workers == 2
        if cancel_shutdown:
            stop.cancel()
            try: await stop
            except asyncio.CancelledError: pass
        release_a.set(); await a; await asyncio.sleep(0)
        assert not adapter._session_db_cache_closed and cached._conn is not None
        assert adapter._session_deletion_workers == 1
        release_b.set()
        await asyncio.gather(*adapter._mobile_deletion_jobs)
        # No second disconnect call: cancellation must not orphan DB teardown.
        for _ in range(100):
            if adapter._session_db_cache_closed: break
            await asyncio.sleep(0)
        assert adapter._session_db_cache_closed and cached._conn is None, 'cancelled shutdown orphaned teardown'
        assert adapter._session_deletion_workers == 0
        assert not adapter._mobile_deletion_jobs
        if not cancel_shutdown: await stop
        assert (await adapter._handle_mobile_delete_receipt(request)).status == 503
    finally:
        release_a.set(); release_b.set()
asyncio.run(test())
''')


@pytest.mark.parametrize('fault', ['rejected', 'enqueued_before_start_failure'])
def test_executor_prestart_failure_cannot_leak_or_execute_abandoned_delete(tmp_path, fault):
    probe(tmp_path, HTTP_SETUP + r'''
from concurrent.futures import ThreadPoolExecutor
make()
''' + f'fault = {fault!r}\n' + r'''
async def test():
    pool = ThreadPoolExecutor(max_workers=1)
    asyncio.get_running_loop().set_default_executor(pool)
    adjust = pool._adjust_thread_count
    if fault == 'rejected':
        pool.shutdown()
    else:
        def fail(): raise RuntimeError("can't start new thread")
        pool._adjust_thread_count = fail
    adapter._mobile_deletion_busy = True
    observed = []
    def work():
        observed.append('destructive work entered')
        return guard.delete('root', 'a'*32)
    try: await adapter._deletion_job(work, exclusive=True)
    except RuntimeError: pass
    else: raise AssertionError('executor failure missing')
    await asyncio.sleep(0)
    assert not observed and db.get_session('root')
    if fault != 'rejected':
        pool._adjust_thread_count = adjust
        await asyncio.to_thread(lambda: None)
        assert not observed, 'abandoned queued wrapper executed destructive work'
    assert adapter._session_deletion_workers == 0, 'executor rejection leaked reservation'
    assert not adapter._mobile_deletion_jobs and not adapter._mobile_deletion_busy
    assert db.get_session('root') and guard.receipt('a'*32) is None
asyncio.run(test())
''')


def test_executor_failure_after_worker_started_retains_fence_and_shutdown_tracking(tmp_path):
    probe(tmp_path, HTTP_SETUP + r'''
import threading
from concurrent.futures import ThreadPoolExecutor
entered, release = threading.Event(), threading.Event()
closed = []
async def base_disconnect(self):
    closed.append(True)
Base.disconnect = base_disconnect
make()
async def test():
    pool = ThreadPoolExecutor(max_workers=1)
    asyncio.get_running_loop().set_default_executor(pool)
    adjust = pool._adjust_thread_count
    def start_then_fail():
        adjust()
        assert entered.wait(2)
        raise RuntimeError('submission failed after worker entry')
    pool._adjust_thread_count = start_then_fail
    def work():
        entered.set(); assert release.wait(5)
        assert adapter._mobile_deletion_busy
        return guard.delete('root', 'a'*32)
    adapter._mobile_deletion_busy = True
    try:
        try: await adapter._deletion_job(work, exclusive=True)
        except RuntimeError: pass
        else: raise AssertionError('submission failure missing')
        await asyncio.sleep(0)
        assert adapter._session_deletion_workers == 1
        assert adapter._mobile_deletion_busy and adapter._mobile_deletion_jobs, 'started work lost fence/tracking'
        stop = asyncio.create_task(adapter.disconnect()); await asyncio.sleep(0)
        assert not closed and not stop.done()
        release.set()
        await stop
        assert db.get_session('root') is None and guard.receipt('a'*32)['deleted']
        assert closed == [True]
        assert adapter._session_deletion_workers == 0 and not adapter._mobile_deletion_jobs
    finally:
        pool._adjust_thread_count = adjust
        release.set()
asyncio.run(test())
''')


def test_executor_shutdown_cancels_queued_job_without_counter_leak(tmp_path):
    probe(tmp_path, HTTP_SETUP + r'''
import threading
from concurrent.futures import ThreadPoolExecutor
entered, release = threading.Event(), threading.Event()
pool = ThreadPoolExecutor(max_workers=1)
def occupying():
    entered.set(); assert release.wait(5)
pool.submit(occupying)
assert entered.wait(2)
async def test():
    asyncio.get_running_loop().set_default_executor(pool)
    observed = []
    adapter._mobile_deletion_busy = True
    job = asyncio.create_task(adapter._deletion_job(lambda: observed.append(True), exclusive=True))
    try:
        await asyncio.sleep(0)
        assert adapter._session_deletion_workers == 1
        pool.shutdown(wait=False, cancel_futures=True)
        for _ in range(10): await asyncio.sleep(0)
        assert adapter._session_deletion_workers == 0, 'cancelled queued job leaked reservation'
        assert not observed and not adapter._mobile_deletion_busy and not adapter._mobile_deletion_jobs
        try: await job
        except asyncio.CancelledError: pass
        else: raise AssertionError('queued work unexpectedly ran')
    finally:
        release.set()
        job.cancel()
asyncio.run(test())
''')


def test_exclusive_completion_cannot_reopen_shutdown_ingress(tmp_path):
    probe(tmp_path, HTTP_SETUP + r'''
import threading
async def base_disconnect(self):
    self.base_disconnected = True
Base.disconnect = base_disconnect
entered_a, release_a, entered_b, release_b = [threading.Event() for _ in range(4)]
def blocked(entered, release):
    entered.set(); assert release.wait(5)
async def test():
    adapter.base_disconnected = False
    adapter._mobile_deletion_busy = True
    a = asyncio.create_task(adapter._deletion_job(
        lambda: blocked(entered_a, release_a), exclusive=True))
    b = asyncio.create_task(adapter._deletion_job(lambda: blocked(entered_b, release_b)))
    try:
        assert await asyncio.to_thread(entered_a.wait, 2)
        assert await asyncio.to_thread(entered_b.wait, 2)
        stop = asyncio.create_task(adapter.disconnect())
        await asyncio.sleep(0)
        release_a.set(); await a; await asyncio.sleep(0)
        assert not stop.done() and not adapter.base_disconnected
        mutate = next(h for method, path, h in adapter._http_route_table() if path == '/mutate')
        assert (await mutate(SimpleNamespace())).status == 409, 'shutdown reopened mutation ingress'
        called = []
        try:
            await adapter._deletion_job(lambda: called.append('late'))
        except DeleteRefused as exc:
            assert exc.code == 'native_closing'
        else:
            raise AssertionError('shutdown admitted late storage job')
        assert not called
    finally:
        release_a.set(); release_b.set()
        await asyncio.gather(a, b)
        if 'stop' in locals(): await stop
    assert adapter.base_disconnected
asyncio.run(test())
''')

