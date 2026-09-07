from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile


def run(scanner: Path, root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(scanner), "--root", str(root)],
                          capture_output=True, text=True, check=False)


def main() -> int:
    scanner = Path(sys.argv[1]).resolve()
    bundle = Path(sys.argv[2]).resolve()
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "safe.py").write_text("# bundled stdlib CDP helper\n", encoding="utf-8")
        assert run(scanner, root).returncode == 0
        (root / "bad.txt").write_text("PYTHONPATH=/retired", encoding="utf-8")
        rejected = run(scanner, root)
        assert rejected.returncode != 0 and "forbidden Webull runtime token" in rejected.stderr
    completed = run(scanner, bundle)
    if completed.returncode:
        print(completed.stderr, file=sys.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
