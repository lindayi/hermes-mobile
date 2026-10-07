import test from 'node:test';
import assert from 'node:assert/strict';
import {createAPI} from '../../frontend/api.mjs';
import {createRequire} from 'node:module';
const require = createRequire('/usr/local/lib/hermes-agent/package.json');
const {JSDOM} = require('jsdom');
const tick = () => new Promise(resolve => setTimeout(resolve, 10));
export async function setup(request, configure = () => {}) {
  const dom = new JSDOM('<div id="app"></div>', {url:'https://lindayi.me/hermes/'});
  configure(dom.window);
  const module = await import('../../frontend/ui.mjs').catch(() => ({}));
  assert.equal(typeof module.mountApp, 'function', 'mobile app exists');
  const app = await module.mountApp(dom.window.document, {request,clear(){}}, dom.window);
  return {dom,doc:dom.window.document,app};
}
export const click = (doc, text) => {
  const el = [...doc.querySelectorAll('button,a')].find(el => el.textContent.trim() === text || el.getAttribute('aria-label') === text);
  assert.ok(el, `control ${text} exists`); el.click(); return el;
};

for(const outcome of ['abandoned','accepted','late upload','concurrent removal'])test(`photo cleanup: ${outcome}`,async t=>{
  const deletes=[],pendingDeletes=new Map(),revoked=[];
  let uploads=0,finishUpload;
  const {doc,app}=await setup(async(path,options={})=>{
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s1',title:'Photo fixture'}],total:1};
    if(path.includes('/messages'))return {items:[]};
    if(path==='/sessions/s1/attachments'){
      const metadata={id:String(++uploads).padStart(32,'0'),status:'pending'};
      if(outcome==='late upload')return new Promise(resolve=>{finishUpload=()=>resolve(metadata);});
      return metadata;
    }
    if(options.method==='DELETE'){
      deletes.push(path);
      if(outcome==='concurrent removal')return new Promise(resolve=>pendingDeletes.set(path,resolve));
      throw new Error('Synthetic release failure');
    }
    if(path==='/runs'){
      if(outcome==='accepted')return {id:'r1',session_id:'s1',status:'completed'};
      throw Object.assign(new Error('Synthetic send failure'),{status:422});
    }
    if(path==='/runs/r1')return {id:'r1',session_id:'s1',status:'completed'};
    return {items:[]};
  },win=>{
    win.URL.createObjectURL=file=>`blob:${file.name}`;
    win.URL.revokeObjectURL=url=>revoked.push(url);
  });
  t.after(()=>{app.destroy();doc.defaultView.close();});
  click(doc,'Photo fixture');await tick();
  const input=doc.querySelector('input[type=file]'),win=doc.defaultView;
  const count=outcome==='concurrent removal'?3:1;
  Object.defineProperty(input,'files',{value:Array.from({length:count},(_,i)=>new win.File(['synthetic'],`photo-${i}.png`,{type:'image/png'}))});
  input.dispatchEvent(new win.Event('change'));
  click(doc,'Send message');await tick();await tick();
  if(outcome==='concurrent removal'){
    click(doc,'Remove photo 1');click(doc,'Remove photo 2');
    pendingDeletes.get('/sessions/s1/attachments/'+String(1).padStart(32,'0'))();await tick();
    pendingDeletes.get('/sessions/s1/attachments/'+String(2).padStart(32,'0'))();await tick();
    assert.deepEqual([...doc.querySelectorAll('.photo-preview img')].map(img=>img.getAttribute('src')),['blob:photo-2.png']);
  }else if(outcome==='accepted'){
    assert.equal(doc.querySelectorAll('.photo-preview').length,0);
    assert.deepEqual(deletes,[],'accepted run photos must not be released');
  }else{
    click(doc,'Chats');await tick();
    if(finishUpload){finishUpload();await tick();}
    assert.deepEqual(deletes,['/sessions/s1/attachments/'+String(1).padStart(32,'0')]);
    assert.deepEqual(revoked,['blob:photo-0.png']);
  }
});

test('rejected mixed photo batches revoke previews created before validation completes',async t=>{
  const created=[],revoked=[];
  const {doc,app}=await setup(async path=>{
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s1',title:'Photo fixture'}],total:1};
    if(path.includes('/messages'))return {items:[]};
    return {items:[]};
  },win=>{
    win.URL.createObjectURL=file=>{const url=`blob:${file.name}`;created.push(url);return url;};
    win.URL.revokeObjectURL=url=>revoked.push(url);
  });
  t.after(()=>{app.destroy();doc.defaultView.close();});
  click(doc,'Photo fixture');await tick();
  const input=doc.querySelector('input[type=file]'),win=doc.defaultView;
  Object.defineProperty(input,'files',{configurable:true,value:[
    new win.File(['synthetic'],'valid.png',{type:'image/png'}),
    new win.File(['synthetic'],'unsupported.heic',{type:'image/heic'}),
  ]});
  input.dispatchEvent(new win.Event('change'));
  assert.deepEqual(created,['blob:valid.png']);
  assert.deepEqual(revoked,['blob:valid.png']);
  assert.equal(doc.querySelectorAll('.photo-preview').length,0);
});

test('photos selected while a run is pending survive its accepted response',async t=>{
  let acceptRun;
  let uploadNumber=0;
  const {doc,app}=await setup(async(path,options={})=>{
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s1',title:'Photo fixture'}],total:1};
    if(path.includes('/messages'))return {items:[]};
    if(path==='/sessions/s1/attachments')
      return {id:String(++uploadNumber).padStart(32,'0'),status:'pending'};
    if(path==='/runs')return new Promise(resolve=>{acceptRun=resolve;});
    if(path==='/runs/r1')return {id:'r1',session_id:'s1',status:'completed'};
    return {items:[]};
  },win=>{win.URL.createObjectURL=file=>`blob:${file.name}`;});
  t.after(()=>{app.destroy();doc.defaultView.close();});
  click(doc,'Photo fixture');await tick();
  const input=doc.querySelector('input[type=file]'),win=doc.defaultView;
  const select=file=>{Object.defineProperty(input,'files',{configurable:true,value:[
    new win.File(['synthetic'],file,{type:'image/png'}),
  ]});input.dispatchEvent(new win.Event('change'));};
  select('submitted.png');click(doc,'Send message');
  for(let i=0;i<20&&!acceptRun;i++)await tick();
  assert.equal(typeof acceptRun,'function');
  click(doc,'Photos');select('later.png');
  acceptRun({id:'r1',session_id:'s1',status:'completed'});
  await tick();await tick();
  assert.deepEqual([...doc.querySelectorAll('.photo-preview img')].map(img=>img.getAttribute('src')),
    ['blob:later.png']);
});

