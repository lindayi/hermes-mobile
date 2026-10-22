import {createModel,ranked,selectionMembers} from './live-model.mjs';
import {makeAPI,lastQuarter,createPager,evidenceURL} from './finance-client.mjs';
import {loadCADQuarter,presentationEquivalent} from './cad-view.mjs';
import {groupedLayout} from './layout.mjs';
const $=s=>document.querySelector(s),el=(tag,text,attrs={})=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);return n;};
const button=(text,action,attrs={})=>{const b=el('button',text,{type:'button',...attrs});b.onclick=action;return b;};
const html=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const api=makeAPI(),range=lastQuarter();const currency='CAD';let fx=null,foreignProjection=null,model=null,p=null,directory=null,expanded=[],collapsed=[],focus='',selection=null,pager=null;
let paused=false,resumeVersion=0,resumeController=null,resumeDetails=false;
let groupGeometry=null,layoutAnchors={};
let epoch=0,detailVersion=0,drawVersion=0,loadController=null,detailController=null,chart=$('#chart'),initialized=false,drawQueue=Promise.resolve(),lastTap=null,detailOrigin=null,pendingTapTimer=null;
const colors={'product':'#7a7c9c','product-group':'#7a7c9c',income:'#327b65','employer-match':'#367662','payroll-saving':'#796496','portfolio-trade':'#75678f',tax:'#b18655','payroll-deduction':'#ad8766','payroll-net':'#397f79',reimbursement:'#548d78',payroll:'#327b65','investment-income':'#327b65',recovery:'#548d78',account:'#69849c','account-group':'#69849c',expense:'#b36d45',transfer:'#92956e',investment:'#7a7c9c',financing:'#8f7470',adjustment:'#9a8e6c',unresolved:'#929292'};
const rgb={income:'50,123,101','employer-match':'54,118,98','payroll-saving':'121,100,150','portfolio-trade':'117,103,143',tax:'177,134,85','payroll-deduction':'173,135,102','payroll-net':'57,127,121',reimbursement:'84,141,120',payroll:'50,123,101','investment-income':'50,123,101',recovery:'84,141,120',transfer:'105,132,156',expense:'179,109,69',investment:'122,124,156',financing:'143,116,112',adjustment:'154,142,108',unresolved:'145,145,145'};
const money=(n,unit=currency)=>Number.isFinite(n)?new Intl.NumberFormat('en-CA',{style:'currency',currency:unit,minimumFractionDigits:2,maximumFractionDigits:2}).format(n/100):'—';
const accountNode=n=>n?.kind==='account'||n?.kind==='account-group';
$('#period').textContent=range.start+' — '+range.end+' · Last quarter · Personal accounts';
function clearPrivate(message=''){
 groupGeometry=null;layoutAnchors={};
 paused=false;resumeVersion++;resumeController?.abort();resumeDetails=false;document.body.classList.remove('private-paused');document.body.dataset.paused='false';
 clearTimeout(pendingTapTimer);pendingTapTimer=null;epoch++;drawVersion++;detailVersion++;loadController?.abort();detailController?.abort();model=null;p=null;directory=null;fx=null;foreignProjection=null;expanded=[];collapsed=[];focus='';selection=null;pager=null;lastTap=null;detailOrigin=null;initialized=false;drawQueue=Promise.resolve();
 const old=chart;chart=old.cloneNode(false);old.replaceWith(chart);try{Plotly.purge(old);}catch{}old.replaceChildren();
 $('#payroll-summary').hidden=true;
 for(const id of ['#rows','#detail','#crumbs','#selection','#accounts','#bands','#enclosures','#notes','#categories','#map-group','#foreign'])$(id).replaceChildren();$('#detail').hidden=true;$('#detail').removeAttribute('role');$('#focus').replaceChildren(el('option','Spending destinations',{value:''}));
 for(const id of ['#income','#investment-income','#employer-match','#spending','#ranking-total','#payroll-gross','#payroll-tax','#payroll-rrsp','#payroll-espp','#payroll-employer'])$(id).textContent='—';$('#coverage').textContent='';$('#denominator').textContent='';$('#ranking-title').textContent='Where spending goes';$('#data-state').replaceChildren(el('span',message));document.body.dataset.ready='false';
}
function suspend(){
 clearTimeout(pendingTapTimer);pendingTapTimer=null;lastTap=null;resumeVersion++;resumeController?.abort();
 if(!model||!initialized){clearPrivate('Private view paused.');paused=true;}else{
  resumeDetails=!!pager&&!!$('#detail .pager')?.textContent.includes('Loading');detailVersion++;detailController?.abort();
 }
 paused=true;document.body.classList.add('private-paused');document.body.dataset.paused='true';$('#data-state').textContent='Private view paused.';
}
async function resume(){
 if(document.hidden||!paused)return;
 if(!model||!initialized){await load();return;}
 const token=epoch,version=++resumeVersion;resumeController?.abort();resumeController=new AbortController();$('#data-state').textContent='Checking access…';
 try{
  const access=await api('compensation',{...range,currency:'CAD',scope:'personal',dataset:'all'},resumeController.signal);
  if(!Array.isArray(access?.payroll)||!access.coverage?.payroll)throw Error('Invalid finance access response');
  if(token!==epoch||version!==resumeVersion||document.hidden)return;
  paused=false;document.body.classList.remove('private-paused');document.body.dataset.paused='false';$('#data-state').replaceChildren();
  if(resumeDetails&&pager){resumeDetails=false;void moreDetails();}
 }catch(e){
  if(token!==epoch||version!==resumeVersion||document.hidden)return;
  if([401,403].includes(e.status)){locked();return;}
  if(e.name==='AbortError')return;
  $('#data-state').replaceChildren(el('span','Unable to check access.'),button('Retry',resume));
 }
}
function locked(){clearPrivate('Finance is locked.');$('#data-state').append(el('a','Sign in with passkey',{href:'/hermes/'}),button('Retry after sign-in',load));}
async function load(){
 clearPrivate('Loading last quarter…');if(document.hidden)return;const token=epoch;loadController=new AbortController();
 try{const raw=await loadCADQuarter(api,{...range,scope:'personal',dataset:'all'},loadController.signal);if(token!==epoch||document.hidden)return;
  if(raw.currency!==currency||raw.range?.start!==range.start||raw.range?.end!==range.end)throw Error('Unexpected finance period');fx=raw.fx;foreignProjection=raw.foreignUnconverted?.edges?.length?createModel(raw.foreignUnconverted).project():null;renderForeign();model=createModel(raw);collapsed=model.project().accountGroups.map(g=>g.id);
  const coverage=raw.payrollCoverage;$('#payroll-summary').hidden=!coverage?.detailed;$('#coverage').textContent=(raw.coverage?.complete?'':'Partial data · ')+currency+(coverage?' · Payslips: '+coverage.count+' available, '+coverage.linked+' linked to deposits.':' · Payslip coverage unavailable.');
  for(const note of raw.notes||[])$('#notes').append(el('p',typeof note==='string'?note:note.message||note.label||''));$('#data-state').replaceChildren();await draw();
 }catch(e){if(token!==epoch||document.hidden)return;if([401,403].includes(e.status)){locked();return;}if(e.name==='AbortError')return;clearPrivate('Unable to load this quarter.');$('#data-state').append(button('Retry',load));}
}
function groupNodeIds(view,id){return new Set([id,...view.nodes.filter(n=>n.groupId===id).map(n=>n.id)]);}
function groupNode(view,id){const g=view.accountGroups.find(g=>g.id===id);if(!g)return null;const owned=groupNodeIds(view,id),edges=[...view.edges,...(view.internalEdges||[])].filter(e=>!e.decomposition);return {...g,kind:'account-group',externalIncoming:edges.filter(e=>owned.has(e.target)&&!owned.has(e.source)).reduce((s,e)=>s+e.amount,0),externalOutgoing:edges.filter(e=>owned.has(e.source)&&!owned.has(e.target)).reduce((s,e)=>s+e.amount,0),internalAmount:edges.filter(e=>owned.has(e.source)&&owned.has(e.target)).reduce((s,e)=>s+e.amount,0)};}
function selectedEdges(){if(!p||!selection)return [];const view=selection.group?p:selection.view||p,owned=selection.group?groupNodeIds(view,selection.group):null;return [...view.edges,...(view.internalEdges||[])].filter(e=>owned?owned.has(e.source)||owned.has(e.target):selection.node?e.source===selection.node||e.target===selection.node:selection.edges.includes(e.id));}
function selectGroup(id){if(!p?.accountGroups.some(g=>g.id===id))return;focus='';beginSelection({group:id,node:'',edges:[],label:p.accountGroups.find(g=>g.id===id).label});}
function redrawRetainingGroup(change,anchorId){layoutAnchors={};const anchor=groupGeometry?.bands.find(b=>b.id===anchorId);if(anchor)layoutAnchors[anchor.id]=anchor.top;const group=selection?.group;stopDetails();const version=detailVersion,token=epoch;change();void draw().then(()=>{if(group&&token===epoch&&version===detailVersion)selectGroup(group);});}
function stopDetails(restore=false){clearTimeout(pendingTapTimer);pendingTapTimer=null;detailVersion++;detailController?.abort();pager=null;selection=null;$('#detail').replaceChildren();$('#detail').hidden=true;$('#selection').textContent='';if(restore&&detailOrigin?.isConnected)detailOrigin.focus({preventScroll:true});detailOrigin=null;}
function toggle(id){if(!model)return;const n=p.nodes.find(n=>n.id===id);if(!n?.expandable)return;redrawRetainingGroup(()=>{lastTap=null;expanded=model.toggle(expanded,id);focus=expanded.includes(id)?id:'';},n.groupId);}
function toggleGroup(id){if(!model)return;redrawRetainingGroup(()=>{lastTap=null;collapsed=model.toggleGroup(collapsed,id);focus='';},id);}
function selectNode(id){if(p?.accountGroups.some(g=>g.id===id)){selectGroup(id);return;}const view=p?.nodes.some(n=>n.id===id)?p:directory,node=view?.nodes.find(n=>n.id===id);if(!node)return;focus=node.outgoing?id:'';beginSelection({node:id,edges:[],label:node.label,view});}
function selectEdges(edges,label,summary=null,view=p){beginSelection({node:'',edges:edges.map(e=>e.id),label,summary,view});}
function beginSelection(next){
 const origin=document.activeElement;stopDetails();detailOrigin=origin;selection=next;const view=next.view||p,members=selectionMembers(view,next),node=next.group?groupNode(p,next.group):view.nodes.find(n=>n.id===next.node);pager=createPager(members,api,node?.kind==='account-group'?node.accounts:node?.kind==='account'?[node.account||model.name(node.id)]:'');
 sidebar();detailHeader();void highlight();void moreDetails();
}
function showList(name){for(const tab of ['accounts','spending','categories']){$('#'+tab+'-panel').hidden=name!==tab;$('#'+tab+'-tab').setAttribute('aria-pressed',String(name===tab));}}
function renderAccounts(){
 const container=$('#accounts');container.replaceChildren();for(const group of p.accountGroups||[]){const box=el('section',null,{class:'account-group','data-role':group.role}),head=el('div',null,{class:'group-heading'});head.append(button(group.label+' · '+group.count,()=>selectGroup(group.id),{'aria-label':'Inspect '+group.label,'aria-pressed':String(selection?.group===group.id),class:'group-label','data-group-select':group.id}),button((group.collapsed?'Expand ':'Fold ')+group.label,()=>toggleGroup(group.id),{'aria-expanded':String(!group.collapsed),class:'group-toggle'}));box.append(head);

 for(const n of directory.nodes.filter(n=>n.groupId===group.id)){const b=button('',()=>selectNode(n.id),{'data-account-select':n.id,'aria-label':'Inspect '+n.label,class:'account-select'});b.append(el('span',n.label),el('small',(isApproximate(n,directory)?'≈ CAD · ':'')+'In '+money(n.incoming)+' · Out '+money(n.outgoing)));box.append(b);}container.append(box);}
}
function categoryLabel(n){return n.kind==='product-group'?n.label+' · '+(directory?.nodes.find(a=>a.id===n.ownerAccountId)?.label||'Investment account'):n.label;}
function renderCategories(){
 const box=$('#categories');box.replaceChildren();for(const n of p.nodes.filter(n=>!accountNode(n)).sort((a,b)=>a.label.localeCompare(b.label))){const row=el('div',null,{class:'category-row'}),b=button('',()=>selectNode(n.id),{class:'account-select','aria-label':'Inspect '+categoryLabel(n)});b.append(el('span',categoryLabel(n)),el('small',(isApproximate(n)?'≈ CAD · ':'')+money(n.value??Math.max(n.incoming,n.outgoing))+' · '+n.kind.replaceAll('-',' ')));row.append(b);if(n.expandable)row.append(button((n.expanded?'Fold ':'Break down ')+categoryLabel(n),()=>toggle(n.id),{class:'drill','aria-expanded':String(n.expanded)}));box.append(row);}
}
function sidebar(){
 if(!model||!p)return;const current=model,focusSelect=$('#focus');focusSelect.replaceChildren(el('option','Spending destinations',{value:''}));for(const n of p.nodes.filter(n=>n.outgoing>0))focusSelect.append(el('option','From '+n.label,{value:n.id}));focusSelect.value=focus;
 const rankingView=selection?.view===foreignProjection?p:selection?.view||p;const rows=ranked(rankingView,focus),total=rows.reduce((n,r)=>n+r.amount,0);$('#ranking-title').textContent=focus?'From '+(p.nodes.find(n=>n.id===focus)?.label||current.name(focus)):'Where spending goes';$('#ranking-total').textContent=money(total);$('#denominator').textContent=focus?'Share of selected outgoing flows · transfers included':'Net spending after refunds · graph shows gross charges and separate recoveries';$('#rows').replaceChildren();
 const selected=new Set(selectedEdges().map(e=>e.id));for(const row of rows){const wrap=el('div',null,{class:'row'}),b=button('',()=>selectEdges(row.edges,row.label,row.gross===undefined?null:{net:row.amount,gross:row.gross,offset:row.offset},rankingView),{class:'amount-row','aria-pressed':String(row.edges.some(e=>selected.has(e.id))),'data-destination':row.id});const top=el('span',null,{class:'row-top'});top.append(el('span',row.label),el('strong',money(row.amount)));const track=el('span',null,{class:'bar-track'}),bar=el('span',null,{class:'bar'});bar.style.width=(Math.min(1,Math.max(0,row.share))*100)+'%';track.append(bar);b.append(top,track,el('span',(row.share*100).toFixed(1)+'%',{class:'share'}));wrap.append(b);
  const node=p.nodes.find(n=>n.id===row.id);if(node?.expandable)wrap.append(button((node.expanded?'Fold ':'Break down ')+row.label,()=>toggle(row.id),{class:'drill','aria-expanded':String(node.expanded),'data-drill':row.id}));$('#rows').append(wrap);
 }if(!rows.length)$('#rows').append(el('p','No outgoing flows.',{class:'empty'}));
 for(const b of document.querySelectorAll('[data-group-select]'))b.setAttribute('aria-pressed',String(b.dataset.groupSelect===selection?.group));
 for(const b of document.querySelectorAll('[data-account-select]'))b.setAttribute('aria-pressed',String(b.dataset.accountSelect===selection?.node));
}
function renderForeign(){
 const box=$('#foreign');box.replaceChildren();if(!foreignProjection)return;
 box.append(el('p','USD activity · CAD conversion unavailable'));
 const nodes=new Map(foreignProjection.nodes.map(n=>[n.id,n]));
 for(const e of foreignProjection.edges.filter(e=>!e.decomposition))box.append(button((nodes.get(e.source)?.label||'')+' → '+(nodes.get(e.target)?.label||'')+' · '+money(e.amount,'USD')+' USD',()=>{focus='';selectEdges([e],(nodes.get(e.source)?.label||'USD activity')+' · USD',null,foreignProjection);}));
}
function isApproximate(node,view=p){return !!node?.approximate||!!view?.edges.some(e=>(e.source===node?.id||e.target===node?.id)&&e.members.some(m=>m.approximate));}
function detailHeader(){
 if(!selection||!pager)return;const box=$('#detail'),n=selection.group?groupNode(p,selection.group):(selection.view||p).nodes.find(n=>n.id===selection.node),edges=selectedEdges();box.hidden=false;box.replaceChildren();const mobile=matchMedia('(max-width:760px)').matches;box.setAttribute('role',mobile?'dialog':'region');box.setAttribute('aria-label',selection.label+' details');const head=el('div',null,{class:'section-heading'});const close=button('×',()=>{stopDetails(true);sidebar();void highlight();},{'aria-label':'Close transactions'});head.append(el('h3',selection.label),close);box.append(head);
 const detailMoney=value=>money(value,(selection.view||p).currency||'CAD');
 const summary=n&&['product','product-group'].includes(n.kind)?'Bought '+detailMoney(n.activityIncoming??n.incoming)+' · Sold '+detailMoney(n.activityOutgoing??n.outgoing):selection.summary?'Net '+detailMoney(selection.summary.net)+' · Charges '+detailMoney(selection.summary.gross)+' · Refunds '+detailMoney(selection.summary.offset):accountNode(n)?'In '+detailMoney(n.externalIncoming??n.incoming)+' · Out '+detailMoney(n.externalOutgoing??n.outgoing):n?(n.kind==='expense'?'Gross charges ':'')+detailMoney(n.value??Math.max(n.incoming,n.outgoing)):detailMoney(edges.reduce((s,e)=>s+e.amount,0));box.append(el('p',summary+((selection.view||p).currency==='USD'?' USD':''),{class:'detail-total'}));
 if(edges.some(e=>e.members.some(m=>m.approximate)))box.append(el('p','Approximate CAD equivalent · USD/CAD '+fx?.rate+' · '+fx?.as_of+' · '+fx?.source,{class:'detail-note'}));
 if(n?.kind==='account-group'){const internal=n.internalAmount??(p.internalEdges||[]).filter(e=>e.source===n.id||e.target===n.id).reduce((s,e)=>s+e.amount,0);box.append(el('p','Within group '+detailMoney(internal)+' · internal transfers and product trades',{class:'detail-note'}),button((n.collapsed?'Expand ':'Fold ')+n.label,()=>toggleGroup(n.id)));}
 for(const note of new Set(selectionMembers(selection.view||p,selection).map(m=>m.evidenceNote).filter(Boolean)))box.append(el('p',note,{class:'evidence-note'}));
 if(n?.ownerAccountId)box.append(el('p','Held in '+(directory.nodes.find(a=>a.id===n.ownerAccountId)?.label||'Investment account')+' · amounts above are quarter trades',{class:'detail-note'}));
 appendHoldings(box,n);
 if(n?.expandable&&n.kind!=='account-group')box.append(button((n.expanded?'Fold ':'Break down ')+categoryLabel(n),()=>toggle(n.id),{'data-drill':n.id}));
 box.append(el('p','Original transactions',{class:'transaction-count'}),el('div',null,{class:'transaction-list',tabindex:0,role:'region','aria-label':'Original transaction rows'}),el('div',null,{class:'pager'}));$('#selection').textContent=selection.label+' · '+summary;if(mobile)close.focus({preventScroll:true});
}
function appendHoldings(box,node){
 if(!node)return;let observations=node.holdingObservations||[];
 if(node.kind==='account')observations=[...observations,...directory.nodes.filter(n=>n.ownerAccountId===node.id).flatMap(n=>n.holdingObservations||[])];
 if(node.kind==='account-group')observations=directory.nodes.filter(n=>n.groupId===node.id).flatMap(n=>n.holdingObservations||[]);
 const unique=[...new Map(observations.map(r=>[r.observation_id||JSON.stringify([r.account,r.security_key,r.as_of,r.currency]),r])).values()];if(!unique.length)return;
 const section=el('details',null,{class:'holding-details'});section.append(el('summary','Recorded holdings · dated separately from quarter trades'));const list=el('div',null,{class:'holding-list'});
 for(const row of unique){const item=el('article',null,{class:'holding-row'});item.append(el('strong',row.name||row.security_key||'Product'),el('p',(row.quantity?row.quantity+' '+(row.quantity_basis||'units')+' · ':'')+money(row.market_value_minor,row.currency)+' '+row.currency),el('p','As of '+row.as_of+' · '+row.account));const url=evidenceURL(row.drive_url);if(url)item.append(el('a','Holding source',{href:url,target:'_blank',rel:'noreferrer'}));list.append(item);}section.append(list);box.append(section);
}
function showRows(error=''){
 if(!pager||!selection)return;const list=$('#detail .transaction-list'),controls=$('#detail .pager');if(!list||!controls)return;list.replaceChildren();
 for(const row of pager.rows){const item=el('article',null,{class:'transaction-row','data-transaction-id':row.id}),top=el('div',null,{class:'tx-top'});top.append(el('time',row.date||'Date unavailable'),el('strong',(row.amount<0?'−':'+')+new Intl.NumberFormat('en-CA',{style:'currency',currency:row.currency}).format(Math.abs(row.amount)/100)+' '+row.currency,{class:'tx-amount'}));item.append(top,el('p',row.description,{class:'tx-description'}),el('p',String(row.account||'')+(row.side?' · '+row.side:'')+(row.category?' · '+row.category:''),{class:'tx-account'}));const equivalent=presentationEquivalent(row,fx);if(equivalent)item.append(el('p','≈ '+money(equivalent.amount_minor,'CAD')+' CAD · rate date '+equivalent.as_of,{class:'fx-equivalent'}));if(row.counterparty)item.append(el('p',(row.side==='debit'?'To ':'From ')+(directory?.nodes.find(n=>n.account===row.counterparty)?.label||row.counterparty),{class:'transfer-counterparty'}));if(row.recordBasis)item.append(el('p',row.recordBasis,{class:'record-basis'}));if(row.evidenceNote)item.append(el('p',row.evidenceNote,{class:'evidence-note'}));const url=evidenceURL(row.url);if(url)item.append(el('a','Source'+(row.page?' · page '+row.page:''),{href:url,target:'_blank',rel:'noreferrer'}));list.append(item);}
 controls.replaceChildren(el('span',error||pager.rows.length+' rows'+(pager.done?' · complete':' loaded')));if(!pager.done||error)controls.append(button(error?'Retry':'Load more',moreDetails));if(pager.done&&!pager.rows.length)list.append(el('p','No matching records.'));
}
async function moreDetails(){
 if(!pager)return;detailController?.abort();detailController=new AbortController();const version=++detailVersion,token=epoch,current=pager;$('#detail .pager')?.replaceChildren(el('span','Loading transactions…'));
 try{await current.more(detailController.signal);if(token!==epoch||version!==detailVersion||current!==pager)return;showRows();}
 catch(e){if(token!==epoch||version!==detailVersion)return;if([401,403].includes(e.status)){locked();return;}if(e.name!=='AbortError')showRows('Unable to load these records.');}
}
function linkColors(){const active=new Set(selectedEdges().flatMap(e=>e.members.map(m=>m.id)));return p.edges.map(e=>`rgba(${rgb[e.displayKind||e.type]||rgb.unresolved},${active.size?(e.members.some(m=>active.has(m.id))?.78:.09):.34})`);}
function highlight(){
 if(!initialized||!p)return;
 const members=new Set(selection?.view===foreignProjection?[]:selectedEdges().flatMap(e=>e.members.map(m=>m.id)));
 const activeEdges=p.edges.filter(e=>e.members.some(m=>members.has(m.id)));
 const edgeIds=new Set(activeEdges.map(e=>e.id)),nodeIds=new Set(activeEdges.flatMap(e=>[e.source,e.target]));
 if(selection?.node&&selection.view!==foreignProjection)nodeIds.add(selection.node);if(selection?.group)for(const id of groupNodeIds(p,selection.group))nodeIds.add(id);
 for(const n of chart.querySelectorAll('.sankey-node')){const id=n.__data__?.node?.customdata?.[0];n.classList.toggle('is-dim',!!selection&&!nodeIds.has(id));}
 for(const n of chart.querySelectorAll('.sankey-link')){const id=n.__data__?.link?.customdata?.[0];n.classList.toggle('is-dim',!!selection&&!edgeIds.has(id));n.classList.toggle('is-selected',!!selection&&edgeIds.has(id));}
}
function renderBands(geometry){
 $('#bands').replaceChildren();$('#enclosures').replaceChildren();const jump=$('#map-group');jump.replaceChildren(el('option','Choose a group',{value:''}));
 for(const band of geometry.bands){const left=12+band.x*(geometry.width-24)-10,wrap=el('div',null,{class:'band-control'}),select=button(band.label+' · '+band.count,()=>selectGroup(band.id),{'aria-label':'Select '+band.label+' group','aria-pressed':String(selection?.group===band.id),'data-group-select':band.id,class:'group-label'}),toggle=button(band.collapsed?'▸':'▾',()=>toggleGroup(band.id),{'aria-expanded':String(!band.collapsed),'aria-label':(band.collapsed?'Expand ':'Fold ')+band.label+' on map',class:'band-toggle'});wrap.append(select,toggle);wrap.style.left=left+'px';wrap.style.top=(band.top+4)+'px';$('#bands').append(wrap);
 if(!band.collapsed){const area=el('div',null,{class:'group-enclosure','data-role':band.role,'data-group':band.id,'aria-hidden':'true'});area.style.left=Math.max(0,left-8)+'px';area.style.top=band.top+'px';area.style.width=(12+band.right*(geometry.width-24)-Math.max(0,left-8))+'px';area.style.height=(band.bottom-band.top)+'px';$('#enclosures').append(area);}
 const option=el('option',band.label+(band.active?'':' · no movement'),{value:band.id});option.dataset.top=String(band.top);option.dataset.left=String(left);jump.append(option);}
 jump.onchange=()=>{const option=jump.selectedOptions[0];if(!option?.value)return;const scroll=$('.chart-scroll');scroll.scrollTop=Number(option.dataset.top);scroll.scrollLeft=Math.max(0,Number(option.dataset.left)-scroll.clientWidth/3);};
}
async function settlePlot(target,token){
 const deadline=performance.now()+4000;
 while(token===epoch&&target===chart){
  const moving=[...target.querySelectorAll('.sankey,.sankey-node,.sankey-link')].some(n=>n.__transition__||n.__transition);
  if(!moving)return;
  if(performance.now()>deadline)throw Error('Flow transition did not settle');
  await new Promise(requestAnimationFrame);
 }
}
function draw(){const token=epoch,version=++drawVersion;document.body.dataset.ready='false';drawQueue=drawQueue.then(async()=>{
 if(token!==epoch||version!==drawVersion||!model)return;const current=model;directory=current.project(expanded,[]);p=current.project(expanded,collapsed);const totals=current.totals();$('#income').textContent=money(totals.netPay);$('#investment-income').textContent=money(totals.investmentIncome);$('#employer-match').textContent=money(totals.employerMatch);$('#spending').textContent=money(totals.spending);for(const [id,key] of [['payroll-gross','payrollGross'],['payroll-tax','payrollTax'],['payroll-rrsp','payrollEmployeeRRSP'],['payroll-espp','payrollESPP'],['payroll-employer','payrollEmployerRRSP']])$('#'+id).textContent=money(totals[key]);renderAccounts();renderCategories();sidebar();
 $('#crumbs').replaceChildren(button('All flows',()=>{stopDetails();layoutAnchors={};expanded=[];collapsed=model.project().accountGroups.map(g=>g.id);focus='';void draw();}));for(const id of expanded)$('#crumbs').append(button(current.name(id)+' ×',()=>toggle(id)));
 const target=chart;if(!p.edges.length){target.replaceChildren(el('p','No recorded flows for this period. Accounts are available in the list.',{class:'empty'}));document.body.dataset.ready='true';return;}
  // Plotly drops zero-value nodes before indexing x/y. Filter only the trace,
 // not the accessible account list or the stable role-band geometry.
 const active=new Set(p.edges.flatMap(e=>[e.source,e.target])),drawNodes=p.nodes.filter(n=>active.has(n.id));
 const mapProjection={...p,nodes:drawNodes},baseWidth=Math.max(720,$('.chart-scroll').clientWidth),geometry=groupedLayout(mapProjection,{width:baseWidth,anchors:layoutAnchors}),{width,height}=geometry,flowHeight=Math.max(200,Math.max(...[...new Set(geometry.x)].map(x=>geometry.x.filter(v=>v===x).length))*4+160);target.style.width=width+'px';target.style.height=height+'px';renderBands(geometry);
 const short=s=>s.length>30?s.slice(0,19)+'…'+s.slice(-9):s;
 const label=n=>{const approx=isApproximate(n)?'≈ ':'';const text=short(n.label),limit=accountNode(n)?25:18,words=text.split(' '),lines=[''];for(const word of words){if(lines.at(-1)&&lines.at(-1).length+word.length+1>limit)lines.push(word);else lines[lines.length-1]+=(lines.at(-1)?' ':'')+word;}return lines.map(html).join('<br>')+(n.expandable?(n.expanded?' ▾':' ▸'):'')+(['product','product-group'].includes(n.kind)?'<br>'+html('Buy '+approx+money(n.activityIncoming??n.incoming))+'<br>'+html('Sell '+approx+money(n.activityOutgoing??n.outgoing)):accountNode(n)?'<br>'+html('In '+approx+money(n.externalIncoming??n.incoming))+'<br>'+html('Out '+approx+money(n.externalOutgoing??n.outgoing)):'<br>'+html(approx+money(n.value??Math.max(n.incoming,n.outgoing))));};
 const index=new Map(drawNodes.map((n,i)=>[n.id,i]));const trace={type:'sankey',domain:{y:[1-flowHeight/height,1]},arrangement:'fixed',orientation:'h',valueformat:'.2f',valueprefix:currency+' ',textfont:{family:'Arial, sans-serif',size:12,color:'#22332f'},node:{x:drawNodes.map(n=>geometry.slots[n.id].x),y:drawNodes.map(n=>(geometry.slots[n.id].top+geometry.slots[n.id].bottom)/2/flowHeight),align:'left',pad:4,thickness:16,line:{width:0},label:drawNodes.map(label),color:drawNodes.map(n=>colors[n.kind]||colors.unresolved),customdata:drawNodes.map(n=>[n.id,html(n.label)]),hovertemplate:'%{customdata[1]}<extra></extra>'},link:{source:p.edges.map(e=>index.get(e.source)),target:p.edges.map(e=>index.get(e.target)),value:p.edges.map(e=>e.amount/100),color:linkColors(),customdata:p.edges.map(e=>[e.id,html((drawNodes[index.get(e.source)]?.label||'')+' → '+(drawNodes[index.get(e.target)]?.label||''))+'<br>'+html(money(e.amount))]),hovertemplate:'%{customdata[1]}<extra></extra>'}};
 const layout={width,height,transition:{duration:0},margin:{l:12,r:12,t:0,b:0},paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',font:{color:'#22332f',family:'Arial, sans-serif'},autosize:false};const config={displayModeBar:false,responsive:false,scrollZoom:false};
 if(initialized)await Plotly.react(target,[trace],layout,config);else await Plotly.newPlot(target,[trace],layout,config);await settlePlot(target,token);
 const connect=()=>target.on('plotly_click',event=>{
  if(token!==epoch||target!==chart||!model||document.body.dataset.ready!=='true')return;const point=event.points?.[0];if(!point)return;if(point.source&&point.target){lastTap=null;const edge=p.edges.find(e=>e.id===point.customdata?.[0]);if(edge){focus=edge.source;selectEdges([edge],(p.nodes.find(n=>n.id===edge.source)?.label||'')+' → '+(p.nodes.find(n=>n.id===edge.target)?.label||''));}}else{const node=p.nodes.find(n=>n.id===point.customdata?.[0]);if(!node)return;const now=performance.now(),double=point.originalEvent?.detail>=2||(lastTap?.id===node.id&&now-lastTap.at<380);lastTap={id:node.id,at:now};clearTimeout(pendingTapTimer);if(node.kind==='account-group'&&double)toggleGroup(node.id);else if(node.expandable&&double)toggle(node.id);else if(node.expandable&&matchMedia('(max-width:760px)').matches){pendingTapTimer=setTimeout(()=>{pendingTapTimer=null;if(token===epoch&&target===chart)selectNode(node.id);},380);}else selectNode(node.id);}
 });
 if(token!==epoch||target!==chart){try{Plotly.purge(target);}catch{}target.replaceChildren();return;}if(version!==drawVersion)return;
 // Reserve typography-sized slots without distorting proportional bars. The
 // fixed-height flow domain sets the shared monetary scale; positions may extend
 // below it in this pinned Plotly build. Measure its real bars once, then place
 // them in a larger, bounded-scroll canvas (no invented balancing flows).
 const heights={};for(const node of target.querySelectorAll('.sankey-node')){const id=node.__data__?.node?.customdata?.[0];if(id)heights[id]=node.querySelector('.node-rect').getBoundingClientRect().height;}
 const measured=groupedLayout(mapProjection,{heights,width:baseWidth,anchors:layoutAnchors});groupGeometry=measured;layout.height=measured.height;trace.domain.y=[1-flowHeight/measured.height,1];trace.node.y=drawNodes.map(n=>(measured.slots[n.id].top+measured.slots[n.id].bottom)/2/flowHeight);target.style.height=measured.height+'px';renderBands(measured);await Plotly.react(target,[trace],layout,config);await settlePlot(target,token);
 if(token!==epoch||target!==chart){try{Plotly.purge(target);}catch{}target.replaceChildren();return;}if(version!==drawVersion)return;initialized=true;target.removeAllListeners('plotly_click');connect();highlight();document.body.dataset.ready='true';
 }).catch(e=>{if(token===epoch){$('#data-state').textContent='Unable to draw this flow. Account controls remain available.';console.error(e);}});return drawQueue;}
$('#refresh').onclick=load;$('#reset').onclick=()=>{stopDetails(true);focus='';sidebar();void highlight();};$('#focus').onchange=e=>{if(e.target.value)selectNode(e.target.value);else{focus='';stopDetails();sidebar();void highlight();}};$('#categories-tab').onclick=()=>showList('categories');$('#accounts-tab').onclick=()=>showList('accounts');$('#spending-tab').onclick=()=>{focus='';sidebar();showList('spending');};
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&selection){stopDetails(true);sidebar();void highlight();}});
document.addEventListener('visibilitychange',()=>{if(document.hidden)suspend();else void resume();});window.addEventListener('pagehide',()=>clearPrivate('Private view paused.'));window.addEventListener('pageshow',e=>{if(e.persisted&&!document.hidden)void load();});
await load();
