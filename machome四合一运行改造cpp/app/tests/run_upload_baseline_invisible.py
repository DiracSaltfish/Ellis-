#!/usr/bin/env python3
"""Run Upload tests while macOS denies every read of the former source tree."""

from __future__ import annotations

import argparse
import subprocess


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-binary", required=True)
    parser.add_argument("--deny-root", required=True)
    args = parser.parse_args()
    escaped = args.deny_root.replace("\\", "\\\\").replace('"', '\\"')
    profile = f'(version 1) (allow default) (deny file-read* (subpath "{escaped}"))'
    completed = subprocess.run(
        ["/usr/bin/sandbox-exec", "-p", profile, args.test_binary],
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
