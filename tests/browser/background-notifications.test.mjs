import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {renderMarkdown} from '../../frontend/markdown.mjs';
import {mountApp} from '../../frontend/ui.mjs';
import {assertPresenceBody} from './push-fixture-contract.mjs';
// Unit API boundary has no HTTP headers: validate the actual body, never invent CSRF.
const withoutUnitPresence=calls=>calls.filter(call=>{
 if(call.path!=='/push/presence' || call.options.method!=='POST')return true;
 assertPresenceBody(call.options.body);return false;
});
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(resolve=>setTimeout(resolve,10));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const item=(id='receipt-1',extra={})=>({id,session_id:'s1',title:'Completed delegation',body:'**Child A**\nReport A\n\n**Child B**\nReport B',created_at:1700000000,kind:'background_result',source_event_id:'async:fanout-1',agent_context_state:'not_injected',...extra});
function click(doc,label){const node=[...doc.querySelectorAll('button')].find(x=>x.getAttribute('aria-label')===label || x.textContent===label);assert.ok(node,label);node.click();}
async function setup(override=()=>undefined,snapshot={items:[{role:'user',content:'Original question'},{role:'assistant',content:'Original answer'}],run:null}){
 const dom=new JSDOM('<div id="app"></div>',{url:'https://example.com/hermes/',pretendToBeVisual:true}),win=dom.window,doc=win.document,calls=[],timers=new Map(),streams=[];let timerId=0;
 win.setTimeout=(fn,delay)=>{timers.set(++timerId,{fn,delay});return timerId;};win.clearTimeout=id=>timers.delete(id);
 class Events{constructor(){this.listeners={};streams.push(this);}addEventListener(n,f){this.listeners[n]=f;}emit(n,d,id){this.listeners[n]?.({data:JSON.stringify(d),lastEventId:id});}close(){this.closed=true;}}
 win.EventSource=Events;
 const app=await mountApp(doc,{clear(){},async request(path,options={}){calls.push({path,options});if(path==='/push/presence' && options.method==='POST'){assertPresenceBody(options.body);return {ok:true};}if(path==='/push/preferences' && (!options.method || options.method==='GET'))return {revision:0,enabled:true,categories:{completion:true,approval:true,attention:true,scheduled:true,operational:true},hide_details:false};const result=await override(path,options);if(result!==undefined)return result;if(path==='/auth/me')return {user:{id:'owner',profile:'default',status:'ready'}};if(path.startsWith('/sessions?'))return {items:[{id:'s1',title:'First conversation'},{id:'s2',title:'Second conversation'}],total:2};if(path.includes('/messages'))return snapshot;return {items:[]};}},win);
 return {doc,win,app,calls,timers,streams,async open(label='First conversation'){click(doc,label);await tick();},async event(name){win.dispatchEvent(new win.Event(name));await tick();},async poll(){const entry=[...timers].find(([,value])=>value.delay===30000);assert.ok(entry,'bounded conversation timer');timers.delete(entry[0]);entry[1].fn();await tick();},close(){app.destroy();win.close();}};
}
test('background placement prefers the originating turn over late completion time and preserves complete native groups',async()=>{
 let items=[item('origin',{origin_run_id:'r1',origin_session_id:'s1',origin_message_id:1,origin_anchor:{session_id:'s1',message_id:0},event_at:999,event_at_source:'completed_at'})];
 const snapshot={items:[{id:1,role:'user',content:'First request',timestamp:100},{id:2,role:'assistant',content:'Working',timestamp:110,tool_calls:[{id:'t',function:{name:'terminal'}}]},{id:3,role:'tool',tool_call_id:'t',name:'terminal',content:'Done',timestamp:120},{id:4,role:'assistant',content:'First answer',timestamp:130},{id:5,role:'user',content:'Later request',timestamp:200},{id:6,role:'assistant',content:'Later answer',timestamp:210}],run:null};
 const h=await setup(p=>p.endsWith('/background')?{items}:undefined,snapshot);
 try{await h.open();const messages=h.doc.querySelector('.messages'),card=messages.querySelector('[data-background-id]'),later=[...messages.querySelectorAll('.user-message')][1];assert.equal(card.nextElementSibling,later,'exact origin must beat completed_at and ingestion time');assert.match(card.previousElementSibling.textContent,/First answer/);assert.equal(messages.querySelectorAll('.tool-activity').length,1);card.open=true;const nodes=[...messages.querySelectorAll('.message,.tool-activity')];items.push(item('late',{...items[0],id:'late'}));await h.event('focus');assert.equal(card.open,true);assert.deepEqual([...messages.querySelectorAll('.message,.tool-activity')],nodes);assert.equal(messages.querySelector('[data-background-id="late"]').nextElementSibling,later);}finally{h.close();}
});

test('background receipt stays before newer native progress and tools within its exact turn on refresh and reload',async()=>{
 const bg=item('within',{origin_session_id:'s1',origin_message_id:1,event_at:115,created_at:999});
 const snapshot={items:[{id:1,role:'user',content:'Request',timestamp:100},{id:2,role:'assistant',channel:'commentary',content:'Earlier progress',timestamp:110},{id:3,role:'assistant',channel:'commentary',content:'Later progress',timestamp:120},{id:4,role:'tool',name:'terminal',content:'Done',timestamp:125},{id:5,role:'assistant',content:'Final answer',timestamp:130},{id:6,role:'user',content:'Next request',timestamp:200}],run:null};
 const h=await setup(p=>p.endsWith('/background')?{items:[bg]}:undefined,snapshot);
 try{
  await h.open();const card=h.doc.querySelector('[data-background-id]'),later=[...h.doc.querySelectorAll('.activity-summary')].find(n=>n.textContent.includes('Later progress'));
  assert.equal(card.nextElementSibling,later,'receipt belongs at its event boundary, not the turn footer');
  card.open=true;const native=[...h.doc.querySelectorAll('.message,.activity-summary,.tool-activity')],text=card.querySelector('.markdown strong').firstChild;h.win.getSelection().setBaseAndExtent(text,0,text,text.length);
  await h.event('focus');assert.equal(h.doc.querySelector('[data-background-id]'),card);assert.equal(card.nextElementSibling,later);assert.equal(card.open,true);assert.equal(h.win.getSelection().toString(),'Child A');assert.deepEqual([...h.doc.querySelectorAll('.message,.activity-summary,.tool-activity')],native);
  click(h.doc,'Chats');await tick();await h.open();assert.match(h.doc.querySelector('[data-background-id]').nextElementSibling.textContent,/Later progress/);
 }finally{h.close();}
});

test('background receipt splits a native tool group at the event boundary without replacing tool rows',async()=>{
 let items=[];
 const snapshot={items:[{id:1,role:'user',content:'Request',timestamp:100},{id:2,role:'tool',name:'first_tool',content:'First done',timestamp:110},{id:3,role:'tool',name:'second_tool',content:'Second done',timestamp:120},{id:4,role:'assistant',content:'Final answer',timestamp:130}],run:null};
 const h=await setup(p=>p.endsWith('/background')?{items}:undefined,snapshot);
 try{
  await h.open();const group=h.doc.querySelector('.tool-activity'),rows=[...group.querySelectorAll('.tool-preview')];group.open=true;
  items=[item('split',{origin_session_id:'s1',origin_message_id:1,event_at:115})];await h.event('focus');
  const card=h.doc.querySelector('[data-background-id]');assert.equal(card.previousElementSibling,group);assert.equal(card.nextElementSibling?.querySelector('.tool-name')?.textContent,'second_tool','later tools belong after the receipt, not in the earlier folded group');
  assert.deepEqual([...h.doc.querySelectorAll('.tool-preview')],rows);assert.equal(group.open,true);assert.equal(card.nextElementSibling.open,true);assert.equal(group.querySelectorAll('.tool-preview').length,1);
  card.open=true;await h.event('focus');assert.equal(h.doc.querySelectorAll('.tool-activity').length,2);assert.equal(h.doc.querySelector('[data-background-id]'),card);assert.equal(card.open,true);
  click(h.doc,'Chats');await tick();await h.open();assert.equal(h.doc.querySelector('[data-background-id]').nextElementSibling?.querySelector('.tool-name')?.textContent,'second_tool');
 }finally{h.close();}
});

test('background late arrivals sharing a native boundary follow event time rather than ingestion order',async()=>{
 const items=[item('later-event',{origin_session_id:'s1',origin_message_id:1,event_at:118,created_at:300}),item('earlier-event',{origin_session_id:'s1',origin_message_id:1,event_at:112,created_at:400})];
 const h=await setup(p=>p.endsWith('/background')?{items}:undefined,{items:[{id:1,role:'user',content:'Request',timestamp:100},{id:2,role:'tool',name:'earlier_tool',timestamp:110},{id:3,role:'tool',name:'later_tool',timestamp:120}],run:null});
 try{await h.open();assert.deepEqual([...h.doc.querySelectorAll('[data-background-id]')].map(n=>n.dataset.backgroundId),['earlier-event','later-event']);const cards=[...h.doc.querySelectorAll('[data-background-id]')];cards[0].open=true;await h.event('focus');assert.deepEqual([...h.doc.querySelectorAll('[data-background-id]')],cards);assert.equal(cards[0].open,true);assert.equal(h.doc.querySelectorAll('.tool-activity').length,2);}finally{h.close();}
});

