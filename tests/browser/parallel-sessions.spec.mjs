import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile, mkdtemp, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {withoutValidatedPresence} from './push-fixture-contract.mjs';

const {chromium} = createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const frontend = process.env.HERMES_FRONTEND_DIR
  ? pathToFileURL(process.env.HERMES_FRONTEND_DIR.replace(/\/$/, '') + '/')
  : new URL('../../frontend/', import.meta.url);
const sessions = ['A', 'B'].map(label => ({id:`parallel-${label}`, title:`Synthetic parallel ${label}`, source:'cli', updated_at:1700000000}));
const prompt = label => `Synthetic prompt ${label}; not a live model request.`;
const partial = label => `Synthetic partial ${label}.`;
const answer = label => `Synthetic completed ${label}; not a live model result.`;
const draft = label => `Unsent private draft ${label}.`;
const runId = label => `parallel-run-${label}`;

async function eventually(check, message) {
  const deadline = Date.now() + 6000;
  while (Date.now() < deadline) {
    if (await check()) return;
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  assert.fail(message);
}

// Real Chromium HTTP/EventSource, synthetic API/model responses only. No backend,
// credentials, live sessions, intercepted fetches, or substituted browser APIs.
async function fixture({steering = false} = {}) {
  // The managed runner supplies a short private TMPDIR; keep Chrome sockets there.
  const temporary = await mkdtemp(join(tmpdir(), 'ps-'));
  const state = {requests:[], unexpected:[], connections:[], snapshots:[], held:[], holdNext:new Set(), records:new Map()};
  for (const session of sessions) state.records.set(session.id, {session, run:null, events:[], clients:new Set(), persisted:false, steering, attempts:[]});
  const record = label => state.records.get(`parallel-${label}`);
  const json = (res, value, status = 200) => {res.writeHead(status, {'Content-Type':'application/json', 'Cache-Control':'no-store'}); res.end(JSON.stringify(value));};
  const encode = entry => `id: ${entry.id}\nevent: ${entry.event}\ndata: ${JSON.stringify(entry.data)}\n\n`;
  state.emit = (label, event, data) => {
    const r = record(label), entry = {id:String(r.events.length + 1), event, data};
    r.events.push(entry);
    for (const res of r.clients) res.write(encode(entry));
  };
  state.disconnect = label => {for (const res of record(label).clients) res.end(); record(label).clients.clear();};
  state.finish = label => {
    const r = record(label);
    r.run = {...r.run, status:'completed', output:answer(label)};
    r.persisted = true;
    state.emit(label, 'done', {status:'completed', output:answer(label)});
  };
  state.release = () => {for (const {res, value} of state.held.splice(0)) json(res, value);};
  const reply = (path, res, value) => {
    if (state.holdNext.delete(path)) {state.held.push({path, res, value:structuredClone(value)}); return;}
    json(res, value);
  };
  let browser;
  const server = createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://fixture'), path = url.pathname;
      if (path.startsWith('/hermes/app-api/')) {
        const api = path.slice('/hermes/app-api'.length);
        let raw = ''; for await (const chunk of req) raw += chunk;
        const call = {path, method:req.method, body:raw ? JSON.parse(raw) : null, csrf:req.headers['x-csrf-token']};
        state.requests.push(call);
        if (req.method === 'POST') assert.equal(call.csrf, 'fixture', 'every write retains CSRF');
        if (api === '/push/presence' && req.method === 'POST') {withoutValidatedPresence([call]); return json(res, {ok:true});}
        if (api === '/auth/me' && req.method === 'GET') return json(res, {user:{id:'fixture-owner', role:'owner', status:'ready'}, csrf_token:'fixture'});
        if (api === '/push/preferences' && req.method === 'GET') return json(res, {revision:0, enabled:true, categories:{completion:true, approval:true, attention:true, scheduled:true, operational:true}, hide_details:false});
        if (api === '/sessions' && req.method === 'GET') return json(res, {items:[...state.records.values()].map(r => ({...r.session, run:r.persisted ? null : r.run})), total:2});
        const match = api.match(/^\/sessions\/([^/]+)\/(messages|telemetry|model-options|background)$/);
        if (match && req.method === 'GET') {
          const r = state.records.get(match[1]); assert.ok(r, 'known synthetic session');
          if (match[2] === 'telemetry') return json(res, {model:null, provider:null, context:{used_tokens:null, limit_tokens:null, source:null, observed_at:null, estimated:false}, usage:{scope:'session_lifetime', input_tokens:null, output_tokens:null}});
          if (match[2] === 'model-options') return json(res, {available:false, models:[], default:null});
          if (match[2] === 'background') return json(res, {items:[]});
          const label = r.session.id.slice(-1);
          const items = [{role:'assistant', content:`Synthetic earlier history ${label}.`}];
          if (r.persisted) items.push({role:'user', content:r.run.input}, {role:'assistant', content:r.run.output});
          const snapshot = {items, offset:0, total:items.length, run:r.persisted ? null : r.run};
          state.snapshots.push({session:r.session.id, run:snapshot.run?.id || null, latest:url.searchParams.get('latest')});
          return json(res, snapshot);
        }
        if (api === '/runs' && req.method === 'POST') {
          const r = state.records.get(call.body.session_id); assert.ok(r, 'run has known session');
          assert.equal(r.run, null, 'navigation/reconnect must never resubmit a run');
          r.run = {id:runId(r.session.id.slice(-1)), session_id:r.session.id, input:call.body.input, status:'running', output:''};
          return json(res, r.run, 202);
        }
        const runMatch = api.match(/^\/runs\/(parallel-run-[AB])(?:\/(events|controls|steer|stop))?$/);
        if (runMatch) {
          const r = record(runMatch[1].slice(-1)); assert.ok(r.run, 'run exists');
          const operation = runMatch[2];
          if (!operation && req.method === 'GET') return reply(api, res, r.run);
          if (operation === 'controls' && req.method === 'GET') return reply(api, res, {steering:r.steering, attempts:r.attempts});
          if (operation === 'steer' && req.method === 'POST') {
            assert.ok(r.steering && r.run.status === 'running', 'guidance targets a capable running run');
            const receipt = {...call.body, id:`guidance-${r.attempts.length}`, run_id:r.run.id, session_id:r.session.id, user_id:'fixture-owner', status:'accepted_unconfirmed', updated_at:1700000001};
            r.attempts.push(receipt); return reply(api, res, receipt);
          }
          if (operation === 'stop' && req.method === 'POST') {r.run = {...r.run, status:'stopping'}; return reply(api, res, r.run);}
          if (operation === 'events' && req.method === 'GET') {
            state.connections.push({run:r.run.id, lastEventId:req.headers['last-event-id'] || ''});
            res.writeHead(200, {'Content-Type':'text/event-stream', 'Cache-Control':'no-cache', 'Connection':'keep-alive'});
            res.write('retry: 500\n\n'); r.clients.add(res); res.on('close', () => r.clients.delete(res));
            for (const entry of r.events) if (Number(entry.id) > Number(req.headers['last-event-id'] || 0)) res.write(encode(entry));
            return;
          }
        }
        if (req.method === 'GET' && ['/devices', '/passkeys', '/invites', '/members', '/inbox', '/approvals', '/jobs'].includes(api)) return json(res, {items:[]});
        state.unexpected.push(`${req.method} ${api}`); return json(res, {detail:'Unknown synthetic route'}, 404);
      }
      if (path === '/favicon.ico') {res.writeHead(204); res.end(); return;}
      const relative = path === '/hermes/' ? 'index.html' : path.replace(/^\/hermes\//, '');
      if (!path.startsWith('/hermes/') || relative.split('/').includes('..')) {res.writeHead(404); res.end(); return;}
      const body = await readFile(new URL(relative, frontend));
      const mime = {html:'text/html', css:'text/css', js:'text/javascript', mjs:'text/javascript', svg:'image/svg+xml', webmanifest:'application/manifest+json', png:'image/png'};
      res.writeHead(200, {'Content-Type':mime[relative.split('.').pop()] || 'application/octet-stream', 'Cache-Control':'no-store'}); res.end(body);
    } catch (error) {state.unexpected.push(error.stack); if (!res.headersSent) res.writeHead(500); res.end('Synthetic fixture failure');}
  });
  const close = async () => {
    try {await browser?.close();}
    finally {
      for (const label of ['A', 'B']) state.disconnect(label);
      server.closeAllConnections();
      try {if (server.listening) await new Promise(resolve => server.close(resolve));}
      finally {await rm(temporary, {recursive:true, force:true});}
    }
  };
  try {
    await new Promise((resolve, reject) => {server.once('error', reject); server.listen(0, '127.0.0.1', resolve);});
    const url = `http://127.0.0.1:${server.address().port}/hermes/`;
    browser = await chromium.launch({executablePath:process.env.HERMES_BROWSER || '/usr/bin/google-chrome', headless:true, args:['--no-sandbox', '--disable-dev-shm-usage'], env:{...process.env, TMPDIR:temporary}});
    const context = await browser.newContext({viewport:{width:390, height:740}, isMobile:true, serviceWorkers:'block'});
    const page = await context.newPage(); page.setDefaultTimeout(6000);
    const errors = [], external = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
    context.on('request', request => {if (new URL(request.url()).origin !== new URL(url).origin) external.push(request.url());});
    await page.goto(url);
    const editor = () => page.getByRole('textbox', {name:'Message Hermes', exact:true});
    const open = async label => {
      await page.getByRole('navigation', {name:'Main navigation'}).getByRole('button', {name:'Chats', exact:true}).click();
      await page.getByRole('button', {name:`Synthetic parallel ${label}`, exact:true}).click();
      await editor().waitFor();
      await page.getByText(`Synthetic earlier history ${label}.`, {exact:true}).waitFor();
    };
    const submit = async label => {
      await editor().fill(prompt(label)); await page.getByRole('button', {name:'Send message', exact:true}).click();
      await eventually(() => record(label).clients.size === 1, `${label} has a real SSE connection`);
      await state.emit(label, 'delta', {text:partial(label)});
      await page.getByText(partial(label), {exact:true}).waitFor();
    };
    const active = async (label, expectedDraft) => {
      const other = label === 'A' ? 'B' : 'A';
      await page.getByText(partial(label), {exact:true}).waitFor();
      await eventually(async () => await page.getByRole('button', {name:'Stop run', exact:true}).isEnabled(), `${label} Stop restored`);
      assert.equal(await editor().inputValue(), expectedDraft, `${label} draft belongs to this session`);
      assert.equal(await page.locator('.live-message').getAttribute('data-history-run'), runId(label));
      assert.equal(await page.locator('.user-message').filter({hasText:prompt(label)}).count(), 1, `${label} own prompt appears exactly once`);
      for (const text of [prompt(other), partial(other), answer(other), draft(other)]) assert.equal(await page.locator('.messages').getByText(text, {exact:true}).count(), 0, `${other} text never leaks into ${label}`);
      assert.equal(await page.getByRole('button', {name:'Send message', exact:true}).count(), 0, 'running session cannot submit another turn');
    };
    const writes = () => withoutValidatedPresence(state.requests.filter(call => call.method !== 'GET'));
    const verify = expectedPaths => {
      assert.deepEqual(writes().map(call => call.path.slice('/hermes/app-api'.length)), expectedPaths, 'exact mutation ledger excludes only schema/CSRF-validated presence');
      const posts = writes().filter(call => call.path === '/hermes/app-api/runs');
      assert.deepEqual(posts.map(call => [call.body.session_id, call.body.input]), sessions.map((s, i) => [s.id, prompt(i ? 'B' : 'A')]));
      assert.ok(posts.every(call => typeof call.body.idempotency_key === 'string' && call.body.idempotency_key.length > 0));
      assert.equal(new Set(posts.map(call => call.body.idempotency_key)).size, 2, 'distinct session submissions have distinct keys');
      assert.ok(state.snapshots.every(snapshot => snapshot.latest === 'true'));
      assert.deepEqual(state.unexpected, []); assert.deepEqual(external, []); assert.deepEqual(errors, []);
    };
    return {page, state, record, editor, open, submit, active, writes, verify, close};
  } catch (error) {await close(); throw error;}
}

