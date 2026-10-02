#!/usr/bin/env python3
import sys

sys.dont_write_bytecode = True

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deploy.workflow_notifications import main


if __name__ == '__main__':
    raise SystemExit(main())
