import test from 'node:test';import assert from 'node:assert/strict';import * as S from '../../../frontend/finance/approved/semantics.mjs';
const node=(id,kind,label=id)=>({id,kind,label,...(kind==='account'?{account:id}:{})});
const edge=(id,source,target,kind,amount,category)=>({id,source,target,kind,amount_minor:amount,currency:'CAD',count:1,selector:{endpoint:'transactions',filters:{start:'2026-07-01',end:'2026-09-30',currency:'CAD',scope:'personal',account:source==='cash'||source==='opaque'?source:target,category,category_mode:'exact',direction:source==='cash'||source==='opaque'?'outgoing':'incoming'}}});
function raw(){return {range:{start:'2026-07-01',end:'2026-09-30'},currency:'CAD',coverage:{complete:true},nodes:[node('opaque','account'),node('cash','account','Chequing'),node('div','investment','Distribution')],edges:[edge('d','div','opaque','investment',1234,'investment.return.distribution')]};}
test('investment metadata labels opaque accounts and distribution is a receipt, not recovery or taxable-capital-gain claim',()=>{
 assert.equal(typeof S.enrichFinance,'function');const r=S.enrichFinance(raw(),{investments:{balances:[{account:'opaque',institution:'Example Broker',registration:'TFSA',as_of:'2026-09-30'}],cashflows:[]}});
 assert.equal(r.nodes.find(n=>n.id==='opaque').accountGroup,'investments');assert.match(r.nodes.find(n=>n.id==='opaque').label,/Example Broker.*TFSA/);assert.equal(r.edges[0].displayKind,'investment-income');assert.equal(r.summary.investmentIncome,1234);assert.equal(r.summary.spending,0);assert.equal(r.edges[0].amount_minor,1234);assert.deepEqual(r.edges[0].selector,raw().edges[0].selector);
});
function payrollFixture(){
 const r=raw();r.nodes.push(node('net','income','Net pay'),node('member','transfer','Member'),node('match','transfer','Employer'));
 r.edges.push(edge('net-edge','net','cash','income',154000,'income.employment.payroll'),edge('member-edge','member','opaque','transfer',10000,'transfer.contribution.member'),edge('match-edge','match','opaque','transfer',10000,'transfer.contribution.employer'));
 const payroll={payroll_id:'p',pay_date:'2026-07-31',currency:'CAD',gross_minor:100000,net_minor:74000,net_difference_minor:0,drive_url:'https://drive.google.com/file/d/synthetic/view'};
 const components=[['salary','earning',100000,100000],['tax','tax',20000,-20000],['rrsp_employee','employee_saving',5000,-5000],['espp','employee_saving',3000,-3000],['reimbursement','reimbursement',2000,2000],['rrsp_employer','employer_benefit',5000,0]].map(([code,kind,amount_minor,cash_effect_minor])=>({payroll_id:'p',code,kind,amount_minor,cash_effect_minor}));
 const links=[['net_pay','net-r','cash'],['rrsp_employee','member-r','opaque'],['rrsp_employer','employer-r','opaque']].map(([code,record_id,account])=>({payroll_id:'p',code,status:code==='net_pay'?'amount_date_payee_match_routing_unresolved':'probable_timing_amount_ytd',linked_record:{record_id,account,date:code==='net_pay'?'2026-07-31':'2026-08-07',currency:'CAD'}}));
 const records=Object.fromEntries(links.map(l=>[l.linked_record.record_id,[{...l.linked_record,entry_id:l.linked_record.record_id,amount_minor:l.code==='net_pay'?74000:5000,description:'Original posting'}]]));
 return {r,supplement:{compensation:{payroll:[payroll],components,links,coverage:{selected_payroll_id:'p',components:{complete:true},links:{complete:true},payroll:{complete:true}}},records}};
}
test('payslip bridge replaces only linked net and retirement posts, leaves uncovered net pay and exact exclusions',()=>{
 const {r,supplement}=payrollFixture(),o=S.enrichFinance(r,supplement);
 assert.equal(o.payrollCoverage.linked,1);assert.equal(o.summary.netPay,154000);assert.equal(o.summary.employerMatch,10000);assert.equal(o.summary.taxWithheld,20000);
 const net=o.edges.find(e=>e.id==='net-edge');assert.equal(net.amount_minor,80000);assert.deepEqual(net.excludeRecordIds,['net-r']);
 const hub=o.nodes.find(n=>n.kind==='payroll'&&n.payroll_id==='p');assert.ok(hub);
 assert.equal(o.edges.filter(e=>e.target===hub.id).reduce((s,e)=>s+e.amount_minor,0),102000);
 assert.equal(o.edges.filter(e=>e.source===hub.id).reduce((s,e)=>s+e.amount_minor,0),102000);
 assert.equal(o.edges.find(e=>e.id==='member-edge').amount_minor,5000);
 const linked=o.edges.find(e=>e.source===hub.id&&e.target==='opaque');assert.equal(linked.amount_minor,5000);assert.match(linked.evidenceNote,/probable/);assert.ok(linked.inlineRows.some(r=>r.record_id==='member-r'));
 assert.equal(o.edges.filter(e=>e.originalId==='net-edge').reduce((s,e)=>s+e.amount_minor,0),154000);
});
test('missing exact payroll link or unbalanced/partial components never fabricate a gross quarter',()=>{
 for(const mutate of [s=>s.records={},s=>s.compensation.payroll[0].net_difference_minor=1,s=>s.compensation.coverage.components.complete=false]){const {r,supplement}=payrollFixture();mutate(supplement);const o=S.enrichFinance(r,supplement);assert.equal(o.edges.find(e=>e.id==='net-edge').amount_minor,154000);assert.equal(o.payrollCoverage.linked,0);}
});
test('loader reads actual supplements and exact linked records; denial is never a partial success',async()=>{
 const {r,supplement}=payrollFixture();const calls=[];const api=async(endpoint,q)=>{calls.push([endpoint,q]);if(endpoint==='flow')return r;if(endpoint==='compensation')return supplement.compensation;if(endpoint==='investments')return {balances:[],cashflows:[],coverage:{balances:{complete:true},cashflows:{complete:true}}};if(endpoint==='transactions'){assert.ok(q.limit<=100,'installed API maximum page is100');const items=supplement.records[q.record_id];return {items,total:items.length};}throw Error('Unexpected endpoint');};
 assert.equal(typeof S.loadQuarter,'function');const out=await S.loadQuarter(api,{...r.range,currency:'CAD',scope:'personal'});assert.equal(out.payrollCoverage.linked,1);assert.equal(calls.filter(c=>c[0]==='transactions').length,3);assert.ok(calls.filter(c=>c[0]==='transactions').every(c=>c[1].record_id&&c[1].account));
 const denied=async(endpoint,q)=>{if(endpoint==='investments')throw Object.assign(Error('Denied'),{status:403});return api(endpoint,q);};await assert.rejects(S.loadQuarter(denied,{...r.range,currency:'CAD',scope:'personal'}),e=>e.status===403);
});
test('loader accepts server-resolved dates only for omitted filter boundaries',async()=>{
 const r=raw(),api=async(endpoint)=>endpoint==='flow'?r:endpoint==='investments'?{}:{};
 for(const query of [
  {start:'',end:'',currency:'CAD',scope:'personal'},
  {start:'',end:r.range.end,currency:'CAD',scope:'personal'},
  {start:r.range.start,end:'',currency:'CAD',scope:'personal'}
 ])assert.equal((await S.loadQuarter(api,query)).currency,'CAD');
 await assert.rejects(S.loadQuarter(api,{start:'2026-08-01',end:r.range.end,currency:'CAD',scope:'personal'}),/Unexpected finance period/);
 await assert.rejects(S.loadQuarter(api,{start:r.range.start,end:'2026-08-31',currency:'CAD',scope:'personal'}),/Unexpected finance period/);
});
test('refund labels name the receiving account and never call a distribution a refund',()=>{
 const r=raw();r.nodes.push(node('fee','expense','Bank fees'));r.edges.push(edge('fee','fee','cash','expense',100,'expense.financial.bank_fees'));
 const o=S.enrichFinance(r);assert.match(o.edges.find(e=>e.id==='fee').displayLabel,/Fee refunds.*Chequing/);assert.equal(o.edges.find(e=>e.id==='d').displayLabel,'Dividends & distributions');
});
test('quarter-observed investment accounts with no flows stay in the group without inventing cash or using future balances',()=>{
 const o=S.enrichFinance(raw(),{investments:{balances:[{account:'future',as_of:'2026-10-19',market_value_minor:1000}],history:[{account:'quiet',institution:'Example Broker',registration:'RRSP',as_of:'2026-09-30',market_value_minor:5000},{account:'closed',as_of:'2026-08-31',market_value_minor:0}]}});const quiet=o.nodes.find(n=>n.account==='quiet');assert.ok(quiet);assert.equal(quiet.accountGroup,'investments');assert.equal(quiet.noPeriodFlows,true);assert.ok(!o.nodes.some(n=>n.account==='future'||n.account==='closed'));assert.equal(o.edges.length,1);
});
export {node,edge,raw,payrollFixture};
