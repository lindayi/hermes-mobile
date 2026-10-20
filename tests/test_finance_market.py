"""Synthetic public-provider responses only; all network transports are replaced."""
import importlib
import importlib.util
import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.auth import AuthService, digest

BASE = '/hermes/app-api/finance/quotes'
NOW = 1781524800.0  # Fixed synthetic clock, never a current-price fixture.


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('Real network is forbidden in market tests')
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', blocked)


@pytest.fixture
def market():
    if importlib.util.find_spec('backend.finance_market'):
        return importlib.import_module('backend.finance_market')
    return None


@pytest.fixture
def auth(tmp_path):
    service = AuthService(tmp_path / 'synthetic-auth.sqlite3', clock=lambda: NOW)
    with service.store.transaction() as db:
        db.execute('INSERT INTO users VALUES (?,?,?,?,?,?)',
                   ('synthetic-owner', 'owner', 'default', 'ready', 'Synthetic owner', NOW))
        db.execute('INSERT INTO sessions(id,token_hash,user_id,csrf_token,created_at,last_active_at,last_verified_at) VALUES (?,?,?,?,?,?,?)',
                   ('synthetic-session', digest('test-cookie'), 'synthetic-owner', 'test-csrf', NOW, NOW, NOW))
    return service


def client_for(market, auth):
    assert market is not None, 'Market router is not implemented'
    app = FastAPI()
    app.include_router(market.build_market_router(auth))
    client = TestClient(app, base_url='https://test.invalid')
    client.cookies.set('hermes_session', 'test-cookie')
    return client


@pytest.mark.parametrize('denial', ['anonymous', 'member', 'profile', 'pending', 'disabled', 'recovery', 'revoked', 'expired', 'idle'])
def test_denied_before_any_provider_call(market, auth, denial):
    client = client_for(market, auth)
    with auth.store.transaction() as db:
        if denial == 'anonymous':
            client.cookies.clear()
        elif denial == 'member':
            db.execute("UPDATE users SET role='member'")
        elif denial == 'profile':
            db.execute("UPDATE users SET profile='another'")
        elif denial in ('pending', 'disabled'):
            db.execute('UPDATE users SET status=?', (denial,))
        elif denial == 'recovery':
            db.execute("UPDATE sessions SET kind='recovery'")
        elif denial == 'revoked':
            db.execute('UPDATE sessions SET revoked=1')
        elif denial == 'expired':
            db.execute('UPDATE sessions SET created_at=?', (NOW - 91 * 86400,))
        elif denial == 'idle':
            db.execute('UPDATE sessions SET last_active_at=?', (NOW - 31 * 86400,))
    response = client.get(BASE, params={'symbols': 'MSFT'})
    assert response.status_code in (401, 403)
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['vary'] == 'Cookie'
    assert 'quotes' not in response.json()


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace('+00:00', 'Z')


def yahoo(symbol='MSFT', **overrides):
    meta = {'symbol': symbol, 'regularMarketPrice': 123.45,
            'currency': 'USD' if symbol == 'MSFT' else 'CAD', 'regularMarketTime': NOW - 60}
    meta.update(overrides)
    return {'chart': {'result': [{'meta': meta}], 'error': None}}


def boc(value='1.3700', date=None):
    return {'observations': [{'d': date or iso(NOW)[:10], 'FXUSDCAD': {'v': value}}]}


def network(monkeypatch, market, handler=None):
    requests = []
    monkeypatch.setattr(market, '_now', lambda: NOW, raising=False)

    def respond(transport, request):
        requests.append(request)
        if handler:
            result = handler(request)
            if isinstance(result, httpx.Response):
                return result
        else:
            result = boc() if request.url.host == 'www.bankofcanada.ca' else yahoo(request.url.path.rsplit('/', 1)[-1])
        return httpx.Response(200, headers={'content-type': 'application/json'},
                              stream=httpx.ByteStream(json.dumps(result).encode()))

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', respond)
    return requests


