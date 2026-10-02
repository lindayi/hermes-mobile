import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from deploy import native_controls_release as r
from deploy import native_readiness as n
from test_native_readiness import evidence,legacy
from backend.runs import RunJournal
from test_native_delete_rollout import versions


def make_probe(tmp_path,monkeypatch):
    probe=object.__new__(r.NativeProbe)
    probe.legacy_notice_approval=None
    probe._start_ticks=lambda pid:456
    probe._bridge_pid=lambda root:789
    monkeypatch.setattr(n,'observe_process',lambda pid,**kw:dict(pid=pid,start_ticks=456,descendants=0,connected_inet_sockets=0))
    journal=RunJournal(tmp_path/'runs.sqlite'); journal.set_deployment_gate('release')
    baseline=dict(root=str(tmp_path),pid=123,start_ticks=456,legacy=True,bootstrap=True,
                  bridge_root=str(tmp_path),bridge_pid=789,gate_owner='release')
    return probe,journal,baseline


def test_idle_and_post_restart_verifier_use_scoped_readiness(tmp_path,monkeypatch):
    probe,journal,baseline=make_probe(tmp_path,monkeypatch)
    import shutil
    from urllib.error import HTTPError
    baseline['legacy']=False
    baseline['source_hashes'] = r.APPROVED_CONTROL_HASHES
    for name in (*r.APPROVED_CONTROL_HASHES, 'backend/model_controls.py'):
        target = tmp_path / name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(Path(__file__).resolve().parents[1] / name, target)
    probe.attest=lambda root,legacy=False:123
    caps={'mobile_run_controls':dict(version=1,steering=True,live_commentary=True),
          'mobile_native_maintenance':dict(version=1,scope='dedicated-listener',atomic_drain=False),
          'features': {'mobile_session_delete_version': 1},
          'mobile_notifications': dict(version=1, delivery='durable-inbox', automatic_model_wake=False)}
    baseline['caps'] = caps
    health = dict(legacy(), **evidence(), status='ok')
    health['native_maintenance']['work']['session_deletion_workers'] = 0
    health['native_maintenance']['work']['notification_workers'] = 0
    health['native_maintenance']['work']['notification_lifecycle_uncertain'] = 0
    health['native_maintenance']['notifications'].update(
        web_pending=78, quarantined=0, foreign_retained=0, web_delivered=0, shutdown_publications=0)
    def request(path, authenticated=True):
        if not authenticated: raise HTTPError('private',401,'Unauthorized',{},None)
        return health if path == '/health/detailed' else caps
    probe.request = request
    assert probe.idle(journal,baseline)
    monkeypatch.setattr(r.time,'sleep',lambda _:None)
    probe.verify(tmp_path)


def test_capture_records_bootstrap_identity_and_bridge(tmp_path,monkeypatch):
    from urllib.error import HTTPError
    probe,journal,baseline=make_probe(tmp_path,monkeypatch)
    (tmp_path/'backend').mkdir();(tmp_path/'backend'/'native_api_service.py').write_bytes(b'legacy')
    import hashlib
    monkeypatch.setattr(r,'LEGACY_LAUNCHER',hashlib.sha256(b'legacy').hexdigest())
    probe.source=tmp_path
    proc = tmp_path / 'proc'
    (proc / '123').mkdir(parents=True)
    (proc / '123/cwd').symlink_to(tmp_path)
    (proc / '123/cmdline').write_bytes((r.NATIVE_PYTHON + '\0' + str(tmp_path / 'backend/native_api_service.py') + '\0').encode())
    monkeypatch.setattr(r, 'PROC_ROOT', proc)
    probe.run=lambda *a, **kw: SimpleNamespace(stdout='123')
    probe.attest=lambda root,legacy=False:123
    def request(path,authenticated=True):
        if not authenticated:raise HTTPError('private',401,'Unauthorized',{},None)
        return dict(legacy(), status='ok') if path=='/health/detailed' else {'legacy':True}
    probe.request=request
    captured=probe.capture(tmp_path,True)
    assert captured['start_ticks']==456 and captured['bootstrap'] is True
    assert captured['bridge_root']==str(tmp_path) and captured['bridge_pid']==789


def test_cli_forwards_explicit_approval_only_with_bootstrap(tmp_path,monkeypatch):
    from test_self_deploy import deploy_fixture
    _,paths=deploy_fixture(tmp_path)
    monkeypatch.setattr(r.os,'geteuid',lambda:1000)
    calls=[]
    approval=tmp_path/'private-approval.json'
    assert r.main(['--schedule','--local-full-checks','--bootstrap-dedicated-native','--legacy-restart-approval',str(approval)],
                  paths=paths,run=lambda cmd,**kw:calls.append(cmd))==0
    assert '--watch-worker' in calls[0]
    assert '--local-full-checks' in calls[1]
    assert calls[1][-2:]==['--legacy-restart-approval',str(approval)]
    with pytest.raises(SystemExit):
        r.main(['--schedule','--legacy-restart-approval',str(approval)],paths=paths,run=lambda *a,**kw:pytest.fail('unauthorized schedule'))


