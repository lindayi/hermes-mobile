import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,readdir,mkdtemp,rm} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {generatedAssets} from './generated-assets.mjs';
const browsers=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');

// Invariant regression, NOT an emulation of iOS's ContentChangeObserver.
// Linux WebKit's dispatchTapEvent manufactures compatibility mouse events;
// a passing native click there does not disprove physical iOS click suppression.
const cases=[['chromium',0],['chromium',8],...(process.env.HERMES_SESSION_WEBKIT==='1'?[['webkit',0]]:[])];
for(const [engine,dx] of cases)
test(`${engine} ${dx}px slop: resting session tap leaves presentation untouched before native activation`,{timeout:30000},async()=>{
 const tmp=await mkdtemp((process.env.TMPDIR||'/tmp')+'/b-');let server,browser;
 try{
  const dir=generatedAssets(tmp),names=await readdir(dir);
  const helper=names.find(n=>/^session-swipe\.[a-f0-9]+\.mjs$/.test(n));
  const css=names.find(n=>/^styles\.[a-f0-9]+\.css$/.test(n));
  assert.ok(helper&&css,'use generated controller and actual app CSS');
  assert.equal(await readFile(dir+'/'+helper,'utf8'),await readFile(new URL('../../frontend/session-swipe.mjs',import.meta.url),'utf8'));
  server=createServer(async(req,res)=>{
   if(req.url==='/')return res.writeHead(200,{'Content-Type':'text/html'}).end(`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/${css}"><div class="session-entry"><button class="session-row"><strong>Conversation</strong></button><button class="session-delete">Delete</button><button class="session-actions">Actions</button></div><script type="module">
    import {createSessionSwipe} from '/${helper}';
    const entry=document.querySelector('.session-entry'),row=entry.querySelector('.session-row'),remove=entry.querySelector('.session-delete'),actions=entry.querySelector('.session-actions');
    createSessionSwipe(document,window).attach(entry,row,remove,actions);
    window.proof={mutations:[],timers:[],trace:[],clicks:0};
    const observer=new MutationObserver(records=>proof.mutations.push(...records.map(r=>({target:r.target.className,attribute:r.attributeName,old:r.oldValue,value:r.target.getAttribute(r.attributeName)}))));
    observer.observe(entry,{subtree:true,attributes:true,attributeOldValue:true});
    const timeout=window.setTimeout;window.setTimeout=(fn,delay,...args)=>{proof.timers.push(delay);return timeout(fn,delay,...args);};
    for(const type of ['pointerdown','pointermove','pointerup','touchend','click'])document.addEventListener(type,e=>proof.trace.push({type,x:e.clientX,y:e.clientY,trusted:e.isTrusted,hidden:remove.hidden,display:getComputedStyle(remove).display,inert:remove.inert,prevented:e.defaultPrevented}));
    row.addEventListener('click',()=>proof.clicks++);
    window.resetProof=()=>{observer.takeRecords();proof={mutations:[],timers:[],trace:[],clicks:0};};window.ready=true;
   </script>`);
   if(![helper,css].includes(req.url.slice(1)))return res.writeHead(404).end();
   res.writeHead(200,{'Content-Type':req.url.endsWith('.css')?'text/css':'text/javascript'}).end(await readFile(dir+req.url));
  });await new Promise(r=>server.listen(0,'127.0.0.1',r));
  browser=await browsers[engine].launch({headless:true,...(engine==='chromium'?{executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',args:['--no-sandbox','--disable-dev-shm-usage']}:{})});
  const context=await browser.newContext({hasTouch:true,isMobile:true,viewport:{width:390,height:844},reducedMotion:'no-preference',serviceWorkers:'block'});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/`);await page.waitForFunction(()=>window.ready);
  for(const state of ['initial','settled-closed']){
   if(state==='settled-closed'){
    await page.locator('.session-row').press('Shift+F10');
    await page.keyboard.press('Escape');await page.waitForTimeout(300);
   }
   await page.evaluate(()=>resetProof());
   if(dx){
    const b=await page.locator('.session-row strong').boundingBox(),x=b.x+b.width/2,y=b.y+b.height/2;
    const cdp=await context.newCDPSession(page);
    await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y,id:1}]});
    await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x-dx,y,id:1}]});
    await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
    await cdp.detach();
   }else await page.locator('.session-row strong').tap();
   const proof=await page.evaluate(()=>window.proof);
   assert.equal(proof.clicks,1,`${state}: native activation still works`);
   if(dx)assert.ok(proof.trace.some(e=>e.type==='pointermove'&&Math.abs(proof.trace.find(p=>p.type==='pointerdown').x-e.x-dx)<1),'trusted within-slop movement actually occurred');
   assert.ok(proof.trace.some(e=>e.type==='pointerup')&&proof.trace.some(e=>e.type==='click')&&proof.trace.every(e=>e.trusted));
   assert.ok(proof.trace.every(e=>e.hidden&&e.display==='none'&&e.inert),`${state}: tap must not expose Delete before click: ${JSON.stringify(proof)}`);
   assert.deepEqual(proof.mutations,[],`${state}: no presentation/ARIA mutations on a resting tap`);
   assert.deepEqual(proof.timers,[],`${state}: no presentation settle timer on a resting tap`);
  }
  assert.deepEqual(errors,[]);
 }finally{
  await browser?.close();if(server){server.closeAllConnections();await new Promise(r=>server.close(r));}await rm(tmp,{recursive:true,force:true});
 }
});
