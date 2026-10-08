import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';

const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const frontend=process.env.HERMES_FRONTEND_DIR
  ? process.env.HERMES_FRONTEND_DIR.replace(/\/$/,'')+'/'
  : fileURLToPath(new URL('../../frontend/',import.meta.url));

async function rollbackRoute(){
 const root=fileURLToPath(new URL('../../',import.meta.url));
 const child=spawn(process.env.HERMES_TEST_PYTHON||root+'.venv/bin/python',['-B','-c',String.raw`
import base64, json, sqlite3, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'tests'))
import httpx
from backend.hermes_client import GatewayClient
from test_photo_attachments import photo_client, png_fixture
from test_auth import BASE
native_posts=[]
async def upstream(request):
    if request.url.path.endswith('/messages'):
        return httpx.Response(200,json={'session_id':'photo-session','requested_session_id':'photo-session','data':[]})
    if request.url.path=='/v1/capabilities':
        return httpx.Response(200,json={'mobile_photos':{'version':1,'max_images':4,'max_image_bytes':2097152,'max_request_bytes':20000000,'private_persistence':True}})
    if request.url.path=='/v1/runs':
        native_posts.append(json.loads(request.content))
        return httpx.Response(202,json={'run_id':f'native-{len(native_posts)}'})
    if request.url.path.endswith('/events'):
        rid=request.url.path.split('/')[-2]
        return httpx.Response(200,text='event: run.completed\ndata: '+json.dumps({'run_id':rid,'output':'Synthetic answer'})+'\n\n')
    return httpx.Response(404)
gateway=GatewayClient('http://127.0.0.1:8642','synthetic-test-token',execution_ready=True,transport=httpx.MockTransport(upstream))
with tempfile.TemporaryDirectory(prefix='photo-browser-route-') as temporary:
    with photo_client(Path(temporary),gateway_client=gateway) as (app,client):
        with sqlite3.connect(app.state.catalog.profiles['default'] / 'state.db') as db:
            db.execute("UPDATE sessions SET id='photo-session',title='Photo repair' WHERE id='wa-1'")
            db.execute("UPDATE messages SET session_id='photo-session' WHERE session_id='wa-1'")
        old=client.post(BASE+'/sessions/photo-session/attachments',content=png_fixture(),headers={'Idempotency-Key':'old-photo'}).json()['id']
        original={'session_id':'photo-session','input':'Old accepted photo','idempotency_key':'old-accepted','attachments':[old]}
        accepted=client.post(BASE+'/runs',json=original)
        assert accepted.status_code==200,accepted.text
        for _ in range(100):
            if client.get(BASE+'/runs/'+accepted.json()['id']).json()['status']=='completed':break
            time.sleep(.01)
        assert client.get(BASE+'/runs/'+accepted.json()['id']).json()['status']=='completed'
        print(json.dumps({'owner':client.get(BASE+'/auth/me').json()['user']['id']}),flush=True)
        for line in sys.stdin:
            request=json.loads(line)
            kwargs={}
            if request.get('raw') is not None:kwargs['content']=base64.b64decode(request['raw'])
            if request.get('body') is not None:kwargs['json']=request['body']
            response=client.request(request['method'],BASE+request['path'],headers=request.get('headers',{}),**kwargs)
            if request['method']=='POST' and request['path'].endswith('/attachments'):
                assert response.status_code==201,response.text
                app.state.settings.photos_enabled=False
                app.state.orchestrator.photos_enabled=False
            result={'status':response.status_code}
            if 'application/json' in response.headers.get('content-type',''):
                result['body']=response.json()
            else:
                result.update(raw=base64.b64encode(response.content).decode(),content_type=response.headers.get('content-type'))
            print(json.dumps(result),flush=True)
        retry=client.post(BASE+'/runs',json=original)
        assert retry.status_code==200 and retry.json()['id']==accepted.json()['id']
        assert len(native_posts)==2,native_posts
        assert client.get(BASE+'/sessions/photo-session/attachments/'+old).status_code==200
`],{cwd:root,stdio:['pipe','pipe','pipe']});
 const reader=createInterface({input:child.stdout}),lines=reader[Symbol.asyncIterator]();
 let diagnostic='';child.stderr.on('data',chunk=>{diagnostic+=chunk;});
 const closed=new Promise(resolve=>child.once('close',code=>resolve(code)));
 const next=async()=>{const line=await lines.next();assert.equal(line.done,false,diagnostic);return JSON.parse(line.value);};
 const startup=await next();let queue=Promise.resolve();
 return {owner:startup.owner,request:value=>{const result=queue.then(()=>{child.stdin.write(JSON.stringify(value)+'\n');return next();});queue=result.catch(()=>{});return result;},
  close:async()=>{await queue;child.stdin.end();const code=await closed;reader.close();assert.equal(code,0,diagnostic);}};
}

