import {decodeOptions, serializeCredential, fromBase64url} from './webauthn.mjs';
import {renderMarkdown} from './markdown.mjs';
import {toolDetail,toolName,toolDuration} from './tool-details.mjs';
import {createModelControls} from './model-controls.mjs';
import {createSessionSwipe} from './session-swipe.mjs';
import {observeDisclosureReachability} from './disclosure-reachability.mjs';
import {markHistoryNode,backgroundPlacement} from './background-placement.mjs';

// Presentation only: these dates never participate in transcript ordering.
export function formatMessageTime(value, {locale, timeZone} = {}) {
  let date,fraction='';
  if(typeof value==='number') {
    // Numeric transport is Unix seconds, never browser milliseconds.
    if(!Number.isFinite(value) || value<0 || value>=253402300800)return null;
    // Fail closed on exponent-form precision instead of dropping or embedding it.
    const decimal=/^\d+(?:\.(\d+))?$/.exec(String(value));
    if(!decimal)return null;
    date=new Date(Math.floor(value)*1000);
    fraction=decimal[1] || '';
  } else if(typeof value==='string' && value.length<=64) {
    const match=/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$/.exec(value);
    if(!match)return null;
    const [,year,month,day,hour,minute,second,decimals,zone]=match;
    const local=new Date(`${year}-${month}-${day}T00:00:00Z`);
    if(!Number.isFinite(local.getTime()) || local.toISOString().slice(0,10)!==`${year}-${month}-${day}` || Number(hour)>23 || Number(minute)>59 || Number(second)>59 || (zone!=='Z' && (Number(zone.slice(1,3))>23 || Number(zone.slice(4))>59)))return null;
    date=new Date(value);fraction=decimals || '';
  } else return null;
  if(!Number.isFinite(date.getTime()) || date.getTime()<0 || date.getUTCFullYear()>9999)return null;
  const options={timeZone,year:'numeric',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'};
  // Keep recorded fractional precision; Date is only used for calendar/timezone conversion.
  const datetime=date.toISOString().replace(/\.\d{3}Z$/,`.${fraction.padEnd(3,'0')}Z`);
  return {datetime,text:new Intl.DateTimeFormat(locale,options).format(date),
    title:new Intl.DateTimeFormat(locale,{...options,second:'2-digit',timeZoneName:'long'}).format(date)};
}

export async function mountApp(doc, api, win = doc.defaultView) {
  const root = doc.getElementById('app');
  const state = {user:null,view:'chats',session:null,query:'',searchOpen:false,offset:0,kind:'chats'};
  let content, nav, routeVersion = 0, destroyed=false;
  let stream = null, cancelTracking=null, headerState=null;
  let stopDisclosureReachability=null;
  let closeEditor=null, stopExtras=null, approvalState=null, currentModelSync=null, stopPushPresence=null;
  let pushIdentity={client_id:win.crypto.randomUUID(),sequence:0},pushIdentityKey=null,releasePushIdentity=null,pushRequest=null;
  const handledApprovals=new Set();
  const approvalKey=item=>`${state.user?.id}:${item.run_id}:${item.id || item.request_id}`;
  function leaveConversation(){stopDisclosureReachability?.();stopDisclosureReachability=null;stopPushPresence?.();stopPushPresence=null;currentModelSync=null;closeEditor?.(false);stopExtras?.();stopExtras=null;approvalState=null;headerState?.destroy();headerState=null;root.querySelector('[data-rename-dialog]')?.remove();}
  function disconnect(){const cancel=cancelTracking;cancelTracking=null;cancel?.();stream?.close();stream=null;}
  const drafts = new Map();
  const attempts = new Map();
  // Identity state survives route replacement; a new row is not a new operation.
  const deletions = new Map();
  const deletionKey=(owner,id)=>JSON.stringify([owner,id]);
  const storage = {get(key,persistent=false) { try { return (persistent ? win.localStorage : win.sessionStorage).getItem(key); } catch { return null; } }, set(key,value,persistent=false) {try { const store=persistent ? win.localStorage : win.sessionStorage;value == null ? store.removeItem(key) : store.setItem(key,value); } catch { /* private browsing may disable storage */ }}};
  const key = name => `hermes:${state.user?.id}:${name}`;
  const h = (tag, attrs = {}, ...children) => {
    const el = doc.createElement(tag);
    for (const [key,value] of Object.entries(attrs)) {
      if (key.startsWith('on')) el.addEventListener(key.slice(2).toLowerCase(), value);
      else if (value !== false && value != null) el.setAttribute(key, value === true ? '' : value);
    }
    for (const child of children.flat(Infinity)) if (child != null) el.append(child.nodeType ? child : doc.createTextNode(String(child)));
    return el;
  };
  const messageTime=(value,label='')=>{
    const formatted=formatMessageTime(value);if(!formatted)return null;
    const prefix=label ? `${label} ` : '';
    return h('time',{class:'message-time',datetime:formatted.datetime,title:prefix+formatted.title,'aria-label':prefix+formatted.title},prefix+formatted.text);
  };
  const button = (text, action, cls = 'secondary', attrs = {}) => h('button', {type:'button',class:cls,onclick:action,...attrs}, text);
  const field = (label,name,type = 'text',value = '') => h('label', {class:'field'},h('span',{},label),h('input',{name,type,value,required:true,autocomplete:type === 'password' ? 'off' : 'name'}));
  const brand = () => h('div',{class:'brand'},h('img',{src:'/hermes/icons/hermes.svg',alt:'',width:36,height:36}),h('span',{},'Hermes'));
  const notice = h('div', {class:'notice',role:'status','aria-live':'polite',hidden:true});
  let noticeOwner=null;
  function inform(message, error = false, owner=null) { noticeOwner=owner; notice.textContent = message; notice.hidden = !message; notice.classList.toggle('error',error); }
  function errorMessage(error) { return error.name === 'NotAllowedError' ? 'Passkey request cancelled or not allowed. Try again when you are ready.' : error.message || 'Something went wrong. Please try again.'; }
  async function action(control, fn) {
    if (control?.disabled) return;
    if (control) control.disabled = true;
    inform('');
    try { await fn(); } catch (error) { if(control && !control.isConnected)return; if(error.status===401 && state.user)expiredSession();inform(errorMessage(error),true); }
    finally { if (control) control.disabled = control.dataset.locked === 'true'; }
  }
  async function ceremony(kind, body = {}) {
    if (!win.PublicKeyCredential || !win.navigator.credentials) throw new Error('Passkeys are not supported here. Open Hermes in a secure, supported browser.');
    const begin = await api.request(`/auth/${kind}/options`, {method:'POST',body});
    const creating = kind === 'register' || kind === 'recovery';
    const credential = await win.navigator.credentials[creating ? 'create' : 'get']({publicKey:decodeOptions(begin.publicKey || begin.options)});
    return api.request(`/auth/${kind}/${kind === 'verify' ? 'finish' : 'verify'}`, {method:'POST',body:{[creating ? 'enrollment_id' : 'challenge_id']:begin[creating ? 'enrollment_id' : 'challenge_id'],credential:serializeCredential(credential)}});
  }
  function showAuth(mode = 'login') {
    root.replaceChildren(h('main',{class:'auth-shell'},h('header',{},brand()),notice,
      h('section',{class:'auth-card'},
        h('h1',{},mode === 'recovery' ? 'Recover access' : mode === 'register' ? 'Create account' : 'Sign in'),
        mode !== 'login' ? enrollment(mode) : h('div',{class:'stack'},
          button('Sign in with a passkey', e => action(e.currentTarget, async () => { await ceremony('login'); await start(); }), 'primary'),
          button('Use an invitation', () => showAuth('register'),'quiet'),
          button('Recover access',()=>showAuth('recovery'),'quiet'),
          h('p',{class:'caption'},'Use Face ID, Touch ID, your device PIN, or a security key.')))));
  }
  function enrollment(mode) {
    const recovery=mode==='recovery';
    return h('form',{class:'stack',onsubmit:e => {
      e.preventDefault(); const form = e.currentTarget;
      action(form.querySelector('button'), async () => { await ceremony(recovery ? 'recovery' : 'register',{code:form.elements.code.value.trim(),display_name:form.elements.display_name.value.trim()}); form.reset(); await start(); });
    }},field(recovery ? 'One-use recovery code' : 'Invitation or owner setup code','code','password'),field('Your name','display_name'),
    h('p',{class:'caption'},recovery ? 'This code is consumed immediately. Previous sessions are revoked. Complete the new passkey now; interrupted recovery needs another saved code.' : 'Invitations create a separate account. Only an administrator’s one-use setup code can enroll the owner.'),
    h('button',{type:'submit',class:'primary'},recovery ? 'Create replacement passkey' : 'Create a passkey'),button('Back to sign in',() => showAuth(),'quiet'));
  }
  // Text-only status symbols and one stroke family keep mechanical controls neutral.
  const icons = {success:'✓',failed:'×',running:'◔',completed:'✓',cancelled:'■',mixed:'!',unknown:'?'};
  const paths = {trash:'M4 6h16 M9 6V3h6v3 M6 6l1 15h10l1-15 M10 10v7 M14 10v7',progress:'M12 3a9 9 0 1 1-9 9',clock:'M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18 M12 7v5l3 2',refresh:'M20 8a8 8 0 1 0 0 8 M20 3v5h-5',search:'M16 16l5 5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',newchat:'M4 4h10 M20 10v6H9l-5 4V4 M19 2v8 M15 6h8',chats:'M4 4h16v12H9l-5 4Z',inbox:'M4 4h16v16H4Z M4 13h5l2 3h2l2-3h5',jobs:'M4 6h16v14H4Z M8 3v6 M16 3v6 M4 11h16',settings:'M4 6h16 M4 12h16 M4 18h16 M9 3v6 M15 9v6 M9 15v6',back:'M14 5l-7 7 7 7 M7 12h13',edit:'M4 20l1-6L16 3l5 5L10 19Z M13 6l5 5',copy:'M8 8h12v12H8Z M16 8V4H4v12h4',send:'M12 20V4 M5 11l7-7 7 7',stop:'M6 6h12v12H6Z',expand:'M14 4h6v6 M20 4l-7 7 M10 20H4v-6 M4 20l7-7',disclosure:'M9 5l7 7-7 7'};
  const icon=name=>{
    const svg=doc.createElementNS('http://www.w3.org/2000/svg','svg');
    for(const [key,value] of Object.entries({'aria-hidden':'true',focusable:'false',class:'ui-icon',viewBox:'0 0 24 24',width:18,height:18,fill:'none',stroke:'currentColor','stroke-width':1.7,'stroke-linecap':'round','stroke-linejoin':'round'}))svg.setAttribute(key,value);
    const path=doc.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',paths[name]);svg.append(path);return svg;
  };
  const statusSymbol=state=>{if(state==='running'){const ring=icon('progress');ring.classList.add('progress-ring');return ring;}return state==='queued'?icon('clock'):icons[state] || '?';};
  const disclosure=()=>h('span',{'aria-hidden':'true',class:'disclosure-icon'},icon('disclosure'));
  const date = seconds => seconds ? new Date(seconds * 1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}) : 'Not available';
  const empty = (title, text) => h('div',{class:'empty'},h('h2',{},title),text ? h('p',{class:'muted'},text) : null);
  function shell() {
    content = h('main',{id:'main',class:'content',tabindex:'-1'});
    nav = h('nav',{'aria-label':'Main navigation',class:'bottom-nav'},...['chats','inbox','jobs','settings'].map(view => button(view[0].toUpperCase()+view.slice(1), () => navigate(view),'nav-item',{'data-view':view,'aria-label':view[0].toUpperCase()+view.slice(1)})));
    for (const item of nav.children) item.prepend(h('span',{'aria-hidden':'true',class:'nav-icon'},icon(item.dataset.view)));
    root.replaceChildren(h('a',{class:'skip-link',href:'#main'},'Skip to content'),h('div',{class:'app-shell'},
      notice,content,nav));
  }
  async function navigate(view) {
    disconnect();leaveConversation();
    content.classList.remove('conversation');
    if(state.user?.status !== 'ready') view='settings';
    state.view = view;
    const version = ++routeVersion;
    inform(state.user?.status !== 'ready' ? `Your profile is ${state.user.status}. Agent access is unavailable until setup is complete.` : '');
    for (const item of nav.children) item.setAttribute('aria-current',item.dataset.view === view ? 'page' : 'false');
    content.replaceChildren(h('p',{class:'loading',role:'status'},'Loading…'));
    try {
      if (view === 'chats') await chats(version);
      else if(view === 'inbox') await inbox(version);
      else if(view === 'jobs') await jobs(version);
      else if(view === 'settings') await settings(version);
      else content.replaceChildren(empty(view[0].toUpperCase()+view.slice(1),'Select refresh to load your information.'));
    } catch (error) {
      if (version !== routeVersion) return;
      if(error.status===401){expiredSession();return;}
      content.replaceChildren(empty('Couldn’t load this view.','Your data has not been changed.'),button('Try again',()=>navigate(view)));
      inform(errorMessage(error),true);
    }
  }
  function expiredSession() {disconnect();leaveConversation();releasePresenceIdentity();routeVersion++;state.user=null;drafts.clear();attempts.clear();api.clear();showAuth();inform('Your session expired. Sign in with your passkey to continue.',true);}
  async function signOut(remote=true) {
    if(remote)await api.request('/auth/logout',{method:'POST',body:{}});
    disconnect();leaveConversation();releasePresenceIdentity();drafts.clear();attempts.clear();
    try {for(const name of Object.keys(win.sessionStorage))if(name.startsWith(`hermes:${state.user?.id}:`))win.sessionStorage.removeItem(name);}catch{}
    try {for(const name of Object.keys(win.localStorage))if(name.startsWith(`hermes:${state.user?.id}:`))win.localStorage.removeItem(name);}catch{}
    api.clear();state.user=null;showAuth();inform('Signed out. Local drafts on this device were cleared.');
  }
  function oneTimeSecret(title,values) {
    content.replaceChildren(h('section',{class:'card stack'},h('h1',{},title),h('p',{class:'muted'},'Shown only once. Save this privately before leaving this screen. Do not post it publicly.'),h('pre',{class:'secret',tabindex:0},values.join('\n')),button('Done',()=>navigate('settings'),'primary')));
  }
  async function settings(version) {
    const theme=h('select',{name:'theme','aria-label':'Appearance'},...['system','light','dark'].map(value=>h('option',{value},value[0].toUpperCase()+value.slice(1))));
    let savedTheme='system';try{savedTheme=win.localStorage.getItem('hermes:theme') || 'system';}catch{}
    theme.value=savedTheme;
    theme.addEventListener('change',()=>{doc.documentElement.dataset.theme=theme.value;try{win.localStorage.setItem('hermes:theme',theme.value);}catch{}});
    const sections=h('div',{class:'settings-grid'},h('section',{class:'card'},h('h2',{},'Account'),h('p',{class:'muted'},`Account: ${state.user.role || 'member'} · ${state.user.status}`),h('label',{class:'field'},h('span',{},'Appearance'),theme)));
    const endpoints=state.user.role==='owner' ? ['/devices','/passkeys','/invites','/members'] : ['/devices','/passkeys'];
    const results=await Promise.allSettled(endpoints.map(path=>api.request(path)));
    if(version!==routeVersion)return;
    if(results.some(result=>result.status==='rejected' && result.reason.status===401)){expiredSession();return;}
    for(let i=0;i<endpoints.length;i++) {
      const name=endpoints[i].slice(1),result=results[i];
      const section=h('section',{class:'card','data-section':name},h('h2',{},({devices:'Trusted devices',passkeys:'Passkeys',invites:'Invitations',members:'Family accounts'})[name]));
      if(result.status==='rejected')section.append(h('p',{class:'inline-error'},errorMessage(result.reason)));
      else for(const item of result.value.items || []) {
        const row=h('div',{class:'setting-row'},h('div',{},h('strong',{},item.label || item.display_name || item.id),h('p',{class:'caption'},item.current ? 'This device' : item.status || date(item.created_at))));
        const label=({devices:'Revoke device',passkeys:'Remove passkey',invites:'Revoke invitation',members:'Disable access'})[name];
        if(name!=='members' || item.role!=='owner')row.append(button(label,e=>action(e.currentTarget,async()=>{
          if(!await confirmChange(label+'?',name==='members' ? 'Web sessions will be revoked. Existing jobs and runs are not deleted or stopped by this action.' : `Apply to ${item.label || item.display_name || item.id}?`))return;
          await ceremony('verify');
          await api.request(`${endpoints[i]}/${encodeURIComponent(item.id)}${name==='members' ? '/disable' : ''}`,{method:name==='members' ? 'POST' : 'DELETE'});
          if(name==='devices' && item.current){await signOut(false);return;}
          await navigate('settings');inform('Security change saved.');
        }),'danger'));
        if(name==='members' && item.role==='member' && item.status==='pending')row.append(button('Provision profile',e=>action(e.currentTarget,async()=>{
          if(!await confirmChange('Provision profile?',`Create a clean profile for ${item.display_name || item.id}? This does not activate agent access; it remains pending until an operator configures and verifies the runtime. Profiles are not filesystem sandboxes.`))return;
          await ceremony('verify');
          const result=await api.request(`/members/${encodeURIComponent(item.id)}/provision`,{method:'POST',body:{}});
          await navigate('settings');inform(`Profile setup: ${result.provisioning_status}. Account access remains pending; operator setup is required.`);
        }),'secondary'));
        section.append(row);
      }
      if(name==='devices')section.append(button('Revoke all devices',e=>action(e.currentTarget,async()=>{if(!await confirmChange('Revoke all devices?','Every web session, including this one, will be signed out.'))return;await ceremony('verify');await api.request('/devices',{method:'DELETE'});await signOut(false);}),'danger'));
      if(name==='passkeys')section.append(button('Add a passkey',passkeyForm,'secondary'));
      if(name==='invites')section.append(h('p',{class:'caption'},'Invite someone into a separate Hermes profile. Profiles are not filesystem sandboxes.'),button('Create invitation',inviteForm,'secondary'));
      sections.append(section);
    }
    sections.append(notificationSettings(version),h('section',{class:'card stack'},h('h2',{},'Recovery & privacy'),h('p',{class:'caption'},'Keep a second passkey and offline recovery codes. Drafts stay in this browser tab; conversations are never cached for offline reading.'),button('Generate recovery codes',e=>action(e.currentTarget,async()=>{if(!await confirmChange('Replace recovery codes?','Any previous unused codes will stop working. Save the new codes offline.'))return;await ceremony('verify');const result=await api.request('/auth/recovery/codes',{method:'POST',body:{}});oneTimeSecret('Your recovery codes',result.codes);}),'secondary'),button('Clear local drafts',()=>{drafts.clear();attempts.clear();try{for(const name of Object.keys(win.sessionStorage))if(name.startsWith(`hermes:${state.user.id}:draft:`)||name.startsWith(`hermes:${state.user.id}:attempt:`))win.sessionStorage.removeItem(name);}catch{}inform('Local drafts cleared. Server conversations are unchanged.');},'quiet'),button('Sign out',e=>action(e.currentTarget,()=>signOut()),'danger')));
    content.replaceChildren(h('div',{class:'page-heading'},h('div',{},h('h1',{},'Settings'))),sections);
  }
  function notificationSettings(version) {
    const owner=state.user?.id,current=()=>!destroyed && version===routeVersion && owner===state.user?.id && state.view==='settings';
    const supported=!!(win.Notification && win.PushManager && win.navigator.serviceWorker);
    const categories={completion:'Request completions',approval:'Approvals',attention:'Action needed or failures',scheduled:'Scheduled updates',operational:'Serious server alerts'};
    let saved,draft,subscription=null,known=false,busy=false;
    const status=h('p',{class:'caption',role:'status','aria-live':'polite'},'Loading notification preferences…');
    const deviceStatus=h('p',{class:'caption'});
    const controls=h('div',{class:'stack'}),retry=h('div',{});
    const master=button('Checking notifications…',()=>void change('master'),'push-master-switch',{'role':'switch','aria-label':'Notifications on this device','aria-checked':'false',disabled:true});
    const masterPanel=h('div',{class:'push-master'},h('div',{},h('h3',{},'Notifications on this device'),deviceStatus),master);
    const saveButton=button('Save',()=>void change('preferences'),'primary',{'aria-label':'Save notification preferences',disabled:true});
    const preferences=h('div',{class:'push-preferences stack',hidden:true},h('h3',{},'Notification preferences'),h('p',{class:'caption'},'These settings affect push on this device only. Inbox and WhatsApp are unchanged when a category is turned off.'),h('p',{class:'caption'},'Informative previews may expose sensitive content on your lock screen. Hide notification details replaces both title and preview with generic text.'),controls,saveButton);
    const testButton=button('Send test notification',()=>void testNotification(),'quiet',{disabled:true});
    const section=h('section',{class:'card stack notification-settings'},h('h2',{},'Notifications'),h('p',{class:'caption'},'On iPhone or iPad, open Share → Add to Home Screen, then launch Hermes from its icon. Notifications are best effort; your inbox keeps the result.'),masterPanel,preferences,status,retry,h('p',{class:'caption'},'Changes apply to notifications not yet sent. Already sent or displayed previews cannot be recalled.'),testButton);
    const effective=()=>known && saved?.enabled===true && supported && win.Notification.permission==='granted' && Boolean(subscription);
    const dirty=()=>Boolean(saved && draft && (draft.hide_details!==saved.hide_details || Object.keys(categories).some(name=>draft.categories[name]!==saved.categories[name])));
    function validate(value) {
      if(!value || !Number.isSafeInteger(value.revision) || value.revision<0 || typeof value.enabled!=='boolean' || typeof value.hide_details!=='boolean' || !value.categories || Object.keys(categories).some(name=>typeof value.categories[name]!=='boolean'))throw new Error('Invalid preferences');
      return {revision:value.revision,enabled:value.enabled,categories:Object.fromEntries(Object.keys(categories).map(name=>[name,value.categories[name]])),hide_details:value.hide_details};
    }
    async function registration() {
      let timer;
      try{return await Promise.race([win.navigator.serviceWorker.ready,new Promise((_,reject)=>{timer=win.setTimeout(()=>reject(new Error('Notification setup is not ready. Reload online and try again.')),10000);})]);}
      finally{win.clearTimeout(timer);}
    }
    function render() {
      const on=effective();
      master.setAttribute('role',known?'switch':'button');
      if(known)master.setAttribute('aria-checked',String(on));else master.removeAttribute('aria-checked');
      master.disabled=busy || !known || !supported;
      master.textContent=!known?(saved?'Status unconfirmed':'Checking notifications…'):on?'Disable notifications':'Enable notifications';
      deviceStatus.textContent=!supported?'Push notifications are unavailable in this browser. Your inbox still works.':`Browser permission: ${win.Notification.permission}. ${!known?(saved?'Device status is unconfirmed.':'Checking device status…'):on?'Notifications are enabled.':!saved.enabled?'Notifications are disabled.':!subscription?'This browser is not subscribed.':'Allow notifications in browser or device settings.'}`;
      preferences.hidden=!on;saveButton.disabled=busy || !on || !dirty();testButton.disabled=busy || !on;
      if(!saved)return;
      if(!controls.childElementCount)for(const [name,label] of [...Object.entries(categories),['hide_details','Hide notification details']]) {
        const input=h('input',{type:'checkbox',name});
        input.addEventListener('change',()=>{
          if(!current() || busy || !effective())return;
          if(name in categories)draft.categories[name]=input.checked;else draft[name]=input.checked;
          retry.replaceChildren();status.textContent=dirty()?'Unsaved changes.':'';saveButton.disabled=!dirty();
        });
        controls.append(h('label',{class:'push-preference'},input,h('span',{},label)));
      }
      for(const input of controls.querySelectorAll('input')){input.checked=input.name in categories?draft.categories[input.name]:draft[input.name];input.disabled=busy;}
    }
    async function load() {
      if(!current() || busy)return;busy=true;known=false;saved=undefined;draft=undefined;controls.replaceChildren();retry.replaceChildren();render();status.textContent='Loading notification preferences…';
      try {
        const result=validate(await api.request('/push/preferences'));if(!current())return;
        saved=result;draft=structuredClone(saved);
        if(supported){const reg=await registration();if(!current())return;subscription=await reg.pushManager.getSubscription();if(!current())return;}
        known=true;status.textContent='';
      }catch(error){
        if(!current())return;if(error.status===401){expiredSession();return;}
        status.textContent=saved?'Could not check notification status.':'Could not load notification preferences.';
        retry.append(button(saved?'Retry checking notifications':'Retry loading preferences',load));
      }finally{busy=false;if(current())render();}
    }
    async function reconcile() {
      if(!current() || busy)return;
      busy=true;known=false;retry.replaceChildren();render();status.textContent='Checking current notification settings…';
      try{
        const result=validate(await api.request('/push/preferences'));if(!current())return;
        if(result.revision<saved.revision)throw new Error('Stale preferences');
        // Preserve local edits, not stale values the user never changed.
        if(draft.hide_details===saved.hide_details)draft.hide_details=result.hide_details;
        for(const name of Object.keys(categories))if(draft.categories[name]===saved.categories[name])draft.categories[name]=result.categories[name];
        saved=result;draft.revision=saved.revision;draft.enabled=saved.enabled;known=true;
        status.textContent='Current notification settings checked. '+(dirty()?'Your choices are unsaved; review and Save to apply them.':'Your choices match the current settings.');
      }catch(error){
        if(!current())return;if(error.status===401){expiredSession();return;}
        status.textContent='Notification status is unconfirmed. The change may have been saved. Your choices are kept locally; check again before making changes.';
        retry.append(button('Retry checking notifications',reconcile));
      }finally{busy=false;if(current())render();}
    }
    async function change(kind,retryPayload=null) {
      if(!current() || busy || !known || !saved)return;
      const enabling=kind==='master'?(retryPayload?retryPayload.enabled:!effective()):null;
      // A master action changes only the saved master value, never unsaved choices.
      const next=structuredClone(retryPayload || (kind==='master'?{...saved,enabled:enabling}:draft));
      const focused=section.contains(doc.activeElement)?doc.activeElement:null;
      let putStarted=false,focusMoved=false;const moved=()=>{focusMoved=true;};
      busy=true;retry.replaceChildren();render();doc.addEventListener('focusin',moved);win.addEventListener('blur',moved);
      status.textContent=kind==='master'?(enabling?'Enabling notifications…':'Disabling notifications…'):'Saving notification preferences…';
      try {
        if(kind==='master' && enabling){
          const permission=win.Notification.permission==='granted'?'granted':await win.Notification.requestPermission();if(!current())return;
          if(permission!=='granted')throw new Error('Notifications were not allowed. Enable them in browser or device settings; your inbox is still available.');
          const reg=await registration();if(!current())return;
          let candidate=await reg.pushManager.getSubscription();if(!current())return;
          if(!candidate){
            const {public_key}=await api.request('/push/key');if(!current())return;
            if(!public_key)throw new Error('Notifications are not configured on the server.');
            candidate=await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:fromBase64url(public_key)});if(!current())return;
          }
          await api.request('/push/subscriptions',{method:'POST',body:candidate.toJSON()});if(!current())return;
          subscription=candidate;
        }
        putStarted=true;
        const result=validate(await api.request('/push/preferences',{method:'PUT',body:next}));if(!current())return;
        saved=result;
        if(kind==='master'){draft.revision=saved.revision;draft.enabled=saved.enabled;}
        else draft=structuredClone(saved);
        status.textContent=kind==='master'?(saved.enabled?'Notifications enabled.':'Notifications disabled.'):'Notification preferences saved.';
      }catch(error){
        if(!current())return;if(error.status===401){expiredSession();return;}
        if(error.status===409){busy=false;await load();if(current() && known)status.textContent='Notification preferences changed elsewhere. Current settings loaded; review and try your change again.';return;}
        if(putStarted && (!error.status || error.status<400)){busy=false;await reconcile();return;}
        if(kind==='preferences'){
          status.textContent='Could not save notification preferences. Your choices are kept locally; review and retry saving.';
          retry.append(button('Retry saving preferences',()=>void change('preferences',next)));
        }else{
          status.textContent=`Could not change notifications. ${errorMessage(error)}`;
          retry.append(button('Retry changing notifications',()=>void change('master',next)));
        }
      }finally{
        busy=false;doc.removeEventListener('focusin',moved);win.removeEventListener('blur',moved);
        if(current()){
          render();
          if(focused?.isConnected && !focused.disabled && !focusMoved && doc.activeElement===doc.body && doc.hasFocus())focused.focus({preventScroll:true});
        }
      }
    }
    async function testNotification() {
      if(!current() || busy || !effective())return;busy=true;render();
      try{await api.request('/push/test',{method:'POST',body:{}});if(current())status.textContent='Test notification requested. Check your device; delivery depends on its permission and connection.';}
      catch(error){if(!current())return;if(error.status===401){expiredSession();return;}status.textContent=errorMessage(error);}
      finally{busy=false;if(current())render();}
    }
    void load();return section;
  }
  function passkeyForm() {
    const form=h('form',{class:'card stack',onsubmit:e=>{e.preventDefault();action(form.querySelector('[type=submit]'),async()=>{
      await ceremony('verify');
      const begin=await api.request('/passkeys',{method:'POST',body:{label:form.elements.label.value.trim()}});
      const credential=await win.navigator.credentials.create({publicKey:decodeOptions(begin.options)});
      await api.request('/passkeys/verify',{method:'POST',body:{enrollment_id:begin.enrollment_id,credential:serializeCredential(credential)}});
      await navigate('settings');inform('Passkey added.');
    });}},h('h1',{},'Add a passkey'),field('Device label','label'),h('p',{class:'caption'},'First verify an existing passkey, then create a new one on your device or security key.'),h('div',{class:'actions'},button('Cancel',()=>navigate('settings'),'secondary'),h('button',{type:'submit',class:'primary'},'Save passkey')));
    content.replaceChildren(form);
  }
  function inviteForm() {
    const form=h('form',{class:'card stack',onsubmit:e=>{e.preventDefault();action(form.querySelector('[type=submit]'),async()=>{
      await ceremony('verify');
      const result=await api.request('/invites',{method:'POST',body:{label:form.elements.label.value.trim(),expires_days:Number(form.elements.expires_days.value)}});
      form.reset();oneTimeSecret('Your invitation code',[result.code]);
    });}},h('h1',{},'Invite someone'),field('Private label','label'),field('Expires after days (1–30)','expires_days','number','7'),h('p',{class:'caption'},'One person. One use. A passkey check is required before creating this code.'),h('div',{class:'actions'},button('Cancel',()=>navigate('settings'),'secondary'),h('button',{type:'submit',class:'primary'},'Create code')));
    form.elements.expires_days.min='1';form.elements.expires_days.max='30';content.replaceChildren(form);
  }
  function confirmChange(title,details,{confirmLabel='Confirm change',destructive=false,plainText=false}={}) {
    return new Promise(resolve=>{
      const previous=doc.activeElement;
      const close=value=>{overlay.remove();previous?.focus();resolve(value);};
      const dialog=h('section',{role:'dialog','aria-modal':'true','aria-label':title,class:'dialog'},h('h2',{},title),h(plainText?'p':'pre',{class:plainText?'confirmation-copy':'review-payload'},typeof details==='string' ? details : JSON.stringify(details,null,2)),h('div',{class:'actions'},button('Cancel',()=>close(false),'secondary'),button(confirmLabel,()=>close(true),destructive?'danger secondary':'primary')));
      const overlay=h('div',{class:'dialog-overlay'},dialog);
      overlay.addEventListener('keydown',event=>{
        if(event.key==='Escape'){event.preventDefault();close(false);}
        if(event.key==='Tab'){const controls=[...dialog.querySelectorAll('button,input,textarea')];const first=controls[0],last=controls.at(-1);if(event.shiftKey && doc.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey && doc.activeElement===last){event.preventDefault();first.focus();}}
      });
      root.append(overlay);dialog.querySelector('button').focus();
    });
  }
  async function jobs(version) {
    const result=await api.request('/jobs');if(version!==routeVersion)return;
    async function mutate(job,method,body) {
      if(!await confirmChange(method==='DELETE' ? 'Delete this job?' : 'Review job change',{name:job?.name || job?.id || 'New job',...(body || {}),...(method==='DELETE' ? {effect:'Stops future scheduling; existing results are kept.'} : {})}))return;
      await ceremony('verify');
      await api.request(`/jobs${job?.id ? '/'+encodeURIComponent(job.id) : ''}`,{method,body:{...body,confirm:true}});
      await navigate('jobs');inform('Job change saved.');
    }
    const list=h('div',{class:'job-list'},...(result.items || []).map(job=>{
      const foldKey=key(`job-open:${job.id}`);
      return h('details',{class:'card job-card',open:storage.get(foldKey)==='true',ontoggle:event=>{
        if(version===routeVersion && event.currentTarget.isConnected)storage.set(foldKey,event.currentTarget.open?'true':null);
      }},
      h('summary',{class:'job-summary'},disclosure(),h('span',{class:'job-summary-text'},h('span',{class:'eyebrow'},job.enabled === false ? 'PAUSED' : 'SCHEDULED'),h('strong',{},job.name || job.title || job.id),h('span',{class:'caption'},typeof job.schedule==='string' ? job.schedule : JSON.stringify(job.schedule)))),
      h('div',{class:'job-body'},h('p',{class:'caption'},'Timezone: ',job.timezone || 'Server-defined',' · Next: ',date(job.next_run_at || job.next_run)),h('p',{class:'muted'},job.prompt || 'Script-based job; script editing is not available here.'),job.last_status ? h('p',{class:'caption'},'Last outcome: ',job.last_status) : null,h('div',{class:'actions'},
        button(job.enabled===false ? 'Resume' : 'Pause',e=>action(e.currentTarget,()=>mutate(job,'PATCH',{enabled:job.enabled===false})),'secondary'),
        button('Edit',()=>jobForm(job,mutate),'quiet'),button('Delete',e=>action(e.currentTarget,()=>mutate(job,'DELETE')),'danger'))));
    }));
    content.replaceChildren(h('div',{class:'page-heading'},h('div',{},h('h1',{},'Scheduled jobs')),button('New job',()=>jobForm(null,mutate),'primary')),result.items?.length ? list : empty('No scheduled jobs',''));
  }
  function jobForm(job,mutate) {
    const form=h('form',{class:'card stack',onsubmit:e=>{e.preventDefault();action(form.querySelector('[type=submit]'),async()=>{
      const body={name:form.elements.name.value.trim(),prompt:form.elements.prompt.value.trim(),schedule:form.elements.schedule.value.trim()};
      await mutate(job,job ? 'PATCH' : 'POST',body);
    });}},h('h2',{},job ? 'Edit job' : 'New scheduled prompt'),field('Name','name','text',job?.name || ''),h('label',{class:'field'},h('span',{},'Prompt'),h('textarea',{name:'prompt',required:true,rows:5},job?.prompt || '')),field('Schedule (cron expression)','schedule','text',typeof job?.schedule==='string' ? job.schedule : ''),h('p',{class:'caption'},'Schedules use the server-configured timezone; timezone editing is not available here.'),h('p',{class:'caption'},'Destination and profile are assigned by the server. Script and routing changes are not available here.'),h('div',{class:'actions'},button('Cancel',()=>navigate('jobs'),'secondary'),h('button',{type:'submit',class:'primary'},'Review change')));
    content.replaceChildren(form);form.querySelector('input').focus();
  }
  async function inbox(version) {
    const owner=state.user?.id;
    const current=()=>!destroyed && version===routeVersion && owner===state.user?.id && state.view==='inbox';
    const inboxAction=(control,fn)=>{
      if(!current())return;
      return action(control,async()=>{try{await fn();}catch(error){if(current())throw error;}});
    };
    const mutate=async path=>{
      if(!current())return;
      await api.request(path,{method:'POST',body:{}});
      if(current())await navigate('inbox');
    };
    const [deliveries,approvals]=await Promise.allSettled([api.request('/inbox'),api.request('/approvals')]);
    if(!current())return;
    if([deliveries,approvals].some(result=>result.status==='rejected' && result.reason.status===401)){expiredSession();return;}
    const section=h('div',{class:'inbox-list'}),rows=[];
    let hasRead=deliveries.status==='fulfilled' && (deliveries.value.read_count ?? (deliveries.value.items || []).filter(item=>item.read).length)>0,bulkBusy=false;
    const bulkStatus=h('div',{class:'inbox-read-status',role:'status','aria-live':'polite'});
    const clear=button('Clear read',e=>inboxAction(e.currentTarget,async()=>{
      if(!await confirmChange('Clear read notifications?','Hide all read notifications, including older pages? Unread notifications, pending approvals and conversation history are kept. Already sent or in-flight pushes cannot be recalled.'))return;
      await mutate('/inbox/clear-read');
    }),'secondary',{disabled:!hasRead});
    const bulk=button('Mark all read',markAll,'secondary',{disabled:deliveries.status!=='fulfilled'});
    async function markAll() {
      if(!current() || bulkBusy)return;
      bulkBusy=true;bulk.disabled=true;bulkStatus.textContent='Marking all notifications read…';
      try {
        const result=await api.request('/inbox/read-all',{method:'POST',body:{}});
        if(!current())return;
        for(const row of rows)row.confirmRead();
        hasRead=hasRead || result.updated>0;clear.disabled=!hasRead;
        bulkStatus.textContent='All notifications marked read. Approvals still require your decision.';
      } catch(error) {
        if(!current())return;
        if(error.status===401){expiredSession();return;}
        bulkStatus.replaceChildren(h('span',{class:'inline-error'},errorMessage(error)),button('Retry marking all read',markAll,'quiet'));
      } finally {bulkBusy=false;if(current())bulk.disabled=false;}
    }
    const pendingApproval=item=>approvals.status==='fulfilled' && (approvals.value.items || []).find(a=>a.id===item.approval_id && a.run_id===item.run_id && a.request_id===item.request_id);
    const approvalCard=item=>[...section.querySelectorAll('[data-approval-id]')].find(el=>el.dataset.approvalId===item.approval_id);
    if(approvals.status==='fulfilled')for(const item of approvals.value.items || []) {
      const actionText=typeof item.action==='string' ? item.action : JSON.stringify(item.action || item.payload || item);
      section.append(h('article',{class:'card approval inbox-approval','data-approval-id':item.id,tabindex:'-1'},h('details',{},
        h('summary',{class:'inbox-summary inbox-approval-summary'},disclosure(),h('span',{class:'inbox-summary-text'},h('span',{class:'eyebrow'},'NEEDS YOUR REVIEW'),h('h2',{},item.title || 'Action requested'),h('span',{class:'caption'},'Expires: ',date(item.expires_at)))),
        h('div',{class:'inbox-body'},h('pre',{class:'review-payload'},actionText),h('p',{},'Target: ',typeof item.target==='string' ? item.target : JSON.stringify(item.target || 'See action details')),
        h('div',{class:'actions'},button('Approve once',e=>inboxAction(e.currentTarget,async()=>{await ceremony('verify');if(!current())return;await api.request(`/approvals/${encodeURIComponent(item.id)}/decision`,{method:'POST',body:{decision:'once'}});if(!current())return;handledApprovals.add(approvalKey(item));await navigate('inbox');}),'primary'),
        button('Deny',e=>inboxAction(e.currentTarget,async()=>{await api.request(`/approvals/${encodeURIComponent(item.id)}/decision`,{method:'POST',body:{decision:'deny'}});if(!current())return;handledApprovals.add(approvalKey(item));await navigate('inbox');}),'secondary'))))));
    } else section.append(h('p',{class:'inline-error'},'Approvals unavailable: ',errorMessage(approvals.reason)));
    if(deliveries.status==='fulfilled') {
      for(const delivery of deliveries.value.items || []) {
        const item={...delivery};let reading=false;
        const readState=h('span',{class:'eyebrow inbox-read-state'},item.read?'READ':'NEW');
        const feedback=h('div',{class:'inbox-read-status',role:'status','aria-live':'polite'});
        const dismiss=button('×',e=>{if(item.read)return inboxAction(e.currentTarget,()=>mutate(`/inbox/${encodeURIComponent(item.id)}/dismiss`));},'quiet icon-button inbox-dismiss',{'aria-label':'Dismiss',title:'Dismiss',hidden:!item.read});
        const summary=h('summary',{class:'inbox-summary'},disclosure(),h('span',{class:'inbox-summary-text'},h('strong',{},item.title || 'Notification'),h('span',{class:'inbox-summary-meta'},readState,h('time',{class:'caption'},date(item.created_at)))));
        const actions=h('div',{class:'actions'},item.approval_id ? pendingApproval(item) ? button('Review approval',()=>{if(!current())return;const card=approvalCard(item);if(card)card.querySelector('details').open=true;card?.scrollIntoView?.({block:'center'});card?.querySelector('summary')?.focus();},'secondary') : h('p',{class:'caption'},approvals.status==='fulfilled'?'This approval is no longer pending.':'Approval status unavailable. Refresh to recheck.') : item.session_id ? button('Open conversation',()=>{if(current())void action(null,()=>openSession({id:item.session_id}));},'secondary') : null);
        const details=h('details',{class:'inbox-disclosure'},summary,h('div',{class:'inbox-body'},renderMarkdown(doc,item.body),feedback,actions));
        const card=h('article',{class:`card inbox-item ${item.read?'read':'unread'}`,'data-inbox-id':item.id},details,dismiss);
        const confirmRead=()=>{
          item.read=true;hasRead=true;clear.disabled=false;
          card.classList.remove('unread');card.classList.add('read');readState.textContent='READ';dismiss.hidden=false;feedback.replaceChildren();
        };
        async function read() {
          if(!current() || item.read || reading)return;
          reading=true;feedback.textContent='Marking read…';
          try {
            await api.request(`/inbox/${encodeURIComponent(item.id)}/read`,{method:'POST',body:{}});
            if(current())confirmRead();
          } catch(error) {
            // A concurrent successful bulk read dominates a late per-item failure.
            if(!current() || item.read)return;
            if(error.status===401){expiredSession();return;}
            feedback.replaceChildren(h('span',{class:'inline-error'},errorMessage(error)),button('Retry marking read',read,'quiet'));
          } finally {reading=false;}
        }
        // Native summary activation (including keyboard) is the sole read intent.
        // Fetch, scroll, programmatic toggle, links and sibling actions are not.
        summary.addEventListener('click',event=>{if(!event.defaultPrevented && !details.open)void read();});
        rows.push({confirmRead});section.append(card);
      }
      if(!deliveries.value.items?.length)section.append(empty('No updates',''));
    } else section.append(h('p',{class:'inline-error'},'Inbox unavailable: ',errorMessage(deliveries.reason)));
    content.replaceChildren(h('div',{class:'page-heading inbox-heading'},h('div',{},h('h1',{},'Inbox')),h('div',{class:'actions'},button(icon('refresh'),()=>navigate('inbox'),'quiet icon-button',{'aria-label':'Refresh',title:'Refresh'}),bulk,clear)),bulkStatus,section);
    const target=new URL(win.location.href).searchParams.get('inbox');
    if(target){const item=deliveries.status==='fulfilled' && deliveries.value.items?.find(item=>item.id===target);const card=item?.approval_id && pendingApproval(item)?approvalCard(item):[...section.querySelectorAll('[data-inbox-id]')].find(el=>el.dataset.inboxId===target);card?.scrollIntoView?.({block:'center'});if(card?.matches('.approval'))card.focus();}
  }
  async function chats(version) {
    const owner=state.user?.id;
    const current=()=>!destroyed && version===routeVersion && owner===state.user?.id && state.view==='chats';
    let requestVersion=0,swipe=null,committed=null;
    const list=h('div',{class:'session-list'});
    // Keep one compact status line reserved without inline styles (strict CSP).
    const clearFeedback=()=>feedback.replaceChildren(h('span',{'aria-hidden':'true'},'\u00a0'));
    const feedback=h('div',{id:'conversation-list-status',class:'list-feedback caption',role:'status','aria-live':'polite'});
    clearFeedback();
    const results=h('section',{class:'session-results','aria-label':'Conversation results'},feedback,list);
    const count=h('span',{class:'caption'});
    const previous=button('Previous',()=>{if(!current())return;state.offset=Math.max(0,state.offset-30);void refresh();},'quiet',{disabled:true});
    const next=button('Next',()=>{if(!current())return;state.offset+=30;void refresh();},'quiet',{disabled:true});
    const describe=selection=>`${selection.kind[0].toUpperCase()+selection.kind.slice(1)}${selection.query ? ` matching “${selection.query}”` : ''} · Page ${Math.floor(selection.offset/30)+1}`;
    function lockStaleDeletes(message) {
      for(const control of list.querySelectorAll('[data-delete-id]')){
        control.dataset.listStale='true';control.dataset.locked='true';control.disabled=true;control.title=message;
        control.setAttribute('aria-describedby','conversation-list-status');
      }
    }
    async function refresh() {
      if(!current())return;
      const revision=++requestVersion,selection={query:state.query,kind:state.kind,offset:state.offset};
      const latest=()=>current() && revision===requestVersion;
      const fetchPage=()=>api.request(`/sessions?limit=30&offset=${selection.offset}&q=${encodeURIComponent(selection.query)}&kind=${selection.kind}`);
      results.setAttribute('aria-busy','true');
      feedback.replaceChildren(h('span',{title:`Loading ${describe(selection)}`},committed ? 'Updating results…' : 'Loading conversations…'));
      previous.disabled=true;next.disabled=true;
      lockStaleDeletes('Results updating. Wait for refreshed results before deleting.');
      try {
        let result=await fetchPage();if(!latest())return;
        if(selection.offset>0 && Number.isSafeInteger(result.total) && result.total>=0 && selection.offset>=result.total){
          selection.offset=Math.max(0,Math.floor((result.total-1)/30)*30);
          result=await fetchPage();if(!latest())return;
        }
        for(const item of Array.isArray(result.pending_deletions)?result.pending_deletions.slice(0,100):[]){
          if(typeof item?.id!=='string' || !item.id || item.status!=='unconfirmed')continue;
          const identity=deletionKey(owner,item.id);
          if(!deletions.has(identity))deletions.set(identity,'unconfirmed');
        }
        state.offset=selection.offset;committed=selection;
        renderRows(result);
        updatePendingDeletions();
        count.textContent=`${result.total ?? result.items?.length ?? 0} conversations`;
        previous.disabled=selection.offset===0;next.disabled=selection.offset+30 >= (result.total ?? 0);
        clearFeedback();
      } catch(error) {
        if(!latest())return;
        if(error.status===401){expiredSession();return;}
        lockStaleDeletes('Results could not refresh. Try again before deleting.');
        feedback.replaceChildren(h('span',{class:'inline-error'},`Couldn’t load ${describe(selection)}. ${errorMessage(error)}${committed ? ' Previous results remain below.' : ''}`),button('Try again',()=>void refresh(),'secondary'));
      } finally {if(latest())results.setAttribute('aria-busy','false');}
    }
    const search = h('input',{type:'search',name:'search',placeholder:'Search your conversations',value:state.query,'aria-label':'Search conversations'});
    const closeSearch=async()=>{
      if(!current())return;
      const needsRefresh=Boolean(state.query) || state.offset!==0;
      state.searchOpen=false;state.query='';state.offset=0;search.value='';
      searchForm.hidden=true;searchToggle.setAttribute('aria-expanded','false');searchToggle.focus();
      if(needsRefresh)await refresh();
    };
    const searchToggle=button(icon('search'),()=>{
      if(!current())return;
      if(state.searchOpen){void closeSearch();return;}
      state.searchOpen=true;searchForm.hidden=false;searchToggle.setAttribute('aria-expanded','true');search.focus();
    },'quiet icon-button',{'aria-label':'Search conversations',title:'Search conversations','aria-controls':'conversation-search','aria-expanded':String(state.searchOpen || Boolean(state.query))});
    const searchForm = h('form',{id:'conversation-search',class:'search',hidden:!state.searchOpen && !state.query,onsubmit:e => {e.preventDefault();if(!current())return;state.searchOpen=true;state.query=search.value;state.offset=0;void refresh();},onkeydown:e=>{if(e.key==='Escape'){e.preventDefault();void closeSearch();}}},search,h('button',{type:'submit',class:'quiet icon-button','aria-label':'Search',title:'Search'},icon('search')),button('×',()=>void closeSearch(),'quiet icon-button',{'aria-label':'Close search',title:'Close search'}));
    const closeFilter=(focus=false)=>{filter.hidden=true;filterToggle.setAttribute('aria-expanded','false');if(focus)filterToggle.focus();};
    const filterToggle=button(icon('settings'),()=>{
      if(!current())return;
      if(!filter.hidden){closeFilter();return;}
      filter.hidden=false;filterToggle.setAttribute('aria-expanded','true');filter.querySelector('[aria-pressed=true]')?.focus();
    },'quiet icon-button filter-toggle',{'aria-label':'Filter conversations',title:`Filter conversations: ${state.kind[0].toUpperCase()+state.kind.slice(1)}`,'aria-controls':'conversation-filter','aria-expanded':'false','data-active':String(state.kind!=='all')});
    const filter=h('div',{id:'conversation-filter',class:'session-filter',role:'group','aria-label':'Conversation filter',hidden:true,onkeydown:e=>{if(e.key==='Escape'){e.preventDefault();closeFilter(true);}}},...['chats','all','cron','tests'].map(value=>button(value[0].toUpperCase()+value.slice(1),()=>{if(!current())return;state.kind=value;state.query=search.value;state.offset=0;for(const control of filter.children)control.setAttribute('aria-pressed',String(control.dataset.kind===value));filterToggle.title=`Filter conversations: ${value[0].toUpperCase()+value.slice(1)}`;filterToggle.dataset.active=String(value!=='all');closeFilter(true);void refresh();},'filter-segment',{'data-kind':value,'aria-pressed':String(state.kind===value)})));
    const outsideFilter=e=>{if(!filter.contains(e.target) && !filterToggle.contains(e.target))closeFilter();};
    doc.addEventListener('pointerdown',outsideFilter);stopExtras=()=>{requestVersion++;doc.removeEventListener('pointerdown',outsideFilter);swipe?.destroy();};
    function renderRows(result) {
    swipe?.destroy();swipe=createSessionSwipe(doc,win);
    const visibleItems=(result.items || []).filter(session=>deletions.get(deletionKey(owner,session.id))!=='deleted');
    list.replaceChildren(...visibleItems.map(session => {
      const rowRevision=requestVersion;
      const rowCurrent=()=>current() && rowRevision===requestVersion && control.isConnected;
      const control = button('',()=>{if(current() && control.isConnected)void action(null,()=>openSession(session));},'session-row',{'aria-label':session.title || 'Untitled conversation'});
      const statuses={queued:['◷','Queued'],running:['◔','Running'],waiting_for_approval:['!','Waiting for approval'],waiting_for_clarification:['?','Waiting for an answer'],stopping:['■','Stopping'],failed:['×','Last run failed'],unknown:['?','Status unavailable'],idle:['−','Idle'],completed:['−','Idle'],cancelled:['■','Stopped']};
      const status=Object.hasOwn(statuses,session.run_status)?session.run_status:'unknown';
      control.append(h('span',{class:'session-info'},h('strong',{},session.title || 'Untitled conversation'),h('span',{class:'session-meta'},h('span',{class:'source'},session.source || 'unknown'),date(session.updated_at))),h('span',{class:'session-run-status','data-state':status,role:'img','aria-label':statuses[status][1],title:statuses[status][1]},['running','queued'].includes(status)?statusSymbol(status):statuses[status][0]));
      if(result.deletion_available!==true)return control;
      const runBusy=['queued','running','waiting_for_approval','waiting_for_clarification','stopping'].includes(status) || status==='unknown' && Boolean(session.run?.id);
      const deletionBusy=deletions.has(deletionKey(state.user?.id,session.id)) || runBusy;
      const remove=button('Delete',e=>{const target=e.currentTarget;void action(target,()=>deleteSession(session,version,rowCurrent)).finally(()=>{if(version===routeVersion && target.isConnected && doc.activeElement===doc.body)target.focus();});},'session-delete',{'aria-label':`Delete conversation: ${session.title || 'Untitled conversation'}`,'data-delete-id':session.id,title:deletionBusy?'Finish or resolve this run or deletion before deleting':'Delete conversation',disabled:deletionBusy,hidden:true});
      remove.dataset.runBusy=String(runBusy);remove.dataset.locked=String(deletionBusy);
      control.setAttribute('aria-describedby','session-action-help');
      const actions=button('⋯',null,'quiet icon-button session-actions',{'aria-label':`Conversation actions: ${session.title || 'Untitled conversation'}`,title:'Conversation actions','aria-expanded':'false'});
      const entry=h('div',{class:'session-entry'},control,actions,remove);
      swipe.attach(entry,control,remove,actions);return entry;
    }));
    if(!visibleItems.length)list.append(empty('No conversations',state.query ? 'No conversations match this search and filter.' : 'No conversations in this filter.'));
    }
    content.replaceChildren(h('div',{class:'page-heading conversation-list-heading'},h('div',{},h('h1',{},'Sessions')),h('div',{class:'history-toolbar'},searchToggle,filterToggle,button(icon('newchat'),e=>action(e.currentTarget,async()=>{if(!current())return;const session=await api.request('/sessions',{method:'POST',body:{title:'New chat'}});if(current())await openSession(session);}),'primary icon-button',{'aria-label':'New chat',title:'New chat'}),filter)),searchForm,
      h('p',{id:'session-action-help',class:'sr-only'},'Swipe left to reveal Delete, or use Conversation actions, right-click, or Shift+F10. Deletion requires confirmation.'),pendingDeletionPanel(version),results,
      h('div',{class:'pagination'},previous,count,next));
    await refresh();
  }
  function pendingDeletionPanel(version) {
    const owner=state.user?.id;
    const panel=h('section',{class:'pending-deletions stack','aria-label':'Unconfirmed deletions'});
    for(const [identity,status] of deletions){
      const [targetOwner,id]=JSON.parse(identity);
      if(targetOwner!==owner || !['unconfirmed','checking'].includes(status))continue;
      panel.append(h('article',{class:'card pending-deletion'},h('h2',{},'Deletion status unconfirmed'),h('p',{class:'pending-deletion-id'},id),
        button(status==='checking'?'Checking status…':'Check status',()=>void checkDeletion(id,owner,version),'secondary',{disabled:status==='checking'})));
    }
    panel.hidden=!panel.childNodes.length;return panel;
  }
  function updatePendingDeletions() {
    if(state.view!=='chats' || content.classList.contains('conversation'))return;
    content.querySelector('.pending-deletions')?.replaceWith(pendingDeletionPanel(routeVersion));
    for(const control of content.querySelectorAll('[data-delete-id]')){
      control.disabled=control.dataset.listStale==='true' || control.dataset.runBusy==='true' || deletions.has(deletionKey(state.user?.id,control.dataset.deleteId));
      control.dataset.locked=String(control.disabled);
    }
  }
  async function checkDeletion(id,owner,version) {
    const identity=deletionKey(owner,id);
    if(destroyed || version!==routeVersion || owner!==state.user?.id || deletions.get(identity)!=='unconfirmed')return;
    deletions.set(identity,'checking');updatePendingDeletions();
    try {
      const result=await api.request(`/sessions/${encodeURIComponent(id)}/deletion`,{method:'GET'});
      if(destroyed || owner!==state.user?.id)return;
      if(result?.id!==id || result.deleted!==true)throw new Error('Unconfirmed deletion receipt');
      await completeDeletion(id,owner);
    } catch(error){
      if(destroyed || owner!==state.user?.id)return;
      if(error.status===409){
        deletions.delete(identity);
        if(state.view==='chats' && !content.classList.contains('conversation')){
          const nextVersion=routeVersion+1;await navigate('chats');
          if(nextVersion===routeVersion && owner===state.user?.id)inform('Conversation was not deleted. Deletion was safely refused; stored history is unchanged.',true);
        }
        return;
      }
      deletions.set(identity,'unconfirmed');
      updatePendingDeletions();
      if(version===routeVersion)inform('Deletion status unconfirmed. Check status again when the connection is available.',true);
    } finally {
      // Ignored cross-account replies cannot leave the old identity busy forever.
      if(deletions.get(identity)==='checking')deletions.set(identity,'unconfirmed');
    }
  }
  async function completeDeletion(id,owner) {
    deletions.set(deletionKey(owner,id),'deleted');
    drafts.delete(id);attempts.delete(id);
    for(const name of ['draft','attempt','run'])for(const persistent of [false,true])storage.set(`hermes:${owner}:${name}:${id}`,null,persistent);
    storage.set(`hermes:${owner}:model-controls:${JSON.stringify([owner,id])}`,null);
    try{for(const name of Object.keys(win.sessionStorage)){const prefix=`hermes:${owner}:steer:`;if(name.startsWith(prefix) && name.slice(prefix.length,name.lastIndexOf(':'))===id)win.sessionStorage.removeItem(name);}}catch{}
    const sameTranscript=state.view==='chats' && content.classList.contains('conversation') && state.session?.id===id;
    const history=state.view==='chats' && !content.classList.contains('conversation');
    if(!sameTranscript && !history)return;
    if(state.session?.id===id)state.session=null;
    const nextVersion=routeVersion+1;await navigate('chats');if(nextVersion===routeVersion && owner===state.user?.id)inform('Conversation deleted.');
  }
  async function deleteSession(session,version,rowCurrent=()=>true) {
    const owner=state.user?.id,current=()=>!destroyed && version===routeVersion && owner===state.user?.id && rowCurrent();
    const identity=deletionKey(owner,session.id);
    if(!current() || deletions.has(identity))return;
    if(!await confirmChange('Delete conversation?',`Delete “${session.title || 'Untitled conversation'}”? This removes its stored history from shared Hermes storage. This cannot be undone. Close other clients first: an open conversation can recreate native history. WhatsApp messages, delivered notifications, memories, created files, logs and backups are not erased.`,{confirmLabel:'Delete conversation',destructive:true,plainText:true}) || !current())return;
    if(deletions.has(identity))return;
    deletions.set(identity,'deleting');
    updatePendingDeletions();
    try {
      const result=await api.request(`/sessions/${encodeURIComponent(session.id)}`,{method:'DELETE',body:{confirm:true}});
      if(destroyed || owner!==state.user?.id)return;
      if(result?.id!==session.id || result.deleted!==true)throw new Error('Deletion could not be confirmed. Refresh the conversation list before trying again.');
      await completeDeletion(session.id,owner);
    } catch(error){
      if(destroyed || owner!==state.user?.id)return;
      if(!error.status || error.status>=500){
        deletions.set(identity,'unconfirmed');updatePendingDeletions();
        if(current())inform('Deletion outcome is unknown. Check status to verify; do not repeat deletion.',true);
      }else{
        deletions.delete(identity);updatePendingDeletions();
        if(current())throw error;
      }
    } finally {
      if(deletions.get(identity)==='deleting')deletions.set(identity,'unconfirmed');
    }
  }
  // Header transport observation is independent of run recovery and never submits work.
  function watchConversation(session,version,node,refreshTitle) {
    const owner=state.user?.id;
    let disposed=false,revision=0,offline=win.navigator.onLine===false,transport='connecting',activity='idle',timer=null,controller=null,deadline=null,refreshBackground=null;
    let titleRevision=0,renaming=false,queuedProbe=null;
    const labels={connecting:'Connecting…',idle:'Connected · Idle',sending:'Sending',submitted:'Queued',queued:'Queued',running:'Running',waiting_for_approval:'Waiting for approval',waiting_for_clarification:'Waiting for an answer',stopping:'Stopping',stopped:'Stopped',cancelled:'Stopped',failed:'Failed',unknown:'Outcome unknown',reconnecting:'Reconnecting',disconnected:'Disconnected'};
    const active=()=>!disposed && version===routeVersion && owner===state.user?.id;
    const render=()=>{if(!active())return;const value=offline?'disconnected':transport==='connected'?activity:transport;const label=labels[value] || 'Recovering';if(node.dataset.state!==value){node.dataset.state=value;node.textContent=label;}};
    const schedule=()=>{if(active() && timer===null && !doc.hidden)timer=win.setTimeout(()=>{timer=null;void probe();},30000);};
    const success=(token=revision)=>{if(!active() || token!==revision || offline)return;revision++;transport='connected';render();return true;};
    const failure=(token=revision,value='disconnected')=>{if(!active() || token!==revision)return;revision++;transport=value;render();};
    const run=value=>{if(!active() || !value)return;revision++;activity=['completed','done'].includes(value)?'idle':Object.hasOwn(labels,value)?value:'unknown';render();};
    const snapshot=(data,token)=>{if(!active() || token!==revision)return;success(token);run(data.run?.status || data.last_run?.status || session.run_status || 'idle');};
    async function probe(backgroundOnly=false,includeTitle=true){
      if(!active() || offline){schedule();return;}
      if(controller){
        // Coalesce refresh intent, retaining the strongest requested read scope.
        queuedProbe={backgroundOnly:backgroundOnly && (queuedProbe?.backgroundOnly ?? true),includeTitle:includeTitle || (queuedProbe?.includeTitle ?? false)};
        return;
      }
      // Share this bounded watcher with receipts, even while the run stream is healthy.
      const token=revision;controller=new win.AbortController();
      const titleToken=titleRevision;
      deadline=win.setTimeout(()=>controller?.abort(),10000);
      const failed=error=>{if(active() && token===revision){if(error.status===401)expiredSession();else failure(token);}};
      try{
        // A rejected health read must not drop the shared deadline or single-flight
        // guard while the independent receipt request is still outstanding.
        await Promise.all([
          refreshBackground?.(controller.signal),
          !includeTitle || renaming ? null : refreshTitle(controller.signal,()=>active() && !renaming && titleToken===titleRevision),
          backgroundOnly || stream && transport==='connected' ? null : api.request(`/sessions/${encodeURIComponent(session.id)}/messages?latest=true&limit=1`,{signal:controller.signal}).then(data=>{if(!Array.isArray(data.items))throw new Error('Invalid conversation snapshot');snapshot(data,token);}).catch(failed)
        ]);
      }catch(error){failed(error);}
      finally{
        win.clearTimeout(deadline);deadline=null;controller=null;
        const queued=queuedProbe;queuedProbe=null;
        if(queued)void probe(queued.backgroundOnly,queued.includeTitle);else schedule();
      }
    }
    const onOffline=()=>{offline=true;revision++;transport='disconnected';render();controller?.abort();};
    const onOnline=()=>{offline=false;revision++;transport='reconnecting';render();void probe();};
    const visible=()=>{if(!doc.hidden){void probe();schedule();}else{win.clearTimeout(timer);timer=null;}};
    const focus=()=>{void probe(true);};
    win.addEventListener('offline',onOffline);win.addEventListener('online',onOnline);win.addEventListener('focus',focus);doc.addEventListener('visibilitychange',visible);
    render();schedule();
    return {token:()=>revision,success,failure,run,snapshot,rename(pending){titleRevision++;renaming=pending;},background(callback,includeTitle=true){refreshBackground=callback;void probe(true,includeTitle);},refresh:focus,destroy(){disposed=true;queuedProbe=null;controller?.abort();win.clearTimeout(timer);win.clearTimeout(deadline);win.removeEventListener('offline',onOffline);win.removeEventListener('online',onOnline);win.removeEventListener('focus',focus);doc.removeEventListener('visibilitychange',visible);}};
  }
  function releasePresenceIdentity() {releasePushIdentity?.();releasePushIdentity=null;pushIdentityKey=null;}
  async function preparePresenceIdentity(data,current) {
    releasePresenceIdentity();pushIdentity={client_id:win.crypto.randomUUID(),sequence:0};
    // The auth API exposes no device ID. Its per-device CSRF fingerprint scopes the
    // non-secret tab record without persisting the credential itself.
    if(!data.csrf_token || !win.crypto.subtle || !win.navigator.locks)return;
    let releaseClaim=null;
    try {
      const bytes=await win.crypto.subtle.digest('SHA-256',new TextEncoder().encode(data.csrf_token));
      if(!current())return;
      const scope=`hermes:${data.user.id}:push-presence:${Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join('')}`;
      let candidate;try{candidate=JSON.parse(win.sessionStorage.getItem(scope));}catch{}
      if(!candidate || !/^[a-f0-9-]{36}$/.test(candidate.client_id) || !Number.isSafeInteger(candidate.sequence) || candidate.sequence<0 || candidate.sequence>=Number.MAX_SAFE_INTEGER)candidate=pushIdentity;
      // Browsers can copy sessionStorage into a duplicated/opened tab. A held
      // origin lock ensures that clone receives a fresh ID, not the live tab's ID.
      const claim=id=>new Promise(resolve=>{void win.navigator.locks.request('hermes-push-'+id,{ifAvailable:true},lock=>{
        if(!lock || !current()){resolve(false);return;}
        return new Promise(release=>{releaseClaim=release;resolve(true);});
      }).catch(()=>resolve(false));});
      if(!await claim(candidate.client_id)){candidate=pushIdentity;if(!await claim(candidate.client_id))return;}
      if(!current()){releaseClaim?.();return;}
      win.sessionStorage.setItem(scope,JSON.stringify(candidate));pushIdentity=candidate;pushIdentityKey=scope;releasePushIdentity=releaseClaim;
    }catch{releaseClaim?.();}
  }
  function cancelPresenceRequest() {if(pushRequest){win.clearTimeout(pushRequest.deadline);pushRequest.controller.abort();pushRequest=null;}}
  function watchPushPresence(session,version) {
    const owner=state.user?.id;
    let disposed=false,focused=doc.hasFocus(),live=false,timer=null,pageHidden=false;
    const current=()=>!disposed && !destroyed && owner===state.user?.id && version===routeVersion;
    function send(visible) {
      // A delayed request can arrive after a hide/new view; the server fences sequence.
      // No response callback can renew a lease or act for a later account.
      if(owner!==state.user?.id)return;
      if(pushIdentity.sequence>=Number.MAX_SAFE_INTEGER)return;
      const body={...pushIdentity,sequence:++pushIdentity.sequence,session_id:visible?session.id:null,visible};
      // Persist the high-water mark before dispatch, including best-effort hides.
      if(pushIdentityKey)try{win.sessionStorage.setItem(pushIdentityKey,JSON.stringify(pushIdentity));}catch{pushIdentityKey=null;}
      cancelPresenceRequest();
      const request={controller:new win.AbortController(),deadline:null};pushRequest=request;
      request.deadline=win.setTimeout(()=>{if(pushRequest===request)cancelPresenceRequest();},10000);
      void api.request('/push/presence',{method:'POST',body,signal:request.controller.signal}).catch(error=>{
        if(error.status===401 && current() && pushRequest===request && !request.controller.signal.aborted)expiredSession();
      }).finally(()=>{
        win.clearTimeout(request.deadline);if(pushRequest===request)pushRequest=null;
      });
    }
    function update(heartbeat=false) {
      win.clearTimeout(timer);timer=null;
      if(!current())return;
      const visible=!pageHidden && doc.visibilityState==='visible' && focused && doc.hasFocus();
      if(visible!==live || visible && heartbeat){live=visible;send(visible);}
      if(visible)timer=win.setTimeout(()=>update(true),15000);
    }
    const focus=()=>{focused=true;update();},blur=()=>{focused=false;update();},visibility=()=>update();
    const hide=()=>{pageHidden=true;update();},show=()=>{pageHidden=false;focused=doc.hasFocus();update();};
    win.addEventListener('focus',focus);win.addEventListener('blur',blur);win.addEventListener('pagehide',hide);win.addEventListener('pageshow',show);doc.addEventListener('visibilitychange',visibility);
    update();
    return ()=>{
      if(disposed)return;disposed=true;win.clearTimeout(timer);
      // A blur may already have dispatched the only revoke. Keep its bounded
      // request alive; send(false) below supersedes an outstanding show if needed.
      win.removeEventListener('focus',focus);win.removeEventListener('blur',blur);win.removeEventListener('pagehide',hide);win.removeEventListener('pageshow',show);doc.removeEventListener('visibilitychange',visibility);
      if(live){live=false;send(false);}
    };
  }
  async function openSession(session) {
    if(deletions.get(deletionKey(state.user?.id,session.id))==='deleted'){inform('This conversation was deleted. Its stored history is no longer available.');return;}
    disconnect();leaveConversation();
    const version = ++routeVersion,owner=state.user?.id;
    const current=()=>!destroyed && version===routeVersion && owner===state.user?.id;
    const resolveTitle=!Object.hasOwn(session,'title');
    state.view='chats';state.session=session;
    for(const item of nav.children)item.setAttribute('aria-current',item.dataset.view==='chats'?'page':'false');
    const title=session.title || (resolveTitle?'Conversation':'Untitled conversation');
    const header=h('header',{class:'topbar conversation-head'},button(icon('back'),()=>navigate('chats'),'quiet',{'aria-label':'Back to chats'}),h('div',{class:'conversation-title'},h('h1',{title},title),button(icon('edit'),()=>renameSession(session),'quiet',{'aria-label':'Rename session',disabled:resolveTitle})),h('span',{class:'conversation-status',role:'status','aria-live':'polite'}));
    content.classList.add('conversation');content.replaceChildren(header,h('p',{class:'loading'},'Loading…'));
    if(resolveTitle) {
      try {
        const metadata=await api.request(`/sessions/${encodeURIComponent(session.id)}`);
        if(!current())return;
        if(metadata?.id!==session.id || (metadata.title!==null && typeof metadata.title!=='string'))throw new Error('Conversation metadata unavailable.');
        session.title=metadata.title;
        const heading=header.querySelector('h1');heading.textContent=heading.title=session.title || 'Untitled conversation';
        header.querySelector('[aria-label="Rename session"]').disabled=false;
      } catch(error) {
        if(!current())return;
        if(error.status===401){expiredSession();return;}
        const heading=header.querySelector('h1');heading.textContent=heading.title='Conversation unavailable';
        content.replaceChildren(header,h('p',{class:'inline-error',role:'status'},errorMessage(error)),button('Retry opening conversation',()=>{if(current())void action(null,()=>openSession({id:session.id}));},'secondary'));
        return;
      }
    }
    const refreshTitle=async(signal,latest)=>{
      try {
        const metadata=await api.request(`/sessions/${encodeURIComponent(session.id)}`,{signal});
        if(!current() || !latest() || signal.aborted || metadata?.id!==session.id || (metadata.title!==null && typeof metadata.title!=='string'))return;
        session.title=metadata.title;
        const heading=header.querySelector('h1');heading.textContent=heading.title=session.title?.trim() ? session.title : 'Untitled conversation';
      } catch(error) {if(current() && latest() && !signal.aborted && error.status===401)expiredSession();}
    };
    const connection=watchConversation(session,version,header.querySelector('.conversation-status'),refreshTitle);headerState=connection;
    const initial=connection.token();
    let result;
    try {result=await api.request(`/sessions/${encodeURIComponent(session.id)}/messages?latest=true&turn_boundary=true`);}
    catch(error){if(!current())return;connection.failure(initial);throw error;}
    if (!current()) return;
    connection.snapshot(result,initial);
    const messages = h('div',{class:'messages','aria-label':'Conversation'});
    let initialRestorePending=true,initialScrollAway=false,recentScrollIntent=-Infinity;
    const scrollKeys=new Set(['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' ']);
    const markInitialScrollIntent=event=>{
      if(!initialRestorePending)return;
      if(event.type==='keydown' && !scrollKeys.has(event.key))return;
      recentScrollIntent=win.performance.now();
    };
    const noteInitialScroll=()=>{
      if(!initialRestorePending || win.performance.now()-recentScrollIntent>250)return;
      if(messages.scrollHeight-messages.scrollTop-messages.clientHeight>4)initialScrollAway=true;
    };
    messages.addEventListener('wheel',markInitialScrollIntent,{passive:true});
    messages.addEventListener('touchstart',markInitialScrollIntent,{passive:true});
    messages.addEventListener('pointerdown',markInitialScrollIntent,{passive:true});
    messages.addEventListener('keydown',markInitialScrollIntent);
    messages.addEventListener('scroll',noteInitialScroll,{passive:true});
    const finishInitialRestore=()=>{initialRestorePending=false;recentScrollIntent=-Infinity;};
    const stopInitialRestoreWatch=()=>{
      finishInitialRestore();
      messages.removeEventListener('wheel',markInitialScrollIntent);
      messages.removeEventListener('touchstart',markInitialScrollIntent);
      messages.removeEventListener('pointerdown',markInitialScrollIntent);
      messages.removeEventListener('keydown',markInitialScrollIntent);
      messages.removeEventListener('scroll',noteInitialScroll);
    };
    const shouldFollowInitialRestore=()=>messages.scrollHeight-messages.scrollTop-messages.clientHeight<100 && !win.getSelection()?.toString() && (!initialRestorePending || !initialScrollAway);
    stopDisclosureReachability=observeDisclosureReachability(messages,current);
    const resultIds=new Set();
    let reflowBackground=()=>({changed:false,tail:false});
    const reminderIds=new Set();
    function renderReminders(owner) {
      if(!Array.isArray(owner.runtime_reminders))return [];
      const nodes=[];
      for(const item of owner.runtime_reminders){
        if(!item || item.role!=='tool' || item.kind!=='context_compression' || typeof item.id!=='string' || !item.id || typeof item.content!=='string' || !item.content.trim() || reminderIds.has(item.id))continue;
        reminderIds.add(item.id);
        nodes.push(markHistoryNode(renderMessage(item),{...item,run_id:owner.run_id || (owner.role===undefined?owner.id:undefined),session_id:session.id},session.id));
      }
      return nodes;
    }
    function renderPage(items=[]) {
      for(const item of items)if(item.role==='tool' && item.tool_call_id)resultIds.add(item.tool_call_id);
      const rendered=[];
      for(const item of items) {
        if(item.role==='assistant' && Array.isArray(item.public_commentary))for(const entry of item.public_commentary){
          if(typeof entry?.content==='string' && entry.content.trim())rendered.push(markHistoryNode(publicActivity(entry.content,undefined,Object.hasOwn(entry,'timestamp')?entry.timestamp:entry.observed_at),{...entry,role:'assistant',run_id:item.run_id,session_id:item.session_id || session.id},session.id));
        }
        const node=renderMessage(item.role==='assistant' ? {...item,tool_calls:(item.tool_calls || []).filter(call=>!call.id || !resultIds.has(call.id))} : item);
        if(node.nodeType===11 && !node.childNodes.length)continue;
        markHistoryNode(node,item,session.id);
        if(item.role==='assistant' && item.source==='journal' && item.run_id && item.channel!=='commentary')
          deferPublicTail(node,item.public_tail,item.observed_at,{run_id:item.run_id,session_id:item.session_id || session.id,source:item.source});
        const previous=rendered.at(-1);
        if(node.matches?.('details.tool-activity') && previous?.matches?.('details.tool-activity')) {
          previous.querySelector('.tool-rows').append(...node.querySelector('.tool-rows').children);
          refreshToolActivity(previous);
        } else rendered.push(node);
        if(item.role==='user')rendered.push(...renderReminders(item));
      }
      return rendered;
    }
    messages.append(...renderPage(result.items));
    let oldestOffset=result.offset ?? 0;
    const turnHint=h('p',{class:'caption turn-continuation',hidden:!result.turn_boundary?.truncated},'This turn continues earlier. Load older messages to see the rest.');
    messages.prepend(turnHint);
    if(oldestOffset > 0){
      const more=button('Load older messages',e=>action(e.currentTarget,async()=>{
        const limit=Math.min(100,oldestOffset),offset=oldestOffset-limit;
        const page=await api.request(`/sessions/${encodeURIComponent(session.id)}/messages?limit=${limit}&offset=${offset}&turn_boundary=true`);
        if(version !== routeVersion)return;
        const previousHeight=messages.scrollHeight,previousTop=messages.scrollTop;
        const fragment=doc.createDocumentFragment();
        const olderNodes=renderPage(page.items),earliest=turnHint.nextElementSibling;
        const seam=olderNodes.at(-1);
        if(seam?.matches?.('details.tool-activity') && earliest?.matches?.('details.tool-activity')) {
          earliest.querySelector('.tool-rows').prepend(...seam.querySelector('.tool-rows').children);
          refreshToolActivity(earliest);olderNodes.pop();
        }
        fragment.append(...olderNodes);
        turnHint.after(fragment);
        turnHint.hidden=!page.turn_boundary?.truncated;
        oldestOffset=page.offset ?? offset;
        reflowBackground();
        if(!page.items?.length || oldestOffset === 0)more.remove();
        messages.scrollTop=previousTop+messages.scrollHeight-previousHeight;
      }),'secondary');messages.prepend(more);
    }
    if (!result.items?.length) messages.append(empty('New chat',''));
    const draftKey=key(`draft:${session.id}`);
    const textarea = h('textarea',{name:'message',rows:2,placeholder:'Message Hermes…','aria-label':'Message Hermes',maxlength:32000});
    textarea.value=drafts.get(session.id) ?? storage.get(draftKey) ?? '';
    const selectedPhotos=[];
    let pendingPhotoSubmission=null;
    const photoInput=h('input',{class:'photo-input',type:'file',hidden:true,multiple:true,accept:'image/jpeg,image/png,image/webp,image/heic,image/heif,.jpg,.jpeg,.png,.webp,.heic,.heif','aria-label':'Add photos'});
    const addPhotos=button('Photos',()=>photoInput.click(),'quiet',{'aria-label':'Add photos',title:'Add up to four JPEG, PNG, or WebP photos'});
    const photoTray=h('div',{class:'photo-tray',hidden:true,'aria-label':'Selected photos'});
    const photoStatus=h('p',{class:'photo-status caption',role:'status','aria-live':'polite',hidden:true});
    const releasePreview=photo=>{if(photo.previewUrl)win.URL?.revokeObjectURL?.(photo.previewUrl);};
    const releasePhoto=async photo=>{if(photo.metadata)try{await api.request(`/sessions/${encodeURIComponent(session.id)}/attachments/${photo.metadata.id}`,{method:'DELETE'});}catch{}};
    const clearPhotos=(release=true)=>{const retained=new Set(pendingPhotoSubmission?.attachmentIds || []);for(const photo of selectedPhotos){if(release && !retained.has(photo.metadata?.id))void releasePhoto(photo);releasePreview(photo);}selectedPhotos.length=0;photoTray.replaceChildren();photoTray.hidden=true;photoInput.value='';};
    const renderPhotoSelection=()=>{
      photoTray.replaceChildren();
      selectedPhotos.forEach((photo,index)=>{
        const remove=button('Remove',event=>action(event.currentTarget,async()=>{
          if(photo.metadata)await api.request(`/sessions/${encodeURIComponent(session.id)}/attachments/${photo.metadata.id}`,{method:'DELETE'});
          if(!current())return;
          const position=selectedPhotos.indexOf(photo);
          if(position<0)return;
          releasePreview(photo);selectedPhotos.splice(position,1);photoStatus.hidden=true;renderPhotoSelection();
        }),'quiet photo-remove',{'aria-label':`Remove photo ${index+1}`});
        const inFlight=pendingPhotoSubmission?.attachmentIds.includes(photo.metadata?.id);
        remove.disabled=!!inFlight;remove.dataset.locked=String(!!inFlight);
        photoTray.append(h('div',{class:'photo-preview'},photo.previewUrl?h('img',{src:photo.previewUrl,alt:`Selected photo ${index+1}`}):h('span',{class:'caption'},'Photo selected'),remove));
      });
      photoTray.hidden=!selectedPhotos.length;
    };
    photoInput.addEventListener('change',()=>{
      const files=[...(photoInput.files || [])];
      photoInput.value='';
      if(!files.length)return;
      if(selectedPhotos.length+files.length>4){
        photoStatus.hidden=false;photoStatus.textContent='Choose no more than four photos per message.';
        return;
      }
      const supported=files.map(file=>{
        const extension=/\.([^.]+)$/.exec(file.name || '')?.[1]?.toLowerCase();
        const type=(file.type || '').toLowerCase();
        if(['image/heic','image/heif','image/heic-sequence','image/heif-sequence'].includes(type)
            || ['heic','heif'].includes(extension)){
          photoStatus.hidden=false;photoStatus.textContent='HEIC/HEIF photos are not supported yet. Choose JPEG, PNG, or WebP.';
          return null;
        }
        if(!['image/jpeg','image/png','image/webp'].includes(type)
            && !['jpg','jpeg','png','webp'].includes(extension)){
          photoStatus.hidden=false;photoStatus.textContent='Choose a JPEG, PNG, or WebP photo.';
          return null;
        }
        let previewUrl='';
        try{previewUrl=win.URL?.createObjectURL?.(file) || '';}catch{}
        return {file,previewUrl,uploadKey:win.crypto.randomUUID(),metadata:null};
      });
      if(supported.some(photo=>!photo)){for(const photo of supported)if(photo)releasePreview(photo);return;}
      selectedPhotos.push(...supported);
      photoStatus.hidden=true;photoStatus.textContent='';
      renderPhotoSelection();
    });
    const accepted=result.run || result.last_run;
    const approvalNotice=h('div',{class:'approval-notice',role:'status','aria-live':'polite',hidden:true});
    let approvalRevision=0, approvalPending=new Map(), genericApproval=false, currentApprovalRun=accepted?.id;
    const renderApprovals=()=>{const count=approvalPending.size;approvalNotice.hidden=!count && !genericApproval;approvalNotice.textContent=count?`${count} action${count===1?'':'s'} needs your review. Open Inbox to see the exact request.`:genericApproval?'An action needs your review. Open Inbox to see the exact request.':'';};
    const reconcileApprovals=async()=>{const revision=++approvalRevision;try{const data=await api.request('/approvals');if(version!==routeVersion || revision!==approvalRevision || !Array.isArray(data.items))return;const next=new Map(data.items.filter(item=>(item.run_id===currentApprovalRun || item.session_id===session.id) && !handledApprovals.has(approvalKey(item))).map(item=>[approvalKey(item),item]));for(const [key] of approvalPending)if(!next.has(key))handledApprovals.add(key);approvalPending=next;genericApproval=false;renderApprovals();if(next.size)connection.run('waiting_for_approval');}catch(error){if(version===routeVersion && error.status===401)expiredSession();}};
    approvalState={reconcile:reconcileApprovals,run(id){currentApprovalRun=id;},event(item,runId){if(version!==routeVersion || handledApprovals.has(approvalKey({...item,run_id:runId})))return false;approvalRevision++;if(item.id || item.request_id){const value={...item,run_id:runId};approvalPending.set(approvalKey(value),value);}else genericApproval=true;renderApprovals();return true;},finish(runId){approvalRevision++;for(const [key,item] of approvalPending)if(item.run_id===runId){handledApprovals.add(key);approvalPending.delete(key);}genericApproval=false;renderApprovals();}};
    win.addEventListener('focus',reconcileApprovals);stopExtras=()=>{approvalRevision++;win.removeEventListener('focus',reconcileApprovals);};
    void reconcileApprovals();
    let pending=attempts.get(session.id);
    try {pending ||= JSON.parse(storage.get(key(`attempt:${session.id}`)));}catch{}
    if(accepted?.idempotency_key && accepted.idempotency_key===pending?.idempotency_key){
      attempts.delete(session.id);storage.set(key(`attempt:${session.id}`),null);
      if(textarea.value.trim()===pending.input){textarea.value='';drafts.delete(session.id);storage.set(draftKey,null);}
    }
    let draftRevision=0;
    textarea.addEventListener('input',()=>{if(version!==routeVersion)return;draftRevision++;drafts.set(session.id,textarea.value);storage.set(draftKey,textarea.value);});
    // Capability gated steering never falls back to submitting a new turn.
    let steeringRun=null,steeringEnabled=false,steeringBusy=false,steeringStatus='idle',steeringStop=null,controlsRevision=0,clearedSteer=null,stopHeading=null,placeGuidance=null;
    const steeringOwner=state.user?.id,steeringOutcomes=new Map();
    let currentSteer=null;
    const steerRetries=h('div',{class:'steering-retries'});
    const renderSteerRetries=()=>{steerRetries.replaceChildren();if(destroyed || version!==routeVersion || state.user?.id!==steeringOwner || state.session?.id!==session.id || !steeringRun || !steeringEnabled || steeringStatus!=='running')return;for(const attempt of steeringOutcomes.values()){if(attempt.status!=='unknown' || !attempt.idempotency_key || !attempt.input)continue;const runId=steeringRun;const retry=button('Retry this steering request',()=>{if(version===routeVersion && state.user?.id===steeringOwner && steeringRun===runId && steeringOutcomes.get(attempt.idempotency_key)===attempt)void steer(attempt);},'secondary',{'aria-label':'Retry this steering request',style:'min-height:44px;min-width:44px;max-width:100%;white-space:normal'});retry.disabled=!steeringEnabled || steeringStatus!=='running' || steeringBusy;const shown=[...messages.querySelectorAll('[data-guidance-key]')].some(node=>node.dataset.guidanceRun===steeringRun && node.dataset.guidanceKey===attempt.idempotency_key);steerRetries.append(h('div',{},shown?null:h('span',{},attempt.input),retry));}};
    const reconcileSteering=attempt=>{const prior=steeringOutcomes.get(attempt.idempotency_key);if(prior && (['not_delivered','rejected'].includes(prior.status) || Number(prior.updated_at)>Number(attempt.updated_at)))return prior;steeringOutcomes.set(attempt.idempotency_key,attempt);return attempt;};
    const steerHelp=h('p',{class:'steering-help caption',id:'steering-help',hidden:true},'Send another message to guide this run.');
    const steerStatus=h('div',{class:'steering-status caption',role:'status','aria-live':'polite',hidden:true});
    const separateStop=button('Stop',e=>action(e.currentTarget,()=>steeringStop?.()),'steering-stop',{'aria-label':'Stop run',title:'Stop this run. Actions already started are not undone.',hidden:true});
    const provisionalGuidance=new WeakSet();
    const displayAttempt=(attempt,previouslyAccepted=false,ordered=false)=>{
      const runId=attempt.run_id || steeringRun;
      let bubble=[...messages.querySelectorAll('[data-guidance-key]')].find(node=>node.dataset.guidanceRun===runId && (node.dataset.guidanceKey===attempt.idempotency_key || attempt.id && node.dataset.guidanceId===attempt.id));
      if(!bubble && (attempt.status==='accepted_unconfirmed' || previouslyAccepted) && placeGuidance && attempt.idempotency_key && attempt.input){
        bubble=renderMessage({role:'user',kind:'guidance',content:attempt.input,run_id:runId,idempotency_key:attempt.idempotency_key,steering_id:attempt.id,steering_status:attempt.status,timestamp:attempt.created_at});placeGuidance(bubble);
        if(!ordered)provisionalGuidance.add(bubble);
      }
      // POST/controls placement is provisional until the first ordered SSE/replay event.
      // Reuse the node and establish its tool boundary once; later receipts only update status.
      if(bubble && ordered && provisionalGuidance.delete(bubble))placeGuidance(bubble);
      if(bubble){if(attempt.id){bubble.dataset.guidanceId=attempt.id;bubble.id=`journal:${runId}:steering:${attempt.id}`;}bubble.querySelector('.guidance-status').textContent=guidanceLabel(attempt.status);steerStatus.hidden=true;steerStatus.textContent='';return;}
      steerStatus.hidden=false;steerStatus.textContent=`${attempt.status==='accepted_unconfirmed'?'Accepted — delivery unconfirmed':attempt.status==='not_delivered'?'Not delivered — retained guidance; retry explicitly':attempt.status==='rejected'?'Steering rejected — draft retained':'Steering outcome unknown — draft retained; retry only explicitly'}: ${attempt.input || ''}`;};
    const refreshSteering=async()=>{
      const runId=steeringRun,revision=++controlsRevision;if(!runId)return;
      try{const controls=await api.request(`/runs/${encodeURIComponent(runId)}/controls`);if(version!==routeVersion || steeringRun!==runId || revision!==controlsRevision)return;steeringEnabled=controls.steering===true;const latest=controls.attempts?.filter(a=>(!a.run_id || a.run_id===runId) && (!a.session_id || a.session_id===session.id) && (!a.user_id || a.user_id===steeringOwner)).map(reconcileSteering).at(-1);if(latest){displayAttempt(latest);if(latest.status==='not_delivered' && clearedSteer?.key===latest.idempotency_key && clearedSteer.revision===draftRevision && !textarea.value){textarea.value=latest.input || '';drafts.set(session.id,textarea.value);storage.set(draftKey,textarea.value);clearedSteer=null;}}}catch{if(version!==routeVersion || steeringRun!==runId || revision!==controlsRevision)return;steeringEnabled=false;}
      composerAction.set(steeringStatus,steeringStop);
    };
    async function steer(retryAttempt){
      if(version!==routeVersion || state.user?.id!==steeringOwner || !steeringEnabled || steeringStatus!=='running' || steeringBusy || !steeringRun)return;
      if(selectedPhotos.length){steerStatus.hidden=false;steerStatus.textContent='Photos cannot be sent as guidance to an active run. Keep them selected and send them in a new message after this run finishes.';return;}
      const input=retryAttempt?.input || textarea.value.trim();if(!input)return;
      const runId=steeringRun,submittedRevision=draftRevision,attemptKey=key(`steer:${session.id}:${runId}`);
      controlsRevision++;
      const previous=currentSteer?.revision===draftRevision && steeringOutcomes.get(currentSteer.key);
      const attempt=retryAttempt || (previous?.status==='unknown' && previous.input===input?previous:{input,idempotency_key:win.crypto.randomUUID()});
      const serialized=JSON.stringify(attempt);storage.set(attemptKey,serialized);
      if(storage.get(attemptKey)!==serialized){steerStatus.hidden=false;steerStatus.textContent='Steering not sent — browser storage unavailable. Guidance retained; Stop remains available.';return;}
      currentSteer=retryAttempt?(currentSteer?.key===attempt.idempotency_key && currentSteer.revision===submittedRevision?currentSteer:null):{key:attempt.idempotency_key,revision:submittedRevision};steeringBusy=true;composerAction.set(steeringStatus,steeringStop);
      try{
        const result=await api.request(`/runs/${encodeURIComponent(runId)}/steer`,{method:'POST',body:{input,idempotency_key:attempt.idempotency_key}});
        if(version!==routeVersion || steeringRun!==runId)return;
        const saved=reconcileSteering({...attempt,...result});storage.set(attemptKey,JSON.stringify(saved));displayAttempt(saved);
        if(!retryAttempt && saved.status==='accepted_unconfirmed' && draftRevision===submittedRevision && textarea.value.trim()===input){clearedSteer={key:attempt.idempotency_key,revision:draftRevision};textarea.value='';drafts.delete(session.id);storage.set(draftKey,null);}
      }catch(error){if(version!==routeVersion || steeringRun!==runId)return;displayAttempt(reconcileSteering({...attempt,status:'unknown'}));if(error.status===401)expiredSession();}
      finally{if(version===routeVersion && steeringRun===runId){steeringBusy=false;composerAction.set(steeringStatus,steeringStop);}}
    }
    let dispatch=submit;
    const activate=()=>{if(version===routeVersion && send.isConnected)return action(send,()=>dispatch?.());};
    const send = button(icon('send'),activate,'send',{'aria-label':'Send message',title:'Send message'});
    const composerAction={activity(heading,place){stopHeading=heading;placeGuidance=place;},receive(attempt,previouslyAccepted=false){if(version!==routeVersion || state.user?.id!==steeringOwner || attempt.run_id && attempt.run_id!==steeringRun || attempt.session_id && attempt.session_id!==session.id || attempt.user_id && attempt.user_id!==steeringOwner || !attempt.idempotency_key || !attempt.input)return;displayAttempt(reconcileSteering(attempt),previouslyAccepted,true);renderSteerRetries();},attach(run){currentSteer=null;steeringOutcomes.clear();steerRetries.replaceChildren();steeringRun=run.id;steeringEnabled=false;steeringBusy=false;clearedSteer=null;steerStatus.hidden=true;void refreshSteering();},refresh:refreshSteering,set(status,stop){
      steeringStatus=status;steeringStop=stop;renderSteerRetries();
      const active=['queued','submitted','running','waiting_for_approval','waiting_for_clarification','stopping'].includes(status);
      const idle=['idle','completed','done','failed','cancelled','stopped'].includes(status);
      const supported=steeringEnabled && (active || status==='unknown');
      if(active)stopHeading?.append(separateStop);else separateStop.remove();
      separateStop.hidden=false;separateStop.disabled=!active || status==='stopping' || !stop;separateStop.dataset.locked=String(separateStop.disabled);
      steerHelp.hidden=!supported || status!=='running';
      steerHelp.textContent=steeringBusy?'Sending guidance…':'Send another message to guide this run.';
      const label=supported?'Steer current run':active?'Run in progress':idle || status==='sending'?'Send message':'Run outcome unknown';
      dispatch=supported?steer:idle?submit:null;
      send.disabled=supported?status!=='running' || steeringBusy:!idle;
      send.dataset.locked=String(send.disabled);send.dataset.state=status;
      addPhotos.disabled=supported;photoInput.disabled=supported;
      send.setAttribute('aria-label',label);send.title=status==='stopping'?'Stop requested':label;
      send.replaceChildren(icon('send'));
      if(supported)send.setAttribute('aria-describedby','steering-help');else send.removeAttribute('aria-describedby');
    }};
    const modelOwner=state.user?.id;
    const modelControls=createModelControls({doc,win,api,sessionId:session.id,userId:modelOwner,isCurrent:()=>version===routeVersion && state.user?.id===modelOwner});
    modelControls.element.className='quiet model-control-button';
    const syncModelLock=()=>{
      if(version!==routeVersion || state.user?.id!==modelOwner)return;
      let currentAttempt=attempts.get(session.id);
      try {currentAttempt ||= JSON.parse(storage.get(`hermes:${modelOwner}:attempt:${session.id}`));}catch{}
      modelControls.setLocked(!!currentAttempt);
    };
    currentModelSync=(owner,sessionId)=>{if(owner===modelOwner && sessionId===session.id)syncModelLock();};
    const modelObserver=new win.MutationObserver(syncModelLock);modelObserver.observe(send,{attributes:true,attributeFilter:['disabled','data-state']});syncModelLock();
    win.addEventListener('focus',refreshSteering);
    const previousExtras=stopExtras;stopExtras=()=>{steeringRun=null;clearPhotos();win.removeEventListener('focus',refreshSteering);modelObserver.disconnect();modelControls.destroy();previousExtras?.();};
    async function submit() {
      if(!textarea.value.trim() && !selectedPhotos.length)return;
      const input=textarea.value.trim() || 'Please describe the attached image(s), including any visible text.';
      let previous=attempts.get(session.id);
      try { previous ||= JSON.parse(storage.get(key(`attempt:${session.id}`))); } catch {}
      if(Array.isArray(previous?.attachment_ids)){
        const submittedPhotos=selectedPhotos.filter(photo=>previous.attachment_ids.includes(photo.metadata?.id));
        const changedPhotoSelection=submittedPhotos.length!==selectedPhotos.length
          || submittedPhotos.length!==previous.attachment_ids.length;
        if(changedPhotoSelection || (previous.attachment_ids.length && previous.input!==input)){
          photoStatus.hidden=false;
          photoStatus.textContent='This send has an uncertain outcome. Retry the original text and photo selection unchanged; remove any newly selected photos before retrying.';
          throw new Error('The original text and photo selection must be retried unchanged while the send outcome is uncertain.');
        }
      }
      if(previous?.input!==input && !modelControls.canSubmit())throw new Error('Saved model choice could not be checked. Reload before sending.');
      const selection=modelControls.selection();
      const attempt=previous?.input===input ? previous : {input,idempotency_key:win.crypto.randomUUID(),...(selection?{selection}:{} )};
      const submittedPhotos=Array.isArray(attempt.attachment_ids)
        ? selectedPhotos.filter(photo=>attempt.attachment_ids.includes(photo.metadata?.id))
        : [...selectedPhotos];
      attempts.set(session.id,attempt);storage.set(key(`attempt:${session.id}`),JSON.stringify(attempt));syncModelLock();
      const owner=state.user?.id;
      composerAction.set('sending');connection.run('sending');const token=connection.token();
      let run;try{
        for(let index=0;index<submittedPhotos.length;index++){
          const photo=submittedPhotos[index];
          if(photo.metadata)continue;
          photoStatus.hidden=false;photoStatus.textContent=`Uploading photo ${index+1} of ${submittedPhotos.length}…`;
          const metadata=await api.request(`/sessions/${encodeURIComponent(session.id)}/attachments`,{
            method:'POST',rawBody:photo.file,headers:{'Idempotency-Key':photo.uploadKey}});
          if(!/^[a-f0-9]{32}$/.test(metadata?.id || '') || metadata.status!=='pending')throw new Error('Photo upload response was invalid.');
          photo.metadata=metadata;
          if(!current() || !selectedPhotos.includes(photo)){void releasePhoto(photo);if(current()){connection.failure(token);composerAction.set('idle');syncModelLock();}return;}
        }
        photoStatus.hidden=false;
        photoStatus.textContent=submittedPhotos.length?'Sending message with photos…':'Sending message…';
        const attachments=Array.isArray(attempt.attachment_ids)
          ? attempt.attachment_ids : submittedPhotos.map(photo=>photo.metadata.id);
        attempt.attachment_ids=attachments;
        attempts.set(session.id,attempt);storage.set(key(`attempt:${session.id}`),JSON.stringify(attempt));
        pendingPhotoSubmission={attachmentIds:attachments,photos:submittedPhotos};
        renderPhotoSelection();
        const {attachment_ids: savedAttachmentIds,...requestAttempt}=attempt;
        run=await api.request('/runs',{method:'POST',body:{session_id:session.id,...requestAttempt,...(attachments.length?{attachments}:{})}});
        connection.success(token);
      }catch(error){
        connection.failure(token);
        const rejectedBeforeAdmission=[400,413,422,507].includes(error.status);
        if(rejectedBeforeAdmission){
          pendingPhotoSubmission=null;
          if(owner===state.user?.id && attempts.get(session.id)?.idempotency_key===attempt.idempotency_key){
            attempts.delete(session.id);storage.set(key(`attempt:${session.id}`),null);currentModelSync?.(owner,session.id);
          }
        }
        if(version===routeVersion){
          photoStatus.hidden=false;
          photoStatus.textContent=rejectedBeforeAdmission
            ? `Photo or message was not sent. Your text and selected photos are retained. ${error.message || 'Correct the issue and try again.'}`
            : `The send outcome is uncertain. Your text and submitted photos are retained; retry the original request unchanged. ${error.message || ''}`;
          renderPhotoSelection();composerAction.set('idle');syncModelLock();
        }
        throw error;
      }
      if(owner!==state.user?.id)return;
      photoStatus.hidden=true;photoStatus.textContent='';
      storage.set(key(`run:${session.id}`),run.id,true);
      if(attempts.get(session.id)?.idempotency_key===attempt.idempotency_key){
        if((drafts.get(session.id) ?? textarea.value).trim()===input){drafts.delete(session.id);storage.set(draftKey,null);}
        if(textarea.value.trim()===input)textarea.value='';
        attempts.delete(session.id);
        storage.set(key(`attempt:${session.id}`),null);
        syncModelLock();
      }
      if(version!==routeVersion)return;
      messages.querySelector('.empty')?.remove();messages.append(renderMessage({role:'user',content:input,timestamp:run.created_at,run_id:run.id,session_id:session.id,attachments:run.attachments}),...renderReminders(run));
      for(const photo of submittedPhotos){const position=selectedPhotos.indexOf(photo);if(position>=0){releasePreview(photo);selectedPhotos.splice(position,1);}}
      pendingPhotoSubmission=null;renderPhotoSelection();
      messages.scrollTop=messages.scrollHeight;
      await trackRun({...run,session_id:session.id},messages,composerAction);
    }
    const expand=button(icon('expand'),()=>{
      const start=textarea.selectionStart,end=textarea.selectionEnd;
      const scrollTop=messages.scrollTop,atBottom=messages.scrollHeight-messages.scrollTop-messages.clientHeight<100;
      const done=button('Done',()=>closeEditor?.(),'primary',{'aria-label':'Done'});
      const dialog=h('section',{class:'editor-dialog',role:'dialog','aria-modal':'true','aria-label':'Message editor','data-editor-dialog':true},h('h2',{},'Message editor'),textarea,done);
      const overlay=h('div',{class:'dialog-overlay editor-overlay'},dialog);
      const shell=root.querySelector('.app-shell');shell?.setAttribute('inert','');
      closeEditor=(focus=true)=>{const a=textarea.selectionStart,b=textarea.selectionEnd;editorSlot.prepend(textarea);overlay.remove();shell?.removeAttribute('inert');closeEditor=null;messages.scrollTop=atBottom?messages.scrollHeight:scrollTop;if(focus && textarea.isConnected){textarea.focus();textarea.setSelectionRange(a,b);}};
      dialog.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();closeEditor?.();}if(event.key==='Tab'){if(event.shiftKey && doc.activeElement===textarea){event.preventDefault();done.focus();}else if(!event.shiftKey && doc.activeElement===done){event.preventDefault();textarea.focus();}}});
      root.append(overlay);textarea.focus();textarea.setSelectionRange(start,end);
    },'quiet icon-button',{'aria-label':'Expand editor',title:'Expand editor'});
    const editorSlot=h('div',{class:'composer-editor'},textarea,expand);
    const composer=h('form',{class:'composer',onsubmit:e=>{e.preventDefault();if(dispatch===submit || dispatch===steer)activate();}},editorSlot,photoTray,photoStatus,photoInput,steerHelp,steerStatus,steerRetries,h('div',{class:'composer-bottom'},modelControls.element,addPhotos,send));
    const technical=h('details',{class:'technical-strip',hidden:true});
    const renderTelemetry=data=>{
      if(version!==routeVersion || !data)return;
      const c=data.context || {},number=value=>Number.isFinite(value) && value>=0;
      const known=value=>typeof value==='string' && value.trim() && !['unknown','unavailable','not available'].includes(value.trim().toLowerCase()) ? value.trim() : null;
      const observed=value=>number(value) && value>0 && Number.isFinite(new Date(value*1000).getTime()) ? date(value) : null;
      const used=number(c.used_tokens)?c.used_tokens:null,limit=number(c.limit_tokens) && c.limit_tokens>0?c.limit_tokens:null;
      const contextLabel=c.estimated?'Estimated context':c.source==='last_request'?'Last request context':['live','current_context'].includes(c.source)?'Current context':'Observed context';
      const context=used!==null?`${contextLabel} ${used}${limit?` / ${limit} · ${Math.round(used/limit*100)}%`:' tokens'}`:limit?`Context capacity ${limit}`:null;
      const summary=[known(data.model),known(data.provider),context].filter(Boolean);
      const metadata=data.metadata || {},details=[];
      if(known(data.model) || known(data.provider)){
        const parts=[metadata.freshness==='persisted'?'Last persisted model/provider':null,known(metadata.source)?`Source: ${known(metadata.source)}`:null,observed(metadata.observed_at)?`Observed: ${observed(metadata.observed_at)}`:null].filter(Boolean);
        if(parts.length)details.push(parts.join(' · '));
      }
      if(context){
        const parts=[known(c.source)?`Context source: ${known(c.source)}`:null,observed(c.observed_at)?`Observed: ${observed(c.observed_at)}`:null].filter(Boolean);
        if(parts.length)details.push(parts.join(' · '));
      }
      const usage=[number(data.usage?.input_tokens)?`input: ${data.usage.input_tokens}`:null,number(data.usage?.output_tokens)?`output: ${data.usage.output_tokens}`:null].filter(Boolean);
      if(usage.length)details.push(`Session lifetime ${usage.join(' · ')}`);
      technical.hidden=!summary.length && !details.length;
      technical.replaceChildren(...(technical.hidden?[]:[h('summary',{class:'technical-summary'},summary.join(' · ') || 'Session lifetime usage'),...details.map(text=>h('p',{},text))]));
    };
    content.replaceChildren(header,approvalNotice,messages,technical,composer);
    stopPushPresence=watchPushPresence(session,version);
    const backgroundCards=new Map();
    let backgroundItems=new Map();
    const backgroundOwner=state.user?.id,backgroundProfile=state.user?.profile;
    const backgroundCurrent=()=>!destroyed && version===routeVersion && backgroundOwner===state.user?.id && backgroundProfile===state.user?.profile && state.session===session && !deletions.has(deletionKey(backgroundOwner,session.id));
    const backgroundText=(value,max)=>typeof value==='string' && value.length<=max && value.trim().length>0;
    let backgroundOmitted=null;
    const clearBackground=()=>{for(const card of backgroundCards.values())card.remove();backgroundCards.clear();backgroundItems.clear();backgroundOmitted?.remove();backgroundOmitted=null;};
    reflowBackground=()=>{
      if(!backgroundCurrent())return {changed:false,tail:false};
      const groups=new Map();let changed=false,tail=false;
      const selection=win.getSelection(),focus=doc.activeElement;
      const selected=selection && !selection.isCollapsed ? {anchor:selection.anchorNode,start:selection.anchorOffset,focus:selection.focusNode,end:selection.focusOffset} : null;
      // Keep row references through group splits; native history is never
      // reordered, and re-reading every growing receipt sibling is quadratic.
      const history=[...messages.children].filter(node=>!node.dataset.backgroundId && node!==backgroundOmitted).flatMap(node=>node.matches('.tool-activity')?[...node.querySelector('.tool-rows').children]:[node]);
      const liveRuns=history.filter(node=>node.matches('.live-message'));
      // Resolve every retained prefix and split before recording anchors: a
      // later receipt can insert native text before an earlier receipt's anchor.
      for(const [id,item] of backgroundItems){
        const live=liveRuns.find(node=>node.dataset.historyRun===item.origin_run_id && node.dataset.historySession===(item.origin_session_id || item.session_id));
        if(live)live.prepareBackground?.(Object.hasOwn(item,'event_at')?item.event_at:item.created_at,backgroundCards.get(id).parentElement!==live);
        const placement=backgroundPlacement(messages,item,history);
        // Split only the background boundary, retaining native row/disclosure
        // identities. Tool rendering itself remains owned by the tool renderer.
        const row=placement.before,rows=row?.parentElement;
        if(rows?.matches('.tool-rows') && (rows.parentElement.parentElement===messages || rows.parentElement.parentElement?.matches('.live-message'))){
          const original=rows.parentElement;
          if(row!==rows.firstElementChild){
            const later=toolActivity();later.open=original.open;
            Object.assign(later.dataset,row.dataset);
            later.querySelector('.tool-rows').append(...[...rows.children].slice([...rows.children].indexOf(row)));
            original.after(later);refreshToolActivity(original);refreshToolActivity(later);
            changed=true;
          }
        }
      }
      for(const [id,item] of backgroundItems){
        const card=backgroundCards.get(id),placement=backgroundPlacement(messages,item,history);
        const rows=placement.before?.parentElement;
        if(rows?.matches('.tool-rows') && (rows.parentElement.parentElement===messages || rows.parentElement.parentElement?.matches('.live-message')))placement.before=rows.parentElement;
        const caption=card.querySelector('.background-placement');
        if(caption.textContent!==placement.label){caption.textContent=placement.label;caption.hidden=!placement.label;changed=true;}
        card.dataset.placement=placement.mode;
        const parent=placement.before?.parentNode || placement.parent || messages;
        const before=placement.before || (parent===messages?backgroundOmitted:null),boundary=before || parent;
        if(!groups.has(boundary))groups.set(boundary,{parent,before,cards:[]});
        groups.get(boundary).cards.push(card);
      }
      for(const {parent,before,cards} of groups.values()){
        const time=card=>{const item=backgroundItems.get(card.dataset.backgroundId),value=Object.hasOwn(item,'event_at')?item.event_at:item.created_at;return Number.isFinite(value)?value:Infinity;};
        // Only background peers are ordered. Ties/unknown times retain the
        // endpoint's stable snapshot order, never a guessed native sequence.
        cards.sort((a,b)=>time(a)-time(b));
        let following=before;
        for(const card of [...cards].reverse()){
          if(card.parentNode!==parent || card.nextElementSibling!==following){
            const fresh=!card.isConnected;
            parent.insertBefore(card,following);changed=true;
            if(fresh && (!before || before===backgroundOmitted) && card.dataset.placement!=='pending')tail=true;
          }
          following=card;
        }
      }
      for(const article of messages.querySelectorAll('.live-message'))article.backgroundPlaced?.();
      // Moving an existing disclosure must not discard a report selection or
      // keyboard focus (insertBefore does so in browsers without moveBefore).
      if(focus?.isConnected && doc.activeElement!==focus)focus.focus({preventScroll:true});
      if(selected?.anchor?.isConnected && selected.focus?.isConnected &&
         (selection.anchorNode!==selected.anchor || selection.anchorOffset!==selected.start || selection.focusNode!==selected.focus || selection.focusOffset!==selected.end))
        selection.setBaseAndExtent(selected.anchor,selected.start,selected.focus,selected.end);
      return {changed,tail};
    };
    composerAction.background=reflowBackground;
    const refreshBackground=async signal=>{
      if(!backgroundCurrent() || signal.aborted)return;
      try{
        const data=await api.request(`/sessions/${encodeURIComponent(session.id)}/background`,{signal});
        if(!backgroundCurrent() || signal.aborted || !Array.isArray(data?.items))return;
        // Receipts are public summaries, not native messages or agent context.
        // The backend is oldest-first. Select newest eligible receipts, then
        // restore chronological display; never truncate a child report.
        const selected=new Map();let size=0,olderOmitted=false,largeOmitted=false;
        for(let i=data.items.length-1;i>=0;i--){
          const item=data.items[i];
          if(!item || item.kind!=='background_result' || item.session_id!==session.id || item.agent_context_state!=='not_injected' || !backgroundText(item.id,1024) || !backgroundText(item.source_event_id,1024) || !backgroundText(item.title,1000) || typeof item.body!=='string' || !item.body.trim() || !Number.isFinite(item.created_at) || item.created_at<0 || selected.has(item.id))continue;
          if(item.body.length>262144){largeOmitted=true;continue;}
          if(selected.size===200){olderOmitted=true;break;}
          if(size+item.body.length>2097152){olderOmitted=true;continue;}
          selected.set(item.id,item);size+=item.body.length;
        }
        const next=new Map([...selected].reverse());
        // The endpoint is a snapshot, not native-history pagination. A reset or
        // deleted receipt must disappear, while unchanged nodes stay untouched.
        const atBottom=messages.scrollHeight-messages.scrollTop-messages.clientHeight<=4;
        const follow=atBottom && !win.getSelection()?.toString();
        // overflow-anchor is disabled for the transcript. Keep a surviving
        // visible node fixed when window eviction removes content above it.
        const viewport=messages.getBoundingClientRect();
        // Sticky live headings do not move with transcript content and cannot
        // anchor a reader when an earlier segment changes height.
        const candidates=[...messages.children].flatMap(node=>node.matches('.live-message')?[...node.children].filter(child=>!child.matches('.message-author,.live-activity-heading')):[node]);
        const anchor=candidates.find(node=>{
          if(node===backgroundOmitted || node.dataset.backgroundId && !next.has(node.dataset.backgroundId))return false;
          const rect=node.getBoundingClientRect();return rect.bottom>viewport.top && rect.top<viewport.bottom;
        });
        const anchorTop=anchor?.getBoundingClientRect().top;
        const selection=win.getSelection();
        const selectedTop=selection?.toString() && messages.contains(selection.anchorNode)
          ? selection.getRangeAt(0).getBoundingClientRect?.().top : undefined;
        let changed=false;
        for(const [id,card] of backgroundCards)if(!next.has(id)){card.remove();backgroundCards.delete(id);changed=true;}
        backgroundItems=next;
        for(const [id,item] of next){
          if(backgroundCards.has(id))continue;
          const card=h('details',{class:'card background-result','data-background-id':id,'aria-label':'Background result'},h('summary',{},disclosure(),h('strong',{class:'background-label'},'Background result'),item.title.trim()==='Background result'?null:h('span',{class:'background-title'},item.title),messageTime(Object.hasOwn(item,'event_at')?item.event_at:item.created_at)),renderMarkdown(doc,item.body),h('p',{class:'caption background-placement',hidden:true}),h('p',{class:'caption'},'No follow-up was run automatically.'));
          messages.querySelector('.empty')?.remove();backgroundCards.set(id,card);changed=true;
        }
        const placement=reflowBackground();changed ||= placement.changed;
        const omittedText=[olderOmitted?'Older background results are omitted from this view.':null,largeOmitted?'Some reports are too large to show here.':null,olderOmitted || largeOmitted?'Full reports remain stored with this conversation.':null].filter(Boolean).join(' ');
        if(omittedText){
          if(!backgroundOmitted){backgroundOmitted=h('p',{class:'caption background-omitted'});messages.querySelector('.empty')?.remove();messages.append(backgroundOmitted);}
          if(backgroundOmitted.textContent!==omittedText){backgroundOmitted.textContent=omittedText;changed=true;}
        }else if(backgroundOmitted){backgroundOmitted.remove();backgroundOmitted=null;changed=true;}
        if(changed){
          if(follow && placement.tail)messages.scrollTop=messages.scrollHeight;
          else if(Number.isFinite(selectedTop) && selection?.toString())messages.scrollTop+=selection.getRangeAt(0).getBoundingClientRect().top-selectedTop;
          else if(anchor?.isConnected)messages.scrollTop+=anchor.getBoundingClientRect().top-anchorTop;
        }
      }catch(error){
        if(!backgroundCurrent() || signal.aborted)return;
        // Older backends have no endpoint; removed targets must not leave stale
        // receipts behind. Transient errors retain the last verified snapshot.
        if(error.status===401)expiredSession();
        else if([403,404,410].includes(error.status))clearBackground();
      }
    };
    // Opening already resolved missing metadata; only the initial receipt read reuses it.
    connection.background(refreshBackground,!resolveTitle);
    void api.request(`/sessions/${encodeURIComponent(session.id)}/telemetry`).then(renderTelemetry).catch(error=>{if(version===routeVersion && error.status===401)expiredSession();});
    content.classList.add('conversation');
    const priorExtras=stopExtras;stopExtras=()=>{stopInitialRestoreWatch();priorExtras?.();};
    messages.scrollTop=messages.scrollHeight;
    if(!result.run && result.last_run)composerAction.attach(result.last_run);
    if(Object.hasOwn(result,'run')) {
      if(result.run) {
        messages.querySelector('.empty')?.remove();
        messages.append(renderMessage({role:'user',content:result.run.input,timestamp:result.run.created_at,run_id:result.run.id,session_id:session.id,attachments:result.run.attachments}),...renderReminders(result.run));
        messages.scrollTop=messages.scrollHeight;
        await trackRun(result.run,messages,composerAction,result.tool_replay,{follow:shouldFollowInitialRestore,finish:finishInitialRestore});
      } else if(result.last_run?.status==='unknown') {
        composerAction.set('unknown');
        inform('Outcome unknown. Check the conversation before submitting again; Hermes will not replay this action.',true);
      } else storage.set(key(`run:${session.id}`),null,true);
    } else {
      const runId=storage.get(key(`run:${session.id}`),true);
      if(runId) {try {
        const run=await api.request(`/runs/${encodeURIComponent(runId)}`);
        if(version===routeVersion)await trackRun(run,messages,composerAction,null,{follow:shouldFollowInitialRestore,finish:finishInitialRestore});
      }catch(error){if(version===routeVersion){if(error.status===401)expiredSession();else inform(errorMessage(error),true);}}}
    }
  }
  const finalStates = new Set(['completed','done','failed','cancelled','stopped','unknown']);
  async function trackRun(run,messages,composerAction,replay=null,initialRestore={}) {
    disconnect();
    const version=routeVersion,connection=headerState;
    const sessionId=run.session_id || state.session.id;
    const atTail=()=>messages.scrollHeight-messages.scrollTop-messages.clientHeight<100 && !win.getSelection()?.toString();
    const followTail=()=>initialRestore.follow?.() ?? atTail();
    approvalState?.run(run.id);
    composerAction.attach?.(run);
    storage.set(key(`run:${sessionId}`),run.id,true);
    const status=h('span',{role:'status','aria-hidden':'true'},run.status || 'submitted');
    const output=h('div',{class:'message-body'});
    let tools=toolActivity();
    let splitTools=false;
    const commentaryIds=new Set();
    const publicText=data=>![data.channel,data.phase].some(value=>['analysis','reasoning'].includes(value)) && !['analysis','reasoning','reasoning_content'].some(key=>Object.hasOwn(data,key)) && typeof (data.text ?? data.delta)==='string' ? (data.text ?? data.delta) : '';
    let deltaChunks=[];
    const retainPublic=(text,time,chunks)=>{if(!text.trim())return;output.before(markHistoryNode(publicActivity(text,chunks,time),{timestamp:time,run_id:run.id,session_id:sessionId},sessionId));splitTools=true;};
    const flushPublic=()=>{if(output.textContent.trim()){retainPublic(output.textContent,output.dataset.historyTime===undefined?undefined:Number(output.dataset.historyTime),deltaChunks);output.textContent='';deltaChunks=[];delete output.dataset.historyTime;}};
    const pendingTools=[];
    let currentStatus=run.status || 'submitted',stopPending=false;
    const stop=async()=>{
      if(version!==routeVersion || !article.isConnected || stopPending || !['queued','submitted','running','waiting_for_approval','waiting_for_clarification'].includes(currentStatus))return;
      clarificationRevision++;stopPending=true;composerAction.set('stopping');
      for(const item of clarificationRecords.values())
        if(item.status==='pending')renderClarification(item);
      try{
        const result=await api.request(`/runs/${encodeURIComponent(run.id)}/stop`,{method:'POST',body:{}});
        if(version!==routeVersion || finalStates.has(currentStatus) || !article.isConnected)return;
        apply({...result,status:result.status || 'stopping'});
        inform('Stop requested. This does not undo actions that have already started.');
      }catch(error){
        if(version!==routeVersion || finalStates.has(currentStatus) || !article.isConnected)return;
        stopPending=false;composerAction.set(currentStatus,stop);
        for(const item of clarificationRecords.values())
          if(item.status==='pending')renderClarification(item);
        throw error;
      }
    };
    const article=h('article',{class:'message assistant-message live-message','data-history-run':run.id,'data-history-session':run.session_id},h('div',{class:'message-author'},'Hermes',messageTime(run.created_at,'Started')),h('div',{class:'live-activity-heading'},h('strong',{},'Activity'),status),tools,output);
    const clarificationOwner=state.user?.id;
    const clarificationCards=new Map(),clarificationTimes=new Map();
    const clarificationRecords=new Map();
    let currentClarificationQuestion=null,currentClarificationCreatedAt=null;
    let clarificationRevision=0,clarificationReconcileSequence=0;
    const clarificationSubmitting=new Set();
    const clarificationRetryTimes=new Map();
    let clarificationAvailable=false,clarificationUnavailable=false;
    const renderClarification=data=>{
      if(!data || data.run_id!==run.id || data.session_id!==sessionId
          || typeof data.question_id!=='string' || !/^[a-f0-9]{32}$/.test(data.question_id)
          || typeof data.question!=='string' || !data.question.trim()
          || !['pending','sending','answered','cancelled','expired','unknown'].includes(data.status)
          || !Number.isFinite(data.updated_at))return;
      const previous=clarificationTimes.get(data.question_id);
      if(Number.isFinite(previous?.updated_at)
          && (previous.updated_at>data.updated_at
              || previous.updated_at===data.updated_at
                  && ['answered','cancelled','expired','unknown'].includes(previous.status)
                  && data.status==='pending'))return;
      const followTail=messages.scrollHeight-messages.scrollTop-messages.clientHeight<100
        && !win.getSelection()?.toString();
      clarificationTimes.set(data.question_id,{updated_at:data.updated_at,status:data.status});
      clarificationRecords.set(data.question_id,data);
      let card=clarificationCards.get(data.question_id);
      if(!card){
        flushPublic();
        card=h('section',{class:'clarification-card','data-clarification-id':data.question_id,'aria-labelledby':`clarification-title-${data.question_id}`});
        clarificationCards.set(data.question_id,card);output.before(card);
      }
      const heading=h('h3',{id:`clarification-title-${data.question_id}`},'Question');
      const question=h('p',{class:'clarification-question'},data.question);
      let body;
      if(data.status==='pending' && clarificationAvailable && !stopPending
          && currentStatus==='waiting_for_clarification'
          && currentClarificationQuestion===data.question_id){
        const form=h('form',{class:'clarification-form',onsubmit:event=>{event.preventDefault();void sendClarification(data,form,submit,feedback);}});
        const inputs=[];
        let otherInput=null,otherText=null,freeText=null;
        if(Array.isArray(data.choices) && data.choices.length){
          const fieldset=h('fieldset',{class:'clarification-options'});
          fieldset.append(h('legend',{},data.multi_select?'Select one or more answers':'Choose an answer'));
          for(const [index,choice] of data.choices.entries()){
            if(typeof choice!=='string' || !choice.trim() || choice.length>512)continue;
            const input=h('input',{type:data.multi_select?'checkbox':'radio',
              name:`clarification-${data.question_id}`,value:choice});
            inputs.push(input);
            fieldset.append(h('label',{class:'clarification-option'},input,
              h('span',{},choice),index===0?h('span',{class:'clarification-recommended'},'Recommended'):null));
          }
          const otherType=data.multi_select?'checkbox':'radio';
          otherInput=h('input',{type:otherType,name:`clarification-${data.question_id}`,
            value:'__other__'});
          if(!data.multi_select)inputs.push(otherInput);
          otherText=h('input',{type:'text',maxlength:32768,autocomplete:'off',
            'aria-label':'Other answer',placeholder:'Write another answer'});
          otherText.addEventListener('focus',()=>{otherInput.checked=true;});
          inputs.push(...(data.multi_select?[otherInput]:[]));
          fieldset.append(h('label',{class:'clarification-option'},otherInput,
            h('span',{},'Other'),otherText));
          form.append(fieldset);
        }else{
          freeText=h('textarea',{rows:2,maxlength:32768,
            'aria-label':'Your answer',placeholder:'Write your answer'});
          form.append(h('label',{class:'clarification-free-text'},h('span',{},'Your answer'),freeText));
        }
        const feedback=h('p',{class:'clarification-feedback',role:'status','aria-live':'polite'});
        const submit=button('Submit answer',null,'primary',{type:'submit'});
        form.append(submit,feedback);
        body=form;
        const sendClarification=async(item,currentForm,control,statusNode)=>{
          const selected=[...currentForm.querySelectorAll('input[type=radio]:checked,input[type=checkbox]:checked')];
          let answer,other=false;
          if(freeText)answer=freeText.value.trim();
          else if(data.multi_select){
            const choices=selected.filter(input=>input!==otherInput).map(input=>input.value);
            if(otherInput.checked){const value=otherText.value.trim();if(value)choices.push(value);other=true;}
            answer=choices;
          }else{
            const choice=selected[0];
            if(choice===otherInput){answer=otherText.value.trim();other=true;}
            else answer=choice?.value;
          }
          if((typeof answer==='string' && !answer) || (Array.isArray(answer) && !answer.length)
              || answer===undefined || (typeof answer==='string' && answer.length>32768)
              || (Array.isArray(answer) && (answer.length>5 || answer.some(value=>value.length>32768)))){
            statusNode.textContent='Choose or enter an answer before submitting.';
            return;
          }
          if(other && !otherText.value.trim()){
            statusNode.textContent='Enter your other answer before submitting.';
            otherText.focus();return;
          }
          if(stopPending || currentStatus!=='waiting_for_clarification'
              || currentClarificationQuestion!==item.question_id)return;
          const answerRevision=++clarificationRevision;
          const sameRoute=()=>version===routeVersion && state.user?.id===clarificationOwner
            && state.session?.id===sessionId && run.id===article.dataset.historyRun
            && article.isConnected;
          const answerStillCurrent=()=>sameRoute()
            && clarificationRevision===answerRevision && !stopPending
            && currentStatus==='waiting_for_clarification'
            && currentClarificationQuestion===item.question_id;
          clarificationSubmitting.add(item.question_id);
          for(const field of currentForm.elements)field.disabled=true;
          control.disabled=true;statusNode.textContent='Submitting answer…';
          try{
            const result=await api.request(`/runs/${encodeURIComponent(run.id)}/clarifications/${encodeURIComponent(item.question_id)}/answer`,
              {method:'POST',body:{answer,other}});
            if(!sameRoute())return;
            if(result?.question_id!==item.question_id || result?.run_id!==run.id
                || !['answered','unknown'].includes(result.status)
                || !Number.isFinite(result.updated_at))throw new Error('Clarification acknowledgement was invalid.');
            if(result.status==='answered'){
              const continuationIsCurrent=answerStillCurrent();
              renderClarification({...item,status:'answered',
                answer:result.answer ?? answer,other:typeof result.other==='boolean'?result.other:other,
                updated_at:result.updated_at});
              if(continuationIsCurrent){
                currentClarificationQuestion=null;
                currentClarificationCreatedAt=null;
                apply({status:'running'});connection?.run('running');
              }
            }else if(answerStillCurrent()){
              renderClarification({...item,status:'unknown',answer,other,
                updated_at:result.updated_at});
            }
          }catch(error){
            if(!sameRoute())return;
            if(error.status===401){expiredSession();return;}
            if(!answerStillCurrent())return;
            if(error.status===422 && error.message==='Invalid clarification answer'){
              for(const field of currentForm.elements)field.disabled=false;
              statusNode.textContent='That answer did not pass validation. Review your selections and Other text, then correct it here.';
              return;
            }
            if(error.status===503 && error.code==='clarification_not_sent'){
              clarificationUnavailable=true;
              for(const field of currentForm.elements)field.disabled=false;
              statusNode.textContent='Clarification is unavailable. This answer was not sent; your draft is retained. Retry only when clarification is available.';
              return;
            }
            if(error.status===409 || error.status===404){
              const reconciled=await reconcileClarifications({preserveQuestionId:item.question_id});
              if(!sameRoute() || clarificationRevision!==answerRevision || stopPending)return;
              if(reconciled?.status)apply({status:reconciled.status});
              const current=reconciled?.items?.find(value=>
                value.question_id===item.question_id);
              if(current?.status==='pending' && currentClarificationQuestion===item.question_id
                  && reconciled.available===true && reconciled.status==='waiting_for_clarification'
                  && !stopPending){
                for(const field of currentForm.elements)field.disabled=false;
                statusNode.textContent='Answer was not sent; this question is still waiting. Review your draft and submit again to retry.';
                return;
              }
              if(current?.status==='pending'){
                for(const field of currentForm.elements)field.disabled=false;
                control.disabled=true;
                statusNode.textContent='Answer was not sent. Clarification is unavailable or the current run no longer permits an answer; your draft is retained.';
                return;
              }
              if(!current){
                for(const field of currentForm.elements)field.disabled=false;
                control.disabled=true;
                statusNode.textContent='Answer was not sent, but the current question could not be verified. Your draft is retained; reopen this session to check.';
              }
              return;
            }
            renderClarification({...item,status:'unknown',answer,other,updated_at:item.updated_at+0.001});
          }finally{
            clarificationSubmitting.delete(item.question_id);
          }
        };
      }else{
        const labels={pending:'Waiting for an answer',sending:'Submitting answer…',
          answered:'Answered',cancelled:'Cancelled',expired:'Expired',
          unknown:'Answer status unknown'};
        body=h('div',{class:'clarification-state',role:'status','aria-live':'polite'},
          h('strong',{},labels[data.status] || 'Clarification unavailable'));
        if(data.status==='answered' && (typeof data.answer==='string' || Array.isArray(data.answer)))
          body.append(h('p',{class:'clarification-answer'},'Answer: ',
            Array.isArray(data.answer)?data.answer.join(', '):data.answer));
        if(['sending','unknown'].includes(data.status)
            && (typeof data.answer==='string' || Array.isArray(data.answer)))
          body.append(h('p',{class:'clarification-answer'},'Attempted answer: ',
            Array.isArray(data.answer)?data.answer.join(', '):data.answer));
        if(data.status==='sending')body.append(h('p',{},'Checking the answer acknowledgement. Do not submit again.'));
        if(data.status==='unknown')body.append(h('p',{},'The answer cannot be confirmed. Reopen the session to check; it will not be resent automatically.'));
        if(data.status==='expired')body.append(h('p',{},'The question expired before an answer was accepted.'));
        if(data.status==='cancelled')body.append(h('p',{},'The question was cancelled with the run.'));
        if(data.status==='pending' && clarificationAvailable)
          body.append(h('p',{},'This question is no longer current or the run cannot accept an answer.'));
        if(data.status==='pending' && clarificationUnavailable)
          body.append(h('p',{},'Clarification controls are unavailable. No answer was sent.'));
      }
      card.replaceChildren(heading,question,body);
      if(followTail)messages.scrollTop=messages.scrollHeight;
    };
    const reconcileClarifications=async({preserveQuestionId=null}={})=>{
      const revision=clarificationRevision;
      const sequence=++clarificationReconcileSequence;
      try{
        const result=await api.request(`/runs/${encodeURIComponent(run.id)}/clarifications`);
        if(version!==routeVersion || state.user?.id!==clarificationOwner
            || state.session?.id!==sessionId || run.id!==article.dataset.historyRun
            || !article.isConnected || stopPending || clarificationRevision!==revision
            || sequence!==clarificationReconcileSequence
            || !Array.isArray(result?.items))return null;
        clarificationAvailable=result.available===true;
        clarificationUnavailable=!clarificationAvailable;
        const validStatuses=['queued','submitted','running','waiting_for_approval',
          'waiting_for_clarification','stopping','completed','done','failed',
          'cancelled','stopped','unknown'];
        const status=result.run_id===run.id && validStatuses.includes(result.status)
          ?result.status:null;
        if(status)currentStatus=status;
        const newestTime=Math.max(...result.items.map(item=>item.created_at)
          .filter(Number.isFinite));
        const newest=result.items.filter(item=>item.created_at===newestTime);
        currentClarificationQuestion=status==='waiting_for_clarification'
          && newest.length===1 && newest[0].status==='pending'
          ?newest[0].question_id:null;
        currentClarificationCreatedAt=currentClarificationQuestion
          ?newest[0].created_at:null;
        for(const item of result.items){
          const preservePending=item.status==='pending'
            && currentClarificationQuestion===item.question_id
            && status==='waiting_for_clarification' && clarificationAvailable
            && (item.question_id===preserveQuestionId
              || clarificationSubmitting.has(item.question_id));
          if(preservePending){
            clarificationTimes.set(item.question_id,{updated_at:item.updated_at,status:item.status});
            clarificationRecords.set(item.question_id,item);
            clarificationRetryTimes.set(item.question_id,item.updated_at);
          }else renderClarification(item);
        }
        if(!clarificationAvailable && currentStatus==='waiting_for_clarification'){
          const notice=h('section',{class:'clarification-card clarification-unavailable',role:'status'},
            h('strong',{},'Clarification unavailable'),
            h('p',{},'This native runtime cannot confirm or answer the pending question. No answer was sent.'));
          article.append(notice);
        }
        return {...result,status};
      }catch(error){
        if(version!==routeVersion || state.user?.id!==clarificationOwner
            || state.session?.id!==sessionId || !article.isConnected
            || clarificationRevision!==revision
            || sequence!==clarificationReconcileSequence)return null;
        clarificationUnavailable=true;
        if(error.status===401)expiredSession();
        if(currentStatus==='waiting_for_clarification'){
          article.append(h('section',{class:'clarification-card clarification-unavailable',role:'status'},
            h('strong',{},'Clarification status unavailable'),
            h('p',{},'The pending question could not be verified. Reopen this session to check; no answer was sent.')));
        }
        return null;
      }
    };
    article.backgroundPlaced=()=>{tools=[...article.children].filter(node=>node.matches('.tool-activity')).at(-1) || tools;splitTools=output.previousElementSibling!==tools;};
    article.prepareBackground=(time,fresh)=>{
      const crossing=Number.isFinite(time) && deltaChunks.some(chunk=>Number.isFinite(chunk.observed_at) && chunk.observed_at<=time) && deltaChunks.some(chunk=>Number.isFinite(chunk.observed_at) && chunk.observed_at>time);
      if(!finalStates.has(currentStatus) && (fresh || crossing))flushPublic();
    };
    composerAction.activity?.(article.querySelector('.live-activity-heading'),node=>{const atBottom=followTail();flushPublic();output.before(node);splitTools=true;if(atBottom)messages.scrollTop=messages.scrollHeight;});
    const wasAtBottom=followTail();
    messages.append(article);
    if(wasAtBottom)messages.scrollTop=messages.scrollHeight;
    let seeding=true;
    const renderTool=data=>{
      const atBottom=!seeding && followTail();
      const name=data.name || data.tool || data.tool_name || 'Tool';
      const id=data.tool_call_id || data.toolCallId;
      const status=toolStatus(data);
      const index=pendingTools.findIndex(item=>id ? item.id===id : item.name===name);
      const summary=data.summary || (index>=0 && status!=='running' ? pendingTools[index].summary : '');
      const next=markHistoryNode(toolPreview({...data,summary}),{timestamp:data.observed_at,run_id:run.id,session_id:sessionId},sessionId);
      if(index>=0 && status!=='running') {
        const card=pendingTools[index].el.closest('.tool-activity');
        // A completion changes outcome, not the original receipt boundary.
        delete next.dataset.historyTime;for(const [key,value] of Object.entries(pendingTools[index].el.dataset))if(key.startsWith('history'))next.dataset[key]=value;
        pendingTools[index].el.replaceWith(next);pendingTools.splice(index,1);if(!seeding)refreshToolActivity(card);
      } else {
        flushPublic();
        if(splitTools){tools=toolActivity();output.before(tools);splitTools=false;}
        tools.querySelector('.tool-rows').append(next);
        if(status==='running')pendingTools.push({id,name,summary,el:next});
      }
      if(!seeding)refreshToolActivity(tools);
      if(atBottom)messages.scrollTop=messages.scrollHeight;
    };
    const renderCommentary=data=>{
      const text=publicText(data);if(!text || (data.id && commentaryIds.has(data.id)))return;
      const atBottom=!seeding && followTail();
      flushPublic();retainPublic(text,data.observed_at);if(data.id)commentaryIds.add(data.id);
      if(atBottom)messages.scrollTop=messages.scrollHeight;
    };
    const renderDelta=data=>{
      const atBottom=!seeding && followTail();
      if(!output.textContent && publicText(data))markHistoryNode(output,{timestamp:data.observed_at,run_id:run.id,session_id:sessionId},sessionId);
      const text=publicText(data);if(text){const node=doc.createTextNode(text);deltaChunks.push({text,observed_at:data.observed_at,node});output.append(node);}
      if(atBottom)messages.scrollTop=messages.scrollHeight;
    };
    const seeded=replay?.run_id===run.id && Array.isArray(replay.events) && Number.isSafeInteger(replay.cursor) && replay.cursor>=0;
    const replayCursor=seeded?replay.cursor:0;
    if(seeded)for(const event of replay.events){
      if(!Number.isSafeInteger(event.id) || event.id<=0 || event.id>replayCursor || !event.data || typeof event.data!=='object')continue;
      const data={...event.data,observed_at:event.observed_at};
      if(event.name==='tool')renderTool(data);
      else if(event.name==='commentary')renderCommentary(data);
      else if(event.name==='delta')renderDelta(data);
      else if(event.name==='steering')composerAction.receive?.(event.data,true);
      else if(event.name==='clarification')renderClarification({...event.data,observed_at:event.observed_at});
    }
    seeding=false;
    if(seeded)for(const card of article.querySelectorAll('.tool-activity'))refreshToolActivity(card);
    const apply=current=>{
      const statusChanged=current.status && current.status!==currentStatus;
      if(statusChanged)clarificationRevision++;
      const terminal=finalStates.has(current.status);
      if(terminal){
        const author=article.querySelector(':scope > .message-author'),prior=author.querySelector('time');
        const recorded=messageTime(current.updated_at);
        // A sparse terminal update must never relabel the start as a sent time.
        if(recorded){prior?.remove();author.append(recorded);}
        else if(!finalStates.has(currentStatus) || prior?.textContent.startsWith('Started '))prior?.remove();
      }
      currentStatus=current.status || currentStatus;
      if(statusChanged && (currentStatus==='stopping' || finalStates.has(currentStatus))){
        for(const item of clarificationRecords.values())
          if(item.status==='pending')renderClarification(item);
      }
      composerAction.set(stopPending && !finalStates.has(currentStatus)?'stopping':currentStatus,stop);
      const atBottom=followTail();
      status.textContent=current.status || status.textContent;connection?.run(current.status);
      if(current.status==='running')void approvalState?.reconcile();
      // Only a terminal output supersedes streamed text. Active snapshots may
      // carry stale output; committed Progress already lives outside this tail.
      if(current.output != null && (finalStates.has(current.status) || !deltaChunks.length)){
        if(finalStates.has(current.status))deferPublicTail(output,deltaChunks,current.updated_at,{run_id:run.id,session_id:sessionId});
        deltaChunks=[];
        output.textContent=typeof current.output==='string' ? current.output : JSON.stringify(current.output);
        delete output.dataset.historyTime;markHistoryNode(output,{timestamp:current.updated_at,run_id:run.id,session_id:sessionId},sessionId);
      }
      if(current.error)inform(typeof current.error==='string' ? current.error : 'The run failed.',true);
      if(finalStates.has(current.status)) {
        article.dataset.historyFinal='true';
        approvalState?.finish(run.id);
        void composerAction.refresh?.();
        for(const item of pendingTools.splice(0)){
          const next=toolPreview({name:item.name,summary:item.summary,status:'unknown'});
          for(const [key,value] of Object.entries(item.el.dataset))if(key.startsWith('history'))next.dataset[key]=value;
          item.el.replaceWith(next);
        }
        for(const card of article.querySelectorAll('.tool-activity'))refreshToolActivity(card);
        disconnect();
        connection?.refresh();
        if(current.status==='unknown')inform('Outcome unknown. Check the conversation before submitting again; Hermes will not replay this action.',true);
        else storage.set(key(`run:${sessionId}`),null,true);
      }
      if(atBottom)messages.scrollTop=messages.scrollHeight;
    };
    const reconciledClarifications=await reconcileClarifications();
    if(version!==routeVersion || state.user?.id!==clarificationOwner
        || state.session?.id!==sessionId || run.id!==article.dataset.historyRun
        || !article.isConnected){
      initialRestore.finish?.();
      return;
    }
    if(reconciledClarifications?.status)
      run={...run,status:reconciledClarifications.status};
    apply(run);
    if(wasAtBottom && version===routeVersion && messages.isConnected && !win.getSelection()?.toString())messages.scrollTop=messages.scrollHeight;
    initialRestore.finish?.();
    if(finalStates.has(currentStatus))return;
    const events=win.EventSource ? new win.EventSource(`/hermes/app-api/runs/${encodeURIComponent(run.id)}/events`) : {addEventListener(){},close(){}};
    stream=events;
    const connectionNotice={};
    let previousNotice=null;
    const connectionInfo=(message,error)=>{
      if(noticeOwner!==connectionNotice)previousNotice={message:notice.textContent,error:notice.classList.contains('error'),owner:noticeOwner};
      inform(message,error,connectionNotice);
    };
    let timer=null,deadline=null,controller=null,disposed=false,polling=false,retryDelay=15000,errorRecoveryStarted=false;
    const wake=()=>{if(timer!==null)win.clearTimeout(timer);timer=null;void reconcile();};
    const visible=()=>{if(doc.visibilityState!=='hidden')wake();};
    win.addEventListener('pageshow',wake);win.addEventListener('focus',wake);doc.addEventListener('visibilitychange',visible);
    cancelTracking=()=>{disposed=true;controller?.abort();if(deadline!==null)win.clearTimeout(deadline);if(timer!==null)win.clearTimeout(timer);timer=null;win.removeEventListener('pageshow',wake);win.removeEventListener('focus',wake);doc.removeEventListener('visibilitychange',visible);};
    const schedule=(delay=retryDelay)=>{if(!disposed && timer===null)timer=win.setTimeout(reconcile,delay);};
    async function reconcile(){
      timer=null;
      if(disposed || version!==routeVersion || polling)return;
      polling=true;
      const token=connection?.token();
      controller=new win.AbortController();
      deadline=win.setTimeout(()=>controller?.abort(),10000);
      try {
        const current=await api.request(`/runs/${encodeURIComponent(run.id)}`,{signal:controller.signal});
        if(disposed || version!==routeVersion)return;
        if(current.id!==run.id || (current.session_id && current.session_id!==sessionId))throw new Error('Run identity mismatch.');
        // Fresh terminal reads still reconcile offline; newer events defer them to the next poll.
        const currentRevision=token===connection?.token();
        if(connection?.success(token))connection.run(current.status);
        if(currentRevision && (finalStates.has(current.status) || current.status==='stopping')) {recovered();apply(current);}
      } catch(error) {
        if(!disposed && version===routeVersion) {
          const currentRevision=token===connection?.token();
          connection?.failure(token);
          if(error.status===401)expiredSession();
          else if(error.status===404 && currentRevision){
            apply({status:'failed'});
            inform('This run is no longer available. Check the conversation before sending again.',true);
          }
        }
      } finally {
        win.clearTimeout(deadline);deadline=null;controller=null;
        polling=false;retryDelay=Math.min(retryDelay*2,30000);schedule();
      }
    }
    const recovered=()=>{
      if(events===stream && noticeOwner===connectionNotice)inform(previousNotice?.message || '',previousNotice?.error || false,previousNotice?.owner ?? null);
      previousNotice=null;
    };
    events.onopen=()=>{if(events!==stream)return;connection?.success();connection?.refresh();recovered();};
    // Keep IDs with this rendered run, across EventSource reconnects, not across views.
    const renderedIds=new Set();
    const on=(name,fn)=>events.addEventListener(name,event=>{
      if(events!==stream || !event.data || (event.lastEventId && (renderedIds.has(event.lastEventId) || (Number.isSafeInteger(Number(event.lastEventId)) && Number(event.lastEventId)<=replayCursor))))return;
      try {
        const data=JSON.parse(event.data);
        connection?.success();recovered();
        fn(data);
        composerAction.background?.();
        if(event.lastEventId)renderedIds.add(event.lastEventId);
      }catch{inform('A live update could not be read. Reopen the conversation to check the saved result.',true);}
    });
    on('status',apply);
    on('steering',data=>{composerAction.receive?.(data);void composerAction.refresh?.();});
    on('steer',data=>{composerAction.receive?.(data);void composerAction.refresh?.();});
    on('commentary',renderCommentary);
    on('delta',renderDelta);
    on('tool',renderTool);
    on('approval',data=>{if(!approvalState?.event(data,run.id))return;apply({status:'waiting_for_approval'});connection?.run('waiting_for_approval');status.textContent='Waiting for approval';if(data.id || data.request_id)void approvalState.reconcile();});
    on('clarification',data=>{
      if(data.run_id!==run.id || data.session_id!==sessionId
          || !Number.isFinite(data.updated_at))return;
      const previous=clarificationTimes.get(data.question_id);
      if(previous && (data.updated_at<previous.updated_at
          || data.updated_at===previous.updated_at && data.status!==previous.status))return;
      if(['pending','sending','unknown'].includes(data.status)
          && (data.updated_at<=clarificationRetryTimes.get(data.question_id)
            || clarificationSubmitting.has(data.question_id)
              && currentClarificationQuestion===data.question_id
              && currentStatus==='waiting_for_clarification' && !stopPending))return;
      clarificationRevision++;
      if(data.status==='pending'){
        const previousQuestion=currentClarificationQuestion;
        if(currentClarificationQuestion===data.question_id
            || currentClarificationQuestion===null
            || Number.isFinite(data.created_at)
                && Number.isFinite(currentClarificationCreatedAt)
                && data.created_at>currentClarificationCreatedAt){
          currentClarificationQuestion=data.question_id;
          currentClarificationCreatedAt=data.created_at;
        }else if(data.created_at===currentClarificationCreatedAt
            && currentClarificationQuestion!==data.question_id){
          currentClarificationQuestion=null;
          currentClarificationCreatedAt=null;
        }
        if(previousQuestion && previousQuestion!==currentClarificationQuestion){
          const previous=clarificationRecords.get(previousQuestion);
          if(previous?.status==='pending')renderClarification(previous);
        }
        apply({status:'waiting_for_clarification'});
        connection?.run('waiting_for_clarification');
      }
      renderClarification(data);
      if(data.status==='answered'){
        const continuationIsCurrent=!stopPending
          && currentStatus==='waiting_for_clarification'
          && currentClarificationQuestion===data.question_id;
        if(continuationIsCurrent){
          currentClarificationQuestion=null;
          currentClarificationCreatedAt=null;
          apply({status:'running'});
          connection?.run('running');
        }
      }else if(['cancelled','expired','unknown'].includes(data.status)
          && currentClarificationQuestion===data.question_id){
        currentClarificationQuestion=null;
        currentClarificationCreatedAt=null;
      }
    });
    on('done',data=>apply({...data,status:data.status || 'completed'}));
    on('error',data=>{apply({...data,status:'failed'});inform(data.message || data.error || 'The run failed.',true);});
    events.onerror=()=>{if(events===stream){
      connection?.failure(undefined,'reconnecting');
      connectionInfo('Live connection interrupted. Reconnecting to the same run; your message will not be sent again.',true);
      if(!errorRecoveryStarted){
        errorRecoveryStarted=true;
        if(!polling){if(timer!==null)win.clearTimeout(timer);timer=null;schedule(1000);}
      }
    }};
    if(!win.EventSource)connectionInfo('Live updates are unavailable. Checking the saved run automatically.',false);
    schedule();
  }
  function renameSession(session) {
    const version=routeVersion,owner=state.user?.id,previous=doc.activeElement,connection=headerState;
    const current=()=>version===routeVersion && owner===state.user?.id && state.session===session && overlay.isConnected;
    const close=()=>{overlay.remove();if(previous?.isConnected)previous.focus();};
    const input=h('input',{name:'title',value:session.title || '',required:true,maxlength:200,'aria-label':'Title'});
    const error=h('p',{class:'inline-error',role:'alert',hidden:true});
    const save=h('button',{type:'submit',class:'primary'},'Save title');
    const form=h('form',{class:'stack',onsubmit:async e=>{
      e.preventDefault();if(save.disabled || !current())return;
      const title=input.value.trim();if(!title || title.length>200){error.hidden=false;error.textContent='Enter a title between 1 and 200 characters.';return;}
      save.disabled=true;error.hidden=true;
      connection?.rename(true);
      try{
        const result=await api.request(`/sessions/${encodeURIComponent(session.id)}`,{method:'PATCH',body:{title}});
        if(!current())return;
        if(result.id!==session.id || !result.title?.trim())throw new Error('The saved title could not be confirmed. Please try again.');
        session.title=result.title;
        const heading=content.querySelector('.conversation-head h1');heading.textContent=result.title;heading.title=result.title;close();
      }catch(reason){if(current()){if(reason.status===401){close();expiredSession();return;}error.hidden=false;error.textContent=errorMessage(reason);}}
      finally{connection?.rename(false);save.disabled=false;}
    }},h('h2',{},'Rename session'),h('label',{class:'field'},h('span',{},'Title'),input),error,h('div',{class:'actions'},button('Cancel',close),save));
    const dialog=h('section',{class:'dialog',role:'dialog','aria-modal':'true','aria-label':'Rename session'},form);
    const overlay=h('div',{class:'dialog-overlay','data-rename-dialog':true},dialog);
    overlay.addEventListener('keydown',event=>{
      if(event.key==='Escape'){event.preventDefault();close();}
      if(event.key==='Tab'){const controls=[...dialog.querySelectorAll('input,button')].filter(el=>!el.disabled),first=controls[0],last=controls.at(-1);if(event.shiftKey && doc.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey && doc.activeElement===last){event.preventDefault();first.focus();}}
    });
    root.append(overlay);input.focus();input.select();
  }
  function toolStatus(data) {
    const status=String(data.status || data.event?.replace(/^tool\./,'') || '').toLowerCase();
    if(data.error || data.is_error || ['failed','error'].includes(status))return 'failed';
    if(['success','succeeded','ok'].includes(status) || (status==='completed' && data.error===false))return 'success';
    if(['started','running','working','pending'].includes(status))return 'running';
    if(['completed','done'].includes(status))return 'completed';
    if(['cancelled','stopped'].includes(status))return 'cancelled';
    if(status==='mixed')return 'mixed';
    return 'unknown';
  }
  function toolPreview(data) {
    const status=toolStatus(data);
    const [symbol,label]=({success:[icons.success,'Succeeded'],failed:[icons.failed,'Failed'],running:[icons.running,'Running'],completed:[icons.completed,'Completed'],cancelled:[icons.cancelled,'Stopped'],mixed:[icons.mixed,'Mixed results'],unknown:[icons.unknown,'Status unavailable']})[status];
    const duration=toolDuration(data);
    return h('div',{class:'tool-preview','data-status':status,'data-duration':duration},h('span',{class:'tool-status-symbol','aria-hidden':'true'},statusSymbol(status)),h('span',{class:'tool-name'},data.kind==='delegation' ? 'Subagent result' : toolName(data)),h('span',{class:'tool-status-label'},duration ? `${label} · ${duration}` : label),
      h('span',{class:'tool-summary'},toolDetail(data)));
  }
  function toolActivity(items=[]) {
    const card=h('details',{class:'tool-activity',hidden:!items.length},h('summary',{},h('span',{class:'activity-symbol','aria-hidden':'true'}),h('span',{class:'activity-title'}),h('span',{class:'activity-preview'}),disclosure()),h('div',{class:'tool-rows'},...items.map(toolPreview)));
    refreshToolActivity(card);
    let revealLatest=false;
    // Native summary activation emits click for both pointer and keyboard input.
    card.querySelector('summary').addEventListener('click',()=>{revealLatest=!card.open;});
    card.addEventListener('toggle',()=>{
      const reveal=revealLatest;revealLatest=false;
      const messages=card.closest('.messages'),rows=card.querySelector('.tool-rows');
      if(!reveal || !card.open || !card.isConnected || !messages)return;
      rows.scrollTop=rows.scrollHeight;
      const latest=rows.lastElementChild;
      if(latest)messages.scrollTop+=latest.getBoundingClientRect().bottom-messages.getBoundingClientRect().bottom+8;
    });
    return card;
  }
  function refreshToolActivity(card) {
    const rows=[...card.querySelector('.tool-rows').children];
    card.hidden=!rows.length;
    if(!rows.length)return;
    const current=rows.findLast(row=>row.dataset.status==='running') || rows.at(-1);
    const state=current.dataset.status;
    card.dataset.status=state;
    const symbol=icons[state];
    const label=({running:'Running',failed:'Failed',mixed:'Mixed results',unknown:'Status unavailable',cancelled:'Stopped',completed:'Completed',success:'Succeeded'})[state];
    card.querySelector('.activity-symbol').replaceChildren(statusSymbol(state));
    card.querySelector('.activity-title').textContent=`${rows.length} ${rows.length===1 ? 'tool' : 'tools'}`;
    const currentName=current.querySelector('.tool-name')?.textContent || 'Tool';
    const detail=current.querySelector('.tool-summary')?.textContent || label;
    const preview=`${currentName}: ${detail}${current.dataset.duration ? ' · '+current.dataset.duration : ''}`;
    card.querySelector('.activity-preview').textContent=preview;
    card.querySelector('summary').setAttribute('aria-label',`${card.querySelector('.activity-title').textContent}: ${label} · ${preview}`);
  }
  function deferPublicTail(carrier,chunks,finalTime,identity) {
    // Not a disclosure until an exact-origin receipt proves a pre-final prefix.
    // No clock fallback, partial truncation, payload metadata or reasoning fields.
    let size=0;
    if(!Number.isFinite(finalTime) || !Array.isArray(chunks) || !chunks.length || chunks.length>8192 ||
       chunks.some((chunk,i)=>!chunk || typeof chunk.text!=='string' || (size+=chunk.text.length)>262144 ||
         !Number.isFinite(chunk.observed_at) || chunk.observed_at>finalTime || i>0 && chunk.observed_at<chunks[i-1].observed_at))return;
    let parts=chunks.map(({text,observed_at})=>({text,observed_at}));
    carrier.retainHistoryBefore=time=>{
      if(!Number.isFinite(time) || time>=finalTime)return null;
      const end=parts.findIndex(chunk=>chunk.observed_at>time),count=end<0?parts.length:end;
      if(!count)return null;
      const prefix=parts.splice(0,count),text=prefix.map(chunk=>chunk.text).join('');
      if(!text.trim())return null;
      const progress=markHistoryNode(publicActivity(text,prefix,prefix[0].observed_at),{...identity,observed_at:prefix[0].observed_at},identity.session_id);
      carrier.before(progress);return progress;
    };
  }
  function publicActivity(text,chunks,time) {
    const card=h('details',{class:'activity-summary',open:true},h('summary',{},disclosure(),h('span',{class:'activity-title'},'Progress'),h('span',{class:'activity-preview'},text.replace(/\s+/g,' ').slice(0,120)),messageTime(time)),renderMarkdown(doc,text));
    // Delta runs stay compact, retaining receipt-level boundaries for a later
    // background arrival. Only known boundaries split disclosures, never tokens.
    const trusted=Array.isArray(chunks) && chunks.length && chunks.every(chunk=>chunk?.node instanceof win.Text);
    if(chunks!==undefined)card.dataset.historyIncomplete='true';
    let chunkLength=0;
    if(Array.isArray(chunks) && chunks.length && (trusted || chunks.length<=8192) && chunks.every(chunk=>chunk && typeof chunk.text==='string' && (chunkLength+=chunk.text.length)<=text.length) && chunks.map(chunk=>chunk.text).join('')===text){
      let parts=chunks.map(chunk=>({text:chunk.text,observed_at:chunk.observed_at,node:trusted?chunk.node:doc.createTextNode(chunk.text)}));
      const body=h('div',{class:'markdown'});body.style.whiteSpace='pre-wrap';
      for(const chunk of parts)body.append(chunk.node);
      card.querySelector('.markdown').replaceWith(body);
      const refresh=()=>{card.querySelector('.activity-preview').textContent=parts.map(chunk=>chunk.text).join('').replace(/\s+/g,' ').slice(0,120);
        delete card.dataset.historyTime;if(Number.isFinite(parts[0]?.observed_at))card.dataset.historyTime=String(parts[0].observed_at);
        card.dataset.historyIncomplete=String(parts.some((chunk,i)=>!Number.isFinite(chunk.observed_at) || i>0 && chunk.observed_at<parts[i-1].observed_at));};
      refresh();
      card.splitHistoryAt=time=>{
        // Missing/nonmonotonic receipts cannot prove a split. Keep native order.
        if(!Number.isFinite(time) || parts.some((chunk,i)=>!Number.isFinite(chunk.observed_at) || i && chunk.observed_at<parts[i-1].observed_at))return null;
        const index=parts.findIndex(chunk=>chunk.observed_at>time);if(index<=0)return null;
        const laterParts=parts.splice(index),later=publicActivity(laterParts.map(chunk=>chunk.text).join(''),laterParts,laterParts[0].observed_at);
        Object.assign(later.dataset,card.dataset);later.dataset.historyTime=String(laterParts[0].observed_at);later.open=card.open;
        refresh();card.after(later);return later;
      };
    }
    return card;
  }
  function guidanceLabel(status){return status==='accepted_unconfirmed'?'Accepted · delivery unconfirmed':status==='not_delivered'?'Not delivered':'Delivery unknown';}
  function renderMessage(message) {
    if(!['user','assistant','tool'].includes(message.role) || ['analysis','reasoning'].includes(message.channel))return doc.createDocumentFragment();
    if(message.role==='tool') {
      const processName=message.kind==='context_compression' ? (message.name==='Runtime reminders'?'Runtime reminders':'Context compression') : message.kind==='runtime_notice' && message.name==='Tool limit reached' && message.status==='completed' ? 'Tool limit reached' : null;
      if(processName && typeof message.content==='string') {
        return h('details',{class:`${message.kind==='context_compression' ? 'context-compression' : 'runtime-notice'} process-history`,'data-status':message.status},h('summary',{},disclosure(),h('strong',{},processName),h('span',{class:'caption'},'Completed'),messageTime(message.timestamp)),renderMarkdown(doc,message.content));
      }
      if(message.kind==='delegation' && typeof message.content==='string' && message.content.trim()) {
        return h('details',{class:'delegation-result'},h('summary',{},disclosure(),toolPreview({...message,name:'Subagent result'}),messageTime(message.timestamp)),renderMarkdown(doc,message.content));
      }
      return toolActivity([message]);
    }
    if(message.role==='assistant' && message.kind==='clarification'){
      const labels={pending:'Pending',sending:'Submitting answer',answered:'Answered',
        cancelled:'Cancelled',expired:'Expired',unknown:'Answer status unknown'};
      const card=h('section',{class:'clarification-card clarification-history'},
        h('h3',{},'Question'),h('p',{class:'clarification-question'},message.content),
        h('p',{class:'clarification-state'},labels[message.clarification_status] || 'Clarification'),
        message.clarification_status==='answered' && (typeof message.clarification_answer==='string'
          || Array.isArray(message.clarification_answer))
          ? h('p',{class:'clarification-answer'},'Answer: ',
              Array.isArray(message.clarification_answer)
                ? message.clarification_answer.join(', '):message.clarification_answer)
          : null);
      if(['sending','unknown'].includes(message.clarification_status)
          && (typeof message.clarification_answer==='string' || Array.isArray(message.clarification_answer)))
        card.append(h('p',{class:'clarification-answer'},'Attempted answer: ',
          Array.isArray(message.clarification_answer)
            ? message.clarification_answer.join(', '):message.clarification_answer));
      card.append(messageTime(message.timestamp));
      return card;
    }
    const text=typeof message.content==='string' ? message.content : message.content == null ? '' : JSON.stringify(message.content);
    const tools=(message.tool_calls || []).map(tool=>({...tool,name:tool.function?.name || tool.name}));
    const attachments=Array.isArray(message.attachments) ? message.attachments.slice(0,4) : [];
    const photos=h('div',{class:'message-photos','aria-label':'Attached photos'});
    for(const item of attachments){
      if(!item || typeof item.id!=='string' || !/^[a-f0-9]{32}$/.test(item.id) || item.status==='expired'){
        photos.append(h('p',{class:'expired-photo caption',role:'status'},'Photo expired; message text is still available.'));
        continue;
      }
      const source=`/hermes/app-api/sessions/${encodeURIComponent(message.session_id || state.session?.id || '')}/attachments/${item.id}`;
      const image=h('img',{src:source,alt:'Attached photo',loading:'lazy'});
      image.addEventListener('error',()=>image.replaceWith(h('p',{class:'expired-photo caption',role:'status'},'Photo expired or unavailable; message text is still available.')),{once:true});
      photos.append(image);
    }
    const gallery=attachments.length?photos:null;
    if(!text.trim() && message.role==='assistant' && tools.length)return toolActivity(tools);
    if(!text.trim() && !tools.length && !gallery)return doc.createDocumentFragment();
    if(message.role==='assistant' && message.channel==='commentary'){const progress=publicActivity(text,message.timed_chunks,Object.hasOwn(message,'timestamp')?message.timestamp:message.observed_at);if(tools.length)progress.append(toolActivity(tools));return progress;}
    const copy=message.role==='assistant' ? button(icon('copy'),e=>action(e.currentTarget,async()=>{if(!win.navigator.clipboard)throw new Error('Copy is unavailable in this browser. Select the text to copy it.');await win.navigator.clipboard.writeText(text);inform('Copied to clipboard.');}),'quiet message-copy',{'aria-label':'Copy message',title:'Copy message'}) : null;
    const node=h('article',{class:`message ${message.role === 'user' ? 'user-message' : 'assistant-message'}`},h('div',{class:'message-author'},message.role === 'user' ? 'You' : 'Hermes',messageTime(message.timestamp),copy),
      text.trim()?renderMarkdown(doc,text):null,gallery,tools.length ? toolActivity(tools) : null);
    if(message.role==='user' && message.kind==='guidance' && message.run_id && message.idempotency_key){
      node.dataset.guidanceRun=message.run_id;node.dataset.guidanceKey=message.idempotency_key;
      if(message.steering_id){node.dataset.guidanceId=message.steering_id;node.id=`journal:${message.run_id}:steering:${message.steering_id}`;}
      node.append(h('small',{class:'guidance-status caption',role:'status'},guidanceLabel(message.steering_status)));
    }
    return markHistoryNode(node,message,message.session_id);
  }
  async function start() {
    if(destroyed)return;
    disconnect();leaveConversation();releasePresenceIdentity();const version=++routeVersion;
    try { const data = await api.request('/auth/me');if(destroyed || version!==routeVersion)return;await preparePresenceIdentity(data,()=>!destroyed && version===routeVersion);if(destroyed || version!==routeVersion)return; if(state.user?.id!==data.user.id){state.kind='chats';state.query='';state.searchOpen=false;state.offset=0;state.session=null;drafts.clear();attempts.clear();}state.user = data.user; shell(); await navigate(new URL(win.location.href).searchParams.has('inbox') ? 'inbox' : 'chats'); }
    catch(error) { if(destroyed || version!==routeVersion)return;state.user = null; showAuth(); if (error.status !== 401) inform(errorMessage(error),true); }
  }
  await start();
  return {state,start,destroy(){destroyed=true;routeVersion++;disconnect();leaveConversation();releasePresenceIdentity();}};
}
