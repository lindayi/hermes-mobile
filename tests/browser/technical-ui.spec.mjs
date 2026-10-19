import {artifactURL} from './artifacts.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,readdir,mkdir,mkdtemp,rm} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {generatedAssets} from './generated-assets.mjs';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const dir=process.env.HERMES_FRONTEND_DIR?process.env.HERMES_FRONTEND_DIR.replace(/\/$/,'')+'/':fileURLToPath(new URL('../../frontend/',import.meta.url));

// setViewportSize acknowledges browser emulation, not observeViewport's queued rAF.
// Wait for the controller's actual output; never poll the geometry being asserted.
async function waitForViewportController(page,size,timeout=5000){
 try{
  await page.waitForFunction(({width,height})=>{
   const root=document.querySelector('#app'),vv=window.visualViewport;
   return innerWidth===width && innerHeight===height && root?.dataset.viewport==='managed' &&
    Math.abs((vv?.scale??1)-1)<=.01 &&
    root.style.getPropertyValue('--app-viewport-height')===`${Math.round(vv?.height??innerHeight)}px` &&
    root.style.getPropertyValue('--app-viewport-top')===`${Math.max(0,Math.round(vv?.offsetTop??0))}px`;
  },size,{polling:20,timeout});
 }catch(cause){
  const state=await page.evaluate(()=>{
   const root=document.querySelector('#app'),vv=window.visualViewport;
   return {innerWidth,innerHeight,visualViewport:vv?{width:vv.width,height:vv.height,offsetTop:vv.offsetTop,scale:vv.scale}:null,
    viewport:root?.dataset.viewport,keyboard:root?.dataset.keyboard,
    appliedHeight:root?.style.getPropertyValue('--app-viewport-height'),appliedTop:root?.style.getPropertyValue('--app-viewport-top'),
    shell:document.querySelector('.app-shell')?.getBoundingClientRect().toJSON(),send:document.querySelector('.composer .send')?.getBoundingClientRect().toJSON()};
  });
  throw new Error(`viewport controller readiness failed: ${JSON.stringify({requested:size,timeout,state})}`,{cause});
 }
}

