import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';

test('PWA worker caches public shell assets only and clamps notification navigation to Hermes inbox',async()=>{
  const source=await readFile(new URL('../../frontend/sw.js',import.meta.url),'utf8').catch(()=>null);
  assert.ok(source,'service worker implemented');
  const handlers={},cached=[],notifications=[],opened=[];
  const self={location:{origin:'https://lindayi.me'},addEventListener:(name,fn)=>handlers[name]=fn,skipWaiting:async()=>{},clients:{claim:async()=>{},matchAll:async()=>[],openWindow:async url=>opened.push(url)},registration:{showNotification:async(title,options)=>notifications.push({title,options})}};
  vm.runInNewContext(source,{self,URL,Response,fetch:async()=>new Response('public asset'),caches:{open:async()=>({addAll:async urls=>cached.push(...urls),match:async()=>new Response('cached public asset'),put:async()=>{}}),keys:async()=>[],delete:async()=>{}}});
  let done;handlers.install({waitUntil:p=>done=p});await done;
  assert.ok(cached.includes('/hermes/index.html'));
  assert.equal(cached.some(url=>url.includes('app-api')),false);
  for(const url of ['https://lindayi.me/hermes/app-api/auth/me','https://lindayi.me/hermes/app-api/sessions','https://evil.test/hermes/app.js','https://lindayi.me/hermes/private.txt']){
    let intercepted=false;handlers.fetch({request:{url,method:'GET',mode:'cors'},respondWith:()=>intercepted=true});assert.equal(intercepted,false,url);
  }
  handlers.push({data:{json:()=>({title:'Secret',body:'Private content',url:'https://evil.test/'})},waitUntil:p=>done=p});await done;
  assert.equal(notifications[0].options.body,'Open Hermes to read your update.');
  handlers.notificationclick({notification:{data:{url:'https://evil.test/'},close(){}},waitUntil:p=>done=p});await done;
  assert.match(opened[0],/^https:\/\/lindayi.me\/hermes\//);
});

for(const dependency of ['/hermes/tool-details.mjs','/hermes/model-controls.mjs'])test(`public worker precaches ${dependency} and serves it anonymously online and offline`,async()=>{
 const source=await readFile(new URL('../../frontend/sw.js',import.meta.url),'utf8');
 const handlers={},stored=new Map(),requests=[];let offline=false;
 const cache={addAll:async urls=>{for(const url of urls)stored.set(url,'public '+url);},match:async url=>stored.has(url)?new Response(stored.get(url)):undefined};
 const self={location:{origin:'https://example.com'},addEventListener:(name,fn)=>handlers[name]=fn};
 vm.runInNewContext(source,{self,URL,Response,caches:{open:async()=>cache},fetch:async(path,options)=>{requests.push({path,options});if(offline)throw new Error('offline');return new Response('online public module');}});
 let done;handlers.install({waitUntil:p=>done=p});await done;
 assert.ok(stored.has(dependency),'the ui.mjs dependency belongs to the precached public shell');
 for(const isOffline of [false,true]){offline=isOffline;let response;handlers.fetch({request:{url:'https://example.com'+dependency,method:'GET',mode:'cors'},respondWith:p=>response=p});assert.ok(response,'module handled');assert.equal(await (await response).text(),isOffline?'public '+dependency:'online public module');}
 assert.ok(requests.every(({options})=>options.credentials==='omit'),'public imports never send credentials');
 for(const path of ['/hermes/app-api/sessions','/hermes/app-api/auth/me',dependency+'?private=1']){let intercepted=false;handlers.fetch({request:{url:'https://example.com'+path,method:'GET',mode:'cors'},respondWith:()=>intercepted=true});assert.equal(intercepted,false);}
});

test('manifest identifies the /hermes scope and install icons',async()=>{
  const manifest=JSON.parse(await readFile(new URL('../../frontend/manifest.webmanifest',import.meta.url),'utf8').catch(()=>'{}'));
  assert.equal(manifest.scope,'/hermes/');assert.equal(manifest.start_url,'/hermes/');
  assert.equal(manifest.display,'standalone');
  assert.ok(manifest.icons.some(icon=>icon.sizes==='192x192'));
  assert.ok(manifest.icons.some(icon=>icon.sizes==='512x512'));
});
