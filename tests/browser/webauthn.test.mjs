import test from 'node:test';
import assert from 'node:assert/strict';
const load = async () => import('../../frontend/webauthn.mjs').catch(() => ({}));

test('WebAuthn options decode browser binary fields without changing server options', async () => {
  const {decodeOptions} = await load();
  assert.equal(typeof decodeOptions, 'function', 'WebAuthn decoder exists');
  const wire = {challenge:'AQID_w',user:{id:'BAU',name:'Ada'},excludeCredentials:[{id:'Bgc',type:'public-key'}],allowCredentials:[{id:'CAk',type:'public-key'}]};
  const result = decodeOptions(wire);
  assert.deepEqual([...new Uint8Array(result.challenge)], [1,2,3,255]);
  assert.deepEqual([...new Uint8Array(result.user.id)], [4,5]);
  assert.deepEqual([...new Uint8Array(result.excludeCredentials[0].id)], [6,7]);
  assert.deepEqual([...new Uint8Array(result.allowCredentials[0].id)], [8,9]);
  assert.equal(wire.challenge, 'AQID_w');
});

test('WebAuthn serializes registration and assertion buffers for real server verification', async () => {
  const {serializeCredential} = await load();
  assert.equal(typeof serializeCredential, 'function');
  const bytes = new Uint8Array([1,2,255]).buffer;
  const credential = {id:'credential',rawId:bytes,type:'public-key',authenticatorAttachment:'platform',getClientExtensionResults:()=>({credProps:{rk:true}}),response:{clientDataJSON:bytes,attestationObject:bytes,getTransports:()=>['internal']}};
  const result = serializeCredential(credential);
  assert.equal(result.rawId, 'AQL_');
  assert.deepEqual(result.response, {clientDataJSON:'AQL_',attestationObject:'AQL_',transports:['internal']});
  assert.deepEqual(result.clientExtensionResults, {credProps:{rk:true}});
  credential.response = {clientDataJSON:bytes,authenticatorData:bytes,signature:bytes,userHandle:null};
  assert.deepEqual(serializeCredential(credential).response, {clientDataJSON:'AQL_',authenticatorData:'AQL_',signature:'AQL_',userHandle:null});
  assert.throws(() => serializeCredential(null), /cancel/i);
});
