import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createRequire} from 'node:module';
import {generatedAssets} from './generated-assets.mjs';
import {withoutValidatedPresence} from './push-fixture-contract.mjs';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const report=label=>`[Start ${label}](https://example.invalid/)\n\n`+Array.from({length:45},(_,i)=>`${label} retained paragraph ${i}: offline fixture content, not a live model response.`).join('\n\n')+`\n\nEND ${label}`;
const tools=Array.from({length:32},(_,i)=>({id:`tool-${i}`,role:'tool',name:'read_file',status:'success',summary:`Read fixture-${i}.txt`,content:''}));
const messages=[
 {id:1,role:'user',content:'Reachable disclosure fixture',timestamp:100},
 ...tools,
 {id:40,role:'assistant',channel:'commentary',content:report('progress'),tool_calls:tools.map((tool,i)=>({...tool,id:`nested-${i}`,function:{name:'read_file'}}))},
 {id:41,role:'tool',kind:'delegation',name:'delegate_task',status:'success',content:report('delegation')},
 {id:42,role:'tool',kind:'context_compression',status:'completed',content:report('compression')},
 {id:43,role:'tool',kind:'runtime_notice',name:'Tool limit reached',status:'completed',content:report('process')},
 {id:44,role:'assistant',content:'End of synthetic saved turn.'}
];