test('ambiguous photo retry blocks a changed photo selection',async t=>{
  const requests=[];
  let uploads=0,runs=0;
  const {doc,app}=await setup(async(path,options={})=>{
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s1',title:'Photo fixture'}],total:1};
    if(path.includes('/messages'))return {items:[]};
    if(path==='/sessions/s1/attachments')
      return {id:String(++uploads).padStart(32,'0'),status:'pending'};
    if(path==='/runs'){
      requests.push(options.body);
      if(++runs===1)throw new Error('response lost');
      return {id:'r1',session_id:'s1',status:'completed'};
    }
    if(path==='/runs/r1')return {id:'r1',session_id:'s1',status:'completed'};
    return {items:[]};
  },win=>{win.URL.createObjectURL=file=>`blob:${file.name}`;});
  t.after(()=>{app.destroy();doc.defaultView.close();});
  click(doc,'Photo fixture');await tick();
  const input=doc.querySelector('input[type=file]'),win=doc.defaultView;
  const select=file=>{Object.defineProperty(input,'files',{configurable:true,value:[
    new win.File(['synthetic'],file,{type:'image/png'}),
  ]});input.dispatchEvent(new win.Event('change'));};
  select('first.png');click(doc,'Send message');await tick();await tick();await tick();
  const firstKey=requests[0]?.idempotency_key;
  assert.ok(firstKey);
  click(doc,'Photos');select('later.png');
  click(doc,'Send message');await tick();await tick();await tick();
  assert.equal(requests.length,1,'changed selection must not retry the ambiguous request');
  assert.equal(requests[0].idempotency_key,firstKey);
  assert.match(doc.querySelector('.photo-status').textContent,/original text and photo selection unchanged/i);
  assert.equal(doc.querySelectorAll('.photo-preview').length,2,'neither the original photo nor new selection is discarded');
});

test('ambiguous text-only retry blocks newly selected photos with recovery guidance',async t=>{
  const requests=[];
  const {doc,app}=await setup(async(path,options={})=>{
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s1',title:'Photo fixture'}],total:1};
    if(path.includes('/messages'))return {items:[]};
    if(path==='/runs'){requests.push(options.body);throw new Error('response lost');}
    return {items:[]};
  },win=>{win.URL.createObjectURL=file=>`blob:${file.name}`;});
  t.after(()=>{app.destroy();doc.defaultView.close();});
  click(doc,'Photo fixture');await tick();
  const textarea=doc.querySelector('textarea'),input=doc.querySelector('input[type=file]'),win=doc.defaultView;
  textarea.value='Describe the next photo';textarea.dispatchEvent(new win.Event('input'));
  click(doc,'Send message');await tick();await tick();
  Object.defineProperty(input,'files',{configurable:true,value:[
    new win.File(['synthetic'],'new-photo.png',{type:'image/png'}),
  ]});
  input.dispatchEvent(new win.Event('change'));
  click(doc,'Send message');await tick();
  assert.equal(requests.length,1,'new photos cannot be omitted from an ambiguous text-only retry');
  assert.match(doc.querySelector('.photo-status').textContent,/uncertain outcome.*original text and photo selection unchanged/i);
  assert.equal(textarea.value,'Describe the next photo');
  assert.equal(doc.querySelectorAll('.photo-preview').length,1);
});

test('ambiguous photo retry keeps the submitted photos locked and reuses the exact request',async t=>{
  const requests=[];
  let uploads=0,runs=0;
  const {doc,app}=await setup(async(path,options={})=>{
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s1',title:'Photo fixture'}],total:1};
    if(path.includes('/messages'))return {items:[]};
    if(path==='/sessions/s1/attachments')
      return {id:String(++uploads).padStart(32,'0'),status:'pending'};
    if(path==='/runs'){
      requests.push(options.body);
      if(++runs===1)throw new Error('response lost');
      return {id:'r1',session_id:'s1',status:'completed'};
    }
    if(path==='/runs/r1')return {id:'r1',session_id:'s1',status:'completed'};
    return {items:[]};
  },win=>{win.URL.createObjectURL=file=>`blob:${file.name}`;});
  t.after(()=>{app.destroy();doc.defaultView.close();});
  click(doc,'Photo fixture');await tick();
  const input=doc.querySelector('input[type=file]'),win=doc.defaultView;
  Object.defineProperty(input,'files',{configurable:true,value:[
    new win.File(['synthetic'],'submitted.png',{type:'image/png'}),
  ]});
  input.dispatchEvent(new win.Event('change'));
  click(doc,'Send message');await tick();await tick();await tick();
  const remove=doc.querySelector('[aria-label="Remove photo 1"]');
  assert.equal(remove.disabled,true,'ambiguous submitted photos cannot be released');
  click(doc,'Send message');await tick();await tick();await tick();
  assert.equal(requests.length,2);
  assert.equal(requests[1].idempotency_key,requests[0].idempotency_key);
  assert.deepEqual(requests[1].attachments,requests[0].attachments);
  assert.deepEqual([...doc.querySelectorAll('.photo-preview img')].map(img=>img.getAttribute('src')),[]);
  assert.equal(uploads,1,'retry reuses the uploaded attachment');
});

test('cancelled photo selection leaves the draft and selected photos unchanged',async t=>{
  const {doc,app}=await setup(async path=>{
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s1',title:'Photo fixture'}],total:1};
    if(path.includes('/messages'))return {items:[]};
    return {items:[]};
  },win=>{win.URL.createObjectURL=file=>`blob:${file.name}`;});
  t.after(()=>{app.destroy();doc.defaultView.close();});
  click(doc,'Photo fixture');await tick();
  const input=doc.querySelector('input[type=file]'),win=doc.defaultView,textarea=doc.querySelector('textarea');
  textarea.value='Keep my draft';textarea.dispatchEvent(new win.Event('input'));
  Object.defineProperty(input,'files',{configurable:true,value:[
    new win.File(['synthetic'],'selected.png',{type:'image/png'}),
  ]});
  input.dispatchEvent(new win.Event('change'));
  Object.defineProperty(input,'files',{configurable:true,value:[]});
  input.dispatchEvent(new win.Event('change'));
  assert.equal(textarea.value,'Keep my draft');
  assert.deepEqual([...doc.querySelectorAll('.photo-preview img')].map(img=>img.getAttribute('src')),
    ['blob:selected.png']);
});

test('removing a photo during its upload restores the composer for retry',async t=>{
  let finishUpload;
  const {doc,app}=await setup(async path=>{
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s1',title:'Photo fixture'}],total:1};
    if(path.includes('/messages'))return {items:[]};
    if(path==='/sessions/s1/attachments')
      return new Promise(resolve=>{finishUpload=()=>resolve({
        id:'00000000000000000000000000000001',status:'pending',
      });});
    return {items:[]};
  },win=>{win.URL.createObjectURL=file=>`blob:${file.name}`;});
  t.after(()=>{app.destroy();doc.defaultView.close();});
  click(doc,'Photo fixture');await tick();
  const input=doc.querySelector('input[type=file]'),win=doc.defaultView;
  Object.defineProperty(input,'files',{value:[
    new win.File(['synthetic'],'remove-during-upload.png',{type:'image/png'}),
  ]});
  input.dispatchEvent(new win.Event('change'));
  click(doc,'Send message');await tick();
  assert.equal(typeof finishUpload,'function');
  click(doc,'Remove photo 1');await tick();
  finishUpload();await tick();await tick();
  assert.equal(doc.querySelector('[aria-label="Send message"]').disabled,false);
  assert.equal(doc.querySelectorAll('.photo-preview').length,0);
});

