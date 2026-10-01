import {artifactURL} from './artifacts.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {withoutValidatedPresence} from './push-fixture-contract.mjs';
import {createServer} from 'node:http';
import {execFileSync} from 'node:child_process';
import {readFile,mkdir,copyFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const dir=process.env.HERMES_FRONTEND_DIR?process.env.HERMES_FRONTEND_DIR.replace(/\/$/,'')+'/':fileURLToPath(new URL('../../frontend/',import.meta.url));
const python=process.env.HERMES_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url));

test('real backend previews survive live completion, saved history and safe expanded display',{timeout:40000},async()=>{
 const fixture=JSON.parse(execFileSync(python,['-c',`import json,sqlite3,tempfile
from pathlib import Path
from backend.native_catalog import NativeCatalog
from backend.tool_presentation import normalize_tool_event
with tempfile.TemporaryDirectory() as d:
 p=Path(d)
 with sqlite3.connect(p/'state.db') as c:
  c.executescript('CREATE TABLE sessions(id TEXT); CREATE TABLE messages(id INTEGER, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT, timestamp REAL, tool_call_id TEXT, reasoning_content TEXT, api_content TEXT);')
  c.execute("INSERT INTO sessions VALUES ('fixture')")
  calls=[{'id':'saved','function':{'name':'terminal','arguments':json.dumps({'command':'pytest tests/test_auth.py -q'})}}]
  c.execute('INSERT INTO messages VALUES (1,?,?,?,?,?,?,?,?)',('fixture','assistant','',json.dumps(calls),1,None,'PRIVATE_REASONING','PRIVATE_API'))
  c.execute('INSERT INTO messages VALUES (2,?,?,?,?,?,?,?,?)',('fixture','tool','Tests passed',None,2,'saved',None,None))
  c.execute('INSERT INTO messages VALUES (3,?,?,?,?,?,?,?,?)',('fixture','assistant','',json.dumps([{'id':'output','function':{'name':'read_file','arguments':'{}'}}]),3,None,None,None))
  c.execute('INSERT INTO messages VALUES (4,?,?,?,?,?,?,?,?)',('fixture','tool','Project uses SQLite for durable run storage.',None,4,'output',None,None))
  c.execute('INSERT INTO messages VALUES (5,?,?,?,?,?,?,?,?)',('fixture','assistant','',json.dumps([{'id':'range','function':{'name':'read_file','arguments':json.dumps({'path':'docs/guide.md','offset':20,'limit':5})}}]),5,None,None,None))
  c.execute('INSERT INTO messages VALUES (6,?,?,?,?,?,?,?,?)',('fixture','tool','Safe excerpt',None,6,'range',None,None))
 items=NativeCatalog({'default':p}).messages('default','fixture')['items']
 events=[normalize_tool_event({'event':'tool.started','tool':'terminal','preview':'pytest tests/test_auth.py -q'}),normalize_tool_event({'event':'tool.completed','tool':'terminal','error':False,'duration':1.25})]
 print(json.dumps({'items':items,'events':events}))`],{encoding:'utf8'}));
 const run={id:'r',session_id:'fixture',status:'running',input:'Synthetic inspection only',output:''};
 const session={id:'fixture',title:'Tool details fixture',source:'cli',run_status:'running'};
 const clients=new Set();const requests=[];
 const server=createServer(async(req,res)=>{
  const u=new URL(req.url,'http://fixture'),p=u.pathname.replace('/hermes/app-api','');
  const json=obj=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(obj));};
  if(u.pathname.startsWith('/hermes/app-api/')){
   let raw='';for await(const chunk of req)raw+=chunk;
   requests.push({path:u.pathname,method:req.method,body:raw?JSON.parse(raw):null,csrf:req.headers['x-csrf-token']});
   if(u.pathname==='/hermes/app-api/push/presence' && req.method==='POST'){withoutValidatedPresence([requests.at(-1)]);return json({ok:true});}
   if(u.pathname==='/hermes/app-api/push/preferences' && req.method==='GET')return json({revision:0,enabled:true,categories:{completion:true,approval:true,attention:true,scheduled:true,operational:true},hide_details:false});
   if(p==='/auth/me')return json({user:{id:'owner',role:'owner',status:'ready'},csrf_token:'fixture'});
   if(p==='/sessions')return json({items:[session],total:1});
   if(p==='/sessions/fixture/messages')return json({items:fixture.items,total:fixture.items.length,offset:0,run});
   if(p==='/sessions/fixture/telemetry')return json({model:'fixture-model',provider:null,context:{used_tokens:null,limit_tokens:null},usage:{}});
   if(p==='/runs/r')return json(run);
   if(p==='/runs/r/events'){res.writeHead(200,{'Content-Type':'text/event-stream'});res.write('retry: 1000\n\n');clients.add(res);res.on('close',()=>clients.delete(res));fixture.events.forEach((data,i)=>res.write(`id: ${i+1}\nevent: tool\ndata: ${JSON.stringify(data)}\n\n`));return;}
   return json({items:[]});
  }
  const rel=u.pathname.replace(/^\/hermes\//,'')||'index.html';if(rel.includes('..')){res.writeHead(400).end();return;}
  try{res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',js:'text/javascript',mjs:'text/javascript',svg:'image/svg+xml',webmanifest:'application/manifest+json'})[rel.split('.').pop()]||'application/octet-stream'});res.end(await readFile(dir+rel));}catch{res.end();}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const page=await browser.newPage({viewport:{width:390,height:844},serviceWorkers:'block'});page.setDefaultTimeout(5000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);await page.getByRole('button',{name:session.title,exact:true}).click();
  await page.locator('.live-message .tool-preview[data-status=success]').waitFor({state:'attached'});
  assert.equal(await page.locator('.live-message .tool-preview').count(),1,'completion updates existing entry');
  const summaries=await page.locator('.tool-preview').filter({has:page.locator('.tool-name',{hasText:'terminal'})}).locator('.tool-summary').allTextContents();assert.equal(summaries.length,2);for(const summary of summaries)assert.match(summary,/pytest tests\/test_auth\.py -q/,'actual saved/live backend preview preserves useful command');
  const reads=page.locator('.tool-preview').filter({has:page.locator('.tool-name',{hasText:'read_file'})});
  assert.match((await reads.allTextContents()).join(' '),/Project uses SQLite for durable run storage/,'saved result is useful when arguments were not recorded');
  assert.match((await reads.allTextContents()).join(' '),/guide\.md.*offset 20.*limit 5/,'actual catalogue preserves rich read range');
  const live=page.locator('.live-message .tool-activity');
  assert.match(await live.locator('.activity-preview').textContent(),/^terminal: .*pytest tests\/test_auth\.py -q.*1\.25s/,'collapsed preview includes identity, same useful context and recorded duration');
  assert.match(await live.locator('.tool-status-label').textContent(),/Succeeded.*1\.25s/);
  assert.equal(await page.locator('.tool-activity[open]').count(),0,'folded by default');
  for(const card of await page.locator('.tool-activity:not([hidden])').all())await card.locator(':scope > summary').click();
  for(const preview of await page.locator('.tool-summary').all())assert.equal(await preview.isVisible(),true);
  assert.doesNotMatch(await page.locator('body').innerText(),/PRIVATE_REASONING|PRIVATE_API|Provider.*unknown|context.*unavailable/i);
  for(const width of [320,390,844]){await page.setViewportSize({width,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);}
  await page.setViewportSize({width:390,height:844});await mkdir(artifactURL(),{recursive:true});await page.screenshot({path:fileURLToPath(artifactURL('tool-details-mobile.png'))});
  if(process.env.HERMES_LAYOUT_PROOF_DIR){
   await mkdir(process.env.HERMES_LAYOUT_PROOF_DIR,{recursive:true,mode:0o700});
   await copyFile(fileURLToPath(artifactURL('tool-details-mobile.png')),process.env.HERMES_LAYOUT_PROOF_DIR+'/tool-details-mobile.png');
   for(const card of await page.locator('.tool-activity:not([hidden])').all())await card.locator(':scope > summary').click();
   await page.screenshot({path:process.env.HERMES_LAYOUT_PROOF_DIR+'/tool-details-folded-mobile.png'});
  }
  assert.equal(withoutValidatedPresence(requests).some(r=>r.method==='POST'),false);assert.deepEqual(errors,[]);
 }finally{await browser.close();for(const c of clients)c.end();server.closeAllConnections();await new Promise(r=>server.close(r));}
});
