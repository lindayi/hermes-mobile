import test from 'node:test';
import assert from 'node:assert/strict';
import * as model from './public/live-model.mjs';
const products = await import('./public/products.mjs').catch(error => {
  if (error.code === 'ERR_MODULE_NOT_FOUND') return {};
  throw error;
});
const groupId = JSON.stringify(['account-group', 'investments']);
const enrich = (...args) => products.enrichProducts(...args);
const range = { start: '2026-07-01', end: '2026-09-30' };
function fixture() {
  const raw = { currency: 'CAD', range, nodes: [
    { id: 'cash', kind: 'account', account: 'broker', label: 'Broker cash', accountGroup: 'investments' },
    { id: 'buys', kind: 'investment', label: 'Investment purchases' },
  ], edges: [{ id: 'buy-edge', source: 'cash', target: 'buys', kind: 'investment', amount_minor: 300, currency: 'CAD', count: 2,
    selector: { endpoint: 'transactions', filters: { ...range, currency: 'CAD', account: 'broker', category: 'investment.trade.buy', category_mode: 'exact', direction: 'outgoing', dataset: 'all', scope: 'personal', q: '' } } }] };
  const investments = { coverage: { cashflows: { complete: true } }, cashflows: [
    { event_id: 'event-a', account: 'broker', security_key: 'AAA', date: '2026-07-05', currency: 'CAD', cash_amount_minor: -100, classification: 'buy' },
    { event_id: 'event-b', account: 'broker', security_key: 'BBB', date: '2026-08-05', currency: 'CAD', cash_amount_minor: -200, classification: 'buy' },
  ], holdings: [] };
  return { raw, investments };
}

test('exact complete trade evidence becomes owned products without mutating the original graph', () => {
  assert.equal(typeof products.enrichProducts, 'function', 'pure product enrichment export exists');
  const { raw, investments } = fixture(), before = structuredClone({ raw, investments });
  const result = products.enrichProducts(raw, { investments });
  assert.deepEqual({ raw, investments }, before);
  const root = result.nodes.find(n => n.kind === 'product-group');
  const children = result.nodes.filter(n => n.kind === 'product');
  assert.equal(root.ownerAccountId, 'cash'); assert.equal(root.groupId, groupId);
  assert.deepEqual(children.map(n => n.security_key).sort(), ['AAA', 'BBB']);
  assert.ok(children.every(n => n.parentId === root.id && n.ownerAccountId === 'cash' && n.groupId === groupId && n.semantic));
  assert.equal(result.edges.reduce((sum, edge) => sum + edge.amount_minor, 0), 300);
  for (const edge of result.edges) {
    assert.equal(edge.source, 'cash'); assert.ok(children.some(n => n.id === edge.target));
    assert.equal(edge.originalId, 'buy-edge'); assert.equal(edge.originalAmount, 300);
    assert.deepEqual(edge.originalSelector, raw.edges[0].selector);
    assert.deepEqual(edge.selector.filters, { ...raw.edges[0].selector.filters, dataset: 'investment', record_id: edge.evidenceRecordIds[0] });
    assert.match(edge.evidenceRecordIds[0], /^investment:event-[ab]$/);
    assert.equal(edge.inlineRows, undefined, 'descriptions and source rows must come from the API');
  }
});


test('unverifiable trade breakdown retains the whole original selector and amount inside its owner', () => {
  const cases = [
    x => x.investments.cashflows.pop(),
    x => { x.investments.coverage.cashflows.complete = false; },
    x => { delete x.investments.coverage; },
    x => { x.investments.cashflows[0].security_key = null; },
    x => { x.investments.cashflows[0].event_id = ''; },
    x => { x.investments.cashflows[1].event_id = x.investments.cashflows[0].event_id; },
    x => { x.investments.cashflows[0].cash_amount_minor = 100; },
    x => { x.investments.cashflows[0].cash_amount_minor = -100.5; },
    x => { x.raw.edges[0].selector.filters.q = 'unverifiable search'; },
    x => { x.raw.edges[0].selector.filters.dataset = 'bank'; },
    x => { x.raw.edges[0].selector.filters.account = 'different'; },
  ];
  for (const modify of cases) {
    const x = fixture(); modify(x);
    const p = enrich(x.raw, x), edge = p.edges[0];
    assert.equal(p.edges.length, 1);
    assert.equal(edge.id, 'buy-edge'); assert.equal(edge.amount_minor, 300);
    assert.deepEqual(edge.selector, x.raw.edges[0].selector);
    assert.equal(edge.originalId, 'buy-edge'); assert.equal(edge.originalAmount, 300);
    assert.deepEqual(edge.originalSelector, x.raw.edges[0].selector);
    assert.equal(p.nodes.find(n => n.id === edge.target).kind, 'product-group');
    assert.equal(p.nodes.filter(n => n.kind === 'product').length, 0);
    assert.equal(edge.productDetailStatus, 'unavailable');
    assert.ok(p.notes.some(note => /retained|unavailable/i.test(note)));
  }
});


