import {generatedAssets} from './generated-assets.mjs';
import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import {withoutValidatedPresence} from './push-fixture-contract.mjs';
import {createServer} from 'node:http';
import {readFile, mkdtemp, rm} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const root=fileURLToPath(new URL('../../',import.meta.url));
const temporary=await mkdtemp((process.env.TMPDIR || '/tmp')+'/b-');
let publicDir;
after(()=>rm(temporary,{recursive:true,force:true}));
// Honor exact staged assets; build only for source-tree checks.
try {
 publicDir=generatedAssets(temporary);
} catch(error) {await rm(temporary,{recursive:true,force:true});throw error;}
const unknown={id:'a',run_id:'r',user_id:'owner',idempotency_key:'exact-server-key',input:'Saved guidance',status:'unknown',updated_at:2};
async function fixture(options={}) {
 const state={status:'running',records:[unknown],steering:true,posts:[],clients:new Set(),held:[],hold:false,controls:0,...options};
 const json=(res,value)=>{res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
 state.emit=(name,data)=>{for(const res of state.clients)res.write(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`);};
 state.release=()=>{for(const {res,value} of state.held.splice(0))json(res,value);};
 const server=createServer(async(req,res)=>{
  try {
   const url=new URL(req.url,'http://fixture'),p=url.pathname.replace('/hermes/app-api','');
   if(url.pathname.startsWith('/hermes/app-api/')) {
    if(req.method==='POST') {let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);state.posts.push({p,body,method:req.method,csrf:req.headers['x-csrf-token']});return json(res,{...body,run_id:'r',status:'accepted_unconfirmed',updated_at:3});}
    if(p==='/auth/me')return json(res,{user:{id:'owner',status:'ready'},csrf_token:'fixture'});
    if(p==='/sessions')return json(res,{items:[{id:'s',title:'Fixture'}],total:1});
    if(p.endsWith('/messages'))return json(res,{items:[{role:'user',kind:'guidance',content:unknown.input,run_id:'r',steering_id:'a',idempotency_key:unknown.idempotency_key,steering_status:'unknown'},...Array.from({length:20},(_,i)=>({role:'assistant',content:`Earlier fixture message ${i}.`}))],run:{id:'r',session_id:'s',status:state.status,input:'Original'}});
    if(p==='/runs/r')return json(res,{id:'r',session_id:'s',status:state.status});
    if(p.endsWith('/controls')){state.controls++;const value={steering:state.steering,attempts:state.records};if(state.hold){state.held.push({res,value});return;}return json(res,value);}
    if(p.endsWith('/events')){res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache'});res.write(': ready\n\n');state.clients.add(res);res.on('close',()=>state.clients.delete(res));return;}
    return json(res,{items:[]});
   }
   const rel=url.pathname.replace(/^\/hermes\//,'') || 'index.html';
   const body=await readFile(join(publicDir,rel));res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',mjs:'text/javascript',js:'text/javascript',svg:'image/svg+xml'})[rel.split('.').pop()] || 'application/octet-stream'});res.end(body);
  }catch{res.writeHead(404).end();}
 });
 let browser;
 const close=async()=>{try{await browser?.close();}finally{server.closeAllConnections();if(server.listening)await new Promise(resolve=>server.close(resolve));}};
 try {
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER || '/usr/bin/google-chrome',headless:true,env:{...process.env,TMPDIR:temporary},args:['--no-sandbox','--disable-dev-shm-usage']});
  const page=await browser.newPage({viewport:{width:320,height:740},serviceWorkers:'block'});page.setDefaultTimeout(4000);
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);
  await page.getByRole('button',{name:'Fixture',exact:true}).click();
  await page.locator('.guidance-status').waitFor();
  await until(()=>state.controls>0,'controls requested');
  await page.waitForFunction(()=>document.querySelector('.send')?.dataset.state!=='idle');
  return {page,state,errors,close};
 }catch(error){await close();throw error;}
}
async function until(check,message){const deadline=Date.now()+4000;while(Date.now()<deadline){if(await check())return;await new Promise(resolve=>setTimeout(resolve,20));}assert.fail(message);}
const retry=page=>page.getByRole('button',{name:'Retry this steering request',exact:true});

for(const status of ['accepted_unconfirmed','rejected','not_delivered'])test(`live ${status} receipt removes retry before its controls GET resolves`,{timeout:15000},async()=>{
 const f=await fixture();
 try {
  await retry(f.page).waitFor();
  f.state.hold=true;
  f.state.emit('steering',{...unknown,status,updated_at:3});
  await until(()=>f.state.held.length>0,'receipt-triggered controls GET is pending');
  assert.match(await f.page.locator('.guidance-status').textContent(),status==='accepted_unconfirmed'?/Accepted.*delivery unconfirmed/:status==='not_delivered'?/Not delivered/:/Delivery unknown/);
  assert.equal(await retry(f.page).count(),0,'receipt must retire retry synchronously, not wait for network');
  assert.equal(withoutValidatedPresence(f.state.posts).length,0);assert.deepEqual(f.errors,[]);
 }finally{await f.close();}
});

test('fresh accepted guidance becomes terminal unknown history, never an actionable retry',{timeout:15000},async()=>{
 const f=await fixture({records:[{...unknown,status:'accepted_unconfirmed'}]});
 try {
  await until(async()=>(await f.page.locator('.guidance-status').textContent()).includes('Accepted'),'accepted receipt loaded');
  assert.equal(await retry(f.page).count(),0);
  f.state.records=[{...unknown,updated_at:3}];f.state.status='cancelled';f.state.emit('done',{status:'cancelled'});
  await until(async()=>(await f.page.locator('.guidance-status').textContent()).includes('Delivery unknown'),'terminal consumption remains unknown');
  assert.equal(await retry(f.page).count(),0);assert.equal(withoutValidatedPresence(f.state.posts).length,0);assert.deepEqual(f.errors,[]);
 }finally{await f.close();}
});

test('fresh generated browser retires terminal unknown retry while preserving truthful guidance',{timeout:30000},async()=>{
 const f=await fixture();
 try {
  await retry(f.page).waitFor();
  const editor=f.page.getByRole('textbox',{name:'Message Hermes',exact:true});await editor.fill('Unrelated draft');
  await editor.evaluate(el=>{el.focus();el.setSelectionRange(2,7);document.querySelector('.messages').scrollTop=40;});
  f.state.status='completed';f.state.emit('done',{status:'completed'});
  await f.page.waitForFunction(()=>document.querySelector('.send')?.dataset.state==='completed');
  assert.equal(await retry(f.page).count(),0,'terminal unknown history must not leave a disabled Retry in the composer');
  assert.match(await f.page.locator('.guidance-status').textContent(),/Delivery unknown/);
  assert.equal(await editor.inputValue(),'Unrelated draft');
  assert.deepEqual(await editor.evaluate(el=>[el.selectionStart,el.selectionEnd]),[2,7]);
  assert.equal(await f.page.locator('.messages').evaluate(el=>el.scrollTop),40);
  f.state.hold=true;await f.page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await until(()=>f.state.held.length>0,'focus controls pending');f.state.release();
  await f.page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.equal(await retry(f.page).count(),0);assert.equal(withoutValidatedPresence(f.state.posts).length,0);assert.deepEqual(f.errors,[]);
 }finally{await f.close();}
});
