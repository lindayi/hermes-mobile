import {generatedAssets} from './generated-assets.mjs';
import {artifactURL} from './artifacts.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {withoutValidatedPresence} from './push-fixture-contract.mjs';
import {createServer} from 'node:http';
import {readFile,mkdtemp,rm} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const repo=fileURLToPath(new URL('../../',import.meta.url));
const receipt=(id='bg-1')=>({id,session_id:'s1',title:'Completed delegation',body:'**Child A**\nFixture report A\n\n**Child B**\nFixture report B\n<img src=x onerror=alert(1)> [unsafe](javascript:alert)',created_at:1700000000,kind:'background_result',source_event_id:'async:fixture-'+id,agent_context_state:'not_injected'});
async function fixture(){
 const temp=await mkdtemp(join(tmpdir(),'hermes-background-browser-'));const dir=generatedAssets(temp);
 let server,browser;
 async function close(){
  try{await browser?.close();}
  finally{
   try{if(server){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}}
   finally{await rm(temp,{recursive:true,force:true});}
  }
 }
 try{
 const index=await readFile(join(dir,'index.html'),'utf8');assert.match(index,/app\.[a-f0-9]+\.js/,'fixture exercises generated assets');
 const state={items:[receipt()],status:200,calls:[],held:null,streams:new Set()};
 state.emit=(name,data,id)=>{for(const stream of state.streams)stream.write(`id: ${id}\nevent: ${name}\ndata: ${JSON.stringify(data)}\n\n`);};
 server=createServer(async(req,res)=>{
  const url=new URL(req.url,'http://localhost'),p=url.pathname;
  if(p.startsWith('/hermes/app-api/')){
   let raw='';for await(const chunk of req)raw+=chunk;
   state.calls.push({path:p,method:req.method,body:raw?JSON.parse(raw):null,csrf:req.headers['x-csrf-token']});let data;
   if(p.endsWith('/runs/r1/events')){res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache'});res.write(': fixture connected\n\n');state.streams.add(res);req.on('close',()=>state.streams.delete(res));return;}
   if(p.endsWith('/runs/r1'))data=state.snapshot?.run || {};
   else if(p.endsWith('/auth/me'))data={user:{id:'fixture-owner',profile:'default',status:'ready'},csrf_token:'fixture'};
   else if(p.endsWith('/sessions'))data={items:[{id:'s1',title:'Notification fixture'},{id:'s2',title:'Another conversation'}],total:2};
   else if(p.endsWith('/messages'))data=(url.searchParams.has('offset') && state.olderSnapshot) || state.snapshot || {items:Array.from({length:24},(_,i)=>({role:i%2?'assistant':'user',content:`Fixture history ${i}. This is test content, not a live model result.`})),offset:0,run:null};
   else if(p.endsWith('/background')){if(state.held)await state.held;data={items:p.includes('/s1/')?state.items:[]};res.writeHead(state.status,{'Content-Type':'application/json'}).end(JSON.stringify(data));return;}
   else data={items:[]};
   res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify(data));return;
  }
  const relative=p.replace(/^\/hermes\//,'')||'index.html';if(relative.includes('..')){res.writeHead(400).end();return;}
  try{const body=await readFile(join(dir,relative));res.writeHead(200,{'Content-Type':({html:'text/html',mjs:'text/javascript',js:'text/javascript',css:'text/css',svg:'image/svg+xml',webmanifest:'application/manifest+json'})[relative.split('.').pop()]||'application/octet-stream'}).end(body);}catch{res.writeHead(404).end();}
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);
 return {state,page,context,errors,dir,temp,async open(){await page.getByRole('button',{name:'Notification fixture',exact:true}).click();await page.getByRole('textbox',{name:'Message Hermes'}).waitFor();},async refresh(){const response=page.waitForResponse(r=>r.url().endsWith('/sessions/s1/background'));await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await response;},close};
 }catch(error){
  // A failed startup must release partial resources without masking its error.
  await close().catch(()=>{});throw error;
 }
}
test('generated background-only tail survives terminal SSE, replay and materialized reload without fake boundaries',async()=>{
 const h=await fixture(),{page,state}=h;
 try{
  state.items=[];
  const run={id:'r1',session_id:'s1',status:'running',input:'Background-only request',created_at:100,updated_at:130,error:null};
  const events=[{id:1,name:'delta',observed_at:110,data:{text:'PUBLIC A'}},{id:2,name:'delta',observed_at:120,data:{text:'DRAFT B'}}];
  state.snapshot={items:[],offset:0,run};
  const connected=page.waitForResponse(r=>r.url().endsWith('/runs/r1/events'));await h.open();await connected;
  state.emit('delta',{text:'PUBLIC A',observed_at:110},1);
  await page.waitForFunction(()=>document.querySelector('.message-body')?.textContent==='PUBLIC A');
  state.items=[{...receipt('tail'),origin_run_id:'r1',origin_session_id:'s1',event_at:115}];await h.refresh();
  const card=page.locator('[data-background-id="tail"]');await card.waitFor({state:'attached'});
  await page.evaluate(()=>{const text=document.querySelector('.activity-summary .markdown').firstChild;window.savedTailText=text;getSelection().setBaseAndExtent(text,0,text,text.length);});
  state.emit('delta',{text:'DRAFT B',observed_at:120},2);
  state.emit('done',{status:'completed',output:'DEFINITIVE F',updated_at:130},3);
  const check=async()=>{await page.waitForFunction(()=>document.querySelector('.messages').textContent.includes('DEFINITIVE F'));assert.deepEqual(await card.evaluate(el=>[el.previousElementSibling?.querySelector('.markdown')?.textContent,el.nextElementSibling.textContent]),['PUBLIC A','DEFINITIVE F']);assert.equal(await page.locator('.activity-summary').count(),1);assert.doesNotMatch(await page.locator('.messages').textContent(),/DRAFT B/);assert.equal(await card.count(),1);};
  await check();assert.equal(await page.evaluate(()=>getSelection().toString()),'PUBLIC A');assert.equal(await page.evaluate(()=>savedTailText.isConnected),true);
  Object.assign(run,{status:'completed',output:'DEFINITIVE F'});state.snapshot.tool_replay={run_id:'r1',cursor:2,events};
  await page.reload();await h.open();await card.waitFor({state:'attached'});await check();
  const materialized=JSON.parse(execFileSync('python3',['-c','import json,sys; from backend.native_catalog import _journal_turn; value=json.load(sys.stdin); print(json.dumps(_journal_turn(value[0],value[1])))'],{cwd:repo,input:JSON.stringify([run,events]),encoding:'utf8'}));
  state.snapshot={items:materialized,offset:0,run:null};state.items=[];
  await page.reload();await h.open();assert.equal(await page.locator('.activity-summary').count(),0);assert.doesNotMatch(await page.locator('.messages').textContent(),/PUBLIC A|DRAFT B/);
  state.items=[{...receipt('tail'),origin_run_id:'r1',origin_session_id:'s1',event_at:115}];await h.refresh();await card.waitFor({state:'attached'});
  assert.equal(await card.evaluate(el=>el.previousElementSibling.querySelector('.markdown').textContent),'PUBLIC A');assert.match(await card.evaluate(el=>el.nextElementSibling.textContent),/DEFINITIVE F/);assert.doesNotMatch(await page.locator('.messages').textContent(),/DRAFT B/);
  await h.refresh();assert.equal(await page.locator('.activity-summary').count(),1);assert.deepEqual(h.errors,[]);
 }finally{await h.close();}
});

test('generated first refresh resolves every retained tail boundary in both receipt orders and reload paths',async()=>{
 const h=await fixture(),{page,state}=h;
 try{
  const events=[{id:1,name:'delta',observed_at:110,data:{text:'PUBLIC A'}},{id:2,name:'delta',observed_at:120,data:{text:'PUBLIC B'}},{id:3,name:'delta',observed_at:128,data:{text:'DRAFT C'}}];
  const run={id:'r1',session_id:'s1',status:'completed',input:'Question',created_at:100,updated_at:130,output:'FINAL F',error:null};
  const materialized=JSON.parse(execFileSync('python3',['-c','import json,sys; from backend.native_catalog import _journal_turn; value=json.load(sys.stdin); print(json.dumps(_journal_turn(value[0],value[1])))'],{cwd:repo,input:JSON.stringify([run,events]),encoding:'utf8'}));
  const order=()=>page.evaluate(()=>[...(document.querySelector('.live-message') || document.querySelector('.messages')).children].filter(n=>n.matches('.activity-summary,.background-result,.message-body,.assistant-message')).map(n=>n.dataset.backgroundId || n.querySelector('.markdown')?.textContent || n.textContent));
  for(const mode of ['completed replay','materialized reload'])for(const times of [[115,125],[125,115]]){
   state.items=[];state.snapshot=mode==='completed replay'?{items:[],offset:0,run,tool_replay:{run_id:'r1',cursor:3,events}}:{items:materialized,offset:0,run:null};
   await page.reload();await h.open();assert.deepEqual(await order(),['FINAL F']);
   await page.evaluate(()=>{window.savedFinal=document.querySelector('.message-body,.assistant-message:not(.live-message)');window.savedFinalText=document.createTreeWalker(savedFinal.querySelector('.markdown') || savedFinal,NodeFilter.SHOW_TEXT).nextNode();getSelection().setBaseAndExtent(savedFinalText,0,savedFinalText,savedFinalText.length);});
   state.items=times.map(event_at=>({...receipt(`bg${event_at}`),origin_run_id:'r1',origin_session_id:'s1',event_at}));
   await h.refresh();await page.locator('[data-background-id="bg125"]').waitFor({state:'attached'});
   assert.deepEqual(await order(),['PUBLIC A','bg115','PUBLIC B','bg125','FINAL F'],`first refresh: ${mode}, ${times}`);
   assert.deepEqual(await page.evaluate(()=>[savedFinal===document.querySelector('.message-body,.assistant-message:not(.live-message)'),savedFinalText.isConnected,getSelection().toString()]),[true,true,'FINAL F']);
   await page.evaluate(()=>{window.savedProgress=document.querySelector('.activity-summary');savedProgress.open=false;window.savedReceipt=document.querySelector('[data-background-id="bg115"]');savedReceipt.open=true;});
   await h.refresh();assert.deepEqual(await order(),['PUBLIC A','bg115','PUBLIC B','bg125','FINAL F']);
   assert.deepEqual(await page.evaluate(()=>[savedProgress===document.querySelector('.activity-summary'),savedProgress.open,savedReceipt===document.querySelector('[data-background-id="bg115"]'),savedReceipt.open]),[true,false,true,true]);
   assert.doesNotMatch(await page.locator('.messages').textContent(),/DRAFT C/);
  }
  assert.deepEqual(h.errors,[]);assert.deepEqual(withoutValidatedPresence(state.calls).filter(c=>c.method!=='GET'),[]);
 }finally{await h.close();}
});

test('generated consecutive SSE deltas retain selected streaming text through late segmentation and replay reload',async()=>{
 const h=await fixture(),{page,state}=h;
 try{
  state.items=[];
  const prior=Array.from({length:10},(_,i)=>({id:i+1,name:'commentary',observed_at:100+i/10,data:{text:'Earlier public context. '.repeat(12)}}));
  state.snapshot={items:[],offset:0,run:{id:'r1',session_id:'s1',status:'running',input:'Synthetic streaming request',created_at:100},tool_replay:{run_id:'r1',cursor:10,events:prior}};
  const connected=page.waitForResponse(r=>r.url().endsWith('/runs/r1/events'));await h.open();await connected;
  const deltas=[{id:11,name:'delta',observed_at:110,data:{text:'SELECTED EARLIER '}},{id:12,name:'delta',observed_at:120,data:{text:'LATER'}}];
  for(const event of deltas)state.emit(event.name,{...event.data,observed_at:event.observed_at},event.id);
  await page.waitForFunction(()=>document.querySelector('.live-message .message-body')?.textContent==='SELECTED EARLIER LATER');
  assert.equal(await page.locator('.activity-summary').count(),10,'no card per streaming token');
  const top=await page.evaluate(()=>{const body=document.querySelector('.live-message .message-body');window.selectedStreamText=body.firstChild;getSelection().setBaseAndExtent(selectedStreamText,0,selectedStreamText,8);const m=document.querySelector('.messages');m.scrollTop=m.scrollHeight;return getSelection().getRangeAt(0).getBoundingClientRect().top;});
  state.items=[{...receipt('delta-boundary'),origin_run_id:'r1',origin_session_id:'s1',event_at:115}];await h.refresh();const card=page.locator('[data-background-id="delta-boundary"]');await card.waitFor({state:'attached'});
  assert.equal(await page.evaluate(()=>getSelection().toString()),'SELECTED','polling must preserve selection in the original streamed text');
  assert.equal(await page.evaluate(()=>selectedStreamText.isConnected),true,'original selected text node survives');
  assert.ok(Math.abs(await page.evaluate(()=>getSelection().getRangeAt(0).getBoundingClientRect().top)-top)<1,'selected text stays at its reading offset');
  assert.deepEqual(await card.evaluate(el=>[el.previousElementSibling.querySelector('.markdown').textContent,el.nextElementSibling.querySelector('.markdown').textContent]),['SELECTED EARLIER ','LATER']);
  await h.refresh();assert.equal(await page.evaluate(()=>getSelection().toString()),'SELECTED');
  state.emit('tool',{name:'last_tool',tool_call_id:'t',status:'running',observed_at:130},13);
  await page.locator('.tool-name').filter({hasText:'last_tool'}).waitFor({state:'attached'});
  state.snapshot.tool_replay={run_id:'r1',cursor:13,events:[...prior,...deltas,{id:13,name:'tool',observed_at:130,data:{name:'last_tool',tool_call_id:'t',status:'running'}}]};
  await page.reload();await h.open();await card.waitFor({state:'attached'});
  assert.deepEqual(await card.evaluate(el=>[el.previousElementSibling.querySelector('.markdown').textContent,el.nextElementSibling.querySelector('.markdown').textContent,el.nextElementSibling.nextElementSibling.querySelector('.tool-name').textContent]),['SELECTED EARLIER ','LATER','last_tool']);
  assert.equal(await page.locator('.activity-summary').count(),12);
  assert.deepEqual(h.errors,[]);assert.deepEqual(withoutValidatedPresence(state.calls).filter(c=>c.method!=='GET'),[]);
 }finally{await h.close();}
});

test('generated background placement keeps a late result with its exact old turn without following or replacing the reader',async()=>{
 const h=await fixture(),{page,state}=h;
 try{
  state.items=[];state.snapshot={items:Array.from({length:24},(_,i)=>({id:i+1,role:i%2?'assistant':'user',timestamp:100+i*10,content:`Turn ${i}. `+'Synthetic visible history. '.repeat(12)})),offset:0,run:null};
  await h.open();await page.getByRole('textbox',{name:'Message Hermes'}).fill('Keep placement draft');
  await page.evaluate(()=>{window.placementEditor=document.querySelector('textarea');placementEditor.focus();placementEditor.setSelectionRange(2,7);window.placementNative=document.querySelectorAll('.assistant-message')[9];const m=document.querySelector('.messages');m.scrollTop=m.scrollHeight;});
  const before=await page.evaluate(()=>placementNative.getBoundingClientRect().top);
  state.items=[{...receipt('old-turn'),origin_run_id:'r1',origin_session_id:'s1',origin_message_id:1,origin_anchor:{session_id:'s1',message_id:0},event_at:9999,event_at_source:'completed_at'}];await h.refresh();
  await page.locator('[data-background-id="old-turn"]').waitFor({state:'attached'});
  assert.equal(await page.locator('[data-background-id="old-turn"]').evaluate(el=>el.nextElementSibling?.textContent.includes('Turn 2.')),true,'exact old turn instead of conversation tail');
  assert.ok(Math.abs(await page.evaluate(()=>placementNative.getBoundingClientRect().top)-before)<1,'late older insertion must not move the reader');
  assert.deepEqual(await page.evaluate(()=>[placementEditor===document.querySelector('textarea'),placementEditor.value,placementEditor.selectionStart,placementEditor.selectionEnd]),[true,'Keep placement draft',2,7]);
  assert.deepEqual(withoutValidatedPresence(state.calls).filter(c=>c.method!=='GET'),[]);assert.deepEqual(h.errors,[]);
 }finally{await h.close();}
});

test('generated background placement pagination relocates an open pending result without losing selected report text',async()=>{
 const h=await fixture(),{page,state}=h;
 try{
  const history=Array.from({length:8},(_,i)=>({id:i+1,role:i%2?'assistant':'user',timestamp:100+i*10,content:`Paged turn ${i}. `+'Synthetic history. '.repeat(6)}));
  state.snapshot={items:history.slice(4),offset:4,run:null};state.olderSnapshot={items:history.slice(0,4),offset:0};
  state.items=[{...receipt('paged-origin'),origin_run_id:'r-old',origin_session_id:'s1',origin_message_id:1,origin_anchor:{session_id:'s1',message_id:0},event_at:999,event_at_source:'completed_at'}];
  await h.open();const card=page.locator('[data-background-id="paged-origin"]');await card.waitFor({state:'attached'});assert.equal(await card.getAttribute('data-placement'),'pending');await card.locator('summary').click();
  await page.evaluate(()=>{window.pendingCard=document.querySelector('[data-background-id]');[...document.querySelectorAll('button')].find(b=>b.textContent==='Load older messages').click();const text=pendingCard.querySelector('.markdown strong').firstChild;getSelection().setBaseAndExtent(text,0,text,text.length);});
  await page.waitForFunction(()=>document.querySelector('[data-background-id]')?.dataset.placement==='exact');
  assert.equal(await card.evaluate(el=>el===window.pendingCard && el.open),true);
  assert.equal(await card.evaluate(el=>el.nextElementSibling?.textContent.includes('Paged turn 2.')),true);
  assert.equal(await page.evaluate(()=>getSelection().toString()),'Child A','pagination re-placement preserves selected report text');
  assert.deepEqual(withoutValidatedPresence(state.calls).filter(c=>c.method!=='GET'),[]);assert.deepEqual(h.errors,[]);
 }finally{await h.close();}
});

test('generated live background chronology preserves nested reader anchors and selected reports on phone and tablet',async()=>{
 for(const width of [390,768]){
  const h=await fixture(),{page,state}=h;
  try{
   await page.setViewportSize({width,height:520});state.items=[];
   const events=[{id:1,name:'tool',observed_at:110,data:{name:'first_tool',tool_call_id:'t1',status:'running'}},{id:2,name:'tool',observed_at:120,data:{name:'second_tool',tool_call_id:'t2',status:'running'}},...Array.from({length:16},(_,i)=>({id:i+3,name:'commentary',observed_at:130+i,data:{id:'progress-'+i,text:`Progress ${i}. `+'Synthetic public progress for reader positioning. '.repeat(8)}}))];
   state.snapshot={items:[],offset:0,run:{id:'r1',session_id:'s1',status:'running',input:'Synthetic live turn',created_at:100},tool_replay:{run_id:'r1',cursor:18,events}};
   await h.open();await page.locator('.live-message .activity-summary').first().waitFor();
   await page.evaluate(()=>{window.liveReader=document.querySelectorAll('.live-message .activity-summary')[8];const m=document.querySelector('.messages');m.scrollTop+=liveReader.getBoundingClientRect().top-m.getBoundingClientRect().top+13;window.savedLive=document.querySelector('.live-message');window.savedGroup=savedLive.querySelector('.tool-activity');});
   const top=await page.evaluate(()=>liveReader.getBoundingClientRect().top);
   state.items=[{...receipt('within-live'),origin_run_id:'r1',origin_session_id:'s1',event_at:115}];await h.refresh();const card=page.locator('[data-background-id="within-live"]');await card.waitFor({state:'attached'});
   assert.deepEqual(await card.evaluate(el=>[el.parentElement===savedLive,el.previousElementSibling===savedGroup,el.nextElementSibling.querySelector('.tool-name').textContent]),[true,true,'second_tool']);
   const after=await page.evaluate(()=>({top:liveReader.getBoundingClientRect().top,scroll:document.querySelector('.messages').scrollTop,viewport:document.querySelector('.messages').getBoundingClientRect().top}));
   assert.ok(Math.abs(after.top-top)<1,`nested live reader anchor survives a late group split: ${JSON.stringify({before:top,...after})}`);
   await card.evaluate(el=>{window.savedReport=el;el.open=true;const text=el.querySelector('.markdown strong').firstChild;getSelection().setBaseAndExtent(text,0,text,text.length);});
   state.items.push({...receipt('earlier-live'),origin_run_id:'r1',origin_session_id:'s1',event_at:112});await h.refresh();await page.locator('[data-background-id="earlier-live"]').waitFor({state:'attached'});
   assert.equal(await card.evaluate(el=>el===savedReport && el.open),true);assert.equal(await page.evaluate(()=>getSelection().toString()),'Child A');assert.deepEqual(await page.locator('[data-background-id]').evaluateAll(nodes=>nodes.map(n=>n.dataset.backgroundId)),['earlier-live','within-live']);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.deepEqual(h.errors,[]);assert.deepEqual(withoutValidatedPresence(state.calls).filter(c=>c.method!=='GET'),[]);
  }finally{await h.close();}
 }
});

test('generated mobile chat displays compact receipts and preserves older reading, selection, draft and layout',async()=>{
 const h=await fixture(),{page,state}=h;
 try{
  await h.open();await page.locator('[data-background-id="bg-1"]').waitFor();
  assert.equal(await page.locator('[data-background-id]').count(),1);
  assert.equal(await page.locator('.messages').evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight<5),true,'initial background result remains in view at the end');
  const card=page.locator('[data-background-id="bg-1"]');assert.equal(await card.evaluate(el=>el.open),false);await card.locator('summary').click();assert.match(await card.innerText(),/Child A.*Fixture report A.*Child B.*Fixture report B/s);assert.equal(await card.locator('script,img,a').count(),0);
  await page.evaluate(()=>{window.savedEditor=document.querySelector('textarea');window.savedCard=document.querySelector('[data-background-id]');window.savedMessage=document.querySelector('.assistant-message');});
  await page.getByRole('textbox',{name:'Message Hermes'}).fill('Keep this unsent draft');await page.evaluate(()=>{savedEditor.focus();savedEditor.setSelectionRange(2,9);document.querySelector('.messages').scrollTop=160;});
  const top=await page.locator('.messages').evaluate(el=>el.scrollTop);state.items.push(receipt('bg-2'));await h.refresh();await page.locator('[data-background-id="bg-2"]').waitFor();
  assert.equal(await page.locator('.messages').evaluate(el=>el.scrollTop),top);
  assert.deepEqual(await page.evaluate(()=>({editor:savedEditor===document.querySelector('textarea'),card:savedCard===document.querySelector('[data-background-id]'),open:savedCard.open,native:savedMessage===document.querySelector('.assistant-message'),focus:document.activeElement===savedEditor,start:savedEditor.selectionStart,end:savedEditor.selectionEnd,value:savedEditor.value})),{editor:true,card:true,open:true,native:true,focus:true,start:2,end:9,value:'Keep this unsent draft'});
  await h.refresh();assert.equal(await page.locator('[data-background-id]').count(),2);
  for(const width of [320,390,768,1280])for(const theme of ['light','dark']){
   await page.setViewportSize({width,height:844});await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   const header=await page.locator('.conversation-head').boundingBox(),composer=await page.locator('.composer').boundingBox();assert.ok(header.height<=60);assert.ok(composer.y+composer.height<=844);
   const padding=await card.evaluate(el=>parseFloat(getComputedStyle(el).paddingTop));assert.ok(padding<=16,`compact card padding: ${padding}`);
  }
  await page.setViewportSize({width:390,height:844});await page.locator('.messages').evaluate(el=>el.scrollTop=el.scrollHeight);await page.screenshot({path:fileURLToPath(artifactURL('hermes-background-notifications-mobile.png'))});
  await page.reload();await h.open();await page.locator('[data-background-id="bg-2"]').waitFor();assert.equal(await page.locator('[data-background-id]').count(),2,'reload does not duplicate receipts');
  state.items=[];await h.refresh();await page.waitForFunction(()=>document.querySelectorAll('[data-background-id]').length===0);
  assert.deepEqual(withoutValidatedPresence(state.calls).filter(c=>c.method!=='GET'),[]);assert.deepEqual(h.errors,[]);
 }finally{await h.close();}
});

test('generated count-saturated chat keeps a surviving reader anchor and surfaces the latest late-focus completion',async()=>{
 const h=await fixture(),{page,state}=h;let release;
 try{
  state.items=Array.from({length:200},(_,i)=>({...receipt('count-'+i),body:'Complete fixture report '+i}));
  await h.open();await page.locator('[data-background-id="count-199"]').waitFor();assert.equal(await page.locator('[data-background-id]').count(),200);
  await page.getByRole('textbox',{name:'Message Hermes'}).fill('Keep saturated draft');
  await page.evaluate(()=>{
   window.saturatedEditor=document.querySelector('textarea');saturatedEditor.focus();saturatedEditor.setSelectionRange(2,8);
   window.readerAnchor=document.querySelector('[data-background-id="count-100"]');window.saturatedNative=document.querySelector('.assistant-message');
   const messages=document.querySelector('.messages');messages.scrollTop+=readerAnchor.getBoundingClientRect().top-messages.getBoundingClientRect().top+13;
  });
  const top=await page.evaluate(()=>readerAnchor.getBoundingClientRect().top);
  state.held=new Promise(resolve=>release=resolve);const request=page.waitForRequest(r=>r.url().endsWith('/sessions/s1/background'));await page.evaluate(()=>dispatchEvent(new Event('focus')));await request;
  await page.evaluate(()=>dispatchEvent(new Event('focus')));state.items.push({...receipt('count-200'),body:'Latest completion after count saturation'});
  const response=page.waitForResponse(r=>r.url().endsWith('/sessions/s1/background'));release();state.held=null;await response;await page.locator('[data-background-id="count-200"]').waitFor({state:'attached'});
  assert.deepEqual(await page.locator('[data-background-id]').evaluateAll(nodes=>nodes.map(n=>n.dataset.backgroundId)),state.items.slice(1).map(i=>i.id));
  assert.ok(Math.abs(await page.evaluate(()=>readerAnchor.getBoundingClientRect().top)-top)<1,'surviving older report stays at its exact viewport offset after eviction');
  assert.deepEqual(await page.evaluate(()=>({anchor:readerAnchor===document.querySelector('[data-background-id="count-100"]'),native:saturatedNative===document.querySelector('.assistant-message'),editor:saturatedEditor===document.querySelector('textarea'),focus:document.activeElement===saturatedEditor,start:saturatedEditor.selectionStart,end:saturatedEditor.selectionEnd,value:saturatedEditor.value})),{anchor:true,native:true,editor:true,focus:true,start:2,end:8,value:'Keep saturated draft'});
  await page.screenshot({path:fileURLToPath(artifactURL('hermes-background-saturation-anchor.png'))});
  await page.locator('.messages').evaluate(el=>el.scrollTop=el.scrollHeight);const note=page.locator('.background-omitted');assert.equal(await note.isVisible(),true);assert.doesNotMatch(await note.innerText(),/Inbox/i);assert.match(await note.innerText(),/older background results.*omitted.*Full reports remain stored with this conversation/i);
  assert.equal(await page.locator('[data-background-id="count-200"]').evaluate(el=>el.open),false);assert.match(await page.locator('[data-background-id="count-200"]').textContent(),/No follow-up was run automatically/);assert.equal(await page.locator('[data-background-id="count-200"]').evaluate(el=>el.matches('.message,.assistant-message,.user-message')),false);
  await page.screenshot({path:fileURLToPath(artifactURL('hermes-background-saturation-count.png'))});
  // Selection deliberately suppresses auto-follow. Release it before exercising
  // the separate bottom-follow intent; preservation was asserted above.
  await page.evaluate(()=>{saturatedEditor.setSelectionRange(8,8);getSelection()?.removeAllRanges();});assert.equal(await page.evaluate(()=>getSelection()?.toString()),'');
  state.items.push({...receipt('count-201'),body:'Next completion after count saturation'});await h.refresh();await page.locator('[data-background-id="count-201"]').waitFor();assert.equal(await page.locator('[data-background-id]').count(),200);assert.equal(await page.locator('.background-omitted').count(),1);
  assert.equal(await page.locator('.messages').evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight<5),true,'bottom reader follows new completion');
  state.items=[];await h.refresh();await page.waitForFunction(()=>!document.querySelector('[data-background-id],.background-omitted'));
  assert.deepEqual(withoutValidatedPresence(state.calls).filter(c=>c.method!=='GET'),[]);assert.deepEqual(h.errors,[]);
 }finally{release?.();await h.close();}
});
test('generated body-saturated chat shows newest complete results and explains retained oversized reports without Inbox noise',async()=>{
 const h=await fixture(),{page,state}=h;
 try{
  const body='```\n'+'L'.repeat(262144-'```\n\n```'.length)+'\n```';
  state.items=Array.from({length:8},(_,i)=>({...receipt('body-'+i),body}));await h.open();await page.locator('[data-background-id="body-7"]').waitFor();assert.equal(await page.locator('[data-background-id]').count(),8);
  state.items.push({...receipt('body-new'),body:'Latest completion after body saturation'});await h.refresh();await page.locator('[data-background-id="body-new"]').waitFor();
  assert.deepEqual(await page.locator('[data-background-id]').evaluateAll(nodes=>nodes.map(n=>n.dataset.backgroundId)),state.items.slice(1).map(i=>i.id));
  assert.equal(await page.locator('[data-background-id="body-7"] .markdown code').textContent(),'L'.repeat(262144-'```\n\n```'.length));assert.match(await page.locator('.background-omitted').innerText(),/older background results.*omitted.*Full reports remain stored with this conversation/i);
  state.items.push({...receipt('body-oversized'),body:'X'.repeat(262145)},{...receipt('body-new-2'),body:'Newest report after oversized report'});await h.refresh();await page.locator('[data-background-id="body-new-2"]').waitFor();
  assert.equal(await page.locator('[data-background-id="body-oversized"]').count(),0);assert.match(await page.locator('.background-omitted').innerText(),/reports.*too large.*Full reports remain stored with this conversation/i);
  await page.locator('.messages').evaluate(el=>el.scrollTop=el.scrollHeight);await page.screenshot({path:fileURLToPath(artifactURL('hermes-background-saturation-body.png'))});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.deepEqual(withoutValidatedPresence(state.calls).filter(c=>c.method!=='GET'),[]);assert.deepEqual(h.errors,[]);
 }finally{await h.close();}
});

test('generated chat tolerates old-backend 404 and fences a late response after route change',async()=>{
 const h=await fixture(),{page,state}=h;let release;
 try{
  state.status=404;await h.open();await page.getByRole('textbox',{name:'Message Hermes'}).fill('Draft during rollout');await h.refresh();assert.equal(await page.locator('[data-background-id]').count(),0);assert.equal(await page.locator('.notice').isVisible(),false);assert.equal(await page.getByRole('textbox',{name:'Message Hermes'}).inputValue(),'Draft during rollout');
  state.status=200;state.held=new Promise(resolve=>release=resolve);const request=page.waitForRequest(r=>r.url().endsWith('/sessions/s1/background'));await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await request;
  await page.getByRole('button',{name:'Back to chats'}).click();await page.getByRole('button',{name:'Another conversation',exact:true}).click();await page.getByRole('textbox',{name:'Message Hermes'}).waitFor();release();state.held=null;
  await page.waitForResponse(r=>r.url().endsWith('/sessions/s2/background'));assert.equal(await page.locator('[data-background-id]').count(),0);assert.deepEqual(withoutValidatedPresence(state.calls).filter(c=>c.method!=='GET'),[]);assert.deepEqual(h.errors,[]);
 }finally{release?.();await h.close();}
});
