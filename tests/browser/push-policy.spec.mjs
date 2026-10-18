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
const responseBatch=(page,path,count)=>new Promise(resolve=>{
 const listener=response=>{
  if(new URL(response.url()).pathname.endsWith(path) && response.request().method()==='GET' && --count===0){
   page.off('response',listener);resolve();
  }
 };
 page.on('response',listener);
});
async function fixture(run){
 const tmp=await mkdtemp((process.env.TMPDIR || '/tmp')+'/b-');let server,browser;
 const dir=generatedAssets(tmp),calls=[],control={preferences:defaults(),getError:false,putError:false,hold:null,user:'fixture-owner',notificationTarget:null,approvals:[],approvalHold:null,approvalDetail:null,approvalDetailHold:null,approvalDetailStatus:200,decisionHold:null,decisionStatus:200,activeRun:null,runEventStreams:[],runEventSequence:0};
 try{
  const names=await readdir(dir),ui=names.find(n=>/^ui\.[a-f0-9]+\.mjs$/.test(n)),sw=names.find(n=>/^sw\.[a-f0-9]+\.js$/.test(n));
  assert.ok(ui && sw,'tests use generated release graph');
  server=createServer(async(req,res)=>{
   const requestURL=new URL(req.url,'http://fixture'),path=requestURL.pathname;
   if(path.startsWith('/hermes/app-api/')){
    let raw='';for await(const part of req)raw+=part;
    const p=path.slice('/hermes/app-api'.length),call={p,query:requestURL.search,method:req.method,body:raw?JSON.parse(raw):null,csrf:req.headers['x-csrf-token']};calls.push(call);
    if(/^\/runs\/[^/]+\/events$/.test(p)){
     res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache','Connection':'keep-alive'});
     res.write(': connected\n\n');
     control.runEventStreams.push(res);
     res.on('close',()=>{control.runEventStreams=control.runEventStreams.filter(stream=>stream!==res);});
     return;
    }
    let data={items:[]},status=200;
    if(p==='/auth/me'){if(control.authHold)await control.authHold;data={user:{id:control.user,status:'ready'},csrf_token:control.csrf || 'fixture-csrf'};}
    if(p==='/auth/logout' && control.logoutDelay)await new Promise(resolve=>setTimeout(resolve,control.logoutDelay));
    if(p==='/push/presence' && call.body.visible && control.presenceHold)await control.presenceHold;
    if(p==='/approvals'){if(control.approvalHold)await control.approvalHold;data={items:control.approvals};}
    if(p.startsWith('/approvals/') && p.endsWith('/decision') && req.method==='POST'){
     if(control.decisionHold)await control.decisionHold;
     status=control.decisionStatus;data=status===200?{status:'resolved'}:{detail:'Synthetic unavailable'};
    }else if(p.startsWith('/approvals/') && req.method==='GET'){
     const detail=control.approvalDetail;
     if(control.approvalDetailHold)await control.approvalDetailHold;
     if(control.approvalDetailLost){res.destroy();return;}
     if(detail?.id===decodeURIComponent(p.split('/').at(-1))){data=detail;status=control.approvalDetailStatus;}
    }
    if(p==='/auth/verify/options')data={challenge_id:'fixture-challenge',publicKey:{challenge:'AQ'}};
    if(p==='/auth/verify/finish')data={verified:true};
    if(p==='/sessions')data={items:[{id:'session-one',title:'First conversation'},{id:'session-two',title:'Second conversation'}]};
    if(p==='/inbox/notice-target/target' && control.notificationTarget)data=control.notificationTarget;
    if(control.notificationTarget && p===`/sessions/${control.notificationTarget.session_id}`)data={id:control.notificationTarget.session_id,title:'Exact notification chat'};
    if(p==='/push/key')data={public_key:'AQID'};
    if((control.notificationTarget && p===`/sessions/${control.notificationTarget.session_id}/messages`)
       || (control.realHistory && p==='/sessions/session-one/messages')){
     if(control.realHistory){
      const offset=requestURL.searchParams.get('offset');
      data=offset===null?control.realHistory.latest:control.realHistory.pages[offset]||{items:[],offset:Number(offset),total:control.realHistory.latest.total};
     }else data=requestURL.searchParams.has('offset')
      ? {items:[{id:1,role:'user',content:'Original request',run_id:'older-run',session_id:'session-one'}],offset:0,total:11}
      : {items:[{id:20,role:'assistant',content:'Newer response',run_id:'newer-run',session_id:'session-one'}],offset:10,total:11};
    }else if(p.endsWith('/messages')){if(control.messagesDelay)await new Promise(resolve=>setTimeout(resolve,control.messagesDelay));data={items:[],total:0};}
    if(p.endsWith('/messages') && control.activeRun)data={...data,run:control.activeRun};
    if(p.endsWith('/controls') && control.activeRun)data={steering:true,attempts:[]};
    if(control.activeRun && p===`/runs/${control.activeRun.id}`)data=control.activeRun;
    else if(p.startsWith('/runs/') && control.realHistory)data=control.realHistory.runs[p.slice('/runs/'.length)]||{items:[]};
    else if(p==='/runs/older-run')data={id:'older-run',session_id:'session-one',input:'Original request',status:'completed',output:'Exact notification result',created_at:1,updated_at:2};
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
  async function mount(page,target=''){
   await page.goto(url+'styles.'+ui.split('.')[1]+'.css'+target);
   await page.evaluate(async({ui})=>{document.body.innerHTML='<div id="app"></div>';const style=document.createElement('link');style.rel='stylesheet';style.href='./styles.'+ui.split('.')[1]+'.css';document.head.append(style);const {createAPI}=await import('./api.'+ui.split('.')[1]+'.mjs');const {mountApp}=await import('./'+ui);window.app=await mountApp(document,createAPI(),window);},{ui});
  }
  await mount(page);
  await run({page,context,url,calls,control,ui,sw,dir,mount,disconnectRunStreams(){
   for(const stream of [...control.runEventStreams])stream.end();
  },emitRunEvent(name,data,id=++control.runEventSequence){
   for(const stream of control.runEventStreams)if(!stream.writableEnded)
    stream.write(`id: ${id}\nevent: ${name}\ndata: ${JSON.stringify(data)}\n\n`);
  }});
  assert.deepEqual(errors,[]);
 }finally{await browser?.close();server?.closeAllConnections();await new Promise(r=>server?server.close(r):r());await rm(tmp,{recursive:true,force:true});}
}
const approvalRecord=(overrides={})=>({
 id:'approval-1',run_id:'approval-run',request_id:'request-1',session_id:'session-one',
 run_session_id:'session-one',status:'pending',title:'Review synthetic action',
 command:'Run a synthetic command',target:'Synthetic target',expires_at:Date.now()/1000+600,
 executable:true,choices:['once','deny'],...overrides
});
const approvalTarget={
 kind:'approval',session_id:'session-one',run_id:'approval-run',
 run_session_id:'session-one',approval_id:'approval-1',request_id:'request-1'
};
const approvalHistory=()=>({
 latest:{items:[{id:20,role:'assistant',content:'Newer response',run_id:'newer-run',session_id:'session-one'}],offset:10,total:11},
 pages:{0:{items:[{id:1,role:'user',content:'Original request',run_id:'approval-run',session_id:'session-one'}],offset:0,total:11}},
 runs:{}
});
async function holdPasskey(page){
 await page.evaluate(()=>{
  window.PublicKeyCredential=function(){};
  let resolve,reject;
  window.passkeyRequest={started:false};
  Object.defineProperty(navigator,'credentials',{configurable:true,value:{get:()=>{passkeyRequest.started=true;return new Promise((yes,no)=>{resolve=yes;reject=no;});}}});
  window.finishPasskey=()=>resolve({id:'fixture',rawId:new Uint8Array([1]).buffer,type:'public-key',response:{clientDataJSON:new Uint8Array([1]).buffer},getClientExtensionResults:()=>({})});
  window.cancelPasskey=()=>reject(new DOMException('Passkey cancelled','NotAllowedError'));
 });
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
 assert.equal(displayed.tag,'hermes-notice-123');assert.equal(displayed.data.url,fixture.url+'?notification=notice-123');
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
  assert.equal(shown.tag,'hermes-notice-123');assert.equal(shown.data.url,base+'?notification=notice-123','tag and destination refer to the same opaque notification');
 }
 const clicks=await worker.evaluate(async()=>{
  const calls=[];self.clients.matchAll=async()=>[{url:self.location.origin+'/hermes/',navigate:async url=>calls.push(['navigate',url]),focus:async()=>calls.push(['focus'])}];
  self.clients.openWindow=async url=>calls.push(['open',url]);
  self.fetch=async()=>{throw new Error('notification click must not fetch or mutate');};
  for(const url of ['https://evil.test/hermes/?inbox=n',self.location.origin+'/hermes/?inbox=notice-123&approve=1#send',self.location.origin+'/hermes/?notification=notice-123&approve=1']){
   const event=new Event('notificationclick');event.notification={data:{url},close:()=>calls.push(['close'])};let pending;event.waitUntil=p=>pending=p;self.dispatchEvent(event);await pending;
  }
  return calls;
 });
 assert.deepEqual(clicks,[['close'],['navigate',base],['focus'],['close'],['navigate',base+'?inbox=notice-123'],['focus'],['close'],['navigate',base+'?notification=notice-123'],['focus']]);
}));
test('generated app restores opaque and legacy notification links to the exact older request',{timeout:30000},()=>fixture(async({page,control,calls,mount})=>{
 control.notificationTarget={kind:'request',session_id:'session-one',run_id:'older-run',run_session_id:'session-one'};
 for(const target of ['?notification=notice-target','?inbox=notice-target']){
  await mount(page,target);
  await page.locator('[data-notification-run="older-run"]').waitFor();
  const card=page.locator('[data-notification-run="older-run"]');
  assert.match(await card.innerText(),/Original request/);assert.match(await card.innerText(),/Exact notification result/);
  assert.equal(await page.getByRole('heading',{name:'Exact notification chat'}).count(),1);
  assert.ok(calls.some(call=>call.p==='/inbox/notice-target/target'));
  assert.ok(calls.some(call=>call.p==='/sessions/session-one/messages' && call.method==='GET'));
  assert.ok(calls.some(call=>call.p==='/runs/older-run'));
  assert.equal(await page.locator('.bottom-nav [aria-current="page"]').getAttribute('data-view'),'chats');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 }
}));
test('generated request routing uses composed native+journal anchors beyond a history page',{timeout:60000},()=>fixture(async({page,control,calls,mount})=>{
 const python=process.env.HERMES_TEST_PYTHON||repo+'.venv/bin/python';
 const history=JSON.parse(execFileSync(python,[repo+'tests/browser/request_target_history_fixture.py'],{cwd:repo,encoding:'utf8'}));
 assert.equal(history.latest.items.some(item=>item.run_id===history.old_run_id),false,'the target starts beyond the latest page');
 assert.equal(history.latest.run.id,history.new_run_id,'the latest page has a newer active run');
 control.realHistory=history;
 control.notificationTarget={kind:'request',session_id:history.session_id,run_id:history.old_run_id,run_session_id:history.session_id};
 await mount(page,'?notification=notice-target');
 const target=page.locator(`[data-notification-run="${history.old_run_id}"]`);
 await target.waitFor();
 const oldRequest=page.locator('.user-message').filter({hasText:'Old notification request'}).first();
 const newerRequest=page.locator('.user-message').filter({hasText:'New active request'}).first();
 await oldRequest.waitFor();await newerRequest.waitFor();
 assert.match(await target.innerText(),/Old notification result/);
 assert.equal(await target.evaluate(node=>node.previousElementSibling?.textContent.includes('Old notification request')),true);
 assert.equal(await newerRequest.evaluate((newer,node)=>Boolean(node.compareDocumentPosition(newer)&Node.DOCUMENT_POSITION_FOLLOWING),await target.elementHandle()),true);
 assert.ok(calls.filter(call=>call.p===`/sessions/${history.session_id}/messages`&&call.query.includes('offset=')).length>=2,'the target required multiple older pages');
 assert.deepEqual(await page.locator('.user-message').filter({hasText:'Old notification request'}).count(),1);
}));
test('generated chat stays usable while approval reconciliation is unresolved and applies late results',{timeout:30000},()=>fixture(async({page,control})=>{
 let release;control.approvalHold=new Promise(resolve=>release=resolve);
 control.approvals=[approvalRecord()];
 control.activeRun={id:'active-run',session_id:'session-one',status:'running',input:'Synthetic active request',created_at:1};
 await page.getByRole('button',{name:/First conversation/}).click();
 await page.getByRole('textbox',{name:'Message Hermes'}).waitFor();
 await page.getByRole('button',{name:'Stop run'}).waitFor();
 assert.equal(await page.locator('.approval-card').count(),0,'unresolved approval lookup must not block chat');
 release();await page.getByRole('heading',{name:'Approval required'}).waitFor();
 assert.equal(await page.getByRole('textbox',{name:'Message Hermes'}).isVisible(),true);
}));