test('product amounts are period activity while holdings remain dated display-only observations', () => {
  const x = fixture();
  x.investments.holdings = [{ account: 'broker', security_key: 'AAA', name: 'Example Fund', currency: 'CAD', as_of: '2026-10-19', quantity: '90', market_value_minor: 999999, book_cost_minor: 888888 }];
  x.investments.cashflows.push({ ...x.investments.cashflows[0], event_id: 'future', date: '2026-10-19', cash_amount_minor: -5000 });
  const p = enrich(x.raw, x), a = p.nodes.find(n => n.security_key === 'AAA'), root = p.nodes.find(n => n.kind === 'product-group');
  assert.equal(a.label, 'Example Fund');
  assert.deepEqual(a.holdingObservations, x.investments.holdings);
  assert.equal(a.activityIncoming, 100); assert.equal(a.activityOutgoing, 0); assert.equal(a.activityAmount, 100); assert.equal(a.value, 100);
  assert.equal(root.activityIncoming, 300); assert.equal(root.activityOutgoing, 0); assert.equal(root.activityAmount, 300); assert.equal(root.value, 300);
  assert.equal(p.edges.reduce((s, e) => s + e.amount_minor, 0), 300);
  assert.equal(p.edges.some(e => e.evidenceRecordIds?.includes('investment:future')), false);
});


test('product hierarchy appends children while preserving cash totals and folding all owned endpoints', () => {
  const x = fixture(), raw = enrich(x.raw, x), m = model.createModel(raw), base = m.project();
  const root = base.nodes.find(n => n.kind === 'product-group');
  assert.ok(root, 'expanded account first shows its product group');
  assert.equal(root.expandable, true); assert.equal(root.expanded, false);
  assert.equal(base.nodes.filter(n => n.kind === 'product').length, 0);
  assert.equal(base.edges[0].source, 'cash'); assert.equal(base.edges[0].target, root.id);
  const expanded = m.toggle([], root.id), p = m.project(expanded);
  assert.equal(p.nodes.filter(n => n.kind === 'product').length, 2);
  assert.deepEqual(p.edges.filter(e => !e.decomposition), base.edges);
  assert.equal(p.edges.filter(e => e.decomposition).length, 2);
  assert.ok(p.edges.filter(e => e.decomposition).every(e => e.source === root.id));
  assert.equal(p.nodes.find(n => n.id === 'cash').outgoing, 300);
  const folded = m.project(expanded, [groupId]);
  assert.equal(folded.nodes.filter(n => n.ownerAccountId).length, 0);
  assert.equal(folded.edges.length, 0);
  assert.ok(folded.internalEdges.length > 0);
  assert.ok(folded.internalEdges.every(e => e.source === groupId && e.target === groupId));
  assert.equal(folded.nodes.find(n => n.id === groupId).outgoing, 300);
  assert.equal(folded.nodes.find(n => n.id === groupId).internalAmount, 300);
  assert.deepEqual(m.project(expanded).edges, p.edges);
  assert.deepEqual(m.toggle(expanded, root.id), []);
  assert.deepEqual(m.totals(), { income: 0, spending: 0, net: 0 });
});


