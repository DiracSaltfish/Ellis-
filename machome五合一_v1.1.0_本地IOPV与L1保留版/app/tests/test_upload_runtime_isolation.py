#!/usr/bin/env python3
from pathlib import Path
import subprocess
import sys
import tempfile


def run(scanner: Path, root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(scanner), "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
    )


def main() -> int:
    scanner = Path(sys.argv[1]).resolve()
    bundle = Path(sys.argv[2]).resolve()
    with tempfile.TemporaryDirectory(prefix="machome-upload-isolation-") as raw:
        root = Path(raw)
        (root / "safe.json").write_text('{"engine":"native"}', encoding="utf-8")
        assert run(scanner, root).returncode == 0
        (root / "unsafe.bin").write_bytes(b"prefix /Users/ellis/newnavnav suffix")
        rejected = run(scanner, root)
        assert rejected.returncode != 0
        assert "forbidden content token" in rejected.stderr
    packaged = run(scanner, bundle)
    if packaged.returncode:
        print(packaged.stderr, file=sys.stderr)
        return packaged.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
