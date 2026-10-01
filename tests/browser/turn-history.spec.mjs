import {artifactURL} from './artifacts.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {withoutValidatedPresence} from './push-fixture-contract.mjs';
import {createServer} from 'node:http';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {readFile,mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const dir=process.env.HERMES_FRONTEND_DIR?process.env.HERMES_FRONTEND_DIR.replace(/\/$/,'')+'/':fileURLToPath(new URL('../../frontend/',import.meta.url));
test('SQLite turn-aware pages reach complete earlier tool turns and public progress in mobile Chromium',{timeout:45000},async()=>{
 const out=await promisify(execFile)(process.env.HERMES_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('./turn_history_fixture.py',import.meta.url))],{timeout:10000,maxBuffer:2000000});const f=JSON.parse(out.stdout);let posts=0;const urls=[],calls=[];
 const server=createServer(async(req,res)=>{const u=new URL(req.url,'http://fixture'),p=u.pathname.replace('/hermes/app-api','');const json=obj=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(obj));};
 if(u.pathname.startsWith('/hermes/app-api/')){urls.push(u.search);let raw='';for await(const chunk of req)raw+=chunk;const call={path:u.pathname,method:req.method,body:raw?JSON.parse(raw):null,csrf:req.headers['x-csrf-token']};calls.push(call);if(req.method==='POST')posts+=withoutValidatedPresence([call]).length;if(u.pathname==='/hermes/app-api/push/presence' && req.method==='POST')return json({ok:true});if(u.pathname==='/hermes/app-api/push/preferences' && req.method==='GET')return json({revision:0,enabled:true,categories:{completion:true,approval:true,attention:true,scheduled:true,operational:true},hide_details:false});if(p==='/auth/me')return json({user:{id:'u',status:'ready'},csrf_token:'fixture'});if(p==='/sessions'){await new Promise(resolve=>setTimeout(resolve,200));return json({items:[{id:'wa-1',title:'Long history',run_status:'running'}],total:1});}if(p.endsWith('/messages'))return json(u.searchParams.has('latest')?f.initial:f.older);return json({items:[]});}
 const rel=u.pathname.replace(/^\/hermes\//,'')||'index.html';if(rel.includes('..'))return res.writeHead(400).end();try{res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',mjs:'text/javascript',js:'text/javascript',svg:'image/svg+xml',webmanifest:'application/manifest+json'})[rel.split('.').pop()]||'application/octet-stream'});res.end(await readFile(dir+rel));}catch{res.end();}});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',args:['--no-sandbox','--disable-dev-shm-usage']});
 try{const page=await browser.newPage({viewport:{width:390,height:844},reducedMotion:'reduce',colorScheme:'dark',serviceWorkers:'block'});page.setDefaultTimeout(7000);const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);
 // Document load is not session-list readiness: the API may still be pending.
 await page.getByRole('button',{name:'Long history',exact:true}).waitFor();
 assert.equal(await page.locator('.session-run-status .progress-ring').count(),1);assert.equal(await page.locator('.progress-ring').evaluate(el=>getComputedStyle(el).animationName),'none');
 await page.getByRole('button',{name:'Long history',exact:true}).click();await page.getByRole('textbox',{name:'Message Hermes'}).waitFor();
 assert.equal(await page.locator('.tool-preview').count(),140);assert.equal(await page.locator('.messages > .tool-activity').count(),1);assert.equal(await page.locator('.user-message').count(),1);
 const before=await page.locator('.messages').evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight);assert.ok(before<5,'open at latest');
 await page.getByRole('button',{name:'Load older messages',exact:true}).click();await page.getByText('Older question',{exact:true}).waitFor();assert.equal(await page.locator('.tool-preview').count(),760);assert.equal(await page.locator('.messages > .tool-activity').count(),2);assert.equal(await page.locator('.user-message').count(),2);assert.equal(await page.getByRole('button',{name:'Load older messages'}).count(),0);
 const progress=page.locator('.activity-summary');assert.equal(await progress.count(),1);assert.equal(await progress.evaluate(el=>el.open),true);assert.equal(await progress.locator('.activity-preview').isVisible(),false,'expanded progress does not repeat its preview');assert.match(await progress.innerText(),/I’ll inspect/);assert.doesNotMatch(await page.locator('.messages').textContent(),/PRIVATE/);assert.equal(await page.locator('.tool-activity[open]').count(),0);assert.ok(urls.filter(u=>u.includes('turn_boundary=true')).length>=2);
 await progress.scrollIntoViewIfNeeded();await mkdir(artifactURL(),{recursive:true});await page.screenshot({path:fileURLToPath(artifactURL('turn-history-progress-mobile.png'))});assert.equal(posts,0);assert.deepEqual(errors,[]);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 }finally{await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
});
