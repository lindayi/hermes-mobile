import test from 'node:test';
import assert from 'node:assert/strict';
import {groupedLayout} from '../../../frontend/finance/approved/layout.mjs';

const groupId=JSON.stringify(['account-group','investments']);
function fixture(){
 const accounts=[{id:'bank',kind:'account',accountGroup:'banking',label:'Checking',incoming:1000,outgoing:500},
  {id:'a',kind:'account',accountGroup:'investments',registration:'RRSP',label:'Retirement',incoming:200,outgoing:100},
  {id:'b',kind:'account',accountGroup:'investments',registration:'TFSA',label:'Savings',incoming:100,outgoing:200}];
 return {nodes:[...accounts,{id:'income',kind:'income',label:'Salary',incoming:0,outgoing:1000},
  {id:'rent',kind:'expense',label:'Rent',incoming:500,outgoing:0},
  ...['a','b'].flatMap(ownerAccountId=>[
   {id:ownerAccountId+'-root',kind:'product-group',label:'Products',ownerAccountId,groupId,hierarchyDepth:0,incoming:0,outgoing:100,polarity:'source'},
   {id:ownerAccountId+'-buy',kind:'product',label:'Purchase',ownerAccountId,groupId,hierarchyDepth:1,incoming:70,outgoing:0},
   {id:ownerAccountId+'-sale',kind:'product',label:'Sale',ownerAccountId,groupId,hierarchyDepth:1,incoming:0,outgoing:30,polarity:'source'}])],edges:[],
  accountGroups:[{id:groupId,role:'investments',label:'Investments',accounts:accounts.slice(1),count:2,collapsed:false}]};
}
const isProduct=n=>['product-group','product'].includes(n.kind);
const center=s=>(s.top+s.bottom)/2;
const pixelX=(layout,id)=>12+layout.slots[id].x*(layout.width-24);

test('owned purchases and sales extend right from their account through Products, never into receipt lanes',()=>{
 const p=fixture(),l=groupedLayout(p),roots={...p,nodes:p.nodes.filter(n=>n.kind!=='product')},base={...p,nodes:p.nodes.filter(n=>!isProduct(n))};
 for(const owner of ['a','b']){
  assert.ok(l.slots[owner].x<l.slots[owner+'-root'].x,'owner precedes Products, including sales');
  for(const suffix of ['buy','sale'])assert.ok(l.slots[owner+'-root'].x<l.slots[owner+'-'+suffix].x,'Products precedes all individual products');
 }
 assert.ok(l.width>groupedLayout(roots).width,'expanding products adds diagram width');
 assert.ok(groupedLayout(roots).width>groupedLayout(base).width,'showing Products adds diagram width');
 for(const id of ['bank','income','rent']){
  assert.ok(Math.abs(pixelX(l,id)-pixelX(groupedLayout(base),id))<1e-8,'ordinary columns retain pixel positions');
  if(id!=='rent')assert.equal(l.slots[id].top,groupedLayout(base).slots[id].top,'non-colliding ordinary rows do not shift');
 }
});

