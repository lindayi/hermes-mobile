import {artifactURL} from './artifacts.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {withoutValidatedPresence} from './push-fixture-contract.mjs';
import {createServer} from 'node:http';
import {readFile,mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const dir=process.env.HERMES_FRONTEND_DIR?process.env.HERMES_FRONTEND_DIR.replace(/\/$/,'')+'/':fileURLToPath(new URL('../../frontend/',import.meta.url));
test('compact controls fit 320/390 light/dark and Stop is separate in the Activity header',{timeout:60000},async()=>{
 const server=createServer(async(req,res)=>{try{const rel=new URL(req.url,'http://fixture').pathname.replace(/^\/hermes\//,'')||'index.html';if(rel.includes('..'))throw Error();const data=await readFile(dir+rel);res.writeHead(200,{'Content-Type':({html:'text/html',mjs:'text/javascript',js:'text/javascript',css:'text/css',svg:'image/svg+xml'})[rel.split('.').pop()]||'application/octet-stream'});res.end(data);}catch{res.writeHead(404).end();}});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const artifacts=fileURLToPath(artifactURL());await mkdir(artifacts,{recursive:true});
 try{for(const width of [320,390])for(const theme of ['light','dark']){
  const page=await browser.newPage({viewport:{width,height:844},serviceWorkers:'block'});page.setDefaultTimeout(7000);const errors=[],posts=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(({theme})=>{localStorage.setItem('hermes:theme',theme);window.fixtureStreams=[];window.EventSource=class{constructor(){this.listeners={};window.fixtureStreams.push(this);}addEventListener(n,f){this.listeners[n]=f;}emit(n,data){this.listeners[n]?.({data:JSON.stringify(data)});}close(){}};},{theme});
  await page.route('**/hermes/app-api/**',async route=>{const req=route.request(),p=new URL(req.url()).pathname.replace('/hermes/app-api','');let data={items:[]};if(req.method()==='POST')posts.push({p,method:req.method(),body:req.postDataJSON(),csrf:req.headers()['x-csrf-token']});
   if(p==='/auth/me')data={user:{id:'fixture',status:'ready'},csrf_token:'fixture'};
   if(p==='/sessions')data={items:[{id:'s',title:'Compact control fixture',source:'web',run_status:'idle'}],total:1};
   if(p.includes('/messages'))data={items:[],run:null};
   if(p==='/inbox')data={items:[{id:'read',read:true,title:'Read receipt with a deliberately long wrapping title',body:'Fixture-only receipt body. This is not a live notification.'},{id:'unread',read:false,title:'Unread receipt',body:'Retained.'}],read_count:1};
   if(p==='/runs')data={id:'r',session_id:'s',status:'running'};
   if(p.endsWith('/stop'))data={status:'stopping'};
   await route.fulfill({json:data});
  });
  const shot=async view=>page.screenshot({path:`${artifacts}compact-controls-${view}-${width}-${theme}.png`});
  const fit=async()=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'no horizontal overflow');
  await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);
  for(const name of ['Search conversations','New chat']){const b=page.getByRole('button',{name,exact:true});await b.waitFor();const r=await b.boundingBox();assert.ok(r.width>=44&&r.width<=48&&r.height>=44,`${name} compact 44px target: ${JSON.stringify(r)}`);assert.equal(await b.locator('svg[stroke="currentColor"]').count(),1);}
  await fit();await shot('chats');
  await page.getByRole('button',{name:'Inbox',exact:true}).click();const dismiss=page.getByRole('button',{name:'Dismiss',exact:true});await dismiss.waitFor();
  const card=page.locator('.inbox-item.read'),c=await card.boundingBox(),d=await dismiss.boundingBox();assert.ok(d.width>=44&&d.height>=44);assert.ok(d.x+d.width<=c.x+c.width && d.x>c.x+c.width/2 && d.y<c.y+16,'Dismiss is top-right inside receipt');
  const title=await card.locator('.inbox-summary-text > strong').boundingBox();assert.ok(title.x+title.width<=d.x,'title reserves close-control space');assert.equal(await page.locator('.unread').getByRole('button',{name:'Dismiss',exact:true}).count(),0);
  await fit();await shot('inbox');
  await page.getByRole('button',{name:'Chats',exact:true}).click();await page.getByRole('button',{name:'Compact control fixture',exact:true}).click();const send=page.getByRole('button',{name:'Send message',exact:true});await send.waitFor();const before=await send.boundingBox();
  const input=page.getByRole('textbox',{name:'Message Hermes'});await input.fill('Fixture submission');await send.click();const stop=page.getByRole('button',{name:'Stop run',exact:true});await stop.waitFor();const after=await stop.boundingBox();assert.ok(after.y+after.height<before.y,'Stop is separate from model/send footer');assert.equal(await page.locator('.live-activity-heading [aria-label="Stop run"]').count(),1);assert.equal(await stop.textContent(),'Stop');
  await input.fill('Unsent next draft');await fit();await shot('stop');await stop.click();await page.waitForFunction(()=>document.querySelector('[aria-label="Stop run"]')?.disabled);assert.equal(await input.inputValue(),'Unsent next draft');const operations=withoutValidatedPresence(posts);assert.deepEqual(operations.map(x=>x.p),['/runs','/runs/r/stop']);assert.deepEqual(operations[1].body,{});
  await page.evaluate(()=>window.fixtureStreams[0].emit('done',{status:'cancelled'}));await send.waitFor();assert.equal(await send.isEnabled(),true);assert.equal(await stop.count(),0);assert.equal(await input.inputValue(),'Unsent next draft');assert.deepEqual(await send.boundingBox(),before);assert.deepEqual(errors,[]);await page.close();
 }}finally{await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
});
