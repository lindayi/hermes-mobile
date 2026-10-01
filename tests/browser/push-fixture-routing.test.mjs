import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {withoutValidatedPresence} from './push-fixture-contract.mjs';

// Exercise the real fixture's accounting branch without starting a browser/server.
const source=readFileSync(new URL('./activity-card.spec.mjs',import.meta.url),'utf8');
const start=source.indexOf('const url=new URL(req.url');
const end=source.indexOf('  if(url.pathname.startsWith',start);
assert.ok(start>=0 && end>start,'bounded activity request accounting branch exists');
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
const count=new AsyncFunction('req','withoutValidatedPresence','let writes=0;'+source.slice(start,end)+'return writes;');
const request=(url,csrf='fixture',extra={})=>({url,method:'POST',headers:{'x-csrf-token':csrf},
  async *[Symbol.asyncIterator](){yield JSON.stringify({client_id:'browser_1',sequence:0,visible:true,session_id:'s',...extra});}});

for(const [path,expected] of [
  ['/hermes/app-api/push/presence',0],
  ['/push/hermes/app-api/presence',1],
  ['/push/presence',1],
  ['/hermes/app-api/runs/r/stop',1],
  ['/hermes/app-api/push/presence/extra',1],
])test(`fixture accounts exact raw route ${path}`,async()=>{
  assert.equal(await count(request(path),withoutValidatedPresence),expected);
});

test('fixture never exempts invalid CSRF or task input disguised as presence',async()=>{
  await assert.rejects(count(request('/hermes/app-api/push/presence','wrong'),withoutValidatedPresence));
  await assert.rejects(count(request('/hermes/app-api/push/presence','fixture',{input:'must count/reject'}),withoutValidatedPresence));
});
