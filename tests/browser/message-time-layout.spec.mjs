import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdtemp,readFile,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createRequire} from 'node:module';
import {generatedAssets} from './generated-assets.mjs';
import {artifactURL} from './artifacts.mjs';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const time='2026-03-08T06:59:59.125Z',seconds=Date.parse(time)/1000;
test('generated timestamp layout stays quiet, readable and aligned at phone/tablet widths with real live completion', {timeout:60000},async()=>{
 const temporary=await mkdtemp(join(tmpdir(),'hmclock-'));let browser,server,eventResponse;
 const errors=[];let completed=false;
 const user={id:1,role:'user',content:'Recorded question',timestamp:seconds,runtime_reminders:[{id:'reminder',role:'tool',kind:'context_compression',name:'Runtime reminders',status:'completed',content:'Public task reminder',timestamp:seconds}]};
 const history=[user,{id:2,role:'assistant',content:'Recorded answer',timestamp:time},{id:3,role:'assistant',channel:'commentary',content:'Public progress',timestamp:seconds},{id:4,role:'tool',kind:'runtime_notice',name:'Tool limit reached',status:'completed',content:'Public process notice',timestamp:seconds},{id:5,role:'tool',kind:'delegation',content:'Public child result',timestamp:seconds},{id:6,role:'tool',kind:'context_compression',name:'Context compression',status:'completed',content:'Retained native envelope.'}];
 // Match ui-refinement's long receipt title and untimed Context compression header,
 // alongside explicit missing event evidence and the recorded timestamp fixture.
 const receipt={session_id:'s',kind:'background_result',agent_context_state:'not_injected',title:'Completed background task',body:'Public background report'};
 const receipts=[{...receipt,id:'background',source_event_id:'receipt',created_at:seconds+100,event_at:seconds},{...receipt,id:'legacy-background',source_event_id:'legacy-receipt',created_at:seconds},{...receipt,id:'untimed-background',source_event_id:'untimed-receipt',created_at:seconds+100,event_at:null}];
 try{
  const assets=generatedAssets(temporary);
  server=createServer(async(req,res)=>{
   try{
    const url=new URL(req.url,'http://fixture');const json=value=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};
    if(url.pathname.startsWith('/hermes/app-api/')){
     const path=url.pathname.slice('/hermes/app-api'.length);
     if(path==='/auth/me')return json({user:{id:'owner',status:'ready'},csrf_token:'fixture'});
     if(path==='/sessions')return json({items:[{id:'s',title:'Clock layout',source:'cli'}],total:1});
     if(path==='/sessions/s/messages')return json({items:[...history,...(completed?[{role:'user',content:'Live question',timestamp:seconds},{role:'assistant',content:'Live final',timestamp:seconds+61}]:[])],run:completed?null:{id:'r',session_id:'s',status:'running',input:'Live question',created_at:seconds}});
     if(path==='/sessions/s/background')return json({items:receipts});
     if(path==='/runs/r/events'){eventResponse=res;res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache'});res.write(': connected\n\n');return;}
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
  const page=await browser.newPage({viewport:{width:320,height:844},isMobile:true,hasTouch:true,timezoneId:'America/Toronto',locale:'en-US',serviceWorkers:'block'});page.setDefaultTimeout(5000);page.on('pageerror',error=>errors.push(error.message));
  const open=async()=>{await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);await page.getByRole('button',{name:'Clock layout',exact:true}).click();await page.locator('[data-background-id=background] time').waitFor();};
  await open();
  for(const width of [320,390,768,1280])for(const theme of ['light','dark']){
   await page.setViewportSize({width,height:844});
   await page.evaluate(value=>document.documentElement.dataset.theme=value,theme);
   const untimedProcess=page.locator('.context-compression').filter({hasText:'Retained native envelope.'});
   assert.equal(await untimedProcess.locator('time').count(),0,'missing process timestamp stays absent');
   assert.equal(await page.locator('[data-background-id=untimed-background] time').count(),0,'missing event evidence stays absent');
   const headers=await page.locator('.background-result > summary,.process-history > summary').evaluateAll(nodes=>nodes.map(node=>{
    const rect=node.getBoundingClientRect();
    return {text:node.textContent,height:rect.height,children:[...node.children].map(child=>{const box=child.getBoundingClientRect();return {text:child.textContent,x:box.x,y:box.y,width:box.width,height:box.height,within:box.left>=rect.left-1 && box.right<=rect.right+1 && box.top>=rect.top-1 && box.bottom<=rect.bottom+1};})};
   }));
   for(const header of headers){
    assert.ok(header.height>=44 && header.height<=68,`compact accessible header ${width}/${theme}: ${JSON.stringify(header)}`);
    assert.ok(header.children.every(child=>child.within),`header children fit ${width}/${theme}: ${JSON.stringify(header)}`);
    for(let i=0;i<header.children.length;i++)for(const b of header.children.slice(i+1)){
     const a=header.children[i];
     assert.ok(a.x+a.width<=b.x+1 || b.x+b.width<=a.x+1 || a.y+a.height<=b.y+1 || b.y+b.height<=a.y+1,`header content must not overlap ${width}/${theme}: ${JSON.stringify(header)}`);
    }
   }
   for(const title of await page.locator('.background-title').all()){
    assert.equal(await title.textContent(),'Completed background task');
    assert.ok((await title.boundingBox()).width>=48,'receipt title retains useful visible text');
   }
   const geometry=await page.locator('.messages').evaluate(messages=>[...messages.querySelectorAll('time.message-time')].map(node=>{
    const rect=node.getBoundingClientRect(),parent=node.parentElement.getBoundingClientRect(),style=getComputedStyle(node);
    return {text:node.textContent,fontSize:parseFloat(style.fontSize),weight:Number(style.fontWeight),color:style.color,parentColor:getComputedStyle(node.parentElement).color,within:rect.left>=parent.left-1 && rect.right<=parent.right+1,notClipped:node.scrollWidth<=node.clientWidth+1,datetime:node.dateTime,title:node.title,accessible:node.getAttribute('aria-label')};
   }));
   assert.ok(geometry.length>=8);
   for(const row of geometry){assert.ok(row.within && row.notClipped,`timestamp must fit ${width}: ${JSON.stringify(row)}`);assert.ok(row.fontSize<=12 && row.weight<=450,'quiet timestamp typography independent of author');assert.match(row.text,/Mar 8, 2026.*1:59/);assert.match(row.title,/Eastern Standard Time/);assert.equal(row.title,row.accessible);assert.equal(row.datetime,time);}
   for(const label of await page.locator('.process-history > summary > .caption').evaluateAll(nodes=>nodes.map(node=>({width:node.getBoundingClientRect().width,scrollWidth:node.scrollWidth,height:node.getBoundingClientRect().height,lineHeight:parseFloat(getComputedStyle(node).lineHeight)}))))assert.ok(label.height<=label.lineHeight+1 && label.scrollWidth<=label.width+1,'Completed must remain one readable word/line');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   assert.equal(await page.locator('.messages').evaluate(node=>node.scrollWidth<=node.clientWidth),true,'transcript has no horizontal overflow');
   for(const card of await page.locator('.context-compression,.background-result').all()){
    assert.equal(await card.evaluate(node=>node.open),false);assert.equal(await card.locator('.message-author').count(),0);
    await card.locator('summary').scrollIntoViewIfNeeded();await card.locator('summary').click();assert.equal(await card.locator('.markdown').isVisible(),true);await card.locator('summary').press('Enter');assert.equal(await card.evaluate(node=>node.open),false);
   }
   if(process.env.HERMES_LAYOUT_PROOF_DIR){await mkdir(process.env.HERMES_LAYOUT_PROOF_DIR,{recursive:true});await page.screenshot({path:join(process.env.HERMES_LAYOUT_PROOF_DIR,`message-time-${width}-${theme}.png`)});}
   await mkdir(artifactURL(),{recursive:true});await page.screenshot({path:artifactURL(`message-time-${width}-${theme}.png`).pathname});
  }
  assert.match(await page.locator('.live-message > .message-author time').textContent(),/^Started/);
  completed=true;eventResponse.write(`event: done\ndata: ${JSON.stringify({status:'completed',output:'Live final',updated_at:seconds+61})}\n\n`);
  await page.locator('.live-message').filter({hasText:'Live final'}).waitFor();const finalISO=new Date((seconds+61)*1000).toISOString();
  assert.equal(await page.locator('.live-message > .message-author time').getAttribute('datetime'),finalISO);assert.doesNotMatch(await page.locator('.live-message > .message-author time').textContent(),/Started/);
  await open();assert.equal(await page.locator('.assistant-message').filter({hasText:'Live final'}).locator('time').getAttribute('datetime'),finalISO);assert.deepEqual(errors,[]);
 }finally{await browser?.close();if(server){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}await rm(temporary,{recursive:true,force:true});}
});
