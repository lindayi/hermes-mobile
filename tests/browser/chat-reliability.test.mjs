import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(resolve=>setTimeout(resolve,10));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function click(doc,name){const el=[...doc.querySelectorAll('button')].find(el=>el.textContent.trim()===name || el.getAttribute('aria-label')===name);assert.ok(el,name);el.click();}
async function harness({snapshot,request:override,storage=true,events=true}={}){
 const dom=new JSDOM('<div id="app"></div>',{url:'https://lindayi.me/hermes/'}),win=dom.window,doc=win.document;
 const streams=[],calls=[],timers=new Map();let timerId=0;
 win.setTimeout=(fn,delay)=>{const id=++timerId;timers.set(id,{fn,delay});return id;};win.clearTimeout=id=>timers.delete(id);
 class Events {constructor(url){this.url=url;this.listeners={};streams.push(this);}addEventListener(name,fn){this.listeners[name]=fn;}close(){this.closed=true;}emit(name,data={},id=''){this.listeners[name]?.({data:JSON.stringify(data),lastEventId:id});}open(){this.onopen?.();this.listeners.open?.({});}error(){this.onerror?.({});}}
 win.EventSource=events ? Events : undefined;
 if(!storage)for(const name of ['localStorage','sessionStorage'])Object.defineProperty(win,name,{get(){throw new Error('storage disabled');}});
 let current=snapshot ?? {items:[],run:{id:'r1',session_id:'s1',input:'My accepted prompt',status:'running'}};
 const request=async(path,options={})=>{calls.push({path,options});const value=await override?.(path,options);if(value!==undefined)return value;
  if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
  if(path.startsWith('/sessions?'))return {items:[{id:'s1',title:'Chat one'},{id:'s2',title:'Chat two'}],total:2};
  if(path.includes('/messages'))return path.includes('/s2/') ? {items:[],run:{id:'r2',session_id:'s2',input:'Second prompt',status:'running'}} : current;
  if(path==='/runs/r1')return current.run ?? {id:'r1',session_id:'s1',input:'My accepted prompt',status:'completed',output:'Saved answer'};
  if(path==='/runs/r2')return {id:'r2',session_id:'s2',input:'Second prompt',status:'running'};
  throw new Error('Unexpected request '+path);
 };
 const app=await mountApp(doc,{request,clear(){}},win);
 return {doc,win,app,streams,calls,timers,setSnapshot(value){current=value;},async open(name='Chat one'){click(doc,name);await tick();},async navigate(){click(doc,'Chats');await tick();},async fireTimer(){const entry=timers.entries().next().value;assert.ok(entry,'reconciliation timer scheduled');timers.delete(entry[0]);entry[1].fn();await tick();},close(){app.destroy();dom.window.close();}};
}

test('late history failure cannot overwrite the currently open chat notice',async()=>{
 const history=deferred();const h=await harness({request:path=>path.startsWith('/sessions/s1/messages') ? history.promise : undefined});try{
  await h.open();await h.navigate();await h.open('Chat two');
  history.reject(new Error('STALE HISTORY ERROR'));await tick();
  assert.doesNotMatch(h.doc.querySelector('.notice').textContent,/STALE HISTORY ERROR/);assert.match(h.doc.querySelector('.conversation-head').textContent,/Chat two/);
 }finally{h.close();}
});

test('unresolved run keeps sending blocked instead of implying a safe retry',async()=>{
 const h=await harness({snapshot:{items:[],run:{id:'r1',session_id:'s1',input:'Unresolved action',status:'unknown'}}});try{
  await h.open();assert.equal(h.doc.querySelector('[aria-label="Send message"]'),null);assert.equal(h.doc.querySelector('[aria-label="Run outcome unknown"]').disabled,true);assert.match(h.doc.querySelector('.notice').textContent,/Outcome unknown/);
 }finally{h.close();}
});

test('completed server history ignores stale local run pointer without duplicate messages',async()=>{
 const h=await harness({snapshot:{items:[{role:'user',content:'Repeat'},{role:'assistant',content:'First'},{role:'user',content:'Repeat'},{role:'assistant',content:'Second'}],run:null}});try{
  h.win.localStorage.setItem('hermes:u:run:s1','stale');await h.open();
  assert.equal(h.doc.querySelectorAll('.user-message').length,2);assert.equal(h.doc.querySelectorAll('.assistant-message').length,2);assert.equal(h.streams.length,0);assert.equal(h.win.localStorage.getItem('hermes:u:run:s1'),null);
 }finally{h.close();}
});

