import test from 'node:test';
import assert from 'node:assert/strict';
import {loadQuarter,enrichFinance} from '../../../frontend/finance/approved/semantics.mjs';
import {createModel,selectionMembers} from '../../../frontend/finance/approved/live-model.mjs';

// Synthetic source records; intentionally not a transcription of private payroll.
const dates=['2026-07-15','2026-07-30','2026-08-14','2026-08-31','2026-09-15','2026-09-29'];
const categories={net_pay:'income.employment.payroll',rrsp_employee:'transfer.contribution.member',rrsp_employer:'transfer.contribution.employer'};
function fixture(){
 const range={start:'2026-07-01',end:'2026-09-30'};
 const payroll=dates.map((pay_date,i)=>({payroll_id:'synthetic-'+i,pay_date,currency:'CAD',gross_minor:100000,net_minor:72000,net_difference_minor:0,source_id:'source-'+i,drive_url:'https://drive.google.com/file/d/synthetic-'+i+'/view'}));
 const records={};
 const payrollDetails=payroll.map((p,i)=>{
  const components=[['salary','earning',100000,100000],['tax','tax',20000,-20000],['espp','employee_saving',3000,-3000],['rrsp_employee','employee_saving',5000,-5000],['rrsp_employer','employer_benefit',5000,0]].map(([code,kind,amount_minor,cash_effect_minor])=>({payroll_id:p.payroll_id,code,kind,amount_minor,cash_effect_minor,ytd_minor:9999999}));
  const links=Object.keys(categories).map(code=>{
   const linked_record={record_id:p.payroll_id+'-'+code,account:code==='net_pay'?'cash':'plan',currency:'CAD',date:code==='net_pay'?p.pay_date:i===5?'2026-10-08':p.pay_date,dataset:code==='net_pay'?'bank':'investment'};
   records[linked_record.record_id]=[{...linked_record,entry_id:linked_record.record_id+'-whole',category_id:categories[code],amount_minor:code==='net_pay'?72000:5000,description:'Synthetic original posting',source_id:'posting-'+i,drive_url:'https://drive.google.com/file/d/posting-'+i+'/view'}];
   return {payroll_id:p.payroll_id,code,linked_record,status:'probable_timing_amount_ytd',evidence:'Synthetic source match; routing unresolved'};
  });
  return {payroll:[p],components,links,coverage:{selected_payroll_id:p.payroll_id,payroll:{complete:true},components:{complete:true},links:{complete:true}}};
 });
 const compensation={...structuredClone(payrollDetails.at(-1)),payroll:structuredClone(payroll)};
 const raw={range,currency:'CAD',coverage:{complete:true},nodes:[{id:'cash',account:'cash',kind:'account',label:'Chequing'},{id:'plan',account:'plan',kind:'account',label:'RRSP'},...Object.keys(categories).map(code=>({id:code,kind:code==='net_pay'?'income':'transfer',label:code}))],edges:Object.entries(categories).map(([code,category])=>({id:code+'-original',source:code,target:code==='net_pay'?'cash':'plan',kind:code==='net_pay'?'income':'transfer',amount_minor:code==='net_pay'?432000:30000,currency:'CAD',count:6,selector:{endpoint:'transactions',filters:{...range,currency:'CAD',scope:'personal',account:code==='net_pay'?'cash':'plan',category,category_mode:'exact',direction:'incoming'}}}))};
 return {raw,compensation,payrollDetails,records};
}
function fakeAPI(f,override=()=>undefined){
 const calls=[];let active=0,peak=0;
 const api=async(endpoint,query,signal)=>{
  calls.push({endpoint,query,signal});active++;peak=Math.max(peak,active);
  try{await new Promise(resolve=>setTimeout(resolve,1));const replacement=await override(endpoint,query);if(replacement!==undefined)return replacement;
   if(endpoint==='flow')return f.raw;
   if(endpoint==='investments')return {};
   if(endpoint==='compensation')return query.start===query.end?(f.payrollDetails.find(d=>d.payroll[0].payroll_id===query.payroll_id)||f.payrollDetails.find(d=>d.payroll[0].pay_date===query.start)):f.compensation;
   if(endpoint==='transactions'){const items=f.records[query.record_id]||[];return {items,total:items.length};}
   throw Error('Unexpected endpoint');
  }finally{active--;}
 };
 return {api,calls,get peak(){return peak;}};
}

