import {generatedAssets} from './generated-assets.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdtemp,rm,mkdir,copyFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createRequire} from 'node:module';

const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');

async function fixture(){
 const temporary=await mkdtemp(join(tmpdir(),'root-design-'));let server,browser;
 const state={authenticated:true,role:'owner',status:'ready',calls:[],errors:[]};
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
    if(endpoint==='/auth/me')data={user:{id:`synthetic-${state.role}`,role:state.role,status:state.status},csrf_token:'fixture-csrf'};
    else if(endpoint==='/sessions')data={deletion_available:true,items:[{id:'s1',title:'Synthetic root chat',run_status:'idle',source:'web'}],total:1};
    else if(endpoint==='/sessions/s1/messages')data={items:[{role:'user',content:'Synthetic question. [Example](https://example.com)'},{role:'assistant',content:'Synthetic answer.'}],run:null};
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
  const context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,reducedMotion:'reduce',serviceWorkers:'block'});
  const page=await context.newPage();page.setDefaultTimeout(7000);page.on('pageerror',error=>state.errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);
  await page.getByRole('button',{name:'Synthetic root chat',exact:true}).waitFor();
  return {page,state,close};
 }catch(error){await close().catch(()=>{});throw error;}
}

async function screenshot(page,name){
 const artifacts=process.env.HERMES_TEST_ARTIFACT_DIR;
 assert.ok(artifacts,'Managed browser artifact directory is required');
 await mkdir(artifacts,{recursive:true,mode:0o700});
 const path=join(artifacts,name+'.png');
 await page.screenshot({path});
 if(process.env.HERMES_DESIGN_PROOF_DIR){
  await mkdir(process.env.HERMES_DESIGN_PROOF_DIR,{recursive:true,mode:0o700});
  await copyFile(path,join(process.env.HERMES_DESIGN_PROOF_DIR,name+'.png'));
 }
}

function contrast(foreground,background){
 const luminance=value=>{const rgb=value.match(/[\d.]+/g).slice(0,3).map(Number).map(v=>v/255).map(v=>v<=0.04045?v/12.92:((v+0.055)/1.055)**2.4);return rgb.reduce((sum,v,i)=>sum+v*[0.2126,0.7152,0.0722][i],0);};
 const values=[luminance(foreground),luminance(background)].sort((a,b)=>a-b);
 return (values[1]+0.05)/(values[0]+0.05);
}

test('selected navigation and filters meet normal-text contrast in both themes', {timeout:45000}, async()=>{
 const h=await fixture(),{page}=h;
 try{
  await page.getByRole('button',{name:'Filter conversations',exact:true}).click();
  await page.locator('.filter-segment[data-kind=chats]').click();
  await page.getByRole('button',{name:'Filter conversations',exact:true}).click();
  for(const width of [390,1280])for(const theme of ['light','dark']){
   await page.setViewportSize({width,height:844});
   await page.evaluate(value=>document.documentElement.dataset.theme=value,theme);
   for(const selector of ['.nav-item[aria-current=page]','.filter-segment[aria-pressed=true]','.filter-toggle[data-active=true]']){
    const target=page.locator(selector);await target.waitFor({state:'visible'});
    const colors=await target.evaluate(node=>{const style=getComputedStyle(node);return [style.color,style.backgroundColor];});
    assert.ok(contrast(...colors)>=4.5,`${selector} ${width}px ${theme} normal-text contrast ${contrast(...colors).toFixed(3)} is below 4.5:1`);
   }
  }
 }finally{await h.close();}
});

