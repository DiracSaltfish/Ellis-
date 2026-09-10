#!/usr/bin/env python3
"""Run each legacy-uploader regression file in its own Python process."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-scripts", default="/Users/ellis/newnavnav/scripts")
    parser.add_argument("--bridge-binary", default="")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    scripts = root / "scripts"
    source_scripts = Path(args.source_scripts).expanduser().resolve()
    if not source_scripts.is_dir():
        parser.error(f"source script directory does not exist: {source_scripts}")

    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = os.pathsep.join((
        str(scripts), str(source_scripts), environment.get("PYTHONPATH", "")
    )).rstrip(os.pathsep)

    tests = sorted((root / "tests").glob("test_*.py"))
    failures: list[str] = []
    for test in tests:
        command = [sys.executable, str(test), "-q"]
        if test.name == "test_shared_client.py" and args.bridge_binary:
            command.extend(("--bridge-binary", str(Path(args.bridge_binary).resolve())))
        print(f"\n=== {test.name} ===", flush=True)
        result = subprocess.run(command, env=environment, check=False)
        if result.returncode:
            failures.append(test.name)
    if failures:
        print("FAILED: " + ", ".join(failures), file=sys.stderr)
        return 1
    print(f"\nPASS: {len(tests)} isolated regression files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
