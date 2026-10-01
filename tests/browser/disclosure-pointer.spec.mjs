import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const dir=process.env.HERMES_FRONTEND_DIR || fileURLToPath(new URL('../../frontend/',import.meta.url));
const files=await readdir(dir);
const viewport=files.find(name=>/^viewport(?:\.[a-f0-9]+)?\.mjs$/.test(name));
assert.ok(viewport,'viewport module is present in the selected frontend tree');
const source=await readFile(`${dir}/${viewport}`,'utf8');

test('focus-only viewport update keeps disclosure under a held pointer',{timeout:15000},async()=>{
 const browser=await chromium.launch({executablePath:process.env.HERMES_BROWSER||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const page=await browser.newPage({viewport:{width:390,height:844}});page.setDefaultTimeout(5000);
  await page.setContent(`<style>
   .messages{height:200px;overflow:auto;overflow-anchor:none}
   .before{height:110px}.after{height:106px}
   summary{height:50px;list-style:none;display:flex;align-items:center}
   .disclosure-icon{display:inline-block}details[open]>summary .disclosure-icon{transform:rotate(90deg)}
  </style><div id="app"><div class="messages"><div class="before"></div><details><summary><span class="disclosure-icon">›</span>Subagent result</summary><p>Recorded result</p></details><div class="after"></div></div></div>`);
  await page.addScriptTag({type:'module',content:source+'\nwindow.disposeViewport=observeViewport(document.querySelector("#app"));'});
  const frames=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  await frames();
  // Like the first tool disclosure's reveal handler, leave a small real gap
  // below the reader. It is inside the observer's bottom-follow tolerance.
  await page.locator('.messages').evaluate(el=>el.scrollTop=0);await frames();
  assert.equal(await page.locator('.messages').evaluate(el=>el.scrollHeight-el.clientHeight-el.scrollTop),66);
  const arrow=page.locator('.disclosure-icon');assert.equal(await arrow.evaluate(el=>getComputedStyle(el).transform),'none','closed disclosure points right');
  const summary=page.locator('summary'),box=await summary.boundingBox();
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2);
  await page.mouse.down();
  // Deterministically exercise the scheduled focus update between down/up;
  // this is a race trigger, not a sleep or a retry that conceals the failure.
  await frames();
  const during=await page.locator('.messages').evaluate(el=>el.scrollTop);
  await page.mouse.up();
  assert.equal(await arrow.evaluate(el=>getComputedStyle(el).transform),'matrix(0, 1, -1, 0, 0, 0)','open disclosure points down');
  assert.equal(during,0,'focus without geometry changes must not move the pointer target');
  assert.equal(await page.locator('details').evaluate(el=>el.open),true);
  await page.evaluate(()=>window.disposeViewport());
 }finally{await browser.close();}
});
