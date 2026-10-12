"""Synthetic CLI -> real coordinator snapshot -> policy integration; no live calls."""
import copy
import json

import pytest

from deploy import cloud_coordinator as coordinator
from scripts import autonomy_policy as cli
from tests.test_autonomy_policy import _sensitive_evidence
from tests.test_cloud_coordinator import FakeApi, enrolled_record


@pytest.fixture
def collection(tmp_path, monkeypatch):
    baseline = _sensitive_evidence('staging')
    review = baseline['copilot_review']
    api = FakeApi(sensitive=True, head_sha=review['head_sha'])
    api.current_main_sha = baseline['main']['sha']
    api.pull['base']['sha'] = api.current_main_sha
    selected = review['reviews'][-1]
    api.set_copilot_review(
        review_id=selected['id'], head_sha=selected['commit_id'], body=selected['body'],
        submitted_at=selected['submitted_at'], state=selected['state'],
        body_html=selected['body_html'],
    )
    command = review['change']['owner_command']
    api.comments.append({
        'id': command['comment_id'], 'user': {'id': command['actor_id']},
        **{key: command[key] for key in ('body', 'created_at', 'updated_at')},
    })
    protection = {
        'required_status_checks': {
            'checks': baseline['protection']['required_checks'], 'strict': True,
            'contexts': [],
        },
        'enforce_admins': {'enabled': True},
        'required_conversation_resolution': {'enabled': True},
    }
    original_get = api.get

    def get(route):
        if route.endswith('/branches/main/protection'):
            return copy.deepcopy(protection)
        return copy.deepcopy(original_get(route))

    api.get = get
    monkeypatch.setattr(coordinator, 'GhApi', lambda: api)
    state = coordinator.StateStore._empty()
    state['enrollments']['16'] = enrolled_record(
        sensitive_authorization=review['change']['owner_authorization'],
        targeted_review=review['change']['targeted_review'],
    )
    state_path = tmp_path / 'state.json'
    state_path.write_text(json.dumps(state))
    state_path.chmod(0o600)
    # A stale/missing baseline command must be replaced by authenticated GET, not reused.
    review['change'].pop('owner_command')
    evidence_path = tmp_path / 'baseline.json'
    evidence_path.write_text(json.dumps(baseline))
    args = ['--phase', 'staging', '--collect-review', '--state', str(state_path),
            '--pull', '16', '--head', review['head_sha'], str(evidence_path)]
    return api, baseline, state_path, evidence_path, args, protection


def test_cli_collects_current_owner_command_through_actual_snapshot(collection, capsys):
    api, baseline, state_path, evidence_path, args, _ = collection
    before = (state_path.read_bytes(), evidence_path.read_bytes())
    assert cli.main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['ready'] is True
    evidence = report['evidence']
    command = evidence['copilot_review']['change']['owner_command']
    assert command['comment_id'] == 4
    assert command['actor_id'] == coordinator.OWNER_ID
    assert evidence['copilot_review']['checks_complete'] is True
    assert evidence['copilot_review']['reviews'][0]['updatedAt'] == api.review_submitted_at
    assert evidence['main'] == baseline['main']
    assert evidence['source_ci'] == baseline['source_ci']
    assert report['collection']['baseline_refreshed'] is False
    assert before == (state_path.read_bytes(), evidence_path.read_bytes())
    assert api.writes == api.graphql_writes == []


@pytest.mark.parametrize('mutation', ['missing', 'edited', 'body', 'actor', 'wrong-context'])
def test_cli_invalid_current_command_blocks_without_mutating_state(collection, capsys, mutation):
    api, _, state_path, _, args, _ = collection
    before = state_path.read_bytes()
    if mutation == 'missing':
        api.comments.pop()
    elif mutation == 'edited':
        api.comments[-1]['updated_at'] = '2026-10-02T00:00:00Z'
    elif mutation == 'body':
        api.comments[-1]['body'] += ' changed'
    elif mutation == 'actor':
        api.comments[-1]['user']['id'] = 999
    else:
        get = api.get

        def wrong_context(route):
            value = get(route)
            if '/issues/comments/' in route:
                value['issue_url'] = 'https://api.github.com/repos/other/repo/issues/16'
            return value

        api.get = wrong_context
    assert cli.main(args) == 1
    report = json.loads(capsys.readouterr().out)
    assert 'sensitive-review-authorization' in report['blockers']
    assert state_path.read_bytes() == before
    assert api.writes == api.graphql_writes == []


