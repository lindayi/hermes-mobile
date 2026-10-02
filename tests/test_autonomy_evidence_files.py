"""Bounded evidence readers reject special files before any blocking read."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest


@pytest.mark.parametrize('phase', ['pre-cutover', 'staging', 'post-cutover'])
def test_cli_rejects_fifo_without_waiting_for_a_writer(tmp_path, phase):
    evidence = tmp_path / 'evidence.fifo'
    os.mkfifo(evidence, 0o600)
    before = evidence.stat()
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'autonomy_policy.py'
    result = subprocess.run(
        [sys.executable, str(script), '--phase', phase, str(evidence)],
        capture_output=True, text=True, timeout=2,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout) == {
        'ready': False, 'phase': phase, 'blockers': ['invalid-evidence'],
    }
    assert 'Evidence must be a regular file' in result.stderr
    assert 'Traceback' not in result.stderr
    after = evidence.stat()
    assert stat.S_ISFIFO(after.st_mode)
    assert (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) == (
        before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
    )
    assert list(tmp_path.iterdir()) == [evidence]