test('navigation during native run admission does not delete submitted photos',async t=>{
  let acceptRun;
  const deletes=[];
  const {doc,app}=await setup(async(path,options={})=>{
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s1',title:'Photo fixture'}],total:1};
    if(path.includes('/messages'))return {items:[]};
    if(path==='/sessions/s1/attachments')
      return {id:'00000000000000000000000000000001',status:'pending'};
    if(options.method==='DELETE'){deletes.push(path);return {released:true};}
    if(path==='/runs')return new Promise(resolve=>{acceptRun=resolve;});
    return {items:[]};
  },win=>{win.URL.createObjectURL=file=>`blob:${file.name}`;});
  t.after(()=>{app.destroy();doc.defaultView.close();});
  click(doc,'Photo fixture');await tick();
  const input=doc.querySelector('input[type=file]'),win=doc.defaultView;
  Object.defineProperty(input,'files',{value:[
    new win.File(['synthetic'],'navigation-pending.png',{type:'image/png'}),
  ]});
  input.dispatchEvent(new win.Event('change'));
  click(doc,'Send message');
  for(let i=0;i<20&&!acceptRun;i++)await tick();
  assert.equal(typeof acceptRun,'function');
  click(doc,'Chats');await tick();
  assert.deepEqual(deletes,[]);
  acceptRun({id:'r1',session_id:'s1',status:'completed'});
  await tick();
});

test('photo selection cannot be silently dropped by text-only steering',async t=>{
  const calls=[];
  class Events{
    addEventListener(){}
    close(){}
  }
  const {doc,app}=await setup(async(path,options={})=>{
    calls.push({path,options});
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s1',title:'Photo fixture'}],total:1};
    if(path.includes('/messages'))return {items:[]};
    if(path==='/runs' || path==='/runs/r1')return {id:'r1',session_id:'s1',status:'running'};
    if(path==='/runs/r1/controls')return {steering:true,attempts:[]};
    return {items:[]};
  },win=>{win.EventSource=Events;win.URL.createObjectURL=file=>`blob:${file.name}`;});
  t.after(()=>{app.destroy();doc.defaultView.close();});
  click(doc,'Photo fixture');await tick();
  const textarea=doc.querySelector('textarea');
  textarea.value='Start run';textarea.dispatchEvent(new doc.defaultView.Event('input'));
  click(doc,'Send message');
  for(let i=0;i<20&&!doc.querySelector('.composer [aria-label="Steer current run"]');i++)await tick();
  const picker=doc.querySelector('.composer-bottom [aria-label="Add photos"]');
  assert.equal(picker.disabled,true);
  const input=doc.querySelector('input[type=file]'),win=doc.defaultView;
  Object.defineProperty(input,'files',{value:[
    new win.File(['synthetic'],'late-photo.png',{type:'image/png'}),
  ]});
  input.dispatchEvent(new win.Event('change'));
  textarea.value='Analyze this photo';textarea.dispatchEvent(new win.Event('input'));
  click(doc,'Steer current run');await tick();
  assert.equal(calls.some(call=>call.path==='/runs/r1/steer'),false);
  assert.match(doc.querySelector('.steering-status').textContent,/send them in a new message/i);
  assert.equal(doc.querySelectorAll('.photo-preview').length,1);
});

test('anonymous shell offers passkey login and invite enrollment, not fake conversations', async () => {
  const {doc} = await setup(async () => {throw Object.assign(new Error('Sign in'), {status:401});});
  assert.match(doc.body.textContent, /Hermes/);
  click(doc, 'Sign in with a passkey');
  await tick();
  assert.match(doc.body.textContent, /passkeys.*supported|secure.*browser/i);
  click(doc, 'Use an invitation');
  assert.ok(doc.querySelector('input[name="code"]'));
  assert.ok(doc.querySelector('input[name="display_name"]'));
  assert.equal(doc.querySelectorAll('.message').length, 0);
});

test('native chats open safely, drafts survive navigation, failed submission remains retryable', async () => {
  const calls = [];
  const {doc,app} = await setup(async (path, options = {}) => {
    calls.push({path,options});
    if (path === '/auth/me') return {user:{id:'u',role:'owner',status:'ready'}};
    if (path.startsWith('/sessions?')) return {items:[{id:'native/1',title:'CLI notes',source:'cli',updated_at:1700000000}],total:1};
    if (path.includes('/messages')) return {items:[{role:'assistant',content:'<img src=x onerror=alert(1)> Hello'}]};
    if (path === '/runs') throw Object.assign(new Error('Native coordination unavailable'), {status:503});
    return {items:[]};
  });
  assert.equal(doc.querySelectorAll('nav button').length, 4);
  click(doc,'CLI notes'); await tick();
  assert.ok(calls.some(c => c.path === '/sessions/native%2F1/messages?latest=true&turn_boundary=true'));
  assert.equal(doc.querySelector('.message img'), null);
  const input = doc.querySelector('textarea'); input.value = 'Keep this draft'; input.dispatchEvent(new doc.defaultView.Event('input'));
  click(doc,'Chats'); await tick(); click(doc,'CLI notes'); await tick();
  assert.equal(doc.querySelector('textarea').value, 'Keep this draft');
  click(doc,'Send message'); await tick();
  assert.match(doc.body.textContent, /Native coordination unavailable/);
  assert.equal(doc.querySelector('textarea').value, 'Keep this draft');
  assert.equal(calls.filter(c => c.path === '/runs').length,1);
  assert.equal(calls.find(c => c.path === '/runs').options.body.session_id,'native/1');
  app.destroy?.();
});

test('successful run reconnects by ID, streams text without resubmission and stops cooperatively', async t => {
  const calls=[]; const streams=[];
  class Events {
    constructor(url){this.url=url;this.listeners={};streams.push(this);}
    addEventListener(name,fn){this.listeners[name]=fn;}
    close(){this.closed=true;}
    emit(name,data){this.listeners[name]?.({data:JSON.stringify(data)});}
  }
  const {doc,app} = await setup(async(path,options={})=>{
    calls.push({path,options});
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s1',title:'Live chat'}],total:1};
    if(path.includes('/messages'))return {items:[]};
    if(path==='/runs')return {id:'r1',status:'running'};
    if(path==='/runs/r1')return {id:'r1',session_id:'s1',status:'running'};
    return {status:'stopping'};
  },win=>{win.EventSource=Events;});
  t.after(()=>{app.destroy();doc.defaultView.close();});
  click(doc,'Live chat');await tick();
  doc.querySelector('textarea').value='Hello'; click(doc,'Send message');await tick();
  assert.equal(streams[0]?.url,'/hermes/app-api/runs/r1/events');
  assert.equal(doc.querySelector('[aria-label="Send message"]'),null,'one active turn replaces Send');assert.equal(doc.querySelector('.live-activity-heading [aria-label="Stop run"]').disabled,false);assert.equal(doc.querySelector('.composer [aria-label="Stop run"]'),null);
  streams[0].emit('delta',{text:'A real response'});
  assert.match(doc.body.textContent,/A real response/);
  click(doc,'Stop run');await tick();
  assert.ok(calls.some(c=>c.path==='/runs/r1/stop'));
  assert.match(doc.body.textContent,/not.*undo|already.*action/i);
  assert.equal(calls.filter(c=>c.path==='/runs').length,1);
  assert.equal(doc.defaultView.localStorage.getItem('hermes:u:run:s1'),'r1','only run ID survives tab closure');
  app.destroy();
});

