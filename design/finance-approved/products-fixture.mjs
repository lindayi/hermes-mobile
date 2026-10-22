import {payrollService} from './payroll-fixture.mjs';
export function productsService(){
 const s=payrollService(),base=s.api,cashflows=[],holdings=[];
 s.r.nodes.push({id:'buy',kind:'investment',label:'Buy'},{id:'sell',kind:'investment',label:'Sell'});
 for(const [kind,amount]of [['buy',20000],['sell',5000]])s.r.edges.push({id:'trade-'+kind,source:kind==='buy'?'plan':'sell',target:kind==='buy'?'buy':'plan',kind:'investment',amount_minor:amount,currency:'CAD',count:kind==='buy'?2:1,selector:{endpoint:'transactions',filters:{...s.r.range,scope:'personal',dataset:'all',currency:'CAD',account:'Example_RRSP',category:'investment.trade.'+kind,category_mode:'exact',flow:'investment',direction:kind==='buy'?'outgoing':'incoming'}}});
 for(const [id,security,classification,amount]of [['buy-a','GROWTH','buy',-12000],['buy-b','INCOME','buy',-8000],['sell-a','GROWTH','sell',5000]]){
  cashflows.push({event_id:id,account:'Example_RRSP',security_key:security,classification,cash_amount_minor:amount,currency:'CAD',date:'2026-09-10'});
  s.records.push({record_id:'investment:'+id,entry_id:id,account:'Example_RRSP',currency:'CAD',date:'2026-09-10',amount_minor:amount,description:'Original '+classification+' '+security,category_id:'investment.trade.'+classification,source_id:'synthetic-product',drive_url:'https://drive.google.com/file/d/synthetic/view'});
 }
 for(const [key,name,value]of [['GROWTH','Growth ETF',88000],['INCOME','Income ETF',66000],['QUIET','Existing Bond',55000]])holdings.push({observation_id:key,account:'Example_RRSP',security_key:key,name,currency:'CAD',as_of:'2026-10-19',market_value_minor:value,book_cost_minor:value,quantity:'10',quantity_basis:'units',source_id:'synthetic-holding',drive_url:'https://drive.google.com/file/d/synthetic/view'});
 s.api=async(endpoint,q)=>{const d=await base(endpoint,q);return endpoint==='investments'&&q.currency!=='USD'?{...d,cashflows,holdings,coverage:{...d.coverage,cashflows:{complete:true},holdings:{complete:true}}}:d;};return s;
}