test('background placement uses a projected commentary segment own timestamp and identity, never its carrier timestamp',async()=>{
 const bg=item('sidecar',{origin_session_id:'s1',origin_message_id:1,event_at:115});
 const h=await setup(p=>p.endsWith('/background')?{items:[bg]}:undefined,{items:[{id:1,role:'user',content:'Request',timestamp:100},{id:2,role:'assistant',content:'Final answer',timestamp:130,public_commentary:[{id:'native:2:commentary:0',content:'Later projected progress',timestamp:120}]}],run:null});
 try{await h.open();const card=h.doc.querySelector('[data-background-id]');assert.match(card.nextElementSibling.textContent,/Later projected progress/);assert.equal(card.nextElementSibling.dataset.historyId,'native:2:commentary:0');assert.equal(card.nextElementSibling.dataset.historyTime,'120');}finally{h.close();}
});

test('background placement pagination waits for the exact unloaded turn and reuses its open card',async()=>{
 const bg=item('paged',{origin_run_id:'r1',origin_session_id:'s1',origin_message_id:1,origin_anchor:{session_id:'s1',message_id:0},event_at:999,event_at_source:'completed_at'});
 const h=await setup(p=>p.endsWith('/background')?{items:[bg]}:p.includes('/messages?') && p.includes('offset=0')?{items:[{id:1,role:'user',content:'Old request',timestamp:100},{id:2,role:'assistant',content:'Old answer',timestamp:110}],offset:0}:undefined,{items:[{id:3,role:'user',content:'New request',timestamp:200},{id:4,role:'assistant',content:'New answer',timestamp:210}],offset:2,run:null});
 try{await h.open();const card=h.doc.querySelector('[data-background-id]'),newTurn=h.doc.querySelector('.user-message');assert.equal(card.dataset.placement,'pending');assert.equal(card.nextElementSibling,newTurn);assert.match(card.textContent,/Originating turn is not loaded/);card.open=true;click(h.doc,'Load older messages');await tick();assert.equal(h.doc.querySelector('[data-background-id]'),card);assert.equal(card.open,true);assert.equal(card.dataset.placement,'exact');assert.equal(card.nextElementSibling,newTurn);assert.match(card.previousElementSibling.textContent,/Old answer/);assert.equal(card.querySelector('.background-placement').hidden,true);assert.deepEqual([...h.doc.querySelectorAll('.user-message .markdown')].map(n=>n.textContent),['Old request','New request']);}finally{h.close();}
});

test('background placement never treats another session run identity as the origin',async()=>{
 const bg=item('foreign-run',{origin_run_id:'shared-run',origin_session_id:'s2',origin_message_id:99,event_at:115});
 const h=await setup(p=>p.endsWith('/background')?{items:[bg]}:undefined,{items:[{id:1,session_id:'s1',run_id:'shared-run',role:'user',content:'Unrelated request',timestamp:100},{id:2,role:'assistant',content:'Unrelated answer',timestamp:120}],run:null});
 try{await h.open();const card=h.doc.querySelector('[data-background-id]');assert.equal(card.dataset.placement,'pending');assert.equal(card.nextElementSibling,h.doc.querySelector('.user-message'));}finally{h.close();}
});

for(const scenario of ['legacy receipt','missing event','untimed native','old journal','untimed live'])test(`background exact ownership labels ${scenario} chronology approximate`,async()=>{
 const live=scenario==='untimed live';
 const bg=item('approx',{origin_run_id:live?'r1':undefined,origin_session_id:'s1',origin_message_id:live?null:1,...(scenario==='legacy receipt'?{created_at:115}:{event_at:scenario==='missing event'?null:115})});
 const snapshot=live?{items:[],run:{id:'r1',session_id:'s1',status:'running',input:'Active question'},tool_replay:{run_id:'r1',cursor:1,events:[{id:1,name:'commentary',data:{text:'Legacy progress'}}]}}:{items:[{id:1,role:'user',content:'Request',timestamp:100},{id:2,role:'assistant',content:'Answer',timestamp:scenario==='untimed native'?undefined:scenario==='old journal'?100:120,...(scenario==='old journal'?{source:'journal'}:{})}],run:null};
 const h=await setup(p=>p.endsWith('/background')?{items:[bg]}:undefined,snapshot);
 try{await h.open();const card=h.doc.querySelector('[data-background-id]');assert.equal(card.dataset.placement,'exact','ownership remains exact, timing does not');assert.match(card.querySelector('.background-placement').textContent,/approximate/i);assert.equal(card.querySelector('.background-placement').hidden,false);if(scenario==='legacy receipt')assert.match(card.textContent,/receipt time/);if(live)assert.equal(card.parentElement,h.doc.querySelector('.live-message'));}finally{h.close();}
});

test('background placement ambiguous origin label does not claim the turn is merely unloaded',async()=>{
 const bg=item('ambiguous',{origin_run_id:'r1',origin_session_id:'s1',origin_message_id:null,origin_anchor:{session_id:'s1',message_id:0},event_at:null,created_at:100});
 const h=await setup(p=>p.endsWith('/background')?{items:[bg]}:undefined,{items:[{id:1,role:'user',content:'Unrelated native request',timestamp:100},{id:2,role:'assistant',content:'Native answer',timestamp:110}],run:null});
 try{await h.open();const card=h.doc.querySelector('[data-background-id]');assert.equal(card.dataset.placement,'pending');assert.equal(card.nextElementSibling,h.doc.querySelector('.user-message'));assert.match(card.textContent,/Originating turn could not be identified in this view/);assert.doesNotMatch(card.textContent,/not loaded/);}finally{h.close();}
});

test('background placement unproved admission uses event time while journal identity still wins',async()=>{
 const origin={origin_run_id:'absent',origin_session_id:'s1',origin_message_id:null,origin_anchor:{session_id:'s1',message_id:0}};
 const items=[item('between',{...origin,event_at:150,created_at:999}),item('before',{...origin,event_at:50,created_at:999}),item('journal',{...origin,origin_run_id:'r2',event_at:150})];
 const h=await setup(p=>p.endsWith('/background')?{items}:undefined,{items:[{id:1,role:'user',content:'First request',timestamp:100},{id:2,role:'assistant',content:'First answer',timestamp:110},{id:3,role:'user',content:'Second request',timestamp:200,run_id:'r2'},{id:4,role:'assistant',content:'Second answer',timestamp:210,run_id:'r2'}],run:null});
 try{await h.open();const between=h.doc.querySelector('[data-background-id="between"]'),before=h.doc.querySelector('[data-background-id="before"]'),journal=h.doc.querySelector('[data-background-id="journal"]'),turns=[...h.doc.querySelectorAll('.user-message')];assert.equal(between.dataset.placement,'time');assert.equal(between.nextElementSibling,turns[1]);assert.match(between.previousElementSibling.textContent,/First answer/);assert.match(between.textContent,/Placed by event time; exact originating turn is unavailable/);assert.doesNotMatch(between.textContent,/receipt time|not loaded/);assert.equal(before.nextElementSibling,turns[0]);assert.equal(before.dataset.placement,'pending');assert.match(before.textContent,/predates the loaded turns/);assert.equal(journal.dataset.placement,'exact');assert.match(journal.previousElementSibling.textContent,/Second answer/);}finally{h.close();}
});

test('background placement timestamp fallback keeps complete turns and distinguishes legacy receipt time from unknown replay time',async()=>{
 const items=[item('event',{event_at:150,event_at_source:'dispatched_at'}),item('legacy',{created_at:150}),item('unknown',{event_at:null,created_at:150}),item('before',{event_at:50})];
 const h=await setup(p=>p.endsWith('/background')?{items}:undefined,{items:[{id:1,role:'user',content:'First request',timestamp:100},{id:2,role:'assistant',content:'First answer after later clock',timestamp:999},{id:3,role:'user',content:'Second request',timestamp:200},{id:4,role:'assistant',content:'Second answer',timestamp:210}],run:null});
 try{await h.open();const cards=[...h.doc.querySelectorAll('[data-background-id]')],second=[...h.doc.querySelectorAll('.user-message')][1];assert.deepEqual(cards.map(n=>n.dataset.backgroundId),['before','event','legacy','unknown']);assert.equal(h.doc.querySelector('[data-background-id="legacy"]').nextElementSibling,second);assert.match(h.doc.querySelector('[data-background-id="event"]').previousElementSibling.textContent,/First answer/);assert.match(h.doc.querySelector('[data-background-id="legacy"]').textContent,/receipt time/);assert.match(h.doc.querySelector('[data-background-id="unknown"]').textContent,/Originating turn unavailable/);}finally{h.close();}
});

