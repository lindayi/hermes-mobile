import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdtemp,rm,mkdir,writeFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const repo=fileURLToPath(new URL('../../',import.meta.url));

// Generated application with a synthetic loopback API, using real trusted input.
test('session opening accepts browser clicks inside tap slop',{timeout:240000},async t=>{
 const tmp=await mkdtemp((process.env.TMPDIR || '/tmp')+'/b-');let server,browser,context;
 const evidence={cases:[],errors:[]},calls=[];
 try {
  let dir=process.env.HERMES_FRONTEND_DIR || repo+'frontend';
  if(!/app\.[a-f0-9]+\.js/.test(await readFile(dir+'/index.html','utf8'))){
   dir=tmp+'/public';const env={...process.env};delete env.HERMES_FRONTEND_DIR;
   execFileSync(process.env.HERMES_TEST_PYTHON || repo+'.venv/bin/python',['-c','from deploy.frontend_release import build_frontend; from pathlib import Path; import sys; build_frontend(Path("frontend"),Path(sys.argv[1]))',dir],{cwd:repo,env});

  }
  server=createServer(async(req,res)=>{
   const p=new URL(req.url,'http://fixture').pathname;
   if(p.startsWith('/hermes/app-api/')){
    const path=p.slice('/hermes/app-api'.length);calls.push({path,method:req.method});let data={items:[]};
    if(path==='/auth/me')data={user:{id:'synthetic-owner',status:'ready'},csrf_token:'fixture'};
    if(path==='/sessions')data={deletion_available:true,total:30,items:Array.from({length:30},(_,i)=>({id:'s'+i,title:'Opening fixture '+i,source:'cli',run_status:'idle'}))};
    if(path==='/sessions/s0/messages')data={items:[{id:'m0',role:'user',content:'Visible fixture message'}]};
    if(path==='/push/preferences')data={revision:0,enabled:false,categories:{completion:true,approval:true,attention:true,scheduled:true,operational:true},hide_details:false};
    res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify(data));return;
   }
   const relative=p.replace(/^\/hermes\//,'')||'index.html';
   if(relative.includes('..'))return res.writeHead(400).end();
   try{res.writeHead(200,{'Content-Type':({html:'text/html',js:'text/javascript',mjs:'text/javascript',css:'text/css',svg:'image/svg+xml'})[relative.split('.').pop()]||'application/octet-stream'}).end(await readFile(dir+'/'+relative));}catch{res.writeHead(404).end();}
  });await new Promise(r=>server.listen(0,'127.0.0.1',r));
  browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage'],env:{...process.env,TMPDIR:tmp}});
  const traceInput=()=>{
   window.inputTrace=[];
   for(const type of ['pointerdown','pointermove','pointerup','pointercancel','lostpointercapture','click','touchstart','touchmove','touchend','touchcancel','contextmenu'])document.addEventListener(type,e=>{
    const record={type,time:e.timeStamp,x:e.clientX,y:e.clientY,trusted:e.isTrusted,pointerType:e.pointerType,target:e.target.className,prevented:false};inputTrace.push(record);
    setTimeout(()=>record.prevented=e.defaultPrevented,0);
   },true);
  };
  let page,cdp;
  const url=`http://127.0.0.1:${server.address().port}/hermes/`;
  const reset=async()=>{
   // Native touch cancellation can contaminate later CDP click synthesis even
   // across pages in one context. Keep each input case in a fresh context.
   await context?.close();
   context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,serviceWorkers:'block'});
   await context.addInitScript(traceInput);
   page=await context.newPage();page.setDefaultTimeout(3000);
   page.on('pageerror',e=>evidence.errors.push(e.message));cdp=await context.newCDPSession(page);
   await page.goto(url);const row=page.locator('.session-row').first();await row.waitFor();calls.length=0;return row;
  };
  const touch=(type,x,y)=>cdp.send('Input.dispatchTouchEvent',{type,touchPoints:type==='touchEnd'||type==='touchCancel'?[]:[{x,y,id:1}]});
  for(const input of ['touch','mouse'])for(const dx of [0,6,7,8,9,10,11])await t.test(`${input} settled row with ${dx}px incidental movement`,async()=>{
   const row=await reset();
   const b=await row.boundingBox(),x=b.x+b.width*.55,y=b.y+b.height/2;
   if(input==='touch'){await touch('touchStart',x,y);if(dx)await touch('touchMove',x-dx,y);await touch('touchEnd');}
   else{await page.mouse.move(x,y);await page.mouse.down();if(dx)await page.mouse.move(x-dx,y);await page.mouse.up();}
   await page.waitForTimeout(150);
   const trace=await page.evaluate(()=>inputTrace),record={input,dx,trace,calls:[...calls],opened:await page.locator('.messages').count()};evidence.cases.push(record);
   assert.ok(trace.some(e=>e.type==='click'&&e.trusted),'Chromium emits a trusted activation click');
   assert.equal(record.opened,1,`${input} ${dx}px: browser tap must open conversation; trace=${JSON.stringify(trace)}`);
   assert.equal(calls.filter(c=>c.path==='/sessions/s0/messages').length,1,'selected messages requested exactly once');
   assert.match(await page.locator('.messages').innerText(),/Visible fixture message/);
   assert.equal(await page.locator('[role=dialog]').count(),0);
   assert.equal(calls.filter(c=>c.method==='DELETE').length,0);
  });
  for(const input of ['touch','mouse'])for(const [dx,dy,cancel] of [[-12,0,false],[-60,0,false],[0,-12,false],[0,-65,false],[-8,0,true]])await t.test(`${input} gesture ${dx}/${dy}${cancel?' cancelled':''} does not activate`,async()=>{
   const row=await reset();
   const b=await row.boundingBox(),x=b.x+b.width*.65,y=b.y+b.height/2;
   if(input==='touch'){await touch('touchStart',x,y);await touch('touchMove',x+dx,y+dy);await touch(cancel?'touchCancel':'touchEnd');}
   else{await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x+dx,y+dy);if(cancel)await page.keyboard.press('Escape');await page.mouse.up();}
   await page.waitForTimeout(100);
   evidence.cases.push({input,dx,dy,cancel,trace:await page.evaluate(()=>inputTrace),calls:[...calls]});
   assert.equal(await page.locator('.messages,[role=dialog]').count(),0,'gesture does not navigate or confirm deletion');
   assert.equal(calls.filter(c=>c.path==='/sessions/s0/messages'||c.method==='DELETE').length,0);
  });
  for(const input of ['touch','mouse','keyboard'])await t.test(`${input} deliberate activation after swipe resets suppression`,async()=>{
   const row=await reset();
   const b=await row.boundingBox(),x=b.x+b.width*.65,y=b.y+b.height/2;
   // A back-to-back CDP move/end starts a native Chromium fling, even with
   // pan-y: the next touch then stops that fling instead of activating. Use
   // Chromium's non-flinging touch swipe; never synthesize an activation click.
   await cdp.send('Input.synthesizeScrollGesture',{x,y,xDistance:-60,yDistance:0,speed:400,preventFling:true,gestureSourceType:'touch'});
   await page.waitForTimeout(270); // Settled, but still inside the 700ms suppression window.
   assert.equal(await page.locator('.messages').count(),0);
   assert.equal(await page.locator('.session-actions').first().getAttribute('aria-expanded'),'true');
   if(input==='touch'){await touch('touchStart',x-60,y);await touch('touchEnd');}
   else if(input==='mouse')await page.mouse.click(x-60,y);
   else await row.press('Enter');
   await page.waitForTimeout(150);
   const trace=await page.evaluate(()=>inputTrace);
   evidence.cases.push({input,afterSwipe:true,trace,calls:[...calls]});
   if(input==='touch'){
    const ups=trace.filter(e=>e.type==='pointerup');
    assert.ok(ups.length>=2 && ups.at(-1).time-ups.at(-2).time<700,'fresh touch finishes inside application suppression window');
    assert.ok(trace.some(e=>e.type==='click' && e.trusted && e.pointerType==='touch'),'fresh stationary touch produces a native trusted click');
   }
   assert.equal(await page.locator('.messages').count(),1,`fresh ${input} activation must open: ${JSON.stringify(trace)}`);
   assert.equal(calls.filter(c=>c.path==='/sessions/s0/messages').length,1);
   assert.equal(calls.filter(c=>c.method==='DELETE').length,0);
  });
  assert.deepEqual(evidence.errors,[]);
 }finally{
  if(process.env.HERMES_TEST_ARTIFACT_DIR){await mkdir(process.env.HERMES_TEST_ARTIFACT_DIR,{recursive:true});await writeFile(process.env.HERMES_TEST_ARTIFACT_DIR+'/session-opening.json',JSON.stringify(evidence,null,2));}
  await context?.close();await browser?.close();if(server){server.closeAllConnections();await new Promise(r=>server.close(r));}await rm(tmp,{recursive:true,force:true});
 }
});

