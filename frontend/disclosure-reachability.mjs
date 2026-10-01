// Sticky fold bars handle ordinary scrolling. Only protect the reader's active,
// previously visible fold bar when the transcript scrollport itself changes size.
export function observeDisclosureReachability(messages, current=()=>true) {
 const doc=messages.ownerDocument,win=doc.defaultView;
 if(!win.ResizeObserver)return ()=>{};
 const selector='details:is(.tool-activity,.activity-summary,.delegation-result,.background-result,.process-history)';
 let active=null,protectedFold=false,bounds=null,disposed=false,suspended=false;
 const usable=()=>!disposed && !suspended && current() && messages.isConnected;
 const unzoomed=()=>Math.abs((win.visualViewport?.scale ?? 1)-1)<=.01;
 const sameSize=box=>bounds && box.width===bounds.width && box.height===bounds.height;
 const summary=()=>active?.open && messages.contains(active)?active.querySelector(':scope > summary'):null;
 function remember() {
  if(!usable())return;
  const box=messages.getBoundingClientRect();
  // Resize/reflow scroll events must not erase the pre-resize visibility snapshot.
  if(!sameSize(box))return;
  const fold=summary(),rect=fold?.getBoundingClientRect();
  protectedFold=!!rect && unzoomed() && rect.top>=box.top-1 && rect.bottom<=box.bottom+1 &&
   fold.contains(doc.elementFromPoint(rect.x+rect.width/2,rect.y+rect.height/2));
 }
 function interact(event) {
  // Programmatic disclosure updates are not reader intent. A native toggle only
  // refreshes visibility for the card already selected by pointer/focus/wheel.
  if(event.type==='toggle'){if(event.target===active)remember();return;}
  const card=event.target.closest?.(selector);
  if(card && messages.contains(card))active=card;
  else if(event.type==='pointerdown' || event.type==='wheel')active=null;
  remember();
 }
 function resize() {
  if(!usable())return;
  const box=messages.getBoundingClientRect(),changed=bounds && !sameSize(box);
  const fold=summary();
  if(changed && protectedFold && fold && unzoomed()) {
   const rect=fold.getBoundingClientRect();
   // Never move focus, the document, or a disclosure's internal scrollport.
   // One minimal transcript adjustment, not a scrollIntoView of every open card.
   if(rect.height<=box.height) {
    if(rect.bottom>box.bottom)messages.scrollTop+=rect.bottom-box.bottom;
    else if(rect.top<box.top)messages.scrollTop+=rect.top-box.top;
   }
  }
  bounds=box;remember();
 }
 function zoom(){if(!unzoomed()){active=null;protectedFold=false;}}
 const observer=new win.ResizeObserver(resize);
 const events=['pointerdown','focusin','wheel','toggle'];
 function resume(){
  if(disposed || !suspended)return;
  suspended=false;bounds=messages.getBoundingClientRect();
  observer.observe(messages);
  for(const event of events)messages.addEventListener(event,interact,true);
  messages.addEventListener('scroll',remember,true);
  win.visualViewport?.addEventListener('resize',zoom);
 }
 function suspend(){
  suspended=true;active=null;protectedFold=false;observer.disconnect();
  for(const event of events)messages.removeEventListener(event,interact,true);
  messages.removeEventListener('scroll',remember,true);
  win.visualViewport?.removeEventListener('resize',zoom);
 }
 suspended=true;resume();
 win.addEventListener('pagehide',suspend);win.addEventListener('pageshow',resume);
 return ()=>{
  if(disposed)return;disposed=true;suspend();
  win.removeEventListener('pagehide',suspend);win.removeEventListener('pageshow',resume);
 };
}
