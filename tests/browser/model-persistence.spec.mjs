import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const dir=process.env.HERMES_FRONTEND_DIR ? process.env.HERMES_FRONTEND_DIR.replace(/\/$/,'')+'/' : fileURLToPath(new URL('../../frontend/',import.meta.url));
const preferenceKey='hermes:owner:model-controls:'+JSON.stringify(['owner','s']);
const selection={model:'claude-fixture',provider:'fixture',reasoning_effort:'high'};
async function fixture() {
 const posts=[],errors=[],held=[];
 const state={owner:'owner',session:'s',catalog:'ready',catalogReads:0};
 const server=createServer(async(req,res)=>{
  const url=new URL(req.url,'http://fixture'),p=url.pathname.replace('/hermes/app-api','');
  const json=value=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};
  if(url.pathname.startsWith('/hermes/app-api/')){
   if(p==='/auth/me')return json({user:{id:state.owner,role:'owner',status:'ready'},csrf_token:'fixture'});
   if(p==='/sessions')return json({items:[{id:state.session,title:'Persistence fixture'}],total:1});
   if(p===`/sessions/${state.session}/messages`)return json({items:[],run:null});
   if(p===`/sessions/${state.session}/model-options`){
    state.catalogReads++;
    if(state.catalog==='held'){held.push(res);return;}
    if(state.catalog==='failed')return res.writeHead(503).end();
    return json({available:true,default:{model:'gpt-fixture',provider:'fixture'},models:[
    {id:'gpt-fixture',provider:'fixture',label:'GPT default fixture',reasoning_efforts:['low']},
    ...(state.catalog==='removed-model'?[]:[{id:'claude-fixture',provider:'fixture',label:'Claude fixture',reasoning_efforts:state.catalog==='removed-effort'?[]:['high']}])]});
   }
   if(p==='/runs'&&req.method==='POST'){
    let body='';for await(const chunk of req)body+=chunk;
    const data=JSON.parse(body);posts.push(data);
    return json({id:`r${posts.length}`,session_id:state.session,input:data.input,status:'completed',output:'Synthetic completion'});
   }
   return json({items:[]});
  }
  const rel=url.pathname.replace(/^\/hermes\//,'')||'index.html';
  if(rel.includes('..'))return res.writeHead(400).end();
  try{const body=await readFile(dir+rel);res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',mjs:'text/javascript',js:'text/javascript',svg:'image/svg+xml'})[rel.split('.').pop()]||'application/octet-stream'});res.end(body);}catch{res.writeHead(404).end();}
 });
 let browser,context,page;
 async function open(storageState){
  context=await browser.newContext({storageState,viewport:{width:390,height:844},serviceWorkers:'block'});
  await context.addInitScript(()=>{window.initialSessionKeys=Object.keys(sessionStorage);});
  page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);
  assert.deepEqual(await page.evaluate(()=>window.initialSessionKeys),[],'fresh context starts without sessionStorage before app writes');
  const response=state.catalog==='held'?null:page.waitForResponse(r=>r.url().endsWith('/model-options'));
  await page.getByRole('button',{name:'Persistence fixture',exact:true}).click();
  if(response)await response;
  await page.getByRole('textbox',{name:'Message Hermes',exact:true}).waitFor();
  if(!['held','failed'].includes(state.catalog))await page.getByRole('combobox',{name:'Model',exact:true}).waitFor();
 }
 try{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  await open();
 }catch(error){await browser?.close();server.closeAllConnections();if(server.listening)await new Promise(r=>server.close(r));throw error;}
 return {posts,errors,state,get page(){return page;},
  async reopen(){const saved=await context.storageState();await context.close();await open(saved);},
  async choose(){await page.getByRole('combobox',{name:'Model',exact:true}).selectOption('1');await page.getByRole('combobox',{name:'Reasoning effort',exact:true}).selectOption('high');},
  async send(text){await page.getByRole('textbox',{name:'Message Hermes',exact:true}).fill(text);await page.getByRole('button',{name:'Send message',exact:true}).click();await page.getByText('Synthetic completion',{exact:true}).waitFor();},
  async close(){await browser?.close();for(const res of held)res.end();server.closeAllConnections();if(server.listening)await new Promise(r=>server.close(r));}
 };
}

