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
