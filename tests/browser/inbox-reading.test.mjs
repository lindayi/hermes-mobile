import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(resolve=>setTimeout(resolve,10));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const control=(doc,text)=>[...doc.querySelectorAll('button')].find(n=>n.getAttribute('aria-label')===text || n.textContent.trim()===text);
async function setup({mutation,metadata,approvals=[],url='https://example.test/hermes/'}={}){
 const dom=new JSDOM('<div id="app"></div>',{url}),win=dom.window,doc=win.document,calls=[];let user='alice';
 const items=[{id:'n1',title:'Notification, not chat title',body:'**Full body** [link](https://example.test/)\n\n'+ 'Long text '.repeat(600),read:false,session_id:'s1',created_at:1700000000},{id:'n2',title:'Other notice',body:'Second full body',read:false}];
 const app=await mountApp(doc,{clear(){},async request(path,options={}){
  calls.push({path,options});
  if(path==='/auth/me')return {user:{id:user,status:'ready'}};
  if(path.startsWith('/sessions?'))return {items:[{id:'s2',title:'Known chat'}],total:1};
  if(path==='/inbox')return {items:items.map(i=>({...i})),read_count:0};
  if(path==='/approvals')return {items:approvals};
  if(path==='/sessions/s1')return metadata?metadata():{id:'s1',title:'Actual conversation title'};
  if(path.includes('/messages?'))return {items:[],offset:0,run:null};
  if(options.method==='POST' && path.startsWith('/inbox/'))return mutation?mutation(path):{ok:true,updated:205};
  return {items:[]};
 }},win);
 if(!url.includes('?inbox=')){control(doc,'Inbox').click();await tick();}
 return {doc,win,app,calls,setUser(id){user=id;},close(){app.destroy();win.close();}};
}
const reads=h=>h.calls.filter(c=>c.options.method==='POST' && c.path.startsWith('/inbox/'));

test('Inbox opening during bulk failure retains explicit single-read intent and inline retry',async()=>{
 const all=deferred(),one=deferred(),h=await setup({mutation:path=>path==='/inbox/read-all'?all.promise:one.promise});try{
 control(h.doc,'Mark all read').click();const card=h.doc.querySelector('[data-inbox-id="n1"]');card.querySelector('summary').click();await tick();
 assert.equal(reads(h).filter(c=>c.path==='/inbox/n1/read').length,1,'explicit read starts despite pending bulk');
 all.reject(new Error('Bulk failed'));one.reject(new Error('Single failed'));await tick();
 assert.equal(card.classList.contains('unread'),true);assert.equal(card.querySelector('details').open,true);assert.ok(control(card,'Retry marking read'));assert.ok(control(h.doc,'Retry marking all read'));
 }finally{h.close();}
});

test('Inbox approval payload starts folded with review state and explicit decision controls inside',async()=>{
 const h=await setup({approvals:[{id:'a1',title:'Long approval',action:'Full approval payload '.repeat(500),target:'Test target',expires_at:1700000000}]});try{
 const card=h.doc.querySelector('[data-approval-id="a1"]'),details=card.querySelector('details');assert.ok(details,'approval payload has disclosure');assert.equal(details.open,false);
 assert.match(details.querySelector('summary').textContent,/NEEDS YOUR REVIEW/);assert.match(details.querySelector('summary').textContent,/Long approval/);assert.match(details.querySelector('summary').textContent,/Expires/);
 assert.ok(details.contains(control(card,'Approve once')));assert.ok(details.contains(control(card,'Deny')));
 details.querySelector('summary').click();await tick();assert.equal(details.open,true);assert.equal(reads(h).length,0);assert.equal(h.calls.some(c=>c.path.includes('/decision')),false);
 control(h.doc,'Mark all read').click();await tick();assert.equal(details.open,true);assert.match(details.querySelector('summary').textContent,/NEEDS YOUR REVIEW/);assert.equal(h.calls.some(c=>c.path.includes('/decision')),false);
 }finally{h.close();}
});