test('approval notification loads the matching older turn before focusing its card',{timeout:30000},()=>fixture(async({page,control,calls,mount})=>{
 control.notificationTarget=approvalTarget;control.approvals=[approvalRecord()];control.approvalDetail=approvalRecord();control.realHistory=approvalHistory();
 await mount(page,'?notification=notice-target');
 const card=page.locator('[data-approval-id="approval-1"]');
 await card.waitFor();await page.waitForTimeout(50);
 assert.ok(calls.some(call=>call.p==='/sessions/session-one/messages' && call.query.includes('offset=0')),
   'approval notification must load its matching older turn');
 await page.locator('.messages [data-history-run="approval-run"]:not(.approval-card)').waitFor();
 assert.equal(await card.evaluate(node=>node.previousElementSibling?.dataset.historyRun),'approval-run');
 assert.equal(calls.filter(call=>call.p.endsWith('/decision')).length,0);
}));

test('generated long approval details are keyboard-scrollable before decision',{timeout:30000},()=>fixture(async({page,control,calls,mount})=>{
 const endMarker='END OF SYNTHETIC APPROVAL DETAILS';
 const command=Array.from({length:100},(_,index)=>`synthetic action detail line ${index+1}: review this safe fixture before approval`).join('\n')+`\n${endMarker}`;
 const item=approvalRecord({command});
 control.notificationTarget=approvalTarget;control.approvals=[item];control.approvalDetail=item;
 await mount(page,'?notification=notice-target');
 const preview=page.getByRole('region',{name:'Approval action details'});
 await preview.waitFor();
 assert.equal(await preview.getAttribute('tabindex'),'0','the full action text must be keyboard focusable');
 await page.keyboard.press('Tab');
 assert.equal(await preview.evaluate(node=>node===document.activeElement),true,'Tab reaches the action text before decision controls');
 assert.equal(await preview.evaluate(node=>getComputedStyle(node).outlineWidth),'3px','keyboard focus is visible');
 const before=await preview.evaluate(node=>({top:node.scrollTop,height:node.scrollHeight,client:node.clientHeight}));
 assert.ok(before.height>before.client,'the long action text has a scrollable viewport');
 for(let step=0;step<Math.ceil((before.height-before.client)/8);step++)await page.keyboard.press('ArrowDown');
 assert.ok(await preview.evaluate((node,top)=>node.scrollTop>top,before.top),'ArrowDown moves the focused action text');
 const endState=await preview.evaluate((node,marker)=>{
 const text=node.firstChild,offset=text.textContent.lastIndexOf(marker),range=document.createRange();
 range.setStart(text,offset);range.setEnd(text,offset+marker.length);
 const textRect=range.getBoundingClientRect(),box=node.getBoundingClientRect();
 return {atEnd:node.scrollTop+node.clientHeight>=node.scrollHeight-1,
   visible:textRect.top>=box.top && textRect.bottom<=box.bottom,
   scrollTop:node.scrollTop,max:node.scrollHeight-node.clientHeight,
   textTop:textRect.top,textBottom:textRect.bottom,boxTop:box.top,boxBottom:box.bottom};
 },endMarker);
 assert.ok(endState.atEnd&&endState.visible,`End reveals the last action text inside the scroll region: ${JSON.stringify(endState)}`);
 await page.keyboard.press('Tab');
 assert.equal(await page.getByRole('button',{name:'Approve once'}).evaluate(node=>node===document.activeElement),
 true,'Tab continues to the explicit decision control');
 assert.equal(calls.filter(call=>call.p.endsWith('/decision')).length,0,'keyboard review does not decide');
}));

