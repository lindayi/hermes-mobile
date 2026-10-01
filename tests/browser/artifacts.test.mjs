import test from 'node:test';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {resolve, sep, join} from 'node:path';
import {mkdtemp, mkdir, writeFile, readFile, readdir, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import * as helper from './artifacts.mjs';

test('artifactURL defaults to the browser artifacts directory with a trailing separator', t => {
  t.mock.method(process, 'cwd', () => '/tmp');
  const original = process.env.HERMES_TEST_ARTIFACT_DIR;
  delete process.env.HERMES_TEST_ARTIFACT_DIR;
  t.after(() => {
    if (original === undefined) delete process.env.HERMES_TEST_ARTIFACT_DIR;
    else process.env.HERMES_TEST_ARTIFACT_DIR = original;
  });
  assert.equal(typeof helper.artifactURL, 'function', 'artifactURL is exported');
  assert.equal(helper.artifactURL().href, new URL('./artifacts/', import.meta.url).href);
  assert.ok(fileURLToPath(helper.artifactURL()).endsWith('/'));
  assert.equal(helper.artifactURL('chat-mobile.png').href, new URL('./artifacts/chat-mobile.png', import.meta.url).href);
});

test('artifactURL honors the managed directory on each call and keeps directory URL semantics', t => {
  const original = process.env.HERMES_TEST_ARTIFACT_DIR;
  t.after(() => {
    if (original === undefined) delete process.env.HERMES_TEST_ARTIFACT_DIR;
    else process.env.HERMES_TEST_ARTIFACT_DIR = original;
  });
  for (const directory of ['/tmp/hermes shots #1?%/', '/tmp/hermes shots #1?%', 'relative-shots', '/']) {
    process.env.HERMES_TEST_ARTIFACT_DIR = directory;
    const expected = resolve(directory);
    assert.equal(fileURLToPath(helper.artifactURL()), expected.endsWith(sep) ? expected : expected + sep);
    assert.equal(fileURLToPath(helper.artifactURL('steering-retry-320-dark.png')), resolve(directory, 'steering-retry-320-dark.png'));
    assert.equal(helper.artifactURL().search, '');
    assert.equal(helper.artifactURL().hash, '');
  }
});

test('artifact URLs work with filesystem writers inside a fresh managed directory', async t => {
  const scratch = await mkdtemp(join(tmpdir(), 'hm-art-'));
  const original = process.env.HERMES_TEST_ARTIFACT_DIR;
  t.after(async () => {
    if (original === undefined) delete process.env.HERMES_TEST_ARTIFACT_DIR;
    else process.env.HERMES_TEST_ARTIFACT_DIR = original;
    await rm(scratch, {recursive: true, force: true});
  });
  process.env.HERMES_TEST_ARTIFACT_DIR = join(scratch, 'shots #1');
  await mkdir(helper.artifactURL(), {recursive: true});
  await writeFile(fileURLToPath(helper.artifactURL('fixture.txt')), 'artifact fixture');
  assert.equal(await readFile(join(scratch, 'shots #1', 'fixture.txt'), 'utf8'), 'artifact fixture');
  assert.deepEqual(await readdir(scratch), ['shots #1']);
  assert.deepEqual(await readdir(helper.artifactURL()), ['fixture.txt']);
});

test('artifactURL rejects traversal, absolute paths and URL syntax instead of escaping its directory', () => {
  for (const filename of ['..', '.', '../escape.png', 'nested/../../escape.png', '/tmp/escape.png', '//host/escape.png', 'nested/image.png', 'nested\\image.png', 'C:\\escape.png', 'file:///tmp/escape.png', 'https://host/image.png', '%2e%2e/escape.png', '%2fescape.png', 'image.png?private=1', 'image.png#fragment', 'image\u0000.png', 'image\n.png', 'image.png\n', 'image.png\r\n', null, 42, {}]) {
    assert.throws(() => helper.artifactURL(filename), /artifact filename/i, String(filename));
  }
});
