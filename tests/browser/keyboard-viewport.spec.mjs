import {artifactURL} from './artifacts.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const dir=process.env.HERMES_FRONTEND_DIR || fileURLToPath(new URL('../../frontend/',import.meta.url));
async function browserFixture(t,{visual=false,live=false,startupFailure=false,fallback=false}={}) {
 const server=createServer(async(req,res)=>{
  const url=new URL(req.url,'http://fixture'),p=url.pathname.replace('/hermes/app-api','');
  const json=o=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(o));};
  if(url.pathname.startsWith('/hermes/app-api/')) {
   if(p==='/auth/me')return json({user:{id:'keyboard-fixture',role:'owner',status:'ready'},csrf_token:'fixture'});
   if(p==='/sessions')return json({items:[{id:'s',title:'Keyboard fixture'}],total:1});
   if(p.endsWith('/messages'))return json({items:Array.from({length:35},(_,i)=>({id:i,role:i%2?'assistant':'user',content:`History ${i}: explicitly synthetic browser fixture. Long history retains reader position during keyboard changes.`})),...(live?{run:{id:'r',session_id:'s',status:'waiting_for_approval',input:'Synthetic current request'}}:{})});
   if(p==='/approvals')return json({items:live?[{id:'a',run_id:'r',session_id:'s',title:'Fixture approval'}]:[]});
   if(p.endsWith('/controls'))return json({steering:true,attempts:[]});
   if(p.endsWith('/events')){res.writeHead(200,{'Content-Type':'text/event-stream'});res.write(': fixture\n\n');return;}
   if(p==='/runs/r')return json({id:'r',session_id:'s',status:'waiting_for_approval'});
   return json({items:[]});
  }
  const name=url.pathname.replace(/^\/hermes\//,'')||'index.html';
  // Match the builder's 24-hex release suffix without intercepting other paths/modules.
  if(startupFailure && /^\/hermes\/ui(?:\.[a-f0-9]{24})?\.mjs$/.test(url.pathname)){res.writeHead(200,{'Content-Type':'text/javascript'});res.end('export async function mountApp(){throw Error("synthetic startup failure")}');return;}
  try {const data=await readFile(join(dir,name));res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',js:'text/javascript',mjs:'text/javascript'})[name.split('.').pop()]||'application/octet-stream'});res.end(data);}catch{res.writeHead(404).end();}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 t.after(async()=>{await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));});
 const page=await browser.newPage({viewport:{width:390,height:844},serviceWorkers:'block'});page.setDefaultTimeout(5000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));t.after(()=>assert.deepEqual(errors,[]));
 if(visual)await page.addInitScript(()=>{
  const viewport=Object.assign(new EventTarget(),{height:844,width:390,offsetTop:0,scale:1});
  Object.defineProperty(window,'visualViewport',{value:viewport});
  window.changeViewport=values=>{Object.assign(viewport,values);viewport.dispatchEvent(new Event('resize'));viewport.dispatchEvent(new Event('scroll'));};
 });
 if(fallback)await page.addInitScript(()=>Object.defineProperty(window,'visualViewport',{value:undefined}));
 await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);
 if(startupFailure){await page.getByRole('alert').waitFor();return page;}
 await page.getByRole('button',{name:'Keyboard fixture',exact:true}).click();
 await page.getByRole('textbox',{name:'Message Hermes',exact:true}).waitFor();
 return page;
}
async function settle(page){await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));}
async function inside(page,selector,top,height) {
 const box=await page.locator(selector).boundingBox();assert.ok(box,`${selector} visible`);
 assert.ok(box.y>=top-1,`${selector} top ${box.y} >= ${top}`);
 assert.ok(box.y+box.height<=top+height+1,`${selector} bottom ${box.y+box.height} <= ${top+height}`);
 assert.ok(box.width>=40 && box.x>=0 && box.x+box.width<=await page.evaluate(()=>innerWidth)+1,`${selector} horizontal bounds`);
 return box;
}
test('browser fallback fits actual short and landscape viewports with native zoom still permitted',{timeout:20000},async t=>{
 const page=await browserFixture(t,{fallback:true});await page.getByRole('textbox',{name:'Message Hermes',exact:true}).fill('Fallback draft');
 for(const size of [{width:390,height:380},{width:844,height:390},{width:390,height:844}]){
  await page.setViewportSize(size);await settle(page);const shell=await inside(page,'.app-shell',0,size.height);assert.equal(shell.height,size.height);
  await inside(page,'[aria-label="Send message"]',0,size.height);await inside(page,'[aria-label="Expand editor"]',0,size.height);
 }
 assert.equal(await page.locator('#app').getAttribute('data-keyboard'),'closed');
 assert.doesNotMatch(await page.locator('meta[name=viewport]').getAttribute('content'),/user-scalable\s*=\s*no|maximum-scale\s*=\s*1(?:\D|$)/);
});
test('visual viewport scroll, zoom and back-forward lifecycle remeasure without losing editor identity',{timeout:20000},async t=>{
 const page=await browserFixture(t,{visual:true});const text=page.getByRole('textbox',{name:'Message Hermes',exact:true});await text.fill('Keep selection through resume');
 await text.evaluate(el=>{window.savedEditor=el;el.setSelectionRange(2,8);});
 await page.evaluate(()=>changeViewport({height:430,offsetTop:25}));await settle(page);
 await page.evaluate(()=>changeViewport({offsetTop:48}));await settle(page);assert.equal((await page.locator('.app-shell').boundingBox()).y,48);
 await page.evaluate(()=>changeViewport({height:215,offsetTop:90,scale:2}));await settle(page);
 assert.equal(await page.locator('#app').getAttribute('data-keyboard'),'open');assert.equal((await page.locator('.app-shell').boundingBox()).height,430,'zoom does not squeeze layout');
 await page.evaluate(()=>{dispatchEvent(new Event('pagehide'));changeViewport({height:844,offsetTop:0,scale:1});});await settle(page);
 assert.equal((await page.locator('.app-shell').boundingBox()).height,430,'hidden page does not mutate layout');
 await page.evaluate(()=>dispatchEvent(new Event('pageshow')));await settle(page);assert.equal((await page.locator('.app-shell').boundingBox()).height,844);
 assert.deepEqual(await text.evaluate(el=>[el===window.savedEditor,document.activeElement===el,el.selectionStart,el.selectionEnd]),[true,true,2,8]);
});
test('pinch preserves exact unzoomed composer, navigation and message geometry for bottom and older readers',{timeout:20000},async t=>{
 const page=await browserFixture(t,{visual:true});
 const text=page.getByRole('textbox',{name:'Message Hermes',exact:true});await text.fill('Pinch preserves this draft');
 await text.evaluate(el=>{window.pinchEditor=el;el.setSelectionRange(2,8);});
 const geometry=()=>page.evaluate(()=>({
  rectangles:Object.fromEntries(['.app-shell','.messages','.composer','[aria-label="Expand editor"]','.composer .send','.bottom-nav'].map(selector=>[selector,document.querySelector(selector).getBoundingClientRect().toJSON()])),
  top:document.querySelector('.messages').scrollTop,
  gap:document.querySelector('.messages').scrollHeight-document.querySelector('.messages').clientHeight-document.querySelector('.messages').scrollTop
 }));
 for(const follow of [true,false]) {
  await page.evaluate(()=>changeViewport({height:380,offsetTop:24,scale:1}));await settle(page);
  await page.locator('.messages').evaluate((el,follow)=>el.scrollTop=follow?el.scrollHeight:250,follow);await settle(page);
  const before=await geometry();
  for(const change of [{height:190,offsetTop:80,scale:2},{height:152,offsetTop:110,scale:2.5}]) {
   await page.evaluate(change=>changeViewport(change),change);await settle(page);
   assert.deepEqual(await geometry(),before,'magnified viewport must not change any unzoomed geometry or reading position');
   assert.equal(await page.locator('#app').getAttribute('data-keyboard'),'open');
  }
  await page.evaluate(()=>changeViewport({height:380,offsetTop:24,scale:1}));await settle(page);
  assert.deepEqual(await geometry(),before,'return to same scale-1 viewport restores exact layout');
  await page.evaluate(()=>changeViewport({height:844,offsetTop:0,scale:1}));await settle(page);
  const recovered=await geometry();
  if(follow)assert.ok(recovered.gap<2);else assert.equal(recovered.top,before.top);
  assert.equal(await page.locator('#app').getAttribute('data-keyboard'),'closed');
 }
 assert.deepEqual(await text.evaluate(el=>[el===window.pinchEditor,document.activeElement===el,el.selectionStart,el.selectionEnd]),[true,true,2,8]);
 // Zoom from a keyboard-closed layout must not manufacture a compact keyboard state.
 const closed=await geometry();await page.evaluate(()=>changeViewport({height:300,offsetTop:100,scale:2}));await settle(page);
 assert.deepEqual(await geometry(),closed);assert.equal(await page.locator('#app').getAttribute('data-keyboard'),'closed');
});
test('startup failure fixture intercepts only exact source and generated UI module paths',{timeout:15000},async t=>{
 const page=await browserFixture(t,{startupFailure:true});
 const hash='0123456789abcdef01234567';
 for(const path of ['/hermes/ui.mjs',`/hermes/ui.${hash}.mjs`,`/hermes/ui.${hash}.mjs?cache=1`]) {
  const response=await page.request.get(new URL(path,page.url()).href);
  assert.equal(response.status(),200,path);
  assert.equal(await response.text(),'export async function mountApp(){throw Error("synthetic startup failure")}',path);
 }
 // These requests must reach ordinary file lookup, not a basename/substring match.
 for(const path of ['/not-hermes/ui.mjs','/other/ui.mjs','/hermes/nested/ui.mjs',`/other/ui.${hash}.mjs`,`/hermes/nested/ui.${hash}.mjs`,`/hermes/prefix-ui.${hash}.mjs`,`/hermes/ui.${hash}.mjs.map`,'/hermes/ui.not-a-hash.mjs','/hermes/ui.abcdef.mjs',`/hermes/ui.${hash}0.mjs`,`/hermes/ui.${'g'.repeat(24)}.mjs`,'/hermes/ui-mjs']) {
  const response=await page.request.get(new URL(path,page.url()).href);
  assert.equal(response.status(),404,path);
  assert.equal(await response.text(),'',path);
 }
 const index=await page.request.get(new URL('/hermes/index.html',page.url()).href);
 assert.equal(index.status(),200);
 assert.equal(await index.text(),await readFile(join(dir,'index.html'),'utf8'),'non-UI asset remains unchanged');
});
test('entry disposes viewport observer when application startup fails',{timeout:15000},async t=>{
 const page=await browserFixture(t,{startupFailure:true,visual:true});await settle(page);
 assert.match(await page.getByRole('alert').textContent(),/could not start/);
 assert.equal(await page.locator('#app').getAttribute('data-viewport'),null);
 await page.evaluate(()=>{changeViewport({height:400});dispatchEvent(new Event('pageshow'));});await settle(page);
 assert.equal(await page.locator('#app').evaluate(el=>el.style.getPropertyValue('--app-viewport-height')),'');
});
test('notice height reflow preserves bottom intent versus older reading before a narrow keyboard resize',{timeout:20000},async t=>{
 const page=await browserFixture(t,{visual:true,live:true});await page.locator('.approval-notice').waitFor();
 const text=page.getByRole('textbox',{name:'Message Hermes',exact:true});await text.fill('Notice reflow draft');
 const reading=()=>page.locator('.messages').evaluate(el=>({top:el.scrollTop,gap:el.scrollHeight-el.clientHeight-el.scrollTop,height:el.clientHeight}));
 for(const follow of [true,false]){
  await page.setViewportSize({width:390,height:844});await page.evaluate(()=>changeViewport({width:390,height:844,offsetTop:0}));await settle(page);
  // Establish reading intent before the two real safety banners consume space.
  await text.evaluate(el=>el.blur());
  await page.evaluate(follow=>{document.querySelector('.approval-notice').hidden=true;document.querySelector('.notice').hidden=true;const m=document.querySelector('.messages');m.scrollTop=follow?m.scrollHeight:250;},follow);await settle(page);
  const before=await reading();
  await page.evaluate(()=>{document.querySelector('.approval-notice').hidden=false;const n=document.querySelector('.notice');n.hidden=false;n.textContent='Security fixture: review the exact request before approval.';document.querySelector('.composer textarea').focus();});await settle(page);
  const after=await reading();assert.ok(before.height-after.height>100,'notice reflow exceeds the bottom-follow tolerance');
  if(follow)assert.ok(after.gap<2,'safety notices do not turn a bottom follower into an older reader');else assert.equal(after.top,before.top,'safety notices preserve older reading offset');
  await page.setViewportSize({width:320,height:844});await page.evaluate(()=>changeViewport({width:320,height:380,offsetTop:24}));await settle(page);
  if(follow){assert.ok((await reading()).gap<2);await inside(page,'[aria-label="Stop run"]',24,380);}else assert.equal((await reading()).top,before.top,'subsequent width/keyboard reflow preserves older reading');
  assert.equal(await text.inputValue(),'Notice reflow draft');
 }
});
test('intentional older-history scroll after notices takes precedence over previous bottom intent',{timeout:20000},async t=>{
 const page=await browserFixture(t,{visual:true,live:true});await page.locator('.approval-notice').waitFor();await settle(page);
 await page.evaluate(()=>{document.querySelector('.approval-notice').hidden=true;const m=document.querySelector('.messages');m.scrollTop=m.scrollHeight;});await settle(page);
 // No viewport/focus update between the banner reflow and an actual reader scroll.
 await page.evaluate(()=>{document.querySelector('.approval-notice').hidden=false;const n=document.querySelector('.notice');n.hidden=false;n.textContent='Security fixture: review the exact request before approval.';document.querySelector('.messages').scrollTop=250;});await settle(page);
 await page.getByRole('textbox',{name:'Message Hermes',exact:true}).focus();await settle(page);
 assert.equal(await page.locator('.messages').evaluate(el=>el.scrollTop),250,'focusing composer must not override a newer intentional reader scroll');
});
test('short narrow keyboard retains approval, security notice and Stop; screenshots document light/dark geometry',{timeout:30000},async t=>{
 const page=await browserFixture(t,{visual:true,live:true});
 await page.locator('.approval-notice').waitFor();await page.getByRole('button',{name:'Stop run'}).waitFor();
 await page.getByRole('textbox',{name:'Message Hermes',exact:true}).fill('Draft preserved above the keyboard');
 await page.evaluate(()=>{const n=document.querySelector('.notice');n.hidden=false;n.textContent='Security fixture: review the exact request before approval.';});
 for(const width of [320,390])for(const theme of ['light','dark']) {
  await page.setViewportSize({width,height:844});await page.evaluate(({width,theme})=>{document.documentElement.dataset.theme=theme;changeViewport({width,height:380,offsetTop:24});},{width,theme});await settle(page);
  await inside(page,'.approval-notice',24,380);await inside(page,'.notice',24,380);
  await inside(page,'[aria-label="Stop run"]',24,380);await inside(page,'[aria-label="Expand editor"]',24,380);await inside(page,'.composer .send',24,380);
  const stopHit=await page.locator('[aria-label="Stop run"]').evaluate(el=>{const r=el.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===el;});assert.equal(stopHit,true,'Stop not occluded');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),width);
  await mkdir(artifactURL(),{recursive:true});await page.screenshot({path:fileURLToPath(artifactURL(`keyboard-viewport-${width}-${theme}.png`))});
 }
 await page.getByRole('button',{name:'Expand editor'}).click();await inside(page,'.editor-dialog textarea',24,380);await inside(page,'.editor-dialog button',24,380);
 await page.screenshot({path:fileURLToPath(artifactURL('keyboard-viewport-editor.png'))});
});
test('300px visual and actual windows keep focused composer reachable and safety controls fully hittable by bounded app scrolling',{timeout:30000},async t=>{
 for(const visual of [true,false]) {
  const page=await browserFixture(t,{visual,live:true});await page.locator('.approval-notice').waitFor();
  await page.setViewportSize({width:320,height:568});
  const text=page.getByRole('textbox',{name:'Message Hermes',exact:true});await text.fill('Short viewport draft');
  await text.evaluate(el=>{window.shortEditor=el;el.setSelectionRange(2,8);});
  await page.evaluate(()=>{const n=document.querySelector('.notice');n.hidden=false;n.textContent='Security fixture: review the exact request before approval.';});
  await page.locator('.messages').evaluate(el=>el.scrollTop=el.scrollHeight);await settle(page);
  if(visual)await page.evaluate(()=>changeViewport({width:320,height:300,offsetTop:24}));else await page.setViewportSize({width:320,height:300});
  await settle(page);const top=visual?24:0;
  await inside(page,'.composer textarea',top,300);await inside(page,'.composer .send',top,300);await inside(page,'[aria-label="Expand editor"]',top,300);
  const hit=async selector=>{
   await inside(page,selector,top,300);
   assert.equal(await page.locator(selector).evaluate(el=>{const r=el.getBoundingClientRect();return [[.1,.1],[.9,.1],[.5,.5],[.1,.9],[.9,.9]].every(([x,y])=>el.contains(document.elementFromPoint(r.x+r.width*x,r.y+r.height*y)));}),true,`${selector}: full target is hittable, not only its center`);
  };
  await hit('.composer .send');await hit('[aria-label="Expand editor"]');
  const measure=()=>page.evaluate(()=>Object.fromEntries(['.app-shell','.notice','.messages','.approval-notice','[aria-label="Stop run"]','.composer','.composer .send'].map(s=>{const el=document.querySelector(s);return [s,{...el.getBoundingClientRect().toJSON(),scrollTop:el.scrollTop}];})));
  t.diagnostic(JSON.stringify({mode:visual?'visual 300 + offset 24':'actual 320x300',view:'composer',geometry:await measure()}));
  await page.screenshot({path:fileURLToPath(artifactURL(`keyboard-viewport-short-${visual?'visual':'actual'}-composer.png`))});
  if(visual){
   const before=await measure();await page.evaluate(()=>changeViewport({height:150,offsetTop:80,scale:2}));await settle(page);
   assert.deepEqual(await measure(),before,'zoom also freezes an already scrolled short shell, notices and Stop');
   await page.evaluate(()=>changeViewport({height:300,offsetTop:24,scale:1}));await settle(page);assert.deepEqual(await measure(),before);
  }
  // A second keyboard/URL-chrome frame must keep following the focused composer,
  // including the shell's trailing padding (not just exact scrollHeight).
  if(visual)await page.evaluate(()=>changeViewport({height:280}));else await page.setViewportSize({width:320,height:280});await settle(page);
  await inside(page,'.composer textarea',top,280);await inside(page,'.composer .send',top,280);
  if(visual)await page.evaluate(()=>changeViewport({height:300}));else await page.setViewportSize({width:320,height:300});await settle(page);
  const scroll=()=>page.locator('.app-shell').evaluate(el=>({top:el.scrollTop,height:el.clientHeight,extent:el.scrollHeight,page:scrollY}));
  assert.equal((await scroll()).page,0,'the document is not the fallback scroller');
  // Wheel the shell's outer gutter, not scrollIntoView (which could conceal clipping
  // in another ancestor); transcript scrolling remains independent.
  await page.mouse.move(4,top+100);await page.mouse.wheel(0,-600);
  await page.waitForFunction(()=>document.querySelector('.app-shell').scrollTop===0);await settle(page);
  await hit('.notice');await hit('[aria-label="Stop run"]');await hit('.approval-notice');
  t.diagnostic(JSON.stringify({mode:visual?'visual 300 + offset 24':'actual 320x300',view:'safety',geometry:await measure()}));
  await page.screenshot({path:fileURLToPath(artifactURL(`keyboard-viewport-short-${visual?'visual':'actual'}-safety.png`))});
  const stop=await page.locator('[aria-label="Stop run"]').boundingBox();assert.ok(stop.height>=44);
  const bar=await page.locator('.live-activity-heading').boundingBox();assert.ok(bar.height>=56);assert.ok(stop.y-bar.y>=6 && bar.y+bar.height-stop.y-stop.height>=6);
  assert.equal(await page.locator('.messages').evaluate(el=>el.scrollHeight-el.clientHeight-el.scrollTop<2),true,'bottom reader still follows the transcript');
  const safetyPosition=await scroll();
  if(visual){await page.evaluate(()=>changeViewport({offsetTop:24.2,height:300.2}));await settle(page);assert.deepEqual(await scroll(),safetyPosition,'viewport jitter must not pull a reader away from safety controls');}
  await page.locator('.messages').evaluate(el=>el.scrollTop=250);await settle(page);const olderTop=await page.locator('.messages').evaluate(el=>el.scrollTop);
  await page.locator('.app-shell').evaluate(el=>el.scrollTop=el.scrollHeight);await settle(page);await hit('.composer .send');
  if(visual)await page.evaluate(()=>changeViewport({height:568,offsetTop:0}));else await page.setViewportSize({width:320,height:568});await settle(page);
  assert.equal(await page.locator('.messages').evaluate(el=>el.scrollTop),olderTop,'older transcript offset survives fallback recovery');
  assert.deepEqual(await text.evaluate(el=>[el===window.shortEditor,document.activeElement===el,el.value,el.selectionStart,el.selectionEnd]),[true,true,'Short viewport draft',2,8]);
  assert.equal((await scroll()).page,0);assert.equal((await scroll()).top,0,'normal-height shell recovers without a stale scroll offset');
 }
});
test('210px landscape fallback can scroll every safety and composer target into the bounded viewport',{timeout:20000},async t=>{
 const page=await browserFixture(t,{visual:true,live:true});await page.locator('.approval-notice').waitFor();
 await page.setViewportSize({width:844,height:390});const text=page.getByRole('textbox',{name:'Message Hermes',exact:true});await text.fill('Landscape draft');
 await page.evaluate(()=>{const n=document.querySelector('.notice');n.hidden=false;n.textContent='Security fixture: review the exact request before approval.';changeViewport({width:844,height:390,offsetTop:0});});await settle(page);
 await page.locator('.messages').evaluate(el=>el.scrollTop=el.scrollHeight);
 await page.evaluate(()=>changeViewport({height:210}));await settle(page);
 await inside(page,'.composer textarea',0,210);await inside(page,'.composer .send',0,210);
 for(const selector of ['.notice','[aria-label="Stop run"]','.approval-notice','[aria-label="Expand editor"]','.composer .send']) {
  await page.locator(selector).evaluate(el=>{const shell=document.querySelector('.app-shell'),r=el.getBoundingClientRect(),s=shell.getBoundingClientRect();shell.scrollTop+=r.top-s.top;});await settle(page);
  await inside(page,selector,0,210);
  assert.equal(await page.locator(selector).evaluate(el=>{const r=el.getBoundingClientRect();return [[.1,.1],[.9,.1],[.5,.5],[.1,.9],[.9,.9]].every(([x,y])=>el.contains(document.elementFromPoint(r.x+r.width*x,r.y+r.height*y)));}),true,`${selector}: landscape target is not occluded`);
 }
 assert.equal(await page.evaluate(()=>scrollY),0);assert.equal(await text.inputValue(),'Landscape draft');
 await page.screenshot({path:fileURLToPath(artifactURL('keyboard-viewport-short-landscape-composer.png'))});
});
test('real resizes preserve bottom following versus older-history reading through repeated keyboard cycles',{timeout:30000},async t=>{
 const page=await browserFixture(t);const text=page.getByRole('textbox',{name:'Message Hermes',exact:true});await text.fill('Long draft '.repeat(40));
 const state=()=>page.locator('.messages').evaluate(el=>({top:el.scrollTop,gap:el.scrollHeight-el.clientHeight-el.scrollTop,height:el.clientHeight}));
 await page.locator('.messages').evaluate(el=>el.scrollTop=el.scrollHeight);
 await page.setViewportSize({width:390,height:420});await settle(page);
 assert.ok((await state()).gap<2,'reader following bottom remains at bottom after shrink');
 await page.setViewportSize({width:390,height:844});await settle(page);assert.ok((await state()).gap<2);
 await page.locator('.messages').evaluate(el=>el.scrollTop=250);const top=(await state()).top;
 for(let i=0;i<3;i++)for(const height of [420,844]){await page.setViewportSize({width:390,height});await settle(page);assert.equal((await state()).top,top,'older reader retains exact offset');}
 assert.equal(await text.inputValue(),'Long draft '.repeat(40));
});
test('real app entry starts viewport observation before any helper test intervention',{timeout:20000},async t=>{
 const page=await browserFixture(t);
 assert.equal(await page.locator('#app').getAttribute('data-viewport'),'managed');
 const text=page.getByRole('textbox',{name:'Message Hermes',exact:true});await text.fill('App entry draft');
 await page.setViewportSize({width:390,height:420});await settle(page);
 assert.equal(await page.locator('#app').getAttribute('data-keyboard'),'open');
 await inside(page,'[aria-label="Send message"]',0,420);await inside(page,'[aria-label="Expand editor"]',0,420);
 await page.setViewportSize({width:390,height:844});await settle(page);
 assert.equal(await page.locator('#app').getAttribute('data-keyboard'),'closed');
});
test('visual-only keyboard geometry fits shell, composer, rename and expanded editor without touching draft',{timeout:30000},async t=>{
 const page=await browserFixture(t,{visual:true});await settle(page);
 const text=page.getByRole('textbox',{name:'Message Hermes',exact:true});await text.fill('Keep my draft and selection');
 await text.evaluate(el=>{window.originalEditor=el;el.setSelectionRange(3,7);});
 await page.evaluate(()=>changeViewport({height:420,offsetTop:36}));await settle(page);
 const shell=await inside(page,'.app-shell',36,420);assert.equal(shell.y,36);assert.equal(shell.height,420);
 await inside(page,'.conversation-head',36,420);await inside(page,'.messages',36,420);
 await inside(page,'[aria-label="Send message"]',36,420);await inside(page,'[aria-label="Expand editor"]',36,420);
 assert.equal(await page.locator('.bottom-nav').isVisible(),false);
 assert.deepEqual(await text.evaluate(el=>[el===window.originalEditor,el.value,el.selectionStart,el.selectionEnd]),[true,'Keep my draft and selection',3,7]);
 await page.getByRole('button',{name:'Expand editor',exact:true}).click();
 await inside(page,'.editor-dialog',36,420);const editor=await inside(page,'.editor-dialog textarea',36,420);assert.ok(editor.height>=180);
 await inside(page,'.editor-dialog button',36,420);
 await page.getByRole('button',{name:'Done',exact:true}).click();
 await page.getByRole('button',{name:'Rename session'}).click();await inside(page,'.dialog',36,420);
 await page.getByRole('button',{name:'Cancel',exact:true}).click();
 await page.evaluate(()=>changeViewport({height:844,offsetTop:0}));await settle(page);
 assert.equal((await inside(page,'.app-shell',0,844)).height,844);assert.equal(await page.locator('.bottom-nav').isVisible(),true);
 assert.equal(await text.inputValue(),'Keep my draft and selection');
});
