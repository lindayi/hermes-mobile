import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(resolve=>setTimeout(resolve,5));
function button(doc,name){return [...doc.querySelectorAll('button')].find(el=>el.getAttribute('aria-label')===name || el.textContent===name);}
async function setup({available=true,status='idle',remove,listing,request}={}){
 const win=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/'}).window,doc=win.document,calls=[];let user='owner',items=[{id:'session /1',title:'Saved chat',source:'cli',run_status:status}];
 const api={clear(){},async request(path,options={}){calls.push({path,options});if(request){const result=request(path,options);if(result!==undefined)return result;}if(path==='/auth/me')return {user:{id:user,status:'ready'}};if(path.startsWith('/sessions?'))return listing?listing(path,items):{deletion_available:available,items,total:items.length};if(path==='/sessions/session%20%2F1' && options.method==='DELETE'){if(remove)return remove(path,options);items=[];return {id:'session /1',deleted:true};}return {items:[]};}};
 const app=await mountApp(doc,api,win);
 return {win,doc,calls,app,changeUser(value){user=value;},deleteButton(){const remove=button(doc,'Delete conversation: Saved chat');if(remove?.hidden)button(doc,'Conversation actions: Saved chat').click();return remove;},close(){app.destroy();win.close();}};
}

test('supported history offers a separate delete control and cancellation sends nothing',async()=>{
 const h=await setup();try{
  const control=h.deleteButton();assert.ok(control,'delete action is discoverable');assert.equal(control.closest('.session-row'),null,'never nest delete in row button');
  control.click();await tick();const dialog=h.doc.querySelector('[role=dialog]');assert.ok(dialog);assert.equal(dialog.querySelector('.confirmation-copy')?.tagName,'P','ordinary confirmation prose is not a code block');assert.match(dialog.textContent,/Saved chat/);assert.match(dialog.textContent,/shared|devices/i);assert.match(dialog.textContent,/cannot be undone/i);assert.equal(h.doc.activeElement,button(h.doc,'Cancel'));
  button(h.doc,'Cancel').click();await tick();assert.equal(h.calls.some(c=>c.options.method==='DELETE'),false);assert.ok(button(h.doc,'Saved chat'));assert.equal(h.doc.activeElement,control);
 }finally{h.close();}
});
test('confirmed deletion sends exact target once and clears only its local state after verified success',async()=>{
 const h=await setup();try{
  h.win.sessionStorage.setItem('hermes:owner:draft:session /1','old draft');h.win.localStorage.setItem('hermes:owner:run:session /1','oldrun');h.win.sessionStorage.setItem('hermes:owner:draft:other','keep');
  h.deleteButton().click();await tick();button(h.doc,'Delete conversation').click();await tick();
  const sent=h.calls.filter(c=>c.options.method==='DELETE');assert.equal(sent.length,1);assert.equal(sent[0].path,'/sessions/session%20%2F1');assert.deepEqual(sent[0].options.body,{confirm:true});
  assert.equal(button(h.doc,'Saved chat'),undefined);assert.equal(h.win.sessionStorage.getItem('hermes:owner:draft:session /1'),null);assert.equal(h.win.localStorage.getItem('hermes:owner:run:session /1'),null);assert.equal(h.win.sessionStorage.getItem('hermes:owner:draft:other'),'keep');assert.match(h.doc.body.textContent,/Conversation deleted/);
 }finally{h.close();}
});

