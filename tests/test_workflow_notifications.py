from copy import deepcopy
from datetime import datetime, timezone
import hashlib

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


def test_digest_changes_when_event_payload_changes():
    original = event()
    modified = deepcopy(original)
    modified['reason'] = 'issue_failed'

    assert event_digest(original) != event_digest(modified)