async function fixture({extraHistory=false,noResizeObserver=false}={}){
 const temp=await mkdtemp(join(tmpdir(),'reach-'));let browser,server;
 const close=async()=>{try{await browser?.close();}finally{try{if(server){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}}finally{await rm(temp,{recursive:true,force:true});}}};
 try{
  const dir=generatedAssets(temp),calls=[],errors=[];let expired=false;
  const history=extraHistory?[...messages,...Array.from({length:24},(_,i)=>({id:100+i,role:i%2?'assistant':'user',content:`Later fixture message ${i}`}))]:messages;
  assert.match(await readFile(join(dir,'index.html'),'utf8'),/app\.[a-f0-9]+\.js/,'real generated release assets');
  server=createServer(async(req,res)=>{
   const url=new URL(req.url,'http://localhost'),path=url.pathname;
   if(path.startsWith('/hermes/app-api/')){
    const p=path.slice('/hermes/app-api'.length);let raw='';for await(const chunk of req)raw+=chunk;
    const call={path:p,method:req.method,body:raw?JSON.parse(raw):null,csrf:req.headers['x-csrf-token']};calls.push(call);
    if(req.method!=='GET' && withoutValidatedPresence([call]).length){res.writeHead(403).end();return;}
    if(expired){res.writeHead(401,{'Content-Type':'application/json'}).end(JSON.stringify({detail:'Fixture session expired'}));return;}
    let data={items:[]};
    if(p==='/auth/me')data={user:{id:'offline-owner',profile:'default',status:'ready'},csrf_token:'fixture'};
    else if(p==='/sessions')data={items:[{id:'s',title:'Reachable fixture'}],total:1};
    else if(p==='/sessions/s/messages')data={items:history,total:history.length,offset:0,run:null};
    else if(p==='/sessions/s/telemetry')data={model:'Fixture model',metadata:{source:report('diagnostics'),freshness:'persisted'},usage:{input_tokens:10,output_tokens:20}};
    else if(p==='/sessions/s/background')data={items:[{id:'background',session_id:'s',title:'Synthetic background report',body:report('background'),kind:'background_result',source_event_id:'async:fixture',created_at:101,event_at:101,origin_message_id:1,origin_session_id:'s',agent_context_state:'not_injected'}]};
    else if(p==='/inbox')data={items:[{id:'notification',title:'Synthetic notification',body:report('inbox'),session_id:'s',read:true}],read_count:1};
    else if(p==='/approvals')data={items:[{id:'approval',title:'Synthetic review',action:report('approval'),target:'END approval target',expires_at:1900000000}]};
    else if(p==='/jobs')data={items:[{id:'job',name:'Synthetic job',schedule:'0 9 * * *',enabled:true,prompt:report('job'),timezone:'UTC',last_status:'success'}]};
    res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify(data));return;
   }
   const relative=path.replace(/^\/hermes\//,'')||'index.html';
   if(relative.includes('..')){res.writeHead(400).end();return;}
   try{const body=await readFile(join(dir,relative));res.writeHead(200,{'Content-Type':({html:'text/html',js:'text/javascript',mjs:'text/javascript',css:'text/css',svg:'image/svg+xml',webmanifest:'application/manifest+json'})[relative.split('.').pop()]||'application/octet-stream'}).end(body);}catch{res.writeHead(404).end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:true,serviceWorkers:'block'}),page=await context.newPage();
  page.setDefaultTimeout(5000);page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(noResizeObserver=>{
   const Native=window.ResizeObserver;window.foldObservers=[];
   if(noResizeObserver){window.ResizeObserver=undefined;return;}
   window.ResizeObserver=class extends Native{
    observe(node,options){if(node.matches('.messages')){this.foldLive=true;if(!window.foldObservers.includes(this))window.foldObservers.push(this);}return super.observe(node,options);}
    disconnect(){this.foldLive=false;return super.disconnect();}
   };
  },noResizeObserver);
  await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);
  await page.getByRole('button',{name:'Reachable fixture',exact:true}).waitFor();
  return {page,context,calls,errors,temp,close,expire(){expired=true;}};
 }catch(error){await close().catch(()=>{});throw error;}
}
const settle=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
async function foldGeometry(summary){
 return summary.evaluate(el=>{
  const s=el.getBoundingClientRect(),m=el.closest('.messages').getBoundingClientRect();
  return {top:s.top,bottom:s.bottom,messagesTop:m.top,messagesBottom:m.bottom,
   contained:s.top>=m.top-1 && s.bottom<=m.bottom+1,
   hit:el.contains(document.elementFromPoint(s.x+s.width/2,s.y+s.height/2)),
   outerScroll:el.closest('.messages').scrollTop};
 });
}
async function expandedToolAtEnd(page,width=320){
 await page.setViewportSize({width,height:844});
 await page.getByRole('button',{name:'Reachable fixture',exact:true}).click();
 await page.locator('.background-result').waitFor({state:'attached'});
 const card=page.locator('.messages > .tool-activity').first(),summary=card.locator(':scope > summary'),body=card.locator('.tool-rows');
 await summary.evaluate(el=>el.addEventListener('click',event=>{window.foldClickTrusted=event.isTrusted;},{once:true}));
 await summary.tap();await settle(page);
 assert.equal(await page.evaluate(()=>window.foldClickTrusted),true);
 await body.evaluate(el=>{el.scrollTop=0;});
 const b=await body.boundingBox(),m=await page.locator('.messages').boundingBox();
 await page.mouse.move(b.x+b.width/2,Math.min(b.y+b.height,m.y+m.height)-15);
 await page.mouse.wheel(0,5000);
 await page.waitForFunction(()=>{const el=document.querySelector('.messages > .tool-activity .tool-rows');return el.scrollTop>100 && el.scrollHeight-el.clientHeight-el.scrollTop<2;});
 const before=await foldGeometry(summary);
 assert.equal(before.contained && before.hit,true,JSON.stringify(before));
 return {card,summary,body,before};
}

