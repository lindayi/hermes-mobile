import asyncio
import queue
import threading
from types import SimpleNamespace

import pytest

from backend.native_maintenance import maintenance_snapshot


def test_pending_clarification_is_counted_as_active_native_work(tmp_path):
    adapter = SimpleNamespace(
        _maintenance_lock=threading.RLock(),
        _active_run_tasks={},
        _run_statuses={'native-run': {'status': 'waiting_for_clarification'}},
        _active_run_agents={},
        _shutdown_interruptible_agents={},
        _stopping_run_ids=set(),
        _pending_agent_requests=0,
        _inflight_agent_runs=0,
        _maintenance_workers=0,
        _maintenance_delegations=SimpleNamespace(count=lambda: 0),
        _maintenance_uncertain=False,
        _session_deletion_workers=0,
    )
    registry = SimpleNamespace(
        _lock=threading.Lock(), _running={}, completion_queue=queue.Queue())
    delegations = SimpleNamespace(_records_lock=threading.Lock(), _records={})

    evidence = maintenance_snapshot(adapter, registry, delegations, tmp_path / 'absent.db')

    assert evidence['status'] == 'ok'
    assert evidence['work']['nonterminal_runs'] == 1


def _composed_listener(monkeypatch, timeout):
    from backend import native_maintenance
    from backend.native_run_controls import run_controls_adapter
    from test_native_run_controls import Base

    monkeypatch.setattr(
        native_maintenance, 'install_delegation_tracking',
        lambda: SimpleNamespace(count=lambda: 0))
    adapter = native_maintenance.maintenance_adapter(run_controls_adapter(Base))()
    adapter._run_streams['native-run'] = asyncio.Queue()
    adapter._run_approval_sessions['native-run'] = 'native-run'
    outcome = {}
    agent = None

    def run():
        try:
            outcome['answer'] = agent.clarify_callback(
                'Which option?', ['Keep', 'Change'])
        except RuntimeError as exc:
            outcome['error'] = str(exc)

    agent = SimpleNamespace(
        steer=lambda text: True, clear_interrupt=lambda: True,
        run_conversation=run, clarify_timeout=timeout,
        _drain_pending_steer=lambda: None)
    callback = adapter._make_run_event_callback(
        'native-run', asyncio.get_running_loop())
    adapter._set_run_status('native-run', 'running')
    adapter.agent_factory = lambda **kwargs: agent
    agent = adapter._create_agent(
        tool_progress_callback=callback, stream_delta_callback=lambda text: None)
    adapter._active_run_agents['native-run'] = agent
    adapter._active_run_tasks = {}
    adapter._shutdown_interruptible_agents = {}
    adapter._pending_agent_requests = 0
    adapter._inflight_agent_runs = 0
    return adapter, agent, outcome


@pytest.mark.parametrize('status', ['completed', 'failed', 'cancelled'])
def test_terminal_queue_frame_releases_waiter_before_status_publication(
        status, monkeypatch, tmp_path):
    from backend.orchestration import Orchestrator
    from backend.runs import RunJournal
    from test_clarifications import OWNER

    async def check():
        adapter, agent, outcome = _composed_listener(monkeypatch, timeout=5)
        journal = RunJournal(tmp_path / 'runs.sqlite')
        run, _ = journal.submit('owner', 'default', 'session', 'Original', 'original')
        journal.set_upstream('owner', run['id'], 'native-run')
        frames, starts = [], []

        async def start(session_id, text, history):
            starts.append((session_id, text))
            return {'run_id': 'native-run'}

        async def events(run_id):
            assert run_id == 'native-run'
            yield frames[0]
            pytest.fail('Bridge must stop at terminal before reading the later release event')

        gateway = SimpleNamespace(require_execution=lambda: None, start=start, events=events)
        runtime = Orchestrator(journal, gateway, SimpleNamespace(profiles={'default': tmp_path}))
        worker = threading.Thread(target=agent.run_conversation, daemon=True)
        worker.start()
        try:
            queue = adapter._run_streams['native-run']
            question = await asyncio.wait_for(queue.get(), 1)
            await runtime._observe_clarification_event(OWNER, run['id'], question)
            queue.put_nowait({'event': 'run.' + status, 'run_id': 'native-run',
                              'output': 'Synthetic terminal output'})
            terminal = await asyncio.wait_for(queue.get(), 1)
            assert terminal['event'] == 'run.' + status
            expected = 'cancelled' if status == 'cancelled' else 'expired'
            assert terminal['clarifications'][0]['status'] == expected
            assert adapter._controls['native-run']['clarifications'][
                question['question_id']]['signal'].is_set()
            frames.append(terminal)
            await runtime._stream(OWNER, run, [])
            assert starts == [('session', 'Original')]
            await asyncio.to_thread(worker.join, 1)
            assert not worker.is_alive()
            assert outcome == {'error': f'Clarification {expected}.'}
            assert runtime.clarifications.list(OWNER, run['id'])[0]['status'] == expected
            events = journal.events('owner', run['id'])
            adapter._set_run_status('native-run', 'running')
            assert adapter._run_statuses['native-run']['status'] == 'waiting_for_clarification'
            adapter._set_run_status('native-run', status)
            adapter._set_run_status('native-run', 'running')
            await runtime._observe_terminal_event(OWNER, run['id'], terminal)
            assert journal.events('owner', run['id']) == events
            assert adapter._run_statuses['native-run']['status'] == status
            response = await adapter._handle_clarification_answer(SimpleNamespace(
                match_info={'run_id': 'native-run', 'question_id': question['question_id']},
                body={'answer': 'Change', 'other': False}))
            assert response.status == 409
            with pytest.raises(RuntimeError, match='unavailable'):
                agent.clarify_callback('Late question', ['Keep', 'Change'])
            await asyncio.sleep(0)
            releases = []
            while not queue.empty():
                event = queue.get_nowait()
                if event['event'] == 'run.clarification':
                    releases.append(event)
            assert len(releases) == 1
            assert releases[0]['status'] == expected
        finally:
            adapter._set_run_status('native-run', status)
            await asyncio.to_thread(worker.join, 1)
            assert not worker.is_alive()

    asyncio.run(check())


