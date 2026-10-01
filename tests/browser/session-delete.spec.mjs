import {artifactURL} from './artifacts.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const dir=process.env.HERMES_FRONTEND_DIR?process.env.HERMES_FRONTEND_DIR.replace(/\/$/,'')+'/':fileURLToPath(new URL('../../frontend/',import.meta.url));
// setViewportSize acknowledges emulation before the page necessarily receives resize.
// The swipe controller intentionally closes actions on resize: settle that event
// before revealing Delete, rather than racing it or retrying a hidden action.
async function resizeBeforeReveal(page,viewport,timeout=5000){
 const previous=page.viewportSize();
 if(previous.width===viewport.width&&previous.height===viewport.height)return;
 await page.evaluate(()=>{
  const state={ready:false,frame:null,listener:null};
  state.listener=()=>{state.frame=requestAnimationFrame(()=>{state.ready=true;state.frame=null;});};
  window.__sessionDeleteResize=state;
  window.addEventListener('resize',state.listener,{once:true});
 });
 try{
  await page.setViewportSize(viewport);
  await page.waitForFunction(()=>window.__sessionDeleteResize?.ready===true,null,{polling:20,timeout});
 }finally{
  await page.evaluate(()=>{
   const state=window.__sessionDeleteResize;
   if(state){window.removeEventListener('resize',state.listener);if(state.frame!==null)cancelAnimationFrame(state.frame);}
   delete window.__sessionDeleteResize;
  });
 }
}
test('session resize readiness rejects a held frame on deadline and cleans up',{timeout:15000},async t=>{
 const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 let pending,watchdog;
 try{
  const page=await browser.newPage({viewport:{width:320,height:568}});
  await page.goto('about:blank');
  await page.evaluate(()=>{
   const native={request:window.requestAnimationFrame,cancel:window.cancelAnimationFrame,add:window.addEventListener,remove:window.removeEventListener};
   const held=new Map(),listeners=new Set();let next=0;
   window.__heldResizeFrame={held,listeners,requested:0,cancelled:0,restore(){
    for(const listener of listeners)native.remove.call(window,'resize',listener);
    held.clear();listeners.clear();
    window.requestAnimationFrame=native.request;window.cancelAnimationFrame=native.cancel;
    window.addEventListener=native.add;window.removeEventListener=native.remove;
    delete window.__heldResizeFrame;delete window.__sessionDeleteResize;
   }};
   window.requestAnimationFrame=callback=>{held.set(++next,callback);window.__heldResizeFrame.requested++;return next;};
   window.cancelAnimationFrame=id=>{if(held.delete(id))window.__heldResizeFrame.cancelled++;};
   window.addEventListener=function(type,listener,options){if(type==='resize')listeners.add(listener);return native.add.call(this,type,listener,options);};
   window.removeEventListener=function(type,listener,options){if(type==='resize')listeners.delete(listener);return native.remove.call(this,type,listener,options);};
  });
  try{
   // Even with rendering held, an unchanged viewport must return immediately.
   await resizeBeforeReveal(page,{width:320,height:568},200);
   assert.equal(await page.evaluate(()=>Object.hasOwn(window,'__sessionDeleteResize')),false);
   const started=performance.now();
   pending=resizeBeforeReveal(page,{width:390,height:568},200).then(()=>({status:'resolved'}),error=>({status:'rejected',name:error.name,message:error.message}));
   const outcome=await Promise.race([pending,new Promise(resolve=>{watchdog=setTimeout(()=>resolve({status:'watchdog'}),2000);})]);
   clearTimeout(watchdog);
   const elapsed=performance.now()-started;
   t.diagnostic(`held-frame outcome=${outcome.status}; elapsed=${Math.round(elapsed)}ms`);
   assert.equal(await page.evaluate(()=>window.__heldResizeFrame.requested),1,'real resize reached the held rendering frame');
   assert.equal(outcome.status,'rejected','helper must reject on its own deadline, not strand teardown until the test watchdog');
   assert.equal(outcome.name,'TimeoutError');assert.match(outcome.message,/200ms/);
   assert.equal(await page.evaluate(()=>Object.hasOwn(window,'__sessionDeleteResize')),false,'deadline removes helper state');
   assert.deepEqual(await page.evaluate(()=>({frames:window.__heldResizeFrame.held.size,listeners:window.__heldResizeFrame.listeners.size,cancelled:window.__heldResizeFrame.cancelled})),{frames:0,listeners:0,cancelled:1});
  }finally{clearTimeout(watchdog);await page.evaluate(()=>window.__heldResizeFrame.restore());}
  // Restore native rendering, then prove a real resize can complete normally.
  await resizeBeforeReveal(page,{width:400,height:568});
  assert.equal(await page.evaluate(()=>innerWidth),400);
  assert.equal(await page.evaluate(()=>Object.hasOwn(window,'__sessionDeleteResize')),false,'success removes helper state');
 }finally{
  clearTimeout(watchdog);await browser.close();
  // Closing the page rejects even the old unbounded evaluate in the RED run.
  if(pending)await pending;
 }
});
test('session deletion controls and confirmation fit narrow light/dark screens',{timeout:45000},async()=>{
 const title='Saved conversation with a long title that must not push actions offscreen';let exists=true;const mutations=[];
 const server=createServer(async(req,res)=>{const url=new URL(req.url,'http://fixture'),p=url.pathname.replace('/hermes/app-api','');const json=obj=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(obj));};
  if(url.pathname.startsWith('/hermes/app-api/')){
   if(p==='/auth/me')return json({user:{id:'owner',role:'owner',status:'ready'},csrf_token:'fixture'});
   if(p==='/sessions')return json({deletion_available:true,items:exists?[{id:'saved',title,run_status:'idle',source:'cli'}]:[],total:exists?1:0});
   if(p==='/sessions/saved'&&req.method==='DELETE'){let data='';for await(const part of req)data+=part;mutations.push(JSON.parse(data));exists=false;return json({id:'saved',deleted:true});}
   return json({items:[]});
  }
  const rel=url.pathname.replace(/^\/hermes\//,'')||'index.html';if(rel.includes('..'))return res.writeHead(400).end();try{const body=await readFile(dir+rel);res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',mjs:'text/javascript',js:'text/javascript',svg:'image/svg+xml'})[rel.split('.').pop()]||'application/octet-stream'});res.end(body);}catch{res.writeHead(404).end();}
 });await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{const page=await browser.newPage({viewport:{width:320,height:568},serviceWorkers:'block',reducedMotion:'reduce'});page.setDefaultTimeout(5000);const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);
  const remove=page.getByRole('button',{name:'Delete conversation: '+title,exact:true}),open=page.getByRole('button',{name:title,exact:true});await open.press('Shift+F10');await remove.waitFor();
  for(const theme of ['light','dark'])for(const width of [320,390,1280]){await resizeBeforeReveal(page,{width,height:568});await page.evaluate(value=>document.documentElement.dataset.theme=value,theme);await open.press('Shift+F10');await remove.waitFor();const rb=await remove.boundingBox(),ob=await open.boundingBox();assert.ok(rb.width>=44&&rb.height>=44);assert.ok(rb.x>=ob.x+ob.width,'separate adjacent delete hit target');assert.ok(rb.y>=ob.y&&rb.y+rb.height<=ob.y+ob.height,'delete is vertically within the row');assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   await remove.click();const dialog=page.getByRole('dialog',{name:'Delete conversation?',exact:true});await dialog.waitFor();assert.equal(await dialog.getByRole('button',{name:'Cancel',exact:true}).evaluate(el=>el===document.activeElement),true);const deletion=dialog.getByRole('button',{name:'Delete conversation',exact:true});const box=await deletion.boundingBox();assert.ok(box.y>=0&&box.y+box.height<=568,'confirmation stays reachable');await page.keyboard.press('Escape');assert.equal(mutations.length,0);assert.equal(await remove.evaluate(el=>el===document.activeElement),true);
  }
  await resizeBeforeReveal(page,{width:390,height:740});await open.press('Shift+F10');await remove.waitFor();await remove.click();await mkdir(artifactURL(),{recursive:true});await page.screenshot({path:fileURLToPath(artifactURL('session-delete-confirmation.png'))});await page.getByRole('dialog').getByRole('button',{name:'Delete conversation',exact:true}).click();await page.getByText('Conversation deleted.',{exact:true}).waitFor();assert.deepEqual(mutations,[{confirm:true}]);assert.equal(await open.count(),0);assert.deepEqual(errors,[]);
 }finally{await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
});

