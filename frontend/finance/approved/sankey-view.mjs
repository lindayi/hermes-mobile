import {createModel,selectionMembers} from './live-model.mjs';
import {groupedLayout} from './layout.mjs';

const account=node=>node?.kind==='account'||node?.kind==='account-group';
const color={income:'#327b65','employer-match':'#367662','payroll-saving':'#796496','portfolio-trade':'#75678f',tax:'#b18655','payroll-deduction':'#ad8766','payroll-net':'#397f79',reimbursement:'#548d78',payroll:'#327b65','investment-income':'#327b65',recovery:'#548d78',transfer:'#69849c',expense:'#b36d45',investment:'#7a7c9c',financing:'#8f7470',adjustment:'#9a8e6c',unresolved:'#929292'};
const html=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');

export function sankeyView(root,raw,{inspect=()=>{},selectedAccount='',Plotly=globalThis.Plotly}={}){
 if(!Plotly)throw new Error('Finance Sankey renderer is unavailable');
 const model=createModel(raw),expanded=[],collapsed=model.project().accountGroups.map(group=>group.id),anchors={};
 const stage=document.createElement('div');stage.className='money-sankey';
 const chart=document.createElement('div');chart.className='money-sankey-chart';chart.setAttribute('role','group');chart.setAttribute('aria-label','Recorded money movement Sankey');
 const bands=document.createElement('div');bands.className='money-sankey-bands';bands.setAttribute('aria-label','Account role groups');
 const controls=document.createElement('div');controls.className='money-sankey-controls';
 const jump=document.createElement('select');jump.setAttribute('aria-label','Jump to account group');
 const status=document.createElement('p');status.className='money-sankey-status';status.setAttribute('role','status');
 stage.append(chart,bands);root.replaceChildren(controls,jump,stage,status);
 let directory,p,geometry,disposed=false,selected=null;
 const sheet=[...document.styleSheets].find(item=>item.href&&new URL(item.href).pathname.endsWith('/plotly.css'));
 if(!sheet)throw new Error('Finance Sankey stylesheet is unavailable');
 const styleRules=[];let styleNumber=0;
 const layoutStyle=(node,properties)=>{
  const name=`finance-sankey-layout-${styleNumber++}`;node.classList.add(name);
  const declarations=Object.entries(properties).map(([key,value])=>`${key}:${value}`).join(';');
  styleRules.push(sheet.cssRules.length);sheet.insertRule(`.${name}{${declarations}}`,sheet.cssRules.length);
 };
 const clearLayoutStyles=()=>{for(const index of styleRules.splice(0).reverse())sheet.deleteRule(index);styleNumber=0;};
 const membersFor=selection=>selectionMembers(selection.view||p,selection);
 const chooseGroup=(id,label)=>{const selectedGroup={group:id,label};select(selectedGroup);open(membersFor(selectedGroup));};
 const select=next=>{
  selected=next;
  status.textContent=next?.label||'';
  bands.querySelectorAll('[data-group-select]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.groupSelect===next?.group)));
  const memberIds=new Set(next?membersFor(next).map(member=>member.id):[]),edges=p.edges.filter(edge=>edge.members?.some(member=>memberIds.has(member.id))),edgeIds=new Set(edges.map(edge=>edge.id)),nodeIds=new Set(edges.flatMap(edge=>[edge.source,edge.target]));
  chart.querySelectorAll('.sankey-node').forEach(node=>node.classList.toggle('is-dim',!!next&&!nodeIds.has(node.dataset.node)));
  chart.querySelectorAll('.sankey-link').forEach(link=>link.classList.toggle('is-selected',!!next&&edgeIds.has(link.dataset.edge)));
 };
 const open=edges=>{
  const members=[...new Map(edges.flatMap(edge=>edge.members||[edge]).map(edge=>[edge.id,edge])).values()];
  if(!members.length)return;
  const edge={id:'sankey-selection:'+members.map(member=>member.id).join('|'),label:members.length===1?members[0].label:`${members.length} exact streams`,amount_minor:members.reduce((sum,member)=>sum+member.amount_minor,0),currency:members[0].currency,members};
  inspect(edge);
 };
 const groupControls=()=>{
  controls.replaceChildren();
  for(const group of p.accountGroups){
   const row=document.createElement('div');row.className='money-sankey-group';
   const selectButton=document.createElement('button');selectButton.type='button';selectButton.dataset.groupSelect=group.id;selectButton.setAttribute('aria-pressed',String(selected?.group===group.id));selectButton.textContent=`${group.label} · ${group.count}`;selectButton.addEventListener('click',()=>chooseGroup(group.id,group.label));
   const toggle=document.createElement('button');toggle.type='button';toggle.className='money-sankey-toggle';toggle.setAttribute('aria-label',`${group.collapsed?'Expand':'Fold'} ${group.label}`);toggle.setAttribute('aria-expanded',String(!group.collapsed));toggle.textContent=group.collapsed?'›':'⌄';toggle.addEventListener('click',()=>{const band=geometry?.bands.find(item=>item.id===group.id);if(band)anchors[group.id]=band.top;const wasSelected=selected?.group===group.id;collapsed.splice(0,collapsed.length,...model.toggleGroup(collapsed,group.id));draw().then(()=>{if(wasSelected)select({group:group.id,label:group.label});});});
   row.append(selectButton,toggle);controls.append(row);
  }
 };
 const renderBands=()=>{
  bands.replaceChildren();jump.replaceChildren(new Option('Jump to account group',''));
  for(const band of geometry.bands){
   const row=document.createElement('div');row.className='money-sankey-band';row.dataset.role=band.role;
   layoutStyle(row,{left:`${12+band.x*(geometry.width-24)-10}px`,top:`${band.top+4}px`,width:`${Math.max(150,12+(band.right-band.x)*(geometry.width-24))}px`,height:`${Math.max(48,band.bottom-band.top-4)}px`});
   const selectButton=document.createElement('button');selectButton.type='button';selectButton.dataset.groupSelect=band.id;selectButton.setAttribute('aria-pressed',String(selected?.group===band.id));selectButton.textContent=band.label;selectButton.addEventListener('click',()=>chooseGroup(band.id,band.label));
   const toggle=document.createElement('button');toggle.type='button';toggle.className='money-sankey-toggle';toggle.setAttribute('aria-label',`${band.collapsed?'Expand':'Fold'} ${band.label}`);toggle.setAttribute('aria-expanded',String(!band.collapsed));toggle.textContent=band.collapsed?'›':'⌄';toggle.addEventListener('click',()=>{anchors[band.id]=band.top;const keep=selected?.group===band.id;collapsed.splice(0,collapsed.length,...model.toggleGroup(collapsed,band.id));draw().then(()=>{if(keep)select({group:band.id,label:band.label});});});
   row.append(selectButton,toggle);bands.append(row);
   if(!band.collapsed){const box=document.createElement('div');box.className='money-sankey-enclosure';box.dataset.role=band.role;layoutStyle(box,{left:`${Math.max(0,12+band.x*(geometry.width-24)-18)}px`,top:`${band.top}px`,width:`${Math.max(180,(band.right-band.x)*(geometry.width-24)+30)}px`,height:`${band.bottom-band.top}px`});bands.append(box);}
   jump.append(new Option(`${band.label}${band.active?'':' · no movement'}`,band.id));
  }
  jump.onchange=()=>{const band=geometry.bands.find(item=>item.id===jump.value);if(band)stage.scrollTo({top:band.top,behavior:'smooth'});};
 };
 async function draw(){
  if(disposed)return;clearLayoutStyles();
  directory=model.project(expanded,[]);p=model.project(expanded,collapsed);
  const active=new Set(p.edges.flatMap(edge=>[edge.source,edge.target])),nodes=p.nodes.filter(node=>active.has(node.id));
  if(!nodes.length||!p.edges.length){try{Plotly.purge(chart);}catch{}chart.replaceChildren();chart.textContent='No recorded movements match these filters.';renderBands();groupControls();return;}
  const projected={...p,nodes},width=Math.max(720,stage.clientWidth||900);
  geometry=groupedLayout(projected,{width,anchors});
  const height=geometry.height,index=new Map(nodes.map((node,i)=>[node.id,i]));
  layoutStyle(chart,{width:`${width}px`,height:`${height}px`});
  renderBands();groupControls();
  const labels=nodes.map(node=>`${html(node.label)}<br>${account(node)?'Account':html(node.kind.replaceAll('-',' '))}<br>${html(new Intl.NumberFormat('en-CA',{style:'currency',currency:raw.currency}).format((node.value??Math.max(node.incoming,node.outgoing))/100))}`);
  const trace={type:'sankey',arrangement:'fixed',orientation:'h',valueformat:'.2f',valueprefix:raw.currency+' ',textfont:{family:'system-ui, sans-serif',size:12,color:'#22332f'},node:{x:nodes.map(node=>geometry.slots[node.id].x),y:nodes.map(node=>(geometry.slots[node.id].top+geometry.slots[node.id].bottom)/(2*height)),align:'left',pad:4,thickness:16,line:{width:0},label:labels,color:nodes.map(node=>color[node.displayKind||node.kind]||color.unresolved),customdata:nodes.map(node=>[node.id,html(node.label)]),hovertemplate:'%{customdata[1]}<extra></extra>'},link:{source:p.edges.map(edge=>index.get(edge.source)),target:p.edges.map(edge=>index.get(edge.target)),value:p.edges.map(edge=>edge.amount/100),color:p.edges.map(edge=>`${color[edge.displayKind||edge.type]||color.unresolved}55`),customdata:p.edges.map(edge=>[edge.id,html((nodes[index.get(edge.source)]?.label||'')+' → '+(nodes[index.get(edge.target)]?.label||''))]),hovertemplate:'%{customdata[1]}<extra></extra>'}};
  const layout={width,height,margin:{l:12,r:12,t:12,b:12},paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',font:{family:'system-ui, sans-serif',color:'#202b2b'}};
  await Plotly.react(chart,[trace],layout,{displayModeBar:false,responsive:false});
  if(disposed)return;
  chart.removeAllListeners?.('plotly_click');
  chart.on('plotly_click',event=>{
   const point=event.points?.[0];if(!point)return;
   if(point.source!==undefined){const edge=p.edges.find(item=>item.id===point.customdata?.[0]);if(edge){select({edges:[edge.id],label:edge.label});open([edge]);}return;}
   const node=p.nodes.find(item=>item.id===point.customdata?.[0]);if(node){const edges=[...p.edges,...p.internalEdges].filter(edge=>edge.source===node.id||edge.target===node.id);select({node:node.id,label:node.label});open(edges);}
  });
  chart.querySelectorAll('.sankey-link').forEach((link,index)=>{const edge=p.edges[index];if(!edge)return;link.dataset.edge=edge.id;link.setAttribute('tabindex','0');link.setAttribute('role','button');link.setAttribute('aria-label',`Inspect ${edge.label||'recorded movement'}`);link.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();select({edges:[edge.id],label:edge.label});open([edge]);}});});
  chart.querySelectorAll('.sankey-node').forEach((node,index)=>{const item=nodes[index];if(!item)return;node.dataset.node=item.id;node.setAttribute('tabindex','0');node.setAttribute('role','button');node.setAttribute('aria-label',`Inspect ${item.label}`);node.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();const edges=[...p.edges,...p.internalEdges].filter(edge=>edge.source===item.id||edge.target===item.id);select({node:item.id,label:item.label});open(edges);}});});
  if(selected)select(selected);
 }
 const onResize=()=>{if(!disposed)void draw();};window.addEventListener('resize',onResize);void draw();
 return {model,destroy(){if(disposed)return;disposed=true;window.removeEventListener('resize',onResize);clearLayoutStyles();try{Plotly.purge(chart);}catch{}root.replaceChildren();},select};
}
