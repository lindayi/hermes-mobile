import queue
import threading
from types import SimpleNamespace

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
