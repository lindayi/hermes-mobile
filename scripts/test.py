#!/usr/bin/env python3
"""Run isolated checks: python3 scripts/test.py [options] {all,python,js,browser} [-- test-files filters]."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from deploy.test_workspace import run_suite

NODE = '/home/lindayi/.hermes/node/bin/node'


def main(argv=None, *, run=run_suite):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--root', type=Path, help='private marked test root (default /tmp/hmt-UID)')
    parser.add_argument('--assets', type=Path, help='use these generated assets; default source/frontend')
    parser.add_argument('suite', nargs='?', default='all', choices=('all', 'python', 'js', 'browser'))
    parser.add_argument('extra_args', nargs=argparse.REMAINDER,
                        help='selected-suite test files and filters, e.g. -- tests/test_member_jobs.py -k ready')
    args = parser.parse_args(argv)
    source = args.source.absolute()
    # Do not resolve executable symlinks: that would discard virtualenv discovery.
    python = Path(os.environ.get('HERMES_TEST_PYTHON') or source / '.venv/bin/python').absolute()
    if not python.is_file() or not os.access(python, os.X_OK):
        parser.error('Python interpreter missing; set HERMES_TEST_PYTHON to a working test interpreter')
    node = os.environ.get('HERMES_TEST_NODE')
    if node is None:
        node = shutil.which('node') or NODE
    node = Path(node).absolute()
    if not node.is_file() or not os.access(node, os.X_OK):
        parser.error('Node interpreter missing or not executable; set HERMES_TEST_NODE to a working test interpreter')
    extra = args.extra_args
    if extra[:1] == ['--']:
        extra = extra[1:]
    try:
        run(source, python=str(python), node=str(node), suite=args.suite,
            assets=args.assets, root=args.root, extra_args=extra)
    except subprocess.CalledProcessError as error:
        return error.returncode if error.returncode > 0 else 128 - error.returncode
    except (ValueError, OSError, RuntimeError) as error:
        print(f'Test runner: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
