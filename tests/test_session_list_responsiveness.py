"""Disposable SQLite locks and coordinated late reads; no native mutation transport."""
import asyncio
from contextlib import closing
import sqlite3
import threading
import time

import pytest

from backend.session_deletion import SessionDeletion
from test_auth import BASE
from test_session_deletion import deletion_app


def list_endpoint(app):
    return next(route.endpoint for route in app.routes
                if getattr(route, 'path', None) == BASE + '/sessions')


@pytest.mark.asyncio
@pytest.mark.parametrize('database', ['native', 'journal'])
async def test_sqlite_contention_does_not_block_list_event_loop(deletion_app, database):
    app, _, user, _, _ = deletion_app
    path = (app.state.catalog.profiles['default'] / 'state.db'
            if database == 'native' else app.state.journal.path)
    locked, release = threading.Event(), threading.Event()

    def lock_database():
        with closing(sqlite3.connect(path)) as connection:
            connection.execute('BEGIN EXCLUSIVE')
            locked.set()
            release.wait(0.5)  # Also releases the lock on the broken synchronous path.
            connection.rollback()

    thread = threading.Thread(target=lock_database)
    thread.start()
    await asyncio.to_thread(locked.wait, 2)
    stamps = []
    done = asyncio.Event()

    async def heartbeat():
        while not done.is_set():
            stamps.append(time.monotonic())
            await asyncio.sleep(0.01)
            if len(stamps) >= 5:
                release.set()

    ticker = asyncio.create_task(heartbeat())
    try:
        await asyncio.sleep(0)
        page = await list_endpoint(app)(limit=1, offset=0, q='', kind='chats', user=user)
        await asyncio.sleep(0.02)
        assert page['total'] == 2
        assert [item['id'] for item in page['items']] == ['cli-1']
        assert max(b - a for a, b in zip(stamps, stamps[1:])) < 0.15
    finally:
        release.set()
        done.set()
        await ticker
        await asyncio.to_thread(thread.join, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['prepared', 'deleted', 'deleted-with-descendant'])
@pytest.mark.parametrize('offset', [0, 1])
@pytest.mark.parametrize('journal_mode', ['DELETE', 'WAL'])
async def test_old_list_snapshot_is_revalidated_after_await(
        deletion_app, monkeypatch, outcome, offset, journal_mode):
    app, _, user, calls, _ = deletion_app
    with closing(app.state.journal.connect()) as connection:
        assert connection.execute('PRAGMA journal_mode=' + journal_mode).fetchone()[0] == journal_mode.lower()
    entered, release = threading.Event(), threading.Event()
    original = SessionDeletion._list_snapshot
    reads = []

    def paused_read(self, *args):
        snapshot = original(self, *args)
        reads.append(snapshot[2])
        if len(reads) == 1:
            entered.set()
            assert release.wait(3), 'test did not release the old list snapshot'
        return snapshot

    monkeypatch.setattr(SessionDeletion, '_list_snapshot', paused_read)
    task = asyncio.create_task(list_endpoint(app)(
        limit=1, offset=offset, q='', kind='chats', user=user))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        journal = app.state.journal
        claim = await asyncio.to_thread(
            journal.claim_deletion, user['id'], user['profile'], 'cli-1')
        targets = ['cli-1', 'wa-1'] if outcome == 'deleted-with-descendant' else ['cli-1']
        if outcome != 'prepared':
            await asyncio.to_thread(SessionDeletion(journal)._outcome,
                                    user, claim['operation_id'], 'deleted', targets)
        release.set()
        page = await asyncio.wait_for(task, 3)
        assert page['total'] == (0 if outcome == 'deleted-with-descendant' else 1)
        expected = ['wa-1'] if offset == 0 and outcome != 'deleted-with-descendant' else []
        assert [item['id'] for item in page['items']] == expected
        assert page.get('pending_deletions', []) == (
            [{'id': 'cli-1', 'status': 'unconfirmed'}] if outcome == 'prepared' else [])
        assert len(reads) == 2
        assert all(method == 'GET' and path == '/v1/capabilities' for method, path in calls)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


def test_repeated_changes_fail_closed_after_one_reread(deletion_app, monkeypatch):
    app, client, user, calls, _ = deletion_app
    original = SessionDeletion.list_page
    reads = []

    def changing_read(self, *args):
        page = original(self, *args)
        reads.append(page)
        target = 'cli-1' if len(reads) == 1 else 'wa-1'
        claim = self.journal.claim_deletion(user['id'], user['profile'], target)
        self._outcome(user, claim['operation_id'], 'deleted', [target])
        return page

    monkeypatch.setattr(SessionDeletion, 'list_page', changing_read)
    response = client.get(BASE + '/sessions')
    assert response.status_code == 503
    assert response.json() == {'detail': 'Session list changed during read; retry the list request'}
    assert len(reads) == 2
    assert all(method == 'GET' and path == '/v1/capabilities' for method, path in calls)


