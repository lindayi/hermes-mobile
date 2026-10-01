import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createSessionSwipe} from '../../frontend/session-swipe.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
function fixture({reduce=true}={}){
 const win=new JSDOM('<main></main>',{pretendToBeVisual:true}).window,doc=win.document,controller=createSessionSwipe(doc,win),items=[];
 win.matchMedia=()=>({matches:reduce});
 for(let i=0;i<2;i++){
  const entry=doc.createElement('div');entry.className='session-entry';entry.innerHTML='<button class="session-row"><span>Fixture</span></button><button class="session-actions">Actions</button><button class="session-delete">Delete</button>';doc.querySelector('main').append(entry);
  const [row,actions,remove]=entry.children;let clicks=0,deletes=0;controller.attach(entry,row,remove,actions);row.addEventListener('click',()=>clicks++);remove.addEventListener('click',()=>deletes++);
  items.push({entry,row,actions,remove,get clicks(){return clicks;},get deletes(){return deletes;}});
 }
 const pointer=(item,type,x,y=100,t=0,extra={})=>{const e=new win.Event(type,{bubbles:true,cancelable:true});Object.assign(e,{clientX:x,clientY:y,pointerId:1,button:0,isPrimary:true,...extra});Object.defineProperty(e,'timeStamp',{value:t});item.row.dispatchEvent(e);return e;};
 return {win,doc,controller,items,pointer,close(){controller.destroy();win.close();}};
}
const offset=item=>Number(item.row.style.transform.match(/translate3d\(([-\d.]+)/)?.[1] || 0);
test('Delete cannot activate while merely revealed underneath a finger drag',()=>{
 const h=fixture(),a=h.items[0];try{
  h.pointer(a,'pointerdown',200);h.pointer(a,'pointermove',150);a.remove.click();assert.equal(a.deletes,0,'drag underlay is not an executable action');
  h.pointer(a,'pointerup',150);a.remove.click();assert.equal(a.deletes,1,'explicit activation remains available once open');
 }finally{h.close();}
});
test('normal-motion initialization does not briefly expose the Delete underlay',()=>{
 const h=fixture({reduce:false});try{assert.equal(h.items[0].remove.hidden,true,'Delete must start hidden synchronously');}finally{h.close();}
});
for(const interruption of ['second-pointer','resize','blur','Escape'])test(`${interruption} interrupts a partial closed-row drag without a late reveal`,()=>{
 const h=fixture(),a=h.items[0];try{
  h.pointer(a,'pointerdown',200);h.pointer(a,'pointermove',150);assert.equal(offset(a),-50);
  if(interruption==='second-pointer')h.pointer(h.items[1],'pointerdown',200,100,0,{pointerId:2,isPrimary:false});
  else if(interruption==='Escape')h.doc.dispatchEvent(new h.win.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
  else h.win.dispatchEvent(new h.win.Event(interruption));
  assert.equal(offset(a),0);h.pointer(a,'pointerup',150);assert.equal(a.remove.hidden,true);a.row.click();assert.equal(a.clicks,0);
 }finally{h.close();}
});
test('cancellation restores the committed open state rather than snapping a partial reverse drag closed',()=>{
 const h=fixture(),a=h.items[0];try{
  a.actions.click();h.pointer(a,'pointerdown',100);h.pointer(a,'pointermove',132);assert.equal(offset(a),-48);
  h.pointer(a,'pointercancel',132);assert.equal(offset(a),-80);assert.equal(a.actions.getAttribute('aria-expanded'),'true');a.row.click();assert.equal(a.clicks,0);
 }finally{h.close();}
});
test('destroy during a partial drag releases capture, resets the row and removes listeners/timers',()=>{
 const h=fixture(),a=h.items[0];try{
  let captured=false,releases=0;a.row.setPointerCapture=()=>captured=true;a.row.hasPointerCapture=()=>captured;a.row.releasePointerCapture=()=>{captured=false;releases++;};
  h.pointer(a,'pointerdown',200);h.pointer(a,'pointermove',165);assert.equal(offset(a),-35);
  h.controller.destroy();assert.equal(offset(a),0,'detached rows must not retain drag transform');assert.equal(a.remove.hidden,true);assert.equal(releases,1);
  h.pointer(a,'pointerup',165);a.actions.click();assert.equal(a.remove.hidden,true,'destroy removes old action listeners');
 }finally{h.close();}
});
test('closed-row tap slop activates, but translated-row regrabs suppress clicks and preserve keyboard activation',()=>{
 const h=fixture(),a=h.items[0];try{
  h.pointer(a,'pointerdown',200);h.pointer(a,'pointermove',192);h.pointer(a,'pointerup',192);a.row.click();
  assert.equal(a.clicks,1,'8px incidental motion on a closed resting row remains a tap');
  a.actions.click();h.pointer(a,'pointerdown',200);h.pointer(a,'pointermove',192);h.pointer(a,'pointerup',192);a.row.click();
  assert.equal(a.clicks,1,'8px regrab of a translated row must not navigate');
  a.row.dispatchEvent(new h.win.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));a.row.click();assert.equal(a.clicks,2);
 }finally{h.close();}
});
test('short deliberate fast flick settles open, but a held or tiny movement is not a flick',()=>{
 const h=fixture(),a=h.items[0];try{
  h.pointer(a,'pointerdown',200,100,0);h.pointer(a,'pointermove',176,100,30);h.pointer(a,'pointerup',176,100,35);
  assert.equal(a.remove.hidden,false,'24px fast deliberate flick must open');assert.equal(offset(a),-80);
  a.actions.click();h.pointer(a,'pointerdown',200,100,100);h.pointer(a,'pointermove',176,100,130);h.pointer(a,'pointerup',176,100,400);
  assert.equal(a.remove.hidden,true,'holding at a partial offset loses flick velocity');assert.equal(offset(a),0);
  h.pointer(a,'pointerdown',200,100,500);h.pointer(a,'pointermove',192,100,505);h.pointer(a,'pointerup',192,100,510);assert.equal(a.remove.hidden,true);
 }finally{h.close();}
});
