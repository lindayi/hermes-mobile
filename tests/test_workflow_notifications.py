from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from deploy.workflow_events import canonical_json, event_digest, validate_export


NOW = datetime(2026, 10, 1, 21, 0, tzinfo=timezone.utc)


def event(outcome='failed', reason='task_failed', **overrides):
    value = {
        'event_id': f'issue:31:{outcome}:1',
        'outcome': outcome,
        'reason': reason,
        'issue_number': 31,
        'pr_number': None,
        'head_sha': None,
        'merge_sha': None,
        'decision': None,
        'occurred_at': '2026-10-01T20:58:00Z',
    }
    value.update(overrides)
    return value


def export(*events):
    return {
        'version': 1,
        'repository_id': 1399942965,
        'repository': 'lindayi/hermes-mobile',
        'owner_user_id': 'owner-user',
        'generated_at': '2026-10-01T20:59:00Z',
        'events': list(events),
    }


def test_valid_sanitized_outcomes_and_canonical_digest():
    approval = event(
        'approval_required', 'sensitive_approval', issue_number=31, pr_number=32,
        head_sha='a' * 40, decision='authorize_sensitive_action')
    merged = event(
        'merged', 'merged', issue_number=31, pr_number=32, head_sha='a' * 40,
        merge_sha='b' * 40)
    deployed = event(
        'deployed', 'controller_verified', issue_number=31, pr_number=32,
        head_sha='a' * 40, merge_sha='b' * 40)
    payload = export(event(), approval, merged, deployed)

    assert validate_export(payload, now=NOW) == payload
    assert canonical_json({'b': 1, 'a': 2}) == '{"a":2,"b":1}'
    assert event_digest(merged) == hashlib.sha256(canonical_json(merged).encode()).hexdigest()


def test_issue30_terminal_outcomes_have_closed_exact_schema():
    closed = event(
        'closed', 'closed_without_merge', issue_number=31, pr_number=32,
        head_sha='a' * 40)
    conflict = event(
        'blocked', 'conflict_incompatible', event_id='pr:32:conflict:1',
        issue_number=31, pr_number=32,
        head_sha='a' * 40)
    policy = event(
        'blocked', 'policy_broken', event_id='pr:32:policy:1',
        issue_number=31, pr_number=32,
        head_sha='a' * 40)

    assert validate_export(export(closed, conflict, policy), now=NOW)['events'] == [
        closed, conflict, policy]
    for item in (closed, conflict, policy):
        assert item['merge_sha'] is None
        assert item['decision'] is None


@pytest.mark.parametrize('change', [
    {'repository_id': 1},
    {'repository': 'attacker/repo'},
    {'version': True},
    {'unexpected': 'raw public body'},
])
def test_export_rejects_wrong_repository_version_and_unbounded_fields(change):
    payload = export(event())
    payload.update(change)
    with pytest.raises(ValueError):
        validate_export(payload, now=NOW)


@pytest.mark.parametrize('change', [
    {'reason': 'arbitrary failure text'},
    {'outcome': 'deployed'},
    {'event_id': 'x' * 129},
    {'issue_number': True},
    {'body': 'raw issue body'},
    {'reason': []},
    {'decision': []},
])
def test_event_rejects_unenumerated_or_malformed_fields(change):
    with pytest.raises(ValueError):
        validate_export(export(event(**change)), now=NOW)


def test_approval_requires_exact_decision_and_current_head():
    missing_decision = event(
        'approval_required', 'sensitive_approval', pr_number=32,
        head_sha='a' * 40)
    wrong_decision = deepcopy(missing_decision)
    wrong_decision['decision'] = 'run arbitrary command'
    missing_head = event(
        'approval_required', 'sensitive_approval', pr_number=32,
        decision='authorize_sensitive_action')

    for invalid in (missing_decision, wrong_decision, missing_head):
        with pytest.raises(ValueError):
            validate_export(export(invalid), now=NOW)


@pytest.mark.parametrize('outcome', ['merged', 'deployed'])
def test_merged_and_deployed_require_exact_head_and_merge_shas(outcome):
    missing_merge = event(
        outcome, 'merged' if outcome == 'merged' else 'controller_verified',
        pr_number=32, head_sha='a' * 40)
    wrong_sha = event(
        outcome, 'merged' if outcome == 'merged' else 'controller_verified',
        pr_number=32, head_sha='A' * 40, merge_sha='b' * 40)

    for invalid in (missing_merge, wrong_sha):
        with pytest.raises(ValueError):
            validate_export(export(invalid), now=NOW)


def test_export_rejects_stale_or_future_timestamps_and_duplicate_event_ids():
    stale = export(event())
    stale['generated_at'] = '2026-09-29T20:59:00Z'
    duplicated = export(event(), event())
    future = export(event())
    future['generated_at'] = '2026-10-01T22:00:00Z'

    for invalid in (stale, duplicated, future):
        with pytest.raises(ValueError):
            validate_export(invalid, now=NOW)


def test_fresh_export_can_retain_old_unacknowledged_incident():
    old = event(occurred_at='2026-09-01T20:58:00Z')
    snapshot = export(old)

    assert validate_export(snapshot, now=NOW)['events'] == [old]


@pytest.mark.parametrize('change', [
    {'pr_number': None},
    {'head_sha': None},
    {'merge_sha': 'b' * 40},
    {'decision': 'resolve_review'},
])
def test_closed_and_blocked_events_require_exact_nonmerged_pr_identity(change):
    for outcome, reason in (('closed', 'closed_without_merge'),
                            ('blocked', 'conflict_incompatible')):
        item = event(outcome, reason, pr_number=32, head_sha='a' * 40)
        item.update(change)
        with pytest.raises(ValueError):
            validate_export(export(item), now=NOW)


def test_digest_changes_when_event_payload_changes():
    original = event()
    modified = deepcopy(original)
    modified['reason'] = 'issue_failed'

    assert event_digest(original) != event_digest(modified)


def adapter_fixture(tmp_path, payload=None, *, owner_id='owner-user', subscribed=False,
                    category_enabled=True, push_enabled=True, hide_details=False):
    from backend.notification_policy import default_preferences
    from backend.notifications import NotificationService
    from deploy.workflow_notifications import Paths

    root = tmp_path / 'private'
    root.mkdir(mode=0o700, parents=True)
    root.chmod(0o700)
    state_dir = root / 'app-state'
    state_dir.mkdir(mode=0o700)
    state_dir.chmod(0o700)
    config = root / 'config.json'
    config.write_text(json.dumps({'state_dir': str(state_dir)}))
    config.chmod(0o600)
    auth = state_dir / 'auth.sqlite'
    with sqlite3.connect(auth) as db:
        db.execute('CREATE TABLE users(id TEXT,role TEXT,status TEXT,profile TEXT)')
        db.executemany('INSERT INTO users VALUES(?,?,?,?)', [
            (owner_id, 'owner', 'ready', 'default'),
            ('member-user', 'member', 'ready', 'member')])
    auth.chmod(0o600)
    inbox = state_dir / 'notifications.sqlite'
    notifications = NotificationService(inbox, clock=lambda: NOW.timestamp())
    prefs = default_preferences()
    prefs['categories']['operational'] = category_enabled
    prefs['enabled'] = push_enabled
    prefs['hide_details'] = hide_details
    with notifications._db() as db:
        db.execute('INSERT INTO push_preferences VALUES(?,?,?)',
                   (owner_id, 'device-1', json.dumps(prefs)))
        if subscribed:
            db.execute('INSERT INTO subscriptions VALUES(?,?,?,?)',
                       ('https://push.example.invalid/synthetic', owner_id, 'device-1', '{}'))
    payload = payload or export(event())
    event_path = state_dir / 'workflow-events.json'
    event_path.write_text(json.dumps(payload))
    event_path.chmod(0o600)
    os.utime(event_path, (NOW.timestamp(), NOW.timestamp()))
    paths = Paths(
        config=config,
        delivery_state=root / 'delivery' / 'state.json',
        controller_state=root / 'controller',
    )
    return paths, state_dir, auth, inbox, event_path, notifications


def test_default_plan_is_read_only_and_missing_export_stays_unavailable(tmp_path):
    from deploy.workflow_notifications import process

    paths, state_dir, auth, inbox, event_path, _ = adapter_fixture(tmp_path)
    before = {path: path.read_bytes() for path in (auth, inbox, event_path, paths.config)}
    result = process(paths, now=NOW)

    assert result == {'status': 'plan', 'events': 1, 'writes': False}
    assert {path: path.read_bytes() for path in before} == before
    assert not (state_dir / 'workflow-notifications.sqlite').exists()
    assert not (state_dir / 'workflow-notifications.lock').exists()

    event_path.unlink()
    with pytest.raises(ValueError):
        process(paths, now=NOW)
    assert not (state_dir / 'workflow-notifications.sqlite').exists()


