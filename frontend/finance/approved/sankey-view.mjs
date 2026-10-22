import {createModel,selectionMembers} from './live-model.mjs';
import {groupedLayout} from './layout.mjs';
import {ranked} from './live-model.mjs';

const account=node=>node?.kind==='account'||node?.kind==='account-group';
const color={income:'#327b65','employer-match':'#367662','payroll-saving':'#796496','portfolio-trade':'#75678f',tax:'#b18655','payroll-deduction':'#ad8766','payroll-net':'#397f79',reimbursement:'#548d78',payroll:'#327b65','investment-income':'#327b65',recovery:'#548d78',transfer:'#69849c',expense:'#b36d45',investment:'#7a7c9c',financing:'#8f7470',adjustment:'#9a8e6c',unresolved:'#929292'};
const html=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');

export function sankeyView(root,raw,{inspect=()=>{},selectedAccount='',Plotly=globalThis.Plotly}={}){
 if(!Plotly)throw new Error('Finance Sankey renderer is unavailable');
 const model=createModel(raw),expanded=[],collapsed=window.matchMedia('(max-width: 600px)').matches?model.project().accountGroups.map(group=>group.id):[],anchors={};
 const stage=document.createElement('div');stage.className='money-sankey';
 const canvas=document.createElement('div');canvas.className='money-sankey-canvas';
 const layer=document.createElement('div');layer.className='money-sankey-layer';
 const chart=document.createElement('div');chart.className='money-sankey-chart';chart.setAttribute('role','group');chart.setAttribute('aria-label','Recorded money movement Sankey');
 const bands=document.createElement('div');bands.className='money-sankey-bands';bands.setAttribute('aria-label','Account role groups');
 const controls=document.createElement('div');controls.className='money-sankey-controls';
 const explore=document.createElement('details');explore.className='money-sankey-explore';
 const exploreSummary=document.createElement('summary');exploreSummary.textContent='Explore accounts, spending and categories';
 const exploreTabs=document.createElement('div');exploreTabs.className='money-sankey-explore-tabs';exploreTabs.setAttribute('role','tablist');exploreTabs.setAttribute('aria-label','Money map lists');
 const exploreList=document.createElement('div');exploreList.className='money-sankey-explore-list';exploreList.setAttribute('role','region');exploreList.setAttribute('aria-label','Money map list');
 explore.append(exploreSummary,exploreTabs,exploreList);
 const viewControls=document.createElement('div');viewControls.className='money-sankey-view-controls';viewControls.setAttribute('role','group');viewControls.setAttribute('aria-label','Map view controls');
 const jump=document.createElement('select');jump.setAttribute('aria-label','Jump to account group');
 const finder=document.createElement('details');finder.className='money-sankey-stream-finder';
 const finderSummary=document.createElement('summary');finderSummary.textContent='Find an exact movement';
 const finderInput=document.createElement('input');finderInput.type='search';finderInput.maxLength=120;finderInput.placeholder='Search movement labels';finderInput.setAttribute('aria-label','Search exact movements');
 const finderStatus=document.createElement('p');finderStatus.setAttribute('role','status');
 const finderResults=document.createElement('div');finderResults.className='money-sankey-stream-results';
 finder.append(finderSummary,finderInput,finderStatus,finderResults);
 const status=document.createElement('p');status.className='money-sankey-status';status.setAttribute('role','status');
 layer.append(chart,bands);canvas.append(layer);stage.append(canvas);root.replaceChildren(viewControls,controls,jump,explore,finder,stage,status);
 let directory,p,geometry,disposed=false,selected=null,zoom=1,finderOffset=0;
 const sheet=[...document.styleSheets].find(item=>item.ownerNode?.id==='plotly.js-style-global');
 if(!sheet)throw new Error('Finance Sankey stylesheet is unavailable');
 const styleRules=[];let styleNumber=0;
 const layoutStyle=(node,properties)=>{
  const name=`finance-sankey-layout-${styleNumber++}`;node.classList.add(name);
  const declarations=Object.entries(properties).map(([key,value])=>`${key}:${value}`).join(';');
  styleRules.push(sheet.cssRules.length);sheet.insertRule(`.${name}{${declarations}}`,sheet.cssRules.length);
 };
 const clearLayoutStyles=()=>{for(const index of styleRules.splice(0).reverse())sheet.deleteRule(index);styleNumber=0;};
 const control=(label,action,ariaLabel=label)=>{const button=document.createElement('button');button.type='button';button.textContent=label;button.setAttribute('aria-label',ariaLabel);button.addEventListener('click',action);return button;};
 const zoomOut=control('−',()=>{zoom=Math.max(.65,Math.round((zoom-.15)*100)/100);void draw();},'Zoom out');
 const zoomLabel=document.createElement('output');zoomLabel.setAttribute('aria-live','polite');
 const zoomIn=control('+',()=>{zoom=Math.min(1.5,Math.round((zoom+.15)*100)/100);void draw();},'Zoom in');
 const fit=control('Fit',()=>{zoom=1;stage.scrollTo({left:0,top:0});void draw();},'Fit map to view');
 const pan=(label,left,top)=>control(label,()=>stage.scrollBy({left,top,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'}),`Pan ${label==='←'?'left':label==='→'?'right':label==='↑'?'up':'down'}`);
 viewControls.append(zoomOut,zoomLabel,zoomIn,fit,pan('←',-180,0),pan('↑',0,-180),pan('↓',0,180),pan('→',180,0));
 const membersFor=selection=>{
  if(Array.isArray(selection?.memberIds)){
   const wanted=new Set(selection.memberIds),found=new Map();
   const visit=member=>{if(!member||found.has(member.id))return;if(wanted.has(member.id))found.set(member.id,member);for(const nested of member.members||[])visit(nested);};
   for(const edge of [...p.edges,...p.internalEdges])for(const member of edge.members||[])visit(member);
   return [...found.values()];
  }
  return selectionMembers(selection.view||p,selection);
 };
 const toggleNode=node=>{
  if(!node?.expandable)return;
  const anchor=geometry?.bands.find(band=>band.id===node.groupId);if(anchor)anchors[node.groupId]=anchor.top;
  const keep=selected;expanded.splice(0,expanded.length,...model.toggle(expanded,node.id));
  void draw().then(()=>{if(keep)select(keep);});
 };
 let exploreMode='spending';
 const renderExplore=()=>{
  const modes=[['accounts','Accounts'],['spending','Spending'],['categories','All categories']];
  exploreTabs.replaceChildren(...modes.map(([id,label])=>{const tab=control(label,()=>{exploreMode=id;renderExplore();},label);tab.setAttribute('role','tab');tab.setAttribute('aria-selected',String(id===exploreMode));return tab;}));
  const list=[];
  if(exploreMode==='accounts')for(const group of p.accountGroups)for(const node of directory.nodes.filter(n=>n.groupId===group.id))list.push(control(node.label,()=>{const members=[...new Map([...p.edges,...p.internalEdges].flatMap(edge=>edge.members||[]).filter(member=>member.source===node.id||member.target===node.id).map(member=>[member.id,member])).values()];select({memberIds:members.map(member=>member.id),label:node.label});open(members);},`Inspect account ${node.label}`));
  if(exploreMode==='spending')for(const row of ranked(p)){
   const currency=raw.currency,fmt=value=>new Intl.NumberFormat('en-CA',{style:'currency',currency}).format(value/100);
   const button=control(`${row.label} · ${fmt(row.net)} net`,()=>{select({edges:row.edges.map(edge=>edge.id),label:row.label});open(row.edges);},`Inspect net spending ${row.label} · gross ${fmt(row.gross)} · refunds ${fmt(row.offset)}`);
   button.title=`Gross ${fmt(row.gross)} · refunds ${fmt(row.offset)} · net ${fmt(row.net)}`;
   button.dataset.gross=String(row.gross);button.dataset.offset=String(row.offset);list.push(button);
  }
  if(exploreMode==='categories')for(const node of p.nodes.filter(n=>!account(n))){
   const row=document.createElement('div');row.className='money-sankey-explore-row';
   row.append(control(`${node.label} · ${node.kind.replaceAll('-',' ')}`,()=>{const edges=[...p.edges,...p.internalEdges].filter(edge=>edge.source===node.id||edge.target===node.id);select({node:node.id,label:node.label});open(edges);},`Inspect ${node.label}`));
   if(node.expandable){const toggle=control(node.expanded?'Fold':'Expand',()=>toggleNode(node),`${node.expanded?'Fold':'Expand'} ${node.label}`);toggle.setAttribute('aria-expanded',String(!!node.expanded));row.append(toggle);}
   list.push(row);
  }
  exploreList.replaceChildren(...list);
 };
 const chooseGroup=(id,label)=>{const selectedGroup={group:id,label};select(selectedGroup);open(membersFor(selectedGroup));};
 const select=next=>{
  selected=next?{...next,memberIds:membersFor(next).map(member=>member.id)}:null;
  status.textContent=next?.label||'';
  bands.querySelectorAll('[data-group-select]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.groupSelect===selected?.group)));
  const memberIds=new Set(selected?membersFor(selected).map(member=>member.id):[]),edges=p.edges.filter(edge=>edge.members?.some(member=>memberIds.has(member.id))),edgeIds=new Set(edges.map(edge=>edge.id)),nodeIds=new Set(edges.flatMap(edge=>[edge.source,edge.target]));
  chart.querySelectorAll('.sankey-node').forEach(node=>node.classList.toggle('is-dim',!!next&&!nodeIds.has(node.dataset.node)));
  chart.querySelectorAll('.sankey-link').forEach(link=>link.classList.toggle('is-selected',!!selected&&edgeIds.has(link.dataset.edge)));
 };
 const open=edges=>{
  const streams=edges.flatMap(edge=>Number.isSafeInteger(edge.amount)&&Array.isArray(edge.members)?edge.members:[edge]);
  const members=[...new Map(streams.map(edge=>[edge.id,edge])).values()];
  if(!members.length)return;
  const projectionIds=[...new Set(edges.filter(edge=>Number.isSafeInteger(edge.amount)&&Array.isArray(edge.members)).map(edge=>edge.id))];
  const edge={id:'sankey-selection:'+members.map(member=>member.id).join('|'),aggregateSelection:true,projectionIds,label:members.length===1?members[0].label:`${members.length} exact streams`,amount_minor:members.reduce((sum,member)=>sum+(Number.isSafeInteger(member.amount_minor)?member.amount_minor:0),0),currency:members[0].currency,members};
  inspect(edge);
 };
 const rebind=edge=>{
  const ids=new Set(edge?.projectionIds||[]);if(!ids.size)return null;
  const members=[...new Map([...p.edges,...p.internalEdges].filter(item=>ids.has(item.id)).flatMap(item=>item.members||[]).map(member=>[member.id,member])).values()];
  if(!members.length)return null;
  return {...edge,id:'sankey-selection:'+members.map(member=>member.id).join('|'),label:members.length===1?members[0].label:`${members.length} exact streams`,members,amount_minor:members.reduce((sum,member)=>sum+(Number.isSafeInteger(member.amount_minor)?member.amount_minor:0),0),currency:members[0].currency||edge.currency};
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
  jump.onchange=()=>{const band=geometry.bands.find(item=>item.id===jump.value);if(band)stage.scrollTo({top:band.top*zoom,left:band.x*geometry.width*zoom,behavior:'smooth'});};
 };
 const renderFinder=()=>{
  const term=finderInput.value.trim().toLocaleLowerCase(),streams=[...new Map(p.edges.flatMap(edge=>edge.members||[edge]).map(edge=>[edge.id,edge])).values()],matches=term?streams.filter(edge=>`${edge.label} ${edge.source} ${edge.target}`.toLocaleLowerCase().includes(term)):[];
  finderOffset=Math.min(finderOffset,Math.max(0,Math.floor((matches.length-1)/25)*25));
  const shown=matches.slice(finderOffset,finderOffset+25);
  finderStatus.textContent=term?`${matches.length?finderOffset+1:0}–${Math.min(finderOffset+shown.length,matches.length)} of ${matches.length} matching movements.`:'Search to choose an exact movement when streams are hard to distinguish.';
  const previous=control('Previous',()=>{finderOffset=Math.max(0,finderOffset-25);renderFinder();},'Previous matching movements');previous.disabled=finderOffset===0;
  const next=control('Next',()=>{finderOffset=Math.min(Math.max(0,matches.length-1),finderOffset+25);renderFinder();},'Next matching movements');next.disabled=finderOffset+25>=matches.length;
  finderResults.replaceChildren(...shown.map(edge=>control(`${edge.label||'Recorded movement'} · ${new Intl.NumberFormat('en-CA',{style:'currency',currency:edge.currency}).format(edge.amount_minor/100)}`,()=>{select({memberIds:[edge.id],label:edge.label});open([edge]);},`Inspect ${edge.label||'recorded movement'}`)),previous,next);
 };
 finderInput.addEventListener('input',()=>{finderOffset=0;renderFinder();});
 async function draw(){
  if(disposed)return;clearLayoutStyles();
  directory=model.project(expanded,[]);p=model.project(expanded,collapsed);
  const active=new Set(p.edges.flatMap(edge=>[edge.source,edge.target])),nodes=p.nodes.filter(node=>active.has(node.id));
  if(!nodes.length||!p.edges.length){try{Plotly.purge(chart);}catch{}chart.replaceChildren();chart.textContent='No recorded movements match these filters.';geometry={bands:[],width:Math.max(720,stage.clientWidth||900),height:280};layoutStyle(canvas,{minHeight:'280px'});renderBands();groupControls();renderFinder();renderExplore();zoomLabel.textContent=`${Math.round(zoom*100)}%`;return;}
  const projected={...p,nodes},width=Math.max(720,stage.clientWidth||900);
  geometry=groupedLayout(projected,{width,anchors});
  const height=geometry.height,index=new Map(nodes.map((node,i)=>[node.id,i]));
  layoutStyle(canvas,{width:`${geometry.width*zoom}px`,height:`${height*zoom}px`});
  layoutStyle(layer,{width:`${geometry.width}px`,height:`${height}px`,transform:`scale(${zoom})`});
  layoutStyle(chart,{width:`${geometry.width}px`,height:`${height}px`});
  renderBands();groupControls();renderFinder();renderExplore();zoomLabel.textContent=`${Math.round(zoom*100)}%`;
  const labels=nodes.map(node=>`${html(node.label)}<br>${account(node)?'Account':html(node.kind.replaceAll('-',' '))}<br>${html(new Intl.NumberFormat('en-CA',{style:'currency',currency:raw.currency}).format((node.value??Math.max(node.incoming,node.outgoing))/100))}`);
  const trace={type:'sankey',arrangement:'fixed',orientation:'h',valueformat:'.2f',valueprefix:raw.currency+' ',textfont:{family:'system-ui, sans-serif',size:12,color:'#22332f'},node:{x:nodes.map(node=>geometry.slots[node.id].x),y:nodes.map(node=>(geometry.slots[node.id].top+geometry.slots[node.id].bottom)/(2*height)),align:'left',pad:4,thickness:16,line:{width:0},label:labels,color:nodes.map(node=>color[node.displayKind||node.kind]||color.unresolved),customdata:nodes.map(node=>[node.id,html(node.label)]),hovertemplate:'%{customdata[1]}<extra></extra>'},link:{source:p.edges.map(edge=>index.get(edge.source)),target:p.edges.map(edge=>index.get(edge.target)),value:p.edges.map(edge=>edge.amount/100),color:p.edges.map(edge=>`${color[edge.displayKind||edge.type]||color.unresolved}55`),customdata:p.edges.map(edge=>[edge.id,html((nodes[index.get(edge.source)]?.label||'')+' → '+(nodes[index.get(edge.target)]?.label||''))]),hovertemplate:'%{customdata[1]}<extra></extra>'}};
  const layout={width:geometry.width,height,margin:{l:12,r:12,t:12,b:12},paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',font:{family:'system-ui, sans-serif',color:'#202b2b'}};
  await Plotly.react(chart,[trace],layout,{displayModeBar:false,responsive:false});
  if(disposed)return;
  chart.removeAllListeners?.('plotly_click');
  let lastNodeTap=null;
  chart.on('plotly_click',event=>{
   const point=event.points?.[0];if(!point)return;
   if(point.source!==undefined){const edge=p.edges.find(item=>item.id===point.customdata?.[0]);if(edge){select({edges:[edge.id],label:edge.label});open([edge]);}return;}
   const node=p.nodes.find(item=>item.id===point.customdata?.[0]);if(node){const doubleTap=point.originalEvent?.detail>=2||(lastNodeTap?.id===node.id&&Date.now()-lastNodeTap.at<380);lastNodeTap={id:node.id,at:Date.now()};if(node.expandable&&doubleTap){toggleNode(node);return;}const edges=[...p.edges,...p.internalEdges].filter(edge=>edge.source===node.id||edge.target===node.id);select({node:node.id,label:node.label});open(edges);}
  });
  chart.querySelectorAll('.sankey-link').forEach((link,index)=>{const edge=p.edges[index];if(!edge)return;link.dataset.edge=edge.id;link.setAttribute('tabindex','0');link.setAttribute('role','button');link.setAttribute('aria-label',`Inspect ${edge.label||'recorded movement'}`);link.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();select({edges:[edge.id],label:edge.label});open([edge]);}});});
  chart.querySelectorAll('.sankey-node').forEach((node,index)=>{const item=nodes[index];if(!item)return;node.dataset.node=item.id;node.setAttribute('tabindex','0');node.setAttribute('role','button');node.setAttribute('aria-label',`Inspect ${item.label}`);node.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();const edges=[...p.edges,...p.internalEdges].filter(edge=>edge.source===item.id||edge.target===item.id);select({node:item.id,label:item.label});open(edges);}});});
  if(selected)select(selected);
 }
 const onResize=()=>{if(!disposed)void draw();};window.addEventListener('resize',onResize);void draw();
 return {model,rebind,destroy(){if(disposed)return;disposed=true;window.removeEventListener('resize',onResize);clearLayoutStyles();try{Plotly.purge(chart);}catch{}root.replaceChildren();},select,snapshot(){return {expanded:[...expanded],collapsed:[...collapsed],anchors:{...anchors},zoom,exploreMode,selected:selected?{...selected}:null,exploreOpen:explore.open,finderOpen:finder.open,finderQuery:finderInput.value,finderOffset,scrollTop:stage.scrollTop,scrollLeft:stage.scrollLeft};},restore(state){if(!state||disposed)return;expanded.splice(0,expanded.length,...(Array.isArray(state.expanded)?state.expanded.filter(id=>model.kind(id)!==undefined):[]));const groups=new Set(model.project().accountGroups.map(group=>group.id));collapsed.splice(0,collapsed.length,...(Array.isArray(state.collapsed)?state.collapsed.filter(id=>groups.has(id)):[]));for(const [id,top] of Object.entries(state.anchors||{}))if(groups.has(id)&&Number.isFinite(top))anchors[id]=top;zoom=Number.isFinite(state.zoom)?Math.min(1.5,Math.max(.65,state.zoom)):1;exploreMode=['accounts','spending','categories'].includes(state.exploreMode)?state.exploreMode:'spending';explore.open=!!state.exploreOpen;finder.open=!!state.finderOpen;finderInput.value=typeof state.finderQuery==='string'?state.finderQuery:'';finderOffset=Number.isSafeInteger(state.finderOffset)?Math.max(0,state.finderOffset):0;selected=state.selected||null;if(selected?.group&&!groups.has(selected.group))selected=null;if(selected&&!selected.group&&!selected.node&&!Array.isArray(selected.memberIds))selected=null;void draw().then(()=>{if(selected){const members=membersFor(selected);if(Array.isArray(selected.memberIds)&&!members.length&&!selected.group)selected=null;else select(selected);}stage.scrollTop=Number.isFinite(state.scrollTop)?state.scrollTop:0;stage.scrollLeft=Number.isFinite(state.scrollLeft)?state.scrollLeft:0;});}};
}
