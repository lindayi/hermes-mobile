import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(r=>setTimeout(r,20));
const tool=(id)=>({role:'tool',name:'terminal',tool_call_id:id,status:'success',summary:`pytest tests/${id}.py`});
async function fixture(initial,older){
 const win=new JSDOM('<div id="app"></div>',{url:'https://example.com/hermes/',pretendToBeVisual:true}).window,doc=win.document,calls=[];
 const app=await mountApp(doc,{clear(){},async request(p){calls.push(p);if(p==='/auth/me')return {user:{id:'u',status:'ready'}};if(p.startsWith('/sessions?'))return {items:[{id:'s',title:'Chat'}],total:1};if(p.includes('/messages'))return p.includes('latest=true')?initial:older;return {items:[]};}},win);
 doc.querySelector('[aria-label=Chat]').click();await tick();return {win,doc,calls,close(){app.destroy();win.close();}};
}
for(const divider of [null,{role:'user',content:'Next question'},{role:'assistant',channel:'commentary',content:'Public progress between tools'}])test(`older seam merges only adjacent tools, preserves expanded existing card (${divider?.role||'tools'})`,async()=>{
 const h=await fixture({items:[tool('b'),tool('c')],offset:100,total:102,run:null},{items:[{role:'user',content:'Earlier question'},tool('a'),...(divider?[divider]:[])],offset:0,total:102,run:null});try{
  const old=h.doc.querySelector('.tool-activity');old.open=true;
  [...h.doc.querySelectorAll('button')].find(b=>b.textContent==='Load older messages').click();await tick();
  const cards=h.doc.querySelectorAll('.messages > .tool-activity');assert.equal(cards.length,divider?2:1);assert.equal(old.open,true);assert.equal(old.isConnected,true);
  assert.deepEqual([...h.doc.querySelectorAll('.tool-summary')].map(e=>e.textContent),['pytest tests/a.py','pytest tests/b.py','pytest tests/c.py']);
  assert.ok(h.calls.some(p=>p.includes('latest=true')&&p.includes('turn_boundary=true')),'initial page requests user-turn boundary');assert.ok(h.calls.some(p=>p.includes('offset=0')&&p.includes('turn_boundary=true')),'older page requests user-turn boundary');
 }finally{h.close();}
});
test('expanded server offset drives next page; capped turn has an honest continuation hint',async()=>{
 const h=await fixture({items:[tool('b')],offset:150,total:151,run:null,turn_boundary:{truncated:true}},{items:[tool('a')],offset:20,total:151,run:null,turn_boundary:{truncated:false}});try{
  assert.equal(h.doc.querySelector('.turn-continuation')?.hidden,false);assert.match(h.doc.querySelector('.turn-continuation').textContent,/turn continues earlier/i);
  [...h.doc.querySelectorAll('button')].find(b=>b.textContent==='Load older messages').click();await tick();
  assert.equal(h.doc.querySelector('.turn-continuation').hidden,true);
  [...h.doc.querySelectorAll('button')].find(b=>b.textContent==='Load older messages').click();await tick();
  assert.ok(h.calls.some(p=>p.includes('limit=20')&&p.includes('offset=0')),'use actual expanded offset, not nominal fifty');
 }finally{h.close();}
});
