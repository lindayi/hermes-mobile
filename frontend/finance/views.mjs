import {money,finite,evidenceURL} from './model.mjs';
export function el(tag,attrs={},...children){const n=document.createElement(tag);for(const [k,v] of Object.entries(attrs)){if(k==='class')n.className=v;else if(k.startsWith('on'))n.addEventListener(k.slice(2),v);else if(v!=null)n.setAttribute(k,String(v));}for(const child of children.flat(Infinity)){if(child!=null)n.append(child instanceof Node?child:document.createTextNode(String(child)));}return n;}
export function svg(tag,attrs={},...children){const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));for(const c of children.flat())if(c!=null)n.append(c instanceof Node?c:document.createTextNode(String(c)));return n;}
export const empty=(text='No recorded observations match these filters.')=>el('p',{class:'empty'},text);
export function panel(title,subtitle,...children){return el('section',{class:'panel'},el('div',{class:'panel-head'},el('div',{},el('h3',{},title),subtitle?el('p',{class:'muted'},subtitle):null)),...children);}
export function table(title,columns,rows,{fold=false}={}){const wrap=el('div',{class:'table-wrap',tabindex:'0',role:'region','aria-label':title});const t=el('table',{},el('caption',{},title),el('thead',{},el('tr',{},columns.map(c=>el('th',{scope:'col'},c[0])))),el('tbody',{},rows.map(r=>el('tr',{},columns.map(c=>el('td',{},typeof c[1]==='function'?c[1](r):display(r[c[1]])))))));wrap.append(rows.length?t:empty());return fold?el('details',{class:'data-alternative'},el('summary',{},title+' · table alternative'),wrap):wrap;}
export function display(v){if(v==null||v==='')return 'Unavailable';if(typeof v==='object')return Object.entries(v).map(([k,x])=>`${k.replaceAll('_',' ')}: ${display(x)}`).join(' · ');return String(v);}
export function evidence(r){const d=el('details',{class:'evidence'},el('summary',{},'Source & rationale'));for(const [label,value] of [['Source',r.source_id],['Page',r.page],['Evidence',r.evidence],['Confidence',r.confidence],['Allocation',r.allocation],['Protected',r.current_planning_protected??r.planning_protected]])if(value!=null)d.append(el('p',{},el('strong',{},label+': '),display(value)));const url=evidenceURL(r.drive_url);if(url)d.append(el('a',{href:url,target:'_blank',rel:'noopener noreferrer',referrerpolicy:'no-referrer'},'Open source ↗'));return d;}
export function chartBox(kind,label,width,height){return svg('svg',{'data-chart':kind,viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':label},svg('title',{},label));}
export const palette=['#289b88','#d59747','#5e8fc6','#9d86c1','#b56f7a','#7f9297'];
export function alluvial(rows,currency,inspect){
 if(!rows.length)return empty('No classified cash flows are available for this range.');
 const visible=[...rows].sort((a,b)=>Math.abs(b.amount_minor)-Math.abs(a.amount_minor)).slice(0,12), height=Math.max(390,visible.length*58+120);
 const s=chartBox('alluvial','Classified cash flows, not a balanced sources-and-uses model. Width is absolute value; signed recoveries are separate.',960,height);
 const total=visible.reduce((n,r)=>n+Math.abs(r.amount_minor),0)||1, available=height-100-visible.length*32;
 const groups=new Map();
 visible.forEach(r=>{const key=r.direction==='recovery'?'Recovery / reversal':r.flow||r.category_id?.split(/[.:/]/)[0]||'Unresolved';if(!groups.has(key))groups.set(key,[]);groups.get(key).push(r);});
 let left=60,right=60,index=0;
 for(const [name,items]of groups){
  const heights=items.map(r=>Math.max(2,Math.abs(r.amount_minor)/total*available));
  const groupHeight=heights.reduce((a,b)=>a+b,0);
  s.append(svg('rect',{x:188,y:left,width:12,height:groupHeight,rx:3,fill:palette[index%6]}),svg('text',{x:177,y:left+14,'text-anchor':'end',class:'chart-label'},name));
  items.forEach((r,j)=>{const h=heights[j],sy=left,y=right,color=palette[index++%6];
   const path=svg('path',{d:`M200 ${sy} C410 ${sy} 475 ${y} 680 ${y} L680 ${y+h} C475 ${y+h} 410 ${sy+h} 200 ${sy+h} Z`,fill:color,opacity:'.32',class:'flow-link',tabindex:0,role:'button','aria-label':`Inspect ${r.label}`},svg('title',{},`${r.label}: ${money(r.amount_minor,currency)} · ${r.direction}`));
   const run=()=>inspect(r);path.addEventListener('click',run);path.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();run();}});
   s.append(path,svg('rect',{x:680,y,width:5,height:h,fill:color}),svg('text',{x:700,y:y+12,class:'chart-label'},String(r.label).slice(0,28)),svg('text',{x:700,y:y+29,class:'chart-value'},money(r.amount_minor,currency)));
   left+=h;right+=Math.max(34,h)+12;
  });left+=22;
 }
 s.prepend(svg('text',{x:35,y:22,class:'chart-axis'},'FLOW CLASS'),svg('text',{x:700,y:22,class:'chart-axis'},'CATEGORY / SIGNED AMOUNT'));
 return el('div',{class:'chart-scroll'},s);
}
export function monthly(rows,currency){
 if(!rows.length)return empty('Monthly trend is unavailable.');
 const w=Math.max(960,rows.length*66+100),h=280,s=chartBox('monthly','Monthly signed amounts and net line. Negative recoveries and drawdowns appear below zero.',w,h);
 const values=rows.flatMap(r=>[r.income_minor,r.expense_minor,r.net_minor]).filter(finite),max=Math.max(1,...values),min=Math.min(0,...values),scale=185/(max-min),base=35+max*scale,step=(w-110)/rows.length,points=[];
 for(let i=0;i<4;i++){const value=max-(max-min)*i/3,y=35+i*185/3;s.append(svg('line',{x1:65,x2:w-20,y1:y,y2:y,class:'gridline'}),svg('text',{x:60,y:y+4,'text-anchor':'end',class:'chart-value'},new Intl.NumberFormat('en-CA',{notation:'compact',maximumFractionDigits:1}).format(value/100)));}
 s.append(svg('line',{x1:65,x2:w-20,y1:base,y2:base,stroke:palette[5],'stroke-width':1.5}));
 rows.forEach((r,i)=>{const x=80+i*step,bw=Math.min(24,step*.24);
  for(const [key,offset,color] of [['income_minor',0,palette[0]],['expense_minor',bw+3,palette[1]]])if(finite(r[key]))s.append(svg('rect',{x:x+offset,y:r[key]>=0?base-r[key]*scale:base,width:bw,height:Math.max(.5,Math.abs(r[key])*scale),rx:2,fill:color},svg('title',{},`${r.month} ${key}: ${money(r[key],currency)}`)));
  if(finite(r.net_minor))points.push(`${x+bw},${base-r.net_minor*scale}`);
  s.append(svg('text',{x:x+bw,y:253,'text-anchor':'middle',class:'chart-value'},r.month));
 });
 s.append(svg('polyline',{points:points.join(' '),fill:'none',stroke:palette[2],'stroke-width':2}));
 return el('div',{class:'chart-scroll'},s);
}
export function allocation(rows,currency){const items=rows.filter(r=>finite(r.market_value_minor)&&r.market_value_minor>0),sum=items.reduce((a,r)=>a+r.market_value_minor,0);if(!sum)return empty('Allocation cannot be drawn without positive recorded balances.');const s=chartBox('allocation','Recorded portfolio allocation by account; cash is included, not added again',400,310);let offset=0;items.forEach((r,i)=>{const part=r.market_value_minor/sum*100;s.append(svg('circle',{cx:200,cy:145,r:103,fill:'none',stroke:palette[i%palette.length],'stroke-width':34,pathLength:100,'stroke-dasharray':`${part} ${100-part}`,'stroke-dashoffset':-offset,transform:'rotate(-90 200 145)'},svg('title',{},`${r.account}: ${money(r.market_value_minor,currency)}`)));offset+=part;});s.append(svg('text',{x:200,y:139,'text-anchor':'middle',class:'chart-axis'},'RECORDED VALUE'),svg('text',{x:200,y:166,'text-anchor':'middle',class:'donut-value'},money(sum,currency)));return el('div',{class:'allocation-layout'},s,el('ul',{class:'legend'},items.map((r,i)=>el('li',{},el('span',{class:`swatch color-${i%6}`}),el('span',{},r.account),el('strong',{},money(r.market_value_minor,currency))))));}
export function network(links,currency,inspect){if(!links.length)return empty('No paired account movements match the selected currency.');const accounts=[...new Set(links.flatMap(l=>[l.source,l.target]))],height=Math.max(320,accounts.length*60),s=chartBox('network','Directional paired transfers; each cash link counted once',960,height),max=Math.max(1,...links.map(l=>Math.abs(l.amount_minor))),pos=new Map(accounts.map((a,i)=>[a,{x:i%2?710:230,y:50+Math.floor(i/2)*(height-100)/Math.max(1,Math.ceil(accounts.length/2)-1)}]));links.forEach((l,i)=>{const a=pos.get(l.source),b=pos.get(l.target);const p=svg('path',{d:`M${a.x},${a.y} C480,${a.y+35} 480,${b.y+35} ${b.x},${b.y}`,fill:'none',stroke:palette[2],'stroke-width':Math.max(2,Math.abs(l.amount_minor)/max*30),opacity:'.38'},svg('title',{},`${l.source} → ${l.target}: ${money(l.amount_minor,currency)} (${l.count} paired links)`));s.append(p,svg('text',{x:475,y:(a.y+b.y)/2+15+i*12,class:'chart-value'},'→'));});for(const [name,p] of pos){const g=svg('g',{tabindex:'0',role:'button','aria-label':`Inspect account ${name}`},svg('circle',{cx:p.x,cy:p.y,r:12,fill:palette[2]}),svg('text',{x:p.x<480?p.x-22:p.x+22,y:p.y+5,'text-anchor':p.x<480?'end':'start',class:'chart-label'},String(name).slice(0,27)));g.addEventListener('click',()=>inspect(name));g.addEventListener('keydown',e=>{if(e.key==='Enter')inspect(name);});s.append(g);}return el('div',{class:'chart-scroll'},s);}
export function waterfall(pay,components){
 if(!pay||!finite(pay.gross_minor))return empty('A source-backed payroll sample is unavailable.');
 const steps=components.filter(c=>finite(c.cash_effect_minor)&&c.cash_effect_minor!==0&&!/^(earnings?|memo)$/i.test(String(c.kind)));
 const rows=[{label:'Gross',amount:pay.gross_minor,total:true},...steps.map(c=>({label:c.code,amount:c.cash_effect_minor})),{label:'Bank net',amount:pay.net_minor,total:true}];
 let current=pay.gross_minor;const plotted=rows.filter(r=>finite(r.amount)).map(r=>{const start=r.total?0:current,end=r.total?r.amount:current+r.amount;if(!r.total)current=end;return {...r,start,end};});
 const calculated=pay.gross_minor+steps.reduce((n,c)=>n+c.cash_effect_minor,0),max=Math.max(1,...plotted.flatMap(r=>[r.start,r.end])),min=Math.min(0,...plotted.flatMap(r=>[r.start,r.end])),scale=190/(max-min),base=30+max*scale,step=850/rows.length;
 const s=chartBox('waterfall','Payroll gross to bank net. Gross already includes earning components; only other cash adjustments bridge to net.',960,300);
 plotted.forEach((r,i)=>{const x=70+i*step;s.append(svg('rect',{x,y:base-Math.max(r.start,r.end)*scale,width:Math.min(65,step*.65),height:Math.max(.5,Math.abs(r.end-r.start)*scale),rx:3,fill:r.total?palette[0]:r.amount<0?palette[1]:palette[2]},svg('title',{},`${r.label}: ${money(r.amount,pay.currency)}`)),svg('text',{x,y:250,class:'chart-value'},r.label.slice(0,16)));});
 const note=finite(pay.net_minor)?calculated===pay.net_minor?'Bridge reconciles to recorded bank net.':`Bridge differs from recorded bank net by ${money(calculated-pay.net_minor,pay.currency)}; component coverage is incomplete or inconsistent.`:'Bank net unavailable; reconciliation cannot be confirmed.';
 return el('div',{},el('div',{class:'chart-scroll'},s),el('p',{class:'warning'},note));
}
export function timeline(rows,dateKey,labelKey,kind='vesting'){if(!rows.length)return empty('No exact scheduled dates are available.');const sorted=[...rows].sort((a,b)=>String(a[dateKey]).localeCompare(String(b[dateKey]))),height=Math.max(180,sorted.length*60+35),s=chartBox(kind,kind==='vesting'?'Award vesting timeline; scheduled does not mean actually vested':'Recorded obligation due dates and payment evidence',960,height);s.append(svg('line',{x1:175,x2:175,y1:20,y2:height-20,class:'gridline'}));sorted.forEach((r,i)=>{const y=35+i*60;s.append(svg('circle',{cx:175,cy:y,r:6,fill:palette[kind==='vesting'?3:1]}),svg('text',{x:150,y:y+5,'text-anchor':'end',class:'chart-label'},r[dateKey]||'Unknown date'),svg('text',{x:200,y:y+5,class:'chart-label'},display(r[labelKey])),svg('text',{x:200,y:y+24,class:'chart-value'},kind==='vesting'?`${display(r.percent_basis_points==null?null:r.percent_basis_points/100)}% · ${display(r.theoretical_shares)} theoretical shares · ${display(r.status)}`:`${money(r.amount_minor,r.currency)} · ${display(r.payment_status)} · Bank date: ${display(r.bank_date)}`));});return el('div',{class:'chart-scroll'},s);}
