import {generatedAssets} from './generated-assets.mjs';
import {artifactURL} from './artifacts.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdtemp,rm,readdir,mkdir,copyFile} from 'node:fs/promises';
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
    if(p==='/auth/logout' && control.logoutDelay)await new Promise(resolve=>setTimeout(resolve,control.logoutDelay));
    if(p==='/push/presence' && call.body.visible && control.presenceHold)await control.presenceHold;
    if(p==='/sessions')data={items:[{id:'session-one',title:'First conversation'},{id:'session-two',title:'Second conversation'}]};
    if(p==='/push/key')data={public_key:'AQID'};
    if(p.endsWith('/messages')){if(control.messagesDelay)await new Promise(resolve=>setTimeout(resolve,control.messagesDelay));data={items:[],total:0};}
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
test('sessions list has a compact heading and Search Filter New toolbar without summary',{timeout:30000},()=>fixture(async({page})=>{
 await page.getByRole('button',{name:'First conversation',exact:true}).waitFor();
 assert.equal(await page.getByRole('heading',{name:'Sessions',exact:true}).count(),1);
 assert.deepEqual(await page.locator('.history-toolbar > button').evaluateAll(nodes=>nodes.map(n=>n.getAttribute('aria-label'))),['Search conversations','Filter conversations','New chat']);
 assert.equal(await page.locator('.list-summary').count(),0);
}));
test('notification choices are local drafts until the explicit Save button is pressed',{timeout:30000},()=>fixture(async({page,calls,control})=>{
 await settings(page);await check(page,'Approvals',true);
 await page.getByRole('checkbox',{name:'Approvals',exact:true}).uncheck();
 await page.getByRole('checkbox',{name:'Hide notification details',exact:true}).check();
 await page.waitForTimeout(100);
 assert.equal(calls.filter(c=>c.method==='PUT').length,0,'editing choices must not autosave');
 assert.equal(control.preferences.categories.approval,true);
 await page.getByRole('button',{name:'Save notification preferences',exact:true}).click();
 await page.getByText('Notification preferences saved.',{exact:true}).waitFor();
 assert.equal(calls.filter(c=>c.method==='PUT').length,1);
 assert.equal(control.preferences.categories.approval,false);assert.equal(control.preferences.hide_details,true);
}));
test('notification master is one effective switch above choices and never saves dirty choices',{timeout:30000},()=>fixture(async({page,control,calls})=>{
 control.preferences.enabled=false;await settings(page);
 const master=page.getByRole('switch',{name:'Notifications on this device',exact:true});await master.waitFor();
 assert.equal(await master.getAttribute('aria-checked'),'false');
 assert.equal(await page.getByRole('checkbox').count(),0);
 assert.equal(await page.getByRole('button',{name:'Enable notifications',exact:true}).count(),0);
 await master.click();await page.getByText('Notifications enabled.',{exact:true}).waitFor();
 assert.equal(await master.getAttribute('aria-checked'),'true');await check(page,'Approvals',true);
 assert.equal(await page.getByRole('checkbox',{name:'Push notifications on this device',exact:true}).count(),0);
 await page.getByRole('checkbox',{name:'Approvals',exact:true}).uncheck();
 await page.getByRole('checkbox',{name:'Hide notification details',exact:true}).check();
 await master.click();await page.getByText('Notifications disabled.',{exact:true}).waitFor();
 assert.equal(control.preferences.enabled,false);assert.equal(control.preferences.categories.approval,true);assert.equal(control.preferences.hide_details,false);
 assert.equal(await page.getByRole('checkbox').count(),0);
 await master.click();await page.getByText('Notifications enabled.',{exact:true}).waitFor();
 await check(page,'Approvals',false);await check(page,'Hide notification details',true);
 await page.getByRole('button',{name:'Save notification preferences',exact:true}).click();
 await page.getByText('Notification preferences saved.',{exact:true}).waitFor();
 assert.equal(control.preferences.categories.approval,false);assert.equal(control.preferences.hide_details,true);
 const layout=await page.evaluate(()=>({master:document.querySelector('.push-master').getBoundingClientRect().bottom,choices:document.querySelector('.push-preference').getBoundingClientRect().top,border:getComputedStyle(document.querySelector('.push-master')).borderWidth}));
 assert.ok(layout.master<=layout.choices);assert.notEqual(layout.border,'0px');
 assert.ok(calls.some(c=>c.p==='/push/subscriptions' && c.method==='POST'));
}));
test('mobile Sessions and notification Settings have readable light dark master controls',{timeout:60000},()=>fixture(async({page})=>{
 await mkdir(fileURLToPath(artifactURL()),{recursive:true});
 const capture=async(name,locator=page)=>{
  const path=fileURLToPath(artifactURL(name));await locator.screenshot({path});
  if(process.env.HERMES_SETTINGS_PROOF_DIR){await mkdir(process.env.HERMES_SETTINGS_PROOF_DIR,{recursive:true,mode:0o700});await copyFile(path,process.env.HERMES_SETTINGS_PROOF_DIR+'/'+name);}
 };
 for(const theme of ['light','dark']){
  await settings(page);await page.getByRole('combobox',{name:'Appearance'}).selectOption(theme);
  await page.getByRole('button',{name:'Chats',exact:true}).click();await page.getByRole('heading',{name:'Sessions',exact:true}).waitFor();
  await capture(`sessions-390-${theme}.png`);
  await settings(page);let master=await checkMaster(page,theme==='light');
  if(theme==='dark'){await master.click();await page.getByText('Notifications enabled.',{exact:true}).waitFor();}
  // Measure the settled state, not an intermediate frame of the shared button background transition.
  await master.evaluate(async el=>{getComputedStyle(el).backgroundColor;await Promise.all(el.getAnimations().map(animation=>animation.finished));});
  const visual=await master.evaluate(el=>{
   const style=getComputedStyle(el),box=el.getBoundingClientRect(),luma=color=>color.match(/[\d.]+/g).slice(0,3).map(Number).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
   const a=luma(style.color),b=luma(style.backgroundColor);return {contrast:(Math.max(a,b)+.05)/(Math.min(a,b)+.05),width:box.width,height:box.height,overflow:document.documentElement.scrollWidth>innerWidth};
  });
  assert.ok(visual.contrast>=4.5,`${theme} master text needs AA contrast: ${JSON.stringify(visual)}`);assert.ok(visual.width>=44 && visual.height>=44);assert.equal(visual.overflow,false);
  await page.locator('.notification-settings h2').evaluate(el=>el.scrollIntoView({block:'start'}));
  await capture(`settings-enabled-390-${theme}.png`);
  await page.getByRole('button',{name:'Save notification preferences',exact:true}).scrollIntoViewIfNeeded();
  await capture(`settings-preferences-390-${theme}.png`);
  await master.click();await page.getByText('Notifications disabled.',{exact:true}).waitFor();assert.equal(await page.getByRole('checkbox').count(),0);
  await page.locator('.notification-settings h2').evaluate(el=>el.scrollIntoView({block:'start'}));
  await capture(`settings-disabled-390-${theme}.png`);
 }
}));
test('failed notification disable remains enabled through pending error and explicit retry',{timeout:30000},()=>fixture(async({page,control})=>{
 await settings(page);const master=await checkMaster(page,true);
 let release;control.hold=new Promise(r=>release=r);control.putError=true;
 await master.click();await page.getByText('Disabling notifications…',{exact:true}).waitFor();
 assert.equal(await master.isDisabled(),true);assert.equal(await master.getAttribute('aria-checked'),'true');
 assert.equal(await page.getByRole('checkbox',{name:'Approvals',exact:true}).isDisabled(),true);
 control.hold=null;release();await page.getByText(/Could not change notifications\./).waitFor();
 await checkMaster(page,true);assert.equal(control.preferences.enabled,true);
 control.putError=false;await page.getByRole('button',{name:'Retry changing notifications',exact:true}).click();
 await page.getByText('Notifications disabled.',{exact:true}).waitFor();await checkMaster(page,false);
}));
test('retrying a failed disable retains disable intent after browser permission changes',{timeout:30000},()=>fixture(async({page,control,calls})=>{
 await settings(page);await checkMaster(page,true);control.putError=true;
 await page.getByRole('switch').click();await page.getByText(/Could not change notifications\./).waitFor();
 control.putError=false;await page.evaluate(()=>{fixturePush.permission='denied';});
 await page.getByRole('button',{name:'Retry changing notifications',exact:true}).click();
 await page.waitForTimeout(100);
 assert.equal(control.preferences.enabled,false,'retry must still turn the saved policy OFF, not reinterpret the gesture as enabling');
 assert.equal(await page.evaluate(()=>fixturePush.prompts),0,'retrying disable must never request notification permission');
 assert.deepEqual(calls.filter(c=>c.method==='PUT').map(c=>c.body.enabled),[false,false]);
}));
test('denied permission never invents enabled delivery or changes preferences',{timeout:30000},()=>fixture(async({page,calls,control})=>{
 await device(page);await page.evaluate(()=>{fixturePush.permission='denied';});await settings(page);await checkMaster(page,false);
 assert.equal(await page.evaluate(()=>fixturePush.prompts),0);
 await page.getByRole('switch').click();await page.getByText(/Notifications were not allowed/).waitFor();
 await checkMaster(page,false);assert.equal(await page.getByRole('checkbox').count(),0);assert.equal(control.preferences.revision,0);
 assert.equal(calls.filter(c=>c.method==='POST' || c.method==='PUT').length,0);
}));
for(const departure of ['route','owner','logout','destroy'])test(`notification setup stops after stale permission across ${departure}`,{timeout:30000},()=>fixture(async({page,calls,control})=>{
 await device(page);await page.evaluate(()=>{fixturePush.permission='default';Notification.requestPermission=()=>new Promise(resolve=>window.releasePermission=()=>{fixturePush.permission='granted';resolve('granted');});});
 await settings(page);await checkMaster(page,false);await page.getByRole('switch').click();await page.getByText('Enabling notifications…',{exact:true}).waitFor();
 if(departure==='route')await page.getByRole('button',{name:'Chats',exact:true}).click();
 if(departure==='owner'){control.user='new-owner';await page.evaluate(()=>app.start());}
 if(departure==='logout'){
  // Keep logout pending long enough to expose releasing permission on click alone.
  // The late permission below is meant to cross completed logout, not its request.
  control.logoutDelay=1000;
  await page.getByRole('button',{name:'Sign out',exact:true}).click();
  await page.getByRole('button',{name:'Sign in with a passkey',exact:true}).waitFor();
  await page.waitForFunction(()=>app.state.user===null);
 }
 if(departure==='destroy')await page.evaluate(()=>app.destroy());
 await page.evaluate(()=>releasePermission());await page.waitForTimeout(100);
 assert.equal(calls.filter(c=>c.p==='/push/subscriptions' || c.p==='/push/preferences' && c.method==='PUT').length,0);
 assert.equal(await page.getByText('Notifications enabled.',{exact:true}).count(),0);
}));
test('failed preference reads fail closed and rejected saves retain local choices for explicit retry',{timeout:30000},()=>fixture(async({page,control,calls})=>{
 control.getError=true;await settings(page);
 await page.getByText('Could not load notification preferences.',{exact:true}).waitFor();
 assert.equal(await page.getByRole('checkbox').count(),0,'no editable fabricated defaults');
 control.getError=false;
 await page.getByRole('button',{name:'Retry loading preferences',exact:true}).click();
 await check(page,'Request completions',true);
 control.putError=true;
 await page.getByRole('checkbox',{name:'Request completions',exact:true}).click();
 await saveChoices(page);
 await page.getByText('Could not save notification preferences. Your choices are kept locally; review and retry saving.',{exact:true}).waitFor();
 assert.equal(await page.getByRole('checkbox',{name:'Request completions',exact:true}).isChecked(),false);
 assert.equal(control.preferences.categories.completion,true,'definite rejection did not commit the local choice');
 control.putError=false;
 await page.getByRole('button',{name:'Retry saving preferences',exact:true}).click();
 await page.getByText('Notification preferences saved.',{exact:true}).waitFor();
 await check(page,'Request completions',false);
 assert.equal(calls.filter(c=>c.method==='PUT').length,2);
 // A malformed server response must not invent valid preferences either.
 control.preferences={enabled:true,categories:{completion:true},hide_details:false};
 await settings(page);await page.getByText('Could not load notification preferences.',{exact:true}).waitFor();
 assert.equal(await page.getByRole('checkbox').count(),0);
}));
test('preference responses and detached controls cannot act after owner changes',{timeout:30000},()=>fixture(async({page,control,calls})=>{
 await settings(page);await check(page,'Approvals',true);
 await page.evaluate(()=>{window.oldPush=document.querySelector('.push-preferences');});
 let release;control.hold=new Promise(r=>release=r);
 await page.getByRole('checkbox',{name:'Approvals',exact:true}).uncheck();
 await saveChoices(page);
 await page.getByText('Saving notification preferences…',{exact:true}).waitFor();
 control.user='fixture-second-owner';await page.evaluate(()=>app.start());
 control.hold=null;release();await page.waitForTimeout(100);
 await page.evaluate(()=>{const input=oldPush.querySelector('input');input.checked=false;input.dispatchEvent(new Event('change'));});
 assert.equal(calls.filter(c=>c.method==='PUT').length,1);
 assert.equal(await page.getByText('Notification preferences saved.',{exact:true}).count(),0);
 await settings(page);await check(page,'Approvals',false);
 await page.evaluate(()=>app.destroy());
 const before=calls.length;await page.evaluate(()=>{const input=document.querySelector('.push-preferences input');input.dispatchEvent(new Event('change'));});
 await page.waitForTimeout(80);assert.equal(calls.length,before);
}));
test('mobile preference labels keep touch targets and checkboxes in a bounded row',{timeout:30000},()=>fixture(async({page})=>{
 await settings(page);await checkMaster(page,true);
 const boxes=await page.locator('.push-preference').evaluateAll(rows=>rows.map(row=>({height:row.getBoundingClientRect().height,input:row.querySelector('input').getBoundingClientRect().width,width:row.getBoundingClientRect().width})));
 assert.ok(boxes.every(b=>b.height>=44 && b.input<=28 && b.width<=390),'scoped mobile rows need 44px targets and compact checkbox widths');
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
}));
test('conversation presence heartbeats only while focused and revokes on leave',{timeout:30000},()=>fixture(async({page,calls})=>{
 await page.clock.install();
 const presence=()=>calls.filter(c=>c.p==='/push/presence');
 assert.equal(presence().length,0);
 await page.getByRole('button',{name:/First conversation/}).click();
 await page.waitForFunction(()=>document.querySelector('.messages'));
 await page.waitForTimeout(100);
 assert.equal(presence().length,1,'opening a verified owned conversation announces visible presence');
 assert.deepEqual({...presence()[0].body,client_id:'tab'},{client_id:'tab',session_id:'session-one',visible:true,sequence:1});
 assert.ok(presence()[0].body.client_id.length>=20 && presence()[0].body.client_id.length<=80);
 assert.equal(presence()[0].csrf,'fixture-csrf');
 await page.clock.fastForward(15000);await page.waitForTimeout(50);
 assert.equal(presence().length,2,'15 second heartbeat');
 await page.evaluate(()=>dispatchEvent(new Event('blur')));await page.waitForTimeout(50);
 assert.equal(presence().at(-1).body.visible,false);
 const hiddenCount=presence().length;await page.clock.fastForward(60000);await page.waitForTimeout(50);assert.equal(presence().length,hiddenCount);
 await page.evaluate(()=>dispatchEvent(new Event('focus')));await page.waitForTimeout(50);
 assert.equal(presence().at(-1).body.visible,true);
 await page.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});document.dispatchEvent(new Event('visibilitychange'));});await page.waitForTimeout(50);
 assert.equal(presence().at(-1).body.visible,false);
 const visibilityCount=presence().length;await page.clock.fastForward(60000);await page.waitForTimeout(50);assert.equal(presence().length,visibilityCount);
 await page.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});document.dispatchEvent(new Event('visibilitychange'));});await page.waitForTimeout(50);
 assert.equal(presence().at(-1).body.visible,true);
 await settings(page);await check(page,'Approvals',true);assert.equal(presence().at(-1).body.visible,false);
 const genericCount=presence().length;await page.clock.fastForward(60000);await page.waitForTimeout(50);assert.equal(presence().length,genericCount,'generic settings never suppress conversation notifications');
 await page.getByRole('button',{name:'Chats',exact:true}).click();await page.getByRole('button',{name:/Second conversation/}).click();await page.waitForTimeout(80);
 assert.equal(presence().at(-1).body.session_id,'session-two');assert.equal(presence().at(-1).body.visible,true);
 const client=presence()[0].body.client_id;assert.ok(presence().every(c=>c.body.client_id===client));
 assert.deepEqual(presence().map(c=>c.body.sequence),presence().map((_,i)=>i+1));
 await page.evaluate(()=>app.destroy());await page.waitForTimeout(50);assert.equal(presence().at(-1).body.visible,false);
 const final=presence().length;await page.evaluate(()=>{dispatchEvent(new Event('focus'));document.dispatchEvent(new Event('visibilitychange'));});await page.clock.fastForward(60000);await page.waitForTimeout(50);assert.equal(presence().length,final);
 assert.ok(calls.every(c=>c.method!=='POST' || c.p==='/push/presence'),'presence never submits, navigates, or changes history');
}));
async function pushWorker({page,context,sw}) {
 await page.evaluate(async sw=>{await navigator.serviceWorker.register('./'+sw,{scope:'/hermes/'});await navigator.serviceWorker.ready;},sw);
 const worker=context.serviceWorkers()[0];assert.ok(worker,'real generated service worker activated');
 await worker.evaluate(()=>{self.notifications=[];self.registration.showNotification=async(title,options)=>{notifications.push({title,...options});};});
 return {worker,push:async data=>worker.evaluate(async data=>{const event=new Event('push');event.data={json:()=>data};let pending;event.waitUntil=p=>pending=p;self.dispatchEvent(event);await pending;return notifications.at(-1);},data)};
}
test('generated service worker renders validated informative previews with inbox identity',{timeout:30000},()=>fixture(async fixture=>{
 const {push,worker}=await pushWorker(fixture);
 const payload={title:'Request complete',body:'Your document is ready for review.',inbox_id:'notice-123',url:fixture.url+'?inbox=notice-123'};
 const displayed=await push(payload);
 assert.equal(displayed.title,payload.title);assert.equal(displayed.body,payload.body);
 assert.equal(displayed.tag,'hermes-notice-123');assert.equal(displayed.data.url,payload.url);
 assert.equal(displayed.icon,new URL('./'+(await readdir(fixture.dir+'/icons')).find(n=>/^icon-192\./.test(n)),fixture.url+'icons/').pathname);
 assert.equal(displayed.image,undefined);assert.equal(displayed.actions,undefined);
 const privacy=await push({...payload,title:'Hermes',body:'Open Hermes to read your update.'});
 assert.equal(privacy.title,'Hermes');assert.equal(privacy.body,'Open Hermes to read your update.');
 const cached=await worker.evaluate(async()=>{const all=[];for(const name of await caches.keys())for(const req of await (await caches.open(name)).keys())all.push(req.url);return all;});
 assert.ok(cached.length>10);assert.ok(cached.every(url=>!url.includes('app-api')),'worker never caches authentication or conversation responses');
}));
test('two tabs reject stale preference revisions without overwriting privacy or master',{timeout:60000},()=>fixture(async({page,context,mount,control,calls})=>{
 const second=await context.newPage();await mount(second);
 await page.bringToFront();await settings(page);await second.bringToFront();await settings(second);
 await check(page,'Hide notification details',false);await check(second,'Hide notification details',false); await page.bringToFront();
 await page.getByRole('checkbox',{name:'Hide notification details',exact:true}).click();
 await saveChoices(page);
 await page.getByText('Notification preferences saved.',{exact:true}).waitFor();
 await second.bringToFront();
 await second.getByRole('switch',{name:'Notifications on this device',exact:true}).click();
 await second.getByText('Notification preferences changed elsewhere. Current settings loaded; review and try your change again.',{exact:true}).waitFor();
 await check(second,'Hide notification details',true);await checkMaster(second,true);
 assert.equal(await second.getByRole('button',{name:'Retry saving preferences',exact:true}).count(),0,'no blind retry of stale full payload');
 assert.equal(calls.filter(c=>c.method==='PUT').length,2); 
 await second.getByRole('switch',{name:'Notifications on this device',exact:true}).click();
 await second.getByText('Notifications disabled.',{exact:true}).waitFor();
 await page.bringToFront();
 await page.getByRole('checkbox',{name:'Approvals',exact:true}).click();
 await saveChoices(page);
 await page.getByText('Notification preferences changed elsewhere. Current settings loaded; review and try your change again.',{exact:true}).waitFor();
 await checkMaster(page,false);assert.equal(await page.getByRole('checkbox').count(),0);assert.equal(await page.locator('input[name=hide_details]').isChecked(),true);assert.equal(await page.locator('input[name=approval]').isChecked(),true);
 assert.equal(control.preferences.revision,2);assert.equal(control.preferences.enabled,false);assert.equal(control.preferences.hide_details,true);
 assert.match(await page.locator('.notification-settings').innerText(),/Changes apply to notifications not yet sent\. Already sent or displayed previews cannot be recalled\./);
 for(const revision of [-1,1.5,'2',null]){control.preferences={...defaults(),revision};await settings(page);await page.getByText('Could not load notification preferences.',{exact:true}).waitFor();assert.equal(await page.getByRole('checkbox').count(),0);}
}));
test('service worker rejects malformed previews and binds navigation and tag to one safe inbox ID',{timeout:30000},()=>fixture(async fixture=>{
 const {push,worker}=await pushWorker(fixture),base=fixture.url;
 for(const data of [null,[],42,'legacy',{}, {title:'Safe',body:'x'.repeat(181)}, {title:'x'.repeat(101),body:'Safe'}, {title:123,body:'Safe'}, {title:'Safe',body:'<img src=x>'}, {title:'Safe',body:'[Click](https://evil.test)'}, {title:'Safe',body:'https://evil.test/path'}, {title:'Safe\u202e',body:'Spoof'}, {title:'Safe',body:'line\ncontrol'}]){
  const shown=await push(data && typeof data==='object' && !Array.isArray(data) ? {...data,inbox_id:'notice-123'} : data);assert.equal(shown.title,'Hermes');assert.equal(shown.body,'Open Hermes to read your update.');
 }
 for(const inbox_id of [{toString:'bad'},123,'x'.repeat(129),'../private','bad\nID']){
  const shown=await push({title:'Safe',body:'Safe',inbox_id,url:base+'?inbox=other'});
  assert.equal(shown.tag,undefined,'malformed ID cannot control notification grouping');assert.equal(shown.data.url,base);
 }
 for(const url of ['https://evil.test/hermes/?inbox=other',base+'?inbox=other#approve',base+'../app-api/approvals/a',base+'?inbox=notice-123&approve=yes']){
  const shown=await push({title:'Safe',body:'Safe',inbox_id:'notice-123',url});
  assert.equal(shown.tag,'hermes-notice-123');assert.equal(shown.data.url,base+'?inbox=notice-123','tag and destination refer to the same inbox receipt');
 }
 const clicks=await worker.evaluate(async()=>{
  const calls=[];self.clients.matchAll=async()=>[{url:self.location.origin+'/hermes/',navigate:async url=>calls.push(['navigate',url]),focus:async()=>calls.push(['focus'])}];
  self.clients.openWindow=async url=>calls.push(['open',url]);
  self.fetch=async()=>{throw new Error('notification click must not fetch or mutate');};
  for(const url of ['https://evil.test/hermes/?inbox=n',self.location.origin+'/hermes/?inbox=notice-123&approve=1#send']){
   const event=new Event('notificationclick');event.notification={data:{url},close:()=>calls.push(['close'])};let pending;event.waitUntil=p=>pending=p;self.dispatchEvent(event);await pending;
  }
  return calls;
 });
 assert.deepEqual(clicks,[['close'],['navigate',base],['focus'],['close'],['navigate',base+'?inbox=notice-123'],['focus']]);
}));
test('pending presence cannot renew during account revalidation or after a new owner',{timeout:30000},()=>fixture(async({page,control,calls})=>{
 await page.clock.install();
 let finishPresence,finishAuth;control.presenceHold=new Promise(r=>finishPresence=r);
 await page.getByRole('button',{name:/First conversation/}).click();await page.waitForTimeout(80);
 const presence=()=>calls.filter(c=>c.p==='/push/presence');assert.equal(presence().at(-1)?.body.visible,true);
 control.authHold=new Promise(r=>finishAuth=r);
 await page.evaluate(()=>{void app.start();});await page.waitForTimeout(80);
 assert.equal(presence().at(-1).body.visible,false,'revalidation must revoke old presence before awaiting new identity');
 assert.ok(presence().at(-1).body.sequence>presence()[0].body.sequence,'revoke overtakes in-flight show with monotonic sequence');
 const count=presence().length;await page.clock.fastForward(60000);await page.waitForTimeout(80);assert.equal(presence().length,count);
 control.user='fixture-new-owner';control.authHold=null;finishAuth();control.presenceHold=null;finishPresence();
 await page.waitForFunction(()=>app.state.user?.id==='fixture-new-owner');
 await page.evaluate(()=>dispatchEvent(new Event('focus')));await page.clock.fastForward(60000);await page.waitForTimeout(80);assert.equal(presence().length,count,'late responses cannot restore the previous account lease');
}));
test('two tab presence IDs are independent and pagehide logout never renew a lease',{timeout:30000},()=>fixture(async({page,context,mount,calls,control})=>{
 control.messagesDelay=1200; // Keep the old click-plus-60ms race reproducible.
 // A click dispatch does not await the owned conversation read or its presence POST.
 await presenceResponse(page,{session_id:'session-one',visible:true},()=>page.getByRole('button',{name:/First conversation/}).click());
 const presence=()=>calls.filter(c=>c.p==='/push/presence');const first=presence().find(c=>c.body.visible)?.body.client_id;assert.ok(first);
 const second=await context.newPage();await mount(second);await second.bringToFront();
 await presenceResponse(second,{session_id:'session-two',visible:true},()=>second.getByRole('button',{name:/Second conversation/}).click());
 const secondId=presence().find(c=>c.body.session_id==='session-two' && c.body.visible)?.body.client_id;assert.ok(secondId);assert.notEqual(first,secondId);
 await presenceResponse(second,{client_id:secondId,visible:false},()=>second.evaluate(()=>dispatchEvent(new Event('pagehide'))));
 assert.equal(presence().at(-1).body.client_id,secondId);assert.equal(presence().at(-1).body.visible,false);
 await presenceResponse(second,{client_id:secondId,visible:true},()=>second.evaluate(()=>dispatchEvent(new Event('pageshow'))));assert.equal(presence().at(-1).body.visible,true);
 await settings(second);await check(second,'Approvals',true);await second.getByRole('button',{name:'Sign out',exact:true}).click();await second.getByRole('button',{name:'Sign in with a passkey',exact:true}).waitFor();
 const count=presence().filter(c=>c.body.client_id===secondId).length;
 await second.evaluate(()=>{dispatchEvent(new Event('focus'));document.dispatchEvent(new Event('visibilitychange'));});await second.waitForTimeout(60);
 assert.equal(presence().filter(c=>c.body.client_id===secondId).length,count);
 const logout=calls.findIndex(c=>c.p==='/auth/logout');assert.ok(logout>0);assert.ok(calls.slice(0,logout).some(c=>c.p==='/push/presence' && c.body.client_id===secondId && !c.body.visible));
 for(const id of [first,secondId]){const seq=presence().filter(c=>c.body.client_id===id).map(c=>c.body.sequence);assert.deepEqual(seq,[...seq].sort((a,b)=>a-b));assert.equal(new Set(seq).size,seq.length);}
}));
test('presence reload preserves tab high-water sequence while copied tabs and devices get separate identity',{timeout:30000},()=>fixture(async({page,context,mount,calls,control})=>{
 await page.getByRole('button',{name:/First conversation/}).click();await page.waitForTimeout(80);
 const presence=()=>calls.filter(c=>c.p==='/push/presence');const first=presence().at(-1).body;
 await page.reload();await mount(page);await page.getByRole('button',{name:/First conversation/}).click();await page.waitForTimeout(80);
 const resumed=presence().at(-1).body;assert.equal(resumed.client_id,first.client_id,'reload must reuse tab identity, not consume another server client slot');assert.ok(resumed.sequence>first.sequence);
 const storage=await page.evaluate(()=>Object.fromEntries(Object.keys(sessionStorage).map(k=>[k,sessionStorage.getItem(k)])));
 const cloned=await context.newPage();await cloned.goto(page.url());await cloned.evaluate(storage=>{for(const [k,v] of Object.entries(storage))sessionStorage.setItem(k,v);},storage);await mount(cloned);await cloned.bringToFront();
 await cloned.getByRole('button',{name:/Second conversation/}).click();await cloned.waitForTimeout(80);
 assert.notEqual(presence().at(-1).body.client_id,first.client_id,'a copied sessionStorage tab cannot share an active lease identity');
 await page.bringToFront();control.csrf='different-device-csrf';await page.evaluate(()=>app.start());await page.getByRole('button',{name:/First conversation/}).click();await page.waitForTimeout(80);
 assert.notEqual(presence().at(-1).body.client_id,first.client_id,'same account with a new authenticated device must not reuse the previous device scope');
 assert.equal(await page.evaluate(()=>Object.keys(localStorage).some(k=>k.includes('push-presence'))),false);
}));
test('hung presence is bounded to one abortable request and ten second teardown',{timeout:30000},()=>fixture(async({page,ui,control})=>{
 control.messagesDelay=1200; // Lease timing starts after conversation readiness, not click dispatch.
 await page.clock.install();
 await page.evaluate(()=>{
  const original=window.fetch;window.presenceTransport={active:0,max:0,requests:[],aborted:0};
  window.fetch=(url,options)=>{
   if(!String(url).endsWith('/push/presence'))return original(url,options);
   const state=presenceTransport;state.active++;state.max=Math.max(state.max,state.active);state.requests.push(JSON.parse(options.body));
   return new Promise((resolve,reject)=>{options.signal?.addEventListener('abort',()=>{state.active--;state.aborted++;reject(new DOMException('Aborted','AbortError'));},{once:true});});
  };
 });
 await page.evaluate(async ui=>{app.destroy();const {mountApp}=await import('./'+ui);const {createAPI}=await import('./api.'+ui.split('.')[1]+'.mjs');window.app=await mountApp(document,createAPI(),window);},ui);
 await page.getByRole('button',{name:/First conversation/}).click();
 // Wait for the intercepted transport to start before measuring its unchanged deadline.
 await page.waitForFunction(()=>presenceTransport.active===1);
 assert.equal(await page.evaluate(()=>presenceTransport.active),1);
 await page.clock.fastForward(10000);await page.waitForTimeout(40);
 assert.equal(await page.evaluate(()=>presenceTransport.active),0,'hung heartbeat is aborted at 10 seconds');
 await page.clock.fastForward(5000);await page.waitForTimeout(40);assert.equal(await page.evaluate(()=>presenceTransport.active),1);
 await page.evaluate(()=>dispatchEvent(new Event('blur')));assert.equal(await page.evaluate(()=>presenceTransport.requests.at(-1).visible),false);
 assert.equal(await page.evaluate(()=>presenceTransport.max),1,'superseded request is aborted before new visibility state dispatch');
 await settings(page);await check(page,'Approvals',true);await page.clock.fastForward(10000);assert.equal(await page.evaluate(()=>presenceTransport.active),0);
 await page.getByRole('button',{name:'Chats',exact:true}).click();await page.getByRole('button',{name:/First conversation/}).click();
 await page.evaluate(()=>dispatchEvent(new Event('focus')));
 await page.waitForFunction(()=>presenceTransport.active===1 && presenceTransport.requests.at(-1)?.visible===true);
 await page.evaluate(()=>app.destroy());
 assert.equal(await page.evaluate(()=>presenceTransport.requests.at(-1).visible),false);
 await page.clock.fastForward(10000);assert.equal(await page.evaluate(()=>presenceTransport.active),0);
 const count=await page.evaluate(()=>presenceTransport.requests.length);await page.clock.fastForward(60000);await page.evaluate(()=>dispatchEvent(new Event('focus')));assert.equal(await page.evaluate(()=>presenceTransport.requests.length),count);
 assert.equal(await page.evaluate(()=>presenceTransport.max),1);
}));
test('effective notification status requires browser permission and subscription without load prompts',{timeout:30000},()=>fixture(async({page,calls})=>{
 await device(page);
 for(const value of [{permission:'denied',subscribed:true},{permission:'granted',subscribed:false}]){
  await page.evaluate(value=>Object.assign(fixturePush,value),value);await settings(page);await checkMaster(page,false);
  assert.equal(await page.getByRole('checkbox').count(),0);
  assert.equal(await page.evaluate(()=>fixturePush.prompts),0);
 }
 await page.getByRole('switch',{name:'Notifications on this device',exact:true}).click();
 await page.getByText('Notifications enabled.',{exact:true}).waitFor();await checkMaster(page,true);
 assert.ok(calls.some(c=>c.p==='/push/subscriptions' && c.method==='POST'));
}));
async function presenceResponse(page,expected,action){
 const response=page.waitForResponse(response=>{
  const request=response.request();
  if(!response.url().endsWith('/push/presence') || request.method()!=='POST')return false;
  const body=request.postDataJSON();return Object.entries(expected).every(([key,value])=>body[key]===value);
 },{timeout:2500});
 await Promise.all([response,action()]);
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
const saveChoices=page=>page.getByRole('button',{name:'Save notification preferences',exact:true}).click();
const checkMaster=async(page,value)=>{const control=page.getByRole('switch',{name:'Notifications on this device',exact:true});await page.waitForFunction(()=>{const n=document.querySelector('[role=switch]');return n && !n.disabled;});assert.equal(await control.getAttribute('aria-checked'),String(value));return control;};
const check=async(page,name,value)=>{const box=page.getByRole('checkbox',{name,exact:true});await box.waitFor();assert.equal(await box.isChecked(),value);return box;};
test('device preferences expose independent explicitly saved controls without permission prompts',{timeout:30000},()=>fixture(async({page,calls,control})=>{
 await settings(page);await checkMaster(page,true);
 const labels={completion:'Request completions',approval:'Approvals',attention:'Action needed or failures',scheduled:'Scheduled updates',operational:'Serious server alerts'};
 for(const [key,label] of Object.entries(labels)){
  await (await check(page,label,true)).uncheck();await saveChoices(page);
  await page.getByText('Notification preferences saved.',{exact:true}).waitFor();
  assert.equal(control.preferences.categories[key],false);assert.equal(control.preferences.enabled,true);
 }
 await (await check(page,'Hide notification details',false)).check();await saveChoices(page);
 await page.getByText('Notification preferences saved.',{exact:true}).waitFor();
 assert.match(await page.locator('.push-preferences').innerText(),/Inbox.*WhatsApp.*unchanged/i);
 assert.match(await page.locator('.push-preferences').innerText(),/lock screen/i);
 await (await checkMaster(page,true)).click();await page.getByText('Notifications disabled.',{exact:true}).waitFor();
 assert.deepEqual(control.preferences,{revision:7,enabled:false,categories:Object.fromEntries(Object.keys(labels).map(k=>[k,false])),hide_details:true});
 assert.equal(await page.evaluate(()=>fixturePush.prompts),0);
 assert.ok(calls.filter(c=>c.method==='PUT').every(c=>c.csrf==='fixture-csrf' && Object.keys(c.body.categories).length===5));
 assert.ok(calls.every(c=>!['/push/subscriptions','/push/test'].includes(c.p)));
 assert.equal(await page.getByRole('switch').count(),1);assert.equal(await page.getByRole('checkbox').count(),0);
 assert.equal(await page.getByRole('button',{name:'Send test notification',exact:true}).isDisabled(),true);
}));
