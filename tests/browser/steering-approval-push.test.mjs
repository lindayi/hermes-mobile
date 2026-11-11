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
   await waitFor(()=>doc.querySelector('[data-approval-id="a"]'));
   assert.match(doc.querySelector('[data-approval-id="a"]').textContent,/Expired/);
   assert.equal(doc.querySelector('[data-approval-id="a"] button'),null);
  }
 }finally{app.destroy();win.close();}
});

test('owned non-chat approval keeps its Inbox card and explicit decisions',async()=>{
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document,calls=[];
 const notice={id:'n',title:'Approval required',body:'Review the scheduled action.',
  session_id:'cron-session',approval_id:'a',run_id:'r',request_id:'native-a',read:false,created_at:1};
 const approval={...approvalItem(),session_id:'cron-session',run_session_id:'cron-session',inbox_visible:true};
 const app=await mountApp(doc,{clear(){},async request(p,o={}){
  calls.push({p,o});
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'inbox'};
  if(p==='/inbox')return {items:[notice],read_count:0};
  if(p==='/approvals')return {items:[approval]};
  return {items:[]};
 }},win);
 try{
  await waitFor(()=>doc.querySelector('.inbox-approval'));
  const card=doc.querySelector('.inbox-approval');
  assert.match(card.textContent,/echo fixture/);
  assert.match(card.textContent,/fixture target/);
  assert.ok([...card.querySelectorAll('button')].some(button=>button.textContent==='Approve once'));
  assert.ok([...card.querySelectorAll('button')].some(button=>button.textContent==='Deny'));
  assert.equal(calls.some(call=>call.p==='/sessions/cron-session'),false);
  assert.equal(calls.some(call=>call.p.endsWith('/decision')),false,'opening Inbox does not decide');
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

for(const status of [404,409,410])test(`missing approval target remains terminal after ${status}`,async()=>{
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document,calls=[];
 const app=await mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0};
  if(p==='/approvals/a')throw Object.assign(new Error('Unavailable'),{status});
  return {items:[]};
 }},win);
 try{
  await waitFor(()=>doc.querySelector('.notification-unavailable h3')?.textContent==='Approval unavailable');
  assert.equal(doc.querySelector('.notification-unavailable button'),null,'authoritative unavailability is not retryable');
  assert.equal(doc.querySelector('[data-approval-id]'),null);
  assert.equal(calls.filter(path=>path.endsWith('/decision')).length,0);
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
 const credential=deferred(),calls=[],expires_at=Date.now()/1000+0.6;
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
  await new Promise(resolve=>setTimeout(resolve,Math.max(0,expires_at*1000+20-Date.now())));
  credential.resolve(fakeCredential());
  await waitFor(()=>calls.includes('/auth/verify/finish'));
  await tick();await tick();
  assert.equal(calls.filter(path=>path.endsWith('/decision')).length,0);
  assert.match(doc.querySelector('[data-approval-id="a"] h3').textContent,/Expired/);
  assert.equal(doc.querySelector('[data-approval-id="a"] button'),null);
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

for(const transport of ['events','polling'])test(`approval notification resumes an active aliased run over ${transport}`,async()=>{
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document,calls=[];
 let owner='owner';
 const approval={...approvalItem(),session_id:'s',run_session_id:'old-alias'};
 const run={id:'r',session_id:'old-alias',status:'waiting_for_approval',input:'Aliased request',created_at:1};
 let polledRun={...run};
 class Events{
  constructor(){this.listeners={};win.activeStream=this;}
  addEventListener(name,fn){this.listeners[name]=fn;}
  emit(name,data){this.listeners[name]?.({data:JSON.stringify(data)});}
  close(){this.closed=true;}
 }
 if(transport==='events')win.EventSource=Events;
 win.PublicKeyCredential=class {};
 win.navigator.credentials={get:async()=>fakeCredential()};
 const app=await mountApp(doc,{clear(){},async request(p,o={}){
  calls.push({p,o});
  if(p==='/auth/me')return {user:{id:owner,status:'ready'}};
  if(p==='/inbox/n/target'){
   if(owner!=='owner')throw Object.assign(new Error('No target for this owner'),{status:404});
   return {kind:'approval',session_id:'s',run_session_id:'old-alias',run_id:'r',approval_id:'a',request_id:'native-a'};
  }
  if(p==='/sessions/s')return {id:'s',title:'Canonical chat'};
  if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0,run};
  if(p==='/approvals')return {items:[approval]};
  if(p==='/approvals/a')return {...approval,status:'pending'};
  if(p==='/runs/r/clarifications')return {run_id:'r',status:'waiting_for_approval',items:[],available:true};
  if(p==='/runs/r')return polledRun;
  if(p==='/auth/verify/options')return {challenge_id:'fixture',options:{challenge:'AQ'}};
  if(p==='/auth/verify/finish')return {verified:true};
  if(p==='/approvals/a/decision')return {status:'resolved'};
  return {items:[]};
 }},win);
 try{
 await waitFor(()=>doc.querySelector('[data-approval-id="a"]'));
 const card=doc.querySelector('[data-approval-id="a"]');
  await waitFor(()=>card?.querySelector('button.primary'));
  assert.equal(doc.querySelector('h1').textContent,'Canonical chat');
  assert.equal(doc.querySelector('.live-message')?.dataset.historySession,'old-alias',
   'the live run retains its original authorized session identity');
  if(transport==='events'){
   await waitFor(()=>win.activeStream);
   const oldStream=win.activeStream;
   oldStream.emit('delta',{text:'Resumed synthetic output'});
   await waitFor(()=>doc.querySelector('.live-message')?.textContent.includes('Resumed synthetic output'));
   card.querySelector('button.primary').click();
   await waitFor(()=>calls.some(call=>call.p==='/approvals/a/decision'));
   oldStream.emit('status',{status:'completed',output:'Terminal synthetic result',updated_at:2});
  }else{
   card.querySelector('button.primary').click();
   await waitFor(()=>calls.some(call=>call.p==='/approvals/a/decision'));
   const reads=()=>calls.filter(call=>call.p==='/runs/r').length;
   for(const mismatched of [
    {...run,id:'other-run',status:'completed',output:'Wrong run'},
    {...run,session_id:'unrelated-alias',status:'completed',output:'Wrong session'}
   ]){
    const before=reads();polledRun=mismatched;win.dispatchEvent(new win.Event('focus'));
    await waitFor(()=>reads()===before+1);await tick();
    assert.doesNotMatch(doc.querySelector('.live-message').textContent,/Wrong run|Wrong session/,
     'polling rejects a mismatched run or source-session identity');
   }
   polledRun={...run,status:'completed',output:'Polled synthetic result',updated_at:2};
   const before=reads();win.dispatchEvent(new win.Event('focus'));
   await waitFor(()=>reads()===before+1);
  }
  const expected=transport==='events'?'Terminal synthetic result':'Polled synthetic result';
  await waitFor(()=>doc.querySelector('.live-message')?.textContent.includes(expected));
  assert.equal(doc.querySelector('.live-message')?.dataset.historySession,'old-alias');
  assert.equal(calls.some(call=>call.p==='/inbox'),false);
  assert.equal(calls.filter(call=>call.p==='/approvals/a/decision').length,1);
 }finally{app.destroy();win.close();}
});