test('double send and explicit retry preserve the same action identity',async()=>{
 const first=deferred();let posts=0;const h=await harness({snapshot:{items:[],run:null},request:(path,options)=>{if(path==='/runs'){posts++;return posts===1 ? first.promise : {id:'r1',session_id:'s1',input:options.body.input,status:'running'};}}});try{
  await h.open();h.doc.querySelector('textarea').value='Once';click(h.doc,'Send message');click(h.doc,'Send message');h.doc.querySelector('.composer').dispatchEvent(new h.win.Event('submit',{cancelable:true}));await tick();assert.equal(posts,1);
  first.reject(new Error('Response lost'));await tick();click(h.doc,'Send message');await tick();
  const sent=h.calls.filter(c=>c.path==='/runs');assert.equal(sent.length,2);assert.equal(sent[0].options.body.idempotency_key,sent[1].options.body.idempotency_key);assert.equal(h.doc.querySelectorAll('.user-message').length,1);
 }finally{h.close();}
});

test('stalled status reads are abortable and navigation cancels all recovery work',async()=>{
 let signal;const h=await harness({request:(path,options)=>{if(path==='/runs/r1'){signal=options.signal;return new Promise((resolve,reject)=>signal?.addEventListener('abort',()=>reject(Object.assign(new Error('Aborted'),{name:'AbortError'}))));}}});try{
  await h.open();h.streams[0].error();await h.fireTimer();
  assert.ok(signal,'status recovery has an abort signal');
  assert.ok(h.timers.size,'stalled request has a deadline');
  await h.navigate();assert.equal(signal.aborted,true);assert.equal(h.timers.size,0);
  const count=h.calls.length;h.win.dispatchEvent(new h.win.Event('pageshow'));await tick();assert.equal(h.calls.length,count);
 }finally{h.close();}
});

test('lost POST response is reconciled by run identity without retaining an already accepted draft',async()=>{
 let attempt;const h=await harness({snapshot:{items:[],run:null},request:(path,options)=>{if(path==='/runs'){attempt=options.body;throw new Error('Response lost');}}});try{
  await h.open();const input=h.doc.querySelector('textarea');input.value='Accepted once';input.dispatchEvent(new h.win.Event('input'));click(h.doc,'Send message');await tick();
  h.setSnapshot({items:[],run:{id:'r1',session_id:'s1',input:attempt.input,idempotency_key:attempt.idempotency_key,status:'running'}});
  await h.navigate();await h.open();
  assert.equal(h.doc.querySelector('textarea').value,'','accepted retry draft must not invite duplicate sending');
  assert.equal(h.win.sessionStorage.getItem('hermes:u:attempt:s1'),null);
  assert.equal(h.doc.querySelectorAll('.user-message').length,1);
  assert.equal(h.calls.filter(c=>c.path==='/runs').length,1);
 }finally{h.close();}
});

test('late stop acknowledgement cannot regress completed status or warn another chat',async()=>{
 const stop=deferred();const h=await harness({request:path=>path.endsWith('/stop') ? stop.promise : undefined});try{
  await h.open();click(h.doc,'Stop run');await tick();h.streams[0].emit('done',{status:'completed',output:'Finished'});
  stop.resolve({status:'stopping'});await tick();
  assert.match(h.doc.querySelector('.live-message [role=status]').textContent,/completed/);
  assert.doesNotMatch(h.doc.querySelector('.notice').textContent,/Stop requested/);
 }finally{h.close();}
});

test('typing the next draft during a pending send does not lose the new text',async()=>{
 const post=deferred();const h=await harness({snapshot:{items:[],run:null},request:path=>path==='/runs' ? post.promise : undefined});try{
  await h.open();const input=h.doc.querySelector('textarea');input.value='First';click(h.doc,'Send message');await tick();
  input.value='Next draft';input.dispatchEvent(new h.win.Event('input'));
  post.resolve({id:'r1',session_id:'s1',input:'First',status:'running'});await tick();
  assert.equal(input.value,'Next draft');assert.equal(h.win.sessionStorage.getItem('hermes:u:draft:s1'),'Next draft');
 }finally{h.close();}
});

test('late failed send does not put a stale error into a different chat',async()=>{
 const post=deferred();const h=await harness({snapshot:{items:[],run:null},request:path=>path==='/runs' ? post.promise : undefined});try{
  await h.open();h.doc.querySelector('textarea').value='First';click(h.doc,'Send message');await tick();
  await h.navigate();await h.open('Chat two');post.reject(new Error('STALE NETWORK ERROR'));await tick();
  assert.doesNotMatch(h.doc.querySelector('.notice').textContent,/STALE NETWORK ERROR/);
 }finally{h.close();}
});

test('late legacy run lookup cannot close the newly opened chat stream',async()=>{
 const lookup=deferred();const h=await harness({snapshot:{items:[]},request:path=>path==='/runs/r1' ? lookup.promise : undefined});try{
  h.win.localStorage.setItem('hermes:u:run:s1','r1');await h.open();
  await h.navigate();await h.open('Chat two');const active=h.streams[0];
  lookup.resolve({id:'r1',session_id:'s1',input:'Older',status:'completed',output:'OLD RESULT'});await tick();
  assert.notEqual(active.closed,true);assert.doesNotMatch(h.doc.body.textContent,/OLD RESULT/);
 }finally{h.close();}
});