test('DISCLOSURE-RESIZE-01 already-expanded visible fold survives shrink before any corrective test scroll',{timeout:30000},async()=>{
 const h=await fixture(),{page}=h;
 try{
  for(const [width,height] of [[320,500],[390,320],[768,400]]){
  const {card,summary,body,before}=await expandedToolAtEnd(page,width);
  const internal=await body.evaluate(el=>el.scrollTop);
  await page.evaluate(()=>{window.foldFocus=document.activeElement;});
  await page.setViewportSize({width,height});await settle(page);await settle(page);
  // Deliberately no scrollIntoView, focus, or locator activation after resize.
  const after=await foldGeometry(summary);
  console.log('DISCLOSURE-RESIZE-01 geometry',JSON.stringify({width,height,before,after}));
  assert.equal(after.contained,true,`entire fold target remains inside messages: ${JSON.stringify(after)}`);
  assert.equal(after.hit,true,'fold target is immediately hit-testable');
  assert.equal(await body.evaluate(el=>el.scrollTop),internal,'internal reading position is retained');
  assert.equal(await page.evaluate(()=>document.activeElement===window.foldFocus),true,'no focus theft');
  assert.equal(await card.evaluate(el=>el.open),true);
  const b=await summary.boundingBox();await page.touchscreen.tap(b.x+b.width/2,b.y+b.height/2);
  assert.equal(await card.evaluate(el=>el.open),false,'trusted touch folds without locator auto-scroll');
  await page.getByRole('button',{name:'Back to chats'}).click();
  assert.equal(await page.evaluate(()=>window.foldObservers.filter(o=>o.foldLive).length),0,'route exit disconnects transcript observer');
  }
  assert.deepEqual(h.errors,[]);
 }finally{await h.close();}
});
test('DISCLOSURE-RESIZE-01 never retrieves a fold the reader scrolled away from; account expiry disposes it',{timeout:30000},async()=>{
 const h=await fixture({extraHistory:true}),{page}=h;
 try{
  const {card,summary,body}=await expandedToolAtEnd(page);
  const internal=await body.evaluate(el=>el.scrollTop);
  // End the body wheel transaction before starting a separate transcript gesture.
  await page.waitForTimeout(400);
  const m=await page.locator('.messages').boundingBox();
  await page.mouse.move(m.x+m.width/2,m.y+20);await page.mouse.wheel(0,650);
  await page.waitForFunction(()=>document.querySelector('.messages').scrollTop>500);await settle(page);
  const before=await foldGeometry(summary);assert.equal(before.hit,false,'reader has genuinely left the fold');
  assert.equal(await page.locator('.messages').evaluate(el=>el.scrollHeight-el.clientHeight-el.scrollTop>200),true,'reader is not following the tail');
  await page.setViewportSize({width:320,height:500});await settle(page);await settle(page);
  const after=await foldGeometry(summary);
  assert.equal(after.outerScroll,before.outerScroll,'resize does not retrieve an abandoned disclosure');
  assert.equal(after.hit,false);assert.equal(await card.evaluate(el=>el.open),true);
  assert.equal(await body.evaluate(el=>el.scrollTop),internal);
  assert.equal(await page.evaluate(()=>window.foldObservers.filter(o=>o.foldLive).length),1);
  h.expire();await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await page.getByRole('heading',{name:'Sign in',exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>window.foldObservers.filter(o=>o.foldLive).length),0,'account expiry disconnects observer');
  await page.setViewportSize({width:320,height:400});await settle(page);
  assert.deepEqual(h.errors,[]);
 }finally{await h.close();}
});

test('DISCLOSURE-RESIZE-01 unrelated disclosure updates cannot replace the active fold target',{timeout:30000},async()=>{
 const h=await fixture(),{page}=h;
 try{
  const {summary}=await expandedToolAtEnd(page);
  await page.locator('.runtime-notice').evaluate(el=>{el.open=true;});await settle(page);
  assert.equal((await foldGeometry(summary)).hit,true,'active fold still visible before shrink');
  await page.setViewportSize({width:320,height:500});await settle(page);await settle(page);
  const after=await foldGeometry(summary);
  assert.equal(after.contained && after.hit,true,`protect active fold, not another programmatically opened card: ${JSON.stringify(after)}`);
  assert.deepEqual(h.errors,[]);
 }finally{await h.close();}
});

test('DISCLOSURE-RESIZE-01 zoom and suspended pages do not retrieve a fold; missing observer is harmless',{timeout:30000},async()=>{
 const h=await fixture(),{page,context}=h;
 try{
  const {summary,before}=await expandedToolAtEnd(page);
  const cdp=await context.newCDPSession(page);
  await cdp.send('Emulation.setPageScaleFactor',{pageScaleFactor:2});await settle(page);
  assert.ok(await page.evaluate(()=>visualViewport.scale)>1.5,'real browser VisualViewport magnification');
  await page.setViewportSize({width:320,height:500});await settle(page);await settle(page);
  assert.equal((await foldGeometry(summary)).outerScroll,before.outerScroll,'zoom does not retrieve fold');
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true})));
  assert.equal(await page.evaluate(()=>window.foldObservers.filter(o=>o.foldLive).length),0);
  await cdp.send('Emulation.setPageScaleFactor',{pageScaleFactor:1});
  await page.setViewportSize({width:320,height:400});await settle(page);
  assert.equal((await foldGeometry(summary)).outerScroll,before.outerScroll,'suspended controller never scrolls');
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));await settle(page);
  assert.equal(await page.evaluate(()=>window.foldObservers.filter(o=>o.foldLive).length),1,'BFCache resumes one observer without stale target');
  assert.equal((await foldGeometry(summary)).outerScroll,before.outerScroll);
  assert.deepEqual(h.errors,[]);
 }finally{await h.close();}
 const fallback=await fixture({noResizeObserver:true});
 try{await expandedToolAtEnd(fallback.page);assert.deepEqual(fallback.errors,[],'CSS/native behavior still works without observer API');}
 finally{await fallback.close();}
});

