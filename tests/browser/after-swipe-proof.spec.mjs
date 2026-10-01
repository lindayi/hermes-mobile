import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const source=await readFile(new URL('../../frontend/session-swipe.mjs',import.meta.url),'utf8');

// Browser-native differential: no application/API, then the exact source controller.
// The original rapid CDP swipe may create a fling. Trace it rather than depending
// on machine speed to assert that a fling always occurs. Corrected swipes MUST click.
test('native after-swipe differential',{timeout:60000},async t=>{
 const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  for(const controller of [false,true])for(const preventFling of [false,true]){
   await t.test(`${controller?'controller':'plain button'} ${preventFling?'non-flinging swipe':'original rapid swipe'}`,async()=>{
    const context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true});
    try{
     const page=await context.newPage(),cdp=await context.newCDPSession(page);
     await page.setContent('<style>body{margin:0}.entry{position:relative;overflow:hidden;width:360px;margin:100px 15px}.row{position:relative;width:100%;height:80px;touch-action:pan-y;transition:transform 240ms;user-select:none}</style><div class="entry"><button class="row"><span class="info">Session title</span></button><button class="remove">Delete</button><button class="actions">Actions</button></div>');
     await page.evaluate(async({source,controller})=>{
      const row=document.querySelector('.row'),entry=document.querySelector('.entry');
      window.trace=[];
      if(controller){
       const {createSessionSwipe}=await import(URL.createObjectURL(new Blob([source],{type:'text/javascript'})));
       createSessionSwipe(document,window).attach(entry,row,document.querySelector('.remove'),document.querySelector('.actions'));
      }
      for(const type of ['pointerdown','pointermove','pointerup','pointercancel','gotpointercapture','lostpointercapture','touchstart','touchmove','touchend','click','focusin','scroll'])document.addEventListener(type,e=>{
       const p=e.changedTouches?.[0]||e,b=row.getBoundingClientRect();
       const record={type,time:e.timeStamp,x:p.clientX,y:p.clientY,target:e.target.className,hit:Number.isFinite(p.clientX)?document.elementFromPoint(p.clientX,p.clientY)?.className:null,trusted:e.isTrusted,pointerType:e.pointerType,sameRow:row===document.querySelector('.row'),disabled:row.disabled,inert:!!row.closest('[inert]'),capture:row.hasPointerCapture(e.pointerId||0),rowX:b.x,scrollLeft:entry.scrollLeft};
       trace.push(record);setTimeout(()=>record.prevented=e.defaultPrevented,0);
      },true);
     },{source,controller});
     const native=[];
     cdp.on('Tracing.dataCollected',({value})=>native.push(...value));
     await cdp.send('Tracing.start',{categories:'input,benchmark,disabled-by-default-input',transferMode:'ReportEvents'});
     const touch=(type,x=189)=>cdp.send('Input.dispatchTouchEvent',{type,touchPoints:type==='touchEnd'?[]:[{x,y:140,id:1}]});
     if(preventFling)await cdp.send('Input.synthesizeScrollGesture',{x:249,y:140,xDistance:-60,yDistance:0,speed:400,preventFling:true,gestureSourceType:'touch'});
     else {await touch('touchStart',249);await touch('touchMove');await touch('touchEnd');}
     await page.waitForTimeout(270);
     assert.equal(await page.evaluate(()=>trace.filter(e=>e.type==='click').length),0,'swipe never activates');
     if(controller)assert.equal(await page.locator('.actions').getAttribute('aria-expanded'),'true');
     await touch('touchStart');await touch('touchEnd');await page.waitForTimeout(150);
     const complete=new Promise(resolve=>cdp.once('Tracing.tracingComplete',resolve));
     await cdp.send('Tracing.end');await complete;
     const trace=await page.evaluate(()=>trace);
     const gestures=native.filter(e=>e.name==='RenderInputRouter::ForwardGestureEvent').map(e=>e.args.type);
     const fling=gestures.includes('GestureFlingStart'),suppressed=native.some(e=>e.name==='FilterTapSuppression');
     const clicks=trace.filter(e=>e.type==='click');
     const ups=trace.filter(e=>e.type==='pointerup'),down=trace.filter(e=>e.type==='pointerdown').at(-1),up=ups.at(-1);
     assert.equal(ups.length,2);assert.ok(up.time-ups[0].time<700,'tap is within controller suppression interval');
     assert.equal(down.hit,up.hit);assert.equal(down.rowX,up.rowX);assert.equal(down.scrollLeft,up.scrollLeft);
     assert.ok(trace.every(e=>e.sameRow&&!e.disabled&&!e.inert),'no disabled/inert/replaced target');
     if(preventFling){assert.equal(fling,false);assert.equal(suppressed,false);assert.equal(clicks.length,1);}
     else if(fling){assert.equal(suppressed,true);assert.equal(clicks.length,0);}
     else assert.equal(clicks.length,1,'without native fling the original sequence also activates');
     for(const click of clicks){assert.equal(click.trusted,true);assert.equal(click.pointerType,'touch');}
     console.log(JSON.stringify({controller,preventFling,fling,suppressed,clicks:clicks.length,tapAfterSwipeMs:up.time-ups[0].time,gestures:[...new Set(gestures)],secondTap:[down,up]}));
    }finally{await context.close();}
   });
  }
 }finally{await browser.close();}
});
