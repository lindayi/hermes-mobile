#!/usr/bin/env python3
"""Validate offline evidence; optionally collect authenticated read-only PR review data."""

import argparse
import json
import os
from pathlib import Path
import stat
import sys

sys.dont_write_bytecode = True
SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from deploy.autonomy_policy import validate_transition

MAX_EVIDENCE = 8 * 1024 * 1024


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate evidence key')
        result[key] = value
    return result


def _read_evidence(path):
    flags = (os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0)
             | getattr(os, 'O_NOFOLLOW', 0))
    descriptor = os.open(Path(path), flags)
    with os.fdopen(descriptor, 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError('Evidence must be a regular file')
        raw = source.read(MAX_EVIDENCE + 1)
    if len(raw) > MAX_EVIDENCE:
        raise ValueError('Evidence exceeds size limit')
    return json.loads(raw, object_pairs_hook=_unique_object)


def collect_review(evidence, number, expected_head, *, api, store):
    """Refresh review/protection only; never refresh or authenticate baseline CI/source."""
    from copy import deepcopy
    from deploy.autonomy_policy import REPOSITORY, REPOSITORY_ID
    from deploy.cloud_coordinator import (
        Coordinator, _current_sensitive_owner_command, _required_checks,
        classify_sensitive_paths,
    )

    evidence = deepcopy(evidence)
    sha = evidence['main']['sha']
    route = f'repos/{REPOSITORY}'
    coordinator = Coordinator(api, store)
    coordinator._identity()
    if api.get(f'{route}/commits/main')['sha'] != sha:
        raise ValueError('Baseline main is no longer current')
    protection = api.get(f'{route}/branches/main/protection')
    state = store.snapshot()
    enrollment = state['enrollments'][str(number)]
    snapshot = coordinator._snapshot_pull(number, enrollment, sha, actions=state['actions'])
    pull = snapshot['pull']
    if (snapshot['head'] != expected_head or snapshot['scoped'] is not True
            or pull['state'] != 'open' or pull['draft'] is not False
            or snapshot['policy_complete'] is not True):
        raise ValueError('Review collection identity/head/base/policy fence failed')
    for name in ('files', 'reviews', 'threads', 'check_runs'):
        if (not isinstance(snapshot.get(name), list)
                or any(not isinstance(item, dict) for item in snapshot[name])):
            raise ValueError(f'Malformed snapshot collection: {name}')
    required = protection['required_status_checks']
    if {(item['context'], item['app_id']) for item in snapshot['required']} != (
            {(item['context'], item['app_id']) for item in required['checks']}
            | {(context, None) for context in required.get('contexts', [])}):
        raise ValueError('Protection snapshot changed or disagrees with branch rules')
    evidence['protection'] = {
        'repository_id': REPOSITORY_ID, 'repository': REPOSITORY, 'branch': 'main',
        'complete': True, 'strict': required['strict'],
        'enforce_admins': protection['enforce_admins']['enabled'],
        'required_conversation_resolution': protection['required_conversation_resolution']['enabled'],
        'required_checks': required['checks'],
    }
    authorization = enrollment.get('sensitive_authorization')
    evidence['copilot_review'] = {
        'pull_number': number, 'pull_id': pull['id'],
        'repository_id': pull['base']['repo']['id'],
        'base_branch': pull['base']['ref'], 'base_sha': pull['base']['sha'],
        'head_sha': expected_head, 'state': pull['state'], 'draft': pull['draft'],
        'pull_author_id': pull['user']['id'],
        'reviews_complete': snapshot['reviews_complete'],
        'threads_complete': snapshot['threads_complete'],
        # Successful _snapshot_pull REST lists have exception-on-partial pagination;
        # unlike reviews/threads, there is no checks_complete snapshot field.
        'checks_complete': True, 'check_runs': snapshot['check_runs'],
        'reviews': snapshot['reviews'], 'threads': snapshot['threads'],
        'change': {
            'head_sha': expected_head, 'files_complete': snapshot['files_complete'],
            'sensitive': classify_sensitive_paths(
                snapshot['files'], complete=snapshot['files_complete']),
            'owner_authorization': authorization,
            'owner_command': _current_sensitive_owner_command(api, number, authorization),
            'targeted_review': enrollment.get('targeted_review'),
        },
    }
    coordinator._identity()
    if (api.get(f'{route}/pulls/{number}') != pull
            or api.get(f'{route}/commits/main')['sha'] != sha
            or api.get(f'{route}/branches/main/protection') != protection
            or _required_checks(api) != (
                snapshot['required'], snapshot['policy_complete'],
                snapshot['up_to_date_required'], snapshot['conversation_resolution_required'])
            or store.snapshot() != state
            or _current_sensitive_owner_command(api, number, authorization)
            != evidence['copilot_review']['change']['owner_command']):
        raise ValueError('Final review collection fence changed')
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', required=True, choices=('pre-cutover', 'staging', 'post-cutover'))
    parser.add_argument('--collect-review', action='store_true', help='opt in to authenticated read-only GitHub review collection')
    parser.add_argument('--state', type=Path, help='explicit read-only coordinator state path')
    parser.add_argument('--pull', type=int, help='exact enrolled pull request number')
    parser.add_argument('--head', help='expected exact pull request head SHA')
    parser.add_argument('evidence', type=Path, help='read-only JSON snapshot collected from GitHub')
    args = parser.parse_args(argv)
    if args.collect_review:
        import re
        if (args.state is None or args.pull is None or args.pull < 1
                or not re.fullmatch('[0-9a-f]{40}', args.head or '')):
            parser.error('--collect-review requires --state, positive --pull and exact --head')
    elif any(value is not None for value in (args.state, args.pull, args.head)):
        parser.error('--state, --pull and --head require --collect-review')
    try:
        evidence = _read_evidence(args.evidence)
        if args.collect_review:
            from deploy.cloud_coordinator import CoordinatorError, GhApi, StateStore
            try:
                evidence = collect_review(evidence, args.pull, args.head,
                                          api=GhApi(), store=StateStore(args.state))
            except (CoordinatorError, KeyError, TypeError) as error:
                raise ValueError(f'Review collection failed: {error}') from error
        report = validate_transition(evidence, phase=args.phase)
        if args.collect_review:
            report['evidence'] = evidence
            report['collection'] = {'refreshed': ['copilot_review', 'protection'],
                                    'baseline_refreshed': False}
    except (OSError, ValueError, json.JSONDecodeError, RecursionError) as error:
        report = {'ready': False, 'phase': args.phase, 'blockers': ['invalid-evidence']}
        print(f'Autonomy policy evidence rejected: {error}', file=sys.stderr)
    print(json.dumps(report, sort_keys=True))
    return 0 if report['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
