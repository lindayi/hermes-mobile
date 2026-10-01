import {generatedAssets} from './generated-assets.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdtemp,rm,mkdir,copyFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createRequire} from 'node:module';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');

// Offline generated-release fixture. Unknown writes are rejected, never forwarded.
async function fixture(){
 const temp=await mkdtemp(join(tmpdir(),'fold-layout-'));let browser,server;
 const close=async()=>{try{await browser?.close();}finally{try{if(server){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}}finally{await rm(temp,{recursive:true,force:true});}}};
 try{
  const dir=generatedAssets(temp),state={calls:[],owner:'synthetic-owner',jobs:[
   {id:'daily/1',name:'Daily report '+ 'longunbrokenname'.repeat(9),enabled:true,schedule:'0 9 * * *',prompt:'Synthetic daily prompt',timezone:'UTC',last_status:'success'},
   {id:'paused',name:'Paused script',enabled:false,schedule:{kind:'interval',minutes:60},last_status:'failed'}
  ]};
  server=createServer(async(req,res)=>{
   const path=new URL(req.url,'http://localhost').pathname;
   if(path.startsWith('/hermes/app-api/')){
    const p=path.slice('/hermes/app-api'.length);let raw='';for await(const chunk of req)raw+=chunk;
    state.calls.push({path:p,method:req.method,body:raw?JSON.parse(raw):null});let data;
    if(req.method!=='GET'){res.writeHead(403,{'Content-Type':'application/json'}).end(JSON.stringify({detail:'Unexpected fixture mutation'}));return;}
    if(p==='/auth/me')data={user:{id:state.owner,profile:'default',status:'ready'},csrf_token:'fixture-csrf'};
    else if(p==='/sessions')data={items:[],total:0};
    else if(p==='/jobs')data={items:state.jobs};
    else if(p==='/inbox')data={items:[{id:'read',title:'Synthetic read notification',body:'Kept report',read:true}],read_count:1};
    else data={items:[]};
    res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify(data));return;
   }
   const relative=path.replace(/^\/hermes\//,'')||'index.html';
   if(relative.includes('..')){res.writeHead(400).end();return;}
   try{const body=await readFile(join(dir,relative));res.writeHead(200,{'Content-Type':({html:'text/html',js:'text/javascript',mjs:'text/javascript',css:'text/css',svg:'image/svg+xml',webmanifest:'application/manifest+json'})[relative.split('.').pop()]||'application/octet-stream'}).end(body);}catch{res.writeHead(404).end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const context=await browser.newContext({viewport:{width:320,height:844},hasTouch:true,isMobile:true}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);
  await page.getByRole('button',{name:'Jobs',exact:true}).waitFor();
  return {page,state,errors,temp,close};
 }catch(error){await close().catch(()=>{});throw error;}
}
async function screenshot(h,name){
 const path=join(process.env.HERMES_TEST_ARTIFACT_DIR||h.temp,name+'.png');
 await h.page.screenshot({path});
 // Explicit opt-in keeps only curated PNG evidence after managed scratch cleanup.
 if(process.env.HERMES_LAYOUT_PROOF_DIR){
  await mkdir(process.env.HERMES_LAYOUT_PROOF_DIR,{recursive:true,mode:0o700});
  await copyFile(path,join(process.env.HERMES_LAYOUT_PROOF_DIR,name+'.png'));
 }
}
const noWrites=state=>assert.deepEqual(state.calls.filter(c=>c.method!=='GET'),[],'folding and cancelled controls never mutate or request step-up');

test('Inbox toolbar shares the title row and is right aligned at mobile and tablet widths',async()=>{
 const h=await fixture(),{page,state}=h;try{
  await page.getByRole('button',{name:'Inbox',exact:true}).tap();await page.locator('.inbox-heading').waitFor();
  const toolbar=page.locator('.inbox-heading .actions'),buttons=toolbar.getByRole('button');
  assert.deepEqual(await buttons.evaluateAll(nodes=>nodes.map(el=>el.getAttribute('aria-label')||el.textContent)),['Refresh','Mark all read','Clear read']);
  for(const width of [320,359,360,375,390,480,481,759,760,768,1024]){
   await page.setViewportSize({width,height:844});
   await screenshot(h,`inbox-toolbar-${width}`);
   const boxes=await buttons.evaluateAll(nodes=>nodes.map(el=>{const b=el.getBoundingClientRect(),s=getComputedStyle(el);return {x:b.x,y:b.y,width:b.width,height:b.height,scroll:el.scrollWidth,client:el.clientWidth,font:s.fontSize,padding:s.padding,minWidth:s.minWidth};}));
   for(const [i,box] of boxes.entries()){
    assert.ok(box.width>=44 && box.height>=44,`${width}px adequate target ${i}`);
    assert.ok(Math.abs(box.y-boxes[0].y)<1,`${width}px controls share a single row`);
    assert.ok(box.x>=0 && box.x+box.width<=width,`${width}px control is within viewport: ${JSON.stringify(boxes)}`);
    assert.ok(box.scroll<=box.client,`${width}px full label fits without clipping`);
    if(i)assert.ok(box.x>=boxes[i-1].x+boxes[i-1].width,`${width}px controls do not overlap`);
   }
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${width}px no page overflow`);
   const heading=await page.locator('.inbox-heading h1').boundingBox();
   assert.ok(heading.x+heading.width<=boxes[0].x,`${width}px toolbar sits to the right of title without overlap`);
   assert.ok(Math.abs(heading.y+heading.height/2-(boxes[0].y+boxes[0].height/2))<1,`${width}px title and toolbar share the same centered row`);
   const row=await page.locator('.inbox-heading').boundingBox();
   assert.ok(Math.abs(boxes.at(-1).x+boxes.at(-1).width-(row.x+row.width))<1,`${width}px toolbar is flush right`);
   assert.ok(heading.height<44,`${width}px title stays on one line`);
  }
  await buttons.nth(0).focus();await page.keyboard.press('Tab');assert.equal(await buttons.nth(1).evaluate(el=>el===document.activeElement),true);
  await page.keyboard.press('Tab');assert.equal(await buttons.nth(2).evaluate(el=>el===document.activeElement),true);
  const before=state.calls.filter(c=>c.path==='/inbox').length;
  await page.getByRole('button',{name:'Refresh',exact:true}).tap();await page.locator('.inbox-heading').waitFor();
  assert.equal(state.calls.filter(c=>c.path==='/inbox').length,before+1);noWrites(state);assert.deepEqual(h.errors,[]);
 }finally{await h.close();}
});

test('job folds survive route refresh and reload by stable owner/job identity',async()=>{
 const h=await fixture(),{page,state}=h;try{
  const jobs=page.getByRole('button',{name:'Jobs',exact:true});
  await jobs.tap();await page.locator('.job-list').waitFor();
  await page.locator('.job-list details').nth(1).locator('summary').tap();
  state.jobs.reverse();
  await jobs.tap();await page.locator('.job-list').waitFor();
  assert.equal(await page.locator('.job-list details').nth(0).evaluate(el=>el.open),true,'refresh preserves the expanded job even after reordering');
  assert.equal(await page.locator('.job-list details').nth(1).evaluate(el=>el.open),false);
  await page.reload();await jobs.tap();await page.locator('.job-list').waitFor();
  assert.equal(await page.locator('.job-list details').nth(0).evaluate(el=>el.open),true,'browser reload keeps session fold preference');
  await page.locator('.job-list details').nth(0).locator('summary').tap();
  await jobs.tap();await page.locator('.job-list').waitFor();
  assert.equal(await page.locator('.job-list details').nth(0).evaluate(el=>el.open),false,'collapsed preference survives refresh too');
  await page.locator('.job-list details').nth(0).locator('summary').tap();
  state.owner='another-synthetic-owner';await page.reload();await jobs.tap();await page.locator('.job-list').waitFor();
  assert.deepEqual(await page.locator('.job-list details').evaluateAll(nodes=>nodes.map(el=>el.open)),[false,false],'different owner starts collapsed');
  noWrites(state);assert.deepEqual(h.errors,[]);
 }finally{await h.close();}
});

 test('generated scheduled jobs start folded with safe independent touch and keyboard disclosures',async()=>{
  const h=await fixture(),{page,state}=h;try{
   await page.getByRole('button',{name:'Jobs',exact:true}).tap();await page.locator('.job-list').waitFor();
   const folds=page.locator('.job-list details');
   assert.equal(await folds.count(),2,'each scheduled job has its own native disclosure');
   const first=folds.nth(0),second=folds.nth(1),summary=first.locator('summary');
   assert.equal(await first.evaluate(el=>el.open),false);assert.equal(await second.evaluate(el=>el.open),false);
   assert.match(await summary.innerText(),/Daily report/);assert.match(await summary.innerText(),/SCHEDULED/);assert.match(await summary.innerText(),/0 9 \* \* \*/);
   assert.match(await second.locator('summary').innerText(),/Paused script/);assert.match(await second.locator('summary').innerText(),/PAUSED/);assert.match(await second.locator('summary').innerText(),/minutes.*60/);
   assert.equal(await first.getByRole('button',{name:'Pause',exact:true}).isVisible(),false);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   const box=await summary.boundingBox();assert.ok(box.height>=44 && box.width>=44);
   await screenshot(h,'jobs-folded-320');
   await summary.tap();assert.equal(await first.evaluate(el=>el.open),true);assert.equal(await second.evaluate(el=>el.open),false);
   assert.equal(await first.getByRole('button',{name:'Pause',exact:true}).isVisible(),true);assert.match(await first.innerText(),/Synthetic daily prompt/);assert.match(await first.innerText(),/Timezone: UTC/);assert.match(await first.innerText(),/Last outcome: success/);
   await first.getByRole('button',{name:'Delete',exact:true}).tap();await page.getByRole('dialog',{name:'Delete this job?'}).waitFor();
   await page.getByRole('button',{name:'Cancel',exact:true}).tap();noWrites(state);
   await summary.focus();await page.keyboard.press('Space');assert.equal(await first.evaluate(el=>el.open),false);
   await second.locator('summary').focus();await page.keyboard.press('Enter');assert.equal(await second.evaluate(el=>el.open),true);assert.equal(await first.evaluate(el=>el.open),false);
   assert.equal(await second.getByRole('button',{name:'Resume',exact:true}).isVisible(),true);assert.match(await second.innerText(),/Script-based job/);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   await screenshot(h,'jobs-expanded-320');
   for(const width of [375,390,768,1024]){
    await page.setViewportSize({width,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${width}px jobs do not overflow`);
    for(const control of await second.getByRole('button').all()){
     const b=await control.boundingBox();assert.ok(b.width>=44 && b.height>=44 && b.x>=0 && b.x+b.width<=width);
    }
    await screenshot(h,`jobs-expanded-${width}`);
   }
   noWrites(state);assert.deepEqual(h.errors,[]);
  }finally{await h.close();}
 });
