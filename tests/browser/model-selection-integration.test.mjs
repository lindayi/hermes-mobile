import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(r=>setTimeout(r,15));
async function fixture(post,catalogue=null,snapshot=null){
 const dom=new JSDOM('<div id="app"></div>',{url:'https://example.test/hermes/'}),win=dom.window,doc=win.document,requests=[];
 const streams=[];if(snapshot)win.EventSource=class{constructor(){this.listeners={};streams.push(this);}addEventListener(n,f){this.listeners[n]=f;}close(){}emit(n,data){this.listeners[n]?.({data:JSON.stringify(data)});}};
 const options={available:true,default:{model:'fixture-a',provider:'fixture'},models:[{id:'fixture-a',provider:'fixture',label:'Fixture A',reasoning_efforts:['low','high']},{id:'fixture-b',provider:'fixture',label:'Fixture B',reasoning_efforts:[]}]};
 const api={clear(){},async request(path,params={}){requests.push({path,params});if(path==='/auth/me')return {user:{id:'owner',role:'owner',status:'ready'}};if(path.startsWith('/sessions?'))return {items:[{id:'s',title:'Settings fixture'}],total:1};if(path.endsWith('/model-options'))return catalogue ? catalogue(options,requests.filter(r=>r.path.endsWith('/model-options')).length) : options;if(path.includes('/messages'))return snapshot || {items:[],run:null};if(path==='/runs')return post(params.body,requests.filter(r=>r.path==='/runs').length);return {items:[]};}};
 const app=await mountApp(doc,api,win);doc.querySelector('[aria-label="Settings fixture"]').click();await tick();
 function control(name){return [...doc.querySelectorAll('button,select')].find(b=>b.getAttribute('aria-label')===name || b.textContent.trim()===name);}
 async function choose(){const model=control('Model');assert.ok(model && !model.hidden,'model picker is integrated');assert.equal(model.tagName,'SELECT');assert.equal(model.disabled,false);const option=[...model.options].find(o=>o.textContent==='Fixture A (fixture) — high');assert.ok(option);model.value=option.value;model.dispatchEvent(new win.Event('change',{bubbles:true}));await tick();assert.equal(doc.querySelector('[role=dialog],.dialog-overlay'),null);}
 function draft(text){const area=doc.querySelector('textarea');area.value=text;area.dispatchEvent(new win.Event('input',{bubbles:true}));}
 return {app,doc,win,requests,streams,control,choose,draft,close(){app.destroy();win.close();}};
}
test('known active run permits next-turn model selection without retargeting its request',async()=>{
 const h=await fixture(body=>({id:'next',session_id:'s',status:'completed',input:body.input,output:'Next answer'}),null,{items:[],run:{id:'active',session_id:'s',status:'running',input:'Already admitted'}});try{
  assert.equal(h.control('Model').disabled,false,'next-turn selection is available during admitted run');
  await h.choose();assert.equal(h.requests.filter(r=>r.params.method==='POST').length,0,'choosing does not retarget active model or submit a run');
  h.streams[0].emit('done',{status:'completed',output:'Prior answer'});await tick();h.draft('Next message');h.control('Send message').click();await tick();
  const sent=h.requests.find(r=>r.path==='/runs').params.body;assert.deepEqual(sent.selection,{model:'fixture-a',provider:'fixture',reasoning_effort:'high'});assert.equal(sent.input,'Next message');
 }finally{h.close();}
});
test('selected model and advertised effort are snapshotted into the next run request',async()=>{
 const h=await fixture(body=>({id:'r',session_id:'s',status:'completed',input:body.input,output:'Synthetic saved answer'}));try{
  await h.choose();h.draft('One request');h.control('Send message').click();await tick();
  const sent=h.requests.find(r=>r.path==='/runs').params.body;
  assert.deepEqual(sent.selection,{model:'fixture-a',provider:'fixture',reasoning_effort:'high'});
  assert.equal(sent.input,'One request');assert.ok(sent.idempotency_key);
 }finally{h.close();}
});
test('uncertain retry preserves the original selection and key across chat reentry',async()=>{
 const h=await fixture((body,count)=>{if(count===1)throw new Error('Response lost');return {id:'r',session_id:'s',status:'completed',input:body.input,output:'Synthetic saved answer'};});try{
  await h.choose();h.draft('Retry once');h.control('Send message').click();await tick();
  assert.equal(h.control('Model').disabled,true,'uncertain selection cannot be retargeted');
  h.control('Back to chats').click();await tick();h.control('Settings fixture').click();await tick();
  assert.equal(h.control('Model').disabled,true,'reentry restores the uncertain-attempt lock');
  h.control('Send message').click();await tick();
  const sent=h.requests.filter(r=>r.path==='/runs').map(r=>r.params.body);assert.equal(sent.length,2);assert.deepEqual(sent[1],sent[0]);
 }finally{h.close();}
});
test('definitive validation rejection unlocks selection instead of trapping an invalid retry',async()=>{
 const h=await fixture((body,count)=>{if(count===1)throw Object.assign(new Error('Selection unavailable'),{status:422});return {id:'r',session_id:'s',status:'completed',input:body.input,output:'Synthetic saved answer'};});try{
  await h.choose();h.draft('Correct selection');h.control('Send message').click();await tick();
  assert.equal(h.control('Model').disabled,false,'rejected admission can be corrected');
  await h.choose();h.control('Send message').click();await tick();
  const sent=h.requests.filter(r=>r.path==='/runs').map(r=>r.params.body);assert.equal(sent.length,2);assert.notEqual(sent[1].idempotency_key,sent[0].idempotency_key);
 }finally{h.close();}
});

