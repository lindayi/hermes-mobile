"""Recovery summaries use disposable journals; list/receipt checks never retry DELETE."""
from contextlib import closing

import pytest

from backend.session_deletion import SessionDeletion
from test_auth import BASE
from test_session_deletion import deletion_app


def test_prepared_root_is_discoverable_while_hidden_from_items(deletion_app):
    app, client, user, calls, _ = deletion_app
    app.state.journal.claim_deletion(user['id'], user['profile'], 'cli-1')

    response = client.get(BASE + '/sessions')

    assert response.status_code == 200
    page = response.json()
    assert page.get('pending_deletions') == [{'id': 'cli-1', 'status': 'unconfirmed'}]
    assert 'cli-1' not in [item['id'] for item in page['items']]
    assert all(method == 'GET' and path == '/v1/capabilities' for method, path in calls)


@pytest.mark.parametrize('query', ['', '?q=does-not-match', '?kind=cron', '?limit=1&offset=100'])
@pytest.mark.parametrize('version', [1, None])
def test_unknown_survives_filters_pages_and_capability_loss(deletion_app, query, version):
    app, client, user, calls, behavior = deletion_app
    claim = app.state.journal.claim_deletion(user['id'], user['profile'], 'cli-1')
    SessionDeletion(app.state.journal)._outcome(user, claim['operation_id'], 'native_unknown')
    behavior['version'] = version
    with closing(app.state.journal.connect()) as c:
        before = list(c.iterdump())

    page = client.get(BASE + '/sessions' + query).json()

    assert page.get('pending_deletions') == [{'id': 'cli-1', 'status': 'unconfirmed'}]
    assert all(method == 'GET' and path == '/v1/capabilities' for method, path in calls)
    with closing(app.state.journal.connect()) as c:
        assert list(c.iterdump()) == before


def test_pending_summaries_are_bounded_distinct_root_ids_in_stable_order(deletion_app):
    app, client, user, calls, _ = deletion_app
    # Seed historical/crash records directly: admission normally prevents a second
    # simultaneous unresolved operation. Exercise the defensive response bound.
    roots = [f'root-{i:03}' for i in range(105)]
    with closing(app.state.journal.connect()) as c, c:
        c.executemany('INSERT INTO session_deletion_operations VALUES(?,?,?,?,?,?)',
            [(f'op-{i}', user['id'], user['profile'], sid, 'prepared', i)
             for i, sid in enumerate(reversed(roots))])
        c.execute('INSERT INTO session_deletion_operations VALUES(?,?,?,?,?,?)',
            ('duplicate-root', user['id'], user['profile'], roots[0], 'native_unknown', 200))

    page = client.get(BASE + '/sessions?limit=1&offset=100').json()

    assert page.get('pending_deletions') == [
        {'id': sid, 'status': 'unconfirmed'} for sid in roots[:100]]


def test_non_owner_has_no_pending_summary_even_for_matching_journal_rows(deletion_app):
    app, _, user, _, _ = deletion_app
    app.state.journal.claim_deletion(user['id'], user['profile'], 'cli-1')

    assert SessionDeletion(app.state.journal).pending(dict(user, role='member')) == []


def test_only_current_owner_profile_pending_roots_are_exposed(deletion_app):
    app, client, user, _, _ = deletion_app
    journal = app.state.journal
    baseline = client.get(BASE + '/sessions').json()
    assert 'pending_deletions' not in baseline
    run, _ = journal.submit(user['id'], user['profile'], 'related-alias',
        'secret prompt', 'secret-key', history_anchor=lambda: dict(
            session_id='history-alias', canonical_session_id='cli-1', message_id=0))
    journal.finish(user['id'], run['id'], 'completed', output='secret output')
    journal.claim_deletion(user['id'], user['profile'], 'cli-1')
    with closing(journal.connect()) as c, c:
        assert c.execute('SELECT count(*) FROM session_deletions').fetchone()[0] == 3
        c.executemany('INSERT INTO session_deletion_operations VALUES(?,?,?,?,?,?)', [
            ('foreign-owner-op', 'foreign-owner', user['profile'], 'foreign-owner-id', 'prepared', 0),
            ('foreign-profile-op', user['id'], 'other-profile', 'foreign-profile-id', 'native_unknown', 0),
            ('deleted-op', user['id'], user['profile'], 'deleted-id', 'deleted', 0),
            ('refused-op', user['id'], user['profile'], 'refused-id', 'refused', 0),
            ('bad-state-op', user['id'], user['profile'], 'bad-state-id', 'raw secret error', 0),
        ])

    page = client.get(BASE + '/sessions').json()

    assert page['pending_deletions'] == [{'id': 'cli-1', 'status': 'unconfirmed'}]
    assert page['total'] == baseline['total'] - 1
    assert [item['id'] for item in page['items']] == [
        item['id'] for item in baseline['items'] if item['id'] != 'cli-1']
    assert SessionDeletion(journal).pending(dict(user, id='no-such-owner')) == []
    assert SessionDeletion(journal).pending(dict(user, profile='no-such-profile')) == []


@pytest.mark.parametrize('state', ['prepared', 'native_unknown'])
@pytest.mark.parametrize('outcome', ['deleted', 'refused', 'unknown', 'mismatched'])
def test_only_explicit_get_receipt_resolves_pending_summary(deletion_app, state, outcome):
    from backend.runs import RunJournal

    app, client, user, calls, behavior = deletion_app
    claim = app.state.journal.claim_deletion(user['id'], user['profile'], 'cli-1')
    reopened = SessionDeletion(RunJournal(app.state.journal.path))
    if state == 'native_unknown':
        reopened._outcome(user, claim['operation_id'], state)
    pending = [{'id': 'cli-1', 'status': 'unconfirmed'}]
    assert reopened.pending(user) == pending
    assert client.get(BASE + '/sessions').json()['pending_deletions'] == pending
    behavior['durable_receipt'] = dict(id='cli-1', operation_id=claim['operation_id'],
        status=outcome, deleted=outcome == 'deleted')
    if outcome == 'mismatched':
        behavior['durable_receipt'].update(status='refused', operation_id='wrong')

    receipt = client.get(BASE + '/sessions/cli-1/deletion')
    page = client.get(BASE + '/sessions').json()

    assert receipt.status_code == {'deleted': 200, 'refused': 409, 'unknown': 503, 'mismatched': 503}[outcome]
    if outcome in ('deleted', 'refused'):
        assert 'pending_deletions' not in page
    else:
        assert page['pending_deletions'] == pending
    if outcome == 'deleted':
        assert receipt.json() == {'id': 'cli-1', 'deleted': True}
    assert ('cli-1' in [item['id'] for item in page['items']]) is (outcome == 'refused')
    assert all(method == 'GET' for method, _ in calls)
    receipt_path = '/api/mobile/session-deletions/' + claim['operation_id']
    assert calls.count(('GET', receipt_path)) == 1
