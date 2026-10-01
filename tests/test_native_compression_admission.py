"""Dynamic admission identity: real native provenance, no live homes or network."""
import os
from pathlib import Path
import subprocess
import json
import sqlite3
from contextlib import closing

import pytest

from backend.native_catalog import NativeCatalog
from backend.orchestration import Orchestrator
from backend.runs import RunConflict, RunJournal
from test_orchestration import USER, complete_context
from test_parallel_sessions import ParallelGateway


@pytest.fixture
def native_catalog(tmp_path):
    home = tmp_path / 'tiny-native'
    home.mkdir()
    with sqlite3.connect(home / 'state.db') as c:
        c.executescript('''CREATE TABLE sessions(id TEXT PRIMARY KEY, parent_session_id TEXT,
            end_reason TEXT, source TEXT, model_config TEXT);
            CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT);''')
    return NativeCatalog({'default': home})


def native_row(catalog, sid, parent=None, reason=None, source='test', config=None):
    with sqlite3.connect(catalog.profiles['default'] / 'state.db') as c:
        c.execute('INSERT INTO sessions VALUES (?,?,?,?,?)',
                  (sid, parent, reason, source, json.dumps(config or {})))


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['queued', 'running', 'stopping', 'waiting_for_approval', 'unknown', 'unrecognized'])
@pytest.mark.parametrize('other_user', [False, True])
async def test_rotated_tip_fences_all_unresolved_states_without_rewriting_anchors(tmp_path, native_catalog, status, other_user):
    native_row(native_catalog, 'old')
    journal = RunJournal(tmp_path / 'runs.db')
    first, _ = journal.submit('owner', 'default', 'old', 'one', 'one',
        history_anchor=lambda: native_catalog.history_anchor('default', 'old'))
    before = journal.latest('owner', 'default', 'old')['anchor']
    with closing(journal.connect()) as c, c:
        c.execute('UPDATE runs SET status=? WHERE id=?', (status, first['id']))
    with sqlite3.connect(native_catalog.profiles['default'] / 'state.db') as c:
        c.execute("UPDATE sessions SET end_reason='compression' WHERE id='old'")
    native_row(native_catalog, 'middle', 'old', 'compression')
    native_row(native_catalog, 'tip', 'middle')
    # Deliberately no gateway alias: live native provenance must supply identity.
    runtime = Orchestrator(RunJournal(journal.path), ParallelGateway(), native_catalog,
                           history_loader=complete_context)
    try:
        user = dict(USER, id='other') if other_user else USER
        with pytest.raises(RunConflict, match='active or unresolved'):
            await runtime.submit(user, dict(session_id='tip', input='two', idempotency_key='two'))
        assert journal.latest('owner', 'default', 'old')['anchor'] == before
        with closing(journal.connect()) as c:
            assert c.execute('SELECT COUNT(*) FROM runs').fetchone()[0] == 1
        replay = await runtime.submit(USER, dict(session_id='old', input='one', idempotency_key='one'))
        assert replay['id'] == first['id']
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('source,config,parent_reason', [
    ('test', {'_branched_from': 'parent'}, 'compression'),
    ('subagent', {'_delegate_from': 'parent'}, 'compression'),
    ('test', {'_reset_from': 'parent'}, 'compression'),
    ('tool', {}, 'compression'),
    ('test', {}, 'completed'),
    ('test', {}, 'reset'),
    ('test', {}, None),
])
async def test_real_forks_remain_independent(tmp_path, native_catalog, source, config, parent_reason):
    native_row(native_catalog, 'parent', reason=parent_reason)
    native_row(native_catalog, 'child', 'parent', source=source, config=config)
    journal = RunJournal(tmp_path / 'runs.db')
    journal.submit('owner', 'default', 'parent', 'one', 'one')
    runtime = Orchestrator(journal, ParallelGateway(), native_catalog, history_loader=complete_context)
    try:
        child = await runtime.submit(USER, dict(session_id='child', input='two', idempotency_key='two'))
        assert child['status'] == 'queued'
        assert native_catalog.conversation_roots('default', {'parent', 'child'}) == {
            'parent': 'parent', 'child': 'child'}
    finally:
        await runtime.close()


@pytest.mark.parametrize('marker', ['_branched_from', '_delegate_from'])
def test_copied_fork_markers_do_not_split_its_own_compression(native_catalog, marker):
    native_row(native_catalog, 'parent', reason='compression')
    native_row(native_catalog, 'fork', 'parent', 'compression', config={marker: 'parent'})
    native_row(native_catalog, 'fork-tip', 'fork', config={marker: 'parent'})
    assert native_catalog.conversation_roots('default', {'parent', 'fork', 'fork-tip'}) == {
        'parent': 'parent', 'fork': 'fork', 'fork-tip': 'fork'}


