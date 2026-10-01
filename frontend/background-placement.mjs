// Placement only: native nodes are never sorted or rebuilt.
export function markHistoryNode(node, message, sessionId) {
  if (!node.dataset) return node;
  if (message.id != null) node.dataset.historyId=String(message.id);
  node.dataset.historySession=message.session_id || sessionId || '';
  if (message.run_id) node.dataset.historyRun=message.run_id;
  if (message.source) node.dataset.historySource=message.source;
  if (Number.isFinite(message.observed_at)) node.dataset.historyObserved='true';
  if (Number.isFinite(message.observed_at ?? message.timestamp)) node.dataset.historyTime=String(message.observed_at ?? message.timestamp);
  if (message.role==='user' && message.kind!=='guidance') node.dataset.turnStart='true';
  // Grouping moves rows, so keep the original native boundary on each row.
  if (node.matches?.('details.tool-activity'))
    for (const row of node.querySelector('.tool-rows').children) markHistoryNode(row,message,sessionId);
  return node;
}

export function backgroundPlacement(messages, item, history) {
  const native=(history || [...messages.children].filter(node=>!node.dataset.backgroundId && !node.classList.contains('background-omitted')))
    .flatMap(node=>node.matches?.('details.tool-activity') ? [...node.querySelector('.tool-rows').children] : [node]);
  const turns=native.filter(node=>node.dataset.turnStart==='true');
  const originSession=item.origin_session_id || item.session_id;
  const runTargets=typeof item.origin_run_id==='string' && item.origin_run_id ? native.filter(node=>node.dataset.historyRun===item.origin_run_id && node.dataset.historySession===originSession) : [];
  // A user/reminder can share recorded ownership with the live container.
  // Its segment boundaries live inside that container, not after the user row.
  let target=runTargets.find(node=>node.matches?.('.live-message')) || runTargets[0];
  if (!target && item.origin_message_id!=null && typeof item.origin_session_id==='string') {
    target=turns.find(node=>node.dataset.historyId===String(item.origin_message_id) && node.dataset.historySession===item.origin_session_id);
  }
  if (target) {
    const position=native.indexOf(target);
    const end=turns.find(node=>native.indexOf(node)>position) || null;
    const live=target.matches?.('.live-message') ? target : null;
    const start=live || turns.filter(node=>native.indexOf(node)<=position).at(-1) || target;
    const time=Object.hasOwn(item,'event_at')?item.event_at:item.created_at;
    const segments=live ? [...live.children].filter(node=>node.matches('.tool-activity,.activity-summary,.guidance-message,.message-body')).flatMap(node=>node.matches('.tool-activity')?[...node.querySelector('.tool-rows').children]:[node]) : native.slice(native.indexOf(start)+1,end?native.indexOf(end):undefined);
    // Split only public text with recorded receipt boundaries. Native order and
    // disclosure identity stay intact; update the shared history for later peers.
    if(Number.isFinite(time))for(let i=0;i<segments.length;i++){
      const node=segments[i];
      // A terminal tail is hidden metadata, not native Progress. Only exact run
      // AND session identity plus a recorded event time can commit it.
      const retained=Object.hasOwn(item,'event_at') && node.dataset.historyRun===item.origin_run_id && node.dataset.historySession===originSession
        ? node.retainHistoryBefore?.(time) : null;
      if(retained){segments.splice(i++,0,retained);const index=history?.indexOf(node);if(index>=0)history.splice(index,0,retained);}
      const later=node.splitHistoryAt?.(time);
      if(later){segments.splice(i+1,0,later);const index=history?.indexOf(node);if(index>=0)history.splice(index+1,0,later);}
    }
    // Old journal projections assigned run creation time to every segment.
    // Without a receipt marker that equal timestamp is not ordering evidence.
    const timed=node=>node.dataset.historyTime!==undefined && !(node.dataset.historySource==='journal' && !node.dataset.historyObserved && node.dataset.historyTime===start.dataset.historyTime);
    const before=Number.isFinite(time) && (start.dataset.historyTime===undefined || time>=Number(start.dataset.historyTime))
      ? segments.find(node=>timed(node) && Number(node.dataset.historyTime)>time) : null;
    const material=segments.filter(node=>!node.matches('.message-body') || node.textContent.trim());
    const label=!Number.isFinite(time)?'Originating turn identified; chronological position is approximate (event time unavailable).':
      !Object.hasOwn(item,'event_at')?'Placed by receipt time within the originating turn; chronological position is approximate.':
      material.some(node=>!timed(node) || node.dataset.historyIncomplete==='true')?'Originating turn identified; chronological position is approximate (segment times unavailable).':'';
    const output=live?.querySelector('.message-body');
    const afterOutput=output?.textContent.trim() && timed(output) && Number.isFinite(time) && Number(output.dataset.historyTime)<=time;
    return {before:before || (live ? (live.dataset.historyFinal==='true' || afterOutput?null:output) : end), parent:live || messages, label, mode:'exact'};
  }
  if (item.origin_message_id!=null) {
    return {before:turns[0] || null, label:'Originating turn is not loaded in this view.', mode:'pending'};
  }
  // Explicit null from a modern backend means unknown event time, not permission
  // to reinterpret ingestion as producer time. Older servers only had created_at.
  const legacy=!Object.hasOwn(item,'event_at');
  const time=legacy?item.created_at:item.event_at;
  if (Number.isFinite(time)) {
    const known=turns.filter(node=>node.dataset.historyTime!==undefined);
    const prior=known.filter(node=>Number(node.dataset.historyTime)<=time).at(-1);
    if (prior) return {before:turns[turns.indexOf(prior)+1] || null,
      label:legacy?'Placed by receipt time; originating turn is unavailable.':'Placed by event time; exact originating turn is unavailable.',mode:'time'};
    if (known.length) return {before:turns[0],label:'This result predates the loaded turns; its exact originating turn is unavailable.',mode:'pending'};
  }
  // An admission boundary alone proves no native turn. Prefer genuine event
  // time above; without a usable time, retain an explicitly unproved placement.
  if (item.origin_anchor) return {before:turns[0] || null,
    label:'Originating turn could not be identified in this view.',mode:'pending'};
  return {before:null,label:'Originating turn unavailable.',mode:'unknown'};
}