test('background reports start folded and keep their disclosure and full body across refresh',async()=>{
 let items=[item()];const h=await setup(p=>p.endsWith('/background')?{items}:undefined);
 try{await h.open();const card=h.doc.querySelector('[data-background-id]');assert.equal(card.tagName,'DETAILS');assert.equal(card.open,false);assert.match(card.querySelector('summary').textContent,/Background result.*Completed delegation/);assert.match(card.querySelector('.markdown').textContent,/Report A.*Report B/s);card.open=true;items.push(item('second'));await h.event('focus');assert.equal(h.doc.querySelector('[data-background-id]'),card);assert.equal(card.open,true);assert.equal(h.doc.querySelector('[data-background-id="second"]').open,false);assert.equal(withoutUnitPresence(h.calls).some(c=>c.options.method && c.options.method!=='GET'),false);}finally{h.close();}
});
test('existing watch refreshes receipts on focus, reconnect, visibility and bounded ticks without replacing draft or cards',async()=>{
 let items=[];const h=await setup(p=>p.endsWith('/background')?{items}:undefined);
 try{
  await h.open();const textarea=h.doc.querySelector('textarea'),messages=h.doc.querySelector('.messages'),native=h.doc.querySelector('.assistant-message');textarea.value='Unsent draft';textarea.dispatchEvent(new h.win.Event('input'));textarea.focus();textarea.setSelectionRange(2,7);
  Object.defineProperties(messages,{scrollHeight:{configurable:true,value:3000},clientHeight:{configurable:true,value:500}});messages.scrollTop=180;
  items=[item()];await h.event('focus');assert.equal(h.doc.querySelectorAll('[data-background-id]').length,1);const card=h.doc.querySelector('[data-background-id]');assert.equal(messages.scrollTop,180,'older reader stays put');
  await h.event('focus');assert.equal(h.doc.querySelector('[data-background-id]'),card);assert.equal(h.doc.querySelector('textarea'),textarea);assert.equal(h.doc.activeElement,textarea);assert.deepEqual([textarea.selectionStart,textarea.selectionEnd],[2,7]);assert.equal(textarea.value,'Unsent draft');assert.equal(h.doc.querySelector('.assistant-message'),native);
  items.push(item('receipt-2'));await h.poll();assert.equal(h.doc.querySelectorAll('[data-background-id]').length,2);
  await h.event('offline');items.push(item('receipt-3'));await h.event('online');assert.equal(h.doc.querySelectorAll('[data-background-id]').length,3);
  items.push(item('receipt-4'));h.doc.dispatchEvent(new h.win.Event('visibilitychange'));await tick();assert.equal(h.doc.querySelectorAll('[data-background-id]').length,4);
  assert.equal([...h.timers.values()].filter(t=>t.delay===30000).length,1);assert.equal(withoutUnitPresence(h.calls).filter(c=>c.options.method && c.options.method!=='GET').length,0);
  click(h.doc,'Chats');await tick();const reads=h.calls.filter(c=>c.path.endsWith('/background')).length;assert.equal(h.timers.size,0);await h.event('focus');await h.event('online');assert.equal(h.calls.filter(c=>c.path.endsWith('/background')).length,reads);
 }finally{h.close();}
});
test('count-saturated snapshots show newest receipts chronologically and disclose omitted older results on late focus',async()=>{
 let items=Array.from({length:200},(_,i)=>item('count-'+i,{body:'Report '+i})),pending=null;
 const h=await setup(p=>p.endsWith('/background')?(pending?pending.promise:{items}):undefined);
 try{
  await h.open();assert.equal(h.doc.querySelectorAll('[data-background-id]').length,200);assert.equal(h.doc.querySelector('.background-omitted'),null);
  const textarea=h.doc.querySelector('textarea'),retained=h.doc.querySelector('[data-background-id="count-100"]'),native=h.doc.querySelector('.assistant-message');
  textarea.value='Keep saturated draft';textarea.dispatchEvent(new h.win.Event('input'));textarea.focus();textarea.setSelectionRange(2,8);
  pending=deferred();await h.event('focus');await h.event('focus');assert.equal(h.calls.filter(c=>c.path.endsWith('/background')).length,2,'late focus shares the outstanding read');
  items.push(item('count-200',{body:'Newest completion'}));pending.resolve({items});await tick();pending=null;
  assert.ok(h.doc.querySelector('[data-background-id="count-200"]'),'latest completion survives count saturation');
  assert.deepEqual([...h.doc.querySelectorAll('[data-background-id]')].map(c=>c.dataset.backgroundId),items.slice(1).map(i=>i.id),'newest bounded window retains chronological display');
  const note=h.doc.querySelector('.background-omitted');assert.ok(note && !note.hidden);assert.match(note.textContent,/older background results.*omitted.*Full reports remain stored with this conversation/i);
  assert.equal(h.doc.querySelector('[data-background-id="count-100"]'),retained);assert.equal(h.doc.querySelector('.assistant-message'),native);assert.equal(h.doc.querySelector('textarea'),textarea);assert.equal(h.doc.activeElement,textarea);assert.equal(textarea.value,'Keep saturated draft');assert.deepEqual([textarea.selectionStart,textarea.selectionEnd],[2,8]);
  await h.event('focus');assert.equal(h.doc.querySelectorAll('.background-omitted').length,1);assert.equal(h.doc.querySelector('[data-background-id="count-100"]'),retained);assert.equal([...h.timers.values()].filter(t=>t.delay===30000).length,1);assert.equal(withoutUnitPresence(h.calls).filter(c=>c.options.method && c.options.method!=='GET').length,0);
  items=[];await h.event('focus');assert.equal(h.doc.querySelectorAll('[data-background-id],.background-omitted').length,0,'reset also clears omission note');
 }finally{h.close();}
});
test('body-saturated snapshots keep newest complete reports and fill remaining capacity with eligible older reports',async()=>{
 const large='L'.repeat(262144),small=item('small-old',{body:'Older small report'});
 let items=[small,...Array.from({length:8},(_,i)=>item('body-'+i,{body:large}))];
 const h=await setup(p=>p.endsWith('/background')?{items}:undefined);
 try{
  await h.open();assert.equal(h.doc.querySelectorAll('[data-background-id]').length,8);assert.equal(h.doc.querySelector('[data-background-id="small-old"]'),null);
  const retained=h.doc.querySelector('[data-background-id="body-7"]');
  items.push(item('new-small',{body:'Newest small report'}));await h.event('focus');
  assert.ok(h.doc.querySelector('[data-background-id="new-small"]'),'new completion survives the body budget');
  assert.ok(h.doc.querySelector('[data-background-id="small-old"]'),'skip an older report that cannot fit, not all remaining eligible reports');
  assert.deepEqual([...h.doc.querySelectorAll('[data-background-id]')].map(c=>c.dataset.backgroundId),['small-old',...items.slice(2).map(i=>i.id)],'reappearing older reports stay chronological');
  assert.equal(h.doc.querySelector('[data-background-id="body-7"]'),retained);
  for(const card of h.doc.querySelectorAll('[data-background-id]'))assert.equal(card.querySelector('.markdown').textContent,items.find(i=>i.id===card.dataset.backgroundId).body,'individual reports are never truncated');
  const bodies=[...h.doc.querySelectorAll('[data-background-id] .markdown')];assert.ok(bodies.reduce((sum,b)=>sum+b.textContent.length,0)<=2097152);assert.match(h.doc.querySelector('.background-omitted').textContent,/older background results.*omitted.*Full reports remain stored with this conversation/i);
  items.push(item('new-small-2',{body:'Another new report'}));await h.event('focus');assert.ok(h.doc.querySelector('[data-background-id="new-small-2"]'));assert.equal(h.doc.querySelector('[data-background-id="body-7"]'),retained);
 }finally{h.close();}
});
test('oversized reports are disclosed rather than silently dropped or truncated, without blocking newer eligible results',async()=>{
 const oversized=item('too-large',{body:'X'.repeat(262145)}),atLimit=item('at-limit',{body:'Y'.repeat(262144)});
 let items=[oversized];const h=await setup(p=>p.endsWith('/background')?{items}:undefined,{items:[],run:null});
 try{
  await h.open();assert.equal(h.doc.querySelectorAll('[data-background-id]').length,0);
  assert.match(h.doc.querySelector('.background-omitted')?.textContent||'',/reports.*too large.*Full reports remain stored with this conversation/i,'oversized-only snapshots must not pretend there are no results');
  assert.equal(h.doc.querySelector('.messages .empty'),null);
  items=[atLimit,oversized,item('after-large',{body:'Newest complete result'})];await h.event('focus');
  assert.deepEqual([...h.doc.querySelectorAll('[data-background-id]')].map(c=>c.dataset.backgroundId),['at-limit','after-large']);assert.equal(h.doc.querySelector('[data-background-id="at-limit"] .markdown').textContent,atLimit.body);assert.equal(h.doc.querySelector('[data-background-id="after-large"] .markdown').textContent,'Newest complete result');
  assert.match(h.doc.querySelector('.background-omitted').textContent,/reports.*too large.*Full reports remain stored with this conversation/i);assert.doesNotMatch(h.doc.querySelector('.background-omitted').textContent,/older background results/i,'do not call an oversized newer report an omitted older result');
  items=[atLimit,item('after-large',{body:'Newest complete result'})];await h.event('focus');assert.equal(h.doc.querySelector('.background-omitted'),null);
 }finally{h.close();}
});
test('saturation eviction preserves the visible surviving older-reader anchor, not just scrollTop',async()=>{
 let items=Array.from({length:200},(_,i)=>item('anchor-'+i,{body:'Report '+i}));
 const h=await setup(p=>p.endsWith('/background')?{items}:undefined);
 try{
  await h.open();const messages=h.doc.querySelector('.messages'),anchor=h.doc.querySelector('[data-background-id="anchor-100"]');
  Object.defineProperties(messages,{scrollHeight:{configurable:true,get:()=>messages.children.length*120},clientHeight:{configurable:true,value:500}});
  messages.getBoundingClientRect=()=>({top:50,bottom:550});
  for(const child of messages.children)child.getBoundingClientRect=()=>{const top=50+[...messages.children].indexOf(child)*120-messages.scrollTop;return {top,bottom:top+120};};
  messages.scrollTop=[...messages.children].indexOf(anchor)*120+13;
  const before=anchor.getBoundingClientRect().top,top=messages.scrollTop;
  items.push(item('anchor-200',{body:'Latest report'}));await h.event('focus');
  assert.equal(h.doc.querySelector('[data-background-id="anchor-100"]'),anchor);assert.equal(anchor.getBoundingClientRect().top,before,'evicting an earlier receipt must not displace the visible report');assert.equal(messages.scrollTop,top-120);
  await h.event('focus');assert.equal(anchor.getBoundingClientRect().top,before,'unchanged snapshots do not drift the reader');
 }finally{h.close();}
});
for(const transition of ['reset','delete'])test(`late saturated response cannot revive cards or omission notice after ${transition}`,async()=>{
 const saturated=Array.from({length:201},(_,i)=>item('late-'+i));let pending=null,items=saturated;
 const h=await setup((p,options)=>{
  if(p.endsWith('/background'))return pending?pending.promise:{items};
  if(p.startsWith('/sessions?'))return {deletion_available:true,items:[{id:'s1',title:'First conversation'}],total:1};
  if(p==='/sessions/s1' && options.method==='DELETE')return {id:'s1',deleted:true};
 });
 try{
  await h.open();assert.ok(h.doc.querySelector('.background-omitted'));pending=deferred();const stale=pending;await h.event('focus');
  const oldMessages=h.doc.querySelector('.messages'),oldHTML=oldMessages.innerHTML,signal=h.calls.filter(c=>c.path.endsWith('/background')).at(-1).options.signal;
  click(h.doc,'Chats');await tick();assert.equal(signal.aborted,true);items=[];pending=null;
  if(transition==='delete'){click(h.doc,'Conversation actions: First conversation');click(h.doc,'Delete conversation: First conversation');await tick();click(h.doc,'Delete conversation');await tick();assert.equal(h.app.state.session,null);}
  else await h.open();
  stale.resolve({items:saturated});await tick();assert.equal(h.doc.querySelectorAll('[data-background-id],.background-omitted').length,0);assert.equal(oldMessages.innerHTML,oldHTML,'detached transcript is not changed by stale callbacks');
  if(transition==='reset'){await h.event('focus');assert.equal(h.doc.querySelectorAll('[data-background-id],.background-omitted').length,0);}
  else assert.equal(h.timers.size,0);
 }finally{h.close();}
});
for(const status of [403,404,410])test(`removed saturated target ${status} clears cards and omission notice together`,async()=>{
 let statusCode=0;const h=await setup(p=>{if(!p.endsWith('/background'))return;if(statusCode)throw Object.assign(new Error('Gone'),{status:statusCode});return {items:Array.from({length:201},(_,i)=>item('gone-'+i))};});
 try{await h.open();assert.ok(h.doc.querySelector('.background-omitted'));statusCode=status;await h.event('focus');assert.equal(h.doc.querySelectorAll('[data-background-id],.background-omitted').length,0);}finally{h.close();}
});
test('untrusted receipt payloads are validated, bounded and rendered only through safe Markdown',async()=>{
 const hostile='<img src=x onerror=alert(1)> [bad](javascript:alert) [safe](https://example.com/report)';
 const invalid=[null,{},item('foreign',{session_id:'s2'}),item('kind',{kind:'user'}),item('context',{agent_context_state:'injected'}),item('object',{body:{private:'hidden'}}),item('date',{created_at:Infinity}),item('long-id',{id:'x'.repeat(1025)}),item('large',{body:'X'.repeat(262145)})];
 // Keep the hostile valid report at the start of the newest 200 so every original safety assertion still exercises a rendered card.
 let items=[...Array.from({length:41},(_,i)=>item('older-'+i,{body:'Short report'})),item('safe',{body:hostile,private_context:'MUST NOT DISPLAY'}),...Array.from({length:199},(_,i)=>item('bounded-'+i,{body:'Short report'})),...invalid];
 const h=await setup(p=>p.endsWith('/background')?{items}:undefined);
 try{await h.open();const cards=[...h.doc.querySelectorAll('[data-background-id]')];assert.ok(cards.length>0 && cards.length<=200,'bounded card count');assert.equal(cards[0].dataset.backgroundId,'safe');assert.equal(cards[0].querySelector('img,script,iframe'),null);assert.equal(cards[0].querySelectorAll('a').length,1);assert.equal(cards[0].querySelector('a').href,'https://example.com/report');assert.ok(cards[0].textContent.includes('<img'));assert.ok(!h.doc.body.textContent.includes('MUST NOT DISPLAY'));items=Array.from({length:240},(_,i)=>item('new-'+i));await h.event('focus');assert.ok(h.doc.querySelectorAll('[data-background-id]').length<=200,'bounded accumulated card count');}finally{h.close();}
});
for(const transition of ['route','account','profile','destroy','abort'])test(`late background response is fenced after ${transition}`,async()=>{
 const pending=deferred();const h=await setup(p=>p.endsWith('/background')?pending.promise:undefined);
 try{await h.open();const messages=h.doc.querySelector('.messages'),request=h.calls.find(c=>c.path.endsWith('/background'));assert.ok(request.options.signal,'bounded abortable request');
  if(transition==='route'){click(h.doc,'Chats');await tick();await h.open('Second conversation');}
  if(transition==='account')h.app.state.user.id='different-user';
  if(transition==='profile')h.app.state.user.profile='different-profile';
  if(transition==='destroy')h.app.destroy();
  if(transition==='abort'){const entry=[...h.timers].find(([,v])=>v.delay===10000);assert.ok(entry);h.timers.delete(entry[0]);entry[1].fn();assert.equal(request.options.signal.aborted,true);}
  pending.resolve({items:[item()]});await tick();assert.equal(messages.querySelectorAll('[data-background-id]').length,0);assert.equal(h.doc.querySelectorAll('[data-background-id]').length,0);
 }finally{h.close();}
});
for(const status of [0,403,404,410])test(`fresh reset or removed target (${status}) removes receipts without reviving history`,async()=>{
 let reset=false;const h=await setup(p=>{if(!p.endsWith('/background'))return;if(!reset)return {items:[item()]};if(status)throw Object.assign(new Error('Gone'),{status});return {items:[]};});
 try{await h.open();assert.equal(h.doc.querySelectorAll('[data-background-id]').length,1);const textarea=h.doc.querySelector('textarea');reset=true;await h.event('focus');assert.equal(h.doc.querySelectorAll('[data-background-id]').length,0);assert.equal(h.doc.querySelector('textarea'),textarea);click(h.doc,'Chats');await tick();await h.open();assert.equal(h.doc.querySelectorAll('[data-background-id]').length,0);assert.equal(withoutUnitPresence(h.calls).filter(c=>c.options.method && c.options.method!=='GET').length,0);}finally{h.close();}
});
test('current background 401 expires authentication but stale 401 cannot sign out another route',async()=>{
 let failure=false;const pending=deferred();const h=await setup(p=>{if(p.endsWith('/background')){if(failure)throw Object.assign(new Error('Expired'),{status:401});return pending.promise;}});
 try{await h.open();click(h.doc,'Chats');await tick();pending.reject(Object.assign(new Error('Expired'),{status:401}));await tick();assert.ok(h.app.state.user);failure=true;await h.open();assert.equal(h.app.state.user,null);assert.match(h.doc.body.textContent,/Sign in/);assert.equal(h.timers.size,0);}finally{h.close();}
});
test('long trusted live chunks flush without argument spread and retain original text nodes',()=>{
 // Exercise the exact private production helper without replaying 150000 SSE
 // callbacks; full real-browser streaming/selection is covered in the spec.
 const source=readFileSync(new URL('../../frontend/ui.mjs',import.meta.url),'utf8');
 const start=source.indexOf('  function publicActivity(text,chunks) {'),end=source.indexOf('  function guidanceLabel(',start);
 const helperStart=source.indexOf('  const h = ('),helperEnd=source.indexOf('  const button = ',helperStart);
 assert.ok(start>=0 && end>start && helperStart>=0 && helperEnd>helperStart);
 const dom=new JSDOM('<div></div>'),win=dom.window,doc=win.document;
 try{
  const publicActivity=new Function('doc','win','renderMarkdown',source.slice(helperStart,helperEnd)+"const disclosure=()=>h('span');"+source.slice(start,end)+';return publicActivity;')(doc,win,renderMarkdown);
  const text='x'.repeat(150000),chunks=Array.from({length:150000},(_,i)=>({text:'x',observed_at:i,node:doc.createTextNode('x')}));
  const card=publicActivity(text,chunks);doc.body.append(card);
  const body=card.querySelector('.markdown');assert.equal(body.textContent,text);assert.equal(body.childNodes.length,150000);assert.equal(body.firstChild,chunks[0].node);assert.equal(body.lastChild,chunks.at(-1).node);assert.equal(chunks[0].node.isConnected,true);
 }finally{win.close();}
});

