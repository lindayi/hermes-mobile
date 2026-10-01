import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(resolve=>setTimeout(resolve,10));
const button=(doc,label)=>doc.querySelector(`[aria-label="${label}"]`);

async function setup(source){
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/',pretendToBeVisual:true});
 const win=dom.window,doc=win.document,streams=[];
 let attempt={id:'a',run_id:'r',idempotency_key:'k',input:'GUIDANCE',status:'accepted_unconfirmed',updated_at:1};
 class Events{
  constructor(){this.listeners={};streams.push(this);}
  addEventListener(name,fn){this.listeners[name]=fn;}
  emit(name,data,id=''){this.listeners[name]?.({data:JSON.stringify(data),lastEventId:id});}
  close(){}
 }
 win.EventSource=Events;
 const app=await mountApp(doc,{clear(){},async request(path,options={}){
  if(path==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(path.startsWith('/sessions?'))return {items:[{id:'s',title:'Fixture'}],total:1};
  if(path.includes('/messages'))return {items:[],run:{id:'r',session_id:'s',status:'running',input:'Original'}};
  if(path.endsWith('/controls'))return {steering:true,attempts:source==='controls'?[attempt]:[]};
  if(path.endsWith('/steer'))return attempt={...attempt,...options.body};
  return {items:[]};
 }},win);
 button(doc,'Fixture').click();await tick();
 if(source==='POST'){
  const textarea=doc.querySelector('textarea');textarea.value='GUIDANCE';textarea.dispatchEvent(new win.Event('input'));
  button(doc,'Steer current run').click();await tick();
 }
 return {doc,win,events:streams[0],attempt,close(){app.destroy();win.close();}};
}

const order=doc=>[...doc.querySelector('.live-message').children]
 .filter(node=>node.matches('.tool-activity,[data-guidance-key]'))
 .map(node=>node.dataset.guidanceKey?'GUIDANCE':[...node.querySelectorAll('.tool-name')].map(name=>name.textContent).join(','))
 .filter(Boolean);

for(const source of ['POST','controls'])for(const event of ['steering','steer']){
 test(`${source}-first guidance acquires its ${event} position once without duplicating or moving on later receipts`,async()=>{
  const h=await setup(source);
  try{
   const bubble=h.doc.querySelector('[data-guidance-key]');assert.ok(bubble,'acceptance is shown before delayed SSE');
   const e=h.events;
   e.emit('tool',{name:'BEFORE',tool_call_id:'before',status:'running'},'1');
   e.emit(event,h.attempt,'2');
   e.emit('tool',{name:'AFTER',tool_call_id:'after',status:'running'},'3');
   assert.deepEqual(order(h.doc),['BEFORE','GUIDANCE','AFTER']);
   assert.equal(h.doc.querySelector('[data-guidance-key]'),bubble,'reposition the original DOM node');
   // Earlier tool completion must still update its original group across the boundary.
   e.emit('tool',{name:'BEFORE',tool_call_id:'before',status:'success'},'4');
   e.emit(event,{...h.attempt,status:'not_delivered',updated_at:2},'5');
   e.emit(event,h.attempt,'6'); // A stale acceptance cannot regress status or move the bubble.
   e.emit(event,h.attempt,'2'); // A repeated SSE ID is also ignored.
   h.win.dispatchEvent(new h.win.Event('focus'));await tick();
   e.emit('tool',{name:'TAIL',tool_call_id:'tail',status:'running'},'7');
   assert.deepEqual(order(h.doc),['BEFORE','GUIDANCE','AFTER,TAIL'],'later receipts neither move guidance nor split tools again');
   assert.equal(h.doc.querySelectorAll('[data-guidance-key]').length,1);
   assert.equal(h.doc.querySelector('[data-guidance-key]'),bubble);
   assert.equal(bubble.querySelector('.guidance-status').textContent,'Not delivered');
   assert.equal(h.doc.querySelector('.tool-preview').dataset.status,'success');
  }finally{h.close();}
 });
}