test('SSE reconnect deduplicates rendered event IDs without losing new deltas or fresh view replay',async()=>{
  const streams=[],calls=[];
  class Events {
    constructor(){this.listeners={};streams.push(this);}
    addEventListener(name,fn){this.listeners[name]=fn;}
    close(){this.closed=true;}
    emit(name,data,lastEventId=''){this.listeners[name]?.({data:JSON.stringify(data),lastEventId});}
  }
  const {doc,app}=await setup(async(path,options={})=>{
    calls.push({path,options});
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s1',title:'Replay chat'}],total:1};
    if(path.includes('/messages'))return {items:[]};
    if(path==='/runs' || path==='/runs/r1')return {id:'r1',session_id:'s1',status:'running'};
    throw new Error(`Unexpected request: ${path}`);
  },win=>{win.EventSource=Events;});
  click(doc,'Replay chat');await tick();
  doc.querySelector('textarea').value='Hello';click(doc,'Send message');await tick();
  const events=streams[0],output=doc.querySelector('.live-message .message-body');
  events.emit('delta',{text:'Hello'},'1');
  events.emit('tool',{name:'Lookup',status:'working'},'2');
  events.onerror();
  events.emit('delta',{text:'Hello'},'1');
  events.emit('tool',{name:'Lookup',status:'working'},'2');
  assert.equal(doc.querySelector('.live-message .activity-summary .markdown').textContent,'Hello','reconnect must not duplicate the public segment folded at the tool boundary');
  assert.equal(output.textContent,'');
  assert.equal(doc.querySelectorAll('.live-message .tool-preview').length,1);
  events.emit('delta',{text:'Hello'},'3');
  assert.equal(output.textContent,'Hello','identical text with a distinct ID is not a duplicate');
  events.listeners.delta({data:'invalid JSON',lastEventId:'4'});
  events.emit('delta',{text:'!'},'4');
  assert.equal(output.textContent,'Hello!','unreadable events do not poison deduplication');
  events.emit('delta',{text:'.'});events.emit('delta',{text:'.'});
  assert.equal(output.textContent,'Hello!..','ID-less compatibility events are not conflated');
  assert.equal(calls.filter(c=>c.path==='/runs').length,1,'reconnect never resubmits');
  click(doc,'Chats');await tick();click(doc,'Replay chat');await tick();
  assert.equal(events.closed,true);
  streams[1].emit('delta',{text:'Hello'},'1');
  assert.equal(doc.querySelector('.live-message .message-body').textContent,'Hello','new view rebuilds output from replay');
  streams[1].emit('done',{},'5');
  assert.equal(streams[1].closed,true);
  assert.equal(doc.querySelector('[aria-label="Send message"]').disabled,false);
  app.destroy();
});

test('inbox shows actual deliveries, marks read and exposes exact approval review', async()=>{
  const calls=[];
  const {doc}=await setup(async(path,options={})=>{
    calls.push({path,options});
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path==='/inbox')return {items:[{id:'n1',title:'Job finished',body:'Actual delivery',read:false,created_at:1700000000,session_id:'s'}]};
    if(path==='/approvals')return {items:[{id:'a1',action:'Create event',target:'Calendar A',expires_at:1999999999}]};
    return {items:[]};
  });
  click(doc,'Inbox');await tick();
  assert.match(doc.body.textContent,/Actual delivery/);
  assert.match(doc.body.textContent,/Create event/);assert.match(doc.body.textContent,/Calendar A/);
  doc.querySelector('[data-inbox-id="n1"] summary').click();await tick();
  assert.ok(calls.some(c=>c.path==='/inbox/n1/read'));
  assert.equal(calls.filter(c=>c.path.includes('/decision')).length,0);
  click(doc,'Deny');await tick();
  assert.deepEqual(calls.find(c=>c.path==='/approvals/a1/decision').options.body,{decision:'deny'});
});

test('job mutations require a review dialog and never show success when backend rejects them',async()=>{
  const calls=[];
  const {doc}=await setup(async(path,options={})=>{
    calls.push({path,options});
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path==='/jobs' && !options.method)return {items:[{id:'j1',name:'Weekly notes',prompt:'Summarize',schedule:'0 9 * * 1',timezone:'America/Toronto',enabled:true}]};
    if(path==='/jobs/j1')throw Object.assign(new Error('Job changes unavailable'),{status:503});
    if(path==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
    return {items:[]};
  },win=>{win.PublicKeyCredential=class {};win.navigator.credentials={get:async()=>fakeCredential()};});
  click(doc,'Jobs');await tick();assert.match(doc.body.textContent,/Weekly notes/);
  doc.querySelector('.job-card summary').click();await tick();assert.equal(doc.querySelector('.job-card').open,true);
  click(doc,'Pause');await tick();
  assert.equal(calls.filter(c=>c.options.method==='PATCH').length,0);
  assert.ok(doc.querySelector('[role="dialog"]'));
  click(doc,'Cancel');await tick();
  assert.equal(calls.filter(c=>c.options.method==='PATCH').length,0);
  assert.equal(calls.filter(c=>c.path.startsWith('/auth/verify/')).length,0);
  click(doc,'Pause');await tick();click(doc,'Confirm change');await tick();
  const verifyIndex=calls.findIndex(c=>c.path==='/auth/verify/finish');
  const mutationIndex=calls.findIndex(c=>c.options.method==='PATCH');
  assert.ok(verifyIndex>=0 && verifyIndex<mutationIndex,'fresh passkey must precede mutation');
  assert.deepEqual(calls[mutationIndex].options.body,{enabled:false,confirm:true});
  assert.match(doc.body.textContent,/Job changes unavailable/);
  assert.doesNotMatch(doc.body.textContent,/Job change saved/);
  assert.ok([...doc.querySelectorAll('button')].some(button=>button.textContent==='Pause'));
});

const fakeCredential = () => ({id:'cred',rawId:new Uint8Array([1]).buffer,type:'public-key',getClientExtensionResults:()=>({}),response:{clientDataJSON:new Uint8Array([2]).buffer,authenticatorData:new Uint8Array([3]).buffer,signature:new Uint8Array([4]).buffer,userHandle:null}});
for (const editing of [false,true]) test(`job ${editing ? 'edit' : 'create'} form sends only supported fields after confirmation and step-up`,async()=>{
  const calls=[];
  const {doc}=await setup(async(path,options={})=>{
    calls.push({path,options});
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path==='/jobs' && !options.method)return {items:[{id:'abcdef012345',name:'Weekly notes',prompt:'Summarize',schedule:'0 9 * * 1',enabled:true}]};
    if(path==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
    if(path.startsWith('/jobs') && options.method)throw Object.assign(new Error('Profile gateway is not ready'),{status:503});
    return {items:[]};
  },win=>{win.PublicKeyCredential=class {};win.navigator.credentials={get:async()=>fakeCredential()};});
  click(doc,'Jobs');await tick();
  if(editing){doc.querySelector('.job-card summary').click();await tick();assert.equal(doc.querySelector('.job-card').open,true);}
  click(doc,editing ? 'Edit' : 'New job');
  const form=doc.querySelector('form');
  form.elements.name.value='Daily notes';form.elements.prompt.value='Summarize notes';form.elements.schedule.value='0 9 * * *';
  click(doc,'Review change');await tick();
  assert.equal(calls.filter(c=>c.path.startsWith('/auth/verify/')).length,0);
  assert.equal(calls.filter(c=>c.path.startsWith('/jobs') && c.options.method).length,0);
  click(doc,'Confirm change');await tick();
  const mutation=calls.find(c=>c.path.startsWith('/jobs') && c.options.method);
  assert.deepEqual(mutation?.options.body,{name:'Daily notes',prompt:'Summarize notes',schedule:'0 9 * * *',confirm:true});
  assert.equal(mutation.path,editing ? '/jobs/abcdef012345' : '/jobs');
  assert.equal(mutation.options.method,editing ? 'PATCH' : 'POST');
  const verified=calls.findIndex(c=>c.path==='/auth/verify/finish');
  assert.ok(verified>=0 && verified<calls.indexOf(mutation));
  assert.equal(form.querySelector('[name="timezone"]'),null,'no unsupported timezone editor');
  assert.match(doc.body.textContent,/server.*timezone|timezone.*server/i);
  assert.match(doc.body.textContent,/Profile gateway is not ready/);
  assert.doesNotMatch(doc.body.textContent,/Job change saved/);
});

