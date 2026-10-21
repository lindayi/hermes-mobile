import test from 'node:test';
import assert from 'node:assert/strict';
const roleOrder=['banking','cards','investments','debt','other'];
function payrollProjection(){
 const nodes=[
  {id:'net',kind:'income',polarity:'source',label:'Unrelated net pay',incoming:0,outgoing:900000},
  {id:'gross',kind:'income',polarity:'source',label:'Gross earnings',incoming:0,outgoing:100000},
  {id:'reimbursement',kind:'reimbursement',polarity:'source',label:'Reimbursement',incoming:0,outgoing:10000},
  {id:'match',kind:'employer-match',polarity:'source',label:'Employer contribution',incoming:0,outgoing:5000},
  {id:'payroll',kind:'payroll',label:'Payroll',incoming:110000,outgoing:110000},
  {id:'bank',kind:'account',accountGroup:'banking',label:'Checking',incoming:980000,outgoing:1000},
  {id:'rrsp',kind:'account',accountGroup:'investments',registration:'RRSP',label:'Retirement',incoming:5000,outgoing:0},
  {id:'tax',kind:'tax',label:'Tax',incoming:30000,outgoing:0},
  {id:'fee',kind:'expense',label:'Fee',incoming:1000,outgoing:0}
 ];
 const edges=[['gross','payroll',100000],['reimbursement','payroll',10000],['payroll','bank',80000],['payroll','tax',30000],['net','bank',900000],['match','rrsp',5000],['bank','fee',1000]].map(([source,target,amount],i)=>({id:'edge-'+i,source,target,amount}));
 return {nodes,edges,accountGroups:[]};
}
test('payroll follows its actual receipts in a dedicated lane above unrelated forward ribbons',async()=>{
 const {groupedLayout}=await import('../../../frontend/finance/approved/layout.mjs'),p=payrollProjection();
 for(const heights of [null,{net:360,gross:40,reimbursement:4,payroll:44,bank:392,rrsp:2,tax:12,fee:0.4,match:2}]){
  const l=groupedLayout(p,{heights}),hub=l.slots.payroll,center=id=>(l.slots[id].top+l.slots[id].bottom)/2;
  assert.ok(l.slots.gross.top<l.slots.net.top,'actual gross must lead, not the largest unrelated net pay');
  assert.ok(l.slots.reimbursement.bottom<l.slots.net.top,'gross/reimbursement stay contiguous');
  const weighted=(center('gross')*100000+center('reimbursement')*10000)/110000;
  assert.ok(Math.abs(center('payroll')-weighted)<1e-8,'hub aligns with amount-weighted incoming receipts');
  for(const e of p.edges.filter(e=>!['gross','reimbursement','payroll'].includes(e.source))){
   assert.ok(l.slots[e.source].top>hub.bottom&&l.slots[e.target].top>hub.bottom,`${e.id}: whole forward ribbon lies below payroll (Bezier convex hull)`);
  }
  assert.equal(hub.x,0.18);assert.equal(l.slots.bank.x,0.32);assert.equal(l.slots.rrsp.x,0.52);
  const reversed=groupedLayout({...p,nodes:[...p.nodes].reverse(),edges:[...p.edges].reverse()},{heights});
  assert.deepEqual(reversed.slots,l.slots,'explicit geometry does not depend on input ordering');
 }
});
test('small account bars reserve full multiline labels beneath their band headers without changing values',async()=>{
 const {groupedLayout}=await import('../../../frontend/finance/approved/layout.mjs'),p=denseProjection(),before=structuredClone(p);
 const heights=Object.fromEntries(p.nodes.map(n=>[n.id,3])),l=groupedLayout(p,{heights});
 for(const band of l.bands){
  const first=p.nodes.filter(n=>n.accountGroup===band.role).sort((a,b)=>l.slots[a.id].top-l.slots[b.id].top)[0];
  if(!first)continue;
  const slot=l.slots[first.id];
  assert.ok(slot.bottom-slot.top>=56,'reserve typography height, not a minimum financial ribbon');
  assert.ok((slot.top+slot.bottom)/2-28>=band.top+36,'four-line label clears the 32px band header at top+4');
 }
 assert.deepEqual(p,before,'layout must not inflate values, fabricate links, or mutate source records');
});
export function denseProjection(){
 const nodes=roleOrder.flatMap((role,r)=>Array.from({length:r===2?8:4},(_,i)=>({id:`${role}-${i}`,kind:'account',groupId:JSON.stringify(['account-group',role]),accountGroup:role,label:`Example ${role} ${i+1}`,incoming:i?17+i:2500000,outgoing:i?900:2200000})));
 nodes.push(...Array.from({length:14},(_,i)=>({id:'expense-'+i,kind:'expense',categoryPath:'expense.category'+i,label:'Destination '+i,incoming:i?10000:1200000,outgoing:0})),{id:'income',kind:'income',label:'Net pay',incoming:0,outgoing:2500000});
 return {nodes,edges:[],accountGroups:roleOrder.map(role=>({id:JSON.stringify(['account-group',role]),role,label:role,accounts:nodes.filter(n=>n.accountGroup===role),count:nodes.filter(n=>n.accountGroup===role).length,collapsed:false}))};
}
test('fixed geometry keeps every account in ordered role bands with readable slots',async()=>{
 const {groupedLayout}=await import('../../../frontend/finance/approved/layout.mjs');const p=denseProjection(),layout=groupedLayout(p);
 assert.equal(layout.x.length,p.nodes.length);assert.equal(layout.y.length,p.nodes.length);assert.deepEqual(layout.bands.map(b=>b.role),roleOrder);
 for(const band of layout.bands)for(const n of p.nodes.filter(n=>n.accountGroup===band.role)){const i=p.nodes.indexOf(n);assert.ok(layout.y[i]>band.top/layout.height);assert.ok(layout.y[i]<band.bottom/layout.height);assert.equal(layout.x[i],band.x);}
 for(const column of new Set(layout.x)){const positions=p.nodes.map((n,i)=>({...layout.slots[n.id],x:layout.x[i]})).filter(s=>s.x===column).sort((a,b)=>a.top-b.top);for(let i=1;i<positions.length;i++)assert.ok(positions[i].top-positions[i-1].bottom>=18,'no overlapping proportional bars/labels');}
 assert.ok(layout.width<=1500);assert.ok(layout.height<3000);const reverse=groupedLayout({...p,nodes:[...p.nodes].reverse()});for(const n of p.nodes)assert.deepEqual(layout.slots[n.id],reverse.slots[n.id]);
});
test('overview width fits desktop and empty role bands do not reserve fake bar space',async()=>{
 const {groupedLayout}=await import('../../../frontend/finance/approved/layout.mjs'),p=denseProjection();p.nodes=p.nodes.filter(n=>n.kind!=='account'||n.accountGroup==='investments');
 const layout=groupedLayout(p,{width:850});assert.equal(layout.width,850);assert.deepEqual(layout.bands.map(b=>b.role),roleOrder);assert.ok(layout.bands.find(b=>b.role==='cards').bottom-layout.bands.find(b=>b.role==='cards').top<60);
});
test('main pay sources and largest destinations lead instead of alphabetical refunds',async()=>{
 const {groupedLayout}=await import('../../../frontend/finance/approved/layout.mjs');const p={nodes:[{id:'refund',label:'A refund',kind:'recovery',polarity:'source',incoming:0,outgoing:100},{id:'salary',label:'Z payroll',kind:'income',polarity:'source',incoming:0,outgoing:100000},{id:'tiny',label:'A tiny fee',kind:'expense',incoming:100,outgoing:0},{id:'large',label:'Z housing',kind:'expense',incoming:50000,outgoing:0}],edges:[],accountGroups:[]};const l=groupedLayout(p);assert.ok(l.slots.salary.top<l.slots.refund.top);assert.ok(l.slots.large.top<l.slots.tiny.top);
});
test('investment registration keeps same-type accounts adjacent across institutions',async()=>{
 const {groupedLayout}=await import('../../../frontend/finance/approved/layout.mjs');const nodes=[['c','CIBC TFSA','CIBC','TFSA'],['r','Sun Life RRSP','Sun Life','RRSP'],['t','TD TFSA','TD','TFSA'],['n','TD Nonregistered','TD','nonregistered']].map(([id,label,institution,registration])=>({id,label,institution,registration,kind:'account',accountGroup:'investments',incoming:100,outgoing:100}));const l=groupedLayout({nodes,edges:[],accountGroups:[]});const order=nodes.slice().sort((a,b)=>l.slots[a.id].top-l.slots[b.id].top).map(n=>n.id);assert.equal(Math.abs(order.indexOf('c')-order.indexOf('t')),1,'TFSA move must not appear to pass through RRSP or other account bars');
});
test('receipts use polarity, including employer match and investment income, while payroll hub is separate',async()=>{
 const {groupedLayout}=await import('../../../frontend/finance/approved/layout.mjs'),p=denseProjection();p.nodes.push({id:'receipt',kind:'investment-income',polarity:'source',incoming:0,outgoing:17,label:'Interest'},{id:'hub',kind:'payroll',semantic:true,incoming:1000,outgoing:1000,label:'Payroll'});
 const layout=groupedLayout(p);assert.ok(layout.slots.receipt.x<layout.accountX);assert.ok(layout.slots.hub.x>layout.slots.receipt.x&&layout.slots.hub.x<layout.accountX);
});
