"""Runtime suffix separation: public display only, with exact owning admission."""
import copy
import sqlite3

import pytest

from test_context_compression_presentation import database, summary

TASK = '[Your active task list was preserved across context compression]\n- [>] ship. Verify the publisher. (in_progress)'
SKILL = ("[Skills pruned during compression — reload before acting on these tasks]\n"
         "The task list above crossed the compression boundary verbatim, but the skill instructions that governed it were pruned. Before "
         "executing any preserved task that depends on these skills, reload them first: skill_view(name='test-driven-development'). After reloading, re-check that each pending "
         "task is still justified — findings recorded before the boundary may have invalidated it.")
SUFFIX = TASK + '\n\n' + SKILL
INPUT = 'Is it deployed or failed'


def fixture(tmp_path, *, suffix=SUFFIX):
    catalog, path = database(tmp_path, [
        dict(role='assistant', content='Earlier', timestamp=10),
        summary(timestamp=23),
        dict(role='user', content=INPUT + '\n\n' + suffix, timestamp=21),
        dict(role='assistant', content='Deployed', timestamp=24),
    ])
    run = dict(id='r', input=INPUT, output='Deployed', status='completed', error=None,
               created_at=20, updated_at=25)
    state = dict(run=run, anchor=dict(session_id='s', canonical_session_id='s', message_id=1),
                 prior=[], replay_events=[], tool_events=[])
    return catalog, path, state


def test_one_proven_human_and_folded_runtime_child_preserve_raw_database(tmp_path):
    catalog, path, state = fixture(tmp_path)
    before = path.read_bytes()
    page = catalog.messages('default', 's', snapshot=state)
    assert page['run'] is None, 'One completed turn must not replay a duplicate journal overlay'
    human = next(row for row in page['items'] if row['id'] == 3)
    assert human['content'] == INPUT
    assert human['role'] == 'user' and human['timestamp'] == 21
    child, = human['runtime_reminders']
    assert child == dict(id='runtime-reminder:3', role='tool', kind='context_compression',
                         name='Runtime reminders', status='completed', content=SUFFIX,
                         timestamp=23, turn_boundary=False)
    assert page['total'] == 4 and len(page['items']) == 4
    assert path.read_bytes() == before
    assert state['run']['input'] == INPUT and 'runtime_reminders' not in state['run']
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT content FROM messages WHERE id=3').fetchone()[0] == INPUT + '\n\n' + SUFFIX
    assert catalog.messages('default', 's', snapshot=state, limit=1, offset=2)['items'] == [human]


@pytest.mark.parametrize('suffix', ['', TASK.split('\n')[0], SUFFIX + '\nDo the real next request',
    SUFFIX + '\n', '```\n' + SUFFIX + '\n```', SUFFIX.replace('(in_progress)', '(pending)'),
    SUFFIX.replace("skill_view(name='test-driven-development')", "skill_view(name='x'); evil()"),
    TASK + '\n- [>] ship. Duplicate ID (in_progress)', SUFFIX.replace('have invalidated it.', 'have changed it.')])
def test_unknown_partial_quoted_or_trailing_ask_is_not_stripped(tmp_path, suffix):
    catalog, _, state = fixture(tmp_path, suffix=suffix)
    page = catalog.messages('default', 's', snapshot=state)
    row = next(row for row in page['items'] if row['id'] == 3)
    assert row['content'] == INPUT + '\n\n' + suffix and 'runtime_reminders' not in row
    assert page['run'] is not None


