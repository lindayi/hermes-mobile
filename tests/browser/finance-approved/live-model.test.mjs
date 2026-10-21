import test from 'node:test';
import assert from 'node:assert/strict';
import * as adapter from '../../../frontend/finance/approved/live-model.mjs';

const account = id => ({ id, label: `Account ${id}`, kind: 'account', account: id });
function fixture() {
  const raw = { currency: 'CAD', range: { start: '2026-07-01', end: '2026-09-30' }, coverage: { complete: true }, nodes: [account('a'), account('b')], edges: [] };
  function tx(id, kind, path, amount_minor, incoming = false, acct = 'a', label = path) {
    const cat = JSON.stringify(['category', kind, path]);
    if (!raw.nodes.some(n => n.id === cat)) raw.nodes.push({ id: cat, label, kind });
    raw.edges.push({ id, source: incoming ? cat : acct, target: incoming ? acct : cat, kind, amount_minor, currency: 'CAD', count: 1,
      selector: { endpoint: 'transactions', filters: { category: path, category_mode: 'exact', account: acct, direction: incoming ? 'incoming' : 'outgoing', flow: kind, transfer_status: 'unmatched', start: raw.range.start, end: raw.range.end } } });
  }
  function transfer(id, source, target, amount_minor) {
    raw.edges.push({ id, source, target, kind: 'transfer', amount_minor, currency: 'CAD', count: 1,
      selector: { endpoint: 'transfers', filters: { source_account: source, target_account: target, link_type: 'internal', transfer_status: 'matched' } } });
  }
  return { raw, tx, transfer };
}
const category = (p, path, kind = 'expense') => p.nodes.find(n => n.kind === kind && n.categoryPath === path);
const sum = edges => edges.reduce((n, e) => n + e.amount, 0);

const byMember = (p, id) => p.edges.find(e => !e.decomposition && e.members.some(m => m.id === id));
const nodeAt = (p, id) => p.nodes.find(n => n.id === id);
const ids = members => members.map(m => m.id).sort();

test('split category values deduplicate originals independently of decomposition geometry', () => {
  const { raw, tx } = fixture();
  tx('purchase', 'expense', 'expense.food.shop.fresh', 10000);
  tx('refund', 'expense', 'expense.food.shop.fresh', 2000, true);
  tx('salary', 'income', 'income.salary.employer', 90000, true);
  tx('reversal', 'income', 'income.salary.employer', 10000);
  tx('refund-only', 'expense', 'expense.other', 100, true);
  const m = adapter.createModel(raw), base = m.project();
  const food = category(base, 'expense.food'), recovery = category(base, 'expense.food', 'recovery');
  const salary = nodeAt(base, byMember(base, 'salary').source), reversal = nodeAt(base, byMember(base, 'reversal').target);
  const roots = [food.id, recovery.id, salary.id, reversal.id];
  const first = m.project(roots);
  const shop = category(first, 'expense.food.shop'), recoveryShop = category(first, 'expense.food.shop', 'recovery');
  const p = m.project([...roots, shop.id, recoveryShop.id]);
  for (const path of ['expense.food', 'expense.food.shop', 'expense.food.shop.fresh']) {
    const sink = category(p, path), source = category(p, path, 'recovery');
    assert.notEqual(sink.id, source.id);
    assert.equal(sink.value, 10000); assert.equal(source.value, 2000);
    assert.equal(sink.value - source.value, 8000);
    assert.deepEqual(ids(adapter.selectionMembers(p, { node: sink.id })), ['purchase']);
    assert.deepEqual(ids(adapter.selectionMembers(p, { node: source.id })), ['refund']);
  }
  assert.equal(category(p, 'expense.other'), undefined);
  assert.equal(category(p, 'expense.other', 'recovery').value, 100);
  assert.notEqual(salary.id, reversal.id);
  for (const path of ['income.salary', 'income.salary.employer']) {
    const sources = p.nodes.filter(n => n.kind === 'income' && n.categoryPath === path);
    assert.equal(sources.length, 2);
    assert.deepEqual(sources.map(n => n.value).sort((a, b) => a - b), [-10000, 90000]);
    assert.equal(sources.reduce((total, n) => total + n.value, 0), 80000);
  }
  assert.equal(category(p, 'expense.food').incoming, 10000);
  assert.equal(category(p, 'expense.food', 'recovery').outgoing, 2000);
  const a = p.nodes.find(n => n.id === 'a');
  assert.equal(a.incoming, 92100); assert.equal(a.outgoing, 20000);
  assert.equal(category(m.project(), 'expense.food').value, 10000);
  assert.equal(category(m.project(), 'expense.food', 'recovery').value, 2000);
  assert.equal(p.edges.filter(e => !e.decomposition).length, 5);
  assert.deepEqual(ids(p.edges.filter(e => !e.decomposition).flatMap(e => e.members)), ids(raw.edges));
});

