import hashlib
import json
from pathlib import Path
import pytest
from deploy import native_readiness as n
from test_native_readiness import legacy, bootstrap_inputs, fake_proc


def test_explicit_legacy_loss_acceptance_is_exact_count_and_not_work_override():
    h=legacy(); h['readiness']['checks']['background_queues']['process_completions']=78
    assert n.require_legacy_bootstrap(h, **bootstrap_inputs(), accepted_backlog=78)
    with pytest.raises(RuntimeError):
        n.require_legacy_bootstrap(h, **bootstrap_inputs(), accepted_backlog=77)
    with pytest.raises(RuntimeError):
        n.require_legacy_bootstrap(h, **bootstrap_inputs(), accepted_backlog=True)
    h['readiness']['checks']['background_queues']['active_api_runs']=1
    assert not n.require_legacy_bootstrap(h, **bootstrap_inputs(), accepted_backlog=78)


def consent_fixture(tmp_path,monkeypatch):
    root=tmp_path/'backups';root.mkdir(mode=0o700)
    snapshot=root/'snapshot';snapshot.mkdir(mode=0o700)
    manifest=snapshot/'manifest.json';manifest.write_text('{}');manifest.chmod(0o600)
    record={'version':1,'pid':123,'start_ticks':456,'backlog':78,
            'consent':'accept-legacy-memory-only-notice-loss',
            'snapshot':str(snapshot),'manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest()}
    approval=tmp_path/'approval.json'
    approval.write_text(json.dumps(record));approval.chmod(0o600)
    from deploy import backup
    seen=[]
    monkeypatch.setattr(backup,'verify',lambda path: seen.append(path) or {})
    return root,approval,record,seen


def test_private_consent_verifies_bound_backup_before_use(tmp_path,monkeypatch):
    root,approval,record,seen=consent_fixture(tmp_path,monkeypatch)
    assert n.load_legacy_notice_approval(approval,backups_root=root)==record
    assert seen==[Path(record['snapshot'])]


@pytest.mark.parametrize('field,value',[('pid',True),('start_ticks',0),('backlog',-1),('version',True),
    ('consent','restart'),('snapshot','/tmp/outside'),('manifest_sha256','0'*64)])
def test_changed_or_unscoped_approval_rejected(tmp_path,monkeypatch,field,value):
    root,approval,record,_=consent_fixture(tmp_path,monkeypatch)
    record[field]=value;approval.write_text(json.dumps(record))
    with pytest.raises((RuntimeError,ValueError)):
        n.load_legacy_notice_approval(approval,backups_root=root)


def test_approval_mode_and_failed_backup_verification_rejected(tmp_path,monkeypatch):
    root,approval,record,_=consent_fixture(tmp_path,monkeypatch)
    approval.chmod(0o644)
    with pytest.raises((RuntimeError,ValueError)):
        n.load_legacy_notice_approval(approval,backups_root=root)
    approval.chmod(0o600)
    from deploy import backup
    monkeypatch.setattr(backup,'verify',lambda _: (_ for _ in ()).throw(ValueError('bad snapshot')))
    with pytest.raises(ValueError):
        n.load_legacy_notice_approval(approval,backups_root=root)


def test_legacy_socket_supplement_ignores_only_proven_peer_and_closed_tcp(tmp_path):
    root,proc=fake_proc(tmp_path)
    peer=root/'456';(peer/'fd').mkdir(parents=True)
    (peer/'fd'/'7').symlink_to('socket:[88]')
    (proc/'fd'/'2').symlink_to('socket:[77]')
    (proc/'fd'/'3').symlink_to('socket:[99]')
    (proc/'net'/'tcp').write_text('header\n'
        '0: 0100007F:48D2 0100007F:1234 01 0 0 0 0 0 77\n'
        '1: 0100007F:1234 0100007F:48D2 01 0 0 0 0 0 88\n'
        '2: 0100007F:5555 ABCDEF01:01BB 08 0 0 0 0 0 99\n')
    assert n.observe_process(123,proc_root=root)['connected_inet_sockets']==2
    assert n.observe_process(123,proc_root=root,trusted_peer_pid=456,allow_closed_tcp=True)['connected_inet_sockets']==0
    (peer/'fd'/'7').unlink()
    assert n.observe_process(123,proc_root=root,trusted_peer_pid=456,allow_closed_tcp=True)['connected_inet_sockets']==1
    with pytest.raises(RuntimeError):
        n.observe_process(123,proc_root=root,trusted_peer_pid=True,allow_closed_tcp=True)
