import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp,formatMessageTime} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(resolve=>setTimeout(resolve,20));
async function setup(snapshot,{background=[],submit}={}){
 const win=new JSDOM('<div id="app"></div>',{url:'https://example.com/hermes/',pretendToBeVisual:true}).window,doc=win.document;let events;
 win.EventSource=class{constructor(){events=this;this.handlers={};}addEventListener(n,f){this.handlers[n]=f;}close(){}emit(n,data){this.handlers[n]?.({data:JSON.stringify(data)});}};
 const app=await mountApp(doc,{clear(){},async request(p,o){if(p==='/auth/me')return {user:{id:'u',status:'ready'}};if(p.startsWith('/sessions?'))return {items:[{id:'s',title:'Clock fixture'}]};if(p.includes('/messages'))return snapshot;if(p.endsWith('/background'))return {items:background};if(p==='/runs' && submit)return submit;return {items:[]};}},win);
 const open=async()=>{doc.querySelector('[aria-label="Clock fixture"]').click();await tick();};
 await open();return {win,doc,app,open,emit:(n,data)=>events.emit(n,data),close(){app.destroy();win.close();}};
}
const iso='2026-03-08T06:59:59.125Z',seconds=Date.parse(iso)/1000;
function checkTime(node,expected){const time=node?.querySelector('time.message-time');assert.ok(time,'recorded message must show time');assert.equal(time.getAttribute('datetime'),expected);assert.match(time.title,/2026/);assert.equal(time.getAttribute('aria-label'),time.title);return time;}
test('native seconds and ISO history display exact recorded time on human and folded process messages',async()=>{
 const h=await setup({items:[{role:'user',content:'Question',timestamp:seconds},{role:'assistant',content:'Answer',timestamp:iso},{role:'tool',kind:'context_compression',status:'completed',content:'Task reminder',timestamp:seconds}],run:null});
 try{checkTime(h.doc.querySelector('.user-message'),iso);checkTime(h.doc.querySelector('.assistant-message'),iso);const card=h.doc.querySelector('.process-history');checkTime(card.querySelector('summary'),iso);assert.equal(card.open,false);assert.equal(card.querySelector('.message-author'),null);assert.match(card.textContent,/Task reminder/);}finally{h.close();}
});
test('invalid, absent, ambiguous and out-of-range timestamps fail closed without a render-clock fallback',async()=>{
 const invalid=[undefined,null,'',true,false,[],{},NaN,Infinity,-1,1e100,1790817877000,'1790817877','not-a-date','2026-03-08','2026-03-08T12:00:00','2026-02-30T00:00:00Z','2099-02-29T00:00:00Z','2026-03-08T24:00:00Z','+100000-01-01T00:00:00Z'];
 for(const value of invalid)assert.equal(formatMessageTime(value),null,`invalid ${String(value)}`);
 const h=await setup({items:[...invalid.map(timestamp=>({role:'user',content:'Missing evidence',timestamp})),{role:'assistant',content:'No final time',created_at:seconds},{role:'system',content:'PRIVATE PROMPT',timestamp:seconds},{role:'assistant',channel:'analysis',content:'PRIVATE ANALYSIS',timestamp:seconds}],run:null});
 try{assert.equal(h.doc.querySelectorAll('.messages time').length,0);assert.doesNotMatch(h.doc.querySelector('.messages').textContent,/PRIVATE/);}finally{h.close();}
});
for(const value of [1e-7,1.2e-7])test(`scientific-notation Unix seconds ${value} fail closed without inventing a timestamp`,()=>{
 assert.equal(formatMessageTime(value),null);
});
test('decimal Unix seconds and offset ISO retain recorded fractional precision',()=>{
 assert.equal(formatMessageTime(0.000001).datetime,'1970-01-01T00:00:00.000001Z');
 assert.equal(formatMessageTime(1772953199).datetime,'2026-03-08T06:59:59.000Z');
 assert.equal(formatMessageTime(1772953199.123456).datetime,'2026-03-08T06:59:59.123456Z');
 assert.equal(formatMessageTime('2026-03-08T01:59:59.123456789-05:00').datetime,'2026-03-08T06:59:59.123456789Z');
});
test('local date is unambiguous across days and DST folds and seconds agree with offset ISO',()=>{
 const options={locale:'en-US',timeZone:'America/Toronto'};
 const before=formatMessageTime(iso,options),after=formatMessageTime('2026-03-08T07:00:00Z',options);
 assert.match(before.text,/Mar 8, 2026.*1:59/);assert.match(before.title,/Eastern Standard Time/);
 assert.match(after.text,/3:00/);assert.match(after.title,/Eastern Daylight Time/);
 assert.deepEqual(formatMessageTime(seconds,options),formatMessageTime('2026-03-08T01:59:59.125-05:00',options));
 assert.match(formatMessageTime('2026-11-01T05:30:00Z',options).title,/Daylight/);
 assert.match(formatMessageTime('2026-11-01T06:30:00Z',options).title,/Standard/);
 assert.match(formatMessageTime('2026-01-01T00:30:00Z',options).text,/Dec 31, 2025/);
 assert.match(formatMessageTime(iso,{locale:'en-GB',timeZone:'Asia/Kolkata'}).text,/12:29/);
 assert.equal(formatMessageTime('2026-03-08T06:59:59.123456Z').datetime,'2026-03-08T06:59:59.123456Z');
 assert.equal(formatMessageTime(1772953199.123456).datetime,'2026-03-08T06:59:59.123456Z');
 assert.equal(formatMessageTime(0).datetime,'1970-01-01T00:00:00.000Z');
});
test('live sent/start/done times use journal evidence and remain stable on reload',async()=>{
 const run={id:'r',session_id:'s',input:'Live question',status:'running',created_at:seconds};
 const snapshot={items:[],run};const h=await setup(snapshot);
 try{
  checkTime(h.doc.querySelector('.user-message'),iso);
  assert.match(checkTime(h.doc.querySelector('.live-message'),iso).textContent,/^Started /);
  h.emit('status',{status:'running',updated_at:seconds+10});checkTime(h.doc.querySelector('.live-message'),iso);
  h.emit('delta',{text:'Private timing must not leak',channel:'analysis',observed_at:seconds+11});
  h.emit('commentary',{text:'Recorded progress',observed_at:seconds+12});
  checkTime(h.doc.querySelector('.live-message .activity-summary'),new Date((seconds+12)*1000).toISOString());
  h.emit('done',{output:'Final answer',updated_at:seconds+20});
  const finalISO=new Date((seconds+20)*1000).toISOString();assert.doesNotMatch(checkTime(h.doc.querySelector('.live-message'),finalISO).textContent,/Started/);
  assert.doesNotMatch(h.doc.querySelector('.messages').textContent,/Private timing/);
  snapshot.run=null;snapshot.items=[{role:'user',content:run.input,timestamp:seconds},{role:'assistant',content:'Final answer',timestamp:seconds+20}];
  h.doc.querySelector('[aria-label="Back to chats"]').click();await tick();await h.open();checkTime(h.doc.querySelector('.user-message'),iso);checkTime(h.doc.querySelector('.assistant-message'),finalISO);
 }finally{h.close();}
});
test('terminal without recorded completion removes Started and never claims creation as sent time',async()=>{
 for(const updated_at of [undefined,null,'bad']){
  const h=await setup({items:[],run:{id:'r',session_id:'s',status:'running',input:'Question',created_at:seconds}});
  try{assert.match(checkTime(h.doc.querySelector('.live-message'),iso).textContent,/Started/);h.emit('done',{output:'No completion evidence',updated_at});assert.equal(h.doc.querySelector('.live-message > .message-author time'),null);}finally{h.close();}
 }
});
test('public history segments, runtime notice and delegation use their own recorded times',async()=>{
 const items=[{role:'assistant',content:'Final',timestamp:seconds+100,public_commentary:[{id:'public',content:'Sidecar progress',timestamp:seconds}]},{role:'assistant',channel:'commentary',content:'Segment progress',timestamp:seconds},{role:'tool',kind:'runtime_notice',name:'Tool limit reached',status:'completed',content:'Limit reached',timestamp:seconds},{role:'tool',kind:'delegation',content:'Child report',timestamp:seconds},{role:'tool',name:'terminal',content:'Tool result',timestamp:seconds,duration_ms:1000}];
 const h=await setup({items,run:null});try{for(const selector of ['.activity-summary','.runtime-notice','.delegation-result'])for(const node of h.doc.querySelectorAll(selector))checkTime(node.querySelector('summary'),iso);assert.equal(h.doc.querySelectorAll('.tool-activity time').length,0,'tool duration contract unchanged');}finally{h.close();}
});
test('background event times are not replaced by ingestion and unknown event time remains unknown',async()=>{
 const background=[{id:'b',session_id:'s',kind:'background_result',source_event_id:'event',agent_context_state:'not_injected',title:'Report',body:'Result',created_at:seconds+50,event_at:seconds},{id:'unknown',session_id:'s',kind:'background_result',source_event_id:'unknown',agent_context_state:'not_injected',title:'Unknown',body:'No timestamp',created_at:seconds+50,event_at:null}];
 const h=await setup({items:[],run:null},{background});try{const card=h.doc.querySelector('[data-background-id="b"]');checkTime(card.querySelector('summary'),iso);card.open=true;h.win.dispatchEvent(new h.win.Event('focus'));await tick();assert.equal(h.doc.querySelector('[data-background-id="b"]'),card);assert.equal(card.open,true);checkTime(card.querySelector('summary'),iso);assert.equal(h.doc.querySelector('[data-background-id="unknown"] time'),null);}finally{h.close();}
});
test('nested runtime reminders render once after the owning user, not as authored chat or extra raw rows',async()=>{
 const reminder={id:'runtime-reminder:12',role:'tool',kind:'context_compression',name:'Runtime reminders',status:'completed',content:'Generated task reminder',timestamp:seconds,turn_boundary:false};
 const h=await setup({items:[{id:12,role:'user',content:'Only question',run_id:'r',timestamp:seconds,runtime_reminders:[reminder,reminder]}],run:{id:'r',session_id:'s',status:'running',created_at:seconds,runtime_reminders:[reminder]}});
 try{const cards=h.doc.querySelectorAll('.context-compression');assert.equal(cards.length,1);const card=cards[0];assert.equal(card.previousElementSibling.textContent.includes('Only question'),true);assert.equal(card.dataset.historyRun,'r');assert.equal(card.dataset.historySession,'s');assert.equal(card.dataset.historyId,reminder.id);assert.equal(card.dataset.turnStart,undefined);assert.equal(card.querySelector('.message-author'),null);assert.match(card.querySelector('summary').textContent,/Runtime reminders/);checkTime(card,iso);assert.equal(card.open,false);}finally{h.close();}
});
test('a native user ID is not invented as a run identity on its reminder',async()=>{
 const h=await setup({items:[{id:12,role:'user',content:'Question',runtime_reminders:[{id:'runtime-reminder:12',role:'tool',kind:'context_compression',content:'Reminder',timestamp:seconds}]}],run:null});
 try{assert.equal(h.doc.querySelector('.context-compression').dataset.historyRun,undefined);}finally{h.close();}
});
test('new submissions display the recorded journal created_at returned by the server',async()=>{
 const h=await setup({items:[],run:null},{submit:{id:'r',session_id:'s',status:'running',created_at:seconds}});
 try{const input=h.doc.querySelector('textarea');input.value='Submitted question';input.dispatchEvent(new h.win.Event('input'));h.doc.querySelector('[aria-label="Send message"]').click();await tick();checkTime(h.doc.querySelector('.user-message'),iso);}finally{h.close();}
});
test('active overlay can carry its own folded runtime reminders',async()=>{
 const reminder={id:'runtime-reminder:run',role:'tool',kind:'context_compression',name:'Runtime reminders',status:'completed',content:'Run task reminder',timestamp:seconds};
 const h=await setup({items:[],run:{id:'r',session_id:'s',status:'running',created_at:seconds,input:'Active question',runtime_reminders:[reminder]}});
 try{const card=h.doc.querySelector('.context-compression');assert.ok(card);assert.match(card.previousElementSibling.textContent,/Active question/);checkTime(card,iso);}finally{h.close();}
});
export {setup,checkTime,iso,seconds};