test('group selection persists across expansion and reports boundary totals without treating trades as consumption', () => {
  const x = fixture();
  x.raw.nodes.push({ id: 'bank', account: 'bank', kind: 'account', label: 'Bank', accountGroup: 'banking' },
    { id: 'other-cash', account: 'other-broker', kind: 'account', label: 'Other broker', accountGroup: 'investments' },
    { id: 'fees', kind: 'expense', label: 'Fees' },
    { id: 'pending', kind: 'payroll-unconfirmed', semantic: true, label: 'Not posted' });
  const selector = { endpoint: 'transactions', filters: { category: 'expense.fees' } };
  x.raw.edges.push({ id: 'funding', source: 'bank', target: 'cash', amount_minor: 600, kind: 'transfer', selector },
    { id: 'fee', source: 'cash', target: 'fees', amount_minor: 10, kind: 'expense', selector },
    { id: 'internal', source: 'cash', target: 'other-cash', amount_minor: 50, kind: 'transfer', selector },
    { id: 'unposted', source: 'bank', target: 'pending', amount_minor: 75, kind: 'payroll-saving', selector });
  const enriched = enrich(x.raw, x), m = model.createModel(enriched), root = enriched.nodes.find(n => n.kind === 'product-group');
  const expectedMembers = enriched.edges.filter(e => e.id !== 'unposted').map(e => e.id).sort();
  for (const p of [m.project(), m.project([root.id]), m.project([root.id], [groupId]), m.project([], [groupId])]) {
    const info = p.accountGroups.find(g => g.id === groupId);
    assert.ok(Array.isArray(info.nodeIds), 'persistent group exposes its account IDs');
    assert.deepEqual(info.nodeIds.sort(), ['cash', 'other-cash']);
    assert.deepEqual(info.allOwnedNodeIds.sort(), enriched.nodes.filter(n => n.ownerAccountId || ['cash', 'other-cash'].includes(n.id)).map(n => n.id).sort());
    assert.equal(info.externalIncoming, 600); assert.equal(info.externalOutgoing, 10); assert.equal(info.internalAmount, 350);
    assert.equal(typeof model.groupSelection, 'function');
    const selected = model.groupSelection(p, groupId);
    assert.equal(selected.label, 'Investments'); assert.deepEqual(selected.accounts.sort(), ['broker', 'other-broker']);
    assert.equal(selected.externalIncoming, 600); assert.equal(selected.externalOutgoing, 10); assert.equal(selected.internalAmount, 350);
    assert.ok(selected.nodes.every(n => n.id === groupId || info.allOwnedNodeIds.includes(n.id)));
    assert.ok(selected.edges.every(e => [...p.edges, ...p.internalEdges].includes(e)));
    assert.deepEqual(model.selectionMembers(p, { group: groupId }).map(m => m.id).sort(), expectedMembers);
    assert.deepEqual(model.selectionMembers(p, { group: groupId, edges: selected.edges.map(e => e.id) }).map(m => m.id).sort(), expectedMembers);
    assert.equal(model.groupSelection(p, 'missing'), null);
    assert.deepEqual(model.selectionMembers(p, { group: 'missing' }), []);
  }
});


test('selector period, currency and direction must corroborate event evidence before splitting', () => {
  for (const change of [
    f => { f.currency = 'USD'; },
    f => { f.direction = 'incoming'; },
    f => { f.start = '2026-08-01'; },
    f => { f.end = '2026-07-31'; },
    f => { f.flow = 'expense'; },
  ]) {
    const x = fixture(); change(x.raw.edges[0].selector.filters);
    const p = enrich(x.raw, x);
    assert.equal(p.edges.length, 1); assert.equal(p.edges[0].productDetailStatus, 'unavailable');
    assert.deepEqual(p.edges[0].selector, x.raw.edges[0].selector);
  }
});


test('product enrichment is idempotent and does not split already narrowed evidence again', () => {
  const x = fixture(), first = enrich(x.raw, x);
  assert.deepEqual(enrich(first, x), first);
});