async function open(card,page){
 const summary=card.locator(':scope > summary');
 if(!await card.evaluate(el=>el.open)){await summary.focus();await page.keyboard.press('Enter');}
 await settle(page);
}
async function cappedBody(card,body,page,label){
 await open(card,page);
 const size=await body.evaluate(el=>({height:el.getBoundingClientRect().height,client:el.clientHeight,scroll:el.scrollHeight,overflow:getComputedStyle(el).overflowY,cap:Math.min(240,innerHeight*.32)}));
 assert.ok(size.height<=size.cap+1,`${label}: body ${size.height}px exceeds ${size.cap}px cap`);
 assert.ok(size.scroll>size.client+100,`${label}: full long content survives in a nested scrollport`);
 assert.match(size.overflow,/auto|scroll/,`${label}: not hidden or clipped`);
 await body.evaluate(el=>{el.scrollTop=el.scrollHeight;});await settle(page);
 assert.equal(await body.evaluate(el=>el.scrollHeight-el.clientHeight-el.scrollTop<2),true,`${label}: last content is scrollable`);
 const last=body.locator(':scope > :last-child');
 if(await last.count())assert.equal(await last.evaluate(el=>{const p=el.parentElement.getBoundingClientRect(),b=el.getBoundingClientRect();return b.bottom<=p.bottom+1 && b.bottom>p.top;}),true,`${label}: final child reaches the scrollport`);
 const summary=card.locator(':scope > summary');
 await summary.scrollIntoViewIfNeeded();
 assert.equal(await summary.evaluate(el=>{const r=el.getBoundingClientRect();return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}),true,`${label}: fold bar hit target remains reachable`);
 await summary.tap();assert.equal(await card.evaluate(el=>el.open),false,`${label}: native touch collapses`);
 await summary.focus();await page.keyboard.press('Enter');assert.equal(await card.evaluate(el=>el.open),true,`${label}: keyboard reopens`);
 await page.keyboard.press('Space');assert.equal(await card.evaluate(el=>el.open),false,`${label}: keyboard collapses`);
}

