import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(resolve=>setTimeout(resolve,5));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const row=(title,id=title)=>({id,title,source:'cli',run_status:'idle'});
const page=(title,total=61)=>({items:[row(title)],total,deletion_available:true});
const button=(doc,name)=>[...doc.querySelectorAll('button')].find(el=>el.getAttribute('aria-label')===name || el.textContent===name);
async function setup({request}={}) {
 const win=new JSDOM('<div id="app"></div>',{url:'https://list-fixture.invalid/hermes/'}).window,doc=win.document,reads=[],calls=[];
 let owner='owner',slow=false;
 const api={clear(){},async request(path,options={}){
  calls.push({path,options});const override=request?.(path,options);if(override!==undefined)return override;
  if(path==='/auth/me')return {user:{id:owner,status:'ready'}};
  if(path.startsWith('/sessions?')){if(!slow)return page('Original');const d=deferred();reads.push({...d,url:new URL(path,'https://fixture')});return d.promise;}
  return {items:[]};
 }};
 const app=await mountApp(doc,api,win);
 return {win,doc,app,reads,calls,slow(value=true){slow=value;},owner(value){owner=value;},search(value){button(doc,'Search conversations').getAttribute('aria-expanded')==='false' && button(doc,'Search conversations').click();const input=doc.querySelector('input[type=search]');input.value=value;input.focus();input.form.dispatchEvent(new win.Event('submit',{bubbles:true,cancelable:true}));return input;},close(){app.destroy();win.close();}};
}

test('detached list controls cannot change another owner’s query/filter/page or start a new chat',async()=>{
 const h=await setup();try{
  const form=h.doc.querySelector('form.search'),input=h.doc.querySelector('input'),all=button(h.doc,'All'),next=button(h.doc,'Next'),create=button(h.doc,'New chat');
  h.owner('other');await h.app.start();const calls=h.calls.length;input.value='old private query';form.dispatchEvent(new h.win.Event('submit',{cancelable:true}));all.click();next.click();create.click();await tick();
  assert.equal(h.app.state.query,'');assert.equal(h.app.state.kind,'chats');assert.equal(h.app.state.offset,0);assert.equal(h.calls.length,calls);
 }finally{h.close();}
});

test('New chat button sends only the New chat placeholder and leaves untitled native creation unresolved',async()=>{
 const h=await setup({request:(path,options)=>{
  if(path==='/sessions' && options.method==='POST')return {id:'native-untitled'};
  if(path==='/sessions/native-untitled')return {id:'native-untitled',title:null};
  if(path.includes('/messages'))return {items:[]};
 }});try{
  button(h.doc,'New chat').click();await tick();
  const create=h.calls.find(call=>call.path==='/sessions' && call.options.method==='POST');
  assert.deepEqual(create.options.body,{title:'New chat'});
  assert.equal(h.doc.querySelector('.conversation-head h1').textContent,'Untitled conversation');
  assert.ok(h.calls.some(call=>call.path==='/sessions/native-untitled'),'untitled creation reads native metadata instead of treating the placeholder as a saved title');
 }finally{h.close();}
});

test('old deletion actions explicitly lock through a failed refresh and recover only on a fresh result',async()=>{
 const h=await setup();try{
  const remove=button(h.doc,'Delete conversation: Original');h.slow();h.search('pending');assert.equal(remove.disabled,true,'stale delete must not look actionable');assert.match(remove.title,/updating|refresh/i);
  h.reads[0].reject(new Error('Synthetic unavailable'));await tick();assert.equal(remove.disabled,true);assert.match(remove.title,/Try again/i);assert.ok(button(h.doc,'Try again'));
  button(h.doc,'Try again').click();h.reads[1].resolve(page('Original'));await tick();assert.equal(button(h.doc,'Delete conversation: Original').disabled,false);assert.notEqual(button(h.doc,'Delete conversation: Original'),remove);
 }finally{h.close();}
});