for(const times of [[110,null],[120,110]])test(`incomplete delta timing never claims exact chronology: ${JSON.stringify(times)}`,async()=>{
 const h=await setup(p=>p.endsWith('/background')?{items:[item('uncertain',{origin_session_id:'s1',origin_message_id:1,event_at:115})]}:undefined,{items:[{id:1,role:'user',content:'Ask',timestamp:100},{id:2,role:'assistant',channel:'commentary',content:'AB',observed_at:times[0],timed_chunks:times.map((observed_at,i)=>({text:i?'B':'A',observed_at}))}],run:null});
 try{await h.open();assert.equal(h.doc.querySelectorAll('.activity-summary').length,1);assert.match(h.doc.querySelector('.background-placement').textContent,/approximate/);}finally{h.close();}
});

test('an already known background boundary splits later SSE deltas only at the recorded crossing',async()=>{
 const h=await setup(p=>p.endsWith('/background')?{items:[item('known-gap',{origin_run_id:'r1',origin_session_id:'s1',event_at:115})]}:undefined,{items:[],run:{id:'r1',session_id:'s1',status:'running',input:'Ask',created_at:100},tool_replay:{run_id:'r1',cursor:0,events:[]}});
 try{await h.open();const stream=h.streams[0];for(let i=0;i<3;i++)stream.emit('delta',{text:'EARLIER ',observed_at:110+i},String(i+1));assert.equal(h.doc.querySelectorAll('.activity-summary').length,0,'same-side chunks are not new cards');assert.equal(h.doc.querySelector('[data-background-id]').previousElementSibling,h.doc.querySelector('.message-body'),'recorded earlier text remains before an already known receipt');stream.emit('delta',{text:'LATER',observed_at:120},'4');const card=h.doc.querySelector('[data-background-id]');assert.equal(card.previousElementSibling.querySelector('.markdown')?.textContent,'EARLIER EARLIER EARLIER ');assert.equal(card.nextElementSibling.querySelector('.markdown')?.textContent,'LATER');assert.equal(h.doc.querySelectorAll('.activity-summary').length,2);}finally{h.close();}
});