test('manual older-history loading repositions an already rendered approval card',{timeout:30000},()=>fixture(async({page,control})=>{
 control.approvals=[approvalRecord()];control.realHistory=approvalHistory();
 await page.getByRole('button',{name:'First conversation',exact:true}).click();
 const card=page.locator('[data-approval-id="approval-1"]');
 await card.waitFor();
 await page.getByRole('button',{name:'Load older messages',exact:true}).click();
 await page.locator('.messages [data-history-run="approval-run"]:not(.approval-card)').waitFor();
 assert.equal(await card.evaluate(node=>node.previousElementSibling?.dataset.historyRun),'approval-run');
}));

test('generated approval expiry rearms an early callback and a capped distant deadline',{timeout:30000},()=>fixture(async({page,control,mount,calls})=>{
 for(const distance of [10000,2147483647+10000]){
  const now=1800000000000;
  control.notificationTarget=approvalTarget;
  control.approvals=[approvalRecord({expires_at:(now+distance)/1000})];
  control.approvalDetail=control.approvals[0];
  await page.addInitScript(({now})=>{
   window.expiryClock={now,next:0,timers:new Map()};
   Date.now=()=>expiryClock.now;
   window.setTimeout=(callback,delay)=>{const id=++expiryClock.next;expiryClock.timers.set(id,{callback,delay});return id;};
   window.clearTimeout=id=>expiryClock.timers.delete(id);
   window.fireExpiry=delay=>{
    const entry=[...expiryClock.timers].find(([,timer])=>timer.delay===delay);
    if(!entry)throw new Error(`No armed expiry timer for ${delay}ms`);
    expiryClock.timers.delete(entry[0]);entry[1].callback();
   };
  },{now});
  await mount(page,'?notification=notice-target');
  const card=page.locator('[data-approval-id="approval-1"]');
  await card.getByRole('button',{name:'Approve once'}).waitFor();
  const handle=await card.elementHandle();
  await page.evaluate(({distance,now})=>{
   expiryClock.now=now+(distance>2147483647?2147483647:distance-1);
   fireExpiry(Math.min(distance,2147483647));
  },{distance,now});
  assert.equal(await handle.evaluate(node=>node.isConnected),true);
  assert.equal(await card.getByRole('heading').innerText(),'Approval required');
  await page.evaluate(({distance,now})=>{
   const remaining=now+distance-expiryClock.now;expiryClock.now=now+distance;fireExpiry(remaining);
  },{distance,now});
  assert.equal(await card.getByRole('heading').innerText(),'Expired');
  assert.equal(await card.getByRole('button').count(),0);
  await page.evaluate(()=>app.destroy());
  await page.waitForFunction(()=>expiryClock.timers.size===0);
  assert.equal(await page.evaluate(()=>expiryClock.timers.size),0);
 }
 assert.equal(calls.filter(call=>call.p.endsWith('/decision')).length,0);
}));

test('generated expired non-pending approval details retain status through same-owner reentry',{timeout:30000},()=>fixture(async({page,control,mount,calls})=>{
 control.notificationTarget=approvalTarget;
 for(const [status,label] of Object.entries({resolved:'Resolved',resolved_external:'No longer pending',cancelled:'Cancelled',sending:'Decision pending',unknown:'Decision status unknown'})){
  control.approvals=[];control.approvalDetail=approvalRecord({status,expires_at:1});
  await mount(page,'?notification=notice-target');
  const card=page.locator('[data-approval-id="approval-1"]');
  await card.getByRole('heading',{name:label,exact:true}).waitFor();
  assert.equal(await card.getByRole('button').count(),0);
  await settings(page);await page.getByRole('button',{name:'Sign out',exact:true}).click();
  await page.getByRole('button',{name:'Sign in with a passkey',exact:true}).waitFor();
  await page.evaluate(()=>app.start());
  await card.getByRole('heading',{name:label,exact:true}).waitFor();
  assert.equal(await card.getByRole('button').count(),0);
 }
 assert.equal(calls.filter(call=>call.p.endsWith('/decision')).length,0);
}));

