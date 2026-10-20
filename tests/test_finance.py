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


@pytest.mark.parametrize('params', ['limit=101', 'offset=-1', 'scope=other', 'currency=EUR', 'start=2025-02-30', 'start=1900-01-01&end=2025-01-01', 'start=2026-01-01&end=2025-01-01', 'q=' + 'x'*121, 'path=/tmp/other', 'sql=DROP', 'dataset=other'])
def test_invalid_filters_rejected(env, params):
    assert env[0].get(BASE + '/transactions?' + params).status_code == 422


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


@pytest.mark.parametrize('route', ['meta','overview','transactions','transfers','investments','compensation','obligations'])
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
