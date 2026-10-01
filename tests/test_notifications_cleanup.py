"""Cleanup uses disposable SQLite and real auth HTTP; never production data/network."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.notifications import NotificationService, build_notifications_router
from test_notifications import delivery_env, subscription
from test_auth import BASE, ORIGIN


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'notifications.db'
        self.service, self.auth, self.client, self.now, self.user = delivery_env(self, None)
        self.client.app.include_router(build_notifications_router(self.service, self.auth), prefix=BASE)

    def item(self, delivery='one', owner=None, read=True):
        item = self.service.ingest(owner or self.user['id'], delivery, 'Title', 'Body', session_id='native-history-kept', category='scheduled')
        if read:
            self.service.mark_read(owner or self.user['id'], item['id'])
        return item

    def post(self, suffix, **kwargs):
        return self.client.post(BASE + '/inbox/' + suffix, json={}, **kwargs)

    def test_cleanup_missing_csrf_and_revoked_sessions_fail_closed(self):
        from fastapi.testclient import TestClient
        item = self.item()
        no_csrf = TestClient(self.client.app, base_url=ORIGIN)
        no_csrf.cookies.update(self.client.cookies)
        for suffix in (item['id'] + '/dismiss', 'clear-read'):
            self.assertEqual(no_csrf.post(BASE + '/inbox/' + suffix, headers={'Origin': ORIGIN}).status_code, 403)
        with self.auth.store.transaction() as db:
            db.execute('UPDATE sessions SET revoked=1 WHERE id=?', (self.user['session_id'],))
        for suffix in (item['id'] + '/dismiss', 'clear-read'):
            self.assertEqual(self.post(suffix).status_code, 401)
        self.assertEqual(len(self.service.list_inbox(self.user['id'])), 1)

    def test_cleanup_transaction_rolls_back_tombstone_when_suppression_fails(self):
        self.service.subscribe(self.user['id'], self.user['session_id'], subscription())
        item = self.item(read=False)
        with sqlite3.connect(self.db) as db:
            # Model a restored read receipt with unclaimed policy work. Calling
            # mark_read now suppresses it first, hiding cleanup's atomicity seam.
            db.execute('UPDATE inbox SET read=1 WHERE id=?', (item['id'],))
            db.execute("CREATE TRIGGER reject_suppress BEFORE UPDATE ON outbox BEGIN SELECT RAISE(ABORT,'simulated disk error'); END")
        for cleanup in (lambda: self.service.dismiss(self.user['id'], item['id']),
                        lambda: self.service.clear_read(self.user['id'])):
            with self.assertRaises(sqlite3.IntegrityError):
                cleanup()
            self.assertEqual(len(self.service.list_inbox(self.user['id'])), 1)
            self.assertEqual(self.service.read_count(self.user['id']), 1)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM dismissed_inbox').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT status FROM outbox').fetchone()[0], 'policy_pending')

    def test_concurrent_cleanup_is_idempotent_and_preserves_redelivery(self):
        from concurrent.futures import ThreadPoolExecutor
        item = self.item()
        def work(n):
            if n % 3 == 0:
                self.service.ingest(self.user['id'], 'one', 'Changed', 'Changed')
                return 0
            return self.service.clear_read(self.user['id']) if n % 3 == 1 else self.service.dismiss(self.user['id'], item['id'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(work, range(12))), 1)
        self.assertEqual(self.service.list_inbox(self.user['id']), [])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM inbox').fetchone()[0], 1)

    def test_legacy_pending_hidden_receipt_does_not_starve_visible_push(self):
        import requests
        calls = []
        def network(**kwargs):
            calls.append(kwargs)
            response = requests.Response()
            response.status_code = 201
            return response
        self.service.send_push = network
        self.service.subscribe(self.user['id'], self.user['session_id'], subscription())
        hidden = self.item('hidden')
        self.service.dismiss(self.user['id'], hidden['id'])
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE outbox SET status='pending' WHERE inbox_id=?", (hidden['id'],))
            self.assertEqual(db.execute('SELECT status FROM outbox WHERE inbox_id=?',
                                       (hidden['id'],)).fetchone()[0], 'suppressed')
        visible = self.item('visible', read=False)
        self.assertEqual(self.service.flush(limit=1)['sent'], 1)
        self.assertEqual(len(calls), 1)
        self.assertIn(visible['id'], calls[0]['data'])

    def test_dismiss_during_inflight_failure_never_schedules_retry(self):
        import requests
        item = None
        calls = []
        def network(**kwargs):
            calls.append(kwargs)
            self.assertTrue(self.service.mark_read(self.user['id'], item['id']))
            self.assertEqual(self.post(item['id'] + '/dismiss').status_code, 200)
            response = requests.Response()
            response.status_code = 503
            return response
        self.service.send_push = network
        self.service.subscribe(self.user['id'], self.user['session_id'], subscription())
        item = self.item(read=False)  # Read/dismiss only after the claim, in network().
        self.assertEqual(self.service.flush()['failed'], 1)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT status FROM outbox').fetchone()[0], 'suppressed')
        self.now[0] += 3600
        self.assertEqual(self.service.flush()['sent'], 0)
        self.assertEqual(len(calls), 1)

    def test_stale_pending_candidate_cannot_claim_hidden_receipt(self):
        calls = []
        self.service.send_push = lambda **kw: calls.append(kw)
        self.service.subscribe(self.user['id'], self.user['session_id'], subscription())
        item = self.item(read=False)
        original = self.service.session_validator
        checks = []
        def validator(user_id, device_id):
            checks.append(1)
            if len(checks) == 2:  # after candidate SELECT, before claim
                self.assertTrue(self.service.mark_read(user_id, item['id']))
                self.service.dismiss(user_id, item['id'])
                # Restore a stale policy candidate, not legacy pending (which
                # the rollback DB guard suppresses before claim is exercised).
                with sqlite3.connect(self.db) as db:
                    db.execute("UPDATE outbox SET status='policy_pending'")
                    self.assertEqual(db.execute('SELECT status FROM outbox').fetchone()[0],
                                     'policy_pending')
            return original(user_id, device_id)
        self.service.session_validator = validator
        self.assertEqual(self.service.flush()['failed'], 0)
        self.assertGreaterEqual(len(checks), 2, 'The stale candidate race must actually run')
        self.assertEqual(calls, [])

    def test_cleanup_suppresses_pending_and_retry_without_recalling_claimed(self):
        calls = []
        self.service.send_push = lambda **kw: calls.append(kw)
        self.service.subscribe(self.user['id'], self.user['session_id'], subscription())
        states = ['policy_pending', 'policy_retry', 'policy_sending', 'sent']
        ids = []
        for status in states:
            item = self.item(status)
            ids.append(item['id'])
            with sqlite3.connect(self.db) as db:
                # Restore pre-cleanup states after mark_read's suppression, so
                # this test exercises cleanup rather than read suppression.
                db.execute('UPDATE outbox SET status=?,next_attempt_at=? WHERE inbox_id=?',
                           (status, self.now[0] + (60 if status == 'policy_sending' else 0), item['id']))
        self.assertEqual(self.post(ids[0] + '/dismiss').status_code, 200)
        self.assertEqual(self.post('clear-read').json()['dismissed'], 3)
        with sqlite3.connect(self.db) as db:
            actual = [db.execute('SELECT status FROM outbox WHERE inbox_id=?', (item_id,)).fetchone()[0] for item_id in ids]
        self.assertEqual(actual, ['suppressed', 'suppressed', 'policy_sending', 'sent'])
        self.assertEqual(self.service.flush()['sent'], 0)
        self.assertEqual(calls, [])

    def test_cleanup_auth_owner_unread_and_hidden_read_boundaries(self):
        from fastapi.testclient import TestClient
        item = self.item()
        unread = self.item('unread', read=False)
        foreign = self.item('foreign', owner='foreign')
        anonymous = TestClient(self.client.app, base_url=ORIGIN)
        for suffix in (item['id'] + '/dismiss', 'clear-read'):
            self.assertEqual(anonymous.post(BASE + '/inbox/' + suffix).status_code, 401)
            self.assertEqual(self.post(suffix, headers={'Origin': 'https://evil.test'}).status_code, 403)
            self.assertEqual(self.post(suffix, headers={'X-CSRF-Token': 'wrong'}).status_code, 403)
        self.assertEqual(self.post(unread['id'] + '/dismiss').status_code, 409)
        for item_id in ('missing', foreign['id']):
            self.assertEqual(self.post(item_id + '/dismiss').status_code, 404)
        self.assertEqual(self.post(item['id'] + '/dismiss').status_code, 200)
        self.assertEqual(self.post(item['id'] + '/read').status_code, 404)
        self.assertEqual(self.post(unread['id'] + '/read').status_code, 200)

    def test_clear_read_uses_all_owned_pages_and_additive_legacy_schema(self):
        # Legacy positional insert stays valid before AND after migration.
        with sqlite3.connect(self.db) as db:
            db.execute('DROP TABLE dismissed_inbox') if db.execute("SELECT 1 FROM sqlite_master WHERE name='dismissed_inbox'").fetchone() else None
            for n in range(205):
                db.execute('INSERT INTO inbox VALUES(?,?,?,?,?,?,?,?)',
                           (f'old-{n}', self.user['id'], f'd-{n}', 'Old', 'Receipt', 'native', 1, 1))
        self.service = NotificationService(self.db)
        unread = self.item('unread', read=False)
        foreign = self.item('foreign', owner='foreign')
        page = self.client.get(BASE + '/inbox?limit=1').json()
        self.assertEqual(page['items'][0]['id'], unread['id'])
        self.assertEqual(page.get('read_count'), 205)
        self.assertEqual(self.post('clear-read').json(), {'ok': True, 'dismissed': 205})
        self.assertEqual(self.post('clear-read').json(), {'ok': True, 'dismissed': 0})
        self.assertEqual(self.client.get(BASE + '/inbox').json(), {'items': [unread], 'read_count': 0})
        self.assertEqual(self.service.list_inbox('foreign')[0]['id'], foreign['id'])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM inbox').fetchone()[0], 207)
            db.execute('INSERT INTO inbox VALUES(?,?,?,?,?,?,?,?)', ('rollback', 'foreign', 'rollback', 'T', 'B', 'native', 2, 0))
        self.assertEqual(len(NotificationService(self.db).list_inbox('foreign')), 2)

    def test_dismiss_is_durable_hidden_without_destroying_receipt_or_dedup(self):
        item = self.item()
        other = self.item(owner='other')
        response = self.post(item['id'] + '/dismiss')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ok': True, 'dismissed': 1})
        reopened = NotificationService(self.db)
        self.assertEqual(reopened.list_inbox(self.user['id']), [])
        self.assertEqual(reopened.list_inbox('other')[0]['id'], other['id'])
        self.assertEqual(reopened.ingest(self.user['id'], 'one', 'Changed', 'Changed')['id'], item['id'])
        self.assertEqual(reopened.list_inbox(self.user['id']), [])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT session_id,body FROM inbox WHERE id=?', (item['id'],)).fetchone(), ('native-history-kept', 'Body'))
        self.assertEqual(self.post(item['id'] + '/dismiss').json(), {'ok': True, 'dismissed': 0})
