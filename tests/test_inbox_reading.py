"""Reading never resolves actions, mutates background receipts, or replays pushes."""
import sqlite3
from fastapi.testclient import TestClient
import unittest
import test_notifications_cleanup as cleanup
from test_notifications import subscription
from test_auth import BASE, ORIGIN


class InboxReadingTests(unittest.TestCase):
    setUp = cleanup.CleanupTests.setUp
    item = cleanup.CleanupTests.item
    post = cleanup.CleanupTests.post
    def test_bulk_read_all_pages_attention_only_idempotent_and_suppresses_push(self):
        self.service.subscribe(self.user['id'], self.user['session_id'], subscription())
        visible = self.item('visible', read=False)
        foreign = self.item('foreign', owner='foreign', read=False)
        background = self.service.ingest(self.user['id'], 'bg', 'Background', 'Kept', category='background')
        receipt = self.service.store_background(scope='default', event_id='event', digest='digest',
            user_id=self.user['id'], origin='origin', session_id='native', event={}, lease_token='lease', body='Kept')
        hidden = self.item('hidden')
        self.service.dismiss(self.user['id'], hidden['id'])
        with sqlite3.connect(self.db) as db:
            for n in range(205):
                db.execute('INSERT INTO inbox VALUES(?,?,?,?,?,?,?,?)',
                           (f'old-{n}', self.user['id'], f'old-{n}', 'Old', 'Body', None, 1, 0))
        assert len(self.client.get(BASE + '/inbox').json()['items']) == 100
        response = self.post('read-all')
        assert response.status_code == 200, response.text
        assert response.json() == {'ok': True, 'updated': 206}
        assert self.post('read-all').json() == {'ok': True, 'updated': 0}
        with sqlite3.connect(self.db) as db:
            assert db.execute('SELECT read FROM inbox WHERE id=?', (visible['id'],)).fetchone()[0] == 1
            for item_id in (foreign['id'], background['id'], receipt):
                assert db.execute('SELECT read FROM inbox WHERE id=?', (item_id,)).fetchone()[0] == 0
            assert db.execute('SELECT status FROM outbox WHERE inbox_id=?', (visible['id'],)).fetchone()[0] == 'suppressed'
            assert db.execute('SELECT count(*) FROM inbox').fetchone()[0] == 210
            assert db.execute('SELECT acknowledged FROM background_receipts').fetchone()[0] == 0
        assert self.service.ingest(self.user['id'], 'visible', 'Changed', 'Changed')['id'] == visible['id']

    def test_bulk_read_rolls_back_on_suppression_failure_and_keeps_approvals(self):
        self.service.subscribe(self.user['id'], self.user['session_id'], subscription())
        item = self.item(read=False)
        self.service.approval_validator = lambda *args: True
        approval = self.service.ingest_approval(self.user['id'], 'run', 'req', 'action', 'native', self.now[0]+3600)
        with sqlite3.connect(self.db) as db:
            before = db.execute('SELECT * FROM approval_notifications').fetchall()
            db.execute("CREATE TRIGGER reject_read BEFORE UPDATE ON outbox BEGIN SELECT RAISE(ABORT,'disk error'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.mark_all_read(self.user['id'])
        assert not any(i['read'] for i in self.service.list_inbox(self.user['id']))
        with sqlite3.connect(self.db) as db:
            db.execute('DROP TRIGGER reject_read')
        assert self.post('read-all').json()['updated'] == 2
        with sqlite3.connect(self.db) as db:
            assert db.execute('SELECT * FROM approval_notifications').fetchall() == before
        assert self.service.list_inbox(self.user['id'])[0]['read']

    def test_bulk_read_revalidates_revocation_after_notification_writer_admission(self):
        from contextlib import contextmanager
        item = self.item(read=False)
        original = self.service._db
        @contextmanager
        def revoked_after_http_auth():
            with original() as db:
                with self.auth.store.transaction() as auth_db:
                    auth_db.execute('UPDATE sessions SET revoked=1 WHERE id=?', (self.user['session_id'],))
                yield db
        self.service._db = revoked_after_http_auth
        assert self.post('read-all').status_code == 401
        self.service._db = original
        assert not self.service.list_inbox(self.user['id'])[0]['read']

    def test_bulk_read_during_inflight_push_failure_does_not_retry(self):
        import requests
        calls=[]
        def network(**kwargs):
            calls.append(kwargs)
            assert self.post('read-all').json()['updated'] == 1
            response=requests.Response();response.status_code=503
            return response
        self.service.send_push=network
        self.service.subscribe(self.user['id'], self.user['session_id'], subscription())
        self.item(read=False)
        assert self.service.flush()['failed'] == 1
        with sqlite3.connect(self.db) as db:
            assert db.execute('SELECT status FROM outbox').fetchone()[0] == 'suppressed'
        self.now[0] += 3600
        assert self.service.flush()['sent'] == 0
        assert len(calls) == 1

    def test_bulk_read_requires_auth_origin_csrf_and_current_session(self):
        self.item(read=False)
        anonymous = TestClient(self.client.app, base_url=ORIGIN)
        assert anonymous.post(BASE + '/inbox/read-all').status_code == 401
        no_csrf = TestClient(self.client.app, base_url=ORIGIN)
        no_csrf.cookies.update(self.client.cookies)
        assert no_csrf.post(BASE + '/inbox/read-all', headers={'Origin': ORIGIN}).status_code == 403
        assert self.post('read-all', headers={'Origin': 'https://evil.test'}).status_code == 403
        assert self.post('read-all', headers={'X-CSRF-Token': 'wrong'}).status_code == 403
        with self.auth.store.transaction() as db:
            db.execute('UPDATE sessions SET revoked=1 WHERE id=?', (self.user['session_id'],))
        assert self.post('read-all').status_code == 401
        assert not self.service.list_inbox(self.user['id'])[0]['read']
