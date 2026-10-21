import {enrichProducts}from'../../../frontend/finance/approved/products.mjs';import {enrichFinance}from'../../../frontend/finance/approved/semantics.mjs';import {createModel,selectionMembers}from'../../../frontend/finance/approved/live-model.mjs';import {createPager}from'../../../frontend/finance/approved/finance-client.mjs';
import test from'node:test';import assert from'node:assert/strict';import {loadCADQuarter}from'../../../frontend/finance/approved/cad-view.mjs';import {productsService}from'./products-fixture.mjs';
test('USD product ownership and trade values are converted together, holdings remain original dated USD',async()=>{
 const service=productsService();const api=async(endpoint,q)=>{
 if(endpoint==='quotes')return {fx:{pair:'USDCAD',rate:1.4,as_of:'2026-10-20',source:'bankofcanada',status:'ok'}};
 if(endpoint==='compensation')return {payroll:[],components:[],links:[],coverage:{payroll:{complete:true},components:{complete:true},links:{complete:true}}};
 if(q.currency==='CAD')return endpoint==='flow'?{nodes:[],edges:[],range:service.r.range,currency:'CAD',coverage:{complete:true}}:{cashflows:[],holdings:[],balances:[],coverage:{cashflows:{complete:true}}};
 const d=structuredClone(await service.api(endpoint,{...q,currency:'CAD'}));if(endpoint==='flow'){d.currency='USD';for(const e of d.edges){e.currency='USD';e.selector.filters.currency='USD';}}else for(const field of ['balances','cashflows','holdings'])for(const row of d[field]||[])row.currency='USD';return d;
 };
 const r=await loadCADQuarter(api,{...service.r.range,scope:'personal',dataset:'all'}),nodes=new Map(r.nodes.map(n=>[n.id,n]));const group=r.nodes.find(n=>n.kind==='product-group');assert.ok(group);assert.ok(nodes.has(group.ownerAccountId),'Converted product must retain converted account ownership');for(const n of r.nodes.filter(n=>n.kind==='product'))assert.ok(nodes.has(n.parentId));
 assert.equal(group.value,Math.round(20000*1.4));assert.equal(group.holdingObservations[0].currency,'USD');assert.equal(group.holdingObservations[0].market_value_minor,88000);
});

test('zero-cash product entry remains inspectable evidence without inventing ribbon width',async()=>{
 const s=productsService(),q={...s.r.range,currency:'CAD'},investments=await s.api('investments',q);
 investments.cashflows.push({event_id:'zero',account:'Example_RRSP',security_key:'GROWTH',classification:'buy',cash_amount_minor:0,currency:'CAD',date:'2026-09-11'});
 s.records.push({record_id:'investment:zero',entry_id:'zero',account:'Example_RRSP',date:'2026-09-11',currency:'CAD',amount_minor:0,category_id:'investment.trade.buy',description:'Zero-cash source entry'});
 const raw=enrichProducts(enrichFinance(s.r,{investments}),{investments}),model=createModel(raw),roots=model.project().nodes.filter(n=>n.kind==='product-group'),p=model.project(roots.map(n=>n.id)),n=p.nodes.find(n=>n.security_key==='GROWTH');const pager=createPager(selectionMembers(p,{node:n.id}),s.api);for(let i=0;i<8&&!pager.done;i++)await pager.more();assert.equal(pager.rows.length,3);assert.ok(pager.rows.some(r=>r.record_id==='investment:zero'&&r.amount===0));assert.equal(n.activityAmount,17000);
});
