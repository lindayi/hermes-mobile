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
test('recovered unknown retry has a 44px target at 320px and posts exact server identity',{timeout:30000},async()=>{
 let events,posts=[],records=[{run_id:'r',input:'Recovered guidance',idempotency_key:'server-recovered-key',status:'unknown'}];
 const server=createServer(async(req,res)=>{
  const u=new URL(req.url,'http://fixture'),p=u.pathname.replace('/hermes/app-api','');
  const json=o=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(o));};
  if(u.pathname.startsWith('/hermes/app-api/')){
   if(req.method==='POST'){let body='';for await(const chunk of req)body+=chunk;posts.push({p,path:u.pathname,method:req.method,body:JSON.parse(body),csrf:req.headers['x-csrf-token']});if(u.pathname==='/hermes/app-api/push/presence'){withoutValidatedPresence([{...posts.at(-1),p:u.pathname}]);return json({ok:true});}if(p.endsWith('/steer')){records=[{...JSON.parse(body),status:'accepted_unconfirmed'}];return json(records[0]);}return json({status:'stopping'});}
   if(u.pathname==='/hermes/app-api/push/preferences' && req.method==='GET')return json({revision:0,enabled:true,categories:{completion:true,approval:true,attention:true,scheduled:true,operational:true},hide_details:false});
   if(p==='/auth/me')return json({user:{id:'fixture',status:'ready'},csrf_token:'fixture'});
   if(p==='/sessions')return json({items:[{id:'s',title:'Fixture'}],total:1});
   if(p.endsWith('/messages'))return json({items:[],run:{id:'r',session_id:'s',status:'running',input:'Original'}});
   if(p.endsWith('/controls'))return json({steering:true,attempts:records});
   if(p.endsWith('/events')){res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache'});res.write(': ready\n\n');events=res;return;}
   return json({items:[]});
  }
  const rel=u.pathname.replace(/^\/hermes\//,'')||'index.html';
  try{const b=await readFile(process.env.HERMES_FRONTEND_DIR?join(process.env.HERMES_FRONTEND_DIR,rel):new URL('../../frontend/'+rel,import.meta.url));res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',js:'text/javascript',mjs:'text/javascript'})[rel.split('.').pop()]||'application/octet-stream'});res.end(b);}catch{res.writeHead(404).end();}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{const page=await browser.newPage({viewport:{width:320,height:740},serviceWorkers:'block'});page.setDefaultTimeout(4000);const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);await page.getByRole('button',{name:'Fixture',exact:true}).click();
  const steer=page.getByRole('button',{name:'Steer current run',exact:true}),stop=page.getByRole('button',{name:'Stop run',exact:true});await steer.waitFor();
  for(const theme of ['light','dark']){await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);const retry=page.getByRole('button',{name:'Retry this steering request',exact:true}),r=await retry.boundingBox();assert.ok(r.width>=44 && r.height>=44);assert.ok(r.x>=0 && r.x+r.width<=320);const a=await stop.boundingBox(),b=await steer.boundingBox();assert.ok(a.width>=44);assert.equal(a.height,44);assert.ok(a.y+a.height<=b.y);assert.equal(await stop.locator('xpath=ancestor::div[contains(@class,"live-activity-heading")]').count(),1);assert.ok(b.x+b.width<=320);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),320);await mkdir(artifactURL(),{recursive:true});await page.screenshot({path:fileURLToPath(artifactURL(`steering-retry-320-${theme}.png`))});}
  await page.getByRole('textbox',{name:'Message Hermes',exact:true}).fill('Guidance');await page.getByRole('button',{name:'Retry this steering request',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.guidance-status')?.textContent.includes('Accepted'));assert.equal(await page.getByRole('textbox',{name:'Message Hermes',exact:true}).inputValue(),'Guidance');const taskPosts=posts.filter(({p,...call})=>withoutValidatedPresence([call]).length);assert.deepEqual(taskPosts[0].body,{input:'Recovered guidance',idempotency_key:'server-recovered-key'});assert.equal(taskPosts.length,1);assert.equal(taskPosts[0].p,'/runs/r/steer');
  events.write('event: approval\ndata: {"id":"a"}\n\n');await page.waitForFunction(()=>document.querySelector('[aria-label="Steer current run"]').disabled);assert.equal(await stop.isEnabled(),true);await stop.click();assert.equal(posts.filter(p=>p.p.endsWith('/stop')).length,1);assert.deepEqual(errors,[]);
 }finally{await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
});
