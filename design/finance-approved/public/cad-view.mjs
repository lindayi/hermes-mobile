// Presentation only: original ledger rows, selectors and source evidence stay USD.
import {loadQuarter,enrichFinance} from './semantics.mjs';

// Accept flat API filters or {start,end,scope,filters}. Explicit top-level values
// win; only currency is forced per read. Selectors stay exactly as API returned:
// feed these members to createPager, never CAD display amounts or rewritten filters.
export async function loadCADQuarter(api,query,signal){
 signal?.throwIfAborted();
 const {filters={},...period}=query;query={...filters,...period};
 let usdSource;
 const read=async(endpoint,filters,readSignal)=>{const data=await api(endpoint,filters,readSignal);if(endpoint==='flow'&&filters.currency==='USD')usdSource=structuredClone(data);return data;};
 const [cad,usd]=await Promise.all(['CAD','USD'].map(currency=>loadQuarter(read,{...query,currency},signal)));
 const usdNotes=usd.notes.filter(note=>note!=='No payslip breakdown recorded for this period.'||usd.summary.netPay>0||usd.summary.employeeRRSP>0||usd.summary.employerMatch>0);
 let fx;
 try{fx=(await api('quotes',{},signal))?.fx;}
 catch(error){if([401,403].includes(error.status)||error.name==='AbortError')throw error;}
 signal?.throwIfAborted();
 if(!usableFX(fx))return {...cad,originalUSD:usd,foreignUnconverted:usd,fx:{pair:'USDCAD',status:'unavailable',rate:null,as_of:null,source:null,approximate:false,label:'USD not converted — exchange rate unavailable'},notes:[...cad.notes,...usdNotes.map(note=>'USD: '+note),'USD not converted: exchange rate unavailable. Original USD flows remain available for inspection and are excluded from CAD totals.']};
 const foreign=structuredClone(usd);
 const namespace=(kind,value)=>JSON.stringify(['presentation','USD',kind,value]);
 for(const n of foreign.nodes){n.originalSourceId=n.id;n.originalCurrency='USD';n.currency='CAD';n.approximate=true;n.id=namespace('node',n.id);if(n.ownerAccountId)n.ownerAccountId=namespace('node',n.ownerAccountId);if(n.parentId)n.parentId=namespace('node',n.parentId);n.label=String(n.label||n.account||'')+' (USD)';}
 for(const e of foreign.edges){
  e.originalSourceId=e.id;e.originalCurrency='USD';e.originalAmountMinor=e.amount_minor;
  e.id=namespace('edge',e.id);e.source=namespace('node',e.source);e.target=namespace('node',e.target);
  e.amount_minor=convertMinor(e.amount_minor,fx.rate);e.currency='CAD';e.approximate=true;
 }
 const productNodes=new Map(foreign.nodes.filter(n=>n.kind==='product'||n.kind==='product-group').map(n=>{n.activityIncoming=0;n.activityOutgoing=0;return [n.id,n];}));
 for(const e of foreign.edges.filter(e=>e.productTrade))for(const [id,field] of [[e.source,'activityOutgoing'],[e.target,'activityIncoming']]){const n=productNodes.get(id);if(n){n[field]+=e.amount_minor;if(n.parentId)productNodes.get(n.parentId)[field]+=e.amount_minor;}}
 for(const n of productNodes.values()){n.activityAmount=n.activityIncoming+n.activityOutgoing;n.value=Math.max(n.activityIncoming,n.activityOutgoing);}
 // Re-run the existing sign/category classification on rounded original flow
 // edges, not a rounded net or payroll-expanded graph (which would double count).
 for(const e of usdSource.edges)e.amount_minor=convertMinor(e.amount_minor,fx.rate);
 const convertedSummary=enrichFinance(usdSource).summary;
 convertedSummary.taxWithheld=foreign.edges.filter(e=>e.payrollBridge&&e.kind==='tax').reduce((sum,e)=>sum+e.amount_minor,0);
 const summary={...cad.summary};
 for(const key of Object.keys(summary))summary[key]+=convertedSummary[key];
 summary.net=summary.income-summary.spending;
 return {...cad,nodes:[...cad.nodes,...foreign.nodes],edges:[...cad.edges,...foreign.edges],summary,originalUSD:usd,foreignUnconverted:null,fx:{...fx,approximate:true,label:equivalentLabel(fx)},notes:[...cad.notes,...usdNotes.map(note=>'USD: '+note),'USD: '+equivalentLabel(fx)+'. Original USD evidence is unchanged.']};
}
function equivalentLabel(fx){return 'Approximate CAD equivalent at rate date '+fx.as_of+(fx.status==='stale'?' (stale rate)':'');}
function usableFX(fx){
 return fx?.pair==='USDCAD'&&['ok','stale'].includes(fx.status)&&typeof fx.rate==='number'&&Number.isFinite(fx.rate)&&fx.rate>0&&/^\d{4}-\d{2}-\d{2}$/.test(fx.as_of)&&Number.isFinite(Date.parse(fx.as_of))&&new Date(fx.as_of).toISOString().slice(0,10)===fx.as_of&&typeof fx.source==='string'&&fx.source.length>0;
}
function convertMinor(amount,rate){
 if(!Number.isSafeInteger(amount))throw Error('Unsafe original monetary amount');
 // Parse the API decimal's string representation, including scientific notation.
 const [mantissa,exponent='0']=String(rate).toLowerCase().split('e');
 const [whole,fraction='']=mantissa.split('.');
 const scale=fraction.length-Number(exponent);
 const numerator=BigInt(whole+fraction)*(scale<0?10n**BigInt(-scale):1n);
 const denominator=scale>0?10n**BigInt(scale):1n;
 const signed=BigInt(amount)*numerator,absolute=signed<0n?-signed:signed;
 const rounded=(absolute+denominator/2n)/denominator*(signed<0n?-1n:1n);
 const value=Number(rounded);if(!Number.isSafeInteger(value))throw Error('Unsafe CAD equivalent');return value;
}
export function presentationEquivalent(row,fx){
 if(row?.currency!=='USD'||!usableFX(fx))return null;
 const originalAmountMinor=row.amount_minor??row.amount;
 return {currency:'CAD',amount_minor:convertMinor(originalAmountMinor,fx.rate),originalCurrency:'USD',originalAmountMinor,approximate:true,rate:fx.rate,as_of:fx.as_of,source:fx.source,status:fx.status,label:equivalentLabel(fx)};
}
