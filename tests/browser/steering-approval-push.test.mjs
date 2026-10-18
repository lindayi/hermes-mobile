import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(resolve=>setTimeout(resolve,10));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const waitFor=async(check,message='expected request was not made')=>{for(let i=0;i<50&&!check();i++)await tick();assert.ok(check(),message);};
const fakeCredential=()=>({id:'cred',rawId:new Uint8Array([1]).buffer,type:'public-key',getClientExtensionResults:()=>({}),response:{clientDataJSON:new Uint8Array([2]).buffer,authenticatorData:new Uint8Array([3]).buffer,signature:new Uint8Array([4]).buffer,userHandle:null}});
const approvalItem=()=>({id:'a',run_id:'r',request_id:'native-a',session_id:'s',run_session_id:'s',title:'Execute fixture',command:'echo fixture',target:'fixture target',expires_at:2000000000,executable:true,choices:['once','deny']});
for(const status of ['pending','expired'])test(`approval notification opens exact chat without deciding (${status})`,async()=>{
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document,calls=[];let scrolled;
 win.HTMLElement.prototype.scrollIntoView=function(){scrolled=this;};
 const app=await mountApp(doc,{clear(){},async request(p,o={}){calls.push({p,o});if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0};if(p==='/approvals')return {items:status==='pending'?[{...approvalItem(),run_id:'r',session_id:'s'}]:[]};if(p==='/approvals/a')return {...approvalItem(),run_id:'r',session_id:'s',status,expires_at:status==='expired'?1:2000000000};return {items:[]};}},win);
 try{
  assert.equal(doc.querySelector('.conversation')!==null,true);
  assert.equal(doc.querySelector('h1').textContent,'Exact chat title');
  assert.equal(calls.some(c=>c.p==='/inbox'),false,'chat-bound notification does not open Inbox');
  assert.equal(calls.filter(c=>c.p.endsWith('/decision')).length,0,'opening a push never decides');
  if(status==='pending'){
   const card=doc.querySelector('[data-approval-id="a"]');
   assert.ok(card,'pending approval is rendered inline in chat');
   assert.match(card.textContent,/Approval required/);
   assert.match(card.textContent,/echo fixture/);
   assert.match(card.textContent,/fixture target/);
   assert.ok([...card.querySelectorAll('button')].some(b=>b.textContent==='Approve once'));
   assert.ok([...card.querySelectorAll('button')].some(b=>b.textContent==='Deny'));
   assert.equal(scrolled,card,'push focuses the exact approval card');
  }else{
   assert.match(doc.querySelector('[data-approval-id="a"]').textContent,/Expired/);
   assert.equal(doc.querySelector('[data-approval-id="a"] button'),null);
  }
 }finally{app.destroy();win.close();}
});

test('a delayed pending approval detail is not actionable after its expiry',async()=>{
 const list=deferred(),detail=deferred(),calls=[];
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 const app=await mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0};
  if(p==='/approvals')return list.promise;
  if(p==='/approvals/a')return detail.promise;
  return {items:[]};
 }},win);
 try{
  await waitFor(()=>calls.includes('/approvals/a'));
  await new Promise(resolve=>setTimeout(resolve,100));
  detail.resolve({...approvalItem(),status:'pending',expires_at:Date.now()/1000-1});
  await waitFor(()=>doc.querySelector('[data-approval-id="a"]'));
  assert.match(doc.querySelector('[data-approval-id="a"] h3').textContent,/Expired/);
  assert.equal(doc.querySelector('[data-approval-id="a"] button'),null,'a delayed pending detail cannot expose expired controls');
  list.resolve({items:[]});
  assert.equal(calls.some(path=>path.endsWith('/decision')),false);
 }finally{app.destroy();win.close();}
});

test('an open approval expires on time without affecting a parallel approval',async()=>{
 const expires_at=Date.now()/1000+0.3,calls=[];
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 const app=await mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0};
  if(p==='/approvals')return {items:[
   {...approvalItem(),expires_at},
   {...approvalItem(),id:'b',request_id:'native-b',expires_at:Date.now()/1000+20}
  ]};
  if(p==='/approvals/a')return {...approvalItem(),status:'pending',expires_at};
  return {items:[]};
 }},win);
 try{
  await waitFor(()=>doc.querySelector('[data-approval-id="a"] button.primary'));
  await waitFor(()=>/Expired/.test(doc.querySelector('[data-approval-id="a"] h3')?.textContent || ''),
    'the open approval must expire at its deadline');
  assert.match(doc.querySelector('[data-approval-id="a"] h3').textContent,/Expired/);
  assert.equal(doc.querySelector('[data-approval-id="a"] button'),null);
  assert.equal(doc.querySelectorAll('[data-approval-id="b"] button').length,2);
  assert.equal(calls.some(path=>path.endsWith('/decision')),false);
 }finally{app.destroy();win.close();}
});

for(const distance of [10000,2147483647+10000])test(`early approval expiry callback rearms to its deadline (${distance})`,async t=>{
 let now=1800000000000,next=0;const timers=new Map(),calls=[];
 t.mock.method(Date,'now',()=>now);
 const item={...approvalItem(),status:'pending',expires_at:(now+distance)/1000};
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 win.setTimeout=(callback,delay)=>{const id=++next;timers.set(id,{callback,delay});return id;};
 win.clearTimeout=id=>timers.delete(id);
 const fire=delay=>{
  const entry=[...timers].find(([,timer])=>timer.delay===delay);
  assert.ok(entry,`expected an armed timer for ${delay}ms`);
  timers.delete(entry[0]);entry[1].callback();
 };
 const app=await mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0};
  if(p==='/approvals')return {items:[item]};
  if(p==='/approvals/a')return item;
  return {items:[]};
 }},win);
 try{
  await waitFor(()=>doc.querySelector('[data-approval-id="a"] button.primary'));
  const card=doc.querySelector('[data-approval-id="a"]');
  now+=distance>2147483647?2147483647:distance-1;
  fire(Math.min(distance,2147483647));
  assert.equal(doc.querySelector('[data-approval-id="a"]'),card);
  assert.equal(card.querySelector('h3').textContent,'Approval required');
  assert.equal(card.querySelectorAll('button').length,2);
  const remaining=item.expires_at*1000-now;
  now=item.expires_at*1000;fire(remaining);
  assert.equal(doc.querySelector('[data-approval-id="a"]'),card);
  assert.equal(card.querySelector('h3').textContent,'Expired');
  assert.equal(card.querySelector('button'),null);
  assert.equal(calls.some(path=>path.endsWith('/decision')),false);
  assert.equal(timers.size,0,'expiry and target timers are cleared');
 }finally{app.destroy();assert.equal(timers.size,0);win.close();}
});

