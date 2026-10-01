"""Policy tests use private temporary SQLite; only provider transport is fake."""
import json
from types import SimpleNamespace
import pytest
from backend.notifications import NotificationService
from test_notifications import subscription


@pytest.fixture
def policy(tmp_path):
    now = [1800000000.0]
    active = {('alice', 'd1'), ('alice', 'd2'), ('bob', 'b1')}
    sent = []
    def send(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(status_code=201)
    service = NotificationService(tmp_path/'n.db', 'private', 'public', send_push=send,
        clock=lambda: now[0], session_validator=lambda u, d: (u, d) in active)
    for u, d in sorted(active):
        service.subscribe(u, d, subscription(d))
    return SimpleNamespace(service=service, now=now, active=active, sent=sent)


def statuses(service):
    with service._db() as db:
        return [tuple(r) for r in db.execute('SELECT status,last_error FROM outbox ORDER BY endpoint')]


def test_unclassified_ingest_is_durable_inbox_only(policy):
    service = policy.service
    item = service.ingest('alice', 'unclassified', 'Private tool', 'Not a public final')
    assert service.list_inbox('alice') == [item]
    assert statuses(service) == []
    assert service.flush()['sent'] == 0


def test_background_receipt_ack_is_durable_without_push(policy):
    s = policy.service
    args = dict(scope='p', event_id='event', digest='digest', user_id='alice', origin='child',
                session_id='chat', event={'output':'result'}, lease_token='lease', body='result')
    receipt = s.store_background(**args)
    assert statuses(s) == []
    s.background_acknowledged('p', 'event', 'lease')
    assert s.background_rows('p', 'alice')[0]['acknowledged'] == 1
    assert s.store_background(**{**args, 'lease_token':'new'}) == receipt
    assert s.background_rows('p', 'alice')[0]['acknowledged'] == 0
    s.background_acknowledged('p', 'event', 'lease')
    assert s.background_rows('p', 'alice')[0]['acknowledged'] == 0
    s.background_acknowledged('p', 'event', 'new')
    assert s.background_rows('p', 'alice')[0]['acknowledged'] == 1
    rows = s.background_rows('p', 'alice')
    assert len(rows) == 1 and rows[0]['inbox_id'] == receipt and rows[0]['body'] == 'result'
    assert s.list_inbox('alice') == []


@pytest.mark.parametrize('category', ['completion', 'approval', 'attention', 'scheduled', 'operational', 'background', 'internal'])
def test_explicit_categories_default_policy_and_dedup(policy, category):
    s = policy.service
    item = s.ingest('alice', category, 'Finished', 'Public response', category=category, profile='default')
    assert s.ingest('alice', category, 'Changed', 'Changed', category=category)['id'] == item['id']
    assert 'category' not in item and 'profile' not in item
    assert s.list_inbox('alice') == ([] if category == 'background' else [item])
    with s._db() as db:
        assert db.execute('SELECT id,body FROM inbox').fetchone()[:] == (item['id'], 'Public response')
    expected = 0 if category in ('background', 'internal') else 2
    assert len(statuses(s)) == expected
    assert s.flush()['sent'] == expected
    assert s.flush()['sent'] == 0
    assert len(policy.sent) == expected
    s.ingest('alice', 'history', 'Old', 'Result', category=category, historical=True)
    s.ingest('alice', 'silent', 'Quiet', 'Result', category=category, silent=True)
    assert s.flush()['sent'] == 0
    with pytest.raises(ValueError):
        s.ingest('alice', 'bad', 'Bad', 'Bad', category='unknown')


@pytest.mark.parametrize('disabled', ['enabled', 'completion', 'approval', 'attention', 'scheduled', 'operational'])
def test_per_device_preferences_suppress_unclaimed_no_replay(policy, disabled):
    s = policy.service
    expected = {'enabled':True, 'categories':dict.fromkeys(('completion','approval','attention','scheduled','operational'), True), 'hide_details':False, 'revision':0}
    assert s.get_preferences('alice', 'd1') == expected
    category = 'completion' if disabled == 'enabled' else disabled
    item = s.ingest('alice', 'before', 'Finished', 'Result', category=category)
    prefs = s.get_preferences('alice', 'd1')
    if disabled == 'enabled':
        prefs['enabled'] = False
    else:
        prefs['categories'][disabled] = False
    saved = s.set_preferences('alice', 'd1', prefs)
    assert saved == {**prefs, 'revision':1}
    assert s.get_preferences('alice', 'd2') == expected
    assert statuses(s)[0][0] == 'suppressed'
    s.ingest('alice', 'during', 'Finished', 'Result', category=category)
    s.set_preferences('alice', 'd1', {**expected,'revision':saved['revision']})
    assert s.flush()['sent'] == 2
    assert all(x['subscription_info']['endpoint'].endswith('/d2') for x in policy.sent)
    assert len(s.list_inbox('alice')) == 2
    assert s.get_preferences('bob', 'b1') == expected


def test_preferences_strict_types_owner_revocation_and_durable(policy):
    s = policy.service
    assert callable(getattr(s, 'set_preferences', None)), 'Preference mutation missing'
    prefs = s.get_preferences('alice','d1')
    invalid = [None, [], {}, {**prefs,'extra':True}, {**prefs,'enabled':1},
               {**prefs,'hide_details':'false'}, {**prefs,'categories':{}},
               {**prefs,'categories':{**prefs['categories'],'completion':0}},
               {**prefs,'categories':{**prefs['categories'],'other':True}}]
    for payload in invalid:
        with pytest.raises(ValueError):
            s.set_preferences('alice','d1',payload)
    with pytest.raises(PermissionError):
        s.get_preferences('bob','d1')
    policy.active.remove(('alice','d1'))
    with pytest.raises(PermissionError):
        s.set_preferences('alice','d1',prefs)
    policy.active.add(('alice','d1'))
    prefs['hide_details'] = True
    prefs = s.set_preferences('alice','d1',prefs)
    reopened = NotificationService(s.db_path, session_validator=s.session_validator)
    assert reopened.get_preferences('alice','d1') == prefs


def test_legacy_outbox_migration_suppresses_only_unclaimed_and_keeps_inbox(policy):
    s = policy.service
    for i, status in enumerate(('pending','retry','sending','unknown','sent')):
        item = s.ingest('alice', f'legacy-{i}', 'Background result', 'Old output')
        with s._db() as db:
            db.execute('DELETE FROM notification_policy WHERE inbox_id=?', (item['id'],))
            db.execute('INSERT INTO outbox(id,inbox_id,endpoint,status,next_attempt_at,expires_at) VALUES(?,?,?,?,?,?)',
                (f'old{i}',item['id'],subscription('d1')['endpoint'],status,policy.now[0]+100,policy.now[0]+86400))
    reopened = NotificationService(s.db_path)
    with reopened._db() as db:
        assert [r[0] for r in db.execute('SELECT status FROM outbox ORDER BY id')] == ['suppressed','suppressed','sending','unknown','sent']
    assert len(reopened.list_inbox('alice')) == 5


def test_approval_policy_retains_authority_expiry_and_category_preferences(policy):
    s = policy.service
    s.approval_validator = lambda *args: True
    prefs = s.get_preferences('alice','d1')
    prefs['categories']['approval'] = False
    s.set_preferences('alice','d1',prefs)
    item = s.ingest_approval('alice','run','request','approval','chat',policy.now[0]+300)
    assert item['approval_id'] == 'approval'
    assert len(statuses(s)) == 1
    assert s.flush()['sent'] == 1
    assert policy.sent[0]['subscription_info']['endpoint'].endswith('/d2')
    s.ingest_approval('alice','run2','request2','approval2','chat',policy.now[0]+300)
    s.approval_validator = lambda *args: False
    assert s.flush()['sent'] == 0
    assert any(status == 'suppressed' for status, reason in statuses(s))


def test_silent_release_observer_works_without_new_schema(policy):
    s = policy.service
    with s._db() as db:
        db.execute('DROP TABLE notification_policy')
        db.execute('DROP TABLE push_preferences')
    class ExistingInbox(NotificationService):
        def __init__(self):
            self.db_path = s.db_path
            self.clock = s.clock
    observer = ExistingInbox()
    item = observer.ingest('alice','release','Release report','Private diagnostics',silent=True)
    assert observer.list_inbox('alice') == [item]
    assert statuses(observer) == []
    with observer._db() as db:
        assert observer._item(db.execute('SELECT * FROM inbox').fetchone()) == item
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='notification_policy'").fetchone() is None


def test_mark_read_suppresses_unclaimed_push_without_dismissing_inbox(policy):
    s = policy.service
    item = s.ingest('alice','read-first','Finished','Public response',category='completion')
    assert not s.mark_read('bob',item['id'])
    assert s.mark_read('alice',item['id'])
    assert all(status == 'suppressed' for status, _ in statuses(s))
    assert s.flush()['sent'] == 0
    assert s.list_inbox('alice')[0]['read'] is True


@pytest.mark.parametrize('phase',['first','retry'])
def test_event_owner_profile_session_revalidated_before_each_send(policy, phase):
    s = policy.service
    eligible = [True]
    calls = []
    def validate(user, profile, session, category):
        calls.append((user,profile,session,category))
        return eligible[0]
    s.event_validator = validate
    s.ingest('alice','result','Finished','Public response',session_id='chat',category='completion',profile='default')
    if phase == 'retry':
        s.send_push = lambda **kw: SimpleNamespace(status_code=503)
        assert s.flush()['failed'] == 2
        policy.now[0] += 61
        s.send_push = lambda **kw: (policy.sent.append(kw) or SimpleNamespace(status_code=201))
    eligible[0] = False
    assert s.flush()['sent'] == 0
    assert policy.sent == []
    assert ('alice','default','chat','completion') in calls
    assert all(status == 'suppressed' for status, _ in statuses(s))


def test_request_authority_revalidated_before_send_and_retry(policy):
    s = policy.service
    current = [True]
    calls = []
    def validate(user, delivery_id, session, category):
        calls.append((user,delivery_id,session,category))
        return current[0]
    s.request_validator = validate
    s.ingest('alice','request:r:unknown','Action needed','Check your request.',session_id='chat',category='attention')
    s.send_push = lambda **kw: SimpleNamespace(status_code=503)
    assert s.flush()['failed'] == 2
    current[0] = False
    policy.now[0] += 61
    s.send_push = lambda **kw: (policy.sent.append(kw) or SimpleNamespace(status_code=201))
    assert s.flush()['sent'] == 0
    assert policy.sent == []
    assert ('alice','request:r:unknown','chat','attention') in calls
