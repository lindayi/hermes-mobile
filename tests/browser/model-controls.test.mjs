import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createModelControls} from '../../frontend/model-controls.mjs';
const {JSDOM} = createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
const deferred = () => {let resolve, reject;const promise = new Promise((a,b) => {resolve=a;reject=b;});return {promise,resolve,reject};};
const catalog = () => ({available:true,models:[
  {id:'alpha',provider:'one',label:'Alpha',reasoning_efforts:['brief','deep']},
  {id:'alpha',provider:'two',label:'Alpha elsewhere',reasoning_efforts:[]},
],default:{model:'alpha',provider:'one'}});
const storageKey = (user='user',session='session') => `hermes:${user}:model-controls:${JSON.stringify([user,session])}`;
function setup({response=catalog(),request,sessionId='session',userId='user',isCurrent=()=>true,win:existing}={}) {
  const win=existing || new JSDOM('<button id="outside">Outside</button>',{url:'https://fixture.test/hermes/',pretendToBeVisual:true}).window;
  const doc=win.document,calls=[];
  const controls=createModelControls({doc,win,sessionId,userId,isCurrent,api:{request(path,options){calls.push({path,options});return request ? request(path,options) : Promise.resolve(response);}}});
  doc.body.append(controls.element);
  return {win,doc,calls,controls,close(){controls.destroy();if(!existing)win.close();}};
}
const button=(doc,text)=>[...doc.querySelectorAll('button')].find(el=>el.textContent===text);
const change=h=>h.controls.element.dispatchEvent(new h.win.Event('change',{bubbles:true}));
function choose(h,value){h.controls.element.value=value;change(h);}

// The old dialog Apply/Cancel/focus-trap contract is superseded by one native select.
// Catalog, persistence, authorization, run lock and detached-handler checks remain.
test('native Model dropdown commits exact selection immediately without a modal or POST',async()=>{
 const h=setup();try{
  await tick();const select=h.controls.element;
  assert.equal(select.tagName,'SELECT','primary model control must be a native select');
  assert.equal(select.getAttribute('aria-label'),'Model');
  assert.equal(select.value,'');assert.match(select.selectedOptions[0].textContent,/alpha/);
  assert.equal(h.controls.selection(),null,'default display must not pin an override');
  choose(h,'1');
  assert.deepEqual(h.controls.selection(),{model:'alpha',provider:'two'});
  assert.deepEqual(JSON.parse(h.win.sessionStorage.getItem(storageKey())),{model:'alpha',provider:'two'});
  assert.equal(h.doc.querySelector('[role=dialog],.dialog-overlay'),null);
  assert.equal(button(h.doc,'Apply'),undefined);assert.equal(h.calls.length,1,'change is local, not a POST');
  choose(h,'');assert.equal(h.controls.selection(),null);assert.equal(h.win.sessionStorage.getItem(storageKey()),null);
 }finally{h.close();}
});
test('native grouped options retain only advertised effort combinations and restore them exactly',async()=>{
 const h=setup();try{
  await tick();const select=h.controls.element;
  const deep=[...select.options].find(o=>o.textContent==='Alpha (one) — deep');
  assert.ok(deep,'advertised effort is a direct native option, not a modal field');
  assert.equal(deep.parentElement.tagName,'OPTGROUP');
  assert.equal([...select.options].some(o=>o.textContent.includes('elsewhere') && o.textContent.includes('deep')),false);
  choose(h,deep.value);
  const expected={model:'alpha',provider:'one',reasoning_effort:'deep'};
  assert.deepEqual(h.controls.selection(),expected);
  const copy=h.controls.selection();copy.model='tampered';assert.deepEqual(h.controls.selection(),expected);
  h.controls.destroy();
  const next=setup({win:h.win});try{await tick();assert.deepEqual(next.controls.selection(),expected);assert.equal(next.controls.element.selectedOptions[0].textContent,deep.textContent);}finally{next.close();}
 }finally{h.close();}
});
test('native dropdown never reflects an unadvertised catalogue default',async()=>{
 for(const value of [null,{model:'untrusted',provider:'one'},{model:'alpha',provider:'absent'},{model:'alpha',provider:'one',secret:'do not show'}]){const h=setup({response:{...catalog(),default:value}});try{await tick();assert.equal(h.controls.element.selectedOptions[0].textContent,'Default');assert.equal(h.controls.selection(),null);}finally{h.close();}}
});
test('saved choice cannot silently become Default while its catalog is pending or unavailable',async()=>{
 for(const result of ['ready','failed','unavailable']){
  const win=new JSDOM('<button id="outside">Outside</button>',{url:'https://fixture.test/hermes/'}).window;
  win.sessionStorage.setItem(storageKey(),JSON.stringify({model:'alpha',provider:'one'}));
  const gate=deferred(),h=setup({win,request:()=>gate.promise});try{
   assert.equal(h.controls.canSubmit(),false,'saved explicit choice waits for validation');
   assert.equal(h.controls.element.disabled,true);
   if(result==='failed')gate.reject(new Error('offline'));else gate.resolve(result==='ready'?catalog():{available:false});
   await tick();assert.equal(h.controls.canSubmit(),result==='ready');
   if(result==='ready')assert.deepEqual(h.controls.selection(),{model:'alpha',provider:'one'});
   h.controls.destroy();assert.equal(h.controls.canSubmit(),false);
  }finally{h.close();win.close();}
 }
 const gate=deferred(),h=setup({request:()=>gate.promise});try{assert.equal(h.controls.canSubmit(),true,'legacy Default has no pending explicit choice');gate.resolve({available:false});await tick();assert.equal(h.controls.canSubmit(),true);}finally{h.close();}
});