for(const [status,label] of Object.entries({
 resolved:'Resolved',resolved_external:'No longer pending',cancelled:'Cancelled',
 sending:'Decision pending',unknown:'Decision status unknown'
}))test(`expired ${status} detail keeps its authoritative status on same-owner reentry`,async()=>{
 const calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 const app=await mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0};
  if(p==='/approvals')return {items:[]};
  if(p==='/approvals/a')return {...approvalItem(),status,expires_at:1};
  return {items:[]};
 }},win);
 try{
  await waitFor(()=>doc.querySelector('[data-approval-id="a"]'));
  assert.equal(doc.querySelector('[data-approval-id="a"] h3').textContent,label);
  assert.equal(doc.querySelector('[data-approval-id="a"] button'),null);
  doc.querySelector('[data-view="settings"]').click();
  await waitFor(()=>[...doc.querySelectorAll('button')].some(button=>button.textContent==='Sign out'));
  [...doc.querySelectorAll('button')].find(button=>button.textContent==='Sign out').click();
  await waitFor(()=>[...doc.querySelectorAll('button')].some(button=>button.textContent==='Sign in with a passkey'));
  await app.start();
  await waitFor(()=>doc.querySelector('[data-approval-id="a"]'));
  assert.equal(doc.querySelector('[data-approval-id="a"] h3').textContent,label);
  assert.equal(doc.querySelector('[data-approval-id="a"] button'),null);
  assert.equal(calls.some(path=>path.endsWith('/decision')),false);
 }finally{app.destroy();win.close();}
});

test('second detail revalidation cannot restore pending after an SSE approval is answered',async()=>{
 const stale=deferred(),revalidated=deferred(),refresh=deferred(),calls=[];
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 win.PublicKeyCredential=class {};
 win.navigator.credentials={get:async()=>fakeCredential()};
 class Events{
  constructor(){this.listeners={};win.activeStream=this;}
  addEventListener(name,fn){this.listeners[name]=fn;}
  emit(name,data){this.listeners[name]?.({data:JSON.stringify(data)});}
  close(){}
 }
 win.EventSource=Events;
 let detailReads=0,listReads=0;
 const app=await mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0,run:{id:'r',session_id:'s',status:'waiting_for_approval'}};
  if(p==='/approvals')return ++listReads===1?{items:[]}:refresh.promise;
  if(p==='/approvals/a')return ++detailReads===1?stale.promise:revalidated.promise;
  if(p==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
  if(p==='/auth/verify/finish')return {ok:true};
  if(p==='/approvals/a/decision')return {status:'resolved'};
  return {items:[]};
 }},win);
 try{
  await waitFor(()=>detailReads===1);await tick();
  stale.resolve({...approvalItem(),status:'pending'});
  await waitFor(()=>detailReads===2);
  win.activeStream.emit('approval',approvalItem());
  await waitFor(()=>doc.querySelector('[data-approval-id="a"] button.primary'));
  doc.querySelector('[data-approval-id="a"] button.primary').click();
  await waitFor(()=>doc.querySelector('[data-approval-id="a"] h3')?.textContent==='Resolved');
  revalidated.resolve({...approvalItem(),status:'pending'});await tick();await tick();
  const card=doc.querySelector('[data-approval-id="a"]');
  assert.equal(card.querySelector('h3').textContent,'Resolved');
  assert.equal(card.querySelector('button'),null);
  win.activeStream.emit('approval',approvalItem());await tick();
  assert.equal(card.querySelector('h3').textContent,'Resolved','handled replay remains final');
  assert.equal(calls.filter(path=>path==='/approvals/a/decision').length,1);
 }finally{app.destroy();refresh.resolve({items:[]});win.close();}
});

test('an unrelated focus refresh rechecks a pending target instead of stranding its placeholder',async()=>{
 const first=deferred(),second=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 let detailReads=0,listReads=0;
 const app=await mountApp(doc,{clear(){},async request(p){
 calls.push(p);
 if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
 if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
 if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
 if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0};
 if(p==='/approvals'){listReads++;return {items:[]};}
 if(p==='/approvals/a'){
  detailReads++;
  if(detailReads===1)return first.promise;
  if(detailReads===2)return second.promise;
  return {...approvalItem(),status:'pending'};
 }
 return {items:[]};
 }},win);
 try{
 await waitFor(()=>detailReads===1&&listReads===1);await tick();
 first.resolve({...approvalItem(),status:'pending'});
 await waitFor(()=>detailReads===2,'the empty accepted list should cause target detail revalidation');
 win.dispatchEvent(new win.Event('focus'));
 await waitFor(()=>listReads===2,'focus must perform an authenticated approval-list refresh');
 await tick();
 second.resolve({...approvalItem(),status:'pending'});
 await waitFor(()=>doc.querySelector('[data-approval-id="a"] button.primary'));
 assert.equal(detailReads,3,'a target-specific read after the unrelated empty-list refresh confirms the pending card');
 assert.equal(doc.querySelector('[data-approval-id="a"] h3').textContent,'Approval required');
 assert.equal(calls.filter(path=>path.endsWith('/decision')).length,0);
 }finally{app.destroy();win.close();}
});

test('repeated unrelated refreshes offer one explicit approval-status retry',async()=>{
 const details=[deferred(),deferred(),deferred()],calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 let detailReads=0,listReads=0;
 const app=await mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0};
  if(p==='/approvals'){listReads++;return {items:[]};}
  if(p==='/approvals/a'){
   detailReads++;
   return detailReads<=details.length?details[detailReads-1].promise:{...approvalItem(),status:'pending'};
  }
  return {items:[]};
 }},win);
 const refresh=async expected=>{
  win.dispatchEvent(new win.Event('focus'));
  await waitFor(()=>listReads===expected);
  await tick();
 };
 try{
  await waitFor(()=>detailReads===1&&listReads===1);await tick();
  details[0].resolve({...approvalItem(),status:'pending'});
  await waitFor(()=>detailReads===2);
  await refresh(2);
  details[1].resolve({...approvalItem(),status:'pending'});
  await waitFor(()=>detailReads===3);
  await refresh(3);
  details[2].resolve({...approvalItem(),status:'pending'});
  await waitFor(()=>doc.querySelector('.notification-unavailable h3')?.textContent==='Approval status changed'
    && [...doc.querySelectorAll('.notification-unavailable button')].some(button=>button.textContent==='Retry status'));
  assert.equal(detailReads,3,'unrelated list churn causes only one bounded target-specific retry');
  doc.querySelector('.notification-unavailable button').click();
  await waitFor(()=>doc.querySelector('[data-approval-id="a"] button.primary'));
  assert.equal(detailReads,4,'the explicit retry starts one fresh target read');
  assert.equal(doc.querySelector('[data-approval-id="a"] h3').textContent,'Approval required');
  assert.equal(calls.filter(path=>path.endsWith('/decision')).length,0);
 }finally{app.destroy();win.close();}
});