for(const status of ['queued','running','waiting_for_approval','stopping'])test(`known ${status} conversation cannot start deletion`,async()=>{
 const h=await setup({status});try{assert.equal(h.deleteButton().disabled,true);h.deleteButton().click();await tick();assert.equal(h.doc.querySelector('[role=dialog]'),null);assert.equal(h.calls.some(c=>c.options.method==='DELETE'),false);}finally{h.close();}
});
for(const response of [{id:'session /1',deleted:false},{id:'different',deleted:true},null])test(`unverified deletion retains the row and draft: ${JSON.stringify(response)}`,async()=>{
 const h=await setup({remove:async()=>response});try{h.win.sessionStorage.setItem('hermes:owner:draft:session /1','keep');h.deleteButton().click();await tick();button(h.doc,'Delete conversation').click();await tick();assert.ok(button(h.doc,'Saved chat'));assert.equal(h.win.sessionStorage.getItem('hermes:owner:draft:session /1'),'keep');assert.doesNotMatch(h.doc.body.textContent,/Conversation deleted\./);assert.match(h.doc.body.textContent,/not be confirmed|outcome.*unknown/i);}finally{h.close();}
});
test('unknown transport result explains uncertainty and never retries automatically',async()=>{
 const h=await setup({remove:async()=>{throw new Error('connection lost');}});try{h.deleteButton().click();await tick();button(h.doc,'Delete conversation').click();await tick();assert.equal(h.calls.filter(c=>c.options.method==='DELETE').length,1);assert.ok(button(h.doc,'Saved chat'));assert.match(h.doc.body.textContent,/outcome.*unknown/i);}finally{h.close();}
});
test('late verified deletion clears the old target cache without navigating a newer view',async()=>{
 let finish;const h=await setup({remove:()=>new Promise(resolve=>{finish=resolve;})});try{
  h.win.sessionStorage.setItem('hermes:owner:draft:session /1','remove');h.win.sessionStorage.setItem('hermes:owner:draft:other','keep');h.deleteButton().click();await tick();button(h.doc,'Delete conversation').click();await tick();button(h.doc,'Inbox').click();await tick();const before=h.calls.filter(c=>c.path.startsWith('/sessions?')).length;finish({id:'session /1',deleted:true});await tick();assert.equal(h.win.sessionStorage.getItem('hermes:owner:draft:session /1'),null);assert.equal(h.win.sessionStorage.getItem('hermes:owner:draft:other'),'keep');assert.equal(h.doc.querySelector('[aria-current=page]').dataset.view,'inbox');assert.equal(h.calls.filter(c=>c.path.startsWith('/sessions?')).length,before);
 }finally{h.close();}
});
test('deleting last item on a later page repairs paging without dropping search/filter',async()=>{
 const h=await setup({listing:(path,items)=>({deletion_available:true,items:items.length?items:[],total:items.length?31:30})});try{
  button(h.doc,'Next').click();await tick();h.deleteButton().click();await tick();button(h.doc,'Delete conversation').click();await tick();const last=new URL(h.calls.filter(c=>c.path.startsWith('/sessions?')).at(-1).path,'https://x');assert.equal(last.searchParams.get('offset'),'0');assert.equal(last.searchParams.get('kind'),'chats');assert.equal(button(h.doc,'Previous').disabled,true);
 }finally{h.close();}
});
for(const departure of ['route','account'])test(`confirmation after ${departure} change cannot send deletion`,async()=>{
 const h=await setup();try{h.deleteButton().click();await tick();const confirm=button(h.doc,'Delete conversation');if(departure==='account'){h.changeUser('other');await h.app.start();}else{button(h.doc,'Inbox').click();await tick();}confirm.click();await tick();assert.equal(h.calls.some(c=>c.options.method==='DELETE'),false);}finally{h.close();}
});
for(const outcome of ['success','auth-error'])test(`pending deletion ${outcome} cannot affect a new account`,async()=>{
 let resolve,reject;const h=await setup({remove:()=>new Promise((a,b)=>{resolve=a;reject=b;})});try{h.deleteButton().click();await tick();button(h.doc,'Delete conversation').click();await tick();h.changeUser('other');await h.app.start();h.win.sessionStorage.setItem('hermes:other:draft:session /1','keep');if(outcome==='success')resolve({id:'session /1',deleted:true});else reject(Object.assign(new Error('old auth'),{status:401}));await tick();assert.equal(h.win.sessionStorage.getItem('hermes:other:draft:session /1'),'keep');assert.ok(h.doc.querySelector('nav'));assert.doesNotMatch(h.doc.body.textContent,/Conversation deleted|old auth|session expired/i);}finally{h.close();}
});
test('double confirmation and repeated row clicks dispatch only one deletion',async()=>{
 let resolve;const h=await setup({remove:()=>new Promise(r=>{resolve=r;})});try{h.deleteButton().click();h.deleteButton().click();await tick();assert.equal(h.doc.querySelectorAll('[role=dialog]').length,1);const confirm=button(h.doc,'Delete conversation');confirm.click();confirm.click();await tick();h.deleteButton().click();await tick();assert.equal(h.calls.filter(c=>c.options.method==='DELETE').length,1);resolve({id:'session /1',deleted:true});await tick();}finally{h.close();}
});
test('unsupported backend does not advertise deletion',async()=>{const h=await setup({available:false});try{assert.equal(h.deleteButton(),undefined);}finally{h.close();}});

