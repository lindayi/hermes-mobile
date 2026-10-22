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
 const raw=enrichProducts(enrichFinance(s.r,{investments}),{investments,filters:q}),model=createModel(raw),roots=model.project().nodes.filter(n=>n.kind==='product-group'),p=model.project(roots.map(n=>n.id)),n=p.nodes.find(n=>n.security_key==='GROWTH');const pager=createPager(selectionMembers(p,{node:n.id}),s.api);for(let i=0;i<8&&!pager.done;i++)await pager.more();assert.equal(pager.rows.length,3);assert.ok(pager.rows.some(r=>r.record_id==='investment:zero'&&r.amount===0));assert.equal(n.activityAmount,17000);
});

test('zero-cash evidence honors omitted, prefix and explicit-empty exact category filters',()=>{
 const row={event_id:'zero-filter',account:'Synthetic brokerage',security_key:'SYNTHETIC',classification:'buy',cash_amount_minor:0,currency:'CAD',date:'2026-08-03'};
 const investments={cashflows:[row],coverage:{cashflows:{complete:true}}},raw={currency:'CAD',range:{start:'2026-07-01',end:'2026-09-30'},nodes:[],edges:[]};
 const members=filters=>enrichProducts(raw,{investments,filters}).nodes.flatMap(node=>node.evidenceMembers||[]);
 assert.equal(members({currency:'CAD'}).length,2,'An omitted category keeps the zero-cash source evidence available');
 assert.equal(members({currency:'CAD',category:'investment.trade',category_mode:'prefix'}).length,2,'A matching category prefix includes the source evidence');
 assert.equal(members({currency:'CAD',category:'',category_mode:'exact'}).length,0,'An explicit empty exact category must not widen to all investments');
});

test('zero-cash member identity survives account flow nodes appearing and disappearing',()=>{
 const row={event_id:'zero-stable',account:'Synthetic brokerage',security_key:'SYNTHETIC',classification:'buy',cash_amount_minor:0,currency:'CAD',date:'2026-08-03'};
 const investments={cashflows:[row],coverage:{cashflows:{complete:true}}},range={start:'2026-07-01',end:'2026-09-30'};
 const graph=active=>({currency:'CAD',range,nodes:active?[{id:'flow-account',account:row.account,label:row.account,kind:'account'}]:[],edges:[]});
 const member=active=>enrichProducts(graph(active),{investments,filters:{currency:'CAD'}}).nodes.flatMap(node=>node.evidenceMembers||[]).find(item=>item.selector.filters.record_id==='investment:zero-stable');
 const empty=member(false),active=member(true),restored=member(false);
 assert.ok(empty&&active&&restored);
 assert.equal(empty.id,active.id);assert.equal(active.id,restored.id);
 assert.deepEqual(active.selector,empty.selector);assert.deepEqual(restored.selector,empty.selector);
 assert.equal(empty.selector.filters.account,row.account);assert.equal(empty.selector.filters.currency,'CAD');
 assert.equal(member(false).id,empty.id,'An unchanged raw source record remains selectable after rebind');
 assert.equal(enrichProducts(graph(false),{investments:{cashflows:[]},filters:{currency:'CAD'}}).nodes.flatMap(node=>node.evidenceMembers||[]).some(item=>item.id===empty.id),false,'A removed source record cannot rebind');
 assert.equal(enrichProducts(graph(false),{investments:{cashflows:[{...row,account:'Other brokerage'}]},filters:{currency:'CAD',account:row.account}}).nodes.flatMap(node=>node.evidenceMembers||[]).some(item=>item.id===empty.id),false,'A different account cannot rebind the saved member');
 assert.equal(enrichProducts(graph(false),{investments:{cashflows:[{...row,currency:'USD'}]},filters:{currency:'CAD'}}).nodes.flatMap(node=>node.evidenceMembers||[]).some(item=>item.id===empty.id),false,'A different currency cannot rebind the saved member');
});
