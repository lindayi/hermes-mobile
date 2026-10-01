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

const master=page=>page.getByRole('switch',{name:'Notifications on this device',exact:true});
const save=page=>page.getByRole('button',{name:'Save notification preferences',exact:true});
const preferenceCalls=calls=>calls.filter(c=>c.p==='/push/preferences');
async function losePutResponse(page){
 await page.route('**/push/preferences',async route=>{
  if(route.request().method()!=='PUT')return route.continue();
  const response=await route.fetch();assert.equal(response.status(),200,'loopback server committed the real PUT');
  await route.abort('failed');
 });
}
test('Save reconciles a committed privacy PUT whose response was lost',()=>fixture(async({page,calls,control})=>{
 await settings(page);await ready(page);await losePutResponse(page);
 await page.getByRole('checkbox',{name:'Hide notification details',exact:true}).check();
 await save(page).click();
 await page.waitForFunction(()=>!document.querySelector('.push-master-switch').disabled);
 assert.equal(control.preferences.hide_details,true,'server committed the privacy choice');
 assert.equal(await page.getByRole('checkbox',{name:'Hide notification details',exact:true}).isChecked(),true,'UI must not falsely restore the old privacy choice');
 assert.deepEqual(preferenceCalls(calls).map(c=>c.method),['GET','PUT','GET'],'read after ambiguity, never automatically retry the mutation');
 assert.equal(await save(page).isDisabled(),true);
 assert.doesNotMatch(await page.locator('.notification-settings').innerText(),/Previous settings restored/);
}));
for(const kind of ['Save','master'])test(`${kind} response loss plus failed read stays unconfirmed until read-only retry`,()=>fixture(async({page,calls,control})=>{
 await settings(page);await ready(page);await losePutResponse(page);
 await page.getByRole('checkbox',{name:'Hide notification details',exact:true}).check();
 control.getError=true;
 await (kind==='Save'?save(page):master(page)).click();
 await page.getByText(/Notification status is unconfirmed/).waitFor();
 assert.equal(control.preferences.hide_details,kind==='Save');
 assert.equal(control.preferences.enabled,kind==='Save');
 const panel=page.locator('.notification-settings');
 assert.doesNotMatch(await panel.innerText(),/Notifications are enabled\.|Notifications are disabled\.|Previous settings restored/);
 assert.equal(await page.locator('.push-master-switch').isDisabled(),true);
 assert.equal(await page.locator('.push-master-switch').getAttribute('aria-checked'),null,'unknown must not announce an authoritative switch value');
 assert.equal(await page.locator('input[name=hide_details]').isChecked(),true,'retain local privacy intent');
 assert.equal(await page.getByRole('button',{name:/Retry (saving|changing)/}).count(),0);
 control.getError=false;
 await page.getByRole('button',{name:'Retry checking notifications',exact:true}).click();
 await page.getByText(/Current notification settings checked/).waitFor();
 assert.deepEqual(preferenceCalls(calls).map(c=>c.method),['GET','PUT','GET','GET']);
 assert.equal(await master(page).getAttribute('aria-checked'),String(kind==='Save'));
 if(kind==='master'){
  await page.unroute('**/push/preferences');
  await master(page).click();await page.getByText('Notifications enabled.',{exact:true}).waitFor();
  assert.equal(control.preferences.hide_details,false,'enabling must not silently apply the local draft');
  assert.equal(await page.getByRole('checkbox',{name:'Hide notification details',exact:true}).isChecked(),true);
  assert.equal(await save(page).isEnabled(),true);
 }else assert.equal(await save(page).isDisabled(),true);
}));
test('recovery rejects a preference revision older than the last confirmed revision',()=>fixture(async({page,calls,control})=>{
 control.preferences.revision=7;
 await settings(page);await ready(page);
 const stale={...defaults(),revision:6};
 await page.route('**/push/preferences',async route=>{
  if(route.request().method()==='PUT'){assert.equal((await route.fetch()).status(),200);return route.abort('failed');}
  await route.fulfill({json:stale});
 });
 await master(page).click();
 await page.getByText(/Notification status is unconfirmed/).waitFor();
 assert.equal(control.preferences.enabled,false);assert.equal(control.preferences.revision,8);
 assert.equal(await page.locator('.push-master-switch').isDisabled(),true);
 await page.unroute('**/push/preferences');
 await page.getByRole('button',{name:'Retry checking notifications',exact:true}).click();
 await page.getByText(/Current notification settings checked/).waitFor();
 assert.equal(await master(page).getAttribute('aria-checked'),'false');
 assert.equal(preferenceCalls(calls).filter(c=>c.method==='PUT').length,1);
}));
test('recovery preserves only edited choices over a newer server revision and requires explicit Save',()=>fixture(async({page,calls,control})=>{
 await settings(page);await ready(page);
 await page.getByRole('checkbox',{name:'Hide notification details',exact:true}).check();
 await page.getByRole('checkbox',{name:'Approvals',exact:true}).uncheck();
 await page.route('**/push/preferences',async route=>{
  if(route.request().method()!=='PUT')return route.continue();
  assert.equal((await route.fetch()).status(),200);
  // Another device supersedes the committed privacy choices before reconciliation.
  control.preferences={...defaults(),revision:2,categories:{...defaults().categories,scheduled:false}};
  await route.abort('failed');
 });
 await save(page).click();await page.getByText(/Current notification settings checked/).waitFor();
 assert.equal(await page.getByRole('checkbox',{name:'Hide notification details',exact:true}).isChecked(),true);
 assert.equal(await page.getByRole('checkbox',{name:'Approvals',exact:true}).isChecked(),false);
 assert.equal(await page.getByRole('checkbox',{name:'Scheduled updates',exact:true}).isChecked(),false,'untouched choice must follow the new authoritative revision');
 assert.equal(await save(page).isEnabled(),true);
 assert.equal(preferenceCalls(calls).filter(c=>c.method==='PUT').length,1);
 assert.equal(control.preferences.hide_details,false,'reconciliation must not replay the local intent');
 await page.unroute('**/push/preferences');
 await save(page).click();await page.getByText('Notification preferences saved.',{exact:true}).waitFor();
 const puts=preferenceCalls(calls).filter(c=>c.method==='PUT');
 assert.equal(puts[1].body.revision,2);assert.equal(control.preferences.revision,3);
 assert.equal(control.preferences.hide_details,true);assert.equal(control.preferences.categories.approval,false);
 assert.equal(control.preferences.categories.scheduled,false);
}));

