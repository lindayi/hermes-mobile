import test from 'node:test';
import assert from 'node:assert/strict';
import { createModel, ranked, selectionMembers } from '../../../frontend/finance/approved/live-model.mjs';

const account = (id, accountGroup, label = id) => ({ id, label, kind: 'account', account: `real:${id}`, accountGroup });
function fixture() {
  const raw = { currency: 'CAD', nodes: [account('bank', 'banking'), account('card', 'cards'), account('broker', 'investments')], edges: [] };
  function tx(id, kind, path, amount_minor, incoming = false, acct = 'bank', extra = {}) {
    const category = JSON.stringify(['raw-category', kind, path]);
    if (!raw.nodes.some(n => n.id === category)) raw.nodes.push({ id: category, kind, label: path.split('.').at(-1) });
    const edge = { id, source: incoming ? category : acct, target: incoming ? acct : category, kind, amount_minor, currency: 'CAD', selector: { endpoint: 'transactions', filters: { category: path, account: acct, direction: incoming ? 'incoming' : 'outgoing', category_mode: 'exact' } }, ...extra };
    raw.edges.push(edge); return edge;
  }
  function transfer(id, source, target, amount_minor) {
    const edge = { id, source, target, kind: 'transfer', amount_minor, currency: 'CAD', selector: { endpoint: 'transfers', filters: { source_account: source, target_account: target } } };
    raw.edges.push(edge); return edge;
  }
  return { raw, tx, transfer };
}
test('investment returns display income while inflow and outflow categories remain separate', () => {
  const { raw, tx } = fixture();
  tx('dividend', 'investment', 'investment.return.dividend', 10003, true, 'broker');
  tx('interest', 'investment', 'investment.return.interest', 203, true, 'broker');
  tx('buy', 'investment', 'investment.capital.fund', 80001, false, 'broker');
  tx('sell', 'investment', 'investment.capital.fund', 12001, true, 'broker');
  tx('labeled', 'income', 'income.benefit.provider', 19, true, 'bank', { displayKind: 'income', displayLabel: 'Evidence-backed benefit' });
  const m = createModel(raw), p = m.project();
  const dividend = nodeAt(p, byMember(p, 'dividend').source), interest = nodeAt(p, byMember(p, 'interest').source);
  assert.equal(dividend.kind, 'income');
  assert.equal(dividend.label, 'Dividends & distributions');
  assert.equal(dividend.value, 10003);
  assert.equal(interest.label, 'Investment interest');
  assert.equal(interest.value, 203);
  assert.equal(byMember(p, 'dividend').type, 'income');
  assert.notEqual(byMember(p, 'buy').target, byMember(p, 'sell').source);
  assert.equal(nodeAt(p, byMember(p, 'sell').source).value, 12001);
  assert.equal(nodeAt(p, byMember(p, 'labeled').source).label, 'Evidence-backed benefit');
  assertAcyclic(p);
  assert.deepEqual(m.totals(), { income: 19, spending: 0, net: 19 });
});

test('accounts sort by role and institution, with inactive accounts in exposed groups', () => {
  const { raw, transfer } = fixture();
  raw.nodes = [account('other', undefined), account('debt', 'debt'), account('card', 'cards'), account('broker', 'investments'),
    { ...account('bank-z', 'banking', 'A savings'), institution: 'Z Bank' }, { ...account('bank', 'banking', 'Z chequing'), institution: 'A Bank' }];
  transfer('invest-only', 'bank', 'broker', 101);
  const m = createModel(raw), p = m.project();
  assert.deepEqual(p.nodes.filter(n => n.kind === 'account').map(n => n.id), ['bank', 'bank-z', 'card', 'broker', 'debt', 'other']);
  assert.deepEqual(p.accountGroups.map(g => [g.role, g.label, g.count, g.collapsed]), [
    ['banking', 'Cash & banking', 2, false], ['cards', 'Credit cards', 1, false], ['investments', 'Investments', 1, false], ['debt', 'Loans', 1, false], ['other', 'Other accounts', 1, false]
  ]);
  assert.equal(nodeAt(p, 'other').accountGroup, 'other');
  for (const group of p.accountGroups) {
    assert.equal(group.id, JSON.stringify(['account-group', group.role]));
    assert.deepEqual(group.accounts, p.nodes.filter(n => n.groupId === group.id).map(n => n.account));
  }
  assert.ok(nodeAt(p, 'broker')); assert.ok(nodeAt(p, 'card'));
  assert.equal(m.name(p.accountGroups[0].id), 'Cash & banking');
  assert.equal(m.kind(p.accountGroups[0].id), 'account-group');
});

