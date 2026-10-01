"""Deletion route fixtures must not run unrelated lifespan capability probes."""
import asyncio
from contextlib import closing

from test_auth import BASE
from test_session_deletion import deletion_app


def test_invalid_delete_has_no_native_calls_after_enrollment(monkeypatch, request):
    # Force the old fixture's first real worker turn to run after enrollment.
    # Events control ordering; no sleeps, shortened timeouts, or filtered calls.
    release, first_turn_done = asyncio.Event(), asyncio.Event()
    workers = []
    create_task, wait_for = asyncio.create_task, asyncio.wait_for

    def controlled_create_task(coro, *args, **kwargs):
        if getattr(coro, '__qualname__', '') == 'create_app.<locals>.lifespan.<locals>.drain_push':
            async def after_enrollment():
                try:
                    await release.wait()
                    await coro
                finally:
                    coro.close()
            task = create_task(after_enrollment(), *args, **kwargs)
            workers.append(task)
            return task
        return create_task(coro, *args, **kwargs)

    async def observed_wait_for(awaitable, timeout):
        if timeout == 15:
            first_turn_done.set()
        return await wait_for(awaitable, timeout)

    monkeypatch.setattr(asyncio, 'create_task', controlled_create_task)
    monkeypatch.setattr(asyncio, 'wait_for', observed_wait_for)
    app, client, user, calls, _ = request.getfixturevalue('deletion_app')
    assert user['status'] == 'ready'
    assert not calls  # Enrollment itself must not call native services.

    if workers:
        async def finish_first_turn():
            release.set()
            await first_turn_done.wait()
        client.portal.call(finish_first_turn)

    result = client.request('DELETE', BASE+'/sessions/cli-1', json={'confirm': 1})
    assert result.status_code == 422
    with closing(app.state.journal.connect()) as db:
        assert db.execute('SELECT count(*) FROM session_deletions').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM events').fetchone()[0] == 0
    assert not calls
    assert not workers
