#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys


FORBIDDEN = (
    b"/Users/ellis/Desktop/ETF\xe4\xba\xa4\xe5\x89\xb2/WebullData",
    b"/Users/ellis/WebullLV2Gateway",
    b"com.ellis.webull-lv2-gateway",
    b"webull_control_runner.py",
    b"PYTHONPATH",
)


def scan(root: Path) -> list[str]:
    findings: list[str] = []
    resolved = root.resolve()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            try:
                path.resolve(strict=True).relative_to(resolved)
            except (FileNotFoundError, ValueError):
                findings.append(f"escaping or broken symlink: {relative}")
            continue
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            findings.append(f"unreadable: {relative}: {exc}")
            continue
        for token in FORBIDDEN:
            # CPython is confined to the separately owned Upload component.
            prefix = "Contents/Resources/upload/machome-upload-component/_internal/"
            if token == b"PYTHONPATH" and relative.as_posix() in (
                prefix + "base_library.zip", prefix + "libpython3.13.dylib"):
                continue
            if token in data or token in str(relative).encode("utf-8"):
                findings.append(f"forbidden Webull runtime token {token!r}: {relative}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    findings = scan(args.root)
    if findings:
        print("Webull runtime isolation check failed:", file=sys.stderr)
        print("\n".join("- " + item for item in findings), file=sys.stderr)
        return 1
    print(f"Webull runtime isolation check passed: {args.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
