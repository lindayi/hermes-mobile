"""Synthetic money-map contracts; never read a private finance database."""
import sqlite3

import pytest

from test_finance import BASE, env, finance_module, insert, tx


@pytest.mark.parametrize('with_nodes', [True, False])
def test_category_labels_are_unique_authoritative_or_humanized(env, with_nodes):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        if with_nodes:
            db.execute('CREATE TABLE category_nodes(category_id TEXT PRIMARY KEY, parent_id TEXT, label TEXT NOT NULL, depth INTEGER)')
            insert(db, 'category_nodes', category_id='expense.food', parent_id='expense', label='Food and groceries', depth=2)
        tx(db, 'one', component='whole')
        tx(db, 'two', component='allocation', parent_id='stale-parent')
        tx(db, 'other', category_id='expense.home_insurance', component='whole')
    response = client.get(BASE + '/meta')
    assert response.status_code == 200, response.text
    categories = response.json()['categories']
    assert len(categories) == 2
    food = next(c for c in categories if c['category_id'] == 'expense.food')
    assert food['label'] == ('Food and groceries' if with_nodes else 'Expense / Food')
    assert food['parent_id'] == 'expense'
    assert next(c for c in categories if c['category_id'] == 'expense.home_insurance')['label'] == 'Expense / Home Insurance'
    assert response.json()['coverage']['categories']['complete'] is True


