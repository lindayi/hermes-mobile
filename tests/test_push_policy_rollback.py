"""Exact previous-release sender against only synthetic upgraded SQLite."""
import importlib.util
from pathlib import Path
from test_push_policy import policy, statuses

BASE = Path('/home/lindayi/.local/share/hermes-mobile-deploy/releases/cb087c0bb8fb4b0b817cef00ba098372')


def test_exact_old_sender_and_legacy_ingress_cannot_bypass_policy_after_rollback(policy):
    spec = importlib.util.spec_from_file_location('legacy_notification_sender', BASE/'backend/notifications.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    s = policy.service
    s.ingest('alice','new-policy','Finished','Public final',category='completion')
    assert {status for status, _ in statuses(s)} == {'policy_pending'}
    old = module.NotificationService(s.db_path, 'private', 'public', clock=s.clock,
        session_validator=s.session_validator, send_push=s.send_push)
    assert old.flush()['sent'] == 0
    old.ingest('alice','legacy-after-rollback','Private output','Never push')
    old.store_background(scope='p',event_id='e',digest='d',user_id='alice',origin='child',session_id=None,
        event={'public':'result'},lease_token='lease',body='Background')
    old.background_acknowledged('p','e','lease')
    assert old.flush()['sent'] == 0
    assert policy.sent == []
    assert old.background_rows('p','alice')[0]['acknowledged'] == 1
    with s._db() as db:
        db.execute("UPDATE outbox SET status='retry' WHERE status='suppressed'")
        assert db.execute("SELECT count(*) FROM outbox WHERE status IN ('pending','retry')").fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM subscriptions').fetchone()[0] == 3
    assert s.flush()['sent'] == 2
    assert {i['title'] for i in s.list_inbox('alice')} == {'Finished', 'Private output'}
    with s._db() as db:
        assert db.execute('SELECT count(*) FROM inbox WHERE user_id=?', ('alice',)).fetchone()[0] == 3
    rows = s.background_rows('p', 'alice')
    assert len(rows) == 1 and rows[0]['acknowledged'] == 1 and rows[0]['body'] == 'Background'