test('folding retains internal transfers as selectable evidence without fake self-loops', () => {
  const { raw, tx, transfer } = fixture();
  raw.nodes.push(account('savings', 'banking'));
  tx('salary', 'income', 'income.salary.employer', 100001, true);
  tx('food', 'expense', 'expense.food.shop.fresh', 10001);
  tx('rebate', 'expense', 'expense.food.shop.fresh', 1, true, 'savings');
  transfer('internal', 'bank', 'savings', 15003);
  transfer('investment-transfer', 'savings', 'broker', 5001);
  const model = createModel(raw), base = model.project(), groupId = JSON.stringify(['account-group', 'banking']);
  const collapsed = model.toggleGroup([], groupId);
  assert.deepEqual(collapsed, [groupId]);
  assert.deepEqual(model.toggleGroup(collapsed, groupId), []);
  assert.deepEqual(model.toggleGroup(collapsed, 'unknown'), collapsed);
  assert.deepEqual(model.toggle([], groupId), []);
  const expanded = base.nodes.filter(n => n.expandable).map(n => n.id);
  const p = model.project(expanded, collapsed), group = nodeAt(p, groupId);
  assert.equal(group.kind, 'account-group');
  assert.equal(group.group, 'banking');
  assert.equal(group.expandable, true); assert.equal(group.expanded, false);
  assert.deepEqual(group.accounts, ['real:bank', 'real:savings']);
  assert.equal(p.accountGroups.find(g => g.id === groupId).collapsed, true);
  assert.equal(nodeAt(p, 'bank'), undefined);
  assert.ok(p.edges.every(e => e.source !== e.target));
  assert.equal(p.internalEdges.length, 1);
  assert.equal(p.internalEdges[0].members[0], raw.edges[3]);
  assert.equal(p.internalEdges[0].amount, 15003);
  assert.deepEqual(ids(selectionMembers(p, { node: groupId })), ids(raw.edges));
  assert.equal(selectionMembers(p, { edges: [p.internalEdges[0].id] })[0].selector, raw.edges[3].selector);
  assert.deepEqual(ids([...p.edges.filter(e => !e.decomposition), ...p.internalEdges].flatMap(e => e.members)), ids(raw.edges));
  assert.equal(group.incoming, 115005); assert.equal(group.outgoing, 30005);
  assert.deepEqual(model.totals(), { income: 100001, spending: 10000, net: 90001 });
  const unfolded = model.project(expanded);
  assert.deepEqual(unfolded.internalEdges, []);
  assert.equal(nodeAt(unfolded, 'bank').incoming, 100001);
  assert.equal(nodeAt(unfolded, 'bank').outgoing, 25004);
  assert.deepEqual(collapsed, [groupId]);
});

test('semantic payroll endpoints preserve labels and lineage without double-counting decomposition', () => {
  const { raw, tx } = fixture();
  const net = tx('net-pay', 'income', 'income.employment.payroll', 70001, true);
  raw.nodes.push({ id: 'employer', semantic: true, kind: 'income', label: 'Employer gross pay' },
    { id: 'payroll-hub', semantic: true, kind: 'payroll', label: 'Payroll allocation', metadata: { verified: true } },
    { id: 'tax', semantic: true, kind: 'tax', label: 'Tax withheld' });
  net.source = 'payroll-hub';
  const rows = [{ id: 'paystub:1', amount_minor: 100001 }];
  raw.edges.push({ id: 'gross', source: 'employer', target: 'payroll-hub', kind: 'income', displayKind: 'payroll', amount_minor: 100001, currency: 'CAD', synthetic: true, decomposition: true, inlineRows: rows, members: [net] },
    { id: 'withholding', source: 'payroll-hub', target: 'tax', kind: 'tax', amount_minor: 30000, currency: 'CAD', synthetic: true, decomposition: true, inlineRows: rows, members: [net] },
    { id: 'duplicate-account-branch', source: 'payroll-hub', target: 'bank', kind: 'income', amount_minor: 70001, currency: 'CAD', decomposition: true, synthetic: true, members: [net] });
  const model = createModel(raw), p = model.project();
  assert.equal(byMember(p, 'net-pay').source, 'payroll-hub');
  assert.equal(nodeAt(p, 'payroll-hub').label, 'Payroll allocation');
  assert.equal(nodeAt(p, 'payroll-hub').kind, 'payroll');
  assert.deepEqual(nodeAt(p, 'payroll-hub').metadata, { verified: true });
  assert.equal(nodeAt(p, 'payroll-hub').value, 170002);
  assert.equal(nodeAt(p, 'employer').value, 100001);
  assert.equal(nodeAt(p, 'tax').value, 30000);
  assert.equal(nodeAt(p, 'bank').incoming, 70001);
  assert.deepEqual(model.toggle([], 'payroll-hub'), []);
  assert.equal(p.edges.find(e => e.source === 'employer').members[0].inlineRows, rows);
  assert.ok(selectionMembers(p, { node: 'tax' }).some(m => m === net));
  assert.deepEqual(model.totals(), { income: 70001, spending: 0, net: 70001 });
  raw.summary = { income: 90001, spending: 300, netPay: 70001, investmentIncome: 20000 };
  assert.deepEqual(createModel(raw).totals(), { income: 90001, spending: 300, net: 89701, netPay: 70001, investmentIncome: 20000 });
  raw.summary.net = 42;
  assert.equal(createModel(raw).totals().net, 42);
});

