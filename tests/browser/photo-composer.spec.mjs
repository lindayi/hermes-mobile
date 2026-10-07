import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const frontend=process.env.HERMES_FRONTEND_DIR
  ? process.env.HERMES_FRONTEND_DIR.replace(/\/$/,'')+'/'
  : fileURLToPath(new URL('../../frontend/',import.meta.url));

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
