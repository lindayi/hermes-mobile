import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const root=process.env.HERMES_FRONTEND_DIR||fileURLToPath(new URL('../../frontend/',import.meta.url));
const common={meta:{generated_at:'2024-01-02T12:00:00Z',source:'sqlite',warnings:[]}};
// Public synthetic hierarchical rows: totals and pages below are derived, never padded.
const debit={entry_id:'bank:synthetic-debit:transfer',record_id:'bank:synthetic-debit',tx_id:'synthetic-debit',dataset:'bank',date:'2023-12-31',account:'Synthetic cash',currency:'CAD',flow:'transfer',category_id:'transfer.own',amount_minor:-50000,description:'Synthetic year-end debit',allocation:'personal',current_planning_protected:0};
const credit={entry_id:'bank:synthetic-credit:transfer',record_id:'bank:synthetic-credit',tx_id:'synthetic-credit',dataset:'bank',date:'2024-01-01',account:'Synthetic protected',currency:'CAD',flow:'transfer',category_id:'transfer.own',amount_minor:50000,description:'Synthetic new-year credit',allocation:'mother',current_planning_protected:1};
const transactionRows=[debit,credit];
function selected(rows,q){return rows.filter(r=>(!q.start||r.date>=q.start)&&(!q.end||r.date<=q.end)&&(!q.currency||r.currency===q.currency)&&(!q.account||r.account===q.account)&&(q.scope==='all'||r.allocation==='personal')&&(!q.dataset||q.dataset==='all'||r.dataset===q.dataset)&&(!q.category||(q.category_mode==='exact'?r.category_id===q.category:r.category_id.startsWith(q.category)))&&(!q.flow||r.flow===q.flow)&&(!q.record_id||[r.record_id,r.tx_id,r.event_id].includes(q.record_id))&&(!q.q||[r.description,r.category_id].join(' ').includes(q.q)));}
async function fixture(t,{rows=transactionRows,responses={}}={}){
 const calls=[];
 const server=createServer(async(req,res)=>{
  const url=new URL(req.url,'http://synthetic');
  if(url.pathname==='/hermes/app-api/auth/me'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({user:{id:'synthetic-owner',role:'owner',profile:'default',status:'ready'}}));return;}
  if(url.pathname.startsWith('/hermes/app-api/finance/')){
   const name=url.pathname.split('/').pop(),q=Object.fromEntries(url.searchParams);calls.push({name,query:q});
   res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');
   let data;
   if(responses[name])data=responses[name](q);
   else if(name==='meta')data={range:{start:'2023-01-01',end:'2024-01-02'},accounts:[{account:'Synthetic cash'},{account:'Synthetic protected'}],categories:[],currencies:['CAD','USD']};
   else if(name==='transactions'){const matched=selected(rows,q);data={items:matched.slice(Number(q.offset||0),Number(q.offset||0)+Number(q.limit||25)),total:matched.length,limit:Number(q.limit||25),offset:Number(q.offset||0)};}
   else if(name==='transfers')data={items:[{link_id:'synthetic-link',link_type:'own_cash',debit,credit,protected_boundary:true,evidence:'Synthetic transfer across a year boundary'}],total:1,limit:25,offset:0,network:{links:[]}};
   else data={};
   res.end(JSON.stringify({...common,...data}));return;
  }
  try{const rel=url.pathname.replace('/hermes/','').replace(/\/$/,'/index.html');if(rel.includes('..'))throw Error();const body=await readFile(root+'/'+rel);res.setHeader('Content-Type',rel.endsWith('.html')?'text/html':rel.endsWith('.css')?'text/css':'text/javascript');res.end(body);}catch{res.writeHead(404);res.end();}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',args:['--no-sandbox','--disable-dev-shm-usage']});t.after(()=>browser.close());
 const page=await browser.newPage({viewport:{width:1280,height:900},serviceWorkers:'block'});page.setDefaultTimeout(5000);
 return {page,calls,url:`http://127.0.0.1:${server.address().port}/hermes/finance/`};
}
async function ready(page){await page.waitForFunction(()=>document.querySelector('#content')?.getAttribute('aria-busy')==='false');for(const selector of ['[data-secondary]','.filter-options']){const d=page.locator(selector);if(await d.count()&&!await d.evaluate(n=>n.open))await d.locator(':scope > summary').click();}}
const categoryName=id=>{const text=id.split('.').slice(1).join(' ');return text.charAt(0).toUpperCase()+text.slice(1);};
async function formValues(page){return page.locator('#filters').evaluate(f=>Object.fromEntries(new FormData(f)));}
function last(calls,name){return calls.filter(c=>c.name===name).at(-1).query;}
const categoryRows=[
 {...debit,record_id:'bank:synthetic-food',date:'2024-01-01',flow:'expense',category_id:'expense.food',amount_minor:-1000,expense_minor:1000,income_minor:0},
 {...debit,record_id:'bank:synthetic-adjustment',date:'2024-01-01',flow:'adjustment',category_id:'expense.food',amount_minor:200,expense_minor:0,income_minor:0},
 {...debit,record_id:'bank:synthetic-nested',date:'2024-01-01',flow:'expense',category_id:'expense.food.cafe',amount_minor:-300,expense_minor:300,income_minor:0},
 {...debit,record_id:'bank:synthetic-refund',date:'2024-01-01',flow:'expense',category_id:'expense.refund',amount_minor:100,expense_minor:-100,income_minor:0},
 {...debit,record_id:'bank:synthetic-income',date:'2024-01-01',flow:'income',category_id:'expense.income',amount_minor:400,expense_minor:0,income_minor:400},
];
function categoryOverview(q){
 const groups=new Map();for(const row of selected(categoryRows,q)){const key=row.category_id+'|'+row.flow;const group=groups.get(key)||{category_id:row.category_id,flow:row.flow,amount_minor:0,expense_minor:0,income_minor:0,count:0};for(const field of ['amount_minor','expense_minor','income_minor'])group[field]+=row[field];group.count++;groups.set(key,group);}return {categories:[...groups.values()]};
}

for(const holdings of [[],[{security_key:'SYNTHETIC_PLAN',quantity:'10',quantity_basis:'plan units',currency:'CAD'}]])test(`quote coverage replaces loading without removing caveats for ${holdings.length?'unsupported':'empty'} holdings`,{timeout:20000},async t=>{
 const {page,calls,url}=await fixture(t,{responses:{investments:()=>({holdings})}});await page.goto(url);await ready(page);
 await page.getByRole('tab',{name:'Investments',exact:true}).click();await ready(page);
 await page.getByText('No supported share symbols supplied by the ledger. Market estimates unavailable.',{exact:true}).waitFor();
 assert.equal(await page.getByText('Loading public quote coverage…',{exact:true}).count(),0);
 await page.getByText('Estimates stay separate from ledger values; unsupported units are excluded.',{exact:true}).waitFor();
 assert.equal(calls.filter(c=>c.name==='quotes').length,0);
});

test('clicked transfer counterpart replaces source-date, account and scope predicates visibly',{timeout:20000},async t=>{
 const {page,calls,url}=await fixture(t);await page.goto(url);await ready(page);
 await page.getByLabel('From',{exact:true}).fill('2023-12-31');await page.getByLabel('Through',{exact:true}).fill('2023-12-31');await page.locator('#filters').getByLabel('Account').selectOption('Synthetic cash');await page.getByLabel('Dataset').selectOption('bank');await page.getByRole('button',{name:'Apply filters'}).click();await ready(page);
 await page.getByRole('tab',{name:'Transfers',exact:true}).click();await ready(page);
 await page.getByLabel('Search transfers').fill('year boundary');await page.getByRole('button',{name:'Search',exact:true}).click();await ready(page);
 await page.getByText('Transfer evidence · table alternative',{exact:true}).click();
 await page.getByRole('button',{name:'Synthetic protected',exact:true}).click();await ready(page);
 assert.deepEqual(last(calls,'transactions'),{start:'2024-01-01',end:'2024-01-01',currency:'CAD',scope:'all',account:'Synthetic protected',dataset:'all',record_id:credit.record_id,offset:'0',limit:'25'});
 assert.deepEqual(await formValues(page),{start:'2024-01-01',end:'2024-01-01',currency:'CAD',scope:'all',account:'Synthetic protected',dataset:'all'});
 assert.equal(await page.getByLabel('Search transactions').inputValue(),'');
 const table=page.getByRole('table',{name:'Transactions',exact:true});assert.equal(await table.locator('tbody tr').count(),1);await table.getByText(credit.description,{exact:true}).waitFor();await table.getByText('CAD 500.00',{exact:true}).waitFor();assert.equal(await page.locator('[data-page-status]').textContent(),'1–1 of 1 records');assert.equal(await page.getByText(debit.description,{exact:true}).count(),0);
});

test('network account nodes keep paired transfers, source dates and scope when filtering either endpoint',{timeout:20000},async t=>{
 const pairs=[{link_id:'synthetic-link',link_type:'own_cash',debit,credit,protected_boundary:true},{link_id:'synthetic-unrelated',link_type:'own_cash',debit:{...debit,account:'Synthetic other'},credit:{...credit,account:'Synthetic other'},protected_boundary:false}];
 const transfers=q=>{
  const items=pairs.filter(p=>(!q.start||p.debit.date>=q.start)&&(!q.end||p.debit.date<=q.end)&&[p.debit,p.credit].some(r=>r.currency===q.currency)&&[p.debit,p.credit].some(r=>q.scope==='all'||r.allocation==='personal')&&(!q.account||[p.debit,p.credit].some(r=>r.account===q.account)));
  return {items,total:items.length,limit:25,offset:Number(q.offset||0),network:{links:items.map(p=>({source:p.debit.account,target:p.credit.account,amount_minor:Math.abs(p.debit.amount_minor),currency:p.debit.currency,count:1}))}};
 };
 const {page,calls,url}=await fixture(t,{responses:{transfers}});await page.goto(url);await ready(page);
 await page.getByLabel('From',{exact:true}).fill(debit.date);await page.getByLabel('Through',{exact:true}).fill(debit.date);await page.getByLabel('Dataset').selectOption('bank');await page.getByRole('button',{name:'Apply filters'}).click();await ready(page);
 await page.getByRole('tab',{name:'Transfers',exact:true}).click();await ready(page);
 await page.getByText('Transfer evidence · table alternative',{exact:true}).click();assert.equal(await page.getByRole('table',{name:'Transfer evidence',exact:true}).locator('tbody tr').count(),2);
 for(const account of [credit.account,debit.account]){
  await page.getByRole('button',{name:'Inspect account '+account,exact:true}).click();await ready(page);
  assert.equal(await page.getByRole('tab',{name:'Transfers',exact:true}).getAttribute('aria-selected'),'true');
  assert.equal(calls.filter(c=>c.name==='transactions').length,0);
  assert.deepEqual(last(calls,'transfers'),{start:debit.date,end:debit.date,currency:'CAD',scope:'personal',dataset:'bank',account,offset:'0',limit:'25'});
  assert.deepEqual(await formValues(page),{start:debit.date,end:debit.date,currency:'CAD',scope:'personal',dataset:'bank',account});
  await page.getByText('Transfer evidence · table alternative',{exact:true}).click();const table=page.getByRole('table',{name:'Transfer evidence',exact:true});assert.equal(await table.locator('tbody tr').count(),1);
  await table.getByText(debit.date,{exact:true}).waitFor();await table.getByText(credit.date,{exact:true}).waitFor();await table.getByRole('button',{name:credit.account,exact:true}).waitFor();
  assert.equal(await page.locator('[data-page-status]').textContent(),'1–1 of 1 records');
 }
});

test('category bands and table drill into exact authoritative flows with signed amount and count parity',{timeout:30000},async t=>{
 const {page,calls,url}=await fixture(t,{rows:categoryRows,responses:{overview:categoryOverview}});await page.goto(url);await ready(page);
 await page.getByLabel('From').fill('2024-01-01');await page.getByLabel('Through').fill('2024-01-02');await page.getByRole('button',{name:'Apply filters'}).click();await ready(page);
 const cases=[['expense.food','expense','CAD 10.00','-CAD 10.00'],['expense.food','adjustment','CAD 2.00','CAD 2.00'],['expense.refund','expense','-CAD 1.00','CAD 1.00'],['expense.income','income','CAD 4.00','CAD 4.00']];
 for(const mode of ['band','table'])for(const [category,flow,aggregate,source] of cases){
  await page.getByRole('tab',{name:'Overview',exact:true}).click();await ready(page);
  await page.getByText('Cash-flow categories · table alternative',{exact:true}).click();
  const rows=page.getByRole('table',{name:'Cash-flow categories',exact:true}).locator('tbody tr');
  const row=rows.filter({has:page.getByRole('button',{name:'Inspect '+categoryName(category),exact:true})}).filter({has:page.getByRole('cell',{name:flow,exact:true})});
  // Same category/direction may occur in different flow classes: expose the authoritative flow.
  assert.equal(await row.count(),1);await row.getByText(aggregate,{exact:true}).waitFor();assert.equal(await row.locator('td').last().textContent(),'1');
  if(mode==='table')await row.getByRole('button').click();
  else {const matching=categoryOverview(last(calls,'overview')).categories.filter(r=>r.category_id===category);const index=matching.findIndex(r=>r.flow===flow);await page.locator('[data-chart="alluvial"]').getByRole('button',{name:'Inspect '+categoryName(category),exact:true}).nth(index).click();}
  await ready(page);const q=last(calls,'transactions');assert.equal(q.category,category);assert.equal(q.category_mode,'exact');assert.equal(q.flow,flow);assert.equal(q.start,'2024-01-01');assert.equal(q.end,'2024-01-02');assert.equal(q.scope,'personal');
  assert.match(await page.locator('.filter-chip').textContent(),new RegExp(category.replaceAll('.','\\.')+'.*exact.*'+flow));
  const ledger=page.getByRole('table',{name:'Transactions',exact:true});assert.equal(await ledger.locator('tbody tr').count(),1);await ledger.getByText(source,{exact:true}).waitFor();assert.equal(await page.locator('[data-page-status]').textContent(),'1–1 of 1 records');
 }
 await page.getByRole('button',{name:'Clear drilldown'}).click();await ready(page);for(const key of ['category','category_mode','flow'])assert.equal(last(calls,'transactions')[key],undefined);assert.equal(await page.getByRole('table',{name:'Transactions',exact:true}).locator('tbody tr').count(),categoryRows.length);
 await page.getByRole('tab',{name:'Overview',exact:true}).click();await ready(page);await page.locator('[data-chart="alluvial"]').getByRole('button',{name:'Inspect Food',exact:true}).first().click();await ready(page);
 await page.getByRole('tab',{name:'Transactions & evidence',exact:true}).click();await ready(page);for(const key of ['category','category_mode','flow'])assert.equal(last(calls,'transactions')[key],undefined);
});

test('payroll drill uses the actual linked record posted date and clears unrelated ledger filters',{timeout:20000},async t=>{
 const bank={...credit,record_id:'bank:synthetic-paynet',tx_id:'synthetic-paynet',date:'2024-01-02',flow:'income',category_id:'income.payroll',amount_minor:350000,description:'Synthetic payroll bank net'};
 const payroll={payroll_id:'synthetic-payroll',pay_date:'2024-01-01',currency:bank.currency,gross_minor:500000,net_minor:350000};
 const links=[{payroll_id:payroll.payroll_id,code:'NET',tx_id:bank.tx_id,status:'matched',linked_record:{record_id:bank.record_id,date:bank.date,account:bank.account,currency:bank.currency,dataset:bank.dataset}},{payroll_id:payroll.payroll_id,code:'SAVINGS',event_id:'synthetic-event',status:'recorded',linked_record:null},{payroll_id:payroll.payroll_id,code:'MISSING',tx_id:'synthetic-missing',linked_record:null},{payroll_id:payroll.payroll_id,code:'LEGACY',tx_id:'synthetic-legacy'}];
 const {page,calls,url}=await fixture(t,{rows:[bank],responses:{compensation:()=>({payroll:[payroll],links})}});
 await page.goto(url);await ready(page);await page.locator('#filters').getByLabel('Account').selectOption('Synthetic cash');await page.getByLabel('Dataset').selectOption('investment');await page.getByRole('button',{name:'Apply filters'}).click();await ready(page);
 await page.getByRole('tab',{name:'Compensation',exact:true}).click();await ready(page);
 await page.getByText('Payroll links · table alternative',{exact:true}).click();
 const linkTable=page.getByRole('table',{name:'Payroll links'});await linkTable.getByText('synthetic-event',{exact:true}).waitFor();await linkTable.getByText('synthetic-missing',{exact:true}).waitFor();await linkTable.getByText('synthetic-legacy',{exact:true}).waitFor();assert.equal(await linkTable.getByRole('button').count(),1);
 await linkTable.getByRole('button',{name:'Inspect linked entry'}).click();await ready(page);
 assert.deepEqual(last(calls,'transactions'),{start:bank.date,end:bank.date,currency:bank.currency,scope:'all',account:bank.account,dataset:'all',record_id:bank.record_id,offset:'0',limit:'25'});
 assert.deepEqual(await formValues(page),{start:bank.date,end:bank.date,currency:bank.currency,scope:'all',dataset:'all',account:bank.account});
 const ledger=page.getByRole('table',{name:'Transactions',exact:true});assert.equal(await ledger.locator('tbody tr').count(),1);await ledger.getByText(bank.description,{exact:true}).waitFor();await ledger.getByText('CAD 3,500.00',{exact:true}).waitFor();assert.equal(await page.locator('[data-page-status]').textContent(),'1–1 of 1 records');
});

test('incomplete selected payroll component coverage hides waterfall without hiding net or linked evidence',{timeout:20000},async t=>{
 const bank={...debit,record_id:'bank:synthetic-net',tx_id:'synthetic-net',date:'2024-01-02',flow:'income',category_id:'income.payroll',amount_minor:350000,description:'Synthetic selected payroll net'};
 const pay={payroll_id:'z-newest',pay_date:'2024-01-01',currency:'CAD',gross_minor:500000,net_minor:350000};
 let complete=false;
 const compensation=()=>({payroll:[pay,{...pay,payroll_id:'a-older',pay_date:'2023-12-01',net_minor:100}],components:complete?[{payroll_id:pay.payroll_id,code:'TAX',kind:'deduction',amount_minor:150000,cash_effect_minor:-150000,ytd_minor:300000}]:[],links:[{payroll_id:pay.payroll_id,code:'NET',tx_id:bank.tx_id,status:'matched',linked_record:{record_id:bank.record_id,date:bank.date,account:bank.account,currency:bank.currency,dataset:bank.dataset}}],awards:[{award_id:'synthetic-grant-reference',employer:'Synthetic employer',grant_currency:'USD',grant_value_minor:100000}],coverage:{selected_payroll_id:pay.payroll_id,components:complete?{total:1,returned:1,omitted:0,complete:true}:{total:1001,returned:0,omitted:1001,complete:false}}});
 const {page,calls,url}=await fixture(t,{rows:[bank],responses:{compensation}});await page.goto(url);await ready(page);
 await page.getByRole('tab',{name:'Compensation',exact:true}).click();await ready(page);
 assert.equal(await page.locator('[data-chart="waterfall"]').count(),0);
 await page.getByText('Payroll breakdown unavailable: component coverage is incomplete.',{exact:true}).waitFor();
 await page.getByText('All selected payslips · table alternative',{exact:true}).click();
 const sample=page.getByRole('table',{name:'All selected payslips',exact:true});await sample.getByRole('button',{name:'Inspect '+pay.pay_date,exact:true}).waitFor();await sample.getByText('CAD 3,500.00',{exact:true}).waitFor();
 await page.getByText('Grant reference values · table alternative',{exact:true}).click();await page.getByRole('table',{name:'Grant reference values',exact:true}).getByText('USD 1,000.00',{exact:true}).waitFor();
 await page.getByText('Payroll links · table alternative',{exact:true}).click();
 await page.getByRole('table',{name:'Payroll links',exact:true}).getByRole('button',{name:'Inspect linked entry'}).click();await ready(page);
 assert.equal(last(calls,'transactions').record_id,bank.record_id);assert.equal(last(calls,'transactions').start,bank.date);
 await page.getByRole('table',{name:'Transactions',exact:true}).getByText(bank.description,{exact:true}).waitFor();
 complete=true;await page.getByRole('tab',{name:'Compensation',exact:true}).click();await ready(page);
 assert.equal(await page.locator('[data-chart="waterfall"]').count(),1);
 assert.equal(await page.getByText('Payroll breakdown unavailable: component coverage is incomplete. Bank net and recorded evidence remain available.',{exact:true}).count(),0);
 await page.getByText('Selected payslip components · table alternative',{exact:true}).click();const components=page.getByRole('table',{name:'Selected payslip components',exact:true}),tax=components.locator('tbody tr').filter({hasText:'TAX'});await tax.getByText('TAX',{exact:true}).waitFor();assert.equal((await tax.locator('td').nth(2).textContent()).trim(),'CAD 1,500.00');assert.equal((await tax.locator('td').nth(3).textContent()).trim(),'-CAD 1,500.00');assert.equal((await tax.locator('td').nth(4).textContent()).trim(),'CAD 3,000.00');
});

test('alluvial keeps skewed category bands, nodes and labels inside its viewBox with a twelve-category bound',{timeout:20000},async t=>{
 const categories=Array.from({length:13},(_,i)=>({category_id:`expense.item${i}`,flow:'expense',amount_minor:-(i===0?1000000:i===12?1:100),count:1}));
 const {page,url}=await fixture(t,{responses:{overview:()=>({categories})}});await page.goto(url);await ready(page);
 const chart=page.locator('[data-chart="alluvial"]');assert.equal(await chart.locator('path').count(),12);
 const overflow=await chart.evaluate(s=>{const v=s.viewBox.baseVal;return [...s.querySelectorAll('rect,path,text')].flatMap(n=>{const b=n.getBBox();return b.x<v.x||b.y<v.y||b.x+b.width>v.x+v.width||b.y+b.height>v.y+v.height?[{tag:n.tagName,label:n.textContent,bounds:{x:b.x,y:b.y,width:b.width,height:b.height},viewBox:s.getAttribute('viewBox')}]:[];});});
 assert.deepEqual(overflow,[]);
 await page.getByText('Diagram shows the 12 largest categories by absolute materiality; the full signed table below retains every returned category.',{exact:true}).waitFor();
 await page.getByText('Cash-flow categories · table alternative',{exact:true}).click();assert.equal(await page.getByRole('table',{name:'Cash-flow categories',exact:true}).locator('tbody tr').count(),13);
});

test('unknown and unsupported source currencies never inherit the selected CAD label',{timeout:20000},async t=>{
 const currencies=[undefined,null,'unknown','EUR','CAD','USD'];
 const awards=currencies.map((grant_currency,i)=>({award_id:`synthetic-currency-${i}`,employer:'Synthetic employer',grant_currency,grant_value_minor:100000,granted_shares:10,status:'scheduled'}));
 const holdings=currencies.map((currency,i)=>({account:'Synthetic cash',name:`Synthetic holding ${i}`,currency,market_value_minor:100000}));
 const {page,url}=await fixture(t,{responses:{compensation:()=>({awards}),investments:()=>({holdings})}});await page.goto(url);await ready(page);
 await page.getByRole('tab',{name:'Compensation',exact:true}).click();await ready(page);
 await page.getByText('Grant reference values · table alternative',{exact:true}).click();const grants=page.getByRole('table',{name:'Grant reference values',exact:true});
 for(let i=0;i<4;i++)assert.equal(await grants.locator('tbody tr').nth(i).locator('td').nth(4).textContent(),'Unavailable');
 await grants.locator('tbody tr').nth(4).getByText('CAD 1,000.00',{exact:true}).waitFor();await grants.locator('tbody tr').nth(5).getByText('USD 1,000.00',{exact:true}).waitFor();
 await page.getByRole('tab',{name:'Investments',exact:true}).click();await ready(page);
 await page.getByText('Recorded holdings · table alternative',{exact:true}).click();const rows=page.getByRole('table',{name:'Recorded holdings',exact:true}).locator('tbody tr');
 for(let i=0;i<4;i++)assert.equal(await rows.nth(i).locator('td').nth(5).textContent(),'Unavailable');
 await rows.nth(4).getByText('CAD 1,000.00',{exact:true}).waitFor();await rows.nth(5).getByText('USD 1,000.00',{exact:true}).waitFor();
});

test('overview treats family allocation assets as restricted even with an unprotected flag',{timeout:20000},async t=>{
 const overview={balances:[{account:'Synthetic investments',currency:'CAD',allocation:'family',planning_protected:0,market_value_minor:10000}],bank_balances:[{account:'Synthetic protected',currency:'CAD',allocation:'mother_reserved',planning_protected:0,amount_minor:3000},{account:'Synthetic cash',currency:'CAD',allocation:'personal',planning_protected:0,amount_minor:5000}],liabilities:[{account:'Synthetic debt',currency:'CAD',amount_minor:4000}]};
 const {page,url}=await fixture(t,{responses:{overview:()=>overview}});await page.goto(url);await ready(page);
 await page.getByLabel('Scope').selectOption('all');await page.getByRole('button',{name:'Apply filters'}).click();await ready(page);
 await page.locator('[data-position="liquid"]').getByText('CAD 50.00',{exact:true}).waitFor();
 await page.locator('[data-position="restricted"]').getByText('CAD 130.00',{exact:true}).waitFor();
 await page.locator('[data-position="net"]').getByText('CAD 140.00',{exact:true}).waitFor();
});

test('vesting policy defaults to full horizon and explicitly selects vest dates without losing grant context',{timeout:20000},async t=>{
 const awards=[{award_id:'synthetic-old-grant',award_date:'2022-04-01',employer:'Synthetic employer',grant_currency:'USD',grant_value_minor:100000,granted_shares:10,status:'scheduled'}];
 const vesting=['2023-01-01','2024-01-02','2025-01-01'].map(vest_date=>({award_id:awards[0].award_id,vest_date,percent_basis_points:2500,theoretical_shares:2.5,actual_vested_shares:null,status:'scheduled'}));
 const {page,calls,url}=await fixture(t,{responses:{compensation:q=>({awards,vesting:q.vesting_policy==='range'?vesting.filter(v=>(!q.start||v.vest_date>=q.start)&&(!q.end||v.vest_date<=q.end)):vesting})}});
 await page.goto(url);await ready(page);await page.getByLabel('From',{exact:true}).fill('2024-01-01');await page.getByLabel('Through',{exact:true}).fill('2024-01-02');await page.getByRole('button',{name:'Apply filters',exact:true}).click();await ready(page);await page.getByRole('tab',{name:'Compensation',exact:true}).click();await ready(page);
 assert.equal(last(calls,'compensation').vesting_policy,'horizon');assert.equal(last(calls,'compensation').start,'2024-01-01');assert.equal(last(calls,'compensation').end,'2024-01-02');assert.equal(await page.getByLabel('Vesting date policy').inputValue(),'horizon');
 const openTables=async()=>{await page.getByText('Vesting schedule · table alternative',{exact:true}).click();await page.getByText('Grant reference values · table alternative',{exact:true}).click();};
 await openTables();
 const schedule=page.getByRole('table',{name:'Vesting schedule',exact:true});assert.equal(await schedule.locator('tbody tr').count(),vesting.length);await schedule.getByText('2025-01-01',{exact:true}).waitFor();await schedule.getByText('2023-01-01',{exact:true}).waitFor();
 assert.equal(await page.getByLabel('From').inputValue(),'2024-01-01');assert.equal(await page.getByLabel('Through').inputValue(),'2024-01-02');
 const grants=page.getByRole('table',{name:'Grant reference values',exact:true});await grants.getByText('2022-04-01',{exact:true}).waitFor();await grants.getByText('USD 1,000.00',{exact:true}).waitFor();assert.equal(await page.getByLabel('Currency').inputValue(),'CAD');
 await page.getByLabel('Vesting date policy').selectOption('range');await ready(page);assert.equal(last(calls,'compensation').vesting_policy,'range');assert.equal(last(calls,'compensation').start,'2024-01-01');assert.equal(last(calls,'compensation').end,'2024-01-02');await openTables();
 assert.equal(await schedule.locator('tbody tr').count(),1);await schedule.getByText('2024-01-02',{exact:true}).waitFor();await grants.getByText('2022-04-01',{exact:true}).waitFor();await grants.getByText('USD 1,000.00',{exact:true}).waitFor();assert.equal(await schedule.locator('tbody tr td').nth(4).textContent(),'Unavailable');await schedule.getByText('scheduled',{exact:true}).waitFor();
 await page.getByLabel('Vesting date policy').selectOption('horizon');await ready(page);await openTables();assert.equal(await schedule.locator('tbody tr').count(),vesting.length);assert.equal(await schedule.getByText('scheduled',{exact:true}).count(),vesting.length);
 await page.getByRole('tab',{name:'Overview',exact:true}).click();await ready(page);assert.equal(last(calls,'overview').vesting_policy,undefined);
});
