import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const root=process.env.HERMES_FRONTEND_DIR||fileURLToPath(new URL('../../frontend/',import.meta.url));
const common={meta:{generated_at:'2024-08-15T12:00:00Z',warnings:[]},currency:'CAD',range:{start:'2024-01-01',end:'2024-08-15'}};
const nodes=[['salary','Salary','income'],['cash','Everyday','account'],['spare','Spare','account'],['reserve','Reserve','account'],['food','Food','expense']].map(([id,label,kind])=>({id,label,kind,...(kind==='account'?{account:label}:{})}));
const pair=(id,link_type)=>({id,source:'cash',target:'reserve',kind:'transfer',label:'Everyday → Reserve · '+link_type,amount_minor:10000,currency:'CAD',count:1,selector:{endpoint:'transfers',filters:{source_account:'Everyday',target_account:'Reserve',link_type,transfer_status:'matched'}}});
const expense=(id,incoming)=>({id,source:incoming?'food':'cash',target:incoming?'cash':'food',kind:'expense',label:incoming?'Food refund → Everyday':'Everyday → Food',amount_minor:incoming?2000:60000,currency:'CAD',count:1,selector:{endpoint:'transactions',filters:{account:'Everyday',category:'expense.food',category_mode:'exact',flow:'expense',direction:incoming?'incoming':'outgoing'}}});
const flow={...common,coverage:{complete:true},nodes,edges:[pair('cash-reserve','own_cash')]};
async function fixture(t,{width=390,edges=flow.edges}={}){
 const calls=[];
 const server=createServer(async(req,res)=>{const u=new URL(req.url,'http://fixture');if(u.pathname.startsWith('/hermes/app-api/finance/')){const name=u.pathname.split('/').pop(),q=Object.fromEntries(u.searchParams);calls.push({name,q});res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');let data={...common};if(name==='meta')data={...common,accounts:nodes.filter(n=>n.account),categories:[],currencies:['CAD']};if(name==='flow')data={...flow,edges};if(name==='transactions')data={...common,items:[{record_id:'synthetic',date:'2024-08-01',account:q.account,description:'Synthetic evidence',amount_minor:q.direction==='incoming'?2000:-60000,currency:'CAD',category_id:q.category}],total:1,offset:0,limit:25};if(name==='transfers')data={...common,items:[{link_id:'synthetic-pair',link_type:q.link_type,debit:{account:q.source_account,date:'2024-08-01',currency:'CAD',amount_minor:-10000},credit:{account:q.target_account,date:'2024-08-01',currency:'CAD',amount_minor:10000}}],total:1,offset:0,limit:25};res.end(JSON.stringify(data));return;}try{const rel=u.pathname.replace('/hermes/','').replace(/\/$/,'/index.html');if(rel.includes('..'))throw Error();res.setHeader('Content-Type',rel.endsWith('.html')?'text/html':rel.endsWith('.css')?'text/css':'text/javascript');res.end(await readFile(root+'/'+rel));}catch{res.writeHead(404);res.end();}});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',args:['--no-sandbox','--disable-dev-shm-usage']});t.after(()=>browser.close());
 const context=await browser.newContext({viewport:{width,height:1000},hasTouch:true,serviceWorkers:'block'}),page=await context.newPage();page.setDefaultTimeout(5000);
 await page.goto(`http://127.0.0.1:${server.address().port}/hermes/finance/`);await page.waitForFunction(()=>document.querySelector('#content')?.getAttribute('aria-busy')==='false');return {page,context,calls};
}
// Sample the actual rendered path and hit-test it; SVG bounding-box centers can
// land off-curve or under a node. The top hit may be any stream in an overlap.
async function streamPoint(page,ids){
 await page.locator('[data-map-viewport]').evaluate(n=>n.scrollIntoView({block:'start'}));
 return page.locator('[data-map-scene]').evaluate((scene,ids)=>{
  const path=[...scene.querySelectorAll('[data-edge]')].find(n=>n.dataset.edge===ids[0]).querySelector('.edge-hit');
  const viewport=document.querySelector('[data-map-viewport]').getBoundingClientRect();
  for(const fraction of [.5,.4,.6,.3,.7,.2,.8,.1,.9]){
   const p=path.getPointAtLength(path.getTotalLength()*fraction),v=new DOMPoint(p.x,p.y).matrixTransform(path.getScreenCTM());
   if(v.x<=viewport.left||v.x>=viewport.right||v.y<=Math.max(0,viewport.top)||v.y>=Math.min(innerHeight,viewport.bottom))continue;
   const hit=document.elementFromPoint(v.x,v.y)?.closest('[data-edge]');
   if(hit&&ids.includes(hit.dataset.edge))return {x:v.x,y:v.y};
  }
  throw Error('No unobscured stream hitpoint for '+ids.join(', '));
 },ids);
}
async function activate(page,method,point){if(method==='touch')await page.touchscreen.tap(point.x,point.y);else await page.mouse.click(point.x,point.y);}
async function activateButton(page,method,button){await button.scrollIntoViewIfNeeded();const b=await button.boundingBox();assert.ok(b);await activate(page,method,{x:b.x+b.width/2,y:b.y+b.height/2});}
for(const width of [390,1280])for(const method of ['mouse','touch'])test(`overlapping expense/refund and parallel pair selectors remain reachable by ${method} at ${width}`,{timeout:30000},async t=>{
 const expenseEdges=[expense('cash-food',false),expense('food-refund',true)],pairEdges=[pair('cash-reserve','own_cash'),pair('cash-reserve-other','brokerage_cash')];
 const {page,calls}=await fixture(t,{width,edges:[...expenseEdges,...pairEdges]});
 // Opening/closing the local chooser must not create a history selection or
 // request evidence; it only passes an original edge after a real choice.
 const initialDetails=calls.filter(c=>['transactions','transfers'].includes(c.name)).length;
 await activate(page,method,await streamPoint(page,expenseEdges.map(e=>e.id)));
 const localChooser=page.getByRole('complementary',{name:'Overlapping streams',exact:true});
 await activateButton(page,method,localChooser.getByRole('button',{name:'Close stream chooser',exact:true}));
 assert.equal(await localChooser.count(),0);
 assert.equal(calls.filter(c=>['transactions','transfers'].includes(c.name)).length,initialDetails);
 assert.equal(await page.getByRole('button',{name:'Back',exact:true}).isDisabled(),true);
 const inspector=page.getByRole('complementary',{name:'Flow inspector'});
 for(const group of [expenseEdges,pairEdges])for(const edge of group){
  const before=await page.locator('[data-map-scene]').getAttribute('transform');
  await activate(page,method,await streamPoint(page,group.map(e=>e.id)));
  const chooser=page.getByRole('complementary',{name:'Overlapping streams',exact:true});
  assert.equal(await chooser.count(),1,'The overlap chooser is map-local, not a persistent inspector selection');
  assert.equal(await inspector.count(),0);
  assert.equal(await chooser.getByRole('button',{name:'Inspect '+edge.label,exact:true}).count(),1,'Overlapping streams must offer each exact selector, not silently choose the top hit stroke');
  for(const member of group)assert.equal(await chooser.getByRole('button',{name:'Inspect '+member.label,exact:true}).count(),1);
  await activateButton(page,method,chooser.getByRole('button',{name:'Inspect '+edge.label,exact:true}));
  assert.equal(await chooser.count(),0);
  await page.waitForFunction(()=>!document.querySelector('.flow-inspector [aria-busy="true"]'));
  await inspector.locator('tbody tr').first().waitFor();
  const request=calls.filter(c=>c.name===edge.selector.endpoint).at(-1);
  assert.deepEqual(request.q,{currency:'CAD',scope:'personal',dataset:'all',start:'2024-01-01',end:'2024-08-15',...edge.selector.filters,offset:'0',limit:'25'});
  await activateButton(page,method,page.getByRole('button',{name:'Close inspector',exact:true}));
  assert.equal(await inspector.count(),0);assert.equal(await page.locator('[data-map-scene]').getAttribute('transform'),before);
 }
});
for(const width of [390,1280])test(`same-column internal curve anchors node boundaries inside realm at ${width}`,{timeout:20000},async t=>{
 const {page}=await fixture(t,{width,edges:[...flow.edges,{...pair('spare-reserve','own_cash'),source:'spare'}]});
 assert.equal(await page.locator('[data-node-kind="account"]').count(),3,'Retain the middle-column filler account through projection');
 assert.equal(await page.evaluate(()=>document.querySelector('[data-node="cash"] .map-node').getBBox().x===document.querySelector('[data-node="reserve"] .map-node').getBBox().x),true,'Exercise an actual same-column route on desktop too');
 const geometry=await page.locator('[data-edge="cash-reserve"] .amount-band').evaluate(path=>{
  const rect=id=>document.querySelector(`[data-node="${id}"] .map-node`).getBBox(),realm=document.querySelector('.account-realm').getBBox(),length=path.getTotalLength();
  const boundary=(p,r)=>Math.min(Math.abs(p.x-r.x),Math.abs(p.x-r.x-r.width),Math.abs(p.y-r.y),Math.abs(p.y-r.y-r.height))<.1&&p.x>=r.x-.1&&p.x<=r.x+r.width+.1&&p.y>=r.y-.1&&p.y<=r.y+r.height+.1;
  const start=path.getPointAtLength(0),end=path.getPointAtLength(length),b=path.getBBox(),half=Number(path.getAttribute('stroke-width'))/2;
  return {start:{x:start.x,y:start.y},end:{x:end.x,y:end.y},anchored:boundary(start,rect('cash'))&&boundary(end,rect('reserve')),inside:b.x-half>=realm.x&&b.x+b.width+half<=realm.x+realm.width&&b.y-half>=realm.y&&b.y+b.height+half<=realm.y+realm.height};
 });
 assert.equal(geometry.anchored,true,JSON.stringify(geometry));assert.equal(geometry.inside,true,'The visible internal band, not only its centerline, stays in Your accounts');
});
for(const width of [390,1280])test(`chooser stays above an existing inspector and returns focus to its invoking edge at ${width}`,{timeout:30000},async t=>{
 const expenseEdges=[expense('cash-food',false),expense('food-refund',true)];
 const {page,calls}=await fixture(t,{width,edges:[...expenseEdges,...flow.edges]});
 await activate(page,'mouse',await streamPoint(page,['cash-reserve']));
 const inspector=page.getByRole('complementary',{name:'Flow inspector'});await inspector.locator('tbody tr').first().waitFor();
 const point=await streamPoint(page,expenseEdges.map(e=>e.id)),invoker=await page.evaluate(p=>document.elementFromPoint(p.x,p.y)?.closest('[data-edge]')?.dataset.edge,point);
 await activate(page,'touch',point);
 const chooser=page.getByRole('complementary',{name:'Overlapping streams',exact:true}),choice=chooser.getByRole('button',{name:'Inspect '+expenseEdges[0].label,exact:true});
 await choice.waitFor();
 assert.equal(await chooser.evaluate(panel=>{const a=panel.getBoundingClientRect(),b=document.querySelector('[aria-label="Flow inspector"]').getBoundingClientRect(),x=(Math.max(a.left,b.left)+Math.min(a.right,b.right))/2,y=(Math.max(a.top,b.top)+Math.min(a.bottom,b.bottom))/2;return panel.contains(document.elementFromPoint(x,y));}),true,'The shared panel area must hit the local chooser, not the previous inspector');
 assert.equal(await choice.evaluate(button=>{const r=button.getBoundingClientRect();return button.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}),true,'The chooser must receive input above the already-open inspector');
 await activateButton(page,'touch',chooser.getByRole('button',{name:'Close stream chooser',exact:true}));
 assert.equal(await page.evaluate(()=>document.activeElement?.dataset.edge),invoker,'Return focus to the invoking edge, not the first edge');
 assert.equal(await inspector.count(),1,'Closing the chooser preserves the prior inspection');
 await activate(page,'mouse',await streamPoint(page,expenseEdges.map(e=>e.id)));
 await page.keyboard.press('Escape');assert.equal(await chooser.count(),0);assert.equal(await page.evaluate(()=>document.activeElement?.dataset.edge),invoker);
 await activate(page,'touch',await streamPoint(page,expenseEdges.map(e=>e.id)));
 await activateButton(page,'touch',choice);await inspector.locator('tbody tr').first().waitFor();
 assert.equal(calls.filter(c=>c.name==='transactions').at(-1).q.direction,'outgoing');assert.equal(await inspector.count(),1);
});
for(const width of [390,1280])test(`large overlap chooser pages every exact stream at ${width}`,{timeout:30000},async t=>{
 const outgoing=Array.from({length:80},(_,i)=>{const edge=expense('food-'+i,false);return {...edge,label:'Food purchase '+i,selector:{...edge.selector,filters:{...edge.selector.filters,category:'expense.food.item'+i}}};}),refund=expense('refund',true);
 const {page,calls}=await fixture(t,{width,edges:[...outgoing,refund]});
 const ids=await page.locator('[data-edge]').evaluateAll(nodes=>nodes.map(n=>n.dataset.edge));
 await activate(page,'touch',await streamPoint(page,ids));
 const chooser=page.getByRole('complementary',{name:'Overlapping streams',exact:true}),seen=[];
 for(let i=0;i<4;i++){
  const labels=await chooser.locator('.stream-member').evaluateAll(nodes=>nodes.map(n=>n.getAttribute('aria-label')));
  assert.ok(labels.length>0&&labels.length<=25,'Large overlapping route lists must render at most 25 exact choices at a time');seen.push(...labels);
  if(i<3)await activateButton(page,'touch',chooser.getByRole('button',{name:'Next streams',exact:true}));
 }
 assert.deepEqual(seen,[...outgoing,refund].map(e=>'Inspect '+e.label));
 assert.equal(await chooser.getByRole('button',{name:'Next streams',exact:true}).isDisabled(),true);
 await activateButton(page,'touch',chooser.getByRole('button',{name:'Previous streams',exact:true}));
 assert.equal(await chooser.locator('.stream-member').first().getAttribute('aria-label'),'Inspect Food purchase 50');
 await activateButton(page,'touch',chooser.getByRole('button',{name:'Next streams',exact:true}));
 await activateButton(page,'touch',chooser.getByRole('button',{name:'Inspect '+refund.label,exact:true}));
 const inspector=page.getByRole('complementary',{name:'Flow inspector'});await inspector.locator('tbody tr').first().waitFor();
 const q=calls.filter(c=>c.name==='transactions').at(-1).q;assert.equal(q.direction,'incoming');assert.equal(q.category,'expense.food');assert.equal(q.category_mode,'exact');
});
for(const width of [390,1280])test(`self-loops retain real selection and zoom/pan at ${width}`,{timeout:30000},async t=>{
 const loop={...pair('self-loop','own_cash'),source:'reserve',target:'reserve',label:'Reserve internal loop',selector:{endpoint:'transfers',filters:{source_account:'Reserve',target_account:'Reserve',link_type:'own_cash',transfer_status:'matched'}}};
 const {page,context,calls}=await fixture(t,{width,edges:[...flow.edges,loop]});
 const path=page.locator('[data-edge="self-loop"] .amount-band'),shape=await path.getAttribute('d');
 assert.ok(await path.evaluate(p=>p.getTotalLength())>100,'A self-loop must not collapse to zero length');
 const scene=page.locator('[data-map-scene]'),initial=await scene.getAttribute('transform'),viewport=page.locator('[data-map-viewport]');
 await page.getByRole('button',{name:'Zoom in',exact:true}).click();assert.notEqual(await scene.getAttribute('transform'),initial);
 await page.getByRole('button',{name:'Fit diagram',exact:true}).click();assert.equal(await scene.getAttribute('transform'),initial);
 await viewport.scrollIntoViewIfNeeded();let box=await viewport.boundingBox();
 await page.mouse.move(box.x+box.width/2,box.y+30);await page.mouse.wheel(0,-180);
 await page.waitForFunction(before=>document.querySelector('[data-map-scene]').getAttribute('transform')!==before,initial);
 await page.getByRole('button',{name:'Fit diagram',exact:true}).click();await viewport.scrollIntoViewIfNeeded();box=await viewport.boundingBox();
 await page.mouse.move(box.x+box.width/2,box.y+30);await page.mouse.down();await page.mouse.move(box.x+box.width/2+35,box.y+65,{steps:5});await page.mouse.up();
 assert.notEqual(await scene.getAttribute('transform'),initial);
 await page.getByRole('button',{name:'Fit diagram',exact:true}).click();await viewport.scrollIntoViewIfNeeded();box=await viewport.boundingBox();
 const cdp=await context.newCDPSession(page),x=box.x+box.width/2,y=box.y+box.height/2;
 await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:x-25,y,id:1},{x:x+25,y,id:2}]});
 await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x-65,y:y+20,id:1},{x:x+65,y:y+20,id:2}]});
 await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});assert.notEqual(await scene.getAttribute('transform'),initial);
 assert.equal(await page.getByRole('complementary',{name:/^(Flow inspector|Overlapping streams)$/}).count(),0,'Dragging and pinching must not select streams');
 await page.getByRole('button',{name:'Fit diagram',exact:true}).click();assert.equal(await scene.getAttribute('transform'),initial);assert.equal(await path.getAttribute('d'),shape);
 for(const method of ['mouse','touch']){
  await activate(page,method,await streamPoint(page,['self-loop']));
  const inspector=page.getByRole('complementary',{name:'Flow inspector'});await inspector.locator('tbody tr').first().waitFor();
  assert.equal(calls.filter(c=>c.name==='transfers').at(-1).q.source_account,'Reserve');assert.equal(calls.filter(c=>c.name==='transfers').at(-1).q.target_account,'Reserve');
  await activateButton(page,method,page.getByRole('button',{name:'Close inspector',exact:true}));
 }
});