for(const outcome of ['resolve','reject'])test(`Inbox bulk success dominates late per-card ${outcome} without duplicate reads`,async()=>{
 const one=deferred(),all=deferred(),h=await setup({mutation:path=>path==='/inbox/read-all'?all.promise:one.promise});try{
 const card=h.doc.querySelector('[data-inbox-id="n1"]'),summary=card.querySelector('summary');
 summary.click();await tick();control(h.doc,'Mark all read').click();control(h.doc,'Mark all read').click();summary.click();summary.click();await tick();
 assert.equal(reads(h).filter(c=>c.path==='/inbox/n1/read').length,1);assert.equal(reads(h).filter(c=>c.path==='/inbox/read-all').length,1);
 all.resolve({ok:true,updated:205});await tick();one[outcome](outcome==='resolve'?{ok:true}:new Error('Late read failure'));await tick();
 assert.equal(card.classList.contains('read'),true);assert.equal(card.querySelector('details').open,true);assert.equal(control(card,'Retry marking read'),undefined);assert.doesNotMatch(card.textContent,/Late read failure/);
 assert.equal(h.calls.filter(c=>c.path==='/inbox').length,1);
 }finally{h.close();}
});

test('Inbox programmatic disclosure and child actions are not read intent',async()=>{
 const h=await setup();try{
 const card=h.doc.querySelector('[data-inbox-id="n1"]'),details=card.querySelector('details');details.open=true;await tick();assert.equal(reads(h).length,0);
 card.querySelector('a').addEventListener('click',e=>e.preventDefault());card.querySelector('a').click();assert.equal(reads(h).length,0);assert.equal(details.open,true);
 control(card,'Open conversation').click();await tick();assert.equal(reads(h).length,0);
 }finally{h.close();}
});

for(const metadata of [()=>({id:'s1',title:null}),()=>Promise.reject(Object.assign(new Error('Unavailable'),{status:404}))])test(`Inbox absent metadata title is truthful (${metadata.toString()})`,async()=>{
 const h=await setup({metadata});try{
 control(h.doc,'Open conversation').click();await tick();
 assert.doesNotMatch(h.doc.querySelector('.conversation-title h1').textContent,/Notification, not chat title|New conversation/);
 assert.equal(h.calls.filter(c=>c.path.startsWith('/sessions?')).length,1);
 if(control(h.doc,'Retry opening conversation'))assert.equal(h.calls.some(c=>c.path.includes('/messages?')),false);
 }finally{h.close();}
});

for(const departure of ['route','account'])test(`Inbox stale successful title cannot overwrite ${departure}`,async()=>{
 const gate=deferred(),h=await setup({metadata:()=>gate.promise});try{
 control(h.doc,'Open conversation').click();await tick();
 if(departure==='account'){h.setUser('bob');await h.app.start();}else{control(h.doc,'Chats').click();await tick();control(h.doc,'Known chat').click();await tick();}
 const text=h.doc.body.textContent;gate.resolve({id:'s1',title:'Stale conversation title'});await tick();
 assert.equal(h.doc.body.textContent,text);assert.equal(h.calls.some(c=>c.path.startsWith('/sessions/s1/messages')),false);
 }finally{h.close();}
});

test('Inbox bodies start folded; explicit opening marks one read in place with inline failure retry',async()=>{
 let gate=deferred();const h=await setup({mutation:()=>gate.promise});try{
 const card=h.doc.querySelector('[data-inbox-id="n1"]'),fold=card.querySelector('details'),summary=card.querySelector('summary');
 assert.ok(fold && summary,'notification has native disclosure summary');assert.equal(fold.open,false);
 assert.equal(control(h.doc,'Mark as read'),undefined);assert.equal(reads(h).length,0);
 h.doc.querySelector('#main').scrollTop=130;
 summary.click();summary.click();summary.click();await tick();assert.equal(reads(h).length,1);assert.equal(card.classList.contains('unread'),true);
 gate.reject(new Error('Offline'));await tick();assert.equal(card.classList.contains('unread'),true);assert.ok(control(card,'Retry marking read'));assert.match(card.textContent,/Offline/);
 gate=deferred();control(card,'Retry marking read').click();await tick();assert.equal(reads(h).length,2);gate.resolve({ok:true});await tick();
 assert.equal(h.doc.querySelector('[data-inbox-id="n1"]'),card);assert.equal(fold.open,true);assert.equal(card.classList.contains('read'),true);
 assert.equal(h.doc.querySelector('#main').scrollTop,130);assert.equal(h.calls.filter(c=>c.path==='/inbox').length,1);assert.ok(control(card,'Dismiss'));assert.equal(control(h.doc,'Clear read').disabled,false);
 card.querySelector('a').addEventListener('click',e=>e.preventDefault());card.querySelector('a').click();assert.equal(fold.open,true);assert.equal(reads(h).length,2);
 }finally{h.close();}
});

