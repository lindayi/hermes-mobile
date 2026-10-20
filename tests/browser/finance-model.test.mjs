import test from 'node:test';
import assert from 'node:assert/strict';
test('reported percentage accepts source decimal text without treating unknown as zero',async()=>{
 const m=await import(moduleURL);
 assert.equal(typeof m.reportedReturn,'function','Source-return formatter is missing');
 assert.equal(m.reportedReturn('8.125','provider'), '8.125% (provider)');
 assert.equal(m.reportedReturn('-2.50','provider'), '-2.5% (provider)');
 for(const value of [null,'',false,'Infinity','1e999','<script>']) assert.equal(m.reportedReturn(value,'provider'),'Unavailable');
});
test('unknown money, signed flows, exact dates and evidence URLs stay conservative',async()=>{
 const {money,initialRange,evidenceURL,quoteEstimate,flowRows,monthlyPeriods}=await import(moduleURL);
 assert.equal(money(null,'CAD'),'Unavailable'); assert.match(money(-123,'USD'),/-.*1.23/);
 assert.deepEqual(initialRange({end:'2024-08-15'}),{start:'2024-01-01',end:'2024-08-15'});
 assert.equal(evidenceURL('javascript:alert(1)'),null);assert.equal(evidenceURL('https://drive.google.com.evil.test/x'),null);
 assert.equal(evidenceURL('https://drive.google.com/file/d/synthetic/view'),'https://drive.google.com/file/d/synthetic/view');
 assert.equal(quoteEstimate({quantity:4,quantity_basis:'par',currency:'CAD'},{price:10,currency:'CAD',status:'ok'}),null);
 assert.equal(quoteEstimate({quantity:4,quantity_basis:'shares',currency:'CAD',security_key:'TGED'},{price:10,currency:'USD',status:'ok'}),null);
 assert.equal(quoteEstimate({quantity:4,quantity_basis:'shares',currency:'CAD',security_key:'TGED'},{price:10,currency:'CAD',status:'ok',stale:false}),4000);
 assert.deepEqual(flowRows([{category_id:'expense.food',amount_minor:-500,count:1}],[])[0].direction,'flow');
 assert.equal(monthlyPeriods([{account:'A',start_date:'2024-01-01',end_date:'2024-12-31'},{account:'A',start_date:'2024-01-01',end_date:'2024-01-31'}]).length,1);
});
test('money requires an explicit supported currency without relabeling unknown or EUR as CAD',async()=>{
 const {money}=await import(moduleURL);
 for(const currency of [undefined,null,'','unknown','EUR'])assert.equal(money(123,currency),'Unavailable',`currency ${currency}`);
 assert.equal(money(123,'CAD'),'CAD 1.23');assert.equal(money(-123,'USD'),'-USD 1.23');
});
test('cashflow uses typed expense sign and maps only supported share symbols',async()=>{
 const {flowRows,quoteSymbol}=await import(moduleURL);
 const rows=flowRows([{category_id:'food',flow:'expense',amount_minor:-1000,expense_minor:1000},{category_id:'food_refund',flow:'expense',amount_minor:200,expense_minor:-200}],[]);
 assert.equal(rows[0].amount_minor,1000);assert.equal(rows[0].direction,'flow');assert.equal(rows[1].direction,'recovery');
 assert.equal(quoteSymbol({security_key:'TGED',quantity_basis:'shares'}),'TGED.TO');
 assert.equal(quoteSymbol({security_key:'SL:PLAN',quantity_basis:'units'}),null);
 assert.equal(quoteSymbol({security_key:'MSFT',quantity_basis:'par'}),null);
});
test('recorded position keeps included investment cash and restricted liquidity distinct',async()=>{
 const {recordedPosition}=await import(moduleURL);
 const result=recordedPosition({balances:[{currency:'CAD',market_value_minor:10000,cash_minor:2000,planning_protected:0}],bank_balances:[{currency:'CAD',amount_minor:5000,planning_protected:0},{currency:'CAD',amount_minor:3000,planning_protected:1},{currency:'USD',amount_minor:9000}],liabilities:[{currency:'CAD',amount_minor:4000}]},'CAD');
 assert.deepEqual(result,{investments:10000,cash:8000,liquid:5000,restricted:3000,debt:4000,net:14000});
 assert.equal(recordedPosition({balances:[],bank_balances:[],liabilities:[]},'CAD').net,null);
 assert.equal(recordedPosition({balances:[{currency:'CAD',market_value_minor:null}],bank_balances:[{currency:'CAD',amount_minor:100}],liabilities:[{currency:'CAD',amount_minor:0}]},'CAD').net,null);
});
test('recorded position protects mother and family allocation prefixes independently of flags',async()=>{
 const {recordedPosition}=await import(moduleURL);
 for(const allocation of ['mother_reserved','family','MOTHER','Family_reserved','motherOther','familyOther'])for(const planning_protected of [0,false,null,undefined]){
  const row={currency:'CAD',allocation,planning_protected};
  assert.deepEqual(recordedPosition({balances:[{...row,market_value_minor:10000}],bank_balances:[{...row,amount_minor:3000},{currency:'CAD',allocation:'personal',amount_minor:5000,current_planning_protected:0}],liabilities:[{currency:'CAD',amount_minor:4000}]},'CAD'),{investments:10000,cash:8000,liquid:5000,restricted:13000,debt:4000,net:14000},`${allocation} / ${planning_protected}`);
 }
 for(const allocation of ['personal','other_family',' mother_reserved',null,undefined]){
  const result=recordedPosition({bank_balances:[{currency:'CAD',allocation,amount_minor:3000,planning_protected:0}]},'CAD');
  assert.equal(result.liquid,3000);assert.equal(result.restricted,0);
 }
 const unknown=recordedPosition({bank_balances:[{currency:'CAD',allocation:'personal',amount_minor:3000}]},'CAD');assert.equal(unknown.liquid,null);assert.equal(unknown.restricted,null);
});
test('monthly performance retains cross-month statement windows without overlapping observations',async()=>{
 const {monthlyPeriods}=await import(moduleURL);
 const rows=[{account:'A',currency:'CAD',start_date:'2024-01-31',end_date:'2024-02-29'},{account:'A',currency:'CAD',start_date:'2024-02-01',end_date:'2024-02-29'},{account:'A',currency:'CAD',start_date:'2024-03-01',end_date:'2024-03-31'},{account:'B',currency:'CAD',start_date:'2024-02-01',end_date:'2024-02-29'}];
 assert.equal(monthlyPeriods(rows).length,3);assert.equal(monthlyPeriods(rows)[0].start_date,'2024-01-31');
});
test('finance requests have a bounded deadline without confusing navigation aborts',async()=>{
 const {financeAPI}=await import(moduleURL);
 const wait=async(_url,{signal})=>new Promise((_resolve,reject)=>signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true}));
 await assert.rejects(financeAPI(wait,5)('overview'),/timed out/);
 const controller=new AbortController();const request=financeAPI(wait,1000)('overview',{},controller.signal);controller.abort();await assert.rejects(request,{name:'AbortError'});
});
test('actual decimal-text units, signed debt credits, category signs and adjacent boundaries',async()=>{
 const {quoteSymbol,quoteEstimate,recordedPosition,flowRows,monthlyPeriods}=await import(moduleURL);
 assert.equal(quoteSymbol({security_key:'TGED',quantity_basis:'units'}),'TGED.TO');
 assert.equal(quoteEstimate({security_key:'TGED',quantity:'4.25',quantity_basis:'units',currency:'CAD'},{price:10,currency:'CAD',status:'ok',stale:false}),4250);
 for(const quantity of ['NaN','Infinity','1e30','-2','999999999999999999999999','4x'])assert.equal(quoteEstimate({security_key:'TGED',quantity,quantity_basis:'units',currency:'CAD'},{price:10,currency:'CAD',status:'ok'}),null);
 assert.equal(quoteSymbol({security_key:'SL:PLAN',quantity_basis:'units'}),null);
 assert.equal(recordedPosition({liabilities:[{currency:'CAD',amount_minor:1000},{currency:'CAD',amount_minor:-200}]},'CAD').debt,800);
 const flows=flowRows([{category_id:'expense.food',amount_minor:-1000},{category_id:'expense.food',amount_minor:200},{category_id:'transfer.cash',amount_minor:-500}],[]);
 assert.equal(flows[0].amount_minor,1000);assert.equal(flows[1].amount_minor,-200);assert.equal(flows[1].direction,'recovery');assert.equal(flows[2].direction,'outflow');
 assert.equal(monthlyPeriods([{account:'A',currency:'CAD',start_date:'2024-01-31',end_date:'2024-02-29'},{account:'A',currency:'CAD',start_date:'2024-02-29',end_date:'2024-03-31'}]).length,2);
});
test('category labels reject allocation components and ambiguous metadata',async()=>{
 const {flowRows}=await import(moduleURL);
 const rows=['expense.food','income.salary','expense.home_utilities','expense.travel'].map(category_id=>({category_id,amount_minor:100}));
 const labels=[{category_id:'expense.food',label:'whole'},{category_id:'income.salary',label:'Salary'},{category_id:'expense.home_utilities',label:'whole'},{category_id:'expense.travel',label:'Trips'},{category_id:'expense.travel',label:'Other'}];
 assert.deepEqual(flowRows(rows,labels).map(r=>r.label),['Food','Salary','Home utilities','Travel']);
});
test('generic category leaves retain parent context',async()=>{const {categoryLabel}=await import(moduleURL);assert.equal(categoryLabel('expense.housing.bank',[{category_id:'expense.housing.bank',label:'Bank'}]),'Housing / bank');assert.equal(categoryLabel('expense.transport.payment',[{category_id:'expense.transport.payment',label:'Payment'}]),'Transport / payment');});
test('dense projection retains every edge, separates direction and offers exact account focus',async()=>{
 const {mapProjection}=await import(moduleURL);assert.equal(typeof mapProjection,'function');
 const nodes=Array.from({length:20},(_,i)=>({id:'a'+i,label:'Synthetic account '+i,account:'Synthetic account '+i,kind:'account'}));
 const edges=[];for(let i=0;i<107;i++){nodes.push({id:'c'+i,label:'Synthetic category '+i,kind:'expense'});edges.push({id:'e'+i,source:'a'+(i%20),target:'c'+i,kind:'expense',amount_minor:100+i,count:1,currency:'CAD',selector:{endpoint:'transactions',filters:{account:'Synthetic account '+(i%20),category:'expense.group'+(i%7)+'.item'+i,direction:'outgoing',category_mode:'exact',flow:'expense'}}});}
 const data={nodes,edges},result=mapProjection(data);
 assert.ok(result.nodes.length<=40);assert.ok(result.edges.length<=107);
 assert.equal(result.nodes.filter(n=>n.kind==='account').length,20,'Owned accounts remain individually visible in the realm');
 assert.equal(result.edges.reduce((n,e)=>n+e.amount_minor,0),edges.reduce((n,e)=>n+e.amount_minor,0));
 assert.deepEqual(result.edges.flatMap(e=>e.members||[e]).map(e=>e.id).sort(),edges.map(e=>e.id).sort());
 const focused=mapProjection(data,'Synthetic account 0');assert.equal(focused.edges.flatMap(e=>e.members||[e]).length,edges.filter(e=>e.source==='a0').length);
 assert.ok(focused.nodes.some(n=>n.account==='Synthetic account 0'));
});
test('flow API forwards bounded selector field allowlist without accepting arbitrary paths',async()=>{
 const {financeAPI}=await import(moduleURL);let url;
 const api=financeAPI(async u=>{url=u;return {ok:true,json:async()=>({})};});
 await api('flow',{direction:'incoming',source_account:'A',target_account:'B',link_type:'own_cash',transfer_status:'unmatched',evil:'no'});
 assert.equal(url,'/hermes/app-api/finance/flow?direction=incoming&source_account=A&target_account=B&link_type=own_cash&transfer_status=unmatched');
});
const moduleURL = new URL('../../frontend/finance/model.mjs', import.meta.url);
test('finance API is fixed-origin, no-store and refuses unknown endpoints', async () => {
 const {financeAPI}=await import(moduleURL);
 let call; const api=financeAPI(async (...args)=>{call=args;return {ok:true,json:async()=>({items:[]})};});
 await api('transactions',{currency:'CAD',q:'a & b',path:'/private'});
 assert.equal(call[0],'/hermes/app-api/finance/transactions?currency=CAD&q=a+%26+b');
 assert.equal(call[1].cache,'no-store');assert.equal(call[1].credentials,'same-origin');
 await assert.rejects(api('../private'),/endpoint/);
});
