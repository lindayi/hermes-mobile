"""Durable, user-owned inbox and Web Push delivery (separate private SQLite)."""
import base64
import hmac
import json
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

import sqlite3
import time
import uuid
from contextlib import contextmanager, nullcontext
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from .notification_policy import ALL_CATEGORIES, CATEGORIES, default_preferences, validate_preferences, validate_presence, safe_preview, GENERIC_PREVIEW


def _attention_visible_sql(db):
    """One attention predicate for listing (before pagination), counts and cleanup."""
    # Provenance must belong to the same owner; titles/bodies never classify.
    predicate = '''
        NOT EXISTS (SELECT 1 FROM dismissed_inbox d
                    WHERE d.inbox_id=inbox.id AND d.user_id=inbox.user_id)
        AND NOT EXISTS (SELECT 1 FROM background_receipts r
                        WHERE r.inbox_id=inbox.id AND r.user_id=inbox.user_id)
    '''
    # The silent release observer can use a pre-policy DB without initializing
    # schema. Only absent metadata is optional; database/query errors propagate.
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='notification_policy'").fetchone():
        predicate += '''AND NOT EXISTS (SELECT 1 FROM notification_policy p
                         WHERE p.inbox_id=inbox.id AND p.category='background')'''
    return predicate


class NotificationService:
    def __init__(self, db_path, vapid_private_key=None, vapid_public_key=None,
                 vapid_subject='https://lindayi.me', send_push=None, *,
                 clock=time.time, session_validator=None, approval_validator=None, presence_validator=None,
                 event_validator=None, presence_resolver=None, request_validator=None,
                 device_claim_guard=None):
        self.db_path = str(db_path)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.db_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.db_path, 0o600)
        self.vapid_private_key = vapid_private_key
        self.vapid_public_key = vapid_public_key
        self.vapid_subject = vapid_subject
        self.send_push = send_push
        self.clock = clock
        self.session_validator = session_validator
        self.approval_validator = approval_validator
        self.presence_validator = presence_validator
        self.event_validator = event_validator
        self.presence_resolver = presence_resolver
        self.request_validator = request_validator
        self.device_claim_guard = device_claim_guard
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS inbox (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, delivery_id TEXT NOT NULL,
                title TEXT NOT NULL, body TEXT NOT NULL, session_id TEXT,
                created_at REAL NOT NULL, read INTEGER NOT NULL DEFAULT 0,
                UNIQUE(user_id, delivery_id))''')
            db.execute('''CREATE TABLE IF NOT EXISTS background_receipts (
                scope TEXT NOT NULL, event_id TEXT NOT NULL, digest TEXT NOT NULL,
                user_id TEXT NOT NULL, origin TEXT NOT NULL,
                inbox_id TEXT NOT NULL UNIQUE REFERENCES inbox(id), event_json TEXT NOT NULL,
                lease_token TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(scope,event_id))''')
            db.execute('''CREATE TABLE IF NOT EXISTS approval_notifications (
                inbox_id TEXT PRIMARY KEY REFERENCES inbox(id),
                approval_id TEXT NOT NULL, run_id TEXT NOT NULL,
                request_id TEXT NOT NULL, expires_at REAL NOT NULL)''')

            db.execute('''CREATE TABLE IF NOT EXISTS dismissed_inbox (
                inbox_id TEXT PRIMARY KEY REFERENCES inbox(id),
                user_id TEXT NOT NULL, dismissed_at REAL NOT NULL)''')

            db.execute('''CREATE TABLE IF NOT EXISTS subscriptions (
                endpoint TEXT PRIMARY KEY, user_id TEXT NOT NULL, device_id TEXT NOT NULL,
                subscription TEXT NOT NULL)''')

            db.execute('''CREATE TABLE IF NOT EXISTS outbox (
                id TEXT PRIMARY KEY, inbox_id TEXT NOT NULL REFERENCES inbox(id),
                endpoint TEXT NOT NULL REFERENCES subscriptions(endpoint) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL, expires_at REAL NOT NULL,
                last_error TEXT, UNIQUE(inbox_id,endpoint))''')
            db.execute('''CREATE TABLE IF NOT EXISTS notification_policy (
                inbox_id TEXT PRIMARY KEY REFERENCES inbox(id), category TEXT NOT NULL,
                profile TEXT, device_id TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS push_preferences (
                user_id TEXT NOT NULL, device_id TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(user_id,device_id))''')
            db.execute('''CREATE TABLE IF NOT EXISTS push_presence (
                user_id TEXT NOT NULL, device_id TEXT NOT NULL, client_id TEXT NOT NULL,
                session_id TEXT, visible INTEGER NOT NULL, sequence INTEGER NOT NULL,
                expires_at REAL NOT NULL, PRIMARY KEY(user_id,device_id,client_id))''')
            # Pre-policy deliveries have no trusted classification. Never replay them.
            db.execute('''UPDATE outbox SET status='suppressed',last_error='legacy_policy'
                WHERE status IN ('pending','retry')''')
            # A rolled-back sender knows only pending/retry. Preserve its Inbox/ACK
            # transactions, but prevent it creating policy-bypassing deliveries.
            for operation in ('INSERT', 'UPDATE OF status'):
                trigger = 'policy_guard_insert' if operation == 'INSERT' else 'policy_guard_update'
                db.execute(f'''CREATE TRIGGER IF NOT EXISTS {trigger} AFTER {operation} ON outbox
                    WHEN NEW.status IN ('pending','retry') BEGIN
                    UPDATE outbox SET status='suppressed',last_error='legacy_policy' WHERE id=NEW.id;
                    END''')

    def _require_device(self, user_id, device_id):
        if not self.session_validator or not self.session_validator(user_id, device_id):
            raise PermissionError('Inactive or foreign device')

    @staticmethod
    def _preferences(db, user_id, device_id):
        row = db.execute('SELECT payload FROM push_preferences WHERE user_id=? AND device_id=?',
                         (user_id, device_id)).fetchone()
        return json.loads(row['payload']) if row else default_preferences()

    def get_preferences(self, user_id, device_id):
        self._require_device(user_id, device_id)
        with self._db() as db:
            return self._preferences(db, user_id, device_id)

    def set_preferences(self, user_id, device_id, payload, *, _authorize=None):
        prefs = validate_preferences(payload)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._require_device(user_id, device_id)
            # Capture the request's bearer generation through the local commit.
            # Lock order is notification -> auth; callbacks run before the guard.
            with _authorize() if _authorize else nullcontext():
                if self._preferences(db, user_id, device_id)['revision'] != prefs['revision']:
                    raise HTTPException(409, 'Push preferences changed; reload and retry')
                prefs['revision'] += 1
                db.execute('''INSERT INTO push_preferences VALUES(?,?,?) ON CONFLICT(user_id,device_id)
                    DO UPDATE SET payload=excluded.payload''', (user_id, device_id, json.dumps(prefs)))
                rows = db.execute('''SELECT o.id,p.category FROM outbox o
                    JOIN subscriptions s ON s.endpoint=o.endpoint
                    LEFT JOIN notification_policy p ON p.inbox_id=o.inbox_id
                    WHERE s.user_id=? AND s.device_id=? AND o.status IN ('policy_pending','policy_retry')''',
                    (user_id, device_id)).fetchall()
                for row in rows:
                    if not self._category_enabled(prefs, row['category']):
                        db.execute("UPDATE outbox SET status='suppressed',last_error='push_disabled' WHERE id=?", (row['id'],))
                db.commit()
        return prefs

    @staticmethod
    def _category_enabled(prefs, category):
        return prefs['enabled'] and (category == 'test' or prefs['categories'].get(category, False))

    def record_presence(self, user_id, device_id, payload, *, _authorize=None):
        payload = validate_presence(payload)
        self._require_device(user_id, device_id)
        session_id = payload['session_id']
        if session_id is not None and self.presence_resolver:
            session_id = self.presence_resolver(user_id, session_id)
            if not session_id:
                raise PermissionError('Conversation unavailable')
        if session_id is not None and (not self.presence_validator or not self.presence_validator(user_id, session_id)):
            raise PermissionError('Conversation unavailable')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT 1 FROM push_presence WHERE user_id=? AND device_id=? AND client_id=?',
                                  (user_id, device_id, payload['client_id'])).fetchone()
            if not existing:
                device_count = db.execute('SELECT count(*) FROM push_presence WHERE user_id=? AND device_id=?',
                                          (user_id, device_id)).fetchone()[0]
                user_count = db.execute('SELECT count(*) FROM push_presence WHERE user_id=?', (user_id,)).fetchone()[0]
                if device_count >= 64 or user_count >= 256:
                    raise ValueError('Presence client limit reached')
            # Retain high-water marks even after hide/expiry. No eviction can let
            # delayed show packets recreate a stale live lease.
            self._require_device(user_id, device_id)
            if _authorize:
                _authorize()
            db.execute('''INSERT INTO push_presence VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(user_id,device_id,client_id) DO UPDATE SET session_id=excluded.session_id,
                visible=excluded.visible,sequence=excluded.sequence,expires_at=excluded.expires_at
                WHERE excluded.sequence > push_presence.sequence''',
                (user_id, device_id, payload['client_id'], session_id, payload['visible'],
                 payload['sequence'], self.clock() + 45))
        return {'ok': True}

    def _present(self, db, user_id, session_id, category):
        if not session_id or category not in ('completion','approval','attention'):
            return False
        resolve = self.presence_resolver or (lambda user, sid: sid)
        session_id = resolve(user_id, session_id)
        if not session_id:
            return False
        if not self.presence_validator or not self.presence_validator(user_id, session_id):
            return False
        rows = db.execute('''SELECT device_id,session_id FROM push_presence WHERE user_id=?
            AND session_id IS NOT NULL AND visible=1 AND expires_at>? LIMIT 256''', (user_id, self.clock())).fetchall()
        return any(resolve(user_id, row['session_id']) == session_id
                   and self.session_validator(user_id, row['device_id']) for row in rows)

    def store_background(self, *, scope, event_id, digest, user_id, origin, session_id,
                         event, lease_token, body, historical=False):
        """Commit public Activity storage and private receipt/ACK intent atomically."""
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT * FROM background_receipts WHERE scope=? AND event_id=?',
                                  (scope, event_id)).fetchone()
            if existing:
                if (existing['digest'], existing['user_id'], existing['origin']) != (digest, user_id, origin):
                    raise ValueError('Background receipt identity conflict')
                db.execute('UPDATE background_receipts SET lease_token=?,acknowledged=0 WHERE scope=? AND event_id=?',
                           (lease_token, scope, event_id))
                return existing['inbox_id']
            receipt_id = uuid.uuid4().hex
            db.execute('''INSERT INTO inbox
                (id,user_id,delivery_id,title,body,session_id,created_at) VALUES(?,?,?,?,?,?,?)''',
                (receipt_id, user_id, 'native-event:v1:' + json.dumps([scope, event_id]),
                 'Background result', body, session_id, self.clock()))
            db.execute('''INSERT INTO background_receipts
                (scope,event_id,digest,user_id,origin,inbox_id,event_json,lease_token)
                VALUES(?,?,?,?,?,?,?,?)''',
                (scope, event_id, digest, user_id, origin, receipt_id,
                 json.dumps(event, sort_keys=True), lease_token))
            return receipt_id

    def background_rows(self, scope, user_id):
        with self._db() as db:
            return [dict(row) for row in db.execute('''SELECT r.*,i.session_id,i.title,i.body,i.created_at
                FROM background_receipts r JOIN inbox i ON i.id=r.inbox_id
                WHERE r.scope=? AND r.user_id=? ORDER BY i.created_at,i.id''', (scope, user_id))]

    def background_acknowledged(self, scope, event_id, token):
        with self._db() as db:
            db.execute('UPDATE background_receipts SET acknowledged=1 WHERE scope=? AND event_id=? AND lease_token=?',
                       (scope, event_id, token))

    def ingest_approval(self, user_id, run_id, request_id, approval_id, session_id, expires_at):
        """Enqueue only an authoritative, new pending action; never decides it."""
        if (not all(isinstance(x, str) and x for x in (user_id, run_id, request_id, approval_id))
                or not isinstance(expires_at, (int, float)) or not math.isfinite(expires_at)
                or expires_at <= self.clock()):
            return None
        validator = getattr(self, 'approval_validator', None)
        if not validator or not validator(user_id, run_id, request_id, approval_id):
            return None
        delivery_id = 'approval:' + json.dumps([run_id, request_id], separators=(',', ':'))
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            inserted = db.execute('''INSERT OR IGNORE INTO inbox
                (id,user_id,delivery_id,title,body,session_id,created_at)
                VALUES(?,?,?,?,?,?,?)''',
                (uuid.uuid4().hex, user_id, delivery_id, 'Approval required',
                 'Open Hermes to review the pending action.', session_id, self.clock()))
            row = db.execute('SELECT * FROM inbox WHERE user_id=? AND delivery_id=?',
                             (user_id, delivery_id)).fetchone()
            if inserted.rowcount:
                db.execute('INSERT INTO approval_notifications VALUES(?,?,?,?,?)',
                           (row['id'], approval_id, run_id, request_id, expires_at))
                db.execute('INSERT INTO notification_policy VALUES(?,?,NULL,NULL)', (row['id'], 'approval'))
                self._enqueue(db, row, expires_at)
            metadata = dict(db.execute('SELECT approval_id,run_id,request_id,expires_at FROM approval_notifications WHERE inbox_id=?', (row['id'],)).fetchone())
            return {**self._item(row), **metadata}

    @staticmethod
    def _validate_subscription(subscription):
        if not isinstance(subscription, dict):
            raise ValueError('Invalid subscription')
        expiration = subscription.get('expirationTime')
        if expiration is not None and (type(expiration) not in (int, float) or not math.isfinite(expiration)):
            raise ValueError('Invalid subscription expiry')
        endpoint = subscription.get('endpoint', '')
        try:
            url = urlsplit(endpoint)
            allowed = {'fcm.googleapis.com', 'updates.push.services.mozilla.com',
                       'push.services.mozilla.com', 'web.push.apple.com'}
            if (not isinstance(endpoint, str) or len(endpoint) > 4096 or
                    any(ord(c) < 33 or ord(c) > 126 for c in endpoint) or
                    url.scheme != 'https' or url.hostname not in allowed or
                    url.username is not None or url.password is not None or
                    url.port not in (None, 443) or url.fragment or not url.path or
                    '\\' in endpoint):
                raise ValueError('Untrusted push endpoint')
            keys = subscription['keys']
            def decode(value):
                if not isinstance(value, str) or len(value) > 200:
                    raise ValueError('Invalid push key')
                return base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
            public, auth = decode(keys['p256dh']), decode(keys['auth'])
            from cryptography.hazmat.primitives.asymmetric import ec
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public)
            if len(auth) != 16:
                raise ValueError('Invalid push auth')
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise ValueError('Invalid push subscription') from exc
        return {'endpoint': endpoint, 'keys': {'p256dh': keys['p256dh'], 'auth': keys['auth']},
                'expirationTime': subscription.get('expirationTime')}

    def subscribe(self, user_id, device_id, subscription):
        if not user_id or not device_id:
            raise ValueError('User and device required')
        valid = self._validate_subscription(subscription)
        if valid['expirationTime'] is not None and valid['expirationTime'] <= self.clock() * 1000:
            raise ValueError('Subscription expired')
        if self.session_validator and not self.session_validator(user_id, device_id):
            raise PermissionError('Inactive or foreign device')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT * FROM subscriptions WHERE endpoint=?',
                                  (valid['endpoint'],)).fetchone()
            if existing and (existing['user_id'] != user_id or existing['device_id'] != device_id):
                raise PermissionError('Subscription belongs to another device')
            db.execute('INSERT INTO subscriptions VALUES(?,?,?,?) ON CONFLICT(endpoint) DO UPDATE SET subscription=excluded.subscription',
                       (valid['endpoint'], user_id, device_id, json.dumps(valid)))
        return {'endpoint': valid['endpoint']}

    def unsubscribe(self, user_id, device_id, endpoint):
        with self._db() as db:
            return bool(db.execute('DELETE FROM subscriptions WHERE user_id=? AND device_id=? AND endpoint=?',
                                   (user_id, device_id, endpoint)).rowcount)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _item(row):
        item = dict(row)
        item.pop('user_id', None)
        item.pop('delivery_id', None)
        item['read'] = bool(item['read'])
        return item

    def ingest(self, user_id, delivery_id, title, body, session_id=None,
               silent=False, historical=False, *, category='internal', profile=None, device_id=None):
        if category not in ALL_CATEGORIES:
            raise ValueError('Unknown notification category')
        if category == 'test':
            self._require_device(user_id, device_id)
        with self._db() as db:
            inserted = db.execute('''INSERT OR IGNORE INTO inbox
                (id,user_id,delivery_id,title,body,session_id,created_at)
                VALUES(?,?,?,?,?,?,?)''',
                (uuid.uuid4().hex, user_id, delivery_id, title, body, session_id, self.clock()))
            row = db.execute('SELECT * FROM inbox WHERE user_id=? AND delivery_id=?',
                             (user_id, delivery_id)).fetchone()
            # The release observer deliberately bypasses schema initialization.
            # Silent internal receipts need no push metadata, even on a legacy DB.
            if inserted.rowcount and not (silent and category == 'internal'):
                db.execute('INSERT INTO notification_policy VALUES(?,?,?,?)',
                           (row['id'], category, profile, device_id))
                if not silent and not historical and category in (*CATEGORIES, 'test'):
                    self._enqueue(db, row, self.clock() + 86400)
            return self._item(row)

    def _enqueue(self, db, item, expires_at):
        policy = db.execute('SELECT * FROM notification_policy WHERE inbox_id=?', (item['id'],)).fetchone()
        for sub in db.execute('SELECT * FROM subscriptions WHERE user_id=?', (item['user_id'],)).fetchall():
            if policy and policy['device_id'] is not None and policy['device_id'] != sub['device_id']:
                continue
            prefs = self._preferences(db, item['user_id'], sub['device_id'])
            if not policy or not self._category_enabled(prefs, policy['category']):
                continue
            db.execute("INSERT INTO outbox(id,inbox_id,endpoint,next_attempt_at,expires_at,status) VALUES(?,?,?,?,?,'policy_pending')",
                       (uuid.uuid4().hex, item['id'], sub['endpoint'], self.clock(), expires_at))

    def _send_web_push(self, subscription, payload):
        import requests
        from pywebpush import webpush

        class NoRedirectSession(requests.Session):
            def request(self, method, url, **kwargs):
                kwargs['allow_redirects'] = False
                return super().request(method, url, **kwargs)

        with NoRedirectSession() as session:
            session.trust_env = False
            return webpush(subscription_info=self._validate_subscription(subscription),
                           data=json.dumps(payload), vapid_private_key=self.vapid_private_key,
                           vapid_claims={'sub': self.vapid_subject}, timeout=10, ttl=300,
                           requests_session=session)

    def _policy_reason(self, db, row):
        notice = db.execute('''SELECT i.*,p.category,p.profile,p.device_id target_device FROM inbox i
            LEFT JOIN notification_policy p ON p.inbox_id=i.id WHERE i.id=?''', (row['inbox_id'],)).fetchone()
        if not notice or notice['read']:
            return 'inbox_read'
        if db.execute('SELECT 1 FROM dismissed_inbox WHERE inbox_id=?', (row['inbox_id'],)).fetchone():
            return 'inbox_dismissed'
        prefs = self._preferences(db, row['user_id'], row['device_id'])
        if not self._category_enabled(prefs, notice['category']):
            return 'push_disabled'
        if notice['target_device'] is not None and notice['target_device'] != row['device_id']:
            return 'wrong_device'
        if self.event_validator and not self.event_validator(row['user_id'], notice['profile'], notice['session_id'], notice['category']):
            return 'event_ineligible'
        if notice['category'] in ('completion', 'attention') and self.request_validator:
            if not self.request_validator(row['user_id'], notice['delivery_id'], notice['session_id'], notice['category']):
                return 'request_ineligible'
        if self._present(db, row['user_id'], notice['session_id'], notice['category']):
            return 'conversation_visible'
        return None

    def flush(self, limit=100):
        result = {'status': 'ok', 'sent': 0, 'failed': 0, 'pruned': 0, 'expired': 0}
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            result['expired'] = db.execute('DELETE FROM outbox WHERE expires_at<=?', (self.clock(),)).rowcount
            db.execute("UPDATE outbox SET status='unknown',last_error='worker_interrupted' WHERE status IN ('sending','policy_sending') AND next_attempt_at<=?", (self.clock(),))
            if self.session_validator:
                for sub in db.execute('SELECT * FROM subscriptions').fetchall():
                    expiration = json.loads(sub['subscription']).get('expirationTime')
                    if ((expiration is not None and expiration <= self.clock() * 1000) or
                            not self.session_validator(sub['user_id'], sub['device_id'])):
                        result['pruned'] += db.execute('DELETE FROM subscriptions WHERE endpoint=?', (sub['endpoint'],)).rowcount
        if not self.vapid_private_key or not self.vapid_public_key or not self.session_validator:
            return {**result, 'status': 'unavailable', 'reason': 'Push or session validation not configured'}
        with self._db() as db:
            rows = db.execute("""SELECT o.*, s.user_id,s.device_id,s.subscription
                FROM outbox o JOIN subscriptions s ON s.endpoint=o.endpoint
                WHERE o.status IN ('policy_pending','policy_retry') AND o.next_attempt_at<=?
                AND NOT EXISTS (SELECT 1 FROM dismissed_inbox d WHERE d.inbox_id=o.inbox_id) LIMIT ?""",
                (self.clock(), min(max(limit, 1), 100))).fetchall()
            db.commit()
            for row in rows:
                # Device validation is an external callback; do not hold the
                # notification writer while invoking it. Re-read local policy
                # after it returns so a read/dismiss racing validation wins.
                if not self.session_validator(row['user_id'], row['device_id']):
                    db.execute('DELETE FROM subscriptions WHERE endpoint=?', (row['endpoint'],))
                    result['pruned'] += 1
                    db.commit()
                    continue
                # Serialize the local policy snapshot and claim with settings,
                # reads and presence writes. Never hold a DB lock over transport.
                db.execute('BEGIN IMMEDIATE')
                if row['expires_at'] <= self.clock():
                    result['expired'] += db.execute('DELETE FROM outbox WHERE id=? AND expires_at<=?',
                                                    (row['id'], self.clock())).rowcount
                    db.commit()
                    continue
                notice = db.execute('''SELECT i.*,p.category,p.profile FROM inbox i
                    LEFT JOIN notification_policy p ON p.inbox_id=i.id WHERE i.id=?''', (row['inbox_id'],)).fetchone()
                reason = self._policy_reason(db, row)
                if reason:
                    db.execute("UPDATE outbox SET status='suppressed',last_error=? WHERE id=? AND status IN ('policy_pending','policy_retry')", (reason, row['id']))
                    db.commit()
                    continue
                item = db.execute('SELECT * FROM approval_notifications WHERE inbox_id=?', (row['inbox_id'],)).fetchone()
                if item:
                    validator = getattr(self, 'approval_validator', None)
                    valid = validator(row['user_id'], item['run_id'], item['request_id'], item['approval_id']) if validator else False
                    if valid is None and item['expires_at'] > self.clock():
                        db.commit()
                        continue  # Uncertain restart: retain pending outbox, never push.
                    if item['expires_at'] <= self.clock() or not valid:
                        db.execute("UPDATE outbox SET status='suppressed',last_error='approval_not_pending' WHERE id=? AND status IN ('policy_pending','policy_retry')", (row['id'],))
                        db.commit()
                        continue
                # Router-backed services fence revocation after the notification
                # writer wait, holding auth validity through the claim commit.
                # No external validators (which may reopen auth) inside the guard.
                guard = self.device_claim_guard
                with guard(row['user_id'], row['device_id']) if guard else nullcontext(True) as active:
                    if not active:
                        result['pruned'] += db.execute('DELETE FROM subscriptions WHERE endpoint=?', (row['endpoint'],)).rowcount
                        db.commit()
                        continue
                    claimed = db.execute('''UPDATE outbox SET status='policy_sending',attempts=attempts+1,next_attempt_at=?
                        WHERE id=? AND status IN ('policy_pending','policy_retry') AND next_attempt_at<=?
                        AND NOT EXISTS (SELECT 1 FROM dismissed_inbox d WHERE d.inbox_id=outbox.inbox_id)''',
                        (self.clock() + 60, row['id'], self.clock())).rowcount
                    if not claimed:
                        db.commit()
                        continue
                    prefs = self._preferences(db, row['user_id'], row['device_id'])
                    preview = GENERIC_PREVIEW if prefs['hide_details'] else safe_preview(notice['title'], notice['body'])
                    payload = {**preview,
                               'url': '/hermes/?inbox=' + row['inbox_id'], 'tag': row['inbox_id'],
                               'inbox_id': row['inbox_id']}
                    db.commit()
                # After this committed claim, revocation cannot recall a payload.
                # Both writers must be released before touching provider transport.
                subscription = self._validate_subscription(json.loads(row['subscription']))
                error = 'provider_error'
                try:
                    if self.send_push:
                        response = self.send_push(subscription_info=subscription, data=json.dumps(payload))
                    else:
                        response = self._send_web_push(subscription, payload)
                except Exception as exc:
                    response = getattr(exc, 'response', None)
                    error = 'transport_error'
                if response is not None and response.status_code in (404, 410):
                    db.execute('DELETE FROM subscriptions WHERE endpoint=?', (row['endpoint'],))
                    result['pruned'] += 1
                    db.commit()
                    continue
                if response is None or not 200 <= response.status_code < 300:
                    delay = min(60 * (2 ** min(row['attempts'], 6)), 3600)
                    db.execute("UPDATE outbox SET status='policy_retry',next_attempt_at=?,last_error=? WHERE id=?",
                               (self.clock() + delay, error, row['id']))
                    reason = self._policy_reason(db, row)
                    if reason:
                        db.execute("UPDATE outbox SET status='suppressed',last_error=? WHERE id=?", (reason, row['id']))
                    self._suppress_hidden_pushes(db, row['user_id'])
                    db.commit()
                    result['failed'] += 1
                    continue
                db.execute("UPDATE outbox SET status='sent',last_error=NULL WHERE id=?", (row['id'],))
                db.commit()
                result['sent'] += 1
        return result

    def mark_all_read(self, user_id, *, _authorize=None):
        """Read owned attention rows across all pages; leave receipts/actions intact."""
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            # Match preference writes' lock order and fence revoked bearer
            # generations after waiting for the notification writer.
            with _authorize() if _authorize else nullcontext():
                visible = _attention_visible_sql(db)
                updated = db.execute(f'''UPDATE inbox SET read=1
                    WHERE user_id=? AND read=0 AND {visible}''', (user_id,)).rowcount
                db.execute(f'''UPDATE outbox SET status='suppressed',last_error='inbox_read'
                    WHERE status IN ('policy_pending','policy_retry') AND inbox_id IN
                    (SELECT id FROM inbox WHERE user_id=? AND read=1 AND {visible})''', (user_id,))
                db.commit()
                return updated

    def mark_read(self, user_id, item_id):
        with self._db() as db:
            updated = bool(db.execute('''UPDATE inbox SET read=1 WHERE id=? AND user_id=? AND NOT EXISTS
                                   (SELECT 1 FROM dismissed_inbox d WHERE d.inbox_id=inbox.id AND d.user_id=inbox.user_id)''',
                                   (item_id, user_id)).rowcount)
            if updated:
                db.execute("UPDATE outbox SET status='suppressed',last_error='inbox_read' WHERE inbox_id=? AND status IN ('policy_pending','policy_retry')", (item_id,))
            return updated

    def dismiss(self, user_id, item_id):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT read FROM inbox WHERE id=? AND user_id=?',
                             (item_id, user_id)).fetchone()
            if row is None:
                raise HTTPException(404, 'Inbox item not found')
            if not row['read']:
                raise HTTPException(409, 'Only read notifications can be dismissed')
            dismissed = db.execute('INSERT OR IGNORE INTO dismissed_inbox VALUES(?,?,?)',
                                   (item_id, user_id, self.clock())).rowcount
            self._suppress_hidden_pushes(db, user_id)
            return dismissed

    @staticmethod
    def _suppress_hidden_pushes(db, user_id):
        # Claimed/sent pushes cannot be recalled. Only unclaimed work is suppressed.
        db.execute('''UPDATE outbox SET status='suppressed',last_error='inbox_dismissed'
            WHERE status IN ('policy_pending','policy_retry') AND inbox_id IN
            (SELECT inbox_id FROM dismissed_inbox WHERE user_id=?)''', (user_id,))

    def clear_read(self, user_id):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            dismissed = db.execute(f'''INSERT OR IGNORE INTO dismissed_inbox(inbox_id,user_id,dismissed_at)
                SELECT id,user_id,? FROM inbox WHERE user_id=? AND read=1 AND {_attention_visible_sql(db)}''',
                (self.clock(), user_id)).rowcount
            self._suppress_hidden_pushes(db, user_id)
            return dismissed

    def read_count(self, user_id):
        with self._db() as db:
            return db.execute(f'''SELECT count(*) FROM inbox WHERE user_id=? AND read=1
                AND {_attention_visible_sql(db)}''',
                (user_id,)).fetchone()[0]

    def list_inbox(self, user_id, limit=100, offset=0):
        with self._db() as db:
            items = [self._item(row) for row in db.execute(
                f'''SELECT * FROM inbox WHERE user_id=? AND {_attention_visible_sql(db)}
                ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?''',
                (user_id, min(max(limit, 1), 200), max(offset, 0)))]
            for item in items:
                metadata = db.execute('SELECT approval_id,run_id,request_id,expires_at FROM approval_notifications WHERE inbox_id=?', (item['id'],)).fetchone()
                if metadata:
                    item.update(dict(metadata))
            return items


def build_notifications_router(service, auth_service):
    if service.session_validator is None:
        service.session_validator = auth_service.is_session_active
    if service.device_claim_guard is None:
        @contextmanager
        def device_claim_guard(user_id, device_id):
            # The notification writer is already held. Use the auth store's
            # transaction directly, never a nested is_session_active callback.
            with auth_service.store.transaction() as db:
                row = db.execute('SELECT token_hash FROM sessions WHERE id=? AND user_id=?',
                                 (device_id, user_id)).fetchone()
                active = row is not None
                if active:
                    try:
                        # Push is device-bound, not bound to an old HTTP bearer.
                        # Reuse full-session validity without extending activity.
                        auth_service.assert_current_session(db, {
                            'id': user_id, 'session_id': device_id, 'token_hash': row['token_hash']})
                    except HTTPException as exc:
                        if exc.status_code != 401:
                            raise
                        active = False
                yield active
        service.device_claim_guard = device_claim_guard
    router = APIRouter()

    @router.get('/inbox')
    def inbox(user=Depends(auth_service.require_user), limit: int = Query(100, ge=1, le=200),
              offset: int = Query(0, ge=0)):
        return {'items': service.list_inbox(user['id'], limit, offset),
                'read_count': service.read_count(user['id'])}

    @router.post('/inbox/read-all')
    def mark_all_read(request: Request, user=Depends(auth_service.require_user)):
        auth_service.require_mutation(request, user)
        return {'ok': True, 'updated': service.mark_all_read(user['id'],
                _authorize=lambda: auth_service.authorized_transaction(user))}

    @router.post('/inbox/clear-read')
    def clear_read(request: Request, user=Depends(auth_service.require_user)):
        auth_service.require_mutation(request, user)
        return {'ok': True, 'dismissed': service.clear_read(user['id'])}

    @router.post('/inbox/{item_id}/read')
    def mark_read(item_id: str, request: Request, user=Depends(auth_service.require_user)):
        auth_service.require_mutation(request, user)
        if not service.mark_read(user['id'], item_id):
            raise HTTPException(404, 'Inbox item not found')
        return {'ok': True}

    @router.post('/inbox/{item_id}/dismiss')
    def dismiss(item_id: str, request: Request, user=Depends(auth_service.require_user)):
        auth_service.require_mutation(request, user)
        return {'ok': True, 'dismissed': service.dismiss(user['id'], item_id)}

    @router.get('/push/preferences')
    def get_preferences(user=Depends(auth_service.require_user)):
        try:
            return service.get_preferences(user['id'], user['session_id'])
        except PermissionError as exc:
            raise HTTPException(401, 'Inactive device') from exc

    @router.post('/push/presence')
    def record_presence(request: Request, body=Body(...), user=Depends(auth_service.require_user)):
        # A heartbeat is authorization, not user activity. Never extend inactivity.
        auth_service.require_origin(request)
        if not hmac.compare_digest(request.headers.get('x-csrf-token', '').encode(), user['csrf_token'].encode()):
            raise HTTPException(403, 'CSRF rejected')
        # Release the auth writer before entering notification storage/validators.
        # Both stores' callbacks may open their own auth transaction.
        def authorize():
            with auth_service.authorized_transaction(user):
                pass
        authorize()
        try:
            return service.record_presence(user['id'], user['session_id'], body, _authorize=authorize)
        except ValueError as exc:
            raise HTTPException(400, 'Invalid presence') from exc
        except PermissionError as exc:
            raise HTTPException(403, 'Presence unavailable') from exc

    @router.put('/push/preferences')
    def set_preferences(request: Request, body=Body(...), user=Depends(auth_service.require_user)):
        auth_service.require_mutation(request, user)
        try:
            return service.set_preferences(user['id'], user['session_id'], body,
                                           _authorize=lambda: auth_service.authorized_transaction(user))
        except ValueError as exc:
            raise HTTPException(400, 'Invalid push preferences') from exc
        except PermissionError as exc:
            raise HTTPException(401, 'Inactive device') from exc

    @router.get('/push/key')
    def push_key(user=Depends(auth_service.require_user)):
        if not service.vapid_private_key or not service.vapid_public_key:
            raise HTTPException(503, 'Push not configured')
        return {'public_key': service.vapid_public_key}

    @router.post('/push/subscriptions', status_code=201)
    def subscribe(request: Request, subscription: dict = Body(...),
                  user=Depends(auth_service.require_user)):
        auth_service.require_mutation(request, user)
        try:
            return service.subscribe(user['id'], user['session_id'], subscription)
        except ValueError as exc:
            raise HTTPException(400, 'Invalid push subscription') from exc
        except PermissionError as exc:
            raise HTTPException(409, 'Subscription unavailable') from exc

    @router.delete('/push/subscriptions')
    def unsubscribe(request: Request, body: dict = Body(...), user=Depends(auth_service.require_user)):
        auth_service.require_mutation(request, user)
        endpoint = body.get('endpoint')
        if not isinstance(endpoint, str) or len(endpoint) > 4096:
            raise HTTPException(400, 'Invalid endpoint')
        return {'removed': service.unsubscribe(user['id'], user['session_id'], endpoint)}

    @router.post('/push/test', status_code=202)
    def push_test(request: Request, user=Depends(auth_service.require_user)):
        auth_service.require_mutation(request, user)
        if not service.vapid_private_key or not service.vapid_public_key or not service.session_validator:
            raise HTTPException(503, 'Push or session validation not configured')
        with service._db() as db:
            subscribed = db.execute('SELECT 1 FROM subscriptions WHERE user_id=? AND device_id=?',
                                    (user['id'], user['session_id'])).fetchone()
        if not subscribed:
            raise HTTPException(409, 'Subscribe this device first')
        if not service.get_preferences(user['id'], user['session_id'])['enabled']:
            raise HTTPException(409, 'Push notifications are disabled on this device')
        item = service.ingest(user['id'], 'push-test:' + uuid.uuid4().hex,
                              'Test notification', 'Web Push test requested.',
                              category='test', device_id=user['session_id'])
        return {'status': 'queued', 'inbox_id': item['id']}

    return router