test('selection deduplicates originals across incident and explicit decomposition edges', () => {
  const { raw, tx, transfer } = fixture();
  tx('purchase', 'expense', 'expense.food.shop.fresh', 10000);
  tx('refund', 'expense', 'expense.food.shop.fresh', 2000, true);
  tx('other', 'expense', 'expense.other', 300);
  transfer('ab', 'a', 'b', 10);
  const before = structuredClone(raw), m = adapter.createModel(raw);
  const food = category(m.project(), 'expense.food');
  const shop = category(m.project([food.id]), 'expense.food.shop');
  const p = m.project([food.id, shop.id]);
  const edgeIds = p.edges.filter(e => e.decomposition).map(e => e.id);
  assert.equal(edgeIds.length, 2);
  assert.deepEqual(ids(adapter.selectionMembers(p, { node: food.id, edges: edgeIds })), ['purchase']);
  const row = adapter.ranked(p).find(r => r.id === food.id);
  assert.equal(row.edges.length, 2);
  const selected = adapter.selectionMembers(p, { node: food.id, edges: [...edgeIds, ...row.edges.map(e => e.id)] });
  assert.deepEqual(selected.map(e => e.id).sort(), ['purchase', 'refund']);
  assert.equal(selected.find(e => e.id === 'purchase'), raw.edges[0]);
  assert.equal(selected.find(e => e.id === 'refund').selector, raw.edges[1].selector);
  assert.deepEqual(adapter.selectionMembers(p, {}), []);
  assert.deepEqual(adapter.selectionMembers(p, { edges: ['missing'] }), []);
  assert.equal(adapter.selectionMembers(p, { node: 'a' }).length, 4);
  assert.deepEqual(raw, before);
});

test('ranking uses net expense denominator and focused actual outgoing links', () => {
  const { raw, tx, transfer } = fixture();
  tx('food', 'expense', 'expense.food.groceries', 6000);
  tx('food-b', 'expense', 'expense.food.dining', 4000, false, 'b');
  tx('refund', 'expense', 'expense.food.groceries', 3000, true);
  tx('rent', 'expense', 'expense.rent', 10000);
  tx('salary', 'income', 'income.salary', 500000, true);
  transfer('ab', 'a', 'b', 100000);
  const m = adapter.createModel(raw), base = m.project(), food = category(base, 'expense.food');
  const p = m.project([food.id]);
  const ranks = adapter.ranked(p);
  assert.equal(ranks.length, 2);
  const foodRank = ranks.find(r => r.id === food.id);
  assert.equal(foodRank.amount, 7000);
  assert.equal(foodRank.gross, 10000); assert.equal(foodRank.offset, 3000); assert.equal(foodRank.net, 7000);
  assert.equal(foodRank.share, 7000 / 17000);
  assert.equal(foodRank.edges.length, 3);
  assert.deepEqual(ids(foodRank.edges.flatMap(e => e.members)), ['food', 'food-b', 'refund']);
  assert.ok(ranks.flatMap(r => r.edges).every(edge => p.edges.includes(edge)));
  assert.equal(sum(ranks), 17000);
  assert.equal(ranks.reduce((total, row) => total + row.gross, 0), 20000);
  assert.equal(sum(ranks), m.totals().spending);
  const focus = adapter.ranked(p, food.id);
  assert.equal(focus.length, 2);
  assert.equal(sum(focus), 10000);
  assert.equal(focus.find(r => r.id === 'a'), undefined);
  assert.equal(focus.find(r => r.id === category(p, 'expense.food.groceries').id).amount, 6000);
  assert.equal(focus.find(r => r.id === category(p, 'expense.food.dining').id).amount, 4000);
  const recoveryFocus = adapter.ranked(p, byMember(p, 'refund').source);
  assert.equal(recoveryFocus.length, 1);
  assert.equal(recoveryFocus[0].id, 'a'); assert.equal(recoveryFocus[0].amount, 3000);
  assert.equal(recoveryFocus[0].edges[0], byMember(p, 'refund'));
  assert.equal(sum(focus) + sum(recoveryFocus), 13000);
  assert.deepEqual(adapter.ranked(p, 'missing'), []);
});