test('user message links retain readable contrast in both themes', {timeout:45000}, async()=>{
 const h=await fixture(),{page}=h;
 try{
  await page.getByRole('button',{name:'Synthetic root chat',exact:true}).click();
  const link=page.locator('.user-message a');await link.waitFor({state:'visible'});
  for(const theme of ['light','dark']){
   await page.evaluate(value=>document.documentElement.dataset.theme=value,theme);
   const colors=await link.evaluate(node=>[getComputedStyle(node).color,getComputedStyle(node.closest('.user-message')).backgroundColor]);
   assert.ok(contrast(...colors)>=4.5,`user message link ${theme} contrast ${contrast(...colors).toFixed(3)} is below 4.5:1`);
  }
 }finally{await h.close();}
});

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
   assert.equal(await page.locator('meta[name=theme-color]').getAttribute('content'),'#f4f3ee');
    const fonts=await page.evaluate(()=>[
     getComputedStyle(document.body).fontFamily,
     getComputedStyle(document.querySelector('.page-heading h1')).fontFamily
    ]);
    assert.match(fonts[0],/Arial/);
    assert.match(fonts[1],/Georgia/);
   }else{
    assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).colorScheme),'dark');
   }
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${width}px ${theme} Sessions has no horizontal overflow`);
   for(const name of ['Search conversations','Filter conversations','New chat']){
    const target=page.getByRole('button',{name,exact:true}),box=await target.boundingBox();
    assert.ok(box.width>=44&&box.height>=44,`${width}px ${theme} ${name} remains reachable`);
   }
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
     const finance=page.locator('.account-finance-link');
     assert.equal(await finance.getAttribute('href'),'/hermes/finance/');
     assert.ok((await finance.boundingBox()).height>=44);
    }
    await screenshot(page,`${label.toLowerCase()}-${width}-${theme}`);
   }
   const chats=page.getByRole('button',{name:'Chats',exact:true});
   if(width<760)await chats.tap();else await chats.click();
   const row=page.getByRole('button',{name:'Synthetic root chat',exact:true});
   if(width<760)await row.tap();else await row.click();
   await page.getByRole('combobox',{name:'Model',exact:true}).waitFor();
   const draft=page.getByRole('textbox',{name:'Message Hermes'});
   assert.equal(await draft.isVisible(),true);
   assert.equal(await page.getByRole('button',{name:'Send message',exact:true}).isVisible(),true);
   const expand=page.getByRole('button',{name:'Expand editor',exact:true});
   assert.equal(await expand.isVisible(),true);
   await draft.fill('Synthetic draft stays in the composer.');
   await expand.click();
   assert.equal(await page.getByRole('dialog',{name:'Message editor'}).isVisible(),true);
   assert.equal(await draft.inputValue(),'Synthetic draft stays in the composer.');
   await page.getByRole('button',{name:'Done',exact:true}).click();
   assert.equal(await draft.inputValue(),'Synthetic draft stays in the composer.');
   assert.match(await page.locator('.messages').innerText(),/Synthetic answer/);
   assert.doesNotMatch(await page.locator('.conversation-head h1').evaluate(element=>getComputedStyle(element).fontFamily),/Georgia/);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${width}px ${theme} active chat has no horizontal overflow`);
   await screenshot(page,`chat-${width}-${theme}`);
   await page.getByRole('button',{name:'Back to chats',exact:true}).click();
   await page.getByRole('heading',{name:'Sessions',exact:true}).waitFor();
  }
  await page.emulateMedia({colorScheme:'dark'});
  await page.evaluate(()=>document.documentElement.dataset.theme='system');
  assert.deepEqual(await page.evaluate(()=>{
   const style=getComputedStyle(document.documentElement);
   return [style.colorScheme,style.getPropertyValue('--bg').trim(),style.getPropertyValue('--accent').trim()];
  }),['dark','#202824','#8fbea4']);
  await page.emulateMedia({colorScheme:'light'});
  state.authenticated=false;
  await page.setViewportSize({width:390,height:844});
  await page.reload();
  await page.evaluate(()=>document.documentElement.dataset.theme='light');
  await page.getByRole('button',{name:'Use an invitation',exact:true}).waitFor();
  assert.equal(await page.getByRole('heading',{name:'Sign in',exact:true}).isVisible(),true);
  await screenshot(page,'signin-390-light');
  await page.getByRole('button',{name:'Use an invitation',exact:true}).click();
  assert.equal(await page.getByRole('heading',{name:'Create account',exact:true}).isVisible(),true);
  assert.match(await page.locator('.auth-card').innerText(),/separate account/);
  await screenshot(page,'enrollment-390-light');
  state.authenticated=true;state.role='owner';state.status='pending';state.calls.length=0;
  await page.reload();
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  await page.locator('[data-section=devices]').waitFor();
  assert.equal(await page.locator('.account-finance-link').count(),0,'pending owners do not get the ready-only link');
  state.role='member';state.status='ready';state.calls.length=0;
  await page.reload();
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  await page.locator('[data-section=devices]').waitFor();
  assert.equal(await page.locator('.account-finance-link').count(),0,'Finance is only offered to a ready owner');
  assert.equal(state.calls.some(call=>['/members','/invites'].includes(call.endpoint)),false,'member routes do not load owner-only settings');
  assert.deepEqual(state.errors,[]);
 }finally{await h.close();}
});
