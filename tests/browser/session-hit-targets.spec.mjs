import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdtemp,rm,mkdir,writeFile,readdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {generatedAssets} from './generated-assets.mjs';
const browsers=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const repo=fileURLToPath(new URL('../../',import.meta.url));
const hash=b=>createHash('sha256').update(b).digest('hex');

// Optional WebKit is explicit: it must launch successfully when requested, never silently skip.
// This is Linux Playwright WebKit, not physical iOS Safari certification.
for(const engine of process.env.HERMES_SESSION_WEBKIT==='1'?['chromium','webkit']:['chromium'])
test(`${engine}: session hit targets deliver trusted clicks and history requests`,{timeout:120000},async t=>{
 const tmp=await mkdtemp((process.env.TMPDIR||'/tmp')+'/b-');let server,browser,context;
 const evidence={engine,cases:[],errors:[],sourceHash:hash(await readFile(repo+'frontend/session-swipe.mjs'))},calls=[];
 try{
  const dir=generatedAssets(tmp),asset=(await readdir(dir)).find(n=>/^session-swipe\.[a-f0-9]+\.mjs$/.test(n));
  evidence.asset=asset;evidence.assetHash=hash(await readFile(dir+'/'+asset));
  assert.equal(evidence.sourceHash,evidence.assetHash,'exact candidate controller is served');
  server=createServer(async(req,res)=>{
   const p=new URL(req.url,'http://fixture').pathname;
   if(p.startsWith('/hermes/app-api/')){
    const path=p.slice('/hermes/app-api'.length);calls.push({path,method:req.method});let data={items:[]};
    if(path==='/auth/me')data={user:{id:'synthetic-owner',status:'ready'},csrf_token:'fixture'};
    if(path==='/sessions')data={deletion_available:true,total:30,items:Array.from({length:30},(_,i)=>({id:'s'+i,title:'Opening fixture '+i,source:'cli',updated_at:'2026-09-01T12:00:00Z',run_status:'idle'}))};
    if(path==='/sessions/s0/messages')data={items:[{id:'m0',role:'user',content:'Visible fixture message'}]};
    if(path==='/push/preferences')data={revision:0,enabled:false,categories:{completion:true,approval:true,attention:true,scheduled:true,operational:true},hide_details:false};
    res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify(data));return;
   }
   const relative=p.replace(/^\/hermes\//,'')||'index.html';
   if(relative.includes('..'))return res.writeHead(400).end();
   try{res.writeHead(200,{'Content-Type':({html:'text/html',js:'text/javascript',mjs:'text/javascript',css:'text/css',svg:'image/svg+xml'})[relative.split('.').pop()]||'application/octet-stream'}).end(await readFile(dir+'/'+relative));}catch{res.writeHead(404).end();}
  });await new Promise(r=>server.listen(0,'127.0.0.1',r));
  browser=await browsers[engine].launch({...(engine==='chromium'?{executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',args:['--no-sandbox','--disable-dev-shm-usage']}:{ }),headless:true,env:{...process.env,TMPDIR:tmp}});
  evidence.version=browser.version();
  const url=`http://127.0.0.1:${server.address().port}/hermes/`;
  const reset=async width=>{
   await context?.close();context=await browser.newContext({viewport:{width,height:900},hasTouch:true,isMobile:true,deviceScaleFactor:2,serviceWorkers:'block'});
   await context.addInitScript(()=>{
    window.inputTrace=[];
    for(const type of ['pointerdown','pointermove','pointerup','pointercancel','gotpointercapture','lostpointercapture','touchstart','touchend','mouseover','mousemove','mousedown','mouseup','click'])document.addEventListener(type,e=>{
     const row=e.target.closest?.('.session-row'),entry=row?.parentElement;
     const record={type,time:e.timeStamp,x:e.clientX,y:e.clientY,trusted:e.isTrusted,pointerType:e.pointerType,target:e.target.tagName+'.'+e.target.className,prevented:false,rowOffset:row?row.getBoundingClientRect().x-entry.getBoundingClientRect().x:null,transform:row?getComputedStyle(row).transform:null};inputTrace.push(record);
     setTimeout(()=>record.prevented=e.defaultPrevented,0);
    },true);
   });
   const page=await context.newPage();page.setDefaultTimeout(3000);page.on('pageerror',e=>evidence.errors.push(e.message));
   await page.goto(url);await page.locator('.session-row').first().waitFor();calls.length=0;return page;
  };
  const point=async(page,area)=>page.locator('.session-row').first().evaluate((row,area)=>{
   const selectors={title:'strong',source:'.source',metadata:'.session-meta',status:'.session-run-status'};
   const b=(selectors[area]?row.querySelector(selectors[area]):row).getBoundingClientRect();
   const x=area==='padding'?b.x+8:area==='whitespace'?b.x+b.width*.68:area==='metadata'?b.right-20:b.x+b.width/2;
   const y=area==='padding'?b.y+6:b.y+b.height/2;
   return {x,y,target:document.elementFromPoint(x,y)?.outerHTML};
  },area);
  for(const width of [390,820])for(const input of ['touch','mouse'])for(const area of ['title','source','metadata','status','padding','whitespace'])await t.test(`${width} ${input} ${area}`,async()=>{
   const page=await reset(width),p=await point(page,area);
   if(input==='touch')await page.touchscreen.tap(p.x,p.y);else await page.mouse.click(p.x,p.y);
   await page.waitForTimeout(200);
   const record={width,input,area,point:p,trace:await page.evaluate(()=>inputTrace),calls:[...calls],opened:await page.locator('.messages').count()};evidence.cases.push(record);
   assert.ok(record.trace.some(e=>e.type==='click'&&e.trusted),'trusted browser click must be emitted: '+JSON.stringify(record));
   assert.equal(record.opened,1,'first activation opens chat: '+JSON.stringify(record));
   assert.equal(calls.filter(c=>c.path==='/sessions/s0/messages').length,1);
   assert.match(await page.locator('.messages').innerText(),/Visible fixture message/);
   assert.equal(calls.filter(c=>c.method==='DELETE').length,0);
  });
  await t.test('successive taps, keyboard activation and accessible action controls',async()=>{
   const page=await reset(390),row=page.locator('.session-row').first();
   for(const mode of ['touch','touch','Enter','Space','mouse-motion','actions']){
    if(mode==='actions'){
     const actions=page.locator('.session-actions').first();
     await actions.focus();await actions.tap();
     assert.equal(await actions.getAttribute('aria-expanded'),'true');
     await page.locator('.session-delete').first().tap();
     await page.getByRole('dialog').waitFor();
     assert.equal(calls.filter(c=>c.method==='DELETE').length,0,'revealing and confirmation do not delete');
     await page.getByRole('button',{name:'Cancel',exact:true}).tap();
     await page.keyboard.press('Escape');await page.waitForTimeout(280);
    }
    const p=await point(page,'title');
    if(mode==='Enter'||mode==='Space')await row.press(mode);
    else if(mode==='mouse-motion'||mode==='actions'){await page.mouse.move(p.x,p.y);await page.mouse.down();await page.mouse.move(p.x-8,p.y);await page.mouse.up();}
    else await page.touchscreen.tap(p.x,p.y);
    await page.waitForTimeout(200);
    const record={mode,opened:await page.locator('.messages').count(),trace:await page.evaluate(()=>inputTrace),calls:[...calls]};evidence.cases.push(record);
    assert.equal(record.opened,1,`${mode}: activation opens chat: ${JSON.stringify(record)}`);
    assert.equal(calls.filter(c=>c.path==='/sessions/s0/messages').length,1,`${mode}: exactly one selected history request`);
    assert.equal(calls.filter(c=>c.method==='DELETE').length,0);
    await page.getByRole('button',{name:'Back to chats',exact:true}).tap();await row.waitFor();
    calls.length=0;await page.evaluate(()=>inputTrace=[]);
   }
  });
  // Capture targets are different on text and row whitespace. Exercise the slop
  // band on both, rather than one convenient central point only.
  if(engine==='chromium')for(const area of ['title','source','status','whitespace'])for(const dx of [6,8,11])await t.test(`touch slop ${area} ${dx}px`,async()=>{
   const page=await reset(390),p=await point(page,area),cdp=await context.newCDPSession(page);
   await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:p.x,y:p.y,id:1}]});
   await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:p.x-dx,y:p.y,id:1}]});
   await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
   await page.waitForTimeout(200);
   const record={area,dx,trace:await page.evaluate(()=>inputTrace),calls:[...calls],opened:await page.locator('.messages').count()};evidence.cases.push(record);
   assert.equal(record.opened,1,'incidental motion opens once: '+JSON.stringify(record));
   assert.ok(record.trace.some(e=>e.type==='click'&&e.trusted));
   assert.equal(calls.filter(c=>c.path==='/sessions/s0/messages').length,1);
   assert.equal(calls.filter(c=>c.method==='DELETE').length,0);
  });
  assert.deepEqual(evidence.errors,[]);
 }finally{
  if(process.env.HERMES_TEST_ARTIFACT_DIR){await mkdir(process.env.HERMES_TEST_ARTIFACT_DIR,{recursive:true});await writeFile(process.env.HERMES_TEST_ARTIFACT_DIR+`/session-hit-targets-${engine}.log`,JSON.stringify(evidence,null,2));}
  await context?.close();await browser?.close();if(server){server.closeAllConnections();await new Promise(r=>server.close(r));}await rm(tmp,{recursive:true,force:true});
 }
});
