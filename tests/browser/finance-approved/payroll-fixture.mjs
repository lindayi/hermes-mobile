import {flow,detail} from './fixture.mjs';
export const dates=['2026-07-15','2026-07-31','2026-08-14','2026-08-31','2026-09-15','2026-09-29'];
export function payrollService(){
 const r=structuredClone(flow),records=[],pays=[];
 r.nodes.push({id:'plan',kind:'account',account:'Example_RRSP',label:'Example RRSP'},{id:'member',kind:'transfer',label:'Member'},{id:'employer',kind:'transfer',label:'Employer'});
 const net=r.edges.find(e=>e.id==='salary1');net.amount_minor=424000;net.count=6;net.selector.filters.category='income.employment.payroll';
 for(const [id,category]of [['member','transfer.contribution.member'],['employer','transfer.contribution.employer']])r.edges.push({id:id+'-flow',source:id,target:'plan',kind:'transfer',amount_minor:30000,currency:'CAD',count:6,selector:{endpoint:'transactions',filters:{...net.selector.filters,account:'Example_RRSP',category,flow:'transfer',direction:'incoming'}}});
 const postingDates=['2026-07-20','2026-08-05','2026-08-19','2026-09-04','2026-09-21','2026-10-08'];
 for(let i=0;i<6;i++){
  const date=dates[i],pid='Example:'+date,netAmount=i===1?74000:70000;
  const comps=[['salary','earning',100000,100000],['income_tax_regular','tax',20000,-20000],['rrsp_employee','employee_saving',5000,-5000],['espp','employee_saving',3000,-3000],['health_dental','employee_deduction',2000,-2000],['rrsp_employer','employer_benefit',5000,0],...(i===1?[['fitness_reimbursement','reimbursement',4000,4000]]:[])].map(([code,kind,amount_minor,cash_effect_minor])=>({payroll_id:pid,code,kind,amount_minor,cash_effect_minor,ytd_minor:null}));
  const links=[['net_pay','Checking',netAmount,date,'income.employment.payroll'],['rrsp_employee','Example_RRSP',5000,postingDates[i],'transfer.contribution.member'],['rrsp_employer','Example_RRSP',5000,postingDates[i],'transfer.contribution.employer']].map(([code,account,amount,posted,category])=>{
   const record_id=pid+':'+code;records.push({record_id,entry_id:record_id,date:posted,account,currency:'CAD',amount_minor:amount,category_id:category,description:'Synthetic '+code+' for '+date,drive_url:'https://drive.google.com/file/d/synthetic/view'});
   return {payroll_id:pid,code,status:code==='net_pay'?'amount_date_payee_match_routing_unresolved':'probable_timing_amount_ytd',evidence:'Synthetic source link',linked_record:{record_id,account,currency:'CAD',date:posted,dataset:code==='net_pay'?'bank':'investment'}};
  });
  pays.push({pay:{payroll_id:pid,pay_date:date,period_end:date,currency:'CAD',gross_minor:100000,net_minor:netAmount,net_difference_minor:0,source_id:'synthetic-'+i,drive_url:'https://drive.google.com/file/d/synthetic/view'},components:comps,links});
 }
 for(const code of ['member','employer'])records.push({record_id:'prior-'+code,entry_id:'prior-'+code,account:'Example_RRSP',currency:'CAD',date:'2026-07-06',amount_minor:5000,category_id:'transfer.contribution.'+code,description:'Prior payroll posting'});
 const empty={payroll:[],components:[],links:[],coverage:{payroll:{complete:true},components:{complete:true},links:{complete:true}}};
 return {r,pays,records,api:async(endpoint,q)=>{
  if(endpoint==='flow')return q.currency==='USD'?{...r,nodes:[],edges:[],currency:'USD'}:r;
  if(endpoint==='investments')return {balances:q.currency==='USD'?[]:[{account:'Example_RRSP',institution:'Example',registration:'RRSP'}],cashflows:[],history:[],coverage:{balances:{complete:true},cashflows:{complete:true},history:{complete:true}}};
  if(endpoint==='quotes')return {fx:{pair:'USDCAD',rate:1.4,as_of:'2026-10-20',source:'bankofcanada',status:'ok'}};
  if(endpoint==='compensation'){
   if(q.currency==='USD')return empty;const selected=pays.filter(p=>p.pay.pay_date>=q.start&&p.pay.pay_date<=q.end).reverse(),latest=selected[0];
   return {payroll:selected.map(p=>p.pay),components:latest?.components||[],links:latest?.links||[],coverage:{...empty.coverage,selected_payroll_id:latest?.pay.payroll_id}};
  }
  if(endpoint==='transactions'){
   const items=records.filter(r=>r.date>=q.start&&r.date<=q.end&&(!q.record_id||r.record_id===q.record_id)&&(!q.category||r.category_id===q.category)&&(!q.account||r.account===q.account));
   if(items.length||q.record_id||q.account==='Example_RRSP')return {items,total:items.length};
  }
  return detail(endpoint,new URLSearchParams(q));
 }};
}
