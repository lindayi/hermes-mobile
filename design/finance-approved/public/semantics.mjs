import {enrichProducts} from './products.mjs';
// Evidence-based presentation only. No ledger mutations or private account literals.
export async function loadQuarter(api,query,signal){
 const unavailable=[];
 async function supplement(endpoint){try{return await api(endpoint,query,signal);}catch(e){if([401,403].includes(e.status)||e.name==='AbortError')throw e;unavailable.push(endpoint+' detail unavailable');return {};}}
 const [raw,investments,compensation]=await Promise.all([api('flow',query,signal),supplement('investments'),supplement('compensation')]);signal?.throwIfAborted();
 if(!Array.isArray(raw.nodes)||!Array.isArray(raw.edges)||raw.currency!==query.currency||raw.range?.start!==query.start||raw.range?.end!==query.end)throw Error('Unexpected finance period');
 const pays=(compensation.payroll||[]).filter(p=>p.currency===raw.currency&&inRange(p.pay_date,raw.range));
 const payrollDetails=await boundedMap(pays,async p=>{
  if(p.payroll_id===compensation.coverage?.selected_payroll_id)return compensation;
  try{const detail=await api('compensation',{...query,start:p.pay_date,end:p.pay_date},signal);signal?.throwIfAborted();return detail;}
  catch(e){if([401,403].includes(e.status)||e.name==='AbortError')throw e;unavailable.push('Payslip detail unavailable: '+p.pay_date);return {};}
 },signal);
 const records={},refs=new Map();
 for(const detail of payrollDetails)for(const link of (detail.links||[]).filter(l=>l.payroll_id===detail.coverage?.selected_payroll_id&&['net_pay','rrsp_employee','rrsp_employer'].includes(l.code))){
  const ref=link.linked_record;if(ref?.record_id&&ref.currency===query.currency&&ref.date&&ref.account)refs.set(ref.record_id,ref);
 }
 await boundedMap([...refs.values()],async ref=>{
  try{const data=await api('transactions',{...query,start:ref.date,end:ref.date,record_id:ref.record_id,account:ref.account,dataset:ref.dataset||'all',limit:100,offset:0},signal);signal?.throwIfAborted();if(Array.isArray(data.items)&&data.total===data.items.length)records[ref.record_id]=data.items;else unavailable.push('Linked payroll records incomplete');}
  catch(e){if([401,403].includes(e.status)||e.name==='AbortError')throw e;unavailable.push('Linked payroll record unavailable');}
 },signal);
 const result=enrichFinance(raw,{investments,compensation:{...compensation,payrollDetails},records});result.notes.push(...new Set(unavailable));
 for(const [name,data,keys] of [['Investment',investments,['balances','cashflows']],['Payroll',compensation,['payroll','components','links']]])if(keys.some(k=>data.coverage?.[k]?.complete===false))result.notes.push(name+' detail partially recorded.');
 return enrichProducts(result,{investments});
}
// One shared pool per phase; never fan out an unbounded number of detail requests.
async function boundedMap(items,work,signal){
 const result=new Array(items.length);let next=0,failed=false;
 await Promise.all(Array.from({length:Math.min(3,items.length)},async()=>{
  while(!failed&&next<items.length){signal?.throwIfAborted();const index=next++;try{result[index]=await work(items[index]);}catch(error){failed=true;throw error;}}
 }));signal?.throwIfAborted();return result;
}
const id=(...p)=>JSON.stringify(['semantic',...p]);
const inRange=(date,range)=>typeof date==='string'&&date.slice(0,10)>=range.start&&date.slice(0,10)<=range.end;
const role=(n,investmentAccounts)=>{
 if(investmentAccounts.has(n.account))return 'investments';
 const name=String(n.account||n.label||'').replaceAll('_',' ').toLowerCase();
 if(/\b(amex|visa|mastercard|credit card|cobalt|simplycash)\b/.test(name))return 'cards';
 if(/\b(rrsp|tfsa|brokerage|rrif|investment|espp)\b/.test(name))return 'investments';
 if(/\b(mortgage|heloc|loan)\b/.test(name))return 'debt';
 if(/\b(chequing|checking|savings|allinclusive|all inclusive)\b/.test(name))return 'banking';
 return 'other';
};
const cleanName=name=>String(name).replaceAll('_',' ').replace(/\bAllInclusive\b/g,'All-Inclusive');
export function enrichFinance(input,{investments={},compensation={},records={}}={}){
 const raw=structuredClone(input);raw.notes=[];raw.payrollCoverage={count:0,linked:0};
 const metadata=new Map();for(const b of [...(investments.balances||[]),...(investments.history||[])])if(!metadata.has(b.account))metadata.set(b.account,b);
 const investmentAccounts=new Set([...metadata.keys(),...(investments.cashflows||[]).map(r=>r.account)]);
 const existingAccounts=new Set(raw.nodes.filter(n=>n.kind==='account').map(n=>n.account));
 for(const observation of investments.history||[]){if(!existingAccounts.has(observation.account)&&inRange(observation.as_of,raw.range)&&Number.isSafeInteger(observation.market_value_minor)&&observation.market_value_minor>0){raw.nodes.push({id:id('quiet-account',observation.account),account:observation.account,label:observation.account,kind:'account',noPeriodFlows:true});existingAccounts.add(observation.account);}}
 for(const n of raw.nodes.filter(n=>n.kind==='account')){
  const b=metadata.get(n.account);n.accountGroup=role(n,investmentAccounts);n.label=cleanName(n.label);
  if(b){n.label=[b.institution,b.registration==='nonregistered'?'Non-registered':b.registration,String(n.account).replaceAll('_',' ').split(' ').at(-1)].filter(Boolean).join(' · ');n.institution=b.institution;n.registration=b.registration;}
 }
 const accounts=new Map(raw.nodes.filter(n=>n.kind==='account').map(n=>[n.id,n]));
 const summary={income:0,spending:0,netPay:0,investmentIncome:0,employerMatch:0,employeeRRSP:0,mortgage:0,taxWithheld:0};
 for(const e of raw.edges){
  e.originalId=e.id;e.originalAmount=e.amount_minor;
  const incoming=accounts.has(e.target)&&!accounts.has(e.source),outgoing=accounts.has(e.source)&&!accounts.has(e.target),c=e.selector?.filters?.category||'';
  if(e.kind==='income')summary.income+=(incoming?1:outgoing?-1:0)*e.amount_minor;
  if(e.kind==='expense')summary.spending+=(outgoing?1:incoming?-1:0)*e.amount_minor;
  if(c==='income.employment.payroll'&&incoming)summary.netPay+=e.amount_minor;
  if(c.startsWith('investment.return.')&&incoming){e.displayKind='investment-income';e.displayLabel=c.endsWith('.interest')?'Investment interest':'Dividends & distributions';summary.investmentIncome+=e.amount_minor;}
  if(c==='transfer.contribution.employer'&&incoming){e.displayKind='employer-match';e.displayLabel='Employer RRSP match';summary.employerMatch+=e.amount_minor;}
  if(c==='transfer.contribution.member'&&incoming){e.displayKind='payroll-saving';e.displayLabel='Employee RRSP · payroll withheld';summary.employeeRRSP+=e.amount_minor;}
  if(c.startsWith('investment.trade.')){e.displayKind='portfolio-trade';e.displayLabel=c.endsWith('.buy')?'Investment purchases':c.endsWith('.sell')?'Investment sale proceeds':'Portfolio activity';}
  if(c==='financing.mortgage.payment'&&outgoing){e.displayLabel='Mortgage payments';summary.mortgage+=e.amount_minor;}
  if(e.kind==='expense'&&incoming){e.displayKind='recovery';const family=c.split('.')[1]||'Expense';const title=/fees|bank_fees|card_fees/.test(c)?'Fee refunds':family==='health'?'Health reimbursements':family.charAt(0).toUpperCase()+family.slice(1)+' recoveries';e.displayLabel=title+' · '+accounts.get(e.target).label;}
  if(c==='income.employment.payroll'&&incoming)e.displayLabel='Payroll deposits';
  if(c.startsWith('transfer.unallocated.'))e.displayLabel=incoming?'Unidentified money received':'Unidentified money sent';
 }
 summary.net=summary.income-summary.spending;raw.summary=summary;
 applyPayroll(raw,compensation,records);
 return raw;
}
function applyPayroll(raw,compensation,records){
 const pays=[...new Map((compensation.payroll||[]).filter(p=>p.currency===raw.currency&&inRange(p.pay_date,raw.range)).map(p=>[p.payroll_id,p])).values()];
 const coverage=raw.payrollCoverage={count:pays.length,linked:0,detailed:0,unavailable:0,complete:false};
 Object.assign(raw.summary,{payrollGross:0,payrollNet:0,payrollTax:0,payrollESPP:0,payrollEmployeeRRSP:0,payrollEmployerRRSP:0,payrollEmployerCompensation:0});
 if(!pays.length){raw.notes.push('No payslip breakdown recorded for this period.');return;}
 const details=compensation.payrollDetails||[compensation],accountNodes=raw.nodes.filter(n=>n.kind==='account'),usedRecords=new Set();
 const hub={id:id('payroll',raw.range.start,raw.range.end),kind:'payroll',label:pays.length===1?'Payroll · '+pays[0].pay_date:'Payroll · selected period',semantic:true,...(pays.length===1?{payroll_id:pays[0].payroll_id}:{})};
 function node(key,kind,label){const ident=id(...key);if(!raw.nodes.some(n=>n.id===ident))raw.nodes.push({id:ident,kind,label,semantic:true});return ident;}
 for(const p of pays){
  const detail=details.find(d=>d.coverage?.selected_payroll_id===p.payroll_id);
  const components=(detail?.components||[]).filter(c=>c.payroll_id===p.payroll_id),links=(detail?.links||[]).filter(l=>l.payroll_id===p.payroll_id);
  const cashSum=components.reduce((s,c)=>s+(Number.isSafeInteger(c.cash_effect_minor)?c.cash_effect_minor:0),0);
  const selected=(detail?.payroll||[]).find(row=>row.payroll_id===p.payroll_id);
  const sameHeader=selected&&['pay_date','currency','gross_minor','net_minor','net_difference_minor','source_id'].every(key=>selected[key]===p[key]);
  const validComponents=components.length>0&&components.every(c=>typeof c.code==='string'&&Number.isSafeInteger(c.cash_effect_minor)&&(Number.isSafeInteger(c.amount_minor)||(c.amount_minor===null&&c.cash_effect_minor===0)))&&new Set(components.map(c=>c.code)).size===components.length;
  if(!sameHeader||!validComponents||['payroll','components','links'].some(key=>detail?.coverage?.[key]?.complete!==true)||p.net_difference_minor!==0||!Number.isSafeInteger(p.net_minor)||!Number.isSafeInteger(p.gross_minor)||cashSum!==p.net_minor){raw.notes.push('Payslip breakdown unavailable or incomplete: '+p.pay_date+'; bank net pay retained.');continue;}
  coverage.detailed++;
  raw.summary.payrollGross+=p.gross_minor;raw.summary.payrollNet+=p.net_minor;
  for(const c of components){
   if(c.kind==='tax'&&Number.isSafeInteger(c.cash_effect_minor))raw.summary.payrollTax-=c.cash_effect_minor;
   if(c.code==='espp'&&Number.isSafeInteger(c.cash_effect_minor))raw.summary.payrollESPP-=c.cash_effect_minor;
   if(c.code==='rrsp_employee'&&Number.isSafeInteger(c.cash_effect_minor))raw.summary.payrollEmployeeRRSP-=c.cash_effect_minor;
   if(c.code==='rrsp_employer'&&Number.isSafeInteger(c.amount_minor))raw.summary.payrollEmployerRRSP+=c.amount_minor;
  }
  function match(code,amount,category){
   const link=links.find(l=>l.code===code),ref=link?.linked_record,rows=records[ref?.record_id];
   if(!ref||typeof ref.date!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(ref.date)||usedRecords.has(ref.record_id)||ref.currency!==raw.currency||!Array.isArray(rows)||!rows.length)return null;
   if(new Set(rows.map(r=>r.entry_id||r.record_id)).size!==rows.length)return null;
   if(rows.some(r=>r.record_id!==ref.record_id||r.account!==ref.account||r.date!==ref.date||r.currency!==raw.currency||!Number.isSafeInteger(r.amount_minor)||r.category_id&&r.category_id!==category)||rows.reduce((s,r)=>s+r.amount_minor,0)!==amount)return null;
   if(!inRange(ref.date,raw.range))return code==='net_pay'?null:{link,ref,rows,deferred:true};
   const node=accountNodes.find(n=>n.account===ref.account),edges=raw.edges.filter(e=>e.target===node?.id&&e.selector?.filters?.category===category&&e.amount_minor>=amount);
   return edges.length===1?{link,ref,rows,node,edge:edges[0]}:null;
  }
  const net=match('net_pay',p.net_minor,'income.employment.payroll');
  const member=components.find(c=>c.code==='rrsp_employee'&&c.cash_effect_minor<0),employer=components.find(c=>c.code==='rrsp_employer'&&c.amount_minor>0);
  const rrsp=member?match('rrsp_employee',-member.cash_effect_minor,'transfer.contribution.member'):null;
  const employerLink=employer?match('rrsp_employer',employer.amount_minor,'transfer.contribution.employer'):null;
  if(!net){raw.notes.push('Payslip is available: '+p.pay_date+'; its deposit/contribution links need confirmation before drawing the bridge.');continue;}
  if(!raw.nodes.some(n=>n.id===hub.id))raw.nodes.push(hub);
  const payrow=(c,amount=c.cash_effect_minor)=>({entry_id:id('payslip-row',p.payroll_id,c.code),record_id:id('payslip',p.payroll_id,c.code),date:p.pay_date,account:'Payroll',currency:p.currency,amount_minor:amount,description:codeLabel(c.code)+' · payslip component',category_id:'payroll.'+c.code,drive_url:p.drive_url,source_id:p.source_id,recordBasis:'Payslip component',ytd_minor:c.ytd_minor});
  function edge(key,source,target,amount,kind,rows,extra={}){raw.edges.push({id:id('payroll-edge',p.payroll_id,key),source,target,kind,displayKind:kind,amount_minor:amount,currency:p.currency,count:1,inlineRows:rows,payrollBridge:true,payroll_id:p.payroll_id,pay_date:p.pay_date,...extra});}
  function replace(key,matched,from,kind,c){
   usedRecords.add(matched.ref.record_id);
   const amount=matched.rows.reduce((s,r)=>s+r.amount_minor,0);
   matched.edge.amount_minor-=amount;matched.edge.count=Math.max(0,matched.edge.count-1);matched.edge.excludeRecordIds=[...(matched.edge.excludeRecordIds||[]),matched.ref.record_id];
   edge(key,from,matched.node.id,amount,kind,[...(c?[payrow(c,c.code==='rrsp_employer'?c.amount_minor:c.cash_effect_minor)]:[]),...matched.rows],{originalId:matched.edge.originalId,originalAmount:matched.edge.originalAmount,evidenceNote:matched.link.status+' · '+(matched.link.evidence||'')});
  }
  function retirement(c,matched,from,kind){
   if(matched&&usedRecords.has(matched.ref.record_id))matched=null;
   if(!matched){
    const amount=c.code==='rrsp_employer'?c.amount_minor:-c.cash_effect_minor;
    const label=codeLabel(c.code)+(c.code==='rrsp_employee'?' · withheld, not confirmed posted':' · compensation, not confirmed posted');
    edge(c.code,from,node(['payroll-posting',c.code,'unconfirmed'],'payroll-unconfirmed',label),amount,kind,[payrow(c,c.code==='rrsp_employer'?c.amount_minor:c.cash_effect_minor)],{postingStatus:'unconfirmed',evidenceNote:'Payslip evidence only; not confirmed posted. No account credit inferred.'});
    raw.notes.push('Retirement amounts on payslips are not confirmed posted where posting evidence is missing.');return;
   }
   if(!matched.deferred){replace(c.code,matched,from,kind,c);return;}
   const amount=c.code==='rrsp_employer'?c.amount_minor:-c.cash_effect_minor;
   usedRecords.add(matched.ref.record_id);
   const target=node(['payroll-posting',c.code,'deferred'],'payroll-deferred',codeLabel(c.code)+' · posted outside selected period');
   edge(c.code,from,target,amount,kind,[payrow(c,c.code==='rrsp_employer'?c.amount_minor:c.cash_effect_minor),...matched.rows],{postingStatus:'deferred',postingDate:matched.ref.date,evidenceNote:'Posted '+matched.ref.date+' outside selected period; not selected-period cash. '+matched.link.status+' · '+(matched.link.evidence||'')});
  }
  for(const c of components.filter(c=>Number.isSafeInteger(c.cash_effect_minor)&&c.cash_effect_minor!==0)){
   const value=c.cash_effect_minor;
   if(value>0){const kind=c.kind==='reimbursement'?'reimbursement':'income';edge(c.code,node(['payroll-source',c.code],kind,codeLabel(c.code)),hub.id,value,kind,[payrow(c)]);}
   else if(c.code==='rrsp_employee'){retirement(c,rrsp,hub.id,'payroll-saving');}
   else {const kind=c.kind==='tax'?'tax':c.kind==='employee_saving'?'payroll-saving':'payroll-deduction';edge(c.code,hub.id,node(['payroll-destination',c.code],kind,codeLabel(c.code)),-value,kind,[payrow(c)]);}
  }
  replace('net-pay',net,hub.id,'payroll-net',{...components.find(c=>c.code==='net_pay'),code:'net_pay',cash_effect_minor:p.net_minor});net.edge.displayLabel='Payroll deposits · other pays';
  if(employer)retirement(employer,employerLink,node(['employer-match'],'income','Employer RRSP match'),'employer-match');
  coverage.linked++;
  if([net,rrsp,employerLink].some(m=>m&&/probable|unresolved/.test(m.link.status)))raw.notes.push('Payroll links retain their recorded uncertainty; select a ribbon for evidence.');
 }
 raw.summary.payrollEmployerCompensation=raw.summary.payrollEmployerRRSP;
 raw.summary.taxWithheld=raw.summary.payrollTax;
 coverage.unavailable=coverage.count-coverage.detailed;
 coverage.complete=compensation.coverage?.payroll?.complete===true&&coverage.linked===coverage.count&&coverage.detailed===coverage.count;
 for(const e of raw.edges){
  if(e.selector?.filters?.category==='transfer.contribution.member')e.displayLabel='Employee RRSP · prior/other payroll (posted)';
  if(e.selector?.filters?.category==='transfer.contribution.employer')e.displayLabel='Employer RRSP · prior/other payroll (posted)';
 }
 raw.edges=raw.edges.filter(e=>e.amount_minor>0);
 raw.notes.push('Payslip detail: '+coverage.linked+' of '+coverage.count+' selected-period pays. Retirement posting dates may differ.');
 raw.notes=[...new Set(raw.notes)];
}
function codeLabel(code){return ({salary:'Gross salary',espp:'ESPP withheld',rrsp_employee:'Employee RRSP · pre-tax',rrsp_employer:'Employer RRSP match',income_tax_regular:'Income tax withheld',income_tax_nonperiodic:'Nonperiodic tax withheld',fitness_reimbursement:'Fitness reimbursement',ltd_insurance:'LTD insurance',health_dental:'Health & dental',tax:'Tax withheld'})[code]||String(code).replaceAll('_',' ');}
