import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {execFileSync} from 'node:child_process';
import {mkdtemp,readFile,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {generatedAssets} from './generated-assets.mjs';
import {withoutValidatedPresence} from './push-fixture-contract.mjs';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const repo=fileURLToPath(new URL('../../',import.meta.url));
const python=process.env.HERMES_TEST_PYTHON || join(repo,'.venv/bin/python');

for(const mode of ['times','reminder'])test(`actual SQLite ${mode} history has single human turns and recorded local times`,{timeout:40000},async()=>{
 const temporary=await mkdtemp(join(tmpdir(),'hm-time-'));let server,browser;
 try{
  const assets=generatedAssets(temporary),fixture=JSON.parse(execFileSync(python,['-B',join(repo,'tests/browser/message_time_fixture.py'),mode],{cwd:repo,encoding:'utf8',timeout:15000})),requests=[],errors=[];
  server=createServer(async(req,res)=>{
   try{
    const url=new URL(req.url,'http://fixture');
    const json=value=>{res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
    if(url.pathname.startsWith('/hermes/app-api/')){
     let raw='';for await(const chunk of req)raw+=chunk;
     const call={path:url.pathname,method:req.method,body:raw?JSON.parse(raw):null,csrf:req.headers['x-csrf-token']};requests.push(call);
     const path=url.pathname.slice('/hermes/app-api'.length);
     if(path==='/auth/me')return json({user:{id:'owner',role:'owner',status:'ready'},csrf_token:'fixture'});
     if(path==='/sessions')return json({items:[{id:'fixture',title:'Message time fixture',source:'cli'}],total:1});
     if(path==='/sessions/fixture/messages')return json(fixture.page);
     if(path==='/push/presence' && req.method==='POST'){withoutValidatedPresence([call]);return json({ok:true});}
     if(path==='/push/preferences')return json({revision:0,enabled:true,categories:{completion:true,approval:true,attention:true,scheduled:true,operational:true},hide_details:false});
     return json({items:[]});
    }
    if(url.pathname==='/favicon.ico'){res.writeHead(204).end();return;}
    const relative=url.pathname==='/hermes/'?'index.html':url.pathname.slice('/hermes/'.length);
    if(!url.pathname.startsWith('/hermes/') || relative.split('/').includes('..')){res.writeHead(404).end();return;}
    const body=await readFile(join(assets,relative));res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',js:'text/javascript',mjs:'text/javascript',svg:'image/svg+xml',png:'image/png',webmanifest:'application/manifest+json'})[relative.split('.').pop()] || 'application/octet-stream'});res.end(body);
   }catch(error){errors.push(error.message);res.writeHead(500).end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER || '/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true,timezoneId:'America/Toronto',locale:'en-CA',serviceWorkers:'block'});page.setDefaultTimeout(5000);page.on('pageerror',error=>errors.push(error.message));
  const open=async()=>{await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);await page.getByRole('button',{name:'Message time fixture',exact:true}).click();await page.getByRole('textbox',{name:'Message Hermes'}).waitFor();};
  await open();
  if(mode==='reminder'){
   const human=page.locator('.user-message');assert.equal(await human.count(),1,'one original user question, not a rewritten duplicate');
   assert.equal(await human.locator('.markdown').textContent(),fixture.question);
   assert.equal(await page.locator('.assistant-message').filter({hasText:'Deployed'}).count(),1,'one final answer');
   assert.doesNotMatch(await human.textContent(),/active task list|Skills pruned/);
   const reminder=page.locator('details.context-compression').filter({hasText:'Your active task list was preserved'});
   assert.equal(await reminder.count(),1,'runtime suffix remains once as separate process information');
   assert.equal(await reminder.evaluate(el=>el.open),false);assert.equal(await reminder.locator('.message-author').count(),0);
   assert.equal(await reminder.locator('summary time').getAttribute('datetime'),'1970-01-01T00:00:23.000Z');
   assert.equal(await human.locator('time').getAttribute('datetime'),'1970-01-01T00:00:21.000Z');
   await reminder.locator('summary').click();assert.match(await reminder.locator('.markdown').textContent(),/skill_view/);
   for(const width of [320,390,768]){await page.setViewportSize({width,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);}
   await open();assert.equal(await page.locator('.user-message').count(),1);assert.equal(await page.locator('details.context-compression').filter({hasText:'Your active task list was preserved'}).count(),1);
   if(process.env.HERMES_LAYOUT_PROOF_DIR){await mkdir(process.env.HERMES_LAYOUT_PROOF_DIR,{recursive:true,mode:0o700});await page.setViewportSize({width:390,height:844});await reminder.locator('summary').scrollIntoViewIfNeeded();await page.screenshot({path:join(process.env.HERMES_LAYOUT_PROOF_DIR,'reminder-separated.png')});}
  }else{
  const question=page.locator('.user-message').filter({hasText:'Original timed question'}),answer=page.locator('.assistant-message').filter({hasText:'Recorded timed answer'}),processCard=page.locator('.context-compression');
  assert.equal(await question.locator('time').count(),1,'native user timestamp must be shown');
  assert.equal(await answer.locator('time').count(),1,'native assistant timestamp must be shown');
  assert.equal(await processCard.locator('summary time').count(),1,'folded system/process timestamp must be shown');
  assert.equal(await question.locator('time').getAttribute('datetime'),'2025-11-02T05:30:00.000Z');
  assert.equal(await answer.locator('time').getAttribute('datetime'),'2025-11-02T06:30:00.000Z');
  assert.match(await question.locator('time').textContent(),/1:30/);assert.match(await answer.locator('time').textContent(),/1:30/);
  assert.notEqual(await question.locator('time').getAttribute('title'),await answer.locator('time').getAttribute('title'),'DST repeated local hour carries distinct full timezone evidence');
  assert.equal(await processCard.locator('summary time').getAttribute('datetime'),'2025-11-02T06:31:00.000Z');
  assert.equal(await processCard.evaluate(el=>el.open),false);assert.equal(await processCard.locator('.message-author').count(),0);
  for(const text of ['Legacy question without a recorded time','Legacy answer without a recorded time'])assert.equal(await page.locator('.user-message,.assistant-message').filter({hasText:text}).locator('time').count(),0,'missing times must not be invented');
  assert.doesNotMatch(await page.locator('.messages').textContent(),/PRIVATE SYSTEM PROMPT/);
  const stamps=await page.locator('.messages time').evaluateAll(elements=>elements.map(el=>[el.dateTime,el.textContent,el.title]));
  for(const width of [320,390,768]){await page.setViewportSize({width,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);for(const card of [question,answer,processCard])assert.equal(await card.locator('time').evaluate(el=>{const a=el.getBoundingClientRect();return a.width>0 && a.right<=innerWidth && a.left>=0;}),true);}
  await open();assert.deepEqual(await page.locator('.messages time').evaluateAll(elements=>elements.map(el=>[el.dateTime,el.textContent,el.title])),stamps,'reload preserves recorded display times');
  if(process.env.HERMES_LAYOUT_PROOF_DIR){await mkdir(process.env.HERMES_LAYOUT_PROOF_DIR,{recursive:true,mode:0o700});await page.setViewportSize({width:390,height:844});await processCard.locator('summary').scrollIntoViewIfNeeded();await page.screenshot({path:join(process.env.HERMES_LAYOUT_PROOF_DIR,'message-times.png')});}
  }
  for(const width of [320,390,768]){
   await page.setViewportSize({width,height:844});
   for(const caption of await page.locator('.process-history > summary > .caption').all()){
    assert.equal(await caption.evaluate(el=>{const range=document.createRange();range.selectNodeContents(el);return range.getClientRects().length;}),1,'Completed status stays intact on one line');
   }
  }
  assert.deepEqual(withoutValidatedPresence(requests).filter(call=>call.method!=='GET'),[]);assert.deepEqual(errors,[]);
 }finally{await browser?.close();if(server){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}await rm(temporary,{recursive:true,force:true});}
});