@pytest.mark.parametrize('database', ['auth', 'notifications'])
def test_plan_rejects_wal_database_without_creating_sidecars(tmp_path, database):
    from deploy.workflow_notifications import process

    paths, state_dir, auth, inbox, _, _ = adapter_fixture(tmp_path)
    target = auth if database == 'auth' else inbox
    with sqlite3.connect(target) as db:
        assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    sidecars = (Path(str(target) + '-wal'), Path(str(target) + '-shm'))
    assert not any(path.exists() for path in sidecars)
    before = {path.name for path in state_dir.iterdir()}

    with pytest.raises(ValueError):
        process(paths, now=NOW)

    assert {path.name for path in state_dir.iterdir()} == before
    assert not any(path.exists() for path in sidecars)


@pytest.mark.parametrize(('suffix', 'kind'), [
    ('-wal', 'private'),
    ('-shm', 'public'),
    ('-journal', 'symlink'),
])
def test_plan_rejects_sqlite_sidecar_states_without_following_them(tmp_path, suffix, kind):
    from deploy.workflow_notifications import process

    paths, state_dir, auth, _, _, _ = adapter_fixture(tmp_path)
    sidecar = Path(str(auth) + suffix)
    target = state_dir.parent / 'sidecar-target'
    target.write_text('synthetic')
    if kind == 'symlink':
        sidecar.symlink_to(target)
    else:
        sidecar.write_text('synthetic sidecar')
        sidecar.chmod(0o600 if kind == 'private' else 0o644)
    before = {path.name for path in state_dir.iterdir()}

    with pytest.raises(ValueError):
        process(paths, now=NOW)

    assert {path.name for path in state_dir.iterdir()} == before
    assert target.read_text() == 'synthetic'


def test_cli_defaults_to_read_only_plan(tmp_path, capsys):
    from deploy.workflow_notifications import main

    paths, state_dir, auth, inbox, event_path, _ = adapter_fixture(tmp_path)
    before = {path: path.read_bytes() for path in (auth, inbox, event_path, paths.config)}

    assert main([], paths=paths, now=NOW) == 0
    assert json.loads(capsys.readouterr().out) == {'events': 1, 'status': 'plan', 'writes': False}
    assert {path: path.read_bytes() for path in before} == before
    assert not (state_dir / 'workflow-notifications.sqlite').exists()


def test_apply_uses_real_owner_operational_policy_and_deduplicates(tmp_path):
    from deploy.workflow_notifications import process

    paths, state_dir, auth, inbox, _, notifications = adapter_fixture(tmp_path, subscribed=True)
    config_before = paths.config.read_bytes()
    auth_before = auth.read_bytes()
    first = process(paths, apply=True, now=NOW)
    second = process(paths, apply=True, now=NOW)

    assert first['status'] == 'applied'
    assert first['inbox_items'] == 1
    assert second['inbox_items'] == 0
    assert len(notifications.list_inbox('owner-user')) == 1
    assert notifications.list_inbox('member-user') == []
    with sqlite3.connect(inbox) as db:
        assert db.execute('SELECT user_id FROM inbox').fetchall() == [('owner-user',)]
        item_id, title, body = db.execute('SELECT id,title,body FROM inbox').fetchone()
        assert db.execute(
            'SELECT category,profile FROM notification_policy WHERE inbox_id=?', (item_id,)
        ).fetchone() == ('operational', 'default')
        assert db.execute('SELECT status FROM outbox WHERE inbox_id=?', (item_id,)).fetchone() == ('policy_pending',)
        assert db.execute('SELECT count(*) FROM inbox').fetchone() == (1,)
    assert 'issue' in title.lower()
    assert '31' in body
    assert (state_dir / 'workflow-notifications.sqlite').stat().st_mode & 0o077 == 0
    assert (state_dir / 'workflow-notifications.lock').stat().st_mode & 0o077 == 0
    assert paths.config.read_bytes() == config_before
    assert auth.read_bytes() == auth_before


def test_meaningful_outcomes_keep_failures_approvals_merge_and_deployment_distinct(tmp_path):
    from deploy.workflow_notifications import process

    head = 'a' * 40
    merge = 'b' * 40
    events = [
        event('failed', 'issue_failed', event_id='issue:31:issue-failed:1'),
        event('failed', 'task_failed', event_id='issue:31:task-failed:1'),
        event('failed', 'execution_exhausted', event_id='issue:31:exhausted:1'),
        event('execution_uncertain', 'execution_uncertain', event_id='issue:31:uncertain:1'),
        event('approval_required', 'sensitive_approval', event_id='pr:32:approval:1',
              pr_number=32, head_sha=head, decision='authorize_sensitive_action'),
        event('merged', 'merged', event_id='pr:32:merged:1',
              pr_number=32, head_sha=head, merge_sha=merge),
    ]
    paths, _, _, inbox, _, _ = adapter_fixture(tmp_path, export(*events))

    process(paths, apply=True, now=NOW)

    with sqlite3.connect(inbox) as db:
        notices = dict(db.execute('SELECT title,body FROM inbox'))
    assert set(notices) == {
        'Issue #31 needs attention',
        'Workflow task failed for issue #31',
        'Workflow repair stopped for issue #31',
        'Workflow outcome uncertain for issue #31',
        'Owner decision required for PR #32',
        'PR #32 merged',
    }
    assert 'authorize the sensitive action' in notices['Owner decision required for PR #32']
    assert head in notices['Owner decision required for PR #32']
    assert 'separate from deployment' in notices['PR #32 merged']
    assert 'Exact stop cause and counts are unavailable' in notices[
        'Workflow repair stopped for issue #31'
    ]
    assert '20 lifetime dispatches' not in notices['Workflow repair stopped for issue #31']
    assert 'not a billing' in notices['Workflow repair stopped for issue #31']
    assert all('deployed' not in title.lower() for title in notices)


def test_repair_stop_notice_names_pull_and_authenticated_linked_issue():
    from deploy.workflow_notifications import _message

    title, body = _message(event(
        'failed', 'execution_exhausted', event_id='pr:86:exhausted:1',
        issue_number=85, pr_number=86, head_sha='a' * 40,
    ))

    assert title == 'Workflow repair stopped for PR #86 (linked issue #85)'
    assert 'Exact stop cause and counts are unavailable' in body
    assert '20 lifetime dispatches' not in body
    assert '3 consecutive completed repairs' not in body
    assert 'not a billing' in body


@pytest.mark.parametrize(('detail', 'expected'), [
    ({
        'cause': 'source-ceiling', 'used': 20, 'remaining': 0, 'limit': 20,
        'source_used': 20, 'source_ceiling': 20, 'stagnation_count': 1,
    }, ('source-repair lifetime ceiling', '20/20 used', '0 remaining', '1/3')),
    ({
        'cause': 'no-progress', 'used': 3, 'remaining': 0, 'limit': 3,
        'source_used': 4, 'source_ceiling': 20, 'stagnation_count': 3,
    }, ('verified no-progress limit', '3/3 used', '4/20', '3/3')),
    ({
        'cause': 'neutral-ceiling', 'used': 3, 'remaining': 0, 'limit': 3,
        'source_used': 3, 'source_ceiling': 20, 'stagnation_count': 0,
    }, ('neutral-reconciliation limit', '3/3 used', '3/20', '0/3')),
])
def test_execution_exhaustion_notice_reports_typed_stop_details(
        tmp_path, detail, expected):
    from deploy.workflow_notifications import process

    item = event(
        'failed', 'execution_exhausted', event_id='pr:86:exhausted:typed',
        issue_number=85, pr_number=86, head_sha='a' * 40,
        stop_detail=detail,
    )
    paths, _, _, inbox, _, _ = adapter_fixture(tmp_path, export(item))

    process(paths, apply=True, now=NOW)

    with sqlite3.connect(inbox) as db:
        title, body = db.execute('SELECT title,body FROM inbox').fetchone()
    assert title == 'Workflow repair stopped for PR #86 (linked issue #85)'
    assert all(value in body for value in expected)
    assert 'not a billing or deployment status' in body


@pytest.mark.parametrize('change', [
    {'cause': 'unknown'},
    {'used': True},
    {'remaining': 1},
    {'source_ceiling': 19},
    {'stagnation_count': 4},
    {'unexpected': 'unbounded'},
])
def test_execution_exhaustion_rejects_malformed_stop_details(change):
    detail = {
        'cause': 'no-progress', 'used': 3, 'remaining': 0, 'limit': 3,
        'source_used': 4, 'source_ceiling': 20, 'stagnation_count': 3,
    } | change
    with pytest.raises(ValueError):
        validate_export(export(event(
            'failed', 'execution_exhausted', pr_number=86, issue_number=85,
            head_sha='a' * 40, stop_detail=detail,
        )), now=NOW)


