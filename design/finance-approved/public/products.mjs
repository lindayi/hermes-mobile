// Evidence-only presentation adapter. No holdings valuation is a cash movement.
const id = (...parts) => JSON.stringify(['product', ...parts]);
const groupId = JSON.stringify(['account-group', 'investments']);
const inRange = (date, range) => typeof date === 'string' && date.slice(0, 10) >= range.start && date.slice(0, 10) <= range.end;

export function enrichProducts(input, { investments = {} } = {}) {
  const raw = structuredClone(input);
  const accounts = new Map(raw.nodes.filter(n => n.kind === 'account').map(n => [n.id, n]));
  for (const account of accounts.values()) {
    account.holdingObservations = structuredClone((investments.holdings ?? []).filter(row => row.account === account.account
      && row.currency === raw.currency && typeof row.as_of === 'string'));
  }
  const result = [], replacedEndpoints = new Set();
  for (const edge of raw.edges) {
    const classification = edge.selector?.filters?.category?.match(/^investment\.trade\.(buy|sell)$/)?.[1];
    if (!classification || edge.productTrade) { result.push(edge); continue; }
    const buy = classification === 'buy';
    const account = accounts.get(buy ? edge.source : edge.target);
    if (!account) { result.push(edge); continue; }
    account.accountGroup = 'investments';
    const oldEndpoint = buy ? edge.target : edge.source;
    if (!accounts.has(oldEndpoint)) replacedEndpoints.add(oldEndpoint);
    const rootId = id('group', account.id, edge.currency ?? raw.currency);
    if (!raw.nodes.some(n => n.id === rootId)) raw.nodes.push({ id: rootId, label: 'Products', kind: 'product-group', semantic: true, ownerAccountId: account.id, groupId });
    const events = (investments.cashflows ?? []).filter(row => row.account === account.account && row.classification === classification && row.currency === (edge.currency ?? raw.currency) && inRange(row.date, raw.range));
    const filters = edge.selector?.filters ?? {};
    const valid = investments.coverage?.cashflows?.complete === true && events.length > 0
      && edge.selector?.endpoint === 'transactions' && !filters.q && !filters.record_id
      && (!filters.dataset || ['all', 'investment'].includes(filters.dataset))
      && (!filters.account || filters.account === account.account)
      && (!filters.currency || filters.currency === (edge.currency ?? raw.currency))
      && (!filters.direction || filters.direction === (buy ? 'outgoing' : 'incoming'))
      && (!filters.flow || filters.flow === 'investment')
      && events.every(row => inRange(row.date, { start: filters.start ?? raw.range.start, end: filters.end ?? raw.range.end }))
      && events.every(row => typeof row.event_id === 'string' && row.event_id.length > 0 && typeof row.security_key === 'string' && row.security_key.length > 0
        && Number.isSafeInteger(row.cash_amount_minor) && (buy ? row.cash_amount_minor <= 0 : row.cash_amount_minor >= 0))
      && new Set(events.map(row => row.event_id)).size === events.length
      && events.reduce((sum, row) => sum + Math.abs(row.cash_amount_minor), 0) === edge.amount_minor;
    if (!valid) {
      result.push({ ...edge, source: buy ? account.id : rootId, target: buy ? rootId : account.id,
        originalId: edge.originalId ?? edge.id, originalAmount: edge.originalAmount ?? edge.amount_minor, originalSelector: structuredClone(edge.originalSelector ?? edge.selector),
        displayKind: 'portfolio-trade', productTrade: true, productDetailStatus: 'unavailable' });
      raw.notes = [...(raw.notes ?? []), 'Product breakdown unavailable; original trade amount and evidence retained.'];
      continue;
    }
    for (const row of events.filter(row => row.cash_amount_minor !== 0)) {
      const productId = id('security', account.id, row.currency, row.security_key);
      if (!raw.nodes.some(n => n.id === productId)) raw.nodes.push({ id: productId, label: row.security_key, security_key: row.security_key, kind: 'product', semantic: true, parentId: rootId, ownerAccountId: account.id, groupId });
      const recordId = 'investment:' + row.event_id;
      // A narrowed selector must not be bypassed by a wider preloaded page or lineage.
      const { inlineRows, members, ...base } = edge;
      result.push({ ...base, id: id('trade', edge.id, row.event_id), source: buy ? account.id : productId, target: buy ? productId : account.id,
        amount_minor: Math.abs(row.cash_amount_minor), count: 1, displayKind: 'portfolio-trade', productTrade: true,
        originalId: edge.originalId ?? edge.id, originalAmount: edge.originalAmount ?? edge.amount_minor, originalSelector: structuredClone(edge.originalSelector ?? edge.selector),
        evidenceRecordIds: [recordId], selector: { ...edge.selector, filters: { ...edge.selector.filters, dataset: 'investment', record_id: recordId } } });
    }
    // Zero-cash rows are source evidence, never invented ribbon widths or values.
    for (const row of events.filter(row => row.cash_amount_minor === 0)) {
      const productId = id('security', account.id, row.currency, row.security_key);
      const owner = result.find(e => e.originalId === (edge.originalId ?? edge.id) && (e.source === productId || e.target === productId));
      if (!owner) continue;
      const filters = {...edge.selector.filters,dataset:'investment',record_id:'investment:'+row.event_id};
      delete filters.direction;
      (owner.members ??= []).push({id:id('zero-evidence',edge.id,row.event_id),kind:'investment',currency:row.currency,amount_minor:0,evidenceNote:'Zero-cash source entry; no flow or holding value inferred.',selector:{...edge.selector,filters}});
    }
  }
  raw.edges = result;
  const used = new Set(result.flatMap(edge => [edge.source, edge.target]));
  raw.nodes = raw.nodes.filter(node => !replacedEndpoints.has(node.id) || used.has(node.id));
  const owned = raw.nodes.filter(n => ['product', 'product-group'].includes(n.kind));
  for (const node of owned) {
    node.currency = raw.currency;
    node.holdingObservations = structuredClone((investments.holdings ?? []).filter(row => row.account === accounts.get(node.ownerAccountId)?.account
      && row.currency === node.currency && typeof row.as_of === 'string' && (node.kind === 'product-group' || row.security_key === node.security_key)));
    if (node.kind === 'product') node.label = [...node.holdingObservations].sort((a, b) => b.as_of.localeCompare(a.as_of)).find(row => row.name)?.name ?? node.security_key;
    node.activityIncoming = 0; node.activityOutgoing = 0;
  }
  const lookup = new Map(owned.map(node => [node.id, node]));
  for (const edge of result.filter(edge => edge.productTrade)) {
    for (const [endpoint, field] of [[edge.source, 'activityOutgoing'], [edge.target, 'activityIncoming']]) {
      const node = lookup.get(endpoint);
      if (!node) continue;
      node[field] += edge.amount_minor;
      if (node.parentId) lookup.get(node.parentId)[field] += edge.amount_minor;
    }
  }
  for (const node of owned) {
    node.activityAmount = node.activityIncoming + node.activityOutgoing;
    node.value = Math.max(node.activityIncoming, node.activityOutgoing);
  }
  return raw;
}
