import {generatedAssets} from './generated-assets.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdtemp,rm,readdir} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const repo=fileURLToPath(new URL('../../',import.meta.url));
const defaults=()=>({revision:0,enabled:true,categories:{completion:true,approval:true,attention:true,scheduled:true,operational:true},hide_details:false});
async function fixture(run){
 const tmp=await mkdtemp((process.env.TMPDIR || '/tmp')+'/b-');let server,browser;
 const dir=generatedAssets(tmp),calls=[],control={preferences:defaults(),getError:false,putError:false,hold:null,user:'fixture-owner'};
 try{
  const names=await readdir(dir),ui=names.find(n=>/^ui\.[a-f0-9]+\.mjs$/.test(n)),sw=names.find(n=>/^sw\.[a-f0-9]+\.js$/.test(n));
  assert.ok(ui && sw,'tests use generated release graph');
  server=createServer(async(req,res)=>{
   const path=new URL(req.url,'http://fixture').pathname;
   if(path.startsWith('/hermes/app-api/')){
    let raw='';for await(const part of req)raw+=part;
    const p=path.slice('/hermes/app-api'.length),call={p,method:req.method,body:raw?JSON.parse(raw):null,csrf:req.headers['x-csrf-token']};calls.push(call);
    let data={items:[]},status=200;
    if(p==='/auth/me'){if(control.authHold)await control.authHold;data={user:{id:control.user,status:'ready'},csrf_token:control.csrf || 'fixture-csrf'};}
    if(p==='/push/presence' && call.body.visible && control.presenceHold)await control.presenceHold;
    if(p==='/sessions')data={items:[{id:'session-one',title:'First conversation'},{id:'session-two',title:'Second conversation'}]};
    if(p==='/push/key')data={public_key:'AQID'};
    if(p.endsWith('/messages'))data={items:[],total:0};
    if(p==='/push/preferences'){
     if(control.hold)await control.hold;
     if(req.method==='GET' && control.getError || req.method==='PUT' && control.putError){status=503;data={detail:'Synthetic unavailable'};}
     else if(req.method==='PUT' && call.body.revision!==control.preferences.revision){status=409;data={detail:'Preferences changed elsewhere'};}
     else {if(req.method==='PUT')control.preferences={...call.body,revision:call.body.revision+1};data=control.preferences;}
    }
    res.writeHead(status,{'Content-Type':'application/json'}).end(JSON.stringify(data));return;
   }
   const relative=path.replace(/^\/hermes\//,'')||'index.html';
   if(relative.includes('..'))return res.writeHead(400).end();
   try{const bytes=await readFile(dir+'/'+relative);res.writeHead(200,{'Content-Type':({html:'text/html',js:'text/javascript',mjs:'text/javascript',css:'text/css',svg:'image/svg+xml'})[relative.split('.').pop()]||'application/octet-stream'}).end(bytes);}catch{res.writeHead(404).end();}
  });await new Promise(r=>server.listen(0,'127.0.0.1',r));
  browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage'],env:{...process.env,TMPDIR:tmp}});
  const context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true}),page=await context.newPage();page.setDefaultTimeout(2500);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const url=`http://127.0.0.1:${server.address().port}/hermes/`;
  // Mount the actual generated UI/API, retaining its disposal handle. No production auth or data.
  async function mount(page){
   await page.goto(url+'styles.'+ui.split('.')[1]+'.css');
   await page.evaluate(async({ui})=>{document.body.innerHTML='<div id="app"></div>';const style=document.createElement('link');style.rel='stylesheet';style.href='./styles.'+ui.split('.')[1]+'.css';document.head.append(style);const {createAPI}=await import('./api.'+ui.split('.')[1]+'.mjs');const {mountApp}=await import('./'+ui);window.app=await mountApp(document,createAPI(),window);},{ui});
  }
  await mount(page);
  await run({page,context,url,calls,control,ui,sw,dir,mount});
  assert.deepEqual(errors,[]);
 }finally{await browser?.close();server?.closeAllConnections();await new Promise(r=>server?server.close(r):r());await rm(tmp,{recursive:true,force:true});}
}
async function pushWorker({page,context,sw}) {
 await page.evaluate(async sw=>{await navigator.serviceWorker.register('./'+sw,{scope:'/hermes/'});await navigator.serviceWorker.ready;},sw);
 const worker=context.serviceWorkers()[0];assert.ok(worker,'real generated service worker activated');
 await worker.evaluate(()=>{self.notifications=[];self.registration.showNotification=async(title,options)=>{notifications.push({title,...options});};});
 return {worker,push:async data=>worker.evaluate(async data=>{const event=new Event('push');event.data={json:()=>data};let pending;event.waitUntil=p=>pending=p;self.dispatchEvent(event);await pending;return notifications.at(-1);},data)};
}