test('filter and native pagination preserve controls while authoritative count and query advance together',async()=>{
 const h=await setup();try{
  h.slow();const input=h.search('draft query');h.reads[0].resolve(page('Matched',97));await tick();
  const heading=h.doc.querySelector('.page-heading'),list=h.doc.querySelector('.session-list'),filter=button(h.doc,'Filter conversations'),next=button(h.doc,'Next'),previous=button(h.doc,'Previous');
  input.value='new body text';button(h.doc,'All').click();assert.equal(h.doc.activeElement,filter);assert.ok(button(h.doc,'Matched'));assert.match(h.doc.querySelector('.pagination').textContent,/97 conversations/);
  assert.equal(h.reads[1].url.searchParams.get('q'),'new body text');assert.equal(h.reads[1].url.searchParams.get('kind'),'all');
  input.focus();input.setSelectionRange(1,4);h.reads[1].resolve(page('All result',97));await tick();assert.equal(h.doc.activeElement,input,'late filter reply cannot steal newer focus');assert.equal(input.selectionEnd,4);
  next.click();assert.equal(h.reads[2].url.searchParams.get('offset'),'30');assert.equal(h.reads[2].url.searchParams.get('q'),'new body text');assert.ok(button(h.doc,'All result'));h.reads[2].resolve(page('Beyond first page',97));await tick();
  assert.ok(button(h.doc,'Beyond first page'));assert.equal(h.app.state.offset,30);assert.equal(h.doc.querySelector('.list-summary'),null);assert.equal(previous.disabled,false);assert.equal(h.doc.querySelector('.page-heading'),heading);assert.equal(h.doc.querySelector('.session-list'),list);assert.equal(button(h.doc,'Next'),next);assert.equal(button(h.doc,'Previous'),previous);assert.equal(h.doc.querySelector('input'),input);
  previous.click();assert.equal(h.reads[3].url.searchParams.get('offset'),'0');h.reads[3].resolve(page('First page',97));await tick();assert.equal(previous.disabled,true);
 }finally{h.close();}
});

for(const outcome of ['success','401','error'])test(`newest query wins over out-of-order stale ${outcome}`,async()=>{
 const h=await setup();try{
  h.slow();const input=h.search('old');h.search('new');h.reads[1].resolve(page('Newest',4));await tick();
  if(outcome==='success')h.reads[0].resolve({...page('Stale',900),pending_deletions:[{id:'private-stale',status:'unconfirmed'}]});else h.reads[0].reject(Object.assign(new Error('stale failure'),{status:outcome==='401'?401:503}));await tick();
  assert.ok(button(h.doc,'Newest'));assert.equal(button(h.doc,'Stale'),undefined);assert.match(h.doc.querySelector('.pagination').textContent,/4 conversations/);assert.doesNotMatch(h.doc.body.textContent,/private-stale|stale failure|session expired/);assert.equal(h.doc.querySelector('input'),input);assert.equal(h.doc.querySelector('.session-results').getAttribute('aria-busy'),'false');
 }finally{h.close();}
});

test('inline failure and retry retain honest old result labels/count and never overwrite newer unsent input',async()=>{
 const h=await setup();try{
  h.slow();const input=h.search('not loaded'),heading=h.doc.querySelector('.page-heading');h.reads[0].reject(new Error('Synthetic offline'));await tick();
  assert.ok(button(h.doc,'Original'));assert.equal(h.doc.querySelector('.list-summary'),null);assert.doesNotMatch(h.doc.querySelector('.session-list').textContent,/not loaded/);assert.match(h.doc.querySelector('.pagination').textContent,/61 conversations/);assert.match(h.doc.querySelector('.list-feedback').textContent,/not loaded.*Synthetic offline.*Previous results/);
  input.value='unsent editing';input.focus();input.setSelectionRange(2,5);button(h.doc,'Try again').click();assert.equal(h.reads[1].url.searchParams.get('q'),'not loaded');h.reads[1].resolve(page('Retry body match',1));await tick();
  assert.equal(input.value,'unsent editing');assert.equal(h.doc.activeElement,input);assert.equal(input.selectionStart,2);assert.equal(h.doc.querySelector('.page-heading'),heading);assert.ok(button(h.doc,'Retry body match'));assert.match(h.doc.querySelector('.pagination').textContent,/1 conversations/);assert.equal(h.doc.querySelector('.list-feedback').textContent.trim(),'');
 }finally{h.close();}
});

