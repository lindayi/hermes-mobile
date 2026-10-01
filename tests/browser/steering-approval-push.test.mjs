import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mountApp} from '../../frontend/ui.mjs';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
for(const pending of [true,false])test(`approval push opens revalidated review, never approves (${pending?'pending':'resolved'})`,async()=>{
 const dom=new JSDOM('<div id="app"></div>',{url:'https://fixture.test/hermes/?inbox=n'}),win=dom.window,doc=win.document,calls=[];let scrolled;
 win.HTMLElement.prototype.scrollIntoView=function(){scrolled=this;};
 const app=await mountApp(doc,{clear(){},async request(p,o={}){calls.push({p,o});if(p==='/auth/me')return {user:{id:'owner',status:'ready'}};if(p==='/inbox')return {items:[{id:'n',title:'Approval',body:'Review required',session_id:'s',approval_id:'a',run_id:'r',request_id:'native-a'}]};if(p==='/approvals')return {items:pending?[{id:'a',run_id:'r',request_id:'native-a',title:'Execute fixture',action:'echo fixture'}]:[]};return {items:[]};}},win);
 try{assert.equal(calls.filter(c=>c.o.method==='POST').length,0);assert.ok(calls.some(c=>c.p==='/approvals'));assert.equal(doc.querySelector('.conversation'),null);if(pending){assert.ok(scrolled?.matches('.approval'),'push must target exact live approval card');assert.ok([...doc.querySelectorAll('button')].some(b=>b.textContent==='Review approval'));}else{assert.equal(doc.querySelector('.approval'),null);assert.match(doc.querySelector('.inbox-item').textContent,/no longer pending/i);}assert.equal([...doc.querySelectorAll('.inbox-item button')].some(b=>b.textContent==='Open conversation'),false);}finally{app.destroy();win.close();}
});
