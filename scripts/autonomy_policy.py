#!/usr/bin/env python3
"""Validate a collected gate-transition evidence snapshot without network or writes."""

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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', required=True, choices=('pre-cutover', 'staging', 'post-cutover'))
    parser.add_argument('evidence', type=Path, help='read-only JSON snapshot collected from GitHub')
    args = parser.parse_args(argv)
    try:
        report = validate_transition(_read_evidence(args.evidence), phase=args.phase)
    except (OSError, ValueError, json.JSONDecodeError, RecursionError) as error:
        report = {'ready': False, 'phase': args.phase, 'blockers': ['invalid-evidence']}
        print(f'Autonomy policy evidence rejected: {error}', file=sys.stderr)
    print(json.dumps(report, sort_keys=True))
    return 0 if report['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