@pytest.mark.parametrize('ending', ['answer', 'timeout', 'stopping', 'completed'])
def test_composed_listener_serializes_clarification_and_lifecycle(ending, monkeypatch):
    async def check():
        adapter, agent, outcome = _composed_listener(
            monkeypatch, timeout=0.2 if ending == 'timeout' else 2)
        assert adapter._controls_lock is adapter._maintenance_lock
        worker = threading.Thread(target=agent.run_conversation, daemon=True)
        worker.start()
        try:
            question = await asyncio.wait_for(
                adapter._run_streams['native-run'].get(), timeout=1)
            assert question['status'] == 'pending'
            registry = SimpleNamespace(
                _lock=threading.Lock(), _running={}, completion_queue=queue.Queue())
            delegations = SimpleNamespace(_records_lock=threading.Lock(), _records={})
            waiting = maintenance_snapshot(
                adapter, registry, delegations, 'unused-state.db')
            assert waiting['status'] == 'ok'
            assert waiting['work']['nonterminal_runs'] == 1
            assert waiting['work']['agent_workers'] == 1

            if ending == 'answer':
                response = await adapter._handle_clarification_answer(SimpleNamespace(
                    match_info={'run_id': 'native-run', 'question_id': question['question_id']},
                    body={'answer': 'Change', 'other': False}))
                assert response.status == 200
                adapter._set_run_status('native-run', 'completed')
            elif ending != 'timeout':
                status = 'stopping' if ending == 'stopping' else 'completed'
                updater = threading.Thread(
                    target=adapter._set_run_status, args=('native-run', status))
                updater.start()
                updater.join(timeout=1)
                assert not updater.is_alive()

            await asyncio.to_thread(worker.join, 1)
            assert not worker.is_alive()
            if ending == 'answer':
                assert outcome == {'answer': 'Change'}
                item = adapter._controls['native-run']['clarifications'][
                    question['question_id']]
                assert item['status'] == 'answered'
                assert item['answer'] == 'Change'
                assert adapter._run_statuses['native-run']['status'] == 'completed'
            else:
                expected = 'expired' if ending in {'timeout', 'completed'} else 'cancelled'
                assert outcome == {'error': f'Clarification {expected}.'}
                assert adapter._controls['native-run']['clarifications'][
                    question['question_id']]['status'] == expected
            assert adapter._maintenance_workers == 0
            finished = maintenance_snapshot(
                adapter, registry, delegations, 'unused-state.db')
            assert finished['status'] == 'ok'
            assert finished['work']['agent_workers'] == 0
            assert finished['work']['nonterminal_runs'] == (
                1 if ending in {'timeout', 'stopping'} else 0)
        finally:
            if worker.is_alive():
                cleanup = threading.Thread(
                    target=adapter._set_run_status,
                    args=('native-run', 'stopping'), daemon=True)
                cleanup.start()
                cleanup.join(timeout=1)
                worker.join(timeout=1)
                assert not cleanup.is_alive()
            assert not worker.is_alive()

    asyncio.run(check())