test('change rejects forged options, numeric coercion, missing selection and duplicate Default identity',async()=>{
 const h=setup();try{
  await tick();choose(h,'0');const select=h.controls.element,wanted={model:'alpha',provider:'one'};
  for(const value of ['invented','999','01','NaN','-1','0.0','1e0',' 1 ','__proto__','0:99','1:0','1','']){
   const forged=new h.win.Option('Forged',value);select.add(forged);forged.selected=true;change(h);
   assert.deepEqual(h.controls.selection(),wanted,`foreign ${JSON.stringify(value)} rejected`);
   assert.deepEqual(JSON.parse(h.win.sessionStorage.getItem(storageKey())),wanted);
   assert.equal(select.value,'0','rejected DOM edit resets to the last committed selection');forged.remove();
  }
  select.selectedIndex=-1;change(h);assert.deepEqual(h.controls.selection(),wanted,'empty selection is not the trusted Default option');assert.equal(select.value,'0');
  const original=[...select.options].find(o=>o.value==='0');original.value='1';original.selected=true;change(h);assert.deepEqual(h.controls.selection(),wanted,'modified trusted option cannot become another provider');
 }finally{h.close();}
});

test('rejected duplicate values cannot shadow the committed option during visual recovery',async()=>{
 const h=setup();try{
  await tick();choose(h,'0');const select=h.controls.element,original=select.selectedOptions[0];
  const shadow=new h.win.Option('Foreign provider','0');select.prepend(shadow);shadow.selected=true;change(h);
  assert.deepEqual(h.controls.selection(),{model:'alpha',provider:'one'});
  assert.equal(select.selectedOptions[0],original,'recover using trusted option identity, not its duplicate value');
 }finally{h.close();}
});

test('accepted catalog is snapshotted so later source mutation cannot retarget a validated choice',async()=>{
 const response=catalog(),h=setup({response});try{
  await tick();response.models[0].id='foreign';response.models[0].provider='foreign';response.models[0].reasoning_efforts.length=0;
  choose(h,'0:1');assert.deepEqual(h.controls.selection(),{model:'alpha',provider:'one',reasoning_effort:'deep'});
  assert.deepEqual(JSON.parse(h.win.sessionStorage.getItem(storageKey())),{model:'alpha',provider:'one',reasoning_effort:'deep'});
 }finally{h.close();}
});