for (const control of ['Delete','Resume']) test(`job ${control} sends confirmed JSON and CSRF through the API client after step-up`,async()=>{
  const calls=[];
  const api=createAPI(async(url,options)=>{
    const path=url.replace('/hermes/app-api','');calls.push({path,options});
    let result={ok:true},status=200;
    if(path==='/auth/me')result={user:{id:'u',status:'ready'},csrf_token:'csrf-test'};
    else if(path==='/jobs')result={items:[{id:'abcdef012345',name:'Paused notes',schedule:'0 9 * * *',enabled:false}]};
    else if(path==='/auth/verify/options')result={challenge_id:'c1',options:{challenge:'AQ'}};
    else if(path==='/jobs/abcdef012345'){result={detail:'Profile gateway is not ready'};status=503;}
    else if(path.startsWith('/sessions?'))result={items:[]};
    return new Response(JSON.stringify(result),{status});
  });
  const {doc}=await setup(api.request,win=>{win.PublicKeyCredential=class {};win.navigator.credentials={get:async()=>fakeCredential()};});
  click(doc,'Jobs');await tick();doc.querySelector('.job-card summary').click();await tick();assert.equal(doc.querySelector('.job-card').open,true);click(doc,control);await tick();
  assert.equal(calls.some(c=>c.path==='/jobs/abcdef012345'),false);
  click(doc,'Confirm change');await tick();
  const mutation=calls.find(c=>c.path==='/jobs/abcdef012345');
  assert.equal(mutation?.options.method,control==='Delete' ? 'DELETE' : 'PATCH');
  assert.deepEqual(JSON.parse(mutation.options.body),control==='Delete' ? {confirm:true} : {enabled:true,confirm:true});
  assert.equal(mutation.options.headers['X-CSRF-Token'],'csrf-test');
  assert.equal(mutation.options.headers['Content-Type'],'application/json');
  const verified=calls.findIndex(c=>c.path==='/auth/verify/finish');
  assert.ok(verified>=0 && verified<calls.indexOf(mutation));
  assert.match(doc.body.textContent,/Profile gateway is not ready/);
  assert.doesNotMatch(doc.body.textContent,/Job change saved/);
});

for (const failure of ['cancelled','rejected','unsupported']) test(`job mutation is not sent when passkey verification is ${failure}`,async()=>{
  const calls=[];
  const {doc}=await setup(async(path,options={})=>{
    calls.push({path,options});
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path==='/jobs')return {items:[{id:'abcdef012345',name:'Notes',schedule:'0 9 * * *',enabled:true}]};
    if(path==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
    if(path==='/auth/verify/finish')throw Object.assign(new Error('Passkey verification failed'),{status:403});
    return {items:[]};
  },win=>{
    if(failure==='unsupported')return;
    win.PublicKeyCredential=class {};
    win.navigator.credentials={get:async()=>{
      if(failure==='cancelled')throw Object.assign(new Error('cancelled'),{name:'NotAllowedError'});
      return fakeCredential();
    }};
  });
  click(doc,'Jobs');await tick();doc.querySelector('.job-card summary').click();await tick();assert.equal(doc.querySelector('.job-card').open,true);click(doc,'Delete');await tick();click(doc,'Confirm change');await tick();
  assert.equal(calls.some(c=>c.path.startsWith('/jobs') && c.options.method),false);
  assert.match(doc.body.textContent,/cancelled|Passkey verification failed|Passkeys are not supported/);
  assert.doesNotMatch(doc.body.textContent,/Job change saved/);
});

