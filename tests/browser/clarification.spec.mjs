import assert from 'node:assert/strict';
import test from 'node:test';
import {createServer} from 'node:http';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createRequire} from 'node:module';
import {generatedAssets} from './generated-assets.mjs';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');

const questionFixture=question_id=>({
  question_id,run_id:'r',session_id:'s',question:'Choose safely?',
  choices:['Keep current','Change it'],multi_select:false,status:'pending',
  answer:null,other:null,created_at:1,updated_at:2,
});

async function fixture(shape,{
  rejectDuplicate=false,holdAnswer=false,initialRunStatus=null,
  rehydrateStatus=null,holdRehydrate=false,itemStatus='pending',
  completeAfterAnswer=false,answerOutcomes=[],holdConflictReconcile=false,
}={}) {
  const temporary=await mkdtemp(join(tmpdir(),'hermes-clarification-browser-'));
  const root=generatedAssets(temporary);
  const answerCalls=[],runCalls=[];
  const streams=[];
  let currentRunStatus=initialRunStatus;
  let currentOutput=null;
  let resolveRehydrateStarted,releaseRehydrate,resolveCompleted;
  const rehydrateStarted=new Promise(resolve=>{resolveRehydrateStarted=resolve;});
  const rehydrateGate=new Promise(resolve=>{releaseRehydrate=resolve;});
  const completed=new Promise(resolve=>{resolveCompleted=resolve;});
  const item={question_id:'a'.repeat(32),run_id:'r',session_id:'s',
    question:'Choose safely?',choices:shape==='open'?null:
      shape.endsWith('-sentinel')?['__other__','A separate choice']:['Keep current','Change it'],
    multi_select:shape==='multi'||shape==='multi-sentinel',status:itemStatus,answer:null,other:null,
    created_at:1,updated_at:2};
  let items=[item];
  const nativeAnswerCalls=[];
  let resolveWaiter;
  const waiter=new Promise(resolve=>{resolveWaiter=resolve;});
  let resolveSubmitted,releaseAck,resolveStreamReady,resolveConflictReconcile,releaseConflictReconcile;
  const answerSubmitted=new Promise(resolve=>{resolveSubmitted=resolve;});
  const acknowledgement=new Promise(resolve=>{releaseAck=resolve;});
  const streamReady=new Promise(resolve=>{resolveStreamReady=resolve;});
  const conflictReconcileStarted=new Promise(resolve=>{resolveConflictReconcile=resolve;});
  const conflictReconcileGate=new Promise(resolve=>{releaseConflictReconcile=resolve;});
  let clarificationGets=0;
  const publish=(event,data)=>{
    for(const response of streams)if(!response.destroyed)
      response.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
  };
  const server=createServer(async(request,response)=>{
    const url=new URL(request.url,'http://fixture');
    const path=url.pathname.replace('/hermes/app-api','');
    const json=(value,status=200)=>{
      response.writeHead(status,{'Content-Type':'application/json'});
      response.end(JSON.stringify(value));
    };
    if(url.pathname.startsWith('/hermes/app-api/')){
      if(request.method==='POST'){
        let body='';for await(const chunk of request)body+=chunk;
        const value=JSON.parse(body);
        if(path==='/runs')runCalls.push(value);
        if(path.endsWith('/answer')){
          answerCalls.push(value);
          resolveSubmitted();
          const outcome=answerOutcomes.shift();
          if(outcome){
            if(outcome.item)Object.assign(item,outcome.item);
            if(outcome.items)items=outcome.items;
            if(outcome.runStatus)currentRunStatus=outcome.runStatus;
            return json(outcome.body||{detail:'Synthetic answer rejection',
              ...(outcome.code?{code:outcome.code}:{})},outcome.status);
          }
          if(item.status!=='pending')return json({error:'conflict'},409);
          if(rejectDuplicate && Array.isArray(value.answer)
              && new Set(value.answer).size!==value.answer.length)
            return json({detail:'Invalid clarification answer'},422);
          nativeAnswerCalls.push(value);
          item.status='answered';item.answer=value.answer;item.other=value.other;item.updated_at=3;
          resolveWaiter(value.answer);
          if(completeAfterAnswer){
            currentRunStatus='completed';
            currentOutput=`Native continuation completed: ${value.answer}`;
            setTimeout(()=>{
              publish('done',{status:'completed',output:currentOutput});
              resolveCompleted({status:currentRunStatus,output:currentOutput});
            },10);
          }
          if(holdAnswer)await acknowledgement;
          return json({question_id:item.question_id,run_id:'r',status:'answered',answer:value.answer});
        }
      }
      if(path==='/auth/me')return json({user:{id:'owner',status:'ready'}});
      if(path==='/sessions')return json({items:[{id:'s',title:'Clarification fixture'}],total:1});
      if(path.endsWith('/messages'))return json({items:[],run:{id:'r',session_id:'s',
        status:initialRunStatus || (item.status==='pending'?'waiting_for_clarification':'running'),
        input:'Original'}});
      if(path.endsWith('/clarifications')){
        clarificationGets++;
        resolveRehydrateStarted();
        if(holdRehydrate)await rehydrateGate;
        if(holdConflictReconcile && answerCalls.length && clarificationGets>1){
          resolveConflictReconcile();await conflictReconcileGate;
        }
        return json({available:true,items,run_id:'r',
          status:rehydrateStatus||currentRunStatus
            ||(items.some(value=>value.status==='pending')
              ?'waiting_for_clarification':'running')});
      }
      if(path==='/runs/r'){
        return json({id:'r',session_id:'s',status:currentRunStatus || 'running',output:currentOutput});
      }
      if(path.endsWith('/controls'))return json({steering:false,attempts:[]});
      if(path.endsWith('/events')){
        response.writeHead(200,{'Content-Type':'text/event-stream'});
        response.write(': ready\n\n');streams.push(response);resolveStreamReady();return;
      }
      return json({items:[]});
    }
    const relative=url.pathname.replace(/^\/hermes\//,'')||'index.html';
    try{
      const content=await readFile(join(root,relative));
      const extension=relative.split('.').at(-1);
      response.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',js:'text/javascript',mjs:'text/javascript',svg:'image/svg+xml',webmanifest:'application/manifest+json'})[extension]||'application/octet-stream'});
      response.end(content);
    }catch{response.writeHead(404).end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  return {server,item,answerCalls,nativeAnswerCalls,runCalls,waiter,answerSubmitted,streamReady,
    conflictReconcileStarted,releaseConflictReconcile:()=>releaseConflictReconcile(),
    rehydrateStarted,completed,streams,releaseAck:()=>releaseAck(),
    releaseRehydrate:()=>releaseRehydrate(),publish,temporary,
    url:`http://127.0.0.1:${server.address().port}/hermes/`};
}

test('real mobile browser answers the same synthetic waiting clarification for all choice modes',{timeout:60000},async()=>{
  const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',
    headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  try{
    for(const mode of ['single','multi','open']){
      const fixtureState=await fixture(mode);
      try{
        const page=await browser.newPage({viewport:{width:320,height:640},serviceWorkers:'block'});
        const errors=[];page.on('pageerror',error=>errors.push(error.message));
        await page.goto(fixtureState.url);
        await page.getByRole('button',{name:'Clarification fixture'}).click();
        const card=page.locator('.clarification-card');
        await card.waitFor();
        assert.equal(await card.locator('input:checked').count(),0,'Recommended is never selected automatically');
        assert.equal(fixtureState.answerCalls.length,0);
        if(mode==='single'){
          await card.getByLabel('Change it').check();
          await card.getByRole('button',{name:'Submit answer'}).focus();
          await page.keyboard.press('Enter');
          assert.equal(await fixtureState.waiter,'Change it');
        }else if(mode==='multi'){
          const boxes=card.locator('input[type=checkbox]');
          await boxes.nth(0).check();await boxes.nth(2).check();
          await card.getByLabel('Other answer').fill('New plan');
          await card.getByRole('button',{name:'Submit answer'}).click();
          assert.deepEqual(await fixtureState.waiter,['Keep current','New plan']);
        }else{
          const answer=card.getByLabel('Your answer');
          await answer.fill('Use the safer plan');
          await card.getByRole('button',{name:'Submit answer'}).click();
          assert.equal(await fixtureState.waiter,'Use the safer plan');
        }
        await card.getByText('Answered').waitFor();
        assert.equal(await page.locator('.live-activity-heading [role=status]').textContent(),'running');
        assert.equal(fixtureState.answerCalls.length,1);
        assert.deepEqual(fixtureState.runCalls,[],'Clarification never starts a new user run');
        const submit=card.getByRole('button',{name:'Submit answer'});
        assert.equal(await submit.count(),0);
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),320);
        assert.deepEqual(errors,[]);
        await page.close();
      }finally{
        fixtureState.server.closeAllConnections();
        await new Promise(resolve=>fixtureState.server.close(resolve));
        await rm(fixtureState.temporary,{recursive:true,force:true});
      }
    }
  }finally{await browser.close();}
});

test('409 answer rejection renders the authoritative first accepted answer',{timeout:60000},async()=>{
  const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',
    headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const fixtureState=await fixture('single',{answerOutcomes:[{
    status:409,runStatus:'running',
    item:{status:'answered',answer:'Keep current',other:false,updated_at:3},
  }]});
  try{
    const page=await browser.newPage({viewport:{width:320,height:640},serviceWorkers:'block'});
    await page.goto(fixtureState.url);
    await page.getByRole('button',{name:'Clarification fixture'}).click();
    const card=page.locator('.clarification-card');
    await card.waitFor();
    await card.getByLabel('Change it').check();
    await card.getByRole('button',{name:'Submit answer'}).click();
    await card.getByText('Answered').waitFor();
    assert.match(await card.textContent(),/Answer: Keep current/);
    assert.doesNotMatch(await card.textContent(),/Attempted answer: Change it/);
    assert.equal(await card.locator('.clarification-form').count(),0);
    assert.equal(fixtureState.answerCalls.length,1);
    assert.deepEqual(fixtureState.nativeAnswerCalls,[]);
    assert.deepEqual(fixtureState.runCalls,[]);
    await page.close();
  }finally{
    fixtureState.server.closeAllConnections();
    await new Promise(resolve=>fixtureState.server.close(resolve));
    await rm(fixtureState.temporary,{recursive:true,force:true});
    await browser.close();
  }
});

test('409 with a still-pending question preserves the draft and requires an explicit retry',{timeout:60000},async()=>{
  const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',
    headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const fixtureState=await fixture('single',{answerOutcomes:[{
    status:409,runStatus:'waiting_for_clarification',
    item:{status:'pending',updated_at:3},
  }]});
  try{
    const page=await browser.newPage({viewport:{width:320,height:640},serviceWorkers:'block'});
    await page.goto(fixtureState.url);
    await page.getByRole('button',{name:'Clarification fixture'}).click();
    const card=page.locator('.clarification-card');
    await card.waitFor();
    await card.getByLabel('Change it').check();
    await card.getByRole('button',{name:'Submit answer'}).click();
    await card.getByText(/not sent.*still waiting/i).waitFor();
    assert.equal(await card.locator('input[value="Change it"]').isChecked(),true);
    assert.equal(await card.getByRole('button',{name:'Submit answer'}).isEnabled(),true);
    assert.equal(fixtureState.nativeAnswerCalls.length,0);
    await card.getByRole('button',{name:'Submit answer'}).click();
    assert.equal(await fixtureState.waiter,'Change it');
    assert.equal(fixtureState.nativeAnswerCalls.length,1);
    assert.equal(fixtureState.answerCalls.length,2);
    assert.deepEqual(fixtureState.runCalls,[]);
    await page.close();
  }finally{
    fixtureState.server.closeAllConnections();
    await new Promise(resolve=>fixtureState.server.close(resolve));
    await rm(fixtureState.temporary,{recursive:true,force:true});
    await browser.close();
  }
});

test('explicit pre-dispatch 503 retains the draft and never claims success',{timeout:60000},async()=>{
  const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',
    headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const fixtureState=await fixture('single',{answerOutcomes:[{
    status:503,code:'clarification_not_sent',
    body:{detail:'Native clarification controls are unavailable',
      code:'clarification_not_sent'},
  }]});
  try{
    const page=await browser.newPage({viewport:{width:320,height:640},serviceWorkers:'block'});
    await page.goto(fixtureState.url);
    await page.getByRole('button',{name:'Clarification fixture'}).click();
    const card=page.locator('.clarification-card');
    await card.waitFor();
    await card.getByLabel('Change it').check();
    await card.getByRole('button',{name:'Submit answer'}).click();
    await card.getByText(/unavailable.*not sent/i).waitFor();
    assert.equal(await card.locator('input[value="Change it"]').isChecked(),true);
    assert.equal(await card.locator('.clarification-form').count(),1);
    assert.equal(fixtureState.nativeAnswerCalls.length,0);
    assert.equal(fixtureState.runCalls.length,0);
    await card.getByRole('button',{name:'Submit answer'}).click();
    assert.equal(await fixtureState.waiter,'Change it');
    assert.equal(fixtureState.nativeAnswerCalls.length,1);
    await page.close();
  }finally{
    fixtureState.server.closeAllConnections();
    await new Promise(resolve=>fixtureState.server.close(resolve));
    await rm(fixtureState.temporary,{recursive:true,force:true});
    await browser.close();
  }
});

test('unmarked 503 remains ambiguous, unknown, and nonretrying',{timeout:60000},async()=>{
  const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',
    headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const fixtureState=await fixture('single',{answerOutcomes:[{
    status:503,item:{status:'unknown',answer:'Change it',other:false,updated_at:3},
    body:{detail:'Synthetic intermediary failure'},
  }]});
  try{
    const page=await browser.newPage({viewport:{width:320,height:640},serviceWorkers:'block'});
    await page.goto(fixtureState.url);
    await page.getByRole('button',{name:'Clarification fixture'}).click();
    const card=page.locator('.clarification-card');
    await card.waitFor();
    await card.getByLabel('Change it').check();
    await card.getByRole('button',{name:'Submit answer'}).click();
    await card.getByText('Answer status unknown',{exact:true}).waitFor();
    assert.match(await card.textContent(),/Attempted answer: Change it/);
    assert.equal(await card.locator('.clarification-form').count(),0);
    await page.getByRole('button',{name:'Back to chats'}).click();
    await page.getByRole('button',{name:'Clarification fixture'}).click();
    await page.locator('.clarification-card').getByText('Answer status unknown',{exact:true}).waitFor();
    assert.equal(fixtureState.answerCalls.length,1);
    assert.equal(fixtureState.nativeAnswerCalls.length,0);
    assert.deepEqual(fixtureState.runCalls,[]);
    await page.close();
  }finally{
    fixtureState.server.closeAllConnections();
    await new Promise(resolve=>fixtureState.server.close(resolve));
    await rm(fixtureState.temporary,{recursive:true,force:true});
    await browser.close();
  }
});

test('409 reconciliation renders an expired old question and the newer current waiter',{timeout:60000},async()=>{
  const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',
    headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const fixtureState=await fixture('single',{answerOutcomes:[{
    status:409,runStatus:'waiting_for_clarification',
    items:[
      {...questionFixture('a'.repeat(32)),status:'expired',updated_at:3},
      {...questionFixture('b'.repeat(32)),question:'Choose the next step?',
        created_at:4,updated_at:4},
    ],
  }]});
  try{
    const page=await browser.newPage({viewport:{width:320,height:640},serviceWorkers:'block'});
    await page.goto(fixtureState.url);
    await page.getByRole('button',{name:'Clarification fixture'}).click();
    const oldCard=page.locator('[data-clarification-id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]');
    await oldCard.waitFor();
    await oldCard.getByLabel('Change it').check();
    await oldCard.getByRole('button',{name:'Submit answer'}).click();
    await oldCard.getByText('Expired',{exact:true}).waitFor();
    const currentCard=page.locator('[data-clarification-id="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]');
    await currentCard.getByRole('button',{name:'Submit answer'}).waitFor();
    assert.equal(await oldCard.locator('.clarification-form').count(),0);
    assert.equal(await currentCard.locator('input:checked').count(),0);
    assert.equal(fixtureState.answerCalls.length,1);
    assert.deepEqual(fixtureState.nativeAnswerCalls,[]);
    assert.deepEqual(fixtureState.runCalls,[]);
    await page.close();
  }finally{
    fixtureState.server.closeAllConnections();
    await new Promise(resolve=>fixtureState.server.close(resolve));
    await rm(fixtureState.temporary,{recursive:true,force:true});
    await browser.close();
  }
});

test('delayed 409 reconciliation is fenced by newer questions, Stop, terminal, and navigation',{timeout:60000},async()=>{
  const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',
    headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  try{
    for(const scenario of ['new-question','stopping','terminal','navigation']){
      const fixtureState=await fixture('single',{
        holdConflictReconcile:true,
        answerOutcomes:[{status:409,runStatus:'waiting_for_clarification'}],
      });
      const page=await browser.newPage({viewport:{width:320,height:640},serviceWorkers:'block'});
      try{
        await page.goto(fixtureState.url);
        await page.getByRole('button',{name:'Clarification fixture'}).click();
        const oldCard=page.locator('.clarification-card').first();
        await oldCard.waitFor();
        await fixtureState.streamReady;
        await oldCard.getByLabel('Change it').check();
        await oldCard.getByRole('button',{name:'Submit answer'}).click();
        await fixtureState.answerSubmitted;
        await fixtureState.conflictReconcileStarted;
        if(scenario==='new-question'){
          fixtureState.publish('clarification',{
            ...questionFixture('b'.repeat(32)),question:'Choose the next step?',
            created_at:4,updated_at:4,
          });
        }else if(scenario==='stopping'){
          fixtureState.publish('status',{status:'stopping'});
        }else if(scenario==='terminal'){
          fixtureState.publish('clarification',{
            ...questionFixture('a'.repeat(32)),status:'expired',updated_at:3,
          });
          fixtureState.publish('done',{status:'completed'});
        }else await page.getByRole('button',{name:'Back to chats'}).click();
        fixtureState.releaseConflictReconcile();
        await page.waitForTimeout(50);
        if(scenario==='new-question'){
          const currentCard=page.locator('[data-clarification-id="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]');
          assert.equal(await currentCard.getByRole('button',{name:'Submit answer'}).isEnabled(),true);
          assert.equal(await oldCard.locator('.clarification-form').count(),0);
        }else if(scenario==='stopping'){
          assert.equal(await page.locator('.live-activity-heading [role=status]').textContent(),'stopping');
          assert.equal(await oldCard.locator('.clarification-form').count(),0);
        }else if(scenario==='terminal'){
          assert.equal(await page.locator('.live-activity-heading [role=status]').textContent(),'completed');
          assert.equal(await oldCard.getByText('Expired',{exact:true}).count(),1);
        }else assert.equal(await page.locator('.clarification-card').count(),0);
        assert.deepEqual(fixtureState.nativeAnswerCalls,[]);
        assert.deepEqual(fixtureState.runCalls,[]);
      }finally{
        fixtureState.releaseConflictReconcile();
        fixtureState.server.closeAllConnections();
        await new Promise(resolve=>fixtureState.server.close(resolve));
        await rm(fixtureState.temporary,{recursive:true,force:true});
        if(!page.isClosed())await page.close();
      }
    }
  }finally{
    await browser.close();
  }
});

test('unknown reopened run tracks verified clarification through same-run completion',{timeout:60000},async()=>{
  const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',
    headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const fixtureState=await fixture('single',{
    initialRunStatus:'unknown',rehydrateStatus:'waiting_for_clarification',
    holdRehydrate:true,completeAfterAnswer:true,
  });
  try{
    const page=await browser.newPage({viewport:{width:320,height:640},serviceWorkers:'block'});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(fixtureState.url);
    await page.getByRole('button',{name:'Clarification fixture'}).click();
    await fixtureState.rehydrateStarted;
    fixtureState.releaseRehydrate();
    const card=page.locator('.clarification-card');
    await card.waitFor();
    await card.getByLabel('Change it').check();
    await card.getByRole('button',{name:'Submit answer'}).click();
    assert.equal(await fixtureState.waiter,'Change it');
    const completion=await fixtureState.completed;
    assert.equal(completion.status,'completed');
    assert.equal(fixtureState.streams.length,1);
    await page.waitForFunction(()=>
      document.querySelector('.live-activity-heading [role=status]')?.textContent==='completed');
    assert.match(await page.locator('.live-message .message-body').textContent(),
      /Native continuation completed: Change it/);
    assert.equal(fixtureState.answerCalls.length,1);
    assert.deepEqual(fixtureState.answerCalls,[{answer:'Change it',other:false}]);
    assert.deepEqual(fixtureState.runCalls,[]);
    assert.deepEqual(errors,[]);
    await page.close();
  }finally{
    fixtureState.server.closeAllConnections();
    await new Promise(resolve=>fixtureState.server.close(resolve));
    await rm(fixtureState.temporary,{recursive:true,force:true});
    await browser.close();
  }
});

test('late clarification reconciliation cannot reconnect after navigation or teardown',{timeout:60000},async()=>{
  const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',
    headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  try{
    for(const exit of ['navigation','teardown']){
      const fixtureState=await fixture('single',{
        initialRunStatus:'unknown',rehydrateStatus:'waiting_for_clarification',
        holdRehydrate:true,
      });
      const page=await browser.newPage({viewport:{width:320,height:640},serviceWorkers:'block'});
      try{
        await page.goto(fixtureState.url);
        await page.getByRole('button',{name:'Clarification fixture'}).click();
        await fixtureState.rehydrateStarted;
        if(exit==='navigation')await page.getByRole('button',{name:'Back to chats'}).click();
        else await page.close();
        fixtureState.releaseRehydrate();
        if(exit==='navigation'){
          await page.waitForTimeout(50);
          assert.equal(await page.locator('.clarification-card').count(),0);
        }else await new Promise(resolve=>setTimeout(resolve,50));
        assert.equal(fixtureState.streams.length,0);
        assert.equal(fixtureState.answerCalls.length,0);
        assert.deepEqual(fixtureState.runCalls,[]);
      }finally{
        fixtureState.server.closeAllConnections();
        await new Promise(resolve=>fixtureState.server.close(resolve));
        await rm(fixtureState.temporary,{recursive:true,force:true});
        if(!page.isClosed())await page.close();
      }
    }
  }finally{
    await browser.close();
  }
});

test('terminal unknown remains non-live after clarification rehydration',{timeout:60000},async()=>{
  const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',
    headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const fixtureState=await fixture('single',{
    initialRunStatus:'unknown',rehydrateStatus:'unknown',itemStatus:'unknown',
  });
  try{
    const page=await browser.newPage({viewport:{width:320,height:640},serviceWorkers:'block'});
    await page.goto(fixtureState.url);
    await page.getByRole('button',{name:'Clarification fixture'}).click();
    await fixtureState.rehydrateStarted;
    await page.locator('.clarification-card').waitFor();
    assert.equal(await page.locator('.clarification-card .clarification-form').count(),0);
    assert.equal(fixtureState.streams.length,0);
    assert.equal(fixtureState.answerCalls.length,0);
    assert.deepEqual(fixtureState.runCalls,[]);
    await page.close();
  }finally{
    fixtureState.server.closeAllConnections();
    await new Promise(resolve=>fixtureState.server.close(resolve));
    await rm(fixtureState.temporary,{recursive:true,force:true});
    await browser.close();
  }
});

test('real browser submits literal __other__ choices separately from dedicated Other',{timeout:60000},async()=>{
  const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',
    headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  try{
    for(const mode of ['single','multi']){
      for(const selected of ['literal','other']){
        const fixtureState=await fixture(`${mode}-sentinel`);
        try{
          const page=await browser.newPage({viewport:{width:320,height:640},serviceWorkers:'block'});
          await page.goto(fixtureState.url);
          await page.getByRole('button',{name:'Clarification fixture'}).click();
          const card=page.locator('.clarification-card');
          await card.waitFor();
          assert.equal(await card.locator('input:checked').count(),0);
          if(selected==='literal'){
            await card.locator('label.clarification-option').first().locator('input').check();
          }else{
            await card.locator('label.clarification-option').last()
              .locator('input[type=checkbox],input[type=radio]').check();
            await card.getByLabel('Other answer').fill('A typed answer');
          }
          await card.getByRole('button',{name:'Submit answer'}).click();
          const expected=selected==='literal'
            ?(mode==='multi'?['__other__']:'__other__')
            :(mode==='multi'?['A typed answer']:'A typed answer');
          await page.waitForTimeout(10);
          assert.equal(fixtureState.answerCalls.length,1,
            'the selected literal or explicitly selected Other value reaches the same waiter');
          assert.deepEqual(await fixtureState.waiter,expected);
          assert.deepEqual(fixtureState.answerCalls,[{answer:expected,other:selected==='other'}]);
          assert.deepEqual(fixtureState.runCalls,[]);
          await page.close();
        }finally{
          fixtureState.server.closeAllConnections();
          await new Promise(resolve=>fixtureState.server.close(resolve));
          await rm(fixtureState.temporary,{recursive:true,force:true});
        }
      }
    }
  }finally{await browser.close();}
});

test('late answer acknowledgement cannot regress authoritative run state',{timeout:60000},async()=>{
  const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',
    headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  try{
    for(const scenario of ['completed','stopping','new-question']){
      const fixtureState=await fixture('single',{holdAnswer:true});
      try{
        const page=await browser.newPage({viewport:{width:320,height:640},serviceWorkers:'block'});
        await page.goto(fixtureState.url);
        await page.getByRole('button',{name:'Clarification fixture'}).click();
        const card=page.locator('.clarification-card').first();
        await card.waitFor();await fixtureState.streamReady;
        await card.getByLabel('Change it').check();
        await card.getByRole('button',{name:'Submit answer'}).click();
        await fixtureState.answerSubmitted;
        if(scenario==='completed')
          fixtureState.publish('done',{status:'completed'});
        else if(scenario==='stopping')
          fixtureState.publish('status',{status:'stopping'});
        else fixtureState.publish('clarification',{
          ...fixtureState.item,question_id:'b'.repeat(32),
          question:'Choose another plan?',status:'pending',created_at:4,updated_at:4,
        });
        const expected=scenario==='new-question'?'waiting_for_clarification':scenario;
        await page.waitForFunction(status=>
          document.querySelector('.live-activity-heading [role=status]')?.textContent===status,expected);
        fixtureState.releaseAck();
        await card.getByText('Answered').waitFor();
        await page.waitForTimeout(10);
        assert.equal(await page.locator('.live-activity-heading [role=status]').textContent(),expected);
        if(scenario==='new-question')
          await page.locator(`[data-clarification-id="${'b'.repeat(32)}"] .clarification-form`).waitFor();
        assert.equal(fixtureState.answerCalls.length,1);
        assert.deepEqual(fixtureState.runCalls,[]);
        await page.close();
      }finally{
        fixtureState.server.closeAllConnections();
        await new Promise(resolve=>fixtureState.server.close(resolve));
        await rm(fixtureState.temporary,{recursive:true,force:true});
      }
    }
  }finally{await browser.close();}
});

test('real browser retains multi-select draft after backend validation rejection',{timeout:60000},async()=>{
  const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',
    headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const fixtureState=await fixture('multi',{rejectDuplicate:true});
  try{
    const page=await browser.newPage({viewport:{width:320,height:640},serviceWorkers:'block'});
    await page.goto(fixtureState.url);
    await page.getByRole('button',{name:'Clarification fixture'}).click();
    const card=page.locator('.clarification-card');
    await card.waitFor();
    const boxes=card.locator('input[type=checkbox]');
    await boxes.nth(0).check();await boxes.nth(2).check();
    const other=card.getByLabel('Other answer');
    await other.fill('Keep current');
    await card.getByRole('button',{name:'Submit answer'}).click();
    await card.getByText(/correct it here/i).waitFor();
    assert.equal(fixtureState.answerCalls.length,1);
    assert.equal(fixtureState.item.status,'pending');
    assert.equal(await card.locator('.clarification-form').count(),1);
    assert.equal(await boxes.nth(0).isChecked(),true);
    assert.equal(await boxes.nth(2).isChecked(),true);
    assert.equal(await other.inputValue(),'Keep current');
    await page.getByRole('button',{name:'Back to chats'}).click();
    await page.getByRole('button',{name:'Clarification fixture'}).click();
    await card.locator('.clarification-form').waitFor();
    assert.equal(fixtureState.answerCalls.length,1);
    assert.deepEqual(fixtureState.runCalls,[]);
    await boxes.nth(0).check();await boxes.nth(2).check();
    await other.fill('A different plan');
    await card.getByRole('button',{name:'Submit answer'}).click();
    assert.deepEqual(await fixtureState.waiter,['Keep current','A different plan']);
    assert.equal(fixtureState.answerCalls.length,2);
    assert.deepEqual(fixtureState.runCalls,[]);
    await page.close();
  }finally{
    fixtureState.server.closeAllConnections();
    await new Promise(resolve=>fixtureState.server.close(resolve));
    await rm(fixtureState.temporary,{recursive:true,force:true});
    await browser.close();
  }
});