test('parallel session navigation keeps two runs, drafts and away completion isolated', {timeout:90000}, async () => {
  const f = await fixture();
  try {
    await f.open('A'); await f.submit('A'); await f.editor().fill(draft('A'));
    await f.open('B');
    assert.equal(await f.editor().inputValue(), '', 'new session does not inherit A draft');
    assert.equal(f.record('A').run.status, 'running', 'A keeps running after navigation');
    await f.submit('B'); await f.editor().fill(draft('B'));
    assert.deepEqual(['A', 'B'].map(label => f.record(label).run.status), ['running', 'running'], 'both accepted runs coexist');
    await f.active('B', draft('B'));
    await f.open('A'); await f.active('A', draft('A'));
    await eventually(() => f.record('B').clients.size === 0, 'away B detaches its browser stream, not its run');
    await f.open('B'); await f.active('B', draft('B'));
    await f.open('A'); await f.active('A', draft('A'));
    // A new app instance must rediscover the correct run and tab-scoped drafts.
    await f.page.reload(); await f.open('A'); await f.active('A', draft('A'));
    await f.open('B'); await f.active('B', draft('B'));
    await f.open('A'); await f.active('A', draft('A'));
    await eventually(() => f.record('B').clients.size === 0, 'B completes with no active subscriber');
    const bConnections = f.state.connections.filter(c => c.run === runId('B')).length;
    f.state.finish('B');
    await f.active('A', draft('A'));
    await f.open('B'); await f.page.getByText(answer('B'), {exact:true}).waitFor();
    assert.equal(await f.editor().inputValue(), draft('B'));
    assert.equal(await f.page.getByRole('button', {name:'Stop run', exact:true}).count(), 0, 'completed B has no Stop');
    assert.equal(await f.page.getByRole('button', {name:'Send message', exact:true}).isEnabled(), true);
    assert.equal(await f.page.locator('.live-message').count(), 0, 'authoritative terminal history suppresses stale run pointer');
    assert.equal(await f.page.getByText(answer('B'), {exact:true}).count(), 1);
    assert.equal(await f.page.locator('.user-message').filter({hasText:prompt('B')}).count(), 1);
    assert.equal(f.state.connections.filter(c => c.run === runId('B')).length, bConnections, 'completed B never reconnects');
    assert.equal(await f.page.getByText(partial('A'), {exact:true}).count(), 0);
    await f.open('A'); await f.active('A', draft('A'));
    f.verify(['/runs', '/runs']);
  } finally {await f.close();}
});

