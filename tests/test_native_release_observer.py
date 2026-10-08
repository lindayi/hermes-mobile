import json
from copy import deepcopy
import pytest

@pytest.mark.parametrize('native_failure',[False,True])
def test_native_observer_independently_checks_listener(tmp_path,monkeypatch,capsys,native_failure):
    from deploy import observe_release as observer
    from deploy import native_controls_release as native
    from deploy.self_deploy import Paths
    calls=[];expected=tmp_path/'expected.json';expected.write_text('{}')
    stage=tmp_path/'reviewed';paths=Paths()
    monkeypatch.setattr(observer,'verify_release',lambda p,s,b:calls.append(('public',s)))
    class Probe:
        def __init__(self,source):pass
        def verify_operational(self,s):
            calls.append(('native',s))
            if native_failure:raise RuntimeError('Native verification failed')
    monkeypatch.setattr(native,'NativeProbe',Probe)
    def observe(p,since,expected,**kw):
        try:
            kw['verify'](p,stage,True)
        except Exception:
            observer.set_diagnostic(kw['diagnostics'],'native_health','verification_failed')
            return 'verification_failed'
        observer.set_diagnostic(kw['diagnostics'],'status_recheck','verified')
        return 'succeeded'
    monkeypatch.setattr(observer,'observe',observe)
    assert observer.main(['--since-ns','1','--expected',str(expected),'--unit','fixture','--native'],paths=paths)==int(native_failure)
    assert json.loads(capsys.readouterr().out)=={
        'unit':'fixture','status':'verification_failed' if native_failure else 'succeeded',
        'notification':'disabled','phase':'native_health' if native_failure else 'status_recheck',
        'reason':'verification_failed' if native_failure else 'verified'}
    assert calls==[('public',stage),('native',stage)]


def operational_probe(tmp_path,monkeypatch,health_change=None):
    from urllib.error import HTTPError
    from deploy import native_controls_release as native
    from test_native_readiness import evidence

    root=tmp_path/'release'
    root.mkdir()
    hashes=dict(native.APPROVED_CONTROL_HASHES)
    monkeypatch.setattr(native,'approved_controls',lambda stage:hashes)
    monkeypatch.setattr(native,'attested_controls',lambda stage:hashes)
    probe=object.__new__(native.NativeProbe)
    probe.attest=lambda stage,legacy=False:123
    probe._start_ticks=lambda pid:456
    health=evidence()
    health['status']='ok'
    health['readiness']={'checks':{'background_queues':{
        'status':'ok','active_api_runs':0,'process_completions':0,'active_delegations':0}}}
    work=health['native_maintenance']['work']
    work.update(session_deletion_workers=0,notification_workers=0,
                notification_lifecycle_uncertain=0)
    notices=health['native_maintenance']['notifications']
    notices.update(web_pending=78,quarantined=0,foreign_retained=0,
                   web_delivered=0,shutdown_publications=0)
    if health_change == 'busy':
        work['active_run_tasks']=1
    elif health_change and health_change.startswith('unsafe:'):
        work[health_change.partition(':')[2]]=1
    elif health_change == 'unknown':
        work['unknown_worker']=0
    elif health_change == 'malformed':
        work['active_run_tasks']=True
    elif health_change == 'wrong_pid':
        health['pid']=124
    elif health_change == 'wrong_start':
        health['native_maintenance']['start_ticks']=457
    elif health_change == 'bad_health':
        health['status']='degraded'
    if health_change == 'unpreserved':
        notices.update(durable_retained=77,unpreserved=1)
    caps={'mobile_run_controls':dict(version=1,steering=True,live_commentary=True,clarifications=True),
          'mobile_native_maintenance':dict(version=1,scope='dedicated-listener',atomic_drain=False),
          'features':{'mobile_session_delete_version':1},
          'mobile_notifications':dict(version=1,delivery='durable-inbox',automatic_model_wake=False)}
    if health_change == 'bad_caps':
        caps['mobile_run_controls']['steering']=False
    def request(path,*,authenticated=True):
        if not authenticated:
            if health_change == 'anonymous':
                return deepcopy(health)
            raise HTTPError('private',401,'Unauthorized',{},None)
        return deepcopy(health) if path == '/health/detailed' else caps
    probe.request=request
    monkeypatch.setattr(native.time,'sleep',lambda _:None)
    return native,probe,root,health


def test_post_release_native_observation_accepts_attested_busy_work(tmp_path,monkeypatch):
    native,probe,root,health=operational_probe(tmp_path,monkeypatch,'busy')
    from deploy.native_readiness import require_native_readiness

    assert require_native_readiness(health,expected_pid=123,expected_start_ticks=456,
                                   notification_version=1) is False
    probe.verify_operational(root)
    with pytest.raises(RuntimeError,match='Native verification failed'):
        probe.verify(root)


def test_native_observation_rejects_unknown_source_family(tmp_path,monkeypatch):
    native,probe,root,_=operational_probe(tmp_path,monkeypatch)
    hashes=dict(native.APPROVED_CONTROL_HASHES)
    hashes['backend/native_run_controls.py']='0'*64
    monkeypatch.setattr(native,'approved_controls',lambda stage:hashes)
    with pytest.raises(RuntimeError,match='Unknown native control source-version baseline'):
        probe.verify_operational(root)


@pytest.mark.parametrize('change',[
    'unsafe:shutdown_agents','unsafe:stopping_runs','unsafe:cancellation_uncertain',
    'unsafe:session_deletion_workers','unsafe:notification_lifecycle_uncertain',
    'unpreserved','unknown','malformed','wrong_pid','wrong_start','bad_health','bad_caps','anonymous',
])
def test_post_release_native_observation_rejects_unsafe_or_unpreserved_work(
        tmp_path,monkeypatch,change):
    _,probe,root,_=operational_probe(tmp_path,monkeypatch,change)
    with pytest.raises(RuntimeError):
        probe.verify_operational(root)
