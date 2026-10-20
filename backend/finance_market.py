"""Owner-only public market data; never reads or changes the financial ledger.

Router mounts at /hermes/app-api/finance/quotes. Prices are latest public
observations, not trade execution prices. USDCAD means CAD per one USD; its
as_of is the Bank of Canada observation date, not the HTTP fetch date.
Status is ok, stale or unavailable. Sources are yahoo / bankofcanada; missing
prices/rates are null, never zero. Quote source age >4 days and FX age >7 days
are stale. The cache retries after 60s (15s on failure); fallback retains the
original fetched_at and is limited to 24h since a successful provider fetch.
"""
from datetime import date, datetime, timezone
import json
import math
import time
from threading import Lock

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.routing import APIRoute


SYMBOLS = {'MSFT': 'USD', 'TGED.TO': 'CAD', 'TPE.TO': 'CAD',
           'TPU.TO': 'CAD', 'TTP.TO': 'CAD', 'TDB.TO': 'CAD'}
YAHOO_URLS = {symbol: f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d'
              for symbol in SYMBOLS}
BOC_URL = 'https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json?recent=1'


def _now():
    return time.time()


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace('+00:00', 'Z')


def _monotonic():
    return time.monotonic()


MAX_BODY_BYTES = 65536
FETCH_BUDGET_SECONDS = 8


def _fetch_json(url):
    """Fixed public endpoints, no redirects/proxies/cookies or decompression.

    Socket operations have a 4s timeout (connect 2s); the streaming loop also
    checks an 8s elapsed budget so trickling bodies cannot run indefinitely.
    One blocking read can overrun that elapsed budget by at most its timeout.
    """
    if url not in (*YAHOO_URLS.values(), BOC_URL):
        raise ValueError('Endpoint not allowed')
    started = _monotonic()
    with httpx.Client(follow_redirects=False, trust_env=False,
                      timeout=httpx.Timeout(4.0, connect=2.0),
                      headers={'Accept': 'application/json', 'Accept-Encoding': 'identity',
                               'User-Agent': 'Hermes-Finance/1.0'}) as client:
        with client.stream('GET', url) as response:
            response.raise_for_status()
            if (response.headers.get('content-encoding', 'identity').lower() != 'identity'
                    or response.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json'
                    or int(response.headers.get('content-length', '0')) > MAX_BODY_BYTES):
                raise ValueError('Invalid response envelope')
            body = bytearray()
            if _monotonic() - started > FETCH_BUDGET_SECONDS:
                raise httpx.ReadTimeout('Provider deadline exceeded')
            for chunk in response.iter_raw():
                if _monotonic() - started > FETCH_BUDGET_SECONDS:
                    raise httpx.ReadTimeout('Provider deadline exceeded')
                if len(body) + len(chunk) > MAX_BODY_BYTES:
                    raise ValueError('Response exceeds byte budget')
                body.extend(chunk)
            return json.loads(body)


def _positive(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError('Invalid positive number')
    return value


def _quote(symbol):
    chart = _fetch_json(YAHOO_URLS[symbol])['chart']
    if chart['error'] is not None:
        raise ValueError('Provider rejected symbol')
    meta = chart['result'][0]['meta']
    price = _positive(meta['regularMarketPrice'])
    timestamp = _positive(meta['regularMarketTime'])
    now = _now()
    if (meta['symbol'] != symbol or meta['currency'] != SYMBOLS[symbol]
            or not 946684800 <= timestamp <= now + 300):
        raise ValueError('Invalid symbol, currency or timestamp')
    stale = now - timestamp > 4 * 86400
    return {'symbol': symbol, 'price': price, 'currency': SYMBOLS[symbol],
            'as_of': _iso(timestamp), 'fetched_at': _iso(now), 'source': 'yahoo',
            'status': 'stale' if stale else 'ok', 'stale': stale,
            'error': 'source_old' if stale else None}


def _fx():
    observation = _fetch_json(BOC_URL)['observations'][-1]
    raw_rate = observation['FXUSDCAD']['v']
    if not isinstance(raw_rate, str) or len(raw_rate) > 32:
        raise ValueError('Invalid rate')
    rate = _positive(float(raw_rate))
    observed = date.fromisoformat(observation['d'])
    today = datetime.fromtimestamp(_now(), timezone.utc).date()
    if observation['d'] != observed.isoformat() or not date(2000, 1, 1) <= observed <= today:
        raise ValueError('Invalid observation date')
    return {'pair': 'USDCAD', 'rate': rate, 'as_of': observed.isoformat(),
            'source': 'bankofcanada', 'status': 'stale' if (today - observed).days > 7 else 'ok'}


def _unavailable(symbol, error):
    if symbol == 'USDCAD':
        return {'pair': 'USDCAD', 'rate': None, 'as_of': None,
                'source': 'bankofcanada', 'status': 'unavailable'}
    return {'symbol': symbol, 'price': None, 'currency': SYMBOLS[symbol], 'as_of': None,
            'fetched_at': _iso(_now()), 'source': 'yahoo', 'status': 'unavailable',
            'stale': False, 'error': error}


def _load(symbol):
    try:
        return _fx() if symbol == 'USDCAD' else _quote(symbol)
    except httpx.HTTPError:
        return _unavailable(symbol, 'provider_unavailable')
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError):
        # Never expose upstream payloads, exception text, filesystem paths or URLs.
        return _unavailable(symbol, 'invalid_response')