test('toggle adds ancestors and collapses descendants without touching sibling namespaces', () => {
  const { raw, tx } = fixture();
  tx('deep', 'expense', 'expense.food.shop.fresh.local', 100);
  tx('lookalike', 'expense', 'expense.food.shopkeeper.leaf', 100);
  tx('other-kind', 'income', 'expense.food.shop.fresh.local', 100, true);
  const m = adapter.createModel(raw), food = category(m.project(), 'expense.food');
  const one = m.project([food.id]);
  const shop = category(one, 'expense.food.shop'), sibling = category(one, 'expense.food.shopkeeper');
  const fresh = category(m.project([food.id, shop.id]), 'expense.food.shop.fresh');
  const expanded = m.toggle([], fresh.id);
  assert.deepEqual(new Set(expanded), new Set([food.id, shop.id, fresh.id]));
  const withSibling = m.toggle(expanded, sibling.id);
  const snapshot = [...withSibling];
  assert.deepEqual(new Set(m.toggle(withSibling, shop.id)), new Set([food.id, sibling.id]));
  assert.deepEqual(withSibling, snapshot);
  assert.deepEqual(m.toggle(withSibling, food.id), []);
  assert.deepEqual(m.toggle([], 'a'), []);
  assert.deepEqual(m.toggle([], 'missing'), []);
  const leaf = category(m.project(expanded), 'expense.food.shop.fresh.local');
  assert.deepEqual(m.toggle([], leaf.id), []);
  assert.equal(category(m.project(), 'expense.food').expanded, false);
});

test('expansion appends direction-preserving deep decomposition without duplicating base members', () => {
  const { raw, tx } = fixture();
  tx('purchase', 'expense', 'expense.food.shop.fresh', 10000);
  tx('refund', 'expense', 'expense.food.shop.fresh', 2000, true);
  tx('direct', 'expense', 'expense.food', 500);
  tx('unrelated', 'expense', 'expense.utilities', 300);
  const m = adapter.createModel(raw), base = m.project(), food = category(base, 'expense.food');
  assert.equal(food.expandable, true);
  const first = m.project([food.id]), shop = category(first, 'expense.food.shop');
  assert.ok(shop); assert.equal(shop.expandable, true);
  const recoveryRoot = category(base, 'expense.food', 'recovery');
  const recoveryShop = category(m.project([recoveryRoot.id]), 'expense.food.shop', 'recovery');
  const p = m.project([food.id, shop.id, recoveryRoot.id, recoveryShop.id]);
  const fresh = category(p, 'expense.food.shop.fresh'), recoveryFresh = category(p, 'expense.food.shop.fresh', 'recovery');
  assert.ok(fresh); assert.equal(fresh.expandable, false);
  assert.ok(recoveryFresh); assert.equal(recoveryFresh.expandable, false);
  assert.equal(m.name(fresh.id), 'expense.food.shop.fresh');
  assert.deepEqual(p.edges.filter(e => !e.decomposition), base.edges);
  assert.equal(base.edges.length, 3);
  assert.equal(p.edges.filter(e => e.decomposition).length, 4);
  for (const [parent, child, recoveryParent, recoveryChild] of [
    [food.id, shop.id, recoveryRoot.id, recoveryShop.id],
    [shop.id, fresh.id, recoveryShop.id, recoveryFresh.id]
  ]) {
    const outward = p.edges.find(e => e.source === parent && e.target === child);
    const recovery = p.edges.find(e => e.source === recoveryChild && e.target === recoveryParent);
    assert.equal(outward.amount, 10000); assert.equal(recovery.amount, 2000);
    assert.ok(outward.decomposition && recovery.decomposition);
    assert.equal(outward.members.length, 1); assert.equal(recovery.members.length, 1);
    assert.equal(outward.members[0], raw.edges[0]);
    assert.equal(recovery.members[0], raw.edges[1]);
    assert.notEqual(parent, recoveryParent); assert.notEqual(child, recoveryChild);
    assert.equal(p.edges.find(e => e.source === child && e.target === parent), undefined);
  }
  const middle = p.nodes.find(n => n.id === shop.id), recoveryMiddle = nodeAt(p, recoveryShop.id);
  assert.equal(middle.incoming, 10000); assert.equal(middle.incoming, middle.outgoing);
  assert.equal(recoveryMiddle.incoming, 2000); assert.equal(recoveryMiddle.incoming, recoveryMiddle.outgoing);
  assert.equal(food.value, 10500); assert.equal(recoveryRoot.value, 2000);
  assert.equal(category(p, 'expense.utilities').incoming, 300);
  assert.deepEqual(m.totals(), { income: 0, spending: 8800, net: -8800 });
});

