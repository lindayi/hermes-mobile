import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const tick=()=>new Promise(resolve=>setTimeout(resolve,10));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const button=(doc,label)=>[...doc.querySelectorAll('button')].find(item=>
  item.getAttribute('aria-label')===label || item.textContent.trim()===label);
const question=(overrides={})=>({
  question_id:'a'.repeat(32),run_id:'r',session_id:'s',question:'Pick a plan?',
  choices:['Keep current','Change it'],multi_select:false,status:'pending',
  answer:null,other:null,created_at:1,updated_at:2,...overrides,
});
async function setup({item=question(),answerResult,waitForAnswer=false,history=[]}={}) {
  const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/',pretendToBeVisual:true});
  const {window:win}=dom,doc=win.document,calls=[],streams=[],gate=deferred();
  class Events {
    constructor(){this.listeners={};streams.push(this);}
    addEventListener(name,callback){this.listeners[name]=callback;}
    emit(name,data){this.listeners[name]?.({data:JSON.stringify(data)});}
    close(){}
  }
  win.EventSource=Events;
  const app=await mountApp(doc,{clear(){},async request(path,options={}) {
    calls.push({path,options});
    if(path==='/auth/me')return {user:{id:'owner',status:'ready'}};
    if(path.startsWith('/sessions?'))return {items:[{id:'s',title:'Fixture'}],total:1};
    if(path.includes('/messages'))return {items:history,run:{id:'r',session_id:'s',status:'running',input:'Original'}};
    if(path.endsWith('/clarifications') && options.method!=='POST')
      return {available:true,items:item?[item]:[]};
    if(path.endsWith('/answer') && options.method==='POST') {
      if(waitForAnswer)await gate.promise;
      return answerResult || {question_id:item.question_id,run_id:'r',status:'answered',answer:options.body.answer};
    }
    if(path.endsWith('/controls'))return {steering:false,attempts:[]};
    if(path.endsWith('/stop'))return {status:'stopping'};
    return {items:[]};
  }},win);
  button(doc,'Fixture').click();await tick();
  return {doc,win,calls,streams,gate,app,close(){app.destroy();win.close();}};
}

test('single selection is explicit, recommended is not preselected, and answer uses the same run',async()=>{
  const h=await setup();
  try {
    const card=h.doc.querySelector('.clarification-card');
    assert.ok(card);
    assert.equal(card.querySelectorAll('input[type=radio]:checked').length,0);
    assert.match(card.textContent,/Keep current.*Recommended/s);
    assert.equal(h.calls.filter(call=>call.options.method==='POST').length,0);
    card.querySelector('input[value="Change it"]').click();
    button(card,'Submit answer').click();await tick();
    const post=h.calls.find(call=>call.path.endsWith('/answer'));
    assert.deepEqual(post.options.body,{answer:'Change it',other:false});
    assert.equal(h.calls.filter(call=>call.path==='/runs').length,0);
    assert.match(card.textContent,/Answered.*Answer: Change it/s);
  } finally {h.close();}
});

test('multi-select and Other return the selected list after explicit submit',async()=>{
  const h=await setup({item:question({multi_select:true})});
  try {
    const card=h.doc.querySelector('.clarification-card');
    const choices=[...card.querySelectorAll('input[type=checkbox]')];
    choices[0].click();choices.at(-1).click();
    card.querySelector('input[aria-label="Other answer"]').value='A third option';
    button(card,'Submit answer').click();await tick();
    assert.deepEqual(h.calls.find(call=>call.path.endsWith('/answer')).options.body,
      {answer:['Keep current','A third option'],other:true});
  } finally {h.close();}
});

test('open-ended answer is text-only and cannot submit an empty value',async()=>{
  const h=await setup({item:question({choices:null})});
  try {
    const card=h.doc.querySelector('.clarification-card');
    button(card,'Submit answer').click();await tick();
    assert.equal(h.calls.filter(call=>call.path.endsWith('/answer')).length,0);
    card.querySelector('textarea[aria-label="Your answer"]').value='  Use the safer plan  ';
    button(card,'Submit answer').click();await tick();
    assert.deepEqual(h.calls.find(call=>call.path.endsWith('/answer')).options.body,
      {answer:'Use the safer plan',other:false});
  } finally {h.close();}
});

test('answer retry is not automatic and reopened sessions rehydrate pending question',async()=>{
  const h=await setup({waitForAnswer:true});
  try {
    const form=h.doc.querySelector('.clarification-form');
    form.querySelector('input[value="Keep current"]').click();
    button(form,'Submit answer').click();button(form,'Submit answer').click();await tick();
    assert.equal(h.calls.filter(call=>call.path.endsWith('/answer')).length,1);
    assert.match(h.doc.querySelector('.clarification-card').textContent,/Submitting answer/);
    h.gate.resolve();await tick();
    button(h.doc,'Back to chats').click();await tick();
    button(h.doc,'Fixture').click();await tick();
    assert.equal(h.doc.querySelectorAll('.clarification-card').length,1);
    assert.equal(h.calls.filter(call=>call.path.endsWith('/answer')).length,1);
  } finally {h.close();}
});

test('replayed clarification stays between surrounding public messages',async()=>{
  const pending=question(),answered=question({status:'answered',answer:'Change it',other:false,updated_at:4});
  const h=await setup({item:null});
  try {
    const stream=h.streams[0];
    stream.emit('commentary',{text:'Before question'});
    stream.emit('clarification',pending);
    stream.emit('clarification',answered);
    stream.emit('commentary',{text:'After answer'});
    await tick();
    const transcript=h.doc.querySelector('.messages').textContent;
    assert.ok(transcript.indexOf('Before question')<transcript.indexOf('Pick a plan?'));
    assert.ok(transcript.indexOf('Pick a plan?')<transcript.indexOf('After answer'));
    assert.match(transcript,/Answered.*Answer: Change it/s);
  } finally {h.close();}
});

test('completed clarification history renders question and answer as plain text',async()=>{
  const unsafe='<img src=x onerror=alert(1)>';
  const h=await setup({item:null,history:[
    {id:'before',role:'assistant',content:'Before answer'},
    {id:'historical',role:'assistant',kind:'clarification',content:unsafe,
      clarification_status:'answered',clarification_answer:'Selected text',timestamp:2},
    {id:'after',role:'assistant',content:'After answer'},
  ]});
  try {
    const cards=h.doc.querySelectorAll('.clarification-history');
    assert.equal(cards.length,1);
    assert.match(cards[0].textContent,/Answered.*Answer: Selected text/s);
    assert.equal(cards[0].querySelector('img'),null);
    const text=h.doc.querySelector('.messages').textContent;
    assert.ok(text.indexOf('Before answer')<text.indexOf(unsafe));
    assert.ok(text.indexOf(unsafe)<text.indexOf('After answer'));
  } finally {h.close();}
});