test('real file input removal aborts an undispatched photo batch and retries remaining photo',async t=>{
  const calls=[],uploads=[],runs=[],deletes=[];
  let finishSecond,notifySecond;
  const secondStarted=new Promise(resolve=>{notifySecond=resolve;});
  const photoBytes=Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jXioAAAAASUVORK5CYII=',
    'base64');
  const json=(res,value,status=200)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};
  const server=createServer(async(req,res)=>{
    const url=new URL(req.url,'http://localhost'),path=url.pathname.replace('/hermes/app-api','');
    if(url.pathname.startsWith('/hermes/app-api')){
      calls.push(`${req.method} ${path}`);
      if(path==='/auth/me')return json(res,{user:{id:'photo-owner',status:'ready'},csrf_token:'fixture'});
      if(path==='/sessions')return json(res,{items:[{id:'photo-session',title:'Photo fixture'}],total:1});
      if(path.endsWith('/messages'))return json(res,{items:[]});
      if(path==='/sessions/photo-session/attachments' && req.method==='POST'){
        const data=[];for await(const chunk of req)data.push(chunk);
        uploads.push(Buffer.concat(data));
        const id=String(uploads.length).padStart(32,'0');
        const metadata={id,status:'pending',url:`/sessions/photo-session/attachments/${id}`};
        if(uploads.length===2){
          notifySecond();
          finishSecond=()=>json(res,metadata,201);
          return;
        }
        return json(res,metadata,201);
      }
      if(req.method==='DELETE'){deletes.push(path);return json(res,{released:true});}
      if(path==='/runs' && req.method==='POST'){
        const body=JSON.parse(await new Promise(resolve=>{let data='';req.setEncoding('utf8');req.on('data',chunk=>data+=chunk);req.on('end',()=>resolve(data));}));
        runs.push(body);
        return json(res,{id:'photo-run',session_id:'photo-session',status:'completed'});
      }
      if(path==='/runs/photo-run')return json(res,{id:'photo-run',session_id:'photo-session',status:'completed'});
      return json(res,{items:[]});
    }
    const resource=url.pathname.replace(/^\/hermes\//,'')||'index.html';
    if(resource.includes('..'))return res.writeHead(400).end();
    try{
      const body=await readFile(frontend+resource);
      const ext=resource.split('.').pop();
      res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',js:'text/javascript',mjs:'text/javascript',svg:'image/svg+xml'})[ext]||'application/octet-stream'});
      res.end(body);
    }catch{return res.writeHead(404).end();}
  });

  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',
    headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  t.after(async()=>{await browser.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));});
  const page=await browser.newPage({viewport:{width:390,height:844}});
  page.setDefaultTimeout(5000);
  await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);
  await page.getByRole('button',{name:'Photo fixture'}).click();
  await page.locator('input[type=file]').setInputFiles([
    {name:'first.png',mimeType:'image/png',buffer:photoBytes},
    {name:'second.png',mimeType:'image/png',buffer:photoBytes},
  ]);
  await page.getByRole('button',{name:'Send message'}).click();
  await secondStarted;
  await page.getByRole('button',{name:'Remove photo 1'}).click();
  finishSecond();
  await page.getByText(/Photo selection changed before sending/).waitFor();
  assert.equal(runs.length,0,'removed IDs must not reach native run admission');
  assert.deepEqual(deletes,['/sessions/photo-session/attachments/00000000000000000000000000000001']);
  assert.equal(await page.locator('.photo-preview').count(),1);
  await page.getByRole('button',{name:'Send message'}).click();
  await page.waitForFunction(()=>document.querySelector('.photo-status')?.hidden===true);
  assert.equal(uploads.length,2);
  assert.deepEqual(runs[0].attachments,['00000000000000000000000000000002']);
  assert.deepEqual(calls.filter(call=>call==='POST /runs').length,1);
});

  for(const scenario of ['disabled','cancel','legacy admitted','legacy lost response']){
   test(`composer recovers only a proven pre-admission photo attempt: ${scenario}`,async t=>{
    const runs=[],uploads=[],deletes=[];
    let finishUpload,lostResponse=scenario==='legacy lost response';
    const route=scenario==='disabled'?await rollbackRoute():null;
    const bytes=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAE0lEQVR4nGNQSFgARAwLEhSACAAfjgSBXOAMLAAAAABJRU5ErkJggg==','base64');
    const json=(res,value,status=200)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};
    const server=createServer(async(req,res)=>{
     const url=new URL(req.url,'http://localhost'),path=url.pathname.replace('/hermes/app-api','');
     if(url.pathname.startsWith('/hermes/app-api')){
      if(route && path.endsWith('/events')){res.writeHead(200,{'Content-Type':'text/event-stream'});return res.end(': fixture\n\n');}
      if(route && !path.endsWith('/model-options')){
       const data=[];for await(const chunk of req)data.push(chunk);
       const raw=Buffer.concat(data);
       if(req.method==='POST' && path.endsWith('/attachments'))uploads.push(raw);
       if(req.method==='DELETE')deletes.push(path);
       const body=path==='/runs' && req.method==='POST'?JSON.parse(raw.toString()):undefined;
       if(body)runs.push(body);
       const response=await route.request({method:req.method,path,body,
        ...(raw.length && !body?{raw:raw.toString('base64')}:{}),
        headers:req.headers['idempotency-key']?{'Idempotency-Key':req.headers['idempotency-key']}:{}});
       if(response.raw!==undefined){res.writeHead(response.status,{'Content-Type':response.content_type||'application/octet-stream'});return res.end(Buffer.from(response.raw,'base64'));}
       return json(res,response.body,response.status);
      }
      if(path==='/auth/me')return json(res,{user:{id:'photo-owner',status:'ready'},csrf_token:'fixture'});
      if(path==='/sessions')return json(res,{items:[{id:'photo-session',title:'Photo repair'}],total:1});
      if(path.endsWith('/messages'))return json(res,{items:[]});
      if(path.endsWith('/model-options'))return json(res,{available:true,default:{model:'vision',provider:'synthetic'},models:[{id:'vision',provider:'synthetic',label:'Synthetic',reasoning_efforts:[]}]});
      if(path.endsWith('/attachments') && req.method==='POST'){
       const data=[];for await(const chunk of req)data.push(chunk);uploads.push(Buffer.concat(data));
       finishUpload=()=>json(res,{id:'1'.repeat(32),status:'pending'},201);
       if(scenario==='cancel')return;
       return finishUpload();
      }
      if(req.method==='DELETE'){deletes.push(path);return json(res,{released:true});}
      if(path==='/runs' && req.method==='POST'){
       let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);runs.push(body);
       if(scenario==='disabled' && runs.length===1)return json(res,{detail:'Photos disabled',code:'photos_disabled_before_admission'},503);
       if(lostResponse)return res.destroy();
       return json(res,{id:'repair-run',session_id:'photo-session',status:'completed'});
      }
      if(path==='/runs/repair-run')return json(res,{id:'repair-run',session_id:'photo-session',status:'completed'});
      return json(res,{items:[]});
     }
     try{const resource=url.pathname.replace(/^\/hermes\//,'')||'index.html',body=await readFile(frontend+resource),ext=resource.split('.').pop();
      res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',mjs:'text/javascript',js:'text/javascript',svg:'image/svg+xml'})[ext]||'application/octet-stream'});res.end(body);
     }catch{res.writeHead(404).end();}
    });
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
    t.after(async()=>{await browser.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));await route?.close();});
    const page=await browser.newPage();page.setDefaultTimeout(5000);
    await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);
    const legacy={input:'Original text',idempotency_key:'legacy-original',selection:{model:'vision',provider:'synthetic'}};
    if(scenario.startsWith('legacy')){
     await page.evaluate(attempt=>sessionStorage.setItem('hermes:photo-owner:attempt:photo-session',JSON.stringify(attempt)),legacy);
     await page.reload();
    }
    await page.getByRole('button',{name:'Photo repair'}).click();
    const text=page.getByRole('textbox',{name:'Message Hermes',exact:true});
    await text.fill(scenario.startsWith('legacy')?'Original text':'New text');
    await page.getByRole('combobox',{name:'Model'}).waitFor();
    await page.locator('input[type=file]').setInputFiles({name:'synthetic.png',mimeType:'image/png',buffer:bytes});
    await page.getByRole('button',{name:'Send message'}).click();
    if(scenario.startsWith('legacy')){
     await page.getByText(/Retry the original text and photo selection unchanged/).waitFor();
     assert.equal(runs.length,0);assert.equal(uploads.length,0);
     await page.getByRole('button',{name:'Remove photo 1'}).click();
     await page.getByRole('button',{name:'Send message'}).click();
     if(scenario==='legacy lost response'){
      await page.getByText(/The send outcome is uncertain/).waitFor();
      lostResponse=false;
      await page.reload();await page.getByRole('button',{name:'Photo repair'}).click();
      await page.getByRole('button',{name:'Send message'}).click();
     }
     await page.waitForFunction(()=>document.querySelector('.photo-status')?.hidden===true);
     for(const run of runs)assert.deepEqual(run,{session_id:'photo-session',...legacy});
     await text.fill('New photo request');
     await page.locator('input[type=file]').setInputFiles({name:'new.png',mimeType:'image/png',buffer:bytes});
     await page.getByRole('button',{name:'Send message'}).click();
     await page.waitForFunction(()=>document.querySelector('.photo-status')?.hidden===true);
     assert.notEqual(runs.at(-1).idempotency_key,legacy.idempotency_key);
     assert.deepEqual(runs.at(-1).attachments,['1'.repeat(32)]);
    }else{
     if(scenario==='cancel'){
      while(!finishUpload)await new Promise(resolve=>setTimeout(resolve,10));
      await page.getByRole('button',{name:'Remove photo 1'}).click();finishUpload();
      await page.waitForFunction(()=>document.querySelector('[aria-label="Send message"]')?.dataset.state==='idle');
      assert.equal(runs.length,0);
     }else{
      await page.getByText(/Photo or message was not sent/).waitFor();
      await page.getByRole('button',{name:'Remove photo 1'}).click();
     }
     await page.waitForFunction(()=>document.querySelector('[aria-label="Model"]')?.disabled===false);
     assert.equal(await page.evaluate(owner=>sessionStorage.getItem(`hermes:${owner}:attempt:photo-session`),route?.owner||'photo-owner'),null);
     await page.reload();await page.getByRole('button',{name:'Photo repair'}).click();
     assert.equal(await page.getByRole('combobox',{name:'Model'}).isDisabled(),false);
     await page.getByRole('button',{name:'Send message'}).click();
     await page.waitForFunction(()=>document.querySelector('.photo-status')?.hidden===true);
     assert.equal(runs.at(-1).attachments,undefined);
     assert.equal(deletes.length,1);
    }
   });
  }