def test_independent_connections_race_across_rotated_ids(tmp_path, native_catalog):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    native_row(native_catalog, 'old', reason='compression')
    native_row(native_catalog, 'tip', 'old')
    journals = [RunJournal(tmp_path / 'runs.db') for _ in range(2)]
    ready = Barrier(2)

    def submit(index):
        sid = ('old', 'tip')[index]
        ready.wait(timeout=10)
        try:
            return journals[index].submit(f'owner-{index}', 'default', sid, 'hello', sid,
                history_anchor=lambda: native_catalog.history_anchor('default', sid),
                conversation_roots=lambda ids: native_catalog.conversation_roots('default', ids))[0]
        except RunConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert len([r for r in results if r]) == 1
    with closing(journals[0].connect()) as c:
        assert c.execute('SELECT COUNT(*) FROM runs').fetchone()[0] == 1
        assert c.execute('SELECT COUNT(*) FROM run_history_anchors').fetchone()[0] == 1


def test_live_lineage_is_read_under_writer_lock_and_native_read_is_closed(tmp_path, native_catalog):
    native_row(native_catalog, 'old')
    journal = RunJournal(tmp_path / 'runs.db')
    journal.submit('owner', 'default', 'old', 'one', 'one')
    # The child is published after the anchor, immediately before identity read.
    def anchor():
        with sqlite3.connect(native_catalog.profiles['default'] / 'state.db') as c:
            c.execute("UPDATE sessions SET end_reason='compression' WHERE id='old'")
        native_row(native_catalog, 'tip', 'old')
        return native_catalog.history_anchor('default', 'tip')

    def roots(ids):
        with closing(sqlite3.connect(journal.path, timeout=0)) as c:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                c.execute('BEGIN IMMEDIATE')
        result = native_catalog.conversation_roots('default', ids)
        # Exclusive lock proves the read-only snapshot does not escape callback.
        with closing(sqlite3.connect(native_catalog.profiles['default'] / 'state.db', timeout=0)) as c:
            c.execute('BEGIN EXCLUSIVE')
            c.rollback()
        return result

    with pytest.raises(RunConflict, match='active or unresolved'):
        journal.submit('other', 'default', 'tip', 'two', 'two',
                       history_anchor=anchor, conversation_roots=roots)


def test_installed_native_midrun_rotation_rejects_duplicate(tmp_path):
    home = tmp_path / 'isolated-home'
    native = home / '.hermes'
    native.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1]
    env = {'HOME': str(home), 'HERMES_HOME': str(native),
           'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
           'PYTHONPATH': os.pathsep.join((str(source), str(source / 'tests'), '/usr/local/lib/hermes-agent')),
           'PYTHONDONTWRITEBYTECODE': '1',
           'XDG_CACHE_HOME': str(home / '.cache'), 'XDG_CONFIG_HOME': str(home / '.config')}
    result = subprocess.run([
        os.environ.get('HERMES_TEST_PYTHON', '/home/lindayi/projects/hermes-mobile/.venv/bin/python'),
        '-c', '''
import sys
sys.path.append('/usr/local/lib/hermes-agent/venv/lib/python3.11/site-packages')
import asyncio
import os
from pathlib import Path
from hermes_state import SessionDB
from backend.native_catalog import NativeCatalog
from backend.orchestration import Orchestrator
from backend.runs import RunConflict, RunJournal
from test_parallel_sessions import ParallelGateway
from test_orchestration import USER, until

async def main():
    home = Path(os.environ['HOME']) / 'native-data'
    home.mkdir()
    db = SessionDB(db_path=home / 'state.db')
    db.create_session('native', source='test')
    assert db.try_acquire_session_turn_lease('native', 'first-holder')
    journal = RunJournal(home / 'runs.db')
    catalog = NativeCatalog({'default': home})
    def history(profile, sid):
        return dict(profile=profile, session_id=sid, complete=True, history=[],
                    canonical_session_id=db.resolve_resume_session_id(sid))
    runtime = Orchestrator(journal, ParallelGateway(), catalog, history_loader=history)
    try:
        first = await runtime.submit(USER, dict(session_id='native', input='one', idempotency_key='one'))
        await until(lambda: runtime.get(USER, first['id'])['status'] == 'running')
        before = journal.latest('owner', 'default', 'native')['anchor']
        assert before['canonical_session_id'] == 'native'
        db.end_session('native', 'compression')
        db.create_session('new-tip', source='test', parent_session_id='native')
        assert db.resolve_resume_session_id('native') == 'new-tip'
        assert db.resolve_resume_session_id('new-tip') == 'new-tip'
        assert not db.try_acquire_session_turn_lease('new-tip', 'second-holder')
        try:
            await runtime.submit(USER, dict(session_id='new-tip', input='two', idempotency_key='two'))
        except RunConflict:
            pass
        else:
            raise AssertionError('Mid-run compression admitted a duplicate conversation')
        with journal.connect() as c:
            assert c.execute('SELECT COUNT(*) FROM runs').fetchone()[0] == 1
        assert journal.latest('owner', 'default', 'native')['anchor'] == before
    finally:
        await runtime.close()
        db.close()

asyncio.run(main())
'''], cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, timeout=60)
    assert result.returncode == 0, result.stdout
