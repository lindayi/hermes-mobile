export function fromBase64url(value) {
  const base = value.replace(/-/g, '+').replace(/_/g, '/');
  return Uint8Array.from(atob(base + '='.repeat((4 - base.length % 4) % 4)), ch => ch.charCodeAt(0));
}
export function decodeOptions(wire) {
  const options = {...wire, challenge:fromBase64url(wire.challenge)};
  if (wire.user) options.user = {...wire.user, id:fromBase64url(wire.user.id)};
  for (const name of ['allowCredentials', 'excludeCredentials']) {
    if (wire[name]) options[name] = wire[name].map(item => ({...item,id:fromBase64url(item.id)}));
  }
  return options;
}
export function toBase64url(value) {
  let binary = '';
  for (const byte of new Uint8Array(value)) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
export function serializeCredential(credential) {
  if (!credential) throw new Error('Passkey request cancelled. Nothing was changed.');
  const response = {};
  for (const name of ['clientDataJSON','attestationObject','authenticatorData','signature','userHandle']) {
    if (name in credential.response) response[name] = credential.response[name] === null ? null : toBase64url(credential.response[name]);
  }
  if (credential.response.getTransports) response.transports = credential.response.getTransports();
  return {id:credential.id, rawId:toBase64url(credential.rawId), type:credential.type,
    authenticatorAttachment:credential.authenticatorAttachment,
    clientExtensionResults:credential.getClientExtensionResults(), response};
}
