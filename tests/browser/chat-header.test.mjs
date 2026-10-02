import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(r=>setTimeout(r,5));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function click(doc,label){const el=[...doc.querySelectorAll('button')].find(x=>x.getAttribute('aria-label')===label || x.textContent===label);assert.ok(el,label);el.click();}
async function setup(override=()=>undefined,snapshot={items:[],run:null}){
 const dom=new JSDOM('<div id="app"></div>',{url:'https://example.com/hermes/',pretendToBeVisual:true}),win=dom.window,doc=win.document,calls=[],timers=new Map(),streams=[];let id=0;
 win.setTimeout=(fn,delay)=>{timers.set(++id,{fn,delay});return id;};win.clearTimeout=id=>timers.delete(id);
 class Events{constructor(){this.listeners={};streams.push(this);}addEventListener(n,f){this.listeners[n]=f;}emit(n,d){this.listeners[n]?.({data:JSON.stringify(d)});}close(){this.closed=true;}}
 win.EventSource=Events;
 const app=await mountApp(doc,{clear(){},async request(path,options={}){calls.push({path,options});const result=await override(path,options);if(result!==undefined)return result;if(path==='/auth/me')return {user:{id:'u',status:'ready'}};if(path.startsWith('/sessions?'))return {items:[{id:'s1',title:'First conversation'},{id:'s2',title:'Second conversation'}],total:2};if(path.includes('/messages'))return snapshot;return {items:[]};}},win);
 return {doc,win,app,calls,timers,streams,async open(){click(doc,'First conversation');await tick();},close(){app.destroy();win.close();}};
}
test('rename persists server title without replacing chat, cancelling or stale completion',async()=>{
 const save=deferred();const h=await setup((p,o)=>o.method==='PATCH'?save.promise:undefined);try{await h.open();const textarea=h.doc.querySelector('textarea');textarea.value='Draft';click(h.doc,'Rename session');let dialog=h.doc.querySelector('[role=dialog]');assert.ok(dialog);const input=dialog.querySelector('input');assert.equal(h.doc.activeElement,input);assert.equal(input.maxLength,200);input.value='  Saved  ';dialog.querySelector('form').dispatchEvent(new h.win.Event('submit',{bubbles:true,cancelable:true}));await tick();assert.equal(h.doc.querySelector('.conversation-head h1').textContent,'First conversation');assert.equal(h.calls.at(-1).options.body.title,'Saved');save.resolve({id:'s1',title:'Server title'});await tick();assert.equal(h.doc.querySelector('.conversation-head h1').textContent,'Server title');assert.equal(h.doc.querySelector('textarea'),textarea);assert.equal(textarea.value,'Draft');click(h.doc,'Rename session');h.doc.querySelector('[role=dialog]').dispatchEvent(new h.win.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(h.doc.querySelector('[role=dialog]'),null);assert.equal(h.calls.filter(c=>c.options.method==='PATCH').length,1);}finally{h.close();}
});
test('status is scoped, read-only verified, offline immediate and health work cleaned up',async()=>{
 let probe;const h=await setup(p=>p.includes('/messages') && probe ? probe.promise:undefined);try{await h.open();const status=h.doc.querySelector('.conversation-status');assert.equal(status.dataset.state,'idle');assert.equal(status.textContent,'Connected · Idle');probe=deferred();h.win.dispatchEvent(new h.win.Event('offline'));assert.equal(status.dataset.state,'disconnected');h.win.dispatchEvent(new h.win.Event('online'));assert.equal(status.dataset.state,'reconnecting');await tick();probe.resolve({items:[],run:null});await tick();assert.equal(status.dataset.state,'idle');assert.ok(h.timers.size);click(h.doc,'Chats');await tick();assert.equal(h.timers.size,0);assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);}finally{h.close();}
});
test('header follows run status including approval and reconnect without changing recovery',async()=>{
 const h=await setup(()=>undefined,{items:[],run:{id:'r1',session_id:'s1',status:'running',input:'Hello'}});try{await h.open();const status=h.doc.querySelector('.conversation-status'),stream=h.streams[0];assert.equal(status.dataset.state,'running');assert.equal(h.doc.querySelector('.live-message [role=status]').getAttribute('aria-hidden'),'true');stream.emit('approval',{});assert.equal(status.dataset.state,'waiting_for_approval');stream.onerror();assert.equal(status.dataset.state,'reconnecting');stream.onopen();assert.equal(status.dataset.state,'waiting_for_approval');stream.emit('done',{status:'unknown'});assert.equal(status.dataset.state,'unknown');assert.equal(h.doc.querySelector('.send').disabled,true);}finally{h.close();}
});
test('late read cannot regress newer approval status and stale rename cannot change another view',async()=>{
 const poll=deferred(),save=deferred();const h=await setup((p,o)=>p==='/runs/r1'?poll.promise:o.method==='PATCH'?save.promise:undefined,{items:[],run:{id:'r1',session_id:'s1',status:'running',input:'Hello'}});try{await h.open();h.win.dispatchEvent(new h.win.Event('focus'));await tick();h.streams[0].emit('approval',{});poll.resolve({id:'r1',session_id:'s1',status:'running'});await tick();assert.equal(h.doc.querySelector('.conversation-status').dataset.state,'waiting_for_approval');click(h.doc,'Rename session');const form=h.doc.querySelector('[role=dialog] form');form.querySelector('input').value='Stale';form.dispatchEvent(new h.win.Event('submit',{cancelable:true}));click(h.doc,'Chats');await tick();click(h.doc,'Second conversation');await tick();save.resolve({id:'s1',title:'Stale'});await tick();assert.equal(h.doc.querySelector('.conversation-head h1').textContent,'Second conversation');assert.equal(h.doc.querySelector('[role=dialog]'),null);}finally{h.close();}
});
for(const terminal of ['unknown','completed','failed'])for(const offline of [false,true])test(`late ${terminal} cannot close approval stream; fresh poll reconciles${offline?' while offline':''}`,async()=>{
 const poll=deferred();let reads=0;
 const h=await setup(p=>p==='/runs/r1'?(++reads===1?poll.promise:{id:'r1',session_id:'s1',status:terminal,output:'Fresh final'}):undefined,{items:[],run:{id:'r1',session_id:'s1',status:'running',input:'Hello'}});
 try{
  await h.open();const stream=h.streams[0],status=h.doc.querySelector('.conversation-status');
  h.win.dispatchEvent(new h.win.Event('focus'));await tick();assert.equal(reads,1);
  stream.emit('approval',{});poll.resolve({id:'r1',session_id:'s1',status:terminal,output:'Stale final'});await tick();
  assert.equal(status.dataset.state,'waiting_for_approval');assert.notEqual(stream.closed,true,'stale terminal must leave the stream open');
  assert.equal(h.doc.querySelector('.live-message [role=status]').textContent,'Waiting for approval');
  assert.equal(h.doc.querySelector('[aria-label="Send message"]'),null);assert.equal(h.doc.querySelector('[aria-label="Stop run"]').disabled,false);assert.equal(h.doc.querySelector('.live-message .message-body').textContent,'');
  if(offline)h.win.dispatchEvent(new h.win.Event('offline'));
  const entry=[...h.timers.entries()].at(-1);assert.ok(entry,'next reconciliation remains scheduled');h.timers.delete(entry[0]);entry[1].fn();await tick();
  assert.equal(reads,2);assert.equal(stream.closed,true,'fresh terminal must finish the run');
  assert.equal(h.doc.querySelector('.live-message .message-body').textContent,'Fresh final');
  assert.equal(h.doc.querySelector('.live-message [role=status]').textContent,terminal);
  assert.equal(h.doc.querySelector('.send').disabled,terminal==='unknown');
  assert.equal(status.dataset.state,offline?'disconnected':terminal==='completed'?'idle':terminal);
 }finally{h.close();}
});
test('stale 404 cannot close approval stream; fresh 404 finishes missing run',async()=>{
 const poll=deferred();let reads=0;
 const h=await setup(p=>{if(p==='/runs/r1'){if(++reads===1)return poll.promise;throw Object.assign(new Error('Missing'),{status:404});}},{items:[],run:{id:'r1',session_id:'s1',status:'running',input:'Hello'}});
 try{
  await h.open();const stream=h.streams[0];h.win.dispatchEvent(new h.win.Event('focus'));await tick();assert.equal(reads,1);
  stream.emit('approval',{});poll.reject(Object.assign(new Error('Missing'),{status:404}));await tick();
  assert.equal(h.doc.querySelector('.conversation-status').dataset.state,'waiting_for_approval');assert.notEqual(stream.closed,true);
  assert.equal(h.doc.querySelector('.live-message [role=status]').textContent,'Waiting for approval');assert.equal(h.doc.querySelector('[aria-label="Send message"]'),null);assert.equal(h.doc.querySelector('[aria-label="Stop run"]').disabled,false);
  assert.match(h.doc.querySelector('.approval-notice').textContent,/needs your review/);
  const entry=[...h.timers.entries()].at(-1);assert.ok(entry);h.timers.delete(entry[0]);entry[1].fn();await tick();
  assert.equal(reads,2);assert.equal(stream.closed,true);assert.equal(h.doc.querySelector('.live-message [role=status]').textContent,'failed');assert.equal(h.doc.querySelector('.send').disabled,false);
  assert.match(h.doc.querySelector('.notice').textContent,/no longer available/);
 }finally{h.close();}
});
test('pending idle read cannot overwrite sending state',async()=>{
 const probe=deferred(),post=deferred();let read=false;const h=await setup((p)=>p==='/runs'?post.promise:p.includes('/messages')&&read?probe.promise:undefined);try{await h.open();read=true;const entry=[...h.timers.entries()].find(([,v])=>v.delay===30000);h.timers.delete(entry[0]);entry[1].fn();await tick();h.doc.querySelector('textarea').value='Send once';click(h.doc,'Send message');await tick();assert.equal(h.doc.querySelector('.conversation-status').dataset.state,'sending');probe.resolve({items:[],run:null});await tick();assert.equal(h.doc.querySelector('.conversation-status').dataset.state,'sending');post.resolve({id:'r1',session_id:'s1',status:'queued'});await tick();}finally{h.close();}
});
test('rename validation and server failure retain input and title',async()=>{
 const h=await setup((p,o)=>{if(o.method==='PATCH')throw new Error('Save unavailable');});try{await h.open();click(h.doc,'Rename session');const form=h.doc.querySelector('[role=dialog] form'),input=form.querySelector('input');input.value='  ';form.dispatchEvent(new h.win.Event('submit',{cancelable:true}));await tick();assert.equal(h.calls.filter(c=>c.options.method==='PATCH').length,0);input.value='Keep input';form.dispatchEvent(new h.win.Event('submit',{cancelable:true}));await tick();assert.equal(input.value,'Keep input');assert.equal(h.doc.querySelector('.conversation-head h1').textContent,'First conversation');assert.match(form.querySelector('[role=alert]').textContent,/Save unavailable/);click(h.doc,'Cancel');assert.equal(h.doc.querySelector('[role=dialog]'),null);}finally{h.close();}
});
test('one compact authenticated header replaces branding and retains back navigation',async()=>{
 const h=await setup();try{assert.equal(h.doc.querySelector('.topbar .brand'),null);await h.open();const header=h.doc.querySelector('.conversation-head');assert.ok(header);assert.equal(header.querySelector('h1').textContent,'First conversation');assert.ok(header.querySelector('[aria-label="Rename session"]'));assert.equal(h.doc.querySelectorAll('.conversation-head').length,1);click(h.doc,'Back to chats');await tick();assert.ok(h.doc.querySelector('.session-list'));}finally{h.close();}
});

