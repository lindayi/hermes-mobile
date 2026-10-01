import os
import queue
import threading
from types import SimpleNamespace
import pytest
from backend.native_maintenance import maintenance_snapshot
from deploy.native_readiness import require_native_readiness


def sample(tmp_path, **changes):
    outbox={'pending':2,'quarantined':1,'delivered':5,'foreign':3,'foreign_retained':3,'conflicts':0,'active_workers':0,'shutdown_publications':0}
    outbox.update(changes)
    a=SimpleNamespace(_maintenance_lock=threading.RLock(),_pending_agent_requests=0,
        _inflight_agent_runs=0,_active_run_tasks={},_run_statuses={},_active_run_agents={},
        _shutdown_interruptible_agents={},_stopping_run_ids=set(),_maintenance_workers=0,
        _maintenance_uncertain=False,_maintenance_delegations=SimpleNamespace(count=lambda:0),
        notification_evidence=lambda:outbox)
    registry=SimpleNamespace(_lock=threading.Lock(),_running={},completion_queue=queue.Queue())
    delegations=SimpleNamespace(_records_lock=threading.Lock(),_records={})
    return maintenance_snapshot(a,registry,delegations,tmp_path/'absent.sqlite')


def test_durable_outbox_is_preserved_for_restart_but_still_blocks_deletion(tmp_path):
    evidence=sample(tmp_path)
    assert evidence['status']=='ok'
    assert evidence['notifications']['backlog']==3,'empty RAM does not mean delivery complete'
    assert evidence['notifications']['durable_retained']==3
    assert evidence['notifications']['unpreserved']==0
    assert evidence['notifications']['web_pending']==2
    assert evidence['notifications']['quarantined']==1
    assert evidence['notifications']['foreign_retained']==3
    assert evidence['work']['notification_workers']==0
    health={'pid':os.getpid(),'native_maintenance':evidence}
    assert require_native_readiness(health,expected_pid=os.getpid(),expected_start_ticks=evidence['start_ticks'],notification_version=1)


def test_actual_notification_worker_blocks_restart(tmp_path):
    evidence=sample(tmp_path,active_workers=1)
    assert evidence['work'].get('notification_workers')==1
    assert not require_native_readiness({'pid':os.getpid(),'native_maintenance':evidence},expected_pid=os.getpid(),expected_start_ticks=evidence['start_ticks'],notification_version=1)


@pytest.mark.parametrize('change',[{'pending':True},{'active_workers':-1},{'conflicts':1},{'quarantined':'0'},{'foreign':None},{'foreign_retained':True},{'foreign_retained':2},{'foreign_retained':4}])
def test_uncertain_outbox_evidence_fails_closed(tmp_path,change):
    assert sample(tmp_path,**change)['status']=='unknown'


def test_delivery_completion_releases_backlog_not_foreign_source_ownership(tmp_path):
    evidence=sample(tmp_path,pending=0,quarantined=0)
    assert evidence['notifications']['backlog']==0
    assert evidence['notifications']['foreign_retained']==3
    with pytest.raises(RuntimeError):
        require_native_readiness({'pid':os.getpid(),'native_maintenance':evidence},expected_pid=os.getpid(),expected_start_ticks=evidence['start_ticks'],notification_version=0)


@pytest.mark.parametrize('field,value',[('web_pending',True),('quarantined',-1),('foreign_retained','3'),('web_delivered',None),('web_pending',100)])
def test_notification_version_requires_positive_typed_delivery_evidence(tmp_path,field,value):
    evidence=sample(tmp_path)
    evidence['notifications'][field]=value
    with pytest.raises(RuntimeError):
        require_native_readiness({'pid':os.getpid(),'native_maintenance':evidence},expected_pid=os.getpid(),expected_start_ticks=evidence['start_ticks'],notification_version=1)


def test_late_publication_signal_survives_capture_and_blocks_false_quiescence(tmp_path):
    evidence=sample(tmp_path,pending=0,quarantined=0,shutdown_publications=1)
    assert evidence['work']['notification_lifecycle_uncertain']==1
    assert evidence['notifications']['shutdown_publications']==1
    assert not require_native_readiness({'pid':os.getpid(),'native_maintenance':evidence},expected_pid=os.getpid(),expected_start_ticks=evidence['start_ticks'],notification_version=1)


@pytest.mark.parametrize('value',[None,False,-1,'0'])
def test_unknown_late_publication_counter_is_not_silently_discarded(tmp_path,value):
    assert sample(tmp_path,shutdown_publications=value)['status']=='unknown'