test('each measured owner row expands in place and excludes colliding ordinary destinations',()=>{
 const p=fixture();
 for(const heights of [null,{'a':240,'a-root':110,'a-buy':350,'a-sale':90,'b':12,'b-root':8,'b-buy':160,'b-sale':3,rent:430}]){
  const l=groupedLayout(p,{heights}),band=l.bands.find(b=>b.id===groupId);
  const base=groupedLayout({...p,nodes:p.nodes.filter(n=>!isProduct(n))},{heights});
  assert.equal(band.top,base.bands.find(b=>b.id===groupId).top,'owned hierarchy grows from its existing group anchor');
  assert.ok(l.slots.rent.top>=band.bottom||l.slots.rent.bottom<=band.top,'ordinary spending bars must not lie in the group enclosure');
  assert.ok(band.right>band.x&&band.right<=1,'band right is a normalized horizontal extent');
  for(const owner of ['a','b']){
   const enclosure=band.productEnclosures.find(e=>e.ownerAccountId===owner);
   assert.ok(enclosure.top>=band.top+52&&enclosure.bottom<=band.bottom,'52px header slot clears the parent’s 44px controls');
   assert.equal(center(l.slots[owner]),center(l.slots[owner+'-root']),'owner and Products share row center');
   for(const n of p.nodes.filter(n=>n.id===owner||n.ownerAccountId===owner)){
    const slot=l.slots[n.id];
    assert.ok(slot.top>=enclosure.top&&slot.bottom<=enclosure.bottom,'all measured child heights remain inside their account row');
    assert.ok(slot.x>=band.x&&slot.x<=band.right);
    assert.ok(slot.bottom-slot.top>=Math.max(n.kind==='account'?56:48,heights?.[n.id]??0),'retain measured and typography slots');
   }
   assert.ok(l.slots[owner+'-sale'].top-l.slots[owner+'-buy'].bottom>=22,'same-column products do not overlap');
  }
  assert.ok(band.productEnclosures[1].top-band.productEnclosures[0].bottom>=22,'owner rows are separated');
  assert.ok(l.height>band.bottom);
  const reverse=groupedLayout({...p,nodes:[...p.nodes].reverse()},{heights});
  assert.deepEqual(reverse.slots,l.slots,'input ordering must not alter geometry');
 }
});

test('quiet or unknown owners do not create financial bars, amounts or trace nodes',()=>{
 for(const retainMetadata of [true,false]){
  const p=fixture();p.nodes=p.nodes.filter(n=>n.id!=='b');
  if(!retainMetadata){p.accountGroups=[];p.nodes=p.nodes.filter(n=>n.id!=='a');}
  const unknown={id:'unknown-product',kind:'product-group',ownerAccountId:'unknown-owner',groupId,label:'Unspecified products',hierarchyDepth:0,incoming:0,outgoing:0};
  p.nodes.push(unknown);
  const before=structuredClone(p),l=groupedLayout(p,{heights:Object.fromEntries(p.nodes.map(n=>[n.id,0]))}),band=l.bands.find(b=>b.role==='investments');
  assert.deepEqual(p,before,'layout must not mutate money values or source metadata');
  assert.deepEqual(Object.keys(l.slots).sort(),p.nodes.map(n=>n.id).sort(),'only supplied visible nodes receive slots');
  assert.equal(l.slots.b,undefined,'metadata-only owner must not invent a minimum money bar');
  assert.equal(l.slots['unknown-owner'],undefined);
  for(const id of ['b-root','b-buy','b-sale','unknown-product']){
   const slot=l.slots[id];assert.ok(Number.isFinite(slot.x)&&Number.isFinite(slot.top));
   assert.ok(slot.top>=band.top+40&&slot.bottom<=band.bottom&&slot.x<=band.right);
  }
  assert.equal(l.x.length,p.nodes.length);assert.equal(l.y.length,p.nodes.length);
 }
 const quiet=fixture();quiet.nodes=quiet.nodes.filter(n=>!isProduct(n)&&!['a','b'].includes(n.id));
 const l=groupedLayout(quiet),band=l.bands.find(b=>b.id===groupId);
 for(const b of l.bands)for(const n of quiet.nodes.filter(n=>n.accountGroup===b.role))assert.ok(l.slots[n.id].top>=b.top+52,'all role headings reserve 52px');
 assert.equal(band.count,2);assert.equal(band.active,0);assert.ok(band.bottom-band.top<60);
 assert.equal(band.productEnclosures,undefined,'quiet metadata alone does not allocate product rows');
});

