"""Assembled-server security regressions; only ephemeral app state is written."""
import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import CSP, Settings, create_app
from test_auth import BASE, BOOTSTRAP, ORIGIN, enroll

LIMIT = 1048576


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(state_dir=tmp_path/'state', profiles={'default':tmp_path/'native'},
                               bootstrap_secret=BOOTSTRAP))


def assert_security_headers(headers):
    assert headers['content-security-policy'] == CSP
    assert headers['x-content-type-options'] == 'nosniff'
    assert headers['x-frame-options'] == 'DENY'
    assert headers['referrer-policy'] == 'no-referrer'
    assert headers['cache-control'] == 'no-store'


def raw_request(app, chunks, *, headers=(), path=BASE+'/runs', method='POST', origin=ORIGIN):
    """Keep distinct ASGI frames (TestClient joins iterator uploads into one frame)."""
    consumed, sent = [], []

    async def run():
        scope = {'type':'http', 'asgi':{'version':'3.0'}, 'http_version':'1.1',
                 'method':method, 'scheme':'https', 'path':path, 'raw_path':path.encode(),
                 'query_string':b'', 'root_path':'', 'server':('lindayi.me',443),
                 'client':('127.0.0.1',1234),
                 'headers':[(b'host',b'lindayi.me'),(b'origin',origin.encode()),
                            (b'content-type',b'application/json'), *headers]}
        async def receive():
            index = len(consumed)
            if index < len(chunks):
                consumed.append(index)
                return {'type':'http.request','body':chunks[index],
                        'more_body':index < len(chunks)-1}
            # A real connected client waits here until the response has completed.
            await asyncio.Event().wait()
        async def send(message):
            sent.append(message)
        await asyncio.wait_for(app(scope, receive, send), timeout=5)
    asyncio.run(run())
    start = next(message for message in sent if message['type']=='http.response.start')
    body = b''.join(message.get('body',b'') for message in sent if message['type']=='http.response.body')
    return start['status'], {k.decode():v.decode() for k,v in start['headers']}, body, consumed


