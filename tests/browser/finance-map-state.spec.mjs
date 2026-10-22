import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {execFileSync} from 'node:child_process';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {lastQuarter} from '../../frontend/finance/approved/finance-client.mjs';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const root=process.env.HERMES_FRONTEND_DIR||fileURLToPath(new URL('../../frontend/',import.meta.url));
let plotlyBuilt=false;
function buildPlotly(){if(plotlyBuilt)return;execFileSync(process.execPath,[fileURLToPath(new URL('../../scripts/build_finance_plotly.mjs',import.meta.url))],{cwd:fileURLToPath(new URL('../..',import.meta.url))});plotlyBuilt=true;}
const common={meta:{generated_at:'2024-08-15T12:00:00Z',warnings:[]},currency:'CAD',range:{start:'2024-01-01',end:'2024-08-15'}};
const nodes=[{id:'cash',label:'Everyday account',account:'Everyday account',kind:'account'},{id:'reserve',label:'Reserve account',account:'Reserve account',kind:'account'},{id:'food',label:'Food',kind:'expense'}];
const edge=(id,source,amount_minor=60000,count=26)=>({id,source,target:'food',kind:'expense',label:id,amount_minor,count,currency:'CAD',selector:{endpoint:'transactions',filters:{account:source==='cash'?'Everyday account':'Reserve account',category:'expense.food',category_mode:'exact',flow:'expense',direction:'outgoing'}}});
const baseFlow={...common,nodes,edges:[edge('cash-food','cash'),edge('reserve-food','reserve',30000)],coverage:{complete:true}};
async function fixture(t){
 buildPlotly();
 const calls=[];let graph=structuredClone(baseFlow);
 const server=createServer(async(req,res)=>{
  const u=new URL(req.url,'http://fixture');
  if(u.pathname==='/hermes/app-api/auth/me'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({user:{id:'synthetic-owner',role:'owner',profile:'default',status:'ready'}}));return;}
  if(u.pathname.startsWith('/hermes/app-api/finance/')){
   const name=u.pathname.split('/').pop(),q=Object.fromEntries(u.searchParams);calls.push({name,q});
   let data={...common};
   if(name==='meta')data={...common,accounts:nodes.filter(n=>n.account),categories:[],currencies:['CAD','USD']};
   if(name==='flow'){const scoped=q.scope==='all'?{...graph,nodes:graph.nodes.filter(n=>n.id!=='reserve'),edges:graph.edges.filter(e=>e.source!=='reserve')}:graph;const edges=q.account?scoped.edges.filter(e=>e.selector?.filters?.account===q.account||e.selector?.filters?.source_account===q.account||e.selector?.filters?.target_account===q.account):scoped.edges;const endpoints=new Set(edges.flatMap(e=>[e.source,e.target]));data={...scoped,currency:q.currency,range:{start:q.start,end:q.end},nodes:scoped.nodes.filter(n=>n.kind==='account'?(!q.account||n.account===q.account):endpoints.has(n.id)),edges:edges.map(edge=>({...edge,currency:q.currency}))};}
   if(name==='transactions')data={...common,items:Array.from({length:Number(q.offset)?1:25},(_,i)=>({record_id:'synthetic-'+(Number(q.offset||0)+i),date:'2024-08-01',account:q.account,description:'Synthetic evidence '+(Number(q.offset||0)+i+1),amount_minor:-2400,currency:q.currency})),total:26,offset:Number(q.offset||0),limit:25};
   res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');res.end(JSON.stringify(data));return;
  }
  try{const rel=u.pathname.replace('/hermes/','').replace(/\/$/,'/index.html');if(rel.includes('..'))throw Error();res.setHeader('Content-Type',rel.endsWith('.html')?'text/html':rel.endsWith('.css')?'text/css':'text/javascript');res.end(await readFile(root+'/'+rel));}catch{res.writeHead(404);res.end();}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',args:['--no-sandbox','--disable-dev-shm-usage']});t.after(()=>browser.close());
 const context=await browser.newContext({viewport:{width:1280,height:900},serviceWorkers:'block'}),page=await context.newPage();page.setDefaultTimeout(5000);
 return {page,calls,setFlow:value=>graph=structuredClone(value),url:`http://127.0.0.1:${server.address().port}/hermes/finance/`};
}

const denseFlow={...common,coverage:{complete:true},nodes:[nodes[0],...Array.from({length:31},(_,i)=>({id:'food'+i,label:'Food '+i,kind:'expense'}))],edges:Array.from({length:30},(_,i)=>({...edge('food-stream-'+i,'cash',1000,1),target:'food'+i,label:'Food stream '+i,selector:{endpoint:'transactions',filters:{account:'Everyday account',category:'expense.food.item'+i,category_mode:'exact',flow:'expense',direction:'outgoing'}}}))};
for(const action of ['refresh','Back'])test(`group inspector rebuilds current members on ${action}, clamps paging and closes removed groups`,{timeout:25000},async t=>{
 const {page,url,calls,setFlow}=await fixture(t);setFlow(denseFlow);await page.goto(url);await ready(page);
 await page.locator('.money-sankey .sankey-link[data-edge]').first().focus();await page.keyboard.press('Enter');await inspector(page).getByRole('heading',{name:'30 exact streams'}).waitFor();
 await inspector(page).getByRole('button',{name:'Next streams',exact:true}).click();await inspector(page).getByRole('button',{name:'Inspect Food stream 25',exact:true}).waitFor();
 if(action==='Back'){await page.getByRole('tab',{name:'Transfers',exact:true}).click();await ready(page);}
 const replacement={...denseFlow.edges[0],id:'new-food',label:'New food member',amount_minor:7000,count:7,target:'food30',selector:{endpoint:'transactions',filters:{...denseFlow.edges[0].selector.filters,category:'expense.food.new'}}};
 const changed={...denseFlow,edges:[...denseFlow.edges.filter(e=>!['food-stream-26','food-stream-27'].includes(e.id)),replacement]};setFlow(changed);
 if(action==='Back'){await page.getByRole('button',{name:'Back',exact:true}).click();await ready(page);}else await refresh(page);
 await inspector(page).getByRole('heading',{name:'29 exact streams'}).waitFor();assert.match(await inspector(page).locator('.inspector-amount').textContent(),/350\.00/);
 assert.deepEqual(await inspector(page).locator('.stream-member').allTextContents(),['Inspect Food stream 25','Inspect Food stream 28','Inspect Food stream 29','Inspect New food member']);
 assert.equal(calls.filter(c=>c.name==='transactions').length,0,'Groups must not invent aggregate evidence requests');
 const evidenceRequest=page.waitForRequest(request=>request.url().includes('/finance/transactions?'));
 await inspector(page).getByRole('button',{name:'Inspect New food member',exact:true}).click();await evidenceRequest;
 assert.equal(calls.filter(c=>c.name==='transactions').at(-1).q.category,'expense.food.new');assert.match(await inspector(page).locator('.inspector-amount').textContent(),/70\.00/);assert.match(await inspector(page).locator('.inspector-amount').textContent(),/7 recorded stream/);
 setFlow({...denseFlow,edges:denseFlow.edges.slice(0,10)});await page.getByRole('button',{name:'Back',exact:true}).click();await ready(page);
 assert.match(await inspector(page).textContent(),/10 exact streams/);assert.equal(await inspector(page).locator('.stream-member').count(),10);assert.equal(await inspector(page).getByRole('button',{name:'Previous streams',exact:true}).isDisabled(),true);
 const before=calls.filter(c=>c.name==='transactions').length;setFlow({...denseFlow,edges:[]});await refresh(page);assert.equal(await inspector(page).count(),0,'A group absent from the current projection closes');assert.equal(calls.filter(c=>c.name==='transactions').length,before);
});

for(const action of ['refresh','Back'])test(`exact inspector rebinds amount and count on ${action}; removed selection closes`,{timeout:25000},async t=>{
 const {page,url,calls,setFlow}=await fixture(t);await page.goto(url);await ready(page);
 await page.getByRole('button',{name:'Zoom in',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.money-sankey-view-controls output')?.textContent==='115%');const zoomed=await page.locator('.money-sankey-view-controls output').textContent();
 await inspect(page,'cash-food');await inspector(page).getByRole('button',{name:'Next page',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.flow-inspector .pager [role=status]')?.textContent==='26–26 of 26 records');assert.equal(await inspector(page).locator('.transaction-row').count(),1);
 if(action==='Back'){await page.getByRole('tab',{name:'Transfers',exact:true}).click();await ready(page);}
 setFlow({...baseFlow,edges:[{...edge('cash-food','cash',99000,52),label:'Updated food stream'},baseFlow.edges[1]]});
 if(action==='Back'){await page.getByRole('button',{name:'Back',exact:true}).click();await ready(page);}else await refresh(page);
 await page.waitForFunction(()=>document.querySelector('.flow-inspector .pager [role=status]')?.textContent==='26–26 of 26 records');assert.equal(await inspector(page).locator('.transaction-row').count(),1);
 assert.match(await inspector(page).locator('.inspector-amount').textContent(),/990\.00/);assert.match(await inspector(page).locator('.inspector-amount').textContent(),/52 recorded streams/);
 assert.equal(await inspector(page).getByRole('heading').textContent(),'Updated food stream');assert.equal(await page.locator('.money-sankey-view-controls output').textContent(),zoomed);
 assert.equal(calls.filter(c=>c.name==='transactions').at(-1).q.offset,'25');assert.ok(calls.filter(c=>c.name==='transactions').every(c=>c.q.limit==='50'&&c.q.category_mode==='exact'));
 if(action==='Back')await page.getByRole('button',{name:'Close inspector',exact:true}).click();
 const before=calls.filter(c=>c.name==='transactions').length;setFlow({...baseFlow,edges:[baseFlow.edges[1]]});
 if(action==='Back'){await page.getByRole('button',{name:'Back',exact:true}).click();await ready(page);}else await refresh(page);
 assert.equal(await inspector(page).count(),0,'Removed exact selections must not reopen old evidence');assert.equal(calls.filter(c=>c.name==='transactions').length,before);
});

async function assertCleared(page){
 assert.equal(await inspector(page).count(),0);assert.equal(await page.getByRole('button',{name:'Back',exact:true}).isDisabled(),true);
 assert.equal(await page.locator('[data-edge]').count(),0);assert.equal(await page.getByLabel('From',{exact:true}).inputValue(),lastQuarter().start);
 assert.equal(await page.locator('#filters select[name=account] option').count(),1);
 assert.match(await page.locator('#coverage').textContent(),/Private ledger is not loaded/);
 assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);assert.equal(await page.evaluate(async()=>(await indexedDB.databases()).length),0);
}
for(const code of [401,403])for(const failure of ['503','network'])test(`flow ${code} independently clears private state after overview ${failure}`,{timeout:20000},async t=>{
 const {page,url,calls}=await fixture(t);await page.goto(url);await ready(page);await inspect(page,'cash-food');
 let held,hit;const reached=new Promise(r=>hit=r);
 await page.route('**/finance/flow?**',route=>{held=route;hit();});
 await page.route('**/finance/overview?**',route=>failure==='503'?route.fulfill({status:503,json:{}}):route.abort('failed'));
 await page.getByRole('button',{name:'Refresh data',exact:true}).click();await reached;
 await page.getByRole('heading',{name:failure==='503'?'Recorded data unavailable':'Unable to load this view',exact:true}).waitFor();
 await held.fulfill({status:code,json:{}});
 await page.getByRole('heading',{name:'Finance is locked',exact:true}).waitFor();await assertCleared(page);
 await page.unroute('**/finance/flow?**');await page.unroute('**/finance/overview?**');
 await page.getByRole('button',{name:'Retry after sign-in',exact:true}).click();await ready(page);
 assert.equal(await inspector(page).count(),0);assert.equal(await page.getByRole('button',{name:'Back',exact:true}).isDisabled(),true);assert.equal(calls.filter(c=>c.name==='meta').length,2);
});
for(const code of [401,403])test(`flow ${code} locks without waiting for a pending overview`,{timeout:20000},async t=>{
 const {page,url}=await fixture(t);await page.goto(url);await ready(page);await inspect(page,'cash-food');
 let held,hit;const reached=new Promise(r=>hit=r);
 await page.route('**/finance/overview?**',route=>{held=route;hit();});await page.route('**/finance/flow?**',route=>route.fulfill({status:code,json:{}}));
 await page.getByRole('button',{name:'Refresh data',exact:true}).click();await reached;
 await page.getByRole('heading',{name:'Finance is locked',exact:true}).waitFor();await assertCleared(page);
 await held.fulfill({status:503,json:{}}).catch(()=>{});await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
 await page.getByRole('heading',{name:'Finance is locked',exact:true}).waitFor();
});
for(const code of [401,403])test(`late stale flow ${code} cannot lock a newer view`,{timeout:20000},async t=>{
 const {page,url}=await fixture(t);
 // Deliberately let a transport complete after cancellation to exercise the generation guard.
 await page.addInitScript(()=>{const original=window.fetch;window.fetch=async(url,options)=>{if(String(url).includes('/finance/flow?')){const response=await original(url,{...options,signal:undefined});if(response.status===401||response.status===403)window.staleDenialReceived=true;return response;}return original(url,options);};});
 await page.goto(url);await ready(page);let held,hit;const reached=new Promise(r=>hit=r);
 await page.route('**/finance/flow?**',route=>{held=route;hit();});await page.getByRole('button',{name:'Refresh data',exact:true}).click();await reached;
 await page.getByRole('tab',{name:'Transactions & evidence',exact:true}).click();await ready(page);await page.getByText('Synthetic evidence 1',{exact:true}).waitFor();
 await held.fulfill({status:code,json:{}});await page.waitForFunction(()=>window.staleDenialReceived);await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
 assert.equal(await page.getByRole('heading',{name:'Finance is locked',exact:true}).count(),0);assert.equal(await page.getByRole('tab',{name:'Transactions & evidence',exact:true}).getAttribute('aria-selected'),'true');await page.getByText('Synthetic evidence 1',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'Back',exact:true}).isDisabled(),false);
});

const ready=page=>page.waitForFunction(()=>document.querySelector('#content')?.getAttribute('aria-busy')==='false');
const refresh=async page=>{await page.getByRole('button',{name:'Refresh data',exact:true}).click();await ready(page);};
const inspector=page=>page.getByRole('complementary',{name:'Flow inspector'});
const inspect=async(page,id)=>{const finder=page.locator('.money-sankey-stream-finder');if(!await finder.evaluate(node=>node.open))await finder.locator('summary').click();await finder.getByLabel('Search exact movements').fill(id);await finder.getByRole('button',{name:new RegExp('Inspect '+id)}).click();await inspector(page).getByRole('button',{name:new RegExp('Inspect '+id)}).click();await inspector(page).locator('.transaction-row').first().waitFor();};
test('filters reset an unavailable map focus; Back restores the available focus and viewport',{timeout:20000},async t=>{
 const {page,url,calls}=await fixture(t);await page.goto(url);await ready(page);
 await page.getByText('Filters',{exact:true}).click();await page.locator('#filters').getByLabel('Account').selectOption('Reserve account');await page.getByRole('button',{name:'Apply filters',exact:true}).click();await ready(page);
 await page.getByRole('button',{name:'Zoom in',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.money-sankey-view-controls output')?.textContent==='115%');const zoomed=await page.locator('.money-sankey-view-controls output').textContent();
 await page.getByLabel('Scope').selectOption('all');await page.getByRole('button',{name:'Apply filters',exact:true}).click();await ready(page);
 assert.equal(await page.locator('#filters').getByLabel('Account').inputValue(),'Reserve account');assert.equal(await page.getByLabel('Scope').inputValue(),'all');
 assert.equal(await page.getByText('No recorded movements match these filters.').count(),1);
 await page.getByRole('button',{name:'Back',exact:true}).click();await ready(page);
 assert.equal(await page.locator('#filters').getByLabel('Account').inputValue(),'Reserve account');assert.equal(await page.getByLabel('Scope').inputValue(),'personal');assert.equal(await page.locator('.money-sankey-view-controls output').textContent(),zoomed);
 assert.ok(calls.filter(c=>c.name==='flow').some(c=>c.q.account==='Reserve account'));
});
