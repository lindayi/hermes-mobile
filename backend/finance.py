"""Owner-only, request-time read-only finance analytics. No private state at import."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
import sqlite3
import json
import math
import time
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

HEADERS = {'Cache-Control': 'no-store', 'Vary': 'Cookie'}
QUERY_SECONDS = 2.0
ROW_LIMIT = 1000
VALID_TX = "included=1 AND COALESCE(duplicate_of,'')='' AND COALESCE(record_status,'posted') NOT IN ('pending','duplicate','excluded','void')"


@contextmanager
def open_finance(profile_root):
    """Fixed server-selected path; never creates a DB, connection, or schema at startup."""
    path = Path(profile_root).resolve() / 'finance' / 'finance.sqlite3'
    public_roots = (Path('/var/www').resolve(), (Path(__file__).resolve().parents[1] / 'frontend').resolve(), (Path(__file__).resolve().parents[1] / 'public').resolve())
    if path.resolve() != path or any(path.is_relative_to(root) for root in public_roots):
        raise sqlite3.OperationalError('Finance database unavailable')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=0.25)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        deadline = time.monotonic() + QUERY_SECONDS
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        # Read-only mode alone leaves SELECTs in autocommit, allowing mixed snapshots.
        db.execute('BEGIN')
        yield db
    finally:
        db.close()


def metadata():
    return {'generated_at': datetime.now(timezone.utc).isoformat(), 'source': 'sqlite', 'warnings': []}


def rows(db, sql, params=()):
    return [dict(row) for row in db.execute(sql, params)]


def category_label(category):
    return ' / '.join(part.replace('_', ' ').replace('-', ' ').title() for part in (category or 'Uncategorized').split('.'))


def category_projection(db):
    # Allocation components (usually "whole") are not taxonomy labels.
    categories = f'(SELECT category_id,MIN(parent_id) parent_id FROM hierarchical_transactions WHERE {VALID_TX} GROUP BY category_id) t'
    if table_exists(db, 'category_nodes'):
        return categories + ' LEFT JOIN category_nodes n ON n.category_id=t.category_id', ['t.category_id', 'n.label', 'CASE WHEN n.category_id IS NOT NULL THEN n.parent_id ELSE t.parent_id END parent_id']
    return categories, ['t.category_id', 'NULL label', 't.parent_id']


def query_meta(db, filters=None):
    span = dict(db.execute(f'SELECT MIN(date) start, MAX(date) end FROM hierarchical_transactions WHERE {VALID_TX}').fetchone())
    accounts, account_coverage = select_bounded(db, f'(SELECT account,allocation,planning_protected FROM account_policies UNION SELECT account,allocation,current_planning_protected planning_protected FROM hierarchical_transactions WHERE {VALID_TX} AND account NOT IN (SELECT account FROM account_policies))', ['account','allocation','planning_protected'], order='ORDER BY account')
    table, fields = category_projection(db)
    categories, category_coverage = select_bounded(db, table, fields, order='ORDER BY t.category_id')
    for category in categories:
        category['label'] = category['label'] or category_label(category['category_id'])
    return {'meta': metadata(), 'range': span,
            'accounts': accounts,
            'currencies': [r[0] for r in db.execute(f'SELECT DISTINCT currency FROM hierarchical_transactions WHERE {VALID_TX} ORDER BY currency')],
            'categories': categories,
            'coverage': {'range_basis': 'recorded transaction dates', 'complete': False, 'accounts': account_coverage, 'categories': category_coverage}}


class Filters(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: date | None = None
    end: date | None = None
    currency: Literal['CAD', 'USD'] = 'CAD'
    scope: Literal['personal', 'all', 'protected'] = 'personal'
    account: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=120)
    category_mode: Literal['prefix', 'exact'] = 'prefix'
    direction: Literal['incoming', 'outgoing'] | None = None
    source_account: str | None = Field(default=None, max_length=200)
    target_account: str | None = Field(default=None, max_length=200)
    link_type: str | None = Field(default=None, max_length=120)
    transfer_status: Literal['matched', 'unmatched'] | None = None
    flow: Literal['income', 'expense', 'transfer', 'investment', 'financing', 'adjustment', 'unresolved'] | None = None
    vesting_policy: Literal['horizon', 'range'] = 'horizon'
    payroll_id: str | None = Field(default=None, max_length=200)
    dataset: Literal['bank', 'investment', 'all'] = 'all'
    q: str = Field(default='', max_length=120)
    record_id: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)

    @model_validator(mode='after')
    def bounded_dates(self):
        if self.start and self.end:
            if self.end < self.start or (self.end - self.start).days > 7305:
                raise ValueError('Invalid date range')
        return self


def escaped_like(value):
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def scope_sql(scope, allocation='allocation', protected='planning_protected'):
    family = f"(LOWER(COALESCE({allocation},'')) LIKE 'mother%' OR LOWER(COALESCE({allocation},'')) LIKE 'family%')"
    if scope == 'personal':
        return f'NOT {family}'
    if scope == 'protected':
        return f'({family} OR COALESCE({protected},0)=1)'
    return '1=1'


def date_range(db, f):
    latest = db.execute(f'SELECT MAX(date) FROM hierarchical_transactions WHERE {VALID_TX}').fetchone()[0]
    end = f.end.isoformat() if f.end else latest
    start = f.start.isoformat() if f.start else (end[:4] + '-01-01' if end else None)
    if start and end and (start > end or (date.fromisoformat(end) - date.fromisoformat(start)).days > 7305):
        raise HTTPException(422, 'Invalid date range')
    return start, end


def transaction_predicate(db, f, *, apply_dates=True):
    clauses = [VALID_TX, 'currency=?', scope_sql(f.scope, protected='current_planning_protected')]
    args = [f.currency]
    start, end = date_range(db, f) if apply_dates else (None, None)
    for value, sql in ((start, 'date>=?'), (end, 'date<=?'), (f.account, 'account=?')):
        if value is not None:
            clauses.append(sql)
            args.append(value)
    if f.record_id is not None:
        # Payroll links carry raw IDs; keep all aliases inside the regular predicate.
        clauses.append('(record_id=? OR tx_id=? OR event_id=?)')
        args.extend([f.record_id] * 3)
    if f.dataset != 'all':
        clauses.append('dataset=?')
        args.append(f.dataset)
    if f.flow is not None:
        clauses.append(f'({FLOW})=?')
        args.append(f.flow)
    if f.direction is not None:
        clauses.append('amount_minor>0' if f.direction == 'incoming' else 'amount_minor<0')
    if f.transfer_status is not None:
        clauses.append(('NOT ' if f.transfer_status == 'unmatched' else '') + MATCHED_LEG)
    if f.category is not None:
        if f.category_mode == 'exact':
            clauses.append('category_id=?')
            args.append(f.category)
        else:
            clauses.append("category_id LIKE ? ESCAPE '\\'")
            args.append(escaped_like(f.category) + '%')
    if f.q:
        clauses.append("(description LIKE ? ESCAPE '\\' OR merchant LIKE ? ESCAPE '\\' OR category_id LIKE ? ESCAPE '\\')")
        args.extend(['%' + escaped_like(f.q) + '%'] * 3)
    return ' AND '.join(clauses), args


TX_FIELDS = 'entry_id record_id dataset account date cash_month covered_month description component category_id parent_id depth flow amount_minor currency entry_type confidence rule_id evidence merchant expense_minor income_minor source_id page drive_url allocation current_planning_protected'.split()
BALANCE_FIELDS = 'observation_id account as_of currency market_value_minor book_cost_minor cash_minor book_scope source_kind source_id page fact_id evidence unrealized_gain_minor institution registration allocation planning_protected drive_url'.split()
POSITION_FIELDS = 'account kind as_of currency amount_minor allocation planning_protected source_id evidence'.split()


def safe_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value):
        return None
    try:
        u = urlsplit(value)
        if u.scheme != 'https' or u.username or u.password or u.port not in (None, 443):
            return None
        if u.hostname == 'drive.google.com' and u.path.startswith(('/file/d/', '/open')):
            return value
        if u.hostname == 'mail.google.com' and u.path.startswith('/mail/'):
            return value
    except ValueError:
        pass
    return None


def safe_rows(items):
    for item in items:
        if 'drive_url' in item:
            item['drive_url'] = safe_url(item['drive_url'])
    return items


def table_exists(db, name):
    return db.execute('SELECT 1 FROM sqlite_master WHERE name=? AND type IN (\'table\',\'view\')', (name,)).fetchone() is not None


def select_bounded(db, table, fields, where='1=1', args=(), order='', limit=ROW_LIMIT):
    """Only internal constants may select SQL identifiers. Report every omission."""
    total = db.execute(f'SELECT COUNT(*) FROM {table} WHERE {where}', args).fetchone()[0]
    items = safe_rows(rows(db, f'SELECT {",".join(fields)} FROM {table} WHERE {where} {order} LIMIT ?', (*args, limit)))
    return items, {'total': total, 'returned': len(items), 'omitted': total - len(items), 'complete': total == len(items)}


def snapshot_predicate(f):
    where = 'currency=? AND ' + scope_sql(f.scope)
    args = [f.currency]
    if f.account is not None:
        where += ' AND account=?'
        args.append(f.account)
    return where, args


def query_transactions(db, f):
    where, args = transaction_predicate(db, f)
    total = db.execute(f'SELECT COUNT(*) FROM hierarchical_transactions WHERE {where}', args).fetchone()[0]
    items = safe_rows(rows(db, f'SELECT {",".join(TX_FIELDS)} FROM hierarchical_transactions WHERE {where} ORDER BY date DESC,entry_id LIMIT ? OFFSET ?', (*args, f.limit, f.offset)))
    return {'meta': metadata(), 'items': items, 'total': total, 'limit': f.limit, 'offset': f.offset}


# The hierarchy owns signed household allocations. Never re-split or ABS recoveries.
FLOW = "CASE WHEN flow IN ('transfer','investment','financing','adjustment','unresolved') THEN flow WHEN category_id LIKE 'transfer%' THEN 'transfer' WHEN flow IN ('income','expense') THEN flow ELSE 'unresolved' END"
OPERATING = f"({FLOW}) IN ('income','expense')"
INCOME = f'COALESCE(SUM(CASE WHEN {OPERATING} THEN income_minor ELSE 0 END),0)'
EXPENSE = f'COALESCE(SUM(CASE WHEN {OPERATING} THEN expense_minor ELSE 0 END),0)'


def flow_id(*parts):
    # JSON tuples are stable, collision-free and do not depend on SQL row order.
    return json.dumps(parts, ensure_ascii=True, separators=(',', ':'))


FLOW_SELECTABLE = 'account IS NOT NULL AND length(account) BETWEEN 1 AND 200 AND category_id IS NOT NULL AND length(category_id)<=120'
PAIR_SELECTABLE = 'length(debit_account) BETWEEN 1 AND 200 AND length(credit_account) BETWEEN 1 AND 200 AND link_type IS NOT NULL AND length(link_type) BETWEEN 1 AND 120'


def query_flow(db, f):
    start, end = date_range(db, f)
    inherited = f.model_dump(mode='json', exclude_none=True, exclude={'limit', 'offset'})
    inherited.update(start=start, end=end)
    candidate_where, candidate_args = transaction_predicate(db, f)
    transaction_stats = rows(db, f'''SELECT COUNT(*) total,
        COALESCE(SUM(amount_minor=0),0) zero_amount,
        COALESCE(SUM(amount_minor IS NULL),0) unknown_amount,
        COALESCE(SUM(CASE WHEN amount_minor!=0 AND NOT ({FLOW_SELECTABLE})
            AND NOT ({MATCHED_LEG}) THEN 1 ELSE 0 END),0) unselectable
        FROM hierarchical_transactions WHERE {candidate_where}''', candidate_args)[0]
    where, args = transaction_predicate(db, f.model_copy(update={'transfer_status': 'unmatched'}))
    if f.transfer_status == 'matched':
        where += ' AND 0=1'
    grouped = f'''SELECT account,category_id,({FLOW}) kind,
        CASE WHEN amount_minor>0 THEN 'incoming' ELSE 'outgoing' END direction,
        SUM(ABS(amount_minor)) amount_minor,COUNT(*) count
        FROM hierarchical_transactions WHERE {where} AND amount_minor!=0 AND {FLOW_SELECTABLE}
        GROUP BY account,category_id,({FLOW}),amount_minor>0'''
    table = '(' + grouped + ') g'
    label = 'NULL label'
    if table_exists(db, 'category_nodes'):
        table += ' LEFT JOIN category_nodes n ON n.category_id=g.category_id'
        label = 'n.label'
    groups, coverage = select_bounded(db, table, ['g.*', label], args=args,
                                     order='ORDER BY account,g.category_id,kind,direction')
    nodes, edges = {}, []
    for group in groups:
        account, category, kind, direction = (group[k] for k in ('account', 'category_id', 'kind', 'direction'))
        account_id, category_id = flow_id('account', account), flow_id('category', kind, category)
        label = group['label'] or category_label(category)
        if kind == 'transfer':
            label = 'Unmatched transfer: ' + label
        nodes[account_id] = {'id': account_id, 'label': account, 'kind': 'account', 'account': account}
        nodes[category_id] = {'id': category_id, 'label': label, 'kind': kind}
        source, target = (category_id, account_id) if direction == 'incoming' else (account_id, category_id)
        edges.append({'id': flow_id('transaction', account, category, kind, direction),
                      'source': source, 'target': target, 'kind': kind,
                      'label': f'{label} — {direction}', 'amount_minor': group['amount_minor'],
                      'currency': f.currency, 'count': group['count'],
                      'selector': {'endpoint': 'transactions', 'filters': {
                          **inherited, 'account': account, 'category': category,
                          'category_mode': 'exact', 'flow': kind, 'direction': direction,
                          'transfer_status': 'unmatched'}}})
    pair_where, pair_args = pair_predicate(db, f)
    if f.flow not in (None, 'transfer'):
        pair_where += ' AND 0=1'
    pair_table = f'({PAIR_CTE} SELECT * FROM pairs WHERE {pair_where})'
    pair_stats = rows(db, f'''SELECT COUNT(*) total,
        COALESCE(SUM(debit_record_id IS NULL OR credit_record_id IS NULL),0) missing_counterpart,
        COALESCE(SUM(debit_currency!=credit_currency),0) cross_currency,
        COALESCE(SUM(debit_account=credit_account),0) same_account,
        COALESCE(SUM(CASE WHEN {MATCHED_PAIR} AND NOT ({PAIR_SELECTABLE}) THEN 1 ELSE 0 END),0) unselectable
        FROM {pair_table}''', pair_args)[0]
    pair_groups = f'''(SELECT debit_account,credit_account,link_type,debit_currency,
        SUM(-debit_amount_minor) amount_minor,COUNT(*) count FROM {pair_table}
        WHERE {MATCHED_PAIR} AND {PAIR_SELECTABLE}
        GROUP BY debit_account,credit_account,link_type,debit_currency)'''
    pairs, pair_coverage = select_bounded(db, pair_groups, ['*'], args=pair_args,
                                        order='ORDER BY debit_account,credit_account,link_type,debit_currency',
                                        limit=max(0, ROW_LIMIT - len(edges)))
    for pair in pairs:
        source, target = pair['debit_account'], pair['credit_account']
        for account in (source, target):
            ident = flow_id('account', account)
            nodes[ident] = {'id': ident, 'label': account, 'kind': 'account', 'account': account}
        edges.append({'id': flow_id('pair', source, target, pair['link_type'], pair['debit_currency']),
                      'source': flow_id('account', source), 'target': flow_id('account', target),
                      'kind': 'transfer', 'label': f"{source} → {target} — {category_label(pair['link_type'])}",
                      'amount_minor': pair['amount_minor'], 'currency': pair['debit_currency'], 'count': pair['count'],
                      'selector': {'endpoint': 'transfers', 'filters': {**inherited,
                          'source_account': source, 'target_account': target, 'link_type': pair['link_type'],
                          'transfer_status': 'matched'}}})
    coverage = {'total': coverage['total'] + pair_coverage['total'], 'returned': len(edges),
                'omitted': coverage['omitted'] + pair_coverage['omitted'],
                'complete': coverage['complete'] and pair_coverage['complete']}
    warnings = ['Recorded aggregate movements, not tracing income dollars or a balanced cash statement.',
                'Personal excludes family earmarks; own restricted accounts remain included. Protected scope overlaps personal.',
                'Paired transfers use debit date (credit date only if debit is missing), either-endpoint membership, and the debit magnitude once; signed counterpart amounts can differ.',
                'Missing counterparts, cross-currency pairs and same-account movements are not matched paths. Unmatched observations retain their recorded sign; in-kind movements are not cash.']
    if not coverage['complete']:
        warnings.append('Partial money map: aggregate edge output limit reached; omitted groups are not shown.')
    unrepresented = transaction_stats['unknown_amount'] or transaction_stats['unselectable'] or pair_stats['unselectable']
    complete = coverage['complete'] and not unrepresented
    if unrepresented:
        warnings.append('Partial money map: unknown amounts or unselectable account/category/link identifiers are omitted; see coverage.')
    return {'meta': metadata(), 'range': {'start': start, 'end': end}, 'currency': f.currency,
            'nodes': sorted(nodes.values(), key=lambda node: node['id']), 'edges': edges,
            'coverage': {'complete': complete, 'edges': coverage, 'pairs': pair_stats,
                         'transactions': transaction_stats, 'edge_limit': ROW_LIMIT}, 'warnings': warnings}


def query_overview(db, f):
    where, args = transaction_predicate(db, f)
    aggregate = f'{INCOME} income_minor,{EXPENSE} expense_minor,({INCOME}-{EXPENSE}) net_minor'
    others = ','.join(f"COALESCE(SUM(CASE WHEN ({FLOW})='{flow}' THEN amount_minor ELSE 0 END),0) {flow}_minor" for flow in ('investment','financing','adjustment','unresolved'))
    transfers = f"COALESCE(SUM(CASE WHEN ({FLOW})='transfer' AND amount_minor>0 THEN amount_minor ELSE 0 END),0) transfer_in_minor,COALESCE(SUM(CASE WHEN ({FLOW})='transfer' AND amount_minor<0 THEN -amount_minor ELSE 0 END),0) transfer_out_minor"
    totals = rows(db, f'SELECT ? currency,{aggregate},{transfers},{others} FROM hierarchical_transactions WHERE {where}', [f.currency, *args])
    monthly = rows(db, f'SELECT substr(date,1,7) month,currency,{aggregate} FROM hierarchical_transactions WHERE {where} GROUP BY substr(date,1,7),currency ORDER BY month', args)
    categories, category_coverage = select_bounded(db, f'(SELECT category_id,({FLOW}) flow,{INCOME} income_minor,{EXPENSE} expense_minor,SUM(amount_minor) amount_minor,COUNT(*) count FROM hierarchical_transactions WHERE {where} GROUP BY category_id,({FLOW}))', ['category_id','flow','income_minor','expense_minor','amount_minor','count'], args=args, order='ORDER BY category_id,flow')
    snap_where, snap_args = snapshot_predicate(f)
    balances, balance_coverage = select_bounded(db, 'investment_latest_balances', BALANCE_FIELDS, snap_where, snap_args, 'ORDER BY account,as_of DESC')
    warnings = ['Net is operating surplus (income minus signed net expense), not cash change or net worth.', 'Personal scope excludes mother/family earmarks; own restricted cash remains included. Protected scope overlaps personal.', 'Recorded snapshots are dated; reported investment totals already include cash. Coverage may be partial.']
    positions = []
    position_coverage = {'available': False}
    if table_exists(db, 'dashboard_positions'):
        positions, position_coverage = select_bounded(db, 'dashboard_positions', POSITION_FIELDS, snap_where + ' AND NOT EXISTS (SELECT 1 FROM dashboard_positions newer WHERE newer.account=dashboard_positions.account AND newer.kind=dashboard_positions.kind AND newer.currency=dashboard_positions.currency AND newer.as_of>dashboard_positions.as_of)', snap_args)
    else:
        warnings.append('Bank balances and liabilities unavailable: no standardized source positions.')
    return {'meta': metadata(), 'totals': totals, 'monthly': monthly, 'categories': categories, 'balances': balances, 'bank_balances': [p for p in positions if p['kind'] in ('cash','bank','bank_balance')], 'liabilities': [p for p in positions if p['kind'] in ('liability','mortgage','heloc','debt')], 'warnings': warnings, 'coverage': {'categories': category_coverage, 'balances': balance_coverage, 'positions': position_coverage}, 'range': dict(zip(('start','end'), date_range(db, f)))}


LEG_FIELDS = 'record_id account date amount_minor currency source_id page drive_url allocation planning_protected'.split()
PAIR_COLUMNS = ','.join(f'{alias}.{field} {side}_{field}' for side, alias in (('debit','d'),('credit','c')) for field in LEG_FIELDS)
PAIR_CTE = f'''WITH legs AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY link_id,CASE WHEN amount_minor<0 THEN 0 ELSE 1 END ORDER BY date,record_id) leg_rank
    FROM (SELECT DISTINCT link_id,{','.join(LEG_FIELDS)} FROM linked_transfer_legs)
), pairs AS (
    SELECT l.link_id,l.link_type,l.evidence,{PAIR_COLUMNS}
    FROM cash_links l
    LEFT JOIN legs d ON d.link_id=l.link_id AND d.amount_minor<0 AND d.leg_rank=1
    LEFT JOIN legs c ON c.link_id=l.link_id AND c.amount_minor>=0 AND c.leg_rank=1
)'''


# Only evidenced, signed, same-currency, distinct-account pairs enter the map.
MATCHED_PAIR = '''debit_record_id IS NOT NULL AND credit_record_id IS NOT NULL
    AND debit_account!=credit_account AND debit_currency=credit_currency
    AND debit_amount_minor<0 AND credit_amount_minor>0'''
MATCHED_LEG = f'''COALESCE(record_id IN ({PAIR_CTE}
    SELECT debit_record_id FROM pairs WHERE {MATCHED_PAIR}
    UNION SELECT credit_record_id FROM pairs WHERE {MATCHED_PAIR}),0)'''


def pair_predicate(db, f):
    clauses = ['COALESCE(debit_currency,credit_currency)=?']
    args = [f.currency]
    if f.scope != 'all':
        clauses.append('((debit_record_id IS NOT NULL AND ' + scope_sql(f.scope, 'debit_allocation', 'debit_planning_protected') + ') OR (credit_record_id IS NOT NULL AND ' + scope_sql(f.scope, 'credit_allocation', 'credit_planning_protected') + '))')
    start, end = date_range(db, f)
    for value, sql in ((start, 'COALESCE(debit_date,credit_date)>=?'), (end, 'COALESCE(debit_date,credit_date)<=?')):
        if value:
            clauses.append(sql)
            args.append(value)
    for value, column in ((f.account, 'account'), (f.record_id, 'record_id')):
        if value is not None:
            clauses.append(f'(debit_{column}=? OR credit_{column}=?)')
            args.extend([value, value])
    if f.q:
        clauses.append("(evidence LIKE ? ESCAPE '\\' OR debit_account LIKE ? ESCAPE '\\' OR credit_account LIKE ? ESCAPE '\\' OR link_type LIKE ? ESCAPE '\\')")
        args.extend(['%' + escaped_like(f.q) + '%'] * 4)
    for value, column in ((f.source_account, 'debit_account'), (f.target_account, 'credit_account'), (f.link_type, 'link_type')):
        if value is not None:
            clauses.append(f'{column}=?')
            args.append(value)
    if f.transfer_status is not None:
        clauses.append(('NOT ' if f.transfer_status == 'unmatched' else '') + f'COALESCE(({MATCHED_PAIR}),0)')
    if f.category is not None or f.dataset != 'all':
        # Source date/scope already select the pair; membership can match either endpoint.
        tx_where, tx_args = transaction_predicate(db, f.model_copy(update={'q': '', 'account': None, 'record_id': None, 'scope': 'all', 'direction': None, 'transfer_status': None}), apply_dates=False)
        clauses.append(f'(debit_record_id IN (SELECT record_id FROM hierarchical_transactions WHERE {tx_where}) OR credit_record_id IN (SELECT record_id FROM hierarchical_transactions WHERE {tx_where}))')
        args.extend(tx_args * 2)
    return ' AND '.join(clauses), args


def transfer_extras(db, f):
    asset_table = '''(SELECT a.event_id,a.account,a.counterparty_account,a.transfer_date,a.currency,a.security_key,a.value_minor,a.source_id,a.page,a.direction,
        p.allocation,p.planning_protected FROM investment_asset_transfer_legs a LEFT JOIN account_policies p ON p.account=a.account)'''
    reversal_table = '''(SELECT r.debit_tx,r.credit_tx,r.evidence,r.as_of,d.account,d.date debit_date,c.date credit_date,
        d.amount_minor debit_amount_minor,c.amount_minor credit_amount_minor,d.currency,d.source_id,d.page,
        p.allocation,p.planning_protected FROM transaction_reversals r
        LEFT JOIN transactions d ON d.tx_id=r.debit_tx LEFT JOIN transactions c ON c.tx_id=r.credit_tx
        LEFT JOIN account_policies p ON p.account=d.account)'''
    start, end = date_range(db, f)
    result, coverage = {}, {}
    for key, table, date_column, fields in (
        ('in_kind', asset_table, 'transfer_date', 'event_id account counterparty_account transfer_date currency security_key value_minor source_id page direction allocation planning_protected'),
        ('reversals', reversal_table, 'debit_date', 'debit_tx credit_tx evidence as_of account debit_date credit_date debit_amount_minor credit_amount_minor currency source_id page allocation planning_protected'),
    ):
        where, args = snapshot_predicate(f)
        for value, operator in ((start, '>='), (end, '<=')):
            if value:
                where += f' AND {date_column}{operator}?'
                args.append(value)
        if f.dataset == ('bank' if key == 'in_kind' else 'investment'):
            where += ' AND 0=1'
        if f.category or f.record_id:
            tw, ta = transaction_predicate(db, f)
            identifier = 'event_id' if key == 'in_kind' else 'debit_tx'
            source_identifier = 'event_id' if key == 'in_kind' else 'tx_id'
            where += f' AND {identifier} IN (SELECT {source_identifier} FROM hierarchical_transactions WHERE {tw})'
            args.extend(ta)
        if f.q:
            where += " AND account LIKE ? ESCAPE '\\'"
            args.append('%' + escaped_like(f.q) + '%')
        result[key], coverage[key] = select_bounded(db, table, fields.split(), where, args, f'ORDER BY {date_column} DESC')
    return result, coverage


def query_transfers(db, f):
    where, args = pair_predicate(db, f)
    cte = PAIR_CTE + f', filtered AS (SELECT * FROM pairs WHERE {where}) '
    total = db.execute(cte + 'SELECT COUNT(*) FROM filtered', args).fetchone()[0]
    raw = rows(db, cte + 'SELECT * FROM filtered ORDER BY COALESCE(debit_date,credit_date) DESC,link_id LIMIT ? OFFSET ?', [*args, f.limit, f.offset])
    items = []
    for pair in raw:
        item = {key: pair[key] for key in ('link_id','link_type','evidence')}
        for side in ('debit','credit'):
            leg = {field: pair[side + '_' + field] for field in LEG_FIELDS}
            item[side] = safe_rows([leg])[0] if leg['record_id'] is not None else None
        item['protected_boundary'] = bool(pair['debit_planning_protected']) != bool(pair['credit_planning_protected']) or (pair['debit_allocation'] != pair['credit_allocation'] and bool(item['debit'] and item['credit']))
        item['missing_counterpart'] = item['debit'] is None or item['credit'] is None
        items.append(item)
    network_sql = '''SELECT debit_account source,credit_account target,debit_currency currency,
        SUM(-debit_amount_minor) amount_minor,COUNT(*) count FROM filtered
        WHERE debit_record_id IS NOT NULL AND credit_record_id IS NOT NULL
        AND debit_account!=credit_account AND debit_currency=credit_currency
        GROUP BY debit_account,credit_account,debit_currency'''
    n = db.execute(cte + 'SELECT COUNT(*) FROM (' + network_sql + ')', args).fetchone()[0]
    links = rows(db, cte + network_sql + ' ORDER BY source,target LIMIT ?', [*args, ROW_LIMIT])
    nodes = sorted({link[key] for link in links for key in ('source','target')})
    extras, coverage = transfer_extras(db, f)
    return {'meta': metadata(), 'items': items, 'total': total, 'limit': f.limit, 'offset': f.offset,
            'network': {'nodes': [{'account': node} for node in nodes], 'links': links,
                        'coverage': {'total': n, 'returned': len(links), 'omitted': n-len(links), 'complete': n == len(links)}},
            **extras, 'coverage': coverage,
            'warnings': ['Gross paired movement is not income or expense. Date filtering uses debit date; missing debit uses credit date.', 'Missing counterparts, same-account reversals and cross-currency pairs are excluded from the cash network.']}


HOLDING_FIELDS = 'observation_id account as_of currency security_key name quantity quantity_basis market_value_minor book_cost_minor source_id page fact_id evidence unrealized_gain_minor unrealized_gain_pct cost_basis_type allocation planning_protected drive_url'.split()
PERIOD_FIELDS = 'period_id account start_date end_date currency opening_minor closing_minor net_flow_minor reported_gain_minor fees_minor gain_basis flow_complete return_pct return_method status source_id page evidence net_gain_minor reconciliation_difference_minor allocation planning_protected drive_url'.split()
REALIZED_FIELDS = 'group_id account security_key trade_date settlement_date currency proceeds_minor cost_minor cost_basis_type include_in_realized_gain status source_id page evidence realized_gain_minor cost_basis_warning tax_acb_minor tax_gain_minor allocation planning_protected drive_url'.split()
CASHFLOW_FIELDS = 'event_id account security_key flow_role account_flow_minor link_id counterparty_account counterparty_investment_account evidence date cash_amount_minor currency classification source_id page allocation planning_protected all_investments_flow_minor personal_investments_flow_minor fee_minor income_minor drive_url'.split()
PAYROLL_FIELDS = 'payroll_id source_id pay_date period_end currency frequency gross_minor net_minor evidence calculated_net_minor net_difference_minor drive_url'.split()
AWARD_FIELDS = 'award_id source_id employer grant_currency grant_value_minor award_month granted_shares current_value_minor first_vest_month_estimate status included_in_assets included_in_cashflow award_date'.split()
TAX_FIELDS = 'bill_id due_date amount_minor tx_id property_key year currency payment_status bank_date account source_id drive_url'.split()


def dated_predicate(where, args, f, column):
    args = list(args)
    for value, operator in ((f.start, '>='), (f.end, '<=')):
        if value:
            where += f' AND substr({column},1,10){operator}?'
            args.append(value.isoformat())
    return where, args


def query_investments(db, f):
    where, args = snapshot_predicate(f)
    result = {'meta': metadata(), 'coverage': {}, 'warnings': [
        'Historical recorded valuations, not current prices. Account totals include cash; do not add cash again.',
        'Broker book cost is not tax ACB. Null basis and gains remain unknown.',
        'Periods contain only non-overlapping monthly windows; longer/overlapping windows are omitted, never summed. Residual dollar gains are not TWR or XIRR.',
        'Latest holdings use the source coherent view (which suppresses exited accounts); quantity and account valuation timestamps may differ. Do not sum holdings and reported balances.']}
    specs = (
        ('balances', 'investment_latest_balances', BALANCE_FIELDS, None),
        ('holdings', 'investment_latest_holdings', HOLDING_FIELDS, None),
        ('history', 'investment_balance_history', BALANCE_FIELDS, 'as_of'),
        ('periods', 'investment_period_metrics', PERIOD_FIELDS, 'end_date'),
        ('realized', 'investment_realized_metrics', REALIZED_FIELDS, 'trade_date'),
        ('cashflows', 'investment_cashflow_metrics', CASHFLOW_FIELDS, 'date'),
    )
    for key, table, fields, date_column in specs:
        pred, params = dated_predicate(where, args, f, date_column) if date_column else (where, args)
        if key == 'history':
            pred += ' AND COALESCE(source_rank,1)=1'
        if key == 'periods':
            pred += ''' AND julianday(end_date)-julianday(start_date) BETWEEN 1 AND 35
                AND NOT EXISTS (SELECT 1 FROM investment_period_metrics other
                WHERE other.account=investment_period_metrics.account AND other.currency=investment_period_metrics.currency
                AND other.start_date<investment_period_metrics.end_date AND other.end_date>investment_period_metrics.start_date
                AND julianday(other.end_date)-julianday(other.start_date) BETWEEN 1 AND 35
                AND (CASE WHEN other.status IN ('complete','reconciled','monthly','monthly_complete') THEN 0 ELSE 1 END,
                     CASE WHEN other.flow_complete=1 THEN 0 ELSE 1 END,other.end_date,other.period_id)
                  < (CASE WHEN investment_period_metrics.status IN ('complete','reconciled','monthly','monthly_complete') THEN 0 ELSE 1 END,
                     CASE WHEN investment_period_metrics.flow_complete=1 THEN 0 ELSE 1 END,investment_period_metrics.end_date,investment_period_metrics.period_id))'''
        result[key], result['coverage'][key] = select_bounded(db, table, fields, pred, params, f'ORDER BY {date_column or "as_of"} DESC,{fields[0]}')
        if key in ('holdings', 'periods', 'history'):
            candidate_where, candidate_args = dated_predicate(where, args, f, date_column) if date_column else (where, args)
            eligible = db.execute(f'SELECT COUNT(*) FROM {table} WHERE {candidate_where}', candidate_args).fetchone()[0]
            result['coverage'][key]['excluded_by_coherence_or_period_policy'] = eligible - result['coverage'][key]['total']
    return result


def query_compensation(db, f):
    result = {'meta': metadata(), 'coverage': {}, 'warnings': [
        'Payroll net already deducts employee savings; current and YTD components are separate.',
        'Scheduled vest dates are not evidence of vesting. Theoretical fractional shares are not actual vested or net delivered shares.',
        'Awards retain original grant currency independently of payroll currency; amounts across currencies must not be added. Awards are separate from owned assets/cash. MSFT is mapped only for explicitly Microsoft employer; no tax or net cash assumptions.']}
    where, args = dated_predicate('currency=?', [f.currency], f, 'pay_date')
    if f.scope == 'protected':
        where += ' AND 0=1'
    if f.account:
        where += ' AND payroll_id IN (SELECT l.payroll_id FROM payroll_component_links l JOIN hierarchical_transactions t ON t.tx_id=l.tx_id OR t.event_id=l.event_id WHERE t.account=?)'
        args.append(f.account)
    result['payroll'], result['coverage']['payroll'] = select_bounded(db, 'payroll_cashflow_bridge', PAYROLL_FIELDS, where, args, 'ORDER BY pay_date DESC,payroll_id')
    payroll_ids = {row['payroll_id'] for row in result['payroll']}
    if f.payroll_id is not None:
        selected_payroll_id = f.payroll_id if f.payroll_id in payroll_ids else None
    else:
        selected_payroll_id = result['payroll'][0]['payroll_id'] if result['payroll'] else None
    result['coverage']['selected_payroll_id'] = selected_payroll_id
    result['warnings'].append('Components and links cover only the selected payroll sample (the requested payroll ID when supplied; otherwise newest pay date, then payroll ID); the payroll list retains bounded reference history.')
    for key, table, fields in (
        ('components','payroll_components','payroll_id code kind amount_minor ytd_minor cash_effect_minor'),
        ('links','payroll_component_links','payroll_id code tx_id event_id status evidence'),
    ):
        result[key], result['coverage'][key] = select_bounded(db, table, fields.split(), 'payroll_id=?', [selected_payroll_id], 'ORDER BY code')
    if not result['coverage']['components']['complete']:
        result['components'] = []
        result['coverage']['components'].update(returned=0, omitted=result['coverage']['components']['total'])
        result['warnings'].append('Payroll breakdown unavailable: the selected sample exceeds the component limit and is withheld in full, never shown as a partial waterfall.')
    # Resolve only returned IDs in one hierarchy read, independent of payroll dates.
    endpoint_fields = ('record_id', 'date', 'account', 'currency', 'dataset')
    endpoint_where, endpoint_args = [], []
    for dataset, identifier in (('bank', 'tx_id'), ('investment', 'event_id')):
        ids = list(dict.fromkeys(link[identifier] for link in result['links'] if link[identifier] is not None))
        if ids:
            endpoint_where.append(f"(dataset='{dataset}' AND {identifier} IN ({','.join('?' for _ in ids)}))")
            endpoint_args.extend(ids)
    endpoints = {}
    if endpoint_args:
        # Allocation rows share an endpoint; keep one per original, dataset-qualified ID.
        linked_id = "CASE WHEN dataset='bank' THEN tx_id ELSE event_id END"
        sql = f'''WITH ranked AS (
            SELECT {','.join(endpoint_fields)},{linked_id} linked_id,
                ROW_NUMBER() OVER (PARTITION BY dataset,{linked_id} ORDER BY record_id,entry_id) endpoint_rank
            FROM hierarchical_transactions WHERE {VALID_TX} AND ({' OR '.join(endpoint_where)})
        ) SELECT {','.join(endpoint_fields)},linked_id FROM ranked WHERE endpoint_rank=1 LIMIT ?'''
        for endpoint in db.execute(sql, (*endpoint_args, len(endpoint_args))):
            endpoints[(endpoint['dataset'], endpoint['linked_id'])] = {field: endpoint[field] for field in endpoint_fields}
    for link in result['links']:
        link['linked_record'] = endpoints.get(('bank', link['tx_id'])) or endpoints.get(('investment', link['event_id']))
    # Grants are context, not vest-date observations: never filter by award_date.
    pred, params = '1=1', []
    if f.scope == 'protected' or f.account:
        pred += ' AND 0=1'
    result['awards'], result['coverage']['awards'] = select_bounded(db, 'equity_awards', AWARD_FIELDS, pred, params, 'ORDER BY award_date DESC,award_id')
    for award in result['awards']:
        award['symbol'] = 'MSFT' if (award['employer'] or '').strip().lower() in ('microsoft', 'microsoft corporation', 'microsoft canada') else None
    vest_pred, vest_params = f'award_id IN (SELECT award_id FROM equity_awards WHERE {pred})', params
    if f.vesting_policy == 'range':
        vest_pred, vest_params = dated_predicate(vest_pred, vest_params, f, 'vest_date')
    result['vesting'], result['coverage']['vesting'] = select_bounded(db, 'equity_vesting_schedule', 'award_id vest_date percent_basis_points theoretical_shares actual_vested_shares status source_id'.split(), vest_pred, vest_params, 'ORDER BY vest_date,award_id')
    result['coverage']['awards']['date_policy'] = 'all_grant_references'
    result['coverage']['vesting'].update(
        policy=f.vesting_policy, date_basis='vest_date',
        start=f.start.isoformat() if f.vesting_policy == 'range' and f.start else None,
        end=f.end.isoformat() if f.vesting_policy == 'range' and f.end else None)
    result['warnings'].extend([
        'Award references are independent of payroll dates and currency. Awards and vesting are unavailable for protected scope or an account selection because award account/protection attribution is absent.',
        'Vesting range filters stored vest dates only; all eligible grant references remain context, regardless of grant date.' if f.vesting_policy == 'range' else
        'Vesting horizon includes all stored eligible schedule dates, independently of the selected payroll date range.'])
    return result


FINANCIAL_FACT_NAMES = ('mortgage_balance','mortgage_payment','mortgage_rate','mortgage_renewal','mortgage_terms','mortgage','heloc_balance','heloc_limit','heloc_rate','heloc','property_tax','property_tax_schedule','hbp_balance','hbp_repayment','hbp','tfsa_room','tfsa_notice','rrsp_room','rrsp_notice','notice_of_assessment','registered_account_notice',
    'hbp_latest_balance_minimum','rrsp_remaining_before_hbp','rrsp_2026_annual_starting_room','rrsp_known_later_contributions','tfsa_indicative_jan_feb_tax','rrsp_remaining_contribution_capacity_current','tfsa_2026_excess_tax_unassessed','tfsa_available_room_current','tfsa_excess_current')
FINANCIAL_FACT_FAMILY = ('rrsp_remaining_with_', '_hbp_from_existing_contributions')
CONTEXT_KEYS = ('partner_rent_schedule','partner_shared_settlement','mortgage','heloc','hbp','tfsa','rrsp','td_mortgage_payment','td_mortgage_fees','tfsa_correction')
NOTE_TOPICS = ('mortgage','heloc','property_tax','tax','registered_accounts','hbp','tfsa','rrsp','coverage','obligations')


def parsed_value(raw):
    # Approved financial facts may be structured JSON; never propagate document/raw blobs.
    if not isinstance(raw, str) or len(raw) > 8192:
        return None
    try:
        value = json.loads(raw, parse_constant=lambda _: None)
    except (ValueError, RecursionError):
        return None
    def clean(v, depth=0):
        if isinstance(v, float) and not math.isfinite(v):
            return None
        if depth > 6:
            return None
        if isinstance(v, dict):
            return {k: clean(x, depth+1) for k, x in list(v.items())[:100]
                    if k.lower() not in ('raw_json','text','document_text','source_pdf','password','token','credential','secret')}
        if isinstance(v, list):
            return [clean(x, depth+1) for x in v[:100]]
        return v
    return clean(value)


def query_obligations(db, f):
    result = {'meta': metadata(), 'coverage': {}, 'warnings': [
        'Scheduled payments are not paid unless bank-matched. No inferred future payments or principal/interest split.',
        'Financial facts/context may be dated and incomplete; absence means unavailable, not zero.',
        'Unscoped contextual facts retain their source currency and are not aggregated.']}
    pred, args = dated_predicate('currency=?', [f.currency], f, 'due_date')
    if f.account:
        pred += ' AND account=?'
        args.append(f.account)
    pred += ' AND ' + scope_sql(f.scope, "(SELECT allocation FROM account_policies p WHERE p.account=property_tax_cashflow.account LIMIT 1)", "(SELECT planning_protected FROM account_policies p WHERE p.account=property_tax_cashflow.account LIMIT 1)")
    result['tax_schedule'], result['coverage']['tax_schedule'] = select_bounded(db, 'property_tax_cashflow', TAX_FIELDS, pred, args, 'ORDER BY due_date,bill_id')
    result['upcoming_tax_schedule'] = []
    result['coverage']['upcoming_tax_schedule'] = {'total': 0, 'returned': 0, 'omitted': 0, 'complete': True}
    if f.end:
        upcoming = f.model_copy(update={'start': None, 'end': None})
        upcoming_pred, upcoming_args = dated_predicate('currency=?', [f.currency], upcoming, 'due_date')
        upcoming_pred += ' AND due_date>? AND due_date<=?'
        upcoming_args.extend([f.end.isoformat(), f'{f.end.year}-12-31'])
        if f.account:
            upcoming_pred += ' AND account=?'
            upcoming_args.append(f.account)
        upcoming_pred += ' AND ' + scope_sql(f.scope, '(SELECT allocation FROM account_policies p WHERE p.account=property_tax_cashflow.account LIMIT 1)', '(SELECT planning_protected FROM account_policies p WHERE p.account=property_tax_cashflow.account LIMIT 1)')
        result['upcoming_tax_schedule'], result['coverage']['upcoming_tax_schedule'] = select_bounded(db, 'property_tax_cashflow', TAX_FIELDS, upcoming_pred, upcoming_args, 'ORDER BY due_date,bill_id')
        result['warnings'].append('Upcoming tax schedule is stored evidence after the selected end date through that calendar year, not inferred payments.')
    # Fixed family with a bounded, digits-only designation; never allow arbitrary facts.
    prefix, suffix = FINANCIAL_FACT_FAMILY
    designation = f'substr(fact_name,{len(prefix) + 1},length(fact_name)-{len(prefix) + len(suffix)})'
    pred = '(fact_name IN (' + ','.join('?' for _ in FINANCIAL_FACT_NAMES) + f") OR (fact_name GLOB ? AND length({designation}) BETWEEN 1 AND 9 AND {designation} NOT GLOB '*[^0-9]*'))"
    args = [*FINANCIAL_FACT_NAMES, prefix + '*' + suffix]
    if f.account:
        pred += ' AND account=?'
        args.append(f.account)
    pred += ' AND ' + scope_sql(f.scope, '(SELECT allocation FROM account_policies p WHERE p.account=facts.account LIMIT 1)', '(SELECT planning_protected FROM account_policies p WHERE p.account=facts.account LIMIT 1)')
    result['facts'], result['coverage']['facts'] = select_bounded(db, 'facts', ['fact_id','account','fact_name','value_json','source_id'], pred, args, 'ORDER BY fact_id', limit=200)
    for fact in result['facts']:
        fact['value'] = parsed_value(fact.pop('value_json'))
        fact['evidence'] = None  # facts schema has no evidence column; do not fabricate it.
    pred = 'key IN (' + ','.join('?' for _ in CONTEXT_KEYS) + ')'
    if f.scope == 'protected' or f.account:
        pred += ' AND 0=1'
    result['context'], result['coverage']['context'] = select_bounded(db, 'user_context', ['key','value_json','as_of','source'], pred, CONTEXT_KEYS, 'ORDER BY key', limit=100)
    for context in result['context']:
        context['value'] = parsed_value(context.pop('value_json'))
    result['notes'] = []
    result['coverage']['notes'] = {'available': False}
    if table_exists(db, 'dashboard_notes'):
        result['notes'], result['coverage']['notes'] = select_bounded(db, 'dashboard_notes', 'note_id topic title body confidence source_id as_of'.split(), 'topic IN (' + ','.join('?' for _ in NOTE_TOPICS) + ')', NOTE_TOPICS, 'ORDER BY as_of DESC,note_id', limit=100)
    return result


def build_finance_router(auth_service, profile_root):
    """Parent mounts this already-prefixed router; profile_root is the default home."""
    router = APIRouter(prefix='/hermes/app-api/finance')

    def handle(request, query):
        try:
            user = auth_service.require_user(request)
            auth_service.require_owner(user)
            if user.get('profile') != 'default' or user.get('status') != 'ready':
                raise HTTPException(403, 'Ready default owner required')
            filters = Filters.model_validate(dict(request.query_params))
            if profile_root is None:
                raise HTTPException(503, 'Finance data unavailable')
            with open_finance(profile_root) as db:
                result = query(db, filters)
            return JSONResponse(result, headers=HEADERS)
        except HTTPException as exc:
            return JSONResponse({'detail': exc.detail}, status_code=exc.status_code, headers=HEADERS)
        except ValidationError:
            return JSONResponse({'detail': 'Invalid finance filters'}, status_code=422, headers=HEADERS)
        except (sqlite3.Error, OSError):
            return JSONResponse({'detail': 'Finance data unavailable'}, status_code=503, headers=HEADERS)

    @router.get('/meta')
    def get_meta(request: Request):
        return handle(request, query_meta)

    @router.get('/overview')
    def get_overview(request: Request):
        return handle(request, query_overview)

    @router.get('/flow')
    def get_flow(request: Request):
        return handle(request, query_flow)

    @router.get('/transactions')
    def get_transactions(request: Request):
        return handle(request, query_transactions)

    @router.get('/transfers')
    def get_transfers(request: Request):
        return handle(request, query_transfers)

    @router.get('/investments')
    def get_investments(request: Request):
        return handle(request, query_investments)

    @router.get('/compensation')
    def get_compensation(request: Request):
        return handle(request, query_compensation)

    @router.get('/obligations')
    def get_obligations(request: Request):
        return handle(request, query_obligations)

    return router