test('every group expands at its own anchored start rather than below unrelated sources and sinks',()=>{
 for(const r of ['banking','cards','investments','debt','other']){
  const id=JSON.stringify(['account-group',r]),p=fixture();
  p.nodes=p.nodes.filter(n=>!isProduct(n)&&!['bank','b'].includes(n.id));p.nodes.find(n=>n.id==='a').accountGroup=r;
  p.accountGroups=[{id,role:r,label:r,count:1,collapsed:false}];
  const collapsed={...p,nodes:p.nodes.map(n=>n.id==='a'?{...n,id,kind:'account-group'}:n),accountGroups:p.accountGroups.map(g=>({...g,collapsed:true}))};
  const l=groupedLayout(p),before=groupedLayout(collapsed),band=l.bands.find(b=>b.id===id);
  assert.equal(band.top,before.bands.find(b=>b.id===id).top,r+' expansion must retain its heading anchor');
  assert.equal(l.slots.rent.top,before.slots.rent.top,'non-colliding sink stays put');
  assert.ok(band.right>band.x&&band.right<l.slots.rent.x,'bounded enclosure excludes unrelated sink column');
  assert.equal(l.width,1280,'a product-free group does not widen the chart');
  assert.equal(band.productEnclosures,undefined,'no product rows are invented');
 }
});

test('product expansion preserves payroll weighting, ordinary scale and two-pass horizontal coordinates',()=>{
 const p=fixture();p.nodes=p.nodes.filter(n=>n.id!=='income');
 p.nodes.push({id:'gross',kind:'income',label:'Gross',incoming:0,outgoing:900},
  {id:'reimbursement',kind:'reimbursement',label:'Reimbursement',incoming:0,outgoing:100},
  {id:'payroll',kind:'payroll',label:'Payroll',incoming:1000,outgoing:1000});
 p.edges=[{source:'gross',target:'payroll',amount:900},{source:'reimbursement',target:'payroll',amount:100},{source:'payroll',target:'bank',amount:1000}];
 for(const n of p.nodes.filter(isProduct)){n.incoming=1e8;n.outgoing=1e9;}
 const base={...p,nodes:p.nodes.filter(n=>!isProduct(n))};
 const heights=Object.fromEntries(p.nodes.map(n=>[n.id,isProduct(n)?130:80]));
 const initial=groupedLayout(p,{width:850}),measured=groupedLayout(p,{width:850,heights});
 assert.equal(initial.width,measured.width);assert.deepEqual(initial.x,measured.x);
 for(const [layout,options] of [[initial,{width:850}],[measured,{width:850,heights}]]){
  const ordinary=groupedLayout(base,options);
  for(const n of base.nodes){
   const a=layout.slots[n.id],b=ordinary.slots[n.id];
   // Collision displacement can be hundreds of millions of pixels in this
   // deliberately huge synthetic pre-measurement case; allow one coordinate ULP.
   assert.ok(Math.abs((a.bottom-a.top)-(b.bottom-b.top))<1e-8+Number.EPSILON*Math.max(a.bottom,b.bottom),`${n.id}: products must not change ordinary money scaling (${a.bottom-a.top} vs ${b.bottom-b.top})`);
  }
  assert.ok(Math.abs(center(layout.slots.payroll)-(center(layout.slots.gross)*900+center(layout.slots.reimbursement)*100)/1000)<1e-8);
  assert.ok(layout.slots.bank.top>layout.slots.payroll.bottom);
  for(const n of p.nodes.filter(isProduct))assert.ok(layout.slots[n.id].top>layout.slots.payroll.bottom);
 }
 // The trace may contain only active leaves after zero-flow Products roots
 // are filtered. It must still reserve the leaves' hierarchy column.
 const leaves={...p,nodes:p.nodes.filter(n=>n.kind!=='product-group')};
 const l=groupedLayout(leaves,{width:850,heights});
 assert.equal(l.width,measured.width);
 for(const n of leaves.nodes.filter(isProduct))assert.ok(l.slots[n.id].x>l.slots[n.ownerAccountId].x);
});
