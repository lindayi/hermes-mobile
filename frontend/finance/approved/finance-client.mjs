export function lastQuarter(now=new Date()){
 const parts=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'America/Toronto',year:'numeric',month:'numeric'}).formatToParts(now).map(p=>[p.type,p.value]));
 const year=Number(parts.year),month=Number(parts.month),quarterStart=Math.floor((month-1)/3)*3;
 return {start:new Date(Date.UTC(year,quarterStart-3,1)).toISOString().slice(0,10),end:new Date(Date.UTC(year,quarterStart,0)).toISOString().slice(0,10)};
}
const allowed=new Set(['start','end','currency','scope','dataset','account','category','category_mode','direction','source_account','target_account','link_type','transfer_status','flow','record_id','q','limit','offset','vesting_policy','payroll_id']);
export function makeAPI(fetcher=globalThis.fetch.bind(globalThis)){
 return async(endpoint,query={},signal)=>{
  if(!['flow','transactions','transfers','compensation','investments','quotes'].includes(endpoint))throw Error('Invalid finance endpoint');
  const params=new URLSearchParams(Object.entries(query).filter(([k,v])=>(endpoint==='quotes'?k==='symbols':allowed.has(k))&&v!=null&&(v!==''||(k==='category'&&query.category_mode==='exact'))));
  const timeout=AbortSignal.timeout(20000),combined=signal?AbortSignal.any([signal,timeout]):timeout;
  const response=await fetcher('/hermes/app-api/finance/'+endpoint+'?'+params,{credentials:'same-origin',cache:'no-store',signal:combined});
  if(!response.ok){const error=new Error([401,403].includes(response.status)?'Sign in to view finance':'Finance request failed ('+response.status+')');error.status=response.status;throw error;}
  return response.json();
 };
}
function transaction(item,streamEvidenceNote=''){
 if(!item||typeof item!=='object'||!Number.isSafeInteger(item.amount_minor)||!['CAD','USD'].includes(item.currency))throw Error('Invalid transaction page');
 const id=item.entry_id||item.record_id;if(typeof id!=='string'||!id)throw Error('Missing transaction identity');
 return {id:(item.entry_id?'entry:':'record:')+id,date:item.date,account:item.account,amount:item.amount_minor,currency:item.currency,description:item.description||item.merchant||'Transaction',category:item.category_id||'',source:item.source_id,page:item.page,url:item.drive_url,record_id:item.record_id,recordBasis:item.recordBasis||'',evidence:item.evidence??item.rationale??'',confidence:item.confidence??null,allocation:item.allocation,current_planning_protected:item.current_planning_protected,planning_protected:item.planning_protected,evidenceNote:item.evidenceNote||'',streamEvidenceNote};
}
export function createPager(members,api,account=''){
 const selectedAccounts=account?new Set(Array.isArray(account)?account:[account]):null;
 const accepts=item=>!selectedAccounts||selectedAccounts.has(item.account);
 const streams=[...new Map(members.map(m=>[m.id,m])).values()],states=streams.map(()=>({offset:0,total:null,restarts:0,records:new Map()}));let index=0,busy=false;
 const result={rows:[],done:streams.length===0};
 const publishRows=()=>{
  const records=new Map();
  for(const state of states)for(const [key,row] of state.records)if(!records.has(key))records.set(key,row);
  result.rows=[...records.values()].sort((a,b)=>String(b.date).localeCompare(String(a.date))||a.id.localeCompare(b.id));
 };
 result.more=async function more(signal){
  if(busy||result.done)return;busy=true;
  try{for(let requests=0;requests<3&&index<streams.length;requests++){
   signal?.throwIfAborted();const stream=streams[index],state=states[index],selector=stream.selector;
   if(Array.isArray(stream.inlineRows)){for(const item of stream.inlineRows.filter(accepts)){const row=transaction(item,stream.evidenceNote||'');state.records.set(row.id+'|'+row.account,row);}index++;publishRows();result.done=index>=streams.length;continue;}
   if(!selector||!['transactions','transfers'].includes(selector.endpoint))throw Error('Unavailable stream selector');
   const data=await api(selector.endpoint,{...selector.filters,limit:50,offset:state.offset},signal);signal?.throwIfAborted();
   if(!Array.isArray(data.items)||!Number.isSafeInteger(data.total)||data.total<0)throw Error('Incomplete transaction page');
   if(state.total!==null&&data.total!==state.total){
    state.restarts++;state.records.clear();state.offset=0;state.total=data.total;publishRows();
    if(state.restarts>2)throw Error('Evidence total changed repeatedly; retry required');
    continue;
   }
   if(state.total===null)state.total=data.total;
   if(!data.items.length&&state.offset<data.total)throw Error('Incomplete transaction page');
   if(state.offset+data.items.length>data.total)throw Error('Incomplete transaction page');
   const added=[];
   for(const item of data.items){
    if(selector.endpoint==='transactions'){if(!stream.excludeRecordIds?.includes(item.record_id)&&accepts(item))added.push(transaction(item,stream.evidenceNote||''));}
    else for(const side of ['debit','credit'])if(item[side]&&accepts(item[side]))added.push({...transaction(item[side],stream.evidenceNote||''),description:'Transfer · '+String(item.link_type||'').replaceAll('_',' '),transfer:item.link_id,side,counterparty:item[side==='debit'?'credit':'debit']?.account,transferEvidence:item.evidence??item.rationale??'',transferConfidence:item.confidence??null,transferProtectedBoundary:item.protected_boundary});
   }
   for(const row of added)state.records.set(row.id+'|'+row.account,row);
   state.offset+=data.items.length;if(state.offset>=data.total)index++;
   publishRows();result.done=index>=streams.length;
  }}finally{busy=false;}
 };return result;
}
export function evidenceURL(value){try{const u=new URL(value);return u.protocol==='https:'&&(!u.port||u.port==='443')&&['drive.google.com','mail.google.com'].includes(u.hostname)&&!u.username&&!u.password?u.href:null;}catch{return null;}}