test('technical chat tools, expanded draft, title edit grouping and resolved approval lifecycle',{timeout:45000},async t=>{
 const command='pytest tests/test_auth.py tests/test_sessions.py tests/test_tools.py tests/test_context.py tests/test_approvals.py tests/test_pagination.py tests/test_notifications.py -q';
 const saved=[{role:'assistant',content:null,tool_calls:[{function:{name:'terminal',arguments:JSON.stringify({command})},status:'success'}]},{role:'tool',kind:'delegation',name:'delegate_task',status:'success',summary:'Review the public fixture and explain recorded results without executing any additional commands or changing the application configuration.',content:'Public fixture review complete.'}];
 const session={id:'research',title:'Research fixture',source:'cli',run_status:'running'};
 const run={id:'research-run',session_id:'research',status:'waiting_for_approval',input:'Synthetic approval fixture.',output:''};
 const approval={id:'a1',run_id:run.id,request_id:'request-a1',title:'Fixture action',action:'No real action is executed',expires_at:2000000000};
 const events=[{id:1,name:'status',data:{status:'queued'}},{id:2,name:'status',data:{status:'running'}},{id:3,name:'approval',data:approval},{id:4,name:'tool',data:{tool:'terminal',event:'tool.started',summary:command}},{id:5,name:'tool',data:{tool:'terminal',event:'tool.completed',error:false}}],clients=new Set();let pending=true,decisions=0,posts=0;
 const encode=e=>`id: ${e.id}\nevent: ${e.name}\ndata: ${JSON.stringify(e.data)}\n\n`;
 const json=(res,obj)=>{res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(obj));};
 const server=createServer(async(req,res)=>{
  const url=new URL(req.url,'http://fixture'),p=url.pathname.replace('/hermes/app-api','');
  if(url.pathname.startsWith('/hermes/app-api/')){
   if(p==='/auth/me')return json(res,{user:{id:'fixture-owner',role:'owner',status:'ready'},csrf_token:'fixture'});
   if(p==='/sessions')return json(res,{items:[session],total:1});
   if(p==='/sessions/research/messages')return json(res,{items:saved,run,total:saved.length,offset:0});
   if(p==='/sessions/research/telemetry')return json(res,{model:'fixture-model',provider:'fixture-provider',context:{used_tokens:1024,limit_tokens:32768,source:'last_request',observed_at:1790000000,estimated:false},usage:{input_tokens:999999,output_tokens:42},run:{status:run.status}});
   if(p==='/runs/research-run')return json(res,run);
   if(p==='/runs/research-run/events'){res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache'});res.write('retry: 1000\n\n');clients.add(res);res.on('close',()=>clients.delete(res));for(const e of events)res.write(encode(e));return;}
   if(p==='/approvals')return json(res,{items:pending?[approval]:[]});
   if(p==='/approvals/a1/decision'&&req.method==='POST'){decisions++;pending=false;run.status='running';const e={id:6,name:'status',data:{status:'running'}};events.push(e);for(const c of clients)c.write(encode(e));return json(res,{status:'resolved',id:'a1',run_id:run.id});}
   if(p==='/runs'&&req.method==='POST')posts++;
   return json(res,{items:[]});
  }
  const rel=url.pathname.replace(/^\/hermes\//,'')||'index.html';if(rel.includes('..')){res.writeHead(400).end();return;}
  try{const b=await readFile(dir+rel);res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',js:'text/javascript',mjs:'text/javascript',svg:'image/svg+xml',webmanifest:'application/manifest+json'})[rel.split('.').pop()]||'application/octet-stream'});res.end(b);}catch{res.writeHead(404).end();}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const context=await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block'}),page=await context.newPage();page.setDefaultTimeout(5000);const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);
  const row=page.getByRole('button',{name:'Research fixture',exact:true});await row.waitFor();assert.equal(await row.locator('[aria-label="Running"]').count(),1,'row exposes real running state');
  await row.click();await page.getByRole('textbox',{name:'Message Hermes',exact:true}).waitFor();await page.getByText('fixture-model',{exact:false}).first().waitFor();
  const title=await page.locator('.conversation-title h1').boundingBox(),edit=await page.getByRole('button',{name:'Rename session'}).boundingBox(),status=await page.locator('.conversation-status').boundingBox();assert.ok(edit.x-title.x-title.width<=8,'edit attaches to title');assert.ok(status.x-edit.x-edit.width>=12,'edit is visibly separate from status');
  const compact=page.getByRole('textbox',{name:'Message Hermes',exact:true});await compact.fill('A long research draft\nSecond paragraph');await page.getByRole('button',{name:'Expand editor',exact:true}).click();
  const dialog=page.getByRole('dialog'),editor=dialog.getByRole('textbox');await editor.waitFor();assert.equal(await editor.inputValue(),'A long research draft\nSecond paragraph');
  const box=await editor.boundingBox();assert.ok(box.height>=300,'expanded editor has meaningful writing space');
  await editor.press('End');await editor.press('Enter');await editor.type('Another line');assert.equal(posts,0,'multiline editing never sends');await page.keyboard.press('Escape');await dialog.waitFor({state:'detached'});assert.match(await compact.inputValue(),/Another line/);
  await page.getByRole('button',{name:'Expand editor',exact:true}).click();await dialog.getByRole('textbox').fill('Preserved expanded draft');await dialog.getByRole('button',{name:'Done',exact:true}).click();assert.equal(await compact.inputValue(),'Preserved expanded draft');
  await page.locator('.approval-notice').filter({hasText:'review'}).waitFor();await page.getByRole('button',{name:'Inbox',exact:true}).click();await page.locator('[data-approval-id="a1"] summary').click();await page.getByRole('button',{name:'Deny',exact:true}).click();await page.getByText('No updates',{exact:true}).waitFor();assert.equal(decisions,1);
  await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Chats',exact:true}).click();await page.getByRole('button',{name:'Research fixture',exact:true}).click();await page.locator('.conversation-status[data-state=running]').waitFor();
  assert.doesNotMatch((await page.locator('.notice,.approval-notice').allInnerTexts()).join(' '),/needs your review|requires.*action|action.*required/i,'old approval replay does not resurrect banner');
  assert.equal(await compact.inputValue(),'Preserved expanded draft');
  await page.locator('.live-message .tool-preview[data-status=success]').waitFor({state:'attached'});
  for(const preview of await page.locator('.tool-preview').filter({has:page.locator('.tool-name',{hasText:'terminal'})}).all())assert.equal(await preview.locator('.tool-summary').textContent(),command,'saved/live preserve useful detail beyond 120 characters');
  for(const card of await page.locator('.tool-activity:not([hidden]),.delegation-result').all()){
   const arrow=card.locator(':scope > summary .disclosure-icon');assert.equal(await arrow.evaluate(el=>getComputedStyle(el).transform),'none','closed disclosure points right');
   await card.locator(':scope > summary').click();assert.equal(await arrow.evaluate(el=>getComputedStyle(el).transform),'matrix(0, 1, -1, 0, 0, 0)','open disclosure points down');
  }
  for(const theme of ['light','dark'])for(const width of [320,390,1280]){
   await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);await page.setViewportSize({width,height:844});await waitForViewportController(page,{width,height:844});
   // Viewport updates can scroll the transcript between protocol round trips.
   // Compare related rectangles from one layout, not different scroll positions.
   for(const row of await page.locator('.tool-preview').all()){const {label,detail,box}=await row.evaluate(el=>({label:el.querySelector('.tool-name').getBoundingClientRect().toJSON(),detail:el.querySelector('.tool-summary').getBoundingClientRect().toJSON(),box:el.getBoundingClientRect().toJSON()}));assert.ok(box.width>0 && label.height>0 && detail.height>0,'tool row has a laid-out name and detail');assert.ok(detail.y>=label.y+label.height-1,'detail wraps below name and status, never inline');assert.ok(detail.width>=box.width-24,'detail uses available row width');const expected=theme==='light'?'rgb(40, 115, 69)':'rgb(141, 204, 160)';assert.equal(await row.evaluate(el=>getComputedStyle(el).color),expected,'succeeded tool status uses restrained semantic green');assert.equal(await row.locator('.tool-summary').evaluate(el=>getComputedStyle(el).color),await page.locator('.technical-strip').evaluate(el=>getComputedStyle(el).color),'tool detail remains neutral');assert.equal(await row.evaluate(el=>el.scrollWidth<=el.clientWidth),true,'long tool detail does not overflow');}
   await mkdir(artifactURL(),{recursive:true});await page.locator('.messages').evaluate(el=>el.scrollTop=0);await page.screenshot({path:fileURLToPath(artifactURL(`restrained-tools-${width}-${theme}.png`))});await page.locator('.messages').evaluate(el=>el.scrollTop=el.scrollHeight);await page.screenshot({path:fileURLToPath(artifactURL(`restrained-live-tools-${width}-${theme}.png`))});
  }
  assert.doesNotMatch(await page.locator('.technical-strip').textContent(),/unknown|Not available/i);
  await t.test('viewport readiness rejects a held resize frame and accepts its actual update',async()=>{
   // Hold delivery deterministically: this reproduces the hosted assertion's race
   // without changing viewport values, CSS, or relying on a machine-speed sleep.
   await page.evaluate(()=>{
    const request=window.requestAnimationFrame.bind(window),cancel=window.cancelAnimationFrame.bind(window),frames=new Map();let id=0;
    window.requestAnimationFrame=callback=>{frames.set(--id,callback);return id;};
    window.cancelAnimationFrame=key=>key<0?frames.delete(key):cancel(key);
    window.__technicalResizeFrames={frames,release(){
     window.requestAnimationFrame=request;window.cancelAnimationFrame=cancel;
     for(const callback of frames.values())request(callback);
     delete window.__technicalResizeFrames;
    }};
   });
   const size={width:320,height:568};
   try{
    await page.setViewportSize(size);
    await page.waitForFunction(()=>window.__technicalResizeFrames.frames.size>0,null,{polling:20,timeout:5000});
    await assert.rejects(waitForViewportController(page,size,200),error=>{
     assert.match(error.message,/viewport controller readiness failed:/);
     assert.match(error.message,/"innerHeight":568/);
     assert.match(error.message,/"appliedHeight":"844px"/);
     assert.equal(error.cause.name,'TimeoutError');return true;
    });
   }finally{await page.evaluate(()=>window.__technicalResizeFrames.release());}
   await waitForViewportController(page,size);
   const send=await page.locator('.composer .send').boundingBox();
   assert.ok(send.y+send.height<=size.height,'input controls remain inside viewport');
  });
  for(const size of [{width:320,height:568},{width:390,height:450},{width:844,height:390},{width:1280,height:900}]){await page.setViewportSize(size);await waitForViewportController(page,size);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'no horizontal overflow');const send=await page.locator('.composer .send').boundingBox();assert.ok(send.y+send.height<=size.height,'input controls remain inside viewport');}
  await page.setViewportSize({width:390,height:844});await waitForViewportController(page,{width:390,height:844});await mkdir(artifactURL(),{recursive:true});await page.screenshot({path:fileURLToPath(artifactURL('technical-chat-mobile.png'))});await page.getByRole('button',{name:'Expand editor',exact:true}).click();await page.screenshot({path:fileURLToPath(artifactURL('expanded-editor-mobile.png'))});assert.equal(posts,0);assert.deepEqual(errors,[]);
 }finally{await browser.close();for(const c of clients)c.end();server.closeAllConnections();await new Promise(r=>server.close(r));}
});

