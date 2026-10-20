import {el,svg,empty} from './views.mjs';
import {money} from './model.mjs';

const colors={income:'#258675',expense:'#b27b3c',account:'#477d91',transfer:'#477d91',investment:'#82729b',financing:'#957253',adjustment:'#8b7a6b',unresolved:'#8b7a6b'};
const action=(label,run,text=label)=>el('button',{type:'button','aria-label':label,onclick:run},text);

function nodeLabel(label,x,y,width){
 const text=String(label),limit=Math.floor((width-24)/5.7),words=text.split(/\s+/),lines=[''];
 for(const word of words){const index=lines.length-1;if((lines[index]+' '+word).trim().length>limit&&lines[index])lines.push(word);else lines[index]+=(lines[index]?' ':'')+word;}
 if(lines.length>2)lines.splice(1,lines.length-1,lines.slice(1).join(' '));
 return svg('text',{x,y,class:'map-node-label'},lines.map((line,i)=>svg('tspan',{x,dy:i?12:0},line.length>limit?line.slice(0,Math.max(3,limit-7))+'…'+line.slice(-6):line)));
}

// One shared linear amount scale. Hit strokes are separate and never imply value.
export function moneyMap(data,{inspect=()=>{},retry=()=>{},viewport={x:0,y:0,k:1},onViewport=()=>{}}={}) {
 const nodes=Array.isArray(data.nodes)?data.nodes:[],edges=Array.isArray(data.edges)?data.edges:[];
 const region=el('section',{class:'money-map',role:'region','aria-label':'Money map'});
 region.append(el('div',{class:'map-heading'},el('div',{},el('span',{class:'eyebrow'},'EXPLORE / RECORDED MOVEMENTS'),el('h2',{},'Your money, in motion')),el('p',{},'Follow a stream. See the evidence.')));
 if(data.unavailable){region.append(el('div',{class:'map-unavailable'},el('h3',{},'Money map unavailable'),el('p',{},'The movement query could not be completed. This is not an empty ledger.'),action('Retry map',retry)));return region;}
 if(data.coverage?.complete===false)region.append(el('p',{class:'warning'},'Partial map · Not all recorded streams are shown. Consult source coverage before interpreting amounts.'));
 if(data.grouped)region.append(el('p',{class:'grouping-note'},`${data.originalCount} streams grouped · All amounts retained. Select a bundle for exact streams, or focus an account.`));
 if(!nodes.length){region.append(empty('No recorded movements match this range. Try a wider period or another scope.'));return region;}
 const compact=window.matchMedia('(max-width:600px)').matches,internalCount=nodes.filter(n=>!['income','expense'].includes(n.kind)).length,columns=compact?1:internalCount>8?4:2;
 const height=Math.max(600,Math.ceil(internalCount/columns)*72+170),width=compact?360:1120,scene=svg('g',{'data-map-scene':''}),s=svg('svg',{'data-compact':compact,viewBox:`0 0 ${width} ${height}`,'aria-label':'Income enters your accounts; recorded spending leaves. Internal movements are separate.'});
 const viewportEl=el('div',{'data-map-viewport':'',class:'map-viewport',tabindex:0,role:'group','aria-label':'Map viewport. Drag to pan, wheel or pinch to zoom. Arrow keys pan, plus and minus zoom, Home fits.'},s);
 s.append(scene);
 scene.append(svg('rect',{x:compact?120:326,y:66,width:compact?120:468,height:height-110,rx:24,class:'account-realm'}));
 const groups=[nodes.filter(n=>n.kind==='income'),nodes.filter(n=>!['income','expense'].includes(n.kind)),nodes.filter(n=>n.kind==='expense')];
 const positions=new Map();
 groups.forEach((items,col)=>items.forEach((n,i)=>{const internal=col===1,cols=internal?columns:1,rows=Math.ceil(items.length/cols),w=compact?92:internal&&columns===4?102:156;positions.set(n.id,{x:compact?[52,180,308][col]:internal?(columns===4?386+(i%4)*116:(i%2?676:444)):col===0?130:990,y:130+Math.floor(i/cols)*Math.min(125,(height-200)/Math.max(1,rows-1)),w,n});}));
 for(const [x,text,note] of [[compact?7:34,'Income',compact?'IN':'FROM OUTSIDE'],[compact?128:352,'Your accounts',compact?'WITHIN':'MOVING WITHIN YOUR REALM'],[compact?270:866,'Spending',compact?'OUT':'TO THE WORLD']])scene.append(svg('text',{x,y:30,class:'map-zone','font-size':compact?15:22},text),svg('text',{x,y:51,class:'map-zone-note'},note));
 const max=Math.max(1,...edges.map(e=>e.amount_minor));
 // Reciprocal and parallel routes share a hit area. Keep every rendered band and
 // exact selector, but offer all coincident streams before opening evidence.
 const routeKey=edge=>JSON.stringify([...new Set([edge.source,edge.target])].sort());
 const routes=new Map();
 for(const edge of edges){const key=routeKey(edge);if(!routes.has(key))routes.set(key,[]);routes.get(key).push(edge);}
 const inspectRoute=edge=>{
  const overlaps=routes.get(routeKey(edge));if(overlaps.length===1){inspect(edge);return;}
  region.querySelector('[data-stream-chooser]')?.remove();
  const members=overlaps.flatMap(e=>e.members||[e]),close=()=>{chooser.remove();[...scene.querySelectorAll('[data-edge]')].find(node=>node.dataset.edge===edge.id)?.focus({preventScroll:true});};
  let offset=0;
  const body=el('div',{class:'inspector-body'}),previous=action('Previous streams',()=>{offset-=25;renderChoices();}),next=action('Next streams',()=>{offset+=25;renderChoices();});
  const chooser=el('aside',{class:'flow-inspector','data-stream-chooser':'',role:'complementary','aria-label':'Overlapping streams',tabindex:-1},
   el('header',{},el('div',{},el('h3',{},'Overlapping streams')),action('Close stream chooser',close,'Close')),body,
   el('div',{class:'pager inspector-body'},previous,next));
  const renderChoices=()=>{
   body.replaceChildren(el('p',{},`These streams share a route. Choose the exact movement to inspect. ${offset+1}–${Math.min(offset+25,members.length)} of ${members.length} streams.`),...members.slice(offset,offset+25).map(member=>{const button=action('Inspect '+member.label,()=>{chooser.remove();inspect(member);},`${member.label} · ${money(member.amount_minor,member.currency)}`);button.className='stream-member';return button;}));
   previous.disabled=offset===0;next.disabled=offset+25>=members.length;body.scrollTop=0;
  };
  renderChoices();
  chooser.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close();}});
  region.append(chooser);chooser.focus({preventScroll:true});
 };
 for(const edge of edges){const a=positions.get(edge.source),b=positions.get(edge.target);if(!a||!b)continue;const outgoing=b.x>=a.x,sx=a.x+(outgoing?a.w/2:-a.w/2),tx=b.x+(outgoing?-b.w/2:b.w/2);let d=`M${sx} ${a.y} C${(sx+tx)/2} ${a.y} ${(sx+tx)/2} ${b.y} ${tx} ${b.y}`;
 if(a===b)d=`M${a.x} ${a.y+24} C${a.x-55} ${a.y+145} ${a.x+55} ${a.y+145} ${a.x} ${a.y+24}`;
 else if(a.x===b.x){const direction=Math.sign(b.y-a.y),sy=a.y+direction*24,ty=b.y-direction*24,bend=Math.min(a.w,b.w)/4;d=`M${a.x} ${sy} C${a.x+bend} ${(sy+ty)/2} ${b.x+bend} ${(sy+ty)/2} ${b.x} ${ty}`;}
 const color=colors[edge.kind]||colors.unresolved;
 const g=svg('g',{'data-edge':edge.id,'data-edge-kind':edge.kind,role:'button',tabindex:0,'aria-label':`Inspect ${edge.label}`,class:'money-edge'},svg('title',{},`${edge.label} · ${money(edge.amount_minor,edge.currency)} · ${edge.count} records`),svg('path',{d,fill:'none',stroke:color,'stroke-width':edge.amount_minor/max*34,opacity:.3,class:'amount-band'}),svg('path',{d,fill:'none',stroke:'transparent','stroke-width':44,'vector-effect':'non-scaling-stroke',class:'edge-hit'}));
 g.addEventListener('click',()=>{if(!dragged)inspectRoute(edge);});g.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();e.stopPropagation();inspect(edge);}});
 // Direction is explicit even for refunds and income reversals.
 const mx=a===b?a.x:(sx+tx)/2,my=a===b?a.y+105:(a.y+b.y)/2;g.append(svg('text',{x:mx,y:my+5,fill:color,'text-anchor':'middle',class:'edge-arrow','pointer-events':'none'},outgoing?'›':'‹'));scene.append(g);
 }
 for(const {x,y,w,n} of positions.values()){const color=colors[n.kind]||colors.unresolved;scene.append(svg('g',{'data-node':n.id,'data-node-kind':n.kind},svg('rect',{x:x-w/2,y:y-24,width:w,height:48,rx:n.kind==='account'?10:20,class:'map-node',stroke:color}),svg('circle',{cx:x-w/2+8,cy:y,r:2,fill:color}),nodeLabel(n.label,x-w/2+15,y-8,w),svg('text',{x:x-w/2+15,y:y+15,class:'map-node-kind'},n.summary?'Tap Expand below':n.kind==='account'?'Account':n.kind),svg('title',{},n.label)));}
 let state={...viewport};
 const paint=()=>{scene.setAttribute('transform',`translate(${state.x} ${state.y}) scale(${state.k})`);output.textContent=Math.round(state.k*100)+'%';onViewport({...state});};
 const point=(x,y)=>{const p=s.createSVGPoint();p.x=x;p.y=y;return p.matrixTransform(s.getScreenCTM().inverse());};
 const zoom=(factor,anchor={x:width/2,y:height/2})=>{const k=Math.min(5,Math.max(.5,state.k*factor)),ratio=k/state.k;state={x:anchor.x-(anchor.x-state.x)*ratio,y:anchor.y-(anchor.y-state.y)*ratio,k};paint();};
 const fit=()=>{state={x:0,y:0,k:1};paint();};
 const output=el('output',{'aria-label':'Map zoom'},'100%');
 const tools=el('div',{class:'map-tools'},action('Zoom out',()=>zoom(1/1.25),'−'),output,action('Zoom in',()=>zoom(1.25),'+'),action('Fit diagram',fit,'Fit diagram'));
 const pointers=new Map();let previous=null,dragged=false;
 const gesture=()=>{const values=[...pointers.values()];if(!values.length)return null;return {x:values.reduce((n,p)=>n+p.x,0)/values.length,y:values.reduce((n,p)=>n+p.y,0)/values.length,d:values.length>1?Math.hypot(values[1].x-values[0].x,values[1].y-values[0].y):0};};
 viewportEl.addEventListener('pointerdown',e=>{if(e.button!==0)return;dragged=pointers.size>0;pointers.set(e.pointerId,point(e.clientX,e.clientY));previous=gesture();if(e.pointerType==='touch')e.target.releasePointerCapture?.(e.pointerId);});
 viewportEl.addEventListener('pointermove',e=>{if(!pointers.has(e.pointerId))return;const next=point(e.clientX,e.clientY),old=pointers.get(e.pointerId);if(Math.hypot(next.x-old.x,next.y-old.y)>2){dragged=true;viewportEl.setPointerCapture(e.pointerId);}pointers.set(e.pointerId,next);const current=gesture();if(previous){if(previous.d&&current.d)zoom(current.d/previous.d,previous);state.x+=current.x-previous.x;state.y+=current.y-previous.y;paint();}previous=current;});
 for(const event of ['pointerup','pointercancel','lostpointercapture'])viewportEl.addEventListener(event,e=>{pointers.delete(e.pointerId);previous=gesture();});
 viewportEl.addEventListener('wheel',e=>{e.preventDefault();zoom(Math.exp(-Math.max(-500,Math.min(500,e.deltaY))*.002),point(e.clientX,e.clientY));},{passive:false});
 viewportEl.addEventListener('keydown',e=>{if(e.target!==viewportEl)return;if(['+','=','-','Home','ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();if(e.key==='Home')fit();else if(['+','=','-'].includes(e.key))zoom(e.key==='-'?1/1.25:1.25);else{state.x+=e.key==='ArrowLeft'?40:e.key==='ArrowRight'?-40:0;state.y+=e.key==='ArrowUp'?40:e.key==='ArrowDown'?-40:0;paint();}}});
 region.append(viewportEl,el('div',{class:'map-bottom'},el('p',{},el('span',{class:'map-key income-key'},'Income'),el('span',{class:'map-key transfer-key'},'Internal movement'),el('span',{class:'map-key expense-key'},'Spending'),el('span',{class:'map-key investment-key'},'Investment')),tools),el('p',{class:'map-caption'},'Drag to explore · Pinch or scroll to zoom · Select a stream to inspect',el('span',{},'Band width = recorded amount on one linear scale. Not a balanced Sankey or a tracing of individual dollars.')));
 paint();return region;
}
