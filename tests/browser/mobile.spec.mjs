import {artifactURL} from './artifacts.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile, mkdir, readdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const dir=process.env.HERMES_FRONTEND_DIR ? process.env.HERMES_FRONTEND_DIR.replace(/\/$/,'')+'/' : fileURLToPath(new URL('../../frontend/',import.meta.url));

test('390px browser shell uses local assets, has no overflow, and supports all four authenticated tabs',async()=>{
  const server=createServer(async(req,res)=>{
    const path=new URL(req.url,'http://localhost').pathname;
    if(path.startsWith('/hermes/app-api/')){res.writeHead(401,{'Content-Type':'application/json'});res.end(JSON.stringify({detail:'Sign in required'}));return;}
    const relative=path.replace(/^\/hermes\//,'') || 'index.html';
    if(relative.includes('..')){res.writeHead(400);res.end();return;}
    try{const body=await readFile(dir+relative);const ext=relative.split('.').pop();res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',js:'text/javascript',mjs:'text/javascript',svg:'image/svg+xml',webmanifest:'application/manifest+json'})[ext] || 'application/octet-stream','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'"});res.end(body);}catch{res.writeHead(404);res.end('Missing public asset');}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER || '/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  try{
    const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,deviceScaleFactor:1});
    const page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));
    const url=`http://127.0.0.1:${server.address().port}/hermes/`;
    await page.goto(url);await page.waitForTimeout(300);
    assert.equal(await page.title(),'Hermes','browser title has no decorative tagline');
    assert.equal(await page.getByRole('button',{name:'Sign in with a passkey',exact:true}).count(),1,'rendered mobile login shell');
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    assert.equal(await page.locator('script[src^="https:"],link[href^="https:"]').count(),0);
    await mkdir(artifactURL(),{recursive:true});
    await page.screenshot({path:fileURLToPath(artifactURL('login-mobile.png')),fullPage:true,animations:'disabled'});
    // Fixtures are exclusively at the network boundary in this test, never shipped assets.
    await page.route('**/hermes/app-api/**',async route=>{
      const path=new URL(route.request().url()).pathname;
      if(path.endsWith('/runs/fixture-run/events')) {
        const events=[['tool',{event:'tool.started',tool:'web_search',summary:'Search: Toronto forecast'}],['tool',{event:'tool.completed',tool:'web_search',error:false}],...Array.from({length:24},(_,i)=>['tool',{event:'tool.completed',tool:i===23 ? 'todo' : 'terminal',summary:i===23 ? 'Tasks complete' : `Check ${i+1}`,error:false}]),['delta',{text:'Partial answer'}],['done',{status:'completed',output:'Live answer.'}]];
        await route.fulfill({contentType:'text/event-stream',body:events.map(([event,data],i)=>`id: ${i+1}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join('')});return;
      }
      const data=path.endsWith('/runs') ? {id:'fixture-run',session_id:'fixture-session',status:'running'} : path.endsWith('/auth/me') ? {user:{id:'browser-fixture',role:'owner',status:'ready'},csrf_token:'test-csrf'} : path.endsWith('/messages') ? (()=>{
        const latest=new URL(route.request().url()).searchParams.has('latest');
        return {offset:latest ? 100 : 0,total:200,items:latest ? [
          ...Array.from({length:100-6},(_,i)=>({role:'assistant',content:`Retained message ${i+100}. A calm place for a thought.\n\nA second paragraph keeps this a long conversation.`})),
          {role:'user',content:'Please check the forecast.'},
          {role:'assistant',channel:'commentary',content:'I’ll check the forecast and compare the sources.'},
          {role:'tool',name:'terminal',status:'success',summary:'Run: pytest tests',content:'private raw log'},
          {role:'tool',name:'web_search',status:'failed',summary:'Search: Toronto forecast',content:'private raw log'},
          {role:'tool',name:'delegate_task',kind:'delegation',status:'completed',content:'Actual subagent report for the browser fixture.'},
          {role:'assistant',content:'Newest answer.\n\n```js\nconsole.log("Hello, Hermes");\n```'}
        ] : Array.from({length:100},(_,i)=>({role:'assistant',content:`Older message ${i}.`}))};
      })() : path.includes('/sessions?') || path.endsWith('/sessions') ? {items:[{id:'fixture-session',title:'A little room to think',source:'cli',updated_at:1700000000}],total:1} : {items:[]};
      await route.fulfill({json:data});
    });
    await page.reload();await page.getByRole('button',{name:'New chat',exact:true}).waitFor();
    assert.equal(await page.locator('nav button').count(),4);
    for(const label of ['Inbox','Jobs','Settings','Chats']){await page.getByRole('button',{name:label,exact:true}).click();await page.waitForTimeout(60);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${label} no overflow`);}
    await page.getByRole('button',{name:'A little room to think',exact:true}).click();await page.getByRole('textbox',{name:'Message Hermes'}).waitFor();
    await page.screenshot({path:fileURLToPath(artifactURL('chat-mobile.png')),fullPage:true,animations:'disabled'});
    assert.equal(await page.locator('.messages').evaluate(el=>Math.abs(el.scrollHeight-el.clientHeight-el.scrollTop)<2),true,'real mobile browser opens at latest message');
    const historyCard=page.locator('details.tool-activity');
    assert.equal(await historyCard.count(),1,'adjacent saved tools share one foldable card');
    assert.equal(await historyCard.getAttribute('open'),null);
    const closedCard=await historyCard.boundingBox();assert.ok(closedCard.height<=64,'collapsed card is compact');
    assert.equal(await historyCard.evaluate(el=>getComputedStyle(el).borderTopStyle),'solid','card has a box border');
    await historyCard.locator('summary').click();
    const success=page.locator('.tool-preview[data-status=success]'),failure=page.locator('.tool-preview[data-status=failed]');
    assert.equal(await success.evaluate(el=>getComputedStyle(el).color),'rgb(40, 115, 69)','succeeded tools use restrained green');assert.equal(await failure.evaluate(el=>getComputedStyle(el).color),'rgb(168, 47, 52)','failed tools use restrained red');
    assert.equal(await success.locator('.tool-status-label').innerText(),'Succeeded');assert.equal(await failure.locator('.tool-status-label').innerText(),'Failed');assert.notEqual(await success.locator('.tool-status-symbol').innerText(),await failure.locator('.tool-status-symbol').innerText(),'outcomes remain distinct through text and symbols, not color');
    assert.match(await historyCard.locator('.tool-rows').innerText(),/Run: pytest tests/);
    assert.match(await historyCard.locator('.tool-rows').innerText(),/Search: Toronto forecast/);
    assert.equal(await page.locator('.user-message button').count(),0,'no Copy beneath own message');
    assert.equal(await page.locator('.activity-summary').evaluate(el=>el.open),true,'public progress starts visible');
    assert.equal(await page.locator('.delegation-result').getAttribute('open'),null);
    await page.screenshot({path:fileURLToPath(artifactURL('tool-history-expanded.png')),fullPage:true,animations:'disabled'});
    await historyCard.locator('summary').click();
    await page.locator('.messages').evaluate(el=>{el.scrollTop=0;});
    const before=await page.locator('.message').first().evaluate(el=>el.getBoundingClientRect().top);
    await page.getByRole('button',{name:'Load older messages',exact:true}).click();
    await page.getByText('Older message 0.',{exact:true}).waitFor({state:'attached'});
    const after=await page.getByText('Retained message 100. A calm place for a thought.',{exact:true}).evaluate(el=>el.closest('.message').getBoundingClientRect().top);
    assert.ok(Math.abs(before-after)<2,'prepending older history preserves the visible anchor');
    await page.locator('.messages').evaluate(el=>{el.scrollTop=el.scrollHeight;});
    const send=await page.getByRole('button',{name:'Send message'}).boundingBox();assert.ok(send.width>=44 && send.height>=44,'44px send target');
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'chat no overflow');
    await page.getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('combobox',{name:'Appearance'}).selectOption('dark');
    assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');
    await page.screenshot({path:fileURLToPath(artifactURL('settings-dark.png')),fullPage:true,animations:'disabled'});
    await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Chats',exact:true}).click();
    await page.getByRole('button',{name:'A little room to think',exact:true}).click();
    await page.getByRole('textbox',{name:'Message Hermes'}).waitFor();
    const small=await page.locator('button:visible').evaluateAll(elements=>elements.filter(el=>el.getBoundingClientRect().height<44 || el.getBoundingClientRect().width<44).map(el=>el.textContent));
    assert.deepEqual(small,[],'every visible button is at least 44px');
    for(const viewport of [{width:320,height:568},{width:390,height:450},{width:844,height:390},{width:1280,height:900}]) {
      await page.setViewportSize(viewport);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`no overflow at ${viewport.width}`);
      const rect=await page.getByRole('button',{name:'Send message'}).boundingBox();assert.ok(rect.y+rect.height<=viewport.height,'composer visible above bottom navigation');
    }
    await page.screenshot({path:fileURLToPath(artifactURL('chat-desktop.png')),fullPage:true,animations:'disabled'});
    await page.setViewportSize({width:390,height:844});
    await page.getByRole('textbox',{name:'Message Hermes'}).fill('Check the forecast');
    await page.getByRole('button',{name:'Send message'}).click();
    await page.locator('.live-message .tool-activity[data-status=success]').waitFor();
    const liveCard=page.locator('.live-message .tool-activity');
    assert.equal(await liveCard.getAttribute('open'),null);
    assert.match(await liveCard.locator('summary').innerText(),/Tasks complete/);
    assert.equal(await liveCard.evaluate(el=>getComputedStyle(el).borderTopStyle),'solid','live uses same card border');
    await liveCard.locator('summary').click();
    await page.evaluate(()=>new Promise(requestAnimationFrame));
    assert.equal(await liveCard.evaluate(el=>el.getBoundingClientRect().bottom<=el.closest('.messages').getBoundingClientRect().bottom+2),true,'expanding bottom activity keeps details above composer');
    assert.equal(await liveCard.locator('.tool-rows .tool-preview').count(),25,'start and completion update the same row');
    assert.match(await liveCard.innerText(),/Succeeded/);
    const liveOutput=page.locator('.live-message .message-body');
    assert.equal(await liveOutput.innerText(),'Live answer.','terminal output replaces partial text without reopening or duplication');
    assert.equal(await liveCard.locator('.tool-preview').last().evaluate(el=>{const r=el.getBoundingClientRect(),v=el.closest('.messages').getBoundingClientRect(),list=el.closest('.tool-rows').getBoundingClientRect(),composer=document.querySelector('.composer').getBoundingClientRect();return r.top>=Math.max(v.top,list.top) && r.bottom<=Math.min(v.bottom,list.bottom,composer.top)+2;}),true,'explicit expansion reveals latest tool row above composer, not the later answer');
    assert.equal(await liveOutput.evaluate(el=>el.getBoundingClientRect().top>=el.parentElement.querySelector('.tool-activity').getBoundingClientRect().bottom),true,'final answer follows tools instead of being buried above them');
    assert.doesNotMatch(await page.locator('body').innerText(),/Your personal space|Your connected assistant|PICK UP A THOUGHT/);
    await page.screenshot({path:fileURLToPath(artifactURL('tool-live-expanded.png')),fullPage:true,animations:'disabled'});
    await page.evaluate(()=>navigator.serviceWorker.ready);
    const cacheURLs=await page.evaluate(async()=>{const keys=await caches.keys();const requests=(await Promise.all(keys.map(async key=>(await caches.open(key)).keys()))).flat();return requests.map(request=>request.url);});
    assert.ok(cacheURLs.some(url=>url.endsWith('/hermes/index.html')));
    const helperFiles=(await readdir(dir)).filter(name=>/^tool-details(?:\.[0-9a-f]{24})?\.mjs$/.test(name));
    assert.equal(helperFiles.length,1,'exactly one source/generated helper dependency');
    assert.ok(cacheURLs.some(url=>new URL(url).pathname==='/hermes/'+helperFiles[0]),'offline shell retains this exact source/generated UI dependency');
    assert.equal(cacheURLs.some(url=>url.includes('app-api')),false,'no private API in cache storage');
    await page.unroute('**/hermes/app-api/**');
    await context.setOffline(true);await page.reload();await page.getByRole('button',{name:'Sign in with a passkey',exact:true}).waitFor();
    assert.match(await page.locator('body').innerText(),/Cannot connect to Hermes/);
    await page.screenshot({path:fileURLToPath(artifactURL('offline.png')),fullPage:true,animations:'disabled'});
    assert.deepEqual(errors,[],'no browser JS errors');
  }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
});
