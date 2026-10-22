import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdir} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {artifactURL} from './artifacts.mjs';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {lastQuarter} from '../../frontend/finance/approved/finance-client.mjs';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const root=process.env.HERMES_FRONTEND_DIR || fileURLToPath(new URL('../../frontend/',import.meta.url));
const common={meta:{generated_at:'2024-08-15T12:00:00Z',source:'sqlite',warnings:[]}};
const tx={entry_id:'synthetic-1',record_id:'synthetic-r',date:'2024-08-01',account:'Fixture cash',description:'<img src=x onerror=alert(1)>',category_id:'expense.food',amount_minor:12000,currency:'CAD',source_id:'fixture-source',page:2,evidence:'Synthetic receipt',drive_url:'javascript:alert(1)',confidence:'reviewed'};
const flowNodes=[{id:'salary',label:'Synthetic salary',kind:'income'},{id:'cash',label:'Synthetic cash',kind:'account',account:'Fixture cash',accountGroup:'banking'},{id:'food',label:'Synthetic food',kind:'expense'}];
const flowEdges=[{id:'salary-cash',source:'salary',target:'cash',kind:'income',label:'Salary deposit',amount_minor:250000,count:1,currency:'CAD',selector:{endpoint:'transactions',filters:{account:'Fixture cash',category:'income.salary',category_mode:'exact',flow:'income',direction:'incoming'}}},{id:'cash-food',source:'cash',target:'food',kind:'expense',label:'Food purchase',amount_minor:12000,count:1,currency:'CAD',selector:{endpoint:'transactions',filters:{account:'Fixture cash',category:'expense.food',category_mode:'exact',flow:'expense',direction:'outgoing'}}}];
const fixtures={
 meta:{...common,range:{start:'2023-01-01',end:'2024-08-15'},currencies:['CAD','USD'],accounts:[{account:'Fixture cash',allocation:'personal'}],categories:[{category_id:'expense.food',label:'Food'}],coverage:{statements:3}},
 overview:{...common,totals:[{currency:'CAD',income_minor:250000,expense_minor:12000,net_minor:238000,transfer_in_minor:50000,transfer_out_minor:50000,investment_minor:10000,financing_minor:0,unresolved_minor:0}],monthly:[{month:'2024-08',currency:'CAD',income_minor:250000,expense_minor:12000,net_minor:238000}],categories:[{category_id:'income.salary',amount_minor:250000,count:1},{category_id:'expense.food',amount_minor:-12000,count:1}],balances:[{account:'Fixture investments',as_of:'2024-08-01',currency:'CAD',market_value_minor:990000,book_cost_minor:null,cash_minor:10000}],bank_balances:[],liabilities:[]},
 transactions:{...common,items:[tx],total:26,limit:25,offset:0},
 transfers:{...common,items:[{link_id:'fixture-link',link_type:'own_cash',debit:{record_id:'synthetic-r',account:'Fixture cash',date:'2024-07-31',amount_minor:-50000,currency:'CAD'},credit:{record_id:'synthetic-c',account:'Fixture savings',date:'2024-08-01',amount_minor:50000,currency:'CAD'},evidence:'Paired fixture',protected_boundary:false}],total:1,network:{nodes:['Fixture cash','Fixture savings'],links:[{source:'Fixture cash',target:'Fixture savings',amount_minor:50000,currency:'CAD',count:1}]},in_kind:[],reversals:[]},
 investments:{...common,balances:[{account:'Fixture investments',as_of:'2024-08-01',currency:'CAD',market_value_minor:990000,book_cost_minor:null,cash_minor:10000}],holdings:[{account:'Fixture investments',name:'Fixture security',security_key:'MSFT',quantity:'4.25',quantity_basis:'units',currency:'USD',as_of:'2024-08-01',market_value_minor:150000}],periods:[{account:'Fixture investments',start_date:'2024-08-01',end_date:'2024-08-31',currency:'CAD',reported_gain_minor:5000,net_gain_minor:4000,return_pct:1,return_method:'reported'}],history:[],realized:[],cashflows:[]},
 compensation:{...common,payroll:[{payroll_id:'fixture-pay',pay_date:'2024-08-01',currency:'CAD',gross_minor:500000,net_minor:350000},{payroll_id:'fixture-pay-old',pay_date:'2024-07-01',currency:'CAD',gross_minor:400000,net_minor:280000}],components:[{payroll_id:'fixture-pay',code:'Tax',kind:'tax',amount_minor:150000,cash_effect_minor:-150000,ytd_minor:900000}],coverage:{selected_payroll_id:'fixture-pay',payroll:{complete:true},components:{complete:true},links:{complete:true}},awards:[{award_id:'fixture-award',employer:'Synthetic company',grant_currency:'USD',grant_value_minor:100000,granted_shares:10,award_date:'2024-01-01',status:'scheduled'}],vesting:[{award_id:'fixture-award',vest_date:'2025-01-15',percent_basis_points:2500,theoretical_shares:2.5,actual_vested_shares:null,status:'scheduled'}]},
 obligations:{...common,tax_schedule:[{bill_id:'fixture-tax',due_date:'2024-09-01',currency:'CAD',amount_minor:25000,payment_status:'scheduled',bank_date:null}],facts:[{fact_id:'fixture-fact',account:'Fixture home',fact_name:'Review principal',value:'No current valuation',evidence:'Synthetic context'}],context:[]},
};
async function fixture(t){
 let denied=0,owner='fixture-owner';const calls=[];
 if((await readFile(root+'/finance/index.html','utf8')).includes('./plotly.bundle.js'))execFileSync(process.execPath,[fileURLToPath(new URL('../../scripts/build_finance_plotly.mjs',import.meta.url))],{cwd:fileURLToPath(new URL('../..',import.meta.url))});
 const server=createServer(async(req,res)=>{
  const url=new URL(req.url,'http://fixture');
  if(url.pathname==='/hermes/app-api/auth/me'){res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');if(denied){res.writeHead(denied);res.end('{}');return;}res.end(JSON.stringify({user:{id:owner,role:'owner',profile:'default',status:'ready'}}));return;}
  if(url.pathname.startsWith('/hermes/app-api/finance/')){
   const name=url.pathname.split('/').pop(),query=Object.fromEntries(url.searchParams);calls.push({name,query});res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');
   if(denied){res.writeHead(denied);res.end('{}');return;}
   if(name==='quotes'){res.writeHead(503);res.end('{}');return;}
   if(name==='flow'){res.end(JSON.stringify({...common,currency:query.currency||'CAD',range:{start:query.start,end:query.end},nodes:flowNodes,edges:flowEdges.map(edge=>({...edge,currency:query.currency||'CAD'})),coverage:{complete:true}}));return;}
   const data=fixtures[name];res.end(JSON.stringify(name==='transactions'?{...data,offset:Number(query.offset||0)}:data));return;
  }
  try{const rel=url.pathname.replace('/hermes/','').replace(/\/$/,'/index.html');if(rel.includes('..'))throw Error();const body=await readFile(root+'/'+rel);res.setHeader('Content-Type',rel.endsWith('.html')?'text/html':rel.endsWith('.css')?'text/css':'text/javascript');res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'");res.end(body);}catch{res.writeHead(404);res.end();}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',args:['--no-sandbox','--disable-dev-shm-usage']});t.after(()=>browser.close());
 const page=await browser.newPage({viewport:{width:1280,height:900},serviceWorkers:'block'});page.setDefaultTimeout(5000);
 return {page,calls,deny:code=>denied=code,setOwner:id=>owner=id,url:`http://127.0.0.1:${server.address().port}/hermes/finance/`};
}
async function openAnalysis(page){await page.waitForFunction(()=>document.querySelector('#content')?.getAttribute('aria-busy')==='false');const details=page.locator('[data-secondary]');if(await details.count())await details.locator(':scope > summary').click();const filters=page.locator('.filter-options');if(!await filters.evaluate(d=>d.open))await filters.locator('summary').click();}
test('Finance Overview mounts the approved amount-first Sankey inside the real filtered route',{timeout:30000},async t=>{const {page,url,calls}=await fixture(t);await page.goto(url);await page.waitForFunction(()=>document.querySelector('#content')?.getAttribute('aria-busy')==='false');await page.locator('#content .money-sankey .sankey-link').first().waitFor();assert.ok(calls.some(c=>c.name==='flow'&&c.query.currency==='CAD'&&c.query.scope==='personal'&&c.query.dataset==='all'));await page.getByLabel('From').fill('2026-08-01');await page.getByLabel('Currency').selectOption('USD');await page.getByRole('button',{name:'Apply filters'}).click();await page.waitForFunction(()=>document.querySelector('#content')?.getAttribute('aria-busy')==='false');assert.ok(calls.some(c=>c.name==='flow'&&c.query.currency==='USD'&&c.query.start==='2026-08-01'&&c.query.scope==='personal'&&c.query.dataset==='all'));});
test('visibility hides but retains the Finance chart and selection until bounded owner revalidation',{timeout:30000},async t=>{const {page,url,calls}=await fixture(t);await page.goto(url);await page.locator('#content .money-sankey .sankey-link').first().waitFor();const group=page.locator('.money-sankey-group button:first-child').first();await group.click();const selected=await group.getAttribute('aria-pressed'),flowCalls=()=>calls.filter(c=>c.name==='flow').length,before=flowCalls();await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});assert.equal(await page.locator('#content .money-sankey').count(),1);await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));});await page.waitForFunction(()=>!document.body.classList.contains('private-paused'));assert.equal(await group.getAttribute('aria-pressed'),selected);assert.equal(flowCalls(),before);});
test('owner denial while the retained view is hidden clears the chart on resume',{timeout:25000},async t=>{const {page,url,deny}=await fixture(t);await page.goto(url);await page.locator('#content .money-sankey .sankey-link').first().waitFor();await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});deny(403);await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));});await page.getByRole('heading',{name:'Finance is locked'}).waitFor();assert.equal(await page.locator('#content .money-sankey').count(),0);});
test('hard reset drops parked Sankey and payslip identity before another owner returns',{timeout:45000},async t=>{
 const {page,url,deny,setOwner}=await fixture(t);await page.goto(url);
 const finder=page.locator('.money-sankey-stream-finder');await finder.locator('summary').click();await finder.getByLabel('Search exact movements').fill('Food purchase');
 await finder.getByRole('button',{name:/Inspect Food purchase/}).click();
 await page.getByRole('tab',{name:'Compensation',exact:true}).click();
 const payslip=page.getByLabel('Select payslip');await payslip.selectOption('fixture-pay-old');
 await page.getByRole('tab',{name:'Overview',exact:true}).click();
 deny(403);await page.getByRole('button',{name:'Refresh data'}).click();await page.getByRole('heading',{name:'Finance is locked'}).waitFor();
 setOwner('replacement-owner');deny(0);await page.getByRole('button',{name:'Retry after sign-in'}).click();
 await page.locator('.money-sankey-stream-finder').waitFor();await page.getByRole('tab',{name:'Compensation',exact:true}).click();
 assert.equal(await page.getByLabel('Select payslip').inputValue(),'fixture-pay','A prior owner’s selected payslip is not restored');
 await page.getByRole('tab',{name:'Overview',exact:true}).click();
 const restoredFinder=page.locator('.money-sankey-stream-finder');await restoredFinder.waitFor();
 assert.equal(await restoredFinder.getByLabel('Search exact movements').inputValue(),'','A denied refresh cannot restore a parked finder query');
 await restoredFinder.locator('summary').click();await restoredFinder.getByLabel('Search exact movements').fill('Food purchase');
 await restoredFinder.getByRole('button',{name:/Inspect Food purchase/}).click();
 await page.getByRole('tab',{name:'Compensation',exact:true}).click();await page.getByLabel('Select payslip').selectOption('fixture-pay-old');
 await page.getByRole('tab',{name:'Overview',exact:true}).click();
 await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true})));
 await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));
 await page.locator('.money-sankey-stream-finder').waitFor();
 assert.equal(await page.locator('.money-sankey-stream-finder input').inputValue(),'','Pagehide clears the retained finder snapshot');
 await page.getByRole('tab',{name:'Compensation',exact:true}).click();
 assert.equal(await page.getByLabel('Select payslip').inputValue(),'fixture-pay','Pagehide clears the selected payslip identity');
});
test('converted CAD movement keeps its original USD selector and signed evidence',{timeout:30000},async t=>{
 const {page,url}=await fixture(t);
 await page.route('**/finance/flow?**',route=>{
  const q=new URL(route.request().url()).searchParams,currency=q.get('currency');
  const edge=currency==='USD'?[{id:'usd-food',source:'cash',target:'food',kind:'expense',label:'Original USD purchase',amount_minor:12345,count:1,currency:'USD',selector:{endpoint:'transactions',filters:{currency:'USD',account:'Fixture cash',category:'expense.food',category_mode:'exact',flow:'expense',direction:'outgoing'}}}]:[];
  return route.fulfill({json:{...common,currency,range:{start:q.get('start'),end:q.get('end')},nodes:flowNodes,edges:edge,coverage:{complete:true}}});
 });
 await page.route('**/finance/quotes**',route=>route.fulfill({json:{...common,fx:{pair:'USDCAD',rate:1.25,as_of:'2026-10-01',source:'Synthetic Bank of Canada fixture',status:'ok'}}}));
 let evidenceQuery;
 await page.route('**/finance/transactions?**',route=>{
  evidenceQuery=new URL(route.request().url()).searchParams;
  return route.fulfill({json:{...common,items:[{record_id:'usd-original-record',date:'2026-08-02',account:'Fixture cash',description:'Original signed USD posting',amount_minor:-12345,currency:'USD',source_id:'usd-source'}],total:1,limit:50,offset:0}});
 });
 await page.goto(url);await page.waitForFunction(()=>document.querySelector('#content')?.getAttribute('aria-busy')==='false');
 await page.locator('.money-map > p.muted').filter({hasText:/USD\/CAD.*1\.25.*Synthetic Bank of Canada fixture.*2026-10-01/}).waitFor();
 assert.ok(await page.locator('.money-sankey .sankey-link').count());
 const finder=page.locator('.money-sankey-stream-finder');await finder.locator('summary').click();
 await finder.getByLabel('Search exact movements').fill('cash');
 await finder.getByRole('button',{name:/Inspect/}).first().click();
 const inspector=page.getByRole('complementary',{name:'Flow inspector',exact:true});
 await inspector.getByRole('button',{name:/Inspect/}).first().click();
 await inspector.getByText('Original signed USD posting',{exact:true}).waitFor();
 assert.equal(evidenceQuery.get('currency'),'USD');
 assert.equal((await inspector.locator('.tx-amount').textContent()).replace(/\u00a0/g,' '),'-USD 123.45');
});
test('zero-cash USD evidence remains inspectable without FX or movement edges and rebinds on Refresh and Back',{timeout:30000},async t=>{
  const {page,url,calls}=await fixture(t);
  await page.route('**/finance/flow?**',route=>{
    const q=new URL(route.request().url()).searchParams,currency=q.get('currency');
    return route.fulfill({json:{...common,currency,range:{start:q.get('start'),end:q.get('end')},nodes:[],edges:[],coverage:{complete:true}}});
  });
  await page.route('**/finance/investments?**',route=>{
    const q=new URL(route.request().url()).searchParams,currency=q.get('currency');
    const cashflows=currency==='USD'?[{event_id:'zero-usd',account:'Fixture investments',security_key:'SYNTHETIC',classification:'buy',cash_amount_minor:0,currency:'USD',date:'2026-08-03'}]:[];
    return route.fulfill({json:{...common,balances:[],history:[],holdings:[],cashflows,coverage:{cashflows:{complete:true}}}});
  });
  await page.route('**/finance/transactions?**',route=>{
    const q=new URL(route.request().url()).searchParams;calls.push({name:'transactions',query:Object.fromEntries(q)});
    return route.fulfill({json:{...common,items:[{entry_id:'zero-usd',record_id:'investment:zero-usd',date:'2026-08-03',account:'Fixture investments',description:'Zero-cash USD trade evidence',category_id:'investment.trade.buy',amount_minor:0,currency:'USD',source_id:'zero-usd-source'}],total:1,limit:50,offset:Number(q.get('offset')||0)}});
  });
  await page.goto(url);await page.waitForFunction(()=>document.querySelector('#content')?.getAttribute('aria-busy')==='false');
  assert.equal(await page.locator('.money-sankey .sankey-link[data-edge]').count(),0);
  const details=page.locator('.foreign-unconverted');await details.locator('summary').waitFor();
  await details.locator('summary').click();
  const inspect=()=>details.getByRole('button').first();
  await inspect().click();
  const panel=page.getByRole('complementary',{name:'Flow inspector',exact:true});
  await panel.getByText('Zero-cash USD trade evidence',{exact:true}).waitFor();
  assert.equal((await panel.locator('.tx-amount').textContent()).replace(/\u00a0/g,' '),'USD 0.00');
  assert.deepEqual(calls.at(-1).query,{start:lastQuarter().start,end:lastQuarter().end,currency:'USD',scope:'personal',dataset:'investment',account:'Fixture investments',category:'investment.trade.buy',category_mode:'exact',flow:'investment',record_id:'investment:zero-usd',offset:'0',limit:'50'});
  await page.getByRole('button',{name:'Refresh data',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#content')?.getAttribute('aria-busy')==='false');
  await panel.getByText('Zero-cash USD trade evidence',{exact:true}).waitFor();
  await page.getByRole('tab',{name:'Cash flow',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#content')?.getAttribute('aria-busy')==='false');
  await page.getByRole('button',{name:'Back',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#content')?.getAttribute('aria-busy')==='false');
  await page.getByRole('complementary',{name:'Flow inspector',exact:true}).getByText('Zero-cash USD trade evidence',{exact:true}).waitFor();
});
test('a zero-cash purchase beside a sale is findable and opens its exact original record',{timeout:30000},async t=>{
 const {page,url,calls}=await fixture(t);
 await page.route('**/finance/flow?**',route=>{
  const q=new URL(route.request().url()).searchParams,currency=q.get('currency');
  const nodes=[{id:'fixture-broker',label:'Fixture investments',account:'Fixture investments',kind:'account'},
   {id:'sale-source',label:'Investment sale proceeds',kind:'investment'}];
  const edges=currency==='CAD'?[{id:'sale-edge',source:'sale-source',target:'fixture-broker',kind:'investment',label:'Recorded sale',amount_minor:1000,count:1,currency:'CAD',selector:{endpoint:'transactions',filters:{start:q.get('start'),end:q.get('end'),currency:'CAD',scope:q.get('scope'),dataset:q.get('dataset'),account:'Fixture investments',category:'investment.trade.sell',category_mode:'exact',flow:'investment',direction:'incoming'}}}]:[];
  return route.fulfill({json:{...common,currency,range:{start:q.get('start'),end:q.get('end')},nodes,edges,coverage:{complete:true}}});
 });
 await page.route('**/finance/investments?**',route=>{
  const q=new URL(route.request().url()).searchParams;
  const cashflows=q.get('currency')==='CAD'?[{event_id:'sale-event',account:'Fixture investments',security_key:'PAID',classification:'sell',date:'2026-08-02',currency:'CAD',cash_amount_minor:1000},{event_id:'zero-buy',account:'Fixture investments',security_key:'ZERO_BUY',classification:'buy',date:'2026-08-03',currency:'CAD',cash_amount_minor:0}]:[];
  return route.fulfill({json:{...common,cashflows,holdings:[],coverage:{cashflows:{complete:true}}}});
 });
 await page.route('**/finance/transactions?**',route=>{
  const q=new URL(route.request().url()).searchParams;calls.push({name:'transactions',query:Object.fromEntries(q)});
  return route.fulfill({json:{...common,items:[{record_id:'investment:zero-buy',entry_id:'zero-buy',date:'2026-08-03',account:'Fixture investments',description:'Zero-cash purchase record',category_id:'investment.trade.buy',amount_minor:0,currency:'CAD'}],total:1,limit:50,offset:0}});
 });
 await page.goto(url);await page.locator('.money-sankey').waitFor();
 const finder=page.locator('.money-sankey-stream-finder');await finder.locator('summary').click();await finder.getByLabel('Search exact movements').fill('ZERO_BUY');
 const result=finder.getByRole('button',{name:/Inspect Zero-cash buy/});await result.waitFor();await result.click();
 const inspector=page.getByRole('complementary',{name:'Flow inspector',exact:true});
 await inspector.getByRole('button',{name:/Inspect Zero-cash buy/}).click();
 await inspector.getByText('Zero-cash purchase record',{exact:true}).waitFor();
 assert.equal((await inspector.locator('.tx-amount').textContent()).replace(/\u00a0/g,' '),'CAD 0.00');
 const query=calls.findLast(call=>call.name==='transactions').query;
 assert.equal(query.record_id,'investment:zero-buy');assert.equal(query.category,'investment.trade.buy');
 assert.equal(query.direction,undefined);assert.equal(query.dataset,'investment');assert.equal(query.currency,'CAD');
 assert.equal(query.account,'Fixture investments');assert.equal(query.scope,'personal');
});
test('approved map exposes explorer lists and reachable zoom, Fit, pan, and group controls',{timeout:30000},async t=>{const {page,url}=await fixture(t);await page.goto(url);await page.locator('#content .money-sankey .sankey-link').first().waitFor();await page.getByRole('button',{name:'Fit map to view'}).click();await page.getByRole('button',{name:'Zoom in'}).click();await page.getByRole('button',{name:'Pan right'}).click();await page.getByText('Explore accounts, spending and categories',{exact:true}).click();await page.getByRole('tab',{name:'Accounts'}).click();await page.getByRole('button',{name:'Inspect account Synthetic cash'}).waitFor();await page.getByRole('tab',{name:'Spending'}).click();await page.getByRole('button',{name:/Inspect net spending Synthetic food/}).waitFor();await page.getByRole('tab',{name:'All categories'}).click();await page.locator('.money-sankey-explore-list button').filter({hasText:'Synthetic food'}).waitFor();});
test('empty filtered Sankey renders a bounded empty state without missing geometry',{timeout:25000},async t=>{const {page,url}=await fixture(t);await page.route('**/finance/flow?**',route=>{const q=new URL(route.request().url()).searchParams;return route.fulfill({json:{...common,currency:q.get('currency'),range:{start:q.get('start'),end:q.get('end')},nodes:[],edges:[],coverage:{complete:true}}});});const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(url);await page.getByText('No recorded movements match these filters.').waitFor();assert.deepEqual(errors,[]);});
test('Back restores the expanded account group and exploration mode',{timeout:30000},async t=>{const {page,url}=await fixture(t);await page.goto(url);await page.locator('#content .money-sankey .sankey-link').first().waitFor();await page.getByText('Explore accounts, spending and categories',{exact:true}).click();const categories=page.getByRole('tab',{name:'All categories'});await categories.click();const expand=page.locator('.money-sankey-controls .money-sankey-toggle').first();if(await expand.getAttribute('aria-expanded')==='true'){await expand.click();await page.waitForFunction(()=>document.querySelector('.money-sankey-controls .money-sankey-toggle[aria-expanded="false"]'));}await expand.click();await page.waitForFunction(()=>document.querySelector('.money-sankey-controls .money-sankey-toggle[aria-expanded="true"]'));assert.equal(await categories.getAttribute('aria-selected'),'true');await page.getByRole('tab',{name:'Cash flow',exact:true}).click();await page.getByRole('button',{name:'Back'}).click();await page.locator('#content .money-sankey .sankey-link').first().waitFor();const restoredCategories=page.getByRole('tab',{name:'All categories'});await restoredCategories.waitFor();await page.waitForFunction(()=>document.querySelector('.money-sankey-controls .money-sankey-toggle[aria-expanded="true"]'));assert.equal(await restoredCategories.getAttribute('aria-selected'),'true','Back restores the All categories exploration mode');});
test('category hierarchy expands from the actual Overview explorer',{timeout:30000},async t=>{const {page,url}=await fixture(t);await page.route('**/finance/flow?**',async route=>{const response=await route.fetch(),data=await response.json();data.nodes.push({id:'groceries',label:'Groceries',kind:'expense'});data.edges.push({id:'fixture-child-category',source:'cash',target:'groceries',kind:'expense',label:'Fixture groceries',amount_minor:5000,count:1,currency:data.currency,selector:{endpoint:'transactions',filters:{account:'Fixture cash',category:'expense.food.groceries',category_mode:'exact',flow:'expense'}}});return route.fulfill({response,json:data});});await page.goto(url);await page.locator('#content .money-sankey .sankey-link').first().waitFor();await page.getByText('Explore accounts, spending and categories',{exact:true}).click();await page.getByRole('tab',{name:'All categories'}).click();const expand=page.locator('.money-sankey-explore-row').filter({hasText:'Synthetic food'}).getByRole('button',{name:'Expand'});await expand.click();await page.locator('.money-sankey-explore-row').filter({hasText:'Groceries'}).waitFor();});
test('exact-account exploration opens only that account’s streams within a collapsed projection',{timeout:30000},async t=>{
 const {page,url,calls}=await fixture(t);
 await page.route('**/finance/flow?**',async route=>{
  const response=await route.fetch(),data=await response.json();
  data.nodes.push({id:'sibling',label:'Synthetic sibling',kind:'account',account:'Synthetic sibling',accountGroup:'banking'});
  data.edges.push({id:'sibling-food',source:'sibling',target:'food',kind:'expense',label:'Sibling food stream',amount_minor:7000,count:1,currency:data.currency,selector:{endpoint:'transactions',filters:{account:'Synthetic sibling',category:'expense.food',category_mode:'exact',flow:'expense',direction:'outgoing'}}});
  return route.fulfill({response,json:data});
 });
 await page.goto(url);await page.locator('#content .money-sankey .sankey-link').first().waitFor();
 await page.getByText('Explore accounts, spending and categories',{exact:true}).click();await page.getByRole('tab',{name:'Accounts'}).click();
 await page.getByRole('button',{name:'Inspect account Synthetic sibling',exact:true}).click();
 const inspector=page.getByRole('complementary',{name:'Flow inspector',exact:true});
 const summary=inspector.locator('p').filter({hasText:/exact streams/});await summary.waitFor();
 assert.match(await summary.textContent(),/^1 exact streams/);
 assert.equal(await inspector.getByRole('button',{name:'Inspect Sibling food stream',exact:true}).count(),1);
 assert.equal(await inspector.getByRole('button',{name:'Inspect Food purchase',exact:true}).count(),0);
 const request=page.waitForResponse(response=>response.url().includes('/finance/transactions?'));
 await inspector.getByRole('button',{name:'Inspect Sibling food stream',exact:true}).click();
 assert.equal(new URL((await request).url()).searchParams.get('account'),'Synthetic sibling');
 assert.equal(calls.filter(call=>call.name==='transactions').at(-1).query.account,'Synthetic sibling');
});
test('exact movement finder selects its projected band without redrawing the Sankey',{timeout:30000},async t=>{
 const {page,url,calls}=await fixture(t);await page.goto(url);
 await page.locator('.money-sankey .sankey-link[data-edge]').first().waitFor();
 const finder=page.locator('.money-sankey-stream-finder');await finder.locator('summary').click();
 await finder.getByLabel('Search exact movements').fill('Food purchase');
 const result=finder.getByRole('button',{name:/Inspect Food purchase/});await result.waitFor();
 const before=await page.locator('.money-sankey .sankey-link').evaluateAll(paths=>paths.map(path=>path.getAttribute('d')));
 const request=page.waitForRequest(request=>request.url().includes('/finance/transactions?'));
 await result.click();const inspector=page.getByRole('complementary',{name:'Flow inspector'});await inspector.getByRole('heading',{name:'Food purchase'}).waitFor();
 await inspector.getByRole('button',{name:'Inspect Food purchase',exact:true}).click();await request;
 assert.equal(await page.locator('.money-sankey .sankey-link.is-selected').count(),1);
 assert.deepEqual(await page.locator('.money-sankey .sankey-link').evaluateAll(paths=>paths.map(path=>path.getAttribute('d'))),before);
 assert.equal(calls.filter(call=>call.name==='transactions').at(-1).query.category,'expense.food');
});
test('exact movement finder pages every dense category and keeps the original final selector',{timeout:30000},async t=>{
 const {page,url}=await fixture(t);
 await page.route('**/finance/flow?**',async route=>{
  const response=await route.fetch(),data=await response.json();
  for(let i=0;i<107;i++){
   const id='dense-category-'+i;
   data.nodes.push({id,label:'Dense category '+i,kind:'expense'});
   data.edges.push({id:'dense-stream-'+i,source:'cash',target:id,kind:'expense',label:'Dense stream '+i,amount_minor:100+i,count:1,currency:data.currency,selector:{endpoint:'transactions',filters:{account:'Fixture cash',category:`expense.item${i}`,category_mode:'exact',flow:'expense',direction:'outgoing'}}});
  }
  return route.fulfill({response,json:data});
 });
 await page.goto(url);await page.locator('.money-sankey .sankey-link').first().waitFor();
 const finder=page.locator('.money-sankey-stream-finder');await finder.locator('summary').click();await finder.getByLabel('Search exact movements').fill('Dense stream');
 const results=finder.locator('.money-sankey-stream-results');await finder.getByText('1–25 of 107 matching movements.').waitFor();
 assert.equal(await results.locator('button[aria-label^="Inspect Dense stream"]').count(),25);
 for(let pageNumber=1;pageNumber<5;pageNumber++)await results.getByRole('button',{name:'Next matching movements'}).click();
 await finder.getByText('101–107 of 107 matching movements.').waitFor();
 assert.equal(await results.locator('button[aria-label^="Inspect Dense stream"]').count(),7);
 assert.equal(await results.getByRole('button',{name:'Next matching movements'}).isDisabled(),true);
 const request=page.waitForRequest(request=>request.url().includes('/finance/transactions?'));
 await results.getByRole('button',{name:/Inspect Dense stream 106/}).click();
 const inspector=page.getByRole('complementary',{name:'Flow inspector'});await inspector.getByRole('button',{name:'Inspect Dense stream 106',exact:true}).click();await request;
 assert.equal(new URL((await request).url()).searchParams.get('category'),'expense.item106');
});
test('selected historical payslip loads its own dated detail without borrowing the newest payslip',{timeout:25000},async t=>{const {page,url}=await fixture(t);let selectedRange='';await page.route('**/finance/compensation?**',async route=>{const requestURL=new URL(route.request().url());selectedRange=requestURL.searchParams.get('start')||'';if(selectedRange==='2024-07-01')return route.fulfill({json:{...common,payroll:[{payroll_id:'fixture-pay-old',pay_date:'2024-07-01',period_end:'2024-06-30',currency:'CAD',gross_minor:400000,net_minor:280000}],components:[{payroll_id:'fixture-pay-old',code:'Old tax',kind:'tax',amount_minor:120000,cash_effect_minor:-120000}],coverage:{selected_payroll_id:'fixture-pay-old',payroll:{complete:true},components:{complete:true},links:{complete:true}}}});return route.fulfill({json:fixtures.compensation});});await page.goto(url);await openAnalysis(page);await page.getByRole('tab',{name:'Compensation',exact:true}).click();await page.getByText('All selected payslips · table alternative').click();await page.getByRole('button',{name:'Inspect 2024-07-01'}).click();await page.getByText('Selected payslip components · table alternative').click();await page.getByRole('table',{name:'Selected payslip components'}).getByText('Old tax',{exact:true}).waitFor();assert.equal(selectedRange,'2024-07-01');await page.getByText('Selected payslip · table alternative').click();await page.getByRole('table',{name:'Selected payslip'}).getByText('2024-07-01',{exact:true}).waitFor();});
test('seven finance surfaces: real filters, diagrams, evidence, mobile tables and paginated search',{timeout:60000},async t=>{const {page,url,calls}=await fixture(t);const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error'&&!m.text().includes('503'))errors.push(m.text());});await page.goto(url);await openAnalysis(page);await page.getByRole('heading',{name:'Recorded overview'}).waitFor();assert.equal(await page.getByRole('tab').count(),7);assert.equal(await page.getByLabel('From').inputValue(),lastQuarter().start);await page.locator('[data-chart="alluvial"]').waitFor();await mkdir(artifactURL(),{recursive:true});for(const width of [390,768,1280]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.screenshot({path:fileURLToPath(artifactURL(`finance-overview-${width}.png`)),fullPage:true});}console.log('Finance screenshots:',fileURLToPath(artifactURL()));await page.getByRole('button',{name:'Inspect Food',exact:true}).first().click();await page.getByRole('heading',{name:'Transactions & evidence',exact:true}).waitFor();assert.ok(calls.some(c=>c.name==='transactions'&&c.query.category==='expense.food'));assert.equal(await page.locator('tbody img').count(),0);await page.getByLabel('Search transactions').fill('receipt');await page.getByRole('button',{name:'Search',exact:true}).click();await page.getByRole('button',{name:'Next page'}).click();await page.waitForFunction(()=>document.querySelector('[data-page-status]')?.textContent.includes('26'));assert.ok(calls.some(c=>c.name==='transactions'&&c.query.offset==='25'&&c.query.q==='receipt'));for(const [tab,heading,chart] of [['Cash flow','Cash flow','monthly'],['Transfers','Transfers','network'],['Investments','Investments','allocation'],['Compensation','Compensation','vesting'],['Obligations','Obligations','schedule']]){await page.getByRole('tab',{name:tab,exact:true}).click();await page.getByRole('heading',{name:heading,exact:true}).waitFor();await page.locator(`[data-chart="${chart}"]`).waitFor();assert.ok(await page.locator('.data-alternative').count());for(const width of [390,768,1280]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${tab} fits ${width}`);}await page.screenshot({path:fileURLToPath(artifactURL(`finance-${chart}-1280.png`)),fullPage:true});}await page.getByRole('tab',{name:'Investments',exact:true}).click();await page.getByText('Market prices unavailable',{exact:false}).waitFor();for(const width of [390,768,1280]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`no page overflow at ${width}`);}assert.deepEqual(await page.evaluate(()=>({local:localStorage.length,session:sessionStorage.length})),{local:0,session:0});assert.deepEqual(errors,[]);});
test('secondary records stay available on demand across all seven Finance views',{timeout:45000},async t=>{
 const {page,url}=await fixture(t);await page.goto(url);await openAnalysis(page);
 const openTable=async name=>{await page.getByText(`${name} · table alternative`,{exact:true}).click();return page.getByRole('table',{name,exact:true});};
 const waitForMapLayout=()=>page.waitForFunction(()=>{const stage=document.querySelector('.money-sankey');return !stage||(stage.getAttribute('aria-busy')==='false'&&stage.dataset.layoutWidth===String(stage.clientWidth));});
 const checkMobile=async view=>{
  await page.setViewportSize({width:390,height:900});
  await waitForMapLayout();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${view} fits 390px`);
  const shortControls=await page.locator('button:visible,input:visible,select:visible,summary:visible').evaluateAll(nodes=>nodes.flatMap(node=>{const rect=node.getBoundingClientRect();if(Math.round(rect.height)>=44)return [];return [{tag:node.tagName,role:node.getAttribute('role'),label:node.getAttribute('aria-label'),text:(node.innerText||node.textContent||'').trim().slice(0,100),className:String(node.className),height:rect.height,width:rect.width,bounds:{top:rect.top,right:rect.right,bottom:rect.bottom,left:rect.left},minHeight:getComputedStyle(node).minHeight,display:getComputedStyle(node).display}];}));
  assert.deepEqual(shortControls,[],`${view} controls remain at least 44px tall; undersized visible controls: ${JSON.stringify(shortControls)}`);
 };
 await page.getByRole('tab',{name:'Overview',exact:true}).click();
 await page.locator('.money-sankey .sankey-link').first().waitFor();await waitForMapLayout();
 const overviewDetails=page.locator('[data-secondary]');if(!await overviewDetails.evaluate(node=>node.open))await overviewDetails.locator(':scope > summary').click();
 await page.getByRole('heading',{name:'Financial position',exact:true}).waitFor();
 await page.getByText('Financial position & cash-flow analysis',{exact:true}).waitFor();await checkMobile('Overview');
 await page.getByRole('tab',{name:'Cash flow',exact:true}).click();
 let table=await openTable('Other classified movements');const transfersIn=table.locator('tbody tr').filter({hasText:'Transfers in'});await transfersIn.getByText('CAD 500.00',{exact:true}).waitFor();await checkMobile('Cash flow');
 await page.getByRole('tab',{name:'Transfers',exact:true}).click();
 table=await openTable('Transfer evidence');await table.getByText('Fixture cash',{exact:true}).waitFor();await table.getByText('2024-07-31',{exact:true}).waitFor();
 table=await openTable('Same-account reversals');assert.equal(await table.locator('tbody tr').count(),0);await checkMobile('Transfers');
 await page.route('**/finance/investments?**',route=>route.fulfill({json:{...fixtures.investments,holdings:fixtures.investments.holdings.map(holding=>({...holding,quantity_basis:'shares'}))}}));
 await page.route('**/finance/quotes?**',route=>route.fulfill({json:{...common,quotes:[{symbol:'MSFT',price:123,currency:'USD',as_of:'2024-08-14T12:00:00Z',fetched_at:'2024-08-14T12:01:00Z',source:'Synthetic quote source',status:'ok',stale:false}]}}));
 await page.getByRole('tab',{name:'Investments',exact:true}).click();
 table=await openTable('Recorded holdings');await table.getByText('Fixture security',{exact:true}).waitFor();await table.getByText('2024-08-01',{exact:true}).waitFor();
 table=await openTable('Monthly investment performance');await table.getByText('1% (reported)',{exact:true}).waitFor();
 table=await openTable('Realized investment results');assert.equal(await table.locator('tbody tr').count(),0);await checkMobile('Investments');
 table=await openTable('Public quote estimates');await table.getByText(/Synthetic quote source/).waitFor();await table.getByText(/2024-08-14T12:00:00Z/).waitFor();
 await page.getByRole('tab',{name:'Compensation',exact:true}).click();
 table=await openTable('Selected payslip components');await table.getByText('Tax',{exact:true}).waitFor();await table.getByText('CAD 9,000.00',{exact:true}).waitFor();
 table=await openTable('Selected payslip');await table.getByText('2024-08-01',{exact:true}).waitFor();
 table=await openTable('Grant reference values');await table.getByText('USD 1,000.00',{exact:true}).waitFor();await checkMobile('Compensation');
 await page.getByRole('tab',{name:'Obligations',exact:true}).click();
 table=await openTable('Property-tax obligations');await table.getByText('2024-09-01',{exact:true}).waitFor();await table.getByText('scheduled',{exact:true}).waitFor();
 table=await openTable('Sourced financial facts');await table.getByText('No current valuation',{exact:true}).waitFor();await checkMobile('Obligations');
 await page.getByRole('tab',{name:'Transactions & evidence',exact:true}).click();
 const ledger=page.getByRole('table',{name:'Transactions',exact:true});await ledger.getByText(tx.description,{exact:true}).waitFor();await ledger.getByText('CAD 120.00',{exact:true}).waitFor();await checkMobile('Transactions & evidence');
});
test('upcoming obligations use explicit full-year policy and render source-backed notes', {timeout:20000},async t=>{const {page,url,calls}=await fixture(t);const original=fixtures.obligations.notes;fixtures.obligations.notes=[{topic:'obligations',title:'Synthetic coverage note',body:'Read-only fixture context',as_of:'2024-08-01',source_id:'fixture-note'}];t.after(()=>{fixtures.obligations.notes=original;});await page.goto(url);await openAnalysis(page);await page.getByRole('tab',{name:'Obligations',exact:true}).click();await page.getByText('Synthetic coverage note',{exact:true}).waitFor();assert.ok(calls.some(c=>c.name==='obligations'&&c.query.start===lastQuarter().end.slice(0,4)+'-01-01'&&c.query.end===lastQuarter().end.slice(0,4)+'-12-31'));await page.getByLabel('Schedule date policy').selectOption('selected');await page.waitForFunction(()=>document.querySelector('#content').getAttribute('aria-busy')==='false');assert.ok(calls.some(c=>c.name==='obligations'&&c.query.end===lastQuarter().end));});
test('award estimates stay separate from grant reference and use original currency',{timeout:20000},async t=>{const {page,url}=await fixture(t);await page.route('**/finance/compensation?**',route=>route.fulfill({json:{...fixtures.compensation,awards:[{...fixtures.compensation.awards[0],symbol:'MSFT'}]}}));await page.route('**/finance/quotes?**',route=>route.fulfill({json:{...common,quotes:[{symbol:'MSFT',price:200,currency:'USD',as_of:'2024-08-15T12:00:00Z',fetched_at:'2024-08-15T12:01:00Z',source:'synthetic',status:'ok',stale:false}]}}));await page.goto(url);await openAnalysis(page);await page.getByRole('tab',{name:'Compensation',exact:true}).click();await page.getByRole('heading',{name:'Unvested award price context'}).waitFor();await page.getByText('Public quote estimates · table alternative',{exact:true}).click();await page.getByText('USD 2,000.00',{exact:true}).waitFor();assert.equal(await page.getByLabel('Currency').inputValue(),'CAD');await page.getByText('Grant reference values · table alternative',{exact:true}).click();await page.getByText('USD 1,000.00',{exact:true}).waitFor();});
test('transfer network visibly binds account filters; keyboard tabs and lifecycle clear stale data', {timeout:25000},async t=>{const {page,url,calls}=await fixture(t);await page.goto(url);await openAnalysis(page);await page.getByRole('tab',{name:'Overview',exact:true}).focus();await page.keyboard.press('ArrowRight');await page.getByRole('heading',{name:'Cash flow',exact:true}).waitFor();await page.getByRole('tab',{name:'Transfers',exact:true}).click();await page.getByRole('button',{name:'Inspect account Fixture cash',exact:true}).click();await page.getByRole('heading',{name:'Transfers',exact:true}).waitFor();assert.equal(await page.locator('#filters select[name="account"]').inputValue(),'Fixture cash');assert.ok(calls.some(c=>c.name==='transfers'&&c.query.account==='Fixture cash'));assert.equal(calls.filter(c=>c.name==='transactions').length,0);await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true})));await page.getByRole('heading',{name:'Private view paused'}).waitFor();assert.equal(await page.getByRole('table',{name:'Transfer evidence',exact:true}).count(),0);assert.equal(await page.getByText('<img src=x onerror=alert(1)>',{exact:true}).count(),0);await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));await page.getByRole('heading',{name:'Transfers',exact:true}).waitFor();});
test('overview disclosure retains recorded financial position without double-counting account cash',{timeout:20000},async t=>{const {page,url}=await fixture(t);await page.route('**/finance/overview?**',route=>route.fulfill({json:{...fixtures.overview,balances:[{account:'Fixture investment',as_of:'2024-08-01',currency:'CAD',market_value_minor:10000,cash_minor:2000,planning_protected:0}],bank_balances:[{account:'Fixture liquidity',as_of:'2024-08-01',currency:'CAD',amount_minor:5000,planning_protected:0}],liabilities:[{account:'Fixture debt',as_of:'2024-08-01',currency:'CAD',amount_minor:4000}]}}));await page.goto(url);await openAnalysis(page);await page.getByRole('heading',{name:'Financial position',exact:true}).waitFor();await page.locator('[data-position="net"]').getByText('CAD 110.00',{exact:true}).waitFor();await page.locator('[data-position="liquid"]').getByText('CAD 50.00',{exact:true}).waitFor();await page.getByText('Excludes home/car; not net worth.',{exact:true}).waitFor();});
test('late reads cannot repaint a newer tab; empty, unavailable and auth states remain distinct',{timeout:25000},async t=>{const {page,url}=await fixture(t);let held,hit;const reached=new Promise(r=>hit=r);await page.route('**/finance/overview?**',route=>{held=route;hit();});await page.goto(url);await reached;await page.getByRole('tab',{name:'Transactions & evidence',exact:true}).click();await page.getByRole('heading',{name:'Transactions & evidence',exact:true}).waitFor();await held.fulfill({json:fixtures.overview}).catch(()=>{});assert.equal(await page.getByRole('heading',{name:'Recorded overview'}).count(),0);await page.route('**/finance/transactions?**',route=>route.fulfill({json:{...common,items:[],total:0,limit:25,offset:0}}));await page.getByRole('button',{name:'Refresh data',exact:true}).click();await page.getByText('No matching records',{exact:true}).waitFor();await page.route('**/finance/obligations?**',route=>route.fulfill({status:503,json:{}}));await page.getByRole('tab',{name:'Obligations',exact:true}).click();await page.getByRole('heading',{name:'Recorded data unavailable'}).waitFor();await page.route('**/finance/investments?**',route=>route.fulfill({status:403,json:{}}));await page.getByRole('tab',{name:'Investments',exact:true}).click();await page.getByRole('heading',{name:'Finance is locked'}).waitFor();assert.equal(await page.getByLabel('From').inputValue(),lastQuarter().start);assert.equal(await page.evaluate(()=>performance.getEntriesByType('resource').filter(x=>new URL(x.name).origin!==location.origin).length),0);assert.equal(await page.evaluate(async()=> (await indexedDB.databases()).length),0);});
test('negative recoveries and drawdowns remain inside monthly chart bounds',{timeout:20000},async t=>{const {page,url}=await fixture(t);await page.route('**/finance/overview?**',route=>route.fulfill({json:{...fixtures.overview,monthly:[{month:'2024-08',currency:'CAD',income_minor:10000,expense_minor:-50000,net_minor:60000}]}}));await page.goto(url);await openAnalysis(page);await page.locator('[data-chart="monthly"]').waitFor();assert.equal(await page.locator('[data-chart="monthly"]').evaluate(s=>[...s.querySelectorAll('rect')].every(r=>Number(r.getAttribute('y'))>=0&&Number(r.getAttribute('y'))+Number(r.getAttribute('height'))<s.viewBox.baseVal.height-15)&&[...s.querySelectorAll('line')].every(r=>Number(r.getAttribute('y1'))<=s.viewBox.baseVal.height)),true);});
test('payroll waterfall does not add source salary a second time',{timeout:20000},async t=>{const {page,url}=await fixture(t);await page.route('**/finance/compensation?**',route=>route.fulfill({json:{...fixtures.compensation,components:[{payroll_id:'fixture-pay',code:'Salary',kind:'earning',amount_minor:500000,cash_effect_minor:500000},...fixtures.compensation.components]}}));await page.goto(url);await openAnalysis(page);await page.getByRole('tab',{name:'Compensation',exact:true}).click();await page.locator('[data-chart="waterfall"]').waitFor();assert.equal(await page.locator('[data-chart="waterfall"] rect').count(),3);await page.getByText('Bridge reconciles to recorded bank net.',{exact:true}).waitFor();});
test('investment performance never connects different accounts as one series',{timeout:20000},async t=>{const {page,url}=await fixture(t);await page.route('**/finance/investments?**',route=>route.fulfill({json:{...fixtures.investments,periods:[...fixtures.investments.periods,{...fixtures.investments.periods[0],account:'Other fixture account'}]}}));await page.goto(url);await openAnalysis(page);await page.getByRole('tab',{name:'Investments',exact:true}).click();await page.getByText('Select an account for a comparable monthly performance chart.',{exact:true}).waitFor();assert.equal(await page.locator('[data-chart="monthly"]').count(),0);await page.getByText('Monthly investment performance · table alternative').click();assert.equal(await page.getByRole('table',{name:'Monthly investment performance'}).locator('tbody tr').count(),2);});
test('recorded unrealized gains and decimal-text reported returns are visible',{timeout:20000},async t=>{const {page,url}=await fixture(t);await page.route('**/finance/investments?**',route=>route.fulfill({json:{...fixtures.investments,holdings:[{...fixtures.investments.holdings[0],unrealized_gain_minor:2500,unrealized_gain_pct:2.5}],periods:[{...fixtures.investments.periods[0],return_pct:'8.125'}]}}));await page.goto(url);await openAnalysis(page);await page.getByRole('tab',{name:'Investments',exact:true}).click();await page.getByText('Recorded holdings · table alternative').click();await page.getByRole('table',{name:'Recorded holdings'}).getByText('USD 25.00',{exact:true}).waitFor();await page.getByText('Monthly investment performance · table alternative').click();await page.getByText('8.125% (reported)',{exact:true}).waitFor();});
for(const status of [401,403])test(`finance ${status} clears all financial state and lifecycle revalidates`,{timeout:20000},async t=>{const {page,url,deny}=await fixture(t);await page.goto(url);await openAnalysis(page);await page.getByText('Investment balances · table alternative',{exact:true}).click();await page.getByText('Fixture investments',{exact:true}).first().waitFor();deny(status);await page.getByRole('button',{name:'Refresh data',exact:true}).click();await page.getByRole('heading',{name:'Finance is locked'}).waitFor();assert.equal(await page.getByText('Fixture investments',{exact:true}).count(),0);assert.equal(await page.locator('#filters').getByLabel('Account').locator('option').count(),1);assert.equal(await page.getByRole('link',{name:'Sign in with passkey'}).getAttribute('href'),'/hermes/');});
test('mounted explorer keeps payroll rows, zero-cash evidence, and excludes bridged duplicates',{timeout:30000},async t=>{
 const {page,url}=await fixture(t);
 await page.route('**/finance/flow?**',async route=>{
  const response=await route.fetch(),data=await response.json();
  data.edges.push({id:'payroll-inline',source:'salary',target:'cash',kind:'income',label:'Payroll gross stream',amount_minor:50000,count:1,currency:data.currency,evidenceNote:'Synthetic payslip source',inlineRows:[{entry_id:'payroll-gross',record_id:'payroll-gross',date:'2024-08-01',account:'Fixture cash',description:'Payroll gross inline row',amount_minor:50000,currency:data.currency}],members:[{id:'zero-cash-source',kind:'investment',label:'Zero-cash source',amount_minor:0,currency:data.currency,selector:{endpoint:'transactions',filters:{record_id:'zero-cash-record',currency:data.currency,account:'Fixture cash'}}}]  });
  const food=data.edges.find(edge=>edge.id==='cash-food');food.excludeRecordIds=['synthetic-r'];
  return route.fulfill({response,json:data});
 });
 await page.route('**/finance/transactions?**',route=>{
  const query=new URL(route.request().url()).searchParams;
  const item=query.get('record_id')==='zero-cash-record'
   ?{entry_id:'zero-cash-row',record_id:'zero-cash-record',date:'2024-08-02',account:'Fixture cash',description:'Zero-cash source evidence',amount_minor:0,currency:'CAD'}
   :tx;
  return route.fulfill({json:{...common,items:[item],total:1,limit:50,offset:Number(query.get('offset')||0)}});
 });
 await page.goto(url);await page.locator('#content .money-sankey .sankey-link').first().waitFor();
 await page.locator('.money-sankey-group [data-group-select]').first().click();
 await page.getByRole('button',{name:'Inspect Payroll gross stream'}).click();
 await page.getByText('Payroll gross inline row',{exact:true}).waitFor();
 await page.getByText('Zero-cash source evidence',{exact:true}).waitFor();
 assert.equal(await page.locator('.flow-inspector .transaction-row').count(),2);
 await page.getByRole('button',{name:'Close inspector'}).click();
 await page.locator('.money-sankey-group [data-group-select]').first().click();
 await page.getByRole('button',{name:'Inspect Food purchase'}).click();
 await page.getByText('No matching records',{exact:true}).waitFor();
 assert.equal(await page.getByText(tx.description,{exact:true}).count(),0,'bridge-assigned transaction must not be counted a second time');
});
test('mounted investment group exposes a zero-only distinct security as exact evidence',{timeout:30000},async t=>{
 const {page,url,calls}=await fixture(t);
 await page.route('**/finance/flow?**',async route=>{
  const response=await route.fetch(),data=await response.json(),q=new URL(route.request().url()).searchParams;
  if(q.get('currency')==='CAD'){
   data.nodes.push({id:'investment-purchases',kind:'investment',label:'Investment purchases'});
   data.edges.push({id:'synthetic-investment-trades',source:'cash',target:'investment-purchases',kind:'investment',label:'Synthetic investment trades',amount_minor:5000,count:2,currency:'CAD',selector:{endpoint:'transactions',filters:{start:q.get('start'),end:q.get('end'),currency:'CAD',scope:'personal',dataset:'all',account:'Fixture cash',category:'investment.trade.buy',category_mode:'exact',flow:'investment',direction:'outgoing'}}});
  }
  return route.fulfill({response,json:data});
 });
 await page.route('**/finance/investments?**',route=>{
  const q=new URL(route.request().url()).searchParams,cashflows=q.get('currency')==='CAD'?[
   {event_id:'paid-a',account:'Fixture cash',security_key:'PAID',classification:'buy',cash_amount_minor:-5000,currency:'CAD',date:'2026-08-02'},
   {event_id:'zero-only-b',account:'Fixture cash',security_key:'ZERO',classification:'buy',cash_amount_minor:0,currency:'CAD',date:'2026-08-03'}
  ]:[];
  return route.fulfill({json:{...fixtures.investments,cashflows,coverage:{cashflows:{complete:true}}}});
 });
 await page.route('**/finance/transactions?**',route=>{
  const q=new URL(route.request().url()).searchParams;calls.push({name:'transactions',query:Object.fromEntries(q)});
  return route.fulfill({json:{...common,items:[{record_id:q.get('record_id'),date:'2026-08-03',account:'Fixture cash',description:'Zero-cash distinct security',amount_minor:0,currency:'CAD'}],total:1,limit:50,offset:0}});
 });
 await page.goto(url);await page.locator('.money-sankey .sankey-link').first().waitFor();
 await page.locator('.money-sankey-controls [data-group-select]').filter({hasText:'Investments'}).click();
 const inspector=page.getByRole('complementary',{name:'Flow inspector',exact:true});
 await inspector.getByRole('button',{name:/Inspect Zero-cash buy · ZERO/}).click();
 await inspector.getByText('Zero-cash distinct security',{exact:true}).waitFor();
 assert.equal(calls.filter(call=>call.name==='transactions').at(-1).query.record_id,'investment:zero-only-b');
 assert.equal((await inspector.locator('.tx-amount').textContent()).replace(/\u00a0/g,' '),'CAD 0.00');
});
test('account and net-spending explorer choices select projected movements',{timeout:25000},async t=>{
 const {page,url}=await fixture(t);await page.goto(url);
 await page.locator('#content .money-sankey .sankey-link').first().waitFor();
 await page.getByText('Explore accounts, spending and categories',{exact:true}).click();
 await page.getByRole('tab',{name:'Accounts'}).click();
 await page.getByRole('button',{name:'Inspect account Synthetic cash'}).click();
 await page.locator('.flow-inspector').waitFor();
 await page.getByRole('button',{name:'Close inspector'}).click();
 await page.getByRole('tab',{name:'Spending'}).click();
 await page.getByRole('button',{name:/Inspect net spending Synthetic food/}).click();
 await page.locator('.flow-inspector').waitFor();
 assert.ok(await page.locator('.flow-inspector .stream-member').count()>0);
});

test('category expansion uses final geometry width and folding reclaims it',{timeout:30000},async t=>{
 const {page,url}=await fixture(t);
 await page.route('**/finance/flow?**',async route=>{
  const response=await route.fetch(),data=await response.json();
  data.nodes.push({id:'deep-food',label:'Restaurant',kind:'expense'});
  data.edges.push({id:'deep-food-edge',source:'cash',target:'deep-food',kind:'expense',label:'Restaurant purchase',amount_minor:5000,count:1,currency:data.currency,selector:{endpoint:'transactions',filters:{account:'Fixture cash',category:'expense.food.dining.restaurant',category_mode:'exact',flow:'expense'}}});
  return route.fulfill({response,json:data});
 });
 await page.goto(url);await page.locator('#content .money-sankey .sankey-link').first().waitFor();
 await page.getByText('Explore accounts, spending and categories',{exact:true}).click();
 await page.getByRole('tab',{name:'All categories'}).click();
 const chart=page.locator('.money-sankey-chart'),band=page.locator('.money-sankey-band').first();
 await page.getByRole('button',{name:'Fit map to view'}).click();
 await page.waitForFunction(()=>{const chart=document.querySelector('.money-sankey-chart'),stage=chart?.closest('.money-sankey');return chart?._fullLayout?.width===stage?.clientWidth;});
 const initial=await chart.evaluate(node=>node._fullLayout.width),anchor=await band.evaluate(node=>node.getBoundingClientRect().top-node.closest('.money-sankey-layer').getBoundingClientRect().top);
 await page.locator('.money-sankey-explore-row').filter({hasText:'Synthetic food'}).getByRole('button',{name:'Expand'}).click();
 await page.locator('.money-sankey-explore-row').filter({hasText:'Dining'}).getByRole('button',{name:'Expand'}).click();
 await page.getByText('Restaurant · expense',{exact:false}).waitFor();
 await page.waitForFunction(width=>document.querySelector('.money-sankey-chart')?._fullLayout?.width>width+200,initial);
 assert.equal(await band.evaluate(node=>node.getBoundingClientRect().top-node.closest('.money-sankey-layer').getBoundingClientRect().top),anchor,'account group heading remains anchored');
 const expanded=await chart.evaluate(node=>node._fullLayout.width);
 await page.locator('.money-sankey-explore-row').filter({hasText:'Synthetic food'}).getByRole('button',{name:'Fold'}).click();
 await page.waitForFunction(width=>document.querySelector('.money-sankey-chart')?._fullLayout?.width===width,initial);
 assert.ok(expanded>initial);
});

test('unconverted USD activity has selectable native evidence with no dated FX',{timeout:25000},async t=>{
 const {page,url}=await fixture(t);let inspectorCurrency='';
 await page.route('**/finance/flow?**',async route=>{
  const response=await route.fetch(),data=await response.json(),query=new URL(route.request().url()).searchParams;
  if(query.get('currency')==='USD')data.edges=[{id:'usd-native-edge',source:'salary',target:'cash',kind:'income',label:'Original USD deposit',amount_minor:12345,count:1,currency:'USD',evidenceNote:'Synthetic link status',selector:{endpoint:'transactions',filters:{account:'Fixture cash',category:'income.salary',category_mode:'exact',flow:'income',direction:'incoming'}}}];
  return route.fulfill({response,json:data});
 });
 await page.route('**/finance/transactions?**',route=>{inspectorCurrency=new URL(route.request().url()).searchParams.get('currency');return route.fulfill({json:{...common,items:[{...tx,entry_id:'usd-native',record_id:'usd-native-record',currency:'USD',amount_minor:12345,drive_url:'https://drive.google.com/file/synthetic',description:'Original USD evidence'}],total:1,limit:50,offset:0}});});
 await page.goto(url);await page.locator('.money-map').getByText('USD activity is shown separately because a dated conversion rate is unavailable.',{exact:false}).waitFor();
 await page.getByText('Inspect original USD movements',{exact:true}).click();
 await page.getByRole('button',{name:/Inspect original USD movement/}).click();
 await page.getByText('Original USD evidence',{exact:true}).waitFor();
 const rationale=page.locator('.flow-inspector details.evidence');await rationale.locator('summary').click();
 assert.match(await rationale.innerText(),/Synthetic receipt/);
 assert.match(await rationale.innerText(),/reviewed/);
 assert.match(await rationale.innerText(),/fixture-source/);
 assert.match(await rationale.innerText(),/Page: 2/);
 assert.equal(await rationale.getByRole('link',{name:'Open source ↗'}).getAttribute('href'),'https://drive.google.com/file/synthetic');
 assert.equal(await page.locator('.flow-inspector time').textContent(),'2024-08-01');
 assert.equal((await page.locator('.flow-inspector .tx-amount').textContent()).replace(/\u00a0/g,' '),'USD 123.45');
 assert.equal(await page.locator('.flow-inspector .evidence-note').textContent(),'Synthetic link status');
 assert.equal(inspectorCurrency,'USD');
});

test('Back and refresh rebuild inspector selection from current stream identities',{timeout:30000},async t=>{
 const {page,url}=await fixture(t);await page.goto(url);
 await page.locator('#content .money-sankey .sankey-link').first().waitFor();
 await page.locator('.money-sankey-group [data-group-select]').first().click();
 await page.getByRole('button',{name:'Inspect Salary deposit'}).waitFor();
 await page.getByRole('tab',{name:'Cash flow',exact:true}).click();
 await page.getByRole('button',{name:'Back'}).click();
 await page.locator('.money-sankey').waitFor();
 await page.locator('.flow-inspector').waitFor();
 await page.getByRole('button',{name:'Inspect Salary deposit'}).waitFor();
 await page.getByRole('button',{name:'Refresh data'}).click();
 await page.locator('.flow-inspector').waitFor();
 await page.getByRole('button',{name:'Inspect Salary deposit'}).waitFor();
});

test('zero-cash investment evidence creates an owner from an empty API graph',{timeout:30000},async t=>{
 const {page,url,calls}=await fixture(t);
 await page.route('**/finance/flow?**',route=>{const q=new URL(route.request().url()).searchParams;return route.fulfill({json:{...common,currency:q.get('currency'),range:{start:q.get('start'),end:q.get('end')},nodes:[],edges:[],coverage:{complete:true}}});});
 await page.route('**/finance/investments?**',route=>{const q=new URL(route.request().url()).searchParams;const cashflows=q.get('currency')==='CAD'?[{event_id:'empty-graph-zero',account:'Fixture orphan investment',security_key:'ZERO_ONLY',classification:'buy',cash_amount_minor:0,currency:'CAD',date:'2026-08-03'},{event_id:'other-zero',account:'Other fixture investment',security_key:'OTHER_ZERO',classification:'buy',cash_amount_minor:0,currency:'CAD',date:'2026-08-03'}]:[];return route.fulfill({json:{...common,balances:[],history:[],holdings:[],cashflows,coverage:{cashflows:{complete:true}}}});});
 await page.route('**/finance/transactions?**',route=>{const query=Object.fromEntries(new URL(route.request().url()).searchParams);calls.push({name:'transactions',query});return route.fulfill({json:{...common,items:[{record_id:'investment:empty-graph-zero',entry_id:'empty-graph-zero',account:'Fixture orphan investment',date:'2026-08-03',description:'Zero-cash source evidence',category_id:'investment.trade.buy',amount_minor:0,currency:'CAD'}],total:1,limit:50,offset:0}});});
 await page.goto(url);await page.locator('.money-sankey').waitFor();await page.getByText('No recorded movements match these filters.',{exact:true}).waitFor();
 await page.locator('.money-sankey-controls [data-group-select]').filter({hasText:'Investments'}).click();
 const inspector=page.getByRole('complementary',{name:'Flow inspector',exact:true});
 await inspector.getByRole('button',{name:/Inspect Zero-cash buy · ZERO_ONLY/}).click();
 await inspector.getByText('Zero-cash source evidence',{exact:true}).waitFor();
 const query=calls.findLast(call=>call.name==='transactions').query;
 assert.equal(query.record_id,'investment:empty-graph-zero');assert.equal(query.account,'Fixture orphan investment');assert.equal(query.category,'investment.trade.buy');assert.equal(query.flow,'investment');assert.equal(query.dataset,'investment');assert.equal(query.scope,'personal');assert.equal(query.currency,'CAD');
 await inspector.getByRole('button',{name:'Close inspector',exact:true}).click();
 const explorer=page.locator('.money-sankey-explore');await explorer.locator('summary').click();await explorer.getByRole('tab',{name:'Accounts',exact:true}).click();
 await explorer.getByRole('button',{name:'Inspect account Fixture orphan investment',exact:true}).click();
 assert.equal(await inspector.count(),1,'an account with zero-cash evidence must open inspection');
 assert.equal(await inspector.getByRole('button',{name:/OTHER_ZERO/}).count(),0,'account selection excludes sibling account evidence');
 await inspector.getByRole('button',{name:/Inspect Zero-cash buy · ZERO_ONLY/}).click();await inspector.getByText('Zero-cash source evidence',{exact:true}).waitFor();
 assert.equal(calls.findLast(call=>call.name==='transactions').query.record_id,'investment:empty-graph-zero');
 assert.equal(await explorer.getByRole('button',{name:/^Inspect account /}).count(),2,'Accounts lists accounts, not their product groups');
 const refreshed=page.waitForResponse(response=>{const url=new URL(response.url());return url.pathname.endsWith('/finance/transactions')&&url.searchParams.get('record_id')==='investment:empty-graph-zero';});
 await page.getByRole('button',{name:'Refresh data',exact:true}).click();await refreshed;await inspector.getByText('Zero-cash source evidence',{exact:true}).waitFor();
 assert.equal(calls.findLast(call=>call.name==='transactions').query.account,'Fixture orphan investment');
});
test('same backend flow IDs retain native CAD and USD evidence across selection order and Back',{timeout:45000},async t=>{
 const {page,url,calls}=await fixture(t);
 await page.route('**/finance/flow?**',async route=>{
  const response=await route.fetch(),data=await response.json(),q=new URL(route.request().url()).searchParams,currency=q.get('currency');
  const edge={id:'shared-flow-id',source:'cash',target:'food',kind:'expense',label:currency+' exact movement',amount_minor:currency==='CAD'?1234:9876,count:1,currency,selector:{endpoint:'transactions',filters:{start:q.get('start'),end:q.get('end'),currency,scope:q.get('scope'),dataset:q.get('dataset'),account:'Fixture cash',category:'expense.food',category_mode:'exact',flow:'expense',direction:'outgoing'}}};
  data.edges=[edge];return route.fulfill({response,json:data});
 });
 await page.route('**/finance/transactions?**',route=>{
  const q=new URL(route.request().url()).searchParams;calls.push({name:'transactions',query:Object.fromEntries(q)});
  const currency=q.get('currency');
  return route.fulfill({json:{...common,items:[{record_id:currency+'-original',date:'2026-08-02',account:'Fixture cash',description:currency+' signed original',amount_minor:currency==='CAD'?-1234:-9876,currency}],total:1,limit:50,offset:0}});
 });
 await page.goto(url);await page.waitForFunction(()=>document.querySelector('#content')?.getAttribute('aria-busy')==='false');
 const originalUSD=page.locator('.foreign-unconverted').getByRole('button',{name:/Inspect original USD movement/});
 assert.equal(await page.locator('.foreign-unconverted').count(),1,await page.locator('#content').innerText());
 await page.locator('.foreign-unconverted summary').click();
 await originalUSD.click();await page.getByText('USD signed original',{exact:true}).waitFor();
 assert.equal(calls.findLast(call=>call.name==='transactions').query.currency,'USD');
 assert.equal((await page.locator('.flow-inspector .tx-amount').textContent()).replace(/\u00a0/g,' '),'-USD 98.76');
 await page.getByRole('button',{name:'Back'}).click();await page.waitForFunction(()=>document.querySelector('#content')?.getAttribute('aria-busy')==='false');
 const finder=page.locator('.money-sankey-stream-finder');await finder.locator('summary').click();await finder.getByLabel('Search exact movements').fill('CAD exact movement');
 await finder.getByRole('button',{name:/Inspect CAD exact movement/}).click();
 const inspector=page.getByRole('complementary',{name:'Flow inspector',exact:true});
 await inspector.getByRole('button',{name:/Inspect CAD exact movement/}).click();await inspector.getByText('CAD signed original',{exact:true}).waitFor();
 assert.equal(calls.findLast(call=>call.name==='transactions').query.currency,'CAD');
 assert.equal((await inspector.locator('.tx-amount').textContent()).replace(/\u00a0/g,' '),'-CAD 12.34');
 await page.keyboard.press('Escape');
 const disclosure=page.locator('.foreign-unconverted'),summary=disclosure.locator('summary');
 if(!await disclosure.evaluate(node=>node.open))await summary.click();
 await originalUSD.click();
 await page.getByText('USD signed original',{exact:true}).waitFor();
 assert.equal(calls.findLast(call=>call.name==='transactions').query.currency,'USD','a prior CAD selection must not replace the native USD stream');
 assert.equal((await page.locator('.flow-inspector .tx-amount').textContent()).replace(/\u00a0/g,' '),'-USD 98.76');
});
test('folding an upstream investment hierarchy reclaims only displaced account-group space',{timeout:45000},async t=>{
 const {page,url}=await fixture(t);
 const products=Array.from({length:8},(_,i)=>({event_id:'synthetic-product-'+i,account:'Fixture broker',security_key:'Synthetic '+i,classification:'buy',date:'2026-08-02',currency:'CAD',cash_amount_minor:-100}));
 await page.route('**/finance/flow?**',async route=>{
  const response=await route.fetch(),data=await response.json(),q=new URL(route.request().url()).searchParams,currency=q.get('currency');
  const nodes=[{id:'broker',label:'Fixture broker',account:'Fixture broker',kind:'account'},
   {id:'loan',label:'Fixture loan',account:'Fixture loan',kind:'account'},
   {id:'buys',label:'Investment purchases',kind:'investment'},
   {id:'interest',label:'Interest expense',kind:'expense'}];
  const edges=[{id:'investment-buy',source:'broker',target:'buys',kind:'investment',label:'Investment purchases',amount_minor:800,count:8,currency,selector:{endpoint:'transactions',filters:{start:q.get('start'),end:q.get('end'),currency,scope:q.get('scope'),dataset:'investment',account:'Fixture broker',category:'investment.trade.buy',category_mode:'exact',flow:'investment',direction:'outgoing'}}},
   {id:'loan-expense',source:'loan',target:'interest',kind:'expense',label:'Loan expense',amount_minor:500,count:1,currency,selector:{endpoint:'transactions',filters:{start:q.get('start'),end:q.get('end'),currency,scope:q.get('scope'),dataset:'all',account:'Fixture loan',category:'expense.interest',category_mode:'exact',flow:'expense',direction:'outgoing'}}}];
  data.nodes=nodes;data.edges=edges;return route.fulfill({response,json:data});
 });
 await page.route('**/finance/investments?**',route=>{
  const q=new URL(route.request().url()).searchParams;
  return route.fulfill({json:{...common,cashflows:q.get('currency')==='CAD'?products:[],holdings:[],coverage:{cashflows:{complete:true}}}});
 });
 await page.goto(url);await page.waitForFunction(()=>document.querySelector('.money-sankey')?.getAttribute('aria-busy')==='false');
 const roleBand=page.locator('.money-sankey-band[data-role="debt"]');
 const readGeometry=()=>roleBand.evaluate(node=>{const layer=document.querySelector('.money-sankey-layer');return {top:node.getBoundingClientRect().top-layer.getBoundingClientRect().top,height:layer.getBoundingClientRect().height};});
 const baseline=await readGeometry();
 await page.getByText('Explore accounts, spending and categories',{exact:true}).click();
 await page.getByRole('tab',{name:'All categories'}).click();
 await page.getByRole('button',{name:'Expand Products'}).click();
 await page.waitForFunction(minimumHeight=>{const band=document.querySelector('.money-sankey-band[data-role="debt"]');return band&&band.getBoundingClientRect().top>200&&document.querySelector('.money-sankey-layer').getBoundingClientRect().height>minimumHeight;},baseline.height);
 const expanded=await readGeometry();assert.ok(expanded.top>baseline.top);
 const loanGroup=page.locator('.money-sankey-controls .money-sankey-group').filter({hasText:'Loans'});
 await loanGroup.locator('[data-group-select]').click();await page.locator('.flow-inspector').waitFor();await page.keyboard.press('Escape');
 await loanGroup.getByRole('button',{name:/Fold Loans/}).click();
 await page.getByRole('button',{name:'Fold Products'}).click();
 await page.waitForFunction(()=>document.querySelector('.money-sankey')?.getAttribute('aria-busy')==='false');
 const folded=await readGeometry();
 assert.ok(Math.abs(folded.top-baseline.top)<2,`folding Products must restore the Loans heading: ${JSON.stringify({baseline,expanded,folded})}`);
 assert.ok(Math.abs(folded.height-baseline.height)<2,`the map height must return to baseline: ${JSON.stringify({baseline,expanded,folded})}`);
 assert.equal(await page.locator('.money-sankey-controls .money-sankey-group').filter({hasText:'Loans'}).locator('[data-group-select]').getAttribute('aria-pressed'),'true','the selected downstream group remains selected through reflow');
});
test('raw-member aggregates refresh current counts and close when only a same-ID foreign stream remains',{timeout:45000},async t=>{
 const {page,url,calls}=await fixture(t);let cadState='initial';
 await page.route('**/finance/flow?**',async route=>{
  const response=await route.fetch(),data=await response.json(),q=new URL(route.request().url()).searchParams,currency=q.get('currency');
  const present=currency==='USD'||cadState!=='removed';
  const amount=currency==='USD'?9876:cadState==='updated'?2468:1234;
  const count=currency==='USD'?1:cadState==='updated'?3:2;
  data.edges=present?[{id:'shared-flow-id',source:'cash',target:'food',kind:'expense',label:currency+(cadState==='updated'?' updated':'')+' exact movement',amount_minor:amount,count,currency,selector:{endpoint:'transactions',filters:{start:q.get('start'),end:q.get('end'),currency,scope:q.get('scope'),dataset:q.get('dataset'),account:'Fixture cash',category:'expense.food',category_mode:'exact',flow:'expense',direction:'outgoing'}}}]:[];
  return route.fulfill({response,json:data});
 });
 await page.route('**/finance/transactions?**',route=>{
  const q=new URL(route.request().url()).searchParams;calls.push({name:'transactions',query:Object.fromEntries(q)});
  const currency=q.get('currency');
  return route.fulfill({json:{...common,items:[{record_id:currency+'-original',date:'2026-08-02',account:'Fixture cash',description:currency+' signed original',amount_minor:currency==='CAD'?-2468:-9876,currency}],total:1,limit:50,offset:0}});
 });
 await page.goto(url);await page.waitForFunction(()=>document.querySelector('#content')?.getAttribute('aria-busy')==='false');
 const finder=page.locator('.money-sankey-stream-finder');await finder.locator('summary').click();await finder.getByLabel('Search exact movements').fill('CAD exact movement');
 await finder.getByRole('button',{name:/Inspect CAD exact movement/}).click();
 let inspector=page.getByRole('complementary',{name:'Flow inspector',exact:true});
 await inspector.getByText(/exact streams/).waitFor();
 await inspector.getByText(/2 source items/).waitFor();
 assert.match((await inspector.locator('.inspector-amount').first().textContent()).replace(/\u00a0/g,' '),/CAD 12\.34/);
 cadState='updated';await page.getByRole('button',{name:'Refresh data'}).click();
 inspector=page.getByRole('complementary',{name:'Flow inspector',exact:true});await inspector.getByText(/exact streams/).waitFor();
 await inspector.getByText(/3 source items/).waitFor();
 assert.match((await inspector.locator('.inspector-amount').first().textContent()).replace(/\u00a0/g,' '),/CAD 24\.68/);
 assert.match(await inspector.textContent(),/CAD updated exact movement/);
 cadState='removed';await page.getByRole('button',{name:'Refresh data'}).click();
 await page.waitForFunction(()=>document.querySelector('#content')?.getAttribute('aria-busy')==='false');
 assert.equal(await page.locator('.flow-inspector').count(),0,'removed CAD selection must close instead of restoring its saved member');
 const foreign=page.locator('.foreign-unconverted');await foreign.locator('summary').click();
 await foreign.getByRole('button',{name:/Inspect original USD movement/}).click();
 await page.getByText('USD signed original',{exact:true}).waitFor();
 assert.equal(calls.findLast(call=>call.name==='transactions').query.currency,'USD');
 assert.match((await page.locator('.flow-inspector .tx-amount').textContent()).replace(/\u00a0/g,' '),/-USD 98\.76/);
 await page.keyboard.press('Escape');cadState='initial';await page.getByRole('button',{name:'Refresh data'}).click();
 await page.waitForFunction(()=>document.querySelector('#content')?.getAttribute('aria-busy')==='false');
 await finder.getByLabel('Search exact movements').fill('CAD exact movement');await finder.getByRole('button',{name:/Inspect CAD exact movement/}).click();
 cadState='removed';await page.getByRole('tab',{name:'Cash flow',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('#content')?.getAttribute('aria-busy')==='false');
 await page.getByRole('button',{name:'Back'}).click();await page.waitForFunction(()=>document.querySelector('#content')?.getAttribute('aria-busy')==='false');
 assert.equal(await page.locator('.flow-inspector').count(),0,'Back must not restore a removed CAD member from a saved aggregate');
});