for(const final of ['Partial answer.', 'Authoritative replacement.'])test(`authoritative final replaces only the uncommitted tail: ${final}`,async()=>{
 const h=await setup(()=>undefined,{items:[],run:{id:'r1',session_id:'s1',status:'running',input:'Ask'}});
 try{
  await h.open();const stream=h.streams[0];
  stream.emit('delta',{text:'Committed progress',observed_at:110},'1');
  stream.emit('tool',{name:'read_file',tool_call_id:'t',status:'running',observed_at:120},'2');
  stream.emit('commentary',{text:'Explicit progress',observed_at:125},'3');
  stream.emit('delta',{text:'Partial ',observed_at:130},'4');
  stream.emit('delta',{text:'answer.',observed_at:131},'5');
  const committed=[...h.doc.querySelectorAll('.activity-summary')];
  stream.emit('tool',{name:'read_file',tool_call_id:'t',status:'completed',observed_at:135},'6');
  stream.emit('done',{status:'completed',output:final,updated_at:140},'7');
  assert.deepEqual([...h.doc.querySelectorAll('.activity-summary')],committed,'completion cannot promote a draft to Progress or replace committed disclosures');
  assert.deepEqual(committed.map(n=>n.querySelector('.markdown').textContent),['Committed progress','Explicit progress']);
  const output=h.doc.querySelector('.live-message .message-body');assert.equal(output.textContent,final);assert.equal(output.dataset.historyTime,'140');
  assert.equal(h.doc.querySelector('.tool-preview').dataset.historyTime,'120');
 }finally{h.close();}
});

for(const status of ['running','stopping'])test(`active ${status} snapshot preserves seeded delta text and timing`,async()=>{
 const run={id:'r1',session_id:'s1',status,input:'Ask',output:'',updated_at:140};
 const h=await setup(p=>p==='/runs/r1'?{...run,status:'stopping'}:undefined,{items:[],run,tool_replay:{run_id:'r1',cursor:1,events:[{id:1,name:'delta',observed_at:110,data:{text:'Seeded draft'}}]}});
 try{
  await h.open();const output=h.doc.querySelector('.live-message .message-body');
  assert.equal(output.textContent,'Seeded draft');assert.equal(output.dataset.historyTime,'110');
  assert.equal(h.doc.querySelectorAll('.activity-summary').length,0);
  await h.event('focus');assert.equal(output.textContent,'Seeded draft');assert.equal(output.dataset.historyTime,'110');
  h.streams[0].emit('delta',{text:' continued',observed_at:150},'2');
  h.streams[0].emit('tool',{name:'read_file',observed_at:160},'3');
  assert.equal(h.doc.querySelector('.activity-summary .markdown').textContent,'Seeded draft continued');
 }finally{h.close();}
});

for(const output of [null,undefined])test(`terminal without authoritative output retains its draft (${output})`,async()=>{
 const h=await setup(()=>undefined,{items:[],run:{id:'r1',session_id:'s1',status:'running',input:'Ask'}});
 try{await h.open();h.streams[0].emit('delta',{text:'Available partial',observed_at:110},'1');h.streams[0].emit('done',{status:'unknown',output},'2');assert.equal(h.doc.querySelector('.message-body').textContent,'Available partial');assert.equal(h.doc.querySelector('.message-body').dataset.historyTime,'110');}finally{h.close();}
});

test('terminal replay retains committed consecutive delta boundaries and authoritative final output',async()=>{
 const h=await setup(p=>p.endsWith('/background')?{items:[item('terminal-gap',{origin_run_id:'r1',origin_session_id:'s1',event_at:115})]}:undefined,{items:[],run:{id:'r1',session_id:'s1',status:'completed',created_at:100,updated_at:140,input:'Ask',output:'Final answer'},tool_replay:{run_id:'r1',cursor:3,events:[{id:1,name:'delta',observed_at:110,data:{text:'EARLIER '}},{id:2,name:'delta',observed_at:120,data:{text:'LATER'}},{id:3,name:'tool',observed_at:130,data:{name:'read_file',status:'completed'}}]}});
 try{await h.open();const card=h.doc.querySelector('[data-background-id]');assert.equal(card.previousElementSibling.querySelector('.markdown')?.textContent,'EARLIER ');assert.equal(card.nextElementSibling.querySelector('.markdown')?.textContent,'LATER');assert.equal(h.doc.querySelector('.live-message .message-body').textContent,'Final answer');}finally{h.close();}
});

