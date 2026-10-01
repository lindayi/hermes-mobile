import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {execFileSync} from 'node:child_process';
import {mkdtemp,readFile,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {generatedAssets} from './generated-assets.mjs';
import {withoutValidatedPresence} from './push-fixture-contract.mjs';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const repo=fileURLToPath(new URL('../../',import.meta.url));
const python=process.env.HERMES_TEST_PYTHON || join(repo,'.venv/bin/python');

function nativeFixture(role){
 return JSON.parse(execFileSync(python,['-B','-c',`import json,sqlite3,tempfile,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_context_compression_presentation import PREFIXES,END
from backend.native_catalog import NativeCatalog
with tempfile.TemporaryDirectory() as directory:
 p=Path(directory); db=p/'state.db'
 summary=PREFIXES[0]+'\\nSynthetic compression body: retained context only.\\n\\n'+END
 human='[CONTEXT SUMMARY]:\\nA genuine human quotation.\\n'+END
 calls=json.dumps([{'id':'read-1','type':'function','function':{'name':'read_file','arguments':'{}'}}])
 originals=[('user','Opening human request',None,1,None),('assistant','',calls,2,None),('tool','Public file excerpt',None,3,'read-1'),('user','Archived middle request',None,4,None),('assistant','Archived middle answer',None,5,None),('user','Retained tail request',None,6,None),('assistant','Retained tail answer',None,7,None)]
 with sqlite3.connect(db) as c:
  c.executescript('CREATE TABLE sessions(id TEXT PRIMARY KEY); INSERT INTO sessions VALUES ("fixture"); CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT DEFAULT "fixture",role TEXT,content TEXT,tool_calls TEXT,timestamp REAL,tool_call_id TEXT,tool_name TEXT,active INTEGER,compacted INTEGER,display_kind TEXT,display_metadata TEXT,platform_message_id TEXT,reasoning_content TEXT DEFAULT "PRIVATE SIDECAR");')
  def add(i,row,active,compacted,platform=None):
   r,content,tc,stamp,call_id=row
   c.execute('INSERT INTO messages(id,role,content,tool_calls,timestamp,tool_call_id,active,compacted,platform_message_id) VALUES (?,?,?,?,?,?,?,?,?)',(i,r,content,tc,stamp,call_id,active,compacted,platform))
  for i,row in enumerate(originals,1):add(i,row,0,1)
  for i,row in enumerate(originals[:3],20):add(i,row,1,0)
  add(23,(sys.argv[1],summary,None,20,None),1,0)
  for i,row in enumerate(originals[5:],24):add(i,row,1,0)
  add(26,('user','New human request',None,21,None),1,0)
  add(27,('assistant','New final answer',None,22,None),1,0)
  add(28,('user',human,None,23,None),1,0,'human-message')
 before=db.read_bytes(); page=NativeCatalog({'default':p}).messages('default','fixture',limit=500,latest=True,turn_boundary=True)
 assert db.read_bytes()==before
 print(json.dumps({'page':page,'summary':summary,'human':human}))`,role],{cwd:repo,encoding:'utf8',timeout:15000,maxBuffer:2000000}));
}

for(const role of ['user','assistant'])test(`generated catalog compression after retained head is folded, never ${role} chat`,{timeout:30000},async()=>{
 const temporary=await mkdtemp(join(tmpdir(),'hmc-'));let server,browser;
 try{
  const assets=generatedAssets(temporary),fixture=nativeFixture(role),requests=[],errors=[];
  server=createServer(async(req,res)=>{
   try{
    const url=new URL(req.url,'http://fixture');
    const json=value=>{res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
    if(url.pathname.startsWith('/hermes/app-api/')){
     let raw='';for await(const chunk of req)raw+=chunk;
     const call={path:url.pathname,method:req.method,body:raw?JSON.parse(raw):null,csrf:req.headers['x-csrf-token']};requests.push(call);
     const path=url.pathname.slice('/hermes/app-api'.length);
     if(path==='/auth/me')return json({user:{id:'owner',role:'owner',status:'ready'},csrf_token:'fixture'});
     if(path==='/sessions')return json({items:[{id:'fixture',title:'Retained context fixture',source:'cli'}],total:1});
     if(path==='/sessions/fixture/messages')return json({...fixture.page,run:null});
     if(path==='/push/presence' && req.method==='POST'){withoutValidatedPresence([call]);return json({ok:true});}
     if(path==='/push/preferences')return json({revision:0,enabled:true,categories:{completion:true,approval:true,attention:true,scheduled:true,operational:true},hide_details:false});
     return json({items:[]});
    }
    if(url.pathname==='/favicon.ico'){res.writeHead(204).end();return;}
    const relative=url.pathname==='/hermes/'?'index.html':url.pathname.slice('/hermes/'.length);
    if(!url.pathname.startsWith('/hermes/') || relative.split('/').includes('..')){res.writeHead(404).end();return;}
    const body=await readFile(join(assets,relative));res.writeHead(200,{'Content-Type':({html:'text/html',css:'text/css',js:'text/javascript',mjs:'text/javascript',svg:'image/svg+xml',png:'image/png',webmanifest:'application/manifest+json'})[relative.split('.').pop()] || 'application/octet-stream'});res.end(body);
   }catch(error){errors.push(error.message);res.writeHead(500).end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER || '/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true,serviceWorkers:'block'});page.setDefaultTimeout(5000);page.on('pageerror',error=>errors.push(error.message));
  const open=async()=>{await page.goto(`http://127.0.0.1:${server.address().port}/hermes/`);await page.getByRole('button',{name:'Retained context fixture',exact:true}).click();await page.getByRole('textbox',{name:'Message Hermes'}).waitFor();};
  await open();
  const card=page.locator('details.context-compression');
  assert.equal(await card.count(),1,'real NativeCatalog projection must classify the retained-head envelope');
  assert.equal(await card.evaluate(el=>el.open),false);assert.equal(await card.locator('.markdown').isVisible(),false);
  assert.equal(await card.locator('.message-author').count(),0,'process scaffolding has no You or Hermes author');
  assert.equal(await page.locator('.user-message,.assistant-message').filter({hasText:'Synthetic compression body:'}).count(),0);
  assert.equal(await page.locator('.user-message').count(),5,'genuine human turns are retained once');
  assert.equal(await page.locator('.user-message').filter({hasText:'A genuine human quotation.'}).count(),1);
  assert.equal(await page.locator('.tool-preview').count(),1,'retained call/result pairing survives classification');
  await card.locator('summary').click();assert.equal(await card.locator('.markdown').textContent().then(text=>text.includes('Synthetic compression body:')),true);
  assert.doesNotMatch(await page.locator('.messages').textContent(),/PRIVATE SIDECAR/);
  for(const width of [320,768]){await page.setViewportSize({width,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);}
  await open();assert.equal(await page.locator('details.context-compression').evaluate(el=>el.open),false,'reload remains classified and folded');
  if(process.env.HERMES_LAYOUT_PROOF_DIR){await mkdir(process.env.HERMES_LAYOUT_PROOF_DIR,{recursive:true,mode:0o700});await page.setViewportSize({width:390,height:844});await page.locator('details.context-compression > summary').scrollIntoViewIfNeeded();await page.screenshot({path:join(process.env.HERMES_LAYOUT_PROOF_DIR,`compression-${role}.png`)});}
  assert.deepEqual(withoutValidatedPresence(requests).filter(call=>call.method!=='GET'),[]);assert.deepEqual(errors,[]);
 }finally{await browser?.close();if(server){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}await rm(temporary,{recursive:true,force:true});}
});
