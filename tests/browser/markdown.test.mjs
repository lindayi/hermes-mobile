import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const {JSDOM}=createRequire('/usr/local/lib/hermes-agent/package.json')('jsdom');
test('Markdown formats code and safe links without interpreting HTML or unsafe schemes',async()=>{
  const {renderMarkdown}=await import('../../frontend/markdown.mjs').catch(()=>({}));
  assert.equal(typeof renderMarkdown,'function');
  const doc=new JSDOM('').window.document;
  const result=renderMarkdown(doc,'# Hello\n\n**Strong** and `code`\n\n[good](https://example.com) [bad](javascript:alert)\n\n```js\n<img src=x onerror=alert(1)>\n```\n\n<script>alert(1)</script>');
  assert.equal(result.querySelector('h2').textContent,'Hello');
  assert.equal(result.querySelector('strong').textContent,'Strong');
  assert.equal(result.querySelectorAll('a').length,1);
  assert.equal(result.querySelector('a').rel,'noopener noreferrer');
  assert.equal(result.querySelector('pre code').textContent,'<img src=x onerror=alert(1)>');
  assert.equal(result.querySelector('script,img'),null);
});
