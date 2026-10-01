import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(r=>setTimeout(r,15));
test('ongoing uses a noninteractive progress ring, queued uses a clock, and completion removes motion',async()=>{
 const win=new JSDOM('<div id="app"></div>',{url:'https://example.com/hermes/',pretendToBeVisual:true}).window,doc=win.document;let events;
 win.EventSource=class{constructor(){events=this;this.handlers={};}addEventListener(n,f){this.handlers[n]=f;}emit(n,d){this.handlers[n]?.({data:JSON.stringify(d)});}close(){}};
 const app=await mountApp(doc,{clear(){},async request(p){if(p==='/auth/me')return {user:{id:'u',status:'ready'}};if(p.startsWith('/sessions?'))return {items:['running','queued','unknown'].map(s=>({id:s,title:s,run_status:s})),total:3};if(p.includes('/messages'))return {items:[{role:'tool',name:'terminal',status:'running',summary:'pytest'}],run:{id:'r',session_id:'running',status:'running'}};return {items:[]};}},win);
 try{
  const running=doc.querySelector('[data-state=running].session-run-status'),queued=doc.querySelector('[data-state=queued].session-run-status');
  assert.equal(running.getAttribute('aria-label'),'Running');assert.equal(queued.getAttribute('aria-label'),'Queued');assert.ok(running.querySelector('.progress-ring'),'ring replaces ambiguous ellipsis');assert.ok(queued.querySelector('svg'),'clock indicates waiting');assert.doesNotMatch(running.textContent+queued.textContent,/…|\.\.\./);assert.equal(running.getAttribute('tabindex'),null);
  doc.querySelector('[aria-label=running]').click();await tick();
  assert.ok(doc.querySelector('.tool-status-symbol .progress-ring'));assert.ok(doc.querySelector('.activity-symbol .progress-ring'));
  events.emit('tool',{name:'search_files',status:'running',summary:'Find references'});events.emit('tool',{name:'search_files',status:'success',summary:'Found references'});
  const card=doc.querySelector('.live-message .tool-activity');assert.equal(card.dataset.status,'success');assert.equal(card.querySelector('.activity-symbol .progress-ring'),null);
 }finally{app.destroy();win.close();}
});