def test_public_quotes_contract_and_source_dates(market, auth, monkeypatch):
    requests = network(monkeypatch, market)
    response = client_for(market, auth).get(BASE, params={'symbols': 'MSFT,TGED.TO,TPE.TO,TPU.TO,TTP.TO,TDB.TO'})
    assert response.status_code == 200
    body = response.json()
    assert len(body['quotes']) == 6
    for quote in body['quotes']:
        assert quote == {
            'symbol': quote['symbol'], 'price': 123.45,
            'currency': 'USD' if quote['symbol'] == 'MSFT' else 'CAD',
            'as_of': iso(NOW - 60), 'fetched_at': iso(NOW), 'source': 'yahoo',
            'status': 'ok', 'stale': False, 'error': None,
        }
    assert body['fx'] == {'pair': 'USDCAD', 'rate': 1.37, 'as_of': iso(NOW)[:10],
                          'source': 'bankofcanada', 'status': 'ok'}
    assert body['meta']['generated_at'] == iso(NOW)
    assert body['meta']['source'] == 'public-market'
    assert body['meta']['warnings'] == []
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['vary'] == 'Cookie'
    assert len(requests) == 7
    for request in requests:
        assert request.url.scheme == 'https'
        assert 'cookie' not in request.headers
        assert 'authorization' not in request.headers
        assert request.content == b''
        assert set(request.url.params) <= {'interval', 'range', 'recent'}
        if request.url.host == 'query1.finance.yahoo.com':
            assert dict(request.url.params) == {'interval': '1d', 'range': '5d'}
        else:
            assert str(request.url) == 'https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json?recent=1'


@pytest.mark.parametrize('params', [
    {'symbols': 'https://127.0.0.1/admin'}, {'symbols': 'MSFT,UNKNOWN'},
    {'symbols': 'MSFT,../../secret'}, {'symbols': 'msft'}, {'symbols': 'MSFT,'},
    {'symbols': ',MSFT'}, {'symbols': ' MSFT'}, {'symbols': 'MSFT\x00'},
    {'symbols': ','.join(['MSFT'] * 13)}, {'symbols': 'M' * 201},
    {'symbols': 'MSFT', 'url': 'https://example.com'},
    {'symbols': 'MSFT', 'account': 'private'}, {'symbols': 'MSFT', 'fx': 'EURCAD'},
    [('symbols', 'MSFT'), ('symbols', 'TGED.TO')],
])
def test_rejects_nonallowlisted_or_unbounded_input_before_network(market, auth, monkeypatch, params):
    requests = network(monkeypatch, market)
    response = client_for(market, auth).get(BASE, params=params)
    assert response.status_code == 400
    assert response.headers['cache-control'] == 'no-store'
    assert requests == []


def test_duplicate_symbols_are_deduplicated_and_empty_is_fx_only(market, auth, monkeypatch):
    requests = network(monkeypatch, market)
    client = client_for(market, auth)
    body = client.get(BASE, params={'symbols': 'MSFT,MSFT'}).json()
    assert [q['symbol'] for q in body['quotes']] == ['MSFT']
    body = client.get(BASE).json()
    assert body['quotes'] == []
    assert body['fx']['rate'] == 1.37
    assert len([r for r in requests if r.url.host == 'query1.finance.yahoo.com']) == 1


@pytest.mark.parametrize('overrides', [
    {'regularMarketPrice': value} for value in [0, -1, None, True, '123', float('nan'), float('inf')]
] + [
    {'currency': value} for value in ['EUR', 'usd', 'CAD', None, ['USD']]
] + [
    {'regularMarketTime': value} for value in [0, True, None, '1781524800', float('nan'), float('inf'), NOW + 301]
] + [{'symbol': 'OTHER'}])
def test_invalid_quote_fields_are_unavailable_not_zero(market, auth, monkeypatch, overrides):
    network(monkeypatch, market, lambda r: boc() if r.url.host == 'www.bankofcanada.ca' else yahoo(**overrides))
    response = client_for(market, auth).get(BASE, params={'symbols': 'MSFT'})
    assert response.status_code == 200
    body = response.json()
    quote = body['quotes'][0]
    assert quote['price'] is None
    assert quote['as_of'] is None
    assert quote['status'] == 'unavailable'
    assert quote['stale'] is False
    assert quote['error'] == 'invalid_response'
    assert body['fx']['status'] == 'ok'
    assert body['meta']['warnings']


@pytest.mark.parametrize('payload', [None, [], {}, {'chart': {'result': []}},
    {'chart': {'result': [None]}}, {'chart': {'result': [{'meta': {}}]}},
    {'chart': {'result': [{'meta': yahoo()['chart']['result'][0]['meta']}], 'error': {'description': 'PRIVATE_UPSTREAM_DETAIL'}}}])
