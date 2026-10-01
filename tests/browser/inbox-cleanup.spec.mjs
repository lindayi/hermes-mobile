import {generatedAssets} from './generated-assets.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const executeFile=promisify(execFile);
import {readFile,mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const repo=fileURLToPath(new URL('../../',import.meta.url));
const python=process.env.HERMES_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url));

test('Inbox cleanup persists through real SQLite and browser reload without clearing unread or approvals',{timeout:45000},async()=>{
 const temp=await mkdtemp(join(tmpdir(),'hermes-inbox-browser-')),dir=generatedAssets(temp)+'/';
 const service=async(operation,id='')=>JSON.parse((await executeFile(python,['-c',`import sys,json
from backend.notifications import NotificationService
s=NotificationService(sys.argv[1]);op=sys.argv[2];ident=sys.argv[3]
if op=='seed':
 for delivery,title in [('one','Read report one'),('two','Read report two')]:
  item=s.ingest('owner',delivery,title,'Synthetic finished report',silent=True);s.mark_read('owner',item['id'])
 s.ingest('owner','unread','Unread report','Keep this unread',silent=True)
 other=s.ingest('other','one','Other account report','Not visible',silent=True);s.mark_read('other',other['id'])
elif op=='dismiss':s.dismiss('owner',ident)
elif op=='clear':s.clear_read('owner')
elif op=='redeliver':s.ingest('owner','one','Read report one','Synthetic finished report',silent=True)
print(json.dumps({'items':s.list_inbox('owner'),'read_count':s.read_count('owner'),'other_count':len(s.list_inbox('other'))}))`,join(temp,'notifications.sqlite'),operation,id],{encoding:'utf8',timeout:15000})).stdout);
 await service('seed');let mutations=0;
 const approval={id:'approval-fixture',run_id:'run-fixture',title:'Pending approval fixture',action:'No real action',expires_at:2000000000};
 const server=createServer(async(req,res)=>{
  const url=new URL(req.url,'http://fixture'),p=url.pathname.replace('/hermes/app-api','');
  const json=obj=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(obj));};
  if(url.pathname.startsWith('/hermes/app-api/')){
   if(p==='/auth/me')return json({user:{id:'owner',role:'owner',status:'ready'},csrf_token:'fixture'});
   if(p==='/inbox'&&req.method==='GET')return json(await service('list'));
   if(p==='/approvals')return json({items:[approval]});
   if(req.method==='POST'){
    assert.equal(req.headers['x-csrf-token'],'fixture');mutations++;
    if(p==='/inbox/clear-read'){await service('clear');return json({ok:true});}
    const match=p.match(/^\/inbox\/([^/]+)\/dismiss$/);if(match){await service('dismiss',decodeURIComponent(match[1]));return json({ok:true});}
    res.writeHead(400).end();return;
   }
   return json({items:[],total:0});
  }
  const rel=url.pathname.replace(/^\/hermes\//,'')||'index.html';if(rel.includes('..')){res.writeHead(400).end();return;}
  try{const b=await readFile(dir+rel);res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',js:'text/javascript',mjs:'text/javascript',svg:'image/svg+xml',webmanifest:'application/manifest+json'})[rel.split('.').pop()]||'application/octet-stream'});res.end(b);}catch{res.writeHead(404).end();}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const page=await browser.newPage({viewport:{width:390,height:844},serviceWorkers:'block'});page.setDefaultTimeout(15000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/hermes/?inbox=fixture`);
  await page.locator('summary strong').filter({hasText:'Read report one'}).waitFor();
  const row=page.locator('.inbox-item').filter({hasText:'Read report one'});await row.getByRole('button',{name:'Dismiss',exact:true}).click();
  await page.locator('summary strong').filter({hasText:'Read report one'}).waitFor({state:'detached'});assert.equal((await service('list')).read_count,1);
  await page.getByRole('button',{name:'Clear read',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Cancel',exact:true}).click();assert.equal(mutations,1,'cancel never mutates');
  await page.getByRole('button',{name:'Clear read',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Confirm change',exact:true}).click();
  await page.locator('summary strong').filter({hasText:'Read report two'}).waitFor({state:'detached'});assert.equal(mutations,2);
  await page.locator('summary strong').filter({hasText:'Unread report'}).waitFor();
  assert.equal(await page.locator('summary strong').filter({hasText:'Unread report'}).count(),1);assert.equal(await page.getByRole('heading',{name:'Pending approval fixture',exact:true}).count(),1);
  const state=await service('redeliver');assert.equal(state.items.length,1);assert.equal(state.read_count,0);assert.equal(state.other_count,1);
  await page.reload();await page.locator('summary strong').filter({hasText:'Unread report'}).waitFor();assert.equal(await page.locator('.inbox-item').count(),1);assert.equal(await page.locator('.approval').count(),1);
  assert.equal(await page.locator('.inbox-item').getByRole('button',{name:'Dismiss',exact:true}).count(),0,'unread cannot be silently dismissed');
  for(const width of [320,390,844]){await page.setViewportSize({width,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);}
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:join(process.env.HERMES_TEST_ARTIFACT_DIR || temp,'inbox-cleanup-mobile.png')});assert.deepEqual(errors,[]);
 }finally{await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));await rm(temp,{recursive:true,force:true});}
});