for(const staleStatus of ['pending','sending','unknown'])test(`an initial ${staleStatus} detail cannot replace an acknowledged approval`,async()=>{
 const detail=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 win.PublicKeyCredential=class {};
 win.navigator.credentials={get:async()=>fakeCredential()};
 const pending={...approvalItem(),status:'pending'};
 let detailReads=0,listItems=[pending],detailStatus='cancelled',detailFinished=false;
 const app=await mountApp(doc,{clear(){},async request(p){
 calls.push(p);
 if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
 if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
 if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
 if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0};
 if(p==='/approvals')return {items:listItems};
 if(p==='/approvals/a'){
  detailReads++;
  if(detailReads===1)return detail.promise.then(item=>{detailFinished=true;return item;});
  return {...pending,status:detailStatus};
 }
 if(p==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
 if(p==='/auth/verify/finish')return {ok:true};
 if(p==='/approvals/a/decision'){listItems=[];return {status:'resolved'};}
 return {items:[]};
 }},win);
 const reenter=async()=>{
 doc.querySelector('[data-view="settings"]').click();
 await waitFor(()=>[...doc.querySelectorAll('button')].some(button=>button.textContent==='Sign out'));
 [...doc.querySelectorAll('button')].find(button=>button.textContent==='Sign out').click();
 await waitFor(()=>[...doc.querySelectorAll('button')].some(button=>button.textContent==='Sign in with a passkey'));
 await app.start();
 };
 try{
 await waitFor(()=>detailReads===1);
 await waitFor(()=>doc.querySelector('[data-approval-id="a"] button.primary'));
 doc.querySelector('[data-approval-id="a"] button.primary').click();
 await waitFor(()=>doc.querySelector('[data-approval-id="a"] h3')?.textContent==='Resolved');
 detail.resolve({...pending,status:staleStatus});
 await waitFor(()=>detailFinished);
 await tick();
 assert.equal(doc.querySelector('[data-approval-id="a"] h3').textContent,'Resolved',
   `an older ${staleStatus} snapshot cannot overwrite the acknowledged decision`);
 assert.equal(calls.filter(path=>path==='/approvals/a/decision').length,1);
 detailStatus='sending';
 await reenter();
 await waitFor(()=>doc.querySelector('[data-approval-id="a"] h3')?.textContent==='Resolved',
   'same-owner reentry must preserve the acknowledged terminal state');
 detailStatus='cancelled';
 await reenter();
 await waitFor(()=>doc.querySelector('[data-approval-id="a"] h3')?.textContent==='Cancelled',
   'a fresh authenticated final detail must still replace the cached result');
 assert.equal(doc.querySelector('[data-approval-id="a"] button'),null);
 assert.equal(calls.filter(path=>path==='/approvals/a/decision').length,1);
 }finally{app.destroy();win.close();}
});

test('an expired timer from a closed chat cannot change a parallel session approval',async()=>{
 const expires_at=Date.now()/1000+0.3,calls=[];
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 let listReads=0;
 const app=await mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'First chat'};
  if(p==='/sessions/s2')return {id:'s2',title:'Second chat'};
  if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0};
  if(p.startsWith('/sessions/s2/messages'))return {items:[],total:0,offset:0,run:{id:'r2',session_id:'s2',status:'waiting_for_approval'}};
  if(p.startsWith('/sessions?'))return {items:[{id:'s2',title:'Second chat'}],total:1};
  if(p==='/approvals')return {items:++listReads===1
   ? [{...approvalItem(),expires_at}]
   : [{...approvalItem(),run_id:'r2',session_id:'s2',run_session_id:'s2',expires_at:Date.now()/1000+20}]};
  if(p==='/approvals/a')return {...approvalItem(),status:'pending',expires_at};
  return {items:[]};
 }},win);
 try{
  await waitFor(()=>doc.querySelector('[data-approval-id="a"] button.primary'));
  doc.querySelector('[data-view="chats"]').click();
  await waitFor(()=>doc.querySelector('[aria-label="Second chat"]'));
  doc.querySelector('[aria-label="Second chat"]').click();
  await waitFor(()=>doc.querySelector('[data-approval-id="a"] button.primary'));
  await waitFor(()=>Date.now()>expires_at*1000+50);
  const cards=doc.querySelectorAll('[data-approval-id="a"]');
  assert.equal(cards.length,1);
  assert.equal(cards[0].closest('.conversation').querySelector('h1').textContent,'Second chat');
  assert.equal(cards[0].querySelectorAll('button').length,2);
  assert.equal(calls.some(path=>path.endsWith('/decision')),false);
 }finally{app.destroy();win.close();}
});

test('approval expiry during a held passkey ceremony prevents the decision POST',async()=>{
 const credential=deferred(),calls=[],expires_at=Date.now()/1000+0.12;
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 win.PublicKeyCredential=class {};
 win.navigator.credentials={get:()=>credential.promise};
 const app=await mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0};
  if(p==='/approvals')return {items:[{...approvalItem(),expires_at}]};
  if(p==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
  if(p==='/auth/verify/finish')return {ok:true};
  if(p==='/approvals/a/decision')return {status:'resolved'};
  return {items:[]};
 }},win);
 try{
  await waitFor(()=>doc.querySelector('[data-approval-id="a"] button.primary'));
  doc.querySelector('[data-approval-id="a"] button.primary').click();
  await waitFor(()=>calls.includes('/auth/verify/options'));
  await waitFor(()=>Date.now()>expires_at*1000+20);
  credential.resolve(fakeCredential());
  await waitFor(()=>calls.includes('/auth/verify/finish'));
  await tick();await tick();
  assert.equal(calls.filter(path=>path.endsWith('/decision')).length,0);
  assert.match(doc.querySelector('[data-approval-id="a"] h3').textContent,/Expired/);
  assert.equal(doc.querySelector('[data-approval-id="a"] button'),null);
 }finally{app.destroy();win.close();}
});

test('an answered approval stays final on same-owner sign-in at the retained notification target',async()=>{
 const calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 win.PublicKeyCredential=class {};
 win.navigator.credentials={get:async()=>fakeCredential()};
 let listReads=0;
 const app=await mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0};
  if(p==='/approvals')return {items:++listReads===1?[approvalItem()]:[]};
  if(p==='/approvals/a')return {...approvalItem(),status:'pending'};
  if(p==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
  if(p==='/auth/verify/finish')return {ok:true};
  if(p==='/approvals/a/decision')return {status:'resolved'};
  return {items:[]};
 }},win);
 try{
  await waitFor(()=>doc.querySelector('[data-approval-id="a"] button.secondary'));
  doc.querySelector('[data-approval-id="a"] button.secondary').click();
  await waitFor(()=>calls.filter(path=>path.endsWith('/decision')).length===1);
  await tick();
  doc.querySelector('[data-view="settings"]').click();await tick();
  await waitFor(()=>doc.querySelector('button.danger'));
  [...doc.querySelectorAll('button')].find(button=>button.textContent==='Sign out').click();await tick();
  assert.ok([...doc.querySelectorAll('button')].some(button=>button.textContent==='Sign in with a passkey'));
  await app.start();await tick();
  const card=doc.querySelector('[data-approval-id="a"]');
  assert.ok(card,'the exact target remains visible after same-owner sign-in');
  assert.match(card.textContent,/Resolved/);
  assert.equal(card.querySelector('button'),null,'a remembered final approval has no decision controls');
  assert.equal(calls.filter(path=>path.endsWith('/decision')).length,1);
 }finally{app.destroy();win.close();}
});

test('a cached sending approval yields to fresh unknown on same-owner reentry',async()=>{
 const calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 let detailStatus='sending';
 const app=await mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0};
  if(p==='/approvals')return {items:[]};
  if(p==='/approvals/a')return {...approvalItem(),status:detailStatus};
  return {items:[]};
 }},win);
 const reenter=async()=>{
  doc.querySelector('[data-view="settings"]').click();
  await waitFor(()=>[...doc.querySelectorAll('button')].some(button=>button.textContent==='Sign out'));
  [...doc.querySelectorAll('button')].find(button=>button.textContent==='Sign out').click();
  await waitFor(()=>[...doc.querySelectorAll('button')].some(button=>button.textContent==='Sign in with a passkey'));
  await app.start();
 };
 try{
  await waitFor(()=>doc.querySelector('[data-approval-id="a"] h3')?.textContent==='Decision pending');
  assert.equal(doc.querySelector('[data-approval-id="a"] button'),null);
  detailStatus='unknown';
  await reenter();
  await waitFor(()=>doc.querySelector('[data-approval-id="a"] h3')?.textContent==='Decision status unknown');
  assert.equal(doc.querySelector('[data-approval-id="a"] button'),null);
  assert.equal(calls.filter(path=>path.endsWith('/decision')).length,0);
 }finally{app.destroy();win.close();}
});

