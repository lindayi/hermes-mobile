import {artifactURL} from './artifacts.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
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
