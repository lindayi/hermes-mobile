"""Read-only deletion classification on synthetic, owner-bound SQLite history."""
from contextlib import closing
import json

import pytest

from backend.runs import RunJournal
from backend.steering import SteeringJournal


@pytest.fixture
def history(tmp_path):
    journal = RunJournal(tmp_path / 'runs.db')
    steering = SteeringJournal(journal)
    user = {'id': 'owner', 'profile': 'default'}
    run, _ = journal.submit(user['id'], user['profile'], 'session', 'input', 'start')
    journal.set_upstream(user['id'], run['id'], 'native-run')
    run = journal.get(user['id'], run['id'])
    attempt, _ = steering.claim(user, run, {'input': 'guidance', 'idempotency_key': 'steer-key'})
    steering.finish(attempt['id'], 'unknown')
    journal.finish(user['id'], run['id'], 'completed')
    with closing(journal.connect()) as db:
        yield db, journal.get(user['id'], run['id'])


def classify(history):
    db, run = history
    before = '\n'.join(db.iterdump())
    changes = db.total_changes
    result = SteeringJournal.blocks_deletion(db, run)
    assert db.total_changes == changes
    assert '\n'.join(db.iterdump()) == before
    return result


def accepted_history(history):
    db, run = history
    db.execute("UPDATE steering_attempts SET steer_id=idempotency_key")
    receipt = {'run_id': run['upstream_id'], 'steer_id': 'steer-key',
               'status': 'unknown', 'accepted': True}
    data = {'steer_receipts': [receipt]}
    save_evidence(history, data)
    return data


def save_evidence(history, data):
    db, run = history
    db.execute('INSERT OR REPLACE INTO steering_evidence VALUES(?,?)',
               (run['id'], json.dumps(data)))


@pytest.mark.parametrize('terminal', ['completed', 'failed', 'cancelled'])
def test_positive_terminal_receipt_is_history_not_delivery(history, terminal):
    db, run = history
    accepted_history(history)
    db.execute('UPDATE runs SET status=?', (terminal,))
    run['status'] = terminal
    assert classify(history) is False
    assert db.execute('SELECT status FROM steering_attempts').fetchone()[0] == 'unknown'


@pytest.mark.parametrize('status,blocked', [
    ('unknown', True), ('sending', True), ('unrecognized', True),
    ('accepted_unconfirmed', False), ('not_delivered', False),
])
def test_existing_attempt_classification_is_preserved(history, status, blocked):
    db, _ = history
    db.execute('UPDATE steering_attempts SET status=?', (status,))
    assert classify(history) is blocked


@pytest.mark.parametrize('damage', [
    'missing-attempts', 'missing-evidence', 'missing-owner-column', 'missing-status-column',
    'missing-evidence-column', 'duplicate-evidence', 'duplicate-attempt', 'null-status',
])
def test_required_schema_and_unique_rows_fail_closed(history, damage):
    db, _ = history
    accepted_history(history)
    if damage == 'missing-attempts':
        db.execute('DROP TABLE steering_attempts')
    elif damage == 'missing-evidence':
        db.execute('DROP TABLE steering_evidence')
    elif damage == 'missing-owner-column':
        db.execute('ALTER TABLE steering_attempts RENAME COLUMN user_id TO wrong_owner')
    elif damage == 'missing-status-column':
        db.execute('ALTER TABLE steering_attempts RENAME COLUMN status TO wrong_status')
    elif damage == 'missing-evidence-column':
        db.execute('ALTER TABLE steering_evidence RENAME COLUMN data TO wrong_data')
    else:
        table = 'steering_evidence' if damage == 'duplicate-evidence' else 'steering_attempts'
        db.execute(f'ALTER TABLE {table} RENAME TO saved_table')
        db.execute(f'CREATE TABLE {table} AS SELECT * FROM saved_table')
        if damage == 'null-status':
            db.execute('UPDATE steering_attempts SET status=NULL')
        else:
            db.execute(f'INSERT INTO {table} SELECT * FROM saved_table')
        db.execute('DROP TABLE saved_table')
    assert classify(history) is True