for(const status of [400,422])test(`late ${status} after same-chat reentry unlocks current picker without losing draft or posting`,async()=>{
 let reject;const h=await fixture(()=>new Promise((_,r)=>{reject=r;}));try{
  await h.choose();h.draft('Preserve this draft');h.control('Send message').click();await tick();
  h.control('Back to chats').click();await tick();h.control('Settings fixture').click();await tick();
  assert.equal(h.control('Model').disabled,true);
  reject(Object.assign(new Error('Selection unavailable'),{status}));await tick();
  assert.equal(h.win.sessionStorage.getItem('hermes:owner:attempt:s'),null);
  assert.equal(h.control('Model').disabled,false,'current picker must discover cleared shared attempt');
  assert.equal(h.doc.querySelector('textarea').value,'Preserve this draft');
  assert.equal(h.requests.filter(r=>r.path==='/runs').length,1);
  await h.choose();
 }finally{h.close();}
});

for(const otherAccount of [false,true])test(`late rejection preserves ${otherAccount?'different account':'newer attempt'} lock and draft`,async()=>{
 let reject;const h=await fixture((body,count)=>count===1?new Promise((_,r)=>{reject=r;}):Promise.reject(new Error('Response lost')));try{
  await h.choose();h.draft('Old draft');h.control('Send message').click();await tick();
  h.control('Back to chats').click();await tick();
  if(otherAccount){
   const oldRow=h.control('Settings fixture');
   h.app.state.user={id:'other',role:'owner',status:'ready'};
   oldRow.click();await tick();assert.equal(h.doc.querySelector('textarea'),null,'the previous account row cannot open a conversation');
   h.doc.querySelector('.nav-item[data-view="chats"]').click();await tick();
  }
  h.control('Settings fixture').click();await tick();h.draft('New draft');h.control('Send message').click();await tick();
  const key=`hermes:${otherAccount?'other':'owner'}:attempt:s`,saved=h.win.sessionStorage.getItem(key);
  reject(Object.assign(new Error('Old rejection'),{status:422}));await tick();
  assert.equal(h.win.sessionStorage.getItem(key),saved);assert.ok(saved);
  assert.equal(h.control('Model').disabled,true);
  assert.equal(h.doc.querySelector('textarea').value,'New draft');
  assert.equal(h.requests.filter(r=>r.path==='/runs').length,2);
 }finally{h.close();}
});

test('saved selection awaiting reentry catalogue validation cannot silently send default',async()=>{
 const h=await fixture(body=>({id:'r',status:'completed',input:body.input}), (options,count)=>count===1?options:new Promise(()=>{}));try{
  await h.choose();h.control('Back to chats').click();await tick();h.control('Settings fixture').click();await tick();
  h.draft('Wait for validation');h.control('Send message').click();await tick();
  assert.equal(h.requests.filter(r=>r.path==='/runs').length,0,'saved choice must be checked before a new admission');
  assert.equal(h.doc.querySelector('textarea').value,'Wait for validation');
 }finally{h.close();}
});

test('default model choice leaves the legacy run body unchanged',async()=>{
 const h=await fixture(body=>({id:'r',session_id:'s',status:'completed',input:body.input,output:'Synthetic saved answer'}));try{
  h.draft('Default request');h.control('Send message').click();await tick();
  assert.deepEqual(Object.keys(h.requests.find(r=>r.path==='/runs').params.body).sort(),['idempotency_key','input','session_id']);
 }finally{h.close();}
});
