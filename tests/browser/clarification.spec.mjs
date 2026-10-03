import assert from 'node:assert/strict';
import test from 'node:test';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {createRequire} from 'node:module';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const root=new URL('../../frontend/',import.meta.url);

async function fixture(shape) {
  const answerCalls=[],runCalls=[];
  const item={question_id:'a'.repeat(32),run_id:'r',session_id:'s',
    question:'Choose safely?',choices:shape==='open'?null:['Keep current','Change it'],
    multi_select:shape==='multi',status:'pending',answer:null,other:null,
    created_at:1,updated_at:2};
  let resolveWaiter;
  const waiter=new Promise(resolve=>{resolveWaiter=resolve;});
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
          if(item.status!=='pending')return json({error:'conflict'},409);
          item.status='answered';item.answer=value.answer;item.other=value.other;item.updated_at=3;
          resolveWaiter(value.answer);
          return json({question_id:item.question_id,run_id:'r',status:'answered',answer:value.answer});
        }
      }
      if(path==='/auth/me')return json({user:{id:'owner',status:'ready'}});
      if(path==='/sessions')return json({items:[{id:'s',title:'Clarification fixture'}],total:1});
      if(path.endsWith('/messages'))return json({items:[],run:{id:'r',session_id:'s',status:'running',input:'Original'}});
      if(path.endsWith('/clarifications'))return json({available:true,items:[item]});
      if(path.endsWith('/controls'))return json({steering:false,attempts:[]});
      if(path.endsWith('/events')){response.writeHead(200,{'Content-Type':'text/event-stream'});response.write(': ready\n\n');return;}
      return json({items:[]});
    }
    const relative=url.pathname.replace(/^\/hermes\//,'')||'index.html';
    try{
      const content=await readFile(join(root.pathname,relative));
      const extension=relative.split('.').at(-1);
      response.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',js:'text/javascript',mjs:'text/javascript'})[extension]||'application/octet-stream'});
      response.end(content);
    }catch{response.writeHead(404).end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  return {server,item,answerCalls,runCalls,waiter,
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
      }
    }
  }finally{await browser.close();}
});
