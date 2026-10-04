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


def test_unicode_multiselect_retry_after_native_answer_event_is_idempotent(tmp_path):
    import json

    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    choices = ['Cafe with accented e', 'Tokyo in Japanese']
    answer = ['Café with accented e', '東京 in Japanese']
    item = pending(choices=choices, multi_select=True)
    bridge.event(OWNER, run, item)
    body = {'answer': answer, 'other': False}
    claimed, fresh = bridge.claim(OWNER, run, item['question_id'], body)
    assert fresh and claimed['status'] == 'sending'

    native_answer = {
        **item, 'status': 'answered', 'answer': answer, 'other': False,
        'updated_at': claimed['updated_at'] + 1,
    }
    observed = bridge.event(OWNER, run, native_answer)
    assert observed['status'] == 'answered'
    legacy_answer = json.dumps(answer)
    assert legacy_answer != bridge._encode_answer(answer)
    with closing(journal.connect()) as connection, connection:
        connection.execute('''UPDATE clarifications SET answer=?
            WHERE user_id=? AND profile=? AND run_id=? AND question_id=?''',
            (legacy_answer, OWNER['id'], OWNER['profile'], run['id'], item['question_id']))

    duplicate, fresh = bridge.claim(OWNER, run, item['question_id'], body)
    assert not fresh
    assert duplicate['status'] == 'answered'
    assert duplicate['answer'] == answer
    with pytest.raises(RunConflict):
        bridge.claim(OWNER, run, item['question_id'],
                     {'answer': answer, 'other': True})


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
        assert state['run_id'] == run['id']
        assert state['status'] == 'waiting_for_clarification'
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
async def test_stale_run_snapshot_preserves_question_published_by_native_stream(tmp_path):
    from backend.orchestration import Orchestrator

    journal, run = run_state(tmp_path)
    question = pending('b' * 32)

    class Gateway:
        def __init__(self):
            self.event_queue = asyncio.Queue()
            self.started = asyncio.Event()
            self.event_delivered = asyncio.Event()
            self.get_started = asyncio.Event()
            self.release_get = asyncio.Event()

        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def start(self, *args, **kwargs):
            self.started.set()
            return {'run_id': 'native-run'}

        async def events(self, run_id):
            while True:
                event = await self.event_queue.get()
                self.event_delivered.set()
                yield event

        async def request(self, method, path, **kwargs):
            self.get_started.set()
            await self.release_get.wait()
            return {'run_id': 'native-run', 'status': 'running', 'clarifications': []}

    gateway = Gateway()
    runtime = Orchestrator(journal, gateway, SimpleNamespace(profiles={'default': 'fixture'}))
    stream = asyncio.create_task(runtime._stream(OWNER, run, []))
    try:
        await gateway.started.wait()
        journal.finish('owner', run['id'], 'unknown')
        snapshot = asyncio.create_task(runtime.clarifications_for_run(OWNER, run['id']))
        await gateway.get_started.wait()
        await gateway.event_queue.put({'event': 'run.clarification', **question})
        await gateway.event_delivered.wait()
        await asyncio.sleep(0)
        gateway.release_get.set()

        await snapshot
        await asyncio.sleep(0.01)
        assert [(item['question_id'], item['status'])
                for item in runtime.clarifications.list(OWNER, run['id'])] == [
                    (question['question_id'], 'pending')]
        assert journal.get('owner', run['id'])['status'] == 'waiting_for_clarification'

        saved = runtime.clarifications.list(OWNER, run['id'])
        assert journal.get('owner', run['id'])['status'] == 'waiting_for_clarification'
        assert [(item['question_id'], item['status']) for item in saved] == [
            (question['question_id'], 'pending')]
        assert ClarificationJournal.validate_answer(
            saved[0], {'answer': 'Change it', 'other': False})
    finally:
        gateway.release_get.set()
        await gateway.event_queue.put({
            'event': 'run.completed', 'run_id': 'native-run', 'output': 'continued'})
        await asyncio.gather(stream, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
async def test_late_answer_ack_preserves_next_question_and_same_run_continuation(tmp_path):
    from backend.orchestration import Orchestrator

    journal, run = run_state(tmp_path)
    first = pending('c' * 32)
    next_question = pending('d' * 32, question='Choose the next step?')

    class Gateway:
        def __init__(self):
            self.event_queue = asyncio.Queue()
            self.started = asyncio.Event()
            self.event_delivered = asyncio.Event()
            self.answer_started = asyncio.Event()
            self.release_answer = asyncio.Event()
            self.starts = []
            self.answers = []

        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def start(self, *args, **kwargs):
            self.starts.append(args)
            self.started.set()
            return {'run_id': 'native-run'}

        async def events(self, run_id):
            while True:
                event = await self.event_queue.get()
                self.event_delivered.set()
                yield event

        async def answer_clarification(self, run_id, question_id, answer, other):
            self.answers.append((run_id, question_id, answer, other))
            if question_id == first['question_id']:
                self.answer_started.set()
                await self.release_answer.wait()
            return {'status': 'answered'}

    gateway = Gateway()
    runtime = Orchestrator(journal, gateway, SimpleNamespace(profiles={'default': 'fixture'}))
    stream = asyncio.create_task(runtime._stream(OWNER, run, []))
    try:
        await gateway.started.wait()
        runtime.clarifications.event(OWNER, runtime.get(OWNER, run['id']), first)
        journal.set_active_status('owner', run['id'], 'waiting_for_clarification',
                                  upstream_id='native-run')
        answer_task = asyncio.create_task(runtime.answer_clarification(
            OWNER, run['id'], first['question_id'],
            {'answer': 'Change it', 'other': False}))
        await gateway.answer_started.wait()
        await gateway.event_queue.put({'event': 'run.clarification', **next_question})
        await gateway.event_delivered.wait()
        await asyncio.sleep(0)
        gateway.release_answer.set()
        answered = await answer_task

        await asyncio.sleep(0.01)
        assert [(item['question_id'], item['status'], item['answer'])
                for item in runtime.clarifications.list(OWNER, run['id'])] == [
                    (first['question_id'], 'answered', 'Change it'),
                    (next_question['question_id'], 'pending', None),
                ]
        assert journal.get('owner', run['id'])['status'] == 'waiting_for_clarification'

        saved = runtime.clarifications.list(OWNER, run['id'])
        assert answered['status'] == 'answered'
        assert journal.get('owner', run['id'])['status'] == 'waiting_for_clarification'
        answerable = await runtime.answer_clarification(
            OWNER, run['id'], next_question['question_id'],
            {'answer': 'Keep current', 'other': False})
        assert answerable['status'] == 'answered'
        assert len(gateway.starts) == 1
        assert gateway.answers == [
            ('native-run', first['question_id'], 'Change it', False),
            ('native-run', next_question['question_id'], 'Keep current', False),
        ]
        retry = await runtime.answer_clarification(
            OWNER, run['id'], first['question_id'],
            {'answer': 'Change it', 'other': False})
        assert retry['status'] == 'answered'
        assert len(gateway.answers) == 2
        with pytest.raises(RunConflict):
            await runtime.answer_clarification(
                OWNER, run['id'], first['question_id'],
                {'answer': 'Keep current', 'other': False})
    finally:
        gateway.release_answer.set()
        await gateway.event_queue.put({
            'event': 'run.completed', 'run_id': 'native-run', 'output': 'continued'})
        await asyncio.gather(stream, return_exceptions=True)
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


@pytest.mark.asyncio
async def test_native_confirmation_promotes_recovered_unicode_answer_monotonically(tmp_path):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    choices = ['Cafe with accented e', 'Tokyo in Japanese']
    answer = ['Café with accented e', '東京 in Japanese']
    question = pending(choices=choices, multi_select=True, created_at=20, updated_at=20)
    bridge.event(OWNER, run, question)
    body = {'answer': answer, 'other': False}
    claimed, fresh = bridge.claim(OWNER, run, question['question_id'], body)
    assert fresh and claimed['status'] == 'sending'
    bridge.recover()
    attempted = bridge.list(OWNER, run['id'])[0]
    assert attempted['status'] == 'unknown'

    native_answer = {
        **question, 'status': 'answered', 'answer': answer, 'other': False,
        'updated_at': 25,
    }

    class Gateway:
        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def request(self, method, path, **kwargs):
            assert method == 'GET'
            assert path == '/v1/runs/native-run'
            return {'run_id': 'native-run', 'status': 'running',
                    'clarifications': [native_answer]}

    from backend.orchestration import Orchestrator
    runtime = Orchestrator(
        journal, Gateway(), SimpleNamespace(profiles={'default': 'fixture'}))
    try:
        state = await runtime.clarifications_for_run(OWNER, run['id'])
    finally:
        await runtime.close()
    confirmed = state['items'][0]
    assert state['available'] is True
    assert state['run_id'] == run['id']
    assert state['status'] == 'running'
    assert confirmed['status'] == 'answered'
    assert confirmed['answer'] == answer
    assert confirmed['other'] is False
    assert confirmed['updated_at'] >= attempted['updated_at']
    assert journal.get('owner', run['id'])['status'] == 'running'
    duplicate, fresh = bridge.claim(OWNER, run, question['question_id'], body)
    assert not fresh
    assert duplicate['status'] == 'answered'
    with pytest.raises(RunConflict):
        bridge.claim(OWNER, run, question['question_id'],
                     {'answer': ['Café with accented e', 'Another answer'], 'other': False})
    with pytest.raises(RunConflict):
        bridge.claim(OWNER, run, question['question_id'],
                     {'answer': answer, 'other': True})


@pytest.mark.asyncio
@pytest.mark.parametrize(('answer', 'other'), [
    (['Café with accented e', 'A different answer'], False),
    (['Café with accented e', '東京 in Japanese'], True),
])
async def test_conflicting_native_answer_does_not_replace_recovered_attempt(
        tmp_path, answer, other):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    choices = ['Cafe with accented e', 'Tokyo in Japanese']
    attempted_answer = ['Café with accented e', '東京 in Japanese']
    question = pending(choices=choices, multi_select=True, created_at=20, updated_at=20)
    bridge.event(OWNER, run, question)
    bridge.claim(OWNER, run, question['question_id'],
                 {'answer': attempted_answer, 'other': False})
    bridge.recover()
    attempted = bridge.list(OWNER, run['id'])[0]

    class Gateway:
        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def request(self, method, path, **kwargs):
            return {'run_id': 'native-run', 'status': 'running',
                    'clarifications': [{
                        **question, 'status': 'answered', 'answer': answer,
                        'other': other, 'updated_at': 25,
                    }]}

    state = await bridge.rehydrate(OWNER, run['id'], Gateway(), run)
    retained = state['items'][0]
    assert retained['status'] == 'unknown'
    assert retained['answer'] == attempted_answer
    assert retained['other'] is False
    assert retained['updated_at'] == attempted['updated_at']


def test_recovered_confirmation_cannot_cross_owner_profile_run_or_question(tmp_path):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    answer = ['Café with accented e', '東京 in Japanese']
    question = pending(
        choices=['Cafe with accented e', 'Tokyo in Japanese'],
        multi_select=True, created_at=20, updated_at=20)
    bridge.event(OWNER, run, question)
    bridge.claim(OWNER, run, question['question_id'], {'answer': answer, 'other': False})
    bridge.recover()
    confirmed = bridge._normalize({
        **question, 'status': 'answered', 'answer': answer, 'other': False,
        'updated_at': 25,
    }, 'native-run')

    assert bridge._save({**OWNER, 'profile': 'other'}, run, confirmed) is None
    assert bridge._save(OWNER, run, {**confirmed, 'upstream_id': 'different-upstream'}) is None
    assert bridge._save(
        OWNER, {**run, 'upstream_id': 'different-upstream'}, confirmed) is None
    assert bridge._save(OWNER, run, {**confirmed, 'question': 'Different question'}) is None
    with pytest.raises(KeyError):
        bridge._save({**OWNER, 'id': 'other-owner'}, run, confirmed)
    with pytest.raises(KeyError):
        bridge._save(OWNER, {**run, 'id': 'other-run'}, confirmed)

    retained = bridge.list(OWNER, run['id'])[0]
    assert retained['status'] == 'unknown'
    assert retained['answer'] == answer
    assert retained['other'] is False


@pytest.mark.asyncio
async def test_real_gateway_run_not_found_retires_waiters_durably_without_resubmission(tmp_path):
    import httpx
    from backend.hermes_client import GatewayClient

    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    unanswered = pending('a' * 32)
    attempted = pending('b' * 32)
    bridge.event(OWNER, run, unanswered)
    bridge.event(OWNER, run, attempted)
    bridge.claim(OWNER, run, attempted['question_id'],
                 {'answer': 'Change it', 'other': False})
    calls = []

    async def handle(request):
        calls.append((request.method, request.url.path))
        if request.url.path == '/v1/capabilities':
            return httpx.Response(200, json={
                'mobile_run_controls': {'version': 1, 'clarifications': True}})
        assert request.url.path == '/v1/runs/native-run'
        return httpx.Response(404, json={'detail': 'synthetic missing run'})

    gateway = GatewayClient(
        'http://127.0.0.1:8642', 'synthetic-token', execution_ready=True,
        transport=httpx.MockTransport(handle))
    try:
        state = await bridge.rehydrate(OWNER, run['id'], gateway, run)
    finally:
        await gateway.close()

    reopened = RunJournal(journal.path)
    recovered = ClarificationJournal(reopened).list(OWNER, run['id'])
    by_id = {item['question_id']: item for item in recovered}
    assert state['available'] is False
    assert by_id[unanswered['question_id']]['status'] == 'unknown'
    assert by_id[attempted['question_id']]['status'] == 'unknown'
    assert by_id[attempted['question_id']]['answer'] == 'Change it'
    with closing(reopened.connect()) as connection:
        replayed = [event for event in reopened._replay_events(connection, run['id'])
                    if event['name'] == 'clarification']
    assert {event['data']['status'] for event in replayed} == {'unknown'}
    assert calls == [
        ('GET', '/v1/capabilities'),
        ('GET', '/v1/runs/native-run'),
    ]
    with pytest.raises(RunConflict):
        ClarificationJournal(reopened).claim(
            OWNER, reopened.get('owner', run['id']), unanswered['question_id'],
            {'answer': 'Keep current', 'other': False})
    with pytest.raises(RunConflict):
        ClarificationJournal(reopened).claim(
            OWNER, reopened.get('owner', run['id']), attempted['question_id'],
            {'answer': 'Change it', 'other': False})


@pytest.mark.asyncio
async def test_real_gateway_transient_run_failure_keeps_waiter_pending(tmp_path):
    import httpx
    from backend.hermes_client import GatewayClient

    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    question = pending()
    bridge.event(OWNER, run, question)

    async def handle(request):
        if request.url.path == '/v1/capabilities':
            return httpx.Response(200, json={
                'mobile_run_controls': {'version': 1, 'clarifications': True}})
        return httpx.Response(503, json={'detail': 'synthetic unavailable'})

    gateway = GatewayClient(
        'http://127.0.0.1:8642', 'synthetic-token', execution_ready=True,
        transport=httpx.MockTransport(handle))
    try:
        state = await bridge.rehydrate(OWNER, run['id'], gateway, run)
    finally:
        await gateway.close()
    assert state['available'] is False
    assert state['items'][0]['status'] == 'pending'
    assert journal.events('owner', run['id'])[-1]['data']['status'] == 'pending'