def test_issue30_terminal_notices_are_safe_and_operational(tmp_path):
    from deploy.workflow_notifications import process

    head = 'a' * 40
    events = [
        event('closed', 'closed_without_merge', event_id='pr:32:closed:1',
              pr_number=32, head_sha=head),
        event('blocked', 'conflict_incompatible', event_id='pr:32:conflict:1',
              pr_number=32, head_sha=head),
        event('blocked', 'policy_broken', event_id='pr:32:policy:1',
              pr_number=32, head_sha=head),
    ]
    paths, _, _, inbox, _, _ = adapter_fixture(tmp_path, export(*events))

    process(paths, apply=True, now=NOW)

    with sqlite3.connect(inbox) as db:
        notices = dict(db.execute('SELECT title,body FROM inbox'))
        assert db.execute('SELECT DISTINCT category FROM notification_policy').fetchall() == [
            ('operational',)]
    assert notices == {
        'PR #32 closed without merging': 'PR #32 closed without merging. Review its current status.',
        'Incompatible conflict for PR #32': 'PR #32 needs owner review because the conflict is incompatible.',
        'Workflow policy needs review for PR #32': 'PR #32 needs owner review because workflow policy is unavailable.',
    }


def test_existing_push_opt_out_suppresses_outbox_and_private_preview_is_generic(tmp_path):
    from deploy.workflow_notifications import process
    from test_notifications import subscription
    from types import SimpleNamespace

    opted_out, _, _, inbox, _, notifications = adapter_fixture(
        tmp_path / 'opted-out', subscribed=True, category_enabled=False)
    process(opted_out, apply=True, now=NOW)
    with sqlite3.connect(inbox) as db:
        assert db.execute('SELECT count(*) FROM inbox').fetchone() == (1,)
        assert db.execute('SELECT count(*) FROM outbox').fetchone() == (0,)

    master_opted_out, _, _, master_inbox, _, _ = adapter_fixture(
        tmp_path / 'master-opted-out', subscribed=True, push_enabled=False)
    process(master_opted_out, apply=True, now=NOW)
    with sqlite3.connect(master_inbox) as db:
        assert db.execute('SELECT count(*) FROM inbox').fetchone() == (1,)
        assert db.execute('SELECT count(*) FROM outbox').fetchone() == (0,)

    private, _, _, private_inbox, _, private_notifications = adapter_fixture(
        tmp_path / 'private-preview', subscribed=True, hide_details=True)
    with private_notifications._db() as db:
        db.execute('UPDATE subscriptions SET subscription=?',
                   (json.dumps(subscription()),))
    process(private, apply=True, now=NOW)
    delivered = []
    private_notifications.session_validator = lambda user, device: True
    private_notifications.vapid_private_key = 'synthetic-private'
    private_notifications.vapid_public_key = 'synthetic-public'
    private_notifications.send_push = lambda **kwargs: (
        delivered.append(json.loads(kwargs['data'])) or SimpleNamespace(status_code=201))
    assert private_notifications.flush()['sent'] == 1
    assert delivered[0]['title'] == 'Hermes'
    assert delivered[0]['body'] == 'You have a new notification.'
    with sqlite3.connect(private_inbox) as db:
        assert db.execute('SELECT count(*) FROM inbox').fetchone() == (1,)


@pytest.mark.parametrize('hazard', ['wrong_owner', 'wrong_repo', 'stale', 'event_symlink', 'event_hardlink'])
def test_apply_fails_closed_before_writes_for_unbound_or_unsafe_export(tmp_path, hazard):
    from deploy.workflow_notifications import process

    payload = export(event())
    if hazard == 'wrong_owner':
        payload['owner_user_id'] = 'member-user'
    if hazard == 'wrong_repo':
        payload['repository_id'] = 1
    paths, state_dir, _, inbox, event_path, _ = adapter_fixture(tmp_path, payload)
    if hazard == 'stale':
        payload['generated_at'] = '2026-09-29T20:59:00Z'
        event_path.write_text(json.dumps(payload))
        event_path.chmod(0o600)
        os.utime(event_path, (NOW.timestamp(), NOW.timestamp()))
    if hazard == 'event_symlink':
        target = event_path.with_suffix('.target')
        event_path.rename(target)
        event_path.symlink_to(target)
    if hazard == 'event_hardlink':
        os.link(event_path, event_path.with_suffix('.hardlink'))
    before = inbox.read_bytes()

    with pytest.raises(ValueError):
        process(paths, apply=True, now=NOW)

    assert inbox.read_bytes() == before
    assert not (state_dir / 'workflow-notifications.sqlite').exists()


@pytest.mark.parametrize('hazard', ['config_symlink', 'config_public', 'state_alias', 'auth_hardlink'])
def test_apply_rejects_private_state_path_hazards(tmp_path, hazard):
    from deploy.workflow_notifications import process

    paths, state_dir, auth, inbox, _, _ = adapter_fixture(tmp_path)
    if hazard == 'config_symlink':
        target = paths.config.with_suffix('.target')
        paths.config.rename(target)
        paths.config.symlink_to(target)
    elif hazard == 'config_public':
        paths.config.chmod(0o644)
    elif hazard == 'state_alias':
        alias = state_dir.parent / 'state-alias'
        alias.symlink_to(state_dir, target_is_directory=True)
        paths.config.write_text(json.dumps({'state_dir': str(alias)}))
        paths.config.chmod(0o600)
    elif hazard == 'auth_hardlink':
        os.link(auth, auth.with_suffix('.hardlink'))
    before = inbox.read_bytes()

    with pytest.raises(ValueError):
        process(paths, apply=True, now=NOW)

    assert inbox.read_bytes() == before
    assert not (state_dir / 'workflow-notifications.sqlite').exists()


def test_apply_rejects_oversized_export_before_creating_state(tmp_path):
    from deploy.workflow_notifications import MAX_EXPORT_BYTES, process

    paths, state_dir, _, inbox, event_path, _ = adapter_fixture(tmp_path)
    event_path.write_bytes(b' ' * (MAX_EXPORT_BYTES + 1))
    event_path.chmod(0o600)
    before = inbox.read_bytes()

    with pytest.raises(ValueError):
        process(paths, apply=True, now=NOW)

    assert inbox.read_bytes() == before
    assert not (state_dir / 'workflow-notifications.sqlite').exists()


def test_wrong_schema_ambiguous_owner_and_altered_payload_are_rejected(tmp_path):
    from deploy.workflow_notifications import process

    paths, state_dir, auth, inbox, event_path, _ = adapter_fixture(tmp_path)
    with sqlite3.connect(auth) as db:
        db.execute("INSERT INTO users VALUES('second-owner','owner','ready','default')")
    with pytest.raises(ValueError):
        process(paths, apply=True, now=NOW)
    with sqlite3.connect(auth) as db:
        db.execute("DELETE FROM users WHERE id='second-owner'")
    with sqlite3.connect(inbox) as db:
        db.execute('ALTER TABLE notification_policy RENAME COLUMN category TO invalid_category')
    before = inbox.read_bytes()
    with pytest.raises(ValueError):
        process(paths, apply=True, now=NOW)
    assert inbox.read_bytes() == before
    assert not (state_dir / 'workflow-notifications.sqlite').exists()

    paths, state_dir, _, inbox, event_path, _ = adapter_fixture(tmp_path / 'immutable')
    process(paths, apply=True, now=NOW)
    changed = export(event(reason='issue_failed'))
    event_path.write_text(json.dumps(changed))
    event_path.chmod(0o600)
    os.utime(event_path, (NOW.timestamp(), NOW.timestamp()))
    before = inbox.read_bytes()
    with pytest.raises(ValueError):
        process(paths, apply=True, now=NOW)
    assert inbox.read_bytes() == before
    assert (state_dir / 'workflow-notifications.sqlite').exists()

    paths, state_dir, _, inbox, _, _ = adapter_fixture(tmp_path / 'recipient')
    process(paths, apply=True, now=NOW)
    with sqlite3.connect(state_dir / 'workflow-notifications.sqlite') as db:
        db.execute("UPDATE events SET recipient_id='member-user'")
    before = inbox.read_bytes()
    with pytest.raises(ValueError):
        process(paths, apply=True, now=NOW)
    assert inbox.read_bytes() == before


def test_replay_after_crash_before_ack_and_concurrent_replay_are_idempotent(tmp_path):
    from deploy.workflow_notifications import process

    paths, state_dir, _, inbox, _, _ = adapter_fixture(tmp_path)
    process(paths, apply=True, now=NOW)
    with sqlite3.connect(state_dir / 'workflow-notifications.sqlite') as db:
        db.execute("UPDATE events SET status='pending',inbox_id=NULL")
    process(paths, apply=True, now=NOW)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: process(paths, apply=True, now=NOW), range(2)))

    with sqlite3.connect(inbox) as db:
        assert db.execute('SELECT count(*) FROM inbox').fetchone() == (1,)
    with sqlite3.connect(state_dir / 'workflow-notifications.sqlite') as db:
        assert db.execute('SELECT status FROM events').fetchone() == ('acked',)