test('serialized null-session approval SSE preserves a held aliased decision acknowledgement',async()=>{
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document,calls=[];
 const acknowledgement=deferred();let decisionPosts=0;
 const {run_session_id,...streamApproval}=approvalItem();
 streamApproval.session_id=null;streamApproval.status='pending';
 const serializedApproval=JSON.parse(JSON.stringify(streamApproval));
 assert.equal(serializedApproval.session_id,null,'the producer serializes an explicit null session');
 assert.equal(Object.hasOwn(serializedApproval,'run_session_id'),false,'the producer does not add a run-session field');
 const approval={...approvalItem(),session_id:'s',run_session_id:'old-alias'};
 const run={id:'r',session_id:'old-alias',status:'waiting_for_approval',input:'Aliased request',created_at:1};
 class Events{
  constructor(){this.listeners={};win.activeStream=this;}
  addEventListener(name,fn){this.listeners[name]=fn;}
  emit(name,data){this.listeners[name]?.({data:JSON.stringify(data)});}
  close(){this.closed=true;}
 }
 win.EventSource=Events;win.PublicKeyCredential=class {};
 win.navigator.credentials={get:async()=>fakeCredential()};
 const app=await mountApp(doc,{clear(){},async request(p,o={}){
  calls.push({p,o});
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'old-alias',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Canonical chat'};
  if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0,run};
  if(p==='/approvals')return {items:[approval]};
  if(p==='/approvals/a')return {...approval,status:'pending'};
  if(p==='/runs/r/clarifications')return {run_id:'r',status:'waiting_for_approval',items:[],available:true};
  if(p==='/auth/verify/options')return {challenge_id:'fixture',options:{challenge:'AQ'}};
  if(p==='/auth/verify/finish')return {verified:true};
  if(p==='/approvals/a/decision'){decisionPosts++;return acknowledgement.promise;}
  return {items:[]};
 }},win);
 try{
  const card=doc.querySelector('[data-approval-id="a"]');
  await waitFor(()=>card?.querySelector('button.primary'));
  card.querySelector('button.primary').click();
  await waitFor(()=>decisionPosts===1);
  win.activeStream.emit('approval',{...serializedApproval,id:'a',run_id:'r',request_id:'native-a'});
  await waitFor(()=>calls.filter(call=>call.p==='/approvals').length>1);
  assert.equal(calls.filter(call=>call.p==='/approvals')[1].p,'/approvals');
  acknowledgement.resolve({status:'resolved'});
  await waitFor(()=>doc.querySelector('[data-approval-id="a"] h3')?.textContent==='Resolved');
  assert.equal(decisionPosts,1,'the held explicit decision is sent exactly once');
  assert.equal(doc.querySelector('[data-approval-id="a"] .inline-error')?.textContent,'');
  assert.equal(doc.querySelector('[data-approval-id="a"]').dataset.historySession,'s',
   'the approval card stays in the canonical chat while its tracked run keeps the source alias');
  win.activeStream.emit('approval',{...serializedApproval,id:'a',run_id:'r',request_id:'native-a'});
  await new Promise(resolve=>setTimeout(resolve,30));
  assert.equal(doc.querySelector('[data-approval-id="a"] h3').textContent,'Resolved',
   'a replay cannot replace the acknowledged final card');
 }finally{app.destroy();win.close();}
});

