// Viewport geometry and bounded in-app scrolling only: never focus, scroll the
// document, submit, or replace editor nodes.
export function observeViewport(root) {
 const win=root.ownerDocument.defaultView,vv=win.visualViewport;
 const properties=['--app-viewport-height','--app-viewport-top'];
 const previous=properties.map(name=>[name,root.style.getPropertyValue(name),root.style.getPropertyPriority(name)]);
 const attributes=['data-viewport','data-keyboard'].map(name=>[name,root.getAttribute(name)]);
 let frame=0,baseline=win.innerHeight,width=win.innerWidth,active=false,disposed=false,reading;
 function rememberReading(event){
  const node=event.target;
  if(!node.matches?.('.messages'))return;
  // Reflow can dispatch scroll after a resize has already changed line wrapping.
  // Keep the pre-reflow intent until update applies the new viewport rectangle.
  // Height-only changes must still accept a newer intentional reader scroll.
  if(reading?.node===node && reading.width!==node.clientWidth)return;
  reading={node,width:node.clientWidth,height:node.clientHeight,follow:node.scrollHeight-node.clientHeight-node.scrollTop<100};
 }
 function update() {
  frame=0;
  // A magnified viewport is not a keyboard. Preserve its unzoomed layout and native panning.
  if(Math.abs((vv?.scale ?? 1)-1)>.01)return;
  const height=Math.round(vv?.height ?? win.innerHeight),top=Math.max(0,Math.round(vv?.offsetTop ?? 0));
  if(!Number.isFinite(height) || height<=0 || !Number.isFinite(top))return;
  if(Math.abs(win.innerWidth-width)>80){baseline=win.innerHeight;width=win.innerWidth;}
  baseline=Math.max(baseline,height,win.innerHeight);
  const focused=root.ownerDocument.activeElement;
  const editable=root.contains(focused) && !focused.readOnly && !focused.disabled &&
   (focused.tagName==='TEXTAREA' || focused.isContentEditable ||
    (focused.tagName==='INPUT' && /^(text|search|email|url|tel|password|number)$/.test(focused.type)));
  // Read before changing geometry; the shell retains its last explicit height
  // even when window.resize has already updated dynamic viewport CSS units.
  const messages=root.querySelector('.messages'),scrollTop=messages?.scrollTop;
  const follow=messages && (reading?.node===messages && (reading.width!==messages.clientWidth || reading.height!==messages.clientHeight)
   ? reading.follow : messages.scrollHeight-messages.clientHeight-scrollTop<100);
  const shell=root.querySelector('.app-shell'),composer=editable?focused.closest('.composer'):null;
  const composerBox=composer?.getBoundingClientRect(),shellBox=shell?.getBoundingClientRect();
  const revealComposer=composer && shell && (shell.scrollHeight-shell.clientHeight<2 ||
   (composerBox.top>=shellBox.top-1 && composerBox.bottom<=shellBox.bottom+1));
  const heightChanged=root.style.getPropertyValue(properties[0])!==`${height}px`;
  const topChanged=root.style.getPropertyValue(properties[1])!==`${top}px`;
  root.dataset.viewport='managed';
  // Keep controls stationary between pointerdown focusout and click. Only real
  // unzoomed viewport recovery dismisses a keyboard already observed as open.
  root.dataset.keyboard=(editable || root.dataset.keyboard==='open') && baseline-height>Math.max(150,baseline*.2)?'open':'closed';
  for(const [name,value] of [[properties[0],`${height}px`],[properties[1],`${top}px`]])
   if(root.style.getPropertyValue(name)!==value)root.style.setProperty(name,value);
  if(messages){
   // Focus can schedule an update between pointerdown and pointerup. Reconcile
   // reading position only for real geometry changes, not a focus-only frame.
   if(heightChanged || topChanged || reading?.node!==messages || reading.width!==messages.clientWidth || reading.height!==messages.clientHeight)
    messages.scrollTop=follow?messages.scrollHeight:scrollTop;
   reading={node:messages,width:messages.clientWidth,height:messages.clientHeight,follow};
  }
  // Only the bounded app scroller may move; never scrollIntoView/the document.
  // Do not pull a user who scrolled up to safety notices back to the composer.
  if(revealComposer && heightChanged){
   const box=composer.getBoundingClientRect();
   const target=box.height<=height?box:focused.getBoundingClientRect();
   const bounds=shell.getBoundingClientRect();
   if(target.bottom>bounds.bottom)shell.scrollTop+=target.bottom-bounds.bottom;
   else if(target.top<bounds.top)shell.scrollTop+=target.top-bounds.top;
  }
 }
 function schedule(){if(active && !frame)frame=win.requestAnimationFrame(update);}
 const events=[[vv,'resize'],[vv,'scroll'],[win,'resize'],[root,'focusin'],[root,'focusout']];
 function resume(){
  if(disposed)return;
  if(!active){active=true;for(const [target,name] of events)target?.addEventListener(name,schedule);root.addEventListener('scroll',rememberReading,true);}
  schedule();
 }
 function suspend(){
  active=false;win.cancelAnimationFrame(frame);frame=0;
  root.removeEventListener('scroll',rememberReading,true);
  for(const [target,name] of events)target?.removeEventListener(name,schedule);
 }
 win.addEventListener('pagehide',suspend);win.addEventListener('pageshow',resume);resume();
 return ()=>{
  if(disposed)return;disposed=true;suspend();
  win.removeEventListener('pagehide',suspend);win.removeEventListener('pageshow',resume);
  for(const [name,value,priority] of previous)if(value)root.style.setProperty(name,value,priority);else root.style.removeProperty(name);
  for(const [name,value] of attributes)if(value===null)root.removeAttribute(name);else root.setAttribute(name,value);
 };
}