@pytest.mark.parametrize('field', ['user_id', 'profile', 'upstream_id'])
def test_empty_bindings_are_not_positive_authority(history, field):
    db, run = history
    data = accepted_history(history)
    db.execute(f"UPDATE steering_attempts SET {field}='' ")
    run[field] = ''
    if field == 'upstream_id':
        data['steer_receipts'][0]['run_id'] = ''
        save_evidence(history, data)
    assert classify(history) is True


@pytest.mark.parametrize('second', ['valid', 'missing-evidence', 'negative', 'sending', 'unknown-request',
                                     'accepted_unconfirmed', 'not_delivered'])
def test_every_attempt_must_independently_allow_deletion(history, second):
    db, _ = history
    data = accepted_history(history)
    db.execute('''INSERT INTO steering_attempts
        SELECT 'attempt-2', user_id, profile, run_id, upstream_id, 'key-2', input,
               'unknown', 'key-2', created_at, updated_at FROM steering_attempts''')
    if second != 'missing-evidence':
        data['steer_receipts'].append(dict(data['steer_receipts'][0], steer_id='key-2',
                                         accepted=second != 'negative'))
    if second in ('sending', 'accepted_unconfirmed', 'not_delivered'):
        db.execute("UPDATE steering_attempts SET status=? WHERE id='attempt-2'", (second,))
    elif second == 'unknown-request':
        db.execute("UPDATE steering_attempts SET steer_id=NULL WHERE id='attempt-2'")
    save_evidence(history, data)
    assert classify(history) is (second not in ('valid', 'accepted_unconfirmed', 'not_delivered'))


def test_excessively_nested_malformed_evidence_fails_closed(history):
    db, _ = history
    accepted_history(history)
    depth = 10000
    db.execute('UPDATE steering_evidence SET data=?', ('[' * depth + ']' * depth,))
    assert classify(history) is True


def test_empty_local_run_identity_cannot_authorize_history(history):
    db, run = history
    accepted_history(history)
    db.execute("UPDATE steering_attempts SET run_id=''")
    db.execute("UPDATE steering_evidence SET run_id=''")
    run['id'] = ''
    assert classify(history) is True


@pytest.mark.parametrize('known_status', ['accepted_unconfirmed', 'not_delivered'])
def test_duplicate_attempt_key_with_conflicting_status_fails_closed(history, known_status):
    db, _ = history
    accepted_history(history)
    db.execute('ALTER TABLE steering_attempts RENAME TO saved_attempts')
    db.execute('CREATE TABLE steering_attempts AS SELECT * FROM saved_attempts')
    db.execute('INSERT INTO steering_attempts SELECT * FROM saved_attempts')
    db.execute('UPDATE steering_attempts SET status=? WHERE rowid=2', (known_status,))
    db.execute('DROP TABLE saved_attempts')
    assert classify(history) is True


def test_no_attempts_preserves_existing_allowance(history):
    db, _ = history
    db.execute('DELETE FROM steering_attempts')
    assert classify(history) is False