test('cold chat loads session-scoped approvals and reports a bounded partial list',async()=>{
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/'}),win=dom.window,doc=win.document,calls=[];
 const target={...approvalItem(),status:'pending',session_id:'s',run_session_id:'old-alias'};
 const app=await mountApp(doc,{clear(){},async request(p,o={}){
  calls.push({p,o});
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p.startsWith('/sessions?'))return {items:[{id:'s',title:'Synthetic chat'}],total:1};
  if(p==='/sessions/s')return {id:'s',title:'Synthetic chat'};
  if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,
   run:{id:'r',session_id:'old-alias',status:'waiting_for_approval'}};
  if(p==='/approvals')return o.query?.session_id==='s'?{items:[target],complete:false}:{items:[]};
  return {items:[]};
 }},win);
 try{
  doc.querySelector('.session-row').click();
  const card=()=>doc.querySelector('[data-approval-id="a"]');
  await waitFor(()=>card()?.querySelector('button.primary'));
  const listCall=calls.find(call=>call.p==='/approvals');
  assert.equal(listCall?.o.query?.session_id,'s','cold chat enumeration is bound to its canonical session');
  assert.match(doc.querySelector('.approval-notice').textContent,/more than 1,000.*some are not shown/i);
  assert.equal(doc.querySelectorAll('[data-approval-id="a"]').length,1);
  assert.equal(calls.filter(call=>call.p.endsWith('/decision')).length,0);
 }finally{app.destroy();win.close();}
});

