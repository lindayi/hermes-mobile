import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdir,mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const dir=process.env.HERMES_FRONTEND_DIR?process.env.HERMES_FRONTEND_DIR.replace(/\/$/,'')+'/':fileURLToPath(new URL('../../frontend/',import.meta.url));
test('native next-turn model dropdown fits mobile with no overlay and preserves separate tools and Stop',{timeout:45000},async()=>{
 const selected='fixture-long-model-identifier-for-narrow-mobile-layout';const posts=[],clients=new Set();
 const artifacts=process.env.HERMES_TEST_ARTIFACT_DIR || process.env.HERMES_MODEL_ARTIFACT_DIR || await mkdtemp(join(tmpdir(),'hmodel-proof-'));
 await mkdir(artifacts,{recursive:true});
 const server=createServer(async(req,res)=>{const u=new URL(req.url,'http://fixture'),p=u.pathname.replace('/hermes/app-api','');const json=obj=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(obj));};
  if(u.pathname.startsWith('/hermes/app-api/')){
   if(p==='/auth/me')return json({user:{id:'owner',role:'owner',status:'ready'},csrf_token:'fixture'});
   if(p==='/sessions')return json({items:[{id:'s',title:'Model fixture'}],total:1});
   if(p==='/sessions/s/messages')return json({items:[{role:'user',content:'Earlier question'},{role:'tool',name:'terminal',status:'success',summary:'Run: isolated fixture',content:'fixture result'},{role:'assistant',content:'Earlier answer'}],run:null});
   if(p==='/sessions/s/model-options')return json({available:true,default:{provider:'fixture',model:selected},models:[{id:selected,provider:'fixture',label:selected,reasoning_efforts:[]}]});
   if(p==='/runs'&&req.method==='POST'){let body='';for await(const chunk of req)body+=chunk;const data=JSON.parse(body);posts.push(data);return json({id:'r',session_id:'s',input:data.input,status:'running',output:''});}
   if(p==='/runs/r/controls')return json({steering:false,attempts:[]});
   if(p==='/runs/r/events'){
    res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache','Connection':'keep-alive'});res.write('retry: 1000\n\n');clients.add(res);res.on('close',()=>clients.delete(res));return;
   }
   return json({items:[]});
  }
  const rel=u.pathname.replace(/^\/hermes\//,'')||'index.html';if(rel.includes('..')){res.writeHead(400).end();return;}
  try{const body=await readFile(dir+rel);res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',mjs:'text/javascript',js:'text/javascript',svg:'image/svg+xml',webmanifest:'application/manifest+json',png:'image/png'})[rel.split('.').pop()]||'application/octet-stream'});res.end(body);}catch{res.writeHead(404).end();}
 });
 let browser;
 try{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const page=await browser.newPage({viewport:{width:320,height:568},serviceWorkers:'block'});page.setDefaultTimeout(10000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);await page.getByRole('button',{name:'Model fixture',exact:true}).click();const input=page.getByRole('textbox',{name:'Message Hermes',exact:true});await input.fill('Preserve my draft');
  const model=page.getByRole('combobox',{name:'Model',exact:true});await model.waitFor();
  assert.equal(await model.evaluate(el=>el.tagName),'SELECT');assert.equal(await model.inputValue(),'');
  assert.match(await model.locator('option:checked').textContent(),/Default/);
  assert.equal(await model.locator('option').count(),2,'default-only reasoning never invents effort choices');
  const tools=page.locator('.messages .tool-activity');assert.equal(await tools.count(),1);
  const originalTool=await tools.elementHandle();
  await model.focus();await page.keyboard.press('Tab');assert.equal(await model.evaluate(el=>document.activeElement===el),false,'native select does not trap keyboard focus');
  await model.click();assert.equal(await page.locator('[role=dialog],.dialog-overlay').count(),0,'native popup never creates a heavy application overlay');await page.keyboard.press('Escape');
  await model.selectOption('0');assert.equal(await input.inputValue(),'Preserve my draft');assert.equal(posts.length,0);
  assert.equal(await page.getByRole('combobox',{name:'Reasoning effort'}).count(),0);
  assert.equal(await page.getByRole('button',{name:'Apply',exact:true}).count(),0);
  const geometry=[];
  for(const theme of ['light','dark']){
   await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
   for(const width of [320,390,844,1280]){
    await page.setViewportSize({width,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'long selected model never overflows');
    const b=await model.boundingBox(),send=await page.getByRole('button',{name:'Send message',exact:true}).boundingBox();
    assert.ok(b.width>=44&&b.height>=44);
    assert.ok(b.x>=0&&b.x+b.width<=width);assert.ok(send.x+send.width<=width,'Send stays onscreen');
    assert.ok(b.x+b.width<=send.x || send.x+send.width<=b.x,'model dropdown never overlaps Send');
    assert.equal(await originalTool.evaluate(el=>el.isConnected),true,'selection preserves original tool disclosure identity');
    geometry.push({theme,width,model:b,send});
    if(width<=390)await page.screenshot({path:join(artifacts,`model-${width}-${theme}.png`)});
   }
  }
  await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'Send message',exact:true}).click();
  const stop=page.getByRole('button',{name:'Stop run',exact:true});await stop.waitFor();assert.equal(posts.length,1);assert.deepEqual(posts[0].selection,{provider:'fixture',model:selected});
  assert.equal(await stop.evaluate(el=>!!el.closest('.live-activity-heading')&&!el.closest('.composer')),true,'Stop remains in Activity, never in model/send controls');
  assert.equal(await originalTool.evaluate(el=>el.isConnected),true,'original tool history survives admission');
  assert.equal(await page.locator('.tool-activity button[aria-label="Stop run"]').count(),0);
  assert.equal(await page.locator('[role=dialog],.dialog-overlay').count(),0);
  await page.screenshot({path:join(artifacts,'model-390-active-stop.png')});
  for(const client of clients)client.write(`id: 1\nevent: done\ndata: ${JSON.stringify({status:'completed',output:'Synthetic response'})}\n\n`);
  await page.getByText('Synthetic response',{exact:true}).waitFor();assert.deepEqual(errors,[]);
  console.log(JSON.stringify({artifacts,frontend:dir,geometry}));
  assert.deepEqual(geometry.filter(({model})=>model.height>44),[],'native dropdown stays a single compact 44px row');
 }finally{await browser?.close();for(const res of clients)res.end();server.closeAllConnections();if(server.listening)await new Promise(r=>server.close(r));}
});
