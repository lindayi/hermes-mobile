import {artifactURL} from './artifacts.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const dir=process.env.HERMES_FRONTEND_DIR ? process.env.HERMES_FRONTEND_DIR.replace(/\/$/,'')+'/' : fileURLToPath(new URL('../../frontend/',import.meta.url));

test('compact chat header preserves space, title editing, draft and connection state in real Chromium',async()=>{
 let title='A deliberately long conversation title to check narrow-screen truncation without losing controls';let patches=0;
 const server=createServer(async(req,res)=>{
  const url=new URL(req.url,'http://localhost'),p=url.pathname;
  if(p.startsWith('/hermes/app-api/')){
   let body={};if(req.method==='PATCH'){let text='';for await(const part of req)text+=part;body=JSON.parse(text);}
   let data;
   if(p.endsWith('/auth/me'))data={user:{id:'header-fixture',role:'owner',status:'ready'},csrf_token:'fixture'};
   else if(p.endsWith('/sessions/header-session') && req.method==='PATCH'){patches++;title=body.title;data={id:'header-session',title};}
   else if(p.endsWith('/messages'))data={items:[{role:'assistant',content:'This is isolated fixture content, not a live model result.'}],offset:0,total:1,run:null,last_run:null};
   else if(p.endsWith('/sessions'))data={items:[{id:'header-session',title,source:'cli',updated_at:1700000000}],total:1};
   else if(p.endsWith('/health'))data={status:'ok',agent_execution:true};
   else data={items:[]};
   res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(data));return;
  }
  const relative=p.replace(/^\/hermes\//,'')||'index.html';
  if(relative.includes('..')){res.writeHead(400).end();return;}
  try{const body=await readFile(dir+relative),ext=relative.split('.').pop();res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',js:'text/javascript',mjs:'text/javascript',svg:'image/svg+xml',webmanifest:'application/manifest+json'})[ext]||'application/octet-stream'});res.end(body);}catch{res.writeHead(404).end();}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);
  await page.getByRole('button',{name:title,exact:true}).click();await page.getByRole('textbox',{name:'Message Hermes'}).waitFor();
  assert.equal(await page.locator('.app-shell .brand').count(),0,'no decorative authenticated branding row');
  const header=page.locator('.topbar'),back=header.getByRole('button',{name:'Back to chats'}),edit=header.getByRole('button',{name:/Rename/});
  await edit.waitFor();
  await mkdir(artifactURL(),{recursive:true});
  for(const theme of ['light','dark'])for(const viewport of [{width:320,height:568},{width:390,height:844},{width:844,height:390},{width:1280,height:900}]){
   await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
   await page.setViewportSize(viewport);const box=await header.boundingBox(),b=await back.boundingBox(),e=await edit.boundingBox(),main=await page.locator('#main').boundingBox();
   assert.ok(box.height<=60,`single compact header at ${viewport.width}: ${box.height}`);
   assert.ok(box.y>=0 && box.y<=4,`header is at top, not an implicit grid row: ${box.y}`);
   assert.ok(Math.abs(box.x-main.x)<1 && Math.abs(box.width-main.width)<1,`edge-to-edge header at ${viewport.width}: ${JSON.stringify({box,main})}`);
   if(viewport.width<760)assert.equal(box.width,viewport.width,'mobile header reaches viewport edges');
   const paint=await header.evaluate(el=>({background:getComputedStyle(el).backgroundColor,border:getComputedStyle(el).borderBottomWidth}));assert.notEqual(paint.background,'rgba(0, 0, 0, 0)');assert.equal(paint.border,'1px');
   assert.ok(b.x>=box.x+8 && e.x+e.width<=box.x+box.width-8,'header controls keep inner padding');
   assert.ok(b.width>=44&&b.height>=44&&e.width>=44&&e.height>=44,'touchable back/edit');
   assert.ok(b.x<e.x,'back stays left of edit');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'no horizontal overflow');
   const messages=await page.locator('.messages').boundingBox();assert.ok(messages.y>=box.y+box.height-1 && messages.y<=box.y+box.height+12,'messages start directly below header');
   const send=await page.getByRole('button',{name:'Send message'}).boundingBox();assert.ok(send.y>=messages.y+messages.height-1 && send.y+send.height<=viewport.height,'composer stays below messages and inside viewport');
   for(const label of ['Send message','Expand editor','Copy message']){const control=page.getByRole('button',{name:label,exact:true});assert.equal(await control.locator('svg[aria-hidden=true][stroke="currentColor"]').count(),1,label+' monochrome SVG');const bounds=await control.boundingBox();assert.ok(bounds.width>=44 && bounds.height>=44,label+' touch target');}
   assert.equal(await page.locator('.technical-strip').isVisible(),false,'empty telemetry has no strip');
   await page.screenshot({path:fileURLToPath(artifactURL(`restrained-header-${viewport.width}-${theme}.png`))});
  }
  await page.setViewportSize({width:390,height:844});await page.getByRole('textbox',{name:'Message Hermes'}).fill('Keep this unsent draft');
  await edit.click();const dialog=page.getByRole('dialog');await dialog.waitFor();await dialog.getByRole('textbox').fill('Renamed fixture');await dialog.getByRole('button',{name:/Save/}).click();await dialog.waitFor({state:'detached'});
  assert.match(await header.innerText(),/Renamed fixture/);assert.equal(patches,1);assert.equal(await page.getByRole('textbox',{name:'Message Hermes'}).inputValue(),'Keep this unsent draft');
  await edit.click();await page.keyboard.press('Escape');assert.equal(await page.getByRole('dialog').count(),0);assert.equal(patches,1);
  await context.setOffline(true);await page.waitForFunction(()=>document.querySelector('.topbar')?.textContent.includes('Disconnected'));
  await context.setOffline(false);await page.waitForFunction(()=>document.querySelector('.topbar')?.textContent.includes('Idle'));
  await mkdir(artifactURL(),{recursive:true});await page.screenshot({path:fileURLToPath(artifactURL('chat-header-mobile.png'))});
  await page.evaluate(()=>document.documentElement.dataset.theme='dark');await page.screenshot({path:fileURLToPath(artifactURL('chat-header-dark.png'))});
  const status=header.getByRole('status'),statusBox=await status.boundingBox(),editBox=await edit.boundingBox();assert.ok(statusBox.x>=editBox.x+editBox.width-1,'status is to the right of title/edit');
  await back.click();await page.getByRole('button',{name:'Renamed fixture',exact:true}).waitFor();await page.reload();await page.getByRole('button',{name:'Renamed fixture',exact:true}).click();
  assert.match(await header.innerText(),/Renamed fixture/);assert.deepEqual(errors,[]);
 }finally{await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
});