def test_malformed_yahoo_does_not_break_fx_or_leak_payload(market, auth, monkeypatch, payload):
    network(monkeypatch, market, lambda r: boc() if r.url.host == 'www.bankofcanada.ca' else payload)
    response = client_for(market, auth).get(BASE, params={'symbols': 'MSFT'})
    assert response.status_code == 200
    assert response.json()['quotes'][0]['status'] == 'unavailable'
    assert response.json()['fx']['status'] == 'ok'
    assert 'PRIVATE_UPSTREAM_DETAIL' not in response.text


@pytest.mark.parametrize('payload', [boc('NaN'), boc('Infinity'), boc('0'), boc('-1'), boc(True), boc(None),
    boc(date='2099-01-01'), boc(date='1999-01-01'), boc(date='2026-02-30'), boc(date='20260615'),
    {'observations': []}, {'observations': [{'d': '2026-06-15', 'FXCADUSD': {'v': '0.73'}}]}])
def test_invalid_fx_is_unavailable_without_losing_quote(market, auth, monkeypatch, payload):
    network(monkeypatch, market, lambda r: payload if r.url.host == 'www.bankofcanada.ca' else yahoo())
    response = client_for(market, auth).get(BASE, params={'symbols': 'MSFT'})
    assert response.status_code == 200
    body = response.json()
    assert body['fx']['rate'] is None
    assert body['fx']['as_of'] is None
    assert body['fx']['status'] == 'unavailable'
    assert body['quotes'][0]['status'] == 'ok'
    assert body['meta']['warnings']


def test_old_provider_observations_are_explicitly_stale(market, auth, monkeypatch):
    old = NOW - 10 * 86400
    network(monkeypatch, market, lambda r: boc(date=iso(old)[:10]) if r.url.host == 'www.bankofcanada.ca' else yahoo(regularMarketTime=old))
    body = client_for(market, auth).get(BASE, params={'symbols': 'MSFT'}).json()
    assert body['quotes'][0]['status'] == 'stale'
    assert body['quotes'][0]['stale'] is True
    assert body['quotes'][0]['as_of'] == iso(old)
    assert body['fx']['status'] == 'stale'
    assert body['fx']['as_of'] == iso(old)[:10]
    assert body['meta']['warnings']


@pytest.mark.parametrize('provider', ['yahoo', 'boc'])
def test_timeout_is_partial_unavailable_with_safe_error(market, auth, monkeypatch, provider):
    def fail(request):
        if (request.url.host == 'www.bankofcanada.ca') == (provider == 'boc'):
            raise httpx.ReadTimeout('PRIVATE_UPSTREAM_DETAIL')
        return boc() if provider == 'yahoo' else yahoo()
    network(monkeypatch, market, fail)
    response = client_for(market, auth).get(BASE, params={'symbols': 'MSFT'})
    assert response.status_code == 200
    body = response.json()
    failed = body['fx'] if provider == 'boc' else body['quotes'][0]
    assert failed['status'] == 'unavailable'
    assert 'PRIVATE_UPSTREAM_DETAIL' not in response.text


class GuardedStream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def __iter__(self):
        yield from self.chunks()

    def close(self):
        self.closed = True


@pytest.mark.parametrize('mode', ['declared', 'streamed', 'compressed', 'slow'])
def test_network_body_and_elapsed_time_are_bounded(market, auth, monkeypatch, mode):
    ticks = [0.0]
    monkeypatch.setattr(market, '_monotonic', lambda: ticks[0], raising=False)

    def chunks():
        if mode in ('declared', 'compressed'):
            raise AssertionError('Rejected body must not be read')
        if mode == 'streamed':
            yield b' ' * 65536
            yield b' '
        else:
            ticks[0] += 9
            yield b' '
        raise AssertionError('Adapter read past its byte/time budget')

    stream = GuardedStream(chunks)
    headers = {'content-type': 'application/json'}
    if mode == 'declared':
        headers['content-length'] = '65537'
    if mode == 'compressed':
        headers['content-encoding'] = 'gzip'
    network(monkeypatch, market, lambda r: boc() if r.url.host == 'www.bankofcanada.ca' else httpx.Response(200, headers=headers, stream=stream))
    response = client_for(market, auth).get(BASE, params={'symbols': 'MSFT'})
    assert response.status_code == 200
    assert response.json()['quotes'][0]['status'] == 'unavailable'
    assert stream.closed


