import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {execFileSync} from 'node:child_process';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const root=process.env.HERMES_FRONTEND_DIR||fileURLToPath(new URL('../../frontend/',import.meta.url));
let plotlyBuilt=false;
function buildPlotly(){if(plotlyBuilt)return;execFileSync(process.execPath,[fileURLToPath(new URL('../../scripts/build_finance_plotly.mjs',import.meta.url))],{cwd:fileURLToPath(new URL('../..',import.meta.url))});plotlyBuilt=true;}
const common={meta:{generated_at:'2024-08-15T12:00:00Z',warnings:[]},currency:'CAD',range:{start:'2024-01-01',end:'2024-08-15'}};
const nodes=[['salary','Salary','income'],['cash','Everyday','account'],['spare','Spare','account'],['reserve','Reserve','account'],['food','Food','expense']].map(([id,label,kind])=>({id,label,kind,...(kind==='account'?{account:label}:{})})).concat({id:'reserve-product',label:'Synthetic fund',kind:'product',ownerAccountId:'reserve',groupId:JSON.stringify(['account-group','other']),semantic:true});
const pair=(id,link_type)=>({id,source:'cash',target:'reserve',kind:'transfer',label:'Everyday → Reserve · '+link_type,amount_minor:10000,currency:'CAD',count:1,selector:{endpoint:'transfers',filters:{source_account:'Everyday',target_account:'Reserve',link_type,transfer_status:'matched'}}});
const expense=(id,incoming)=>({id,source:incoming?'food':'cash',target:incoming?'cash':'food',kind:'expense',label:incoming?'Food refund → Everyday':'Everyday → Food',amount_minor:incoming?2000:60000,currency:'CAD',count:1,selector:{endpoint:'transactions',filters:{account:'Everyday',category:'expense.food',category_mode:'exact',flow:'expense',direction:incoming?'incoming':'outgoing'}}});
const flow={...common,coverage:{complete:true},nodes,edges:[pair('cash-reserve','own_cash')]};
async function fixture(t,{width=390,edges=flow.edges,includeProduct=false,graphNodes=nodes}={}){
 buildPlotly();
 const calls=[];
 const server=createServer(async(req,res)=>{
  const u=new URL(req.url,'http://fixture');
  if(u.pathname==='/hermes/app-api/auth/me'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({user:{id:'synthetic-owner',role:'owner',profile:'default',status:'ready'}}));return;}
  if(u.pathname.startsWith('/hermes/app-api/finance/')){
   const name=u.pathname.split('/').pop(),q=Object.fromEntries(u.searchParams);calls.push({name,q});res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');
   let data={...common};
   if(name==='meta')data={...common,accounts:graphNodes.filter(n=>n.account),categories:[],currencies:['CAD']};
   if(name==='flow')data={...flow,nodes:graphNodes,currency:q.currency,range:{start:q.start,end:q.end},edges:[...edges,...(includeProduct?[{id:'reserve-product-flow',source:'reserve',target:'reserve-product',kind:'investment',label:'Synthetic fund activity',amount_minor:10000,count:1,currency:q.currency,selector:{endpoint:'transactions',filters:{account:'Reserve',category:'investment.trade.synthetic',category_mode:'exact',flow:'investment',direction:'outgoing'}}}]:[])].map(edge=>({...edge,currency:q.currency}))};
   if(name==='transactions')data={...common,items:[{record_id:'synthetic',date:'2024-08-01',account:q.account,description:'Synthetic evidence',amount_minor:q.direction==='incoming'?2000:-60000,currency:q.currency,category_id:q.category}],total:1,offset:0,limit:25};
   if(name==='transfers')data={...common,items:[{link_id:'synthetic-pair',link_type:q.link_type,debit:{record_id:'synthetic-debit',account:q.source_account,date:'2024-08-01',currency:q.currency,amount_minor:-10000},credit:{record_id:'synthetic-credit',account:q.target_account,date:'2024-08-01',currency:q.currency,amount_minor:10000}}],total:1,offset:0,limit:25};
   res.end(JSON.stringify(data));return;
  }
  try{const rel=u.pathname.replace('/hermes/','').replace(/\/$/,'/index.html');if(rel.includes('..'))throw Error();res.setHeader('Content-Type',rel.endsWith('.html')?'text/html':rel.endsWith('.css')?'text/css':'text/javascript');res.end(await readFile(root+'/'+rel));}catch{res.writeHead(404);res.end();}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',args:['--no-sandbox','--disable-dev-shm-usage']});t.after(()=>browser.close());
 const context=await browser.newContext({viewport:{width,height:1000},hasTouch:true,serviceWorkers:'block'}),page=await context.newPage();page.setDefaultTimeout(5000);
 await page.goto(`http://127.0.0.1:${server.address().port}/hermes/finance/`);await page.waitForFunction(()=>document.querySelector('#content')?.getAttribute('aria-busy')==='false');return {page,context,calls};
}
async function activateAccount(page,method){
 const group=page.locator('.money-sankey-band[data-role="other"] > button:first-child');await group.scrollIntoViewIfNeeded();
 if(method==='mouse')await group.click();else await group.tap();
}
async function activateButton(method,button){if(method==='touch')await button.tap();else await button.click();}
async function expandNarrowAccounts(page,width){if(width<=600){const expand=page.locator('.money-sankey-controls').getByRole('button',{name:'Expand Other accounts',exact:true});if(await expand.count())await expand.click();}}
async function inspectMember(page,label,method='mouse'){
 const inspector=page.getByRole('complementary',{name:'Flow inspector',exact:true});
 await activateButton(method,inspector.getByRole('button',{name:'Inspect '+label,exact:true}));
 await inspector.locator('.transaction-row').first().waitFor();
 return inspector;
}
for(const width of [390,1280])for(const method of ['mouse','touch'])test(`mounted Sankey exposes exact expense, refund and parallel selectors by ${method} at ${width}`,{timeout:30000},async t=>{
 const expenses=[expense('cash-food',false),expense('food-refund',true)],pairs=[pair('cash-reserve','own_cash'),pair('cash-reserve-other','brokerage_cash')];
 const {page,calls}=await fixture(t,{width,edges:[...expenses,...pairs]});await expandNarrowAccounts(page,width);
 const inspector=page.getByRole('complementary',{name:'Flow inspector',exact:true});
 await activateAccount(page,method);
 assert.equal(await inspector.count(),1,'Selecting a mounted account node opens its exact stream list');
 for(const stream of [...expenses,...pairs]){
  await inspectMember(page,stream.label,method);
  const request=calls.filter(call=>call.name===stream.selector.endpoint).at(-1);
  for(const [key,value] of Object.entries(stream.selector.filters))assert.equal(request.q[key],value,`Preserve the exact ${key} selector`);
  const inspector=page.getByRole('complementary',{name:'Flow inspector',exact:true});
  if(stream.selector.endpoint==='transactions'){const amount=(await inspector.locator('.transaction-row .tx-amount').first().textContent()).replace(/\s+/g,' ').trim();assert.equal(amount,stream===expenses[0]?'-CAD 600.00':'CAD 20.00','The rendered evidence retains the outgoing expense or positive refund sign');}
  else {const rows=inspector.locator('.transaction-row');assert.deepEqual(await rows.evaluateAll(items=>items.map(row=>row.dataset.recordId).sort()),['synthetic-credit','synthetic-debit'],'A paired selector displays both exact endpoint records');const amounts=await rows.locator('.tx-amount').evaluateAll(items=>items.map(item=>item.textContent.replace(/\s+/g,' ').trim()).sort());assert.deepEqual(amounts,['-CAD 100.00','CAD 100.00'],'Transfer debit and credit retain opposite signs');}
  await page.getByRole('button',{name:'Close inspector',exact:true}).click();assert.equal(await inspector.count(),0);
  if(stream!==pairs.at(-1))await activateAccount(page,method);
 }
});
test('account-group overlay passes pointer input to a mounted product node',{timeout:30000},async t=>{
 const {page}=await fixture(t,{width:1280,includeProduct:true});
 const product=page.locator('.money-sankey .sankey-node[data-node="reserve-product"]');
 await product.waitFor();
 const box=await product.boundingBox();assert.ok(box);
 const hit=await page.evaluate(({x,y})=>{
  const target=document.elementFromPoint(x,y);
  return {node:target?.closest('.sankey-node')?.getAttribute('data-node')||'',band:!!target?.closest('.money-sankey-band')};
 },{x:box.x+box.width/2,y:box.y+box.height/2});
 assert.equal(hit.node,'reserve-product',JSON.stringify(hit));
 assert.equal(hit.band,false,JSON.stringify(hit));
});
for(const width of [390,1280])test(`mounted Sankey nodes and paths have finite contained geometry at ${width}`,{timeout:20000},async t=>{
 const {page}=await fixture(t,{width,edges:[...flow.edges,{...pair('spare-reserve','own_cash'),source:'spare'}],includeProduct:true});await expandNarrowAccounts(page,width);await page.locator('.money-sankey .sankey-link[data-edge]').first().waitFor();
 const geometry=await page.locator('.money-sankey-chart').evaluate(chart=>{
  const finite=node=>{const r=node.getBBox();return [r.x,r.y,r.width,r.height].every(Number.isFinite)&&r.width>=0&&r.height>=0;};
  const rect=node=>{const r=node.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};};
  const box=document.querySelector('.money-sankey-enclosure[data-role="other"]'),bounds=box&&rect(box),owned=[...chart.querySelectorAll('.sankey-node[data-node="cash"],.sankey-node[data-node="spare"],.sankey-node[data-node="reserve"],.sankey-node[data-node="reserve-product"]')];
  const stage=document.querySelector('.money-sankey');
  return {nodes:[...chart.querySelectorAll('.sankey-node')].every(finite),links:[...chart.querySelectorAll('.sankey-link')].every(finite),enclosure:bounds&&[bounds.left,bounds.top,bounds.width,bounds.height].every(Number.isFinite)&&bounds.width>0&&bounds.height>0?bounds:null,owned:owned.map(node=>({id:node.dataset.node,bounds:rect(node)})),canvas:rect(document.querySelector('.money-sankey-canvas')),stage:{...rect(stage),clientWidth:stage.clientWidth,scrollWidth:stage.scrollWidth}};
 });
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'The integrated map remains contained in the browser viewport');
 assert.equal(geometry.nodes,true,JSON.stringify(geometry));assert.equal(geometry.links,true,JSON.stringify(geometry));assert.ok(geometry.enclosure,'The accepted account-role enclosure is mounted with the Plotly geometry');assert.deepEqual(geometry.owned.map(node=>node.id).sort(),['cash','reserve','reserve-product','spare']);for(const node of geometry.owned)assert.ok(node.bounds.left>=geometry.enclosure.left-1&&node.bounds.right<=geometry.enclosure.right+1&&node.bounds.top>=geometry.enclosure.top-1&&node.bounds.bottom<=geometry.enclosure.bottom+1,`${node.id} is contained by its owning account enclosure: ${JSON.stringify({node:node.bounds,enclosure:geometry.enclosure})}`);assert.ok(geometry.canvas.width<=geometry.stage.scrollWidth+1,'The chart canvas remains inside the Sankey scroll extent');assert.ok(geometry.stage.scrollWidth<=Math.max(width,720)+500,`The graph scroll extent is bounded for its product lane: ${JSON.stringify({canvas:geometry.canvas,stage:geometry.stage})}`);assert.ok(geometry.stage.clientWidth<=width,'The graph scroll viewport does not exceed the browser viewport');
});
for(const width of [390,1280])test(`selection inspector dismisses cleanly and returns focus to the selected Sankey link at ${width}`,{timeout:25000},async t=>{
 const {page}=await fixture(t,{width,edges:[expense('cash-food',false),...flow.edges]});await expandNarrowAccounts(page,width);
 const link=page.locator('.money-sankey .sankey-link[data-edge]').first();await link.focus();await page.keyboard.press('Enter');
 const inspector=page.getByRole('complementary',{name:'Flow inspector',exact:true});await inspector.waitFor();await inspector.getByRole('button',{name:/Inspect/}).first().click();await inspector.locator('.transaction-row').first().waitFor();
 await page.keyboard.press('Escape');assert.equal(await inspector.count(),0);assert.equal(await page.evaluate(()=>document.activeElement?.matches('.money-sankey .sankey-link.is-selected')),true,'Dismissal keeps keyboard focus on the selected mounted route');
});
for(const action of ['replace','dismiss'])test(`inspection ${action} replaces or clears actual Finance evidence without stale overlays`,{timeout:20000},async t=>{
 const {page,calls}=await fixture(t,{width:1280,edges:[expense('cash-food',false),expense('food-refund',true),...flow.edges]});
 await page.locator('.money-sankey-band[data-role="other"] > button:first-child').click();const inspector=page.getByRole('complementary',{name:'Flow inspector',exact:true});await inspector.getByRole('button',{name:'Inspect Everyday → Food',exact:true}).click();await inspector.locator('.transaction-row').first().waitFor();
 if(action==='replace'){
  await page.locator('.money-sankey .sankey-link[data-edge]').nth(1).focus();await page.keyboard.press('Enter');
  await inspector.getByRole('button',{name:'Inspect Food refund → Everyday',exact:true}).click();await inspector.locator('.transaction-row').first().waitFor();
  assert.equal(calls.filter(call=>call.name==='transactions').at(-1).q.direction,'incoming');
  await page.getByRole('button',{name:'Close inspector',exact:true}).click();
 }else await page.keyboard.press('Escape');
 assert.equal(await page.locator('.flow-inspector').count(),0,'No previous exact evidence panel survives dismissal or replacement');
});
for(const width of [390,1280])test(`exact movement finder pages every dense selector at ${width}`,{timeout:30000},async t=>{
 const outgoing=Array.from({length:80},(_,i)=>{const edge=expense('food-'+i,false);return {...edge,label:'Food purchase '+i,selector:{...edge.selector,filters:{...edge.selector.filters,category:'expense.food.item'+i}}};}),refund=expense('refund',true);
 const {page,calls}=await fixture(t,{width,edges:[...outgoing,refund]});await expandNarrowAccounts(page,width);
 const finder=page.locator('.money-sankey-stream-finder');await finder.waitFor({state:'visible'});await finder.locator('summary').click();await finder.getByLabel('Search exact movements').fill('Food purchase');
 const results=finder.locator('.money-sankey-stream-results'),seen=[];
 for(let i=0;i<4;i++){const labels=await results.locator('button[aria-label^="Inspect Food purchase"]').evaluateAll(nodes=>nodes.map(node=>node.getAttribute('aria-label')));assert.ok(labels.length>0&&labels.length<=25);seen.push(...labels);if(i<3)await results.getByRole('button',{name:'Next matching movements'}).click();}
 assert.deepEqual(seen,outgoing.map(edge=>'Inspect '+edge.label));
 await finder.getByLabel('Search exact movements').fill('Food refund → Everyday');await results.getByRole('button',{name:/Inspect Food refund/}).click();
 const inspector=page.getByRole('complementary',{name:'Flow inspector',exact:true});await inspectMember(page,refund.label);
 const request=calls.filter(call=>call.name==='transactions').at(-1).q;assert.equal(request.direction,'incoming');assert.equal(request.category,'expense.food');assert.equal(request.category_mode,'exact');
});
test('folded account-role transfers remain searchable and inspectable without expanding the graph',{timeout:30000},async t=>{
 const loop={...pair('folded-loop','own_cash'),label:'Everyday internal transfer',selector:{endpoint:'transfers',filters:{source_account:'Everyday',target_account:'Reserve',link_type:'own_cash',transfer_status:'matched'}}};
 const {page,calls}=await fixture(t,{width:390,edges:[loop]});
 assert.equal(await page.locator('.money-sankey-controls .money-sankey-toggle').first().getAttribute('aria-expanded'),'false','Mobile account roles start folded');
 assert.equal(await page.getByText('No recorded movements match these filters.',{exact:true}).count(),0,'Folded transfers are recorded movements, not an empty ledger');
 await page.getByText('Movements are within folded account groups. Expand a group to see them.',{exact:true}).waitFor();
 assert.equal(await page.locator('.money-sankey-controls').getByRole('button',{name:'Expand Other accounts',exact:true}).isVisible(),true);
 const finder=page.locator('.money-sankey-stream-finder');await finder.locator('summary').click();await finder.getByLabel('Search exact movements').fill('Everyday internal transfer');
 const result=finder.getByRole('button',{name:/Inspect Everyday internal transfer/});await result.waitFor();
 await result.click();const inspector=page.getByRole('complementary',{name:'Flow inspector',exact:true});
 await inspector.getByRole('button',{name:'Inspect Everyday internal transfer',exact:true}).click();await inspector.locator('.transaction-row').first().waitFor();
 const q=calls.filter(call=>call.name==='transfers').at(-1).q;
 assert.equal(q.source_account,'Everyday');assert.equal(q.target_account,'Reserve');assert.equal(q.transfer_status,'matched');
 assert.deepEqual(await inspector.locator('.transaction-row').evaluateAll(rows=>rows.map(row=>row.dataset.recordId).sort()),['synthetic-credit','synthetic-debit']);
 await page.getByRole('button',{name:'Close inspector',exact:true}).click();
 await page.locator('.money-sankey-controls').getByRole('button',{name:'Expand Other accounts',exact:true}).click();
 await page.locator('.money-sankey .sankey-link[data-edge]').first().waitFor();
 assert.equal(await page.getByText('Movements are within folded account groups. Expand a group to see them.',{exact:true}).count(),0);
});