test('parallel session reconnect and late controls never steer, stop or overwrite the other session', {timeout:90000}, async () => {
  const f = await fixture({steering:true});
  try {
    await f.open('A'); await f.submit('A'); await f.editor().fill(draft('A'));
    f.state.disconnect('A');
    await f.page.locator('.notice').filter({hasText:'Live connection interrupted'}).waitFor();
    await eventually(() => f.state.connections.some(c => c.run === runId('A') && c.lastEventId === '1'), 'A reconnect carries its own Last-Event-ID');
    await f.page.locator('.notice').waitFor({state:'hidden'});
    await f.active('A', draft('A'));
    assert.equal(await f.page.getByText(partial('A'), {exact:true}).count(), 1, 'reconnect does not duplicate A delta');
    await f.open('B'); await f.submit('B'); await f.editor().fill(draft('B'));
    assert.equal(f.state.connections.find(c => c.run === runId('B')).lastEventId, '', 'B never inherits A cursor');
    f.state.disconnect('B');
    await eventually(() => f.state.connections.some(c => c.run === runId('B') && c.lastEventId === '1'), 'B reconnect carries B cursor');
    await f.active('B', draft('B'));

    // Deliberately stale capability response from A arrives while B is current.
    f.record('A').steering = false;
    f.state.holdNext.add(`/runs/${runId('A')}/controls`);
    await f.open('A');
    await eventually(() => f.state.held.length === 1, 'A controls request held');
    await f.open('B'); await f.page.getByRole('button', {name:'Steer current run', exact:true}).waitFor();
    const controlsResponse = f.page.waitForResponse(response => response.url().endsWith(`/runs/${runId('A')}/controls`));
    f.state.release(); await (await controlsResponse).finished();
    await f.active('B', draft('B'));
    assert.equal(await f.page.getByRole('button', {name:'Steer current run', exact:true}).isEnabled(), true, 'late A controls cannot disable B guidance');

    f.record('A').steering = true;
    await f.open('A'); await f.page.getByRole('button', {name:'Steer current run', exact:true}).waitFor();
    const guidance = 'Synthetic explicit guidance only for A.';
    await f.editor().fill(guidance);
    f.state.holdNext.add(`/runs/${runId('A')}/steer`);
    await f.page.getByRole('button', {name:'Steer current run', exact:true}).click();
    await eventually(() => f.state.held.length === 1, 'A guidance receipt held');
    await f.open('B'); await f.active('B', draft('B'));
    const steerResponse = f.page.waitForResponse(response => response.url().endsWith(`/runs/${runId('A')}/steer`));
    f.state.release(); await (await steerResponse).finished();
    await f.active('B', draft('B'));
    assert.equal(await f.page.getByText(guidance, {exact:true}).count(), 0, 'late A receipt does not append into B');
    assert.equal(await f.page.getByRole('button', {name:'Steer current run', exact:true}).isEnabled(), true);

    await f.open('A'); await f.page.getByRole('button', {name:'Stop run', exact:true}).waitFor();
    f.state.holdNext.add(`/runs/${runId('A')}/stop`);
    await f.page.getByRole('button', {name:'Stop run', exact:true}).click();
    await eventually(() => f.state.held.length === 1, 'A Stop response held');
    await f.open('B'); await f.active('B', draft('B'));
    const stopResponse = f.page.waitForResponse(response => response.url().endsWith(`/runs/${runId('A')}/stop`));
    f.state.release(); await (await stopResponse).finished();
    await f.active('B', draft('B'));
    assert.equal(f.record('A').run.status, 'stopping');
    assert.equal(f.record('B').run.status, 'running', 'only A was stopped');
    assert.equal(await f.page.getByRole('button', {name:'Steer current run', exact:true}).isEnabled(), true, 'late A Stop must not lock B');
    f.verify(['/runs', '/runs', `/runs/${runId('A')}/steer`, `/runs/${runId('A')}/stop`]);
    const guidanceWrite = f.writes().find(call => call.path.endsWith('/steer'));
    assert.equal(guidanceWrite.body.input, guidance); assert.ok(guidanceWrite.body.idempotency_key);
    assert.deepEqual(f.writes().find(call => call.path.endsWith('/stop')).body, {});
  } finally {await f.close();}
});