@pytest.mark.asyncio
async def test_final_validation_under_exclusive_lock_fails_closed_without_waiting(
        deletion_app, monkeypatch):
    app, _, user, _, _ = deletion_app
    original = SessionDeletion._list_snapshot
    locked, release = threading.Event(), threading.Event()

    def locker():
        with closing(sqlite3.connect(app.state.journal.path)) as connection:
            connection.execute('BEGIN EXCLUSIVE')
            locked.set()
            release.wait(0.5)
            connection.rollback()

    thread = threading.Thread(target=locker)

    def locked_snapshot(self, *args):
        result = original(self, *args)
        thread.start()
        assert locked.wait(2)
        return result

    monkeypatch.setattr(SessionDeletion, '_list_snapshot', locked_snapshot)
    started = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            await list_endpoint(app)(limit=1, offset=0, q='', kind='chats', user=user)
        assert time.monotonic() - started < 0.15
    finally:
        release.set()
        await asyncio.to_thread(thread.join, 2)


@pytest.mark.asyncio
async def test_cancelled_list_closes_observer_only_after_worker_finishes(
        deletion_app, monkeypatch):
    app, _, user, _, _ = deletion_app
    original = SessionDeletion._list_snapshot
    entered, release = threading.Event(), threading.Event()
    snapshots = []
    loop = asyncio.get_running_loop()
    finished = asyncio.Event()

    def paused_snapshot(self, *args):
        result = original(self, *args)
        snapshots.append(result)
        entered.set()
        assert release.wait(3)
        # Cancellation must not close a connection still owned by the worker.
        result[0].execute('PRAGMA data_version').fetchone()
        loop.call_soon_threadsafe(finished.set)
        return result

    monkeypatch.setattr(SessionDeletion, '_list_snapshot', paused_snapshot)
    task = asyncio.create_task(list_endpoint(app)(limit=1, offset=0, q='', kind='chats', user=user))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await asyncio.wait_for(finished.wait(), 2)
        # Allow the thread completion and cancellation cleanup callback to run.
        for _ in range(100):
            try:
                snapshots[0][0].execute('PRAGMA data_version')
            except sqlite3.ProgrammingError as error:
                assert 'closed' in str(error)
                break
            await asyncio.sleep(0.001)
        else:
            pytest.fail('cancelled list leaked its observer connection')
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('query, expected, total', [
    ('?limit=1', ['wa-1'], 1),
    ('?limit=1&offset=1', [], 1),
    ('?q=CLI', [], 0),
    ('?q=WhatsApp', ['wa-1'], 1),
    ('?kind=cron', [], 0),
])
def test_stable_list_preserves_filter_counts_paging_and_private_statuses(
        deletion_app, query, expected, total):
    app, client, user, _, behavior = deletion_app
    journal = app.state.journal
    foreign, _ = journal.submit('someone-else', user['profile'], 'wa-1', 'private', 'foreign-key')
    journal.finish('someone-else', foreign['id'], 'failed', output='private result')
    journal.claim_deletion(user['id'], user['profile'], 'cli-1')
    behavior['version'] = None
    page = client.get(BASE + '/sessions' + query).json()
    assert page['total'] == total
    assert [item['id'] for item in page['items']] == expected
    assert all(item['run_status'] == 'unknown' for item in page['items'])
    assert page['pending_deletions'] == [{'id': 'cli-1', 'status': 'unconfirmed'}]
    assert 'deletion_available' not in page
    assert foreign['id'] not in str(page)
    assert 'private' not in str(page)


@pytest.mark.asyncio
async def test_catalog_status_and_pending_reads_all_run_off_loop(deletion_app, monkeypatch):
    app, _, user, _, _ = deletion_app
    loop_thread = threading.get_ident()
    seen = []

    def check_thread(name, original):
        def wrapped(*args, **kwargs):
            assert threading.get_ident() != loop_thread, name + ' blocked the event loop'
            seen.append(name)
            return original(*args, **kwargs)
        return wrapped

    monkeypatch.setattr(SessionDeletion, 'sessions',
                        check_thread('catalog', SessionDeletion.sessions))
    monkeypatch.setattr(app.state.journal, 'session_statuses',
                        check_thread('statuses', app.state.journal.session_statuses))
    monkeypatch.setattr(SessionDeletion, 'pending',
                        check_thread('pending', SessionDeletion.pending))
    page = await list_endpoint(app)(limit=1, offset=1, q='', kind='chats', user=user)
    assert seen == ['catalog', 'statuses', 'pending']
    assert page['total'] == 2
    assert [item['id'] for item in page['items']] == ['wa-1']
