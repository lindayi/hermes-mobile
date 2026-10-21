import {generatedAssets} from './generated-assets.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdtemp,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createRequire} from 'node:module';

const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const proof='/tmp/hermes-issue128-review';

async function fixture(){
 const temporary=await mkdtemp(join(tmpdir(),'root-design-'));let server,browser;
 const state={authenticated:true,role:'owner',calls:[],errors:[]};
 const close=async()=>{
  try{await browser?.close();}finally{
   if(server){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
   await rm(temporary,{recursive:true,force:true});
  }
 };
 try{
  const dir=generatedAssets(temporary);
  server=createServer(async(req,res)=>{
   const path=new URL(req.url,'http://fixture').pathname;
   if(path.startsWith('/hermes/app-api/')){
    const endpoint=path.slice('/hermes/app-api'.length);let raw='';
    for await(const chunk of req)raw+=chunk;
    state.calls.push({endpoint,method:req.method});
    if(endpoint==='/auth/me' && !state.authenticated){
     res.writeHead(401,{'Content-Type':'application/json'}).end('{"detail":"Synthetic sign-in required"}');return;
    }
    let data={items:[]};
    if(endpoint==='/auth/me')data={user:{id:'synthetic-owner',role:state.role,status:'ready'},csrf_token:'fixture-csrf'};
    else if(endpoint==='/sessions')data={deletion_available:true,items:[{id:'s1',title:'Synthetic root chat',run_status:'idle',source:'web'}],total:1};
    else if(endpoint==='/sessions/s1/messages')data={items:[{role:'user',content:'Synthetic question.'},{role:'assistant',content:'Synthetic answer.'}],run:null};
    else if(endpoint==='/sessions/s1/model-options')data={available:true,models:[{id:'fixture-model',provider:'fixture',label:'Synthetic model',reasoning_efforts:[]}]};
    else if(endpoint==='/push/preferences')data={revision:0,enabled:false,categories:{completion:true,approval:true,attention:true,scheduled:true,operational:true},hide_details:false};
    else if(endpoint==='/jobs')data={items:[]};
    else if(endpoint==='/inbox')data={items:[],read_count:0};
    else if(endpoint==='/approvals')data={items:[]};
    res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify(data));return;
   }
   const relative=path.replace(/^\/hermes\//,'')||'index.html';
   if(relative.includes('..')){res.writeHead(400).end();return;}
   try{
    const body=await readFile(join(dir,relative));
    res.writeHead(200,{'Content-Type':({html:'text/html',js:'text/javascript',mjs:'text/javascript',css:'text/css',svg:'image/svg+xml',webmanifest:'application/manifest+json'})[relative.split('.').pop()]||'application/octet-stream'}).end(body);
   }catch{res.writeHead(404).end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage'],env:{...process.env,TMPDIR:temporary}});
  const context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,serviceWorkers:'block'});
  const page=await context.newPage();page.setDefaultTimeout(7000);page.on('pageerror',error=>state.errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);
  await page.getByRole('button',{name:'Synthetic root chat',exact:true}).waitFor();
  return {page,state,close};
 }catch(error){await close().catch(()=>{});throw error;}
}

async function screenshot(page,name){
 await mkdir(proof,{recursive:true,mode:0o700});
 await page.screenshot({path:join(proof,name+'.png')});
}

test('generated root routes share the calm design and retain their controls', {timeout:90000}, async()=>{
 const h=await fixture(),{page,state}=h;
 try{
  for(const width of [390,1280])for(const theme of ['light','dark']){
   await page.setViewportSize({width,height:844});
   await page.evaluate(value=>document.documentElement.dataset.theme=value,theme);
   if(theme==='light'){
    assert.deepEqual(await page.evaluate(()=>{
     const style=getComputedStyle(document.documentElement);
     return ['--bg','--surface','--text','--muted','--border','--accent'].map(name=>style.getPropertyValue(name).trim());
    }),['#f4f3ee','#fffefa','#22332f','#616d67','#d7dcd3','#327b65']);
   }else{
    assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).colorScheme),'dark');
   }
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${width}px ${theme} Sessions has no horizontal overflow`);
   await screenshot(page,`sessions-${width}-${theme}`);
   for(const [label,heading] of [['Inbox','Inbox'],['Jobs','Scheduled jobs'],['Settings','Settings']]){
    const navigation=page.getByRole('button',{name:label,exact:true});
    if(width<760)await navigation.tap();else await navigation.click();
    await page.getByRole('heading',{name:heading,exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${width}px ${theme} ${heading} has no horizontal overflow`);
    if(label==='Inbox')assert.equal(await page.getByRole('button',{name:'Mark all read',exact:true}).isVisible(),true);
    if(label==='Jobs')assert.equal(await page.getByRole('button',{name:'New job',exact:true}).isVisible(),true);
    if(label==='Settings'){
     await page.locator('[data-section=devices]').waitFor();
     assert.equal(await page.locator('.notification-settings').count(),1);
     assert.equal(await page.locator('.push-preferences').isVisible(),false);
     assert.equal(await page.locator('.account-finance-link').getAttribute('href'),'/hermes/finance/');
    }
    await screenshot(page,`${label.toLowerCase()}-${width}-${theme}`);
   }
   const chats=page.getByRole('button',{name:'Chats',exact:true});
   if(width<760)await chats.tap();else await chats.click();
   const row=page.getByRole('button',{name:'Synthetic root chat',exact:true});
   if(width<760)await row.tap();else await row.click();
   await page.getByRole('combobox',{name:'Model',exact:true}).waitFor();
   assert.equal(await page.getByRole('textbox',{name:'Message Hermes'}).isVisible(),true);
   assert.equal(await page.getByRole('button',{name:'Send message',exact:true}).isVisible(),true);
   assert.equal(await page.getByRole('button',{name:'Expand editor',exact:true}).isVisible(),true);
   assert.match(await page.locator('.messages').innerText(),/Synthetic answer/);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${width}px ${theme} active chat has no horizontal overflow`);
   await screenshot(page,`chat-${width}-${theme}`);
  }
  state.authenticated=false;
  await page.reload();
  await page.getByRole('button',{name:'Use an invitation',exact:true}).waitFor();
  assert.equal(await page.getByRole('heading',{name:'Sign in',exact:true}).isVisible(),true);
  await page.getByRole('button',{name:'Use an invitation',exact:true}).click();
  assert.equal(await page.getByRole('heading',{name:'Create account',exact:true}).isVisible(),true);
  assert.match(await page.locator('.auth-card').innerText(),/separate account/);
  await screenshot(page,'enrollment-390-light');
  state.authenticated=true;state.role='member';
  await page.reload();
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  await page.locator('[data-section=devices]').waitFor();
  assert.equal(await page.locator('.account-finance-link').count(),0,'Finance is only offered to a ready owner');
  assert.equal(state.calls.some(call=>['/members','/invites'].includes(call.endpoint)),false,'member routes do not load owner-only settings');
  assert.deepEqual(state.errors,[]);
 }finally{await h.close();}
});