test('generated disclosures cap full content at phone/tablet and short viewport sizes without changing Inbox toolbar',{timeout:90000},async()=>{
 const h=await fixture(),{page}=h;
 try{
  for(const viewport of [{width:320,height:844},{width:390,height:500},{width:768,height:600},{width:1024,height:1000}]){
   await page.setViewportSize(viewport);
   await page.getByRole('button',{name:'Reachable fixture',exact:true}).click();
   await page.locator('.background-result').waitFor({state:'attached'});
   for(const [selector,body,label] of [
    ['.messages > .tool-activity','.tool-rows','tools'],
    ['.activity-summary',':scope > .markdown','progress'],
    ['.delegation-result',':scope > .markdown','delegation'],
    ['.context-compression',':scope > .markdown','compression'],
    ['.runtime-notice',':scope > .markdown','process'],
    ['.background-result',':scope > .markdown','background']
   ]){
    const card=page.locator(selector).first();
    await cappedBody(card,card.locator(body),page,`${viewport.width}x${viewport.height} ${label}`);
    if(label!=='tools')assert.match(await card.locator(body).textContent(),new RegExp(`END ${label}`),'report tail is preserved verbatim');
   }
   await page.getByRole('button',{name:'Back to chats'}).click();
   await page.getByRole('button',{name:'Inbox',exact:true}).click();
   const toolbar=await page.locator('.inbox-heading button').evaluateAll(nodes=>nodes.map(el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};}));
   const heading=await page.locator('.inbox-heading h1').boundingBox();
   for(const b of toolbar){assert.ok(Math.abs(b.y-toolbar[0].y)<1,'Inbox buttons retain one row');assert.ok(Math.abs(b.y+b.height/2-heading.y-heading.height/2)<1,'Inbox controls retain title row');assert.ok(b.width>=44 && b.height>=44);}
   for(const selector of ['.inbox-disclosure','.inbox-approval > details']){
    const card=page.locator(selector);await cappedBody(card,card.locator('.inbox-body'),page,`${viewport.width} ${selector}`);
    if(selector.includes('approval')){
     await open(card,page);const payload=card.locator('.review-payload');
     assert.equal(await payload.textContent(),report('approval'),'approval action is not truncated');
     await payload.focus();await page.keyboard.press('End');
     await page.waitForFunction(()=>{const el=document.querySelector('.inbox-approval .review-payload');return el.scrollHeight-el.scrollTop-el.clientHeight<2;});
     await card.locator('.inbox-body').evaluate(el=>{el.scrollTop=el.scrollHeight;});
     await card.getByRole('button',{name:'Deny',exact:true}).focus();
     assert.equal(await card.getByRole('button',{name:'Deny',exact:true}).evaluate(el=>el===document.activeElement),true,'nested action remains keyboard reachable without deciding');
     await card.locator('summary').focus();await page.keyboard.press('Space');
    }
   }
   await page.getByRole('button',{name:'Jobs',exact:true}).click();
   const job=page.locator('.job-card');await cappedBody(job,job.locator('.job-body'),page,`${viewport.width} jobs`);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'no horizontal page overflow');
   await page.getByRole('button',{name:'Chats',exact:true}).click();
  }
  assert.deepEqual(h.errors,[]);assert.deepEqual(withoutValidatedPresence(h.calls).filter(c=>c.method!=='GET'),[],'no live mutations or approval decisions');
 }finally{await h.close();}
});

test('fragmented diagnostics and nested Progress keep a sticky fold bar while scrolling to their last child',{timeout:45000},async()=>{
 const h=await fixture(),{page}=h;
 try{
  await page.getByRole('button',{name:'Reachable fixture',exact:true}).click();
  for(const viewport of [{width:390,height:844},{width:390,height:500},{width:768,height:600},{width:1024,height:1000}]){
   await page.setViewportSize(viewport);
   for(const selector of ['.technical-strip','.activity-summary']){
   const card=page.locator(selector);await open(card,page);
   if(selector==='.activity-summary')await open(card.locator('.tool-activity'),page);
   const size=await card.evaluate(el=>({height:el.getBoundingClientRect().height,summary:el.querySelector('summary').getBoundingClientRect().height,cap:Math.min(240,innerHeight*.32)}));
   assert.ok(size.height<=size.cap+size.summary+2,`${selector}: total panel, not each child, is bounded`);
   await card.evaluate(el=>{el.scrollTop=el.scrollHeight;});await settle(page);
   assert.ok(await card.evaluate(el=>el.scrollTop)>0,`${selector}: fragmented contents really scroll`);
   const sticky=await card.evaluate(el=>{const r=el.getBoundingClientRect(),s=el.querySelector('summary').getBoundingClientRect();return {top:s.top-r.top,bottom:s.bottom-r.bottom,position:getComputedStyle(el.querySelector('summary')).position};});
   assert.equal(sticky.position,'sticky');assert.ok(sticky.top>=0 && sticky.top<3,`${selector}: fold bar stays at panel top after scroll`);assert.ok(sticky.bottom<0);
   const summary=card.locator(':scope > summary');await summary.scrollIntoViewIfNeeded();
   assert.equal(await summary.evaluate(el=>{const r=el.getBoundingClientRect();return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}),true,`${selector}: sticky summary is not occluded by nested content`);
   await summary.tap();assert.equal(await card.evaluate(el=>el.open),false);
   }
  }
  assert.deepEqual(h.errors,[]);
 }finally{await h.close();}
});