CACHE_TTL_SECONDS = 60
FAILURE_TTL_SECONDS = 15
STALE_FALLBACK_SECONDS = 86400


def _credible_cached(result):
    """Recheck source time after host-clock corrections, including warm cache."""
    if result['as_of'] is None:
        return True
    if 'symbol' in result:
        return datetime.fromisoformat(result['as_of']).timestamp() <= _now() + 300
    return result['as_of'] <= _iso(_now())[:10]


def _cached_copy(value):
    result = dict(value)
    if result['status'] == 'ok':
        if 'symbol' in result:
            if _now() - datetime.fromisoformat(result['as_of']).timestamp() > 4 * 86400:
                result.update(status='stale', stale=True, error='source_old')
        elif (datetime.fromtimestamp(_now(), timezone.utc).date()
              - date.fromisoformat(result['as_of'])).days > 7:
            result['status'] = 'stale'
    return result


class _MarketCache:
    """Seven public-only entries, per-router, single-flight per symbol.

    No identity, holdings or position data is stored. A failed refresh never
    extends the age of the last successful fetch. Returned dicts are copies.
    """
    def __init__(self):
        self._locks = {symbol: Lock() for symbol in (*SYMBOLS, 'USDCAD')}
        self._results = {}
        self._good = {}

    def get(self, symbol):
        lock = self._locks[symbol]
        if not lock.acquire(timeout=1):
            return _unavailable(symbol, 'provider_busy')
        try:
            now = _monotonic()
            cached = self._results.get(symbol)
            if cached and now < cached[0] and _credible_cached(cached[1]):
                return _cached_copy(cached[1])
            result = _load(symbol)
            finished = _monotonic()
            ttl = CACHE_TTL_SECONDS
            if result['status'] != 'unavailable':
                self._good[symbol] = (finished, dict(result))
            else:
                ttl = FAILURE_TTL_SECONDS
                previous = self._good.get(symbol)
                if (previous and 0 <= finished - previous[0] <= STALE_FALLBACK_SECONDS
                        and _credible_cached(previous[1])):
                    ttl = min(ttl, previous[0] + STALE_FALLBACK_SECONDS - finished)
                    failure = result.get('error', 'provider_unavailable')
                    result = dict(previous[1], status='stale')
                    if symbol != 'USDCAD':
                        result.update(stale=True, error=failure)
                else:
                    self._good.pop(symbol, None)
            self._results[symbol] = (finished + ttl, dict(result))
            return result
        finally:
            lock.release()


class _PrivateRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request):
            try:
                response = await handler(request)
            except HTTPException as exc:
                exc.headers = {**(exc.headers or {}), 'Cache-Control': 'no-store', 'Vary': 'Cookie'}
                raise
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Vary'] = 'Cookie'
            return response

        return private_handler


def build_market_router(auth_service):
    """Mount directly on the app (prefix already includes /hermes/app-api)."""
    router = APIRouter(prefix='/hermes/app-api/finance', route_class=_PrivateRoute)
    cache = _MarketCache()

    def owner(user=Depends(auth_service.require_user)):
        auth_service.require_owner(user)
        if user.get('profile') != 'default' or user.get('status') != 'ready':
            raise HTTPException(403, 'Ready default-profile owner required')
        return user

    @router.get('/quotes')
    def quotes(request: Request, symbols: str = '', user=Depends(owner)):
        if (set(request.query_params) - {'symbols'}
                or len(request.query_params.getlist('symbols')) > 1
                or len(symbols) > 200):
            raise HTTPException(400, 'Only a bounded symbols list is accepted')
        selected = symbols.split(',') if symbols else []
        if len(selected) > 12 or any(symbol not in SYMBOLS for symbol in selected):
            raise HTTPException(400, 'Unsupported symbols or excessive symbol count')
        selected = list(dict.fromkeys(selected))
        values = [cache.get(symbol) for symbol in selected]
        fx = cache.get('USDCAD')
        warnings = [f"{item.get('symbol', 'USDCAD')}: {item['status']}"
                    for item in [*values, fx] if item['status'] != 'ok']
        return {'quotes': values, 'fx': fx, 'meta': {'generated_at': _iso(_now()),
                                                  'source': 'public-market', 'warnings': warnings}}

    return router