@pytest.mark.parametrize('interruption', ['empty', 'schema', 'binding', 'commit', 'validated', 'installed'])
def test_first_initialization_interruption_never_publishes_partial_state(tmp_path, interruption):
    import multiprocessing
    from deploy import workflow_notifications as adapter

    paths, state_dir, _, inbox, _, _ = adapter_fixture(tmp_path)
    state = state_dir / adapter.ADAPTER_STATE_NAME
    before = inbox.read_bytes()

    def interrupted_apply():
        real_connection = adapter._state_connection
        real_install = adapter._install_state

        def install(source, destination):
            if interruption == 'validated':
                os._exit(73)
            real_install(source, destination)
            if interruption == 'installed':
                os._exit(73)

        adapter._install_state = install

        def connection(path):
            if interruption == 'empty':
                os._exit(73)
            db = real_connection(path)

            def interrupt_sql(statement):
                normalized = statement.strip().upper()
                stop = {'schema': 'CREATE TABLE EVENTS', 'binding': 'INSERT INTO BINDING',
                        'commit': 'COMMIT'}.get(interruption, 'NEVER')
                if normalized.startswith(stop):
                    os._exit(73)

            db.set_trace_callback(interrupt_sql)
            return db

        adapter._state_connection = connection
        adapter.process(paths, apply=True, now=NOW)

    child = multiprocessing.get_context('fork').Process(target=interrupted_apply)
    child.start()
    try:
        child.join(10)
        assert child.exitcode == 73
    finally:
        if child.is_alive():
            child.kill()
            child.join()
    assert os.path.lexists(state) == (interruption == 'installed')
    if state.exists():
        adapter._validate_adapter_state(state)
        adapter._check_binding(state, 'owner-user')
        assert state.stat().st_nlink == 1
    assert inbox.read_bytes() == before
    abandoned = {p: p.read_bytes() for p in state_dir.glob('.workflow-notifications.sqlite.*')}
    assert bool(abandoned) == (interruption != 'installed')
    assert all(p.stat().st_mode & 0o077 == 0 for p in abandoned)
    assert adapter.process(paths, now=NOW) == {'status': 'plan', 'events': 1, 'writes': False}
    assert adapter.process(paths, apply=True, now=NOW)['inbox_items'] == 1
    assert {p: p.read_bytes() for p in abandoned} == abandoned
    assert state.stat().st_nlink == 1
    adapter._validate_adapter_state(state)
    adapter._check_binding(state, 'owner-user')
    assert adapter.process(paths, apply=True, now=NOW)['inbox_items'] == 0


@pytest.mark.parametrize('suffix', ['-journal', '-wal', '-shm'])
def test_first_initialization_preserves_unknown_canonical_sidecars(tmp_path, suffix):
    from deploy import workflow_notifications as adapter

    paths, state_dir, _, inbox, _, _ = adapter_fixture(tmp_path)
    state = state_dir / adapter.ADAPTER_STATE_NAME
    sidecar = Path(str(state) + suffix)
    sidecar.write_bytes(b'unknown interrupted state')
    sidecar.chmod(0o600)
    before = inbox.read_bytes()
    with pytest.raises(adapter.Blocked):
        adapter.process(paths, apply=True, now=NOW)
    assert not os.path.lexists(state)
    assert sidecar.read_bytes() == b'unknown interrupted state'
    assert inbox.read_bytes() == before
    assert not list(state_dir.glob('.workflow-notifications.sqlite.*'))


@pytest.mark.parametrize('kind', ['empty', 'partial', 'symlink', 'valid_other_owner'])
def test_first_initialization_never_repairs_existing_canonical_state(tmp_path, kind):
    from deploy import workflow_notifications as adapter

    paths, state_dir, _, inbox, _, _ = adapter_fixture(tmp_path)
    state = state_dir / adapter.ADAPTER_STATE_NAME
    if kind == 'valid_other_owner':
        adapter._initialize_state(state, 'other-owner')
    elif kind == 'symlink':
        state.symlink_to(state_dir / 'absent')
    else:
        state.touch(mode=0o600)
        if kind == 'partial':
            with sqlite3.connect(state) as db:
                db.execute('CREATE TABLE binding(singleton TEXT)')
    before = None if state.is_symlink() else state.read_bytes()
    inode = state.lstat().st_ino
    inbox_before = inbox.read_bytes()
    for apply in (False, True):
        with pytest.raises(adapter.Blocked):
            adapter.process(paths, apply=apply, now=NOW)
    with pytest.raises(adapter.Blocked):
        adapter._initialize_state(state, 'owner-user')
    assert state.lstat().st_ino == inode
    assert (None if state.is_symlink() else state.read_bytes()) == before
    assert inbox.read_bytes() == inbox_before


@pytest.mark.parametrize('kind', ['file', 'symlink', 'sidecar'])
def test_atomic_install_does_not_overwrite_state_appearing_during_initialization(tmp_path, monkeypatch, kind):
    from deploy import workflow_notifications as adapter

    paths, state_dir, _, inbox, _, _ = adapter_fixture(tmp_path)
    state = state_dir / adapter.ADAPTER_STATE_NAME
    original_install = adapter._install_state
    before = inbox.read_bytes()
    observed = {}

    def competing_install(source, destination):
        adapter._validate_adapter_state(source)
        adapter._check_binding(source, 'owner-user')
        assert source.parent == destination.parent
        assert source.stat().st_size <= adapter.MAX_STATE_BYTES
        if kind == 'sidecar':
            appeared = Path(str(destination) + '-journal')
            appeared.write_bytes(b'unknown competing journal')
            appeared.chmod(0o600)
        elif kind == 'file':
            appeared = destination
            destination.write_bytes(b'unknown competing canonical state')
            destination.chmod(0o600)
        else:
            appeared = destination
            destination.symlink_to(state_dir / 'absent')
        observed['path'] = appeared
        observed['inode'] = appeared.lstat().st_ino
        original_install(source, destination)

    monkeypatch.setattr(adapter, '_install_state', competing_install)
    with pytest.raises(adapter.Blocked):
        adapter.process(paths, apply=True, now=NOW)
    assert observed['path'].lstat().st_ino == observed['inode']
    if kind == 'sidecar':
        assert not os.path.lexists(state)
        assert observed['path'].read_bytes() == b'unknown competing journal'
    elif kind == 'file':
        assert state.read_bytes() == b'unknown competing canonical state'
    else:
        assert state.is_symlink()
    assert inbox.read_bytes() == before
    assert not list(state_dir.glob('.workflow-notifications.sqlite.*'))


def test_first_initialization_real_capacity_failure_cleans_only_owned_temporary(tmp_path):
    from deploy import workflow_notifications as adapter

    paths, state_dir, _, _, _, _ = adapter_fixture(tmp_path)
    state = state_dir / adapter.ADAPTER_STATE_NAME
    abandoned = state_dir / '.workflow-notifications.sqlite.abandoned'
    abandoned.write_bytes(b'untrusted abandoned temporary')
    abandoned.chmod(0o600)
    with pytest.raises(adapter.Blocked) as failure:
        adapter._initialize_state(state, 'x' * adapter.MAX_STATE_BYTES)
    assert failure.value.__cause__.sqlite_errorcode == sqlite3.SQLITE_FULL
    assert not state.exists()
    assert list(state_dir.glob('.workflow-notifications.sqlite.*')) == [abandoned]
    assert abandoned.read_bytes() == b'untrusted abandoned temporary'
    assert adapter.process(paths, apply=True, now=NOW)['inbox_items'] == 1


