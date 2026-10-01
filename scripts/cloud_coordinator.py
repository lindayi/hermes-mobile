#!/usr/bin/env python3
"""Poll, plan, and optionally apply bounded cloud-coordination actions."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deploy.cloud_coordinator import main


if __name__ == "__main__":
    raise SystemExit(main())
