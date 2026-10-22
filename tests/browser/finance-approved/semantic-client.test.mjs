import test from 'node:test';import assert from 'node:assert/strict';import {createPager,makeAPI} from '../../../frontend/finance/approved/finance-client.mjs';
const row=(id,account,amount)=>({entry_id:id,record_id:id,account,date:'2026-07-31',amount_minor:amount,currency:'CAD',description:'Original '+id});
test('excluded linked payroll posting is absent from remainder; inline evidence keeps exact sign/source and no request',async()=>{
 let calls=0;const api=async()=>{calls++;return {items:[row('linked','bank',740),row('other','bank',800)],total:2};};
 const p=createPager([{id:'net',selector:{endpoint:'transactions',filters:{}},excludeRecordIds:['linked']},{id:'bridge',inlineRows:[{...row('linked','bank',740),recordBasis:'Bank deposit'}, {...row('rrsp','Payroll',-50),recordBasis:'Payslip component'}]}],api);
 await p.more();assert.equal(p.done,true);assert.equal(calls,1);assert.equal(p.rows.length,3);assert.equal(p.rows.find(r=>r.record_id==='rrsp').amount,-50);assert.equal(p.rows.find(r=>r.record_id==='rrsp').recordBasis,'Payslip component');
 const q=createPager([{id:'net',selector:{endpoint:'transactions',filters:{}},excludeRecordIds:['linked']}],api);await q.more();assert.deepEqual(q.rows.map(r=>r.record_id),['other']);
});
test('group selection retains both internal transfer legs but never unrelated account legs',async()=>{
 const p=createPager([{id:'x',selector:{endpoint:'transfers',filters:{}}}],async()=>({items:[{link_id:'l',link_type:'transfer',debit:row('d','invest1',-100),credit:row('c','invest2',100)},{link_id:'l2',link_type:'transfer',debit:row('d2','bank',-50),credit:row('c2','invest2',50)}],total:2}),['invest1','invest2']);await p.more();assert.deepEqual(p.rows.map(r=>r.account).sort(),['invest1','invest2','invest2']);assert.equal(p.rows.find(r=>r.account==='invest1').counterparty,'invest2');
});
test('existing authenticated compensation and investments endpoints use no-store without permitting unknown endpoints',async()=>{
 const calls=[];const api=makeAPI(async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({})};});await api('compensation');await api('investments');assert.equal(calls.length,2);assert.ok(calls.every(c=>c.options.cache==='no-store'&&c.options.credentials==='same-origin'));await assert.rejects(api('config'));
});
