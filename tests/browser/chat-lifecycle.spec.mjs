import test from 'node:test';
import assert from 'node:assert/strict';
import {withoutValidatedPresence} from './push-fixture-contract.mjs';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';

const {chromium} = createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const frontend = process.env.HERMES_FRONTEND_DIR ? pathToFileURL(process.env.HERMES_FRONTEND_DIR.replace(/\/$/,'')+'/') : new URL('../../frontend/', import.meta.url);
const session = {id:'lifecycle-session', title:'Lifecycle fixture', source:'cli', updated_at:1700000000};
const prompt = 'Check the isolated lifecycle fixture.';
const answer = 'Fixture completed. This is a deterministic test answer, not a live agent result.';
const prefix = [{role:'user',content:'Earlier fixture question.'}, {role:'assistant',content:'Earlier fixture answer.'}];

// All API responses are explicitly synthetic fixtures. Real Chromium fetch/EventSource
// connect to a loopback HTTP server; no route.fulfill, fake EventSource, or live services.
async function fixture() {
  const state = {run:null, persisted:false, posts:[], connections:[], clients:new Set(), events:[], snapshots:[], unexpected:[], requests:[]};
  const frozenUI = process.env.HERMES_LIFECYCLE_UI_FILE
    ? await readFile(process.env.HERMES_LIFECYCLE_UI_FILE) : null;
  const json = (res,value,status=200) => {res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
  const encode = entry => `id: ${entry.id}\nevent: ${entry.event}\ndata: ${JSON.stringify(entry.data)}\n\n`;
  state.emit = (event,data) => {
    const entry = {id:String(state.events.length+1),event,data};
    state.events.push(entry);
    for (const res of state.clients) res.write(encode(entry));
  };
  state.disconnect = () => {for(const res of state.clients) res.end();state.clients.clear();};
  const server = createServer(async(req,res)=>{
    try {
      const url = new URL(req.url,'http://fixture');
      const path = url.pathname;
      if(path.startsWith('/hermes/app-api/')) {
        const api = path.slice('/hermes/app-api'.length);
        let raw='';for await(const chunk of req)raw+=chunk;
        const call={path,method:req.method,body:raw?JSON.parse(raw):null,csrf:req.headers['x-csrf-token']};state.requests.push(call);
        if(api==='/push/preferences' && req.method==='GET')return json(res,{revision:0,enabled:true,categories:{completion:true,approval:true,attention:true,scheduled:true,operational:true},hide_details:false});
        if(api==='/push/presence' && req.method==='POST'){withoutValidatedPresence([call]);return json(res,{ok:true});}
        if(api==='/auth/me') return json(res,{user:{id:'fixture-owner',role:'owner',status:'ready'},csrf_token:'fixture'});
        if(api==='/runs/fixture-run/controls')return json(res,{steering:false,attempts:[]});
        if(api==='/sessions') return json(res,{items:[session],total:1});
        if(api===`/sessions/${session.id}` && req.method==='GET')return json(res,session);
        if(api===`/sessions/${session.id}/telemetry`)return json(res,{model:null,provider:null,context:{used_tokens:null,limit_tokens:null,source:null,observed_at:null,estimated:false},usage:{scope:'session_lifetime',input_tokens:null,output_tokens:null}});
        if(api===`/sessions/${session.id}/model-options`)return json(res,{available:false,models:[],default:null});
        if(api===`/sessions/${session.id}/background` && req.method==='GET')return json(res,{items:[]});
        if(api===`/sessions/${session.id}/messages`) {
          const items = state.persisted ? [...prefix,{role:'user',content:prompt},{role:'tool',name:'terminal',status:'success',summary:'Run: isolated fixture',content:'fixture result'},{role:'assistant',content:answer}] : prefix;
          const snapshot = {items,offset:0,total:items.length,run:state.persisted ? null : state.run};
          state.snapshots.push({latest:url.searchParams.get('latest'),run:snapshot.run?.id || null});
          return json(res,snapshot);
        }
        if(api==='/runs' && req.method==='POST') {
          const body=call.body;state.posts.push(body);
          state.run={id:'fixture-run',session_id:session.id,input:body.input,status:'running',output:''};
          return json(res,state.run,202);
        }
        if(api==='/runs/fixture-run') return json(res,state.run);
        if(api==='/runs/fixture-run/events') {
          state.connections.push({lastEventId:req.headers['last-event-id'] || ''});
          res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache','Connection':'keep-alive'});
          res.write('retry: 1000\n\n');
          state.clients.add(res);res.on('close',()=>state.clients.delete(res));
          const after=Number(req.headers['last-event-id'] || 0);
          for(const entry of state.events) if(Number(entry.id)>after)res.write(encode(entry));
          return;
        }
        if(['/devices','/passkeys','/invites','/members','/inbox','/approvals','/jobs'].includes(api))return json(res,{items:[]});
        state.unexpected.push(`${req.method} ${api}`);return json(res,{detail:'Unknown fixture route'},404);
      }
      if(path==='/favicon.ico'){res.writeHead(204);res.end();return;}
      // Separate public-file route: never imports the backend or reads private config.
      const relative=path==='/hermes/' ? 'index.html' : path.replace(/^\/hermes\//,'');
      if(!path.startsWith('/hermes/') || relative.split('/').includes('..')){res.writeHead(404);res.end();return;}
      const body=relative==='ui.mjs' && frozenUI ? frozenUI : await readFile(new URL(relative,frontend));
      const mime={html:'text/html',css:'text/css',js:'text/javascript',mjs:'text/javascript',svg:'image/svg+xml',webmanifest:'application/manifest+json',png:'image/png'};
      res.writeHead(200,{'Content-Type':mime[relative.split('.').pop()] || 'application/octet-stream','Cache-Control':'no-store'});res.end(body);
    } catch(error) {state.unexpected.push(error.message);res.writeHead(500);res.end('Fixture failure');}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  return {state,url:`http://127.0.0.1:${server.address().port}/hermes/`,close:async()=>{state.disconnect();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}};
}

async function eventually(check,message) {
  const deadline=Date.now()+5000;
  while(Date.now()<deadline){if(await check())return;await new Promise(resolve=>setTimeout(resolve,25));}
  assert.fail(message);
}
async function visibleInMessages(locator,message,failures) {
  await locator.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const geometry=await locator.evaluate(el=>{
    const r=el.getBoundingClientRect(),v=el.closest('.messages').getBoundingClientRect();
    return {top:r.top,bottom:r.bottom,height:r.height,containerTop:v.top,containerBottom:v.bottom,viewport:innerHeight};
  });
  const g=geometry;
  if(!(g.height>0 && g.top>=g.containerTop-2 && g.bottom<=g.containerBottom+2 && g.bottom<=g.viewport))failures.push({message,...geometry});
}

test('real HTTP/SSE chat survives navigation, disconnect, cleared storage, and persisted completion',{timeout:45000},async()=>{
  const f=await fixture();
  let browser;
  try {
    browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER || '/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
    const context=await browser.newContext({viewport:{width:390,height:450},isMobile:true,deviceScaleFactor:1,serviceWorkers:'block'});
    const page=await context.newPage();page.setDefaultTimeout(5000);
    const errors=[],visibilityFailures=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
    const external=[];context.on('request',request=>{if(new URL(request.url()).origin!==new URL(f.url).origin)external.push(request.url());});
    const open=async()=>{await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Chats',exact:true}).click();await page.getByRole('button',{name:session.title,exact:true}).click();await page.getByRole('textbox',{name:'Message Hermes'}).waitFor();};
    const owns=()=>page.locator('.user-message').filter({hasText:prompt});
    const outputs=()=>page.getByText(answer,{exact:true});
    await page.goto(f.url);
    await page.getByRole('button',{name:'Settings',exact:true}).click();
    await page.getByText('Account: owner · ready',{exact:true}).waitFor();
    await open();
    await page.getByText('Earlier fixture answer.',{exact:true}).waitFor();
    await page.getByRole('textbox',{name:'Message Hermes'}).fill(prompt);
    await page.getByRole('button',{name:'Send message'}).click();
    await eventually(()=>f.state.clients.size===1,'initial real SSE connection established');
    assert.equal(await owns().count(),1,'accepted own prompt renders once');
    await owns().locator('.markdown p').evaluate(el=>el.scrollIntoView({block:'center'}));
    await visibleInMessages(owns().locator('.markdown p'),'own accepted text remains readable in a short viewport',visibilityFailures);
    await page.locator('.messages').evaluate(el=>{el.scrollTop=el.scrollHeight;});
    f.state.emit('tool',{event:'tool.started',tool:'terminal',tool_call_id:'fixture-tool',summary:'Run: isolated fixture'});
    await page.locator('.live-message .tool-preview[data-status=running]').waitFor({state:'attached'});
    await page.getByRole('button',{name:'Settings',exact:true}).click();
    await eventually(()=>f.state.clients.size===0,'leaving chat closes SSE');
    await open();
    await eventually(()=>f.state.connections.length===2,'reopening attaches to the same run');
    assert.equal(await owns().count(),1,'snapshot restores own accepted prompt exactly once');
    await page.locator('.live-message .tool-preview[data-status=running]').waitFor({state:'attached'});
    assert.equal(await page.getByRole('button',{name:'Send message'}).count(),0);assert.equal(await page.getByRole('button',{name:'Stop run'}).isEnabled(),true);

    f.state.disconnect();
    const notice=page.locator('.notice');
    await notice.filter({hasText:'Live connection interrupted'}).waitFor();
    await eventually(()=>f.state.connections.length===3,'native EventSource reconnects over HTTP');
    await notice.waitFor({state:'hidden'});
    assert.equal(f.state.connections.at(-1).lastEventId,'1','browser sends Last-Event-ID on reconnect');
    f.state.emit('delta',{text:'Fixture partial answer.'});
    await page.getByText('Fixture partial answer.',{exact:true}).waitFor();
    assert.equal(await notice.isVisible(),false,'successful events keep interruption warning cleared');

    // No local run pointer survives. A fresh app must discover the run from /messages.
    await page.evaluate(()=>{localStorage.clear();sessionStorage.clear();});
    await page.reload();await open();
    await eventually(()=>f.state.connections.length===4,'cleared-storage reload discovers snapshot run');
    assert.equal(await owns().count(),1,'cold reload restores prompt exactly once');
    await page.locator('.live-message .tool-preview[data-status=running]').waitFor({state:'attached'});
    f.state.emit('tool',{event:'tool.completed',tool:'terminal',tool_call_id:'fixture-tool',error:false});
    f.state.run={...f.state.run,status:'completed',output:answer};
    f.state.emit('done',{status:'completed',output:answer});
    await page.getByText(answer,{exact:true}).waitFor();
    assert.equal(await outputs().count(),1,'terminal answer replaces delta exactly once');
    assert.equal(await page.getByText('Fixture partial answer.',{exact:true}).count(),0);
    await visibleInMessages(outputs(),'final answer visible above composer at 390x450',visibilityFailures);
    assert.equal(await page.getByRole('button',{name:'Send message'}).isEnabled(),true);
    await eventually(()=>f.state.clients.size===0,'completion closes SSE');

    f.state.persisted=true;
    // Even a stale pointer must not override the authoritative run:null snapshot.
    await page.evaluate(()=>localStorage.setItem('hermes:fixture-owner:run:lifecycle-session','fixture-run'));
    await page.getByRole('button',{name:'Settings',exact:true}).click();await open();
    await page.getByText(answer,{exact:true}).waitFor();
    assert.equal(await owns().count(),1,'persisted history contains own prompt once');
    assert.equal(await outputs().count(),1,'persisted final answer is not duplicated by stale run');
    assert.equal(await page.locator('.live-message').count(),0,'run:null suppresses live reconstruction');
    await visibleInMessages(outputs(),'persisted answer remains visible in short viewport',visibilityFailures);
    assert.equal(f.state.connections.length,4,'persisted reopen does not reconnect');
    assert.equal(f.state.posts.length,1,'navigation/reconnect/reload never resubmits');
    assert.equal(f.state.posts[0].input,prompt);
    assert.equal(f.state.posts[0].session_id,session.id);
    assert.ok(f.state.posts[0].idempotency_key);
    assert.ok(f.state.snapshots.every(snapshot=>snapshot.latest==='true'));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'no horizontal overflow');
    const send=await page.getByRole('button',{name:'Send message'}).boundingBox();
    assert.ok(send.y+send.height<=450,'composer remains within short viewport');
    assert.deepEqual(f.state.unexpected,[],'no unknown fixture requests');
    assert.deepEqual(external,[],'all traffic stays on fixture origin');
    assert.deepEqual(errors,[],'no console errors or uncaught JavaScript exceptions');
    assert.deepEqual(visibilityFailures,[],'short viewport visibility regressions');
  } finally {await browser?.close();await f.close();}
});