test('opening long sessions lands on latest replay or terminal output in a narrow viewport',{timeout:120000},async t=>{
 const tmp=await mkdtemp((process.env.TMPDIR || '/tmp')+'/b-');let server,browser,context;
 const evidence={cases:[],errors:[]},calls=[];
 try {
  let dir=process.env.HERMES_FRONTEND_DIR || repo+'frontend';
  if(!/app\.[a-f0-9]+\.js/.test(await readFile(dir+'/index.html','utf8'))){
   dir=tmp+'/public';const env={...process.env};delete env.HERMES_FRONTEND_DIR;
   execFileSync(process.env.HERMES_TEST_PYTHON || repo+'.venv/bin/python',['-c','from deploy.frontend_release import build_frontend; from pathlib import Path; import sys; build_frontend(Path("frontend"),Path(sys.argv[1]))',dir],{cwd:repo,env});
  }
  let scenario,eventSink,fallbackRequestResolve,fallbackRequest=Promise.resolve(),fallbackResponseSent=false;
  const replay=()=>{
   const events=Array.from({length:60},(_,index)=>{
    const id=index+1,observed_at=1000+id;
    if(id%3===1)return {id,name:'commentary',observed_at,data:{text:`Public replay commentary ${id}. ${'A restored public segment with useful progress. '.repeat(8)}`}};
    if(id%3===2)return {id,name:'delta',observed_at,data:{text:`Restored output ${id}. ${'A long synthetic output segment. '.repeat(8)}`}};
    return {id,name:'tool',observed_at,data:{name:`synthetic_tool_${id}`,tool_call_id:`tool-${id}`,status:'completed',summary:`Restored tool result ${id}. ${'Synthetic result detail. '.repeat(4)}`}};
   });
   events.push({id:61,name:'commentary',observed_at:1061,data:{text:'Latest live progress from restored activity.'}});
   return events;
  };
  server=createServer(async(req,res)=>{
   const p=new URL(req.url,'http://fixture').pathname;
   if(p.startsWith('/hermes/app-api/')){
    const path=p.slice('/hermes/app-api'.length);calls.push({path,method:req.method});
    if(path==='/auth/me')return res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify({user:{id:'synthetic-owner',status:'ready'},csrf_token:'fixture'}));
    if(path==='/sessions')return res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify({deletion_available:true,total:2,items:[{id:'s0',title:'Long synthetic session',source:'cli',run_status:scenario?.status || 'idle'},{id:'s1',title:'Second synthetic session',source:'cli',run_status:'idle'}]}));
    if(path==='/sessions/s1/messages'){
     const items=Array.from({length:12},(_,i)=>({id:`second-${i}`,role:i%2?'assistant':'user',content:`Second session history ${i}. ${'Independent synthetic content. '.repeat(12)}`,timestamp:800+i}));
     return res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify({items,offset:0}));
    }
    if(path==='/sessions/s0/messages'){
     const status=scenario.status==='fallback'?'running':scenario.status;
     const run={id:'run-'+(scenario.status==='fallback'?'fallback':scenario.status),session_id:'s0',status,input:'Current synthetic prompt',created_at:1000,...(['completed','fallback'].includes(scenario.status)?{output:scenario.output,updated_at:1100}:{})};
     const items=Array.from({length:24},(_,i)=>({id:`history-${i}`,role:i%2?'assistant':'user',content:`Synthetic retained history item ${i}. ${'Ordinary public history. '.repeat(10)}`,timestamp:900+i}));
     if(scenario.status==='fallback')return res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify({items,offset:0,last_run:run}));
     return res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify({items,offset:0,run,tool_replay:{run_id:run.id,cursor:61,events:replay()}}));
    }
    if(path==='/runs/run-fallback'){
     fallbackRequestResolve?.();
     setTimeout(()=>{fallbackResponseSent=true;res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify({id:'run-fallback',session_id:'s0',status:'running',created_at:1000,output:scenario.output,updated_at:1100}));},800);
     return;
    }
    if(path.endsWith('/events')){
     eventSink=res;res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache'});res.write(': connected\n\n');return;
    }
    const data=path==='/push/preferences'?{revision:0,enabled:false,categories:{completion:true,approval:true,attention:true,scheduled:true,operational:true},hide_details:false}:{items:[]};
    return res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify(data));
   }
   const relative=p.replace(/^\/hermes\//,'')||'index.html';
   if(relative.includes('..'))return res.writeHead(400).end();
   try{res.writeHead(200,{'Content-Type':({html:'text/html',js:'text/javascript',mjs:'text/javascript',css:'text/css',svg:'image/svg+xml'})[relative.split('.').pop()]||'application/octet-stream'}).end(await readFile(dir+'/'+relative));}catch{res.writeHead(404).end();}
  });
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage'],env:{...process.env,TMPDIR:tmp}});
  const url=`http://127.0.0.1:${server.address().port}/hermes/`;
  for(const next of [{status:'running'},{status:'completed',output:`Latest terminal output. ${'Synthetic terminal detail. '.repeat(120)}`},{status:'fallback',output:`Latest fallback output. ${'Synthetic delayed detail. '.repeat(120)}`}])await t.test(`${next.status} run replay`,async()=>{
   scenario=next;eventSink=null;calls.length=0;
   fallbackResponseSent=false;
   if(next.status==='fallback')fallbackRequest=new Promise(resolve=>{fallbackRequestResolve=resolve;});
   context=await browser.newContext({viewport:{width:320,height:640},hasTouch:true,serviceWorkers:'block'});
   const page=await context.newPage();page.setDefaultTimeout(5000);page.on('pageerror',e=>evidence.errors.push(e.message));
   if(next.status==='fallback')await page.addInitScript(()=>localStorage.setItem('hermes:synthetic-owner:run:s0','run-fallback'));
   await page.goto(url);
   await page.locator('.session-row').first().click();
   await page.locator('.messages').waitFor();
   let fallbackBefore;
   if(next.status==='fallback'){
    await fallbackRequest;
    const messages=page.locator('.messages');
    await messages.hover();
    await page.mouse.wheel(0,-50);
    await page.waitForTimeout(50);
    fallbackBefore=await messages.evaluate(el=>({top:el.scrollTop,gap:el.scrollHeight-el.clientHeight-el.scrollTop}));
   }
   const latest=next.status==='running'?'Latest live progress from restored activity.':next.status==='completed'?'Latest terminal output.':'Latest fallback output.';
   await page.locator('.live-message').waitFor();
   await page.waitForTimeout(100);
   const state=await page.evaluate(()=>{
    const messages=document.querySelector('.messages'),composer=document.querySelector('.composer'),live=document.querySelector('.live-message');
    const tail=[...live.querySelectorAll('.activity-summary,.message-body')].filter(node=>node.textContent.trim()).at(-1),box=messages.getBoundingClientRect(),tailBox=tail.getBoundingClientRect();
    return {gap:messages.scrollHeight-messages.clientHeight-messages.scrollTop,scrollY,focusedComposer:composer.contains(document.activeElement),messagesBottom:box.bottom,composerTop:composer.getBoundingClientRect().top,tailBottom:tailBox.bottom,tailTop:tailBox.top,text:live.textContent,tailText:tail.textContent};
   });
   evidence.cases.push({scenario:next.status,state,before:fallbackBefore,calls:[...calls]});
   if(next.status==='fallback'){
    const after=await page.locator('.messages').evaluate(el=>({top:el.scrollTop,gap:el.scrollHeight-el.clientHeight-el.scrollTop}));
    assert.equal(after.top,fallbackBefore.top,'delayed fallback preserves a deliberate small upward reading position near the bottom');
    assert.ok(after.gap>=fallbackBefore.gap,'preserving the viewport may increase the remaining gap when delayed output renders below it');
   }
   else assert.ok(state.gap<=4,`opening ${next.status} run should land at the transcript bottom; gap=${state.gap}`);
   assert.ok(state.text.includes(latest),`latest ${next.status} progress is rendered`);
   assert.ok(state.tailText.includes(latest),'newest progress/output is the final run segment');
   if(next.status!=='fallback')assert.ok(state.tailBottom<=state.composerTop+1 && state.tailBottom>state.tailTop,'latest replay/output is visible above the composer');
   assert.equal(state.focusedComposer,false,'opening never focuses the composer');
   assert.equal(state.scrollY,0,'opening moves only the transcript scroller');
   assert.equal(await page.locator('.live-message').count(),1,'replay restores exactly one live run');
   assert.equal(calls.filter(call=>call.method==='POST' && call.path==='/runs').length,0,'opening never executes a new run');
   assert.equal(calls.filter(call=>call.path==='/sessions/s0/messages').length,1,'latest history is fetched once');
   if(next.status==='running'){
    const before=await page.locator('.messages').evaluate(el=>el.scrollTop);
    assert.ok(eventSink,'the synthetic live event stream is connected');
    await page.evaluate(()=>{
     const root=document.querySelector('.live-message'),walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
     let text;while((text=walker.nextNode()) && !text.textContent.trim()){}
     const range=document.createRange();range.selectNodeContents(text);getSelection().addRange(range);
    });
    eventSink.write('id: 62\nevent: delta\ndata: {"text":"A later live update"}\n\n');
    await page.waitForTimeout(100);
    assert.equal(await page.locator('.messages').evaluate(el=>el.scrollTop),before,'later updates preserve selected transcript text');
    await page.evaluate(()=>{getSelection().removeAllRanges();document.querySelector('.messages').scrollTop=0;});
    eventSink.write('id: 63\nevent: delta\ndata: {"text":"Another later live update"}\n\n');
    await page.waitForTimeout(100);
    assert.equal(await page.locator('.messages').evaluate(el=>el.scrollTop),0,'later updates preserve deliberate upward reading');
    assert.ok(before>0,'replay content is taller than the narrow transcript viewport');
   }
   await context.close();context=null;
  });
  await t.test('late fallback for a prior session cannot affect the newly opened session',async()=>{
   scenario={status:'fallback',output:`Stale synthetic output. ${'Must remain detached. '.repeat(80)}`};
   fallbackResponseSent=false;fallbackRequest=new Promise(resolve=>{fallbackRequestResolve=resolve;});
   context=await browser.newContext({viewport:{width:320,height:640},hasTouch:true,serviceWorkers:'block'});
   const page=await context.newPage();page.setDefaultTimeout(5000);page.on('pageerror',e=>evidence.errors.push(e.message));
   await page.addInitScript(()=>localStorage.setItem('hermes:synthetic-owner:run:s0','run-fallback'));
   await page.goto(url);
   await page.locator('.session-row').first().click();
   await fallbackRequest;
   await page.getByRole('button',{name:'Back to chats'}).click();
   await page.locator('.session-row').filter({hasText:'Second synthetic session'}).click();
   await page.locator('.messages').waitFor();
   await page.getByText('Second session history 11.',{exact:false}).waitFor();
   const before=await page.locator('.messages').evaluate(el=>({top:el.scrollTop,gap:el.scrollHeight-el.clientHeight-el.scrollTop}));
   assert.equal(fallbackResponseSent,false,'old run lookup is still pending after navigation');
   await page.waitForTimeout(900);
   assert.equal(await page.locator('.conversation-head h1').textContent(),'Second synthetic session');
   assert.equal(await page.locator('.live-message').count(),0,'prior run replay is not rendered into the new session');
   assert.deepEqual(await page.locator('.messages').evaluate(el=>({top:el.scrollTop,gap:el.scrollHeight-el.clientHeight-el.scrollTop})),before,'late prior-session work cannot change the new transcript position');
   await context.close();context=null;
  });
  assert.deepEqual(evidence.errors,[]);
 }finally{
  if(process.env.HERMES_TEST_ARTIFACT_DIR){await mkdir(process.env.HERMES_TEST_ARTIFACT_DIR,{recursive:true});await writeFile(process.env.HERMES_TEST_ARTIFACT_DIR+'/session-opening-scroll.json',JSON.stringify(evidence,null,2));}
  await context?.close();await browser?.close();if(server){server.closeAllConnections();await new Promise(r=>server.close(r));}await rm(tmp,{recursive:true,force:true});
 }
});
