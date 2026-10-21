import test from 'node:test';
import assert from 'node:assert/strict';
import {makeAPI,createPager} from '../../../frontend/finance/approved/finance-client.mjs';
const CAD=await import('../../../frontend/finance/approved/cad-view.mjs').catch(e=>{if(e.code==='ERR_MODULE_NOT_FOUND')return {};throw e;});
const fx={pair:'USDCAD',rate:1.125,as_of:'2026-08-31',source:'synthetic',status:'ok'};
const query={start:'2026-07-01',end:'2026-09-30',scope:'personal',dataset:'all',category:'',category_mode:'exact',q:'synthetic'};
function sample(currency,edges=[['buy','a','b',12,'transfer','investment.trade.buy']]){
 return {currency,range:{start:query.start,end:query.end},coverage:{complete:true},nodes:[{id:'a',kind:'account',account:'Synthetic broker',label:'Synthetic broker'},{id:'b',kind:'expense',label:'Portfolio'}],edges:edges.map(([id,source,target,amount_minor,kind,category])=>({id,source,target,amount_minor,kind,currency,source_id:'evidence-'+id,count:1,selector:{endpoint:'transactions',filters:{...query,currency,account:'Synthetic broker',category,category_mode:'exact'}}}))};
}
function service(cad=sample('CAD',[]),usd=sample('USD'),quotes={fx}){
 const calls=[];
 return {calls,api:async(endpoint,q,signal)=>{calls.push({endpoint,q,signal});return structuredClone(endpoint==='flow'?(q.currency==='CAD'?cad:usd):endpoint==='quotes'?quotes:{});}};
}

test('CAD-first merge namespaces USD graph, keeps original evidence and sends every filter to both currencies',async()=>{
 assert.equal(typeof CAD.loadCADQuarter,'function');
 const usd=sample('USD'),cad=sample('CAD');const svc=service(cad,usd);const controller=new AbortController();
 const result=await CAD.loadCADQuarter(svc.api,query,controller.signal);
 assert.equal(result.currency,'CAD');assert.equal(result.edges.length,2);assert.equal(result.nodes.length,4);
 assert.equal(new Set(result.nodes.map(n=>n.id)).size,4);assert.equal(new Set(result.edges.map(e=>e.id)).size,2);
 assert.equal(result.edges[0].amount_minor,12);assert.equal(result.edges[0].approximate,undefined);
 const edge=result.edges[1];assert.equal(edge.amount_minor,14);assert.equal(edge.currency,'CAD');assert.equal(edge.originalCurrency,'USD');assert.equal(edge.originalAmountMinor,12);assert.equal(edge.originalSourceId,'buy');assert.equal(edge.source_id,'evidence-buy');assert.equal(edge.approximate,true);
 assert.deepEqual(edge.selector,usd.edges[0].selector);assert.ok(result.nodes.some(n=>n.id===edge.source&&n.label.endsWith('(USD)')&&n.account==='Synthetic broker'&&n.currency==='CAD'&&n.approximate===true));
 assert.deepEqual(result.originalUSD.edges[0].selector,usd.edges[0].selector);assert.equal(result.originalUSD.edges[0].amount_minor,12);assert.equal(result.originalUSD.currency,'USD');
 assert.equal(result.summary.spending,0);assert.equal(result.summary.income,0);assert.equal(result.foreignUnconverted,null);
 assert.equal(result.fx.as_of,fx.as_of);assert.match(result.fx.label,/CAD equivalent at rate date/);
 for(const currency of ['CAD','USD'])for(const endpoint of ['flow','investments','compensation'])assert.deepEqual(svc.calls.find(c=>c.endpoint===endpoint&&c.q.currency===currency).q,{...query,currency});
 assert.deepEqual(svc.calls.find(c=>c.endpoint==='quotes').q,{});assert.ok(svc.calls.every(c=>c.signal===controller.signal));
 assert.deepEqual(usd,sample('USD'));
});