test('loader collects every selected-period payslip and outside-period linked evidence with bounded concurrency',async()=>{
 const f=fixture(),client=fakeAPI(f),signal=new AbortController().signal;
 const query={...f.raw.range,currency:'CAD',scope:'personal',q:'source filter',vesting_policy:'range'};
 await loadQuarter(client.api,query,signal);
 const compensationCalls=client.calls.filter(c=>c.endpoint==='compensation');
 assert.equal(compensationCalls.length,6,'reuse newest detail, fetch other five pay dates');
 assert.deepEqual(compensationCalls.filter(c=>c.query.start===c.query.end).map(c=>c.query.start).sort(),dates.slice(0,-1));
 assert.ok(client.peak<=3&&client.peak>=2,'bounded parallel requests');
 assert.ok(client.calls.every(c=>c.signal===signal&&c.query.q===query.q&&c.query.scope===query.scope&&c.query.vesting_policy==='range'));
 const tx=client.calls.filter(c=>c.endpoint==='transactions');assert.equal(tx.length,18);
 assert.ok(tx.every(c=>c.query.limit===100&&c.query.offset===0&&c.query.start===c.query.end));
 assert.equal(tx.filter(c=>c.query.start==='2026-10-08').length,2,'October rows are evidence, not Q3 cash');
});

test('same-date payslips request and retain detail by payroll ID',async()=>{
 const f=fixture(),sameDate='2026-08-12';
 for(const id of ['synthetic-0','synthetic-1']){
  const detail=f.payrollDetails.find(item=>item.payroll[0].payroll_id===id);
  detail.payroll[0].pay_date=sameDate;
  f.compensation.payroll.find(item=>item.payroll_id===id).pay_date=sameDate;
 }
 const client=fakeAPI(f),out=await loadQuarter(client.api,{...f.raw.range,currency:'CAD',scope:'personal'});
 const detailCalls=client.calls.filter(call=>call.endpoint==='compensation'&&call.query.start===sameDate&&call.query.end===sameDate);
 assert.deepEqual(detailCalls.map(call=>call.query.payroll_id).sort(),['synthetic-0','synthetic-1']);
 assert.equal(out.payrollCoverage.detailed,6);
 assert.equal(out.edges.filter(edge=>edge.payrollBridge&&edge.payroll_id==='synthetic-1').length>0,true,'The second same-date payslip keeps its own breakdown');
 assert.ok(out.edges.some(edge=>edge.payroll_id==='synthetic-1'&&edge.inlineRows?.some(row=>row.recordBasis==='Payslip component'&&row.date===sameDate)));
});

test('per-pay authorization failures and aborts propagate rather than returning partial finance',async()=>{
 for(const endpoint of ['compensation','transactions'])for(const error of [Object.assign(Error('Denied'),{status:401}),Object.assign(Error('Denied'),{status:403}),new DOMException('Cancelled','AbortError')]){
  const f=fixture(),client=fakeAPI(f,(name,q)=>{if(name===endpoint&&(endpoint==='transactions'||q.start===q.end))throw error;});
  await assert.rejects(loadQuarter(client.api,{...f.raw.range,currency:'CAD',scope:'personal'}),e=>e===error);
 }
 const controller=new AbortController(),f=fixture();controller.abort();await assert.rejects(loadQuarter(fakeAPI(f).api,{...f.raw.range,currency:'CAD'},controller.signal),e=>e.name==='AbortError');
});

test('failed per-pay requests retain original cash and report partial payroll coverage',async()=>{
 const f=fixture(),client=fakeAPI(f,(endpoint,q)=>{if(endpoint==='compensation'&&q.start===dates[0]&&q.start===q.end)throw Error('Unavailable');});
 const out=await loadQuarter(client.api,{...f.raw.range,currency:'CAD',scope:'personal'});
 assert.equal(out.payrollCoverage.count,6);assert.equal(out.payrollCoverage.linked,5);assert.equal(out.payrollCoverage.unavailable,1);assert.equal(out.payrollCoverage.complete,false);assert.equal(out.summary.payrollGross,500000);assert.equal(out.summary.netPay,432000);
 assert.equal(out.edges.find(e=>e.id==='net_pay-original').amount_minor,72000);assert.ok(out.notes.some(n=>n.includes('unavailable: '+dates[0])));
});

