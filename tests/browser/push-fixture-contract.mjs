import assert from 'node:assert/strict';

// Presence is not a run/steer/stop or history mutation. Exempt only the exact
// endpoint after checking its entire recorded payload and fixture CSRF token.
// Keep every other operation (and its order/object identity) for existing checks.
export function withoutValidatedPresence(calls) {
 return calls.filter(call=>{
  const path=call.p ?? call.path;
  if(call.method!=='POST' || (path!=='/push/presence' && path!=='/hermes/app-api/push/presence'))return true;
  assertPresenceBody(call.body);
  assert.equal(call.csrf,'fixture','presence retains fixture CSRF protection');
  return false;
 });
}

// api.request unit doubles see payloads before the HTTP client adds CSRF.
// Share only the unchanged body checks; HTTP callers must still use the wrapper.
export function assertPresenceBody(body) {
  assert.ok(body && typeof body==='object' && !Array.isArray(body),'presence body is an object');
  assert.deepEqual(Object.keys(body).sort(),['client_id','sequence','session_id','visible'],'presence has only its exact schema; never input/action fields');
  assert.equal(typeof body.client_id,'string');
  assert.match(body.client_id,/^[A-Za-z0-9_-]{1,80}$/);
  assert.ok(Number.isSafeInteger(body.sequence) && body.sequence>=0,'presence sequence is a nonnegative safe integer');
  assert.equal(typeof body.visible,'boolean');
  assert.ok(body.session_id===null || (typeof body.session_id==='string' && body.session_id.length>=1 && body.session_id.length<=256 && !/[\x00-\x20]/.test(body.session_id)),'presence session is null or a bounded session ID');
}
