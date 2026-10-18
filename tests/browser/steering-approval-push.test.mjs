import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(resolve=>setTimeout(resolve,10));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const fakeCredential=()=>({id:'cred',rawId:new Uint8Array([1]).buffer,type:'public-key',getClientExtensionResults:()=>({}),response:{clientDataJSON:new Uint8Array([2]).buffer,authenticatorData:new Uint8Array([3]).buffer,signature:new Uint8Array([4]).buffer,userHandle:null}});
const approvalItem=()=>({id:'a',run_id:'r',request_id:'native-a',session_id:'s',title:'Execute fixture',command:'echo fixture',target:'fixture target',expires_at:2000000000,executable:true,choices:['once','deny']});
for(const status of ['pending','expired'])test(`approval notification opens exact chat without deciding (${status})`,async()=>{
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?notification=n'}),win=dom.window,doc=win.document,calls=[];let scrolled;
 win.HTMLElement.prototype.scrollIntoView=function(){scrolled=this;};
 const app=await mountApp(doc,{clear(){},async request(p,o={}){calls.push({p,o});if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};if(p.startsWith('/sessions/s/messages'))return {items:[],total:0,offset:0};if(p==='/approvals')return {items:status==='pending'?[{id:'a',run_id:'r',request_id:'native-a',session_id:'s',title:'Execute fixture',command:'echo fixture',target:'fixture target',expires_at:2000000000,executable:true,choices:['once','deny']}]:[]};if(p==='/approvals/a')return {id:'a',run_id:'r',request_id:'native-a',session_id:'s',status,command:'echo fixture',target:'fixture target',expires_at:status==='expired'?1:2000000000,executable:true,choices:['once','deny']};return {items:[]};}},win);
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
 const mounted=mountApp(doc,{clear(){},async request(p){calls.push(p);if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[],offset:0,total:0};if(p==='/approvals')return gate.promise;if(p==='/approvals/a')throw Object.assign(new Error('unavailable'),{status:404});return {items:[]};}},win);
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
 const mounted=mountApp(doc,{clear(){},async request(p){calls.push(p);if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[],offset:0,total:0};if(p==='/approvals')return {items:[]};if(p==='/approvals/a')return gate.promise;return {items:[]};}},win);
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
 const app=await mountApp(doc,{clear(){},async request(p){calls.push(p);if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};if(p==='/sessions/s')return {id:'s',title:'Exact chat title'};if(p==='/sessions/s/messages?latest=true&turn_boundary=true')return {items:[],offset:0,total:0};if(p==='/approvals')return {items:[approvalItem()]};if(p==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};if(p==='/auth/verify/finish')return {ok:true};if(p==='/approvals/a/decision')return {status:'resolved'};return {items:[]};}},win);
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
  if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
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
  while(!calls.some(call=>call.p==='/auth/verify/options'))await tick();
  credential.resolve(fakeCredential());
  while(!calls.some(call=>call.p==='/approvals/a/decision'))await tick();
  win.dispatchEvent(new win.Event('focus'));await tick();
  assert.equal(card.querySelector('h3').textContent,'Approval unavailable');
  decision.resolve({status:'resolved'});await tick();
  assert.equal(calls.filter(call=>call.p==='/approvals/a/decision').length,1);
  assert.equal(card.querySelector('h3').textContent,'Resolved');
  assert.equal(card.querySelector('button'),null);
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
    if(p==='/inbox/n/target')return {kind:'approval',session_id:'s',run_id:'r',approval_id:'a',request_id:'native-a'};
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