test('a late acknowledgement cannot change the same approval ID in another chat',async()=>{
 const ack=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 win.PublicKeyCredential=class {};win.navigator.credentials={get:async()=>fakeCredential()};
 const original={...approvalItem(),status:'pending'},
  other={...approvalItem(),run_id:'r2',request_id:'native-other',session_id:'s2',run_session_id:'s2',status:'pending'};
 const app=await mountApp(doc,{clear(){},async request(p,o={}){
  calls.push({p,o});
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p.startsWith('/sessions?'))return {items:[{id:'s2',title:'Second chat'}],total:1};
  if(p==='/sessions/s')return {id:'s',title:'First chat'};
  if(p==='/sessions/s2')return {id:'s2',title:'Second chat'};
  if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,run:{id:'r',session_id:'s',status:'waiting_for_approval'}};
  if(p.startsWith('/sessions/s2/messages'))return {items:[],total:0,run:{id:'r2',session_id:'s2',status:'waiting_for_approval'}};
  if(p==='/approvals')return {items:o.query?.session_id==='s2'?[other]:[original]};
  if(p==='/approvals/a')return original;
  if(p==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
  if(p==='/auth/verify/finish')return {ok:true};
  if(p==='/approvals/a/decision')return ack.promise;
  return {items:[]};
 }},win);
 const card=()=>doc.querySelector('[data-approval-id="a"]');
 try{
  await waitFor(()=>card()?.querySelector('button.primary'));
  card().querySelector('button.primary').click();
  await waitFor(()=>calls.some(call=>call.p==='/approvals/a/decision'));
  doc.querySelector('[data-view="chats"]').click();
  await waitFor(()=>doc.querySelector('.session-row'));
  doc.querySelector('.session-row').click();
  await waitFor(()=>card()?.querySelector('h3').textContent==='Approval required');
  ack.resolve({status:'resolved'});
  await tick();await tick();
  assert.equal(card().querySelector('h3').textContent,'Approval required');
  assert.equal(card().querySelectorAll('button:enabled').length,2,
   'an acknowledgement for another canonical chat cannot alter this approval');
  assert.equal(calls.filter(call=>call.p==='/approvals/a/decision').length,1);
 }finally{ack.resolve({status:'resolved'});app.destroy();win.close();}
});