test('incomplete linked transaction pages never replace the original deposit',async()=>{
 const f=fixture(),record='synthetic-0-net_pay',client=fakeAPI(f,(endpoint,q)=>endpoint==='transactions'&&q.record_id===record?{items:f.records[record],total:2}:undefined);
 const out=await loadQuarter(client.api,{...f.raw.range,currency:'CAD',scope:'personal'});
 assert.equal(out.payrollCoverage.linked,5);assert.equal(out.payrollCoverage.complete,false);assert.equal(out.edges.find(e=>e.id==='net_pay-original').amount_minor,72000);assert.ok(out.notes.includes('Linked payroll records incomplete'));
});

test('no payroll evidence has zero payroll summary fields without erasing actual cash totals',()=>{
 const f=fixture(),out=enrichFinance(f.raw);
 for(const key of ['payrollGross','payrollTax','payrollESPP','payrollEmployeeRRSP','payrollEmployerRRSP','payrollNet'])assert.equal(out.summary[key],0);
 assert.equal(out.summary.employeeRRSP,30000);assert.equal(out.summary.employerMatch,30000);assert.equal(out.summary.netPay,432000);assert.equal(out.payrollCoverage.complete,false);
});

test('member and employer components cannot claim the same evidence endpoint within one pay',()=>{
 const f=fixture(),links=f.payrollDetails[0].links,member=links.find(l=>l.code==='rrsp_employee'),employer=links.find(l=>l.code==='rrsp_employer');
 employer.linked_record={...member.linked_record};delete f.records[member.linked_record.record_id][0].category_id;
 const out=enrichFinance(f.raw,{compensation:{...f.compensation,payrollDetails:f.payrollDetails},records:f.records});
 assert.equal(out.edges.flatMap(e=>e.inlineRows||[]).filter(r=>r.record_id===member.linked_record.record_id).length,1);
 assert.equal(out.edges.find(e=>e.payroll_id==='synthetic-0'&&e.id.includes('rrsp_employer')).postingStatus,'unconfirmed');
});

test('each replacement net-pay edge retains both dated payslip evidence and the exact original deposit',()=>{
 const f=fixture(),out=enrichFinance(f.raw,{compensation:{...f.compensation,payrollDetails:f.payrollDetails},records:f.records});
 const edges=out.edges.filter(e=>e.kind==='payroll-net');assert.equal(edges.length,6);
 for(const e of edges){const slip=e.inlineRows.find(r=>r.recordBasis==='Payslip component'),original=f.records[e.payroll_id+'-net_pay'][0];assert.ok(slip);assert.equal(slip.date,e.pay_date);assert.equal(slip.amount_minor,72000);assert.equal(slip.source_id,f.compensation.payroll.find(p=>p.payroll_id===e.payroll_id).source_id);assert.deepEqual(e.inlineRows.find(r=>r.record_id===original.record_id),original);}
});

test('posting date and original row identity must corroborate the linked endpoint before replacing quarter cash',()=>{
 for(const mutate of [
  (f,l)=>f.records[l.linked_record.record_id][0].date='2026-10-08',
  (f,l)=>delete l.linked_record.date,
  (f,l)=>f.records[l.linked_record.record_id].push({...f.records[l.linked_record.record_id][0],amount_minor:0}),
 ]){
  const f=fixture(),link=f.payrollDetails[0].links.find(l=>l.code==='rrsp_employee');mutate(f,link);
  const out=enrichFinance(f.raw,{compensation:{...f.compensation,payrollDetails:f.payrollDetails},records:f.records});
  assert.equal(out.edges.find(e=>e.payroll_id==='synthetic-0'&&e.id.includes('rrsp_employee')).postingStatus,'unconfirmed');
  assert.equal(out.edges.find(e=>e.id==='rrsp_employee-original').amount_minor,10000);
 }
});

test('blank current amounts with explicit zero cash effect are not repeated YTD deductions',()=>{
 const f=fixture();for(const detail of f.payrollDetails)detail.components.push({payroll_id:detail.payroll[0].payroll_id,code:'cpp_employee',kind:'tax',amount_minor:null,cash_effect_minor:0,ytd_minor:123456});
 const out=enrichFinance(f.raw,{compensation:{...f.compensation,payrollDetails:f.payrollDetails},records:f.records});
 assert.equal(out.payrollCoverage.linked,6);assert.equal(out.summary.payrollTax,120000);assert.ok(!out.edges.some(e=>e.inlineRows?.some(r=>r.category_id==='payroll.cpp_employee')));
});

