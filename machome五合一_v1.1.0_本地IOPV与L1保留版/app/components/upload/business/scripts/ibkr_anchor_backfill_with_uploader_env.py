#!/usr/bin/env python3
"""Run IBKR anchor backfill with the same local uploader defaults.

This intentionally reuses sina_ws_uploader_with_token.py when it exists, so the
scheduled backfill does not need a second copy of the upload token.
"""
import os
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
os.chdir(REPO_DIR)

try:
    import sina_ws_uploader_with_token  # noqa: F401
except Exception as exc:  # pragma: no cover - best-effort local launcher
    print(f"warning: could not load uploader defaults: {exc}", file=sys.stderr, flush=True)

from ibkr_backfill_valuation_anchors import main


if __name__ == "__main__":
    raise SystemExit(main())