test('default ranking nets all recoveries by root, including zero-net and recovery-only entries', () => {
  const { raw, tx, transfer } = fixture();
  tx('food-bank', 'expense', 'expense.food.shop', 6001);
  tx('food-card', 'expense', 'expense.food.dining', 3999, false, 'card');
  tx('food-refund', 'expense', 'expense.food.shop', 3000, true, 'card');
  tx('fee', 'expense', 'expense.fees.service', 201);
  tx('fee-refund', 'expense', 'expense.fees.service', 201, true);
  tx('refund-only', 'expense', 'expense.travel.hotel', 100, true);
  tx('tax', 'tax', 'payroll.tax.withheld', 50000);
  tx('mortgage', 'financing', 'financing.mortgage.payment', 60000);
  transfer('payment', 'bank', 'card', 80000);
  const model = createModel(raw), base = model.project();
  const p = model.project(base.nodes.filter(n => n.expandable).map(n => n.id), [JSON.stringify(['account-group', 'banking'])]);
  const ranks = ranked(p), food = ranks.find(r => r.categoryPath === 'expense.food');
  assert.equal(food.amount, 7000); assert.equal(food.net, 7000);
  assert.equal(food.gross, 10000); assert.equal(food.offset, 3000);
  assert.deepEqual(ids(selectionMembers(p, { edges: food.edges.map(e => e.id) })), ['food-bank', 'food-card', 'food-refund']);
  assert.equal(ranks.find(r => r.categoryPath === 'expense.fees').amount, 0);
  assert.equal(ranks.find(r => r.categoryPath === 'expense.travel').amount, -100);
  assert.equal(ranks.length, 3);
  assert.equal(ranks.reduce((s, r) => s + r.amount, 0), 6900);
  assert.equal(food.share, 7000 / 6900);
  assert.ok(ranks.flatMap(r => r.edges).every(e => p.edges.includes(e)));
  const accountGroup = JSON.stringify(['account-group', 'banking']);
  assert.equal(ranked(p, accountGroup).reduce((s, r) => s + r.amount, 0), p.edges.filter(e => e.source === accountGroup).reduce((s, e) => s + e.amount, 0));
  assert.ok(ranked(p, accountGroup).some(r => r.id === 'card'));
  assert.deepEqual(ranked(p, 'missing'), []);
});

test('enrichment source types keep positive values and explicit labels', () => {
  const { raw, tx } = fixture();
  tx('dividend', 'investment', 'investment.return.dividend', 17, true, 'broker', { displayKind: 'investment-income', displayLabel: 'Dividends & distributions' });
  tx('match', 'transfer', 'transfer.contribution.employer', 3001, true, 'broker', { displayKind: 'employer-match', displayLabel: 'Employer RRSP match' });
  tx('saving', 'transfer', 'transfer.contribution.member', 2999, true, 'broker', { displayKind: 'payroll-saving', displayLabel: 'Employee RRSP · payroll withheld' });
  const model = createModel(raw), p = model.project();
  for (const original of raw.edges) {
    const edge = byMember(p, original.id), source = nodeAt(p, edge.source);
    assert.equal(edge.type, original.displayKind);
    assert.equal(source.kind, original.displayKind);
    assert.equal(source.label, original.displayLabel);
    assert.equal(source.value, original.amount_minor);
  }
});

