import test from 'node:test';
import assert from 'node:assert/strict';

const load = async () => import('../../frontend/api.mjs').catch(() => ({}));
test('API client uses app base, same-origin cookies, and server-issued CSRF for mutations', async () => {
  const {createAPI} = await load();
  assert.equal(typeof createAPI, 'function', 'API client is implemented');
  const calls = [];
  const api = createAPI(async (url, options) => {
    calls.push({url, options});
    return new Response(JSON.stringify(url.endsWith('/auth/me') ? {user:{id:'u'},csrf_token:'csrf'} : {id:'s'}), {headers:{'Content-Type':'application/json'}});
  });
  await api.request('/auth/me');
  await api.request('/sessions', {method:'POST', body:{title:'A'}});
  assert.equal(calls[1].url, '/hermes/app-api/sessions');
  assert.equal(calls[1].options.credentials, 'same-origin');
  assert.equal(calls[1].options.cache, 'no-store');
  assert.equal(calls[1].options.headers['X-CSRF-Token'], 'csrf');
  assert.deepEqual(JSON.parse(calls[1].options.body), {title:'A'});
});

test('API rejects HTTP and transport errors honestly and refuses foreign paths', async () => {
  const {createAPI} = await load();
  const api = createAPI(async () => new Response(JSON.stringify({detail:'Native coordination unavailable'}), {status:503}));
  await assert.rejects(api.request('/runs'), error => error.status === 503 && /Native coordination unavailable/.test(error.message));
  await assert.rejects(createAPI(async () => new Response('<html>proxy down</html>', {status:502})).request('/jobs'), /502/);
  await assert.rejects(createAPI(async () => {throw new TypeError('fetch failed');}).request('/jobs'), /connect|offline/i);
  await assert.rejects(api.request('https://evil.test/'), /path/i);
});
