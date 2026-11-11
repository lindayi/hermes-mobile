// Contract for the simplified chat approval lifecycle: the server is the source of
// truth, the client refetches on focus/visibility/events, buttons are disabled while
// a decision is in flight, and a stale list response is ignored.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(resolve=>setTimeout(resolve,10));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const waitFor=async(check,message='condition not met')=>{for(let i=0;i<80&&!check();i++)await tick();assert.ok(check(),message);};
const item=(extra={})=>({id:'a',run_id:'r',request_id:'native-a',session_id:'s',run_session_id:'s',title:'Execute fixture',
  command:'echo fixture',target:'fixture target',expires_at:2000000000,executable:true,choices:['once','deny'],status:'pending',...extra});

async function mount(handler){
  const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document,calls=[];
  win.PublicKeyCredential=class {};
  win.navigator.credentials={get:async()=>({id:'c',rawId:new Uint8Array([1]).buffer,type:'public-key',getClientExtensionResults:()=>({}),
    response:{clientDataJSON:new Uint8Array([1]).buffer,authenticatorData:new Uint8Array([1]).buffer,signature:new Uint8Array([1]).buffer,userHandle:null}})};
  const app=await mountApp(doc,{clear(){},async request(p,o={}){
    calls.push({p,o});
    const handled=await handler(p,o);if(handled!==undefined)return handled;
    if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
    if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
    if(p==='/sessions/s')return {id:'s',title:'Chat'};
    if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0};
    if(p==='/auth/verify/options')return {challenge_id:'c',options:{challenge:'AQ'}};
    if(p==='/auth/verify/finish')return {verified:true};
    return {items:[]};
  }},win);
  return {app,win,doc,calls,close(){app.destroy();win.close();}};
}
const buttons=(doc,id='a')=>[...doc.querySelectorAll(`[data-approval-id="${id}"] button`)];

test('decision buttons disable while the POST is in flight and a double tap posts once',async()=>{
  const decision=deferred();
  const h=await mount(p=>p==='/approvals'?{items:[item()]}:p==='/approvals/a/decision'?decision.promise:undefined);
  try{
    await waitFor(()=>buttons(h.doc).length===2);
    buttons(h.doc)[0].click();buttons(h.doc)[0].click();
    await waitFor(()=>h.calls.some(c=>c.p==='/approvals/a/decision'));
    assert.ok(buttons(h.doc).every(b=>b.disabled),'buttons are disabled while sending');
    assert.match(h.doc.querySelector('[data-approval-id="a"] h3').textContent,/Decision pending/);
    decision.resolve({status:'resolved'});
    await waitFor(()=>/Resolved/.test(h.doc.querySelector('[data-approval-id="a"] h3').textContent));
    assert.equal(buttons(h.doc).length,0);
    assert.equal(h.calls.filter(c=>c.p==='/approvals/a/decision').length,1);
  }finally{h.close();}
});

test('a stale list response is ignored once a newer refetch has started',async()=>{
  const lists=[];
  const h=await mount(p=>{if(p==='/approvals'){const d=deferred();lists.push(d);return d.promise;}});
  try{
    await waitFor(()=>lists.length===1);
    h.win.dispatchEvent(new h.win.Event('focus'));
    await waitFor(()=>lists.length===2);
    lists[1].resolve({items:[]});await tick();
    lists[0].resolve({items:[item()]});await tick();
    assert.equal(buttons(h.doc).length,0,'the older pending list must not revive controls');
  }finally{h.close();}
});

test('server-side removal settles a mounted card on the next refetch',async()=>{
  let pending=true;
  const h=await mount(p=>p==='/approvals'?{items:pending?[item()]:[]}:undefined);
  try{
    await waitFor(()=>buttons(h.doc).length===2);
    pending=false;
    Object.defineProperty(h.doc,'visibilityState',{value:'visible',configurable:true});
    h.doc.dispatchEvent(new h.win.Event('visibilitychange'));
    await waitFor(()=>buttons(h.doc).length===0,'visibility refetch retires the card');
    assert.match(h.doc.querySelector('[data-approval-id="a"] h3').textContent,/No longer pending/);
  }finally{h.close();}
});

test('a 409 decision response shows the server state instead of an unknown outcome',async()=>{
  let listed=[item()];
  const h=await mount(p=>{
    if(p==='/approvals')return {items:listed};
    if(p==='/approvals/a/decision'){listed=[];throw Object.assign(new Error('stale'),{status:409});}
  });
  try{
    await waitFor(()=>buttons(h.doc).length===2);
    buttons(h.doc)[1].click();
    await waitFor(()=>/no longer pending/.test(h.doc.querySelector('[data-approval-id="a"]').textContent));
    await waitFor(()=>buttons(h.doc).length===0);
  }finally{h.close();}
});

test('a 5xx decision response is shown as unknown and never retried automatically',async()=>{
  let listed=[item()];
  const h=await mount(p=>{
    if(p==='/approvals')return {items:listed};
    // The server marks an ambiguous claim unknown and stops listing it as pending.
    if(p==='/approvals/a/decision'){listed=[];throw Object.assign(new Error('Approval outcome is unresolved'),{status:503});}
  });
  try{
    await waitFor(()=>buttons(h.doc).length===2);
    buttons(h.doc)[0].click();
    await waitFor(()=>/Decision status unknown/.test(h.doc.querySelector('[data-approval-id="a"] h3').textContent));
    await tick();await tick();
    assert.equal(h.calls.filter(c=>c.p==='/approvals/a/decision').length,1);
  }finally{h.close();}
});
