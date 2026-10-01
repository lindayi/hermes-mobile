import {createAPI} from './api.mjs';
import {mountApp} from './ui.mjs';
import {observeViewport} from './viewport.mjs';

// The stable root also owns dialogs; navigation replaces only its children.
// The observer suspends/resumes itself across pagehide/pageshow (including bfcache).
const disposeViewport=observeViewport(document.getElementById('app'));

try {
  const theme=localStorage.getItem('hermes:theme');
  if(['light','dark','system'].includes(theme))document.documentElement.dataset.theme=theme;
} catch { /* Storage can be disabled; system appearance remains available. */ }

if('serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.register('/hermes/sw.js',{scope:'/hermes/'}).catch(()=>{
    const message=document.createElement('p');message.className='notice error';message.textContent='Offline setup is unavailable. Hermes still works online; reload to retry.';document.getElementById('app').append(message);
  });
}

mountApp(document,createAPI()).catch(()=>{
  disposeViewport();
  const root=document.getElementById('app');
  const message=document.createElement('p');message.className='notice error';message.setAttribute('role','alert');
  message.textContent='Hermes could not start. Reload this page to try again.';
  root.replaceChildren(message);
});