test('generated browser preserves dispatched decisions and fences unknown-run approval reads',{timeout:60000},async()=>{
 const temporary=await mkdtemp(join(tmpdir(),'hermes-approval-lifecycle-')),assets=generatedAssets(temporary),
  session={id:'research',title:'Lifecycle fixture'},run={id:'research-run',session_id:'source-alias',status:'waiting_for_approval'},
  first={id:'a',run_id:run.id,request_id:'request-a',session_id:session.id,run_session_id:'source-alias',
   status:'pending',title:'First action',command:'echo first',target:'synthetic',expires_at:2000000000,executable:true,choices:['once','deny']},
  sibling={...first,id:'b',request_id:'request-b',title:'Sibling action',command:'echo sibling'},
  calls=[],eventStreams=new Set(),decisionAck={};
 let decisionPosts=0,detailReads=0,runState='waiting_for_approval',listMode='full',
  heldList=null,holdDetail=false,releaseDetail=null;
 let server,browser;
 const json=(res,data,status=200)=>{res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(data));};
 try{
  server=createServer(async(req,res)=>{
   const url=new URL(req.url,'http://fixture'),path=url.pathname;
   if(path.startsWith('/hermes/app-api/')){
    const p=path.slice('/hermes/app-api'.length);calls.push({path:p,method:req.method});
    if(p==='/runs/research-run/events'){
     res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache','Connection':'keep-alive'});
     res.write(': connected\n\n');eventStreams.add(res);res.on('close',()=>eventStreams.delete(res));return;
    }
    if(p==='/auth/me')return json(res,{user:{id:'fixture-owner',role:'owner',status:'ready'},csrf_token:'fixture'});
    if(p==='/sessions')return json(res,{items:[session],total:1});
    if(p==='/sessions/research')return json(res,session);
    if(p==='/sessions/research/messages')return json(res,{items:[],total:0,offset:0,run});
    if(p==='/sessions/research/telemetry')return json(res,{});
    if(p==='/runs/research-run')return json(res,{...run,status:runState});
    if(p==='/runs/research-run/controls')return json(res,{steering:false,attempts:[]});
    if(p==='/approvals'){
     if(listMode==='hold-once'){
      listMode='full';heldList={resolve:()=>json(res,{items:[first,sibling]})};return;
     }
     const items=listMode==='omit-sibling'?[first]:[first,sibling];
     return json(res,{items});
    }
    if(p==='/approvals/b' && req.method==='GET'){
     detailReads++;
     if(holdDetail)await new Promise(resolve=>{releaseDetail=()=>{json(res,sibling);resolve();};});
     else return json(res,sibling);
     return;
    }
    if(p==='/auth/verify/options')return json(res,{challenge_id:'fixture',publicKey:{challenge:'AQ'}});
    if(p==='/auth/verify/finish')return json(res,{verified:true});
    if(p==='/approvals/a/decision' && req.method==='POST'){
     decisionPosts++;await new Promise(resolve=>{decisionAck.resolve=resolve;});return json(res,{status:'resolved'});
    }
    return json(res,{items:[]});
   }
   const relative=path.replace(/^\/hermes\//,'')||'index.html';
   if(relative.includes('..'))return res.writeHead(400).end();
   try{
    const body=await readFile(join(assets,relative));
    res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',js:'text/javascript',mjs:'text/javascript',svg:'image/svg+xml',webmanifest:'application/manifest+json'})[relative.split('.').pop()]||'application/octet-stream'});
    res.end(body);
   }catch{res.writeHead(404).end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const context=await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block'}),page=await context.newPage();
  page.setDefaultTimeout(7000);
  await page.addInitScript(()=>{
   window.PublicKeyCredential=class {};
   Object.defineProperty(navigator,'credentials',{configurable:true,value:{get:async()=>({
    id:'fixture',rawId:new Uint8Array([1]).buffer,type:'public-key',getClientExtensionResults:()=>({}),
    response:{clientDataJSON:new Uint8Array([1]).buffer,authenticatorData:new Uint8Array([2]).buffer,
     signature:new Uint8Array([3]).buffer,userHandle:null}
   })}});
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);
  await page.locator('.session-row').click();
  await page.locator('[data-approval-id="a"] button.primary').click();
  await page.waitForFunction(()=>document.querySelector('[data-approval-id="a"] h3')?.textContent==='Decision pending');
  for(let i=0;i<100 && decisionPosts!==1;i++)await new Promise(resolve=>setTimeout(resolve,10));
  assert.equal(decisionPosts,1,'the explicit decision POST is held');
  await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Chats',exact:true}).click();
  await page.locator('.session-row').waitFor();
  await page.locator('.session-row').click();
  await page.locator('[data-approval-id="a"] h3').filter({hasText:'Decision pending'}).waitFor();
  assert.equal(await page.locator('[data-approval-id="a"] button:enabled').count(),0,
   'generated browser does not re-enable the held decision on route reentry');
  assert.equal(await page.locator('[data-approval-id="b"] button:enabled').count(),2,
   'the sibling approval remains actionable');

  holdDetail=true;listMode='omit-sibling';
  const omittedList=page.waitForResponse(async response=>{
   if(new URL(response.url()).pathname!=='/hermes/app-api/approvals' || response.request().method()!=='GET')return false;
   const data=await response.json();return data.items?.length===1 && data.items[0].id===first.id;
  });
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await omittedList;
  await page.waitForFunction(()=>document.querySelector('[data-approval-id="b"]')!==null);
  for(let i=0;i<100 && detailReads===0;i++)await new Promise(resolve=>setTimeout(resolve,10));
  assert.equal(detailReads,1,'omission starts exact-detail revalidation for the sibling');
  listMode='hold-once';
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  for(let i=0;i<100 && !heldList;i++)await new Promise(resolve=>setTimeout(resolve,10));
  assert.ok(heldList,'an approval list read is held before the unknown transition');
  for(const stream of eventStreams)stream.write(`event: status\ndata: ${JSON.stringify({status:'unknown'})}\n\n`);
  await page.locator('[data-approval-id="b"] h3').filter({hasText:'Approval details unavailable'}).waitFor();
  assert.equal(await page.locator('[data-approval-id="b"] button').count(),0,
   'unknown run immediately suspends only the unsubmitted sibling');
  assert.equal(await page.locator('[data-approval-id="a"] h3').textContent(),'Decision pending',
   'unknown run does not reinterpret the dispatched decision');
  heldList.resolve();releaseDetail?.();
  await page.waitForTimeout(30);
  assert.equal(await page.locator('[data-approval-id="b"] button').count(),0,
   'pre-unknown list and exact detail cannot restore stale controls');
  runState='waiting_for_approval';listMode='full';
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await page.locator('[data-approval-id="b"] button.primary').waitFor();
  assert.equal(await page.locator('[data-approval-id="b"] button:enabled').count(),2,
   'fresh pending authority restores controls after run recovery');
  decisionAck.resolve();
  await page.locator('[data-approval-id="a"] h3').filter({hasText:'Resolved'}).waitFor();
  assert.equal(decisionPosts,1,'route and run recovery never resend the held decision');
 }finally{
  decisionAck.resolve?.();for(const stream of eventStreams)stream.end();
  if(browser)await browser.close();
  if(server){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
  await rm(temporary,{recursive:true,force:true});
 }
});

for(const runSession of ['research','source-alias'])test(`generated browser aborts stale ${runSession} approval detail on unknown-run transition`,{timeout:60000},async()=>{
 const temporary=await mkdtemp(join(tmpdir(),'hermes-approval-generation-')),assets=generatedAssets(temporary),
  session={id:'research',title:'Generation fixture'},
  run={id:'generation-run',session_id:runSession,status:'waiting_for_approval'},
  pending={id:'a',run_id:run.id,request_id:'request-a',session_id:session.id,run_session_id:runSession,
   status:'pending',title:'Synthetic action',command:'echo synthetic',target:'fixture',expires_at:2000000000,
   executable:true,choices:['once','deny']},
  eventStreams=new Set(),calls=[];
 let detailReads=0,listReads=0,omitNextList=false,firstDetailAborted=false,releaseInitial=null,server,browser;
 try{
  const names=await readdir(assets),ui=names.find(name=>/^ui\.[a-f0-9]+\.mjs$/.test(name)),
   sw=names.find(name=>/^sw\.[a-f0-9]+\.js$/.test(name));
  assert.ok(ui&&sw,'the browser test serves the generated release graph');
  server=createServer(async(req,res)=>{
   const url=new URL(req.url,'http://fixture'),path=url.pathname;
   if(path.startsWith('/hermes/app-api/')){
    const p=path.slice('/hermes/app-api'.length);calls.push({p,method:req.method});
    const json=(data,status=200)=>{res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(data));};
    if(p==='/runs/generation-run/events'){
     res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache','Connection':'keep-alive'});
     res.write(': connected\n\n');eventStreams.add(res);res.on('close',()=>eventStreams.delete(res));return;
    }
    if(p==='/auth/me')return json({user:{id:'fixture-owner',role:'owner',status:'ready'},csrf_token:'fixture'});
    if(p==='/inbox/notice-target/target')return json({kind:'approval',session_id:session.id,
     run_session_id:runSession,run_id:run.id,approval_id:pending.id,request_id:pending.request_id});
    if(p==='/sessions')return json({items:[session],total:1});
    if(p==='/sessions/research')return json(session);
    if(p==='/sessions/research/messages')return json({items:[],total:0,offset:0,run});
    if(p==='/sessions/research/telemetry')return json({});
    if(p==='/approvals'){
     listReads++;
     const items=omitNextList?[]:[pending];omitNextList=false;
     return json({items});
    }
    if(p==='/approvals/a' && req.method==='GET'){
     if(++detailReads===1){
      await new Promise(resolve=>{
       releaseInitial=()=>{if(!req.aborted&&!res.destroyed)json(pending);resolve();};
       req.once('aborted',()=>{firstDetailAborted=true;resolve();});
      });
      if(req.aborted || res.destroyed)return;
     }else if(detailReads===2){
      return json({detail:'Synthetic transient detail failure'},503);
     }
     return json(pending);
    }
    if(p==='/runs/generation-run')return json(run);
    if(p==='/runs/generation-run/controls')return json({steering:false,attempts:[]});
    return json({items:[]});
   }
   const relative=path.replace(/^\/hermes\//,'')||'index.html';
   if(relative.includes('..'))return res.writeHead(400).end();
   try{
    const body=await readFile(join(assets,relative));
    res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',js:'text/javascript',mjs:'text/javascript',svg:'image/svg+xml',webmanifest:'application/manifest+json'})[relative.split('.').pop()]||'application/octet-stream'});
    res.end(body);
   }catch{res.writeHead(404).end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const context=await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block'}),page=await context.newPage();
  page.setDefaultTimeout(7000);
  await page.addInitScript(()=>{window.PublicKeyCredential=class {};Object.defineProperty(navigator,'credentials',{configurable:true,value:{get:async()=>({
   id:'fixture',rawId:new Uint8Array([1]).buffer,type:'public-key',getClientExtensionResults:()=>({}),
   response:{clientDataJSON:new Uint8Array([1]).buffer,authenticatorData:new Uint8Array([2]).buffer,
    signature:new Uint8Array([3]).buffer,userHandle:null}
  })}});});
  await page.goto(`http://127.0.0.1:${server.address().port}/hermes/?notification=notice-target`);
  const card=page.locator('[data-approval-id="a"]');
  await card.getByRole('button',{name:'Approve once',exact:true}).waitFor();
  await page.waitForFunction(()=>document.querySelector('[data-approval-id="a"]'));
  for(let i=0;i<100&&!eventStreams.size;i++)await page.waitForTimeout(10);
  assert.ok(eventStreams.size,'the rendered run has an active event stream');
  for(let i=0;i<100&&detailReads!==1;i++)await page.waitForTimeout(10);
  assert.equal(detailReads,1,'the initial notification detail request is held');
  for(const stream of eventStreams)stream.write(`event: status\ndata: ${JSON.stringify({status:'unknown'})}\n\n`);
  await card.getByRole('heading',{name:'Approval details unavailable',exact:true}).waitFor();
  omitNextList=true;
  const priorListReads=listReads;
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  for(let i=0;i<100&&listReads<=priorListReads;i++)await page.waitForTimeout(10);
  for(let i=0;i<100&&detailReads<2;i++)await page.waitForTimeout(10);
  assert.ok(listReads>=2,'the empty list is refreshed after the unknown transition');
  for(let i=0;i<100&&!firstDetailAborted;i++)await page.waitForTimeout(10);
  assert.equal(firstDetailAborted,true,'the pre-unknown HTTP request was actually aborted');
  assert.ok(detailReads>=2,'post-transition detail reads do not reuse the old in-flight request');
  assert.equal(await card.getByRole('button').count(),0,'unknown status remains non-actionable');
  run.status='waiting_for_approval';
  await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Chats',exact:true}).click();
  await page.locator('.session-row').click();
  await card.getByRole('button',{name:'Approve once',exact:true}).waitFor();
  assert.equal(await page.locator('[data-approval-id="a"]').count(),1,'re-entry keeps one exact approval card');
  assert.equal(calls.filter(call=>call.p.endsWith('/decision')).length,0,'status recovery never decides');
 }finally{
  releaseInitial?.();for(const stream of eventStreams)stream.end();
  if(browser)await browser.close();
  if(server){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
  await rm(temporary,{recursive:true,force:true});
 }
});
