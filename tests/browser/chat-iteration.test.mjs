import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(r=>setTimeout(r,5));
function click(doc,label){const el=[...doc.querySelectorAll('button')].find(x=>x.getAttribute('aria-label')===label || x.textContent.trim()===label);assert.ok(el,label);el.click();}
async function setup(){
 const dom=new JSDOM('<div id="app"></div>',{url:'https://example.com/hermes/'}),win=dom.window,doc=win.document,calls=[],streams=[];let user='u';
 win.setTimeout=()=>1;win.clearTimeout=()=>{};
 class Events{constructor(){this.listeners={};streams.push(this);}addEventListener(n,f){this.listeners[n]=f;}emit(n,d,id=''){this.listeners[n]?.({data:JSON.stringify(d),lastEventId:id});}close(){}}
 win.EventSource=Events;
 const app=await mountApp(doc,{clear(){},async request(path,options={}){calls.push({path,options});if(path==='/auth/me')return {user:{id:user,status:'ready'}};if(path.startsWith('/sessions?'))return {items:[{id:'s1',title:'Conversation'}],total:90};if(path.includes('/messages'))return {items:[],run:{id:'r1',session_id:'s1',status:'running',input:'Hello'}};return {status:'stopping',items:[]};}},win);
 return {doc,win,app,calls,streams,setUser(id){user=id;},async open(){click(doc,'Conversation');await tick();},close(){app.destroy();win.close();}};
}
test('monochrome controls keep accessible names and decorative icons hidden',async()=>{
 const h=await setup();try{
  for(const node of h.doc.querySelectorAll('.nav-icon,.session-symbol'))assert.ok(node.querySelector('svg[aria-hidden=true]'),'navigation uses a monochrome SVG');
  assert.doesNotMatch(h.doc.querySelector('.session-run-status').textContent,/\p{Emoji_Presentation}|\uFE0F/u);
  await h.open();
  for(const name of ['Back to chats','Rename session','Expand editor']){
   const button=h.doc.querySelector(`[aria-label="${name}"]`);assert.ok(button,name);
   const svg=button.querySelector('svg[aria-hidden=true]');assert.ok(svg,name+' has a decorative SVG');assert.equal(svg.getAttribute('stroke'),'currentColor');assert.equal(svg.getAttribute('focusable'),'false');
  }
  h.streams[0].emit('tool',{name:'tool',status:'success'});
  assert.equal(h.doc.querySelector('.tool-status-symbol').textContent,'✓');
  assert.ok(h.doc.querySelector('summary .disclosure-icon[aria-hidden=true]'));
  assert.doesNotMatch(h.doc.querySelector('.disclosure-icon').textContent,/\p{Emoji_Presentation}|\uFE0F/u);
 }finally{h.close();}
});
test('history search starts collapsed, focuses on open and clears on Escape without hidden filtering',async()=>{
 const h=await setup();try{
  const toggle=()=>h.doc.querySelector('[aria-label="Search conversations"][aria-expanded]');
  assert.ok(toggle(),'search disclosure exists');assert.equal(toggle().getAttribute('aria-expanded'),'false');assert.equal(h.doc.querySelector('.search').hidden,true);
  toggle().click();const input=h.doc.querySelector('[name=search]');assert.equal(h.doc.querySelector('.search').hidden,false);assert.equal(h.doc.activeElement,input);
  input.value='needle';h.doc.querySelector('.search').dispatchEvent(new h.win.Event('submit',{cancelable:true}));await tick();click(h.doc,'Next');await tick();
  assert.equal(toggle().getAttribute('aria-expanded'),'true');assert.equal(h.doc.querySelector('[name=search]').value,'needle');
  h.doc.querySelector('[name=search]').dispatchEvent(new h.win.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));await tick();
  const url=new URL(h.calls.at(-1).path,'https://x');assert.equal(url.searchParams.get('q'),'');assert.equal(url.searchParams.get('offset'),'0');assert.equal(h.doc.querySelector('.search').hidden,true);assert.equal(h.doc.activeElement,toggle());
  toggle().click();h.setUser('other');await h.app.start();assert.equal(toggle().getAttribute('aria-expanded'),'false');assert.equal(h.doc.querySelector('.search').hidden,true);
 }finally{h.close();}
});
test('history rows omit generic chat decoration but retain meaningful run status',async()=>{
 const h=await setup();try{const row=h.doc.querySelector('.session-row');assert.equal(row.querySelector('.session-symbol'),null);assert.equal(row.firstElementChild.className,'session-info');assert.equal(row.querySelector('strong').textContent,'Conversation');assert.ok(row.querySelector('.source'));assert.equal(row.querySelector('.session-run-status').getAttribute('aria-label'),'Status unavailable');}finally{h.close();}
});
test('closing empty search on a later page resets the rendered page as well as state',async()=>{
 const h=await setup();try{click(h.doc,'Next');await tick();h.doc.querySelector('[aria-controls="conversation-search"]').click();click(h.doc,'Close search');await tick();const url=new URL(h.calls.at(-1).path,'https://x');assert.equal(url.searchParams.get('offset'),'0');assert.equal([...h.doc.querySelectorAll('button')].find(b=>b.textContent==='Previous').disabled,true);}finally{h.close();}
});
test('public text stays chronological between tool groups with authoritative final and scoped replay',async()=>{
 const h=await setup();try{await h.open();const e=h.streams[0];
  const replay=()=>{e.emit('tool',{name:'first',tool_call_id:'a',status:'running'},'1');e.emit('commentary',{text:'Public update',id:'c'},'2');e.emit('commentary',{text:'Public update',id:'c'},'3');e.emit('tool',{name:'second',tool_call_id:'b',status:'running'},'4');e.emit('delta',{text:'Ordinary public update'},'5');e.emit('tool',{name:'third',tool_call_id:'d',status:'running'},'6');e.emit('tool',{name:'first',tool_call_id:'a',status:'success'},'7');};replay();
  const live=h.doc.querySelector('.live-message');assert.equal(live.querySelectorAll('.activity-summary').length,2);assert.deepEqual([...live.querySelectorAll('.tool-activity,.activity-summary')].map(n=>n.className),['tool-activity','activity-summary','tool-activity','activity-summary','tool-activity']);assert.equal(live.querySelector('.tool-preview').dataset.status,'success');
  e.emit('delta',{channel:'analysis',text:'PRIVATE'},'8');e.emit('delta',{channel:'commentary',phase:'reasoning',text:'PRIVATE'},'8b');e.emit('commentary',{reasoning_content:'PRIVATE'},'9');e.emit('delta',{text:'Final'},'10');assert.doesNotMatch(live.textContent,/PRIVATE/);e.emit('done',{output:'Final'},'11');assert.equal(live.querySelector('.message-body').textContent,'Final');assert.doesNotMatch(live.textContent,/PRIVATE/);assert.equal(live.querySelectorAll('.tool-preview').length,3);
  click(h.doc,'Back to chats');await tick();await h.open();const fresh=h.streams[1];fresh.emit('commentary',{text:'Public update',id:'c'},'2');assert.equal(h.doc.querySelectorAll('.live-message .activity-summary').length,1);
 }finally{h.close();}
});
test('live stop replaces composer Send outside disclosure and restores Send on completion',async()=>{
 const h=await setup();try{await h.open();const stop=[...h.doc.querySelectorAll('button')].find(b=>b.getAttribute('aria-label')==='Stop run');const heading=h.doc.querySelector('.live-activity-heading');assert.ok(heading);assert.equal(stop.parentElement,heading);assert.equal(heading.querySelector('button'),stop);assert.equal(stop.closest('summary'),null);assert.equal(heading.querySelector('[role=status]').hidden,false);stop.click();assert.equal(stop.disabled,true);await tick();assert.equal(h.doc.querySelector('.tool-activity').open,false);h.streams[0].emit('done',{output:'Final'});assert.equal(h.doc.querySelector('[aria-label="Stop run"]'),null);assert.equal(stop.isConnected,false);assert.equal(h.doc.querySelector('[aria-label="Send message"]').disabled,false);}finally{h.close();}
});
test('session kind filter defaults to chats, resets paging, preserves search/back and resets for another account',async()=>{
 const h=await setup();try{
  assert.equal(new URL(h.calls.at(-1).path,'https://x').searchParams.get('kind'),'chats');
  const filter=h.doc.querySelector('[aria-label="Conversation filter"]');assert.ok(filter);assert.equal(filter.getAttribute('role'),'group');assert.deepEqual([...filter.querySelectorAll('button')].map(o=>o.textContent),['Chats','All','Cron','Tests']);assert.equal(filter.querySelector('[aria-pressed=true]').textContent,'Chats');assert.equal(filter.querySelector('svg,select'),null);
  h.doc.querySelector('[name=search]').value='needle';h.doc.querySelector('.search').dispatchEvent(new h.win.Event('submit',{cancelable:true}));await tick();click(h.doc,'Next');await tick();
  const next=h.doc.querySelector('[aria-label="Conversation filter"]');next.querySelector('[data-kind=cron]').click();await tick();
  let url=new URL(h.calls.at(-1).path,'https://x');assert.equal(url.searchParams.get('kind'),'cron');assert.equal(url.searchParams.get('offset'),'0');assert.equal(url.searchParams.get('q'),'needle');
  await h.open();click(h.doc,'Back to chats');await tick();assert.equal(h.doc.querySelector('.session-filter [aria-pressed=true]').textContent,'Cron');
  h.setUser('other');await h.app.start();assert.equal(h.doc.querySelector('.session-filter [aria-pressed=true]').textContent,'Chats');assert.equal(h.doc.querySelector('[name=search]').value,'');
 }finally{h.close();}
});
