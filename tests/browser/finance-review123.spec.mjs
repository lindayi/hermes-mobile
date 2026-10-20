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
async function ready(page){await page.waitForFunction(()=>document.querySelector('#content')?.getAttribute('aria-busy')==='false');}
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
 const groups=new Map();for(const row of selected(categoryRows,q)){const key=row.category_id+'|'+row.flow;const group=groups.get(key)||{category_id:row.category_id,flow:row.flow,currency:row.currency,amount_minor:0,expense_minor:0,income_minor:0,count:0};for(const field of ['amount_minor','expense_minor','income_minor'])group[field]+=row[field];group.count++;groups.set(key,group);}return {categories:[...groups.values()]};
}

test('clicked transfer counterpart replaces source-date, account and scope predicates visibly',{timeout:20000},async t=>{
 const {page,calls,url}=await fixture(t);await page.goto(url);await ready(page);
 await page.getByLabel('From',{exact:true}).fill('2023-12-31');await page.getByLabel('Through',{exact:true}).fill('2023-12-31');await page.getByLabel('Account').selectOption('Synthetic cash');await page.getByLabel('Dataset').selectOption('bank');await page.getByRole('button',{name:'Apply filters'}).click();await ready(page);
 await page.getByRole('tab',{name:'Transfers',exact:true}).click();await ready(page);
 await page.getByLabel('Search transfers').fill('year boundary');await page.getByRole('button',{name:'Search',exact:true}).click();await ready(page);
 await page.getByRole('button',{name:'Synthetic protected',exact:true}).click();await ready(page);
 assert.deepEqual(last(calls,'transactions'),{start:'2024-01-01',end:'2024-01-01',currency:'CAD',scope:'all',account:'Synthetic protected',dataset:'all',record_id:credit.record_id,offset:'0',limit:'25'});
 assert.deepEqual(await formValues(page),{start:'2024-01-01',end:'2024-01-01',currency:'CAD',scope:'all',account:'Synthetic protected',dataset:'all'});
 assert.equal(await page.getByLabel('Search transactions').inputValue(),'');
 const table=page.getByRole('table',{name:'Transactions',exact:true});assert.equal(await table.locator('tbody tr').count(),1);await table.getByText(credit.description,{exact:true}).waitFor();await table.getByText('CAD 500.00',{exact:true}).waitFor();assert.equal(await page.locator('[data-page-status]').textContent(),'1–1 of 1 records');assert.equal(await page.getByText(debit.description,{exact:true}).count(),0);
});

test('category bands and table drill into exact authoritative flows with signed amount and count parity',{timeout:30000},async t=>{
 const {page,calls,url}=await fixture(t,{rows:categoryRows,responses:{overview:categoryOverview}});await page.goto(url);await ready(page);
 const cases=[['expense.food','expense','CAD 10.00','-CAD 10.00'],['expense.food','adjustment','CAD 2.00','CAD 2.00'],['expense.refund','expense','-CAD 1.00','CAD 1.00'],['expense.income','income','CAD 4.00','CAD 4.00']];
 for(const mode of ['band','table'])for(const [category,flow,aggregate,source] of cases){
  await page.getByRole('tab',{name:'Overview',exact:true}).click();await ready(page);
  await page.getByText('Cash-flow categories · table alternative',{exact:true}).click();
  const rows=page.getByRole('table',{name:'Cash-flow categories',exact:true}).locator('tbody tr');
  const row=rows.filter({has:page.getByRole('button',{name:'Inspect '+category,exact:true})}).filter({has:page.getByRole('cell',{name:flow,exact:true})});
  // Same category/direction may occur in different flow classes: expose the authoritative flow.
  assert.equal(await row.count(),1);await row.getByText(aggregate,{exact:true}).waitFor();assert.equal(await row.locator('td').last().textContent(),'1');
  if(mode==='table')await row.getByRole('button').click();
  else {const matching=categoryOverview(last(calls,'overview')).categories.filter(r=>r.category_id===category);const index=matching.findIndex(r=>r.flow===flow);await page.locator('[data-chart="alluvial"]').getByRole('button',{name:'Inspect '+category,exact:true}).nth(index).click();}
  await ready(page);const q=last(calls,'transactions');assert.equal(q.category,category);assert.equal(q.category_mode,'exact');assert.equal(q.flow,flow);assert.equal(q.start,'2024-01-01');assert.equal(q.end,'2024-01-02');assert.equal(q.scope,'personal');
  assert.match(await page.locator('.filter-chip').textContent(),new RegExp(category.replaceAll('.','\\.')+'.*exact.*'+flow));
  const ledger=page.getByRole('table',{name:'Transactions',exact:true});assert.equal(await ledger.locator('tbody tr').count(),1);await ledger.getByText(source,{exact:true}).waitFor();assert.equal(await page.locator('[data-page-status]').textContent(),'1–1 of 1 records');
 }
 await page.getByRole('button',{name:'Clear drilldown'}).click();await ready(page);for(const key of ['category','category_mode','flow'])assert.equal(last(calls,'transactions')[key],undefined);assert.equal(await page.getByRole('table',{name:'Transactions',exact:true}).locator('tbody tr').count(),categoryRows.length);
 await page.getByRole('tab',{name:'Overview',exact:true}).click();await ready(page);await page.locator('[data-chart="alluvial"]').getByRole('button',{name:'Inspect expense.food',exact:true}).first().click();await ready(page);
 await page.getByRole('tab',{name:'Transactions & evidence',exact:true}).click();await ready(page);for(const key of ['category','category_mode','flow'])assert.equal(last(calls,'transactions')[key],undefined);
});