test('route/account fences and destroy ignore late GETs without restoring storage or stealing focus',async()=>{
 for(const mode of ['stale','destroy']){
  let current=true;const gate=deferred(),h=setup({request:()=>gate.promise,isCurrent:()=>current});
  try{
   const saved=JSON.stringify({model:'alpha',provider:'one'});h.win.sessionStorage.setItem(storageKey(),saved);
   if(mode==='stale')current=false;else h.controls.destroy();
   h.doc.querySelector('#outside').focus();gate.resolve(catalog());await tick();
   assert.equal(h.controls.element.hidden,true,mode);assert.equal(h.controls.selection(),null);
   assert.equal(h.win.sessionStorage.getItem(storageKey()),saved);assert.equal(h.doc.activeElement.id,'outside');change(h);
   assert.equal(h.doc.querySelector('[role="dialog"]'),null);
   h.controls.setLocked(false);assert.equal(h.controls.element.disabled,true);
  }finally{h.close();}
 }
});

test('stale change and navigation cleanup cannot mutate a selection or reactivate detached listeners',async()=>{
 for(const mode of ['stale','destroy']){
  let current=true;const h=setup({isCurrent:()=>current});try{
   await tick();choose(h,'0');const saved=h.win.sessionStorage.getItem(storageKey());
   h.controls.element.value='1';h.doc.querySelector('#outside').focus();
   if(mode==='stale')current=false;else h.controls.destroy();
   change(h);assert.equal(h.win.sessionStorage.getItem(storageKey()),saved);
   assert.equal(h.controls.selection(),null);assert.equal(h.doc.querySelector('[role="dialog"]'),null);assert.equal(h.doc.activeElement.id,'outside');
   h.controls.destroy();h.controls.destroy();change(h);assert.equal(h.win.sessionStorage.getItem(storageKey()),saved);
  }finally{h.close();}
 }
});

test('lock disables direct selection before and after load and rejects queued changes without mutation',async()=>{
 const h=setup();try{
  h.controls.setLocked(true);await tick();assert.equal(h.controls.element.disabled,true);
  choose(h,'1');assert.equal(h.controls.selection(),null);assert.equal(h.win.sessionStorage.getItem(storageKey()),null);
  h.controls.setLocked(false);choose(h,'0');const saved=h.win.sessionStorage.getItem(storageKey());
  h.controls.element.value='1';h.controls.setLocked(true);change(h);
  assert.equal(h.controls.element.disabled,true);assert.deepEqual(h.controls.selection(),{model:'alpha',provider:'one'});
  assert.equal(h.win.sessionStorage.getItem(storageKey()),saved);
  h.controls.setLocked(false);assert.equal(h.controls.element.disabled,false);assert.equal(h.controls.element.value,'0','blocked change must not leave an uncommitted provider on screen');
 }finally{h.close();}
});

test('native keyboard events are not trapped and focus is not stolen by load or change',async()=>{
 const h=setup();try{
  h.doc.querySelector('#outside').focus();await tick();assert.equal(h.doc.activeElement.id,'outside');
  const select=h.controls.element;select.focus();
  for(const key of ['Tab','Escape','ArrowDown']){
   const event=new h.win.KeyboardEvent('keydown',{key,bubbles:true,cancelable:true});select.dispatchEvent(event);assert.equal(event.defaultPrevented,false,'browser owns native keyboard behavior');
  }
  choose(h,'1');assert.equal(h.doc.activeElement,select);assert.deepEqual(h.controls.selection(),{model:'alpha',provider:'two'});
  assert.equal(h.doc.querySelector('[role=dialog],.dialog-overlay'),null);assert.equal(select.hasAttribute('aria-haspopup'),false,'do not claim a custom dialog');
 }finally{h.close();}
});