test('each payslip validates its own complete detail coverage and matching source header',()=>{
 for(const mutate of [
  d=>d.coverage.payroll.complete=false,
  d=>d.coverage.components.complete=false,
  d=>d.coverage.links.complete=false,
  d=>d.coverage.components.complete=undefined,
  d=>d.payroll[0].pay_date='2026-06-30',
  d=>d.payroll[0].net_minor++,
  d=>d.components.push({...d.components[0],code:'unknown',cash_effect_minor:null}),
  d=>d.components.push({...d.components[0],cash_effect_minor:0}),
 ]){
  const f=fixture();mutate(f.payrollDetails[0]);
  const out=enrichFinance(f.raw,{compensation:{...f.compensation,payrollDetails:f.payrollDetails},records:f.records});
  assert.equal(out.payrollCoverage.linked,5);assert.equal(out.payrollCoverage.detailed,5);assert.equal(out.payrollCoverage.unavailable,1);assert.equal(out.payrollCoverage.complete,false);
  assert.equal(out.summary.payrollGross,500000);assert.equal(out.edges.find(e=>e.id==='net_pay-original').amount_minor,72000);
  assert.ok(out.notes.some(n=>n.includes(dates[0])&&/incomplete/.test(n)));
 }
});

test('a linked posting is consumed at most once across repeated equal payslips',()=>{
 for(const code of ['net_pay','rrsp_employee','rrsp_employer']){
  const f=fixture();const first=f.payrollDetails[0].links.find(l=>l.code===code),second=f.payrollDetails[1].links.find(l=>l.code===code);second.linked_record={...first.linked_record};
  const out=enrichFinance(f.raw,{compensation:{...f.compensation,payrollDetails:f.payrollDetails},records:f.records});
  const reused=out.edges.flatMap(e=>e.inlineRows||[]).filter(r=>r.record_id===first.linked_record.record_id);assert.equal(reused.length,1,code+' actual posting cannot be reused');
  assert.equal(out.payrollCoverage.linked,code==='net_pay'?5:6);
  if(code!=='net_pay')assert.equal(out.edges.find(e=>e.payroll_id==='synthetic-1'&&e.id.includes(code)).postingStatus,'unconfirmed');
  for(const original of f.raw.edges)assert.equal(out.edges.filter(e=>e.originalId===original.id).reduce((s,e)=>s+e.amount_minor,0),original.amount_minor);
 }
});

test('missing retirement links preserve a full payslip bridge without inventing account credits',()=>{
 const f=fixture();f.payrollDetails[0].links=f.payrollDetails[0].links.filter(l=>l.code==='net_pay');
 const out=enrichFinance(f.raw,{compensation:{...f.compensation,payrollDetails:f.payrollDetails},records:f.records});
 assert.equal(out.payrollCoverage.linked,6);
 const unconfirmed=out.edges.filter(e=>e.payroll_id==='synthetic-0'&&e.postingStatus==='unconfirmed');assert.equal(unconfirmed.length,2);
 for(const e of unconfirmed){assert.equal(e.amount_minor,5000);assert.equal(e.originalId,undefined);assert.equal(e.inlineRows.length,1);assert.equal(e.inlineRows[0].recordBasis,'Payslip component');assert.notEqual(out.nodes.find(n=>n.id===e.target).kind,'account');assert.match(e.evidenceNote,/not confirmed posted/i);}
 const member=out.edges.find(e=>e.id==='rrsp_employee-original');assert.equal(member.amount_minor,10000);assert.ok(!member.excludeRecordIds.includes('synthetic-0-rrsp_employee'));
 assert.equal(out.summary.payrollEmployeeRRSP,30000);assert.equal(out.summary.employeeRRSP,30000);assert.equal(out.summary.payrollEmployerRRSP,30000);
 assert.ok(out.notes.some(n=>/not confirmed posted/i.test(n)));
});

