"""Owner/member wrapper contract: no live native listener or registry imports."""
from pathlib import Path
import pytest
from backend import native_controls_service as entry


@pytest.mark.parametrize('kwargs', [{}, {'state_dir': None}], ids=['omitted', 'none'])
def test_owner_requires_explicit_state_before_any_wrapper_or_database(monkeypatch, tmp_path, kwargs):
    calls = []

    def forbidden(*args, **kw):
        calls.append((args, kw))
        raise AssertionError('wrapper reached before explicit owner state validation')

    # Block every wrapper, including notification DB construction, even on RED.
    for name in ('member_adapter', 'run_controls_adapter', 'maintenance_adapter',
                 'notification_adapter', 'session_deletion_adapter'):
        monkeypatch.setattr(entry, name, forbidden)
    with pytest.raises(ValueError, match='[Ee]xplicit.*state_dir'):
        entry.listener_adapter('base', tmp_path, member=False, **kwargs)
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_member_still_accepts_missing_owner_state(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(entry, 'member_adapter',
                        lambda base, home: calls.append((base, home)) or 'member')

    def forbidden(*args, **kwargs):
        raise AssertionError('owner-only wrapper reached')

    for name in ('run_controls_adapter', 'maintenance_adapter',
                 'notification_adapter', 'session_deletion_adapter'):
        monkeypatch.setattr(entry, name, forbidden)
    assert entry.listener_adapter('base', tmp_path, member=True) == 'member'
    assert calls == [('base', tmp_path)]


def test_owner_notifications_are_inside_deletion_and_outside_maintenance(monkeypatch,tmp_path):
    calls=[]
    def wrap(label):
        def factory(base,*args,**kwargs):
            calls.append((label,base,args,kwargs))
            return label
        return factory
    monkeypatch.setattr(entry,'run_controls_adapter',wrap('controls'))
    monkeypatch.setattr(entry,'maintenance_adapter',wrap('maintenance'))
    monkeypatch.setattr(entry,'notification_adapter',wrap('notifications'),raising=False)
    monkeypatch.setattr(entry,'session_deletion_adapter',wrap('deletion'))
    state=tmp_path/'state'
    result=entry.listener_adapter('base',tmp_path,member=False,state_dir=state)
    assert result=='deletion'
    assert [r[0] for r in calls]==['controls','maintenance','notifications','deletion']
    assert calls[2]==('notifications','maintenance',(tmp_path,),{'state_dir':state})
    assert calls[3][1]=='notifications'


def test_member_never_installs_owner_notification_or_deletion_wrappers(monkeypatch,tmp_path):
    calls=[]
    monkeypatch.setattr(entry,'member_adapter',lambda base,home:calls.append((base,home)) or 'member')
    def forbidden(*a,**kw):raise AssertionError('owner-only wrapper reached')
    for name in ('run_controls_adapter','maintenance_adapter','notification_adapter','session_deletion_adapter'):
        monkeypatch.setattr(entry,name,forbidden,raising=False)
    assert entry.listener_adapter('base',tmp_path,member=True,state_dir=tmp_path/'state')=='member'
    assert calls==[('base',tmp_path)]
