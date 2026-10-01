// Gestures reveal an action, never execute it. One controller per mounted list.
export function createSessionSwipe(doc, win) {
  const WIDTH=80;
  let opened=null;
  const disposers=new Set();
  const close=(focus=false)=>{if(!opened)return;const old=opened;opened=null;old.set(false);if(focus)old.actions.focus();};
  const outside=e=>{if(opened && !doc.querySelector('[role=dialog]') && !opened.entry.contains(e.target))close();};
  const escape=e=>{if(e.key==='Escape' && !e.defaultPrevented && opened && !doc.querySelector('[role=dialog]')){e.preventDefault();close(true);}};
  doc.addEventListener('pointerdown',outside);doc.addEventListener('keydown',escape);
  return {
    attach(entry,row,remove,actions){
      let gesture=null,suppressUntil=0,offset=0,timer=null;
      const abort=new win.AbortController();
      const on=(node,type,fn,capture=false)=>node.addEventListener(type,fn,{capture,signal:abort.signal});
      const reduced=()=>win.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? true;
      const paint=x=>{offset=Math.max(-WIDTH,Math.min(0,x));row.style.transform=`translate3d(${offset}px,0,0)`;};
      const semantics=value=>{actions.setAttribute('aria-expanded',String(value));remove.setAttribute('aria-hidden',String(!value));remove.inert=!value;};
      const item={entry,actions,set(value){
        win.clearTimeout(timer);entry.classList.remove('swipe-dragging');
        entry.classList.toggle('actions-open',value);semantics(value);
        remove.hidden=false;
        row.style.transitionDuration=reduced()?'0ms':'240ms';
        paint(value?-WIDTH:0);
        if(!value){if(reduced())remove.hidden=true;else timer=win.setTimeout(()=>{remove.hidden=true;},250);}
      }};
      const reveal=(focus=false)=>{if(opened!==item)close();opened=item;item.set(true);if(focus)(remove.disabled?actions:remove).focus();};
      paint(0);semantics(false);remove.hidden=true;
      on(remove,'click',e=>{if(remove.inert || remove.hidden){e.preventDefault();e.stopImmediatePropagation();}},true);
      on(actions,'click',()=>opened===item?close():reveal(true));
      on(row,'contextmenu',e=>{e.preventDefault();reveal(true);});
      on(row,'keydown',e=>{if(e.key==='Enter' || e.key===' ')suppressUntil=0;if(e.key==='ContextMenu' || e.shiftKey && e.key==='F10' || e.key==='Delete'){e.preventDefault();reveal(true);}});
      on(row,'click',e=>{if(win.performance.now()<suppressUntil){e.preventDefault();e.stopImmediatePropagation();}},true);
      on(row,'pointerdown',e=>{
        if(e.isPrimary===false || e.button!==0)return;
        // Freeze the rendered compositor position, not the previous settle target.
        const bounds=row.getBoundingClientRect();
        // The underlay stays exposed throughout a settle. A hidden underlay
        // means this closed row is resting: leave ordinary taps presentation-free.
        if(bounds.width && !remove.hidden){
          const rendered=bounds.x-entry.getBoundingClientRect().x;
          win.clearTimeout(timer);row.style.transitionDuration='0ms';paint(rendered);
        }
        suppressUntil=0;gesture={id:e.pointerId,x:e.clientX,y:e.clientY,axis:null,dx:0,start:offset,time:e.timeStamp,velocity:0};
      });
      on(row,'pointermove',e=>{
        if(!gesture || e.pointerId!==gesture.id)return;
        const dx=e.clientX-gesture.x,dy=e.clientY-gesture.y;
        const elapsed=e.timeStamp-gesture.time;
        gesture.velocity=elapsed>0 && elapsed<=100 ? Math.max(-2,Math.min(2,(dx-gesture.dx)/elapsed)) : 0;
        gesture.time=e.timeStamp;gesture.dx=dx;
        // A closed resting row remains a tap until gesture intent is resolved.
        // Regrabbing a translated row still suppresses its interruption click.
        if(Math.max(Math.abs(dx),Math.abs(dy))>=(gesture.start===0?12:6)){gesture.moved=true;suppressUntil=win.performance.now()+700;}
        if(!gesture.axis && Math.max(Math.abs(dx),Math.abs(dy))>=12){
          // Diagonal/vertical intent stays a browser pan for this entire gesture.
          gesture.axis=Math.abs(dx)>Math.abs(dy)*1.5?'x':'y';
          suppressUntil=win.performance.now()+700;
          if(gesture.axis==='x'){
            if(opened!==item)close();win.clearTimeout(timer);
            entry.classList.add('swipe-dragging');row.style.transitionDuration='0ms';
            remove.hidden=false;semantics(false);
            try{row.setPointerCapture(e.pointerId);}catch{}
          }
        }
        if(gesture.axis==='x'){e.preventDefault();suppressUntil=win.performance.now()+700;paint(gesture.start+dx);}
      });
      const finish=(e,cancel=false)=>{
        if(!gesture || e.pointerId!==gesture.id)return;
        const saved=gesture;gesture=null;
        if(saved.moved || cancel)suppressUntil=win.performance.now()+700;
        if(saved.axis==='x'){
          const velocity=e.timeStamp-saved.time<=90 && Math.abs(saved.dx)>=18?saved.velocity:0;
          const shouldOpen=Math.abs(velocity)>=.5?velocity<0:offset<=-44;
          if(cancel ? opened===item : shouldOpen)reveal();else if(opened===item)close();else item.set(false);
        }else if(!remove.hidden){
          // Resume an exposed/regrabbed settle, not an untouched closed-row tap.
          item.set(opened===item);
        }
        try{if(row.hasPointerCapture?.(e.pointerId))row.releasePointerCapture(e.pointerId);}catch{}
      };
      on(row,'pointerup',e=>finish(e));
      on(row,'pointercancel',e=>finish(e,true));
      // Touch implicitly captures the inner text span. Transfer is not cancellation.
      on(row,'lostpointercapture',e=>{if(e.target===row)finish(e,true);});
      const interrupt=()=>{if(gesture)finish({pointerId:gesture.id},true);};
      on(doc,'pointerdown',e=>{if(gesture && e.pointerId!==gesture.id)interrupt();},true);
      on(win,'resize',()=>{interrupt();if(opened===item)close();});
      on(win,'blur',()=>{interrupt();if(opened===item)close();});
      on(doc,'keydown',e=>{if(e.key==='Escape' && gesture && !doc.querySelector('[role=dialog]')){interrupt();if(opened===item)close(true);e.preventDefault();}});
      const dispose=()=>{
        abort.abort();
        if(gesture)finish({pointerId:gesture.id},true);
        if(opened===item)opened=null;
        win.clearTimeout(timer);entry.classList.remove('swipe-dragging','actions-open');
        row.style.transitionDuration='0ms';paint(0);semantics(false);remove.hidden=true;
        disposers.delete(dispose);
      };
      disposers.add(dispose);return dispose;
    },
    destroy(){for(const dispose of disposers)dispose();doc.removeEventListener('pointerdown',outside);doc.removeEventListener('keydown',escape);}
  };
}