def test_worker_loads_verified_scoped_approval(tmp_path,monkeypatch):
    from backend import native_api_service
    from test_self_deploy import deploy_fixture
    _,paths=deploy_fixture(tmp_path)
    monkeypatch.setattr(r.os,'geteuid',lambda:1000)
    monkeypatch.setenv('INVOCATION_ID','test-only')
    monkeypatch.setattr(native_api_service,'OWNER_HOME',tmp_path/'synthetic-home')
    approval=tmp_path/'private-approval.json';seen=[]
    monkeypatch.setattr(n,'load_legacy_notice_approval',lambda path:seen.append(path) or {'pid':123})

    class FakeNativeProbe(dict):
        def __init__(self, source, **kwargs):
            super().__init__(kwargs)
            self.config_bytes=json.dumps(
                {'state_dir':str(paths.database.parent.resolve())}).encode()

    deployed=[]
    monkeypatch.setattr(r,'NativeProbe',FakeNativeProbe)
    monkeypatch.setattr(r,'deploy',lambda deployed_paths,**kw:deployed.append((deployed_paths,kw)))
    assert r.main(['--worker','--local-full-checks','--bootstrap-dedicated-native','--legacy-restart-approval',str(approval)],paths=paths)==0
    assert seen[0]==approval and deployed[0][1]['native']['legacy_notice_approval']=={'pid':123}
    deployed_paths,callbacks=deployed[0]
    notification_callbacks=callbacks['handoff']
    assert deployed_paths.state.resolve()==notification_callbacks.controller
    assert deployed_paths.database.resolve()==notification_callbacks.runs
    assert notification_callbacks.state==deployed_paths.database.parent.resolve()
    assert notification_callbacks.controller!=notification_callbacks.state


def test_worker_forwards_hosted_run_id_to_controller(tmp_path, monkeypatch):
    from backend import native_api_service
    from deploy import native_notification_release
    from test_self_deploy import deploy_fixture
    _, paths = deploy_fixture(tmp_path)
    monkeypatch.setattr(r.os, 'geteuid', lambda: 1000)
    monkeypatch.setenv('INVOCATION_ID', 'synthetic-systemd-context')
    monkeypatch.setattr(native_api_service, 'OWNER_HOME', tmp_path / 'synthetic-home')
    monkeypatch.setattr(r, 'NativeProbe', lambda *a, **kw: object())

    class Notifications:
        probe = object()
        verify_rollback = object()

        def __init__(self, *a, **kw):
            self.capture = None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(native_notification_release, 'NativeNotificationCallbacks', Notifications)
    deployed = []
    monkeypatch.setattr(r, 'deploy', lambda paths, **kw: deployed.append(kw))
    assert r.main(['--worker', '--hosted-run-id', '765', '--expected-source-sha', 'a' * 40],
                  paths=paths) == 0
    assert deployed[0]['hosted_run_id'] == 765
    assert deployed[0]['local_full_checks'] is False
    assert deployed[0]['expected_source_sha'] == 'a' * 40


def test_controller_new_readiness_consumes_scoped_evidence(tmp_path,monkeypatch,versions):
    probe,journal,baseline=make_probe(tmp_path,monkeypatch)
    root = versions[1]
    health=evidence();health['native_maintenance']['work']['session_deletion_workers']=0
    health['native_maintenance']['work']['notification_workers'] = 0
    health['native_maintenance']['work']['notification_lifecycle_uncertain'] = 0
    health['native_maintenance']['notifications'].update(
        web_pending=78, quarantined=0, foreign_retained=0, web_delivered=0, shutdown_publications=0)
    assert probe._ready(health, root=root)
    bad=health;bad['native_maintenance']['work']['pending_admissions']=1
    assert not probe._ready(bad, root=root)
    with pytest.raises(RuntimeError):probe._ready(legacy(), root=root)


def test_controller_legacy_consent_and_owned_gate_are_mandatory(tmp_path,monkeypatch):
    probe,journal,baseline=make_probe(tmp_path,monkeypatch)
    h=legacy();h['readiness']['checks']['background_queues']['process_completions']=78
    with pytest.raises(RuntimeError,match='opaque'):probe._ready(h,baseline=baseline,journal=journal)
    probe.legacy_notice_approval=dict(pid=123,start_ticks=456,backlog=78)
    assert probe._ready(h,baseline=baseline,journal=journal)
    journal.clear_deployment_gate('release');journal.set_deployment_gate('other')
    with pytest.raises(RuntimeError):probe._ready(h,baseline=baseline,journal=journal)


@pytest.mark.parametrize('change',['pid','start_ticks','count','bootstrap','bridge','active'])
def test_controller_restart_consent_cannot_expand_scope(tmp_path,monkeypatch,change):
    probe,journal,baseline=make_probe(tmp_path,monkeypatch)
    h=legacy();h['readiness']['checks']['background_queues']['process_completions']=78
    probe.legacy_notice_approval=dict(pid=123,start_ticks=456,backlog=78)
    if change in ('pid','start_ticks'):probe.legacy_notice_approval[change]+=1
    if change=='count':h['readiness']['checks']['background_queues']['process_completions']=79
    if change=='bootstrap':baseline['bootstrap']=False
    if change=='bridge':probe._bridge_pid=lambda root:999
    if change=='active':
        h['readiness']['checks']['background_queues']['active_api_runs']=1
        assert not probe._ready(h,baseline=baseline,journal=journal)
    else:
        with pytest.raises(RuntimeError):probe._ready(h,baseline=baseline,journal=journal)
