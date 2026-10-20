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
const moduleURL = new URL('../../frontend/finance/model.mjs', import.meta.url);
test('finance API is fixed-origin, no-store and refuses unknown endpoints', async () => {
 const {financeAPI}=await import(moduleURL);
 let call; const api=financeAPI(async (...args)=>{call=args;return {ok:true,json:async()=>({items:[]})};});
 await api('transactions',{currency:'CAD',q:'a & b',path:'/private'});
 assert.equal(call[0],'/hermes/app-api/finance/transactions?currency=CAD&q=a+%26+b');
 assert.equal(call[1].cache,'no-store');assert.equal(call[1].credentials,'same-origin');
 await assert.rejects(api('../private'),/endpoint/);
});