test('raw payroll tx link uses its known pay date and clears unrelated ledger filters',{timeout:20000},async t=>{
 const bank={...credit,record_id:'bank:synthetic-paynet',tx_id:'synthetic-paynet',date:'2024-01-02',flow:'income',category_id:'income.payroll',amount_minor:350000,description:'Synthetic payroll bank net'};
 const payroll={payroll_id:'synthetic-payroll',pay_date:bank.date,currency:bank.currency,gross_minor:500000,net_minor:350000};
 const links=[{payroll_id:payroll.payroll_id,code:'NET',tx_id:bank.tx_id,status:'matched'},{payroll_id:payroll.payroll_id,code:'SAVINGS',event_id:'synthetic-event',status:'recorded'}];
 const {page,calls,url}=await fixture(t,{rows:[bank],responses:{compensation:()=>({payroll:[payroll],links})}});
 await page.goto(url);await ready(page);await page.getByLabel('Account').selectOption('Synthetic cash');await page.getByLabel('Dataset').selectOption('investment');await page.getByRole('button',{name:'Apply filters'}).click();await ready(page);
 await page.getByRole('tab',{name:'Compensation',exact:true}).click();await ready(page);
 const linkTable=page.getByRole('table',{name:'Payroll links'});await linkTable.getByText('synthetic-event',{exact:true}).waitFor();assert.equal(await linkTable.getByRole('button').count(),1);
 await linkTable.getByRole('button',{name:'Inspect linked entry'}).click();await ready(page);
 assert.deepEqual(last(calls,'transactions'),{start:bank.date,end:bank.date,currency:bank.currency,scope:'all',dataset:'all',record_id:bank.tx_id,offset:'0',limit:'25'});
 assert.deepEqual(await formValues(page),{start:bank.date,end:bank.date,currency:bank.currency,scope:'all',dataset:'all',account:''});
 const ledger=page.getByRole('table',{name:'Transactions',exact:true});assert.equal(await ledger.locator('tbody tr').count(),1);await ledger.getByText(bank.description,{exact:true}).waitFor();await ledger.getByText('CAD 3,500.00',{exact:true}).waitFor();assert.equal(await page.locator('[data-page-status]').textContent(),'1–1 of 1 records');
});

test('vesting policy defaults to full horizon and explicitly selects vest dates without losing grant context',{timeout:20000},async t=>{
 const awards=[{award_id:'synthetic-old-grant',award_date:'2022-04-01',employer:'Synthetic employer',grant_currency:'USD',grant_value_minor:100000,granted_shares:10,status:'scheduled'}];
 const vesting=['2023-01-01','2024-01-02','2025-01-01'].map(vest_date=>({award_id:awards[0].award_id,vest_date,percent_basis_points:2500,theoretical_shares:2.5,actual_vested_shares:null,status:'scheduled'}));
 const {page,calls,url}=await fixture(t,{responses:{compensation:q=>({awards,vesting:q.vesting_policy==='range'?vesting.filter(v=>(!q.start||v.vest_date>=q.start)&&(!q.end||v.vest_date<=q.end)):vesting})}});
 await page.goto(url);await ready(page);await page.getByRole('tab',{name:'Compensation',exact:true}).click();await ready(page);
 assert.equal(last(calls,'compensation').vesting_policy,'horizon');assert.equal(await page.getByLabel('Vesting date policy').inputValue(),'horizon');
 const schedule=page.getByRole('table',{name:'Vesting schedule',exact:true});assert.equal(await schedule.locator('tbody tr').count(),vesting.length);await schedule.getByText('2025-01-01',{exact:true}).waitFor();await schedule.getByText('2023-01-01',{exact:true}).waitFor();
 assert.equal(await page.getByLabel('From').inputValue(),'2024-01-01');assert.equal(await page.getByLabel('Through').inputValue(),'2024-01-02');
 const grants=page.getByRole('table',{name:'Grant reference values',exact:true});await grants.getByText('2022-04-01',{exact:true}).waitFor();await grants.getByText('USD 1,000.00',{exact:true}).waitFor();assert.equal(await page.getByLabel('Currency').inputValue(),'CAD');
 await page.getByLabel('Vesting date policy').selectOption('range');await ready(page);assert.equal(last(calls,'compensation').vesting_policy,'range');assert.equal(last(calls,'compensation').start,'2024-01-01');assert.equal(last(calls,'compensation').end,'2024-01-02');
 assert.equal(await schedule.locator('tbody tr').count(),1);await schedule.getByText('2024-01-02',{exact:true}).waitFor();await grants.getByText('2022-04-01',{exact:true}).waitFor();await grants.getByText('USD 1,000.00',{exact:true}).waitFor();assert.equal(await schedule.locator('tbody tr td').nth(4).textContent(),'Unavailable');await schedule.getByText('scheduled',{exact:true}).waitFor();
 await page.getByLabel('Vesting date policy').selectOption('horizon');await ready(page);assert.equal(await schedule.locator('tbody tr').count(),vesting.length);assert.equal(await schedule.getByText('scheduled',{exact:true}).count(),vesting.length);
 await page.getByRole('tab',{name:'Overview',exact:true}).click();await ready(page);assert.equal(last(calls,'overview').vesting_policy,undefined);
});
