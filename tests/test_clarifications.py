import asyncio
import time
from contextlib import closing
from types import SimpleNamespace

import pytest

from backend.clarifications import ClarificationJournal
from backend.runs import RunConflict, RunJournal


OWNER = {'id': 'owner', 'profile': 'default', 'role': 'owner'}


def pending(question_id='a' * 32, **overrides):
    now = time.time()
    return {
        'question_id': question_id, 'run_id': 'native-run',
        'question': 'Choose a plan?', 'choices': ['Keep current', 'Change it'],
        'multi_select': False, 'status': 'pending', 'answer': None, 'other': None,
        'created_at': now, 'updated_at': now, **overrides,
    }


def run_state(tmp_path):
    journal = RunJournal(tmp_path / 'runs.sqlite')
    run, _ = journal.submit('owner', 'default', 'session', 'Original', 'original')
    journal.set_upstream('owner', run['id'], 'native-run')
    journal.set_active_status('owner', run['id'], 'waiting_for_clarification',
                              upstream_id='native-run')
    return journal, journal.get('owner', run['id'])


def test_waiter_answer_is_idempotent_owned_and_replayed_in_original_position(tmp_path):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    item = pending()
    view = bridge.event(OWNER, run, {**item, 'event': 'run.clarification'})
    assert view['status'] == 'pending'
    body = {'answer': 'Change it', 'other': False}
    claimed, fresh = bridge.claim(OWNER, run, item['question_id'], body)
    assert fresh and claimed['status'] == 'sending'
    duplicate, fresh = bridge.claim(OWNER, run, item['question_id'], body)
    assert not fresh and duplicate['status'] == 'sending'
    bridge.finish(OWNER, run['id'], item['question_id'], 'answered')
    duplicate, fresh = bridge.claim(OWNER, run, item['question_id'], body)
    assert not fresh and duplicate['status'] == 'answered'
    replay = journal.events('owner', run['id'])
    with closing(journal.connect()) as connection:
        cards = [event for event in journal._replay_events(connection, run['id'])
                 if event['name'] == 'clarification']
    assert len(cards) == 1
    assert cards[0]['data']['status'] == 'answered'
    assert cards[0]['data']['answer'] == 'Change it'
    with pytest.raises(KeyError):
        bridge.list({'id': 'other-owner', 'profile': 'default'}, run['id'])


def test_completed_run_history_keeps_question_and_answer_at_the_event_position():
    from backend.native_catalog import _journal_turn

    run = {'id': 'local-run', 'created_at': 1, 'status': 'completed', 'error': None,
           'input': 'Original', 'output': 'Done'}
    clarification = {
        'question_id': 'b' * 32, 'run_id': 'local-run', 'session_id': 'session',
        'question': 'Choose a plan?', 'choices': ['Keep current', 'Change it'],
        'multi_select': False, 'status': 'answered', 'answer': 'Change it',
        'other': False, 'created_at': 2, 'updated_at': 4,
    }
    items = _journal_turn(run, [
        {'id': 1, 'name': 'commentary', 'data': {'text': 'Before'}, 'observed_at': 1.5},
        {'id': 2, 'name': 'clarification', 'data': clarification, 'observed_at': 2},
        {'id': 3, 'name': 'commentary', 'data': {'text': 'After'}, 'observed_at': 5},
    ])
    cards = [item for item in items if item.get('kind') == 'clarification']
    assert len(cards) == 1
    assert cards[0]['content'] == 'Choose a plan?'
    assert cards[0]['clarification_status'] == 'answered'
    assert cards[0]['clarification_answer'] == 'Change it'
    assert items.index(cards[0]) == 2


def test_stale_question_cannot_be_claimed_after_stop_intent(tmp_path):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    item = pending()
    bridge.event(OWNER, run, item)
    journal.finish('owner', run['id'], 'stopping')
    with pytest.raises(RunConflict, match='stale'):
        bridge.claim(OWNER, run, item['question_id'],
                     {'answer': 'Keep current', 'other': False})