@pytest.mark.parametrize('failure', [None, 'file', 'directory'])
def test_first_initialization_fsync_order_and_failure_preserve_retry(tmp_path, monkeypatch, failure):
    import fcntl
    import stat
    from deploy import workflow_notifications as adapter

    paths, state_dir, _, inbox, _, _ = adapter_fixture(tmp_path)
    state = state_dir / adapter.ADAPTER_STATE_NAME
    before = inbox.read_bytes()
    real_fsync = adapter.os.fsync
    synced = []

    def fsync(fd):
        kind = 'directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file'
        synced.append(kind)
        # Validate the fully committed DB before publication, with the lock held.
        check = os.open(state_dir / adapter.LOCK_NAME, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(check, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(check)
        if kind == 'file':
            assert not state.exists()
            temporary, = state_dir.glob('.workflow-notifications.sqlite.*')
            adapter._validate_adapter_state(temporary)
            adapter._check_binding(temporary, 'owner-user')
        else:
            adapter._validate_adapter_state(state)
            adapter._check_binding(state, 'owner-user')
            assert state.stat().st_nlink == 1
        if failure == kind:
            raise OSError('synthetic fsync failure')
        real_fsync(fd)

    with monkeypatch.context() as patcher:
        patcher.setattr(adapter.os, 'fsync', fsync)
        if failure is None:
            assert adapter.process(paths, apply=True, now=NOW)['inbox_items'] == 1
        else:
            with pytest.raises(adapter.Blocked):
                adapter.process(paths, apply=True, now=NOW)
            assert inbox.read_bytes() == before
            assert state.exists() == (failure == 'directory')
    assert synced == (['file'] if failure == 'file' else ['file', 'directory'])
    assert not list(state_dir.glob('.workflow-notifications.sqlite.*'))
    assert adapter.process(paths, apply=True, now=NOW)['inbox_items'] == (0 if failure is None else 1)


def _write_deployed_proof(paths, merge_sha, *, status='succeeded', duplicate_last=False):
    delivery_file = paths.delivery_state
    delivery_file.parent.mkdir(mode=0o700)
    delivery = {
        'status': 'deployed', 'reason': '', 'sha': merge_sha,
        'approval_run_id': 123, 'source_run_id': 456, 'deployment_id': 789,
    }
    last = {'status': 'duplicate', 'reason': 'Consumed intent: deployed'} if duplicate_last else delivery
    delivery_state = {'version': 1, 'latest_id': 789, 'last': last,
                      'records': {f'{merge_sha}:123': delivery}}
    delivery_file.write_text(json.dumps(delivery_state))
    delivery_file.chmod(0o600)
    controller = paths.controller_state
    controller.mkdir(mode=0o700)
    (controller / 'releases').mkdir(mode=0o700)
    release = 'c' * 32
    stage = controller / 'releases' / release
    stage.mkdir(mode=0o700)
    provenance = stage / 'git-provenance.json'
    provenance.write_text(json.dumps({'git_sha': merge_sha}))
    provenance.chmod(0o600)
    (controller / 'current').symlink_to(stage, target_is_directory=True)
    status_file = controller / 'status.json'
    status_file.write_text(json.dumps({'status': status, 'release': release, 'git_sha': merge_sha}))
    status_file.chmod(0o600)
    for path in (delivery_file, status_file):
        os.utime(path, (NOW.timestamp(), NOW.timestamp()))


@pytest.mark.parametrize('reserved', [False, True])
@pytest.mark.parametrize('change', ['current', 'ledger', 'clock'])
def test_deployment_proof_changed_while_waiting_on_real_lock_defers(tmp_path, monkeypatch, reserved, change):
    import fcntl
    from datetime import timedelta
    from threading import Event
    from deploy import workflow_notifications as adapter

    deployed = event('deployed', 'controller_verified', pr_number=32,
                     head_sha='a' * 40, merge_sha='b' * 40)
    fresh = event(event_id='fresh:after-lock')
    paths, state_dir, _, inbox, event_path, _ = adapter_fixture(tmp_path, export(deployed, fresh))
    _write_deployed_proof(paths, deployed['merge_sha'])
    state = state_dir / adapter.ADAPTER_STATE_NAME
    if reserved:
        adapter._initialize_state(state, 'owner-user')
        with sqlite3.connect(state) as db:
            db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?)',
                       (deployed['event_id'], event_digest(deployed), 'owner-user', 'pending', None, 1, 1))
    export_before = event_path.read_bytes()
    current_time = [NOW]

    class WallClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current_time[0]

    monkeypatch.setattr(adapter, 'datetime', WallClock)
    if change == 'clock':
        almost_stale = NOW.timestamp() - adapter.MAX_EVENT_AGE + 1
        for proof in (paths.delivery_state, paths.controller_state / 'status.json'):
            os.utime(proof, (almost_stale, almost_stale))
    waiting = Event()
    real_flock = fcntl.flock

    def announce_wait(fd, operation):
        waiting.set()
        return real_flock(fd, operation)

    lock_fd = os.open(state_dir / adapter.LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o600)
    real_flock(lock_fd, fcntl.LOCK_EX)
    monkeypatch.setattr(adapter.fcntl, 'flock', announce_wait)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            # Exercise the production wall-clock default, not the fixed-now seam.
            future = pool.submit(adapter.process, paths, apply=True)
            try:
                assert waiting.wait(10)
                assert not future.done(), 'The worker must actually wait on the held lock'
                if change == 'current':
                    # Old terminal record remains, but current provenance changed.
                    current = paths.controller_state / 'current'
                    other = paths.controller_state / 'releases' / ('d' * 32)
                    other.mkdir(mode=0o700)
                    current.unlink()
                    current.symlink_to(other, target_is_directory=True)
                elif change == 'ledger':
                    ledger = json.loads(paths.delivery_state.read_text())
                    ledger['latest_id'] += 1
                    paths.delivery_state.write_text(json.dumps(ledger))
                    os.utime(paths.delivery_state, (NOW.timestamp(), NOW.timestamp()))
                else:
                    current_time[0] += timedelta(seconds=2)
            finally:
                real_flock(lock_fd, fcntl.LOCK_UN)
            result = future.result(timeout=10)
    finally:
        os.close(lock_fd)
    assert result == {
        'status': 'applied', 'events': 2, 'inbox_items': 1,
        'deferred': [{'event_id': deployed['event_id'], 'status': 'deferred',
                      'reason': 'deployment_evidence_unavailable'}],
    }
    with sqlite3.connect(state) as db:
        assert db.execute('SELECT digest,status,inbox_id FROM events WHERE event_id=?',
                          (deployed['event_id'],)).fetchone() == (event_digest(deployed), 'pending', None)
    with sqlite3.connect(inbox) as db:
        assert db.execute('SELECT delivery_id FROM inbox').fetchall() == [
            ('workflow-event:v1:' + fresh['event_id'],)]
    assert event_path.read_bytes() == export_before


@pytest.mark.parametrize('reserved', [False, True])
@pytest.mark.parametrize('phase', ['initialization', 'prior_event', 'reservation', 'inbox_owner'])
def test_deployment_proof_is_fresh_throughout_locked_apply(tmp_path, monkeypatch, reserved, phase):
    from datetime import timedelta
    from deploy import workflow_notifications as adapter

    deployed = event('deployed', 'controller_verified', pr_number=32,
                     head_sha='a' * 40, merge_sha='b' * 40)
    first = event(event_id='fresh:before-deployment')
    items = [first, deployed] if phase == 'prior_event' else [deployed]
    paths, state_dir, _, inbox, _, _ = adapter_fixture(tmp_path, export(*items))
    _write_deployed_proof(paths, deployed['merge_sha'])
    state = state_dir / adapter.ADAPTER_STATE_NAME
    if reserved:
        adapter._initialize_state(state, 'owner-user')
        with sqlite3.connect(state) as db:
            db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?)',
                       (deployed['event_id'], event_digest(deployed), 'owner-user', 'pending', None, 1, 1))
    current_time = [NOW]
    changed = []
    if phase == 'prior_event':
        almost_stale = NOW.timestamp() - adapter.MAX_EVENT_AGE + 1
        for proof in (paths.delivery_state, paths.controller_state / 'status.json'):
            os.utime(proof, (almost_stale, almost_stale))

    def change_evidence():
        if not changed:
            changed.append(True)
            if phase == 'prior_event':
                current_time[0] += timedelta(seconds=2)
            else:
                paths.delivery_state.unlink()

    if phase == 'initialization':
        original = adapter._initialize_state

        def initialize(*args):
            original(*args)
            change_evidence()

        monkeypatch.setattr(adapter, '_initialize_state', initialize)
    elif phase == 'prior_event':
        original = adapter._notification_copy

        def notifications(*args, **kwargs):
            service = original(*args, **kwargs)
            ingest = service.ingest

            def ingest_first(*args, **kwargs):
                result = ingest(*args, **kwargs)
                change_evidence()
                return result

            service.ingest = ingest_first
            return service

        monkeypatch.setattr(adapter, '_notification_copy', notifications)
    elif phase == 'reservation':
        original = adapter._state_connection

        def connection(path):
            db = original(path)
            if path == state:
                db.set_trace_callback(lambda sql: change_evidence()
                                      if sql == 'BEGIN IMMEDIATE' else None)
            return db

        monkeypatch.setattr(adapter, '_state_connection', connection)
    else:
        original = adapter._owner
        calls = []

        def owner(path):
            result = original(path)
            calls.append(True)
            if len(calls) == 3:  # Full preflight, locked binding, then pre-Inbox binding.
                change_evidence()
            return result

        monkeypatch.setattr(adapter, '_owner', owner)
    result = adapter.process(paths, apply=True, clock=lambda: current_time[0])
    assert changed
    assert result['inbox_items'] == len(items) - 1
    assert result['deferred'] == [{'event_id': deployed['event_id'], 'status': 'deferred',
                                  'reason': 'deployment_evidence_unavailable'}]
    with sqlite3.connect(state) as db:
        assert db.execute('SELECT digest,status,inbox_id FROM events WHERE event_id=?',
                          (deployed['event_id'],)).fetchone() == (event_digest(deployed), 'pending', None)
    with sqlite3.connect(inbox) as db:
        assert db.execute('SELECT count(*) FROM inbox').fetchone() == (len(items) - 1,)
        assert db.execute("SELECT count(*) FROM inbox WHERE title LIKE '%verified deployed%'").fetchone() == (0,)