test('generated run status refresh removes an externally answered approval and preserves its sibling',{timeout:30000},()=>fixture(async({page,control,calls,emitRunEvent})=>{
 const answered=approvalRecord(),sibling=approvalRecord({id:'approval-2',request_id:'request-2'});
 control.approvals=[answered,sibling];
 control.activeRun={id:answered.run_id,session_id:answered.session_id,status:'waiting_for_approval',input:'Synthetic request',created_at:1};
 await page.getByRole('button',{name:'First conversation',exact:true}).click();
 const connectDeadline=Date.now()+2500;
 while(!control.runEventStreams.length&&Date.now()<connectDeadline)await page.waitForTimeout(10);
 assert.ok(control.runEventStreams.length,`the generated chat must open its run event stream: ${JSON.stringify(calls.map(call=>call.p))}`);
 const answeredCard=page.locator(`[data-approval-id="${answered.id}"]`),siblingCard=page.locator(`[data-approval-id="${sibling.id}"]`);
 await answeredCard.getByRole('button',{name:'Approve once'}).waitFor();
 await siblingCard.getByRole('button',{name:'Approve once'}).waitFor();
 await page.getByRole('textbox',{name:'Message Hermes'}).focus();
 const initialReads=calls.filter(call=>call.p==='/approvals'&&call.method==='GET').length;
 control.approvals=[sibling];
 emitRunEvent('status',{status:'waiting_for_approval'},'external-approval-response');
 const deadline=Date.now()+2500;
 while(calls.filter(call=>call.p==='/approvals'&&call.method==='GET').length===initialReads&&Date.now()<deadline)
  await page.waitForTimeout(10);
 await answeredCard.getByRole('heading',{name:'Approval unavailable',exact:true}).waitFor();
 assert.equal(await answeredCard.getByRole('button').count(),0,'the externally answered card has no controls');
 assert.equal(await siblingCard.getByRole('button').count(),2,'the still-pending sibling remains actionable');
 assert.equal(await page.locator('.approval-notice').innerText(),'1 approval require your review.');
 assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('aria-label')),'Message Hermes','refresh does not steal focus');
 const refreshedReads=calls.filter(call=>call.p==='/approvals'&&call.method==='GET').length;
 emitRunEvent('status',{status:'waiting_for_approval'},'external-approval-response');
 await page.waitForTimeout(100);
 assert.equal(calls.filter(call=>call.p==='/approvals'&&call.method==='GET').length,refreshedReads,
  'replayed status IDs do not cause another list request');
 assert.equal(calls.filter(call=>call.p.endsWith('/decision')).length,0,'status reconciliation never decides an approval');
}));

test('generated unavailable approval stays retired after list refresh and approval replay',{timeout:30000},()=>fixture(async({page,control,calls,mount,emitRunEvent})=>{
 const target=approvalRecord(),sibling=approvalRecord({id:'approval-2',request_id:'request-2'});
 control.notificationTarget=approvalTarget;control.approvals=[target,sibling];
 control.activeRun={id:target.run_id,session_id:target.session_id,status:'waiting_for_approval'};
 control.approvalDetail=target;control.approvalDetailStatus=409;
 await mount(page,'?notification=notice-target');
 const connectDeadline=Date.now()+2500;
 while(!control.runEventStreams.length&&Date.now()<connectDeadline)await page.waitForTimeout(10);
 assert.ok(control.runEventStreams.length,'generated chat opens its run event stream');
 const card=page.locator(`[data-approval-id="${target.id}"]`),other=page.locator(`[data-approval-id="${sibling.id}"]`);
 await card.getByRole('heading',{name:'Approval unavailable',exact:true}).waitFor();
 await other.getByRole('button',{name:'Approve once'}).waitFor();
 const before=calls.filter(call=>call.p==='/approvals'&&call.method==='GET').length;
 control.approvals=[
  ...Array.from({length:100},(_,index)=>approvalRecord({
   id:`expired-${index}`,request_id:`expired-request-${index}`,expires_at:1
  })),
  target,sibling
 ];
 emitRunEvent('status',{status:'waiting_for_approval'},'refresh-after-unavailable');
 const deadline=Date.now()+2500;
 while(calls.filter(call=>call.p==='/approvals'&&call.method==='GET').length===before&&Date.now()<deadline)
  await page.waitForTimeout(10);
 await page.waitForTimeout(30);
 assert.equal(calls.filter(call=>call.p==='/approvals'&&call.method==='GET').length,before+1,
  'status refresh reads the same pending database row once');
 assert.equal(await card.getByRole('heading').innerText(),'Approval unavailable');
 emitRunEvent('approval',target,'replayed-unavailable-approval');
 await page.waitForTimeout(80);
 assert.equal(await page.locator(`[data-approval-id="${target.id}"]`).count(),1);
 assert.equal(await card.getByRole('button').count(),0);
 assert.equal(await other.getByRole('button').count(),2,'a sibling with a distinct request remains actionable');
 assert.equal(await page.locator('.approval-notice').innerText(),'1 approval require your review.');
 assert.equal(calls.filter(call=>call.p==='/approvals'&&call.method==='GET').length,before+1,
  'replayed approval events do not start another list read');
 assert.equal(calls.filter(call=>call.p.endsWith('/decision')).length,0);
}));

test('generated pending-list card loses controls after its held detail returns deletion-fence 409',{timeout:30000},()=>fixture(async({page,control,calls,mount})=>{
 const target=approvalRecord(),sibling=approvalRecord({id:'approval-2',request_id:'request-2'});
 control.notificationTarget=approvalTarget;
 control.approvals=[target,sibling];control.approvalDetail=target;control.approvalDetailStatus=409;
 let release;control.approvalDetailHold=new Promise(resolve=>release=resolve);
 const listResponse=page.waitForResponse(response=>response.url().endsWith('/approvals')&&response.request().method()==='GET');
 await mount(page,'?notification=notice-target');
 await listResponse;
 const card=page.locator(`[data-approval-id="${target.id}"]`),other=page.locator(`[data-approval-id="${sibling.id}"]`);
 await card.getByRole('button',{name:'Approve once'}).waitFor();
 await other.getByRole('button',{name:'Approve once'}).waitFor();
 const detailPath='/approvals/approval-1';
 const detailResponse=page.waitForResponse(response=>response.url().endsWith(detailPath)&&response.request().method()==='GET');
 const deadline=Date.now()+2500;
 while(!calls.some(call=>call.p===detailPath&&call.method==='GET')&&Date.now()<deadline)await page.waitForTimeout(10);
 assert.ok(calls.some(call=>call.p===detailPath&&call.method==='GET'),'the exact target detail read is held');
 control.approvalDetailHold=null;release();await detailResponse;
 await card.getByRole('heading',{name:'Approval unavailable',exact:true}).waitFor();
 assert.equal(await card.getByRole('button').count(),0);
 assert.equal(await other.getByRole('button').count(),2,'the other approval is unchanged');
 assert.equal(await page.locator('.approval-notice').innerText(),'1 approval require your review.');
 assert.equal(calls.filter(call=>call.p.endsWith('/decision')).length,0);
}));

