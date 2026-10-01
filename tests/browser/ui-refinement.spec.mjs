import {generatedAssets} from './generated-assets.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {withoutValidatedPresence} from './push-fixture-contract.mjs';
import {createServer} from 'node:http';
import {readFile,mkdtemp,rm,mkdir} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const repo=fileURLToPath(new URL('../../',import.meta.url));
const receipt=id=>({id,session_id:'s1',kind:'background_result',source_event_id:'event-'+id,title:'Completed background task',body:'**Full child report**\n\n'+Array.from({length:30},(_,i)=>`Report line ${i}: preserved safe content.`).join('\n\n'),created_at:1700000000,agent_context_state:'not_injected'});
async function fixture({longList=false}={}){
 const tmp=await mkdtemp((process.env.TMPDIR || '/tmp')+'/b-');let server,browser;
 const close=async()=>{try{await browser?.close();}finally{if(server){server.closeAllConnections();await new Promise(r=>server.close(r));}await rm(tmp,{recursive:true,force:true});}};
 try{
 const dir=generatedAssets(tmp);
 assert.match(await readFile(dir+'/index.html','utf8'),/app\.[a-f0-9]+\.js/);
 const state={items:[receipt('bg1')],calls:[],errors:[]};
 server=createServer(async(req,res)=>{
  const url=new URL(req.url,'http://fixture'),p=url.pathname;
  if(p.startsWith('/hermes/app-api/')){
   let raw='';for await(const chunk of req)raw+=chunk;
   const path=p.slice('/hermes/app-api'.length);state.calls.push({path,query:url.search,method:req.method,body:raw?JSON.parse(raw):null,csrf:req.headers['x-csrf-token']});let data={items:[]};
   if(path==='/auth/me')data={user:{id:'owner',status:'ready'},csrf_token:'fixture'};
   if(path==='/sessions')data={deletion_available:true,items:[{id:'s1',title:'First fixture chat',run_status:'idle',source:'cli'},{id:'s2',title:'Second fixture chat',run_status:'idle',source:'web'}],total:2};
   if(path==='/sessions' && longList)data.items.push(...Array.from({length:28},(_,i)=>({id:'extra-'+i,title:'Extra chat '+i,run_status:'idle',source:'cli'})));
   if(path.endsWith('/model-options'))data={available:true,models:[{id:'fixture-long-model',provider:'fixture',label:'A deliberately long model label for compact controls',reasoning_efforts:[]}]};
   if(path.endsWith('/messages'))data={items:[...Array.from({length:18},(_,i)=>({role:i%2?'assistant':'user',content:`Earlier fixture turn ${i}.`})),{role:'tool',kind:'context_compression',name:'Context compression',status:'completed',content:'Retained native envelope.\n\n**Full summary**\n\n<img src=x onerror=alert(1)>\n\n'+ 'Safe summary paragraph.\n\n'.repeat(15)}],run:null};
   if(path.endsWith('/background'))data={items:state.items};
   res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify(data));return;
  }
  const relative=p.replace(/^\/hermes\//,'') || 'index.html';if(relative.includes('..'))return res.writeHead(400).end();
  try{res.writeHead(200,{'Content-Type':({html:'text/html',js:'text/javascript',mjs:'text/javascript',css:'text/css',svg:'image/svg+xml',webmanifest:'application/manifest+json'})[relative.split('.').pop()]||'application/octet-stream'}).end(await readFile(dir+'/'+relative));}catch{res.writeHead(404).end();}
 });await new Promise(r=>server.listen(0,'127.0.0.1',r));
 browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage'],env:{...process.env,TMPDIR:tmp}});
 const page=await browser.newPage({viewport:{width:390,height:844},hasTouch:true,serviceWorkers:'block',reducedMotion:'reduce'});page.setDefaultTimeout(5000);page.on('pageerror',e=>state.errors.push(e.message));
 await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);await page.locator('.session-entry').first().waitFor();
 return {page,state,close,refresh:async()=>{const response=page.waitForResponse(r=>r.url().endsWith('/background'));await page.evaluate(()=>dispatchEvent(new Event('focus')));await response;await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));}};
 }catch(e){await close();throw e;}
}
async function drag(page,locator,dx,dy=0){const b=await locator.boundingBox();await page.mouse.move(b.x+b.width*.8,b.y+b.height/2);await page.mouse.down();await page.mouse.move(b.x+b.width*.8+dx,b.y+b.height/2+dy,{steps:6});await page.mouse.up();}
const frames=page=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
test('generated toolbar, swipe and folded process layout at 320/390/768/1280 in both themes',{timeout:60000},async()=>{
 const h=await fixture(),{page,state}=h,proof=process.env.HERMES_TEST_ARTIFACT_DIR || process.env.HERMES_REFINEMENT_PROOF || '/tmp/hermes-refinement-proof';await mkdir(proof,{recursive:true,mode:0o700});
 try{
 for(const width of [320,390,768,1280])for(const theme of ['light','dark']){
  await page.setViewportSize({width,height:844});await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
  const targets=await Promise.all(['New chat','Filter conversations','Search conversations'].map(name=>page.getByRole('button',{name,exact:true}).boundingBox()));
  assert.ok(targets.every(b=>b.width>=44&&b.height>=44));assert.ok(targets.every(b=>Math.abs(b.y-targets[0].y)<1),'three actions on one row');assert.ok(targets.every(b=>b.x>=0&&b.x+b.width<=width),'toolbar stays inside viewport');
  const heading=await page.locator('.conversation-list-heading').boundingBox();assert.ok(heading.height<=100,'compact heading plus one action row, no permanent segments');
  assert.equal(await page.locator('.session-delete').first().isVisible(),false);await page.screenshot({path:`${proof}/list-closed-${width}-${theme}.png`});
  const actions=page.getByRole('button',{name:'Conversation actions: First fixture chat',exact:true});assert.ok((await actions.boundingBox()).width<=1,'no permanently visible per-row More clutter: '+JSON.stringify(await actions.evaluate(e=>({box:e.getBoundingClientRect().toJSON(),focus:e===document.activeElement,width:getComputedStyle(e).width,border:getComputedStyle(e).borderWidth,padding:getComputedStyle(e).padding}))));await actions.focus();let ab=await actions.boundingBox();assert.ok(ab.width>=44&&ab.height>=44,'focused keyboard and screen-reader action is a full target');await page.keyboard.press('Enter');assert.equal(await page.locator('[data-delete-id=s1]').isVisible(),true);await page.keyboard.press('Escape');await page.getByRole('heading',{name:'Sessions'}).click();
  const filter=page.getByRole('button',{name:'Filter conversations',exact:true});await filter.click();const menu=page.getByRole('group',{name:'Conversation filter'});assert.equal(await menu.isVisible(),true);const mb=await menu.boundingBox();assert.ok(mb.x>=0&&mb.x+mb.width<=width);for(const b of await menu.getByRole('button').all()){const box=await b.boundingBox();assert.ok(box.width>=44&&box.height>=44);}await page.screenshot({path:`${proof}/filter-${width}-${theme}.png`});await page.keyboard.press('Escape');assert.equal(await filter.evaluate(e=>e===document.activeElement),true);
  const row=page.getByRole('button',{name:'First fixture chat',exact:true});assert.equal(await row.evaluate(e=>getComputedStyle(e).touchAction),'pan-y');await drag(page,row,-70);
  const remove=page.getByRole('button',{name:'Delete conversation: First fixture chat',exact:true});assert.equal(await remove.isVisible(),true,'left swipe exposes Delete without opening chat');assert.equal(await page.locator('[role=dialog]').count(),0);assert.equal(await page.locator('.messages').count(),0);
  const rb=await remove.boundingBox();assert.ok(rb.width>=44&&rb.height>=44&&rb.x+rb.width<=width);assert.equal(await remove.evaluate(e=>getComputedStyle(e).backgroundColor),'rgb(168, 47, 52)');
  await page.screenshot({path:`${proof}/list-${width}-${theme}.png`});await page.keyboard.press('Escape');assert.equal(await remove.isVisible(),false);
  await row.focus();await page.keyboard.press('Shift+F10');await remove.click();await page.getByRole('dialog').waitFor();await page.getByRole('button',{name:'Cancel',exact:true}).click();assert.equal(await remove.isVisible(),true);await page.keyboard.press('Escape');
  await row.click();await page.locator('[data-background-id=bg1]').waitFor();
  const bg=page.locator('[data-background-id=bg1]'),process=page.locator('.context-compression');assert.equal(await bg.evaluate(e=>e.open),false);assert.equal(await process.evaluate(e=>e.open),false);assert.equal(await bg.locator('.markdown').isVisible(),false);assert.equal(await process.locator('.markdown').isVisible(),false);
  for(const summary of [bg.locator('summary'),process.locator('summary')]){const b=await summary.boundingBox();assert.ok(b.height>=44&&b.height<=68,'compact accessible process header');}
  const model=page.getByRole('combobox',{name:'Model',exact:true});await model.waitFor();const b=await model.boundingBox();assert.ok(b.width>=44&&b.height>=44&&b.height<=48&&b.x+b.width<=width);assert.equal(await model.evaluate(e=>getComputedStyle(e).appearance),'auto');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.locator('.messages').evaluate(e=>e.scrollTop=e.scrollHeight);await page.screenshot({path:`${proof}/process-${width}-${theme}.png`});
  await page.getByRole('button',{name:'Back to chats'}).click();
 }
 assert.equal(withoutValidatedPresence(state.calls).some(c=>c.method!=='GET'),false);assert.deepEqual(state.errors,[]);
 }finally{await h.close();}
});
test('Chromium touch swipe reveals only, cancellation does not reveal, and vertical pan scrolls naturally',{timeout:30000},async()=>{
 const h=await fixture({longList:true}),{page,state}=h;try{
 const cdp=await page.context().newCDPSession(page),row=page.getByRole('button',{name:'First fixture chat',exact:true}),remove=page.locator('[data-delete-id=s1]');
 const gesture=async(dx,dy=0,cancel=false)=>{const b=await row.boundingBox(),x=b.x+b.width*.75,y=b.y+b.height/2;await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});for(let i=1;i<=6;i++){await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x+dx*i/6,y:y+dy*i/6}]});}await cdp.send('Input.dispatchTouchEvent',{type:cancel?'touchCancel':'touchEnd',touchPoints:[]});await frames(page);};
 await page.evaluate(()=>{window.gestureTrace=[];for(const type of ['pointerdown','pointermove','pointerup','pointercancel','lostpointercapture','gotpointercapture'])document.addEventListener(type,e=>gestureTrace.push({type,target:e.target.tagName,id:e.pointerId,x:e.clientX,y:e.clientY,button:e.button,primary:e.isPrimary}),true);});
 await gesture(-110,0,true);assert.equal(await remove.isVisible(),false);assert.equal(await page.locator('.messages').count(),0);
 await gesture(-110);assert.equal(await remove.isVisible(),true,JSON.stringify(await page.evaluate(()=>gestureTrace)));assert.equal(await page.locator('[role=dialog],.messages').count(),0);await gesture(70);assert.equal(await remove.isVisible(),false);
 const second=page.getByRole('button',{name:'Second fixture chat',exact:true});await row.click({button:'right'});assert.equal(await remove.isVisible(),true);await second.press('Shift+F10');assert.equal(await remove.isVisible(),false);assert.equal(await page.locator('[data-delete-id=s2]').isVisible(),true);await page.getByRole('heading',{name:'Sessions'}).click();assert.equal(await page.locator('.session-delete:not([hidden])').count(),0);
 // Start on a middle row so the vertical drag remains within the viewport.
 const b=await page.getByRole('button',{name:'Extra chat 2',exact:true}).boundingBox(),x=b.x+b.width/2,y=b.y+b.height/2;const before=await page.locator('#main').evaluate(e=>e.scrollTop);
 await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});for(let i=1;i<=6;i++)await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x-5*i,y:y-25*i}]});await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await frames(page);
 assert.ok(await page.locator('#main').evaluate(e=>e.scrollTop)>before,'browser keeps vertical list scrolling');assert.equal(await page.locator('.session-delete:not([hidden])').count(),0);assert.equal(await page.locator('.messages,[role=dialog]').count(),0);assert.equal(withoutValidatedPresence(state.calls).some(c=>c.method!=='GET'),false);assert.deepEqual(state.errors,[]);
 }finally{await h.close();}
});
test('generated disclosure refresh keeps open DOM, selected text, and the reader instead of false near-bottom following',{timeout:30000},async()=>{
 const h=await fixture(),{page,state}=h;try{
 await page.getByRole('button',{name:'First fixture chat',exact:true}).click();const bg=page.locator('[data-background-id=bg1]');await bg.waitFor();await bg.locator('summary').click();assert.equal(await bg.evaluate(e=>e.open),true);await frames(page);
 await page.evaluate(()=>{window.savedCard=document.querySelector('[data-background-id]');window.savedBody=savedCard.querySelector('.markdown');window.savedEditor=document.querySelector('textarea');const m=document.querySelector('.messages');m.scrollTop=m.scrollHeight-m.clientHeight-50;});
 const before=await page.locator('.messages').evaluate(e=>e.scrollTop);state.items.push(receipt('bg2'));await h.refresh();await page.locator('[data-background-id=bg2]').waitFor();assert.equal(await page.locator('.messages').evaluate(e=>e.scrollTop),before,'reader 50px above bottom must not be yanked down');assert.deepEqual(await page.evaluate(()=>({card:savedCard===document.querySelector('[data-background-id]'),body:savedBody===savedCard.querySelector('.markdown'),editor:savedEditor===document.querySelector('textarea'),open:savedCard.open})),{card:true,body:true,editor:true,open:true});
 await page.evaluate(()=>{const range=document.createRange();range.selectNodeContents(savedBody.querySelector('strong'));getSelection().removeAllRanges();getSelection().addRange(range);});const selected=await page.evaluate(()=>getSelection().toString()),selectedTop=await page.locator('.messages').evaluate(e=>e.scrollTop);assert.equal(selected,'Full child report');state.items.push(receipt('bg3'));await h.refresh();assert.equal(await page.evaluate(()=>getSelection().toString()),selected);assert.equal(await page.locator('.messages').evaluate(e=>e.scrollTop),selectedTop);await page.evaluate(()=>getSelection().removeAllRanges());
 await bg.locator('summary').click();await frames(page);const afterFold=await page.locator('.messages').evaluate(e=>e.scrollTop);await h.refresh();assert.equal(await page.locator('.messages').evaluate(e=>e.scrollTop),afterFold,'unchanged refresh must not move folded reader');assert.equal(await bg.evaluate(e=>e.open),false);
 const process=page.locator('.context-compression');await process.locator('summary').click();assert.equal(await process.locator('.markdown').isVisible(),true);assert.equal(await process.locator('img,script').count(),0);assert.match(await process.locator('.markdown').textContent(),/Retained native envelope/);assert.equal(await process.locator('.message-author').count(),0);
 assert.deepEqual(withoutValidatedPresence(state.calls).filter(c=>c.method!=='GET'),[]);assert.deepEqual(state.errors,[]);
 }finally{await h.close();}
});