for(const mode of ['unsupported','wake','silent'])test(`read-only recovery handles ${mode} stream and cleans up`,async()=>{
 let finished=false;const h=await harness({events:mode!=='unsupported',request:path=>path==='/runs/r1' && finished ? {id:'r1',session_id:'s1',status:'completed',output:'Recovered without stream'} : undefined});try{
  await h.open();finished=true;
  if(mode==='wake'){h.win.dispatchEvent(new h.win.Event('pageshow'));await tick();}else await h.fireTimer();
  assert.equal(h.doc.querySelector('.live-message .message-body').textContent,'Recovered without stream');
  assert.equal(h.timers.size,0);
  assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);
 }finally{h.close();}
});

test('read-only recovery rejects expired auth and removes stale missing run references',async()=>{
 for(const status of [401,404]){
  const h=await harness({request:path=>{if(path==='/runs/r1')throw Object.assign(new Error('Unavailable'),{status});}});try{
   await h.open();h.streams[0].error();await h.fireTimer();
   assert.equal(h.timers.size,0);assert.equal(h.streams[0].closed,true);
   if(status===401)assert.equal(h.doc.querySelector('.messages'),null);
   else {assert.match(h.doc.querySelector('.notice').textContent,/no longer available/i);assert.equal(h.win.localStorage.getItem('hermes:u:run:s1'),null);assert.equal(h.doc.querySelector('[aria-label="Send message"]').disabled,false);}
  }finally{h.close();}
 }
});

test('interrupted stream reconciles terminal output without reopening or resubmission',async()=>{
 let finished=false;const h=await harness({request:path=>path==='/runs/r1' && finished ? {id:'r1',session_id:'s1',status:'completed',output:'Recovered final'} : undefined});try{
  await h.open();h.streams[0].emit('delta',{text:'Partial'},'1');finished=true;h.streams[0].error();
  await h.fireTimer();
  assert.equal(h.doc.querySelector('.live-message .message-body').textContent,'Recovered final');
  assert.equal(h.doc.querySelector('.notice').hidden,true);
  assert.equal(h.doc.querySelector('[aria-label="Send message"]').disabled,false);
  assert.equal(h.streams[0].closed,true);assert.equal(h.timers.size,0);
  assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);
 }finally{h.close();}
});

test('delayed send response cannot steal the stream after switching chats',async()=>{
 const post=deferred();const h=await harness({snapshot:{items:[],run:null},request:(path,options)=>path==='/runs' && options.method==='POST' ? post.promise : undefined});try{
  await h.open();h.doc.querySelector('textarea').value='Sent once';click(h.doc,'Send message');await tick();
  await h.navigate();await h.open('Chat two');const active=h.streams[0];
  post.resolve({id:'r1',session_id:'s1',input:'Sent once',status:'running'});await tick();
  assert.equal(h.streams.length,1,'late submit must not attach a detached view');
  assert.notEqual(active.closed,true);
  assert.equal(h.win.localStorage.getItem('hermes:u:run:s1'),'r1','accepted run is retained for its own chat');
  assert.equal(h.calls.filter(c=>c.path==='/runs').length,1);
 }finally{h.close();}
});

test('late events from a departed or completed run never touch a new view or stream',async()=>{
 const h=await harness();try{
  await h.open();const old=h.streams[0];await h.navigate();await h.open('Chat two');const active=h.streams[1];
  old.emit('done',{status:'failed',error:'STALE FAILURE'},'8');
  assert.notEqual(active.closed,true,'old final must not close new chat stream');
  assert.doesNotMatch(h.doc.body.textContent,/STALE FAILURE/);
  active.emit('done',{status:'completed',output:'Only answer'},'9');
  active.emit('delta',{text:'STALE DELTA'},'10');
  active.emit('tool',{name:'STALE TOOL',status:'running'},'11');
  assert.doesNotMatch(h.doc.body.textContent,/STALE DELTA|STALE TOOL/);
 }finally{h.close();}
});

for(const recovery of ['open','tool'])test(`connection warning clears on ${recovery} recovery, not unrelated errors`,async()=>{
 const h=await harness();try{
  await h.open();const events=h.streams[0];
  events.error();assert.match(h.doc.querySelector('.notice').textContent,/interrupted/i);
  if(recovery==='open')events.open();else events.emit('tool',{name:'todo',status:'success'},'1');
  assert.equal(h.doc.querySelector('.notice').hidden,true,'connection restored clears its stale warning');
  events.listeners.delta({data:'bad json',lastEventId:'2'});
  assert.match(h.doc.querySelector('.notice').textContent,/could not be read/);
  events.open();events.emit('tool',{name:'terminal',status:'success'},'3');
  assert.match(h.doc.querySelector('.notice').textContent,/could not be read/,'recovery must not erase a separate error');
 }finally{h.close();}
});