async function confirmDeletion(h){h.deleteButton().click();await tick();button(h.doc,'Delete conversation').click();await tick();}
test('receipt tears down a reopened exact transcript and its detached draft callback',async()=>{
 let finish;const h=await setup({remove:()=>new Promise(r=>finish=r)});try{
  await confirmDeletion(h);button(h.doc,'Saved chat').click();await tick();
  const input=h.doc.querySelector('textarea[name=message]');assert.ok(input);input.value='before';input.dispatchEvent(new h.win.Event('input'));
  finish({id:'session /1',deleted:true});await tick();
  assert.equal(h.doc.querySelector('.messages'),null,'verified deletion tears down the newer same-target transcript');
  input.value='detached callback';input.dispatchEvent(new h.win.Event('input'));
  assert.equal(h.win.sessionStorage.getItem('hermes:owner:draft:session /1'),null);
  assert.equal(h.app.state.session,null);assert.equal(h.calls.filter(c=>c.options.method==='DELETE').length,1);
 }finally{h.close();}
});

test('fresh Chats list keeps the pending identity locked and removes it on receipt despite stale catalogue',async()=>{
 let finish;const h=await setup({remove:()=>new Promise(r=>finish=r)});try{
  await confirmDeletion(h);button(h.doc,'Inbox').click();await tick();button(h.doc,'Chats').click();await tick();
  assert.equal(h.deleteButton().disabled,true,'fresh row cannot submit another DELETE');
  finish({id:'session /1',deleted:true});await tick();assert.equal(button(h.doc,'Saved chat'),undefined);
  button(h.doc,'Inbox').click();await tick();button(h.doc,'Chats').click();await tick();assert.equal(button(h.doc,'Saved chat'),undefined,'stale list cannot resurrect a verified deleted identity');
  assert.equal(h.calls.filter(c=>c.options.method==='DELETE').length,1);
 }finally{h.close();}
});

test('durable pending summaries survive filters/pages without capability and explicit GET success clears only exact cache',async()=>{
 const id='root / <img>:1';let pending=true;
 const h=await setup({listing:()=>({deletion_available:false,items:[],total:61,...(pending?{pending_deletions:[{id,status:'unconfirmed'}]}:{})}),request:(path,options)=>{if(path===`/sessions/${encodeURIComponent(id)}/deletion`){assert.equal(options.method,'GET');pending=false;return {id,deleted:true};}}});try{
  h.win.sessionStorage.setItem(`hermes:owner:draft:${id}`,'target');h.win.localStorage.setItem(`hermes:owner:run:${id}`,'run');h.win.sessionStorage.setItem(`hermes:owner:draft:${id}:neighbor`,'keep');h.win.sessionStorage.setItem(`hermes:other:draft:${id}`,'other');
  assert.match(h.doc.body.textContent,/Deletion status unconfirmed/);assert.ok(button(h.doc,'Check status'));assert.equal(h.doc.querySelector('.pending-deletions img'),null);
  button(h.doc,'All').click();await tick();button(h.doc,'Next').click();await tick();assert.ok(button(h.doc,'Check status'));assert.match(h.doc.body.textContent,/root \/ <img>:1/);
  button(h.doc,'Check status').click();await tick();assert.equal(h.calls.filter(c=>c.options.method==='DELETE').length,0);assert.equal(h.calls.filter(c=>c.path.endsWith('/deletion')).length,1);
  assert.equal(h.win.sessionStorage.getItem(`hermes:owner:draft:${id}`),null);assert.equal(h.win.localStorage.getItem(`hermes:owner:run:${id}`),null);assert.equal(h.win.sessionStorage.getItem(`hermes:owner:draft:${id}:neighbor`),'keep');assert.equal(h.win.sessionStorage.getItem(`hermes:other:draft:${id}`),'other');assert.equal(button(h.doc,'Check status'),undefined);assert.match(h.doc.body.textContent,/Conversation deleted\./);
 }finally{h.close();}
});