@pytest.mark.parametrize(('record', 'answer'), [
    (pending(multi_select=True), {'answer': ['Keep current', 'Change it'], 'other': False}),
    (pending(multi_select=True), {'answer': ['Keep current', 'Another'], 'other': True}),
    (pending(choices=None), {'answer': 'A free answer', 'other': False}),
])
def test_native_answer_shape_accepts_supported_choices(record, answer):
    assert ClarificationJournal.validate_answer(record, answer)


@pytest.mark.parametrize(('record', 'answer'), [
    (pending(), {'answer': 'Unlisted', 'other': False}),
    (pending(), {'answer': '', 'other': True}),
    (pending(multi_select=True), {'answer': ['Keep current', 'Keep current'], 'other': False}),
    (pending(multi_select=True), {'answer': ['Keep current', 'Unlisted', 'Also unlisted'], 'other': True}),
    (pending(choices=None), {'answer': 'Free answer', 'other': True}),
])
def test_invalid_or_unrecognized_answers_are_rejected(record, answer):
    assert not ClarificationJournal.validate_answer(record, answer)


@pytest.mark.asyncio
async def test_owned_answer_resolves_the_waiting_native_request_without_new_run(tmp_path):
    from backend.orchestration import Orchestrator

    journal, run = run_state(tmp_path)
    question = pending(multi_select=True)
    native = SimpleNamespace(status='waiting_for_clarification', item=question,
                             answer_gate=asyncio.get_running_loop().create_future(),
                             answer_calls=[])

    class Gateway:
        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def request(self, method, path, **kwargs):
            return {'run_id': 'native-run', 'status': native.status,
                    'clarifications': [native.item]}

        async def answer_clarification(self, run_id, question_id, answer, other):
            assert run_id == 'native-run'
            assert question_id == question['question_id']
            native.answer_calls.append((answer, other))
            native.status = 'running'
            native.item = {**native.item, 'status': 'answered', 'answer': answer,
                           'other': other, 'updated_at': time.time()}
            native.answer_gate.set_result(answer)
            return {'object': 'hermes.run.clarification', 'run_id': run_id,
                    'question_id': question_id, 'status': 'answered', 'answer': answer}

    runtime = Orchestrator(journal, Gateway(), SimpleNamespace(profiles={'default': 'fixture'}))
    async def wait_for_native_answer():
        return await native.answer_gate

    native_waiter = asyncio.create_task(wait_for_native_answer())
    try:
        state = await runtime.clarifications_for_run(OWNER, run['id'])
        assert state['available'] is True
        assert state['items'][0]['status'] == 'pending'
        with pytest.raises(ValueError, match='Invalid clarification'):
            await runtime.answer_clarification(
                OWNER, run['id'], question['question_id'],
                {'answer': ['Keep current', 'Keep current'], 'other': True})
        assert native.answer_calls == []
        assert journal.get('owner', run['id'])['status'] == 'waiting_for_clarification'
        assert runtime.clarifications.list(OWNER, run['id'])[0]['status'] == 'pending'
        answer = ['Keep current', 'A new plan']
        answered = await runtime.answer_clarification(
            OWNER, run['id'], question['question_id'],
            {'answer': answer, 'other': True})
        assert answered['status'] == 'answered'
        assert await native_waiter == answer
        assert native.answer_calls == [(answer, True)]
        assert journal.get('owner', run['id'])['status'] == 'running'
        retry = await runtime.answer_clarification(
            OWNER, run['id'], question['question_id'],
            {'answer': answer, 'other': True})
        assert retry['status'] == 'answered'
        assert native.answer_calls == [(answer, True)]
        with pytest.raises(RunConflict):
            await runtime.answer_clarification(
                OWNER, run['id'], question['question_id'],
                {'answer': ['Change it'], 'other': False})
        with pytest.raises(KeyError):
            await runtime.answer_clarification(
                {**OWNER, 'id': 'other-owner'}, run['id'], question['question_id'],
                {'answer': answer, 'other': True})
        with closing(journal.connect()) as connection:
            assert connection.execute('SELECT count(*) FROM runs').fetchone()[0] == 1
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_recovery_restores_only_verified_unanswered_waiters(tmp_path):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    question = pending()
    bridge.event(OWNER, run, question)
    bridge.recover()
    recovered = bridge.list(OWNER, run['id'])[0]
    assert recovered['status'] == 'unknown'
    assert recovered['updated_at'] >= question['updated_at']

    class Gateway:
        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def request(self, method, path, **kwargs):
            return {'run_id': 'native-run', 'status': 'waiting_for_clarification',
                    'clarifications': [question]}

    state = await bridge.rehydrate(OWNER, run['id'], Gateway(), run)
    restored = state['items'][0]
    assert state['available'] is True
    assert restored['status'] == 'pending'
    assert restored['answer'] is None
    assert restored['updated_at'] >= recovered['updated_at']
    replay = journal.events('owner', run['id'])
    assert replay[-1]['data']['status'] == 'pending'
    assert replay[-1]['data']['updated_at'] == restored['updated_at']


