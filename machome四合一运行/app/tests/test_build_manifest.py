#!/usr/bin/env python3
"""Validate the generated, packaged build identity (never deployment config)."""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        raise AssertionError("usage: test_build_manifest.py PATH")
    path = Path(sys.argv[1])
    assert path.is_file(), f"packaged build manifest missing: {path}"
    raw = path.read_bytes()
    assert len(raw) <= 64 * 1024
    manifest = json.loads(raw.decode("utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["kind"] == "machome_hub_build_manifest"
    assert re.fullmatch(r"[A-Za-z0-9._+-]{8,160}", manifest["build_id"])
    assert re.fullmatch(r"(?:[0-9a-f]{40}|unversioned)", manifest["source"]["git_commit"])
    assert isinstance(manifest["source"]["git_dirty"], bool)
    assert manifest["contains_deployment_secrets"] is False
    assert set(manifest["components"]) == {
        "Machome 四合一运行中心",
        "machome-hub-agent",
    }
    parsed = dt.datetime.fromisoformat(manifest["built_at"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