for(const trigger of ['completion','watcher'])test(`native automatic title refreshes an active untitled session after ${trigger} without navigation`,async()=>{
 let title=null;
 const h=await setup(p=>{
  if(p.startsWith('/sessions?'))return {items:[{id:'s1',title:null}],total:1};
  if(p==='/sessions/s1')return {id:'s1',title};
 },{items:[],run:trigger==='completion'?{id:'r1',session_id:'s1',status:'running',input:'Hello'}:null});
 try{
  click(h.doc,'Untitled conversation');await tick();
  const header=h.doc.querySelector('.conversation-head'),textarea=h.doc.querySelector('textarea');
  textarea.value='Retained draft';assert.equal(header.querySelector('h1').textContent,'Untitled conversation');
  title='Generated native title';
  if(trigger==='completion')h.streams[0].emit('done',{status:'completed'});
  else {const entry=[...h.timers.entries()].find(([,v])=>v.delay===30000);h.timers.delete(entry[0]);entry[1].fn();}
  await tick();
  assert.equal(header.querySelector('h1').textContent,title);assert.equal(header.querySelector('h1').title,title);
  assert.equal(h.doc.querySelector('.conversation-head'),header);assert.equal(h.doc.querySelector('textarea'),textarea);assert.equal(textarea.value,'Retained draft');
 }finally{h.close();}
});

for(const departure of ['rename','navigation'])test(`late native title refresh cannot overwrite ${departure}`,async()=>{
 let pending=null;
 const h=await setup((p,o)=>{
  if(o.method==='PATCH')return {id:'s1',title:'Manual title'};
  if(p==='/sessions/s1')return pending?pending.promise:{id:'s1',title:null};
 });
 try{
  await h.open();pending=deferred();h.win.dispatchEvent(new h.win.Event('focus'));await tick();
  assert.ok(h.calls.some(c=>c.path==='/sessions/s1'),'watcher refresh reads native metadata');
  if(departure==='rename'){
   click(h.doc,'Rename session');const form=h.doc.querySelector('[role=dialog] form');form.querySelector('input').value='Manual title';form.dispatchEvent(new h.win.Event('submit',{cancelable:true}));await tick();
  }else{click(h.doc,'Back to chats');await tick();click(h.doc,'Second conversation');await tick();}
  pending.resolve({id:'s1',title:'Stale generated title'});await tick();
  assert.equal(h.doc.querySelector('.conversation-head h1').textContent,departure==='rename'?'Manual title':'Second conversation');
 }finally{h.close();}
});
