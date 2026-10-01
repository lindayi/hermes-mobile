// Version this cache whenever a public asset changes. Never store authenticated responses.
const CACHE='hermes-public-v4';
const ASSETS=['/hermes/index.html','/hermes/styles.css','/hermes/app.js','/hermes/viewport.mjs','/hermes/disclosure-reachability.mjs','/hermes/api.mjs','/hermes/ui.mjs','/hermes/background-placement.mjs','/hermes/session-swipe.mjs','/hermes/tool-details.mjs','/hermes/model-controls.mjs','/hermes/markdown.mjs','/hermes/webauthn.mjs','/hermes/manifest.webmanifest','/hermes/icons/hermes.svg','/hermes/icons/icon-192.png','/hermes/icons/icon-512.png','/hermes/icons/apple-touch-icon.png'];
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(ASSETS))));
self.addEventListener('activate',event=>event.waitUntil((async()=>{
  for(const key of await caches.keys())if(key.startsWith('hermes-public-') && key!==CACHE)await caches.delete(key);
  await self.clients.claim();
})()));
self.addEventListener('fetch',event=>{
  const request=event.request,url=new URL(request.url);
  if(request.method!=='GET' || url.origin!==self.location.origin)return;
  const shell=url.pathname==='/hermes/' || url.pathname==='/hermes';
  if(!shell && (!ASSETS.includes(url.pathname) || url.search))return;
  event.respondWith((async()=>{
    const cache=await caches.open(CACHE),path=shell ? '/hermes/index.html' : url.pathname;
    // Public shell is identical for all users. The worker never handles app-api routes.
    try {const response=await fetch(path,{credentials:'omit',cache:'no-cache'});if(response.ok)return response;}catch{}
    return await cache.match(path) || new Response('Hermes is offline. Reconnect and reload.',{status:503,headers:{'Content-Type':'text/plain'}});
  })());
});
function safeInboxId(value) {return typeof value==='string' && /^[A-Za-z0-9_-]{1,128}$/.test(value) ? value : null;}
function safeTarget(value) {
  const base=self.location.origin+'/hermes/';
  try {
    const url=new URL(value || base,base);
    if(url.origin!==self.location.origin || url.pathname!=='/hermes/')return base;
    const id=safeInboxId(url.searchParams.get('inbox'));
    return id ? base+'?inbox='+encodeURIComponent(id) : base;
  }catch{return base;}
}
self.addEventListener('push',event=>event.waitUntil((async()=>{
  let data={};try{data=event.data?.json() || {};}catch{}
  // Match the server's URI policy: prose labels need whitespace, with only
  // balanced Markdown label decoration exempted (never opaque URI punctuation).
  const uri=value=>/\b(?:https?|ftps?|mailto|tel|data|javascript|file|urn)\s*:/iu.test(value) || /\b[a-z][a-z0-9+.-]*:\S/iu.test(value.replace(/(\*\*|__)([a-z][a-z0-9+.-]*:)\1(?=\s|$)/giu,'$2 '));
  const plain=(value,limit)=>typeof value==='string' && value.trim().length>0 && Array.from(value).length<=limit && !uri(value.normalize('NFKC')) && !/[<>`\p{Cf}\u0000-\u001f\u007f-\u009f\u2028-\u202e]|www\.|\[[^\]]*\]\(/iu.test(value);
  const id=safeInboxId(data?.inbox_id),base=self.location.origin+'/hermes/';
  const informative=id && plain(data?.title,100) && plain(data?.body,180);
  // One validated receipt identity owns both grouping and navigation, never payload URL.
  await self.registration.showNotification(informative?data.title:'Hermes',{body:informative?data.body:'Open Hermes to read your update.',icon:'/hermes/icons/icon-192.png',badge:'/hermes/icons/icon-192.png',tag:id ? 'hermes-'+id : undefined,data:{url:id ? base+'?inbox='+encodeURIComponent(id) : base}});
})()));
self.addEventListener('notificationclick',event=>event.waitUntil((async()=>{
  event.notification.close();const url=safeTarget(event.notification.data?.url);
  // Navigation only. Notification clicks never mark read, approve, or submit a run.
  const windows=await self.clients.matchAll({type:'window',includeUncontrolled:true});
  const existing=windows.find(client=>client.url?.startsWith(self.location.origin+'/hermes/'));
  if(existing){await existing.navigate(url);await existing.focus();}else await self.clients.openWindow(url);
})()));