@pytest.mark.parametrize('status', [301, 302, 307, 308, 403, 429, 500])
def test_http_failure_never_follows_redirect_or_exposes_body(market, auth, monkeypatch, status):
    requests = network(monkeypatch, market, lambda r: boc() if r.url.host == 'www.bankofcanada.ca' else httpx.Response(
        status, headers={'location': 'http://169.254.169.254/latest/meta-data/'},
        stream=httpx.ByteStream(b'PRIVATE_UPSTREAM_DETAIL')))
    response = client_for(market, auth).get(BASE, params={'symbols': 'MSFT'})
    assert response.status_code == 200
    assert response.json()['quotes'][0]['status'] == 'unavailable'
    assert len(requests) == 2
    assert 'PRIVATE_UPSTREAM_DETAIL' not in response.text


def test_transport_has_explicit_timeout_and_identity_encoding(market, auth, monkeypatch):
    requests = network(monkeypatch, market)
    client_for(market, auth).get(BASE, params={'symbols': 'MSFT'})
    for request in requests:
        assert request.headers['accept-encoding'] == 'identity'
        assert request.headers['accept'] == 'application/json'
        assert all(0 < n <= 4 for n in request.extensions['timeout'].values())


def test_internal_fetch_rejects_any_nonfixed_url(market):
    for url in ['https://127.0.0.1', 'http://www.bankofcanada.ca/',
                'https://query1.finance.yahoo.com/anything', 'file:///etc/passwd']:
        with pytest.raises(ValueError):
            market._fetch_json(url)


def test_ttl_cache_and_bounded_stale_fallback(market, auth, monkeypatch):
    ticks = [1000.0]
    wall = [NOW]
    failed = [False]

    def handler(request):
        if failed[0]:
            raise httpx.ReadTimeout('PRIVATE_UPSTREAM_DETAIL')
        return boc() if request.url.host == 'www.bankofcanada.ca' else yahoo()

    requests = network(monkeypatch, market, handler)
    monkeypatch.setattr(market, '_monotonic', lambda: ticks[0])
    monkeypatch.setattr(market, '_now', lambda: wall[0])
    client = client_for(market, auth)
    original = client.get(BASE, params={'symbols': 'MSFT'}).json()
    assert len(requests) == 2
    for _ in range(3):
        assert client.get(BASE, params={'symbols': 'MSFT'}).json()['quotes'] == original['quotes']
    assert len(requests) == 2
    failed[0] = True
    ticks[0] += 61
    wall[0] += 61
    fallback = client.get(BASE, params={'symbols': 'MSFT'}).json()
    quote = fallback['quotes'][0]
    assert quote['price'] == 123.45
    assert quote['status'] == 'stale'
    assert quote['stale'] is True
    assert quote['fetched_at'] == original['quotes'][0]['fetched_at']
    assert quote['as_of'] == original['quotes'][0]['as_of']
    assert quote['error'] == 'provider_unavailable'
    assert fallback['fx']['rate'] == 1.37
    assert fallback['fx']['status'] == 'stale'
    assert fallback['meta']['warnings']
    assert len(requests) == 4
    client.get(BASE, params={'symbols': 'MSFT'})
    assert len(requests) == 4, 'Failures must have a short retry cooldown too'
    ticks[0] += 86401
    wall[0] += 86401
    expired = client.get(BASE, params={'symbols': 'MSFT'}).json()
    assert expired['quotes'][0]['price'] is None
    assert expired['quotes'][0]['status'] == 'unavailable'
    assert expired['fx']['rate'] is None
    assert expired['fx']['status'] == 'unavailable'


def test_cold_failure_is_cached_and_later_success_recovers(market, auth, monkeypatch):
    ticks = [1000.0]
    failed = [True]

    def handler(request):
        if failed[0]:
            raise httpx.ConnectError('synthetic offline')
        return boc() if request.url.host == 'www.bankofcanada.ca' else yahoo()

    requests = network(monkeypatch, market, handler)
    monkeypatch.setattr(market, '_monotonic', lambda: ticks[0])
    client = client_for(market, auth)
    for _ in range(3):
        body = client.get(BASE, params={'symbols': 'MSFT'}).json()
        assert body['quotes'][0]['status'] == 'unavailable'
    assert len(requests) == 2
    failed[0] = False
    ticks[0] += 16
    body = client.get(BASE, params={'symbols': 'MSFT'}).json()
    assert body['quotes'][0]['status'] == 'ok'
    assert body['fx']['status'] == 'ok'
    assert len(requests) == 4