async function device(page){
 await page.evaluate(()=>{
  if(window.fixturePush)return;
  window.fixturePush={permission:'granted',subscribed:true,prompts:0};
  Object.defineProperty(Notification,'permission',{configurable:true,get:()=>fixturePush.permission});
  Notification.requestPermission=async()=>{fixturePush.prompts++;return fixturePush.permission;};
  const subscription={endpoint:'https://push.fixture.test/synthetic',toJSON(){return {endpoint:this.endpoint,keys:{p256dh:'fixture',auth:'fixture'}};},async unsubscribe(){fixturePush.subscribed=false;return true;}};
  Object.defineProperty(navigator.serviceWorker,'ready',{configurable:true,value:Promise.resolve({pushManager:{async getSubscription(){return fixturePush.subscribed?subscription:null;},async subscribe(){fixturePush.subscribed=true;return subscription;}}})});
 });
}
const settings=async page=>{await device(page);await page.getByRole('button',{name:'Settings',exact:true}).click();};

const ready=page=>page.getByRole('checkbox',{name:'Hide notification details',exact:true}).waitFor();
test('adversarial: authoritative heartbeat 401 must stop the lease controller',()=>fixture(async({page,calls})=>{
 await page.clock.install();
 await page.getByRole('button',{name:/First conversation/}).click();await page.waitForTimeout(60);
 const rejected=[];await page.route('**/push/presence',async route=>{rejected.push(JSON.parse(route.request().postData()));await route.fulfill({status:401,json:{detail:'Session expired'}});});
 await page.clock.fastForward(15000);await page.waitForTimeout(80);
 await page.clock.fastForward(60000);await page.waitForTimeout(80);
 const result={rejected,user:await page.evaluate(()=>app.state.user),signinCount:await page.getByRole('button',{name:'Sign in with a passkey',exact:true}).count()};
 assert.equal(result.signinCount,1,'presence 401 should expire current authentication rather than silently continue visible heartbeats');
 assert.equal(result.user,null);assert.equal(rejected.filter(body=>body.visible).length,1,'no renewal after authoritative 401');
}));

test('adversarial: navigating after blur must not abort the only outstanding hide without replacement',()=>fixture(async({page,ui})=>{
 await page.clock.install();
 await page.evaluate(async ui=>{
  app.destroy();const {mountApp}=await import('./'+ui),{createAPI}=await import('./api.'+ui.split('.')[1]+'.mjs');const actual=window.fetch.bind(window);
  window.transport={sent:[],accepted:[],aborted:[]};
  const fetcher=(url,options)=>{
   if(!String(url).endsWith('/push/presence'))return actual(url,options);
   const body=JSON.parse(options.body);transport.sent.push(body);
   if(body.visible){transport.accepted.push(body);return Promise.resolve(new Response('{}',{headers:{'content-type':'application/json'}}));}
   return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{transport.accepted.push(body);resolve(new Response('{}',{headers:{'content-type':'application/json'}}));},500);options.signal.addEventListener('abort',()=>{clearTimeout(timer);transport.aborted.push(body);reject(new DOMException('Aborted','AbortError'));},{once:true});});
  };window.app=await mountApp(document,createAPI(fetcher),window);
 },ui);
 await page.getByRole('button',{name:/First conversation/}).click();await page.waitForTimeout(40);
 await page.evaluate(()=>dispatchEvent(new Event('blur')));
 await settings(page);await ready(page);await page.clock.fastForward(1000);await page.waitForTimeout(50);
 const result=await page.evaluate(()=>transport);
 assert.equal(result.accepted.at(-1).visible,false,'the sole pending revoke was canceled; server still has visible presence until TTL');
 assert.equal(result.sent.length,2,'teardown retains the revoke rather than duplicating transport');assert.deepEqual(result.aborted,[]);
 assert.equal(result.accepted[1].client_id,result.accepted[0].client_id);assert.ok(result.accepted[1].sequence>result.accepted[0].sequence);
}));