test('net totals use original signed account directions and exclude transfers', () => {
  const { raw, tx, transfer } = fixture();
  tx('salary', 'income', 'income.salary', 100000, true);
  tx('salary-reversed', 'income', 'income.salary', 5000);
  tx('purchase', 'expense', 'expense.food', 20000);
  tx('refund', 'expense', 'expense.food', 3000, true);
  transfer('ab', 'a', 'b', 500000);
  tx('unknown', 'unresolved', '', 900000);
  const m = adapter.createModel(raw);
  assert.deepEqual(m.totals(), { income: 95000, spending: 17000, net: 78000 });
  const p = m.project(), food = category(p, 'expense.food'), recovery = category(p, 'expense.food', 'recovery');
  assert.notEqual(recovery.id, food.id);
  assert.equal(p.edges.find(e => e.source === recovery.id && e.target === 'a').amount, 3000);
  assert.equal(p.edges.find(e => e.source === 'a' && e.target === food.id).amount, 20000);
  assert.equal(p.edges.find(e => e.source === food.id && e.target === 'a'), undefined);
  assert.equal(recovery.value, 3000); assert.equal(food.value, 20000);
  assert.equal(food.value - recovery.value, m.totals().spending);
  assert.equal(byMember(p, 'refund').members[0], raw.edges[3]);
  assert.notEqual(byMember(p, 'salary').source, byMember(p, 'salary-reversed').target);
  assert.equal(p.edges.length, 6);
  assert.deepEqual(ids(p.edges.flatMap(e => e.members)), ids(raw.edges));
  const selected = adapter.selectionMembers(p, { node: 'a' });
  assert.equal(selected.length, 6);
  assert.equal(selected.find(e => e.id === 'refund'), raw.edges[3]);
});

test('category identity separates namespaces and cannot collide with retained account IDs', () => {
  const { raw, tx } = fixture();
  const collision = JSON.stringify(['live-category', 'expense', 'shared']);
  raw.nodes.push(account(collision));
  tx('expense', 'expense', 'shared.leaf', 100);
  tx('income', 'income', 'shared.leaf', 200, true);
  tx('empty', 'unresolved', '', 1, false, 'a', 'Unknown category');
  tx('unknown', 'transfer', 'transfer.unknown.cash', 50, true, 'a', 'Unmatched transfer: Cash');
  const m = adapter.createModel(raw), p = m.project();
  const expense = category(p, 'shared'), income = category(p, 'shared', 'income');
  assert.ok(expense); assert.ok(income);
  assert.notEqual(expense.id, collision);
  assert.notEqual(expense?.id, income?.id);
  assert.equal(m.kind(collision), 'account');
  assert.equal(category(p, '', 'unresolved').label, 'Unknown category');
  assert.match(category(p, 'transfer.unknown', 'transfer').label, /^Unmatched transfer:/);
  assert.equal(p.edges.find(e => e.members[0].id === 'empty').members[0].selector.filters.category, '');
});

test('base projection retains every positive member once, including tiny flows and account cycles', () => {
  const { raw, tx, transfer } = fixture();
  tx('food1', 'expense', 'expense.food.groceries', 10000);
  tx('food2', 'expense', 'expense.food.dining', 2500);
  tx('tiny', 'expense', 'expense.other', 1);
  transfer('ab', 'a', 'b', 2000); transfer('ba', 'b', 'a', 1000);
  const m = adapter.createModel(raw), p = m.project();
  assert.deepEqual(p.edges.flatMap(e => e.members.map(x => x.id)).sort(), raw.edges.map(e => e.id).sort());
  assert.equal(p.edges.length, 4);
  assert.equal(category(p, 'expense.food').incoming, 12500);
  assert.equal(category(p, 'expense.other').incoming, 1);
  assert.equal(p.edges.find(e => e.source === 'a' && e.target === 'b').amount, 2000);
  assert.equal(p.edges.find(e => e.source === 'b' && e.target === 'a').amount, 1000);
  assert.equal(m.name('a'), 'Account a'); assert.equal(m.kind('a'), 'account');
  assert.equal(m.kind(category(p, 'expense.food').id), 'expense');
  assert.ok(p.edges.every(e => !e.decomposition && e.type));
  assert.deepEqual(p.currency, raw.currency); assert.deepEqual(p.range, raw.range); assert.deepEqual(p.coverage, raw.coverage);
});
