import {generatedAssets} from './generated-assets.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdtemp,rm,copyFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const repo=fileURLToPath(new URL('../../',import.meta.url));
async function fixture({approval=false}={}){
 const temp=await mkdtemp(join(tmpdir(),'inbox-reading-')),dir=generatedAssets(temp);let browser,server;
 const close=async()=>{try{await browser?.close();}finally{try{if(server){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}}finally{await rm(temp,{recursive:true,force:true});}}};
 try{
 assert.match(await readFile(join(dir,'index.html'),'utf8'),/app\.[a-f0-9]+\.js/);
 const state={calls:[],readGate:null,bulkGate:null,readFail:false,bulkFail:false,metadataGate:null};
 const items=[{id:'long',title:'A notification heading that must never become the conversation title — '+ 'very long notification title '.repeat(12),body:'**Complete notification**\n\n[Safe link](https://example.test/)\n\n'+Array.from({length:70},(_,i)=>`Paragraph ${i}: Full safely rendered long report, available only when opened.`).join('\n\n')+'\n\nEND OF COMPLETE BODY',created_at:1700000000,read:false,session_id:'s1'},...Array.from({length:14},(_,i)=>({id:'n'+i,title:'Notification '+i,body:'Other body '+i,read:false,created_at:1700000000-i}))];
 server=createServer(async(req,res)=>{
  const url=new URL(req.url,'http://localhost'),path=url.pathname;
  if(path.startsWith('/hermes/app-api/')){
   let raw='';for await(const chunk of req)raw+=chunk;
   const p=path.slice('/hermes/app-api'.length);state.calls.push({path:p,method:req.method,csrf:req.headers['x-csrf-token'],body:raw?JSON.parse(raw):null});let data;
   if(p==='/auth/me')data={user:{id:'synthetic-owner',profile:'default',status:'ready'},csrf_token:'fixture-csrf'};
   else if(p==='/sessions')data={items:[{id:'s1',title:'Authorized conversation title'}],total:1};
   else if(p==='/sessions/s1'){if(state.metadataGate)await state.metadataGate;data={id:'s1',title:'Authorized conversation title'};}
   else if(p.endsWith('/messages'))data={items:[{role:'assistant',content:'Synthetic conversation'}],offset:0,run:null};
   else if(p==='/inbox')data={items,read_count:items.filter(i=>i.read).length};
   else if(p==='/approvals')data={items:approval?[{id:'approval',title:'Pending action',action:'Long payload '.repeat(600),target:'Synthetic only',expires_at:1700000000}]:[]};
   else if(p==='/inbox/read-all'){
    if(state.bulkGate)await state.bulkGate;if(state.bulkFail){res.writeHead(503,{'Content-Type':'application/json'}).end(JSON.stringify({detail:'Bulk unavailable'}));return;}
    data={ok:true,updated:items.filter(i=>!i.read).length};items.forEach(i=>i.read=true);
   }else if(p.endsWith('/read')){
    if(state.readGate)await state.readGate;if(state.readFail){res.writeHead(503,{'Content-Type':'application/json'}).end(JSON.stringify({detail:'Read unavailable'}));return;}
    items.find(i=>i.id===p.split('/')[2]).read=true;data={ok:true};
   }else if(p.endsWith('/dismiss')){items.splice(items.findIndex(i=>i.id===p.split('/')[2]),1);data={ok:true,dismissed:1};}
   else data={items:[]};
   res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify(data));return;
  }
  const relative=path.replace(/^\/hermes\//,'')||'index.html';if(relative.includes('..')){res.writeHead(400).end();return;}
  try{const body=await readFile(join(dir,relative));res.writeHead(200,{'Content-Type':({html:'text/html',mjs:'text/javascript',js:'text/javascript',css:'text/css',svg:'image/svg+xml',webmanifest:'application/manifest+json'})[relative.split('.').pop()]||'application/octet-stream'}).end(body);}catch{res.writeHead(404).end();}
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:320,height:844},hasTouch:true}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(`http://127.0.0.1:${server.address().port}/hermes/?inbox=long`);
 await page.locator('[data-inbox-id="long"] summary').waitFor();
 return {page,state,errors,temp,close};
 }catch(error){await close().catch(()=>{});throw error;}
}
const readCalls=state=>state.calls.filter(c=>c.path.startsWith('/inbox/') && c.method==='POST');