test('a dispatched 401 cannot expire a newer account chat or erase its draft',async()=>{
 const decision=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 win.PublicKeyCredential=class {};win.navigator.credentials={get:async()=>fakeCredential()};
 let user='owner';
 const pending={...approvalItem(),status:'pending'};
 const app=await mountApp(doc,{clear(){},async request(p,o={}){
  calls.push({p,o});
  if(p==='/auth/me')return {user:{id:user,status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p.startsWith('/sessions?'))return {items:[{id:'s',title:'First chat'},{id:'s2',title:'Second chat'}],total:2};
  if(p==='/sessions/s'||p==='/sessions/s2')return {id:p.split('/').at(-1),title:p.endsWith('/s2')?'Second chat':'First chat'};
  if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,run:{id:'r',session_id:'s',status:'waiting_for_approval'}};
  if(p.startsWith('/sessions/s2/messages'))return {items:[],total:0,run:null};
  if(p==='/approvals')return user==='owner' && o.query?.session_id==='s'?[pending]:{items:[]};
  if(p==='/approvals/a')return pending;
  if(p==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
  if(p==='/auth/verify/finish')return {ok:true};
  if(p==='/approvals/a/decision')return decision.promise;
  return {items:[]};
 }},win);
 try{
  await waitFor(()=>doc.querySelector('[data-approval-id="a"] button.primary'));
  doc.querySelector('[data-approval-id="a"] button.primary').click();
  await waitFor(()=>calls.some(call=>call.p==='/approvals/a/decision'));
  user='other';win.history.replaceState({},'', '/hermes/');await app.start();
  await waitFor(()=>app.state.user?.id==='other' && doc.querySelectorAll('.session-row').length===2);
  doc.querySelectorAll('.session-row')[1].click();
  await waitFor(()=>app.state.session?.id==='s2' && doc.querySelector('textarea'));
  const draft=doc.querySelector('textarea');draft.value='new account draft';
  draft.dispatchEvent(new win.Event('input',{bubbles:true}));
  decision.reject(Object.assign(new Error('expired'),{status:401}));
  await tick();await tick();
  assert.equal(app.state.user?.id,'other','a dispatched old-account 401 cannot sign out the new account');
  assert.equal(app.state.session?.id,'s2','the newer route remains mounted');
  assert.equal(doc.querySelector('textarea')?.value,'new account draft','the newer account draft remains intact');
  assert.equal(doc.querySelector('[aria-label="Sign in with a passkey"]'),null);
  assert.equal(calls.filter(call=>call.p==='/approvals/a/decision').length,1,'the uncertain decision is never replayed');
  doc.querySelector('[data-view="chats"]').click();
  await waitFor(()=>doc.querySelectorAll('.session-row').length===2);
  doc.querySelectorAll('.session-row')[0].click();
  await waitFor(()=>app.state.session?.id==='s' && doc.querySelector('.conversation'));
  assert.equal(doc.querySelector('[data-approval-id="a"]'),null,'a different owner cannot restore the old approval identity');
 }finally{decision.resolve({status:'resolved'});app.destroy();win.close();}
});

test('ordinary run accepts the producer null-session placeholder and rejects conflicting sessions',async()=>{
 const calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/'}),win=dom.window,doc=win.document;
 let owner='owner';
 class Events{
  constructor(){this.listeners={};win.activeStream=this;}
  addEventListener(name,fn){this.listeners[name]=fn;}
  emit(name,data){this.listeners[name]?.({data:JSON.stringify(data)});}
  close(){}
 }
 win.EventSource=Events;
 const producerEvent={id:'placeholder',run_id:'r',request_id:'native-placeholder',session_id:null,
  status:'details_unavailable',expires_at:null,choices:[],executable:false,
  error:'Native approval details are unavailable after interruption; review in the native client'};
 const serialized=JSON.parse(JSON.stringify(producerEvent));
 assert.equal(serialized.session_id,null);
 assert.equal(Object.hasOwn(serialized,'run_session_id'),false);
 let approvals=[];
 const app=await mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:owner,status:'ready'}};
  if(p.startsWith('/sessions?'))return {items:[{id:'s',title:'Ordinary chat'}],total:1};
  if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,run:{id:'r',session_id:'s',status:'waiting_for_approval'}};
  if(p==='/approvals')return {items:approvals};
  if(p==='/approvals/placeholder')return approvals[0];
  return {items:[]};
 }},win);
 try{
  doc.querySelector('.session-row[aria-label="Ordinary chat"]').click();
  await waitFor(()=>win.activeStream);
  const initialLists=calls.filter(path=>path==='/approvals').length;
  win.activeStream.emit('approval',{...serialized,session_id:'foreign-session'});
  await tick();
  assert.equal(doc.querySelector('[data-approval-id="placeholder"]'),null,
   'a conflicting non-null source session is rejected');
  assert.equal(calls.filter(path=>path==='/approvals').length,initialLists);
  approvals=[{...producerEvent,session_id:'s'}];
  win.activeStream.emit('approval',serialized);
  await waitFor(()=>doc.querySelector('[data-approval-id="placeholder"] h3'));
  await waitFor(()=>calls.filter(path=>path==='/approvals').length>initialLists);
  const card=doc.querySelector('[data-approval-id="placeholder"]');
  assert.equal(card.querySelector('h3').textContent,'Approval details unavailable');
  assert.match(card.textContent,/No decision was sent/);
  assert.equal(card.querySelector('button'),null);
  assert.equal(card.dataset.historySession,'s');
  assert.equal(calls.filter(path=>path.endsWith('/decision')).length,0);
  const staleStream=win.activeStream;
  doc.querySelector('[data-view="chats"]').click();
  await waitFor(()=>doc.querySelector('.session-row[aria-label="Ordinary chat"]'));
  const beforeRouteEvent=calls.filter(path=>path==='/approvals').length;
  staleStream.emit('approval',{...serialized,id:'stale-route',request_id:'stale-route'});
  await tick();
  assert.equal(doc.querySelector('[data-approval-id="stale-route"]'),null,
   'a null-session event from a left route is ignored');
  assert.equal(calls.filter(path=>path==='/approvals').length,beforeRouteEvent);
  owner='another-owner';await app.start();
  const beforeOwnerEvent=calls.filter(path=>path==='/approvals').length;
  staleStream.emit('approval',{...serialized,id:'stale-owner',request_id:'stale-owner'});
  await tick();
  assert.equal(doc.querySelector('[data-approval-id="stale-owner"]'),null,
   'a tracked event from the prior owner is ignored');
  assert.equal(calls.filter(path=>path==='/approvals').length,beforeOwnerEvent);
 }finally{app.destroy();win.close();}
});