test('server snapshot restores accepted input and active run without browser storage',async()=>{
 const h=await harness({storage:false});try{
  await h.open();
  assert.equal(h.doc.querySelectorAll('.user-message').length,1);
  assert.match(h.doc.querySelector('.user-message').textContent,/My accepted prompt/);
  assert.equal(h.streams.length,1);
  h.streams[0].emit('tool',{name:'todo',status:'success'},'1');
  await h.navigate();await h.open();
  assert.equal(h.doc.querySelectorAll('.user-message').length,1);
  assert.equal(h.streams.length,2);
  assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0,'navigation never submits');
 }finally{h.close();}
});

test('REVIEW: old send acknowledgement preserves a newer attempt on same session',async()=>{
 const old=deferred(),ownAck=deferred();let posts=0;
 const h=await harness({snapshot:{items:[],run:null},request:(path,options)=>{if(path==='/runs'){posts++;if(posts===1)return old.promise;if(posts===3)return ownAck.promise;throw new Error('Second response lost');}}});try{
  await h.open();h.doc.querySelector('textarea').value='First';click(h.doc,'Send message');await tick();
  await h.navigate();await h.open();const input=h.doc.querySelector('textarea');input.value='Second';input.dispatchEvent(new h.win.Event('input'));click(h.doc,'Send message');await tick();
  const second=h.calls.filter(c=>c.path==='/runs')[1].options.body;
  old.resolve({id:'r0',session_id:'s1',input:'First',status:'completed',output:'Done first'});await tick();
  click(h.doc,'Send message');await tick();
  const retry=h.calls.filter(c=>c.path==='/runs')[2].options.body;
  assert.equal(retry.idempotency_key,second.idempotency_key,'late first response discarded second request idempotency key');
  assert.equal(input.value,'Second');assert.equal(h.win.sessionStorage.getItem('hermes:u:draft:s1'),'Second');
  assert.equal(JSON.parse(h.win.sessionStorage.getItem('hermes:u:attempt:s1')).idempotency_key,second.idempotency_key);
  ownAck.resolve({id:'r1',session_id:'s1',input:'Second',status:'completed',output:'Done second'});await tick();
  assert.equal(input.value,'');assert.equal(h.win.sessionStorage.getItem('hermes:u:draft:s1'),null);assert.equal(h.win.sessionStorage.getItem('hermes:u:attempt:s1'),null);
 }finally{h.close();}
});

test('REVIEW: approval notice survives connection error and recovery',async()=>{
 const h=await harness();try{
  await h.open();const events=h.streams[0];events.emit('approval',{},'1');
  assert.match(h.doc.querySelector('.approval-notice').textContent,/needs your review/);
  events.error();events.open();
  assert.match(h.doc.querySelector('.approval-notice').textContent,/needs your review/);
  events.listeners.delta({data:'bad json',lastEventId:'2'});
  events.error();events.error();events.open();
  assert.match(h.doc.querySelector('.notice').textContent,/could not be read/);assert.equal(h.doc.querySelector('.notice').classList.contains('error'),true);
 }finally{h.close();}
});

test('REVIEW: legacy unknown last_run keeps send locked without duplicate bubble',async()=>{
 const h=await harness({snapshot:{items:[{role:'assistant',content:'Existing history'}],run:null,last_run:{id:'r1',session_id:'s1',status:'unknown',error:'Unresolved'},snapshot:{mode:'legacy-unanchored',anchored:false}}});try{
  await h.open();assert.equal(h.doc.querySelector('[aria-label="Send message"]'),null);assert.equal(h.doc.querySelector('[aria-label="Run outcome unknown"]').disabled,true);
  assert.match(h.doc.querySelector('.notice').textContent,/unknown|Unresolved/i);
  assert.equal(h.doc.querySelectorAll('.message').length,1);assert.equal(h.doc.querySelectorAll('.live-message').length,0);assert.equal(h.streams.length,0);
 }finally{h.close();}
});

test('REVIEW: repeated stream errors respect status recovery backoff',async()=>{
 const h=await harness();try{
  await h.open();h.streams[0].error();await h.fireTimer();
  assert.ok([...h.timers.values()].every(t=>t.delay>=15000));
  h.streams[0].error();
  assert.ok([...h.timers.values()].every(t=>t.delay>=15000),'second stream error reset status polling to 1 second');
  for(let i=0;i<4;i++){h.streams[0].error();await h.fireTimer();assert.equal(h.timers.size,1);assert.equal([...h.timers.values()][0].delay,30000);}
  assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);
 }finally{h.close();}
});
