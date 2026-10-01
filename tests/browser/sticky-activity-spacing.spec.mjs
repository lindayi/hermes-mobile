import {artifactURL} from './artifacts.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {withoutValidatedPresence} from './push-fixture-contract.mjs';
import {createServer} from 'node:http';
import {readFile,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const frontend=process.env.HERMES_FRONTEND_DIR || fileURLToPath(new URL('../../frontend/',import.meta.url));
const sizes=[{width:320,height:740},{width:390,height:844},{width:1280,height:900},{width:320,height:450},{width:390,height:450},{width:1280,height:450}];
async function fixture(t){
 let events;const posts=[];
 const server=createServer(async(req,res)=>{
  const url=new URL(req.url,'http://fixture'),p=url.pathname.replace('/hermes/app-api','');
  const json=o=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(o));};
  if(url.pathname.startsWith('/hermes/app-api/')){
   if(req.method==='POST'){let body='';for await(const chunk of req)body+=chunk;posts.push({p,method:req.method,body:JSON.parse(body),csrf:req.headers['x-csrf-token']});return json({status:'stopping'});}
   if(p==='/auth/me')return json({user:{id:'spacing-fixture',status:'ready'},csrf_token:'fixture'});
   if(p==='/sessions')return json({items:[{id:'s',title:'Activity spacing fixture'}],total:1});
   if(p.endsWith('/messages'))return json({items:Array.from({length:35},(_,i)=>({id:i,role:i%2?'assistant':'user',content:i===0?'History 0: first message.':`History ${i}: synthetic browser fixture with enough history to exercise scrolling.`})),run:{id:'r',session_id:'s',status:'running',input:'Synthetic current request'}});
   if(p.endsWith('/controls'))return json({steering:true,attempts:[]});
   if(p.endsWith('/events')){res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache'});res.write(': synthetic fixture\n\n');events=res;return;}
   if(p==='/runs/r')return json({id:'r',session_id:'s',status:'running'});
   return json({items:[]});
  }
  const name=url.pathname.replace(/^\/hermes\//,'')||'index.html';
  try{const b=await readFile(join(frontend,name));res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',js:'text/javascript',mjs:'text/javascript'})[name.split('.').pop()]||'application/octet-stream'});res.end(b);}catch{res.writeHead(404).end();}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 t.after(async()=>{await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));});
 const page=await browser.newPage({viewport:sizes[0],serviceWorkers:'block'});page.setDefaultTimeout(5000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));t.after(()=>assert.deepEqual(errors,[]));
 await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);
 await page.getByRole('button',{name:'Activity spacing fixture',exact:true}).click();
 await page.getByRole('button',{name:'Stop run',exact:true}).waitFor();
 await page.waitForFunction(()=>document.querySelector('.live-message'));
 // Wait for the real EventSource HTTP request, then stream deliberately long public activity.
 await new Promise((resolve,reject)=>{const end=Date.now()+4000;const check=()=>events?resolve():Date.now()>end?reject(Error('SSE fixture not connected')):setTimeout(check,10);check();});
 for(let i=0;i<3;i++)events.write('event: commentary\ndata: '+JSON.stringify({id:`progress-${i}`,text:`Synthetic live progress ${i}. `+'Synthetic live progress for sticky geometry. '.repeat(600)})+'\n\n');
 await page.waitForFunction(()=>document.querySelectorAll('.live-message .activity-summary').length===3);
 return {page,posts};
}
async function settle(page){await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));}
async function pin(page,size,theme){
 await page.setViewportSize(size);await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);await settle(page);
 await page.locator('.messages').evaluate(el=>el.scrollTop=el.scrollHeight);await settle(page);
 return page.evaluate(()=>{
  const rect=s=>{const r=document.querySelector(s).getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,width:r.width,height:r.height};};
  return {header:rect('.conversation-head'),heading:rect('.live-activity-heading'),messages:rect('.messages'),stop:rect('.steering-stop'),scroll:document.querySelector('.messages').scrollTop,documentWidth:document.documentElement.scrollWidth};
 });
}
test('scrolled Activity is flush with session header without cropping the first history message',{timeout:30000},async t=>{
 const {page}=await fixture(t);
 for(const size of sizes)for(const theme of ['light','dark']){
  const g=await pin(page,size,theme);t.diagnostic(JSON.stringify({size,theme,...g}));
  assert.ok(g.scroll>100,'real long history was scrolled');
  assert.ok(Math.abs(g.heading.top-g.header.bottom)<1,`Activity/header seam: ${g.heading.top-g.header.bottom}px at ${size.width}x${size.height} ${theme}`);
  assert.ok(g.heading.top>=g.header.bottom,'Activity never covers session header');
  assert.equal(g.documentWidth,size.width,'no horizontal document overflow');
  assert.deepEqual(await page.locator('.live-activity-heading').evaluate(el=>({position:getComputedStyle(el).position,top:getComputedStyle(el).top})),{position:'sticky',top:'0px'});
  await page.locator('.messages').evaluate(el=>el.scrollTop=0);await settle(page);
  const first=await page.locator('.messages > .message').first().evaluate(el=>{
   const r=el.getBoundingClientRect(),m=el.parentElement.getBoundingClientRect();
   const author=el.querySelector('.message-author').getBoundingClientRect();
   return {top:r.top,containerTop:m.top,bottom:r.bottom,containerBottom:m.bottom,authorTop:author.top,text:el.textContent};
  });
  assert.match(first.text,/History 0/);assert.ok(first.top>=first.containerTop+12,'first message keeps reading inset');assert.ok(first.authorTop>=first.containerTop,'first author not cropped');assert.ok(first.bottom<=first.containerBottom,'first message completely visible at scroll start');
 }
});
test('Stop is visually inset but keeps its native 44px target and pointer action',{timeout:30000},async t=>{
 const {page,posts}=await fixture(t),stop=page.getByRole('button',{name:'Stop run',exact:true});
 for(const size of sizes)for(const theme of ['light','dark']){
  const g=await pin(page,size,theme);
  assert.ok(g.stop.top-g.heading.top>=6,'Stop has at least 6px upper visual inset');
  assert.ok(g.heading.bottom-g.stop.bottom>=6,'Stop has at least 6px lower visual inset');
  assert.ok(g.stop.width>=44 && g.stop.height>=44,'actual native button target is at least 44px');
  assert.ok(Math.abs(g.stop.right-g.heading.right)<1,'Stop stays right aligned');
  assert.ok(g.stop.top>=g.messages.top && g.stop.bottom<=g.messages.bottom,'Stop stays inside scroll viewport');
  assert.equal(await stop.textContent(),'Stop');assert.equal(await stop.locator('xpath=ancestor::details').count(),0);
  const style=await stop.evaluate(el=>{const s=getComputedStyle(el);return {color:s.color,border:s.borderTopColor,width:s.borderTopWidth};});
  assert.deepEqual(style,{color:theme==='dark'?'rgb(255, 170, 170)':'rgb(168, 47, 52)',border:theme==='dark'?'rgb(255, 170, 170)':'rgb(168, 47, 52)',width:'1px'});
  assert.deepEqual(await stop.evaluate(el=>{const r=el.getBoundingClientRect();return [r.top+1,r.top+r.height/2,r.bottom-1].map(y=>document.elementFromPoint(r.x+r.width/2,y)===el);}),[true,true,true],'center and near-edge pointer targets are unobscured');
  await mkdir(artifactURL(),{recursive:true});
  await page.screenshot({path:fileURLToPath(artifactURL(`sticky-activity-${size.width}x${size.height}-${theme}.png`))});
 }
 // Click near the upper edge, not just the center, to exercise the real target and handler.
 const g=await pin(page,{width:320,height:450},'light');
 await page.mouse.click(g.stop.left+g.stop.width/2,g.stop.top+1);
 await page.waitForFunction(()=>document.querySelector('[aria-label="Stop run"]').disabled);
 const operations=withoutValidatedPresence(posts);assert.equal(operations.length,1);assert.equal(operations[0].p,'/runs/r/stop');
});