test('an aliased run stream cannot update the view after its owner changes',async()=>{
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document,calls=[];
 let owner='owner';
 class Events{
  constructor(){this.listeners={};win.activeStream=this;}
  addEventListener(name,fn){this.listeners[name]=fn;}
  emit(name,data){this.listeners[name]?.({data:JSON.stringify(data)});}
  close(){this.closed=true;}
 }
 win.EventSource=Events;
 const app=await mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:owner,status:'ready'}};
  if(p==='/inbox/n/target'){
   if(owner!=='owner')throw Object.assign(new Error('No target for this owner'),{status:404});
   return {kind:'request',session_id:'s',run_session_id:'old-alias',run_id:'r'};
  }
  if(p==='/sessions/s')return {id:'s',title:'Canonical chat'};
  if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0,run:{id:'r',session_id:'old-alias',status:'running',input:'Aliased request'}};
  if(p==='/runs/r/clarifications')return {run_id:'r',status:'running',items:[],available:true};
  return {items:[]};
 }},win);
 try{
  await waitFor(()=>win.activeStream);
  const stream=win.activeStream;
  owner='another-owner';await app.start();
  stream.emit('delta',{text:'Stale owner output'});
  assert.equal(stream.closed,true);
  assert.doesNotMatch(doc.body.textContent,/Stale owner output/);
 }finally{app.destroy();win.close();}
});

test('an aliased run snapshot cannot attach after its canonical chat is left',async()=>{
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document,calls=[];
 let release;const delayed=new Promise(resolve=>release=resolve);
 class Events{constructor(){this.listeners={};win.activeStream=this;}addEventListener(name,fn){this.listeners[name]=fn;}close(){}}
 win.EventSource=Events;
 const mounting=mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'request',session_id:'s',run_session_id:'old-alias',run_id:'r'};
  if(p==='/sessions/s')return {id:'s',title:'Canonical chat'};
  if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0,run:{id:'r',session_id:'old-alias',status:'running',input:'Aliased request'}};
  if(p==='/runs/r/clarifications')return delayed;
  return {items:[]};
 }},win);
 await waitFor(()=>calls.includes('/runs/r/clarifications'));
 doc.querySelector('[data-view="chats"]').click();await tick();
 release({run_id:'r',status:'running',items:[],available:true});
 const app=await mounting;await tick();
 try{assert.equal(win.activeStream,undefined,'a stale canonical view cannot resume its aliased run');}
 finally{app.destroy();win.close();}
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
  if(p==='/approvals')return {items:[{...approvalItem(),status:'pending'}]};
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
  await waitFor(()=>doc.querySelector('[data-approval-id="a"]'));
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

for(const [status,label] of Object.entries({
 resolved:'Resolved',resolved_external:'No longer pending',cancelled:'Cancelled',expired:'Expired'
}))test(`authoritative ${status} list status wins over an elapsed deadline and stale pending detail`,async()=>{
 const calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 const final={...approvalItem(),status,expires_at:1,executable:false,choices:[]};
 const app=await mountApp(doc,{clear(){},async request(p){
  calls.push(p);
  if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
  if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
  if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[],total:0,offset:0};
  if(p==='/approvals')return {items:[final]};
  if(p==='/approvals/a')return {...final,status:'pending',executable:true,choices:['once','deny']};
  return {items:[]};
 }},win);
 try{
  await waitFor(()=>doc.querySelector('[data-approval-id="a"] h3')?.textContent===label);
  assert.equal(doc.querySelector('[data-approval-id="a"] button'),null);
  assert.equal(calls.filter(path=>path==='/approvals/a/decision').length,0);
 }finally{app.destroy();win.close();}
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

test('late unavailable approval errors cannot update a changed route or owner',async()=>{
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
   detail.reject(Object.assign(new Error('Approval unavailable'),{status:409}));
   await tick();await tick();
   assert.equal(doc.querySelector('[data-approval-id="a"]'),null);
   assert.equal(calls.filter(path=>path.endsWith('/decision')).length,0);
  }finally{app.destroy();win.close();}
 }
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
  const retry=[...doc.querySelectorAll('button')].find(button=>button.textContent==='Retry opening session');
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

test('request notification retries a failed run read without losing its exact target',async()=>{
 const calls=[],runRead=deferred(),dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 let runReads=0;
 const app=await mountApp(doc,{clear(){},async request(p){
 calls.push(p);
 if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
 if(p==='/inbox/n/target')return {kind:'request',session_id:'s',run_id:'old-run',run_session_id:'s'};
 if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
 if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[{id:20,role:'assistant',content:'Newer response',run_id:'new-run',session_id:'s'}],offset:0,total:1};
 if(p==='/runs/old-run'){
  if(++runReads===1)throw Object.assign(new Error('Temporary run read failure'),{status:503});
  return runRead.promise;
 }
 return {items:[]};
 }},win);
 try{
 const retry=doc.querySelector('.notification-unavailable button');
 assert.equal(retry?.textContent,'Retry request details');
 assert.match(doc.querySelector('.notification-unavailable').textContent,/could not be loaded/i);
 retry.click();retry.click();retry.click();
 await waitFor(()=>runReads===2);
 assert.equal(runReads,2,'repeated clicks share one in-flight target read');
 runRead.resolve({id:'old-run',session_id:'s',input:'Original request',status:'completed',output:'Exact result'});
 await waitFor(()=>doc.querySelector('[data-notification-run="old-run"]'));
 assert.match(doc.querySelector('[data-notification-run="old-run"]').textContent,/Original request/);
 assert.match(doc.querySelector('[data-notification-run="old-run"]').textContent,/Exact result/);
 assert.equal(doc.querySelectorAll('[data-history-run="new-run"]').length,1,'existing history is retained once');
 assert.equal(calls.filter(path=>path==='/inbox/n/target').length,1);
 assert.equal(calls.filter(path=>path==='/sessions/s/messages?latest=true&turn_boundary=true').length,1);
 assert.equal(calls.some(path=>path.endsWith('/decision') || path.endsWith('/stop')),false);
 }finally{app.destroy();win.close();}
});

