"""Synthetic-only finance API contract and security regressions."""
import importlib
import json
from pathlib import Path
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.auth import AuthService, COOKIE, digest

ROOT = Path(__file__).resolve().parents[1]
BASE = '/hermes/app-api/finance'
SCHEMA = json.loads((ROOT / 'docs/finance-schema-contract.json').read_text())


def finance_module():
    assert (ROOT / 'backend/finance.py').exists(), 'Finance router implementation is missing'
    return importlib.import_module('backend.finance')


def insert(db, table, **values):
    keys = ','.join('"' + k + '"' for k in values)
    db.execute(f'INSERT INTO "{table}" ({keys}) VALUES ({",".join("?" for _ in values)})', tuple(values.values()))


@pytest.fixture
def env(tmp_path):
    module = finance_module()
    profile = tmp_path / 'default'
    (profile / 'finance').mkdir(parents=True)
    path = profile / 'finance/finance.sqlite3'
    with sqlite3.connect(path) as db:
        for table, columns in SCHEMA.items():
            ddl = ','.join('"' + c['name'] + '" ' + c['type'] for c in columns)
            db.execute(f'CREATE TABLE "{table}" ({ddl})')
    now = [1_800_000_000.0]
    auth = AuthService(tmp_path / 'auth.sqlite3', clock=lambda: now[0])
    with auth.store.transaction() as db:
        db.execute('INSERT INTO users VALUES (?,?,?,?,?,?)', ('owner', 'owner', 'default', 'ready', 'Synthetic owner', now[0]))
        db.execute('INSERT INTO sessions(id,token_hash,user_id,csrf_token,created_at,last_active_at,last_verified_at) VALUES (?,?,?,?,?,?,?)', ('session', digest('synthetic-session'), 'owner', 'csrf', now[0], now[0], now[0]))
    app = FastAPI()
    app.include_router(module.build_finance_router(auth, profile))
    client = TestClient(app, base_url='https://finance.example')
    client.cookies.set(COOKIE, 'synthetic-session', domain='finance.example', path='/hermes')
    return client, path, auth, now


def tx(db, entry_id, **kw):
    values = dict(entry_id=entry_id, record_id=entry_id, dataset='bank', account='Checking', date='2025-02-10', cash_month='2025-02', category_id='expense.food', parent_id='expense', component='food', flow='expense', amount_minor=-100, income_minor=0, expense_minor=100, currency='CAD', included=1, record_status='posted', allocation='personal', current_planning_protected=0, description='Synthetic purchase', merchant='Synthetic shop')
    values.update(kw)
    insert(db, 'hierarchical_transactions', **values)


def test_registered_room_facts_and_mortgage_intent_are_available_as_dated_context(env):
    client,path,_,_=env
    with sqlite3.connect(path) as db:
        insert(db,'facts',fact_id='room',fact_name='rrsp_remaining_before_hbp',value_json='"123.45"',reviewer='cross_tax',source_id='synthetic-tax')
        insert(db,'facts',fact_id='hbp',fact_name='hbp_latest_balance_minimum',value_json='{"balance":"900.00","minimum_2030":"90.00"}',reviewer='cross_tax',source_id='synthetic-tax')
        insert(db,'user_context',key='td_mortgage_payment',value_json='{"status":"intended, not confirmed set"}',as_of='2030-01-01',source='Synthetic user context')
    result=client.get(BASE+'/obligations').json()
    assert {r['fact_id'] for r in result['facts']} >= {'room','hbp'}
    assert 'td_mortgage_payment' in {r['key'] for r in result['context']}