def test_flow_aggregates_all_rows_by_authoritative_kind_and_sign_with_exact_details(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE category_nodes(category_id TEXT PRIMARY KEY, parent_id TEXT, label TEXT NOT NULL, depth INTEGER)')
        insert(db, 'category_nodes', category_id='expense.food', label='Groceries')
        for i in range(1005):
            tx(db, f'food-{i}', component='whole', amount_minor=-10)
        for ident, kind, category, amount in [
            ('refund', 'expense', 'expense.food', 50),
            ('nested', 'expense', 'expense.food.delivery', -70),
            ('salary', 'income', 'income.salary', 15000),
            ('reversed', 'income', 'income.salary', -100),
            ('buy', 'investment', 'invest.trades', -200),
            ('sale', 'investment', 'invest.trades', 300),
            ('loan', 'financing', 'financing.loan', 400),
            ('adjust', 'adjustment', 'expense.food', 20),
            ('unknown', None, 'unknown', -30),
        ]:
            tx(db, ident, flow=kind, category_id=category, amount_minor=amount, component='whole')
        for ident, override in [('family', {'allocation': 'mother'}), ('pending', {'record_status': 'pending'}),
                                ('duplicate', {'duplicate_of': 'food-0'}), ('excluded', {'included': 0}),
                                ('old', {'date': '2024-12-31'}), ('usd', {'currency': 'USD'})]:
            tx(db, ident, amount_minor=-999999, **override)
    response = client.get(BASE + '/flow', params={'start': '2025-01-01', 'end': '2025-12-31', 'limit': 1, 'offset': 50})
    assert response.status_code == 200, response.text
    result = response.json()
    assert response.headers['cache-control'] == 'no-store'
    assert result['range'] == {'start': '2025-01-01', 'end': '2025-12-31'}
    assert result['currency'] == 'CAD'
    assert result['coverage']['complete'] is True
    assert len(result['edges']) == 10
    nodes = {n['id']: n for n in result['nodes']}
    food = next(e for e in result['edges'] if e['selector']['filters']['category'] == 'expense.food' and e['kind'] == 'expense' and e['selector']['filters']['direction'] == 'outgoing')
    assert food['amount_minor'] == 10050 and food['count'] == 1005
    assert nodes[food['target']]['label'] == 'Groceries'
    for edge in result['edges']:
        selector = edge['selector']
        f = selector['filters']
        assert selector['endpoint'] == 'transactions'
        assert f['account'] == 'Checking' and f['category_mode'] == 'exact'
        assert f['flow'] == edge['kind']
        assert f['start'] == result['range']['start'] and f['end'] == result['range']['end']
        assert f['currency'] == 'CAD' and f['scope'] == 'personal' and f['dataset'] == 'all'
        assert 'limit' not in f and 'offset' not in f
        all_items = []
        for offset in range(0, edge['count'], 100):
            detail = client.get(BASE + '/' + selector['endpoint'], params={**f, 'limit': 100, 'offset': offset})
            assert detail.status_code == 200, detail.text
            assert detail.json()['total'] == edge['count']
            all_items.extend(detail.json()['items'])
        assert sum(abs(r['amount_minor']) for r in all_items) == edge['amount_minor']
        account_side = 'target' if f['direction'] == 'incoming' else 'source'
        assert nodes[edge[account_side]]['kind'] == 'account'
        assert 'incoming' in edge['label'].lower() if f['direction'] == 'incoming' else 'outgoing' in edge['label'].lower()
    again = client.get(BASE + '/flow', params={'start': '2025-01-01', 'end': '2025-12-31'}).json()
    assert again['nodes'] == result['nodes'] and again['edges'] == result['edges']


def pair(db, ident, *, source='Checking', target='Broker', amount=200, kind='own_cash',
         credit_currency='CAD', credit_date='2025-02-11', credit_flow='transfer', credit_allocation='personal'):
    insert(db, 'cash_links', link_id=ident, link_type=kind, evidence='Synthetic pair')
    for record, account, value, day, currency, flow, allocation in [
        (ident + '-d', source, -amount, '2025-02-10', 'CAD', 'transfer', 'personal'),
        (ident + '-c', target, amount, credit_date, credit_currency, credit_flow, credit_allocation),
    ]:
        if account is None:
            continue
        leg = dict(link_id=ident, record_id=record, account=account, amount_minor=value,
                   date=day, currency=currency, allocation=allocation, planning_protected=int(allocation != 'personal'))
        insert(db, 'linked_transfer_legs', **leg)
        insert(db, 'linked_transfer_legs', **leg)  # joined-source duplicates must not duplicate cash_links
        tx(db, record, account=account, amount_minor=value, date=day, currency=currency,
           flow=flow, category_id='transfer.cash' if flow == 'transfer' else 'invest.funding',
           dataset='investment' if account == 'Broker' else 'bank', allocation=allocation,
           current_planning_protected=int(allocation != 'personal'))


def test_flow_pairs_once_excludes_matched_legs_and_keeps_exact_unmatched_details(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        pair(db, 'one', credit_flow='investment')
        pair(db, 'two', amount=300, credit_flow='financing')
        pair(db, 'card', target='Card', kind='card_settlement', amount=80)
        pair(db, 'other-kind', kind='investment_funding', amount=100)
        pair(db, 'fx', credit_currency='USD', amount=90)
        pair(db, 'missing', target=None, amount=60)
        pair(db, 'same', target='Checking', amount=40)
        tx(db, 'unmatched', flow='expense', category_id='transfer.cash', amount_minor=-10)
    response = client.get(BASE + '/flow', params={'start': '2025-02-10', 'end': '2025-02-28'})
    assert response.status_code == 200, response.text
    body = response.json()
    paired = [e for e in body['edges'] if e['selector']['endpoint'] == 'transfers']
    assert len(paired) == 3
    own = next(e for e in paired if e['selector']['filters']['link_type'] == 'own_cash')
    assert own['count'] == 2 and own['amount_minor'] == 500
    assert own['selector']['filters']['source_account'] == 'Checking'
    assert own['selector']['filters']['target_account'] == 'Broker'
    assert all(e['kind'] == 'transfer' for e in paired)
    observations = [e for e in body['edges'] if e['selector']['endpoint'] == 'transactions']
    assert len(observations) == 2
    assert all('unmatched' in e['label'].lower() for e in observations)
    assert sum(e['amount_minor'] for e in observations) == 240
    for edge in body['edges']:
        selector = edge['selector']
        detail = client.get(BASE + '/' + selector['endpoint'], params=selector['filters'])
        assert detail.status_code == 200, detail.text
        data = detail.json()
        assert data['total'] == edge['count']
        if selector['endpoint'] == 'transfers':
            assert sum(-item['debit']['amount_minor'] for item in data['items']) == edge['amount_minor']
        else:
            assert sum(abs(item['amount_minor']) for item in data['items']) == edge['amount_minor']
            assert {r['entry_id'] for r in data['items']} <= {'fx-d', 'missing-d', 'same-d', 'same-c', 'unmatched'}
    assert body['coverage']['pairs']['missing_counterpart'] == 1
    assert body['coverage']['pairs']['cross_currency'] == 1
    assert body['coverage']['pairs']['same_account'] == 1
    assert any('debit' in w.lower() and 'date' in w.lower() for w in body['warnings'])
    assert any('counterpart' in w.lower() for w in body['warnings'])


def test_flow_reports_output_bounds_and_unrepresentable_observations(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        for i in range(1002):
            tx(db, str(i), category_id=f'expense.synthetic_{i:04}', amount_minor=-10)
        tx(db, 'zero', amount_minor=0)
        tx(db, 'no-amount', amount_minor=None)
        tx(db, 'no-category', category_id=None, amount_minor=-50)
        tx(db, 'no-account', account=None, amount_minor=-60)
        tx(db, 'long-category', category_id='x' * 121, amount_minor=-70)
        pair(db, 'valid-pair')
    response = client.get(BASE + '/flow')
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body['edges']) == 1000
    assert body['coverage']['edges'] == {'total': 1003, 'returned': 1000, 'omitted': 3, 'complete': False}
    assert body['coverage']['transactions']['zero_amount'] == 1
    assert body['coverage']['transactions']['unknown_amount'] == 1
    assert body['coverage']['transactions']['unselectable'] == 3
    assert body['coverage']['complete'] is False
    assert any('partial' in w.lower() for w in body['warnings'])
    assert any('unknown' in w.lower() or 'unselectable' in w.lower() for w in body['warnings'])
    assert all(e['amount_minor'] > 0 for e in body['edges'])
    assert all(e['selector']['filters']['category'] is not None for e in body['edges'])


@pytest.mark.parametrize('params,expected', [
    ({'flow': 'expense'}, [('transactions', 25)]),
    ({'flow': 'income'}, []),
    ({'flow': 'transfer'}, [('transfers', 200)]),
    ({'scope': 'protected'}, [('transfers', 200)]),
    ({'account': 'Broker'}, [('transfers', 200)]),
    ({'dataset': 'investment'}, [('transfers', 200)]),
    ({'category': 'transfer.cash', 'category_mode': 'exact', 'dataset': 'investment', 'direction': 'outgoing'}, [('transfers', 200)]),
    ({'start': '2025-02-11', 'end': '2025-02-11'}, []),
    ({'transfer_status': 'matched'}, [('transfers', 200)]),
    ({'transfer_status': 'unmatched'}, [('transactions', 25)]),
])
def test_flow_filters_preserve_pair_source_date_membership_and_scope(env, params, expected):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        pair(db, 'boundary', credit_allocation='mother_reserved')
        tx(db, 'food', amount_minor=-25)
    response = client.get(BASE + '/flow', params={'start': '2025-02-10', 'end': '2025-02-10', **params})
    assert response.status_code == 200, response.text
    body = response.json()
    assert sorted((e['selector']['endpoint'], e['amount_minor']) for e in body['edges']) == expected
    for edge in body['edges']:
        selector = edge['selector']
        detail = client.get(BASE + '/' + selector['endpoint'], params=selector['filters'])
        assert detail.status_code == 200, detail.text
        assert detail.json()['total'] == edge['count']
        if selector['endpoint'] == 'transfers':
            assert detail.json()['items'][0]['protected_boundary'] is True


def test_flow_exact_empty_categories_and_records_without_source_id_are_not_lost(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        pair(db, 'matched')
        tx(db, 'no-source-id', record_id=None, category_id='', amount_minor=-25)
        tx(db, 'other-category', amount_minor=-100)
    body = client.get(BASE + '/flow').json()
    edge = next((e for e in body['edges'] if e['selector']['filters'].get('category') == ''), None)
    assert edge is not None, 'An unlinked record without source ID is still an eligible observation'
    assert edge['count'] == 1 and edge['amount_minor'] == 25
    detail = client.get(BASE + '/transactions', params=edge['selector']['filters'])
    assert detail.status_code == 200, detail.text
    assert detail.json()['total'] == 1
    assert detail.json()['items'][0]['entry_id'] == 'no-source-id'


@pytest.mark.parametrize('params', [
    {'source_account': 'x' * 201}, {'target_account': 'x' * 201},
    {'link_type': 'x' * 121}, {'direction': 'sideways'}, {'transfer_status': 'unknown'},
])
def test_new_flow_filters_are_bounded(env, params):
    response = env[0].get(BASE + '/flow', params=params)
    assert response.status_code == 422
    assert response.headers['cache-control'] == 'no-store'


def test_pair_selectors_are_parameterized_and_do_not_constrain_transactions(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        pair(db, 'one')
    for field in ('source_account', 'target_account', 'link_type'):
        params = {field: "' OR 1=1--"}
        assert client.get(BASE + '/transfers', params=params).json()['total'] == 0
        assert client.get(BASE + '/transactions', params=params).json()['total'] == 2


def test_unselectable_pair_identifiers_are_omitted_with_explicit_coverage(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        pair(db, 'null-type', kind=None)
        pair(db, 'long-source', source='x' * 201)
        pair(db, 'valid', kind='own_cash')
    response = client.get(BASE + '/flow')
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body['edges']) == 1
    assert body['edges'][0]['amount_minor'] == 200
    assert body['coverage']['pairs']['unselectable'] == 2
    assert body['coverage']['complete'] is False
    assert any('unselectable' in w.lower() for w in body['warnings'])
