"""Real temporary SQLite tests; network is the only fake boundary."""
import tempfile
import unittest
from pathlib import Path


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'notifications.db'

    def test_inbox_is_durable_and_idempotent_per_user(self):
        from backend.notifications import NotificationService
        service = NotificationService(self.db)
        first = service.ingest('alice', 'cron:1', 'Brief', 'private body', session_id='chat:1')
        self.assertEqual(first['id'], service.ingest('alice', 'cron:1', 'changed', 'changed')['id'])
        other = service.ingest('bob', 'cron:1', 'Bob', 'his body')
        reopened = NotificationService(self.db)
        self.assertNotEqual(first['id'], other['id'])
        self.assertEqual(reopened.list_inbox('alice'), [first])
        self.assertEqual(first['body'], 'private body')
        self.assertEqual(first['session_id'], 'chat:1')
        self.assertFalse(first['read'])

    def test_mark_read_enforces_owner_and_persists(self):
        from backend.notifications import NotificationService
        service = NotificationService(self.db)
        item = service.ingest('alice', '1', 'A', 'B')
        self.assertFalse(service.mark_read('bob', item['id']))
        self.assertFalse(service.list_inbox('alice')[0]['read'])
        self.assertTrue(service.mark_read('alice', item['id']))
        self.assertTrue(NotificationService(self.db).list_inbox('alice')[0]['read'])

    def test_subscription_validation_rejects_ssrf_and_invalid_keys(self):
        from backend.notifications import NotificationService
        service = NotificationService(self.db)
        valid = subscription()
        self.assertEqual(service.subscribe('alice', 'device-1', valid)['endpoint'], valid['endpoint'])
        for endpoint in ('http://fcm.googleapis.com/a', 'https://127.0.0.1/a',
                         'https://[::1]/a', 'https://10.0.0.1/a',
                         'https://fcm.googleapis.com.evil.test/a',
                         'https://user:pw@fcm.googleapis.com/a',
                         'https://fcm.googleapis.com:8443/a',
                         'https://fcm.googleapis.com/a#fragment', 'https://evil.test/a'):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                service.subscribe('alice', 'device-1', {**valid, 'endpoint': endpoint})
        with self.assertRaises(ValueError):
            service.subscribe('alice', 'device-1', {**valid, 'keys': {'auth': 'bad', 'p256dh': 'bad'}})
        with self.assertRaises(ValueError):
            service.subscribe('alice', '', valid)
        with self.assertRaises(PermissionError):
            service.subscribe('bob', 'device-2', valid)
        self.assertFalse(service.unsubscribe('bob', 'device-1', valid['endpoint']))
        self.assertFalse(service.unsubscribe('alice', 'device-2', valid['endpoint']))
        self.assertTrue(service.unsubscribe('alice', 'device-1', valid['endpoint']))

    def test_outbox_is_transactional_deduplicated_and_suppresses_silent_history(self):
        import sqlite3
        from concurrent.futures import ThreadPoolExecutor
        from backend.notifications import NotificationService
        service = NotificationService(self.db)
        service.subscribe('alice', 'device-1', subscription())
        service.subscribe('bob', 'device-2', subscription('b'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            items = list(pool.map(lambda _: service.ingest('alice', 'same', 'T', 'B', category='scheduled'), range(12)))
        self.assertEqual(len({item['id'] for item in items}), 1)
        service.ingest('alice', 'silent', 'T', 'B', silent=True, category='scheduled')
        service.ingest('alice', 'history', 'T', 'B', historical=True, category='scheduled')
        with sqlite3.connect(self.db) as db:
            rows = db.execute('SELECT inbox_id,endpoint,status FROM outbox').fetchall()
        self.assertEqual(rows, [(items[0]['id'], subscription()['endpoint'], 'policy_pending')])
        self.assertEqual(len(service.list_inbox('alice')), 3)

    def test_missing_credentials_never_reports_push_success(self):
        from backend.notifications import NotificationService
        sent = []
        service = NotificationService(self.db, send_push=lambda **kw: sent.append(kw))
        service.subscribe('alice', 'device-1', subscription())
        service.ingest('alice', 'one', 'T', 'B', category='scheduled')
        result = service.flush()
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['sent'], 0)
        self.assertEqual(sent, [])
        self.assertEqual(len(service.list_inbox('alice')), 1)

    def test_real_pywebpush_encrypts_signs_and_disables_redirects(self):
        import json
        import requests
        from unittest.mock import patch
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
        from backend.notifications import NotificationService
        key = Path(self.temp.name) / 'vapid.pem'
        key.write_bytes(ec.generate_private_key(ec.SECP256R1()).private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
        service = NotificationService(self.db, vapid_private_key=str(key), vapid_public_key='public')
        captured = []
        def network(session, request, **kwargs):
            captured.append((request, kwargs, session.trust_env))
            response = requests.Response()
            response.status_code = 201
            return response
        with patch.object(requests.Session, 'send', network):
            response = service._send_web_push(subscription(), {'body': 'private-safe generic'})
        self.assertEqual(response.status_code, 201)
        request, kwargs, trust_env = captured[0]
        self.assertNotIn(b'private-safe generic', request.body)
        self.assertEqual(request.headers['content-encoding'], 'aes128gcm')
        self.assertTrue(request.headers['authorization'].startswith('vapid '))
        self.assertFalse(kwargs['allow_redirects'])
        self.assertFalse(trust_env)
        self.assertEqual(kwargs['timeout'], 10)

    def test_subscription_refresh_does_not_drop_queued_deliveries(self):
        import sqlite3
        from backend.notifications import NotificationService
        service = NotificationService(self.db)
        service.subscribe('alice', 'device-1', subscription())
        service.ingest('alice', 'one', 'T', 'B', category='scheduled')
        service.subscribe('alice', 'device-1', subscription())
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM outbox').fetchone()[0], 1)

    def test_flush_sends_private_payload_once_and_filters_user(self):
        import json
        import requests
        sent = []
        def network(**kwargs):
            sent.append(kwargs)
            response = requests.Response()
            response.status_code = 201
            return response
        service, auth, client, now, user = delivery_env(self, network)
        service.subscribe(user['id'], user['session_id'], subscription())
        with self.assertRaises(PermissionError):
            service.subscribe('foreign', 'foreign-device', subscription('foreign'))
        # Privacy remains explicit now that useful previews are the default.
        preferences = service.get_preferences(user['id'], user['session_id'])
        service.set_preferences(user['id'], user['session_id'], {**preferences, 'hide_details': True})
        item = service.ingest(user['id'], 'once', 'Secret title', 'Secret message', category='scheduled')
        self.assertEqual(service.flush()['sent'], 1)
        self.assertEqual(service.flush()['sent'], 0)
        self.assertEqual(len(sent), 1)
        payload = json.loads(sent[0]['data'])
        self.assertEqual(payload['url'], '/hermes/?inbox=' + item['id'])
        self.assertNotIn('Secret', sent[0]['data'])
        self.assertEqual(payload['title'], 'Hermes')
        self.assertEqual(payload['body'], 'You have a new notification.')
        self.assertEqual(service.list_inbox(user['id'])[0]['body'], 'Secret message')
        self.assertEqual(sent[0]['subscription_info']['endpoint'], subscription()['endpoint'])
        service.ingest(user['id'], 'once', 'T', 'B', category='scheduled')
        self.assertEqual(service.flush()['sent'], 0)

    def test_failed_push_retries_after_backoff_without_resending_success(self):
        import sqlite3
        import requests
        from backend.notifications import NotificationService
        sent = []
        def network(**kwargs):
            endpoint = kwargs['subscription_info']['endpoint']
            sent.append(endpoint)
            response = requests.Response()
            response.status_code = 503 if endpoint.endswith('/b') and sent.count(endpoint) == 1 else 201
            return response
        service, auth, client, now, user = delivery_env(self, network)
        for suffix in ('a', 'b'):
            service.subscribe(user['id'], user['session_id'], subscription(suffix))
        service.ingest(user['id'], 'retry', 'T', 'B', category='scheduled')
        result = service.flush()
        self.assertEqual(result['sent'], 1)
        self.assertEqual(result['failed'], 1)
        with sqlite3.connect(self.db) as db:
            status, attempts, next_at = db.execute("SELECT status,attempts,next_attempt_at FROM outbox WHERE endpoint LIKE '%/b'").fetchone()
        self.assertEqual((status, attempts), ('policy_retry', 1))
        self.assertGreater(next_at, now[0])
        reopened = NotificationService(self.db, vapid_private_key='test', vapid_public_key='test',
            send_push=network, session_validator=service.session_validator, clock=lambda: now[0])
        self.assertEqual(reopened.flush()['sent'], 0)
        now[0] = next_at
        self.assertEqual(reopened.flush()['sent'], 1)
        self.assertEqual(sent.count(subscription()['endpoint']), 1)
        self.assertEqual(sent.count(subscription('b')['endpoint']), 2)
        self.assertEqual(len(service.list_inbox(user['id'])), 1)

    def test_transport_failure_is_durable_and_does_not_rollback_other_success(self):
        import sqlite3
        import requests
        calls = []
        def network(**kwargs):
            calls.append(kwargs['subscription_info']['endpoint'])
            if calls[-1].endswith('/b'):
                raise requests.Timeout('secret endpoint must not be logged')
            response = requests.Response()
            response.status_code = 201
            return response
        service, auth, client, now, user = delivery_env(self, network)
        for suffix in ('a', 'b'):
            service.subscribe(user['id'], user['session_id'], subscription(suffix))
        service.ingest(user['id'], 'network', 'T', 'B', category='scheduled')
        result = service.flush()
        self.assertEqual((result['sent'], result['failed']), (1, 1))
        with sqlite3.connect(self.db) as db:
            rows = db.execute('SELECT status,last_error FROM outbox ORDER BY endpoint').fetchall()
        self.assertEqual(rows, [('sent', None), ('policy_retry', 'transport_error')])
        self.assertEqual(service.flush()['sent'], 0)

    def test_expired_provider_endpoints_are_pruned_without_losing_inbox(self):
        import sqlite3
        import requests
        from pywebpush import WebPushException
        def network(**kwargs):
            response = requests.Response()
            response.status_code = 410 if kwargs['subscription_info']['endpoint'].endswith('/a') else 404
            if response.status_code == 404:
                raise WebPushException('do not expose endpoint', response=response)
            return response
        service, auth, client, now, user = delivery_env(self, network)
        for suffix in ('a', 'b'):
            service.subscribe(user['id'], user['session_id'], subscription(suffix))
        service.ingest(user['id'], 'gone', 'T', 'B', category='scheduled')
        result = service.flush()
        self.assertEqual(result['pruned'], 2)
        self.assertEqual(result['sent'], 0)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM subscriptions').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM outbox').fetchone()[0], 0)
        self.assertEqual(len(service.list_inbox(user['id'])), 1)

    def test_revoked_device_is_pruned_before_delivery(self):
        import sqlite3
        sent = []
        service, auth, client, now, user = delivery_env(self, lambda **kw: sent.append(kw))
        service.subscribe(user['id'], user['session_id'], subscription())
        service.ingest(user['id'], 'revoked', 'T', 'B', category='scheduled')
        with auth.store.transaction() as db:
            db.execute('UPDATE sessions SET revoked=1 WHERE id=?', (user['session_id'],))
        result = service.flush()
        self.assertEqual(sent, [])
        self.assertEqual(result['pruned'], 1)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM subscriptions').fetchone()[0], 0)
        self.assertEqual(len(service.list_inbox(user['id'])), 1)

    def test_expired_outbox_is_pruned_while_inbox_remains(self):
        import sqlite3
        sent = []
        service, auth, client, now, user = delivery_env(self, lambda **kw: sent.append(kw))
        service.subscribe(user['id'], user['session_id'], subscription())
        service.ingest(user['id'], 'expired', 'T', 'B', category='scheduled')
        now[0] += 86401
        result = service.flush()
        self.assertEqual(sent, [])
        self.assertEqual(result['expired'], 1)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM outbox').fetchone()[0], 0)
        self.assertEqual(len(service.list_inbox(user['id'])), 1)

    def test_worker_crash_preserves_success_and_does_not_replay_ambiguous_send(self):
        import sqlite3
        import requests
        def network(**kwargs):
            if kwargs['subscription_info']['endpoint'].endswith('/b'):
                raise SystemExit('simulated worker death at external boundary')
            response = requests.Response()
            response.status_code = 201
            return response
        service, auth, client, now, user = delivery_env(self, network)
        for suffix in ('a', 'b'):
            service.subscribe(user['id'], user['session_id'], subscription(suffix))
        service.ingest(user['id'], 'crash', 'T', 'B', category='scheduled')
        with self.assertRaises(SystemExit):
            service.flush()
        with sqlite3.connect(self.db) as db:
            states = db.execute('SELECT status FROM outbox ORDER BY endpoint').fetchall()
        self.assertEqual(states, [('sent',), ('policy_sending',)])
        now[0] += 600
        self.assertEqual(service.flush()['sent'], 0)
        with sqlite3.connect(self.db) as db:
            states = db.execute('SELECT status FROM outbox ORDER BY endpoint').fetchall()
        self.assertEqual(states, [('sent',), ('unknown',)])

    def test_router_uses_real_auth_ownership_and_csrf(self):
        from fastapi.testclient import TestClient
        from backend.notifications import build_notifications_router
        from test_auth import BASE, ORIGIN
        service, auth, client, now, user = delivery_env(self, None)
        client.app.include_router(build_notifications_router(service, auth), prefix=BASE)
        anonymous = TestClient(client.app, base_url=ORIGIN)
        for path in ('/inbox', '/push/key'):
            self.assertEqual(anonymous.get(BASE + path).status_code, 401)
        mine = service.ingest(user['id'], 'mine', 'Mine', 'secret')
        foreign = service.ingest('other', 'foreign', 'Other', 'other secret')
        self.assertEqual(client.get(BASE + '/inbox').json()['items'], [mine])
        self.assertEqual(client.post(BASE + '/inbox/' + foreign['id'] + '/read').status_code, 404)
        self.assertEqual(client.post(BASE + '/inbox/' + mine['id'] + '/read', headers={'X-CSRF-Token': 'wrong'}).status_code, 403)
        self.assertEqual(client.post(BASE + '/inbox/' + mine['id'] + '/read', headers={'Origin': 'https://evil.test'}).status_code, 403)
        self.assertEqual(client.post(BASE + '/inbox/' + mine['id'] + '/read').status_code, 200)
        self.assertEqual(client.get(BASE + '/push/key').json(), {'public_key': 'test-public'})
        self.assertEqual(client.post(BASE + '/push/subscriptions', json=subscription()).status_code, 201)
        self.assertEqual(client.post(BASE + '/push/subscriptions', json={**subscription(), 'endpoint': 'https://127.0.0.1'}).status_code, 400)
        result = client.request('DELETE', BASE + '/push/subscriptions', json={'endpoint': subscription()['endpoint']})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()['removed'])

    def test_push_test_is_explicitly_queued_not_reported_delivered(self):
        from backend.notifications import build_notifications_router
        from test_auth import BASE
        sent = []
        service, auth, client, now, user = delivery_env(self, lambda **kw: sent.append(kw))
        client.app.include_router(build_notifications_router(service, auth), prefix=BASE)
        self.assertEqual(client.post(BASE + '/push/test').status_code, 409)
        client.post(BASE + '/push/subscriptions', json=subscription())
        response = client.post(BASE + '/push/test')
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['status'], 'queued')
        self.assertEqual(len(service.list_inbox(user['id'])), 1)
        self.assertEqual(sent, [])
        service.vapid_private_key = None
        self.assertEqual(client.get(BASE + '/push/key').status_code, 503)
        self.assertEqual(client.post(BASE + '/push/test').status_code, 503)
        self.assertEqual(len(service.list_inbox(user['id'])), 1)

    def test_router_binds_auth_active_session_validator(self):
        from backend.notifications import build_notifications_router
        service, auth, client, now, user = delivery_env(self, None)
        service.session_validator = None
        build_notifications_router(service, auth)
        self.assertEqual(service.session_validator, auth.is_session_active)

    def test_flush_prunes_idle_revoked_subscriptions_even_without_vapid(self):
        import sqlite3
        service, auth, client, now, user = delivery_env(self, None)
        service.subscribe(user['id'], user['session_id'], subscription())
        service.vapid_private_key = None
        with auth.store.transaction() as db:
            db.execute('UPDATE sessions SET revoked=1 WHERE id=?', (user['session_id'],))
        result = service.flush()
        self.assertEqual(result['status'], 'unavailable')
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM subscriptions').fetchone()[0], 0)

    def test_subscription_rejects_foreign_or_revoked_auth_session(self):
        service, auth, client, now, user = delivery_env(self, None)
        with self.assertRaises(PermissionError):
            service.subscribe('foreign', user['session_id'], subscription())
        with self.assertRaises(PermissionError):
            service.subscribe(user['id'], 'foreign-device', subscription())
        with auth.store.transaction() as db:
            db.execute('UPDATE sessions SET revoked=1 WHERE id=?', (user['session_id'],))
        with self.assertRaises(PermissionError):
            service.subscribe(user['id'], user['session_id'], subscription())

    def test_private_database_creates_parent_and_restricts_permissions(self):
        import stat
        from backend.notifications import NotificationService
        path = Path(self.temp.name) / 'private' / 'notifications.sqlite'
        service = NotificationService(path)
        service.ingest('alice', 'one', 'T', 'B')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_browser_subscription_expiry_prevents_delivery(self):
        import sqlite3
        sent = []
        service, auth, client, now, user = delivery_env(self, lambda **kw: sent.append(kw))
        service.subscribe(user['id'], user['session_id'], {**subscription(), 'expirationTime': (now[0] + 10) * 1000})
        service.ingest(user['id'], 'expires', 'T', 'B', category='scheduled')
        now[0] += 11
        result = service.flush()
        self.assertEqual(sent, [])
        self.assertEqual(result['pruned'], 1)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM subscriptions').fetchone()[0], 0)

    def test_subscription_expiry_rejects_invalid_or_past_values(self):
        service, auth, client, now, user = delivery_env(self, None)
        for expiry in ('soon', {}, True, float('nan'), float('inf'), -1, now[0] * 1000):
            with self.subTest(expiry=expiry), self.assertRaises(ValueError):
                service.subscribe(user['id'], user['session_id'], {**subscription(), 'expirationTime': expiry})

    def test_concurrent_workers_claim_once_without_holding_sqlite_during_network(self):
        from concurrent.futures import ThreadPoolExecutor
        import requests
        sent = []
        def network(**kwargs):
            sent.append(kwargs)
            # A separate write connection must work while the fake network is in flight.
            self.assertTrue(service.mark_read(user['id'], item['id']))
            response = requests.Response()
            response.status_code = 201
            return response
        service, auth, client, now, user = delivery_env(self, network)
        service.subscribe(user['id'], user['session_id'], subscription())
        item = service.ingest(user['id'], 'concurrent', 'T', 'B', category='scheduled')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: service.flush(), range(8)))
        self.assertEqual(sum(r['sent'] for r in results), 1)
        self.assertEqual(len(sent), 1)

    def test_sqlite_outbox_failure_rolls_back_inbox_insert(self):
        import sqlite3
        from backend.notifications import NotificationService
        service = NotificationService(self.db)
        service.subscribe('alice', 'device-1', subscription())
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TRIGGER reject_outbox BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT,'disk simulation'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            service.ingest('alice', 'atomic', 'T', 'B', category='scheduled')
        self.assertEqual(service.list_inbox('alice'), [])

    def test_absent_validator_fails_closed_despite_configured_push_credentials(self):
        from backend.notifications import NotificationService
        calls = []
        service = NotificationService(self.db, vapid_private_key='key', vapid_public_key='key',
                                      send_push=lambda **kw: calls.append(kw))
        service.subscribe('alice', 'device-1', subscription())
        service.ingest('alice', 'closed', 'T', 'B', category='scheduled')
        self.assertEqual(service.flush()['status'], 'unavailable')
        self.assertEqual(calls, [])

    def test_concurrent_stale_worker_cannot_bypass_retry_backoff(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        import requests
        entered, release = Event(), Event()
        calls = []
        def network(**kwargs):
            endpoint = kwargs['subscription_info']['endpoint']
            calls.append(endpoint)
            response = requests.Response()
            if endpoint.endswith('/a'):
                entered.set()
                self.assertTrue(release.wait(5))
                response.status_code = 201
            else:
                response.status_code = 503
            return response
        service, auth, client, now, user = delivery_env(self, network)
        for suffix in ('a', 'b'):
            service.subscribe(user['id'], user['session_id'], subscription(suffix))
        service.ingest(user['id'], 'race', 'T', 'B', category='scheduled')
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(service.flush)
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(service.flush()['failed'], 1)
            finally:
                release.set()
            first.result(timeout=5)
        self.assertEqual(calls.count(subscription('b')['endpoint']), 1)


def delivery_env(test, network):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from starlette.requests import Request
    from backend.auth import AuthService, build_auth_router
    from backend.notifications import NotificationService
    from test_auth import BOOTSTRAP, enroll, ORIGIN, BASE
    now = [1800000000.0]
    auth = AuthService(Path(test.temp.name) / 'auth.db', clock=lambda: now[0], bootstrap_secret=BOOTSTRAP)
    app = FastAPI()
    app.include_router(build_auth_router(auth), prefix=BASE)
    client = TestClient(app, base_url=ORIGIN)
    client.headers['Origin'] = ORIGIN
    enroll(client)
    cookie = client.cookies.get('hermes_session')
    request = Request({'type': 'http', 'headers': [(b'cookie', ('hermes_session=' + cookie).encode())]})
    user = auth.require_user(request)
    service = NotificationService(test.db, vapid_private_key='test-key', vapid_public_key='test-public',
                                  send_push=network, clock=lambda: now[0], session_validator=auth.is_session_active)
    return service, auth, client, now, user


def subscription(suffix='a'):
    import base64
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    public = ec.derive_private_key(1, ec.SECP256R1()).public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    encode = lambda raw: base64.urlsafe_b64encode(raw).decode().rstrip('=')
    return {'endpoint': 'https://fcm.googleapis.com/fcm/send/' + suffix,
            'keys': {'p256dh': encode(public), 'auth': encode(b'0123456789abcdef')}}


if __name__ == '__main__':
    unittest.main()
