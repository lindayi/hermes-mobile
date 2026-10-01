import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(resolve=>setTimeout(resolve,10));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const record={id:'a',run_id:'r',session_id:'s',user_id:'owner',input:'Saved guidance',idempotency_key:'exact-key',status:'unknown',updated_at:2};
const button=(doc,name)=>[...doc.querySelectorAll('button')].find(el=>el.getAttribute('aria-label')===name || el.textContent.trim()===name);
const retry=doc=>button(doc,'Retry this steering request');
async function fixture(options={}) {
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/',pretendToBeVisual:true}),win=dom.window,doc=win.document;
 const state={owner:'owner',status:'running',records:[record],steering:true,calls:[],streams:[],controls:null,post:null,...options};
 class Events {constructor(){this.listeners={};state.streams.push(this);}addEventListener(name,fn){this.listeners[name]=fn;}close(){}emit(name,data){this.listeners[name]?.({data:JSON.stringify(data)});}}
 win.EventSource=Events;let app;
 try {
  app=await mountApp(doc,{clear(){},async request(p,o={}){
   state.calls.push({p,o});
   if(p==='/auth/me')return {user:{id:state.owner,status:'ready'}};
   if(p.startsWith('/sessions?'))return {items:[{id:'s',title:'Fixture'}],total:1};
   if(p.includes('/messages'))return {items:[{role:'user',kind:'guidance',content:record.input,run_id:'r',idempotency_key:record.idempotency_key,steering_id:'a',steering_status:'unknown'}],run:state.history?null:{id:'r',session_id:'s',status:state.status},last_run:{id:'r',session_id:'s',status:state.status}};
   if(p.endsWith('/controls'))return state.controls?state.controls():{steering:state.steering,attempts:state.records};
   if(p.endsWith('/steer'))return state.post?state.post():{...record,...o.body,status:'accepted_unconfirmed',updated_at:3};
   if(p==='/runs' && o.method==='POST')return {id:'r2',session_id:'s',status:'running'};
   if(p.startsWith('/runs/'))return {id:'r',session_id:'s',status:state.status};
   return {items:[]};
  }},win);
  button(doc,'Fixture').click();await tick();
  return {doc,win,state,app,edit(value){const el=doc.querySelector('textarea');el.value=value;el.dispatchEvent(new win.Event('input'));return el;},close(){app.destroy();win.close();}};
 }catch(error){app?.destroy();win.close();throw error;}
}

for(const channel of ['controls','SSE'])test(`foreign-session ${channel} record is not actionable for the current conversation`,async()=>{
 const foreign={...record,session_id:'other-session'};
 const f=await fixture({records:channel==='controls'?[foreign]:[]});
 try {
  if(channel==='SSE')f.state.streams[0].emit('steering',foreign);
  await tick();assert.equal(retry(f.doc),undefined,'same run/key with foreign session must not expose retry');
  assert.equal(f.state.calls.filter(call=>call.o.method==='POST').length,0);
 }finally{f.close();}
});

for(const status of ['completed','done','failed','cancelled','stopped','stopping','unknown','waiting_for_approval','queued','submitted'])test(`${status} removes retry and rejects detached button after late focus controls/SSE`,async()=>{
 const f=await fixture();
 try {
  const old=retry(f.doc);assert.ok(old);const draft=f.edit('Preserve selection');draft.setSelectionRange(2,6);const messages=f.doc.querySelector('.messages');Object.defineProperties(messages,{scrollHeight:{value:1000},clientHeight:{value:100}});messages.scrollTop=21;
  const gate=deferred();f.state.controls=()=>gate.promise;f.win.dispatchEvent(new f.win.Event('focus'));await tick();
  f.state.status=status;f.state.streams[0].emit('status',{status});
  assert.equal(retry(f.doc),undefined);
  f.state.streams[0].emit('steering',{...record,updated_at:4});
  gate.resolve({steering:true,attempts:[{...record,updated_at:5}]});await tick();old.click();await tick();
  assert.equal(retry(f.doc),undefined);assert.equal(draft.value,'Preserve selection');assert.deepEqual([draft.selectionStart,draft.selectionEnd],[2,6]);
  assert.equal(f.doc.querySelector('.messages').scrollTop,21);assert.match(f.doc.querySelector('.guidance-status').textContent,/Delivery unknown/);
  assert.equal(f.state.calls.filter(call=>call.o.method==='POST').length,0);
 }finally{f.close();}
});

for(const status of ['completed','failed','cancelled','stopped','unknown'])test(`historical ${status} unknown guidance is never a composer retry`,async()=>{
 const f=await fixture({status,history:true});try{assert.equal(retry(f.doc),undefined);assert.match(f.doc.querySelector('.guidance-status').textContent,/Delivery unknown/);}finally{f.close();}
});

for(const change of ['navigation','owner','new run'])test(`late old-run receipt and controls cannot resurrect retry after ${change}`,async()=>{
 const f=await fixture();
 try {
  const old=retry(f.doc),stream=f.state.streams[0],gate=deferred();f.state.controls=()=>gate.promise;f.win.dispatchEvent(new f.win.Event('focus'));await tick();
  if(change==='owner'){f.state.owner='other-owner';await f.app.start();}
  else if(change==='navigation'){button(f.doc,'Back to chats').click();await tick();}
  else {stream.emit('done',{status:'completed'});f.edit('Explicit next turn');button(f.doc,'Send message').click();await tick();}
  stream.emit('steering',{...record,updated_at:4});gate.resolve({steering:true,attempts:[record]});await tick();old.click();await tick();
  assert.equal(retry(f.doc),undefined);assert.equal(f.state.calls.filter(call=>call.p.endsWith('/steer')).length,0);
  assert.equal(f.state.calls.filter(call=>call.p==='/runs' && call.o.method==='POST').length,change==='new run'?1:0);
 }finally{f.close();}
});

test('capability revocation retires retry while a verified running retry keeps busy protection and exact identity',async()=>{
 const f=await fixture();
 try {
  const draft=f.edit('Independent draft');draft.setSelectionRange(3,8);const old=retry(f.doc),gate=deferred();f.state.post=()=>gate.promise;
  old.click();old.click();await tick();assert.equal(retry(f.doc).disabled,true);assert.equal(button(f.doc,'Stop run').disabled,false);
  assert.deepEqual(f.state.calls.filter(call=>call.p.endsWith('/steer')).map(call=>call.o.body),[{input:record.input,idempotency_key:record.idempotency_key}]);
  f.state.steering=false;f.win.dispatchEvent(new f.win.Event('focus'));await tick();assert.equal(retry(f.doc),undefined);
  gate.resolve({...record,status:'unknown',updated_at:3});await tick();assert.equal(retry(f.doc),undefined);
  assert.equal(draft.value,'Independent draft');assert.deepEqual([draft.selectionStart,draft.selectionEnd],[3,8]);assert.equal(f.state.calls.filter(call=>call.p==='/runs').length,0);
 }finally{f.close();}
});
