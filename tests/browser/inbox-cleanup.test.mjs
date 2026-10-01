import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(resolve=>setTimeout(resolve,5));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function control(doc,text){const node=[...doc.querySelectorAll('button')].find(n=>n.getAttribute('aria-label')===text || n.textContent.trim()===text);assert.ok(node,`control ${text} exists`);return node;}
async function setup({items,readCount,mutation}={}){
 const dom=new JSDOM('<div id="app"></div>',{url:'https://example.test/hermes/'}),win=dom.window,doc=win.document,calls=[];
 let user='alice',receipts=items ?? [{id:'read/1',title:'Read receipt',body:'Kept conversation',read:true,session_id:'native'},{id:'new',title:'Unread receipt',body:'Unread',read:false}];
 const approvals=[{id:'approval',title:'Pending approval',action:'Review separately'}];
 const app=await mountApp(doc,{clear(){},async request(path,options={}){
  calls.push({path,options});
  if(path==='/auth/me')return {user:{id:user,status:'ready'}};
  if(path.startsWith('/sessions?'))return {items:[],total:0};
  if(path==='/inbox')return {items:receipts,read_count:readCount ?? receipts.filter(i=>i.read).length};
  if(path==='/approvals')return {items:approvals};
  if(options.method==='POST' && path.startsWith('/inbox/')){
   if(mutation)await mutation(path,options);
   if(path==='/inbox/clear-read')receipts=receipts.filter(i=>!i.read);
   else if(path.endsWith('/dismiss'))receipts=receipts.filter(i=>encodeURIComponent(i.id)!==path.split('/')[2]);
   else if(path.endsWith('/read'))receipts=receipts.map(i=>encodeURIComponent(i.id)===path.split('/')[2]?{...i,read:true}:i);
   return {ok:true,dismissed:1};
  }
  return {items:[]};
 }},win);
 control(doc,'Inbox').click();await tick();
 return {doc,app,calls,setUser(id){user=id;},close(){app.destroy();win.close();}};
}

for(const label of ['Dismiss','Clear read','Open notification'])for(const departure of ['route','account'])test(`${label} completion cannot navigate after ${departure} changes`,async()=>{
 const gate=deferred(),h=await setup({mutation:()=>gate.promise});try{
  (label==='Open notification'?h.doc.querySelector('.inbox-item.unread summary'):control(h.doc,label)).click();await tick();if(label==='Clear read'){control(h.doc,'Confirm change').click();await tick();}
  if(departure==='account'){h.setUser('bob');await h.app.start();}else{control(h.doc,'Chats').click();await tick();}
  const inboxGets=h.calls.filter(c=>c.path==='/inbox').length;
  gate.resolve();await tick();
  assert.equal(h.calls.filter(c=>c.path==='/inbox').length,inboxGets,'stale completion must not refresh or navigate');
  assert.equal(h.doc.querySelector('nav [aria-current=page]').dataset.view,'chats');
 }finally{h.close();}
});

test('stale clear confirmation must not mutate after navigation',async()=>{
 const h=await setup();try{
  control(h.doc,'Clear read').click();await tick();control(h.doc,'Chats').click();await tick();
  control(h.doc,'Confirm change').click();await tick();
  assert.equal(h.calls.some(c=>c.path==='/inbox/clear-read'),false);
  assert.equal(h.doc.querySelector('nav [aria-current=page]').dataset.view,'chats');
 }finally{h.close();}
});

test('late cleanup errors cannot expire or overwrite a new account',async()=>{
 const gate=deferred(),h=await setup({mutation:()=>gate.promise});try{
  control(h.doc,'Dismiss').click();await tick();h.setUser('bob');await h.app.start();
  gate.reject(Object.assign(new Error('Old account expired'),{status:401}));await tick();
  assert.ok(h.doc.querySelector('nav'));assert.doesNotMatch(h.doc.body.textContent,/Old account expired|Your session expired/);
  assert.equal(h.doc.querySelector('nav [aria-current=page]').dataset.view,'chats');
 }finally{h.close();}
});