@pytest.mark.parametrize('damage', [
    'absent', 'foreign-run', 'invalid-json', 'null', 'list', 'string', 'empty-object',
    'null-receipts', 'object-receipts', 'string-receipts', 'empty-receipts',
    'null-receipt', 'list-receipt', 'missing-field', 'extra-field',
    'duplicate-receipt', 'conflicting-receipt', 'malformed-neighbor', 'foreign-neighbor',
    'duplicate-json-field', 'invalid-pending', 'extra-envelope-field',
])
def test_missing_malformed_or_conflicting_evidence_fails_closed(history, damage):
    db, _ = history
    data = accepted_history(history)
    receipt = data['steer_receipts'][0]
    if damage == 'absent':
        db.execute('DELETE FROM steering_evidence')
    elif damage == 'foreign-run':
        db.execute("UPDATE steering_evidence SET run_id='other-run'")
    elif damage == 'invalid-json':
        db.execute("UPDATE steering_evidence SET data='not json'")
    elif damage == 'duplicate-json-field':
        raw = json.dumps(data).replace('"accepted": true', '"accepted": false, "accepted": true')
        db.execute('UPDATE steering_evidence SET data=?', (raw,))
    else:
        if damage in ('null', 'list', 'string', 'empty-object'):
            data = {'null': None, 'list': [], 'string': 'bad', 'empty-object': {}}[damage]
        elif damage in ('null-receipts', 'object-receipts', 'string-receipts', 'empty-receipts'):
            data['steer_receipts'] = {'null-receipts': None, 'object-receipts': {},
                                     'string-receipts': 'bad', 'empty-receipts': []}[damage]
        elif damage in ('null-receipt', 'list-receipt'):
            data['steer_receipts'] = [None if damage == 'null-receipt' else []]
        elif damage == 'missing-field':
            del receipt['accepted']
        elif damage == 'extra-field':
            receipt['idempotency_key'] = 'different-key'
        elif damage == 'duplicate-receipt':
            data['steer_receipts'].append(dict(receipt))
        elif damage == 'conflicting-receipt':
            data['steer_receipts'].append(dict(receipt, accepted=False))
        elif damage == 'malformed-neighbor':
            data['steer_receipts'].append(None)
        elif damage == 'foreign-neighbor':
            data['steer_receipts'].append(dict(receipt, steer_id='other-key', run_id='other-native'))
        elif damage == 'invalid-pending':
            data['pending_steer'] = ['bad']
        elif damage == 'extra-envelope-field':
            data['run_id'] = 'other-native'
        save_evidence(history, data)
    assert classify(history) is True


@pytest.mark.parametrize('pending', ['', 'retained terminal guidance'])
def test_optional_pending_text_is_terminal_history_not_proof_of_delivery(history, pending):
    data = accepted_history(history)
    data['pending_steer'] = pending
    save_evidence(history, data)
    assert classify(history) is False


@pytest.mark.parametrize('field,value', [
    ('user_id', 'other-owner'), ('profile', 'other-profile'), ('upstream_id', 'other-native'),
    ('idempotency_key', 'other-key'), ('idempotency_key', ''),
    ('steer_id', None), ('steer_id', ''), ('steer_id', 'other-steer'),
    ('status', 'sending'), ('status', 'unrecognized'),
])
def test_positive_receipt_never_excuses_an_unbound_attempt(history, field, value):
    db, _ = history
    accepted_history(history)
    db.execute(f'UPDATE steering_attempts SET {field}=?', (value,))
    assert classify(history) is True


@pytest.mark.parametrize('field,value', [
    ('run_id', 'other-native'), ('steer_id', 'other-steer'),
    ('status', 'accepted_unconfirmed'), ('status', 'not_delivered'), ('status', 'bogus'),
    ('accepted', False), ('accepted', 1), ('accepted', 1.0), ('accepted', 'true'),
    ('accepted', None),
])
def test_unknown_needs_exact_positive_native_receipt(history, field, value):
    data = accepted_history(history)
    data['steer_receipts'][0][field] = value
    save_evidence(history, data)
    assert classify(history) is True


@pytest.mark.parametrize('state', ['queued', 'running', 'waiting_for_approval', 'stopping', 'unknown', 'bogus'])
def test_unknown_on_nonterminal_run_remains_blocked(history, state):
    db, run = history
    accepted_history(history)
    db.execute('UPDATE runs SET status=?', (state,))
    run['status'] = state
    assert classify(history) is True


def test_unknown_request_without_native_id_stays_blocked_even_with_receipt(history):
    db, _ = history
    data = accepted_history(history)
    db.execute('UPDATE steering_attempts SET steer_id=NULL')
    data['steer_receipts'][0]['steer_id'] = None
    save_evidence(history, data)
    assert classify(history) is True


def test_empty_request_key_cannot_be_positive_history(history):
    db, _ = history
    data = accepted_history(history)
    db.execute("UPDATE steering_attempts SET steer_id='',idempotency_key=''")
    data['steer_receipts'][0]['steer_id'] = ''
    save_evidence(history, data)
    assert classify(history) is True