for(const staleStatus of ['pending','sending','unknown'])test(`a held ${staleStatus} detail cannot clear an ambiguous decision on reentry`,async()=>{
 const detail=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 win.PublicKeyCredential=class {};
 win.navigator.credentials={get:async()=>fakeCredential()};
 const pending={...approvalItem(),status:'pending'};
 let detailReads=0,detailFinished=false,detailStatus=staleStatus;
 const app=await mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0};
  if(p==='/approvals')return {items:[pending]};
  if(p==='/approvals/a'){
   detailReads++;
   if(detailReads===1)return detail.promise.then(item=>{detailFinished=true;return item;});
   return {...approvalItem(),status:detailStatus};
  }
  if(p==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
  if(p==='/auth/verify/finish')return {ok:true};
  if(p==='/approvals/a/decision')throw Object.assign(new Error('Synthetic unavailable'),{status:503});
  return {items:[]};
 }},win);
 const reenter=async()=>{
  doc.querySelector('[data-view="settings"]').click();
  await waitFor(()=>[...doc.querySelectorAll('button')].some(button=>button.textContent==='Sign out'));
  [...doc.querySelectorAll('button')].find(button=>button.textContent==='Sign out').click();
  await waitFor(()=>[...doc.querySelectorAll('button')].some(button=>button.textContent==='Sign in with a passkey'));
  await app.start();
 };
 try{
  await waitFor(()=>detailReads===1);
  await waitFor(()=>doc.querySelector('[data-approval-id="a"] button.primary'));
  doc.querySelector('[data-approval-id="a"] button.primary').click();
  await waitFor(()=>calls.includes('/auth/verify/options'));
  await waitFor(()=>calls.includes('/approvals/a/decision'));
  await waitFor(()=>doc.querySelector('[data-approval-id="a"] h3')?.textContent==='Decision status unknown');
  assert.match(doc.querySelector('[data-approval-id="a"] .inline-error').textContent,/Synthetic unavailable/);
  detail.resolve({...approvalItem(),status:staleStatus});
  await waitFor(()=>detailFinished);
  await waitFor(()=>doc.querySelector('[data-approval-id="a"] h3')?.textContent==='Decision status unknown');
  assert.match(doc.querySelector('[data-approval-id="a"] .inline-error').textContent,/Synthetic unavailable/,
    `a stale ${staleStatus} detail cannot erase the ambiguous-decision error`);
  assert.equal(doc.querySelector('[data-approval-id="a"] button'),null,
    `a held ${staleStatus} detail cannot restore decision controls`);
  assert.equal(calls.filter(path=>path.endsWith('/decision')).length,1);
  detailStatus='resolved';
  await reenter();
  await waitFor(()=>doc.querySelector('[data-approval-id="a"] h3')?.textContent==='Resolved',
    'a later authenticated final detail recovers the uncertain state');
  assert.equal(doc.querySelector('[data-approval-id="a"] button'),null);
  assert.equal(calls.filter(path=>path.endsWith('/decision')).length,1);
 }finally{app.destroy();win.close();}
});

for(const revalidated of ['resolved','pending'])test(`empty approval list revalidates a delayed pending detail (${revalidated})`,async()=>{
 const stale=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 let detailReads=0;
 const app=await mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0};
  if(p==='/approvals')return {items:[]};
  if(p==='/approvals/a')return ++detailReads===1?stale.promise:{...approvalItem(),status:revalidated};
  return {items:[]};
 }},win);
 try{
  await waitFor(()=>calls.includes('/approvals/a'),`approval detail request missing: ${JSON.stringify(calls)}`);
  await tick();
  stale.resolve({...approvalItem(),status:'pending'});
  await waitFor(()=>detailReads===2);
  const card=doc.querySelector('[data-approval-id="a"]');
  assert.ok(card);
  if(revalidated==='resolved'){
   assert.match(card.textContent,/Resolved/);
   assert.equal(card.querySelector('button'),null);
  }else{
   assert.match(card.textContent,/Approval required/);
   assert.equal(card.querySelectorAll('button').length,2,'a genuinely new pending action after an empty snapshot remains actionable');
  }
  assert.equal(calls.filter(path=>path.endsWith('/decision')).length,0);
 }finally{app.destroy();win.close();}
});

test('automatic approval history shares manual loads and refocuses after relocation',async()=>{
 const older=deferred(),calls=[],scrolls=[];
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 win.HTMLElement.prototype.scrollIntoView=function(){scrolls.push(this);};
 const app=await mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p==='/sessions/s/messages?latest=true&turn_boundary=true')
   return {items:[{role:'user',content:'Newer request',run_id:'new',session_id:'s'}],total:11,offset:10};
  if(p==='/sessions/s/messages?limit=10&offset=0&turn_boundary=true')return older.promise;
  if(p==='/approvals')return {items:[approvalItem()]};
  if(p==='/approvals/a')return {...approvalItem(),status:'pending'};
  return {items:[]};
 }},win);
 try{
  await waitFor(()=>calls.includes('/sessions/s/messages?limit=10&offset=0&turn_boundary=true'));
  doc.querySelector('button[aria-label="Load older messages"]')?.click();
  [...doc.querySelectorAll('button')].find(button=>button.textContent==='Load older messages')?.click();
  assert.equal(calls.filter(path=>path==='/sessions/s/messages?limit=10&offset=0&turn_boundary=true').length,1,
    'manual and automatic requests share the same in-flight page');
  older.resolve({items:[{role:'user',content:'Original request',run_id:'r',session_id:'s'}],total:11,offset:0});
  await waitFor(()=>doc.querySelector('[data-approval-id="a"]')?.previousElementSibling?.dataset.historyRun==='r');
  const card=doc.querySelector('[data-approval-id="a"]');
  assert.ok(scrolls.filter(node=>node===card).length>=2,'the moved target is scrolled into view after automatic history restoration');
  assert.equal(doc.querySelectorAll('.user-message').length,2,'the concurrently requested history page is inserted once');
 }finally{app.destroy();win.close();}
});

test('opening a canonical chat normally discovers its pending compressed-session approval',async()=>{
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/'}),win=dom.window,doc=win.document;
 const item={...approvalItem(),session_id:'tip',run_session_id:'wa-1'};
 const app=await mountApp(doc,{clear(){},async request(p){
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p.startsWith('/sessions?'))return {items:[{id:'tip',title:'Canonical chat'}],total:1};
  if(p==='/sessions/tip/messages?latest=true&turn_boundary=true')return {items:[],total:0,offset:0};
  if(p==='/approvals')return {items:[item]};
  return {items:[]};
 }},win);
 try{
  doc.querySelector('.session-row[aria-label="Canonical chat"]').click();await tick();
  const card=doc.querySelector('[data-approval-id="a"]');
  assert.ok(card,'approval is discoverable without opening the notification');
  assert.match(card.textContent,/Approval required/);
  assert.equal(card.dataset.historySession,'tip');
  assert.equal(card.querySelectorAll('button').length,2);
 }finally{app.destroy();win.close();}
});

