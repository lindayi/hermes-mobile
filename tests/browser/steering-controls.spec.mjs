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
test('real HTTP/SSE narrow light and dark composer has compact separate Stop; approval blocks steer',{timeout:30000},async()=>{
 let events,posts=[],records=[];
 const server=createServer(async(req,res)=>{
  const u=new URL(req.url,'http://fixture'),p=u.pathname.replace('/hermes/app-api','');
  const json=o=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(o));};
  if(u.pathname.startsWith('/hermes/app-api/')){
   if(req.method==='POST'){let body='';for await(const chunk of req)body+=chunk;posts.push({p,method:req.method,body:JSON.parse(body),csrf:req.headers['x-csrf-token']});if(p.endsWith('/steer')){records=[{...JSON.parse(body),status:'accepted_unconfirmed'}];return json(records[0]);}return json({status:'stopping'});}
   if(p==='/auth/me')return json({user:{id:'fixture',status:'ready'},csrf_token:'fixture'});
   if(p==='/sessions')return json({items:[{id:'s',title:'Fixture'}],total:1});
   if(p.endsWith('/messages'))return json({items:[],run:{id:'r',session_id:'s',status:'running',input:'Original'}});
   if(p.endsWith('/controls'))return json({steering:true,attempts:records});
   if(p.endsWith('/model-options'))return json({available:true,default:{provider:'fixture',model:'fixture-long-model-identifier'},models:[{id:'fixture-long-model-identifier',provider:'fixture',label:'Available model',reasoning_efforts:[]}]});
   if(p.endsWith('/events')){res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache'});res.write(': ready\n\n');events=res;return;}
   return json({items:[]});
  }
  const rel=u.pathname.replace(/^\/hermes\//,'')||'index.html';
  try{const b=await readFile(process.env.HERMES_FRONTEND_DIR?join(process.env.HERMES_FRONTEND_DIR,rel):new URL('../../frontend/'+rel,import.meta.url));res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',js:'text/javascript',mjs:'text/javascript'})[rel.split('.').pop()]||'application/octet-stream'});res.end(b);}catch{res.writeHead(404).end();}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER || '/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{const page=await browser.newPage({viewport:{width:320,height:740},serviceWorkers:'block'});page.setDefaultTimeout(4000);const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);await page.getByRole('button',{name:'Fixture',exact:true}).click();
  const steer=page.getByRole('button',{name:'Steer current run',exact:true}),stop=page.getByRole('button',{name:'Stop run',exact:true});await steer.waitFor();
  for(const size of [{width:320,height:740},{width:390,height:844},{width:390,height:450},{width:320,height:450}])for(const theme of ['light','dark']){
   await page.setViewportSize(size);await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
   const a=await stop.boundingBox(),b=await steer.boundingBox(),heading=await page.locator('.live-activity-heading').boundingBox(),model=await page.locator('.model-control-button').boundingBox();
   assert.ok(a.width>=44 && a.height>=44);assert.ok(Math.abs(a.x+a.width-heading.x-heading.width)<1,'Stop right aligned');assert.ok(a.y>=heading.y && a.y+a.height<=heading.y+heading.height,'Stop inside header');assert.ok(a.y>=0 && a.y+a.height<=size.height,'Stop in viewport');assert.ok(a.y+a.height<=model.y,'Stop separated from model selection');assert.ok(b.x+b.width<=size.width);assert.equal(await stop.textContent(),'Stop');
   const color=await stop.evaluate(el=>{const s=getComputedStyle(el);return {color:s.color,border:s.borderTopColor,width:s.borderTopWidth,danger:getComputedStyle(document.documentElement).getPropertyValue('--danger').trim()};});
   assert.equal(color.border,color.color);assert.equal(color.width,'1px');assert.equal(color.color,theme==='dark'?'rgb(255, 170, 170)':'rgb(168, 47, 52)');
   const text=page.getByRole('textbox',{name:'Message Hermes',exact:true}),expand=page.getByRole('button',{name:'Expand editor',exact:true});await text.fill('Long typed text must not collide with Expand. '.repeat(5));
   const t=await text.boundingBox(),e=await expand.boundingBox(),composer=await page.locator('.composer').boundingBox();assert.ok(e.width>=44 && e.height>=44);assert.ok(e.x>=t.x+t.width,'Expand is beside, not over, the textarea');assert.ok(Math.abs(e.y-t.y)<1,'Expand aligned at textarea top');assert.ok(e.x+e.width>composer.x+composer.width-20,'Expand in composer top-right');await expand.click();await page.getByRole('button',{name:'Done',exact:true}).click();assert.deepEqual(await text.boundingBox(),t,'closing editor preserves layout');assert.equal(await stop.evaluate(el=>{const r=el.getBoundingClientRect(),m=document.querySelector('.messages').getBoundingClientRect();return r.y>=m.y && r.bottom<=m.bottom;}),true,'Stop remains reachable after restoring the editor');
   assert.equal(await stop.locator('xpath=ancestor::details').count(),0);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),size.width);await mkdir(artifactURL(),{recursive:true});await page.screenshot({path:fileURLToPath(artifactURL(`steering-${size.width}x${size.height}-${theme}.png`))});
  }
  events.write('event: commentary\ndata: '+JSON.stringify({text:'Long public progress. '.repeat(120)})+'\n\n');
  events.write('event: tool\ndata: '+JSON.stringify({name:'terminal',tool_call_id:'t',status:'running',summary:'Fixture command'})+'\n\n');
  await page.locator('.tool-preview').waitFor({state:'attached'});
  const stopReachable=async()=>{const geometry=await stop.evaluate(el=>{const r=el.getBoundingClientRect(),m=document.querySelector('.messages').getBoundingClientRect();return {visible:r.y>=m.y && r.bottom<=m.bottom,hit:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===el};});assert.equal(geometry.visible,true,'Stop stays in the scroll viewport with long activity');assert.equal(geometry.hit,true,'Stop is not occluded');};
  for(const size of [{width:320,height:740},{width:390,height:450}])for(const theme of ['light','dark'])for(const open of [false,true]){await page.setViewportSize(size);await page.evaluate(({theme,open})=>{document.documentElement.dataset.theme=theme;document.querySelector('.tool-activity').open=open;const m=document.querySelector('.messages');m.scrollTop=m.scrollHeight;},{theme,open});await stopReachable();}
  await page.getByRole('textbox',{name:'Message Hermes',exact:true}).fill('Guidance');await steer.click();await page.waitForFunction(()=>document.querySelector('textarea').value==='');const operations=withoutValidatedPresence(posts);assert.equal(operations.length,1);assert.equal(operations[0].p,'/runs/r/steer');const bubble=page.locator('.user-message[data-guidance-key]');assert.equal(await bubble.count(),1);assert.match(await bubble.textContent(),/Guidance.*Accepted.*delivery unconfirmed/s);assert.doesNotMatch(await page.locator('.composer').textContent(),/Guidance/);events.write('event: steering\ndata: '+JSON.stringify(records[0])+'\n\n');await stopReachable();await page.screenshot({path:fileURLToPath(artifactURL('composer-guidance-390-short.png'))});await page.setViewportSize({width:390,height:844});await bubble.scrollIntoViewIfNeeded();await page.screenshot({path:fileURLToPath(artifactURL('guidance-accepted-mobile.png'))});
  events.write('event: approval\ndata: {"id":"a"}\n\n');await page.waitForFunction(()=>document.querySelector('[aria-label="Steer current run"]').disabled);assert.equal(await stop.isEnabled(),true);await stopReachable();await stop.click();await page.waitForFunction(()=>document.querySelector('[aria-label="Stop run"]').disabled);await stopReachable();assert.equal(withoutValidatedPresence(posts).filter(p=>p.p.endsWith('/stop')).length,1);assert.deepEqual(errors,[]);
 }finally{await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
});