for(const chunks of [[null],Array.from({length:8193},()=>({text:'',observed_at:110})),[{text:'Public',observed_at:110,node:'Injected untrusted node'}],[{text:'Public',observed_at:110,reasoning:'Hidden metadata'}]])test(`timed chunk metadata is bounded public text only: ${chunks.length===8193?'oversized':JSON.stringify(chunks)}`,async()=>{
 // Include ordinary content so oversized empty-chunk arrays cannot allocate nodes.
 const timed_chunks=chunks.length===8193?[{text:'Public',observed_at:110},...chunks]:chunks;
 const h=await setup(p=>p.endsWith('/background')?{items:[item('invalid-chunks',{origin_session_id:'s1',origin_message_id:1,event_at:115})]}:undefined,{items:[{id:1,role:'user',content:'Ask',timestamp:100},{id:2,role:'assistant',channel:'commentary',content:'Public',observed_at:110,timed_chunks}],run:null});
 try{await h.open();const body=h.doc.querySelector('.activity-summary .markdown');assert.ok(body,'malformed metadata must not break public history');assert.equal(body.textContent,'Public');assert.ok(body.childNodes.length<10,'untrusted metadata cannot create unlimited DOM nodes');assert.doesNotMatch(h.doc.querySelector('.messages').textContent,/Injected untrusted node|Hidden metadata/);}finally{h.close();}
});

for(const mode of ['online','replay','materialized'])test(`consecutive delta boundaries survive ${mode} and late background receipt`,async()=>{
 let items=[];
 const events=[{id:1,name:'delta',observed_at:110,data:{text:'EARLIER '}},{id:2,name:'delta',observed_at:120,data:{text:'LATER'}},{id:3,name:'tool',observed_at:130,data:{name:'last_tool',tool_call_id:'t',status:'running'}}];
 const snapshot=mode==='materialized'?{items:[{id:'user',role:'user',run_id:'r1',timestamp:100,content:'Ask'},{id:'text',role:'assistant',run_id:'r1',channel:'commentary',source:'journal',observed_at:110,content:'EARLIER LATER',timed_chunks:[{text:'EARLIER ',observed_at:110},{text:'LATER',observed_at:120}]},{id:'tool',role:'tool',name:'last_tool',run_id:'r1',observed_at:130},{id:'final',role:'assistant',content:'Final output',observed_at:140}],run:null}:{items:[],run:{id:'r1',session_id:'s1',status:'running',created_at:100,input:'Ask'},tool_replay:{run_id:'r1',cursor:mode==='online'?0:3,events:mode==='online'?[]:events}};
 const h=await setup(p=>p.endsWith('/background')?{items}:undefined,snapshot);
 try{
  await h.open();if(mode==='online')for(const e of events)h.streams[0].emit(e.name,{...e.data,observed_at:e.observed_at},String(e.id));
  assert.equal(h.doc.querySelectorAll('.activity-summary').length,1,'consecutive deltas remain one disclosure without a boundary');
  const first=h.doc.querySelector('.activity-summary');first.open=false;
  items=[item('delta-boundary',{origin_run_id:'r1',origin_session_id:'s1',event_at:115})];await h.event('focus');
  const card=h.doc.querySelector('[data-background-id]');
  assert.equal(card.previousElementSibling.querySelector('.markdown').textContent,'EARLIER ');
  assert.equal(card.nextElementSibling.querySelector('.markdown').textContent,'LATER');
  assert.equal(card.nextElementSibling.nextElementSibling.querySelector('.tool-name').textContent,'last_tool');
  assert.equal(card.previousElementSibling,first);assert.equal(first.open,false);assert.equal(card.nextElementSibling.open,false);
  await h.event('focus');assert.equal(h.doc.querySelectorAll('.activity-summary').length,2);
  if(mode==='online'){snapshot.tool_replay={run_id:'r1',cursor:3,events};}
  click(h.doc,'Chats');await tick();await h.open();
  const reloaded=h.doc.querySelector('[data-background-id]');
  assert.equal(reloaded.previousElementSibling.querySelector('.markdown').textContent,'EARLIER ');
  assert.equal(reloaded.nextElementSibling.querySelector('.markdown').textContent,'LATER');
  if(mode==='materialized')assert.match(h.doc.querySelector('.messages').textContent,/Final output/);
 }finally{h.close();}
});

test('background receipt remains inside live run between timed replay and newer SSE segments',async()=>{
 let items=[];
 const snapshot={items:[],run:{id:'r1',session_id:'s1',status:'running',input:'Active question',created_at:100},tool_replay:{run_id:'r1',cursor:2,events:[{id:1,name:'tool',observed_at:110,data:{name:'first_tool',tool_call_id:'t1',status:'running'}},{id:2,name:'commentary',observed_at:120,data:{id:'c1',text:'Later progress'}}]}};
 const h=await setup(p=>p.endsWith('/background')?{items}:undefined,snapshot);
 try{
  await h.open();const stream=h.streams[0],live=h.doc.querySelector('.live-message'),first=live.querySelector('.tool-activity');first.open=true;
  items=[item('live',{origin_run_id:'r1',origin_session_id:'s1',event_at:115})];await h.event('focus');
  const card=h.doc.querySelector('[data-background-id]');assert.equal(card.parentElement,live,'receipt must interleave inside the live run');assert.equal(card.previousElementSibling,first);assert.match(card.nextElementSibling.textContent,/Later progress/);
  card.open=true;stream.emit('tool',{name:'second_tool',tool_call_id:'t2',status:'running',observed_at:130},'3');stream.emit('tool',{name:'second_tool',tool_call_id:'t2',status:'running',observed_at:130},'3');
  stream.emit('tool',{name:'first_tool',tool_call_id:'t1',status:'completed',observed_at:140},'4');
  assert.equal(live.querySelectorAll('.tool-preview').length,2);assert.equal(card.previousElementSibling,first,'completion keeps start position');assert.equal(first.open,true);assert.equal(card.open,true);assert.match(card.nextElementSibling.textContent,/Later progress/);assert.ok([...live.children].indexOf(live.querySelectorAll('.tool-activity')[1])>[...live.children].indexOf(card));
  await h.event('focus');assert.equal(h.doc.querySelector('[data-background-id]'),card);assert.equal(card.parentElement,live);assert.equal(card.open,true);
  click(h.doc,'Chats');await tick();await h.open();assert.equal(h.doc.querySelector('[data-background-id]').parentElement,h.doc.querySelector('.live-message'));assert.match(h.doc.querySelector('[data-background-id]').nextElementSibling.textContent,/Later progress/);
 }finally{h.close();}
});

test('background live boundary splits grouped replay tools and keeps pending completions and future tools on their sides',async()=>{
 let items=[];const snapshot={items:[],run:{id:'r1',session_id:'s1',status:'running',input:'Active question',created_at:100},tool_replay:{run_id:'r1',cursor:2,events:[{id:1,name:'tool',observed_at:110,data:{name:'first_tool',tool_call_id:'t1',status:'running'}},{id:2,name:'tool',observed_at:120,data:{name:'second_tool',tool_call_id:'t2',status:'running'}}]}};
 const h=await setup(p=>p.endsWith('/background')?{items}:undefined,snapshot);
 try{
  await h.open();const live=h.doc.querySelector('.live-message'),stream=h.streams[0],first=live.querySelector('.tool-activity');first.open=true;
  items=[item('between-tools',{origin_run_id:'r1',origin_session_id:'s1',event_at:115})];await h.event('focus');const card=h.doc.querySelector('[data-background-id]');
  assert.equal(card.parentElement,live);assert.equal(card.previousElementSibling,first);assert.equal(card.nextElementSibling?.querySelector('.tool-name')?.textContent,'second_tool');
  stream.emit('tool',{name:'second_tool',tool_call_id:'t2',status:'completed',observed_at:125},'3');stream.emit('tool',{name:'third_tool',tool_call_id:'t3',status:'running',observed_at:130},'4');
  assert.deepEqual([...live.querySelectorAll('.tool-name')].map(n=>n.textContent),['first_tool','second_tool','third_tool']);assert.equal(first.querySelectorAll('.tool-preview').length,1);assert.equal(card.nextElementSibling.querySelector('.tool-preview').dataset.status,'completed');
  items.push(item('after-tools',{origin_run_id:'r1',origin_session_id:'s1',event_at:135}));await h.event('focus');const last=h.doc.querySelector('[data-background-id="after-tools"]');stream.emit('tool',{name:'fourth_tool',tool_call_id:'t4',status:'running',observed_at:140},'5');
  assert.equal(last.nextElementSibling?.querySelector('.tool-name')?.textContent,'fourth_tool','new tools do not grow the pre-receipt group');assert.equal(first.open,true);assert.equal(h.doc.querySelector('.notice').hidden,true);
 }finally{h.close();}
});