test('generated terminal run finish wins over held pending detail but fresh final detail replaces it',{timeout:30000},()=>fixture(async({page,control,calls,mount,emitRunEvent})=>{
 const pending=approvalRecord();
 control.notificationTarget=approvalTarget;control.approvals=[pending];
 control.activeRun={id:pending.run_id,session_id:pending.session_id,status:'running'};
 control.approvalDetail=pending;
 let releaseDetail;control.approvalDetailHold=new Promise(resolve=>releaseDetail=resolve);
 await mount(page,'?notification=notice-target');
 const connectDeadline=Date.now()+2500;
 while(!control.runEventStreams.length&&Date.now()<connectDeadline)await page.waitForTimeout(10);
 assert.ok(control.runEventStreams.length,'generated chat opens its run event stream');
 const card=page.locator(`[data-approval-id="${pending.id}"]`),detailPath='/approvals/approval-1';
 await card.getByRole('button',{name:'Approve once'}).waitFor();
 const deadline=Date.now()+2500;
 while(!calls.some(call=>call.p===detailPath&&call.method==='GET')&&Date.now()<deadline)await page.waitForTimeout(10);
 assert.ok(calls.some(call=>call.p===detailPath&&call.method==='GET'),'initial pending detail is held');
 emitRunEvent('status',{status:'cancelled'},'terminal-before-pending-detail');
 await card.getByRole('heading',{name:'Approval unavailable',exact:true}).waitFor();
 assert.equal(await card.getByRole('button').count(),0);
 assert.equal(await page.locator('.approval-notice').isHidden(),true);
 const staleResponse=page.waitForResponse(response=>response.url().endsWith(detailPath)&&response.request().method()==='GET');
 control.approvalDetailHold=null;releaseDetail();await staleResponse;
 await page.waitForTimeout(30);
 assert.equal(await card.getByRole('heading').innerText(),'Approval unavailable',
  'the held pending detail cannot replace terminal-run retirement');
 assert.equal(await card.getByRole('button').count(),0);
 assert.equal(await page.locator('.approval-notice').isHidden(),true);

 control.approvalDetail=approvalRecord({status:'resolved',executable:false,choices:[]});
 control.approvals=[];
 await settings(page);await page.getByRole('button',{name:'Sign out',exact:true}).click();
 await page.getByRole('button',{name:'Sign in with a passkey',exact:true}).waitFor();
 await page.evaluate(()=>app.start());
 await card.getByRole('heading',{name:'Resolved',exact:true}).waitFor();
 assert.equal(await card.getByRole('button').count(),0,'a fresh final detail remains authoritative');
 assert.equal(calls.filter(call=>call.p.endsWith('/decision')).length,0);
}));

test('generated disconnected-stream polling reconciles an externally answered approval and keeps its sibling',{timeout:30000},()=>fixture(async({page,control,calls,disconnectRunStreams})=>{
 const answered=approvalRecord(),sibling=approvalRecord({id:'approval-2',request_id:'request-2'});
 control.approvals=[answered,sibling];
 control.activeRun={id:answered.run_id,session_id:answered.session_id,status:'waiting_for_approval'};
 await page.getByRole('button',{name:'First conversation',exact:true}).click();
 const connectDeadline=Date.now()+2500;
 while(!control.runEventStreams.length&&Date.now()<connectDeadline)await page.waitForTimeout(10);
 assert.ok(control.runEventStreams.length,'generated chat opens its run event stream');
 const answeredCard=page.locator(`[data-approval-id="${answered.id}"]`),siblingCard=page.locator(`[data-approval-id="${sibling.id}"]`);
 await answeredCard.getByRole('button',{name:'Approve once'}).waitFor();
 await siblingCard.getByRole('button',{name:'Approve once'}).waitFor();
 await page.getByRole('textbox',{name:'Message Hermes'}).focus();
 disconnectRunStreams();
 const disconnectDeadline=Date.now()+1000;
 while(control.runEventStreams.length&&Date.now()<disconnectDeadline)await page.waitForTimeout(10);
 assert.equal(control.runEventStreams.length,0,'poll recovery runs with no connected event stream');
 const initialLists=calls.filter(call=>call.p==='/approvals'&&call.method==='GET').length;
 const initialPolls=calls.filter(call=>call.p===`/runs/${answered.run_id}`&&call.method==='GET').length;
 control.approvals=[sibling];
 const pollResponse=page.waitForResponse(response=>response.url().endsWith(`/runs/${answered.run_id}`)&&response.request().method()==='GET');
 await page.evaluate(()=>dispatchEvent(new Event('pageshow')));
 await pollResponse;
 const deadline=Date.now()+2500;
 while(calls.filter(call=>call.p==='/approvals'&&call.method==='GET').length===initialLists&&Date.now()<deadline)
  await page.waitForTimeout(10);
 await answeredCard.getByRole('heading',{name:'Approval unavailable',exact:true}).waitFor();
 assert.equal(calls.filter(call=>call.p===`/runs/${answered.run_id}`&&call.method==='GET').length,initialPolls+1);
 assert.equal(calls.filter(call=>call.p==='/approvals'&&call.method==='GET').length,initialLists+1,
  'one waiting poll performs one bounded approval reconciliation');
 assert.equal(await answeredCard.getByRole('button').count(),0);
 assert.equal(await siblingCard.getByRole('button').count(),2);
 assert.equal(await page.locator('.approval-notice').innerText(),'1 approval require your review.');
 assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('aria-label')),'Message Hermes',
  'polling does not move reader focus');
 await page.waitForTimeout(100);
 assert.equal(calls.filter(call=>call.p==='/approvals'&&call.method==='GET').length,initialLists+1,
  'reconciliation does not create an approval-read loop');
 assert.equal(calls.filter(call=>call.p.endsWith('/decision')).length,0);
}));

