import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, readFile, rm, access} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';
import {generatedAssets} from './generated-assets.mjs';

function preserve(t) {
  const before={...process.env};
  t.after(()=>{for(const key of ['HERMES_FRONTEND_DIR','HERMES_TEST_PYTHON']){
    if(before[key]===undefined)delete process.env[key];else process.env[key]=before[key];
  }});
}

test('generated fixture uses exact supplied assets without invoking any interpreter',async t=>{
  preserve(t);
  const temp=await mkdtemp(join(tmpdir(),'hga-'));t.after(()=>rm(temp,{recursive:true,force:true}));
  process.env.HERMES_FRONTEND_DIR=temp;
  process.env.HERMES_TEST_PYTHON='/missing/interpreter';
  assert.equal(generatedAssets(join(temp,'unused')),temp);
  await assert.rejects(access(join(temp,'unused')));
});

test('generated fixture builds default source into disposable storage without a source virtualenv',async t=>{
  preserve(t);
  const temp=await mkdtemp(join(tmpdir(),'hga-'));t.after(()=>rm(temp,{recursive:true,force:true}));
  process.env.HERMES_FRONTEND_DIR=fileURLToPath(new URL('../../frontend',import.meta.url));
  // The managed runner supplies the working interpreter, including no-venv stages.
  assert.ok(process.env.HERMES_TEST_PYTHON);
  const assets=generatedAssets(temp);
  assert.equal(assets,join(temp,'public'));
  assert.match(await readFile(join(assets,'index.html'),'utf8'),/app\.[a-f0-9]+\.js/);
});