test('background receipt separates public delta segments using recorded receipt time, not replay wall clock',async()=>{
 let items=[];const snapshot={items:[],run:{id:'r1',session_id:'s1',status:'running',input:'Active question',created_at:100},tool_replay:{run_id:'r1',cursor:1,events:[{id:1,name:'delta',observed_at:110,data:{text:'Earlier streamed progress'}}]}};
 const h=await setup(p=>p.endsWith('/background')?{items}:undefined,snapshot);
 try{
  await h.open();items=[item('delta-gap',{origin_run_id:'r1',origin_session_id:'s1',event_at:115})];await h.event('focus');const card=h.doc.querySelector('[data-background-id]'),live=h.doc.querySelector('.live-message');
  assert.equal(card.parentElement,live);assert.match(card.previousElementSibling.textContent,/Earlier streamed progress/,'existing output is retained before the receipt');
  h.streams[0].emit('delta',{text:'Later streamed progress',observed_at:120},'2');h.streams[0].emit('tool',{name:'later_tool',tool_call_id:'t2',status:'running',observed_at:130},'3');
  assert.match(card.nextElementSibling.textContent,/Later streamed progress/);assert.equal(card.previousElementSibling.dataset.historyTime,'110');assert.equal(card.nextElementSibling.dataset.historyTime,'120');
  await h.event('focus');assert.match(card.nextElementSibling.textContent,/Later streamed progress/);assert.equal(h.doc.querySelectorAll('[data-background-id]').length,1);
 }finally{h.close();}
});

test('background late insertion inside a live article preserves the visible segment reader anchor',async()=>{
 let items=[];const snapshot={items:[],run:{id:'r1',session_id:'s1',status:'running',input:'Active question',created_at:100},tool_replay:{run_id:'r1',cursor:2,events:[{id:1,name:'tool',observed_at:110,data:{name:'first_tool',tool_call_id:'t1',status:'running'}},{id:2,name:'commentary',observed_at:120,data:{text:'Visible reader progress'}}]}};
 const h=await setup(p=>p.endsWith('/background')?{items}:undefined,snapshot);
 try{
  await h.open();const messages=h.doc.querySelector('.messages'),live=h.doc.querySelector('.live-message'),anchor=live.querySelector('.activity-summary');
  Object.defineProperties(messages,{scrollHeight:{configurable:true,get:()=>live.children.length*120+500},clientHeight:{configurable:true,value:300}});messages.getBoundingClientRect=()=>({top:0,bottom:300});live.getBoundingClientRect=()=>({top:-messages.scrollTop,bottom:live.children.length*120-messages.scrollTop});
  for(const node of live.children)node.getBoundingClientRect=()=>{const top=[...live.children].indexOf(node)*120-messages.scrollTop;return {top,bottom:top+120};};
  messages.scrollTop=[...live.children].indexOf(anchor)*120+13;const before=anchor.getBoundingClientRect().top;
  items=[item('late-live',{origin_run_id:'r1',origin_session_id:'s1',event_at:115})];await h.event('focus');assert.equal(anchor.getBoundingClientRect().top,before,'anchor is the visible segment, not the live article top');
  await h.event('focus');assert.equal(anchor.getBoundingClientRect().top,before,'unchanged nested placement does not drift');
 }finally{h.close();}
});

test('background boundary survives terminal reconciliation of incomplete tool rows',async()=>{
 const snapshot={items:[],run:{id:'r1',session_id:'s1',status:'running',input:'Active question',created_at:100},tool_replay:{run_id:'r1',cursor:2,events:[{id:1,name:'tool',observed_at:110,data:{name:'first_tool',tool_call_id:'t1',status:'running'}},{id:2,name:'tool',observed_at:120,data:{name:'second_tool',tool_call_id:'t2',status:'running'}}]}};
 const items=[item('terminal-gap',{origin_run_id:'r1',origin_session_id:'s1',event_at:115})];
 const h=await setup(p=>p.endsWith('/background')?{items}:undefined,snapshot);
 try{await h.open();const card=h.doc.querySelector('[data-background-id]'),later=card.nextElementSibling;card.open=true;h.streams[0].emit('done',{status:'completed',output:'Final answer'},'3');assert.equal(card.nextElementSibling,later,'unknown outcome still retains the original start boundary');assert.equal(card.open,true);assert.deepEqual([...h.doc.querySelectorAll('.tool-preview')].map(row=>[row.dataset.status,row.dataset.historyTime]),[['unknown','110'],['unknown','120']]);items.push(item('after-terminal',{origin_run_id:'r1',origin_session_id:'s1',event_at:125}));await h.event('focus');assert.equal(h.doc.querySelector('.live-message .message-body').textContent,'Final answer','late receipt must not turn the final answer into progress');}finally{h.close();}
});

for(const mode of ['completed replay','materialized reload'])test(`background-only live tail survives ${mode} without a tool or commentary boundary`,async()=>{
 let items=[];
 const run={id:'r1',session_id:'s1',status:'running',input:'Question',created_at:100,updated_at:130,error:null};
 const events=[{id:1,name:'delta',observed_at:110,data:{text:'PUBLIC A'}},{id:2,name:'delta',observed_at:120,data:{text:'DRAFT B'}}];
 const snapshot={items:[],run};
 const h=await setup(p=>p.endsWith('/background')?{items}:undefined,snapshot);
 const check=()=>{const card=h.doc.querySelector('[data-background-id="tail"]');assert.ok(card);assert.equal(card.previousElementSibling?.querySelector('.markdown')?.textContent,'PUBLIC A');assert.match(card.nextElementSibling.textContent,/DEFINITIVE F/);assert.doesNotMatch(h.doc.querySelector('.messages').textContent,/DRAFT B/);assert.equal(h.doc.querySelectorAll('.activity-summary').length,1);assert.equal(h.doc.querySelectorAll('[data-background-id]').length,1);};
 try{
  await h.open();const stream=h.streams[0];stream.emit('delta',{text:'PUBLIC A',observed_at:110},'1');
  items=[item('tail',{origin_run_id:'r1',origin_session_id:'s1',event_at:115})];await h.event('focus');
  const progress=h.doc.querySelector('.activity-summary'),text=progress.querySelector('.markdown').firstChild;h.win.getSelection().setBaseAndExtent(text,0,text,text.length);
  stream.emit('delta',{text:'DRAFT B',observed_at:120},'2');stream.emit('done',{status:'completed',output:'DEFINITIVE F',updated_at:130},'3');check();
  assert.equal(h.win.getSelection().toString(),'PUBLIC A');assert.equal(text.isConnected,true);
  Object.assign(run,{status:'completed',output:'DEFINITIVE F'});
  if(mode==='completed replay')snapshot.tool_replay={run_id:'r1',cursor:2,events};
  else{snapshot.items=JSON.parse(execFileSync('python3',['-c','import json,sys; from backend.native_catalog import _journal_turn; value=json.load(sys.stdin); print(json.dumps(_journal_turn(value[0],value[1])))'],{cwd:new URL('../../',import.meta.url),input:JSON.stringify([run,events]),encoding:'utf8'}));snapshot.run=null;}
  click(h.doc,'Chats');await tick();await h.open();check();await h.event('focus');check();
  // A delayed endpoint must not resurrect a draft merely because metadata exists.
  items=[];click(h.doc,'Chats');await tick();await h.open();assert.equal(h.doc.querySelectorAll('.activity-summary').length,0);assert.doesNotMatch(h.doc.querySelector('.messages').textContent,/PUBLIC A|DRAFT B/);
  items=[item('tail',{origin_run_id:'r1',origin_session_id:'s1',event_at:115})];await h.event('focus');check();
 }finally{h.close();}
});

for(const mode of ['completed replay','materialized reload'])for(const times of [[115,125],[125,115]])test(`first refresh resolves all retained tail boundaries: ${mode}, ${times}`,async()=>{
 const events=[{id:1,name:'delta',observed_at:110,data:{text:'PUBLIC A'}},{id:2,name:'delta',observed_at:120,data:{text:'PUBLIC B'}},{id:3,name:'delta',observed_at:128,data:{text:'DRAFT C'}}];
 const run={id:'r1',session_id:'s1',status:'completed',input:'Question',created_at:100,updated_at:130,output:'FINAL F',error:null};
 const snapshot=mode==='completed replay'?{items:[],run,tool_replay:{run_id:'r1',cursor:3,events}}:{items:JSON.parse(execFileSync('python3',['-c','import json,sys; from backend.native_catalog import _journal_turn; value=json.load(sys.stdin); print(json.dumps(_journal_turn(value[0],value[1])))'],{cwd:new URL('../../',import.meta.url),input:JSON.stringify([run,events]),encoding:'utf8'})),run:null};
 let items=[];
 const h=await setup(p=>p.endsWith('/background')?{items}:undefined,snapshot);
 const order=()=>[...(h.doc.querySelector('.live-message') || h.doc.querySelector('.messages')).children].filter(n=>n.matches('.activity-summary,.background-result,.message-body,.assistant-message')).map(n=>n.dataset.backgroundId || n.querySelector('.markdown')?.textContent || n.textContent);
 try{
  await h.open();assert.deepEqual(order(),['FINAL F']);
  const final=h.doc.querySelector('.message-body,.assistant-message:not(.live-message)'),text=h.doc.createTreeWalker(final.querySelector('.markdown') || final,h.win.NodeFilter.SHOW_TEXT).nextNode();
  h.win.getSelection().setBaseAndExtent(text,0,text,text.length);
  items=times.map(event_at=>item(`bg${event_at}`,{origin_run_id:'r1',origin_session_id:'s1',event_at}));
  await h.event('focus');
  assert.deepEqual(order(),['PUBLIC A','bg115','PUBLIC B','bg125','FINAL F'],'first refresh must not need a second placement pass');
  assert.equal(h.doc.querySelector('.message-body,.assistant-message:not(.live-message)'),final);assert.equal(text.isConnected,true);assert.equal(h.win.getSelection().toString(),'FINAL F');
  const progress=h.doc.querySelector('.activity-summary'),card=h.doc.querySelector('[data-background-id="bg115"]');progress.open=false;card.open=true;
  const publicText=progress.querySelector('.markdown').firstChild;
  await h.event('focus');
  assert.deepEqual(order(),['PUBLIC A','bg115','PUBLIC B','bg125','FINAL F']);assert.equal(progress,h.doc.querySelector('.activity-summary'));assert.equal(progress.open,false);assert.equal(progress.querySelector('.markdown').firstChild,publicText);assert.equal(card,h.doc.querySelector('[data-background-id="bg115"]'));assert.equal(card.open,true);
  assert.doesNotMatch(h.doc.querySelector('.messages').textContent,/DRAFT C/);
 }finally{h.close();}
});

