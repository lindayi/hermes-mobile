#!/usr/bin/env python3
"""Read-only issue starter plan; pass --once --apply to permit writes."""

import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deploy.issue_starter import main


if __name__ == "__main__":
    raise SystemExit(main())
