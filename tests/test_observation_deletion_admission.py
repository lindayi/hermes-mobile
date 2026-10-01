"""Local observation admission uses real awaits and disposable SQLite state."""
import asyncio
from contextlib import closing

import pytest

from backend.runs import RunConflict
from test_orchestration import BODY, USER, runtime_at
from test_run_tracking_recovery import existing_run


@pytest.mark.asyncio
@pytest.mark.parametrize('session_id', [BODY['session_id'], 'other-session-in-profile'])
@pytest.mark.parametrize('outcome', ['returned', 'failed', 'cancelled'])
async def test_deletion_waits_for_observer_even_after_authoritative_terminal(tmp_path, session_id, outcome):
    runtime, journal, gateway = runtime_at(tmp_path)
    run = existing_run(runtime, journal)
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        if outcome == 'failed':
            raise RuntimeError('transport unavailable')
        return dict(run_id='upstream', status='running', pending_steer='late control evidence')

    gateway.request = delayed
    observer = asyncio.create_task(runtime.refresh(USER, run['id']))
    try:
        async with asyncio.timeout(2):
            await entered.wait()
        journal.finish(USER['id'], run['id'], 'completed', output='authoritative')
        # There are no tracked stream tasks or control locks: this is a refresh.
        assert not runtime._tasks and not runtime._dispatching and not runtime._control_locks
        lock = runtime._observation_locks[run['id']]
        assert lock.locked()
        with pytest.raises(RunConflict, match='local workers'):
            runtime.claim_deletion(USER, session_id)
        with closing(journal.connect()) as db:
            assert db.execute('SELECT count(*) FROM session_deletions').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM session_deletion_operations').fetchone()[0] == 0

        if outcome == 'cancelled':
            observer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await observer
        else:
            release.set()
            result = await observer
            assert (result['status'], result['output']) == ('completed', 'authoritative')
        assert not lock.locked()
        # A retained but unlocked observation lock must not permanently fence deletion.
        assert runtime.claim_deletion(USER, session_id)['state'] == 'prepared'
        assert not gateway.starts and not gateway.stops
    finally:
        release.set()
        observer.cancel()
        await asyncio.gather(observer, return_exceptions=True)
        await runtime.close()