test('adversarial: generated worker rejects non-HTTP URLs and additional Unicode format controls',()=>fixture(async f=>{
 const {push}=await pushWorker(f),result=[];
 for(const body of ['ftp://private.example/file','mailto:secret@example.com','Read\u061c backwards','Read\u2060joined','Read\ufeffhidden']){
  const notification=await push({title:'Request complete',body,inbox_id:'notice-independent'});result.push({input:body,notification});
 }
 
 for(const {input,notification} of result)assert.equal(notification.body,'Open Hermes to read your update.',`unsafe preview accepted: ${JSON.stringify(input)}`);
}));

test('independent mobile light/dark targets and keyboard order',()=>fixture(async({page,control})=>{
 await settings(page);await ready(page);
 assert.equal(await page.getByRole('checkbox').count(),6);
 const names=['completion','approval','attention','scheduled','operational','hide_details'],checks=[];
 for(const theme of ['light','dark']){
  await page.getByRole('combobox',{name:'Appearance'}).selectOption(theme);
  for(const name of names){
   await page.locator(`input[name=${name}]`).focus();await page.keyboard.press('Space');
   const measurement=await page.locator(`input[name=${name}]`).evaluate(input=>({name:input.name,checked:input.checked,focusTag:document.activeElement.tagName,focusName:document.activeElement.name,outline:getComputedStyle(input).outlineStyle,labelHeight:input.closest('label').getBoundingClientRect().height,inputWidth:input.getBoundingClientRect().width,bodyWidth:document.documentElement.scrollWidth,viewport:innerWidth}));
   await page.keyboard.press('Tab');measurement.nextFocus=await page.evaluate(()=>({tag:document.activeElement.tagName,name:document.activeElement.name,text:document.activeElement.textContent}));
   assert.ok(measurement.labelHeight>=44);assert.ok(measurement.bodyWidth<=measurement.viewport);checks.push({theme,...measurement});
  }
  await page.getByRole('button',{name:'Save notification preferences',exact:true}).click();await page.getByText('Notification preferences saved.',{exact:true}).waitFor();
  await page.getByText('Changes apply to notifications not yet sent. Already sent or displayed previews cannot be recalled.',{exact:true}).scrollIntoViewIfNeeded();
 }
 assert.equal(control.preferences.revision,2);
 assert.ok(checks.every(v=>v.focusName===v.name),'saved checkbox loses active keyboard focus');
}));
// A transport may finish after AbortSignal. A rejected old request must never
// expire the next owner/view, or a newer visibility request in the same view.
for(const transition of ['route','owner','destroy','superseded'])test(`stale presence 401 cannot expire after ${transition}`,()=>fixture(async({page,ui,control})=>{
 await page.clock.install();
 await page.evaluate(async ui=>{
  app.destroy();const {mountApp}=await import('./'+ui),{createAPI}=await import('./api.'+ui.split('.')[1]+'.mjs');const actual=window.fetch.bind(window);
  window.latePresence=[];
  const fetcher=(url,options)=>{
   if(!String(url).endsWith('/push/presence') || !JSON.parse(options.body).visible)return actual(url,options);
   return new Promise(resolve=>latePresence.push(()=>resolve(new Response('{"detail":"expired"}',{status:401,headers:{'content-type':'application/json'}}))));
  };window.app=await mountApp(document,createAPI(fetcher),window);
 },ui);
 await page.getByRole('button',{name:/First conversation/}).click();
 await page.waitForFunction(()=>latePresence.length===1);
 if(transition==='route'){await settings(page);await ready(page);}
 if(transition==='owner'){control.user='new-owner';await page.evaluate(()=>app.start());}
 if(transition==='destroy')await page.evaluate(()=>app.destroy());
 if(transition==='superseded')await page.evaluate(()=>dispatchEvent(new Event('blur')));
 const expectedOwner=transition==='owner'?'new-owner':'fixture-owner';
 await page.evaluate(()=>latePresence[0]());await page.waitForTimeout(80);
 assert.equal(await page.evaluate(()=>app.state.user?.id),expectedOwner);
 assert.equal(await page.getByRole('button',{name:'Sign in with a passkey',exact:true}).count(),0);
}));

