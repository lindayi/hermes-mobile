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