test('request notification loads older history and shows its exact run input and result',async()=>{
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document,calls=[];
 win.HTMLElement.prototype.scrollIntoView=function(){};
 const app=await mountApp(doc,{clear(){},async request(p,o={}){calls.push({p,o});if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};if(p==='/inbox/n/target')return {kind:'request',session_id:'s',run_id:'old-run',run_session_id:'s'};if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[{id:20,role:'assistant',content:'A newer response',run_id:'new-run',session_id:'s'}],offset:10,total:11};if(p==='/sessions/s/messages?limit=10&offset=0&turn_boundary=true')return {items:[{id:1,role:'user',content:'The original request',run_id:'old-run',session_id:'s'}],offset:0,total:11};if(p==='/runs/old-run')return {id:'old-run',session_id:'s',input:'The original request',status:'completed',output:'The exact result',created_at:1,updated_at:2};return {items:[]};}},win);
 try{
  const target=doc.querySelector('[data-notification-run="old-run"]');
  assert.ok(target);
  assert.match(target.textContent,/The original request/);
  assert.match(target.textContent,/The exact result/);
  assert.ok(calls.some(call=>call.p==='/sessions/s/messages?limit=10&offset=0&turn_boundary=true'));
  assert.ok(target.previousElementSibling?.textContent.includes('The original request'),'target is placed next to its original turn');
  assert.equal(doc.querySelector('h1').textContent,'Exact chat title');
  assert.equal(calls.some(call=>call.p==='/inbox'),false);
 }finally{app.destroy();win.close();}
});

test('request notification follows verified compression lineage',async()=>{
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document,calls=[];
 let focused;
 Object.defineProperty(win.HTMLElement.prototype,'scrollHeight',{configurable:true,get(){return this.classList?.contains('messages')?1000:0;}});
 Object.defineProperty(win.HTMLElement.prototype,'clientHeight',{configurable:true,get(){return this.classList?.contains('messages')?300:0;}});
 win.HTMLElement.prototype.scrollIntoView=function(){focused=this;const messages=this.closest('.messages');if(messages)messages.scrollTop=500;};
 const app=await mountApp(doc,{clear(){},async request(p,o={}){calls.push({p,o});if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};if(p==='/inbox/n/target')return {kind:'request',session_id:'tip',run_id:'old-run',run_session_id:'wa-1'};if(p==='/sessions/tip')return {id:'tip',title:'Exact chat title'};if(p==='/sessions/tip/messages?latest=true&turn_boundary=true')return {items:[],offset:0,total:0};if(p==='/runs/old-run')return {id:'old-run',session_id:'wa-1',input:'Original request',status:'completed',output:'The exact result',created_at:1,updated_at:2};return {items:[]};}},win);
 try{
  const target=doc.querySelector('[data-notification-run="old-run"]'),messages=doc.querySelector('.messages');
  assert.ok(target);
  assert.equal(focused,target);
  assert.equal(messages.scrollTop,500,'compressed run history must route to its authorized current chat');
 }finally{app.destroy();win.close();}
});

test('active run does not reset a request target and following resumes at the bottom',async()=>{
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 Object.defineProperty(win.HTMLElement.prototype,'scrollHeight',{configurable:true,get(){return this.classList?.contains('messages')?1000:0;}});
 Object.defineProperty(win.HTMLElement.prototype,'clientHeight',{configurable:true,get(){return this.classList?.contains('messages')?300:0;}});
 win.HTMLElement.prototype.scrollIntoView=function(){const messages=this.closest('.messages');if(messages)messages.scrollTop=500;};
 class Events{constructor(){this.listeners={};win.activeStream=this;}addEventListener(name,fn){this.listeners[name]=fn;}emit(name,data){this.listeners[name]?.({data:JSON.stringify(data)});}close(){}}
 win.EventSource=Events;
 const app=await mountApp(doc,{clear(){},async request(p){if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};if(p==='/inbox/n/target')return {kind:'request',session_id:'s',run_id:'old-run',run_session_id:'s'};if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[],offset:0,total:0,run:{id:'new-run',session_id:'s',status:'running',input:'Newer request'}};if(p==='/runs/old-run')return {id:'old-run',session_id:'s',input:'Original request',status:'completed',output:'The exact result',created_at:1,updated_at:2};return {items:[]};}},win);
 try{
  const messages=doc.querySelector('.messages');
  assert.equal(messages.scrollTop,500,'restoration must preserve the request target over the newer active run');
  messages.scrollTop=700;
  win.activeStream.emit('delta',{text:'More output'});
  assert.equal(messages.scrollTop,1000,'following resumes after the reader returns to the bottom');
 }finally{app.destroy();win.close();}
});

test('navigation during approval reconciliation cannot mount a stale chat',async()=>{
 const gate=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 const mounted=mountApp(doc,{clear(){},async request(p){calls.push(p);if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[],offset:0,total:0};if(p==='/approvals')return gate.promise;if(p==='/approvals/a')throw Object.assign(new Error('unavailable'),{status:404});return {items:[]};}},win);
 try{
  while(!calls.includes('/approvals'))await tick();
  doc.querySelector('[data-view="chats"]').click();
  await tick();
  gate.resolve({items:[]});
  const app=await mounted;await tick();
  assert.ok(doc.querySelector('.session-list'));
  assert.equal(doc.querySelector('.conversation'),null);
  app.destroy();
 }finally{win.close();}
});

test('late approval detail 401 cannot expire a newer route',async()=>{
 const gate=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 const mounted=mountApp(doc,{clear(){},async request(p){calls.push(p);if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[],offset:0,total:0};if(p==='/approvals')return {items:[]};if(p==='/approvals/a')return gate.promise;return {items:[]};}},win);
 try{
  while(!calls.includes('/approvals/a'))await tick();
  doc.querySelector('[data-view="chats"]').click();await tick();
  gate.reject(Object.assign(new Error('expired'),{status:401}));
  const app=await mounted;await tick();
  assert.equal(app.state.user?.id,'owner');
  assert.equal(doc.querySelector('.session-list')!==null,true);
  assert.equal(doc.querySelector('[aria-label="Sign in with a passkey"]'),null);
  app.destroy();
 }finally{win.close();}
});

test('unchanged approval survives a focus refresh during passkey verification',async()=>{
 const credential=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 win.PublicKeyCredential=class {};
 win.navigator.credentials={get:()=>credential.promise};
 const app=await mountApp(doc,{clear(){},async request(p){calls.push(p);if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[],total:0,offset:0};if(p==='/approvals')return {items:[approvalItem()]};if(p==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};if(p==='/auth/verify/finish')return {ok:true};if(p==='/approvals/a/decision')return {status:'resolved'};return {items:[]};}},win);
 try{
  doc.querySelector('[data-approval-id="a"] button').click();
  while(!calls.includes('/auth/verify/options'))await tick();
  win.dispatchEvent(new win.Event('focus'));await tick();
  credential.resolve(fakeCredential());await tick();await tick();
  assert.equal(calls.filter(path=>path==='/approvals/a/decision').length,1);
 }finally{app.destroy();win.close();}
});