test('headers sum individually rounded original edge signs, never convert a net total',async()=>{
 const usd=sample('USD',[
  ['income1','b','a',4,'income','income.other'],['income2','b','a',4,'income','income.other'],
  ['expense1','a','b',4,'expense','expense.fees'],['expense2','a','b',4,'expense','expense.fees'],
  ['refund','b','a',1,'expense','expense.fees'],['buy','a','b',100,'transfer','investment.trade.buy']
 ]);
 const result=await CAD.loadCADQuarter(service(sample('CAD',[]),usd).api,query);
 assert.equal(result.summary.income,10);assert.equal(result.summary.spending,9);assert.equal(result.summary.net,1);
 assert.equal(result.originalUSD.summary.income,8);assert.equal(result.originalUSD.summary.spending,7);
});

test('unavailable or invalid FX retains the complete inspectable USD graph outside CAD totals',async()=>{
 const usd=sample('USD');
 for(const invalid of [null,{...fx,status:'unavailable'},{...fx,pair:'CADUSD'},{...fx,rate:0},{...fx,rate:-2},{...fx,rate:NaN},{...fx,as_of:'2026-02-31'},{...fx,source:''}]){
  const result=await CAD.loadCADQuarter(service(sample('CAD',[]),usd,{fx:invalid}).api,query);
  assert.equal(result.currency,'CAD');assert.equal(result.edges.length,0);assert.equal(result.summary.spending,0);
  assert.equal(result.fx.status,'unavailable');assert.equal(result.fx.rate,null);assert.equal(result.foreignUnconverted.currency,'USD');
  assert.equal(result.foreignUnconverted.edges[0].amount_minor,12);assert.deepEqual(result.foreignUnconverted.edges[0].selector,usd.edges[0].selector);
  assert.deepEqual(result.foreignUnconverted,result.originalUSD);assert.ok(result.notes.some(n=>/USD.*not converted/.test(n)));
 }
 const svc=service();
 const result=await CAD.loadCADQuarter(async(endpoint,...args)=>{if(endpoint==='quotes')throw new TypeError('Network offline');return svc.api(endpoint,...args);},query);
 assert.equal(result.foreignUnconverted.edges.length,1);assert.equal(result.fx.status,'unavailable');
});

test('structured UI filters are flattened identically without overriding period or currency',async()=>{
 const svc=service();const {start,end,scope,...filters}=query;
 await CAD.loadCADQuarter(svc.api,{start,end,scope,filters:{...filters,currency:'EUR',start:'1900-01-01'}},undefined);
 for(const c of svc.calls.filter(c=>c.endpoint!=='quotes'))assert.deepEqual(c.q,{...query,currency:c.q.currency});
});

test('cancellation before the load performs no reads; cancellation during quotes never yields partial success',async()=>{
 const svc=service(),before=new AbortController();before.abort();
 await assert.rejects(CAD.loadCADQuarter(svc.api,query,before.signal),e=>e.name==='AbortError');assert.equal(svc.calls.length,0);
 const during=new AbortController();
 await assert.rejects(CAD.loadCADQuarter(async(endpoint,...args)=>{if(endpoint==='quotes')during.abort();return svc.api(endpoint,...args);},query,during.signal),e=>e.name==='AbortError');
});

test('401 and 403 on every quarter read including optional FX propagate, never become missing data',async()=>{
 for(const status of [401,403])for(const currency of ['CAD','USD'])for(const target of ['flow','investments','compensation','quotes']){
  const svc=service(),denied=Object.assign(new Error('Denied'),{status});
  await assert.rejects(CAD.loadCADQuarter(async(endpoint,q,signal)=>{if(endpoint===target&&(target==='quotes'||q.currency===currency))throw denied;return svc.api(endpoint,q,signal);},query),e=>e===denied);
 }
});