@pytest.mark.parametrize('status', [401, 403, 429, 500, None])
def test_cli_transient_command_read_preserves_authorization(collection, capsys, status):
    api, _, state_path, _, args, _ = collection
    before = state_path.read_bytes()
    get = api.get

    def unavailable(route):
        if '/issues/comments/' in route:
            raise coordinator.ApiError('synthetic unavailable', status=status)
        return get(route)

    api.get = unavailable
    assert cli.main(args) == 1
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report['ready'] is False
    assert 'evidence' not in report
    assert 'synthetic unavailable' in captured.err
    assert report['blockers'] == ['invalid-evidence']
    assert state_path.read_bytes() == before
    assert api.writes == api.graphql_writes == []


@pytest.mark.parametrize('read', ['initial', 'final'])
@pytest.mark.parametrize('field, malformed', [
    (None, None), (None, []), (None, 'not a comment'),
    ('user', []), ('user', None), ('user', {'id': '1'}),
    ('body', []), ('body', None), ('created_at', []),
    ('created_at', 'invalid'), ('updated_at', {}), ('id', True),
    ('issue_url', []),
])
def test_malformed_owner_comment_aborts_collection(collection, capsys, read, field, malformed):
    api, _, state_path, evidence_path, args, _ = collection
    before = (state_path.read_bytes(), evidence_path.read_bytes())
    get = api.get
    final = False
    command_reads = 0

    def malformed_get(route):
        nonlocal final, command_reads
        value = get(route)
        # The final identity read is after the initial command has been collected.
        if route == 'user' and command_reads:
            final = True
        if '/issues/comments/' in route:
            command_reads += 1
            if read == 'initial' or final:
                return malformed if field is None else value | {field: malformed}
        return value

    api.get = malformed_get
    assert cli.main(args) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        'ready': False, 'phase': 'staging', 'blockers': ['invalid-evidence'],
    }
    assert 'Malformed current owner-comment response' in captured.err
    assert read == 'initial' or (final and command_reads >= 2)
    assert before == (state_path.read_bytes(), evidence_path.read_bytes())
    assert api.writes == api.graphql_writes == []


@pytest.mark.parametrize('hazard', ['head', 'base', 'pull-id', 'head-repository',
                                  'base-ref', 'author', 'draft', 'main', 'protection',
                                  'owner', 'repository'])
def test_final_collection_fences_fail_closed(collection, capsys, hazard):
    api, _, state_path, _, args, _ = collection
    before = state_path.read_bytes()
    get = api.get
    counts = {}

    def changed(route):
        value = get(route)
        counts[route] = counts.get(route, 0) + 1
        if route.endswith('/pulls/16') and counts[route] == 2:
            if hazard == 'head':
                value['head']['sha'] = 'e' * 40
            elif hazard == 'base':
                value['base']['sha'] = 'e' * 40
            elif hazard == 'pull-id':
                value['id'] += 1
            elif hazard == 'head-repository':
                value['head']['repo']['id'] = 1
            elif hazard == 'base-ref':
                value['base']['ref'] = 'other'
            elif hazard == 'author':
                value['user']['id'] = 1
            elif hazard == 'draft':
                value['draft'] = True
        if hazard == 'main' and route.endswith('/commits/main') and counts[route] == 2:
            value['sha'] = 'e' * 40
        if (hazard == 'protection' and route.endswith('/branches/main/protection')
                and counts[route] >= 3):
            value['enforce_admins']['enabled'] = False
        if hazard == 'owner' and route == 'user' and counts[route] == 2:
            value['id'] = 1
        if hazard == 'repository' and route == 'repos/lindayi/hermes-mobile' and counts[route] == 2:
            value['id'] = 1
        return value

    api.get = changed
    assert cli.main(args) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['ready'] is False
    assert 'evidence' not in report
    assert state_path.read_bytes() == before
    assert api.writes == api.graphql_writes == []