test('generated Inbox approval is folded and explicit review never decides or marks notification read',async()=>{
 const h=await fixture({approval:true}),{page,state}=h;try{
 const card=page.locator('[data-approval-id="approval"]'),details=card.locator('details');
 assert.equal(await details.evaluate(el=>el.open),false);assert.equal(await card.getByRole('button',{name:'Approve once'}).isVisible(),false);
 await card.locator('summary').focus();await page.keyboard.press('Enter');assert.equal(await details.evaluate(el=>el.open),true);
 assert.equal(await card.getByRole('button',{name:'Approve once'}).isVisible(),true);assert.equal(readCalls(state).length,0);assert.equal(state.calls.some(c=>c.path.includes('/decision')),false);
 await page.getByRole('button',{name:'Mark all read',exact:true}).tap();await page.waitForFunction(()=>document.querySelector('.inbox-item.read'));
 assert.equal(await details.evaluate(el=>el.open),true);assert.match(await card.locator('summary').innerText(),/NEEDS YOUR REVIEW/);assert.equal(state.calls.some(c=>c.path.includes('/decision')),false);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.deepEqual(h.errors,[]);
 }finally{await h.close();}
});

test('generated Inbox opening during failed bulk keeps per-item retry and successful reads in place',async()=>{
 const h=await fixture(),{page,state}=h;let release;try{
 state.bulkGate=new Promise(resolve=>release=resolve);state.bulkFail=true;state.readFail=true;
 await page.getByRole('button',{name:'Mark all read',exact:true}).tap();const card=page.locator('[data-inbox-id="long"]');await card.locator('summary').tap();
 await card.getByRole('button',{name:'Retry marking read',exact:true}).waitFor();release();await page.getByRole('button',{name:'Retry marking all read',exact:true}).waitFor();
 assert.equal(await card.evaluate(el=>el.classList.contains('unread')),true);assert.equal(await card.locator('details').evaluate(el=>el.open),true);
 state.readFail=false;await card.getByRole('button',{name:'Retry marking read',exact:true}).tap();await page.waitForFunction(()=>document.querySelector('[data-inbox-id="long"]').classList.contains('read'));
 state.bulkFail=false;await page.getByRole('button',{name:'Retry marking all read',exact:true}).tap();await page.waitForFunction(()=>document.querySelectorAll('.inbox-item.unread').length===0);
 assert.equal(await card.locator('details').evaluate(el=>el.open),true);assert.equal(state.calls.filter(c=>c.path==='/inbox').length,1);assert.deepEqual(h.errors,[]);
 }finally{release?.();await h.close();}
});

