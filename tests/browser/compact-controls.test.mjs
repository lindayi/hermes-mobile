import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(r=>setTimeout(r,5));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const control=(doc,name)=>[...doc.querySelectorAll('button')].find(b=>b.getAttribute('aria-label')===name || b.textContent.trim()===name);
async function setup({status=null,last=false,request}={}){
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/',pretendToBeVisual:true}),win=dom.window,doc=win.document,calls=[],streams=[];
 class Events{constructor(){this.listeners={};streams.push(this);}addEventListener(n,f){this.listeners[n]=f;}emit(n,data){this.listeners[n]?.({data:JSON.stringify(data)});}close(){this.closed=true;}}
 win.EventSource=Events;
 const run=status?{id:'r',session_id:'s',status,input:'Accepted'}:null;
 const app=await mountApp(doc,{clear(){},async request(p,o={}){calls.push({p,o});const v=await request?.(p,o);if(v!==undefined)return v;
 if(p==='/auth/me')return {user:{id:'fixture',status:'ready'}};
 if(p.startsWith('/sessions?'))return {items:[{id:'s',title:'Fixture'}],total:1};
 if(p.includes('/messages'))return {items:[],run:last?null:run,last_run:last?run:null};
 if(p==='/inbox')return {items:[{id:'read',read:true,title:'Read title',body:'Read body'},{id:'new',read:false,title:'Unread title',body:'Unread body'}]};
 if(p==='/approvals')return {items:[{id:'approval',title:'Approval title',action:'Review'}]};
 if(p.endsWith('/stop'))return {status:'stopping'};
 return {items:[]};}},win);
 return {doc,win,calls,streams,app,async open(){control(doc,'Fixture').click();await tick();},close(){app.destroy();win.close();}};
}
for(const status of ['queued','running','waiting_for_approval'])test(`${status}: Stop stays in Activity and never posts the draft`,async()=>{
 const h=await setup({status});try{await h.open();const b=control(h.doc,'Stop run');assert.equal(b.parentElement,h.doc.querySelector('.live-activity-heading'));assert.equal(b.closest('details'),null);assert.equal(b.textContent.trim(),'Stop');assert.equal(h.doc.querySelector('.composer [aria-label="Stop run"]'),null);assert.equal(control(h.doc,'Send message'),undefined);
 const input=h.doc.querySelector('textarea');input.value='Never dispatch this draft';input.dispatchEvent(new h.win.Event('input'));b.click();b.click();h.doc.querySelector('.composer').dispatchEvent(new h.win.Event('submit',{cancelable:true}));await tick();
 assert.deepEqual(h.calls.filter(c=>c.o.method==='POST'),[{p:'/runs/r/stop',o:{method:'POST',body:{}}}]);assert.equal(input.value,'Never dispatch this draft');assert.equal(b.disabled,true);assert.equal(b.getAttribute('aria-label'),'Stop run');
 h.streams[0].emit('done',{status:'cancelled'});const send=control(h.doc,'Send message');assert.ok(send);assert.equal(send.disabled,false);assert.equal(control(h.doc,'Stop run'),undefined);assert.equal(input.value,'Never dispatch this draft');
 }finally{h.close();}
});
for(const status of ['stopping','unknown'])for(const last of status==='unknown'?[false,true]:[false])test(`${status} snapshot stays locked (${last?'last':'active'} run)`,async()=>{
 const h=await setup({status,last});try{await h.open();assert.equal(control(h.doc,'Send message'),undefined);const b=h.doc.querySelector('.composer-bottom > button:last-child');assert.equal(b.disabled,true);b.click();h.doc.querySelector('.composer').dispatchEvent(new h.win.Event('submit',{cancelable:true}));assert.equal(h.calls.filter(c=>c.o.method==='POST').length,0);}finally{h.close();}
});
for(const status of ['completed','failed','cancelled'])test(`${status} restores Send and clears old stop dispatch`,async()=>{
 const h=await setup({status:'running',request:(p,o)=>p==='/runs'?{id:'new',status:'queued',input:o.body.input}:undefined});try{await h.open();h.streams[0].emit('done',{status});const b=control(h.doc,'Send message');assert.equal(b.disabled,false);h.doc.querySelector('textarea').value='New turn';h.doc.querySelector('.composer').dispatchEvent(new h.win.Event('submit',{cancelable:true}));await tick();assert.equal(h.calls.filter(c=>c.p==='/runs').length,1);assert.equal(h.calls.some(c=>c.p.endsWith('/stop')),false);assert.equal(control(h.doc,'Stop run').disabled,false);}finally{h.close();}
});
test('pending Send has no Stop dispatch before ID; new draft and editor selection survive',async()=>{
 const gate=deferred(),h=await setup({request:p=>p==='/runs'?gate.promise:undefined});try{await h.open();const input=h.doc.querySelector('textarea');input.value='First';control(h.doc,'Send message').click();await tick();assert.equal(control(h.doc,'Send message').disabled,true);assert.equal(control(h.doc,'Stop run'),undefined);input.value='Next draft';input.dispatchEvent(new h.win.Event('input'));control(h.doc,'Send message').click();h.doc.querySelector('.composer').dispatchEvent(new h.win.Event('submit',{cancelable:true}));gate.resolve({id:'r',status:'running'});await tick();input.focus();input.setSelectionRange(2,5);control(h.doc,'Expand editor').click();control(h.doc,'Done').click();assert.equal(h.doc.activeElement,input);assert.equal(input.selectionStart,2);assert.equal(input.selectionEnd,5);control(h.doc,'Stop run').click();await tick();assert.equal(input.value,'Next draft');assert.equal(h.calls.filter(c=>c.p==='/runs').length,1);}finally{h.close();}
});
test('read-only reconciliation disables Stop when another client has requested stopping',async()=>{
 const h=await setup({status:'running',request:p=>p==='/runs/r'?{id:'r',session_id:'s',status:'stopping'}:undefined});try{await h.open();h.win.dispatchEvent(new h.win.Event('pageshow'));await tick();assert.equal(control(h.doc,'Stop run').disabled,true);assert.equal(h.calls.filter(c=>c.o.method==='POST').length,0);}finally{h.close();}
});
test('read receipt dismiss is a top-level named close control outside the action row',async()=>{
 const h=await setup();try{control(h.doc,'Inbox').click();await tick();const b=control(h.doc,'Dismiss'),card=b.closest('.inbox-item');
 assert.equal(b.textContent,'×');assert.equal(b.title,'Dismiss');assert.equal(b.parentElement,card);assert.equal(b.closest('.actions'),null);
 const unreadDismiss=h.doc.querySelector('.unread [aria-label="Dismiss"]');assert.ok(unreadDismiss.hidden);assert.equal(unreadDismiss.closest('summary'),null);unreadDismiss.click();await tick();assert.equal(h.calls.some(c=>c.p==='/inbox/new/dismiss'),false,'unread close control remains non-actionable even on synthetic activation');assert.equal(h.doc.querySelector('.approval [aria-label="Dismiss"]'),null);
 b.click();await tick();assert.deepEqual(h.calls.find(c=>c.p==='/inbox/read/dismiss').o,{method:'POST',body:{}});
 }finally{h.close();}
});
test('unambiguous toolbar controls are named monochrome icons, not text labels',async()=>{
 const h=await setup();try{
 for(const name of ['Search conversations','New chat']){const b=control(h.doc,name);assert.ok(b.querySelector('svg[aria-hidden="true"][stroke="currentColor"]'),name);assert.equal(b.textContent,'');assert.equal(b.title,name);}
 control(h.doc,'Inbox').click();await tick();const refresh=control(h.doc,'Refresh');assert.ok(refresh.querySelector('svg'));assert.equal(refresh.textContent,'');assert.equal(refresh.title,'Refresh');
 assert.equal(control(h.doc,'Clear read').textContent,'Clear read','destructive bulk action retains words');
 }finally{h.close();}
});
