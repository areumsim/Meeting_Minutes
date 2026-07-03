#!/usr/bin/env python3
"""Unified launcher for meeting workflows.

Thin backward-compatible shim — the real dispatch logic lives in
meeting_minutes_app/cli.py (also the implementation behind the
pip-installed `meeting-minutes` console script). Kept at the repo root
so existing scripts/windows/*.bat launchers and docs that invoke
`python run_meeting.py ...` keep working unmodified.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from meeting_minutes_app.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