test('September pay remains fully bridged with October retirement postings outside Q3 actual cash',async()=>{
 const f=fixture(),out=await loadQuarter(fakeAPI(f).api,{...f.raw.range,currency:'CAD',scope:'personal'});
 assert.equal(out.payrollCoverage.linked,6);assert.equal(out.payrollCoverage.complete,true);
 const deferred=out.edges.filter(e=>e.postingStatus==='deferred');assert.equal(deferred.length,2);
 for(const e of deferred){
  assert.equal(e.postingDate,'2026-10-08');assert.equal(e.amount_minor,5000);assert.equal(e.originalId,undefined);
  const destination=out.nodes.find(n=>n.id===e.target);assert.notEqual(destination.kind,'account');assert.match(destination.label,/outside selected period/i);
  assert.ok(e.inlineRows.some(r=>r.date==='2026-10-08'&&r.account==='plan'&&r.source_id==='posting-5'));
  assert.ok(e.inlineRows.some(r=>r.date==='2026-09-29'&&r.recordBasis==='Payslip component'));
  assert.match(e.evidenceNote,/2026-10-08/);
 }
 for(const code of ['rrsp_employee','rrsp_employer']){
  const residual=out.edges.find(e=>e.id===code+'-original');assert.equal(residual.amount_minor,5000);assert.equal(residual.excludeRecordIds.length,5);assert.match(residual.displayLabel,/prior\/other payroll/i);assert.deepEqual(residual.selector,f.raw.edges.find(e=>e.id===residual.id).selector);
 }
 for(const original of f.raw.edges)assert.equal(out.edges.filter(e=>e.originalId===original.id).reduce((s,e)=>s+e.amount_minor,0),original.amount_minor);
 assert.equal(out.summary.employeeRRSP,30000);assert.equal(out.summary.employerMatch,30000);assert.equal(out.summary.payrollEmployerRRSP,30000);assert.equal(out.summary.payrollGross,600000);
 const hub=out.nodes.find(n=>n.kind==='payroll');assert.equal(out.edges.filter(e=>e.source===hub.id).reduce((s,e)=>s+e.amount_minor,0),600000);
 assert.equal(out.edges.filter(e=>e.target==='plan').reduce((s,e)=>s+e.amount_minor,0),60000,'future evidence is never an actual account inflow');
});

test('all six current payslips share a balanced hub and component nodes while original quarter cash is conserved',()=>{
 const f=fixture();for(const link of f.payrollDetails.at(-1).links){link.linked_record.date=dates.at(-1);for(const row of f.records[link.linked_record.record_id])row.date=dates.at(-1);}
 const before=structuredClone(f.raw);
 const out=enrichFinance(f.raw,{compensation:{...f.compensation,payrollDetails:f.payrollDetails},records:f.records});
 assert.equal(out.payrollCoverage.linked,6);assert.equal(out.payrollCoverage.detailed,6);assert.equal(out.payrollCoverage.complete,true);
 assert.deepEqual(f.raw,before,'projection never mutates original ledger objects');
 const hubs=out.nodes.filter(n=>n.kind==='payroll');assert.equal(hubs.length,1);assert.equal(hubs[0].label,'Payroll · selected period');
 for(const label of ['Gross salary','Tax withheld','ESPP withheld'])assert.equal(out.nodes.filter(n=>n.label===label).length,1,label+' is shared');
 assert.equal(out.edges.filter(e=>e.target===hubs[0].id).reduce((s,e)=>s+e.amount_minor,0),600000);
 assert.equal(out.edges.filter(e=>e.source===hubs[0].id).reduce((s,e)=>s+e.amount_minor,0),600000);
 for(const original of before.edges)assert.equal(out.edges.filter(e=>e.originalId===original.id).reduce((s,e)=>s+e.amount_minor,0),original.amount_minor);
 assert.equal(out.summary.payrollGross,600000);assert.equal(out.summary.payrollNet,432000);assert.equal(out.summary.payrollTax,120000);assert.equal(out.summary.payrollESPP,18000);assert.equal(out.summary.payrollEmployeeRRSP,30000);assert.equal(out.summary.payrollEmployerCompensation,30000);
 assert.equal(out.summary.income,432000);assert.equal(out.summary.netPay,432000);assert.equal(out.summary.employeeRRSP,30000);assert.equal(out.summary.employerMatch,30000);
 const model=createModel(out),projection=model.project(),gross=projection.edges.find(e=>projection.nodes.find(n=>n.id===e.source)?.label==='Gross salary');assert.equal(gross.members.length,6);assert.equal(gross.amount,600000);
 const evidence=selectionMembers(projection,{edges:[gross.id]}).flatMap(e=>e.inlineRows||[]);assert.deepEqual(evidence.map(r=>r.date).sort(),dates);assert.equal(new Set(evidence.map(r=>r.source_id)).size,6);assert.ok(evidence.every(r=>r.ytd_minor===9999999&&r.amount_minor===100000),'YTD retained as nonadditive evidence');
});