def test_obligation_nested_numeric_overflow_remains_json_serializable(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        insert(db, 'facts', fact_id='overflow', fact_name='rrsp_room', value_json='{"amount_minor":1e999,"nested":[-1e999,{"value":1e999,"finite":1.5}],"zero":0}', reviewer='cross_tax')
    response = client.get(BASE + '/obligations')
    assert response.status_code == 200
    value = next(row['value'] for row in response.json()['facts'] if row['fact_id'] == 'overflow')
    assert value == {'amount_minor': None, 'nested': [None, {'value': None, 'finite': 1.5}], 'zero': 0}
    assert 'Infinity' not in response.text


def test_obligation_fact_numeric_family_is_bounded_and_keeps_safe_projection(env):
    client, path, _, _ = env
    prefix, suffix = 'rrsp_remaining_with_', '_hbp_from_existing_contributions'
    with sqlite3.connect(path) as db:
        for designation in ('731', '815', '', '73x1', '-731', '7.31', '1234567890'):
            insert(db, 'facts', fact_id='family-' + designation, account='Checking',
                   fact_name=prefix + designation + suffix, value_json='{"amount_minor":123}',
                   raw_json='PRIVATE RAW', source_id='synthetic')
        for name in ('arbitrary_secret', prefix + '731_wrong_suffix', 'wrong_prefix_731' + suffix):
            insert(db, 'facts', fact_id=name, fact_name=name, value_json='"PRIVATE VALUE"')
        insert(db, 'account_policies', account='Checking', allocation='personal', planning_protected=0)
        insert(db, 'account_policies', account='Family', allocation='mother', planning_protected=1)
        insert(db, 'facts', fact_id='protected', account='Family', fact_name=prefix + '731' + suffix, value_json='123')
    response = client.get(BASE + '/obligations')
    assert response.status_code == 200, response.text
    facts = response.json()['facts']
    assert {r['fact_id'] for r in facts} == {'family-731', 'family-815'}
    assert {r['fact_name'] for r in facts} == {prefix + n + suffix for n in ('731', '815')}
    assert all(r['value'] == {'amount_minor': 123} for r in facts)
    assert 'PRIVATE' not in response.text and 'raw_json' not in response.text
    assert client.get(BASE + '/obligations?account=Other').json()['facts'] == []
    protected = client.get(BASE + '/obligations?scope=protected').json()['facts']
    assert [r['fact_id'] for r in protected] == ['protected']


@pytest.mark.parametrize('root',[Path('/var/www/html/owner'),ROOT/'frontend'])
def test_public_roots_rejected_before_sqlite_connect(root,monkeypatch):
    called=[]
    def connect(*args,**kwargs):
        called.append(args)
        raise sqlite3.OperationalError('synthetic denied open')
    monkeypatch.setattr(sqlite3,'connect',connect)
    with pytest.raises(sqlite3.OperationalError):
        with finance_module().open_finance(root):pass
    assert not called


def test_meta_uses_recorded_range_and_private_headers(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        tx(db, 'a')
    response = client.get(BASE + '/meta')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['range'] == {'start': '2025-02-10', 'end': '2025-02-10'}
    assert body['meta']['source'] == 'sqlite'
    assert body['accounts'][0]['account'] == 'Checking'
    assert body['currencies'] == ['CAD']
    assert body['categories'][0]['category_id'] == 'expense.food'
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['vary'] == 'Cookie'


def test_overview_and_drilldown_conserve_signed_allocations(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        tx(db, 'pay', flow='income', category_id='income.salary', amount_minor=10000, income_minor=10000, expense_minor=0)
        tx(db, 'food', amount_minor=-3000, expense_minor=3000)
        tx(db, 'recovery', amount_minor=500, expense_minor=-500)
        tx(db, 'transfer', flow='transfer', category_id='transfer.card', amount_minor=-4000, expense_minor=4000)
        tx(db, 'restricted', amount_minor=-200, expense_minor=200, current_planning_protected=1)
        tx(db, 'mother', allocation='mother', amount_minor=-999, expense_minor=999, current_planning_protected=1)
        tx(db, 'usd', currency='USD', expense_minor=900)
        tx(db, 'pending', record_status='pending', expense_minor=900)
        tx(db, 'duplicate', duplicate_of='food', expense_minor=900)
        tx(db, 'excluded', included=0, expense_minor=900)
        tx(db, 'old', date='2024-02-10', cash_month='2024-02', expense_minor=900)
    response = client.get(BASE + '/overview')
    assert response.status_code == 200, response.text
    data = response.json()
    total = data['totals'][0]
    assert (total['income_minor'], total['expense_minor'], total['net_minor'], total['transfer_out_minor']) == (10000, 2700, 7300, 4000)
    assert data['monthly'][0]['net_minor'] == 7300
    assert data['bank_balances'] == []
    assert data['warnings']
    detail = client.get(BASE + '/transactions?category=expense.food').json()
    assert detail['total'] == 3
    assert sum(item['expense_minor'] for item in detail['items']) == total['expense_minor']
    assert client.get(BASE + '/overview?scope=all').json()['totals'][0]['expense_minor'] == 3699
    assert client.get(BASE + '/overview?scope=protected').json()['totals'][0]['expense_minor'] == 1199
    assert client.get(BASE + '/transactions?currency=USD').json()['total'] == 1


def test_exact_category_drilldown_excludes_nested_categories(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        tx(db, 'food', amount_minor=-100, expense_minor=100)
        tx(db, 'delivery', category_id='expense.food.delivery', amount_minor=-200, expense_minor=200)
    # The legacy prefix selector remains the hierarchy-wide search.
    prefix = client.get(BASE + '/transactions', params={'category': 'expense.food'}).json()
    assert prefix['total'] == 2
    response = client.get(BASE + '/transactions', params={'category': 'expense.food', 'category_mode': 'exact'})
    assert response.status_code == 200, response.text
    detail = response.json()
    assert [r['entry_id'] for r in detail['items']] == ['food']
    band = next(r for r in client.get(BASE + '/overview').json()['categories'] if r['category_id'] == 'expense.food')
    assert sum(r['amount_minor'] for r in detail['items']) == band['amount_minor'] == -100


def test_category_bands_separate_mixed_flows_and_reconcile_exact_drilldowns(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        tx(db, 'food', amount_minor=-1000, expense_minor=1000)
        tx(db, 'correction', flow='adjustment', amount_minor=200, expense_minor=-200)
        tx(db, 'delivery', category_id='expense.food.delivery', amount_minor=-300, expense_minor=300)
        tx(db, 'recovery', category_id='expense.refund', amount_minor=50, expense_minor=-50)
        tx(db, 'pay', category_id='income.salary', flow='income', amount_minor=2000, income_minor=2000, expense_minor=0)
        tx(db, 'pay-reversal', category_id='income.salary', flow='income', amount_minor=-100, income_minor=-100, expense_minor=0)
        # Effective FLOW overrides raw expense for transfer categories, and falls back to unresolved.
        tx(db, 'card', category_id='transfer.card', flow='expense', amount_minor=-400, expense_minor=400)
        tx(db, 'unknown', category_id='other', flow=None, amount_minor=-10, expense_minor=10)
        tx(db, 'investment', flow='investment', amount_minor=-20, expense_minor=20)
        tx(db, 'financing', flow='financing', amount_minor=30, expense_minor=-30)
    overview = client.get(BASE + '/overview').json()
    bands = overview['categories']
    food = [r for r in bands if r['category_id'] == 'expense.food']
    assert len(food) == 4  # expense, adjustment, investment and financing stay distinct.
    assert all({'flow', 'income_minor', 'expense_minor', 'amount_minor', 'count'} <= r.keys() for r in bands)
    assert next(r for r in food if r['flow'] == 'expense')['expense_minor'] == 1000
    assert next(r for r in food if r['flow'] == 'adjustment')['expense_minor'] == 0
    for band in bands:
        params = {'category': band['category_id'], 'category_mode': 'exact', 'flow': band['flow']}
        response = client.get(BASE + '/transactions', params=params)
        assert response.status_code == 200, response.text
        detail = response.json()
        assert detail['total'] == band['count']
        assert sum(r['amount_minor'] for r in detail['items']) == band['amount_minor']
        totals = client.get(BASE + '/overview', params=params).json()['totals'][0]
        assert (totals['income_minor'], totals['expense_minor']) == (band['income_minor'], band['expense_minor'])
    assert sum(r['income_minor'] for r in bands) == overview['totals'][0]['income_minor'] == 1900
    assert sum(r['expense_minor'] for r in bands) == overview['totals'][0]['expense_minor'] == 1250
    assert next(r for r in bands if r['category_id'] == 'expense.refund')['expense_minor'] == -50
    assert {r['entry_id'] for r in client.get(BASE + '/transactions?flow=transfer').json()['items']} == {'card'}
    assert {r['entry_id'] for r in client.get(BASE + '/transactions?flow=unresolved').json()['items']} == {'unknown'}


def test_transactions_bound_search_escape_and_safe_projection(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        tx(db, 'one', description='<script>alert(1)</script> 10%_', drive_url='https://evil.example/secret', source_pdf='/private/file', evidence='Synthetic evidence')
        tx(db, 'two', description='ordinary', drive_url='https://drive.google.com/file/d/synthetic/view')
    result = client.get(BASE + '/transactions?q=%25_&limit=1').json()
    assert result['total'] == 1
    assert result['items'][0]['entry_id'] == 'one'
    assert result['items'][0]['description'].startswith('<script>')
    assert result['items'][0]['drive_url'] is None
    assert 'source_pdf' not in result['items'][0]
    assert client.get(BASE + '/transactions?q=%27%20OR%201%3D1--').json()['total'] == 0
    result = client.get(BASE + '/transactions?record_id=two').json()
    assert result['total'] == 1
    assert result['items'][0]['drive_url'].startswith('https://drive.google.com/')
    assert client.get(BASE + '/transactions?limit=1&offset=1').json()['total'] == 2


@pytest.mark.parametrize('identifier', ['tx_id', 'event_id'])
def test_raw_payroll_record_id_drilldown_preserves_regular_filters(env, identifier):
    client, path, _, _ = env
    raw_id = 'raw-payroll-link'
    with sqlite3.connect(path) as db:
        tx(db, 'eligible', record_id='bank:raw-payroll-link', **{identifier: raw_id})
        for name, override in [
            ('currency', {'currency': 'USD'}), ('account', {'account': 'Other'}),
            ('scope', {'allocation': 'mother'}), ('dataset', {'dataset': 'investment'}),
            ('category', {'category_id': 'expense.food.delivery'}), ('flow', {'flow': 'adjustment'}),
            ('search', {'description': 'unrelated', 'merchant': 'unrelated'}),
            ('old', {'date': '2024-12-31'}), ('future', {'date': '2026-01-01'}),
            ('pending', {'record_status': 'pending'}), ('duplicate', {'duplicate_of': 'eligible'}),
            ('excluded', {'included': 0}),
        ]:
            tx(db, name, **{identifier: raw_id}, **override)
        insert(db, 'payroll_cashflow_bridge', payroll_id='pay', pay_date='2025-02-10', currency='CAD')
        insert(db, 'payroll_component_links', payroll_id='pay', code='NET', **{identifier: raw_id})
    linked = client.get(BASE + '/compensation').json()['links'][0][identifier]
    params = {'record_id': linked, 'start': '2025-01-01', 'end': '2025-12-31',
              'account': 'Checking', 'dataset': 'bank', 'category': 'expense.food',
              'category_mode': 'exact', 'flow': 'expense', 'q': 'Synthetic', 'limit': 1}
    response = client.get(BASE + '/transactions', params=params)
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail['total'] == 1
    assert [r['entry_id'] for r in detail['items']] == ['eligible']
    assert client.get(BASE + '/transactions', params={**params, 'offset': 1}).json()['items'] == []
    assert client.get(BASE + '/transactions', params={**params, 'record_id': "' OR 1=1--"}).json()['total'] == 0
    # Canonical record IDs still work alongside the two raw identifiers.
    assert client.get(BASE + '/transactions', params={**params, 'record_id': 'bank:raw-payroll-link'}).json()['total'] == 1


@pytest.mark.parametrize('membership', [
    {'dataset': 'investment'},
    {'category': 'transfer.receiving', 'category_mode': 'exact'},
    {'category': 'transfer.receiv', 'category_mode': 'prefix'},
    {'dataset': 'investment', 'category': 'transfer.receiving', 'category_mode': 'exact'},
])
@pytest.mark.parametrize('receiving_allocation', ['personal', 'mother_reserved'])
def test_transfer_membership_keeps_cross_date_receiving_endpoint(env, membership, receiving_allocation):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        insert(db, 'cash_links', link_id='year-boundary', debit_tx='debit',
               credit_tx='credit', link_type='investment_funding', evidence='Synthetic pair')
        for record, account, amount, day, dataset, category in [
            ('debit', 'Checking', -200, '2025-12-31', 'bank', 'transfer.sending'),
            ('credit', 'Broker', 200, '2026-01-01', 'investment', 'transfer.receiving'),
        ]:
            insert(db, 'linked_transfer_legs', link_id='year-boundary', record_id=record,
                   account=account, amount_minor=amount, date=day, currency='CAD',
                   allocation=receiving_allocation if record == 'credit' else 'personal', planning_protected=0)
            tx(db, record, account=account, amount_minor=amount, date=day,
               dataset=dataset, category_id=category, flow='transfer', expense_minor=0,
               allocation=receiving_allocation if record == 'credit' else 'personal')
    params = {'start': '2025-12-31', 'end': '2025-12-31', 'account': 'Checking', **membership}
    response = client.get(BASE + '/transfers', params=params)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['total'] == 1
    pair = result['items'][0]
    assert pair['link_id'] == 'year-boundary'
    assert pair['debit']['date'] == '2025-12-31'
    assert pair['credit']['date'] == '2026-01-01'
    assert pair['missing_counterpart'] is False
    assert result['network']['links'] == [
        {'source': 'Checking', 'target': 'Broker', 'currency': 'CAD', 'amount_minor': 200, 'count': 1},
    ]
    # Membership does not move the pair into the credit-date range or relax filters.
    for override in [
        {'start': '2026-01-01', 'end': '2026-01-01'},
        {'account': 'Other'}, {'currency': 'USD'},
        *([{'scope': 'protected'}] if receiving_allocation == 'personal' else []),
        {'category': 'transfer.absent'},
        {'dataset': 'bank', 'category': 'transfer.receiving', 'category_mode': 'exact'},
    ]:
        excluded = client.get(BASE + '/transfers', params={**params, **override})
        assert excluded.status_code == 200, excluded.text
        assert excluded.json()['total'] == 0, override
        assert excluded.json()['network']['links'] == [], override


def test_transfers_pair_once_complete_network_and_boundary_dates(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        tx(db, 'span', date='2025-12-31')
        for i in range(3):
            insert(db, 'cash_links', link_id=f'l{i}', debit_tx=f'd{i}', credit_tx=f'c{i}', link_type='investment_funding', evidence='Matched pair')
            for record, account, amount, day, allocation, protected in [
                (f'd{i}', 'Checking', -200, '2025-12-31', 'personal', 0),
                (f'c{i}', 'Savings', 200, '2026-01-01', 'mother_reserved' if i == 0 else 'personal', int(i == 0)),
            ]:
                insert(db, 'linked_transfer_legs', link_id=f'l{i}', record_id=record, account=account, amount_minor=amount, date=day, currency='CAD', allocation=allocation, planning_protected=protected)
        insert(db, 'cash_links', link_id='missing', debit_tx='dmissing', link_type='own_cash')
        insert(db, 'linked_transfer_legs', link_id='missing', record_id='dmissing', account='Checking', amount_minor=-100, date='2025-12-31', currency='CAD', allocation='personal', planning_protected=0)
    response = client.get(BASE + '/transfers?start=2025-12-31&end=2025-12-31&limit=1')
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['total'] == 4
    assert len(result['items']) == 1
    link = result['network']['links'][0]
    assert (link['source'], link['target'], link['amount_minor'], link['count']) == ('Checking', 'Savings', 600, 3)
    result = client.get(BASE + '/transfers?start=2025-12-31&end=2025-12-31').json()
    pairs = {item['link_id']: item for item in result['items']}
    assert pairs['l0']['protected_boundary'] is True
    assert pairs['l0']['credit']['date'] == '2026-01-01'
    assert pairs['missing']['credit'] is None
    assert result['network']['coverage']['complete'] is True


def test_investment_policy_exclusions_do_not_count_date_only_exclusions(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        for month, start, end in [('march', '2025-03-01', '2025-03-31'), ('april', '2025-04-01', '2025-04-30')]:
            insert(db, 'investment_balance_history', observation_id=month, account='Broker', as_of=end, currency='CAD', source_rank=1, allocation='personal', planning_protected=0)
            insert(db, 'investment_period_metrics', period_id=month, account='Broker', start_date=start, end_date=end, currency='CAD', allocation='personal', planning_protected=0)
        insert(db, 'investment_balance_history', observation_id='duplicate-april', account='Broker', as_of='2025-04-30', currency='CAD', source_rank=2, allocation='personal', planning_protected=0)
    result = client.get(BASE + '/investments?start=2025-04-01&end=2025-04-30').json()
    assert [row['observation_id'] for row in result['history']] == ['april']
    assert [row['period_id'] for row in result['periods']] == ['april']
    assert result['coverage']['history']['excluded_by_coherence_or_period_policy'] == 1
    assert result['coverage']['periods']['excluded_by_coherence_or_period_policy'] == 0


def test_investments_preserve_unknown_basis_and_nonoverlapping_periods(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        tx(db, 'range')
        insert(db, 'investment_latest_balances', observation_id='balance', account='Broker', as_of='2025-02-28', currency='CAD', market_value_minor=11000, cash_minor=1000, book_cost_minor=None, allocation='personal', planning_protected=0)
        insert(db, 'investment_latest_holdings', observation_id='current', account='Broker', as_of='2025-02-27', currency='CAD', security_key='SYNTH', quantity='10', quantity_basis='shares', market_value_minor=10000, book_cost_minor=None, cost_basis_type='unknown', allocation='personal', planning_protected=0)
        for ident, start, end in [('jan','2025-01-01','2025-01-31'), ('feb','2025-01-31','2025-02-28'), ('ytd','2025-01-01','2025-02-28')]:
            insert(db, 'investment_period_metrics', period_id=ident, account='Broker', start_date=start, end_date=end, currency='CAD', reported_gain_minor=100, return_pct=None, allocation='personal', planning_protected=0)
        insert(db, 'investment_realized_metrics', group_id='unknown', account='Broker', currency='CAD', cost_minor=None, realized_gain_minor=None, cost_basis_type='unknown', raw_json='DO NOT RETURN', allocation='personal', planning_protected=0)
        for i in range(1001):
            insert(db, 'investment_balance_history', observation_id=str(i), account='Broker', as_of='2025-02-01', currency='CAD', market_value_minor=i, allocation='personal', planning_protected=0)
    response = client.get(BASE + '/investments')
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['balances'][0]['market_value_minor'] == 11000
    assert result['holdings'][0]['book_cost_minor'] is None
    assert len(result['holdings']) == 1
    assert {r['period_id'] for r in result['periods']} == {'jan', 'feb'}
    assert result['realized'][0]['realized_gain_minor'] is None
    assert len(result['history']) == 1000
    assert result['coverage']['history']['omitted'] == 1
    assert not result['coverage']['history']['complete']
    assert 'raw_json' not in response.text and 'DO NOT RETURN' not in response.text
    assert result['warnings']


@pytest.mark.parametrize('identifier,dataset', [('tx_id', 'bank'), ('event_id', 'investment')])
def test_payroll_links_resolve_safe_actual_endpoint_outside_pay_date(env, identifier, dataset):
    client, path, _, _ = env
    raw_id = "raw-payroll' OR 1=1--"
    expected = {'record_id': dataset + ':posted', 'date': '2026-01-02',
                'account': 'Payroll destination', 'currency': 'USD', 'dataset': dataset}
    with sqlite3.connect(path) as db:
        insert(db, 'payroll_cashflow_bridge', payroll_id='pay', pay_date='2025-12-31', currency='CAD')
        insert(db, 'payroll_component_links', payroll_id='pay', code='NET', **{identifier: raw_id})
        tx(db, 'posted', **expected, **{identifier: raw_id},
           source_pdf='PRIVATE PATH', description='PRIVATE DESCRIPTION')
        tx(db, 'posted-allocation', **expected, **{identifier: raw_id}, allocation='family')
        tx(db, 'aaa-wrong-dataset', **{identifier: raw_id},
           dataset='investment' if dataset == 'bank' else 'bank')
        # An unrelated earlier record must not satisfy the bound original ID.
        tx(db, 'aaa-unrelated', **{identifier: 'unrelated'})
        for code, invalid in [('pending', {'record_status': 'pending'}),
                              ('duplicate', {'duplicate_of': 'posted'}),
                              ('excluded', {'included': 0}), ('void', {'record_status': 'void'}),
                              ('status-excluded', {'record_status': 'excluded'}),
                              ('status-duplicate', {'record_status': 'duplicate'})]:
            tx(db, code, dataset=dataset, **{identifier: code}, **invalid)
            insert(db, 'payroll_component_links', payroll_id='pay', code=code, **{identifier: code})
        insert(db, 'payroll_component_links', payroll_id='pay', code='missing', **{identifier: 'absent'})
        insert(db, 'payroll_component_links', payroll_id='pay', code='unmatched')
    response = client.get(BASE + '/compensation', params={'start': '2025-12-31', 'end': '2025-12-31'})
    assert response.status_code == 200, response.text
    result = response.json()
    links = {link['code']: link for link in result['links']}
    assert links['NET'].get('linked_record') == expected
    assert links['NET'][identifier] == raw_id
    assert all(link['linked_record'] is None for code, link in links.items() if code != 'NET')
    assert result['coverage']['links']['total'] == len(links) == 9
    assert result['coverage']['links']['complete'] is True
    assert 'PRIVATE' not in response.text and 'source_pdf' not in response.text
    assert response.headers['cache-control'] == 'no-store'
    endpoint = links['NET']['linked_record']
    detail = client.get(BASE + '/transactions', params={
        'record_id': endpoint['record_id'], 'start': endpoint['date'], 'end': endpoint['date'],
        'account': endpoint['account'], 'currency': endpoint['currency'],
        'dataset': endpoint['dataset'], 'scope': 'all',
    })
    assert detail.status_code == 200, detail.text
    assert {item['entry_id'] for item in detail.json()['items']} == {'posted', 'posted-allocation'}


def test_payroll_link_resolution_reads_hierarchy_once_for_returned_ids(env, monkeypatch):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        insert(db, 'payroll_cashflow_bridge', payroll_id='pay', pay_date='2025-12-31', currency='CAD')
        for dataset, identifier in [('bank', 'tx_id'), ('investment', 'event_id')]:
            for i in range(3):
                raw_id = 'raw-' + str(i)
                record_id = dataset + ':' + raw_id
                tx(db, record_id, dataset=dataset, **{identifier: raw_id})
                insert(db, 'payroll_component_links', payroll_id='pay', code=record_id, **{identifier: raw_id})
    statements = []
    connect = sqlite3.connect
    def traced_connect(*args, **kwargs):
        db = connect(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr(sqlite3, 'connect', traced_connect)
    response = client.get(BASE + '/compensation')
    assert response.status_code == 200, response.text
    links = response.json()['links']
    assert len(links) == 6
    assert all(link['linked_record']['record_id'] == link['code'] for link in links)
    hierarchy_reads = [sql for sql in statements if 'FROM HIERARCHICAL_TRANSACTIONS' in sql.upper()]
    assert len(hierarchy_reads) == 1, hierarchy_reads


def test_compensation_children_only_cover_newest_payroll_not_lexical_first(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        for i in range(41):
            payroll_id = f'pay-{i:02}'
            insert(db, 'payroll_cashflow_bridge', payroll_id=payroll_id,
                   pay_date='2025-02-01' if i == 40 else '2025-01-01', currency='CAD')
            for j in range(25):
                insert(db, 'payroll_components', payroll_id=payroll_id, code=f'C{j:02}', amount_minor=100)
                insert(db, 'payroll_component_links', payroll_id=payroll_id, code=f'C{j:02}', tx_id=payroll_id)
        tx(db, 'bank:newest', tx_id='pay-40', date='2025-02-02')
    response = client.get(BASE + '/compensation')
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['payroll'][0]['payroll_id'] == 'pay-40'
    assert len(result['payroll']) == 41
    assert result['coverage']['payroll'] == {'total': 41, 'returned': 41, 'omitted': 0, 'complete': True}
    for key in ('components', 'links'):
        assert {r['payroll_id'] for r in result[key]} == {'pay-40'}
        assert len(result[key]) == 25
        assert result['coverage'][key] == {'total': 25, 'returned': 25, 'omitted': 0, 'complete': True}
    assert result['coverage']['selected_payroll_id'] == 'pay-40'
    assert all(link['linked_record']['record_id'] == 'bank:newest' for link in result['links'])
    assert any('Components and links' in warning and 'selected payroll sample' in warning for warning in result['warnings'])
    # The server's date-descending, ID-ascending order also selects ties deterministically.
    older = client.get(BASE + '/compensation?end=2025-01-01').json()
    assert older['coverage']['selected_payroll_id'] == older['payroll'][0]['payroll_id'] == 'pay-00'
    for key in ('components', 'links'):
        assert {r['payroll_id'] for r in older[key]} == {'pay-00'}
    empty = client.get(BASE + '/compensation?currency=USD').json()
    assert empty['coverage']['selected_payroll_id'] is None
    assert empty['payroll'] == empty['components'] == empty['links'] == []


@pytest.mark.parametrize('component_count', [1000, 1001])
def test_compensation_oversized_selected_component_group_is_withheld(env, component_count):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        for payroll_id, pay_date in [('old', '2025-01-01'), ('selected', '2025-02-01')]:
            insert(db, 'payroll_cashflow_bridge', payroll_id=payroll_id, pay_date=pay_date,
                   currency='CAD', gross_minor=500000, net_minor=350000)
        for i in range(component_count):
            insert(db, 'payroll_components', payroll_id='selected', code=f'C{i:04}', amount_minor=100)
        for i in range(1001):
            insert(db, 'payroll_component_links', payroll_id='selected', code=f'L{i:04}', tx_id='posted')
        tx(db, 'bank:posted', tx_id='posted')
        insert(db, 'equity_awards', award_id='reference', employer='Synthetic employer', grant_currency='USD')
    response = client.get(BASE + '/compensation')
    assert response.status_code == 200, response.text
    result = response.json()
    complete = component_count <= 1000
    assert len(result['components']) == (component_count if complete else 0)
    assert result['coverage']['components'] == {
        'total': component_count, 'returned': component_count if complete else 0,
        'omitted': 0 if complete else component_count, 'complete': complete,
    }
    assert result['coverage']['selected_payroll_id'] == 'selected'
    assert [r['payroll_id'] for r in result['payroll']] == ['selected', 'old']
    assert result['payroll'][0]['net_minor'] == 350000
    assert result['awards'][0]['award_id'] == 'reference'
    assert result['coverage']['links'] == {'total': 1001, 'returned': 1000, 'omitted': 1, 'complete': False}
    assert len(result['links']) == 1000
    assert all(link['linked_record']['record_id'] == 'bank:posted' for link in result['links'])
    if not complete:
        assert any('breakdown unavailable' in warning and 'withheld in full' in warning for warning in result['warnings'])


def test_compensation_dated_awards_and_payroll_components(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        insert(db, 'payroll_cashflow_bridge', payroll_id='pay', pay_date='2025-02-01', currency='CAD', gross_minor=10000, net_minor=7000, raw_json='SECRET')
        insert(db, 'payroll_components', payroll_id='pay', code='RRSP', kind='employee_savings', amount_minor=1000, ytd_minor=2000)
        insert(db, 'payroll_component_links', payroll_id='pay', code='RRSP', tx_id='bank', status='matched')
        insert(db, 'equity_awards', award_id='grant', employer='Microsoft', grant_currency='USD', granted_shares='10', award_date='2025-01-01', status='unvested', included_in_assets=0, raw_json='SECRET')
        insert(db, 'equity_vesting_schedule', award_id='grant', vest_date='2025-01-02', theoretical_shares='2.5', actual_vested_shares=None, percent_basis_points=2500, status='scheduled')
    result = client.get(BASE + '/compensation?currency=USD').json()
    assert result['awards'][0]['symbol'] == 'MSFT'
    assert result['vesting'][0]['status'] == 'scheduled'
    assert result['vesting'][0]['actual_vested_shares'] is None
    assert result['payroll'] == []
    response = client.get(BASE + '/compensation?currency=CAD')
    data = response.json()
    assert data['payroll'][0]['net_minor'] == 7000
    assert data['components'][0]['ytd_minor'] == 2000
    assert data['links'][0]['tx_id'] == 'bank'
    assert data['awards'][0]['grant_currency'] == 'USD'
    assert 'raw_json' not in response.text


@pytest.fixture
def vesting_env(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        tx(db, 'latest', date='2026-06-30')
        for ident, grant_date in [('old', '2025-01-01'), ('current', '2026-01-01')]:
            insert(db, 'equity_awards', award_id=ident, employer='Microsoft', grant_currency='USD',
                   award_date=grant_date, granted_shares='10', status='unvested', included_in_assets=0)
        for ident, vest_date in [('old', '2025-12-31'), ('old', '2026-01-01'), ('old', '2026-12-31'),
                                 ('old', '2027-01-01'), ('current', '2027-06-01')]:
            insert(db, 'equity_vesting_schedule', award_id=ident, vest_date=vest_date,
                   theoretical_shares='2.5', percent_basis_points=2500, actual_vested_shares=None, status='scheduled')
        for ident, day in [('prior', '2025-06-01'), ('selected', '2026-06-01'), ('future', '2027-06-01')]:
            insert(db, 'payroll_cashflow_bridge', payroll_id=ident, pay_date=day, currency='CAD')
            insert(db, 'payroll_components', payroll_id=ident, code='NET', amount_minor=100)
            insert(db, 'payroll_component_links', payroll_id=ident, code='NET', tx_id=ident)
    return client


def test_compensation_default_horizon_ignores_grant_and_payroll_date_range(vesting_env):
    client = vesting_env
    result = client.get(BASE + '/compensation?start=2026-01-01&end=2026-12-31').json()
    assert {r['award_id'] for r in result['awards']} == {'old', 'current'}
    assert {(r['award_id'], r['vest_date']) for r in result['vesting']} == {
        ('old', '2025-12-31'), ('old', '2026-01-01'), ('old', '2026-12-31'),
        ('old', '2027-01-01'), ('current', '2027-06-01'),
    }
    assert all(r['actual_vested_shares'] is None and r['status'] == 'scheduled' for r in result['vesting'])
    assert all(r['included_in_assets'] == 0 for r in result['awards'])
    for key in ('payroll', 'components', 'links'):
        assert [r['payroll_id'] for r in result[key]] == ['selected']
    assert result['coverage']['vesting']['policy'] == 'horizon'
    assert result['coverage']['vesting']['date_basis'] == 'vest_date'
    assert result['coverage']['vesting']['start'] is None
    assert result['coverage']['vesting']['end'] is None
    assert result['coverage']['awards']['date_policy'] == 'all_grant_references'
    # No dates still means the entire stored schedule, not latest recorded year.
    assert len(client.get(BASE + '/compensation').json()['vesting']) == 5


@pytest.mark.parametrize('dates,expected', [
    ({'start': '2026-01-01', 'end': '2026-12-31'}, {('old', '2026-01-01'), ('old', '2026-12-31')}),
    ({'start': '2027-01-01'}, {('old', '2027-01-01'), ('current', '2027-06-01')}),
    ({'end': '2025-12-31'}, {('old', '2025-12-31')}),
    ({'start': '2028-01-01', 'end': '2028-12-31'}, set()),
])
def test_compensation_range_filters_vest_dates_not_grant_dates(vesting_env, dates, expected):
    response = vesting_env.get(BASE + '/compensation', params={'vesting_policy': 'range', **dates})
    assert response.status_code == 200, response.text
    result = response.json()
    assert {(r['award_id'], r['vest_date']) for r in result['vesting']} == expected
    # All eligible grant references remain explicit context, even if no event falls in range.
    assert {r['award_id'] for r in result['awards']} == {'old', 'current'}
    assert result['coverage']['awards']['date_policy'] == 'all_grant_references'
    coverage = result['coverage']['vesting']
    assert coverage['total'] == len(expected)
    assert coverage['complete'] is True
    assert coverage['policy'] == 'range'
    assert coverage['date_basis'] == 'vest_date'
    assert coverage['start'] == dates.get('start')
    assert coverage['end'] == dates.get('end')
    assert all(r['actual_vested_shares'] is None and r['status'] == 'scheduled' for r in result['vesting'])


@pytest.mark.parametrize('policy', ['horizon', 'range'])
@pytest.mark.parametrize('selector', [{'scope': 'protected'}, {'account': 'Checking'}])
def test_compensation_vesting_preserves_explicit_account_and_protected_exclusions(vesting_env, policy, selector):
    response = vesting_env.get(BASE + '/compensation', params={'vesting_policy': policy, **selector})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['awards'] == result['vesting'] == []
    assert result['coverage']['awards']['total'] == result['coverage']['vesting']['total'] == 0
    assert any('protected scope' in warning and 'account selection' in warning for warning in result['warnings'])


def test_obligations_allowlist_and_source_backed_positions(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        tx(db, 'range')
        db.execute('CREATE TABLE dashboard_positions(account,kind,as_of,currency,amount_minor,allocation,planning_protected,source_id,evidence)')
        for day, amount in [('2024-01-01', 9000), ('2025-01-01', 8000)]:
            insert(db, 'dashboard_positions', account='Loan', kind='mortgage', as_of=day, currency='CAD', amount_minor=amount, allocation='personal', planning_protected=0)
        insert(db, 'dashboard_positions', account='Checking', kind='cash', as_of='2025-02-01', currency='CAD', amount_minor=1000, allocation='personal', planning_protected=1)
        insert(db, 'facts', fact_id='mortgage', account='Loan', fact_name='mortgage_balance', value_json='{"amount_minor":8000,"currency":"CAD"}', source_id='synthetic', reviewer='financial_review', raw_json='SECRET')
        insert(db, 'facts', fact_id='password', fact_name='password', value_json='"SECRET"')
        insert(db, 'user_context', key='partner', value_json='"OUTDATED"')
        insert(db, 'user_context', key='partner_rent_schedule', value_json='{"amount_minor":300}', as_of='2025-01-01')
        insert(db, 'property_tax_cashflow', bill_id='bill', due_date='2025-06-01', currency='CAD', amount_minor=100, payment_status='scheduled')
        db.execute('CREATE TABLE dashboard_notes(note_id,topic,title,body,confidence,source_id,as_of)')
        insert(db, 'dashboard_notes', note_id='n', topic='mortgage', title='Coverage', body='Principal split unavailable', source_id='synthetic', as_of='2025-01-01')
    response = client.get(BASE + '/obligations')
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['facts'][0]['value']['amount_minor'] == 8000
    assert len(result['facts']) == 1
    assert result['context'][0]['key'] == 'partner_rent_schedule'
    assert result['tax_schedule'][0]['payment_status'] == 'scheduled'
    assert result['notes'][0]['source_id'] == 'synthetic'
    assert 'SECRET' not in response.text and 'OUTDATED' not in response.text
    overview = client.get(BASE + '/overview').json()
    assert len(overview['liabilities']) == 1
    assert overview['liabilities'][0]['amount_minor'] == 8000
    assert overview['bank_balances'][0]['planning_protected'] == 1


def test_transfer_non_cash_migrations_and_reversals_are_separate(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        tx(db, 'range')
        insert(db, 'account_policies', account='Broker', allocation='personal', planning_protected=0)
        insert(db, 'investment_asset_transfer_legs', event_id='migration', account='Broker', counterparty_account='New broker', transfer_date='2025-02-01', currency='CAD', security_key='SYNTH', value_minor=1000, direction='out')
        insert(db, 'transactions', tx_id='rev-debit', account='Checking', date='2025-02-01', amount_minor=-100, currency='CAD')
        insert(db, 'transactions', tx_id='rev-credit', account='Checking', date='2025-03-01', amount_minor=100, currency='CAD')
        insert(db, 'transaction_reversals', debit_tx='rev-debit', credit_tx='rev-credit', evidence='Same-account correction', as_of='2025-03-01')
    result = client.get(BASE + '/transfers?start=2025-02-01&end=2025-02-28').json()
    assert result['in_kind'][0]['event_id'] == 'migration'
    assert result['reversals'][0]['debit_tx'] == 'rev-debit'
    assert result['reversals'][0]['credit_date'] == '2025-03-01'
    assert result['network']['links'] == []
    assert result['coverage']['in_kind']['complete'] is True


def test_meta_includes_policy_accounts_without_transactions(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        insert(db, 'account_policies', account='Broker only', allocation='mother_joint', planning_protected=1)
    data = client.get(BASE + '/meta').json()
    assert data['accounts'][0]['account'] == 'Broker only'
    assert data['coverage']['accounts']['complete'] is True


def test_adjustments_are_not_operating_or_unresolved(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        tx(db, 'correction', flow='adjustment', amount_minor=200, expense_minor=-200)
    total = client.get(BASE + '/overview').json()['totals'][0]
    assert total['adjustment_minor'] == 200
    assert total['net_minor'] == 0 and total['unresolved_minor'] == 0


def test_query_deadline_and_symlink_fail_closed(env, monkeypatch):
    client, path, _, _ = env
    monkeypatch.setattr(finance_module(), 'QUERY_SECONDS', -1)
    with finance_module().open_finance(path.parent.parent) as db:
        with pytest.raises(sqlite3.OperationalError, match='interrupted'):
            db.execute('WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<1000000) SELECT sum(x) FROM n').fetchone()
    other = path.with_name('other.sqlite3')
    path.rename(other)
    path.symlink_to(other)
    assert client.get(BASE + '/meta').status_code == 503


def test_history_includes_end_day_timestamp_and_prefers_complete_period(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        insert(db, 'investment_balance_history', observation_id='timestamp', account='Broker', as_of='2031-04-15T14:00:00-04:00', currency='CAD', market_value_minor=100, allocation=None)
        for ident, start, end, status, complete in [('a-unknown','2031-02-28','2031-03-29','unknown',0),('b-complete','2031-02-28','2031-03-29','complete',1),('march','2031-03-29','2031-04-27','complete',1)]:
            insert(db, 'investment_period_metrics', period_id=ident, account='Broker', start_date=start, end_date=end, currency='CAD', status=status, flow_complete=complete)
    response = client.get(BASE + '/investments?start=2031-04-01&end=2031-04-15')
    assert response.json()['history'][0]['as_of'] == '2031-04-15T14:00:00-04:00'
    periods = client.get(BASE + '/investments').json()['periods']
    assert {p['period_id'] for p in periods} == {'b-complete', 'march'}


def test_upcoming_tax_schedule_separate_from_filtered_paid_history(env):
    client, path, _, _ = env
    with sqlite3.connect(path) as db:
        insert(db, 'property_tax_cashflow', bill_id='future', due_date='2025-12-01', currency='CAD', amount_minor=100, payment_status='scheduled')
    data = client.get(BASE + '/obligations?start=2025-01-01&end=2025-02-01').json()
    assert data['tax_schedule'] == []
    assert data['upcoming_tax_schedule'][0]['bill_id'] == 'future'


@pytest.mark.parametrize('params', ['limit=101', 'offset=-1', 'scope=other', 'currency=EUR', 'start=2025-02-30', 'start=1900-01-01&end=2025-01-01', 'start=2026-01-01&end=2025-01-01', 'q=' + 'x'*121, 'path=/tmp/other', 'sql=DROP', 'dataset=other', 'category_mode=other', 'flow=other', 'vesting_policy=other'])
def test_invalid_filters_rejected(env, params):
    assert env[0].get(BASE + '/transactions?' + params).status_code == 422


def test_read_connection_keeps_one_snapshot_during_concurrent_wal_commit(env):
    _, path, _, _ = env
    writer = sqlite3.connect(path)
    try:
        assert writer.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        tx(writer, 'original', expense_minor=100)
        writer.commit()
        with finance_module().open_finance(path.parent.parent) as reader:
            before = tuple(reader.execute('SELECT COUNT(*), SUM(expense_minor) FROM hierarchical_transactions').fetchone())
            writer.execute('UPDATE hierarchical_transactions SET expense_minor=200')
            tx(writer, 'new', expense_minor=300)
            writer.commit()
            after = tuple(reader.execute('SELECT COUNT(*), SUM(expense_minor) FROM hierarchical_transactions').fetchone())
            assert before == after == (1, 100)
            with pytest.raises(sqlite3.OperationalError, match='readonly'):
                reader.execute('DELETE FROM hierarchical_transactions')
        with finance_module().open_finance(path.parent.parent) as reader:
            assert tuple(reader.execute('SELECT COUNT(*), SUM(expense_minor) FROM hierarchical_transactions').fetchone()) == (2, 500)
    finally:
        writer.close()


def test_missing_database_schema_and_readonly(env):
    client, path, _, _ = env
    with finance_module().open_finance(path.parent.parent) as db:
        with pytest.raises(sqlite3.OperationalError):
            db.execute('DELETE FROM hierarchical_transactions')
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE hierarchical_transactions')
    response = client.get(BASE + '/overview')
    assert response.status_code == 503
    assert str(path) not in response.text
    path.unlink()
    assert client.get(BASE + '/meta').status_code == 503
    assert not path.exists()


@pytest.mark.parametrize('route', ['meta','overview','flow','transactions','transfers','investments','compensation','obligations'])
@pytest.mark.parametrize('change,code', [
    ('anonymous', 401), ('member', 403), ('profile', 403), ('provisioning', 403),
    ('disabled', 401), ('revoked', 401), ('recovery', 401), ('expired', 401),
])
def test_real_auth_denies_before_finance_open(env, monkeypatch, change, code, route):
    client, _, auth, now = env
    if change == 'anonymous':
        client.cookies.clear()
    elif change == 'expired':
        now[0] += 91 * 86400
    else:
        with auth.store.transaction() as db:
            if change == 'member':
                db.execute("UPDATE users SET role='member'")
            elif change == 'profile':
                db.execute("UPDATE users SET profile='other'")
            elif change in ('disabled', 'provisioning'):
                db.execute('UPDATE users SET status=?', (change,))
            elif change == 'revoked':
                db.execute('UPDATE sessions SET revoked=1')
            else:
                db.execute("UPDATE sessions SET kind='recovery'")
    def forbidden(*args, **kwargs):
        pytest.fail('Unauthorized finance DB open')
    monkeypatch.setattr(finance_module(), 'open_finance', forbidden)
    response = client.get(BASE + '/' + route)
    assert response.status_code == code
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['vary'] == 'Cookie'
