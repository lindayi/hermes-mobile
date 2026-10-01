import test from 'node:test';
import assert from 'node:assert/strict';
import {withoutValidatedPresence} from './push-fixture-contract.mjs';
import {createServer} from 'node:http';
import {execFileSync} from 'node:child_process';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const dir=process.env.HERMES_FRONTEND_DIR?process.env.HERMES_FRONTEND_DIR.replace(/\/$/,'')+'/':fileURLToPath(new URL('../../frontend/',import.meta.url));

test('completed latest tools survive actual SQLite snapshot reentry with and without native final persistence',{timeout:40000},async()=>{
 const fixtures=JSON.parse(execFileSync(process.env.HERMES_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('./latest_tools_fixture.py',import.meta.url))],{encoding:'utf8'}));
 let mode='overlay',streams=0,posts=0;const calls=[];
 const server=createServer(async(req,res)=>{
  const u=new URL(req.url,'http://fixture'),p=u.pathname.replace('/hermes/app-api','');
  const json=obj=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(obj));};
  if(u.pathname.startsWith('/hermes/app-api/')){
   let raw='';for await(const chunk of req)raw+=chunk;
   const call={path:u.pathname,method:req.method,body:raw?JSON.parse(raw):null,csrf:req.headers['x-csrf-token']};calls.push(call);
   if(req.method==='POST')posts+=withoutValidatedPresence([call]).length;
   if(u.pathname==='/hermes/app-api/push/presence' && req.method==='POST')return json({ok:true});
   if(u.pathname==='/hermes/app-api/push/preferences' && req.method==='GET')return json({revision:0,enabled:true,categories:{completion:true,approval:true,attention:true,scheduled:true,operational:true},hide_details:false});
   if(p==='/auth/me')return json({user:{id:'fixture-owner',status:'ready'},csrf_token:'fixture'});
   if(p==='/sessions')return json({items:[{id:'wa-1',title:'Replay fixture'}],total:1});
   if(p==='/sessions/wa-1/messages')return json(fixtures[mode]);
   if(p.endsWith('/events'))streams++;
   return json({items:[]});
  }
  const rel=u.pathname.replace(/^\/hermes\//,'')||'index.html';if(rel.includes('..')){res.writeHead(400).end();return;}
  try{const b=await readFile(dir+rel);res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',js:'text/javascript',mjs:'text/javascript',svg:'image/svg+xml',webmanifest:'application/manifest+json'})[rel.split('.').pop()]||'application/octet-stream'});res.end(b);}catch{res.writeHead(404).end();}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const page=await browser.newPage({viewport:{width:390,height:844},serviceWorkers:'block'});page.setDefaultTimeout(5000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);
  for(const variant of ['overlay','history','history']){
   mode=variant;await page.getByRole('button',{name:'Replay fixture',exact:true}).click();await page.getByRole('textbox',{name:'Message Hermes',exact:true}).waitFor();
   assert.equal(await page.locator('.tool-preview').count(),2,variant+' restores both actual journal tools once');
   assert.equal(await page.locator('.user-message').filter({hasText:'Completed fixture turn'}).count(),1);
   assert.equal(await page.getByText('Completed fixture answer',{exact:true}).count(),1);
   const details=await page.locator('.tool-summary').allTextContents();assert.ok(details.some(s=>s.includes('pytest tests -q')));assert.ok(details.some(s=>s.includes('docs/guide.md')));
   assert.equal(await page.locator('.tool-activity[open]').count(),0);
   await page.getByRole('button',{name:'Back to chats',exact:true}).click();
  }
  assert.equal(streams,0,'completed snapshots do not need SSE');assert.equal(posts,0,'restoring tools never reruns them');assert.deepEqual(errors,[]);
 }finally{await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
});
