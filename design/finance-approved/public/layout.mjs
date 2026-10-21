// Connectivity never determines account-group order. Zero-movement accounts
// remain in the linked list, without inventing minimum-value Sankey ribbons.
export const roleOrder=['banking','cards','investments','debt','other'];
const isAccount=n=>n.kind==='account'||n.kind==='account-group';
const isProduct=n=>n.kind==='product-group'||n.kind==='product';
const productDepth=n=>n.hierarchyDepth??(n.kind==='product-group'?0:1);
const role=n=>n.accountGroup||n.role||'other';
const compare=(a,b)=>String(a.label).localeCompare(String(b.label))||a.id.localeCompare(b.id);
const roleX={banking:0.32,cards:0.52,investments:0.52,debt:0.52,other:0.52};
const headerHeight=52;
export function groupedLayout(p,{heights=null,width:baseWidth=1280,anchors={}}={}){
 const columns=new Map(),accounts=p.nodes.filter(isAccount),products=p.nodes.filter(isProduct);
 const hubs=p.nodes.filter(n=>n.kind==='payroll'&&n.incoming>0&&n.outgoing>0);
 const sources=p.nodes.filter(n=>!isAccount(n)&&!isProduct(n)&&!hubs.includes(n)&&(n.polarity==='source'||(!n.incoming&&n.outgoing>0)||['income','recovery','reimbursement'].includes(n.kind)));
 const sinks=p.nodes.filter(n=>!isAccount(n)&&!isProduct(n)&&!sources.includes(n)&&!hubs.includes(n));
 const depth=n=>n.hierarchyDepth??new Set(p.nodes.filter(parent=>parent.kind===n.kind&&parent.categoryPath&&n.categoryPath?.startsWith(parent.categoryPath+'.')).map(parent=>parent.categoryPath)).size;
 const maxDepth=Math.max(0,...sinks.map(depth)),width=baseWidth+maxDepth*180,accountX=roleX.banking;
 columns.set('source',sources);columns.set('payroll',hubs);columns.set('accounts',accounts);for(const n of sinks){const key=depth(n);if(!columns.has(key))columns.set(key,[]);columns.get(key).push(n);}
 const weight=n=>Math.max(n.externalIncoming??n.incoming??0,n.externalOutgoing??n.outgoing??0,0),maxTotal=Math.max(1,...[...columns.values()].map(ns=>ns.reduce((s,n)=>s+weight(n),0)));
 const scale=240/maxTotal,h=n=>Math.max(isAccount(n)?56:48,heights?.[n.id]??weight(n)*scale),slots={},bands=[];
 // Only real source→payroll edges reserve the upstream lane. Monetary heights
 // are unchanged; separating both endpoints also keeps bypass ribbons below it.
 let payrollBottom=0;
 for(const hub of [...hubs].sort(compare)){
  const incoming=(p.edges||[]).filter(e=>e.target===hub.id&&e.amount>0&&sources.some(n=>n.id===e.source));
  const receipts=sources.filter(n=>incoming.some(e=>e.source===n.id)).sort((a,b)=>weight(b)-weight(a)||compare(a,b));
  if(!receipts.length)continue;
  let y=Math.max(62,payrollBottom+26);
  for(const n of receipts){if(slots[n.id])continue;slots[n.id]={top:y,bottom:y+h(n),x:0.018};y+=h(n)+26;}
  const total=incoming.reduce((sum,e)=>sum+e.amount,0),center=incoming.reduce((sum,e)=>sum+e.amount*(slots[e.source].top+slots[e.source].bottom)/2,0)/total;
  const top=center-h(hub)/2;
  slots[hub.id]={top,bottom:top+h(hub),x:0.18};
  payrollBottom=Math.max(payrollBottom,y-26,slots[hub.id].bottom);
 }
 const upstream=Object.values(slots),shift=Math.max(0,62-Math.min(62,...upstream.map(s=>s.top)));
 for(const slot of upstream){slot.top+=shift;slot.bottom+=shift;}
 if(upstream.length)payrollBottom+=shift;
 const accountStart=payrollBottom?payrollBottom+26:30;
 const finalWidth=width+(products.length?(1+Math.max(...products.map(productDepth)))*240:0);
 const ratio=(width-24)/(finalWidth-24),pixel=px=>px/(finalWidth-24);
 for(const slot of Object.values(slots))slot.x*=ratio;
 const ownerRole=n=>{
  const owner=accounts.find(a=>a.id===n.ownerAccountId);
  if(owner)return role(owner);
  const group=p.accountGroups?.find(g=>g.id===n.groupId||g.accounts?.some(a=>a.id===n.ownerAccountId));
  if(group)return group.role;
  try{const [kind,r]=JSON.parse(n.groupId);if(kind==='account-group'&&roleOrder.includes(r))return r;}catch{}
  return 'other';
 };
 const laneY=new Map();
 for(const r of roleOrder){
  const ns=accounts.filter(n=>role(n)===r),ownedProducts=products.filter(n=>ownerRole(n)===r),group=p.accountGroups?.find(g=>g.role===r);
  if(!ns.length&&!group&&!ownedProducts.length)continue;
  const id=group?.id||JSON.stringify(['account-group',r]);
  // Absorb subpixel measurement drift only, not real changes to the
  // upstream lane (which must reclaim its original spacing on fold).
  const x=roleX[r]*ratio,start=laneY.get(x)||accountStart;
  const top=anchors[id]!=null&&Math.abs(anchors[id]-start)<1?anchors[id]:Math.max(start,anchors[id]??0);
  const band={id:group?.id||JSON.stringify(['account-group',r]),role:r,label:group?.label||r,x,right:x+pixel(180),top,collapsed:!!group?.collapsed,count:group?.count??ns.length,active:ns.length};
  const ownerIds=new Set([...ns.map(n=>n.id),...ownedProducts.map(n=>n.ownerAccountId)]);
  const owners=[...ownerIds].map(id=>ns.find(n=>n.id===id)||group?.accounts?.find(n=>n.id===id)||{id,label:id})
   .sort((a,b)=>String(a.registration||'').localeCompare(String(b.registration||''))||compare(a,b));
  let y=top+headerHeight;
  for(const owner of owners){
   const owned=ownedProducts.filter(n=>n.ownerAccountId===owner.id),lanes=new Map();
   for(const n of owned){const d=productDepth(n);if(!lanes.has(d))lanes.set(d,[]);lanes.get(d).push(n);}
   const laneHeight=nodes=>nodes.reduce((sum,n)=>sum+h(n),0)+Math.max(0,nodes.length-1)*22;
   const ownerHeight=ns.includes(owner)?h(owner):0,rowHeight=Math.max(ownerHeight,...[...lanes.values()].map(laneHeight));
   const ownerTop=y+(rowHeight-ownerHeight)/2;
   if(ownerHeight)slots[owner.id]={top:ownerTop,bottom:ownerTop+ownerHeight,x};
   for(const [d,nodes] of lanes){
    let childTop=y+(rowHeight-laneHeight(nodes))/2;
    for(const n of nodes.sort(compare)){
     slots[n.id]={top:childTop,bottom:childTop+h(n),x:x+pixel((d+1)*240)};
     childTop+=h(n)+22;
    }
   }
   if(owned.length){
    const right=Math.max(...owned.map(n=>slots[n.id].x))+pixel(180);
    (band.productEnclosures??=[]).push({ownerAccountId:owner.id,x,right,top:y,bottom:y+rowHeight});
    band.right=Math.max(band.right,right);
   }
   y+=rowHeight+22;
  }
  band.bottom=y;
  // Reserve each group's full footprint, folded or open. When a hierarchy
  // grows, only later intersecting groups yield; its own heading stays put.
  for(const previous of [...bands].sort((a,b)=>a.top-b.top)){
   if(band.x-pixel(18)>=previous.right+pixel(12)||band.right+pixel(12)<=previous.x-pixel(18))continue;
   if(band.top>=previous.bottom+12||band.bottom<=previous.top-12)continue;
   const shift=previous.bottom+12-band.top;
   if(shift<1&&anchors[id]===band.top)continue; // retain the >11px gutter across rounding
   band.top+=shift;band.bottom+=shift;
   for(const n of [...ns,...ownedProducts]){slots[n.id].top+=shift;slots[n.id].bottom+=shift;}
   for(const enclosure of band.productEnclosures||[]){enclosure.top+=shift;enclosure.bottom+=shift;}
  }
  bands.push(band);laneY.set(x,band.bottom+12);
 }
 let height=Math.max(520,...bands.map(b=>b.bottom+32));
 for(const [column,ns] of columns){
  if(column==='accounts')continue;let y=payrollBottom?payrollBottom+26:62;
  const x=(column==='source'?0.018:column==='payroll'?0.18:0.79+Number(column)*0.15/(Math.max(maxDepth,1)))*ratio;
  const priority=n=>n.kind==='recovery'?3:['transfer','unresolved'].includes(n.kind)?4:n.kind==='investment-income'?2:0;
  const ordered=[...ns].sort((a,b)=>(column==='source'?priority(a)-priority(b):0)||weight(b)-weight(a)||compare(a,b));
  for(const n of ordered){
   if(slots[n.id])continue;
   // Only neighbors whose bars actually enter a group's horizontal extent
   // yield to that anchored group. Unrelated columns keep their original rows.
   for(const band of bands.filter(b=>x+pixel(10)>b.x-pixel(18)&&x-pixel(10)<b.right).sort((a,b)=>a.top-b.top)){
    if(y<band.bottom+22&&y+h(n)>band.top-22)y=band.bottom+22;
   }
   slots[n.id]={top:y,bottom:y+h(n),x};y+=h(n)+26;
  }
  height=Math.max(height,y+32);
 }
 return {width:finalWidth,height,accountX:accountX*ratio,bands,slots,x:p.nodes.map(n=>slots[n.id].x),y:p.nodes.map(n=>(slots[n.id].top+slots[n.id].bottom)/2/height)};
}