for(const width of [390,1280])test(`tall Sankey keeps its lowest account and product nodes visible at ${width}`,{timeout:30000},async t=>{
 const accounts=Array.from({length:28},(_,i)=>({id:`tall-account-${i}`,label:`Tall account ${i}`,account:`Tall account ${i}`,accountGroup:'other',kind:'account'}));
 const categories=Array.from({length:28},(_,i)=>({id:`tall-expense-${i}`,label:`Tall expense ${i}`,kind:'expense'}));
 const edges=categories.map((node,i)=>({id:`tall-edge-${i}`,source:accounts[i].id,target:node.id,kind:'expense',label:`Tall stream ${i}`,amount_minor:1000+i,currency:'CAD',count:1,selector:{endpoint:'transactions',filters:{account:accounts[i].account,category:`expense.tall.${i}`,category_mode:'exact',flow:'expense',direction:'outgoing'}}}));
 edges.push({id:'tall-product-edge',source:'reserve',target:'reserve-product',kind:'investment',label:'Tall product activity',amount_minor:12000,currency:'CAD',count:1,selector:{endpoint:'transactions',filters:{account:'Reserve',category:'investment.trade.synthetic',category_mode:'exact',flow:'investment',direction:'outgoing'}}});
 const graphNodes=[...nodes,...accounts,...categories];
 const {page}=await fixture(t,{width,edges,includeProduct:true,graphNodes});
 if(width===390)await page.locator('.money-sankey-controls').getByRole('button',{name:'Expand Other accounts',exact:true}).click();
 const chart=page.locator('.money-sankey-chart'),stage=page.locator('.money-sankey');
 const svg=chart.locator('svg.main-svg').first();await svg.waitFor();
 const metrics=await svg.evaluate(node=>({height:node.getBoundingClientRect().height,declared:Number(node.getAttribute('height')),viewBox:node.viewBox.baseVal.height}));
 assert.ok(metrics.declared>680,JSON.stringify(metrics));assert.ok(metrics.height>=metrics.declared-1,`Tall Plotly SVG is not clipped: ${JSON.stringify(metrics)}`);
 const last=chart.locator('.sankey-node[data-node="tall-account-27"]');await last.waitFor();
 await page.waitForFunction(()=>document.querySelector('.money-sankey')?.getAttribute('aria-busy')==='false');
 await stage.evaluate(node=>node.scrollTop=node.scrollHeight);
 await last.scrollIntoViewIfNeeded();
 const bounds=await page.evaluate(()=>{const node=document.querySelector('.sankey-node[data-node="tall-account-27"]'),stage=document.querySelector('.money-sankey'),r=node.getBoundingClientRect(),s=stage.getBoundingClientRect();return {node:{left:r.left,right:r.right,top:r.top,bottom:r.bottom},stage:{left:s.left,right:s.right,top:s.top,bottom:s.bottom,scrollTop:stage.scrollTop,scrollHeight:stage.scrollHeight,clientHeight:stage.clientHeight}};});
 assert.ok(bounds.node.bottom<=bounds.stage.bottom+1&&bounds.node.top>=bounds.stage.top-1,JSON.stringify(bounds));
 const label=last.locator('text');assert.ok(await label.count()>0,'The lowest account label remains mounted with its bar');
 const labelBounds=await label.first().boundingBox();assert.ok(labelBounds&&labelBounds.y>=bounds.stage.top-1&&labelBounds.y+labelBounds.height<=bounds.stage.bottom+1,JSON.stringify({bounds,labelBounds}));
 const hit=await page.evaluate(()=>{const node=document.querySelector('.sankey-node[data-node="tall-account-27"]'),r=node.getBoundingClientRect(),target=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return target?.closest('.sankey-node')?.dataset.node||'';});
 assert.equal(hit,'tall-account-27','The lowest account is visible and pointer-addressable after scrolling');
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
});