test('status 409 restores catalogue row and retains draft with an honest refusal notice',async()=>{
 let pending=true;const h=await setup({listing:(path,items)=>({deletion_available:true,items:pending?[]:items,total:pending?0:1,...(pending?{pending_deletions:[{id:'session /1',status:'unconfirmed'}]}:{})}),request:path=>{if(path.endsWith('/deletion')){pending=false;return Promise.reject(Object.assign(new Error('safe refusal'),{status:409}));}}});try{
  h.win.sessionStorage.setItem('hermes:owner:draft:session /1','keep');button(h.doc,'Check status').click();await tick();
  assert.ok(button(h.doc,'Saved chat'),'safe refusal refresh restores unchanged history');assert.equal(button(h.doc,'Check status'),undefined);assert.equal(h.deleteButton().disabled,false);
  assert.match(h.doc.body.textContent,/not deleted|deletion refused/i);assert.doesNotMatch(h.doc.body.textContent,/Conversation deleted\./);assert.equal(h.win.sessionStorage.getItem('hermes:owner:draft:session /1'),'keep');assert.equal(h.calls.filter(c=>c.options.method==='DELETE').length,0);
 }finally{h.close();}
});

test('ambiguous DELETE offers GET-only recovery without enabling a destructive retry on any list',async()=>{
 const h=await setup({remove:async()=>{throw new Error('lost response');},request:path=>path.endsWith('/deletion')?Promise.reject(Object.assign(new Error('unknown'),{status:503})):undefined});try{
  await confirmDeletion(h);assert.ok(button(h.doc,'Check status'),'ambiguous local outcome is recoverable');assert.equal(h.deleteButton().disabled,true);
  button(h.doc,'Check status').click();await tick();assert.match(h.doc.body.textContent,/Deletion status unconfirmed/);assert.equal(h.deleteButton().disabled,true);
  button(h.doc,'Inbox').click();await tick();button(h.doc,'Chats').click();await tick();assert.equal(h.deleteButton().disabled,true);assert.ok(button(h.doc,'Check status'));h.deleteButton().click();await tick();assert.equal(h.doc.querySelector('[role=dialog]'),null);
  assert.equal(h.calls.filter(c=>c.options.method==='DELETE').length,1);assert.equal(h.calls.filter(c=>c.path.endsWith('/deletion')).length,1);
 }finally{h.close();}
});

test('destroyed UI cannot be refreshed or cleared by a late deletion receipt',async()=>{
 let finish;const h=await setup({remove:()=>new Promise(r=>finish=r)});try{
  await confirmDeletion(h);h.win.sessionStorage.setItem('hermes:owner:draft:session /1','keep');h.app.destroy();const before=h.doc.body.innerHTML,count=h.calls.length;
  finish({id:'session /1',deleted:true});await tick();assert.equal(h.doc.body.innerHTML,before);assert.equal(h.calls.length,count);assert.equal(h.win.sessionStorage.getItem('hermes:owner:draft:session /1'),'keep');
 }finally{h.close();}
});

for(const status of [404,409])test(`direct DELETE ${status} retains history and unlocks the exact refused action`,async()=>{
 const h=await setup({remove:async()=>{throw Object.assign(new Error('Deletion refused'),{status});}});try{await confirmDeletion(h);assert.ok(button(h.doc,'Saved chat'));assert.equal(h.deleteButton().disabled,false);assert.match(h.doc.body.textContent,/refused/i);assert.equal(button(h.doc,'Check status'),undefined);}finally{h.close();}
});