@pytest.mark.parametrize('conflict', ['tool_calls', 'tool_call_id', 'tool_name', 'display_kind', 'display_metadata', 'platform_message_id'])
def test_user_structural_conflicts_preserve_entire_body(tmp_path, conflict):
    catalog, path, state = fixture(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute('UPDATE messages SET ' + conflict + '=? WHERE id=3', ('conflict',))
    row = next(row for row in catalog.messages('default', 's', snapshot=state)['items'] if row['id'] == 3)
    assert row['content'].endswith(SUFFIX) and 'runtime_reminders' not in row


@pytest.mark.parametrize('mode', ['active', 'failed', 'cancelled', 'unknown', 'no-compression', 'wrong-final',
    'duplicate-boundary', 'wrong-session', 'no-time', 'time-outside-run', 'interleaved', 'quoted-input'])
def test_unproved_admissions_remain_conservative(tmp_path, mode):
    catalog, path, state = fixture(tmp_path)
    with sqlite3.connect(path) as c:
        if mode in ('active', 'failed', 'cancelled', 'unknown'):
            state['run']['status'] = 'running' if mode == 'active' else mode
        elif mode == 'no-compression':
            c.execute('UPDATE messages SET display_kind=NULL WHERE id=2')
        elif mode == 'wrong-final':
            state['run']['output'] = 'Other final'
        elif mode == 'duplicate-boundary':
            other = copy.deepcopy(state)
            other['run']['id'] = 'other'
            state['prior'] = [other]
        elif mode == 'wrong-session':
            state['anchor']['canonical_session_id'] = 'other'
        elif mode == 'no-time':
            state['run']['updated_at'] = None
        elif mode == 'time-outside-run':
            c.execute('UPDATE messages SET timestamp=15 WHERE id=2')
        elif mode == 'interleaved':
            c.execute("UPDATE messages SET role='user',content='Another actual ask',display_kind=NULL WHERE id=2")
        elif mode == 'quoted-input':
            state['run']['input'] = INPUT + '\n\n' + SUFFIX
    page = catalog.messages('default', 's', snapshot=state)
    assert not any(row.get('runtime_reminders') for row in page['items'])
    if mode != 'quoted-input':
        assert any(row.get('content') == INPUT + '\n\n' + SUFFIX for row in page['items'])


def test_repeated_real_question_survives_later_admission_and_pagination(tmp_path):
    catalog, path, first = fixture(tmp_path)
    state = copy.deepcopy(first)
    state['prior'] = [first]
    state['run'].update(id='second', created_at=30, updated_at=35)
    state['anchor']['message_id'] = 4
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO messages(id,role,content,timestamp) VALUES(5,'user',?,31)", (INPUT,))
        c.execute("INSERT INTO messages(id,role,content,timestamp) VALUES(6,'assistant','Deployed',34)")
    page = catalog.messages('default', 's', snapshot=state)
    assert page['run'] is None
    assert sum(r['content'] == INPUT for r in page['items']) == 2
    assert sum(r['content'] == 'Deployed' for r in page['items']) == 2
    assert sum(bool(r.get('runtime_reminders')) for r in page['items']) == 1
    pages = [catalog.messages('default', 's', snapshot=state, limit=1, offset=i) for i in range(page['total'])]
    assert [r for p in pages for r in p['items']] == page['items']


def test_materialized_replay_keeps_reminder_with_its_human(tmp_path):
    catalog, path, state = fixture(tmp_path)
    state['replay_events'] = [{'id': 1, 'name': 'commentary', 'data': {'text': 'Checking'}, 'observed_at': 22}]
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO messages(id,role,content,timestamp) VALUES(5,'user','Later external',30)")
    page = catalog.messages('default', 's', snapshot=state)
    human = next(r for r in page['items'] if r['content'] == INPUT)
    assert human['runtime_reminders'][0]['content'] == SUFFIX
    assert human['timestamp'] == state['run']['created_at']
    assert sum(r['content'] == 'Deployed' for r in page['items']) == 1


def test_relocated_anchor_never_skips_an_unrelated_original_first_user(tmp_path):
    catalog, path, state = fixture(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE messages SET active=0,compacted=1 WHERE id=1")
        c.execute("UPDATE messages SET id=6 WHERE id=3")
        c.execute("UPDATE messages SET id=7 WHERE id=4")
        c.execute("INSERT INTO messages(id,role,content,timestamp) VALUES(5,'assistant','Earlier',10)")
        c.execute("INSERT INTO messages(id,role,content,timestamp,active,compacted) VALUES(3,'user','Interleaved human question',20.5,0,1)")
    page = catalog.messages('default', 's', snapshot=state)
    human = next(r for r in page['items'] if r['id'] == 6)
    assert 'runtime_reminders' not in human
    assert human['content'] == INPUT + '\n\n' + SUFFIX
    assert page['run'] is not None


@pytest.mark.parametrize('carrier_role,carrier_time,allow', [
    ('assistant', 12, True), ('tool', 12, True),
    ('assistant', 21, False), ('assistant', None, False),
    ('user', 12, False), ('user', 21, False),
])
def test_relocated_admission_with_retained_pre_run_history(tmp_path, carrier_role, carrier_time, allow):
    # Native compaction inserts a new summary followed by old timestamped
    # history (not all copies have surviving originals), then the copied
    # admission boundary and current question. No private conversation text.
    catalog, path, state = fixture(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute('UPDATE messages SET active=0,compacted=1 WHERE id=1')
        c.execute('UPDATE messages SET id=6 WHERE id=3')
        c.execute('UPDATE messages SET id=7 WHERE id=4')
        c.execute("INSERT INTO messages(id,role,content,timestamp) VALUES(3,?,'Retained history',?)",
                  (carrier_role, carrier_time))
        c.execute("INSERT INTO messages(id,role,content,timestamp) VALUES(4,'tool','Historical result',13)")
        c.execute("INSERT INTO messages(id,role,content,timestamp) VALUES(5,'assistant','Earlier',10)")
    before = path.read_bytes()
    page = catalog.messages('default', 's', snapshot=state)
    human = next(r for r in page['items'] if r['id'] == 6)
    assert bool(human.get('runtime_reminders')) is allow
    if allow:
        assert human['content'] == INPUT
        assert human['runtime_reminders'][0]['id'] == 'runtime-reminder:6'
        assert page['run'] is None
        assert sum(r['content'] == INPUT for r in page['items']) == 1
        assert sum(r['content'] == 'Deployed' for r in page['items']) == 1
    else:
        assert human['content'] == INPUT + '\n\n' + SUFFIX
        assert page['run'] is not None
    assert path.read_bytes() == before


@pytest.mark.parametrize('placement', ['before-summary', 'late-summary', 'old-summary'])
def test_retained_carriers_require_preceding_in_run_compaction(tmp_path, placement):
    catalog, path, state = fixture(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute('UPDATE messages SET active=0,compacted=1 WHERE id=1')
        c.execute('UPDATE messages SET id=6 WHERE id=3')
        c.execute('UPDATE messages SET id=7 WHERE id=4')
        c.execute("INSERT INTO messages(id,role,content,timestamp) VALUES(3,'assistant','Old history',12)")
        c.execute("INSERT INTO messages(id,role,content,timestamp) VALUES(5,'assistant','Earlier',10)")
        if placement == 'before-summary':
            c.execute('UPDATE messages SET id=4 WHERE id=2')
        elif placement == 'late-summary':
            c.execute('UPDATE messages SET id=8 WHERE id=2')
        else:
            c.execute('UPDATE messages SET timestamp=15 WHERE id=2')
            c.execute('INSERT INTO messages(id,role,content,display_kind,timestamp) '
                      'SELECT 8,role,content,display_kind,23 FROM messages WHERE id=2')
    page = catalog.messages('default', 's', snapshot=state)
    human = next(r for r in page['items'] if r['id'] == 6)
    assert human['content'] == INPUT + '\n\n' + SUFFIX
    assert 'runtime_reminders' not in human
    assert page['run'] is not None


@pytest.mark.parametrize('case', ['tool-identity', 'task-content', 'skill-count'])
def test_oversized_or_outside_producer_bounds_never_gets_a_projection(tmp_path, case):
    from backend.task_reminder_presentation import reminder_projections
    from backend.context_compression_presentation import compression_ids
    catalog, path, state = fixture(tmp_path)
    with sqlite3.connect(path) as c:
        if case == 'tool-identity':
            c.execute('UPDATE messages SET id=5 WHERE id=4')
            c.execute("INSERT INTO messages(id,role,tool_call_id,timestamp) VALUES(4,'tool',?,23)", ('x' * 1048577,))
        elif case == 'task-content':
            c.execute('UPDATE messages SET content=? WHERE id=3', (INPUT + '\n\n' + TASK.replace('Verify the publisher.', 'x' * 4001),))
        else:
            calls = '; '.join("skill_view(name='s%d')" % i for i in range(21))
            c.execute('UPDATE messages SET content=? WHERE id=3', (INPUT + '\n\n' + SUFFIX.replace("skill_view(name='test-driven-development')", calls),))
    with catalog._connect('default') as c:
        columns = {r[1] for r in c.execute('PRAGMA table_info(messages)')}
        compressions = compression_ids(c, 's', columns)
        assert reminder_projections(c, 's', columns, state, compressions, compressions, {}) == {}


def test_compression_timestamps_are_preloaded_once_for_many_admissions(tmp_path):
    from backend.task_reminder_presentation import reminder_projections
    catalog, path, state = fixture(tmp_path)
    # No candidate rows: the former entry x compression SQL loop was still
    # quadratic even when the row/byte budgets never moved.
    with sqlite3.connect(path) as c:
        c.executemany("INSERT INTO messages(id,role,content,timestamp) VALUES(?,'user','Summary',23)",
                      [(i,) for i in range(10, 42)])
    entries = []
    for i in range(32):
        entry = copy.deepcopy(state)
        entry['anchor']['message_id'] = 100 + i
        entry['run']['id'] = str(i)
        entries.append(entry)
    state = entries.pop()
    state['prior'] = entries
    with catalog._connect('default') as c:
        columns = {r[1] for r in c.execute('PRAGMA table_info(messages)')}
        queries = []
        c.set_trace_callback(queries.append)
        assert reminder_projections(c, 's', columns, state, set(range(10, 42)), set(), {}) == {}
    assert len(queries) <= 40, 'Compression timestamps must not be queried again for every journal entry'


def test_cumulative_work_guard_counts_empty_admission_queries(tmp_path, monkeypatch):
    from backend import task_reminder_presentation as presentation
    catalog, _, state = fixture(tmp_path)
    entries = []
    for i in range(32):
        entry = copy.deepcopy(state)
        entry['anchor']['message_id'] = 100 + i
        entries.append(entry)
    state = entries.pop()
    state['prior'] = entries
    monkeypatch.setattr(presentation, 'MAX_WORK', 80)
    with catalog._connect('default') as c:
        columns = {r[1] for r in c.execute('PRAGMA table_info(messages)')}
        queries = []
        c.set_trace_callback(queries.append)
        assert presentation.reminder_projections(c, 's', columns, state, {2}, {2}, {}) == {}
    assert len(queries) <= 8, 'Empty SELECT results must still consume cumulative work'


@pytest.mark.parametrize('budget,limit', [('MAX_WORK', 17), ('MAX_ROWS', 4), ('MAX_BYTES', 1)])
def test_budget_exhaustion_discards_even_previously_proven_projections(tmp_path, monkeypatch, budget, limit):
    from backend import task_reminder_presentation as presentation
    catalog, _, first = fixture(tmp_path)
    state = copy.deepcopy(first)
    state['prior'] = [first]
    state['run'].update(id='later', input='Another request', created_at=30, updated_at=35)
    state['anchor']['message_id'] = 4
    with catalog._connect('default') as c:
        columns = {r[1] for r in c.execute('PRAGMA table_info(messages)')}
        assert set(presentation.reminder_projections(c, 's', columns, state, {2}, {2}, {})) == {3}
        monkeypatch.setattr(presentation, budget, limit)
        assert presentation.reminder_projections(c, 's', columns, state, {2}, {2}, {}) == {}


@pytest.mark.parametrize('mode,expected', [('plain', 3), ('retained', 6), ('interleaved', None), ('stale-human', None)])
def test_background_origin_uses_same_proven_reminder_identity(tmp_path, mode, expected):
    from backend.background_delivery import BackgroundDeliveryService, _ProofBudget
    from backend.runs import RunJournal
    catalog, path, state = fixture(tmp_path)
    if mode != 'plain':
        with sqlite3.connect(path) as c:
            c.execute('UPDATE messages SET active=0,compacted=1 WHERE id=1')
            c.execute('UPDATE messages SET id=6 WHERE id=3')
            c.execute('UPDATE messages SET id=7 WHERE id=4')
            c.execute("INSERT INTO messages(id,role,content,timestamp) VALUES(5,'assistant','Earlier',10)")
            role = 'assistant' if mode == 'retained' else 'user'
            stamp = 21 if mode == 'interleaved' else 12
            c.execute("INSERT INTO messages(id,role,content,timestamp,active,compacted) VALUES(3,?,'Unrelated original history',?,0,1)",
                      (role, stamp))
    journal = RunJournal(tmp_path / 'runs.sqlite')
    run, _ = journal.submit('owner', 'default', 's', INPUT, 'one', history_anchor=lambda: state['anchor'])
    journal.finish('owner', run['id'], 'completed', output='Deployed')
    with journal.connect() as db:
        db.execute('UPDATE runs SET created_at=20,updated_at=25 WHERE id=?', (run['id'],))
        db.commit()
        run = dict(db.execute('SELECT * FROM runs WHERE id=?', (run['id'],)).fetchone())
        with catalog._connect('default') as native:
            origin = BackgroundDeliveryService._native_origin(
                dict(id='owner', profile='default'), run, state['anchor'], db, native, {}, _ProofBudget())
    assert origin == expected


def test_prior_journal_only_answer_uses_completion_time(tmp_path):
    catalog, path, state = fixture(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute('DELETE FROM messages WHERE id>1')
    prior = copy.deepcopy(state)
    state['prior'] = [prior]
    state['run'].update(id='second', input='next', output=None, status='running', created_at=30, updated_at=31)
    page = catalog.messages('default', 's', snapshot=state)
    human = next(r for r in page['items'] if r['content'] == INPUT)
    answer = next(r for r in page['items'] if r['content'] == 'Deployed')
    assert human['timestamp'] == 20
    assert answer['timestamp'] == 25