for(const width of [390,1280])for(const method of ['mouse','touch'])test(`account heading and chevron remain 44px clickable at minimum zoom by ${method} at ${width}`,{timeout:30000},async t=>{
 const {page}=await fixture(t,{width,edges:[expense('zoom-expense',false)]});
 const zoomOut=page.getByRole('button',{name:'Zoom out',exact:true});for(let i=0;i<3;i++)await zoomOut.click();
 await page.getByText('65%',{exact:true}).waitFor();
 const band=page.locator('.money-sankey-band[data-role="other"]');
 const buttons=band.locator('button');await buttons.first().waitFor();
 for(const button of [buttons.first(),buttons.nth(1)]){
  const box=await button.boundingBox();assert.ok(box&&box.width>=44&&box.height>=44,JSON.stringify(box));
  const hit=await button.evaluate((node,{x,y})=>{const target=document.elementFromPoint(x,y);return target===node||node.contains(target);},{x:box.x+box.width/2,y:box.y+box.height/2});
  assert.equal(hit,true,'The visible hit target belongs to the heading control');
 }
 if(method==='touch')await buttons.first().tap();else await buttons.first().click();
 await page.getByRole('complementary',{name:'Flow inspector',exact:true}).waitFor();
 await page.getByRole('button',{name:'Close inspector',exact:true}).click();
 const expanded=await buttons.nth(1).getAttribute('aria-expanded');
 if(method==='touch')await buttons.nth(1).tap();else await buttons.nth(1).click();
 await page.waitForFunction(value=>document.querySelector('.money-sankey-band[data-role="other"] .money-sankey-toggle')?.getAttribute('aria-expanded')!==value,expanded);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
});
for(const width of [390,1280])test(`same-account loop remains exact evidence with real zoom, pan and Fit controls at ${width}`,{timeout:30000},async t=>{
 const loop={...pair('self-loop','own_cash'),source:'reserve',target:'reserve',label:'Reserve internal loop',selector:{endpoint:'transfers',filters:{source_account:'Reserve',target_account:'Reserve',link_type:'own_cash',transfer_status:'matched'}}};
 const deepExpense={...expense('deep-expense',false),label:'Synthetic deep expense',selector:{...expense('deep-expense',false).selector,filters:{...expense('deep-expense',false).selector.filters,category:'expense.food.household.supplies.recurring'}}};
 const {page,calls}=await fixture(t,{width,edges:[...flow.edges,loop,deepExpense],includeProduct:true});await expandNarrowAccounts(page,width);
 const initial=await page.locator('.money-sankey-view-controls output').textContent();await page.getByRole('button',{name:'Zoom in',exact:true}).click();assert.notEqual(await page.locator('.money-sankey-view-controls output').textContent(),initial);
 await page.getByRole('button',{name:'Fit map to view',exact:true}).click();assert.equal(await page.locator('.money-sankey-view-controls output').textContent(),initial);
 await page.getByRole('button',{name:'Pan right',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.money-sankey').scrollLeft>0,'Pan right changes the actual chart viewport');
 const finder=page.locator('.money-sankey-stream-finder');await finder.locator('summary').click();await finder.getByLabel('Search exact movements').fill('Reserve internal loop');await finder.getByRole('button',{name:'Inspect Reserve internal loop',exact:true}).click();
 const inspector=page.getByRole('complementary',{name:'Flow inspector',exact:true});await inspectMember(page,'Reserve internal loop');
 const request=calls.filter(call=>call.name==='transfers').at(-1).q;assert.equal(request.source_account,'Reserve');assert.equal(request.target_account,'Reserve');assert.equal(request.link_type,'own_cash');assert.equal(request.transfer_status,'matched');
 assert.deepEqual(await inspector.locator('.transaction-row').evaluateAll(rows=>rows.map(row=>row.dataset.recordId).sort()),['synthetic-credit','synthetic-debit'],'The same-account transfer retains both exact endpoint records');
 await page.getByRole('button',{name:'Close inspector',exact:true}).click();
});
