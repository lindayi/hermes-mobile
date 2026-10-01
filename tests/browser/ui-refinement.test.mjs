import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(r=>setTimeout(r,5));
const button=(doc,name)=>[...doc.querySelectorAll('button')].find(b=>b.getAttribute('aria-label')===name || b.textContent===name);
async function setup({messages=[]}={}){
 const win=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/',pretendToBeVisual:true}).window,doc=win.document,calls=[];
 const app=await mountApp(doc,{clear(){},async request(path,options={}){calls.push({path,options});if(path==='/auth/me')return {user:{id:'owner',status:'ready'}};if(path.startsWith('/sessions?'))return {deletion_available:true,items:[{id:'s1',title:'First chat',run_status:'idle'},{id:'s2',title:'Second chat',run_status:'idle'}],total:61};if(path.includes('/messages'))return {items:messages,run:null};return {items:[]};}},win);
 return {win,doc,calls,app,close(){app.destroy();win.close();}};
}
function pointer(h,node,type,x,y,id=1){const e=new h.win.Event(type,{bubbles:true,cancelable:true});Object.assign(e,{clientX:x,clientY:y,pointerId:id,button:0,isPrimary:true});node.dispatchEvent(e);return e;}
function swipe(h,row,dx,dy=0,cancel=false){pointer(h,row,'pointerdown',200,100);const move=pointer(h,row,'pointermove',200+dx,100+dy);pointer(h,row,cancel?'pointercancel':'pointerup',200+dx,100+dy);return move;}
test('deliberate swipe reveals only one guarded Delete; vertical intent, cancellation and drag clicks never navigate or delete',async()=>{
 const h=await setup();try{
 const rows=[...h.doc.querySelectorAll('.session-row')],remove=h.doc.querySelector('[data-delete-id=s1]');assert.equal(remove.hidden,true,'Delete is not permanently visible');assert.equal(remove.textContent,'Delete');assert.equal(h.doc.querySelector('.session-entry svg path[d^="M4 6h16"]'),null);
 assert.match(rows[0].getAttribute('aria-describedby'),/session-action-help/);assert.match(h.doc.getElementById('session-action-help').textContent,/swipe.*left.*actions/i);
 assert.equal(swipe(h,rows[0],-8).defaultPrevented,false);assert.equal(remove.hidden,true);
 assert.equal(swipe(h,rows[0],-90,130).defaultPrevented,false);assert.equal(remove.hidden,true);
 pointer(h,rows[0],'pointerdown',200,100);pointer(h,rows[0],'pointermove',195,125);assert.equal(pointer(h,rows[0],'pointermove',50,130).defaultPrevented,false,'vertical intent stays locked');pointer(h,rows[0],'pointerup',50,130);assert.equal(remove.hidden,true);
 swipe(h,rows[0],-100,0,true);assert.equal(remove.hidden,true,'cancelled gesture does not reveal');rows[0].click();await tick();assert.equal(h.doc.querySelector('.messages'),null,'cancelled drag click suppressed');
 assert.equal(swipe(h,rows[0],-150).defaultPrevented,true);assert.equal(remove.hidden,false);rows[0].click();await tick();assert.equal(h.doc.querySelector('.messages'),null,'drag synthetic click suppressed');assert.equal(h.doc.querySelector('[role=dialog]'),null,'full swipe never confirms or deletes');
 swipe(h,rows[1],-60);assert.equal(remove.hidden,true);assert.equal(h.doc.querySelector('[data-delete-id=s2]').hidden,false);
 swipe(h,rows[1],70);assert.equal(h.doc.querySelector('[data-delete-id=s2]').hidden,true);
 button(h.doc,'Conversation actions: First chat').click();assert.equal(remove.hidden,false);h.doc.dispatchEvent(new h.win.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(remove.hidden,true);
 rows[0].dispatchEvent(new h.win.KeyboardEvent('keydown',{key:'F10',shiftKey:true,bubbles:true,cancelable:true}));assert.equal(remove.hidden,false);h.doc.body.dispatchEvent(new h.win.Event('pointerdown',{bubbles:true}));assert.equal(remove.hidden,true);
 rows[0].dispatchEvent(new h.win.MouseEvent('contextmenu',{bubbles:true,cancelable:true}));assert.equal(remove.hidden,false);remove.click();await tick();assert.ok(h.doc.querySelector('[role=dialog]'));assert.match(h.doc.querySelector('[role=dialog]').textContent,/First chat/);button(h.doc,'Cancel').click();assert.equal(h.calls.some(c=>c.options.method==='DELETE'),false);
 }finally{h.close();}
});
test('public context compression is folded process history with safe full text; genuine human lookalikes stay human',async()=>{
 const text='[Context compression] retained native envelope <img src=x onerror=alert(1)>\n\n'+'Full summary '.repeat(500);
 const h=await setup({messages:[{role:'tool',kind:'context_compression',name:'Context compression',status:'completed',content:text},{role:'user',content:text},{role:'tool',name:'terminal',status:'completed',content:'Normal tool'}]});try{
 button(h.doc,'First chat').click();await tick();const card=h.doc.querySelector('details.context-compression');assert.ok(card,'distinct compact process disclosure');assert.equal(card.open,false);assert.equal(card.querySelector('summary').textContent,'Context compressionCompleted');assert.ok(card.querySelector('.markdown').textContent.includes('Full summary '.repeat(499)));assert.equal(card.querySelector('img,script'),null);assert.equal(card.querySelector('.message-author'),null);assert.equal(h.doc.querySelectorAll('.user-message').length,1);assert.match(h.doc.querySelector('.user-message').textContent,/You/);assert.equal(h.doc.querySelectorAll('details.tool-activity').length,1,'normal tools are not merged into compression');
 }finally{h.close();}
});
test('filter dropdown shares New chat row, restores focus, and preserves typed query while resetting pagination',async()=>{
 const h=await setup();try{
 const filter=button(h.doc,'Filter conversations');assert.ok(filter,'icon filter trigger replaces permanent segments');assert.equal(filter.parentElement,button(h.doc,'New chat').parentElement);assert.equal(filter.parentElement,button(h.doc,'Search conversations').parentElement);assert.ok(filter.querySelector('svg'));assert.equal(filter.getAttribute('aria-expanded'),'false');
 const menu=h.doc.querySelector('#conversation-filter');assert.equal(menu.hidden,true);assert.deepEqual([...menu.querySelectorAll('[data-kind]')].map(b=>b.dataset.kind),['chats','all','cron','tests']);assert.equal(menu.querySelector('[aria-pressed=true]').dataset.kind,'chats');
 filter.click();assert.equal(menu.hidden,false);menu.dispatchEvent(new h.win.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(menu.hidden,true);assert.equal(h.doc.activeElement,filter);
 button(h.doc,'Next').click();await tick();button(h.doc,'Search conversations').click();h.doc.querySelector('[name=search]').value='typed & exact';button(h.doc,'Filter conversations').click();button(h.doc,'Cron').click();await tick();
 const url=new URL(h.calls.filter(c=>c.path.startsWith('/sessions?')).at(-1).path,'https://x');assert.equal(url.searchParams.get('kind'),'cron');assert.equal(url.searchParams.get('q'),'typed & exact');assert.equal(url.searchParams.get('offset'),'0');assert.equal(h.doc.querySelector('[name=search]').value,'typed & exact');assert.equal(h.doc.activeElement,button(h.doc,'Filter conversations'));assert.match(button(h.doc,'Filter conversations').title,/Cron/);
 button(h.doc,'Filter conversations').click();h.doc.body.dispatchEvent(new h.win.Event('pointerdown',{bubbles:true}));assert.equal(h.doc.querySelector('#conversation-filter').hidden,true);
 }finally{h.close();}
});
