#!/usr/bin/env python3
"""Fail closed when a packaged Upload runtime can reach the retired program.

The check intentionally scans raw bytes as well as paths.  This covers text
configuration, Python imports and embedded Mach-O strings without executing
anything from the candidate bundle.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


FORBIDDEN = (
    b"/Users/ellis/newnavnav",
    b"/Users/ellis/NAVNAV",
    b"newnavnav-shared-ibkr",
    b"com.newnavnav.",
    b"modules/bridge/UploadClient",
    b"PYTHONPATH",
)


def scan(root: Path) -> list[str]:
    findings: list[str] = []
    if not root.is_dir():
        return [f"runtime root is not a directory: {root}"]
    resolved_root = root.resolve()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        encoded_name = str(relative).encode("utf-8", "surrogateescape")
        for token in FORBIDDEN:
            if token in encoded_name:
                findings.append(f"forbidden path token {token!r}: {relative}")
        if path.is_symlink():
            try:
                path.resolve(strict=True).relative_to(resolved_root)
            except (FileNotFoundError, ValueError):
                findings.append(f"escaping or broken symlink: {relative} -> {path.readlink()}")
            continue
        if not path.is_file():
            continue
        try:
            payload = path.read_bytes()
        except OSError as exc:
            findings.append(f"unreadable file: {relative}: {exc}")
            continue
        for token in FORBIDDEN:
            # Accepted 2026-09-05: Upload owns a bundled CPython runtime and
            # retained business code. A stdlib environment-variable symbol or
            # historical monitor label is not an external runtime dependency.
            location = relative.as_posix()
            prefix = "Contents/Resources/upload/machome-upload-component/_internal/"
            if token == b"PYTHONPATH" and location in (
                prefix + "base_library.zip", prefix + "libpython3.13.dylib"):
                continue
            if token == b"com.newnavnav." and location in (
                prefix + "business/scripts/upload_health_monitor.py",
                prefix + "business/scripts/test_upload_health_monitor.py"):
                continue
            if token == b"modules/bridge/UploadClient" and location == "Contents/Resources/source_manifest.json":
                continue  # names + SHA-256 metadata, never executable content
            if token in payload:
                findings.append(f"forbidden content token {token!r}: {relative}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    findings = scan(args.root)
    if findings:
        print("Upload runtime isolation check failed:", file=sys.stderr)
        for finding in findings:
            print(f"- {finding}", file=sys.stderr)
        return 1
    print(f"Upload runtime isolation check passed: {args.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
