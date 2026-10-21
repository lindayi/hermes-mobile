// Pure adapter for /hermes/app-api/finance/flow. All displayed amounts are
// integer minor units; formatting alone converts to dollars. Selectors stay untouched.
const key = (...parts) => JSON.stringify(parts);
const labelFor = path => {const text=path.split('.').at(-1).replaceAll('_', ' ') || 'Uncategorized';return text.charAt(0).toUpperCase()+text.slice(1);};
const rootPath = (kind, path) => {
  const parts = path.split('.');
  return parts.slice(0, parts[0] === kind ? 2 : 1).join('.');
};

// Default shares use NET consumption by category root. Recovery source nodes
// stay separate in the graph but contribute negative offsets here. Focus remains
// exact outgoing graph geometry, including expanded hierarchy links.
export function ranked(p, focus = '') {
  const nodes = new Map(p.nodes.map(n => [n.id, n]));
  const groups = new Map();
  const isAccount = id => ['account', 'account-group'].includes(nodes.get(id)?.kind);
  for (const edge of p.edges) {
    if (focus) {
      if (edge.source !== focus) continue;
      if (!groups.has(edge.target)) groups.set(edge.target, { id: edge.target, label: nodes.get(edge.target)?.label ?? edge.target, amount: 0, share: 0, edges: [] });
      const group = groups.get(edge.target);
      group.amount += edge.amount; group.edges.push(edge);
      continue;
    }
    if (edge.decomposition) continue;
    const direction = Number(isAccount(edge.source)) - Number(isAccount(edge.target));
    if (!direction) continue;
    for (const member of edge.members) {
      if (member.kind !== 'expense') continue;
      const path = member.selector?.filters?.category ?? '';
      const categoryPath = rootPath('expense', path);
      const categoryNode = p.nodes.find(n => n.kind === 'expense' && n.categoryPath === categoryPath);
      const id = categoryNode?.id ?? key('live-category', 'expense', categoryPath);
      if (!groups.has(id)) groups.set(id, { id, categoryPath, label: categoryNode?.label ?? labelFor(categoryPath), amount: 0, gross: 0, offset: 0, net: 0, share: 0, edges: [] });
      const group = groups.get(id);
      if (direction > 0) group.gross += member.amount_minor; else group.offset += member.amount_minor;
      group.net = group.amount = group.gross - group.offset;
      if (!group.edges.includes(edge)) group.edges.push(edge);
    }
  }
  const destinations = [...groups.values()];
  const denominator = destinations.reduce((total, d) => total + d.amount, 0);
  for (const destination of destinations) destination.share = denominator ? destination.amount / denominator : 0;
  return destinations.sort((a, b) => b.amount - a.amount || a.id.localeCompare(b.id));
}

// The heading is a selection target even when its synthetic folded node is absent.
export function groupSelection(p, groupId) {
  const group = p.accountGroups?.find(g => g.id === groupId);
  if (!group) return null;
  const owned = new Set([group.id, ...(group.allOwnedNodeIds ?? group.nodeIds ?? [])]);
  return { ...group, nodes: p.nodes.filter(n => owned.has(n.id)),
    edges: [...p.edges, ...(p.internalEdges ?? [])].filter(e => owned.has(e.source) || owned.has(e.target)) };
}

// Union node/group incidents and explicit projected edge IDs, deduplicated by API ID.
export function selectionMembers(p, { node = '', group = '', edges = [] } = {}) {
  const ids = new Set([...edges, ...(groupSelection(p, group)?.edges ?? []).map(e => e.id)]), members = new Map();
  for (const edge of [...p.edges, ...(p.internalEdges ?? [])]) {
    if (ids.has(edge.id) || (node && (edge.source === node || edge.target === node))) {
      const retain = member => {
        if (members.has(member.id)) return;
        members.set(member.id, member);
        for (const original of member.members ?? []) retain(original);
      };
      for (const member of edge.members) retain(member);
    }
  }
  return [...members.values()];
}

