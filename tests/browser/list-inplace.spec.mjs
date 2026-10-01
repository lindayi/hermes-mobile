import {generatedAssets} from './generated-assets.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdtemp,mkdir,rm} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const repo=fileURLToPath(new URL('../../',import.meta.url));
const result=(title,total=91)=>({items:[{id:'fixture',title,source:'cli',run_status:'idle'}],total,deletion_available:true});
const frames=page=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
async function fixture(width=390){
 const tmp=await mkdtemp((process.env.TMPDIR || '/tmp')+'/b-');let browser;
 const close=async()=>{try{await browser?.close();}finally{await rm(tmp,{recursive:true,force:true});}};
 try{
  await mkdir(tmp+'/home');const env={...process.env,TMPDIR:tmp,HOME:tmp+'/home',HERMES_HOME:tmp+'/home/.hermes'};
  const dir=generatedAssets(tmp);
  assert.match(await readFile(dir+'/index.html','utf8'),/app\.[a-f0-9]+\.js/);
  browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage'],env});
  const page=await browser.newPage({viewport:{width,height:740},serviceWorkers:'block'});page.setDefaultTimeout(5000);
  const state={slow:false,reads:[],errors:[],calls:[]};page.on('pageerror',e=>state.errors.push(e.message));
  await page.route('**/*',async route=>{
   const req=route.request(),url=new URL(req.url()),p=url.pathname.replace('/hermes/app-api','');
   const json=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
   if(url.pathname.startsWith('/hermes/app-api/')){
    state.calls.push({path:p,method:req.method(),query:url.search});
    if(p==='/auth/me')return json({user:{id:'synthetic-owner',status:'ready'},csrf_token:'fixture'});
    if(p==='/sessions'){
     if(!state.slow)return json(result('Original fixture'));
     return new Promise(resolve=>state.reads.push({url,async finish(body,status=200){await json(body,status);resolve();}}));
    }
    if(p.endsWith('/messages'))return json({items:[{role:'user',content:'Human fixture'},{role:'tool',kind:'runtime_notice',name:'Tool limit reached',status:'completed',content:'**Exact runtime fixture body**\n\nRetained process guidance.'},{role:'tool',kind:'context_compression',status:'completed',content:'Compression retained.'}]});
    return json({items:[]});
   }
   const rel=url.pathname.replace(/^\/hermes\//,'')||'index.html';if(rel.includes('..'))return route.abort();
   try{return route.fulfill({status:200,headers:{'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"},contentType:({html:'text/html',css:'text/css',mjs:'text/javascript',js:'text/javascript',svg:'image/svg+xml'})[rel.split('.').pop()]||'application/octet-stream',body:await readFile(dir+'/'+rel)});}catch{return route.fulfill({status:404,body:''});}
  });
  await page.goto('https://list-fixture.invalid/hermes/');await page.getByRole('button',{name:'Original fixture',exact:true}).waitFor();
  return {page,state,close,async read(index){await page.waitForFunction(()=>true);for(let i=0;i<100 && !state.reads[index];i++)await new Promise(r=>setTimeout(r,10));assert.ok(state.reads[index],'deferred HTTP read reached fixture');return state.reads[index];}};
 }catch(error){await close();throw error;}
}
async function remember(page){
 await page.evaluate(()=>{
  window.kept={main:document.querySelector('#main'),heading:document.querySelector('.page-heading'),search:document.querySelector('input[type=search]'),filter:document.querySelector('.filter-toggle'),list:document.querySelector('.session-list'),nav:document.querySelector('nav')};
  window.mainRemovals=[];window.observer=new MutationObserver(records=>{for(const r of records)if(r.target===kept.main)mainRemovals.push(...[...r.removedNodes].filter(n=>Object.values(kept).includes(n)).map(n=>n.nodeName));});observer.observe(kept.main,{childList:true});
 });
}
async function retained(page){
 assert.deepEqual(await page.evaluate(()=>({identical:kept.main===document.querySelector('#main')&&kept.heading===document.querySelector('.page-heading')&&kept.search===document.querySelector('input[type=search]')&&kept.filter===document.querySelector('.filter-toggle')&&kept.list===document.querySelector('.session-list')&&kept.nav===document.querySelector('nav'),removed:mainRemovals,loading:document.querySelector('#main > .loading')!==null})),{identical:true,removed:[],loading:false});
}
for(const width of [320,390])test(`generated ${width}px search/filter/page requests retain shell, input selection and authoritative results`,{timeout:30000},async()=>{
 const h=await fixture(width),{page,state}=h;try{
  await remember(page);state.slow=true;
  await page.getByRole('button',{name:'Search conversations',exact:true}).click();const beforeY=(await page.getByRole('button',{name:'Original fixture',exact:true}).boundingBox()).y;const search=page.getByRole('searchbox',{name:'Search conversations'});await search.fill('old body query');await search.press('Enter');await h.read(0);
  assert.equal((await page.getByRole('button',{name:'Original fixture',exact:true}).boundingBox()).y,beforeY,'pending feedback does not shift existing rows');
  await retained(page);assert.equal(await page.getByRole('button',{name:'Original fixture',exact:true}).count(),1);
  await search.fill('latest body / literal%');await search.evaluate(el=>el.setSelectionRange(2,8));await search.press('Enter');const newest=await h.read(1);assert.equal(newest.url.searchParams.get('q'),'latest body / literal%');await newest.finish(result('Body-only beyond title',97));await page.getByRole('button',{name:'Body-only beyond title',exact:true}).waitFor();
  await state.reads[0].finish(result('Obsolete',500));await frames(page);await retained(page);
  assert.deepEqual(await search.evaluate(el=>[el===document.activeElement,el.selectionStart,el.selectionEnd]),[true,2,8]);assert.equal(await page.getByRole('button',{name:'Obsolete',exact:true}).count(),0);assert.match(await page.locator('.pagination').textContent(),/97 conversations/);
  await page.getByRole('button',{name:'Filter conversations',exact:true}).click();await page.getByRole('button',{name:'All',exact:true}).click();const all=await h.read(2);assert.equal(all.url.searchParams.get('kind'),'all');await retained(page);assert.equal(await page.locator('.filter-toggle').evaluate(el=>el===document.activeElement),true);
  await search.focus();await search.evaluate(el=>el.setSelectionRange(3,9));await all.finish(result('All body result',97));await page.getByRole('button',{name:'All body result',exact:true}).waitFor();assert.deepEqual(await search.evaluate(el=>[el===document.activeElement,el.selectionStart,el.selectionEnd]),[true,3,9]);
  await page.getByRole('button',{name:'Next',exact:true}).click();const next=await h.read(3);assert.equal(next.url.searchParams.get('offset'),'30');assert.equal(next.url.searchParams.get('q'),'latest body / literal%');await retained(page);assert.equal(await page.getByRole('button',{name:'All body result',exact:true}).count(),1);await next.finish(result('Server page two',97));await page.getByRole('button',{name:'Server page two',exact:true}).waitFor();await retained(page);
  assert.equal(await page.getByRole('button',{name:'Previous',exact:true}).isEnabled(),true);assert.equal(await page.locator('.list-summary').count(),0);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.deepEqual(state.errors,[]);assert.ok(state.calls.every(c=>c.method==='GET'));
 }finally{await h.close();}
});

test('generated inline retry and navigation/logout fences preserve previous results without private resurrection',{timeout:30000},async()=>{
 const h=await fixture(),{page,state}=h;try{
  await remember(page);state.slow=true;await page.getByRole('button',{name:'Search conversations',exact:true}).click();const search=page.getByRole('searchbox');await search.fill('failed body query');await search.press('Enter');await (await h.read(0)).finish({detail:'Fixture offline'},503);await page.getByRole('button',{name:'Try again',exact:true}).waitFor();await retained(page);assert.equal(await page.getByRole('button',{name:'Original fixture',exact:true}).count(),1);assert.doesNotMatch(await page.locator('.session-list').textContent(),/failed body/);assert.match(await page.locator('.pagination').textContent(),/91 conversations/);
  await page.getByRole('button',{name:'Try again',exact:true}).click();await (await h.read(1)).finish(result('Recovered',1));await page.getByRole('button',{name:'Recovered',exact:true}).waitFor();await retained(page);
  await search.fill('departing');await search.press('Enter');await h.read(2);await page.getByRole('button',{name:'Inbox',exact:true}).click();await page.getByRole('heading',{name:'Inbox',exact:true}).waitFor();await state.reads[2].finish({detail:'Old auth failure'},401);await frames(page);assert.equal(await page.getByRole('heading',{name:'Inbox',exact:true}).count(),1);
  state.slow=false;await page.locator('nav').getByRole('button',{name:'Chats',exact:true}).click();await page.getByRole('button',{name:'Original fixture',exact:true}).waitFor();state.slow=true;await page.getByRole('searchbox').fill('before logout');await page.getByRole('searchbox').press('Enter');await h.read(3);await page.getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('button',{name:'Sign out',exact:true}).click();await page.getByRole('heading',{name:'Sign in',exact:true}).waitFor();await state.reads[3].finish(result('Private late data',99));await frames(page);assert.equal(await page.locator('.session-list').count(),0);assert.equal(await page.getByRole('heading',{name:'Sign in',exact:true}).count(),1);assert.deepEqual(state.errors,[]);
 }finally{await h.close();}
});

test('generated tool-limit process detail is folded, accessible and distinct from the human turn',{timeout:20000},async()=>{
 const h=await fixture(320),{page,state}=h;try{
  await page.getByRole('button',{name:'Original fixture',exact:true}).click();const process=page.locator('details.runtime-notice.process-history');await process.waitFor();assert.equal(await process.evaluate(el=>el.open),false);assert.equal(await process.locator('.markdown').isVisible(),false);assert.equal(await page.locator('.user-message').count(),1);assert.equal(await page.locator('.context-compression').count(),1);
  await process.locator('summary').focus();await page.keyboard.press('Enter');assert.equal(await process.locator('.markdown').isVisible(),true);assert.match(await process.locator('.markdown').textContent(),/Exact runtime fixture body.*Retained process guidance/s);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.deepEqual(state.errors,[]);
 }finally{await h.close();}
});
