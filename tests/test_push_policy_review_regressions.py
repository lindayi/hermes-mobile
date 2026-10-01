"""Review blockers: synthetic providers, isolated real auth, deterministic barriers."""
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace

import pytest

from backend.notification_policy import GENERIC_PREVIEW, safe_preview
from test_auth import BASE
from test_notifications import subscription
from test_push_policy import policy
from test_push_policy_routes import api


@pytest.mark.parametrize('field', ['title', 'body'])
def test_all_uri_schemes_fail_closed_before_clipping(policy, field):
    uris = (
        'ftp://example.test/private', 'mailto:private@example.test',
        'file:///private/report', 'data:text/plain,private',
        'javascript:alert(1)', 'tel:+15551234567', 'urn:example:private',
        'custom+scheme.v1-x:private', 'ＦＴＰ：//example.test/private',
    )
    for index, uri in enumerate(uris):
        for prefix in ('', 'Public result. ' * 30):
            text = {'title': 'Public result', 'body': 'Review your result.'}
            text[field] = prefix + uri
            item = policy.service.ingest('alice', f'{index}:{len(prefix)}', **text, category='scheduled')
            assert policy.service.flush()['sent'] == 2
            for sent in policy.sent[-2:]:
                payload = json.loads(sent['data'])
                assert {key: payload[key] for key in ('title', 'body')} == GENERIC_PREVIEW, uri
                assert payload['inbox_id'] == item['id']
            assert next(row for row in policy.service.list_inbox('alice') if row['id'] == item['id'])[field] == text[field]


def test_plain_email_and_personal_public_prose_remain_informative():
    assert safe_preview('Message from Alice', 'Contact alice@example.test about dinner.') == {
        'title': 'Message from Alice', 'body': 'Contact alice@example.test about dinner.'}


def test_preferences_reject_rotated_bearer_after_notification_writer_wait(api):
    s, auth, user, client = api.service, api.auth, api.user, api.client
    original = client.get(BASE + '/push/preferences').json()
    original = client.put(BASE + '/push/preferences', json={**original, 'hide_details': True}).json()
    reached = Event()
    mutation = auth.require_mutation

    def observed(*args, **kwargs):
        mutation(*args, **kwargs)
        reached.set()

    auth.require_mutation = observed
    with ThreadPoolExecutor(max_workers=1) as pool:
        with s._db() as db:
            db.execute('BEGIN IMMEDIATE')
            posted = pool.submit(client.put, BASE + '/push/preferences', json={**original, 'hide_details': False})
            assert reached.wait(3)
            with auth.store.transaction() as adb:
                adb.execute('UPDATE sessions SET token_hash=? WHERE id=?', ('rotated', user['session_id']))
        response = posted.result(timeout=3)
    assert response.status_code == 401, (response.status_code, response.json())
    with s._db() as db:
        assert json.loads(db.execute('SELECT payload FROM push_preferences').fetchone()[0]) == original


def test_preference_bearer_guard_lasts_through_notification_commit(api):
    s, auth, client = api.service, api.auth, api.client
    original = client.get(BASE + '/push/preferences').json()
    authorized = auth.authorized_transaction
    calls = []

    @contextmanager
    def observed(user, kind='full'):
        with authorized(user, kind) as db:
            calls.append(kind)
            yield db
            if len(calls) == 2:
                # A separate reader sees the new revision before auth unlocks.
                with s._db() as ndb:
                    saved = json.loads(ndb.execute('SELECT payload FROM push_preferences').fetchone()[0])
                assert saved == {**original, 'hide_details': True, 'revision': 1}

    auth.authorized_transaction = observed
    response = client.put(BASE + '/push/preferences', json={**original, 'hide_details': True})
    assert response.status_code == 200
    assert calls == ['full', 'full'], 'Mutation authentication and final bearer guard are distinct'


def test_device_revoked_while_waiting_notification_writer_never_claims(api):
    s, auth, user = api.service, api.auth, api.user
    s.subscribe(user['id'], user['session_id'], subscription())
    s.ingest(user['id'], 'scheduled', 'Public update', 'Ready.', category='scheduled')
    observed, release = Event(), Event()
    validator = s.session_validator
    calls = []

    def validate(uid, device):
        valid = validator(uid, device)
        calls.append(valid)
        if len(calls) == 2:
            observed.set()
            assert release.wait(3)
        return valid

    s.session_validator = validate
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(s.flush)
        try:
            assert observed.wait(3)
            with s._db() as db:
                db.execute('BEGIN IMMEDIATE')
                assert db.execute('SELECT status FROM outbox').fetchone()[0] == 'policy_pending'
                release.set()
                with auth.store.transaction() as adb:
                    adb.execute('UPDATE sessions SET revoked=1 WHERE id=?', (user['session_id'],))
                assert db.execute('SELECT status,attempts FROM outbox').fetchone()[:] == ('policy_pending', 0)
        finally:
            release.set()
        result = future.result(timeout=3)
    assert result['sent'] == 0 and not api.sent, result
    assert result['pruned'] == 1
    with s._db() as db:
        assert db.execute('SELECT count(*) FROM outbox').fetchone()[0] == 0


def test_claim_guard_holds_auth_until_commit_but_not_provider_transport(api):
    import sqlite3

    s, auth, user = api.service, api.auth, api.user
    s.subscribe(user['id'], user['session_id'], subscription())
    s.ingest(user['id'], 'guard', 'Public update', 'Ready.', category='scheduled')
    before_activity = user['last_active_at']
    api.now[0] += 10
    original = getattr(s, 'device_claim_guard', None)
    assert original is not None, 'Actual router must inject a transactional claim guard'
    committed = []

    @contextmanager
    def observed(uid, device):
        with original(uid, device) as active:
            assert active
            yield active
            # Auth remains locked after the notification claim is visible.
            with s._db() as db:
                assert db.execute('SELECT status,attempts FROM outbox').fetchone()[:] == ('policy_sending', 1)
            db = sqlite3.connect(auth.store.path, timeout=0)
            try:
                with pytest.raises(sqlite3.OperationalError, match='locked'):
                    db.execute('BEGIN IMMEDIATE')
            finally:
                db.close()
            committed.append(True)

    s.device_claim_guard = observed

    def send(**kwargs):
        assert committed == [True]
        # A provider callback can acquire BOTH real writers immediately.
        # Revocation here is after the claim and cannot recall this disclosure.
        adb = sqlite3.connect(auth.store.path, timeout=0)
        ndb = sqlite3.connect(s.db_path, timeout=0)
        try:
            adb.execute('BEGIN IMMEDIATE')
            ndb.execute('BEGIN IMMEDIATE')
            assert ndb.execute('SELECT status FROM outbox').fetchone()[0] == 'policy_sending'
            adb.execute('UPDATE sessions SET revoked=1 WHERE id=?', (user['session_id'],))
            adb.commit()
            ndb.commit()
        finally:
            adb.close()
            ndb.close()
        api.sent.append(kwargs)
        return SimpleNamespace(status_code=201)

    s.send_push = send
    assert s.flush()['sent'] == 1
    assert len(api.sent) == 1
    with auth.store.transaction() as db:
        assert db.execute('SELECT revoked,last_active_at FROM sessions WHERE id=?', (user['session_id'],)).fetchone()[:] == (1, before_activity)
    assert s.flush()['sent'] == 0
    assert len(api.sent) == 1