test('Clear read failure preserves receipts, enables retry, and never reports success',async()=>{
 const gate=deferred(),h=await setup({mutation:()=>gate.promise});try{
  control(h.doc,'Clear read').click();await tick();control(h.doc,'Confirm change').click();await tick();
  assert.equal(control(h.doc,'Clear read').disabled,true);control(h.doc,'Clear read').click();
  assert.equal(h.calls.filter(c=>c.path==='/inbox/clear-read').length,1);
  gate.reject(new Error('Database unavailable'));await tick();
  assert.equal(control(h.doc,'Clear read').disabled,false);assert.equal(h.doc.querySelectorAll('.inbox-item').length,2);
  assert.match(h.doc.body.textContent,/Database unavailable/);assert.equal(h.doc.querySelectorAll('.approval').length,1);
 }finally{h.close();}
});

test('opening an unread summary exposes Dismiss only after read success',async()=>{
 const h=await setup({items:[{id:'new',title:'New',body:'B',read:false}]});try{
  assert.equal(control(h.doc,'Clear read').disabled,true);h.doc.querySelector('.inbox-item.unread summary').click();await tick();
  assert.ok(control(h.doc,'Dismiss'));assert.equal(control(h.doc,'Clear read').disabled,false);
 }finally{h.close();}
});

test('Clear read confirms all pages, cancel does nothing, no passkey and unread/approvals stay',async()=>{
 const h=await setup();try{
  const clear=control(h.doc,'Clear read');assert.equal(clear.disabled,false);clear.click();await tick();
  const dialog=h.doc.querySelector('[role=dialog]');assert.ok(dialog);
  assert.match(dialog.textContent,/all.*read/i);assert.match(dialog.textContent,/unread/i);assert.match(dialog.textContent,/approval/i);assert.match(dialog.textContent,/conversation/i);
  control(h.doc,'Cancel').click();await tick();assert.equal(h.calls.some(c=>c.path==='/inbox/clear-read'),false);
  control(h.doc,'Clear read').click();await tick();control(h.doc,'Confirm change').click();await tick();
  assert.equal(h.calls.filter(c=>c.path==='/inbox/clear-read').length,1);
  assert.equal(h.doc.querySelectorAll('.inbox-item').length,1);assert.ok(control(h.doc,'Approve once'));
  assert.equal(control(h.doc,'Clear read').disabled,true);
  assert.equal(h.calls.some(c=>c.path.startsWith('/auth/verify')),false);
 }finally{h.close();}
});

test('Clear read is enabled by global count even when page has only unread receipts',async()=>{
 const h=await setup({items:[{id:'new',title:'Unread',body:'B',read:false}],readCount:205});try{
  assert.equal(control(h.doc,'Clear read').disabled,false);control(h.doc,'Clear read').click();await tick();
  control(h.doc,'Confirm change').click();await tick();assert.equal(h.calls.filter(c=>c.path==='/inbox/clear-read').length,1);
 }finally{h.close();}
});

test('Dismiss exists only on read receipts; busy/error are honest, success keeps unread/approvals and no step-up',async()=>{
 const gate=deferred();let fail=true;
 const h=await setup({mutation:async()=>{if(fail){await gate.promise;throw new Error('Cleanup unavailable');}}});
 try{
  assert.equal(h.doc.querySelectorAll('.inbox-item').length,2);
  assert.equal(h.doc.querySelector('.inbox-item.unread').textContent.includes('Dismiss'),false);
  const dismiss=control(h.doc,'Dismiss');dismiss.click();dismiss.click();
  assert.equal(dismiss.disabled,true);assert.equal(h.calls.filter(c=>c.path.endsWith('/dismiss')).length,1);
  gate.resolve();await tick();assert.match(h.doc.body.textContent,/Cleanup unavailable/);
  assert.equal(dismiss.disabled,false);assert.equal(h.doc.querySelectorAll('.inbox-item').length,2);
  fail=false;dismiss.click();await tick();
  assert.equal(h.doc.querySelectorAll('.inbox-item').length,1);
  assert.ok(h.doc.querySelector('.inbox-item.unread summary'));assert.ok(control(h.doc,'Approve once'));assert.ok(control(h.doc,'Deny'));
  assert.equal(h.calls.some(c=>/auth\/verify|\/decision|\/sessions\//.test(c.path)),false);
  assert.equal(h.calls.find(c=>c.path.endsWith('/dismiss')).path,'/inbox/read%2F1/dismiss');
 }finally{h.close();}
});
