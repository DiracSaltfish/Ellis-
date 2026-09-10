#!/usr/bin/env python3
"""Static release gate for the self-contained Premium A runtime."""

import json
import pathlib
import sys


def fail(message: str) -> None:
    raise AssertionError(message)


source_root = pathlib.Path(sys.argv[1]).resolve()
runtime_roots = [
    source_root / "modules" / "premium" / "engine",
    source_root / "helpers" / "premium_tgw",
    source_root / "third_party" / "tgw_cpp",
]
for root in runtime_roots:
    if not root.is_dir():
        fail(f"missing Premium A runtime directory: {root}")

old_absolute_roots = (
    "/Users/ellis/工具程序开发/溢价率拉升监控cpp",
    "/Users/ellis/newnavnav",
    "/Users/ellis/Desktop/ETF交割/WebullData",
    "/Users/ellis/Desktop/ETF交割/实时申购赎回数据",
)
for root in runtime_roots:
    for path in root.rglob("*"):
        if not path.is_file() or path.name == "source-manifest.json":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for forbidden in old_absolute_roots:
            if forbidden in text:
                fail(f"runtime source contains old absolute root: {path}")
        if "src/client/QmtClient" in text or "src/console/" in text:
            fail(f"runtime source contains B-side implementation reference: {path}")

engine_manifest_path = source_root / "modules" / "premium" / "engine" / "source-manifest.json"
engine_manifest = json.loads(engine_manifest_path.read_text(encoding="utf-8"))
if engine_manifest.get("module") != "premium_a":
    fail("Premium A source manifest has the wrong module")
excluded = set(engine_manifest.get("excluded_source_prefixes", []))
if not {"src/client", "src/console", "src/client/QmtClient"}.issubset(excluded):
    fail("Premium A source manifest does not explicitly exclude all B-side code")
if len(engine_manifest.get("files", [])) != 18:
    fail("Premium A source manifest must freeze the 18 migrated A-side files")
for entry in engine_manifest["files"]:
    digest = entry.get("sha256", "")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        fail(f"invalid baseline SHA-256 entry: {entry}")
    destination = entry.get("destination", "")
    prefix = "app/"
    if not destination.startswith(prefix):
        fail(f"destination escapes app source tree: {destination}")
    if not (source_root / destination[len(prefix):]).is_file():
        fail(f"migrated destination is missing: {destination}")

helper_manifest = json.loads(
    (source_root / "helpers" / "premium_tgw" / "source-manifest.json").read_text(encoding="utf-8")
)
if helper_manifest.get("component") != "machome-premium-tgw-helper":
    fail("TGW source manifest has the wrong component")
if helper_manifest.get("runtime_dependency_on_source_roots") is not False:
    fail("TGW source manifest does not declare runtime source-root independence")
if len(helper_manifest.get("files", [])) < 20:
    fail("TGW source manifest is incomplete")

print("Premium A runtime isolation gate passed")