test('successful approval acknowledgement wins over a concurrent pending-list removal',async()=>{
 const credential=deferred(),decision=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=dom.window.document;
 win.PublicKeyCredential=class {};
 win.navigator.credentials={get:()=>credential.promise};
 let approvalReads=0;
 const app=await mountApp(doc,{clear(){},async request(p,o={}){
  calls.push({p,o});
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[],total:0,offset:0};
  if(p==='/approvals')return {items:++approvalReads===1?[approvalItem()]:[]};
  if(p==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
  if(p==='/auth/verify/finish')return {ok:true};
  if(p==='/approvals/a/decision')return decision.promise;
  return {items:[]};
 }},win);
 try{
  const card=doc.querySelector('[data-approval-id="a"]');
  card.querySelector('button.primary').click();
  await waitFor(()=>calls.some(call=>call.p==='/auth/verify/options'));
  credential.resolve(fakeCredential());
  while(!calls.some(call=>call.p==='/approvals/a/decision'))await tick();
  win.dispatchEvent(new win.Event('focus'));await tick();
  assert.equal(card.querySelector('h3').textContent,'Decision pending');
  decision.resolve({status:'resolved'});await tick();
  assert.equal(calls.filter(call=>call.p==='/approvals/a/decision').length,1);
  assert.equal(card.querySelector('h3').textContent,'Resolved');
  assert.equal(card.querySelector('button'),null);
 }finally{app.destroy();win.close();}
});

for(const choice of ['once','deny'])test(`${choice} waits for passkey verification and sends no decision after cancellation`,async()=>{
 const credential=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 win.PublicKeyCredential=class {};
 win.navigator.credentials={get:()=>credential.promise};
 const app=await mountApp(doc,{clear(){},async request(p,o={}){
  calls.push({p,o});
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[],total:0,offset:0};
  if(p==='/approvals')return {items:[approvalItem()]};
  if(p==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
  if(p==='/approvals/a/decision')return {status:'resolved'};
  return {items:[]};
 }},win);
 try{
  doc.querySelector(`[data-approval-id="a"] button.${choice==='once'?'primary':'secondary'}`).click();
  await waitFor(()=>calls.some(call=>call.p==='/auth/verify/options'));
  assert.equal(calls.filter(call=>call.p==='/approvals/a/decision').length,0,
    'decision must wait for a successful passkey ceremony');
  credential.reject(Object.assign(new Error('cancelled'),{name:'NotAllowedError'}));
  await tick();await tick();
  const card=doc.querySelector('[data-approval-id="a"]');
  assert.match(card.querySelector('h3').textContent,/Approval required/);
  assert.doesNotMatch(card.textContent,/Decision pending/);
  assert.equal(card.querySelectorAll('button').length,2);
  assert.equal(calls.filter(call=>call.p==='/approvals/a/decision').length,0);
 }finally{app.destroy();win.close();}
});

for(const choice of ['once','deny'])test(`${choice} is sent only after passkey verification completes`,async()=>{
 const credential=deferred(),verified=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 win.PublicKeyCredential=class {};
 win.navigator.credentials={get:()=>credential.promise};
 const app=await mountApp(doc,{clear(){},async request(p,o={}){
  calls.push({p,o});
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[],total:0,offset:0};
  if(p==='/approvals')return {items:[approvalItem()]};
  if(p==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
  if(p==='/auth/verify/finish')return verified.promise;
  if(p==='/approvals/a/decision')return {status:'resolved'};
  return {items:[]};
 }},win);
 try{
  doc.querySelector(`[data-approval-id="a"] button.${choice==='once'?'primary':'secondary'}`).click();
  await waitFor(()=>calls.some(call=>call.p==='/auth/verify/options'));
  credential.resolve(fakeCredential());
  await waitFor(()=>calls.some(call=>call.p==='/auth/verify/finish'));
  assert.equal(calls.filter(call=>call.p==='/approvals/a/decision').length,0,
    'decision must not be posted before verification finishes');
  verified.resolve({ok:true});
  await waitFor(()=>calls.some(call=>call.p==='/approvals/a/decision'));
  assert.deepEqual(calls.find(call=>call.p==='/approvals/a/decision').o.body,{decision:choice});
 }finally{app.destroy();win.close();}
});

test('non-executable approval details show the capability error beside the command',async()=>{
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 const blocked={...approvalItem(),executable:false,choices:[],error:'Native API cannot atomically bind decisions to this action'};
 const app=await mountApp(doc,{clear(){},async request(p){
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[],total:0,offset:0};
  if(p==='/approvals')return {items:[]};
  if(p==='/approvals/a')return {...blocked,status:'pending'};
  return {items:[]};
 }},win);
 try{
  const card=doc.querySelector('[data-approval-id="a"]');
  assert.ok(card);
  assert.match(card.textContent,/echo fixture/);
  assert.match(card.textContent,/Native API cannot atomically bind decisions to this action/);
  const explanation=[...card.querySelectorAll('.caption')].find(node=>node.textContent.includes('cannot atomically bind'));
  assert.ok(explanation && !explanation.hidden);
  assert.equal(card.querySelector('button'),null);
 }finally{app.destroy();win.close();}
});

test('unchanged approval stream data preserves verification and uncertain action state',async()=>{
 const credential=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 win.PublicKeyCredential=class {};
 win.navigator.credentials={get:()=>credential.promise};
 class Events{
  constructor(){this.listeners={};win.activeStream=this;}
  addEventListener(name,fn){this.listeners[name]=fn;}
  emit(name,data){this.listeners[name]?.({data:JSON.stringify(data)});}
  close(){}
 }
 win.EventSource=Events;
 const app=await mountApp(doc,{clear(){},async request(p,o={}){
  calls.push({p,o});
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[],total:0,offset:0,
    run:{id:'r',session_id:'s',status:'waiting_for_approval'}};
  if(p==='/approvals')return {items:[approvalItem()]};
  if(p==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
  if(p==='/auth/verify/finish')return {ok:true};
  if(p==='/approvals/a/decision')return {status:'resolved'};
  return {items:[]};
 }},win);
 try{
  doc.querySelector('[data-approval-id="a"] button.primary').click();
  await waitFor(()=>calls.some(call=>call.p==='/auth/verify/options'));
  win.activeStream.emit('approval',approvalItem());
  await tick();await tick();
  credential.resolve(fakeCredential());await tick();await tick();
  assert.equal(calls.filter(call=>call.p==='/approvals/a/decision').length,1);
 }finally{app.destroy();win.close();}
});

test('changed approval identity fences a decision after passkey verification',async()=>{
 const credential=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 win.PublicKeyCredential=class {};
 win.navigator.credentials={get:()=>credential.promise};
 let approvalReads=0;
 const changed={...approvalItem(),request_id:'replacement-request',command:'echo replacement'};
 const app=await mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[],total:0,offset:0};
  if(p==='/approvals')return {items:++approvalReads===1?[approvalItem()]:[changed]};
  if(p==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
  if(p==='/auth/verify/finish')return {ok:true};
  return {items:[]};
 }},win);
 try{
  doc.querySelector('[data-approval-id="a"] button.primary').click();
  await waitFor(()=>calls.includes('/auth/verify/options'));
  win.dispatchEvent(new win.Event('focus'));
  await waitFor(()=>approvalReads===2);
  assert.match(doc.querySelector('[data-approval-id="a"]').textContent,/echo replacement/);
  credential.resolve(fakeCredential());await tick();await tick();
  assert.equal(calls.filter(path=>path==='/approvals/a/decision').length,0);
 }finally{app.destroy();win.close();}
});

