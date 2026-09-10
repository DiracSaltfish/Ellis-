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
    assert re.fullmatch(r"[0-9a-f]{64}",manifest["source"]["content_sha256"])
    assert manifest["contains_deployment_secrets"] is False
    expected_components = {
        "Machome 四合一运行中心",
        "machome-hub-agent",
    }
    bundle_macos = path.parent.parent / "MacOS"
    bundle_helpers = path.parent.parent / "Helpers"
    if (bundle_helpers / "machome-webull-browser-helper").is_file():
        expected_components.add("machome-webull-browser-helper")
    if (bundle_helpers / "machome-premium-tgw-helper").is_file():
        expected_components.add("machome-premium-tgw-helper")
    if (bundle_helpers / "machome-wind-probe-helper").is_file():
        expected_components.add("machome-wind-probe-helper")
    if (bundle_helpers / "libmachome_wind_tbapi_probe.dylib").is_file():
        expected_components.add("machome-wind-tbapi-probe")
    if (bundle_macos / "machome-ibkr-bridge").is_file():
        expected_components.add("machome-ibkr-bridge")
    if (path.parent / "upload/machome-upload-component/machome-upload-component").is_file():
        expected_components.update({"machome-upload-component","machome-upload-web"})
    if (bundle_helpers / "machome-iopv-server").is_file():
        expected_components.update({"machome-iopv-server","local-iopv-web"})
        assert (path.parent / "iopv/universe.json").is_file()
    import hashlib
    fresh_agent=path.parents[3]/"machome-hub-agent"
    if fresh_agent.is_file():
        assert hashlib.sha256(fresh_agent.read_bytes()).digest()==hashlib.sha256((bundle_macos/"machome-hub-agent").read_bytes()).digest(), "packaged Agent is stale"
    assert set(manifest["components"]) == expected_components
    parsed = dt.datetime.fromisoformat(manifest["built_at"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
