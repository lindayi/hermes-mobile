export const finite = value => typeof value === 'number' && Number.isFinite(value);
export function reportedReturn(value,method) {
 const n=typeof value==='string'&&value.length<=32&&/^-?[0-9]+(?:\.[0-9]+)?$/.test(value)?Number(value):value;
 return finite(n)?`${n}% (${method||'method unavailable'})`:'Unavailable';
}
export function money(value,currency) {
 if(!finite(value)||!['CAD','USD'].includes(currency))return 'Unavailable';
 return new Intl.NumberFormat('en-CA',{style:'currency',currency,currencyDisplay:'code',maximumFractionDigits:2}).format(value/100);
}
export function initialRange(range={}) {return /^\d{4}-\d{2}-\d{2}$/.test(range.end||'')?{start:range.end.slice(0,4)+'-01-01',end:range.end}:{start:'',end:''};}
export function evidenceURL(value) {
 try {const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password&&(!u.port||u.port==='443')&&['drive.google.com','mail.google.com'].includes(u.hostname)?u.href:null;}catch{return null;}
}
export function quoteEstimate(holding,quote) {
 const raw=holding.quantity;
 const quantity=typeof raw==='string'&&/^(?:0|[1-9][0-9]{0,12})(?:\.[0-9]{1,12})?$/.test(raw)?Number(raw):raw;
 if(!quoteSymbol(holding)||!finite(quantity)||quantity<0||quantity>1e12||!quote||!finite(quote.price)||quote.price<=0||quote.currency!==holding.currency||quote.stale||!['ok','fresh'].includes(quote.status))return null;
 const value=Math.round(quantity*quote.price*100);return Number.isSafeInteger(value)?value:null;
}
export function recordedPosition(data,currency) {
 const selected=key=>(Array.isArray(data[key])?data[key]:[]).filter(r=>r.currency===currency);
 const investmentRows=selected('balances'),cashRows=selected('bank_balances'),debtRows=selected('liabilities');
 const value=r=>r.balance_minor??r.amount_minor;
 const sum=(rows,get)=>rows.length&&rows.every(r=>finite(get(r)))?rows.reduce((n,r)=>n+get(r),0):null;
 const investments=sum(investmentRows,r=>r.market_value_minor),cash=sum(cashRows,value),debt=sum(debtRows,value);
 const protection=r=>r.planning_protected??r.current_planning_protected;
 // Match scope_sql: case-insensitive mother% / family%, without trimming or a delimiter requirement.
 const family=r=>/^(mother|family)/i.test(r.allocation??'');
 const known=r=>family(r)||[0,1,false,true].includes(protection(r));
 const protectedRow=r=>family(r)||protection(r)===1||protection(r)===true;
 const liquid=cashRows.length&&cashRows.every(r=>known(r)&&finite(value(r)))?cashRows.filter(r=>!protectedRow(r)).reduce((n,r)=>n+value(r),0):null;
 const all=[...investmentRows.map(r=>({...r,position_value:r.market_value_minor})),...cashRows.map(r=>({...r,position_value:value(r)}))];
 const restricted=all.length&&all.every(r=>known(r)&&finite(r.position_value))?all.filter(protectedRow).reduce((n,r)=>n+r.position_value,0):null;
 return {investments,cash,liquid,restricted,debt,net:[investments,cash,debt].every(finite)?investments+cash-debt:null};
}
export function quoteSymbol(holding) {
 if(!['share','shares','units'].includes(String(holding.quantity_basis).toLowerCase()))return null;
 const value=holding.symbol||holding.security_key;
 if(value==='MSFT')return value;
 for(const ticker of ['TGED','TPE','TPU','TTP','TDB'])if(value===ticker||value===ticker+'.TO')return ticker+'.TO';
 return null;
}
export function categoryLabel(id,labels=[]) {
 const names=[...new Set(labels.filter(x=>x.category_id===id).map(x=>String(x.label||'').trim()).filter(x=>x&&!/^(whole|split|unknown|unclassified)$/i.test(x)))];
 if(names.length===1&&!/^(bank|payment|unspecified|other)$/i.test(names[0]))return names[0];
 const name=String(id||'Unresolved').replace(/^(income|expense|investment|transfer|financing|adjustment)[.:/]/i,'').replace(/[.:/]+/g,' / ').replace(/[_-]+/g,' ');
 return name.charAt(0).toUpperCase()+name.slice(1);
}
export function flowRows(categories=[],labels=[]) {
 return categories.map(x=>{
  const prefix=String(x.category_id||'').split(/[.:/]/)[0].toLowerCase();
  const flow=x.flow||(['expense','income','transfer','investment','financing','adjustment'].includes(prefix)?prefix:'unresolved');
  const value=flow==='expense'?(finite(x.expense_minor)?x.expense_minor:finite(x.amount_minor)?-x.amount_minor:null):flow==='income'&&finite(x.income_minor)?x.income_minor:x.amount_minor;
  return {...x,flow,amount_minor:value,label:categoryLabel(x.category_id,labels),direction:value<0?(flow==='expense'?'recovery':flow==='income'?'income reversal':'outflow'):'flow'};
 }).filter(x=>finite(x.amount_minor));
}
// Dense overview is an explicit aggregation, never a top-N sample. Every original
// selector survives as a member; inspecting a group never invents a union filter.
export function mapProjection(data,focus='',compact=false) {
 const originalNodes=Array.isArray(data.nodes)?data.nodes:[],originalEdges=Array.isArray(data.edges)?data.edges:[];
 const byId=new Map(originalNodes.map(n=>[n.id,n]));
 const edges=focus?originalEdges.filter(e=>[e.source,e.target].some(id=>byId.get(id)?.account===focus)):originalEdges;
 const used=new Set(edges.flatMap(e=>[e.source,e.target]));
 const nodes=originalNodes.filter(n=>used.has(n.id));
 const dense=nodes.length>24||edges.length>60||['income','expense'].some(kind=>nodes.filter(n=>n.kind===kind).length>8);
 if(!dense)return {...data,nodes,edges,grouped:false};
 const accounts=nodes.filter(n=>n.kind==='account'),mapped=new Map(),projected=new Map();
 for(const n of nodes){
  let key=n.id,label=n.label;
  if(n.kind==='account'&&!focus&&compact&&accounts.length>8){key='group:accounts';label=`${accounts.length} accounts`;}
  else if(n.kind!=='account'){
   const member=edges.find(e=>e.source===n.id||e.target===n.id),category=member?.selector?.filters?.category;
   const path=category?category.split(/[.:/]/).slice(0,2).join('.'):n.kind;
   key='group:'+n.kind+':'+path;label=categoryLabel(path);
  }
  mapped.set(n.id,key);if(!projected.has(key))projected.set(key,{...n,id:key,label,...(key==='group:accounts'?{account:undefined,summary:true}:{})});
 }
 // Keep eight readable families per zone, folding the remainder explicitly.
 for(const kind of ['income','expense','investment','financing','adjustment','unresolved','transfer']){
  const family=[...projected.values()].filter(n=>n.kind===kind).sort((a,b)=>a.label.localeCompare(b.label));
  if(family.length>8){const overflow=new Set(family.slice(7).map(n=>n.id)),key='group:other:'+kind;for(const [id,to] of mapped)if(overflow.has(to))mapped.set(id,key);for(const id of overflow)projected.delete(id);projected.set(key,{id:key,label:'Other '+kind+' categories',kind});}
 }
 const bundles=new Map();
 for(const edge of edges){const source=mapped.get(edge.source),target=mapped.get(edge.target);const key=JSON.stringify([source,target,edge.kind,edge.currency,edge.selector?.endpoint,edge.selector?.filters?.direction,edge.selector?.filters?.transfer_status]);
  let group=bundles.get(key);if(!group){group={id:'bundle:'+key,source,target,kind:edge.kind,currency:edge.currency,label:`${projected.get(source)?.label} → ${projected.get(target)?.label}`,amount_minor:0,count:0,members:[]};bundles.set(key,group);}group.amount_minor+=edge.amount_minor;group.count+=edge.count;group.members.push(edge);
 }
 return {...data,nodes:[...projected.values()],edges:[...bundles.values()],grouped:true,originalCount:edges.length};
}
export function monthlyPeriods(periods=[]) {
 const accepted=[];
 return periods.filter(p=>{const days=(Date.parse(p.end_date)-Date.parse(p.start_date))/86400000;return Number.isFinite(days)&&days>=0&&days<=35;}).sort((a,b)=>a.start_date.localeCompare(b.start_date)).filter(p=>{if(accepted.some(q=>q.account===p.account&&q.currency===p.currency&&q.start_date<p.end_date&&q.end_date>p.start_date))return false;accepted.push(p);return true;});
}
const endpoints = new Set(['meta','flow','overview','transactions','transfers','investments','compensation','obligations','quotes']);
const filters = new Set(['transfer_status','direction','source_account','target_account','link_type','start','end','currency','scope','account','category','category_mode','flow','vesting_policy','dataset','record_id','q','limit','offset','symbols']);
export function financeAPI(fetcher=globalThis.fetch.bind(globalThis),timeout=15000) {
 return async (endpoint, query={}, signal) => {
  if (!endpoints.has(endpoint)) throw new Error('Invalid finance endpoint');
  const params=new URLSearchParams(Object.entries(query).filter(([key,value])=>filters.has(key)&&value!=null&&(value!==''||(key==='category'&&query.category_mode==='exact'))));
  const local=new AbortController(),abort=()=>local.abort();let timedOut=false;
  if(signal?.aborted)throw new DOMException('Aborted','AbortError');
  signal?.addEventListener('abort',abort,{once:true});
  const timer=setTimeout(()=>{timedOut=true;local.abort();},timeout);
  try {
   const response=await fetcher(`/hermes/app-api/finance/${endpoint}${params.size?'?'+params:''}`,{method:'GET',credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'},signal:local.signal});
   if(!response.ok) { const error=new Error(response.status===503?'Recorded data is unavailable.':`Request failed (${response.status}).`);error.status=response.status;throw error; }
   return await response.json();
  }catch(error){if(timedOut)throw new Error('Finance request timed out. Retry when the connection is available.');throw error;}
  finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);}
 };
}