test('bulk read is one explicit request, retains all rows/open state on failure and updates in place after retry',async()=>{
 let gate=deferred();const h=await setup({mutation:()=>gate.promise});try{
 const bulk=control(h.doc,'Mark all read');assert.ok(bulk,'top-level bulk action exists');
 const card=h.doc.querySelector('[data-inbox-id="n1"]');bulk.click();bulk.click();await tick();
 card.querySelector('summary').click();await tick();assert.equal(reads(h).length,2);assert.equal(reads(h)[0].path,'/inbox/read-all');assert.equal(reads(h)[1].path,'/inbox/n1/read');assert.equal(card.classList.contains('unread'),true);
 gate.reject(new Error('Bulk offline'));await tick();assert.ok(control(h.doc,'Retry marking all read'));assert.equal(h.doc.querySelectorAll('.inbox-item.unread').length,2);
 gate=deferred();control(h.doc,'Retry marking all read').click();await tick();gate.resolve({ok:true,updated:205});await tick();
 assert.equal(h.doc.querySelectorAll('.inbox-item.read').length,2);assert.equal(h.doc.querySelector('[data-inbox-id="n1"]'),card);assert.equal(card.querySelector('details').open,true);assert.equal(h.calls.filter(c=>c.path==='/inbox').length,1);
 assert.equal(h.calls.some(c=>c.path.includes('/decision')),false);assert.equal(control(h.doc,'Clear read').disabled,false);
 }finally{h.close();}
});

for(const deep of [false,true])test(`Inbox conversation resolves real title and selects Chats${deep?' after notification deep link':''}`,async()=>{
 const h=await setup(deep?{url:'https://example.test/hermes/?inbox=n1'}:{});try{
 h.doc.querySelector('[data-inbox-id="n1"] summary')?.click();await tick();control(h.doc,'Open conversation').click();await tick();
 assert.equal(h.doc.querySelector('nav [aria-current=page]').dataset.view,'chats');
 assert.equal(h.doc.querySelector('.conversation-title h1').textContent,'Actual conversation title');
 assert.equal(h.calls.filter(c=>c.path==='/sessions/s1').length,1);assert.equal(h.calls.filter(c=>c.path.startsWith('/sessions?')).length,deep?0:1);
 }finally{h.close();}
});

for(const operation of ['single','bulk','title'])for(const departure of ['route','account'])test(`${operation} stale response cannot affect ${departure}`,async()=>{
 const gate=deferred(),h=await setup({mutation:()=>gate.promise,metadata:()=>gate.promise});try{
 if(operation==='bulk')control(h.doc,'Mark all read')?.click();
 else if(operation==='single')h.doc.querySelector('[data-inbox-id="n1"] summary')?.click();
 else control(h.doc,'Open conversation').click();
 await tick();assert.ok(h.calls.some(c=>c.path===(operation==='title'?'/sessions/s1':operation==='bulk'?'/inbox/read-all':'/inbox/n1/read')),'operation actually started');
 if(departure==='account'){h.setUser('bob');await h.app.start();}else{control(h.doc,'Chats').click();await tick();}
 gate.reject(Object.assign(new Error('Expired old account'),{status:401}));await tick();
 assert.ok(h.doc.querySelector('nav'));assert.equal(h.doc.querySelector('nav [aria-current=page]').dataset.view,'chats');assert.doesNotMatch(h.doc.body.textContent,/Expired old|session expired/);
 }finally{h.close();}
});
