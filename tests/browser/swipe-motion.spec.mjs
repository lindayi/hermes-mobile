import {generatedAssets} from './generated-assets.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdtemp,rm,mkdir,writeFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const repo=fileURLToPath(new URL('../../',import.meta.url));
const proof=process.env.HERMES_TEST_ARTIFACT_DIR || process.env.HERMES_SWIPE_PROOF || '/tmp/hermes-swipe-proof';
async function fixture({reducedMotion='no-preference'}={}) {
 const tmp=await mkdtemp((process.env.TMPDIR || '/tmp')+'/b-');let server,browser,context;
 const close=async()=>{await context?.close();await browser?.close();if(server){server.closeAllConnections();await new Promise(r=>server.close(r));}await rm(tmp,{recursive:true,force:true});};
 try {
  const dir=generatedAssets(tmp);
  assert.match(await readFile(dir+'/index.html','utf8'),/app\.[a-f0-9]+\.js/);
  const state={calls:[],errors:[]};
  server=createServer(async(req,res)=>{
   const p=new URL(req.url,'http://fixture').pathname;
   if(p.startsWith('/hermes/app-api/')) {
    const path=p.slice('/hermes/app-api'.length);state.calls.push({path,method:req.method});let data={items:[]};
    if(path==='/auth/me')data={user:{id:'synthetic-owner',status:'ready'},csrf_token:'fixture'};
    if(path==='/sessions')data={deletion_available:true,total:30,items:Array.from({length:30},(_,i)=>({id:'s'+i,title:i?'Synthetic conversation '+i:'Long synthetic conversation title stays exactly the same width',source:'cli',run_status:'idle'}))};
    res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify(data));return;
   }
   const relative=p.replace(/^\/hermes\//,'') || 'index.html';
   if(relative.includes('..'))return res.writeHead(400).end();
   try{res.writeHead(200,{'Content-Type':({html:'text/html',js:'text/javascript',mjs:'text/javascript',css:'text/css',svg:'image/svg+xml'})[relative.split('.').pop()]||'application/octet-stream'}).end(await readFile(dir+'/'+relative));}catch{res.writeHead(404).end();}
  });await new Promise(r=>server.listen(0,'127.0.0.1',r));
  await mkdir(proof,{recursive:true});
  browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage'],env:{...process.env,TMPDIR:tmp}});
  context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,serviceWorkers:'block',reducedMotion,recordVideo:{dir:proof,size:{width:390,height:844}}});
  const page=await context.newPage();page.setDefaultTimeout(5000);page.on('pageerror',e=>state.errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);await page.locator('.session-entry').first().waitFor();
  const row=page.locator('.session-row').first(),cdp=await context.newCDPSession(page);
  const touch=(type,x,y)=>cdp.send('Input.dispatchTouchEvent',{type,touchPoints:type==='touchEnd'||type==='touchCancel'?[]:[{x,y}]});
  return {page,row,touch,state,close};
 }catch(e){await close();throw e;}
}
const frame=page=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
const metrics=row=>row.evaluate(e=>{const r=e.getBoundingClientRect(),p=e.parentElement.getBoundingClientRect(),text=e.querySelector('.session-info').getBoundingClientRect();return {x:r.x-p.x,width:r.width,height:r.height,textWidth:text.width,entryHeight:p.height};});

test('keyboard action focus overlays the row without reflowing its text',{timeout:30000},async()=>{
 const h=await fixture(),{page,row}=h;try{
  const before=await metrics(row);await page.locator('.session-actions').first().focus();
  assert.equal((await metrics(row)).textWidth,before.textWidth,'focus must not change row content width');
  assert.equal(await page.locator('.session-actions').first().evaluate(e=>{const r=e.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===e;}),true,'focused action must be above translated row');
 }finally{await h.close();}
});

test('grabbing a settling row resumes from its rendered position without a reversal jump',{timeout:30000},async()=>{
 const h=await fixture(),{page,row,touch}=h;
 try {
  const b=await row.boundingBox(),x=b.x+180,y=b.y+b.height/2;
  await touch('touchStart',x,y);await touch('touchMove',x-50,y);await frame(page);await touch('touchEnd');await frame(page);
  await page.evaluate(()=>document.querySelector('.session-row').addEventListener('pointerdown',e=>{window.handoff=e.currentTarget.getBoundingClientRect().x-e.currentTarget.parentElement.getBoundingClientRect().x;},{capture:true,once:true}));
  await touch('touchStart',x,y);const handoff=await page.evaluate(()=>handoff);assert.ok(handoff<-50&&handoff>-80,'regrab really occurred during settle');
  await touch('touchMove',x+18,y);await frame(page);
  assert.ok(Math.abs((await metrics(row)).x-(handoff+18))<2,'reverse drag must start at rendered offset, not the -80 target');
  await touch('touchCancel');await page.waitForTimeout(300);assert.ok(Math.abs((await metrics(row)).x+80)<1,'cancel returns to committed open state');
 }finally{await h.close();}
});

test('generated CDP touch follows intermediate finger positions with stable text geometry and a multi-frame settle',{timeout:30000},async()=>{
 const h=await fixture(),{page,row,touch,state}=h;
 try {
  const b=await row.boundingBox(),x=b.x+b.width*.8,y=b.y+b.height/2,initial=await metrics(row),samples=[];
  await page.screenshot({path:proof+'/motion-00-closed.png'});
  await touch('touchStart',x,y);
  for(const dx of [-18,-32,-50]) {
   await touch('touchMove',x+dx,y);await frame(page);const m=await metrics(row);samples.push({dx,...m});
   await page.screenshot({path:proof+`/motion-${Math.abs(dx)}-drag.png`});
   assert.ok(Math.abs(m.x-dx)<2,`row must track finger before release: dx=${dx}, actual=${m.x}`);
   assert.equal(m.width,initial.width);assert.equal(m.textWidth,initial.textWidth);assert.equal(m.entryHeight,initial.entryHeight);
  }
  await page.evaluate(()=>{window.motionFrames=[];window.motionStart=performance.now();const row=document.querySelector('.session-row');const record=t=>{motionFrames.push({t:t-motionStart,x:row.getBoundingClientRect().x-row.parentElement.getBoundingClientRect().x,width:row.getBoundingClientRect().width});if(t-motionStart<400)requestAnimationFrame(record);};requestAnimationFrame(record);});
  await touch('touchEnd');await page.waitForTimeout(400);
  const frames=await page.evaluate(()=>motionFrames);await writeFile(proof+'/motion.json',JSON.stringify({initial,samples,frames},null,2));
  assert.ok(new Set(frames.filter(f=>f.x < -51 && f.x > -79).map(f=>Math.round(f.x))).size>=3,'release must include multiple intermediate positions');
  assert.ok(frames.every(f=>f.x>=-80.1 && f.x<=0),'settling never overshoots bounds');
  const end=await metrics(row);assert.equal(end.width,initial.width);assert.equal(end.textWidth,initial.textWidth);assert.ok(Math.abs(end.x+80)<1);
  assert.equal(await page.locator('.session-delete').first().isVisible(),true);await page.screenshot({path:proof+'/motion-90-open.png'});
  assert.equal(await page.locator('.messages,[role=dialog]').count(),0);assert.ok(state.calls.every(c=>c.method==='GET'));assert.deepEqual(state.errors,[]);
 }finally{await h.close();}
});