test('current hide 401 expires and leaves no renewable presence',()=>fixture(async({page})=>{
 await page.clock.install();await page.getByRole('button',{name:/First conversation/}).click();await page.waitForTimeout(60);
 const rejected=[];await page.route('**/push/presence',async route=>{rejected.push(JSON.parse(route.request().postData()));await route.fulfill({status:401,json:{detail:'expired'}});});
 await page.evaluate(()=>dispatchEvent(new Event('blur')));
 await page.getByRole('button',{name:'Sign in with a passkey',exact:true}).waitFor({timeout:5000});
 assert.equal(await page.getByRole('button',{name:'Sign in with a passkey',exact:true}).count(),1);
 const count=rejected.length;
 await page.evaluate(()=>{dispatchEvent(new Event('focus'));dispatchEvent(new Event('pageshow'));document.dispatchEvent(new Event('visibilitychange'));});
 await page.clock.fastForward(60000);await page.waitForTimeout(80);
 assert.equal(rejected.length,count);assert.equal(await page.evaluate(()=>app.state.user),null);
}));

test('saving preferences never steals user-moved focus or restores a detached checkbox',()=>fixture(async({page,control})=>{
 await settings(page);await ready(page);
 for(const transition of ['focus','focus-then-body','route']){
  let release;control.hold=new Promise(r=>release=r);
  await page.locator('input[name=approval]').focus();await page.keyboard.press('Space');
  await page.getByRole('button',{name:'Save notification preferences',exact:true}).click();
  await page.getByText('Saving notification preferences…',{exact:true}).waitFor();
  if(transition==='route')await page.getByRole('button',{name:'Chats',exact:true}).click();
  else {await page.getByRole('combobox',{name:'Appearance'}).focus();if(transition==='focus-then-body')await page.evaluate(()=>document.activeElement.blur());}
  const before=await page.evaluate(()=>({tag:document.activeElement.tagName,name:document.activeElement.name}));
  control.hold=null;release();await page.waitForTimeout(80);
  if(transition!=='route')await page.getByText('Notification preferences saved.',{exact:true}).waitFor();
  assert.deepEqual(await page.evaluate(()=>({tag:document.activeElement.tagName,name:document.activeElement.name})),before);
 }
}));


test('generated worker rejects all Unicode Cf and scheme-shaped links in either preview field',()=>fixture(async f=>{
 const {push}=await pushWorker(f),unsafe=['ftp://private.example/file','mailto:secret@example.com','tel:+15555550100','data:text/plain,private','javascript:alert(1)','file:///secret','web+custom.resource-1:private'];
 for(let code=0;code<=0x10ffff;code++){const c=String.fromCodePoint(code);if(/\p{Cf}/u.test(c))unsafe.push('Read'+c+'hidden');}
 for(const value of unsafe)for(const field of ['title','body']){
  const shown=await push({title:'Request complete',body:'Your document is ready for review.',inbox_id:'notice-safe',[field]:value});
  assert.equal(shown.title,'Hermes',`${field}: ${JSON.stringify(value)}`);assert.equal(shown.body,'Open Hermes to read your update.');
  assert.equal(shown.actions,undefined);assert.equal(shown.image,undefined);assert.equal(shown.data.url,f.url+'?inbox=notice-safe');
 }
 for(const body of ['Your document is ready for review.','Result: completed successfully.','Révision terminée — 文档已准备好。']){
  const shown=await push({title:'Request complete',body,inbox_id:'notice-safe'});assert.equal(shown.title,'Request complete');assert.equal(shown.body,body);
 }
}));