test('generated second detail GET cannot replay a successfully answered approval',{timeout:30000},()=>fixture(async({page,control,mount,calls})=>{
 control.notificationTarget=approvalTarget;control.approvals=[];
 const item=approvalRecord();control.approvalDetail=item;
 control.activeRun={id:item.run_id,session_id:item.session_id,status:'waiting_for_approval'};
 await page.addInitScript(()=>{
  window.EventSource=class {
   constructor(){this.listeners={};window.approvalStream=this;}
   addEventListener(name,fn){this.listeners[name]=fn;}
   emit(name,data){this.listeners[name]?.({data:JSON.stringify(data)});}
   close(){}
  };
 });
 let releaseFirst,releaseSecond,reads=0;
 const first=new Promise(resolve=>releaseFirst=resolve),second=new Promise(resolve=>releaseSecond=resolve);
 await page.route('**/app-api/approvals/approval-1',async route=>{
  await (++reads===1?first:second);
  await route.fulfill({json:item});
 });
 const listResponse=page.waitForResponse(response=>response.url().endsWith('/approvals'));
 await mount(page,'?notification=notice-target');await listResponse;
 await page.waitForTimeout(20);
 releaseFirst();
 const deadline=Date.now()+2500;
 while(reads<2 && Date.now()<deadline)await page.waitForTimeout(10);
 assert.equal(reads,2,'an empty list forces the second exact-detail GET');
 let releaseRefresh;control.approvalHold=new Promise(resolve=>releaseRefresh=resolve);
 await page.evaluate(item=>approvalStream.emit('approval',item),item);
 const card=page.locator('[data-approval-id="approval-1"]');
 await card.getByRole('button',{name:'Approve once'}).waitFor();
 await holdPasskey(page);
 await card.getByRole('button',{name:'Approve once'}).click();
 await page.waitForFunction(()=>window.passkeyRequest?.started);
 await page.evaluate(()=>finishPasskey());
 await card.getByRole('heading',{name:'Resolved',exact:true}).waitFor();
 const response=page.waitForResponse(response=>response.url().endsWith('/approvals/approval-1'));
 releaseSecond();await response;
 await page.waitForTimeout(50);
 assert.equal(await card.getByRole('heading').innerText(),'Resolved');
 assert.equal(await card.getByRole('button').count(),0);
 assert.equal(calls.filter(call=>call.p==='/approvals/approval-1/decision').length,1);
 await page.evaluate(()=>app.destroy());releaseRefresh();
}));

test('held approval details cannot replace an ambiguous decision with sending',{timeout:30000},()=>fixture(async({page,control,mount,calls})=>{
 const pending=approvalRecord(),sending=approvalRecord({status:'sending',executable:false,choices:[]});
 control.notificationTarget=approvalTarget;control.approvals=[pending];control.approvalDetail=sending;
 let releaseDetail,releaseDecision;
 control.approvalDetailHold=new Promise(resolve=>releaseDetail=resolve);
 control.decisionHold=new Promise(resolve=>releaseDecision=resolve);control.decisionStatus=503;
 await mount(page,'?notification=notice-target');
 const card=page.locator('.approval-card');
 await card.getByRole('button',{name:'Approve once'}).waitFor();
 const decisionPath='/approvals/approval-1/decision',detailPath='/approvals/approval-1';
 const deadline=Date.now()+5000;
 while(!calls.some(call=>call.p===detailPath && call.method==='GET') && Date.now()<deadline)await page.waitForTimeout(10);
 assert.ok(calls.some(call=>call.p===detailPath && call.method==='GET'),'the initial detail read is held');
 await holdPasskey(page);await card.getByRole('button',{name:'Approve once'}).click();
 await page.waitForFunction(()=>window.passkeyRequest?.started);
 await page.evaluate(()=>finishPasskey());
 while(!calls.some(call=>call.p===decisionPath) && Date.now()<deadline)await page.waitForTimeout(10);
 assert.ok(calls.some(call=>call.p===decisionPath),'verified decision reaches the API');
 releaseDecision();
 await card.getByRole('heading',{name:'Decision status unknown',exact:true}).waitFor();
 const staleResponse=page.waitForResponse(response=>response.url().endsWith(detailPath) && response.request().method()==='GET');
 control.approvalDetailHold=null;releaseDetail();await staleResponse;
 await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,0)));
 assert.equal(await card.getByRole('heading').innerText(),'Decision status unknown');
 assert.match(await card.locator('.inline-error').innerText(),/Synthetic unavailable/);
 assert.equal(await card.getByRole('button').count(),0,'a stale sending detail cannot restore controls');
 assert.equal(calls.filter(call=>call.p===decisionPath).length,1,'uncertainty never replays the decision');
 await page.evaluate(()=>app.destroy());
}));

for(const failure of ['503','lost response'])for(const [initial,final] of [['sending','resolved'],['unknown','cancelled']])
test(`missing ${initial} notification approval recovers after ${failure} without reload or decision`,{timeout:30000},()=>fixture(async({page,control,mount,calls})=>{
 control.notificationTarget=approvalTarget;control.approvals=[];
 control.approvalDetail=approvalRecord({status:initial,executable:false,choices:[]});
 control.approvalDetailStatus=failure==='503'?503:200;control.approvalDetailLost=failure==='lost response';
 await mount(page,'?notification=notice-target');
 const placeholder=page.getByRole('region',{name:'Approval status',exact:true});
 await page.getByRole('heading',{name:'Approval status unavailable',exact:true}).waitFor();
 await placeholder.getByRole('button',{name:'Retry status',exact:true}).waitFor();
 const detailPath='/approvals/approval-1',reads=()=>calls.filter(call=>call.p===detailPath && call.method==='GET').length;
 assert.ok(reads()>=1);
 assert.equal(await page.locator('[data-approval-id]').count(),0,'an empty pending list must not invent a card');
 await placeholder.getByRole('button',{name:'Retry status',exact:true}).click();
 await placeholder.getByRole('button',{name:'Retry status',exact:true}).waitFor();
 await placeholder.getByRole('heading',{name:'Approval status unavailable',exact:true}).waitFor();
 const before=reads();
 let release;control.approvalDetailHold=new Promise(resolve=>release=resolve);
 control.approvalDetailStatus=200;control.approvalDetailLost=false;
 const response=page.waitForResponse(res=>res.url().endsWith(detailPath) && res.request().method()==='GET');
 const request=page.waitForRequest(req=>req.url().endsWith(detailPath) && req.method()==='GET');
 await placeholder.getByRole('button',{name:'Retry status',exact:true}).evaluate(button=>{
  for(let i=0;i<4;i++)button.click();
 });
 await request;
 await page.getByRole('heading',{name:'Checking approval status',exact:true}).waitFor();
 assert.equal(reads(),before+1,'repeated clicks start only one held read');
 control.approvalDetailHold=null;release();await response;
 const card=page.locator('[data-approval-id="approval-1"]');
 await card.getByRole('heading',{name:initial==='sending'?'Decision pending':'Decision status unknown',exact:true}).waitFor();
 assert.equal(await page.locator('.approval-card').count(),1,'recovery replaces the placeholder with exactly one card');
 assert.equal(await card.getByRole('button').count(),0);
 control.approvalDetail=approvalRecord({status:final,executable:false,choices:[]});
 await page.evaluate(()=>dispatchEvent(new Event('focus')));
 await card.getByRole('heading',{name:final==='resolved'?'Resolved':'Cancelled',exact:true}).waitFor();
 assert.equal(await card.getByRole('button').count(),0);
 assert.equal(calls.filter(call=>call.p.includes('/decision') || call.p.startsWith('/auth/verify')
  || call.p.startsWith('/runs/') && call.method==='POST').length,0,'status recovery is read-only');
 await page.evaluate(()=>app.destroy());
}));