for(const outcome of ['503','transport','wrong-id','false','null'])test(`status ${outcome} stays unconfirmed and keeps exact cache without mutation`,async()=>{
 const id='session /1';const h=await setup({listing:()=>({items:[],total:0,pending_deletions:[{id,status:'unconfirmed'}]}),request:path=>{if(!path.endsWith('/deletion'))return;return outcome==='503'?Promise.reject(Object.assign(new Error('unknown'),{status:503})):outcome==='transport'?Promise.reject(new Error('offline')):outcome==='wrong-id'?{id:'other',deleted:true}:outcome==='false'?{id,deleted:false}:null;}});try{
  h.win.sessionStorage.setItem('hermes:owner:draft:'+id,'keep');button(h.doc,'Check status').click();await tick();assert.ok(button(h.doc,'Check status'));assert.match(h.doc.body.textContent,/Deletion status unconfirmed/);assert.doesNotMatch(h.doc.body.textContent,/Conversation deleted\./);assert.equal(h.win.sessionStorage.getItem('hermes:owner:draft:'+id),'keep');assert.equal(h.calls.filter(c=>c.options.method==='DELETE').length,0);
 }finally{h.close();}
});
for(const outcome of ['success','409','401','503'])test(`late status ${outcome} never changes a newer account`,async()=>{
 let resolve,reject;const h=await setup({listing:()=>({items:[],total:0,pending_deletions:[{id:'session /1',status:'unconfirmed'}]}),request:path=>path.endsWith('/deletion')?new Promise((a,b)=>{resolve=a;reject=b;}):undefined});try{
  button(h.doc,'Check status').click();await tick();h.changeUser('other');await h.app.start();h.win.sessionStorage.setItem('hermes:other:draft:session /1','keep');const before=h.doc.body.innerHTML;
  if(outcome==='success')resolve({id:'session /1',deleted:true});else reject(Object.assign(new Error('old response'),{status:Number(outcome)}));await tick();assert.equal(h.doc.body.innerHTML,before);assert.equal(h.win.sessionStorage.getItem('hermes:other:draft:session /1'),'keep');
 }finally{h.close();}
});
test('slow same-target messages cannot resurrect after receipt',async()=>{
 let finish,messages;const h=await setup({remove:()=>new Promise(r=>finish=r),request:path=>path.includes('/messages?')?new Promise(r=>messages=r):undefined});try{
  await confirmDeletion(h);button(h.doc,'Saved chat').click();await tick();finish({id:'session /1',deleted:true});await tick();messages({items:[{role:'assistant',content:'must not resurrect'}]});await tick();assert.equal(h.doc.querySelector('.messages'),null);assert.doesNotMatch(h.doc.body.textContent,/must not resurrect/);
 }finally{h.close();}
});
test('receipt refresh fences a partial history load and cannot overwrite a subsequent unrelated view',async()=>{
 let finish,stale,fresh;let reads=0;const h=await setup({remove:()=>new Promise(r=>finish=r),listing:(path,items)=>{reads++;if(reads===2)return new Promise(r=>stale=r);if(reads===3)return new Promise(r=>fresh=r);return {items,deletion_available:true,total:1};}});try{
  await confirmDeletion(h);button(h.doc,'Inbox').click();await tick();button(h.doc,'Chats').click();await tick();finish({id:'session /1',deleted:true});await tick();
  button(h.doc,'Inbox').click();await tick();const before=h.doc.body.innerHTML;
  stale({items:[{id:'session /1',title:'Stale deleted row'}],total:1,deletion_available:true});fresh({items:[],total:0});await tick();assert.equal(h.doc.body.innerHTML,before);assert.equal(h.app.state.view,'inbox');
 }finally{h.close();}
});
test('checking is identity scoped across fresh lists and repeated detached button clicks',async()=>{
 let finish;const h=await setup({listing:()=>({items:[],total:0,pending_deletions:[{id:'session /1',status:'unconfirmed'}]}),request:path=>path.endsWith('/deletion')?new Promise(r=>finish=r):undefined});try{
  const check=button(h.doc,'Check status');check.click();check.click();await tick();button(h.doc,'Inbox').click();await tick();button(h.doc,'Chats').click();await tick();assert.equal(button(h.doc,'Checking status…').disabled,true);assert.equal(h.calls.filter(c=>c.path.endsWith('/deletion')).length,1);finish({id:'session /1',deleted:true});await tick();assert.equal(button(h.doc,'Check status'),undefined);
 }finally{h.close();}
});