test('nested generated tool list retains real keyboard and touch scrolling independently of Progress',{timeout:45000},async()=>{
 const h=await fixture(),{page,context}=h;
 try{
  await page.getByRole('button',{name:'Reachable fixture',exact:true}).click();
  const progress=page.locator('.activity-summary'),card=progress.locator('.tool-activity'),rows=card.locator('.tool-rows');
  await open(progress,page);await open(card,page);
  await progress.evaluate(el=>{el.scrollTop=el.scrollHeight;});
  await rows.evaluate(el=>{el.scrollTop=0;});
  await rows.scrollIntoViewIfNeeded();await rows.focus();
  await rows.evaluate(el=>{
   window.nestedKeyboardEnded=false;
   const ended=event=>{
    if(event.target!==el || el.scrollHeight-el.clientHeight-el.scrollTop>=2)return;
    window.nestedKeyboardEnded=true;el.removeEventListener('scrollend',ended);
   };
   el.addEventListener('scrollend',ended);
  });
  await page.keyboard.press('End');
  // A >100px sample is mid-animation: resetting there lets later keyboard
  // frames overwrite zero and falsely satisfy the subsequent touch assertion.
  await page.waitForFunction(()=>window.nestedKeyboardEnded);
  assert.ok(await rows.evaluate(el=>el.scrollTop)>100,'End really scrolls the nested list');
  await rows.evaluate(el=>{el.scrollTop=0;});await settle(page);
  const parentBefore=await progress.evaluate(el=>el.scrollTop);
  const box=await rows.boundingBox(),messagesBox=await page.locator('.messages').boundingBox(),outer=await progress.boundingBox();
  const x=box.x+box.width/2,y=Math.min(box.y+box.height,messagesBox.y+messagesBox.height,outer.y+outer.height)-12;
  assert.ok(y>Math.max(box.y,messagesBox.y,outer.y)+25,'nested content has a real touch surface');
  const before=await rows.evaluate((el,{x,y})=>{
   window.nestedTouch={start:null,cancelled:false,ended:false};
   el.addEventListener('touchstart',event=>{
    window.nestedTouch.start={trusted:event.isTrusted,inside:el.contains(event.target),scroll:el.scrollTop};
   },{once:true,passive:true});
   el.addEventListener('pointercancel',event=>{window.nestedTouch.cancelled=event.isTrusted;},{once:true});
   el.addEventListener('scrollend',event=>{
    const start=window.nestedTouch.start;
    if(event.target===el && start && el.scrollTop>start.scroll)window.nestedTouch.ended=true;
   });
   return {scroll:el.scrollTop,hit:el.contains(document.elementFromPoint(x,y))};
  },{x,y});
  assert.equal(before.scroll,0,'keyboard scrolling has completed before the touch baseline');
  assert.equal(before.hit,true,'gesture starts on the nested list, not an overlapping sticky bar');
  const cdp=await context.newCDPSession(page);
  // The synthetic-scroll touch driver emitted pointer motion without native
  // scrolling even on a bare overflow:auto control. Dispatch real touch input
  // instead, and prove the browser takes over the pan (trusted pointercancel).
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});
  for(const dy of [20,40,60,80,100]){
   await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y:y-dy}]});
   await settle(page);
  }
  await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  await page.waitForFunction(()=>document.querySelector('.activity-summary .tool-rows').scrollTop>20 && window.nestedTouch.ended);
  const touch=await page.evaluate(()=>window.nestedTouch);
  assert.deepEqual(touch.start,{trusted:true,inside:true,scroll:0},'trusted touch starts at the verified reset position');
  assert.equal(touch.cancelled,true,'native browser scrolling takes over the pointer');
  assert.ok(await rows.evaluate(el=>el.scrollTop)-before.scroll>20,'touch itself advances the nested list');
  assert.ok(Math.abs(await progress.evaluate(el=>el.scrollTop)-parentBefore)<2,'touch scroll remains within nested content');
  await rows.evaluate(el=>{el.scrollTop=el.scrollHeight;});
  assert.match(await rows.locator('.tool-preview').last().textContent(),/fixture-31/,'last tool is retained');
  const summary=progress.locator(':scope > summary');await summary.scrollIntoViewIfNeeded();await summary.tap();assert.equal(await progress.evaluate(el=>el.open),false);
  assert.deepEqual(h.errors,[]);
 }finally{await h.close();}
});