for (const inputText of ['Describe both photos.','']) {
 test(`uncertain photo retry survives navigation and reload (${inputText?'text plus photos':'photos only'})`,{timeout:30000},async t=>{
   const uploads=[],runs=[],deletes=[],imageURLs=[];
   const photoBytes=[
     Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jXioAAAAASUVORK5CYII=','base64'),
     Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64'),
   ];
   const ids=[
     '00000000000000000000000000000011',
     '00000000000000000000000000000012',
   ];
   const json=(res,value,status=200)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};
   const server=createServer(async(req,res)=>{
     const url=new URL(req.url,'http://localhost'),path=url.pathname.replace('/hermes/app-api','');
     if(url.pathname.startsWith('/hermes/app-api')){
       if(req.method==='GET' && /\/sessions\/photo-session\/attachments\/[a-f0-9]{32}$/.test(path)){
         imageURLs.push(url.pathname);res.writeHead(200,{'Content-Type':'image/png'});res.end(photoBytes[ids.indexOf(path.split('/').at(-1))]||photoBytes[0]);return;
       }
       if(path==='/auth/me')return json(res,{user:{id:'photo-owner',status:'ready'},csrf_token:'fixture'});
       if(path==='/sessions')return json(res,{items:[{id:'photo-session',title:'Photo recovery'}],total:1});
       if(path.endsWith('/messages'))return json(res,{items:[]});
       if(path==='/sessions/photo-session/model-options')return json(res,{
         available:true,default:{model:'vision-model',provider:'synthetic'},
         models:[{id:'vision-model',provider:'synthetic',label:'Synthetic vision',reasoning_efforts:[]}]});
       if(path==='/sessions/photo-session/attachments' && req.method==='POST'){
         const data=[];for await(const chunk of req)data.push(chunk);
         uploads.push(Buffer.concat(data));
         const id=ids[uploads.length-1];
         return json(res,{id,status:'pending',url:`/sessions/photo-session/attachments/${id}`},201);
       }
       if(path.startsWith('/sessions/photo-session/attachments/') && req.method==='DELETE'){
         deletes.push(path);return json(res,{released:true});
       }
       if(path==='/runs' && req.method==='POST'){
         const body=JSON.parse(await new Promise(resolve=>{let data='';req.setEncoding('utf8');req.on('data',chunk=>data+=chunk);req.on('end',()=>resolve(data));}));
         runs.push(body);
         if(runs.length===1)return json(res,{detail:'Synthetic unknown outcome'},503);
         return json(res,{id:'recovered-run',session_id:'photo-session',status:'completed',attachments:[]});
       }
       if(path==='/runs/recovered-run')return json(res,{id:'recovered-run',session_id:'photo-session',status:'completed'});
       if(path.endsWith('/events')){res.writeHead(200,{'Content-Type':'text/event-stream'});res.end(': fixture\\n\\n');return;}
       return json(res,{items:[]});
     }
     const resource=url.pathname.replace(/^\/hermes\//,'')||'index.html';
     try{const body=await readFile(frontend+resource);const ext=resource.split('.').pop();res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',js:'text/javascript',mjs:'text/javascript',svg:'image/svg+xml'})[ext]||'application/octet-stream'});res.end(body);}
     catch{return res.writeHead(404).end();}
   });
   await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
   const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',
     headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
   t.after(async()=>{await browser.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));});
   const page=await browser.newPage({viewport:{width:390,height:844}});
   page.setDefaultTimeout(5000);
   await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);
   await page.getByRole('button',{name:'Photo recovery'}).click();
   const text=page.getByRole('textbox',{name:'Message Hermes',exact:true});
   await text.fill(inputText);
   const model=page.getByRole('combobox',{name:'Model'});
   await model.waitFor({state:'visible'});
   await model.selectOption('0');
   const bytes=[
     {name:'first.png',mimeType:'image/png',buffer:photoBytes[0]},
     {name:'second.png',mimeType:'image/png',buffer:photoBytes[1]},
   ];
   await page.locator('input[type=file]').setInputFiles(bytes);
   await page.getByRole('button',{name:'Send message'}).click();
   await page.getByText(/The send outcome is uncertain/).waitFor();
   const original=runs[0];
   assert.equal(original.input,inputText||'Please describe the attached image(s), including any visible text.');
   assert.deepEqual(original.attachments,ids);
   assert.deepEqual(original.selection,{model:'vision-model',provider:'synthetic'});
   assert.deepEqual(uploads,photoBytes);

   await page.getByRole('button',{name:'Back to chats'}).click();
   assert.deepEqual(deletes,[],'navigation preserves IDs for an uncertain admitted attempt');
   await page.getByRole('button',{name:'Photo recovery'}).click();
   await page.getByRole('button',{name:'Remove photo 1'}).waitFor();
   assert.equal(await page.locator('.photo-preview').count(),2);
   assert.equal(await page.getByRole('button',{name:'Remove photo 1'}).isDisabled(),true);
   assert.equal(await page.getByRole('button',{name:'Remove photo 2'}).isDisabled(),true);

   await page.reload();
   await page.getByRole('button',{name:'Photo recovery'}).click();
   await page.getByRole('button',{name:'Remove photo 1'}).waitFor();
   assert.equal(await page.locator('.photo-preview').count(),2);
   assert.equal(await page.locator('.photo-preview img').evaluateAll(images=>
     images.every(image=>image.src.startsWith(location.origin+'/hermes/app-api/sessions/photo-session/attachments/'))),true);
   assert.ok(imageURLs.length>=2);
   assert.ok(imageURLs.every(path=>path.startsWith('/hermes/app-api/sessions/photo-session/attachments/')));
   assert.equal(await page.getByRole('button',{name:'Remove photo 1'}).isDisabled(),true);
   assert.equal(await page.getByRole('button',{name:'Remove photo 2'}).isDisabled(),true);

   await page.getByRole('button',{name:'Back to chats'}).click();
   assert.deepEqual(deletes,[],'a second navigation still retains the unresolved IDs');
   await page.getByRole('button',{name:'Photo recovery'}).click();
   await page.getByRole('button',{name:'Remove photo 1'}).waitFor();
   assert.equal(await page.locator('.photo-preview').count(),2);
   assert.equal(await page.getByRole('button',{name:'Remove photo 1'}).isDisabled(),true);
   assert.equal(await page.getByRole('button',{name:'Remove photo 2'}).isDisabled(),true);

   const additional={name:'new.png',mimeType:'image/png',buffer:photoBytes[0]};
   await page.locator('input[type=file]').setInputFiles([additional]);
   assert.equal(await page.locator('.photo-preview').count(),3,'a new selection remains visible while the old request is locked');
   await page.getByRole('button',{name:'Send message'}).click();
   await page.getByText(/Retry the original text and photo selection unchanged/).waitFor();
   assert.equal(runs.length,1);
   await page.getByRole('button',{name:'Remove photo 3'}).click();
   await page.getByRole('button',{name:'Send message'}).click();
   await page.waitForFunction(()=>document.querySelector('.photo-status')?.hidden===true);
   assert.deepEqual(runs[1],original);
   assert.deepEqual(uploads,photoBytes,'retry uses server-side IDs and does not replace the original bytes');
   assert.deepEqual(deletes,[]);
 });
}