test('an old Inbox link cannot reopen a verified deleted exact identity',async()=>{
 const h=await setup({request:path=>path==='/inbox'?{items:[{id:'notification',title:'Old result',session_id:'session /1',body:'kept notification'}]}:undefined});try{
  await confirmDeletion(h);button(h.doc,'Inbox').click();await tick();const reads=h.calls.filter(c=>c.path.includes('/messages?')).length;button(h.doc,'Open conversation').click();await tick();assert.equal(h.doc.querySelector('.messages'),null);assert.equal(h.app.state.view,'inbox');assert.equal(h.calls.filter(c=>c.path.includes('/messages?')).length,reads);assert.match(h.doc.body.textContent,/deleted/i);
 }finally{h.close();}
});

test('returning to the original account after an ignored receipt allows safe status recovery',async()=>{
 let finish;const h=await setup({listing:()=>({items:[],total:0,pending_deletions:[{id:'session /1',status:'unconfirmed'}]}),request:path=>path.endsWith('/deletion')?new Promise(r=>finish=r):undefined});try{
  button(h.doc,'Check status').click();await tick();h.changeUser('other');await h.app.start();finish({id:'session /1',deleted:true});await tick();h.changeUser('owner');await h.app.start();assert.ok(button(h.doc,'Check status'),'ignored callback must not leave former account checking forever');assert.equal(h.calls.filter(c=>c.path.endsWith('/deletion')).length,1);
 }finally{h.close();}
});

test('ignored cross-account DELETE remains recoverable on return instead of stuck deleting',async()=>{
 let finish;const h=await setup({remove:()=>new Promise(r=>finish=r)});try{await confirmDeletion(h);h.changeUser('other');await h.app.start();finish({id:'session /1',deleted:true});await tick();h.changeUser('owner');await h.app.start();assert.ok(button(h.doc,'Check status'));assert.equal(h.deleteButton().disabled,true);}finally{h.close();}
});

for(const outcome of ['success','409','503'])test(`status ${outcome} preserves a newer unrelated transcript and its draft`,async()=>{
 let finish,reject;const h=await setup({listing:()=>({items:[{id:'other',title:'Other chat',run_status:'idle'}],total:1,pending_deletions:[{id:'session /1',status:'unconfirmed'}]}),request:path=>path.endsWith('/deletion')?new Promise((a,b)=>{finish=a;reject=b;}):undefined});try{
  button(h.doc,'Check status').click();await tick();button(h.doc,'Other chat').click();await tick();const input=h.doc.querySelector('textarea[name=message]');input.value='keep this draft';input.dispatchEvent(new h.win.Event('input'));const before=h.doc.body.innerHTML,count=h.calls.filter(c=>c.path.startsWith('/sessions?')).length;
  if(outcome==='success')finish({id:'session /1',deleted:true});else reject(Object.assign(new Error('old outcome'),{status:Number(outcome)}));await tick();assert.equal(h.doc.body.innerHTML,before);assert.equal(h.win.sessionStorage.getItem('hermes:owner:draft:other'),'keep this draft');assert.equal(h.calls.filter(c=>c.path.startsWith('/sessions?')).length,count);
 }finally{h.close();}
});
test('verified deletion retains nonempty search/filter while repairing last page',async()=>{
 let gone=false;const h=await setup({remove:async()=>{gone=true;return {id:'session /1',deleted:true};},listing:(path,items)=>({items:gone?[]:items,deletion_available:true,total:gone?30:31})});try{
  button(h.doc,'Search conversations').click();h.doc.querySelector('input[type=search]').value='needle / alpha';button(h.doc,'All').click();await tick();button(h.doc,'Next').click();await tick();await confirmDeletion(h);const last=new URL(h.calls.filter(c=>c.path.startsWith('/sessions?')).at(-1).path,'https://fixture');assert.equal(last.searchParams.get('q'),'needle / alpha');assert.equal(last.searchParams.get('kind'),'all');assert.equal(last.searchParams.get('offset'),'0');
 }finally{h.close();}
});
