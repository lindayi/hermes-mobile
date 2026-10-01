#!/usr/bin/env python3
"""Report managed stale scratch and expired failure evidence; only --apply deletes."""
import argparse
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deploy.test_workspace import cleanup


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, help='private marked test root (default /tmp/hmt-UID)')
    parser.add_argument('--apply', action='store_true', help='delete eligible inactive managed records')
    args = parser.parse_args(argv)
    try:
        report = cleanup(args.root, apply=args.apply)
    except (ValueError, OSError, RuntimeError) as error:
        print(f'Test cleanup failed: {error}', file=sys.stderr)
        return 1
    for item in report:
        action = ('removed' if args.apply else 'would remove') if item['action'] == 'remove' else 'skip'
        print(f"{action}: {item['path']} ({item['bytes']} bytes): {item['reason']}")
    candidates = [item for item in report if item['action'] == 'remove']
    print(f"{'Apply' if args.apply else 'Dry run'}: {len(candidates)} candidates, "
          f"{sum(item['bytes'] for item in candidates)} bytes")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