test('closing search clears only submitted search in place and fences its late response',async()=>{
 const h=await setup();try{
  h.slow();const input=h.search('pending'),heading=h.doc.querySelector('.page-heading'),toggle=button(h.doc,'Search conversations');
  button(h.doc,'Close search').click();assert.equal(h.doc.activeElement,toggle);assert.equal(input.form.hidden,true);assert.equal(h.reads[1].url.searchParams.get('q'),'');h.reads[1].resolve(page('Unfiltered',61));await tick();h.reads[0].resolve(page('Late searched',1));await tick();
  assert.equal(h.doc.querySelector('.page-heading'),heading);assert.equal(h.doc.querySelector('input'),input);assert.ok(button(h.doc,'Unfiltered'));assert.equal(button(h.doc,'Late searched'),undefined);assert.equal(h.doc.activeElement,toggle);
 }finally{h.close();}
});

for(const departure of ['inbox','account','logout','destroy'])for(const outcome of ['success','401'])test(`${departure} invalidates pending list ${outcome}`,async()=>{
 const h=await setup();try{
  h.slow();const oldInput=h.search('old owner query');
  if(departure==='inbox'){button(h.doc,'Inbox').click();await tick();}
  if(departure==='account'){h.slow(false);h.owner('new-owner');await h.app.start();}
  if(departure==='logout'){button(h.doc,'Settings').click();await tick();button(h.doc,'Sign out').click();await tick();}
  if(departure==='destroy')h.app.destroy();
  const before=h.doc.body.innerHTML;
  if(outcome==='success')h.reads[0].resolve(page('Private old response',99));else h.reads[0].reject(Object.assign(new Error('old unauthorized'),{status:401}));await tick();
  assert.equal(h.doc.body.innerHTML,before);if(departure==='account'){assert.equal(h.app.state.query,'');assert.notEqual(h.doc.querySelector('input'),oldInput);assert.equal(h.app.state.user.id,'new-owner');}
 }finally{h.close();}
});

test('current unauthorized list response removes authenticated content',async()=>{
 const h=await setup();try{h.slow();h.search('private');h.reads[0].reject(Object.assign(new Error('unauthorized'),{status:401}));await tick();assert.equal(h.doc.querySelector('nav'),null);assert.equal(h.doc.querySelector('.session-list'),null);assert.ok(button(h.doc,'Sign in with a passkey'));}finally{h.close();}
});

test('superseded empty-page repair cannot overwrite a newer query or its offset',async()=>{
 const h=await setup();try{
  h.slow();button(h.doc,'Next').click();h.reads[0].resolve({items:[],total:0});await tick();assert.equal(h.reads[1].url.searchParams.get('offset'),'0');
  h.search('fresh');h.reads[2].resolve(page('Fresh query',1));await tick();h.reads[1].resolve(page('Late repair',61));await tick();assert.ok(button(h.doc,'Fresh query'));assert.equal(button(h.doc,'Late repair'),undefined);assert.equal(h.app.state.offset,0);assert.equal(h.app.state.query,'fresh');
 }finally{h.close();}
});

test('verified deletion fences deferred list reads and never resurrects a deleted row',async()=>{
 const deletion=deferred();const h=await setup({request:(path,options)=>options.method==='DELETE'?deletion.promise:undefined});try{
  button(h.doc,'Conversation actions: Original').click();button(h.doc,'Delete conversation: Original').click();await tick();button(h.doc,'Delete conversation').click();await tick();
  h.slow();h.search('pending');deletion.resolve({id:'Original',deleted:true});await tick();assert.equal(h.reads.length,2);h.reads[1].resolve(page('Original',1));await tick();h.reads[0].resolve(page('Original',1));await tick();assert.equal(button(h.doc,'Original'),undefined);assert.equal(h.doc.querySelectorAll('.session-row').length,0);
 }finally{h.close();}
});

