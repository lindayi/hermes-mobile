import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {execFileSync} from 'node:child_process';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
const module=await import('../../frontend/viewport.mjs').catch(()=>({}));
function fixture({visual=true}={}) {
 const dom=new JSDOM('<div id="app"><textarea></textarea><input type="checkbox"><button>Button</button></div>',{pretendToBeVisual:true});
 const win=dom.window,root=win.document.getElementById('app');
 Object.defineProperties(win,{innerHeight:{value:844,writable:true},innerWidth:{value:390,writable:true}});
 const vv=Object.assign(new win.EventTarget(),{height:844,width:390,offsetTop:0,scale:1});
 if(visual)Object.defineProperty(win,'visualViewport',{value:vv});
 let frames=new Map(),next=0;
 win.requestAnimationFrame=fn=>{frames.set(++next,fn);return next;};win.cancelAnimationFrame=id=>frames.delete(id);
 const flush=()=>{const tasks=[...frames.values()];frames.clear();tasks.forEach(fn=>fn());};
 const change=(props,event='resize')=>{Object.assign(visual?vv:win,props);(visual?vv:win).dispatchEvent(new win.Event(event));};
 assert.equal(typeof module.observeViewport,'function','viewport observer is exported');
 const dispose=module.observeViewport(root);flush();
 return {win,root,vv,flush,change,dispose,dom,frames};
}
test('text keyboard compacts only on substantial unzoomed loss, restores and resets orientation',()=>{
 const {root,win,vv,change,flush,dispose,dom}=fixture();const text=root.querySelector('textarea');
 text.value='Draft stays';text.focus();text.setSelectionRange(2,5);flush();
 change({height:760});flush();assert.equal(root.dataset.keyboard,'closed','URL chrome is not a keyboard');
 change({height:430,offsetTop:40});flush();assert.equal(root.dataset.keyboard,'open');
 assert.equal(win.document.activeElement,text);assert.equal(text.value,'Draft stays');assert.equal(text.selectionStart,2);assert.equal(text.selectionEnd,5);
 root.querySelector('button').focus();flush();assert.equal(root.dataset.keyboard,'open','pointer focus on Expand must not shift it before click');text.focus();flush();
 change({height:280,scale:2,offsetTop:90});flush();assert.equal(root.dataset.keyboard,'open','pinch preserves the last unzoomed presentation');
 assert.equal(root.style.getPropertyValue('--app-viewport-height'),'430px','zoom does not squeeze unzoomed layout');
 assert.equal(root.style.getPropertyValue('--app-viewport-top'),'40px');
 change({height:844,scale:1,offsetTop:0});flush();assert.equal(root.dataset.keyboard,'closed');
 root.querySelector('input').focus();flush();change({height:400});flush();assert.equal(root.dataset.keyboard,'closed','checkbox is not a text keyboard');
 text.focus();flush();assert.equal(root.dataset.keyboard,'open');
 win.innerWidth=844;win.innerHeight=390;change({height:390,width:844});flush();assert.equal(root.dataset.keyboard,'closed','orientation resets tall baseline');
 change({height:190});flush();assert.equal(root.dataset.keyboard,'open');
 change({height:390});flush();assert.equal(root.dataset.keyboard,'closed');
 dispose();dom.window.close();
});
test('observer coalesces jitter, ignores transient zero and resumes after pagehide/pageshow; dispose is final',()=>{
 const {root,win,change,flush,dispose,dom,frames}=fixture();
 assert.equal(root.dataset.viewport,'managed');
 change({height:420.1,offsetTop:32.1});change({height:420.2,offsetTop:32.2});assert.equal(frames.size,1);flush();
 assert.equal(root.style.getPropertyValue('--app-viewport-height'),'420px');
 const before=root.getAttribute('style');change({height:420.3,offsetTop:32.3});flush();assert.equal(root.getAttribute('style'),before);
 change({height:0});flush();assert.equal(root.getAttribute('style'),before);
 change({height:600});win.dispatchEvent(new win.Event('pagehide'));assert.equal(frames.size,0);
 change({height:700});flush();assert.equal(root.getAttribute('style'),before);
 win.dispatchEvent(new win.Event('pageshow'));win.dispatchEvent(new win.Event('pageshow'));assert.equal(frames.size,1);flush();
 assert.equal(root.style.getPropertyValue('--app-viewport-height'),'700px');
 change({height:800});dispose();dispose();assert.equal(frames.size,0);assert.equal(root.style.getPropertyValue('--app-viewport-height'),'');assert.equal(root.dataset.viewport,undefined);assert.equal(root.dataset.keyboard,undefined);
 change({height:600});win.dispatchEvent(new win.Event('pageshow'));flush();assert.equal(root.style.getPropertyValue('--app-viewport-height'),'');
 dom.window.close();
});
test('viewport public module joins builder and versioned anonymous offline cache graph',async()=>{
 const source=await readFile(new URL('../../frontend/sw.js',import.meta.url),'utf8');
 const handlers={},stored=new Set(),credentials=[];let offline=false;
 vm.runInNewContext(source,{URL,Response,self:{location:{origin:'https://example.test'},addEventListener:(n,fn)=>handlers[n]=fn},caches:{open:async()=>({addAll:async urls=>urls.forEach(u=>stored.add(u)),match:async path=>stored.has(path)?new Response('cached viewport'):undefined})},fetch:async(path,options)=>{credentials.push(options.credentials);if(offline)throw Error('offline');return new Response('online viewport');}});
 let done;handlers.install({waitUntil:p=>done=p});await done;
 assert.ok(stored.has('/hermes/viewport.mjs'),'new entry dependency is precached');
 assert.doesNotMatch(source,/CACHE='hermes-public-v3'/,'source cache version advances');
 for(offline of [false,true]){let response;handlers.fetch({request:{method:'GET',url:'https://example.test/hermes/viewport.mjs'},respondWith:p=>response=p});assert.equal(await (await response).text(),offline?'cached viewport':'online viewport');}
 assert.deepEqual(credentials,['omit','omit']);
 for(const path of ['/hermes/app-api/auth/me','/hermes/viewport.mjs?private=1']){let intercepted=false;handlers.fetch({request:{method:'GET',url:'https://example.test'+path},respondWith:()=>intercepted=true});assert.equal(intercepted,false);}
 const names=execFileSync('python3',['-c',"from pathlib import Path; from deploy.assets import public_tree; print('\\n'.join(p.name for p in public_tree(Path('frontend'))))"],{cwd:new URL('../../',import.meta.url),encoding:'utf8'});
 assert.ok(names.split('\n').includes('viewport.mjs'));
});
test('visual viewport geometry follows height and offset; fallback follows innerHeight',()=>{
 for(const visual of [true,false]) {
  const f=fixture({visual});const {root,flush,change}=f;
  assert.equal(root.style.getPropertyValue('--app-viewport-height'),'844px');
  change(visual?{height:420,offsetTop:38}:{innerHeight:420});flush();
  assert.equal(root.style.getPropertyValue('--app-viewport-height'),'420px');
  assert.equal(root.style.getPropertyValue('--app-viewport-top'),visual?'38px':'0px');
  f.dispose();f.dom.window.close();
 }
});