test('converted members still inspect signed original USD rows, inline evidence and original source',async()=>{
 const usd=sample('USD');usd.edges[0].inlineRows=[{entry_id:'synthetic-entry',record_id:'synthetic-record',currency:'USD',amount_minor:-12,account:'Synthetic broker',source_id:'synthetic-source',drive_url:'https://drive.google.com/synthetic',description:'Original trade'}];
 const result=await CAD.loadCADQuarter(service(sample('CAD',[]),usd).api,query);
 assert.deepEqual(result.edges[0].inlineRows,usd.edges[0].inlineRows);
 const pager=createPager(result.edges,async()=>{throw Error('Inline evidence must not fetch');},'Synthetic broker');await pager.more();
 assert.equal(pager.rows[0].amount,-12);assert.equal(pager.rows[0].currency,'USD');assert.equal(pager.rows[0].source,'synthetic-source');
 assert.equal(CAD.presentationEquivalent(pager.rows[0],result.fx).amount_minor,-14);
 const plain=await CAD.loadCADQuarter(service().api,query);let request;
 const remote=createPager(plain.edges,async(endpoint,q)=>{request=q;return {items:usd.edges[0].inlineRows,total:1};});await remote.more();
 assert.equal(request.currency,'USD');assert.equal(request.category_mode,'exact');assert.equal(remote.rows[0].amount,-12);
});

test('stale FX stays explicitly stale and dated, never presented as a historical quarter rate',async()=>{
 const stale={...fx,status:'stale',as_of:'2026-11-02'};
 const result=await CAD.loadCADQuarter(service(sample('CAD',[]),sample('USD'),{fx:stale}).api,query);
 assert.equal(result.fx.as_of,'2026-11-02');assert.equal(result.fx.status,'stale');assert.match(result.fx.label,/stale/i);
 assert.match(result.notes.join(' '),/stale/i);assert.equal(result.edges[0].amount_minor,14);
 assert.match(CAD.presentationEquivalent({amount:12,currency:'USD'},stale).label,/stale/i);
});

test('presentation equivalent rounds signed minor units exactly and never rewrites original detail',()=>{
 assert.equal(typeof CAD.presentationEquivalent,'function');
 const row=Object.freeze({amount:-12,currency:'USD',source:'synthetic-source'});
 const eq=CAD.presentationEquivalent(row,fx);
 assert.equal(eq.amount_minor,-14);assert.equal(eq.currency,'CAD');assert.equal(eq.approximate,true);
 assert.equal(eq.originalCurrency,'USD');assert.equal(eq.originalAmountMinor,-12);
 assert.equal(eq.as_of,fx.as_of);assert.equal(eq.rate,fx.rate);
 assert.match(eq.label,/CAD equivalent at rate date 2026-08-31/);
 assert.equal(row.amount,-12);assert.equal(row.currency,'USD');
 assert.equal(CAD.presentationEquivalent({amount_minor:12,currency:'USD'},fx).amount_minor,14);
 assert.equal(CAD.presentationEquivalent(row,{...fx,rate:1.005}).amount_minor,-12);
 assert.equal(CAD.presentationEquivalent({amount_minor:100,currency:'USD'},{...fx,rate:1.005}).amount_minor,101);
 assert.equal(CAD.presentationEquivalent({amount:12,currency:'CAD'},fx),null);
 assert.equal(CAD.presentationEquivalent(row,{...fx,status:'unavailable'}),null);
});

test('quotes transport permits only symbols and retains authentication, timeout and no-store',async()=>{
 const calls=[];
 const api=makeAPI(async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({fx:{status:'unavailable'}})};});
 await api('quotes',{symbols:'',currency:'USD',start:'2026-07-01',account:'synthetic'});
 await api('quotes',{symbols:'SYNTH.TO',limit:50});
 assert.equal(new URL(calls[0].url,'https://example.test').search,'');
 assert.equal(new URL(calls[1].url,'https://example.test').search,'?symbols=SYNTH.TO');
 assert.ok(calls.every(c=>c.options.credentials==='same-origin'&&c.options.cache==='no-store'&&c.options.signal instanceof AbortSignal));
 await assert.rejects(api('config'),/Invalid finance endpoint/);
});