test('retained rows remain navigable during a slow search without allowing its late response into the transcript',async()=>{
 const h=await setup();try{
  h.slow();h.search('pending query');button(h.doc,'Original').click();await tick();
  assert.ok(h.doc.querySelector('textarea[name=message]'),'retained previous results are still usable');
  const input=h.doc.querySelector('textarea[name=message]');input.value='preserved draft';input.dispatchEvent(new h.win.Event('input'));
  h.reads[0].resolve(page('Late result'));await tick();assert.ok(h.doc.querySelector('.messages'));assert.equal(button(h.doc,'Late result'),undefined);assert.equal(input.value,'preserved draft');
 }finally{h.close();}
});

test('tool-limit runtime notice renders folded generic process history with unchanged body',async()=>{
 const body='Tool limit fixture body.\n\n**Retained** content <img src=x onerror=alert(1)>.';
 const h=await setup({request:path=>path.includes('/messages?')?{items:[{role:'user',content:'Actual human turn'},{role:'tool',kind:'runtime_notice',name:'Tool limit reached',status:'completed',content:body},{role:'tool',kind:'context_compression',name:'Context compression',status:'completed',content:'Existing compression body'}]}:undefined});try{
  button(h.doc,'Original').click();await tick();
  const process=[...h.doc.querySelectorAll('details.process-history')].find(el=>el.querySelector('summary')?.textContent.includes('Tool limit reached'));
  assert.ok(process,'runtime notice uses the same foldable process history as compression');assert.equal(process.open,false);assert.equal(process.dataset.status,'completed');assert.match(process.querySelector('.markdown').textContent,/Retained.*content/);assert.equal(process.querySelector('img'),null);
  assert.equal(h.doc.querySelectorAll('.user-message').length,1);assert.match(h.doc.querySelector('.user-message').textContent,/Actual human turn/);assert.ok(h.doc.querySelector('.context-compression'));
 }finally{h.close();}
});

test('filter replacement invalidates an already-open deletion confirmation',async()=>{
 const h=await setup();try{
  button(h.doc,'Conversation actions: Original').click();button(h.doc,'Delete conversation: Original').click();await tick();
  const confirm=button(h.doc,'Delete conversation');assert.ok(confirm);
  h.slow();button(h.doc,'All').click();h.reads[0].resolve({...page('Original'),deletion_available:false});await tick();
  confirm.click();await tick();
  assert.equal(h.calls.filter(c=>c.options.method==='DELETE').length,0,'a replaced capability/row cannot authorize the prior confirmation');
 }finally{h.close();}
});

test('search keeps mounted shell, input focus/selection and old rows during authoritative deferred reads',async()=>{
 const h=await setup();try{
  h.slow();const main=h.doc.querySelector('#main'),heading=h.doc.querySelector('.page-heading'),list=h.doc.querySelector('.session-list'),filter=button(h.doc,'Filter conversations');
  const input=h.search('body only / literal%');input.setSelectionRange(2,7);
  assert.equal(h.doc.querySelector('.page-heading'),heading,'search must not replace the heading with whole-page Loading');
  assert.equal(h.doc.querySelector('.session-list'),list);assert.ok(button(h.doc,'Original'),'old rows remain while fetching');
  assert.equal(h.doc.activeElement,input);assert.equal(input.selectionStart,2);assert.equal(input.selectionEnd,7);
  assert.equal(h.reads[0].url.searchParams.get('q'),'body only / literal%');
  h.reads[0].resolve(page('Body-only match',97));await tick();
  assert.equal(h.doc.querySelector('#main'),main);assert.equal(h.doc.querySelector('.page-heading'),heading);assert.equal(button(h.doc,'Filter conversations'),filter);assert.equal(h.doc.querySelector('.session-list'),list);assert.equal(h.doc.querySelector('input[type=search]'),input);assert.equal(h.doc.activeElement,input);assert.equal(input.selectionStart,2);assert.equal(input.selectionEnd,7);
  assert.ok(button(h.doc,'Body-only match'));assert.match(h.doc.querySelector('.pagination').textContent,/97 conversations/);assert.equal(button(h.doc,'Original'),undefined);
 }finally{h.close();}
});
