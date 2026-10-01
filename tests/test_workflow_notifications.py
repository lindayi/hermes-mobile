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
        'Workflow budget exhausted for issue #31',
        'Workflow outcome uncertain for issue #31',
        'Owner decision required for PR #32',
        'PR #32 merged',
    }
    assert 'authorize the sensitive action' in notices['Owner decision required for PR #32']
    assert head in notices['Owner decision required for PR #32']
    assert 'separate from deployment' in notices['PR #32 merged']
    assert all('deployed' not in title.lower() for title in notices)


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


def test_deployed_event_uses_durable_terminal_after_duplicate_poll(tmp_path):
    from deploy.workflow_notifications import process

    deployed = event('deployed', 'controller_verified', pr_number=32,
                     head_sha='a' * 40, merge_sha='b' * 40)
    paths, _, _, inbox, _, _ = adapter_fixture(tmp_path, export(deployed))
    _write_deployed_proof(paths, 'b' * 40, duplicate_last=True)

    assert process(paths, apply=True, now=NOW)['inbox_items'] == 1
    with sqlite3.connect(inbox) as db:
        assert 'verified deployed' in db.execute('SELECT title FROM inbox').fetchone()[0].lower()


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

    with pytest.raises(ValueError):
        process(paths, apply=True, now=NOW)
    assert inbox.exists()
    assert not (state_dir / 'workflow-notifications.sqlite').exists()


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
    with pytest.raises(ValueError):
        process(deployed_paths, apply=True, now=NOW)
    assert not (state_dir / 'workflow-notifications.sqlite').exists()

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
    with pytest.raises(ValueError):
        process(mismatch_paths, apply=True, now=NOW)
    assert mismatch_inbox.read_bytes() == before
    assert not (mismatch_state / 'workflow-notifications.sqlite').exists()

    failed_paths, failed_state, _, failed_inbox, _, _ = adapter_fixture(
        tmp_path / 'failed-controller', export(deployed))
    _write_deployed_proof(failed_paths, 'b' * 40, status='running')
    before = failed_inbox.read_bytes()
    with pytest.raises(ValueError):
        process(failed_paths, apply=True, now=NOW)
    assert failed_inbox.read_bytes() == before
    assert not (failed_state / 'workflow-notifications.sqlite').exists()

    stale_paths, stale_state, _, stale_inbox, _, _ = adapter_fixture(
        tmp_path / 'stale-controller', export(deployed))
    _write_deployed_proof(stale_paths, 'b' * 40)
    status_path = stale_paths.controller_state / 'status.json'
    old = NOW.timestamp() - 24 * 60 * 60 - 1
    os.utime(status_path, (old, old))
    before = stale_inbox.read_bytes()
    with pytest.raises(ValueError):
        process(stale_paths, apply=True, now=NOW)
    assert stale_inbox.read_bytes() == before
    assert not (stale_state / 'workflow-notifications.sqlite').exists()