@pytest.mark.parametrize('transition', ['proof_recovered', 'acked'])
def test_lock_waiter_refreshes_preflight_deferral_and_exact_ack(tmp_path, monkeypatch, transition):
    import fcntl
    from threading import Event
    from deploy import workflow_notifications as adapter

    deployed = event('deployed', 'controller_verified', pr_number=32,
                     head_sha='a' * 40, merge_sha='b' * 40)
    paths, state_dir, _, inbox, _, notifications = adapter_fixture(tmp_path, export(deployed))
    state = state_dir / adapter.ADAPTER_STATE_NAME
    adapter._initialize_state(state, 'owner-user')
    with sqlite3.connect(state) as db:
        db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?)',
                   (deployed['event_id'], event_digest(deployed), 'owner-user', 'pending', None, 1, 1))
    waiting = Event()
    real_flock = fcntl.flock

    def announce_wait(fd, operation):
        waiting.set()
        return real_flock(fd, operation)

    lock_fd = os.open(state_dir / adapter.LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o600)
    real_flock(lock_fd, fcntl.LOCK_EX)
    monkeypatch.setattr(adapter.fcntl, 'flock', announce_wait)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(adapter.process, paths, apply=True, now=NOW)
            try:
                assert waiting.wait(10)
                assert not future.done()
                if transition == 'proof_recovered':
                    _write_deployed_proof(paths, deployed['merge_sha'])
                else:
                    title, body = adapter._message(deployed)
                    notice = notifications.ingest(
                        'owner-user', 'workflow-event:v1:' + deployed['event_id'], title, body,
                        session_id=None, category='operational', profile='default')
                    with sqlite3.connect(state) as db:
                        db.execute("UPDATE events SET status='acked',inbox_id=?", (notice['id'],))
            finally:
                real_flock(lock_fd, fcntl.LOCK_UN)
            result = future.result(timeout=10)
    finally:
        os.close(lock_fd)
    assert result == {'status': 'applied', 'events': 1,
                      'inbox_items': 1 if transition == 'proof_recovered' else 0}
    with sqlite3.connect(inbox) as db:
        assert db.execute('SELECT count(*) FROM inbox').fetchone() == (1,)
    # Replays after proof disappears still neither defer nor recreate retained ACKs.
    if paths.delivery_state.exists():
        paths.delivery_state.unlink()
    assert adapter.process(paths, apply=True, now=NOW) == {
        'status': 'applied', 'events': 1, 'inbox_items': 0}


@pytest.mark.parametrize('proof', ['superseded', 'stale', 'missing'])
@pytest.mark.parametrize('reserved', [False, True])
def test_unacked_deployment_defers_without_poisoning_fresh_batch(tmp_path, proof, reserved):
    from deploy import workflow_notifications as adapter

    old = event('deployed', 'controller_verified', event_id='pr:32:deployed:old',
                pr_number=32, head_sha='a' * 40, merge_sha='b' * 40,
                occurred_at='2026-09-28T20:58:00Z')
    fresh = event(event_id='issue:31:fresh:1')
    current = event('deployed', 'controller_verified', event_id='pr:33:deployed:new',
                    pr_number=33, head_sha='d' * 40, merge_sha='e' * 40)
    items = [old, fresh, current] if proof == 'superseded' else [old, fresh]
    paths, state_dir, auth, inbox, event_path, _ = adapter_fixture(tmp_path, export(*items))
    state = state_dir / adapter.ADAPTER_STATE_NAME
    if reserved:
        adapter._initialize_state(state, 'owner-user')
        with sqlite3.connect(state) as db:
            db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?)',
                       (old['event_id'], event_digest(old), 'owner-user', 'pending', None, 1, 1))
    if proof != 'missing':
        _write_deployed_proof(paths, current['merge_sha'] if proof == 'superseded' else old['merge_sha'])
        if proof == 'superseded':
            # Even a retained terminal ledger record is not current controller proof.
            ledger = json.loads(paths.delivery_state.read_text())
            ledger['records'][old['merge_sha'] + ':122'] = {
                'status': 'deployed', 'reason': '', 'sha': old['merge_sha'],
                'approval_run_id': 122, 'source_run_id': 455, 'deployment_id': 788,
            }
            paths.delivery_state.write_text(json.dumps(ledger))
            os.utime(paths.delivery_state, (NOW.timestamp(), NOW.timestamp()))
        else:
            for path in (paths.delivery_state, paths.controller_state / 'status.json'):
                stamp = NOW.timestamp() - 3 * 86400
                os.utime(path, (stamp, stamp))
    expected = [{'event_id': old['event_id'], 'status': 'deferred',
                 'reason': 'deployment_evidence_unavailable'}]
    before = {p: p.read_bytes() for p in (auth, inbox, event_path)}
    plan = adapter.process(paths, now=NOW)
    assert plan['deferred'] == expected
    assert {p: p.read_bytes() for p in before} == before
    assert state.exists() == reserved
    assert not (state_dir / adapter.LOCK_NAME).exists()

    result = adapter.process(paths, apply=True, now=NOW)
    assert result['deferred'] == expected
    assert result['events'] == len(items)
    assert result['inbox_items'] == len(items) - 1
    with sqlite3.connect(state) as db:
        assert db.execute('SELECT digest,recipient_id,status,inbox_id FROM events WHERE event_id=?',
                          (old['event_id'],)).fetchone() == (
                              event_digest(old), 'owner-user', 'pending', None)
    with sqlite3.connect(inbox) as db:
        assert db.execute("SELECT count(*) FROM inbox WHERE title='PR #32 verified deployed'").fetchone() == (0,)
        assert db.execute('SELECT count(*) FROM inbox').fetchone() == (len(items) - 1,)
    assert event_path.read_bytes() == before[event_path]
    assert adapter.process(paths, apply=True, now=NOW)['inbox_items'] == 0
    assert adapter.process(paths, apply=True, now=NOW)['deferred'] == expected


@pytest.mark.parametrize('hazard', [
    'event_schema', 'owner', 'digest', 'recipient', 'binding', 'adapter_schema', 'ack_identity',
])
def test_deferred_deployment_does_not_hide_later_batch_security_conflict(tmp_path, hazard):
    from deploy import workflow_notifications as adapter

    old = event('deployed', 'controller_verified', pr_number=32,
                head_sha='a' * 40, merge_sha='b' * 40)
    fresh = event(event_id='fresh:1')
    paths, state_dir, auth, inbox, event_path, _ = adapter_fixture(tmp_path, export(old, fresh))
    state = state_dir / adapter.ADAPTER_STATE_NAME
    adapter._initialize_state(state, 'owner-user')
    with sqlite3.connect(state) as db:
        db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?)',
                   (fresh['event_id'], event_digest(fresh), 'owner-user', 'pending', None, 1, 1))
        if hazard == 'digest':
            db.execute("UPDATE events SET digest=?", ('0' * 64,))
        elif hazard == 'recipient':
            db.execute("UPDATE events SET recipient_id='member-user'")
        elif hazard == 'binding':
            db.execute("UPDATE binding SET owner_user_id='member-user'")
        elif hazard == 'adapter_schema':
            db.execute('ALTER TABLE events RENAME COLUMN digest TO invalid_digest')
        elif hazard == 'ack_identity':
            db.execute("UPDATE events SET status='acked',inbox_id=''")
    if hazard == 'event_schema':
        fresh['body'] = 'arbitrary untrusted body'
        event_path.write_text(json.dumps(export(old, fresh)))
        os.utime(event_path, (NOW.timestamp(), NOW.timestamp()))
    elif hazard == 'owner':
        with sqlite3.connect(auth) as db:
            db.execute("UPDATE users SET id='replacement-owner' WHERE role='owner'")
    before = {p: p.read_bytes() for p in (state, auth, inbox, event_path)}
    for apply in (False, True):
        with pytest.raises(adapter.Blocked):
            adapter.process(paths, apply=apply, now=NOW)
        assert {p: p.read_bytes() for p in before} == before
    assert not (state_dir / adapter.LOCK_NAME).exists()