@pytest.mark.parametrize('headers', [(), ((b'transfer-encoding',b'chunked'),), ((b'content-length',b'2'),)])
@pytest.mark.parametrize('prefix', [b'{"input":"', b'invalid-json'])
def test_streamed_body_is_rejected_before_json_parse_or_auth(app, headers, prefix):
    status, response_headers, body, consumed = raw_request(
        app, [prefix, b'x'*(LIMIT//2), b'x'*(LIMIT//2), b'never-consume'], headers=headers)
    assert status == 413
    assert json.loads(body) == {'detail':'Request too large'}
    assert consumed == [0,1,2], 'Reject immediately at cumulative overflow; do not drain attacker data'
    assert_security_headers(response_headers)


def test_body_limit_includes_routes_that_authenticate_without_reading_body(app):
    status, headers, body, consumed = raw_request(app, [b'x'*LIMIT,b'x',b'never-consume'],
                                                path=BASE+'/runs/no-run/stop')
    assert status == 413
    assert json.loads(body)['detail'] == 'Request too large'
    assert consumed == [0,1]
    assert_security_headers(headers)


@pytest.mark.parametrize('size', [LIMIT-1, LIMIT])
def test_at_or_below_body_limit_reaches_auth(app, size):
    payload = b'{"padding":"' + b'x'*(size-14) + b'"}'
    assert len(payload) == size
    status, headers, _, consumed = raw_request(app, [payload[:100],payload[100:]])
    assert status == 401
    assert consumed == [0,1]
    assert_security_headers(headers)


@pytest.mark.parametrize('length', [str(LIMIT+1), '9'*5000])
def test_declared_oversize_rejected_without_receiving_body(app, length):
    status, headers, body, consumed = raw_request(app, [b'never-consume'],
                                                 headers=((b'content-length',length.encode()),))
    assert status == 413
    assert json.loads(body)['detail'] == 'Request too large'
    assert consumed == []
    assert_security_headers(headers)


def test_origin_rejection_precedes_receiving_body(app):
    status, headers, body, consumed = raw_request(app, [b'never-consume'], origin='https://evil.example')
    assert status == 403
    assert json.loads(body)['detail'] == 'Origin rejected'
    assert consumed == []
    assert_security_headers(headers)


def test_auth_keeps_narrower_body_limit(app):
    status, headers, body, _ = raw_request(app, [b'x'*32768,b'x'*32769], path=BASE+'/auth/login/options')
    assert status == 413
    assert json.loads(body)['detail'] == 'Authentication payload too large'
    assert_security_headers(headers)


def test_static_and_streamed_valid_auth_body_still_work(app):
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get('/hermes/')
        assert response.status_code == 200
        assert_security_headers(response.headers)
    status, headers, body, _ = raw_request(app, [b'{',b'}'], path=BASE+'/auth/login/options')
    assert status == 200
    assert 'options' in json.loads(body)
    assert_security_headers(headers)


def test_disconnect_during_upload_does_not_route_partial_body(app):
    async def run():
        sent = []
        scope = {'type':'http','method':'POST','path':BASE+'/runs',
                 'headers':[(b'origin',ORIGIN.encode())]}
        async def receive():
            return {'type':'http.disconnect'}
        async def send(message):
            sent.append(message)
        await asyncio.wait_for(app(scope,receive,send), timeout=5)
        assert sent == []
    asyncio.run(run())


PUBLIC_ROOTS = [Path('/var/www'), Path(__file__).resolve().parents[1]/'frontend']


@pytest.mark.parametrize('root', PUBLIC_ROOTS)
@pytest.mark.parametrize('child', ['', 'private-state'])
@pytest.mark.parametrize('via_symlink', [False, True])
def test_direct_settings_reject_public_state_before_any_write(tmp_path, monkeypatch, root, via_symlink, child):
    if via_symlink:
        alias = tmp_path/'public-alias'
        alias.symlink_to(root, target_is_directory=True)
        root = alias
    def forbidden_write(*args, **kwargs):
        pytest.fail('Unsafe state path reached mkdir before rejection')
    monkeypatch.setattr(Path, 'mkdir', forbidden_write)
    with pytest.raises(ValueError, match='public'):
        create_app(Settings(state_dir=str(root/child)))


@pytest.mark.parametrize('root', PUBLIC_ROOTS)
@pytest.mark.parametrize('via_symlink', [False, True])
def test_config_in_public_root_rejected_before_reading(tmp_path, root, via_symlink):
    from backend.configuration import load_settings
    if via_symlink:
        alias = tmp_path/'public-alias'
        alias.symlink_to(root, target_is_directory=True)
        root = alias
    # Deliberately nonexistent: validation must run before stat/read or any state writes.
    with pytest.raises(ValueError, match='public'):
        load_settings(root/'security-regression-never-created.json')


@pytest.mark.parametrize('root', PUBLIC_ROOTS)
def test_private_config_cannot_select_public_state(tmp_path, root):
    from backend.configuration import load_settings
    config = tmp_path/'config.json'
    config.write_text(json.dumps({'state_dir':str(root/'private-state')}))
    config.chmod(0o600)
    with pytest.raises(ValueError, match='public'):
        load_settings(config)


@pytest.fixture
def completed_stream(app):
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        client.headers['Origin'] = ORIGIN
        _, result = enroll(client)
        uid = result.json()['user']['id']
        journal = app.state.journal
        run, _ = journal.submit(uid,'default','synthetic-session','test','test-key')
        rid = run['id']
        journal.event(uid,rid,'delta',{'text':'first'})
        journal.event(uid,rid,'delta',{'text':'second'})
        journal.finish(uid,rid,'completed',output='firstsecond')
        ids = [event['id'] for event in journal.events(uid,rid)]
        yield client, BASE+'/runs/'+rid+'/events', ids


def event_ids(response):
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('text/event-stream')
    assert response.headers['x-accel-buffering'] == 'no'
    assert_security_headers(response.headers)
    return [int(line[4:]) for line in response.text.splitlines() if line.startswith('id: ')]


def test_sse_reconnect_resumes_strictly_after_last_delivered_event(completed_stream):
    client, url, ids = completed_stream
    assert event_ids(client.get(url)) == ids
    assert event_ids(client.get(url,headers={'Last-Event-ID':str(ids[0])})) == ids[1:]
    assert event_ids(client.get(url,headers={'Last-Event-ID':str(ids[-1])})) == []


def test_sse_explicit_after_takes_precedence_even_when_zero(completed_stream):
    client, url, ids = completed_stream
    assert event_ids(client.get(url,params={'after':ids[1]},headers={'Last-Event-ID':str(ids[0])})) == ids[2:]
    assert event_ids(client.get(url,params={'after':0},headers={'Last-Event-ID':str(ids[-1])})) == ids


@pytest.mark.parametrize('cursor', ['', '-1', '+1', '1.5', 'nope', ' 1', '1 ', '1,2',
                                    '9223372036854775808', '9'*100])
def test_sse_invalid_last_event_id_rejected_before_streaming(completed_stream, cursor):
    client, url, _ = completed_stream

    response = client.get(url,headers={'Last-Event-ID':cursor})
    assert response.status_code == 422
    assert response.json() == {'detail':'Invalid Last-Event-ID'}
    assert_security_headers(response.headers)


@pytest.mark.parametrize('cursor', ['-1', 'nope', '9223372036854775808'])
def test_sse_invalid_after_rejected_with_security_headers(completed_stream, cursor):
    client, url, _ = completed_stream

    response = client.get(url,params={'after':cursor},headers={'Last-Event-ID':'0'})
    assert response.status_code == 422
    assert_security_headers(response.headers)


def test_sse_explicit_after_ignores_invalid_header(completed_stream):
    client, url, ids = completed_stream
    assert event_ids(client.get(url,params={'after':ids[0]},headers={'Last-Event-ID':'not-used'})) == ids[1:]


def test_sse_maximum_cursor_is_valid_and_does_not_overflow_sqlite(completed_stream):
    client, url, _ = completed_stream
    assert event_ids(client.get(url,headers={'Last-Event-ID':'9223372036854775807'})) == []
    assert event_ids(client.get(url,params={'after':'9223372036854775807'})) == []