test('valid selections persist per account/session and invalid saved overrides are removed before forwarding',async()=>{
 const h=setup();try{
  await tick();choose(h,'0:1');
  const expected={model:'alpha',provider:'one',reasoning_effort:'deep'};
  assert.deepEqual(JSON.parse(h.win.sessionStorage.getItem(storageKey())),expected);h.controls.destroy();
  for(const [userId,sessionId,wanted] of [['user','session',expected],['other','session',null],['user','other',null]]){
   const next=setup({win:h.win,userId,sessionId});try{assert.equal(next.controls.selection(),null,'never restore before GET');await tick();assert.deepEqual(next.controls.selection(),wanted);}finally{next.close();}
  }
  const invalid=[{...expected,model:'retired'}, {...expected,provider:'unknown'}, {...expected,reasoning_effort:'invented'}, {...expected,extra:true}, {model:'alpha',provider:'two',reasoning_effort:'deep'}, [], null, 'broken-json'];
  for(const value of invalid){
   h.win.sessionStorage.setItem(storageKey(),value==='broken-json'?value:JSON.stringify(value));
   const next=setup({win:h.win});try{await tick();assert.equal(next.controls.selection(),null);assert.equal(h.win.sessionStorage.getItem(storageKey()),null);}finally{next.close();}
  }
  const next=setup({win:h.win});try{await tick();choose(next,'1');assert.ok(h.win.sessionStorage.getItem(storageKey()));choose(next,'');assert.equal(h.win.sessionStorage.getItem(storageKey()),null);}finally{next.close();}
 }finally{h.close();}
});

test('only advertised effort is submitted; base choices reset effort and Default omits overrides',async()=>{
 const h=setup();try{
  await tick();assert.deepEqual([...h.controls.element.options].map(o=>o.textContent),['Default — alpha','Alpha (one)','Alpha (one) — brief','Alpha (one) — deep','Alpha elsewhere (two)']);
  choose(h,'0:1');assert.deepEqual(h.controls.selection(),{model:'alpha',provider:'one',reasoning_effort:'deep'});
  choose(h,'1');assert.deepEqual(h.controls.selection(),{model:'alpha',provider:'two'});
  choose(h,'0');assert.deepEqual(h.controls.selection(),{model:'alpha',provider:'one'});
  choose(h,'');assert.equal(h.controls.selection(),null);assert.equal(h.controls.element.selectedOptions[0].textContent,'Default — alpha');assert.equal(h.calls.length,1);
 }finally{h.close();}
});

test('unavailable, failed, and partial catalogs never expose invented choices',async()=>{
 const good=catalog();
 const cases=[null,{}, {available:false,models:good.models}, {...good,available:'true'}, {...good,models:[]},
  {...good,models:[...good.models,{id:'broken'}]},
  ...['id','provider','label','reasoning_efforts'].map(key=>({...good,models:[{...good.models[0],[key]:null}]})),
  {...good,models:[{...good.models[0],reasoning_efforts:['deep','deep']}]},
  {...good,models:[{...good.models[0],reasoning_efforts:[true]}]},
  {...good,models:[good.models[0],good.models[0]]},new Error('403'), 'sync-error'];
 for(const response of cases){
  const h=setup({response,request:response instanceof Error?()=>Promise.reject(response):response==='sync-error'?()=>{throw new Error('offline');}:undefined});
  try{await tick();assert.equal(h.controls.element.hidden,true,JSON.stringify(response));assert.equal(h.controls.element.disabled,true);assert.equal(h.controls.selection(),null);change(h);assert.equal(h.doc.querySelector('[role="dialog"]'),null);}finally{h.close();}
 }
});

test('loads read-only catalog once and reveals a named native select without submitting',async()=>{
 const gate=deferred(),h=setup({sessionId:'session /?',request:()=>gate.promise});
 try {
  assert.equal(h.controls.element.hidden,true);assert.equal(h.controls.element.disabled,true);h.controls.setLocked(false);assert.equal(h.controls.element.disabled,true);assert.equal(h.controls.selection(),null);
  gate.resolve(catalog());await tick();
  assert.equal(h.controls.element.hidden,false);assert.equal(h.controls.element.disabled,false);
  assert.equal(h.controls.element.type,'select-one');assert.equal(h.controls.element.getAttribute('aria-label'),'Model');
  assert.match(h.controls.element.title,/next message/);
  assert.deepEqual(h.calls,[{path:'/sessions/session%20%2F%3F/model-options',options:undefined}]);
 } finally {h.close();}
});