def test_complete_batch_identity_rechecked_after_lock_before_any_reservation(tmp_path, monkeypatch):
    from deploy import workflow_notifications as adapter

    old = event('deployed', 'controller_verified', pr_number=32,
                head_sha='a' * 40, merge_sha='b' * 40)
    fresh = event(event_id='fresh:1')
    paths, state_dir, _, inbox, _, _ = adapter_fixture(tmp_path, export(fresh, old))
    state = state_dir / adapter.ADAPTER_STATE_NAME
    adapter._initialize_state(state, 'owner-user')
    real_flock = adapter.fcntl.flock
    snapshots = {}

    def conflict_while_waiting(fd, operation):
        real_flock(fd, operation)
        with sqlite3.connect(state) as db:
            db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?)',
                       (old['event_id'], '0' * 64, 'owner-user', 'pending', None, 1, 1))
        snapshots[state] = state.read_bytes()

    monkeypatch.setattr(adapter.fcntl, 'flock', conflict_while_waiting)
    before = inbox.read_bytes()
    with pytest.raises(adapter.Blocked):
        adapter.process(paths, apply=True, now=NOW)
    assert inbox.read_bytes() == before
    assert state.read_bytes() == snapshots[state]


def _fill_adapter_to_capacity(state, limit, *, status='acked'):
    """Real SQLite fixture: retain bounded, valid synthetic event identities."""
    with sqlite3.connect(state) as db:
        page_size = db.execute('PRAGMA page_size').fetchone()[0]
        db.execute(f'PRAGMA max_page_count={limit // page_size}')
        index = 0
        for batch_size in (512, 32, 1):
            while True:
                rows = []
                for number in range(index, index + batch_size):
                    item = event(event_id=f'history:{number:08d}:' + 'x' * 111)
                    rows.append((item['event_id'], event_digest(item), 'owner-user',
                                 status, 'i' * 36 if status == 'acked' else None,
                                 NOW.timestamp(), NOW.timestamp()))
                try:
                    db.executemany('INSERT INTO events VALUES(?,?,?,?,?,?,?)', rows)
                    db.commit()
                    index += batch_size
                except sqlite3.OperationalError as error:
                    assert error.sqlite_errorcode == sqlite3.SQLITE_FULL
                    db.rollback()
                    break
        assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
    assert state.stat().st_size == limit


def test_real_capacity_boundary_preserves_history_and_reconciles_inflight(tmp_path):
    from deploy import workflow_notifications as adapter

    old = event('deployed', 'controller_verified', pr_number=32,
                head_sha='a' * 40, merge_sha='b' * 40)
    after_inbox = event(event_id='recover:after-inbox')
    before_inbox = event(event_id='recover:before-inbox')
    paths, state_dir, _, inbox, event_path, _ = adapter_fixture(tmp_path, export(old, after_inbox))
    _write_deployed_proof(paths, old['merge_sha'])
    adapter.process(paths, apply=True, now=NOW)
    state = state_dir / adapter.ADAPTER_STATE_NAME
    with sqlite3.connect(state) as db:
        # Reserve eventual ACK space for an inflight item without an Inbox row.
        db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?)',
                   (before_inbox['event_id'], event_digest(before_inbox), 'owner-user',
                    'acked', 'i' * 36, NOW.timestamp(), NOW.timestamp()))
    _fill_adapter_to_capacity(state, adapter.MAX_STATE_BYTES)
    with sqlite3.connect(state) as db:
        db.execute("UPDATE events SET status='pending',inbox_id=NULL WHERE event_id IN (?,?)",
                   (after_inbox['event_id'], before_inbox['event_id']))
        preserved = db.execute("SELECT * FROM events WHERE status='acked' ORDER BY event_id").fetchall()
    # Replay must not recheck old deployment proof, including under capacity pressure.
    paths.delivery_state.unlink()
    incoming = [event(event_id=f'new:{n:08d}:' + 'y' * 115) for n in range(253)]
    event_path.write_text(json.dumps(export(*incoming, after_inbox, before_inbox, old)))
    os.utime(event_path, (NOW.timestamp(), NOW.timestamp()))
    try:
        result = adapter.process(paths, apply=True, now=NOW)
    except adapter.Blocked:
        result = None
    # The original bug commits over the bound then bricks its very next open.
    assert state.stat().st_size <= adapter.MAX_STATE_BYTES
    assert result is not None
    assert result['deferred']
    assert all(row['reason'] == 'state_capacity' for row in result['deferred'])
    assert {row['event_id'] for row in result['deferred']} <= {e['event_id'] for e in incoming}
    with sqlite3.connect(state) as db:
        assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert db.execute("SELECT * FROM events WHERE status='acked' AND event_id NOT LIKE 'new:%' "
                          "AND event_id NOT LIKE 'recover:%' ORDER BY event_id").fetchall() == preserved
        assert db.execute("SELECT event_id,status FROM events WHERE event_id LIKE 'recover:%' ORDER BY event_id").fetchall() == [
            (after_inbox['event_id'], 'acked'), (before_inbox['event_id'], 'acked')]
    with sqlite3.connect(inbox) as db:
        for item in (after_inbox, before_inbox, old):
            assert db.execute('SELECT count(*) FROM inbox WHERE delivery_id=?',
                              ('workflow-event:v1:' + item['event_id'],)).fetchone() == (1,)
        for deferred in result['deferred']:
            assert db.execute('SELECT count(*) FROM inbox WHERE delivery_id=?',
                              ('workflow-event:v1:' + deferred['event_id'],)).fetchone() == (0,)
    # Retained ACKs still deduplicate after Inbox retention; no history deletion.
    with sqlite3.connect(inbox) as db:
        db.execute('DELETE FROM notification_policy')
        db.execute('DELETE FROM inbox')
    event_path.write_text(json.dumps(export(old, after_inbox, before_inbox)))
    os.utime(event_path, (NOW.timestamp(), NOW.timestamp()))
    snapshot = state.read_bytes()
    assert adapter.process(paths, now=NOW)['status'] == 'plan'
    assert adapter.process(paths, apply=True, now=NOW)['inbox_items'] == 0
    assert state.read_bytes() == snapshot
    with sqlite3.connect(inbox) as db:
        assert db.execute('SELECT count(*) FROM inbox').fetchone() == (0,)


def test_ack_capacity_failure_keeps_pending_identity_and_inbox_replay(tmp_path):
    from deploy import workflow_notifications as adapter

    paths, state_dir, _, inbox, event_path, _ = adapter_fixture(tmp_path)
    state = state_dir / adapter.ADAPTER_STATE_NAME
    adapter._initialize_state(state, 'owner-user')
    _fill_adapter_to_capacity(state, adapter.MAX_STATE_BYTES, status='pending')
    with sqlite3.connect(state) as db:
        page_size = db.execute('PRAGMA page_size').fetchone()[0]
        db.execute(f'PRAGMA max_page_count={adapter.MAX_STATE_BYTES // page_size}')
        candidates = db.execute('SELECT event_id FROM events ORDER BY rowid LIMIT 256').fetchall()
        for (event_id,) in candidates:
            try:
                db.execute("UPDATE events SET status='acked',inbox_id=? WHERE event_id=?",
                           ('i' * 36, event_id))
                db.commit()
            except sqlite3.OperationalError as error:
                assert error.sqlite_errorcode == sqlite3.SQLITE_FULL
                db.rollback()
                target = event(event_id=event_id)
                break
        else:
            pytest.fail('Fixture did not reach an ACK page split at the real limit')
    event_path.write_text(json.dumps(export(target)))
    os.utime(event_path, (NOW.timestamp(), NOW.timestamp()))
    before = state.read_bytes()
    expected = [{'event_id': target['event_id'], 'status': 'deferred', 'reason': 'state_capacity'}]
    for _ in range(2):
        result = adapter.process(paths, apply=True, now=NOW)
        assert result['deferred'] == expected
        assert result['inbox_items'] == 0
        assert state.read_bytes() == before
        with sqlite3.connect(state) as db:
            assert db.execute('SELECT digest,status,inbox_id FROM events WHERE event_id=?',
                              (target['event_id'],)).fetchone() == (event_digest(target), 'pending', None)
            assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        with sqlite3.connect(inbox) as db:
            assert db.execute('SELECT count(*) FROM inbox').fetchone() == (1,)
        assert adapter.process(paths, now=NOW)['status'] == 'plan'


@pytest.mark.parametrize('page_size', [1024, 4096, 65536])
def test_every_adapter_write_connection_bounds_sqlite_pages(tmp_path, page_size):
    from contextlib import closing
    from deploy import workflow_notifications as adapter

    paths, state_dir, _, _, _, _ = adapter_fixture(tmp_path)
    state = state_dir / adapter.ADAPTER_STATE_NAME
    adapter.process(paths, apply=True, now=NOW)
    with sqlite3.connect(state) as db:
        db.execute(f'PRAGMA page_size={page_size}')
        db.execute('VACUUM')
    before = state.read_bytes()
    # SQLite does not persist max_page_count; reconnect twice and exercise a
    # real growing UPDATE, not only an assertion on configured PRAGMAs.
    for _ in range(2):
        with closing(adapter._state_connection(state)) as db:
            with pytest.raises(sqlite3.OperationalError) as failure:
                with db:
                    db.execute('UPDATE events SET inbox_id=?', ('x' * adapter.MAX_STATE_BYTES,))
            assert failure.value.sqlite_errorcode == sqlite3.SQLITE_FULL
        assert state.read_bytes() == before
        assert state.stat().st_size <= adapter.MAX_STATE_BYTES
    assert adapter.process(paths, now=NOW)['status'] == 'plan'