test('late background receipt after terminal SSE reveals only the proved public prefix',async()=>{
 let items=[];const h=await setup(p=>p.endsWith('/background')?{items}:undefined,{items:[],run:{id:'r1',session_id:'s1',status:'running',input:'Question'}});
 try{
  await h.open();const stream=h.streams[0];stream.emit('delta',{text:'PUBLIC A',observed_at:110},'1');stream.emit('delta',{text:'DRAFT B',observed_at:120},'2');stream.emit('done',{status:'completed',output:'DEFINITIVE F',updated_at:130},'3');
  assert.equal(h.doc.querySelectorAll('.activity-summary').length,0);
  for(const extra of [{origin_run_id:'other',origin_session_id:'s1',event_at:115},{origin_run_id:'r1',origin_session_id:'s2',event_at:115},{origin_run_id:'r1',origin_session_id:'s1',event_at:null},{origin_run_id:'r1',origin_session_id:'s1',event_at:140}]){
   items=[item('unproved',extra)];await h.event('focus');assert.equal(h.doc.querySelectorAll('.activity-summary').length,0);
  }
  items=[item('proved',{origin_run_id:'r1',origin_session_id:'s1',event_at:115})];await h.event('focus');
  const card=h.doc.querySelector('[data-background-id]');assert.equal(card.previousElementSibling.querySelector('.markdown').textContent,'PUBLIC A');assert.equal(card.nextElementSibling.textContent,'DEFINITIVE F');assert.doesNotMatch(h.doc.querySelector('.messages').textContent,/DRAFT B/);
  await h.event('focus');assert.equal(h.doc.querySelectorAll('.activity-summary').length,1);
 }finally{h.close();}
});

test('background chronological boundary can follow the final answer inside a completed live article',async()=>{
 const snapshot={items:[],run:{id:'r1',session_id:'s1',status:'completed',input:'Question',output:'Final answer',created_at:100,updated_at:130},tool_replay:{run_id:'r1',cursor:1,events:[{id:1,name:'tool',observed_at:110,data:{name:'first_tool',tool_call_id:'t1',status:'completed'}}]}};
 const items=[item('before-final',{origin_run_id:'r1',origin_session_id:'s1',event_at:115}),item('after-final',{origin_run_id:'r1',origin_session_id:'s1',event_at:145})];
 const h=await setup(p=>p.endsWith('/background')?{items}:undefined,snapshot);
 try{await h.open();const live=h.doc.querySelector('.live-message'),output=live.querySelector('.message-body');assert.equal(output.previousElementSibling.dataset.backgroundId,'before-final');assert.equal(output.nextElementSibling?.dataset.backgroundId,'after-final','late receipt follows the final answer rather than moving it');assert.equal(output.textContent,'Final answer');await h.event('focus');assert.equal(output.nextElementSibling?.dataset.backgroundId,'after-final');}finally{h.close();}
});

test('live-stream reconnect refreshes background receipts without changing approval or stop controls',async()=>{
 let items=[];const h=await setup(p=>p.endsWith('/background')?{items}:undefined,{items:[],run:{id:'r1',session_id:'s1',status:'running',input:'Active question'}});
 try{await h.open();const stream=h.streams[0];stream.onopen();await tick();stream.emit('approval',{});const stop=h.doc.querySelector('[aria-label="Stop run"]'),activity=h.doc.querySelector('.live-message');items=[item()];stream.onerror();stream.onopen();await tick();assert.equal(h.doc.querySelectorAll('[data-background-id]').length,1);assert.equal(h.doc.querySelector('[aria-label="Stop run"]'),stop);assert.equal(stop.disabled,false);assert.equal(h.doc.querySelector('.live-message'),activity);assert.equal(h.doc.querySelector('.conversation-status').dataset.state,'waiting_for_approval');items.push(item('after-tick'));await h.poll();assert.equal(h.doc.querySelectorAll('[data-background-id]').length,2);}finally{h.close();}
});
test('a failed health probe cannot release an outstanding background read or start overlapping refreshes',async()=>{
 let pending=null;const h=await setup(p=>{if(p.endsWith('/background'))return pending?pending.promise:{items:[]};if(p.includes('limit=1') && pending)throw Object.assign(new Error('Temporarily unavailable'),{status:503});});
 try{await h.open();pending=deferred();await h.poll();const reads=h.calls.filter(c=>c.path.endsWith('/background')).length;for(let i=0;i<5;i++)await h.event('focus');assert.equal(h.calls.filter(c=>c.path.endsWith('/background')).length,reads);assert.equal([...h.timers.values()].filter(v=>v.delay===10000).length,1,'deadline remains until receipt finishes');pending.resolve({items:[item()]});await tick();assert.equal(h.doc.querySelectorAll('[data-background-id]').length,1);assert.equal([...h.timers.values()].filter(v=>v.delay===10000).length,0);}finally{h.close();}
});
test('an otherwise empty conversation shows the receipt without duplicate labels or a misleading empty state',async()=>{
 const h=await setup(p=>p.endsWith('/background')?{items:[item('only-result',{title:'Background result'})]}:undefined,{items:[],run:null});
 try{await h.open();const card=h.doc.querySelector('[data-background-id]');assert.equal(card.textContent.match(/Background result/g)?.length,1);assert.equal(h.doc.querySelector('.messages .empty'),null);}finally{h.close();}
});
test('optional endpoint errors and malformed envelopes stay silent, retain verified cards, and recover on the next focus',async()=>{
 let response={items:[item()]};const h=await setup(p=>{if(p.endsWith('/background')){if(response instanceof Error)throw response;return response;}});
 try{await h.open();const card=h.doc.querySelector('[data-background-id]');for(const value of [null,{}, {items:'not-an-array'},Object.assign(new Error('Transient'),{status:503})]){response=value;await h.event('focus');assert.equal(h.doc.querySelector('[data-background-id]'),card);assert.equal(h.doc.querySelector('.notice').hidden,true);}response=Object.assign(new Error('Old backend'),{status:404});await h.event('focus');assert.equal(h.doc.querySelectorAll('[data-background-id]').length,0);assert.equal(h.doc.querySelector('.notice').hidden,true);response={items:[item()]};await h.event('focus');assert.equal(h.doc.querySelectorAll('[data-background-id]').length,1);}finally{h.close();}
});
test('chat open renders one distinct Background result card per receipt, retaining every fanout report',async()=>{
 const h=await setup(p=>p.endsWith('/background')?{items:[item(),item()]}:undefined);
 try{await h.open();const cards=h.doc.querySelectorAll('[data-background-id]');assert.equal(cards.length,1);const card=cards[0];assert.equal(card.getAttribute('aria-label'),'Background result');assert.equal(card.querySelector('.background-label').textContent,'Background result');assert.match(card.textContent,/Child A.*Report A.*Child B.*Report B/s);assert.match(card.textContent,/No follow-up was run automatically\./);assert.equal(card.matches('.message,.user-message,.assistant-message'),false);assert.equal(h.doc.querySelectorAll('.user-message').length,1);assert.equal(h.doc.querySelectorAll('.assistant-message').length,1);assert.ok(card.querySelector('.markdown strong'));assert.equal(h.calls.filter(c=>c.path==='/sessions/s1/background').length,1);assert.equal(withoutUnitPresence(h.calls).filter(c=>c.options.method && c.options.method!=='GET').length,0);}finally{h.close();}
});
