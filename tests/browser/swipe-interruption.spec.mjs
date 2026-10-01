import {generatedAssets} from './generated-assets.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {withoutValidatedPresence} from './push-fixture-contract.mjs';
import {createServer} from 'node:http';
import {readFile,mkdtemp,rm,mkdir,writeFile,readdir} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const repo=fileURLToPath(new URL('../../',import.meta.url));
const proof=process.env.HERMES_TEST_ARTIFACT_DIR || process.env.HERMES_SWIPE_INTERRUPTION_PROOF || '/tmp/hermes-swipe-interruption-proof';
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const frame=page=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
const metrics=row=>row.evaluate(e=>{
 const b=e.getBoundingClientRect(),p=e.parentElement,remove=p.querySelector('.session-delete');
 return {x:b.x-p.getBoundingClientRect().x,width:b.width,textWidth:e.querySelector('.session-info').getBoundingClientRect().width,
  open:p.classList.contains('actions-open'),expanded:p.querySelector('.session-actions').getAttribute('aria-expanded'),
  hidden:remove.hidden,inert:remove.inert,ariaHidden:remove.getAttribute('aria-hidden'),duration:getComputedStyle(e).transitionDuration};
});

// One real Chromium instance, fresh hashed frontend, synthetic loopback API only.
test('non-horizontal regrabs resume the interrupted committed settle',{timeout:60000},async t=>{
 const tmp=await mkdtemp((process.env.TMPDIR || '/tmp')+'/b-'),dir=generatedAssets(tmp);let server,browser,context;
 const evidence={cases:[],errors:[],sourceHash:hash(await readFile(repo+'frontend/session-swipe.mjs'))};
 const calls=[];
 try {
  const env={...process.env};delete env.HERMES_FRONTEND_DIR;
  const asset=(await readdir(dir)).find(n=>/^session-swipe\.[a-f0-9]+\.mjs$/.test(n));
  assert.ok(asset,'fixture serves a freshly generated hashed swipe controller');
  evidence.asset=asset;evidence.assetHash=hash(await readFile(dir+'/'+asset));
  assert.equal(evidence.assetHash,evidence.sourceHash,'generated controller must match this candidate');
  server=createServer(async(req,res)=>{
   const p=new URL(req.url,'http://fixture').pathname;
   if(p.startsWith('/hermes/app-api/')){
    const path=p.slice('/hermes/app-api'.length);let raw='';for await(const chunk of req)raw+=chunk;
    calls.push({path,method:req.method,body:raw?JSON.parse(raw):null,csrf:req.headers['x-csrf-token']});let data={items:[]};
    if(path==='/push/presence' && req.method==='POST'){withoutValidatedPresence([calls.at(-1)]);data={ok:true};}
    if(path==='/push/preferences' && req.method==='GET')data={revision:0,enabled:true,categories:{completion:true,approval:true,attention:true,scheduled:true,operational:true},hide_details:false};
    if(path==='/auth/me')data={user:{id:'synthetic-owner',status:'ready'},csrf_token:'fixture'};
    if(path==='/sessions')data={deletion_available:true,total:30,items:Array.from({length:30},(_,i)=>({id:'s'+i,title:i?'Synthetic conversation '+i:'Long synthetic conversation title keeps its original width while settling',source:'cli',run_status:'idle'}))};
    res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify(data));return;
   }
   const relative=p.replace(/^\/hermes\//,'')||'index.html';
   if(relative.includes('..'))return res.writeHead(400).end();
   try{res.writeHead(200,{'Content-Type':({html:'text/html',js:'text/javascript',mjs:'text/javascript',css:'text/css',svg:'image/svg+xml'})[relative.split('.').pop()]||'application/octet-stream'}).end(await readFile(dir+'/'+relative));}catch{res.writeHead(404).end();}
  });await new Promise(r=>server.listen(0,'127.0.0.1',r));
  browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage'],env:{...env,TMPDIR:tmp}});
  context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,serviceWorkers:'block',reducedMotion:'no-preference'});
  await context.addInitScript(()=>{
   window.pointerTrace=[];
   for(const type of ['pointerdown','pointermove','pointerup','pointercancel','click'])document.addEventListener(type,e=>pointerTrace.push({type,x:e.clientX,y:e.clientY,trusted:e.isTrusted}),true);
  });
  const page=await context.newPage();page.setDefaultTimeout(5000);page.on('pageerror',e=>evidence.errors.push(e.message));
  const url=`http://127.0.0.1:${server.address().port}/hermes/`,row=page.locator('.session-row').first();
  const cdp=await context.newCDPSession(page);
  const touch=(type,x,y)=>cdp.send('Input.dispatchTouchEvent',{type,touchPoints:type==='touchEnd'||type==='touchCancel'?[]:[{x,y,id:1}]});
  const reset=async()=>{await page.goto(url);await row.waitFor();calls.length=0;};
  for(const target of ['open','closed'])for(const completion of ['cancel','vertical','subthreshold'])await t.test(`${target} settle / ${completion}`,async()=>{
   await reset();
   const initial=await metrics(row),b=await row.boundingBox(),x=b.x+b.width*.65,y=b.y+b.height/2;
   if(target==='open'){
    await touch('touchStart',x,y);await touch('touchMove',x-50,y);await frame(page);await touch('touchEnd');await frame(page);
   }else{
    await row.press('Shift+F10');await page.waitForTimeout(300);
    await cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});await frame(page);
   }
   await touch('touchStart',x,y);
   const frozen=await metrics(row);
   assert.ok(frozen.x<-1&&frozen.x>-79,`must regrab a genuinely intermediate rendered offset, not an endpoint: ${frozen.x}`);
   assert.equal(frozen.duration,'0s','pointerdown freezes the actual compositor position');
   await page.waitForTimeout(80);
   assert.ok(Math.abs((await metrics(row)).x-frozen.x)<1,'held row stays at its grabbed position');
   await page.evaluate(()=>{
    window.resumeFrames=[];const e=document.querySelector('.session-row'),start=performance.now();
    const record=t=>{resumeFrames.push({t:t-start,x:e.getBoundingClientRect().x-e.parentElement.getBoundingClientRect().x});if(t-start<500)requestAnimationFrame(record);};requestAnimationFrame(record);
    window.pointerTrace=[];
   });
   if(completion==='cancel')await touch('touchCancel');
   else if(completion==='vertical'){
    // First move locks vertical intent; the larger move exercises native pan/cancel.
    await touch('touchMove',x,y-14);await touch('touchMove',x,y-65);await touch('touchEnd');
   }else{await touch('touchMove',x-8,y);await touch('touchEnd');}
   await page.waitForTimeout(550);
   const final=await metrics(row),frames=await page.evaluate(()=>resumeFrames),trace=await page.evaluate(()=>pointerTrace);
   const entry={target,completion,initial,frozen,final,frames,trace,calls:[...calls]};evidence.cases.push(entry);
   assert.ok(Math.abs(final.x-(target==='open'?-80:0))<1,`${completion} must resume committed ${target} target, not strand row at ${final.x}px`);
   const end=target==='open'?-80:0,lo=Math.min(frozen.x,end),hi=Math.max(frozen.x,end);
   assert.ok(new Set(frames.filter(f=>f.x>lo+1&&f.x<hi-1).map(f=>Math.round(f.x*10))).size>=3,'resuming must animate through multiple intermediate positions, not snap');
   assert.ok(frames.every(f=>f.x>=lo-1&&f.x<=hi+1),'resume stays between frozen position and committed target');
   assert.equal(final.open,target==='open');assert.equal(final.expanded,String(target==='open'));
   assert.equal(final.inert,target!=='open');assert.equal(final.hidden,target!=='open');assert.equal(final.ariaHidden,String(target!=='open'));
   assert.equal(final.width,initial.width);assert.equal(final.textWidth,initial.textWidth);
   assert.ok(trace.every(e=>e.trusted),'interruption uses native browser touch, not dispatched PointerEvents');
   if(completion==='cancel')assert.ok(trace.some(e=>e.type==='pointercancel'));
   if(completion==='vertical')assert.ok(trace.some(e=>e.type==='pointermove'&&Math.abs(e.y-y)>=12),'browser sees vertical intent');
   if(completion==='subthreshold')assert.ok(trace.some(e=>e.type==='pointerup'),'subthreshold gesture ends without cancellation');
   assert.equal(await page.locator('.messages,[role=dialog]').count(),0,'interruption must neither navigate nor prompt deletion');
   assert.ok(withoutValidatedPresence(calls).every(c=>c.method==='GET'&&!/^\/sessions\/s0(?:\/|$)/.test(c.path)),'no destructive or synthetic navigation API calls');
  });
  await t.test('ordinary stationary touch still opens its conversation',async()=>{
   await reset();const b=await row.boundingBox(),x=b.x+b.width*.65,y=b.y+b.height/2;
   await touch('touchStart',x,y);await touch('touchEnd');
   await page.waitForFunction(()=>document.querySelector('.messages'));
   assert.ok(calls.some(c=>/^\/sessions\/s0(?:\/|$)/.test(c.path)),'deliberate tap retains navigation');
   assert.ok(withoutValidatedPresence(calls).every(c=>c.method==='GET'));assert.equal(await page.locator('[role=dialog]').count(),0);
  });
  assert.deepEqual(evidence.errors,[]);
 }finally{
  await mkdir(proof,{recursive:true});await writeFile(proof+'/results.json',JSON.stringify(evidence,null,2));
  await context?.close();await browser?.close();if(server){server.closeAllConnections();await new Promise(r=>server.close(r));}await rm(tmp,{recursive:true,force:true});
 }
});