test('generated Inbox mobile folds long cards, native keyboard and touch read locally, links do not toggle, authorized title selects Chats',async()=>{
 const h=await fixture(),{page,state}=h;try{
 const card=page.locator('[data-inbox-id="long"]'),summary=card.locator('summary'),details=card.locator('details');
 assert.equal(await details.evaluate(el=>el.open),false);assert.equal(await card.locator('.inbox-body').isVisible(),false);assert.equal(readCalls(state).length,0);
 assert.ok((await summary.boundingBox()).height<=110,'folded long heading stays compact');
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'320px has no horizontal overflow');
 for(const node of [summary,page.getByRole('button',{name:'Mark all read',exact:true}),page.getByRole('button',{name:'Clear read',exact:true}),page.getByRole('button',{name:'Refresh',exact:true})]){
  const box=await node.boundingBox();assert.ok(box.height>=44 && box.width>=44,'touch target');assert.ok(box.x>=0 && box.x+box.width<=320,'actions wrap within 320px');
 }
 await page.locator('#main').evaluate(el=>el.scrollTop=el.scrollHeight);assert.equal(readCalls(state).length,0);await summary.scrollIntoViewIfNeeded();
 await page.screenshot({path:join(process.env.HERMES_TEST_ARTIFACT_DIR || h.temp,'folded.png')});
 let release;state.readGate=new Promise(resolve=>release=resolve);
 const request=page.waitForRequest(r=>r.url().endsWith('/inbox/long/read'));await summary.focus();await page.keyboard.press('Enter');await request;
 assert.equal(await details.evaluate(el=>el.open),true);assert.equal(await card.evaluate(el=>el.classList.contains('unread')),true);
 await page.evaluate(()=>{window.readingCard=document.querySelector('[data-inbox-id="long"]');window.readingFold=readingCard.querySelector('details');document.querySelector('#main').scrollTop=150;});
 const top=await page.locator('#main').evaluate(el=>el.scrollTop);
 release();await page.waitForFunction(()=>document.querySelector('[data-inbox-id="long"]').classList.contains('read'));
 assert.equal(await page.locator('#main').evaluate(el=>el.scrollTop),top);assert.equal(await page.evaluate(()=>readingCard===document.querySelector('[data-inbox-id="long"]')&&readingFold.open),true);
 assert.equal(state.calls.filter(c=>c.path==='/inbox').length,1);assert.equal(readCalls(state).length,1);assert.equal(readCalls(state)[0].csrf,'fixture-csrf');
 await card.locator('a').evaluate(el=>el.addEventListener('click',e=>e.preventDefault()));await card.locator('a').tap();assert.equal(await details.evaluate(el=>el.open),true);assert.equal(readCalls(state).length,1);
 assert.match(await card.locator('.inbox-body').innerText(),/END OF COMPLETE BODY/);
 await summary.focus();await page.keyboard.press('Space');assert.equal(await details.evaluate(el=>el.open),false);await summary.tap();assert.equal(await details.evaluate(el=>el.open),true);assert.equal(readCalls(state).length,1);
 await page.setViewportSize({width:390,height:844});await summary.scrollIntoViewIfNeeded();await page.screenshot({path:join(process.env.HERMES_TEST_ARTIFACT_DIR || h.temp,'open.png')});
 const before=readCalls(state).length;await card.getByRole('button',{name:'Open conversation',exact:true}).tap();await page.getByRole('textbox',{name:'Message Hermes'}).waitFor();
 assert.equal(await page.locator('.conversation-title h1').innerText(),'Authorized conversation title');assert.equal(await page.locator('nav [aria-current=page]').getAttribute('data-view'),'chats');assert.equal(readCalls(state).length,before);
 await page.getByRole('textbox',{name:'Message Hermes'}).fill('Preserve unsent draft');await page.getByRole('button',{name:'Inbox',exact:true}).tap();await page.locator('[data-inbox-id="long"] summary').tap();await page.locator('[data-inbox-id="long"]').getByRole('button',{name:'Open conversation',exact:true}).tap();await page.getByRole('textbox',{name:'Message Hermes'}).waitFor();assert.equal(await page.getByRole('textbox',{name:'Message Hermes'}).inputValue(),'Preserve unsent draft');
 assert.deepEqual(h.errors,[]);
 if(process.env.HERMES_INBOX_SCREENSHOT_DIR && !process.env.HERMES_TEST_ARTIFACT_DIR)for(const name of ['folded','open'])await copyFile(join(h.temp,name+'.png'),join(process.env.HERMES_INBOX_SCREENSHOT_DIR,'chat-inbox-reading-'+name+'.png'));
 }finally{await h.close();}
});