test('owner creates invitations only after passkey verification and sees the code once',async()=>{
  const calls=[];
  const {doc}=await setup(async(path,options={})=>{
    calls.push({path,options});
    if(path==='/auth/me')return {user:{id:'u',role:'owner',status:'ready'}};
    if(path==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ',rpId:'lindayi.me'}};
    if(path==='/invites' && options.method==='POST')return {code:'ONE-TIME-TEST-CODE',invite:{id:'i1'}};
    return {items:[]};
  },win=>{win.PublicKeyCredential=class {};win.navigator.credentials={get:async()=>fakeCredential()};});
  click(doc,'Settings');await tick();
  assert.ok(doc.querySelector('select[name="theme"]'));
  click(doc,'Create invitation');await tick();
  doc.querySelector('input[name="label"]').value='Family';
  click(doc,'Create code');await tick();
  assert.match(doc.body.textContent,/ONE-TIME-TEST-CODE/);
  assert.ok(calls.findIndex(c=>c.path==='/auth/verify/finish') < calls.findIndex(c=>c.path==='/invites' && c.options.method==='POST'));
  assert.deepEqual(calls.find(c=>c.path==='/invites' && c.options.method==='POST').options.body,{label:'Family',expires_days:7});
  click(doc,'Done');await tick();
  assert.doesNotMatch(doc.body.textContent,/ONE-TIME-TEST-CODE/);
});

test('security settings revoke a device with explicit confirmation and a fresh passkey',async()=>{
  const calls=[];
  const {doc}=await setup(async(path,options={})=>{
    calls.push({path,options});
    if(path==='/auth/me')return {user:{id:'u',role:'member',status:'ready'}};
    if(path==='/devices')return {items:[{id:'d1',label:'Old phone',current:false}]};
    if(path==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
    return {items:[]};
  },win=>{win.PublicKeyCredential=class {};win.navigator.credentials={get:async()=>fakeCredential()};});
  click(doc,'Settings');await tick();
  assert.doesNotMatch(doc.body.textContent,/Create invitation/);
  click(doc,'Revoke device');await tick();assert.equal(calls.filter(c=>c.options.method==='DELETE').length,0);
  click(doc,'Confirm change');await tick();
  assert.ok(calls.some(c=>c.path==='/devices/d1' && c.options.method==='DELETE'));
  assert.ok(calls.findIndex(c=>c.path==='/auth/verify/finish')<calls.findIndex(c=>c.options.method==='DELETE'));
});

test('owner provisions only pending members after confirmation and step-up, without promising activation',async()=>{
  const calls=[];
  const {doc}=await setup(async(path,options={})=>{
    calls.push({path,options});
    if(path==='/auth/me')return {user:{id:'owner',role:'owner',status:'ready'}};
    if(path==='/members')return {items:[
      {id:'pending1',role:'member',display_name:'Pending family',status:'pending'},
      {id:'ready1',role:'member',display_name:'Ready family',status:'ready'},
      {id:'disabled1',role:'member',display_name:'Disabled family',status:'disabled'}]};
    if(path==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
    if(path==='/auth/verify/finish')return {ok:true};
    if(path==='/members/pending1/provision')return {id:'pending1',status:'pending',provisioning_status:'provisioned',ready:false};
    return {items:[]};
  },win=>{win.PublicKeyCredential=class {};win.navigator.credentials={get:async()=>fakeCredential()};});
  click(doc,'Settings');await tick();
  assert.equal([...doc.querySelectorAll('button')].filter(b=>b.textContent==='Provision profile').length,1);
  assert.equal(calls.some(c=>c.path.endsWith('/provision')),false,'opening settings never provisions');
  click(doc,'Provision profile');await tick();
  assert.match(doc.querySelector('[role="dialog"]').textContent,/Pending family/);
  assert.match(doc.querySelector('[role="dialog"]').textContent,/not.*activate|remains pending/i);
  click(doc,'Cancel');await tick();
  assert.equal(calls.some(c=>c.options.method==='POST'),false);
  click(doc,'Provision profile');await tick();click(doc,'Confirm change');await tick();
  const mutations=calls.filter(c=>c.options.method==='POST');
  assert.deepEqual(mutations.map(c=>c.path),['/auth/verify/options','/auth/verify/finish','/members/pending1/provision']);
  assert.deepEqual(mutations.at(-1).options.body,{});
  assert.match(doc.body.textContent,/provisioned/i);
  assert.match(doc.body.textContent,/access remains pending.*operator/i);
});


test('pending member stays in settings and cannot load agent sessions',async()=>{
  const calls=[];
  const {doc}=await setup(async path=>{calls.push(path);return path==='/auth/me' ? {user:{id:'u',role:'member',status:'pending'}} : {items:[]};});
  assert.match(doc.body.textContent,/profile.*pending|preparing.*profile/i);
  click(doc,'Chats');await tick();
  assert.equal(calls.some(path=>path.startsWith('/sessions')),false);
});

test('recovery enrolls a replacement passkey and adding a second passkey uses its documented route',async()=>{
  const calls=[];let signedIn=false;
  const {doc}=await setup(async(path,options={})=>{
    calls.push({path,options});
    if(path==='/auth/me'){if(!signedIn)throw Object.assign(new Error('Sign in'),{status:401});return {user:{id:'u',role:'member',status:'ready'}};}
    if(path==='/auth/recovery/options' || path==='/passkeys')return options.method==='POST' ? {enrollment_id:'e1',options:{challenge:'AQ',user:{id:'Ag',name:'A'},rp:{name:'Hermes'},pubKeyCredParams:[{type:'public-key',alg:-7}]}} : {items:[]};
    if(path==='/auth/recovery/verify')signedIn=true;
    if(path==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
    return {items:[]};
  },win=>{win.PublicKeyCredential=class {};win.navigator.credentials={create:async()=>fakeCredential(),get:async()=>fakeCredential()};});
  click(doc,'Recover access');
  doc.querySelector('input[name="code"]').value='RECOVERY-TEST';doc.querySelector('input[name="display_name"]').value='A';
  click(doc,'Create replacement passkey');await tick();
  assert.ok(calls.some(c=>c.path==='/auth/recovery/verify' && c.options.body.enrollment_id==='e1'));
  click(doc,'Settings');await tick();click(doc,'Add a passkey');await tick();
  doc.querySelector('input[name="label"]').value='Laptop';click(doc,'Save passkey');await tick();
  assert.ok(calls.some(c=>c.path==='/passkeys/verify'));
  assert.deepEqual(calls.find(c=>c.path==='/passkeys' && c.options.method==='POST').options.body,{label:'Laptop'});
});

test('notifications subscribe only after a user gesture using the real PushManager contract',async()=>{
  const calls=[];let permissions=0;let subscribed;
  const subscription={toJSON:()=>({endpoint:'https://push.example/test',keys:{auth:'a',p256dh:'b'}})};
  const preferences={revision:0,enabled:true,categories:{completion:true,approval:true,attention:true,scheduled:true,operational:true},hide_details:false};
  const {doc}=await setup(async(path,options={})=>{calls.push({path,options});if(path==='/auth/me')return {user:{id:'u',role:'member',status:'ready'}};if(path==='/push/key')return {public_key:'AQID'};if(path==='/push/preferences')return options.method==='PUT'?{...options.body,revision:1}:preferences;return {items:[]};},win=>{
    win.Notification={permission:'default',requestPermission:async()=>{permissions++;win.Notification.permission='granted';return 'granted';}};
    win.PushManager=class {};
    win.navigator.serviceWorker={ready:Promise.resolve({pushManager:{getSubscription:async()=>null,subscribe:async options=>{subscribed=options;return subscription;}}})};
  });
  click(doc,'Settings');await tick();assert.equal(permissions,0);
  click(doc,'Enable notifications');await tick();
  assert.equal(permissions,1);assert.equal(subscribed.userVisibleOnly,true);
  assert.deepEqual([...subscribed.applicationServerKey],[1,2,3]);
  assert.deepEqual(calls.find(c=>c.path==='/push/subscriptions').options.body,subscription.toJSON());
  assert.match(doc.body.textContent,/Notifications enabled\./);
  assert.equal(doc.querySelector('[role=switch]').getAttribute('aria-checked'),'true');
  assert.equal(calls.find(c=>c.path==='/push/preferences' && c.options.method==='PUT').options.body.enabled,true);
});

test('recovery codes are displayed once and signing out clears account-local drafts',async()=>{
  const {doc,dom}=await setup(async(path)=>{
    if(path==='/auth/me')return {user:{id:'u',role:'owner',status:'ready'}};
    if(path==='/auth/verify/options')return {challenge_id:'c1',options:{challenge:'AQ'}};
    if(path==='/auth/recovery/codes')return {codes:['TEST-RECOVERY']};
    return {items:[]};
  },win=>{win.PublicKeyCredential=class {};win.navigator.credentials={get:async()=>fakeCredential()};});
  dom.window.sessionStorage.setItem('hermes:u:draft:s','secret');
  click(doc,'Settings');await tick();click(doc,'Generate recovery codes');await tick();click(doc,'Confirm change');await tick();
  assert.match(doc.body.textContent,/TEST-RECOVERY/);click(doc,'Done');await tick();
  click(doc,'Sign out');await tick();
  assert.equal(dom.window.sessionStorage.getItem('hermes:u:draft:s'),null);
  assert.ok([...doc.querySelectorAll('button')].some(b=>b.textContent==='Sign in with a passkey'));
});

test('resuming live run stays at bottom but incoming output does not yank someone reading older history',async()=>{
  let events;
  class Events {constructor(){events=this;this.listeners={};}addEventListener(name,fn){this.listeners[name]=fn;}close(){}emit(name,data){this.listeners[name]({data:JSON.stringify(data)});}}
  const {doc,app}=await setup(async path=>{
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s',title:'Resume'}],total:1};
    if(path==='/runs/r')return {id:'r',session_id:'s',status:'running'};
    return {items:[{role:'assistant',content:'Saved answer'}]};
  },win=>{
    win.EventSource=Events;win.localStorage.setItem('hermes:u:run:s','r');
    Object.defineProperty(win.HTMLElement.prototype,'scrollHeight',{get(){return this.classList.contains('messages') ? this.querySelectorAll('.message').length*200+(this.querySelector('.message-body')?.textContent.length || 0)*10 : 0;}});
    Object.defineProperty(win.HTMLElement.prototype,'clientHeight',{get(){return 100;}});
  });
  click(doc,'Resume');await tick();
  const messages=doc.querySelector('.messages');assert.equal(messages.scrollTop,messages.scrollHeight,'restored live card is visible');
  messages.scrollTop=0;events.emit('delta',{text:'New output'});assert.equal(messages.scrollTop,0,'history reading position preserved');
  messages.scrollTop=messages.scrollHeight;events.emit('delta',{text:' more'});assert.equal(messages.scrollTop,messages.scrollHeight,'bottom-follow continues');
  app.destroy();
});

test('delegation reports are folded tool results with real report content, never user bubbles',async()=>{
  const {doc}=await setup(async path=>{
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s',title:'Delegation'}],total:1};
    return {items:[{role:'user',content:'Please review this.'},{role:'tool',kind:'delegation',name:'delegate_task',status:'completed',content:'[ASYNC DELEGATION BATCH COMPLETE — batch1]\n\nReview passed. <script>unsafe()</script>'}]};
  });
  click(doc,'Delegation');await tick();
  assert.equal(doc.querySelectorAll('.user-message').length,1);
  const result=doc.querySelector('details.delegation-result');assert.ok(result);
  assert.equal(result.open,false);assert.match(result.querySelector('summary').textContent,/Subagent result/);
  assert.match(result.querySelector('.markdown').textContent,/Review passed/);
  assert.equal(result.querySelector('script'),null);
  assert.doesNotMatch(result.textContent,/logs are not shown/);
});

test('terminal run leaves unfinished tools unknown rather than falsely running or successful',async()=>{
  let events;
  class Events {constructor(){events=this;this.listeners={};}addEventListener(name,fn){this.listeners[name]=fn;}close(){}emit(name,data){this.listeners[name]({data:JSON.stringify(data)});}}
  const {doc,app}=await setup(async path=>{
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s',title:'Interrupted tool'}],total:1};
    if(path==='/runs')return {id:'r',status:'running'};
    return {items:[]};
  },win=>{win.EventSource=Events;});
  click(doc,'Interrupted tool');await tick();doc.querySelector('textarea').value='Go';click(doc,'Send message');await tick();
  events.emit('tool',{event:'tool.started',tool:'terminal'});
  events.emit('done',{status:'cancelled'});
  assert.equal(doc.querySelector('.live-message .tool-preview').dataset.status,'unknown');
  assert.doesNotMatch(doc.querySelector('.live-message .tool-preview').textContent,/Running|Succeeded/);
  app.destroy();
});

test('live tool events update a compact preview with native success/failure instead of duplicate entries',async()=>{
  let events;
  class Events {constructor(){events=this;this.listeners={};}addEventListener(name,fn){this.listeners[name]=fn;}close(){}emit(data){this.listeners.tool({data:JSON.stringify(data)});}}
  const {doc,app}=await setup(async path=>{
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s',title:'Live tools'}],total:1};
    if(path==='/runs')return {id:'r',status:'running'};
    return {items:[]};
  },win=>{win.EventSource=Events;});
  click(doc,'Live tools');await tick();doc.querySelector('textarea').value='Go';click(doc,'Send message');await tick();
  const card=doc.querySelector('.live-message details.tool-activity');assert.ok(card,'live uses shared activity card');
  assert.equal(card.hidden,true,'no empty card before tools run');
  events.emit({event:'tool.started',tool:'terminal',summary:'Run: pytest tests'});
  assert.equal(card.hidden,false);assert.equal(card.open,false);
  assert.match(card.querySelector('summary').textContent,/Run: pytest tests/);
  card.open=true;
  assert.equal(doc.querySelector('.live-message .tool-preview').dataset.status,'running');
  events.emit({event:'tool.completed',tool:'terminal',error:false});
  assert.equal(card.open,true,'completion preserves disclosure state');
  assert.match(card.querySelector('.tool-rows').textContent,/Run: pytest tests/,'completion retains started summary');
  assert.equal(doc.querySelector('.live-message .tool-preview').dataset.status,'success');
  assert.equal(doc.querySelectorAll('.live-message .tool-preview').length,1);
  events.emit({event:'tool.started',tool:'terminal'});
  events.emit({event:'tool.completed',tool:'terminal',error:true});
  assert.deepEqual([...doc.querySelectorAll('.live-message .tool-preview')].map(el=>el.dataset.status),['success','failed']);
  app.destroy();
});

test('public activity summaries are foldable without exposing private reasoning fields',async()=>{
  const {doc}=await setup(async path=>{
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s',title:'Activity summary'}],total:1};
    return {items:[
      {role:'assistant',channel:'commentary',content:'I’ll check the available dates.',reasoning_content:'PRIVATE REASONING'},
      {role:'assistant',channel:'analysis',content:'PRIVATE ANALYSIS'},
      {role:'assistant',content:'The result.',reasoning_content:'PRIVATE REASONING'}
    ]};
  });
  click(doc,'Activity summary');await tick();
  const card=doc.querySelector('details.activity-summary');assert.ok(card);assert.equal(card.open,true);card.open=false;assert.equal(card.open,false,'public progress remains collapsible');
  assert.match(card.querySelector('summary').textContent,/Progress/);
  assert.match(card.querySelector('.markdown').textContent,/check the available dates/);
  assert.doesNotMatch(doc.body.textContent,/PRIVATE REASONING|PRIVATE ANALYSIS/);
  assert.equal(doc.querySelectorAll('.assistant-message').length,1);
});

test('UI removes decorative slogans while retaining functional instructions and controls',async()=>{
  const slogans=/YOUR HERMES, WITH YOU|Good to have you here|A place of your own|Find your way back|thoughtful follow-ups|A familiar assistant|Your personal space|PICK UP A THOUGHT|MAKE YOURSELF AT HOME|LET HERMES REMEMBER|WHILE YOU WERE AWAY|Your connected assistant|Hermes can make mistakes|One schedule, connected|Every change is yours|A little room to think|What’s on your mind|Make space for later/;
  const {doc}=await setup(async path=>{
    if(path==='/auth/me')return {user:{id:'u',role:'owner',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s',title:'Chat'}],total:1};
    return {items:[]};
  });
  for(const view of ['Chats','Inbox','Jobs','Settings']) {click(doc,view);await tick();assert.doesNotMatch(doc.body.textContent,slogans);}
  assert.match(doc.body.textContent,/Recovery|passkey/i,'security functions remain');
  click(doc,'Chats');await tick();click(doc,'Chat');await tick();assert.doesNotMatch(doc.body.textContent,slogans);
  assert.ok(doc.querySelector('textarea'));assert.ok(doc.querySelector('[aria-label="Send message"]'));
  const auth=await setup(async()=>{throw Object.assign(new Error('Sign in'),{status:401});});
  assert.doesNotMatch(auth.doc.body.textContent,slogans);
  click(auth.doc,'Use an invitation');assert.doesNotMatch(auth.doc.body.textContent,slogans);
  assert.match(auth.doc.body.textContent,/one-use|one person|passkey/i);
});

test('only assistant answers have a compact Copy control, not user messages',async()=>{
  const {doc}=await setup(async path=>{
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s',title:'No user copy'}],total:1};
    return {items:[{role:'user',content:'My message'},{role:'assistant',content:'Answer'}]};
  });
  click(doc,'No user copy');await tick();
  assert.equal(doc.querySelector('.user-message button'),null);
  const copy=doc.querySelector('.assistant-message button.message-copy');assert.ok(copy);
  assert.equal(copy.getAttribute('aria-label'),'Copy message');
  assert.notEqual(copy.textContent,'Copy');
});

test('saved tool runs share one compact foldable activity card with useful bounded summaries',async()=>{
  const {doc}=await setup(async path=>{
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s',title:'Tool cards'}],total:1};
    return {items:[
      {role:'tool',name:'web_search',status:'success',summary:'Search: Toronto forecast',content:'RAW LOG'},
      {role:'tool',name:'read_file',status:'failed',summary:'Read: README.md',content:'RAW LOG'},
      {role:'assistant',content:'The answer.'}
    ]};
  });
  click(doc,'Tool cards');await tick();
  const card=doc.querySelector('details.tool-activity');assert.ok(card,'history uses a card');
  assert.equal(card.open,false);assert.equal(doc.querySelectorAll('details.tool-activity').length,1);
  assert.match(card.querySelector('summary').textContent,/2 tools/);
  assert.equal(card.dataset.status,'failed');
  assert.equal(card.querySelectorAll('.tool-rows .tool-preview').length,2);
  assert.match(card.querySelector('.tool-rows').textContent,/Search: Toronto forecast/);
  assert.match(card.querySelector('.tool-rows').textContent,/Read: README.md/);
  assert.doesNotMatch(doc.querySelector('.messages').textContent,/RAW LOG|logs are not shown/);
});

test('persisted tool call and its result render once, including across older-page boundary',async()=>{
  const {doc}=await setup(async path=>{
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s',title:'Paired tools'}],total:1};
    if(path.includes('latest=true'))return {offset:1,total:2,items:[{role:'tool',tool_call_id:'call1',name:'terminal',status:'success',content:'raw'}]};
    return {offset:0,total:2,items:[{role:'assistant',content:null,tool_calls:[{id:'call1',function:{name:'terminal'}}]}]};
  });
  click(doc,'Paired tools');await tick();click(doc,'Load older messages');await tick();
  assert.equal(doc.querySelectorAll('.tool-preview').length,1);
  assert.equal(doc.querySelector('.tool-preview').dataset.status,'success');
});

test('tool history has compact accessible status previews, not empty disclosures or assistant bubbles',async()=>{
  const {doc}=await setup(async path=>{
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s',title:'Tool chat'}],total:1};
    return {items:[
      {role:'assistant',content:null,tool_calls:[{function:{name:'web_search'}}]},
      {role:'tool',name:'terminal',status:'success',content:'RAW SECRET'},
      {role:'tool',name:'web_extract',status:'failed',content:'RAW SECRET'},
      {role:'tool',name:'calendar',status:'unknown',content:'RAW SECRET'}
    ]};
  });
  click(doc,'Tool chat');await tick();
  assert.equal(doc.querySelectorAll('.tool-preview').length,4);
  assert.equal(doc.querySelectorAll('.messages .assistant-message').length,0);
  assert.equal(doc.querySelectorAll('.messages details.tool-activity').length,1);
  assert.doesNotMatch(doc.querySelector('.messages').textContent,/RAW SECRET|null|logs are not shown/);
  for(const [status,symbol,label] of [['success','✓','Succeeded'],['failed','×','Failed'],['unknown','?','Status unavailable']]){
    const item=doc.querySelector(`.tool-preview[data-status="${status}"]`);
    assert.ok(item);assert.match(item.textContent,new RegExp(label));assert.ok(item.textContent.includes(symbol));
  }
});

test('native private context is hidden and tool history is collapsed instead of exposing raw logs',async()=>{
  const {doc}=await setup(async path=>{
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s',title:'Safe chat'}],total:1};
    return {items:[{role:'system',content:'SYSTEM SECRET'},{role:'assistant',channel:'analysis',content:'PRIVATE REASONING'},{role:'tool',name:'calendar',content:'RAW SECRET TOOL LOG'},{role:'assistant',channel:'commentary',content:'Checking your calendar.'},{role:'assistant',content:'Your answer.'}]};
  });
  click(doc,'Safe chat');await tick();
  assert.doesNotMatch(doc.body.textContent,/SYSTEM SECRET|PRIVATE REASONING|RAW SECRET TOOL LOG/);
  assert.match(doc.body.textContent,/Progress/);assert.match(doc.body.textContent,/Your answer/);
  assert.ok(doc.querySelector('.tool-preview'));
  assert.ok(doc.querySelector('details.tool-activity .tool-rows'),'tool disclosure contains actual status rows');
  click(doc,'Rename session');await tick();
  assert.ok(doc.querySelector('input[name="title"]'));
});

test('opening history requests newest page, scrolls bottom, and prepends older messages without moving reading position',async()=>{
  const calls=[];
  const {doc}=await setup(async path=>{
    calls.push(path);
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s',title:'Long chat'}],total:1};
    if(path.includes('latest=true'))return {items:[{id:2,role:'assistant',content:'Newest retained page'}],offset:1,total:2};
    return {items:[{id:1,role:'assistant',content:'Oldest retained page'}],offset:0,total:2};
  },win=>{
    Object.defineProperty(win.HTMLElement.prototype,'scrollHeight',{get(){return this.classList.contains('messages') ? this.querySelectorAll('.message').length*200 : 0;}});
    Object.defineProperty(win.HTMLElement.prototype,'clientHeight',{get(){return 100;}});
  });
  click(doc,'Long chat');await tick();
  assert.ok(calls.some(path=>path.includes('latest=true')),'initial load explicitly requests latest page');
  const messages=doc.querySelector('.messages');
  assert.equal(messages.scrollTop,messages.scrollHeight,'initial view is at bottom');
  assert.doesNotMatch(messages.textContent,/Oldest retained/);
  messages.scrollTop=10;
  click(doc,'Load older messages');await tick();
  assert.deepEqual([...messages.querySelectorAll('.markdown')].map(el=>el.textContent.trim()),['Oldest retained page','Newest retained page']);
  assert.equal(messages.scrollTop,210,'prepend preserves previous content position');
  assert.ok(calls.some(path=>path.includes('limit=1&offset=0')));
  assert.equal([...messages.querySelectorAll('button')].some(el=>el.textContent==='Load older messages'),false);
});

test('expired API session removes private content and returns to passkey sign in',async()=>{
  const {doc}=await setup(async path=>{
    if(path==='/auth/me')return {user:{id:'u',status:'ready'}};
    if(path==='/jobs')throw Object.assign(new Error('Session expired'),{status:401});
    return {items:[]};
  });
  click(doc,'Jobs');await tick();
  assert.ok([...doc.querySelectorAll('button')].some(b=>b.textContent==='Sign in with a passkey'));
  assert.equal(doc.querySelector('nav'),null);
});

test('notification deep link opens authorized inbox and never submits or approves anything',async()=>{
  const calls=[];
  const {doc}=await setup(async(path,options={})=>{calls.push({path,options});if(path==='/auth/me')return {user:{id:'u',status:'ready'}};return {items:[]};},win=>win.history.replaceState(null,'','/hermes/?inbox=item1'));
  assert.ok(calls.some(c=>c.path==='/inbox'));
  assert.equal(calls.some(c=>c.options.method==='POST'),false);
  assert.equal(doc.querySelector('[aria-current="page"]').getAttribute('aria-label'),'Inbox');
});