export function createModel(raw) {
  const inputNodes = new Map(raw.nodes.map(n => [n.id, n]));
  const roles = ['banking', 'cards', 'investments', 'debt', 'other'];
  const groupLabels = ['Cash & banking', 'Credit cards', 'Investments', 'Loans', 'Other accounts'];
  const accounts = raw.nodes.filter(n => n.kind === 'account').map(n => {
    const accountGroup = roles.includes(n.accountGroup) ? n.accountGroup : 'other';
    return { ...n, accountGroup, groupId: key('account-group', accountGroup) };
  }).sort((a, b) => roles.indexOf(a.accountGroup) - roles.indexOf(b.accountGroup)
    || String(a.registration ?? '').localeCompare(String(b.registration ?? ''))
    || String(a.institution ?? '').localeCompare(String(b.institution ?? ''))
    || String(a.label).localeCompare(String(b.label)) || a.id.localeCompare(b.id));
  const accountGroups = roles.map((role, i) => {
    const members = accounts.filter(n => n.accountGroup === role);
    const nodeIds = members.map(n => n.id);
    return { id: key('account-group', role), label: groupLabels[i], role, accounts: members.map(n => n.account ?? n.label), nodeIds,
      allOwnedNodeIds: [...nodeIds, ...raw.nodes.filter(n => ['product', 'product-group'].includes(n.kind) && nodeIds.includes(n.ownerAccountId)).map(n => n.id)],
      count: members.length, collapsed: false };
  }).filter(g => g.count);
  const groupLookup = new Map(accountGroups.map(g => [g.id, g]));
  const nodes = new Map(accounts.map(n => [n.id, n]));
  for (const edge of raw.edges) {
    if (!Number.isSafeInteger(edge.amount_minor)) throw new TypeError(`Edge ${edge.id} amount_minor must be a safe integer`);
  }
  const originals = raw.edges.filter(e => e.amount_minor > 0);
  const parents = new Map(), children = new Map();
  for (const node of raw.nodes.filter(n => ['product', 'product-group'].includes(n.kind))) {
    const owner = nodes.get(node.ownerAccountId);
    nodes.set(node.id, { ...node, groupId: owner?.groupId ?? node.groupId });
    if (node.parentId) {
      parents.set(node.id, node.parentId);
      if (!children.has(node.parentId)) children.set(node.parentId, new Set());
      children.get(node.parentId).add(node.id);
    }
  }
  const normalized = originals.map(member => {
    const direction = accountDirection(member);
    const category = member.selector?.filters?.category ?? '';
    const recovery = member.kind === 'expense' && direction > 0;
    const investment = member.kind === 'investment' || category.startsWith('investment.');
    const investmentReturn = investment && direction > 0 && category.startsWith('investment.return.');
    const displayKind = recovery ? 'recovery' : member.displayKind ?? (investmentReturn ? 'income' : member.kind);
    const displayLabel = member.displayLabel ?? (investmentReturn ? (/interest/i.test(category) ? 'Investment interest' : 'Dividends & distributions') : undefined);
    function endpoint(id) {
      const node = inputNodes.get(id);
      if (node.kind === 'account') return { id, chain: [] };
      if (['product', 'product-group'].includes(node.kind)) {
        const chain = [], seen = new Set();
        for (let current = id; current !== undefined && !seen.has(current); current = parents.get(current)) {
          seen.add(current); chain.unshift(current);
        }
        return { id: chain[0], chain };
      }
      if (node.semantic) { nodes.set(id, { ...node }); return { id, chain: [] }; }
      const path = category;
      const root = member.displayLabel ? path : investmentReturn ? path.split('.').slice(0, 3).join('.') : rootPath(node.kind, path);
      const segments = path.split('.');
      const chain = [];
      for (let depth = root.split('.').length; depth <= segments.length; depth++) {
        const categoryPath = segments.slice(0, depth).join('.');
        const identity = ['live-category', displayKind, categoryPath, ...(recovery ? [member.target] : investment || !['expense'].includes(member.kind) ? [direction > 0 ? 'inflow' : 'outflow'] : [])];
        let ident = key(...identity);
        for (let salt = 1; inputNodes.has(ident); salt++) ident = key(...identity, salt);
        const exact = categoryPath === path;
        const label = displayLabel ?? (exact ? node.label : `${node.kind === 'transfer' ? 'Unmatched transfer: ' : ''}${labelFor(categoryPath)}`);
        if (!nodes.has(ident) || exact) nodes.set(ident, { id: ident, label, kind: displayKind, categoryPath, hierarchyDepth: chain.length, polarity: direction > 0 || displayKind === 'income' ? 'source' : 'sink' });
        if (chain.length) {
          const parent = chain.at(-1);
          parents.set(ident, parent);
          if (!children.has(parent)) children.set(parent, new Set());
          children.get(parent).add(ident);
        }
        chain.push(ident);
      }
      return { id: chain[0], chain };
    }
    return { source: endpoint(member.source), target: endpoint(member.target), member, displayKind };
  });
  function project(expandedPaths = [], collapsedGroups = []) {
    const expanded = new Set(expandedPaths), collapsed = new Set(collapsedGroups);
    const accountId = id => collapsed.has(nodes.get(id)?.groupId) ? nodes.get(id).groupId : id;
    const groups = new Map();
    function add(source, target, member, displayKind, decomposition = false) {
      source = accountId(source); target = accountId(target);
      const currency = member.currency ?? raw.currency;
      const id = key(decomposition ? 'branch' : 'base', source, target, displayKind, currency);
      if (!groups.has(id)) groups.set(id, { id, source, target, type: displayKind, currency, amount: 0, members: [], decomposition });
      const edge = groups.get(id);
      edge.amount += member.amount_minor;
      edge.members.push(member);
    }
    for (const { source, target, member, displayKind } of normalized) {
      add(source.id, target.id, member, displayKind, !!member.decomposition);
      for (const [endpoint, incoming] of [[source, true], [target, false]]) {
        for (let i = 0; i < endpoint.chain.length - 1; i++) {
          const parent = endpoint.chain[i], child = endpoint.chain[i + 1];
          if (!expanded.has(parent)) break;
          add(incoming ? child : parent, incoming ? parent : child, member, displayKind, true);
        }
      }
    }
    const allEdges = [...groups.values()];
    const internalEdges = allEdges.filter(e => e.source === e.target && collapsed.has(e.source) && groupLookup.has(e.source));
    const edges = allEdges.filter(e => !internalEdges.includes(e));
    const visible = new Set(edges.flatMap(e => [e.source, e.target]));
    const visibleAccounts = [], emitted = new Set();
    for (const account of accounts) {
      if (!collapsed.has(account.groupId)) { visibleAccounts.push(account); continue; }
      if (emitted.has(account.groupId)) continue;
      emitted.add(account.groupId);
      const group = groupLookup.get(account.groupId);
      visibleAccounts.push({ ...group, accounts: [...group.accounts], kind: 'account-group', group: group.role, accountGroup: group.role, groupId: group.id });
    }
    const projectedNodes = [...visibleAccounts, ...[...nodes.values()].filter(n => n.kind !== 'account' && visible.has(n.id))].map(n => ({ ...n, incoming: 0, outgoing: 0, expandable: n.kind === 'account-group' || children.has(n.id), expanded: n.kind !== 'account-group' && children.has(n.id) && expanded.has(n.id) }));
    const lookup = new Map(projectedNodes.map(n => [n.id, n]));
    for (const edge of allEdges) {
      const source = lookup.get(edge.source), target = lookup.get(edge.target);
      const isAccount = node => node.kind === 'account' || node.kind === 'account-group';
      if (!edge.decomposition || !isAccount(source)) source.outgoing += edge.amount;
      if (!edge.decomposition || !isAccount(target)) target.incoming += edge.amount;
    }
    const projectedGroups = accountGroups.map(group => {
      const owned = new Set([group.id, ...group.allOwnedNodeIds]);
      const summary = { externalIncoming: 0, externalOutgoing: 0, internalAmount: 0 };
      for (const edge of allEdges) {
        if (edge.decomposition) continue;
        const source = owned.has(edge.source), target = owned.has(edge.target);
        if (source && target) summary.internalAmount += edge.amount;
        else if (source) summary.externalOutgoing += edge.amount;
        else if (target) summary.externalIncoming += edge.amount;
      }
      if (collapsed.has(group.id)) Object.assign(lookup.get(group.id), summary);
      return { ...group, ...summary, accounts: [...group.accounts], nodeIds: [...group.nodeIds], allOwnedNodeIds: [...group.allOwnedNodeIds], collapsed: collapsed.has(group.id) };
    });
    const projection = { nodes: projectedNodes, edges, internalEdges, accountGroups: projectedGroups, currency: raw.currency, range: raw.range, coverage: raw.coverage };
    // Values follow semantic source/sink polarity, independently of expanded
    // branch geometry. Explicit semantic hubs display their larger flow side.
    for (const node of projectedNodes) {
      if (node.kind === 'account' || node.kind === 'account-group') continue;
      if (node.semantic) {
        node.value = node.value ?? Math.max(node.incoming, node.outgoing);
        continue;
      }
      // Lineage is evidence, not another money movement. Deduplicate the raw
      // movement objects incident through base/hierarchy links, without walking
      // their nested evidence members (selectionMembers deliberately does walk it).
      const incidents = new Map();
      for (const edge of edges) {
        if (edge.source !== node.id && edge.target !== node.id) continue;
        for (const member of edge.members) incidents.set(member.id, member);
      }
      const netIncoming = [...incidents.values()].reduce((total, member) => total + accountDirection(member) * member.amount_minor, 0);
      node.value = (node.polarity === 'source' ? netIncoming : -netIncoming);
    }
    return projection;
  }
  function accountDirection(edge) {
    return Number(inputNodes.get(edge.target)?.kind === 'account') - Number(inputNodes.get(edge.source)?.kind === 'account');
  }
  function totals() {
    let income = 0, spending = 0;
    for (const e of originals) {
      if (e.decomposition) continue;
      const direction = accountDirection(e);
      if (e.kind === 'income') income += direction * e.amount_minor;
      if (e.kind === 'expense') spending -= direction * e.amount_minor;
    }
    income = raw.summary?.income ?? income;
    spending = raw.summary?.spending ?? spending;
    return { income, spending, net: income - spending, ...raw.summary };
  }
  function toggle(expandedPaths, id) {
    const expanded = new Set(expandedPaths);
    if (!children.has(id)) return [...expanded];
    if (expanded.has(id)) {
      for (const candidate of expanded) {
        for (let ancestor = candidate; ancestor !== undefined; ancestor = parents.get(ancestor)) {
          if (ancestor === id) { expanded.delete(candidate); break; }
        }
      }
    } else {
      const ancestors = [];
      for (let ancestor = id; ancestor !== undefined; ancestor = parents.get(ancestor)) ancestors.push(ancestor);
      for (const ancestor of ancestors.reverse()) expanded.add(ancestor);
    }
    return [...expanded];
  }
  function toggleGroup(collapsedGroups, id) {
    const collapsed = new Set(collapsedGroups);
    if (groupLookup.has(id)) {
      if (collapsed.has(id)) collapsed.delete(id); else collapsed.add(id);
    }
    return [...collapsed];
  }
  return { name: id => nodes.get(id)?.label ?? groupLookup.get(id)?.label ?? id, kind: id => nodes.get(id)?.kind ?? (groupLookup.has(id) ? 'account-group' : undefined), project, totals, toggle, toggleGroup };
}