for(const outcome of ['detail','failure'])test(`late approval retry ${outcome} cannot update a newer session`,{timeout:30000},()=>fixture(async({page,control,mount,calls})=>{
 control.notificationTarget=approvalTarget;control.approvals=[];
 control.approvalDetail=approvalRecord({status:'unknown',executable:false,choices:[]});control.approvalDetailStatus=503;
 await mount(page,'?notification=notice-target');
 const retry=page.getByRole('button',{name:'Retry status',exact:true});await retry.waitFor();
 let release;control.approvalDetailHold=new Promise(resolve=>release=resolve);
 control.approvalDetailStatus=outcome==='detail'?200:503;
 const request=page.waitForRequest(req=>req.url().endsWith('/app-api/approvals/approval-1'));
 const response=page.waitForResponse(res=>res.url().endsWith('/app-api/approvals/approval-1'));
 await retry.click();await request;
 await page.getByRole('button',{name:'Chats',exact:true}).click();
 await page.getByRole('button',{name:/Second conversation/}).click();
 await page.getByRole('heading',{name:'Second conversation'}).waitFor();
 control.approvalDetailHold=null;release();await response;
 await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,0)));
 assert.equal(await page.locator('.approval-card').count(),0);
 assert.equal(await page.getByRole('button',{name:'Retry status',exact:true}).count(),0);
 assert.equal(calls.filter(call=>call.p.includes('/decision') || call.p.startsWith('/auth/verify')).length,0);
 await page.evaluate(()=>app.destroy());
}));

for(const [initial,final] of [['sending','resolved'],['unknown','cancelled']])test(`mounted ${initial} approval revalidates after focus without losing reader focus`,{timeout:30000},()=>fixture(async({page,control,mount,calls})=>{
 const transitioning=approvalRecord({status:initial,executable:false,choices:[]});
 control.notificationTarget=approvalTarget;control.approvals=[];control.approvalDetail=transitioning;
 await mount(page,'?notification=notice-target');
 const card=page.locator('.approval-card');
 await card.getByRole('heading',{name:initial==='sending'?'Decision pending':'Decision status unknown',exact:true}).waitFor();
 assert.equal(await card.getByRole('button').count(),0);
 const detailPath='/approvals/approval-1',before=calls.filter(call=>call.p===detailPath && call.method==='GET').length;
 let release;control.approvalDetailHold=new Promise(resolve=>release=resolve);
 const listResponses=responseBatch(page,'/app-api/approvals',4);
 await page.evaluate(()=>{for(let i=0;i<4;i++)dispatchEvent(new Event('focus'));});
 await listResponses;
 await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,0)));
 assert.equal(calls.filter(call=>call.p===detailPath && call.method==='GET').length,before+1,
  'repeated focus refreshes share one held target-detail request');
 const staleResponse=page.waitForResponse(response=>response.url().endsWith(detailPath) && response.request().method()==='GET');
 const focused=await page.evaluate(()=>document.activeElement===document.querySelector('.approval-card'));
 control.approvalDetail=approvalRecord({status:final,executable:false,choices:[]});
 control.approvalDetailHold=null;release();await staleResponse;
 await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,0)));
 assert.equal(await card.getByRole('heading').innerText(),initial==='sending'?'Decision pending':'Decision status unknown',
  'the captured transitional response does not invent a final outcome');
 assert.equal(await page.evaluate(()=>document.activeElement===document.querySelector('.approval-card')),focused,
  'refresh does not steal reader focus');
 await page.evaluate(()=>dispatchEvent(new Event('focus')));
 await card.getByRole('heading',{name:final==='resolved'?'Resolved':'Cancelled',exact:true}).waitFor();
 assert.equal(await card.getByRole('button').count(),0);
 assert.equal(calls.filter(call=>call.p==='/approvals/approval-1/decision').length,0);
 await page.evaluate(()=>app.destroy());
}));

test('a held transitional detail cannot update a newer session after route teardown',{timeout:30000},()=>fixture(async({page,control,mount,calls})=>{
 control.notificationTarget=approvalTarget;control.approvals=[];
 control.approvalDetail=approvalRecord({status:'sending',executable:false,choices:[]});
 await mount(page,'?notification=notice-target');
 await page.getByRole('heading',{name:'Decision pending',exact:true}).waitFor();
 let release;control.approvalDetailHold=new Promise(resolve=>release=resolve);
 const detailPath='/approvals/approval-1',before=calls.filter(call=>call.p===detailPath && call.method==='GET').length;
 const listResponse=responseBatch(page,'/app-api/approvals',1);
 await page.evaluate(()=>dispatchEvent(new Event('focus')));
 await listResponse;
 await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,0)));
 assert.equal(calls.filter(call=>call.p===detailPath && call.method==='GET').length,before+1);
 const staleResponse=page.waitForResponse(response=>response.url().endsWith(detailPath) && response.request().method()==='GET');
 await page.getByRole('button',{name:'Chats',exact:true}).click();
 await page.getByRole('button',{name:/Second conversation/}).click();
 await page.getByRole('heading',{name:'Second conversation'}).waitFor();
 control.approvalDetailHold=null;release();await staleResponse;
 await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,0)));
 assert.equal(await page.locator('.approval-card').count(),0,'a response from the closed chat cannot place an approval');
 assert.equal(calls.filter(call=>call.p==='/approvals/approval-1/decision').length,0);
 await page.evaluate(()=>app.destroy());
}));

