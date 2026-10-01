import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {createAPI} from '../../frontend/api.mjs';
import {mountApp} from '../../frontend/ui.mjs';
import {fromBase64url} from '../../frontend/webauthn.mjs';
const require = createRequire('/usr/local/lib/hermes-agent/package.json');
const {JSDOM} = require('jsdom');
const root = fileURLToPath(new URL('../../', import.meta.url));

async function waitFor(check) {
  for (let i=0;i<200;i++) {
    if (check()) return;
    await new Promise(resolve=>setTimeout(resolve,10));
  }
  assert.ok(check(), 'UI action completed within two seconds');
}
const click=(doc,text)=>{
  const button=[...doc.querySelectorAll('button')].find(el=>el.textContent.trim()===text || el.getAttribute('aria-label')===text);
  assert.ok(button, `control ${text} exists`); button.click();
};

test('real auth/profile routers accept UI step-up and stable CSRF, rotate cookies, and reject unavailable provisioning honestly', async(t)=>{
  const child=spawn(process.env.HERMES_TEST_PYTHON || `${root}.venv/bin/python`, ['tests/auth_router_bridge.py'],{cwd:root,stdio:['pipe','pipe','pipe']});
  const pending=[];let stderr='';
  child.stderr.on('data',data=>{stderr+=data;});
  child.on('error',error=>{for(const waiter of pending.splice(0))waiter.reject(error);});
  child.on('exit',code=>{for(const waiter of pending.splice(0))waiter.reject(new Error(`bridge exited ${code}: ${stderr}`));});
  const lines=createInterface({input:child.stdout});
  lines.on('line',line=>{const waiter=pending.shift();try{waiter.resolve(JSON.parse(line));}catch(error){waiter.reject(error);}});
  const bridge=message=>new Promise((resolve,reject)=>{pending.push({resolve,reject});child.stdin.write(JSON.stringify(message)+'\n');});
  t.after(async()=>{
    child.stdin.end();
    if(child.exitCode===null)await new Promise(resolve=>child.once('exit',resolve));
    lines.close();
  });
  const calls=[];
  const api=createAPI(async(path,options)=>{
    const response=await bridge({op:'request',path,...options});
    calls.push({path,...options,status:response.status});
    return new Response(response.text,{status:response.status});
  });
  const dom=new JSDOM('<div id="app"></div>',{url:'https://lindayi.me/hermes/'});
  t.after(()=>dom.window.close());
  dom.window.PublicKeyCredential=class {};
  dom.window.navigator.credentials={get:async({publicKey})=>{
    const signed=await bridge({op:'assert',challenge:Buffer.from(publicKey.challenge).toString('base64url')});
    return {...signed,rawId:fromBase64url(signed.rawId),getClientExtensionResults:()=>({}),
      response:Object.fromEntries(Object.entries(signed.response).map(([key,value])=>[key,fromBase64url(value)]))};
  }};
  const {document:doc}=dom.window;
  const app=await mountApp(doc,api,dom.window);
  t.after(()=>app.destroy());
  click(doc,'Settings');await waitFor(()=>doc.body.textContent.includes('Family accounts'));
  assert.equal(calls.some(c=>c.method==='POST'),false);
  click(doc,'Create invitation');
  doc.querySelector('[name=label]').value='Router-verified fixture invitation';
  click(doc,'Create code');await waitFor(()=>doc.body.textContent.includes('Your invitation code'));
  let facts=await bridge({op:'inspect'});
  assert.equal(facts.rotated,true);assert.equal(facts.stable_session,true);assert.equal(facts.stable_csrf,true);
  assert.equal(facts.stale_status,401);assert.equal(facts.invites,1);
  const verify=calls.find(c=>c.path.endsWith('/auth/verify/finish'));
  const invite=calls.find(c=>c.path.endsWith('/invites') && c.method==='POST');
  assert.equal(verify.status,200);assert.equal(invite.status,200);
  assert.ok(verify.headers['X-CSRF-Token']);
  assert.equal(invite.headers['X-CSRF-Token'],verify.headers['X-CSRF-Token']);
  click(doc,'Done');await waitFor(()=>doc.body.textContent.includes('Family accounts'));
  click(doc,'Provision profile');click(doc,'Confirm change');
  await waitFor(()=>doc.body.textContent.includes('Profile provisioning is not configured'));
  const provision=calls.find(c=>c.path.endsWith('/members/family1/provision'));
  assert.equal(provision.method,'POST');assert.equal(provision.body,'{}');assert.equal(provision.status,503);
  assert.doesNotMatch(doc.body.textContent,/Profile setup: provisioned/);
  facts=await bridge({op:'inspect'});
  assert.equal(facts.member_status,'pending');assert.equal(facts.profile_created,false);
});
