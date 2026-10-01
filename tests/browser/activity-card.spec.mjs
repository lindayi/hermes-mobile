import {artifactURL} from './artifacts.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {withoutValidatedPresence} from './push-fixture-contract.mjs';
import {createServer} from 'node:http';
import {readFile,mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const dir=process.env.HERMES_FRONTEND_DIR?process.env.HERMES_FRONTEND_DIR.replace(/\/$/,'')+'/':fileURLToPath(new URL('../../frontend/',import.meta.url));
async function fixture(check){
 const saved=[{role:'user',content:'Older turn'},...Array.from({length:12},(_,i)=>({role:'assistant',content:`Older paragraph ${i}. `+'Recorded fixture text. '.repeat(15)})),{role:'user',content:'Latest turn'},...Array.from({length:28},(_,i)=>({role:'tool',name:`tool${i}`,status:i===0?'failed':i===26?'running':'success',summary:`Recorded detail ${i}`}))];
 const run={id:'r',session_id:'s',status:'running',input:'Latest turn',output:''},clients=new Set();let seq=0,writes=0;
 const json=(res,obj)=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(obj));};
 const server=createServer(async(req,res)=>{const url=new URL(req.url,'http://fixture'),p=url.pathname.replace('/hermes/app-api','');
  if(req.method==='POST' && url.pathname==='/hermes/app-api/push/presence'){let raw='';for await(const chunk of req)raw+=chunk;withoutValidatedPresence([{p,method:req.method,body:JSON.parse(raw),csrf:req.headers['x-csrf-token']}]);}
  else if(req.method!=='GET')writes++;
  if(url.pathname.startsWith('/hermes/app-api/')){
   if(p==='/auth/me')return json(res,{user:{id:'fixture',status:'ready'},csrf_token:'fixture'});
   if(p==='/sessions')return json(res,{items:[{id:'s',title:'Activity fixture'}],total:1});
   if(p==='/sessions/s/messages')return json(res,{items:saved,run,total:saved.length,offset:0});
   if(p==='/runs/r')return json(res,run);
   if(p==='/runs/r/events'){res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache'});res.write('retry: 1000\n\n');clients.add(res);res.on('close',()=>clients.delete(res));return;}
   return json(res,{items:[]});
  }
  const rel=url.pathname.replace(/^\/hermes\//,'')||'index.html';if(rel.includes('..'))return res.writeHead(400).end();
  try{res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',js:'text/javascript',mjs:'text/javascript',svg:'image/svg+xml'})[rel.split('.').pop()]||'application/octet-stream'});res.end(await readFile(dir+rel));}catch{res.end();}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;
 try{browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});const context=await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block'}),page=await context.newPage(),errors=[];page.setDefaultTimeout(5000);page.on('pageerror',e=>errors.push(e.message));await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);await page.getByRole('button',{name:'Activity fixture',exact:true}).click();await page.locator('.messages .tool-activity:not([hidden])').waitFor();await page.waitForFunction(()=>!!document.querySelector('.live-message'));
  const emit=(name,data)=>{const event=`id: ${++seq}\nevent: ${name}\ndata: ${JSON.stringify(data)}\n\n`;for(const c of clients)c.write(event);};
  await check({page,emit});assert.deepEqual(errors,[]);assert.equal(writes,0,'read-only browser fixture never submits a run or mutation');
 }finally{await browser?.close();for(const c of clients)c.end();server.closeAllConnections();await new Promise(r=>server.close(r));}
}
const settle=page=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
async function latestVisible(card,page){
 const row=await card.locator('.tool-preview').last().boundingBox(),messages=await page.locator('.messages').boundingBox(),composer=await page.locator('.composer').boundingBox(),rows=await card.locator('.tool-rows').boundingBox();
 assert.ok(row.y>=messages.y-1,'latest row begins inside conversation viewport');assert.ok(row.y+row.height<=messages.y+messages.height+1,'latest row ends inside conversation viewport');assert.ok(row.y+row.height<=composer.y,'latest row is above composer');assert.ok(row.y>=rows.y-1 && row.y+row.height<=rows.y+rows.height+1,'latest row visible inside nested tall tool list');
}
test('opening tall cards by pointer/keyboard explicitly reveals latest row from near bottom or older reading position',{timeout:45000},async()=>fixture(async({page,emit})=>{
 const card=page.locator('.messages > .tool-activity'),summary=card.locator(':scope > summary'),messages=page.locator('.messages');
 for(const width of [320,390])for(const position of ['near-bottom','older'])for(const activation of ['pointer','Enter','Space']){
  await page.setViewportSize({width,height:844});await summary.evaluate(el=>el.scrollIntoView({block:'center'}));await settle(page);
  if(position==='older'){await messages.evaluate(el=>el.scrollTop-=110);assert.ok(await messages.evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight)>100,'starts away from bottom');}
  else await messages.evaluate(el=>el.scrollTop=el.scrollHeight);
  await summary.focus();if(activation==='pointer')await summary.click();else await page.keyboard.press(activation);await settle(page);assert.equal(await card.evaluate(el=>el.open),true);await latestVisible(card,page);
  // Put the summary near the viewport bottom; collapse then needs no native range clamp.
  await summary.evaluate(el=>el.scrollIntoView({block:'end'}));await settle(page);const before=await messages.evaluate(el=>el.scrollTop);if(activation==='pointer')await summary.click();else{await summary.focus();await page.keyboard.press(activation);}await settle(page);assert.ok(Math.abs(await messages.evaluate(el=>el.scrollTop)-before)<2,'collapse does not jump');
 }
 await messages.evaluate(el=>{el.scrollTop=0;el.dispatchEvent(new Event('scroll'));});emit('tool',{name:'live1',tool_call_id:'one',status:'running',summary:'Live older-reader guard'});await page.locator('.live-message .tool-preview').waitFor({state:'attached'});await settle(page);assert.equal(await messages.evaluate(el=>el.scrollTop),0,'automatic updates preserve older reader');
 await summary.evaluate(el=>el.scrollIntoView({block:'center'}));await summary.click();await settle(page);await latestVisible(card,page);await mkdir(artifactURL(),{recursive:true});await page.screenshot({path:fileURLToPath(artifactURL('activity-card-tall-mobile.png'))});
}));
const colors={light:{success:'rgb(40, 115, 69)',failed:'rgb(168, 47, 52)',running:'rgb(40, 111, 168)'},dark:{success:'rgb(141, 204, 160)',failed:'rgb(255, 170, 170)',running:'rgb(140, 200, 244)'}};
function contrast(a,b){const luminance=s=>{const c=s.match(/\d+/g).slice(0,3).map(n=>{const v=Number(n)/255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;});return c[0]*.2126+c[1]*.7152+c[2]*.0722;};const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);}
test('semantic tool status colors and symbols match ongoing/latest heading in saved/live latest turn, light and dark',{timeout:45000},async()=>fixture(async({page,emit})=>{
 const saved=page.locator('.messages > .tool-activity'),live=page.locator('.live-message .tool-activity');
 assert.equal(await saved.getAttribute('data-status'),'running');assert.equal(await saved.locator('.activity-preview').textContent(),'tool26: Recorded detail 26');assert.equal(await saved.locator('.tool-preview').count(),28);
 emit('tool',{name:'failedTool',tool_call_id:'f',status:'failed',summary:'Failure fixture'});emit('tool',{name:'ongoingTool',tool_call_id:'o',status:'running',summary:'Ongoing fixture'});emit('tool',{name:'latestTool',tool_call_id:'l',status:'success',summary:'Latest fixture'});await page.waitForFunction(()=>document.querySelectorAll('.live-message .tool-preview').length===3);
 assert.equal(await live.getAttribute('data-status'),'running');assert.equal(await live.locator('.activity-preview').textContent(),'ongoingTool: Ongoing fixture');assert.match(await live.locator('summary').getAttribute('aria-label'),/Running/);
 await live.locator('summary').click();await settle(page);
 for(const theme of ['light','dark'])for(const width of [320,390,1280]){
  await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);await page.setViewportSize({width,height:844});
  for(const status of ['success','failed','running']){
   const row=live.locator(`.tool-preview[data-status=${status}]`),expected=colors[theme][status];
   for(const selector of ['.tool-name','.tool-status-symbol','.tool-status-label'])assert.equal(await row.locator(selector).evaluate(el=>getComputedStyle(el).color),expected,`${theme} ${status} ${selector} semantic color`);
   const bg=await live.evaluate(el=>getComputedStyle(el).backgroundColor);assert.ok(contrast(expected,bg)>=4.5,`${theme} ${status} small text contrast`);assert.ok(await row.locator('.tool-status-symbol').evaluate(el=>!!el.textContent.trim() || !!el.querySelector('svg.progress-ring')),'outcome has a visible symbol');assert.equal(await row.locator('.tool-status-label').textContent(),{success:'Succeeded',failed:'Failed',running:'Running'}[status]);
  }
  for(const card of [saved,live])for(const selector of ['.activity-symbol','.activity-title'])assert.equal(await card.locator(selector).evaluate(el=>getComputedStyle(el).color),colors[theme].running);
  assert.equal(await live.locator('.disclosure-icon').evaluate(el=>getComputedStyle(el).color),await page.locator('.technical-strip').evaluate(el=>getComputedStyle(el).color),'disclosure stays monochrome');
  const send=await page.getByRole('button',{name:'Stop run'}).boundingBox();assert.ok(send.width>=44 && send.height>=44,'touch target retained');assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'no horizontal overflow');
  await page.locator('.messages').evaluate(el=>el.scrollTop=el.scrollHeight);await settle(page);await latestVisible(live,page);
  await mkdir(artifactURL(),{recursive:true});await page.screenshot({path:fileURLToPath(artifactURL(`activity-card-${width}-${theme}.png`))});
 }
 emit('tool',{name:'ongoingTool',tool_call_id:'o',status:'success'});await page.waitForFunction(()=>document.querySelector('.live-message .tool-activity').dataset.status==='success');assert.equal(await live.locator('.activity-preview').textContent(),'latestTool: Latest fixture');assert.equal(await live.locator('.activity-symbol').evaluate(el=>getComputedStyle(el).color),colors.dark.success);assert.equal(await live.locator('.tool-preview').count(),3);assert.equal(await page.locator('.conversation-status').getAttribute('data-state'),'running');
 emit('tool',{name:'lastFailure',tool_call_id:'z',status:'failed',summary:'Last failure fixture'});await page.waitForFunction(()=>document.querySelector('.live-message .tool-activity').dataset.status==='failed');assert.equal(await live.locator('.activity-preview').textContent(),'lastFailure: Last failure fixture');assert.equal(await live.locator('.activity-title').evaluate(el=>getComputedStyle(el).color),colors.dark.failed);
}));
