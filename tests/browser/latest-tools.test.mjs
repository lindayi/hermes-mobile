import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(resolve=>setTimeout(resolve,10));
const activity=[
 {id:2,name:'commentary',data:{text:'Checking recorded work'}},
 {id:3,name:'tool',data:{event:'tool.started',tool:'terminal',summary:'terminal: pytest tests -q'}},
 {id:4,name:'tool',data:{event:'tool.completed',tool:'terminal',error:false}},
 {id:5,name:'commentary',data:{text:'Checking another source'}},
 {id:6,name:'tool',data:{event:'tool.started',tool:'read_file',summary:'read_file: docs/guide.md'}},
];
async function setup(status='completed',replay={run_id:'r',cursor:6,events:activity}){
 const dom=new JSDOM('<div id="app"></div>',{url:'https://example.test/hermes/'}),win=dom.window,doc=win.document,streams=[],requests=[];
 const metrics={reads:0};Object.defineProperty(win.HTMLElement.prototype,'scrollHeight',{get(){if(this.classList.contains('messages'))metrics.reads++;return 0;},configurable:true});
 class Events {constructor(){this.listeners={};streams.push(this);}addEventListener(n,fn){this.listeners[n]=fn;}close(){this.closed=true;}emit(n,data,id){this.listeners[n]?.({data:JSON.stringify(data),lastEventId:String(id)});}}
 win.EventSource=Events;
 const run={id:'r',session_id:'s',status,input:'Current turn',output:status==='completed'?'Final saved answer':null};
 const request=async(path,options={})=>{requests.push({path,options});if(path==='/auth/me')return {user:{id:'owner',status:'ready'}};if(path.startsWith('/sessions?'))return {items:[{id:'s',title:'Fixture'}],total:1};if(path.includes('/messages'))return {items:[],run,tool_replay:replay};if(path==='/runs/r')return run;return {items:[]};};
 const app=await mountApp(doc,{request,clear(){}},win);doc.querySelector('[aria-label=Fixture]').click();await tick();
 return {doc,streams,requests,metrics,close(){app.destroy();win.close();}};
}
test('terminal reentry seeds recorded tools and public chronology before final output without SSE',async()=>{
 const h=await setup();try{
  assert.equal(h.doc.querySelectorAll('.tool-preview').length,2,'recorded latest tools survive terminal early return');
  assert.equal(h.streams.length,0,'terminal history needs no live stream');
  const article=h.doc.querySelector('.live-message');
  assert.deepEqual([...article.querySelectorAll('details:not([hidden])')].map(n=>n.className),['activity-summary','tool-activity','activity-summary','tool-activity']);
  assert.equal(article.querySelector('.message-body').textContent,'Final saved answer');
  assert.equal(article.querySelectorAll('.tool-preview')[1].dataset.status,'unknown','unfinished tool is not falsely successful');
  assert.equal(h.doc.querySelectorAll('.user-message').length,1);
  assert.equal(h.requests.some(r=>r.options.method==='POST'),false);
 }finally{h.close();}
});
test('active snapshot seeds once, ignores old SSE through cursor and accepts new live events',async()=>{
 const h=await setup('running');try{
  assert.equal(h.doc.querySelectorAll('.tool-preview').length,2);
  const stream=h.streams[0];for(const e of activity)stream.emit(e.name,e.data,e.id);
  stream.emit('status',{status:'failed'},1);
  assert.equal(h.doc.querySelectorAll('.tool-preview').length,2);
  assert.equal(h.doc.querySelectorAll('.activity-summary').length,2);
  assert.notEqual(stream.closed,true,'old status cannot terminate the seeded current run');
  stream.emit('tool',{event:'tool.completed',tool:'read_file',error:false},7);
  assert.equal(h.doc.querySelectorAll('.tool-preview').length,2);
  assert.equal(h.doc.querySelectorAll('.tool-preview')[1].dataset.status,'success');
  stream.emit('tool',{event:'tool.started',tool:'web_search',summary:'web_search: evidence'},8);
  assert.equal(h.doc.querySelectorAll('.tool-preview').length,3);
 }finally{h.close();}
});
test('snapshot replay batches layout instead of reading geometry for every event',async()=>{
 const events=Array.from({length:80},(_,i)=>({id:i+1,name:'tool',data:{event:'tool.completed',tool:'terminal',summary:'terminal: pytest tests -q',error:false}}));
 const h=await setup('completed',{run_id:'r',cursor:80,events});try{
  assert.equal(h.doc.querySelectorAll('.tool-preview').length,80);
  assert.ok(h.metrics.reads<30,`batched replay has bounded layout reads, got ${h.metrics.reads}`);
 }finally{h.close();}
});

test('large replay keeps all tools, public delta chronology and private-text exclusion',async()=>{
 const events=[{id:1,name:'delta',data:{text:'Public introduction'}},{id:2,name:'delta',data:{text:'HIDDEN',channel:'analysis'}}];
 for(let i=0;i<1002;i++)events.push({id:i+3,name:'tool',data:{event:'tool.completed',tool:'terminal',summary:'terminal: pytest tests -q',error:false}});
 events.push({id:1005,name:'commentary',data:{text:'Public closing'}},{id:1006,name:'delta',data:{text:'HIDDEN',reasoning_content:'HIDDEN'}});
 const h=await setup('running',{run_id:'r',cursor:1006,events});try{
  assert.equal(h.doc.querySelectorAll('.tool-preview').length,1002);
  assert.match(h.doc.querySelector('.live-message').textContent,/Public introduction/);
  assert.match(h.doc.querySelector('.live-message').textContent,/Public closing/);
  assert.doesNotMatch(h.doc.body.textContent,/HIDDEN/);
  h.streams[0].emit('delta',{text:'Public introduction'},1);
  assert.equal(h.doc.querySelectorAll('.activity-summary').length,2);
  h.streams[0].emit('done',{status:'completed',output:'Final large answer'},1007);
  assert.equal(h.doc.querySelector('.live-message .message-body').textContent,'Final large answer');
  assert.ok(h.metrics.reads<30);
 }finally{h.close();}
});

test('foreign replay does not seed or suppress the current run stream',async()=>{
 const h=await setup('running',{run_id:'foreign',cursor:99,events:activity});try{
  assert.equal(h.doc.querySelectorAll('.tool-preview').length,0);
  h.streams[0].emit('tool',{event:'tool.started',tool:'terminal',summary:'terminal: pytest tests -q'},3);
  assert.equal(h.doc.querySelectorAll('.tool-preview').length,1);
 }finally{h.close();}
});