test('currency and integer-cent evidence survive expansion and folding down to one cent', () => {
  const { raw, tx } = fixture();
  tx('cent', 'expense', 'expense.food.shop.fresh', 1);
  tx('odd', 'expense', 'expense.food.shop.fresh', 101, false, 'card');
  tx('offset', 'expense', 'expense.food.shop.fresh', 1, true, 'card');
  const before = structuredClone(raw), model = createModel(raw), base = model.project();
  const p = model.project(base.nodes.filter(n => n.expandable).map(n => n.id), base.accountGroups.map(g => g.id));
  assert.equal(p.currency, 'CAD');
  assert.ok(p.edges.every(e => e.currency === 'CAD'));
  assert.ok(p.edges.every(e => Number.isSafeInteger(e.amount) && e.amount > 0));
  assert.ok(p.nodes.every(n => Number.isSafeInteger(n.incoming) && Number.isSafeInteger(n.outgoing)));
  assert.equal(ranked(p)[0].amount, 101);
  assert.deepEqual(model.totals(), { income: 0, spending: 101, net: -101 });
  for (const member of selectionMembers(p, { edges: p.edges.map(e => e.id) })) {
    assert.equal(member, raw.edges.find(e => e.id === member.id));
    assert.equal(member.selector, raw.edges.find(e => e.id === member.id).selector);
  }
  assert.deepEqual(raw, before);
  const omittedCurrency = structuredClone(raw); delete omittedCurrency.edges[0].currency;
  assert.equal(byMember(createModel(omittedCurrency).project(), 'cent').currency, 'CAD');
});

test('invalid fractional or unsafe input amounts cannot enter a minor-unit graph', () => {
  for (const invalid of [0.1, Number.MAX_SAFE_INTEGER + 1, '100', NaN, Infinity]) {
    const { raw, tx } = fixture(); tx('bad', 'expense', 'expense.food', invalid);
    assert.throws(() => createModel(raw), /integer|minor|amount/i);
  }
});

test('evidence lineage does not multiply category value or economic totals', () => {
  const { raw, tx } = fixture();
  const original = tx('original', 'expense', 'expense.food.shop.fresh', 103);
  const wrapper = { ...original, id: 'enriched-slice', originalId: original.id, members: [original], inlineRows: [{ entry_id: 'entry', amount_minor: -103 }] };
  raw.edges = [wrapper];
  const model = createModel(raw), base = model.project();
  const p = model.project(base.nodes.filter(n => n.expandable).map(n => n.id));
  for (const node of p.nodes.filter(n => n.kind === 'expense')) assert.equal(node.value, 103);
  assert.equal(model.totals().spending, 103);
  assert.equal(ranked(p)[0].amount, 103);
  assert.deepEqual(ids(selectionMembers(p, { edges: p.edges.map(e => e.id) })), ['enriched-slice', 'original']);
});

const byMember = (p, id) => p.edges.find(e => !e.decomposition && e.members.some(m => m.id === id));
const nodeAt = (p, id) => p.nodes.find(n => n.id === id);
const ids = members => members.map(m => m.id).sort();
function assertAcyclic(p) {
  const visiting = new Set(), visited = new Set();
  function visit(id) {
    assert.ok(!visiting.has(id), `directed category cycle at ${id}`);
    if (visited.has(id)) return;
    visiting.add(id);
    for (const edge of p.edges.filter(e => e.source === id)) visit(edge.target);
    visiting.delete(id); visited.add(id);
  }
  for (const node of p.nodes) visit(node.id);
}

test('expense recoveries are positive account-local sources, not shared expense sinks', () => {
  const { raw, tx, transfer } = fixture();
  tx('bank-fee', 'expense', 'expense.fees.service', 1501);
  tx('bank-rebate', 'expense', 'expense.fees.service', 1001, true);
  tx('card-fee', 'expense', 'expense.fees.service', 9900, false, 'card');
  tx('card-rebate', 'expense', 'expense.fees.service', 9900, true, 'card');
  transfer('payment', 'bank', 'card', 10000);
  const before = structuredClone(raw), model = createModel(raw), p = model.project();
  const bankRecovery = nodeAt(p, byMember(p, 'bank-rebate').source);
  const cardRecovery = nodeAt(p, byMember(p, 'card-rebate').source);
  assert.equal(bankRecovery.kind, 'recovery');
  assert.equal(byMember(p, 'bank-rebate').type, 'recovery');
  assert.equal(bankRecovery.value, 1001);
  assert.notEqual(bankRecovery.id, cardRecovery.id);
  assert.notEqual(bankRecovery.id, byMember(p, 'bank-fee').target);
  assert.equal(byMember(p, 'bank-rebate').members[0], raw.edges[1]);
  assert.equal(raw.edges[1].kind, 'expense');
  assertAcyclic(p);
  const expanded = model.project(p.nodes.filter(n => n.expandable).map(n => n.id));
  assertAcyclic(expanded);
  assert.deepEqual(model.totals(), { income: 0, spending: 500, net: -500 });
  assert.deepEqual(raw, before);
});