@pytest.mark.parametrize('fence, final_read', [
    ('/pulls/16', 2), ('/commits/main', 2), ('/branches/main/protection', 3),
])
@pytest.mark.parametrize('mutation', ['edited', 'deleted', 401, 403, 429, 500, None])
def test_final_owner_command_read_fails_closed(collection, capsys, fence, final_read, mutation):
    api, baseline, state_path, evidence_path, args, _ = collection
    before = (state_path.read_bytes(), evidence_path.read_bytes())
    original_pull = copy.deepcopy(api.pull)
    assert api.pull['head']['sha'] != baseline['main']['sha']
    get = api.get
    fence_reads = command_reads = 0
    changed = False

    def concurrent_change(route):
        nonlocal fence_reads, command_reads, changed
        if '/issues/comments/' in route:
            command_reads += 1
            if changed and mutation not in ('edited', 'deleted'):
                raise coordinator.ApiError('synthetic final command unavailable', status=mutation)
        value = get(route)
        if route.endswith(fence):
            fence_reads += 1
            if fence_reads == final_read:
                assert command_reads >= 1
                changed = True
                if mutation == 'edited':
                    api.comments[-1]['updated_at'] = '2026-10-02T00:00:00Z'
                elif mutation == 'deleted':
                    api.comments.pop()
        return value

    api.get = concurrent_change
    assert cli.main(args) == 1
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert changed and command_reads >= 2
    assert report['ready'] is False
    assert report['blockers'] == ['invalid-evidence']
    assert 'evidence' not in report
    if mutation in ('edited', 'deleted'):
        assert 'Final review collection fence changed' in captured.err
    else:
        assert 'synthetic final command unavailable' in captured.err
    assert api.pull == original_pull
    assert before == (state_path.read_bytes(), evidence_path.read_bytes())
    assert api.writes == api.graphql_writes == []


@pytest.mark.parametrize('hazard', ['edited-review', 'pending-check', 'partial-checks', 'malformed-check'])
def test_real_snapshot_review_and_check_contracts_remain_blocking(collection, capsys, hazard):
    api, _, state_path, _, args, _ = collection
    before = state_path.read_bytes()
    if hazard == 'edited-review':
        api.copilot_review_last_edited_at = '2026-10-02T00:00:00Z'
    elif hazard == 'pending-check':
        api.pending_required = True
    else:
        get_all = api.get_all

        def checks(route, *, collection=None):
            if '/check-runs?' in route:
                if hazard == 'partial-checks':
                    raise coordinator.ApiError('partial check pagination', status=500)
                return [None]
            return get_all(route, collection=collection)

        api.get_all = checks
    assert cli.main(args) == 1
    assert json.loads(capsys.readouterr().out)['ready'] is False
    assert state_path.read_bytes() == before
    assert api.writes == api.graphql_writes == []


def test_offline_cli_does_not_construct_transport(collection, monkeypatch, capsys):
    _, _, _, evidence_path, _, _ = collection

    def forbidden():
        pytest.fail('offline validation must not construct a network transport')

    monkeypatch.setattr(coordinator, 'GhApi', forbidden)
    assert cli.main(['--phase', 'staging', str(evidence_path)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['blockers'] == ['sensitive-review-authorization']
    assert 'evidence' not in report


@pytest.mark.parametrize('flag', ['--state', '--pull', '--head'])
def test_collect_mode_requires_explicit_context(collection, capsys, flag):
    _, _, _, _, args, _ = collection
    index = args.index(flag)
    del args[index:index + 2]
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
    assert '--collect-review requires' in capsys.readouterr().err


def test_collection_does_not_relabel_bad_baseline_source_ci(collection, capsys):
    _, baseline, _, evidence_path, args, _ = collection
    baseline['source_ci']['head_sha'] = 'e' * 40
    evidence_path.write_text(json.dumps(baseline))
    assert cli.main(args) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['blockers']
    assert report['evidence']['source_ci'] == baseline['source_ci']
    assert report['collection']['baseline_refreshed'] is False