test('request notification retries failed older-history reads without duplicating history',async()=>{
 const calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
 let historyReads=0,runReads=0;
 const historyPath='/sessions/s/messages?limit=10&offset=0&turn_boundary=true';
 const app=await mountApp(doc,{clear(){},async request(p){
 calls.push(p);
 if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
 if(p==='/inbox/n/target')return {kind:'request',session_id:'s',run_id:'old-run',run_session_id:'s'};
 if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
 if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[{id:20,role:'assistant',content:'Newer response',run_id:'new-run',session_id:'s'}],offset:10,total:11};
 if(p===historyPath){
  if(++historyReads<=2)throw Object.assign(new Error('Temporary history read failure'),{status:503});
  return {items:[{id:1,role:'user',content:'Original request',run_id:'old-run',session_id:'s'}],offset:0,total:11};
 }
 if(p==='/runs/old-run'){
  runReads++;
  return {id:'old-run',session_id:'s',input:'Original request',status:'completed',output:'Exact result'};
 }
 return {items:[]};
 }},win);
 try{
 assert.equal(runReads,1);
 for(let attempt=0;attempt<2;attempt++){
  const retry=doc.querySelector('.notification-unavailable button');
  assert.equal(retry?.textContent,'Retry request details');
  retry.click();retry.click();
  await waitFor(()=>historyReads===attempt+2);
  assert.equal(historyReads,attempt+2,'repeated clicks share one history read');
  if(attempt===0)await waitFor(()=>{
   const next=doc.querySelector('.notification-unavailable button');
   return next && !next.disabled;
  });
 }
 await waitFor(()=>doc.querySelector('[data-notification-run="old-run"]'));
 assert.equal(doc.querySelectorAll('[data-history-run="new-run"]').length,1);
 assert.equal(doc.querySelectorAll('.user-message').length,1);
 assert.equal(doc.querySelectorAll('[data-notification-run="old-run"]').length,1);
 assert.equal(runReads,1,'retry reuses the already verified run identity');
 assert.equal(calls.filter(path=>path==='/inbox/n/target').length,1);
 assert.equal(calls.some(path=>path.endsWith('/decision') || path.endsWith('/stop')),false);
 }finally{app.destroy();win.close();}
});

