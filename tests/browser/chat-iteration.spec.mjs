import {artifactURL} from './artifacts.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {execFileSync} from 'node:child_process';
import {readFile,mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const dir=process.env.HERMES_FRONTEND_DIR ? process.env.HERMES_FRONTEND_DIR.replace(/\/$/,'')+'/' : fileURLToPath(new URL('../../frontend/',import.meta.url));

test('monochrome navigation, filtered sessions, chronological public activity and composer Stop in real browser',{timeout:45000},async()=>{
 const sessions=[{id:'human',title:'Human fixture',kind:'chats',source:'cli'},{id:'cron',title:'Cron fixture',kind:'cron',source:'cron'},{id:'probe',title:'Verification fixture',kind:'tests',source:'test'}];
 const events=[],clients=new Set();let stops=0;const calls=[];
 const snapshot=JSON.parse(execFileSync(process.env.HERMES_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('./reopen_snapshot_fixture.py',import.meta.url))],{encoding:'utf8'}));
 assert.equal(snapshot.items.filter(m=>m.role==='user'&&m.content==='Isolated fixture input.').length,0,'actual backend excludes already persisted current input');
 assert.equal(snapshot.items.filter(m=>m.role==='tool'&&m.content==='Frozen fixture tool result').length,0,'actual backend excludes frozen copy of active tools');
 const run={...snapshot.run,id:'ux-run',session_id:'human'};
 const encode=e=>`id: ${e.id}\nevent: ${e.name}\ndata: ${JSON.stringify(e.data)}\n\n`;
 function emit(name,data){const e={id:events.length+1,name,data};events.push(e);for(const c of clients)c.write(encode(e));}
 const json=(res,obj)=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(obj));};
 const server=createServer(async(req,res)=>{
  const url=new URL(req.url,'http://fixture'),p=url.pathname.replace('/hermes/app-api','');
  if(url.pathname.startsWith('/hermes/app-api')){
   calls.push(req.method+' '+p);
   if(p==='/auth/me')return json(res,{user:{id:'fixture-owner',role:'owner',status:'ready'},csrf_token:'fixture'});
   if(p==='/sessions'){const kind=url.searchParams.get('kind')||'chats',q=url.searchParams.get('q')||'';const items=sessions.filter(s=>(kind==='all'||s.kind===kind)&&s.title.includes(q));return json(res,{items,total:items.length});}
   if(p==='/sessions/cron')return json(res,{id:'cron',title:'Cron fixture'});
   if(p.endsWith('/messages'))return json(res,p.includes('/human/')?{...snapshot,run}:{items:[],total:0,offset:0,run:null});
   if(p==='/runs/ux-run')return json(res,run);
   if(p==='/runs/ux-run/events'){
    res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache'});res.write('retry: 1000\n\n');clients.add(res);res.on('close',()=>clients.delete(res));const after=Number(req.headers['last-event-id']||0);for(const e of events)if(e.id>after)res.write(encode(e));return;
   }
   if(p==='/runs/ux-run/stop'){stops++;run.status='stopping';return json(res,run);}
   if(p==='/inbox')return json(res,{items:[{id:'cron-result',title:'Scheduled fixture result',body:'Fixture cron result is available without opening its chat.',session_id:'cron',read:true}]});
   return json(res,{items:[]});
  }
  const rel=url.pathname.replace(/^\/hermes\//,'')||'index.html';if(rel.includes('..')){res.writeHead(400).end();return;}
  try{const b=await readFile(dir+rel);res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',js:'text/javascript',mjs:'text/javascript',svg:'image/svg+xml',webmanifest:'application/manifest+json'})[rel.split('.').pop()]||'application/octet-stream'});res.end(b);}catch{res.writeHead(404).end();}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const context=await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block',reducedMotion:'reduce'}),page=await context.newPage();page.setDefaultTimeout(5000);const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);
  const filter=page.getByRole('group',{name:'Conversation filter'}),filterToggle=page.getByRole('button',{name:'Filter conversations',exact:true});await filterToggle.waitFor();assert.equal(await filter.isVisible(),false);
  const searchToggle=page.getByRole('button',{name:'Search conversations',exact:true});
  assert.equal(await page.getByRole('searchbox',{name:'Search conversations'}).count(),0,'search input collapsed by default');
  assert.equal(await page.locator('.session-symbol').count(),0,'no repeated generic icons');
  for(const width of [320,390,1280]){
   await page.setViewportSize({width,height:844});
   const fb=await filterToggle.boundingBox(),sb=await searchToggle.boundingBox(),nb=await page.getByRole('button',{name:'New chat',exact:true}).boundingBox();assert.ok(Math.abs(fb.y-sb.y)<5&&Math.abs(nb.y-sb.y)<5,'Search, Filter, New share the compact toolbar');assert.ok(sb.width>=44&&sb.height>=44);assert.ok(fb.x>=sb.x+sb.width&&nb.x>=fb.x+fb.width,'Search, Filter, New do not overlap');
   await searchToggle.click();const input=page.getByRole('searchbox',{name:'Search conversations'});assert.equal(await input.evaluate(el=>el===document.activeElement),true);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   await input.fill('Human');await input.press('Enter');await page.getByRole('button',{name:'Human fixture',exact:true}).waitFor();assert.equal(await input.inputValue(),'Human');
   await page.getByRole('button',{name:'Close search',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.search')?.hidden);assert.equal(await searchToggle.evaluate(el=>el===document.activeElement),true);
  }
  for(const width of [320,390,1280]){await page.setViewportSize({width,height:844});await filterToggle.click();const box=await filter.boundingBox();assert.ok(box.width<=300 && box.x>=0 && box.x+box.width<=width,'compact dropdown fits viewport');assert.equal(await filter.locator('select,svg').count(),0);for(const option of await filter.getByRole('button').all()){const b=await option.boundingBox();assert.ok(b.width>=44 && b.height>=44);}await page.keyboard.press('Escape');assert.equal(await filter.isVisible(),false);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);}
  await mkdir(artifactURL(),{recursive:true});
  for(const theme of ['light','dark'])for(const width of [320,390,1280]){await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);await page.setViewportSize({width,height:844});await page.screenshot({path:fileURLToPath(artifactURL(`restrained-filter-${width}-${theme}.png`))});}
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.getByRole('button',{name:'Cron fixture',exact:true}).count(),0);
  assert.equal(await page.getByRole('button',{name:'Verification fixture',exact:true}).count(),0);
  assert.equal(await page.locator('.nav-icon svg[stroke=\"currentColor\"]').count(),4);
  await filterToggle.click();await filter.getByRole('button',{name:'Cron',exact:true}).click();await page.getByRole('button',{name:'Cron fixture',exact:true}).waitFor();
  await filterToggle.click();await filter.getByRole('button',{name:'Tests',exact:true}).click();await page.getByRole('button',{name:'Verification fixture',exact:true}).waitFor();
  await page.getByRole('button',{name:'Inbox',exact:true}).click();await page.locator('[data-inbox-id="cron-result"] summary').click();await page.getByText('Fixture cron result is available without opening its chat.',{exact:true}).waitFor();await page.getByRole('button',{name:'Open conversation'}).click();await page.getByRole('textbox',{name:'Message Hermes'}).waitFor();
  await page.getByRole('button',{name:'Back to chats'}).click();await filterToggle.click();await filter.getByRole('button',{name:'Chats',exact:true}).click();await page.getByRole('button',{name:'Human fixture',exact:true}).click();await page.getByRole('button',{name:'Stop run',exact:true}).waitFor();
  const stop=page.getByRole('button',{name:'Stop run',exact:true});assert.equal(await stop.locator('xpath=ancestor::summary').count(),0,'stop is not nested in disclosure toggle');
  await page.waitForFunction(()=>document.querySelector('.live-message'));
  const geometry=()=>page.locator('.messages').evaluate(el=>({top:el.scrollTop,height:el.scrollHeight,client:el.clientHeight,gap:el.scrollHeight-el.scrollTop-el.clientHeight}));
  assert.ok(Math.abs((await geometry()).gap)<=1,'initial transcript starts at bottom');
  for(let i=0;i<20;i++)emit('commentary',{text:`Public scroll probe ${i}`});
  await page.waitForFunction(()=>document.querySelectorAll('.live-message .activity-summary').length===20);
  const following=await geometry();console.log('COMMENTARY_FOLLOW',following);
  assert.ok(Math.abs(following.gap)<=1,`commentary keeps bottom-follow: ${JSON.stringify(following)}`);
  await page.locator('.messages').evaluate(el=>{el.scrollTop=0;el.dispatchEvent(new Event('scroll'));});
  assert.ok((await geometry()).gap>100,'reader deliberately scrolls away from bottom');
  emit('commentary',{text:'Public scroll probe while reading history'});
  await page.waitForFunction(()=>document.querySelectorAll('.live-message .activity-summary').length===21);
  const reading=await geometry();console.log('COMMENTARY_READING',reading);
  assert.equal(reading.top,0,'commentary preserves explicitly scrolled reader position');
  // Isolate the scroll probe: replay only the original chronology fixture below.
  await page.getByRole('button',{name:'Back to chats'}).click();events.length=0;
  await page.getByRole('button',{name:'Human fixture',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.live-message'));
  emit('commentary',{text:'Checking public sources.'});emit('tool',{event:'tool.started',tool:'web_search',tool_call_id:'t1',summary:'Search: fixture'});emit('tool',{event:'tool.completed',tool:'web_search',tool_call_id:'t1',error:false});emit('commentary',{text:'Comparing public results.'});emit('tool',{event:'tool.started',tool:'read_file',tool_call_id:'t2',summary:'Read: fixture'});
  await page.locator('.live-message .tool-preview').nth(1).waitFor({state:'attached'});
  assert.equal(await page.locator('.live-message .activity-summary').count(),2,'public commentary kept between tool groups');
  assert.equal(await page.locator('.live-message .tool-activity:not([hidden])').count(),2,'tool groups preserve commentary order');
  const ordered=await page.locator('.live-message').evaluate(el=>[...el.querySelectorAll('details.activity-summary,details.tool-activity')].filter(n=>!n.hidden).map(n=>n.classList.contains('activity-summary')?'text':'tools'));assert.deepEqual(ordered,['text','tools','text','tools']);
  const stopBox=await stop.boundingBox(),headingBox=await page.locator('.live-activity-heading').boundingBox();assert.ok(stopBox.y>=headingBox.y && stopBox.y+stopBox.height<=headingBox.y+headingBox.height,'Stop in Activity header');assert.equal(await page.locator('.live-activity-heading button').count(),1);assert.ok(stopBox.height>=44&&stopBox.width>=44,'44px Stop');
  await page.getByRole('button',{name:'Back to chats'}).click();await page.getByRole('button',{name:'Human fixture',exact:true}).click();await page.locator('.live-message .tool-preview').nth(1).waitFor({state:'attached'});assert.equal(await page.locator('.user-message').filter({hasText:'Isolated fixture input.'}).count(),1);assert.equal(await page.locator('.tool-activity:not([hidden])').count(),2,'no frozen native tool box beside replayed live groups');assert.equal(await page.locator('.live-message').count(),1);assert.equal(await page.locator('.live-message .activity-summary').count(),2,'replayed commentary appears once per event');
  for(const size of [{width:320,height:568},{width:390,height:450},{width:844,height:390}]){await page.setViewportSize(size);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);}
  await page.setViewportSize({width:390,height:844});await stop.click();assert.equal(stops,1);assert.equal(await page.locator('details.tool-activity[open]').count(),0,'Stop does not expand tools');
  run.status='cancelled';emit('done',{status:'cancelled'});await stop.waitFor({state:'hidden'});
  assert.equal(calls.filter(c=>c==='POST /runs').length,0,'reopen never resubmits');assert.deepEqual(errors,[]);
  await mkdir(artifactURL(),{recursive:true});await page.screenshot({path:fileURLToPath(artifactURL('chat-iteration-mobile.png'))});
 }finally{await browser.close();for(const c of clients)c.end();server.closeAllConnections();await new Promise(r=>server.close(r));}
});