test('pending list and matching stream replay cannot unlock an uncertain decision',async()=>{
 const credential=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 win.PublicKeyCredential=class {};
 win.navigator.credentials={get:()=>credential.promise};
 class Events{
  constructor(){this.listeners={};win.activeStream=this;}
  addEventListener(name,fn){this.listeners[name]=fn;}
  emit(name,data){this.listeners[name]?.({data:JSON.stringify(data)});}
  close(){}
 }
 win.EventSource=Events;
 const app=await mountApp(doc,{clear(){},async request(p,o={}){
  calls.push({p,o});
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[],total:0,offset:0,
    run:{id:'r',session_id:'s',status:'waiting_for_approval'}};
  if(p==='/approvals')return {items:[approvalItem()]};
  if(p==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
  if(p==='/auth/verify/finish')return {ok:true};
  if(p==='/approvals/a/decision')throw Object.assign(new Error('outcome is unresolved'),{status:503});
  return {items:[]};
 }},win);
 try{
  const card=doc.querySelector('[data-approval-id="a"]');
  card.querySelector('button.primary').click();
  await waitFor(()=>calls.some(call=>call.p==='/auth/verify/options'));
  credential.resolve(fakeCredential());
  await waitFor(()=>calls.some(call=>call.p==='/approvals/a/decision'));
  await waitFor(()=>card.querySelector('h3').textContent==='Decision status unknown');
  const decisions=calls.filter(call=>call.p==='/approvals/a/decision').length;
  win.dispatchEvent(new win.Event('focus'));await tick();await tick();
  win.activeStream.emit('approval',approvalItem());await tick();await tick();
  assert.equal(card.querySelector('h3').textContent,'Decision status unknown');
  assert.equal(card.querySelector('button'),null);
  assert.equal(calls.filter(call=>call.p==='/approvals/a/decision').length,decisions);
 }finally{app.destroy();win.close();}
});

test('known pre-admission 403 keeps a retryable approval instead of an unknown outcome',async()=>{
 const credential=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 win.PublicKeyCredential=class {};
 win.navigator.credentials={get:()=>credential.promise};
 const app=await mountApp(doc,{clear(){},async request(p,o={}){
  calls.push({p,o});
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[],total:0,offset:0};
  if(p==='/approvals')return {items:[approvalItem()]};
  if(p==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
  if(p==='/auth/verify/finish')return {ok:true};
  if(p==='/approvals/a/decision')throw Object.assign(new Error('Fresh passkey verification required'),{status:403});
  return {items:[]};
 }},win);
 try{
  const card=doc.querySelector('[data-approval-id="a"]');
  card.querySelector('button.secondary').click();
  await waitFor(()=>calls.some(call=>call.p==='/auth/verify/options'));
  credential.resolve(fakeCredential());
  await waitFor(()=>calls.some(call=>call.p==='/approvals/a/decision'));
  await waitFor(()=>card.querySelector('.inline-error').textContent.includes('Passkey verification expired'));
  assert.equal(card.querySelector('h3').textContent,'Approval required');
  assert.equal(card.querySelectorAll('button').length,2);
  assert.doesNotMatch(card.textContent,/Decision status unknown/);
  assert.equal(calls.filter(call=>call.p==='/approvals/a/decision').length,1);
 }finally{app.destroy();win.close();}
});

test('final approval detail wins over pending list data in either response order',async()=>{
 for(const detailFirst of [false,true]){
  const list=deferred(),detail=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
  const app=await mountApp(doc,{clear(){},async request(p){
   calls.push(p);
   if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
   if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
   if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
   if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[],total:0,offset:0};
   if(p==='/approvals')return list.promise;
   if(p==='/approvals/a')return detail.promise;
   return {items:[]};
  }},win);
  try{
   await waitFor(()=>calls.includes('/approvals') && calls.includes('/approvals/a'));
   const final={...approvalItem(),status:'expired',executable:false,choices:[]};
   if(detailFirst){
    detail.resolve(final);await tick();await tick();
    list.resolve({items:[approvalItem()]});
   }else{
    list.resolve({items:[approvalItem()]});await tick();await tick();
    detail.resolve(final);
   }
   await tick();await tick();
   const card=doc.querySelector('[data-approval-id="a"]');
   assert.match(card.querySelector('h3').textContent,/Expired/);
   assert.equal(card.querySelector('button'),null);
   assert.equal(calls.filter(path=>path==='/approvals/a/decision').length,0);
  }finally{app.destroy();win.close();}
 }
});

test('late approval details cannot update a changed route or owner',async()=>{
 for(const departure of ['route','owner']){
  const detail=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
  let owner='owner';
  const app=await mountApp(doc,{clear(){},async request(p){
   calls.push(p);
   if(p==='/auth/me')return {user:{id:owner,status:'ready'}};
   if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
   if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
   if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[],total:0,offset:0};
   if(p==='/approvals')return {items:[]};
   if(p==='/approvals/a')return detail.promise;
   if(p.startsWith('/sessions?'))return {items:[],total:0};
   return {items:[]};
  }},win);
  try{
   await waitFor(()=>calls.includes('/approvals/a'));
   if(departure==='route')doc.querySelector('[data-view="chats"]').click();
   else{
    win.history.replaceState({},'','/hermes/');owner='another-owner';
    await app.start();
   }
   detail.resolve({...approvalItem(),status:'expired',executable:false,choices:[]});await tick();await tick();
   assert.equal(doc.querySelector('[data-approval-id="a"]'),null);
   assert.equal(calls.filter(path=>path==='/approvals/a/decision').length,0);
  }finally{app.destroy();win.close();}
 }
});

test('stale pending detail cannot restore an approval after acknowledged decision',async()=>{
 const list=deferred(),detail=deferred(),credential=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 win.PublicKeyCredential=class {};
 win.navigator.credentials={get:()=>credential.promise};
 const app=await mountApp(doc,{clear(){},async request(p,o={}){
  calls.push({p,o});
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[],total:0,offset:0};
  if(p==='/approvals')return list.promise;
  if(p==='/approvals/a')return detail.promise;
  if(p==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
  if(p==='/auth/verify/finish')return {ok:true};
  if(p==='/approvals/a/decision')return {status:'resolved'};
  return {items:[]};
 }},win);
 try{
  await waitFor(()=>calls.some(call=>call.p==='/approvals') && calls.some(call=>call.p==='/approvals/a'));
  list.resolve({items:[approvalItem()]});await tick();await tick();
  const card=doc.querySelector('[data-approval-id="a"]');
  card.querySelector('button.primary').click();
  await waitFor(()=>calls.some(call=>call.p==='/auth/verify/options'));
  credential.resolve(fakeCredential());
  await waitFor(()=>calls.some(call=>call.p==='/approvals/a/decision'));
  await tick();
  assert.match(card.querySelector('h3').textContent,/Resolved/);
  detail.resolve({...approvalItem(),status:'pending'});await tick();await tick();
  assert.match(card.querySelector('h3').textContent,/Resolved/);
  assert.equal(card.querySelector('button'),null);
  assert.equal(calls.filter(call=>call.p==='/approvals/a/decision').length,1);
 }finally{app.destroy();win.close();}
});