test('explicit Default stays reset across context closure and does not resurrect legacy storage',{timeout:45000},async()=>{
 const h=await fixture();try{
  await h.choose();await h.page.getByRole('combobox',{name:'Model',exact:true}).selectOption('');
  await h.reopen();
  await h.page.evaluate(({key,value})=>sessionStorage.setItem(key,JSON.stringify(value)),{key:preferenceKey,value:selection});
  await h.page.reload();await h.page.getByRole('button',{name:'Persistence fixture',exact:true}).click();
  await h.page.getByRole('combobox',{name:'Model',exact:true}).waitFor();
  assert.equal(await h.page.getByRole('combobox',{name:'Model',exact:true}).inputValue(),'');
  await h.send('Use the configured default');assert.equal(Object.hasOwn(h.posts[0],'selection'),false);
  assert.equal(await h.page.evaluate(key=>sessionStorage.getItem(key),preferenceKey),null);
 }finally{await h.close();}
});
for(const mode of ['held','failed','removed-model','removed-effort'])test(`reopened ${mode} catalog cannot silently dispatch Default`,{timeout:45000},async()=>{
 const h=await fixture();try{
  await h.choose();h.state.catalog=mode;await h.reopen();
  await h.page.getByRole('textbox',{name:'Message Hermes',exact:true}).fill('Preserve the chosen model');
  await h.page.getByRole('button',{name:'Send message',exact:true}).click();
  await h.page.getByText('Saved model choice could not be checked. Reload before sending.',{exact:true}).waitFor();
  assert.equal(h.posts.length,0);
  assert.equal(await h.page.getByRole('textbox',{name:'Message Hermes',exact:true}).inputValue(),'Preserve the chosen model');
  assert.deepEqual(await h.page.evaluate(key=>JSON.parse(localStorage.getItem(key)),preferenceKey),selection);
  if(mode.startsWith('removed'))assert.equal(await h.page.getByRole('combobox',{name:'Model',exact:true}).inputValue(),'stale');
  h.state.catalog='ready';await h.reopen();await h.send('Recovered catalog');assert.deepEqual(h.posts[0].selection,selection);
 }finally{await h.close();}
});
for(const scope of ['owner','session'])test(`reopened different ${scope} does not inherit the saved override`,{timeout:45000},async()=>{
 const h=await fixture();try{
  await h.choose();h.state[scope]='other';await h.reopen();
  await h.send('Independent default');assert.equal(Object.hasOwn(h.posts[0],'selection'),false);
  assert.deepEqual(await h.page.evaluate(key=>JSON.parse(localStorage.getItem(key)),preferenceKey),selection);
  h.state[scope]=scope==='owner'?'owner':'s';await h.reopen();await h.send('Original selection');
  assert.deepEqual(h.posts[1].selection,selection);
 }finally{await h.close();}
});

test('closed app context restores non-default model and reasoning in the actual next POST',{timeout:45000},async()=>{
 const h=await fixture();try{
  await h.choose();assert.equal(h.posts.length,0);
  await h.reopen();
  // Check dispatch first: a retained label alone cannot establish the fix.
  await h.send('After closing the app');
  assert.equal(h.posts.length,1);
  assert.deepEqual(h.posts[0].selection,selection);
  assert.equal(await h.page.getByRole('combobox',{name:'Model',exact:true}).inputValue(),'1');
  assert.equal(await h.page.getByRole('combobox',{name:'Reasoning effort',exact:true}).inputValue(),'high');
  assert.deepEqual(h.errors,[]);
 }finally{await h.close();}
});
