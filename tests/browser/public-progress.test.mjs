import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(r=>setTimeout(r,20));
test('saved and live public commentary is visible by default, ordered before tools, never private sidecars',async()=>{
 const win=new JSDOM('<div id="app"></div>',{url:'https://example.com/hermes/',pretendToBeVisual:true}).window,doc=win.document;let events;
 win.EventSource=class{constructor(){events=this;this.h={};}addEventListener(n,f){this.h[n]=f;}close(){}emit(n,data){this.h[n]?.({data:JSON.stringify(data)});}};
 const app=await mountApp(doc,{clear(){},async request(p){if(p==='/auth/me')return {user:{id:'u',status:'ready'}};if(p.startsWith('/sessions?'))return {items:[{id:'s',title:'Chat'}]};if(p.includes('/messages'))return {items:[{id:1,role:'user',content:'Question'},{id:2,role:'assistant',content:'',public_commentary:[{id:'commentary:2:0',content:'Public saved progress'}],reasoning_content:'PRIVATE SENTINEL',tool_calls:[{id:'t',function:{name:'terminal',arguments:'{"command":"pwd"}'}}]},{role:'assistant',channel:'commentary',content:'Earlier progress'}],run:{id:'r',session_id:'s',status:'running'}};return {items:[]};}},win);
 try{doc.querySelector('[aria-label=Chat]').click();await tick();
  assert.match(doc.querySelector('.messages').textContent,/Public saved progress/);
  assert.ok([...doc.querySelectorAll('.activity-summary')].every(e=>e.open));
  const children=[...doc.querySelector('.messages').children];assert.ok(children.findIndex(e=>e.textContent.includes('Public saved progress'))<children.findIndex(e=>e.matches('.tool-activity')));
  assert.equal(doc.querySelector('.tool-activity').open,false);
  events.emit('commentary',{text:'Live public update'});events.emit('commentary',{channel:'analysis',text:'PRIVATE LIVE'});
  const progress=[...doc.querySelectorAll('.activity-summary')].find(e=>e.textContent.includes('Live public update'));assert.ok(progress?.open);assert.doesNotMatch(doc.querySelector('.messages').textContent,/PRIVATE/);
 }finally{app.destroy();win.close();}
});