// Every API response below is synthetic. No authenticated service is contacted.
async function recoveryFixture({pending=false,slowMessages=false,longId=false}={}){
 const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const page=await browser.newPage({viewport:{width:320,height:568},serviceWorkers:'block',reducedMotion:'reduce'});page.setDefaultTimeout(5000);
 const id=longId?'root/'+ 'a'.repeat(240)+'<img>':'session /1';
 const state={pending,deleted:false,calls:[],errors:[],finishDelete:null,finishStatus:null,finishMessages:null};page.on('pageerror',e=>state.errors.push(e.message));
 await page.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname.replace('/hermes/app-api','');
  const json=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
  if(url.pathname.startsWith('/hermes/app-api/')){
   state.calls.push({path:p,query:url.search,method:req.method(),body:req.postData()});
   if(p==='/auth/me')return json({user:{id:'owner',role:'owner',status:'ready'},csrf_token:'synthetic'});
   if(p==='/sessions')return json({deletion_available:!pending,items:state.pending||state.deleted?[]:[{id,title:'Saved chat',run_status:'idle',source:'cli'}],total:state.pending?61:state.deleted?0:1,...(state.pending?{pending_deletions:[{id,status:'unconfirmed'}]}:{})});
   if(req.method()==='DELETE')return new Promise(resolve=>{state.finishDelete=async()=>{state.deleted=true;await json({id,deleted:true});resolve();};});
   if(p.endsWith('/deletion'))return new Promise(resolve=>{state.finishStatus=async(status=200,body={id,deleted:true})=>{if(status===200&&body.id===id&&body.deleted===true){state.pending=false;state.deleted=true;}if(status===409)state.pending=false;await json(status===200?body:{detail:'Synthetic receipt unavailable or refused'},status);resolve();};});
   if(p.endsWith('/messages')){const body={items:[{role:'assistant',content:'Synthetic exact-target transcript'}]};if(slowMessages)return new Promise(resolve=>{state.finishMessages=async()=>{await json(body);resolve();};});return json(body);}
   return json({items:[]});
  }
  const rel=url.pathname.replace(/^\/hermes\//,'')||'index.html';try{return route.fulfill({status:200,contentType:({html:'text/html',css:'text/css',mjs:'text/javascript',js:'text/javascript',svg:'image/svg+xml'})[rel.split('.').pop()]||'application/octet-stream',body:await readFile(dir+rel)});}catch{return route.fulfill({status:404,body:''});}
 });
 await page.goto('https://session-delete-fixture.invalid/hermes/');await page.getByRole('heading',{name:'Sessions',exact:true}).waitFor();
 return {page,state,id,async close(){await browser.close();},async begin(){await page.getByRole('button',{name:'Saved chat',exact:true}).press('Shift+F10');await page.getByRole('button',{name:'Delete conversation: Saved chat',exact:true}).click();await page.getByRole('button',{name:'Delete conversation',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.session-delete')?.disabled);assert.ok(state.finishDelete);}};
}
for(const mode of ['reopened','slow-messages','fresh-list'])test(`Chromium verified receipt reconciles ${mode} exact identity`,{timeout:20000},async()=>{
 const h=await recoveryFixture({slowMessages:mode==='slow-messages'});const {page,state}=h;try{
  await h.begin();
  if(mode==='fresh-list'){await page.getByRole('button',{name:'Inbox',exact:true}).click();await page.getByRole('heading',{name:'Inbox',exact:true}).waitFor();await page.locator('nav').getByRole('button',{name:'Chats',exact:true}).click();await page.getByRole('button',{name:'Saved chat',exact:true}).waitFor();assert.equal(await page.locator('.session-delete').isDisabled(),true);}
  else{await page.getByRole('button',{name:'Saved chat',exact:true}).click();if(mode==='reopened'){await page.getByRole('textbox',{name:'Message Hermes',exact:true}).fill('before receipt');await page.evaluate(()=>window.oldComposer=document.querySelector('textarea[name=message]'));}else await page.locator('.conversation-head').waitFor();}
  await state.finishDelete();await page.getByText('Conversation deleted.',{exact:true}).waitFor();
  if(mode==='slow-messages'){assert.ok(state.finishMessages);await state.finishMessages();}
  if(mode==='reopened')await page.evaluate(()=>{oldComposer.value='detached after receipt';oldComposer.dispatchEvent(new Event('input'));});
  await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  assert.equal(await page.locator('.messages').count(),0);assert.equal(await page.getByRole('button',{name:'Saved chat',exact:true}).count(),0);assert.equal(await page.evaluate(()=>sessionStorage.getItem('hermes:owner:draft:session /1')),null);assert.equal(state.calls.filter(c=>c.method==='DELETE').length,1);assert.deepEqual(state.errors,[]);
 }finally{await h.close();}
});
for(const outcome of [200,409,503,'wrong-id'])test(`Chromium pending recovery ${outcome} uses only GET and preserves honest state`,{timeout:20000},async()=>{
 const h=await recoveryFixture({pending:true});const {page,state,id}=h;try{
  await page.evaluate(()=>sessionStorage.setItem('hermes:owner:draft:session /1','keep until verified'));
  await page.getByRole('button',{name:'Filter conversations',exact:true}).click();await page.getByRole('button',{name:'All',exact:true}).click();await page.getByRole('button',{name:'Check status',exact:true}).waitFor();await page.getByRole('button',{name:'Next',exact:true}).click();await page.getByRole('button',{name:'Check status',exact:true}).waitFor();
  assert.ok(state.calls.some(c=>c.path==='/sessions'&&c.query.includes('offset=30')&&c.query.includes('kind=all')));
  await page.getByRole('button',{name:'Check status',exact:true}).click();await page.getByRole('button',{name:'Checking status…',exact:true}).waitFor();assert.ok(state.finishStatus);
  if(outcome==='wrong-id')await state.finishStatus(200,{id:'neighbor',deleted:true});else await state.finishStatus(outcome);
  if(outcome===200){await page.getByText('Conversation deleted.',{exact:true}).waitFor();assert.equal(await page.locator('.pending-deletion').count(),0);}else if(outcome===409){await page.getByRole('button',{name:'Saved chat',exact:true}).waitFor();assert.match(await page.locator('.notice').textContent(),/not deleted/);}else await page.getByRole('button',{name:'Check status',exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('hermes:owner:draft:session /1')),outcome===200?null:'keep until verified');assert.equal(state.calls.filter(c=>c.method==='DELETE').length,0);assert.deepEqual(state.calls.filter(c=>c.path.endsWith('/deletion')).map(c=>[c.path,c.method]),[[`/sessions/${encodeURIComponent(id)}/deletion`,'GET']]);assert.deepEqual(state.errors,[]);
 }finally{await h.close();}
});
test('Chromium pending recovery card wraps exact long IDs at 320px in both themes',{timeout:20000},async()=>{
 const h=await recoveryFixture({pending:true,longId:true});const {page}=h;try{
  for(const theme of ['light','dark']){await page.evaluate(value=>document.documentElement.dataset.theme=value,theme);const check=page.getByRole('button',{name:'Check status',exact:true});await check.waitFor();const b=await check.boundingBox();assert.ok(b.width>=44&&b.height>=44);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'pending exact ID never causes horizontal overflow');assert.equal(await page.locator('.pending-deletion img').count(),0);assert.equal(await page.locator('.pending-deletion-id').evaluate(el=>el.scrollWidth<=el.clientWidth),true,'exact ID wraps inside its card rather than being clipped by the shell');}
  await page.getByRole('button',{name:'Check status',exact:true}).click();
  await page.getByRole('button',{name:'Checking status…',exact:true}).waitFor();assert.ok(h.state.finishStatus);await h.state.finishStatus(503);
  await page.getByRole('button',{name:'Check status',exact:true}).waitFor();
  await page.getByRole('button',{name:'Check status',exact:true}).scrollIntoViewIfNeeded();
  await page.screenshot({path:fileURLToPath(artifactURL('session-delete-ui-recovery.png'))});
 }finally{await h.close();}
});