test('approval removal during verification clears local pending state without deciding',async()=>{
 const credential=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 win.PublicKeyCredential=class {};
 win.navigator.credentials={get:()=>credential.promise};
 let approvalReads=0;
 const app=await mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[],total:0,offset:0};
  if(p==='/approvals')return {items:++approvalReads===1?[approvalItem()]:[]};
  if(p==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
  if(p==='/auth/verify/finish')return {ok:true};
  return {items:[]};
 }},win);
 try{
  const card=doc.querySelector('[data-approval-id="a"]');
  card.querySelector('button.primary').click();
  await waitFor(()=>calls.includes('/auth/verify/options'));
  win.dispatchEvent(new win.Event('focus'));await tick();await tick();
  assert.doesNotMatch(card.querySelector('h3').textContent,/Decision pending/);
  credential.resolve(fakeCredential());await tick();await tick();
  assert.doesNotMatch(card.querySelector('h3').textContent,/Decision pending/);
  assert.equal(card.querySelector('button'),null);
  assert.equal(calls.filter(path=>path==='/approvals/a/decision').length,0);
 }finally{app.destroy();win.close();}
});

test('approval decision is not sent after its route or owner changes during passkey verification',async()=>{
 for(const departure of ['route','owner']){
  const credential=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
  win.PublicKeyCredential=class {};
  win.navigator.credentials={get:()=>credential.promise};
  let owner='owner';
  const app=await mountApp(doc,{clear(){},async request(p){
    calls.push(p);
    if(p==='/auth/me')return {user:{id:owner,status:'ready'}};
    if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
    if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
    if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[],total:0,offset:0};
    if(p==='/approvals')return {items:[approvalItem()]};
    if(p==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
    if(p==='/auth/verify/finish')return {ok:true};
    if(p.startsWith('/sessions?'))return {items:[{id:'s',title:'Exact chat title'}],total:1};
    return {items:[]};
  }},win);
  try{
    doc.querySelector('[data-approval-id="a"] button.primary').click();
    while(!calls.includes('/auth/verify/options'))await tick();
    if(departure==='route'){doc.querySelector('[data-view="chats"]').click();await tick();}
    else{owner='another-owner';await app.start();await tick();}
    credential.resolve(fakeCredential());await tick();await tick();
    assert.equal(calls.filter(path=>path==='/approvals/a/decision').length,0,`${departure} change must fence decision submission`);
  }finally{app.destroy();win.close();}
 }
});

test('stale older-history notification restore cannot replace current approval reconciliation',async()=>{
 for(const departure of ['route','owner']){
  const older=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/'}),win=dom.window,doc=win.document;
  let owner='owner';
  const currentApproval={...approvalItem(),run_id:'new-run',session_id:'new-chat'};
  const app=await mountApp(doc,{clear(){},async request(p){
    calls.push(p);
    if(p==='/auth/me')return {user:{id:owner,status:'ready'}};
    if(p==='/inbox/n/target')return {kind:'request',session_id:'old-chat',run_id:'old-run',run_session_id:'old-chat'};
    if(p.startsWith('/sessions?'))return {items:[{id:'old-chat',title:'Old chat'},{id:'new-chat',title:'New chat'}],total:2};
    if(p==='/sessions/old-chat')return {id:'old-chat',title:'Old chat'};
    if(p==='/sessions/new-chat')return {id:'new-chat',title:'New chat'};
    if(p==='/sessions/old-chat/messages?latest=true&turn_boundary=true')return {items:[],offset:10,total:11};
    if(p==='/sessions/old-chat/messages?limit=10&offset=0&turn_boundary=true')return older.promise;
    if(p==='/runs/old-run')return {id:'old-run',session_id:'old-chat',input:'Old request',status:'completed',output:'Old result'};
    if(p==='/sessions/new-chat/messages?latest=true&turn_boundary=true')return {items:[],offset:0,total:0,run:{id:'new-run',session_id:'new-chat',status:'waiting_for_approval',input:'New request'}};
    if(p==='/approvals')return {items:[currentApproval]};
    return {items:[]};
  }},win);
  try{
    win.history.replaceState({},'','/hermes/?notification=n');
    const opening=app.start();
    while(!calls.includes('/sessions/old-chat/messages?limit=10&offset=0&turn_boundary=true'))await tick();
    const sessionReads=calls.filter(path=>path.startsWith('/sessions?')).length;
    if(departure==='route')doc.querySelector('[data-view="chats"]').click();
    else{win.history.replaceState({},'','/hermes/');owner='another-owner';void app.start();}
    while(app.state.user?.id!==owner || calls.filter(path=>path.startsWith('/sessions?')).length<=sessionReads)await tick();
    while(!doc.querySelector('.session-row[aria-label="New chat"]'))await tick();
    const newChat=doc.querySelector('.session-row[aria-label="New chat"]');
    assert.ok(newChat,JSON.stringify({departure,body:doc.body.textContent,calls}));
    newChat.click();await tick();
    assert.ok(doc.querySelector('[data-approval-id="a"]'));
    const approvalReads=calls.filter(path=>path==='/approvals').length;
    older.resolve({items:[{id:1,role:'user',content:'Old request',run_id:'old-run',session_id:'old-chat'}],offset:0,total:11});
    await opening;await tick();
    assert.match(doc.querySelector('.conversation-head h1').textContent,/New chat/);
    assert.ok(doc.querySelector('[data-approval-id="a"]'));
    assert.equal(calls.filter(path=>path==='/approvals').length,approvalReads,
      `${departure} history completion must not install stale approval work`);
    win.dispatchEvent(new win.Event('focus'));await tick();
    assert.equal(calls.filter(path=>path==='/approvals').length,approvalReads+1,
      'current approval focus listener remains attached');
  }finally{app.destroy();win.close();}
 }
});

for(const query of ['notification','inbox'])test(`initial ${query} target shows retry after a message 503 and retains exact target`,async()=>{
 const calls=[],dom=new JSDOM('<div id="app"></div>',{url:`https://fixture.test/hermes/?${query}=n`}),win=dom.window,doc=win.document;
 let fail=true;
 const app=await mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'request',session_id:'s',run_id:'r',run_session_id:'s'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p==='/sessions/s/messages?latest=true&turn_boundary=true'){
    if(fail)throw Object.assign(new Error('Temporary service failure'),{status:503});
    return {items:[],offset:0,total:0};
  }
  if(p==='/runs/r')return {id:'r',session_id:'s',input:'Exact request',status:'completed',output:'Exact result'};
  return {items:[]};
 }},win);
 try{
  assert.ok(doc.querySelector('.inline-error'),'current message failure must replace Loading with a retryable error');
  assert.match(doc.querySelector('.inline-error').textContent,/Temporary service failure/);
  const retry=[...doc.querySelectorAll('button')].find(button=>button.textContent==='Retry opening conversation');
  assert.ok(retry);
  fail=false;retry.click();await tick();await tick();
  assert.ok(doc.querySelector('[data-notification-run="r"]'));
  assert.equal(calls.filter(path=>path==='/inbox/n/target').length,1);
  assert.ok(calls.includes('/runs/r'));
 }finally{app.destroy();win.close();}
});

test('initial notification message 401 expires only the current login',async()=>{
 const calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 const app=await mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'request',session_id:'s',run_id:'r',run_session_id:'s'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p==='/sessions/s/messages?latest=true&turn_boundary=true')throw Object.assign(new Error('Expired'),{status:401});
  return {items:[]};
 }},win);
 try{
  assert.equal(app.state.user,null);
  assert.match(doc.querySelector('.auth-card h1').textContent,/Sign in/);
  assert.equal(doc.querySelector('.loading'),null);
  assert.ok(calls.includes('/sessions/s/messages?latest=true&turn_boundary=true'));
 }finally{app.destroy();win.close();}
});