def test_cli_reports_sanitized_partial_apply_without_forging_event_reason(tmp_path, capsys):
    from deploy import workflow_notifications as adapter

    deployed = event('deployed', 'controller_verified', pr_number=32,
                     head_sha='a' * 40, merge_sha='b' * 40)
    paths, _, _, _, event_path, _ = adapter_fixture(tmp_path, export(deployed, event()))
    _write_deployed_proof(paths, deployed['merge_sha'], status='synthetic-private-error')
    original = event_path.read_bytes()
    assert adapter.main(['--apply'], paths=paths, now=NOW) == 0
    output = capsys.readouterr()
    assert not output.err
    assert json.loads(output.out) == {
        'status': 'applied', 'events': 2, 'inbox_items': 1,
        'deferred': [{'event_id': deployed['event_id'], 'status': 'deferred',
                      'reason': 'deployment_evidence_unavailable'}],
    }
    assert 'synthetic-private-error' not in output.out
    assert str(paths.config.parent) not in output.out
    assert event_path.read_bytes() == original


def test_deployed_event_uses_durable_terminal_after_duplicate_poll(tmp_path):
    from deploy.workflow_notifications import process

    deployed = event('deployed', 'controller_verified', pr_number=32,
                     head_sha='a' * 40, merge_sha='b' * 40)
    paths, _, _, inbox, _, _ = adapter_fixture(tmp_path, export(deployed))
    _write_deployed_proof(paths, 'b' * 40, duplicate_last=True)

    assert process(paths, apply=True, now=NOW)['inbox_items'] == 1
    with sqlite3.connect(inbox) as db:
        assert 'verified deployed' in db.execute('SELECT title FROM inbox').fetchone()[0].lower()


def _assert_deployment_deferred(paths, state_dir, inbox, item):
    from deploy import workflow_notifications as adapter

    before = inbox.read_bytes()
    result = adapter.process(paths, apply=True, now=NOW)
    assert result['inbox_items'] == 0
    assert result['deferred'] == [
        {'event_id': item['event_id'], 'status': 'deferred',
         'reason': 'deployment_evidence_unavailable'}]
    assert inbox.read_bytes() == before
    with sqlite3.connect(state_dir / adapter.ADAPTER_STATE_NAME) as db:
        assert db.execute('SELECT digest,status,inbox_id FROM events').fetchall() == [
            (event_digest(item), 'pending', None)]


def test_deployed_event_rejects_terminal_record_behind_latest_intent(tmp_path):
    from deploy.workflow_notifications import process

    deployed = event('deployed', 'controller_verified', pr_number=32,
                     head_sha='a' * 40, merge_sha='b' * 40)
    paths, state_dir, _, inbox, _, _ = adapter_fixture(tmp_path, export(deployed))
    _write_deployed_proof(paths, 'b' * 40, duplicate_last=True)
    ledger = json.loads(paths.delivery_state.read_text())
    ledger['latest_id'] = 790
    paths.delivery_state.write_text(json.dumps(ledger))
    paths.delivery_state.chmod(0o600)
    os.utime(paths.delivery_state, (NOW.timestamp(), NOW.timestamp()))

    _assert_deployment_deferred(paths, state_dir, inbox, deployed)


def test_acked_deployment_replay_skips_obsolete_proof_without_blocking_new_events(tmp_path):
    from deploy.workflow_notifications import process

    deployed = event('deployed', 'controller_verified', event_id='pr:32:deployed:1',
                     pr_number=32, head_sha='a' * 40, merge_sha='b' * 40)
    paths, _, _, inbox, event_path, _ = adapter_fixture(tmp_path, export(deployed))
    _write_deployed_proof(paths, 'b' * 40)
    process(paths, apply=True, now=NOW)

    newer = event(event_id='issue:31:after-deployment:1')
    event_path.write_text(json.dumps(export(deployed, newer)))
    event_path.chmod(0o600)
    os.utime(event_path, (NOW.timestamp(), NOW.timestamp()))
    status_path = paths.controller_state / 'status.json'
    status_path.write_text(json.dumps({
        'status': 'succeeded', 'release': 'c' * 32, 'git_sha': 'd' * 40}))
    status_path.chmod(0o600)
    os.utime(status_path, (NOW.timestamp(), NOW.timestamp()))

    assert process(paths, apply=True, now=NOW)['inbox_items'] == 1
    with sqlite3.connect(inbox) as db:
        assert db.execute('SELECT count(*) FROM inbox').fetchone() == (2,)
        assert {row[0] for row in db.execute('SELECT title FROM inbox')} == {
            'PR #32 verified deployed', 'Workflow task failed for issue #31'}


def test_old_unacknowledged_event_catches_up_and_replay_stays_deduplicated(tmp_path):
    from datetime import timedelta
    from deploy.workflow_notifications import process

    old = event(event_id='issue:31:outage-catchup:1',
                occurred_at='2026-09-28T20:58:00Z')
    paths, _, _, inbox, event_path, _ = adapter_fixture(tmp_path, export(old))
    process(paths, apply=True, now=NOW)

    later = NOW + timedelta(days=2)
    regenerated = export(old)
    regenerated['generated_at'] = later.strftime('%Y-%m-%dT%H:%M:%SZ')
    event_path.write_text(json.dumps(regenerated))
    event_path.chmod(0o600)
    os.utime(event_path, (later.timestamp(), later.timestamp()))
    assert process(paths, apply=True, now=later)['inbox_items'] == 0

    with sqlite3.connect(inbox) as db:
        assert db.execute('SELECT count(*) FROM inbox').fetchone() == (1,)


def test_merged_is_not_deployed_and_deployed_requires_fresh_exact_controller_ledger(tmp_path):
    from deploy.workflow_notifications import process

    merged = event('merged', 'merged', pr_number=32, head_sha='a' * 40, merge_sha='b' * 40)
    merged_paths, _, _, merged_inbox, _, _ = adapter_fixture(tmp_path / 'merged', export(merged))
    process(merged_paths, apply=True, now=NOW)
    with sqlite3.connect(merged_inbox) as db:
        assert 'merged' in db.execute('SELECT body FROM inbox').fetchone()[0].lower()

    deployed = event('deployed', 'controller_verified', pr_number=32,
                     head_sha='a' * 40, merge_sha='b' * 40)
    deployed_paths, state_dir, _, inbox, _, _ = adapter_fixture(tmp_path / 'deployed', export(deployed))
    _assert_deployment_deferred(deployed_paths, state_dir, inbox, deployed)

    _write_deployed_proof(deployed_paths, 'b' * 40)
    process(deployed_paths, apply=True, now=NOW)
    with sqlite3.connect(inbox) as db:
        title, body = db.execute('SELECT title,body FROM inbox').fetchone()
        assert 'verified deployed' in title.lower()
        assert 'b' * 40 in body
        assert db.execute('SELECT count(*) FROM inbox').fetchone() == (1,)

    mismatch_paths, mismatch_state, _, mismatch_inbox, _, _ = adapter_fixture(
        tmp_path / 'sha-mismatch', export(event(
            'deployed', 'controller_verified', pr_number=32,
            head_sha='a' * 40, merge_sha='d' * 40)))
    _write_deployed_proof(mismatch_paths, 'b' * 40)
    before = mismatch_inbox.read_bytes()
    _assert_deployment_deferred(mismatch_paths, mismatch_state, mismatch_inbox,
                                dict(deployed, merge_sha='d' * 40))
    assert mismatch_inbox.read_bytes() == before

    failed_paths, failed_state, _, failed_inbox, _, _ = adapter_fixture(
        tmp_path / 'failed-controller', export(deployed))
    _write_deployed_proof(failed_paths, 'b' * 40, status='running')
    before = failed_inbox.read_bytes()
    _assert_deployment_deferred(failed_paths, failed_state, failed_inbox, deployed)
    assert failed_inbox.read_bytes() == before

    stale_paths, stale_state, _, stale_inbox, _, _ = adapter_fixture(
        tmp_path / 'stale-controller', export(deployed))
    _write_deployed_proof(stale_paths, 'b' * 40)
    status_path = stale_paths.controller_state / 'status.json'
    old = NOW.timestamp() - 24 * 60 * 60 - 1
    os.utime(status_path, (old, old))
    before = stale_inbox.read_bytes()
    _assert_deployment_deferred(stale_paths, stale_state, stale_inbox, deployed)
    assert stale_inbox.read_bytes() == before
