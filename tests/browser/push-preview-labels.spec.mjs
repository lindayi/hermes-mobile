import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const handlers={},shown=[];
vm.runInNewContext(readFileSync(process.env.HERMES_TEST_SW || new URL('../../frontend/sw.js',import.meta.url),'utf8'),{
 self:{addEventListener:(name,fn)=>handlers[name]=fn,location:{origin:'https://fixture.test'},
 registration:{showNotification:async(title,options)=>shown.push({title,...options})}},URL,
});
async function push(data){let pending;handlers.push({data:{json:()=>({inbox_id:'fixture',title:'Public update',body:'Ready.',...data})},waitUntil:p=>pending=p});await pending;return shown.at(-1);}
const labels=['Minimalist: CAD105, in stock.','**Summary:** Freed200MB.','__Summary:__ Freed200MB.','## Summary: Freed200MB.','Reminder: meeting at 09:00.'];
const uris=['ftp://example.test/private','mailto:private@example.test','tel:+15555550100','data:text/plain,private','javascript:alert(1)','file:///private/report','urn:example:private','custom+scheme.v1-x:private','custom:*','custom:**','custom:_','custom:#','custom:~','**custom:private**','ＦＴＰ：//example.test/private'];
for(const scheme of ['http','https','ftp','ftps','mailto','tel','data','javascript','file','urn'])for(const spacing of ['',' '])uris.push(scheme+spacing+': private');
for(const field of ['title','body']){
 for(const value of labels)test(`informative ${field}: ${value}`,async()=>assert.equal((await push({[field]:value}))[field],value));
 for(const value of uris)test(`reject ${field}: ${value}`,async()=>{
  const result=await push({[field]:value});assert.equal(result.title,'Hermes');assert.equal(result.body,'Open Hermes to read your update.');
 });
}
// Keep every Unicode format-control rejection covered without a browser or network.
test('all Unicode Cf remain rejected',async()=>{
 for(let code=0;code<=0x10ffff;code++)if(/\p{Cf}/u.test(String.fromCodePoint(code))){
  assert.equal((await push({body:'Read'+String.fromCodePoint(code)+'hidden'})).title,'Hermes');
 }
});