@pytest.mark.asyncio
async def test_rehydrate_does_not_keep_changed_waiter_payload_pending(tmp_path):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    question = pending()
    bridge.event(OWNER, run, question)

    class Gateway:
        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def request(self, method, path, **kwargs):
            changed = {**question, 'question': 'Changed question'}
            return {'run_id': 'native-run', 'status': 'waiting_for_clarification',
                    'clarifications': [changed]}

    state = await bridge.rehydrate(OWNER, run['id'], Gateway(), run)
    assert state['available'] is True
    assert state['items'][0]['status'] == 'unknown'
    assert journal.events('owner', run['id'])[-1]['data']['status'] == 'unknown'


def test_recovery_transition_is_reflected_in_event_replay(tmp_path):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    bridge.event(OWNER, run, pending())
    bridge.recover()
    assert bridge.list(OWNER, run['id'])[0]['status'] == 'unknown'
    replay = journal.events('owner', run['id'])
    assert replay[-1]['data']['status'] == 'unknown'


@pytest.mark.asyncio
async def test_transient_rehydrate_failure_preserves_live_pending_question(tmp_path):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    bridge.event(OWNER, run, pending())

    class Gateway:
        def require_execution(self):
            return None

        async def require_clarifications(self):
            raise RuntimeError('synthetic transport unavailable')

    state = await bridge.rehydrate(OWNER, run['id'], Gateway(), run)
    assert state['available'] is False
    assert state['items'][0]['status'] == 'pending'
    assert journal.events('owner', run['id'])[-1]['data']['status'] == 'pending'


@pytest.mark.asyncio
async def test_valid_native_snapshot_marks_missing_waiter_unknown(tmp_path):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    bridge.event(OWNER, run, pending())

    class Gateway:
        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def request(self, method, path, **kwargs):
            return {'run_id': 'native-run', 'status': 'running', 'clarifications': []}

    state = await bridge.rehydrate(OWNER, run['id'], Gateway(), run)
    assert state['available'] is True
    assert state['items'][0]['status'] == 'unknown'
    assert journal.events('owner', run['id'])[-1]['data']['status'] == 'unknown'


@pytest.mark.asyncio
async def test_uncertain_answer_is_not_restored_from_pending_native_snapshot(tmp_path):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    question = pending()
    bridge.event(OWNER, run, question)
    body = {'answer': 'Change it', 'other': False}
    claimed, fresh = bridge.claim(OWNER, run, question['question_id'], body)
    assert fresh and claimed['status'] == 'sending'
    bridge.recover()
    attempted = bridge.list(OWNER, run['id'])[0]
    assert attempted['status'] == 'unknown'
    assert attempted['answer'] == 'Change it'

    class Gateway:
        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def request(self, method, path, **kwargs):
            return {'run_id': 'native-run', 'status': 'waiting_for_clarification',
                    'clarifications': [question]}

    state = await bridge.rehydrate(OWNER, run['id'], Gateway(), run)
    assert state['items'][0]['status'] == 'unknown'
    assert state['items'][0]['answer'] == 'Change it'
