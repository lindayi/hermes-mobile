import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(r=>setTimeout(r,10));
async function fixture(items=[],run=null){
 const dom=new JSDOM('<div id="app"></div>',{url:'https://example.com/hermes/',pretendToBeVisual:true}),win=dom.window,doc=win.document,streams=[];
 class Events{constructor(){this.listeners={};streams.push(this);}addEventListener(n,f){this.listeners[n]=f;}emit(n,d,id=''){this.listeners[n]?.({data:JSON.stringify(d),lastEventId:id});}close(){}}
 win.EventSource=Events;
 const app=await mountApp(doc,{clear(){},async request(p){if(p==='/auth/me')return {user:{id:'fixture',status:'ready'}};if(p.startsWith('/sessions?'))return {items:[{id:'s',title:'Fixture'}],total:1};if(p.includes('/messages'))return {items,run};return {items:[]};}},win);
 [...doc.querySelectorAll('button')].find(b=>b.getAttribute('aria-label')==='Fixture').click();await tick();
 return {doc,win,streams,app,close(){app.destroy();win.close();}};
}
const row=(name,status)=>({role:'tool',name,status,summary:`${name} detail`});
test('programmatic expansion and a queued toggle after navigation never scroll the reader',async()=>{
 const h=await fixture([row('recorded','success')]);
 try{const messages=h.doc.querySelector('.messages'),card=h.doc.querySelector('.tool-activity');let writes=0;Object.defineProperty(messages,'scrollTop',{get:()=>10,set:()=>writes++});
 card.open=true;await tick();assert.equal(writes,0,'automatic disclosure changes do not imply user scroll intent');card.open=false;await tick();
 card.querySelector('summary').click();[...h.doc.querySelectorAll('button')].find(b=>b.getAttribute('aria-label')==='Back to chats').click();await tick();assert.equal(card.isConnected,false);assert.equal(writes,0,'deferred toggle cannot scroll detached conversation after route change');
 }finally{h.close();}
});
for(const [states,expected,index] of [
 [['failed','success'],'success',1],
 [['failed','running'],'running',1],
 [['running','success'],'running',0],
 [['running','running','success'],'running',1],
 [['success','failed'],'failed',1],
 [['failed','unknown'],'unknown',1],
 [['failed','completed'],'completed',1],
 [['failed','cancelled'],'cancelled',1],
])test(`history headline chooses ongoing/latest row: ${states}`,async()=>{
 const h=await fixture(states.map((s,i)=>row(`tool${i}`,s)));
 try{const card=h.doc.querySelector('.tool-activity');assert.equal(card.dataset.status,expected);assert.equal(card.querySelector('.activity-preview').textContent,`tool${index}: tool${index} detail`);assert.deepEqual([...card.querySelectorAll('.tool-preview')].map(r=>r.dataset.status),states);assert.match(card.querySelector('summary').getAttribute('aria-label'),new RegExp(expected==='success'?'Succeeded':expected==='unknown'?'Status unavailable':expected==='cancelled'?'Stopped':expected,'i'));}finally{h.close();}
});
test('live headline follows ongoing/latest rows without reordering, duplicate replay or claiming run success',async()=>{
 const h=await fixture([{role:'user',content:'Latest turn'}],{id:'r',session_id:'s',status:'running',input:'Latest turn'});
 try{const e=h.streams[0];e.emit('tool',{name:'first',tool_call_id:'a',status:'failed',summary:'first detail'},'1');e.emit('tool',{name:'second',tool_call_id:'b',status:'running',summary:'second detail'},'2');e.emit('tool',{name:'third',tool_call_id:'c',status:'success',summary:'third detail'},'3');
 const card=h.doc.querySelector('.live-message .tool-activity');assert.equal(card.dataset.status,'running');assert.equal(card.querySelector('.activity-preview').textContent,'second: second detail');
 e.emit('tool',{name:'second',tool_call_id:'b',status:'success'},'4');e.emit('tool',{name:'second',tool_call_id:'b',status:'success'},'4');assert.equal(card.dataset.status,'success');assert.equal(card.querySelector('.activity-preview').textContent,'third: third detail');assert.deepEqual([...card.querySelectorAll('.tool-name')].map(r=>r.textContent),['first','second','third']);assert.equal(h.doc.querySelector('.conversation-status').dataset.state,'running');
 e.emit('tool',{name:'fourth',tool_call_id:'d',status:'running',summary:'fourth detail'},'5');e.emit('done',{output:'Interrupted fixture'},'6');assert.equal(card.dataset.status,'unknown');assert.equal(card.querySelector('.activity-preview').textContent,'fourth: fourth detail');assert.equal(card.querySelector('.tool-preview').dataset.status,'failed');
 }finally{h.close();}
});

test('saved and live previews keep canonical tool identity and recorded duration in both views',async()=>{
 const h=await fixture([{role:'tool',name:'functions.read_file',summary:'read_file: docs/guide.md',status:'completed',duration:1.25}],{id:'r',session_id:'s',status:'running',input:'Fixture'});
 try{
  const saved=h.doc.querySelector('.messages > .tool-activity');
  assert.equal(saved.querySelector('.tool-name').textContent,'read_file');
  assert.equal(saved.querySelector('.activity-title').textContent,'1 tool','single-call count avoids repeating the tool name');
  assert.match(saved.querySelector('.activity-preview').textContent,/^read_file: .*docs\/guide\.md/);
  assert.match(saved.querySelector('.tool-status-label').textContent,/Completed.*1\.25s/);
  assert.match(saved.querySelector('summary').getAttribute('aria-label'),/read_file.*docs\/guide\.md.*1\.25s/);
  const e=h.streams[0];e.emit('tool',{name:'functions.web_search',tool_call_id:'web',status:'running',summary:'web_search: Toronto weather'},'1');
  e.emit('tool',{name:'functions.web_search',tool_call_id:'web',status:'completed',duration:0.5},'2');
  const live=h.doc.querySelector('.live-message .tool-activity');
  assert.equal(live.querySelectorAll('.tool-preview').length,1);
  assert.equal(live.querySelector('.tool-name').textContent,'web_search');
  assert.match(live.querySelector('.tool-status-label').textContent,/Completed.*0\.5s/);
  assert.match(live.querySelector('.activity-preview').textContent,/^web_search: Toronto weather.*0\.5s/);
  assert.doesNotMatch(live.textContent,/Succeeded/,'neutral completion does not imply success');
 }finally{h.close();}
});
