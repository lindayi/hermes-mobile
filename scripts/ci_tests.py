#!/usr/bin/env python3
"""Run complete hosted shards or the residual local compatibility suite safely."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from deploy.ci_selection import select_tests
from deploy.test_workspace import run_suite


def main(argv=None, *, run=run_suite):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets', type=Path)
    parser.add_argument('--shard', type=int, default=0)
    parser.add_argument('--shards', type=int, default=1)
    parser.add_argument('--list', action='store_true', help='print exact selected files without running')
    parser.add_argument('suite', choices=('python','host','native','js','browser'))
    args = parser.parse_args(argv)
    try:
        selected = select_tests(SOURCE, args.suite, shard=args.shard, shards=args.shards)
        print(json.dumps({'suite':args.suite,'shard':args.shard,'shards':args.shards,'files':selected}), flush=True)
        if args.list:
            return 0
        python = Path(os.environ.get('HERMES_TEST_PYTHON') or sys.executable).absolute()
        node = shutil.which('node') or '/home/lindayi/.hermes/node/bin/node'
        if not python.is_file() or not os.access(python, os.X_OK) or not os.access(node, os.X_OK):
            raise ValueError('Working Python and Node interpreters are required')
        run(SOURCE, python=str(python), node=node,
            suite='python' if args.suite in {'host', 'native'} else args.suite,
            assets=args.assets, extra_args=selected, node_concurrency=1)
    except subprocess.CalledProcessError as error:
        return error.returncode if error.returncode > 0 else 128 - error.returncode
    except (ValueError, OSError, RuntimeError) as error:
        print(f'CI runner: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