def test_concurrent_requests_single_flight_public_cache(market, auth, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    import time

    def handler(request):
        time.sleep(0.03)  # Synthetic overlap; never a real provider request.
        return boc() if request.url.host == 'www.bankofcanada.ca' else yahoo()

    requests = network(monkeypatch, market, handler)
    client = client_for(market, auth)
    barrier = Barrier(6)

    def fetch(_):
        barrier.wait(timeout=5)
        return client.get(BASE, params={'symbols': 'MSFT'}).json()

    with ThreadPoolExecutor(max_workers=6) as pool:
        bodies = list(pool.map(fetch, range(6)))
    assert all(body['quotes'][0]['status'] == 'ok' for body in bodies)
    assert len(requests) == 2


def test_cached_data_never_bypasses_revocation_and_routers_do_not_share_cache(market, auth, monkeypatch):
    requests = network(monkeypatch, market)
    client = client_for(market, auth)
    assert client.get(BASE, params={'symbols': 'MSFT'}).status_code == 200
    second = client_for(market, auth)
    assert second.get(BASE, params={'symbols': 'MSFT'}).status_code == 200
    assert len(requests) == 4
    with auth.store.transaction() as db:
        db.execute('UPDATE sessions SET revoked=1')
    response = client.get(BASE, params={'symbols': 'MSFT'})
    assert response.status_code == 401
    assert 'quotes' not in response.json()
    assert len(requests) == 4


def test_clock_rollback_cannot_serve_future_cached_observations(market, auth, monkeypatch):
    requests = network(monkeypatch, market)
    wall = [NOW]
    monkeypatch.setattr(market, '_now', lambda: wall[0])
    client = client_for(market, auth)
    assert client.get(BASE, params={'symbols': 'MSFT'}).json()['quotes'][0]['status'] == 'ok'
    wall[0] -= 2 * 86400
    body = client.get(BASE, params={'symbols': 'MSFT'}).json()
    assert body['quotes'][0]['price'] is None
    assert body['quotes'][0]['status'] == 'unavailable'
    assert body['fx']['rate'] is None
    assert body['fx']['status'] == 'unavailable'
    assert len(requests) == 4


def test_forward_clock_correction_ages_cached_observations(market, auth, monkeypatch):
    requests = network(monkeypatch, market)
    wall = [NOW]
    monkeypatch.setattr(market, '_now', lambda: wall[0])
    client = client_for(market, auth)
    original = client.get(BASE, params={'symbols': 'MSFT'}).json()
    wall[0] += 10 * 86400
    body = client.get(BASE, params={'symbols': 'MSFT'}).json()
    assert body['quotes'][0]['status'] == 'stale'
    assert body['quotes'][0]['stale'] is True
    assert body['quotes'][0]['fetched_at'] == original['quotes'][0]['fetched_at']
    assert body['fx']['status'] == 'stale'
    assert len(requests) == 2


def test_stale_fallback_expiry_is_not_extended_by_retry_cooldown(market, auth, monkeypatch):
    ticks = [1000.0]
    failed = [False]

    def handler(request):
        if failed[0]:
            raise httpx.ConnectError('synthetic offline')
        return boc() if request.url.host == 'www.bankofcanada.ca' else yahoo()

    network(monkeypatch, market, handler)
    monkeypatch.setattr(market, '_monotonic', lambda: ticks[0])
    client = client_for(market, auth)
    client.get(BASE, params={'symbols': 'MSFT'})
    failed[0] = True
    ticks[0] += 86399
    assert client.get(BASE, params={'symbols': 'MSFT'}).json()['quotes'][0]['status'] == 'stale'
    ticks[0] += 2
    body = client.get(BASE, params={'symbols': 'MSFT'}).json()
    assert body['quotes'][0]['price'] is None
    assert body['fx']['rate'] is None


def test_cache_lock_wait_is_bounded(market, monkeypatch):
    cache = market._MarketCache()

    class BusyLock:
        def acquire(self, *, timeout):
            assert 0 < timeout <= 1
            return False

        def __enter__(self):
            raise AssertionError('Unbounded lock wait')

        def __exit__(self, *args):
            pass

    cache._locks['MSFT'] = BusyLock()
    result = cache.get('MSFT')
    assert result['status'] == 'unavailable'
    assert result['price'] is None
    assert result['error'] == 'provider_busy'