test('approval controls retain pending and card-scoped errors across focus refresh',{timeout:30000},()=>fixture(async({page,control,mount,calls})=>{
 const item=approvalRecord();control.notificationTarget=approvalTarget;control.approvals=[item];control.approvalDetail=item;
 await mount(page,'?notification=notice-target');await page.getByRole('button',{name:'Approve once'}).waitFor();
 await page.waitForTimeout(100);
 const preview=page.locator('.review-payload');
 await preview.evaluate(node=>{node.style.width='10px';node.style.height='8px';node.style.overflow='auto';node.scrollTop=8;});
 const previewScroll=await preview.evaluate(node=>node.scrollTop);
 const approve=page.getByRole('button',{name:'Approve once'}),cardElement=await page.locator('.approval-card').elementHandle();
 await holdPasskey(page);
 await approve.focus();
 await approve.click();
 await page.waitForFunction(()=>window.passkeyRequest?.started);
 const passkeyFocus=await cardElement.evaluate(node=>({connected:node.isConnected,active:document.activeElement===node}));
 assert.ok(passkeyFocus.connected && passkeyFocus.active,`passkey flow keeps focus in the approval card: ${JSON.stringify({passkeyFocus,calls:calls.map(call=>call.p)})}`);
 await page.evaluate(()=>dispatchEvent(new Event('focus')));await page.waitForTimeout(80);
 const focusAfterRefresh=await cardElement.evaluate(node=>({connected:node.isConnected,active:document.activeElement===node}));
 assert.ok(focusAfterRefresh.connected && focusAfterRefresh.active,`an unchanged refresh preserves keyboard focus: ${JSON.stringify(focusAfterRefresh)}`);
 assert.equal(await preview.evaluate(node=>node.scrollTop),previewScroll,'an unchanged refresh preserves preview scroll');
 assert.equal(await page.getByRole('button',{name:'Approve once'}).isDisabled(),true,'focus refresh must not enable duplicate approval while passkey is pending');
 await page.evaluate(()=>cancelPasskey());
 await page.locator('.approval-card').getByText(/Passkey request cancelled or not allowed/).waitFor();
 assert.equal(await page.locator('.approval-card').getByRole('button',{name:'Approve once'}).isDisabled(),false,'a cancelled verification can be deliberately retried');

 let release;control.decisionHold=new Promise(resolve=>release=resolve);control.decisionStatus=503;
 await holdPasskey(page);
 await page.locator('.approval-card').getByRole('button',{name:'Deny'}).click();
 await page.waitForFunction(()=>window.passkeyRequest?.started);
 await page.evaluate(()=>finishPasskey());
 const decisionPath='/approvals/approval-1/decision',deadline=Date.now()+5000;
 while(!calls.some(call=>call.p===decisionPath) && Date.now()<deadline)await page.waitForTimeout(10);
 assert.ok(calls.some(call=>call.p===decisionPath),'verified deny request must reach the decision endpoint');
 await page.evaluate(()=>dispatchEvent(new Event('focus')));await page.waitForTimeout(80);
 assert.equal(await page.locator('.approval-card').getByRole('button',{name:'Deny'}).isDisabled(),true,'refresh must keep a submitted decision locked');
 release();await page.locator('.approval-card').getByText('Synthetic unavailable').waitFor();
 assert.equal(await page.locator('.approval-card').getByRole('button',{name:'Deny'}).count(),0,'an uncertain decision must not be resubmitted');
}));
test('approval acknowledgement wins over list removal and changed actions reject stale passkey results',{timeout:30000},()=>fixture(async({page,control,mount,calls})=>{
 const item=approvalRecord();control.notificationTarget=approvalTarget;control.approvals=[item];control.approvalDetail=item;
 await mount(page,'?notification=notice-target');await page.getByRole('button',{name:'Deny'}).waitFor();
 let release;control.decisionHold=new Promise(resolve=>release=resolve);
 await holdPasskey(page);
 await page.getByRole('button',{name:'Deny'}).click();
 await page.waitForFunction(()=>window.passkeyRequest?.started);
 await page.evaluate(()=>finishPasskey());
 const decisionPath='/approvals/approval-1/decision',deadline=Date.now()+5000;
 while(!calls.some(call=>call.p===decisionPath) && Date.now()<deadline)await page.waitForTimeout(10);
 assert.ok(calls.some(call=>call.p===decisionPath),'verified deny request must reach the decision endpoint');
 control.approvals=[];await page.evaluate(()=>dispatchEvent(new Event('focus')));await page.waitForTimeout(80);
 release();await page.locator('.approval-card').getByRole('heading',{name:'Resolved'}).waitFor();
 assert.equal(calls.filter(call=>call.p==='/approvals/approval-1/decision').length,1);

 control.approvals=[item];control.approvalDetail=item;await mount(page,'?notification=notice-target');await page.getByRole('button',{name:'Approve once'}).waitFor();
 await holdPasskey(page);await page.getByRole('button',{name:'Approve once'}).click();await page.waitForFunction(()=>window.passkeyRequest?.started);
 control.approvals=[approvalRecord({command:'Changed synthetic command'})];
 await page.evaluate(()=>dispatchEvent(new Event('focus')));await page.locator('.approval-card').getByText('Changed synthetic command').waitFor();
 await page.evaluate(()=>finishPasskey());await page.waitForTimeout(80);
 assert.equal(calls.filter(call=>call.p==='/approvals/approval-1/decision').length,1,'changed action details must reject the stale passkey result');
}));
test('late approval lists and passkey results cannot cross a newer route or owner',{timeout:30000},()=>fixture(async({page,control,mount,calls})=>{
 let release;control.approvalHold=new Promise(resolve=>release=resolve);control.approvals=[approvalRecord()];
 await page.getByRole('button',{name:/First conversation/}).click();
 await page.getByRole('textbox',{name:'Message Hermes'}).waitFor();
 await page.getByRole('button',{name:'Chats',exact:true}).click();
 await page.getByRole('button',{name:/Second conversation/}).click();
 await page.getByRole('textbox',{name:'Message Hermes'}).waitFor();
 release();await page.waitForTimeout(80);
 assert.match(await page.locator('.conversation-head h1').innerText(),/Second conversation/);
 assert.equal(await page.locator('.approval-card').count(),0,'a prior session response cannot place its approval in a newer chat');

 control.notificationTarget=approvalTarget;control.approvals=[approvalRecord()];control.approvalDetail=control.approvals[0];
 await mount(page,'?notification=notice-target');await page.getByRole('button',{name:'Approve once'}).waitFor();
 await holdPasskey(page);await page.getByRole('button',{name:'Approve once'}).click();await page.waitForFunction(()=>window.passkeyRequest?.started);
 control.user='fixture-new-owner';control.approvals=[];
 await page.evaluate(()=>app.start());await page.waitForFunction(()=>app.state.user?.id==='fixture-new-owner');
 await page.evaluate(()=>finishPasskey());await page.waitForTimeout(80);
 assert.equal(calls.filter(call=>call.p==='/approvals/approval-1/decision').length,0,'a late verification cannot decide for a replacement owner');
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