for(const enabled of [true,false])test(`master response loss reconciles committed ${enabled?'disable':'enable'} without saving dirty choices`,()=>fixture(async({page,calls,control})=>{
 control.preferences.enabled=enabled;
 await settings(page);await master(page).waitFor();await losePutResponse(page);
 if(enabled){await ready(page);await page.getByRole('checkbox',{name:'Hide notification details',exact:true}).check();}
 await master(page).click();await page.getByText(/Current notification settings checked/).waitFor();
 assert.equal(control.preferences.enabled,!enabled);
 assert.equal(control.preferences.hide_details,false);
 assert.equal(await master(page).getAttribute('aria-checked'),String(!enabled));
 assert.deepEqual(preferenceCalls(calls).map(c=>c.method),['GET','PUT','GET']);
 if(!enabled)assert.equal(calls.filter(c=>c.p==='/push/subscriptions').length,1);
}));
test('Save transport failure before commit retains local intent at the same revision without automatic replay',()=>fixture(async({page,calls,control})=>{
 await settings(page);await ready(page);
 await page.route('**/push/preferences',route=>route.request().method()==='PUT'?route.abort('failed'):route.continue());
 await page.getByRole('checkbox',{name:'Hide notification details',exact:true}).check();
 await save(page).click();await page.getByText(/Your choices are unsaved/).waitFor();
 assert.equal(control.preferences.revision,0);assert.equal(control.preferences.hide_details,false);
 assert.equal(await page.getByRole('checkbox',{name:'Hide notification details',exact:true}).isChecked(),true);
 assert.equal(await save(page).isEnabled(),true);
 assert.deepEqual(preferenceCalls(calls).map(c=>c.method),['GET','GET']);
 await page.unroute('**/push/preferences');
 await save(page).click();await page.getByText('Notification preferences saved.',{exact:true}).waitFor();
 assert.equal(control.preferences.hide_details,true);assert.equal(control.preferences.revision,1);
}));
for(const invalid of ['json','schema'])test(`committed PUT with unreadable ${invalid} response reconciles rather than reverting`,()=>fixture(async({page,calls,control})=>{
 await settings(page);await ready(page);
 await page.route('**/push/preferences',async route=>{
  if(route.request().method()!=='PUT')return route.continue();
  assert.equal((await route.fetch()).status(),200);
  await route.fulfill({status:200,contentType:'application/json',body:invalid==='json'?'not json':'{}'});
 });
 await page.getByRole('checkbox',{name:'Hide notification details',exact:true}).check();
 await save(page).click();await page.getByText(/Current notification settings checked/).waitFor();
 assert.equal(control.preferences.hide_details,true);
 assert.equal(await page.getByRole('checkbox',{name:'Hide notification details',exact:true}).isChecked(),true);
 assert.deepEqual(preferenceCalls(calls).map(c=>c.method),['GET','PUT','GET']);
}));
for(const departure of ['route','owner','logout','destroy'])for(const httpStatus of [200,401])test(`late recovery ${httpStatus} cannot affect ${departure}`,()=>fixture(async({page,calls,control})=>{
 await settings(page);await ready(page);
 let release,seen;const pending=new Promise(r=>release=r),started=new Promise(r=>seen=r);
 let hold=true;
 await page.route('**/push/preferences',async route=>{
  if(route.request().method()==='PUT'){assert.equal((await route.fetch()).status(),200);return route.abort('failed');}
  if(!hold)return route.continue();hold=false;
  const snapshot=structuredClone(control.preferences);seen();await pending;
  await route.fulfill({status:httpStatus,json:httpStatus===401?{detail:'expired'}:snapshot});
 });
 await page.getByRole('checkbox',{name:'Hide notification details',exact:true}).check();
 await save(page).click();await started;
 if(departure==='route'){
  await page.getByRole('button',{name:'Chats',exact:true}).click();
  control.preferences={...defaults(),revision:2};await settings(page);await ready(page);
 }
 if(departure==='owner'){control.user='new-owner';await page.evaluate(()=>app.start());}
 if(departure==='logout'){
  await page.getByRole('button',{name:'Sign out',exact:true}).click();
  // click() dispatches the async action; it does not await the logout request.
  // Establish the signed-out state before releasing a response meant to be late.
  await page.getByRole('button',{name:'Sign in with a passkey',exact:true}).waitFor();
  await page.waitForFunction(()=>app.state.user===null);
 }
 if(departure==='destroy')await page.evaluate(()=>app.destroy());
 const before=await page.evaluate(()=>({html:document.querySelector('#app').innerHTML,owner:app.state.user?.id,focus:document.activeElement.outerHTML}));
 release();await page.waitForTimeout(120);
 assert.deepEqual(await page.evaluate(()=>({html:document.querySelector('#app').innerHTML,owner:app.state.user?.id,focus:document.activeElement.outerHTML})),before);
 assert.equal(preferenceCalls(calls).filter(c=>c.method==='PUT').length,1);
}));
test('current recovery 401 expires authentication',()=>fixture(async({page,calls})=>{
 await settings(page);await ready(page);
 await page.route('**/push/preferences',async route=>{
  if(route.request().method()==='PUT'){assert.equal((await route.fetch()).status(),200);return route.abort('failed');}
  await route.fulfill({status:401,json:{detail:'expired'}});
 });
 await master(page).click();await page.getByRole('button',{name:'Sign in with a passkey',exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>app.state.user),null);
 assert.equal(preferenceCalls(calls).filter(c=>c.method==='PUT').length,1);
}));
test('pending recovery blocks duplicate mutations and never steals moved focus',()=>fixture(async({page,calls})=>{
 await settings(page);await ready(page);
 let release,seen;const pending=new Promise(r=>release=r),started=new Promise(r=>seen=r);
 await page.route('**/push/preferences',async route=>{
  if(route.request().method()==='PUT'){assert.equal((await route.fetch()).status(),200);return route.abort('failed');}
  seen();await pending;await route.continue();
 });
 await page.getByRole('checkbox',{name:'Hide notification details',exact:true}).check();
 await save(page).click();await started;
 await page.evaluate(()=>{for(let i=0;i<2;i++){document.querySelector('.push-master-switch').click();document.querySelector('.push-preferences button').click();}});
 await page.getByRole('combobox',{name:'Appearance'}).focus();release();
 await page.getByText(/Current notification settings checked/).waitFor();
 assert.equal(await page.getByRole('combobox',{name:'Appearance'}).evaluate(el=>document.activeElement===el),true);
 assert.deepEqual(preferenceCalls(calls).map(c=>c.method),['GET','PUT','GET']);
}));