test('request-target read errors retain auth expiry and terminal unavailable states',async()=>{
 for(const read of ['run','history'])for(const status of [401,404,409,410]){
  const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document;
  const historyPath='/sessions/s/messages?limit=10&offset=0&turn_boundary=true';
  const app=await mountApp(doc,{clear(){},async request(p){
   if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};
   if(p==='/inbox/n/target')return {kind:'request',session_id:'s',run_id:'old-run',run_session_id:'s'};
   if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};
   if(p==='/sessions/s/messages?latest=true&turn_boundary=true')
    return {items:[{id:20,role:'assistant',content:'Newer response',run_id:'new-run',session_id:'s'}],offset:read==='history'?10:0,total:11};
   if(p==='/runs/old-run'){
    if(read==='run')throw Object.assign(new Error('Read failed'),{status});
    return {id:'old-run',session_id:'s',input:'Original request',status:'completed',output:'Exact result'};
   }
   if(p===historyPath)throw Object.assign(new Error('Read failed'),{status});
   return {items:[]};
  }},win);
  try{
   if(status===401){
    assert.equal(app.state.user,null,`${read} 401 expires the current context`);
    assert.match(doc.querySelector('.auth-card h1').textContent,/Sign in/);
   }else{
    assert.equal(doc.querySelector('.notification-unavailable h3').textContent,'Request unavailable');
    assert.equal(doc.querySelector('.notification-unavailable button'),null,
      `${read} ${status} stays terminal`);
   }
  }finally{app.destroy();win.close();}
 }
});

test('late notification-target retries cannot cross a route, owner, or opaque target change',async()=>{
 for(const departure of ['route','owner','target']){
  const lateTarget=deferred(),calls=[],dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=old'}),win=dom.window,doc=win.document;
  let owner='owner',oldTargetReads=0;
  const app=await mountApp(doc,{clear(){},async request(p){
   calls.push(p);
   if(p==='/auth/me')return {user:{id:owner,status:'ready'}};
   if(p==='/inbox/old/target'){
    if(++oldTargetReads===1)throw Object.assign(new Error('Temporary target failure'),{status:503});
    if(oldTargetReads===2)return lateTarget.promise;
    return {kind:'request',session_id:'new-chat',run_id:'new-run',run_session_id:'new-chat'};
   }
   if(p==='/inbox/next/target')return {kind:'request',session_id:'new-chat',run_id:'new-run',run_session_id:'new-chat'};
   if(p.startsWith('/sessions?'))return {items:[{id:'new-chat',title:'New chat'}],total:1};
   if(p==='/sessions/old-chat')return {id:'old-chat',title:'Old chat'};
   if(p==='/sessions/new-chat')return {id:'new-chat',title:'New chat'};
   if(p==='/sessions/new-chat/messages?latest=true&turn_boundary=true'
       || p==='/sessions/old-chat/messages?latest=true&turn_boundary=true')return {items:[],offset:0,total:0};
   if(p==='/runs/new-run')return {id:'new-run',session_id:'new-chat',input:'New request',status:'completed',output:'New result'};
   if(p==='/approvals')return {items:[]};
   return {items:[]};
  }},win);
  try{
   const retry=doc.querySelector('.notification-target-retry button');
   assert.equal(retry?.textContent,'Retry notification');
   retry.click();
   await waitFor(()=>oldTargetReads===2);
   if(departure==='route'){
    doc.querySelector('[data-view="inbox"]').click();
    await waitFor(()=>app.state.view==='inbox');
   }else{
    if(departure==='owner')owner='another-owner';
    else win.history.replaceState({},'','/hermes/?notification=next');
    await app.start();
    assert.equal(doc.querySelector('.conversation-head h1').textContent,'New chat');
    assert.equal(doc.querySelector('[data-notification-run="new-run"]')?.textContent.includes('New request'),true);
   }
   const priorReads=oldTargetReads;
   retry.disabled=false;retry.click();await tick();
   assert.equal(oldTargetReads,priorReads,'detached retry controls cannot repeat a stale lookup');
   lateTarget.resolve({kind:'request',session_id:'old-chat',run_id:'old-run',run_session_id:'old-chat'});
   await tick();await tick();
   if(departure==='route')assert.equal(app.state.view,'inbox');
   else{
    assert.equal(doc.querySelector('.conversation-head h1').textContent,'New chat');
    assert.equal(doc.querySelector('[data-notification-run="old-run"]'),null);
   }
   assert.equal(calls.includes('/runs/old-run'),false);
  }finally{app.destroy();win.close();}
 }
});