test('a product selection returns only its real buy and sale API records across decomposition', async () => {
  const { createPager } = await import('./public/finance-client.mjs');
  const x = fixture();
  x.investments.cashflows.push({ ...x.investments.cashflows[0], event_id: 'repeat-buy', cash_amount_minor: -50 },
    { ...x.investments.cashflows[0], event_id: 'sale', classification: 'sell', cash_amount_minor: 30 });
  x.raw.edges[0].amount_minor = 350; x.raw.edges[0].count = 3;
  x.raw.nodes.push({ id: 'sales', kind: 'investment', label: 'Sales' });
  x.raw.edges.push({ ...x.raw.edges[0], id: 'sale-edge', source: 'sales', target: 'cash', amount_minor: 30, count: 1,
    selector: { endpoint: 'transactions', filters: { ...x.raw.edges[0].selector.filters, category: 'investment.trade.sell', direction: 'incoming' } } });
  const enriched = enrich(x.raw, x), m = model.createModel(enriched), root = enriched.nodes.find(n => n.kind === 'product-group');
  const p = m.project([root.id]), a = p.nodes.find(n => n.security_key === 'AAA');
  assert.equal(a.activityIncoming, 150); assert.equal(a.activityOutgoing, 30); assert.equal(a.activityAmount, 180);
  assert.equal(p.nodes.find(n => n.id === 'cash').incoming, 30); assert.equal(p.nodes.find(n => n.id === 'cash').outgoing, 350);
  assert.ok(p.edges.some(e => e.decomposition && e.source === a.id && e.target === root.id && e.amount === 30));
  const group = model.groupSelection(p, groupId);
  assert.equal(group.externalIncoming, 0); assert.equal(group.externalOutgoing, 0); assert.equal(group.internalAmount, 380);
  const members = model.selectionMembers(p, { node: a.id });
  assert.equal(members.length, 3);
  const sourceRows = x.investments.cashflows.map(row => ({ record_id: 'investment:' + row.event_id, entry_id: row.event_id,
    account: row.account, date: row.date, currency: row.currency, amount_minor: row.cash_amount_minor,
    description: 'Source description ' + row.event_id, source_id: 'source-' + row.event_id }));
  const calls = [], pager = createPager(members, async (endpoint, filters) => {
    calls.push({ endpoint, filters });
    assert.equal(filters.dataset, 'investment'); assert.equal(endpoint, 'transactions');
    const items = sourceRows.filter(r => r.record_id === filters.record_id);
    return { items, total: items.length };
  });
  while (!pager.done) await pager.more();
  assert.deepEqual(pager.rows.map(r => r.record_id).sort(), ['investment:event-a', 'investment:repeat-buy', 'investment:sale']);
  assert.ok(pager.rows.every(r => r.description === 'Source description ' + r.record_id.slice('investment:'.length) && r.source));
  assert.equal(calls.length, 3);
  assert.deepEqual(m.totals(), { income: 0, spending: 0, net: 0 });
});


test('narrowed product evidence never inherits an aggregate inline page or unrelated member stream', () => {
  const x = fixture();
  x.raw.edges[0].inlineRows = [{ record_id: 'investment:event-b', description: 'Other product' }];
  x.raw.edges[0].members = [{ id: 'unrelated', selector: x.raw.edges[0].selector }];
  const p = enrich(x.raw, x);
  for (const edge of p.edges) {
    assert.equal(edge.inlineRows, undefined);
    assert.equal(edge.members, undefined);
    assert.ok(edge.selector.filters.record_id);
  }
});


test('replaced trade-only spent endpoints disappear while shared category endpoints are preserved', () => {
  const x = fixture();
  assert.equal(enrich(x.raw, x).nodes.some(n => n.id === 'buys'), false);
  x.raw.edges.push({ ...x.raw.edges[0], id: 'other', amount_minor: 5,
    selector: { endpoint: 'transactions', filters: { category: 'investment.fee' } } });
  assert.equal(enrich(x.raw, x).nodes.some(n => n.id === 'buys'), true);
});


test('explicit portfolio trades identify their opaque owner as an investment account even without metadata', () => {
  const x = fixture(); x.raw.nodes[0].accountGroup = 'other'; x.investments = {};
  const p = model.createModel(enrich(x.raw, x)).project([], [groupId]);
  assert.equal(p.accountGroups.find(g => g.role === 'investments')?.count, 1);
  assert.equal(p.edges.length, 0);
  assert.equal(p.internalEdges.filter(e => !e.decomposition).reduce((sum, e) => sum + e.amount, 0), 300);
});


test('quiet accounts expose dated holdings without inventing activity or replacing account values', () => {
  const x = fixture(); x.raw.edges = []; x.raw.nodes = [x.raw.nodes[0]]; x.raw.nodes[0].value = 77;
  const holding = { account: 'broker', security_key: 'QUIET', name: 'Quiet holding', currency: 'CAD', as_of: '2026-10-19', quantity: '10', market_value_minor: 999999, book_cost_minor: 888888 };
  x.investments.holdings = [holding, { ...holding, account: 'another' }, { ...holding, currency: 'USD' }];
  const result = enrich(x.raw, x), account = result.nodes.find(n => n.id === 'cash');
  assert.deepEqual(account.holdingObservations, [holding]);
  assert.equal(account.value, 77); assert.equal(account.activityAmount, undefined);
  assert.equal(result.nodes.length, 1); assert.deepEqual(result.edges, []);
  assert.deepEqual(model.createModel(result).project().nodes[0].holdingObservations, [holding]);
  assert.equal(x.raw.nodes[0].holdingObservations, undefined);
});
