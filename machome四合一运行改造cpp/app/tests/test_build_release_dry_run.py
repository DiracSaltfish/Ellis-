#!/usr/bin/env python3
"""Regression: build-release --dry-run must not invoke tools or create outputs."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


def main() -> int:
    app = Path(__file__).resolve().parents[1]
    release_script = (app / "scripts" / "build-release.sh").read_text(
        encoding="utf-8"
    )
    assert '"$PYTHON_BIN_RESOLVED" -m json.tool' in release_script
    assert 'plutil -lint "$STAGED_APP/Contents/Resources/build_manifest.json"' not in release_script
    assert '/bin/rmdir "$STAGE_ROOT"\nSTAGE_ROOT=' in release_script
    assert 'Contents/Helpers/machome-webull-browser-helper' in release_script
    assert 'Contents/Helpers/machome-wind-probe-helper' in release_script
    with tempfile.TemporaryDirectory(prefix="machome-dry-run-") as raw:
        root = Path(raw)
        build = root / "never-created-build"
        dist = root / "never-created-dist"
        marker = root / "tool-was-executed"
        fake = root / "side-effect-tool"
        fake.write_text(
            "#!/bin/sh\n"
            'touch "$MACHOME_TEST_TOOL_MARKER"\n'
            "exit 77\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        env = os.environ.copy()
        env["MACHOME_TEST_TOOL_MARKER"] = str(marker)
        completed = subprocess.run(
            [
                "/bin/bash",
                str(app / "scripts" / "build-release.sh"),
                "--source-dir", str(app),
                "--build-dir", str(build),
                "--dist-dir", str(dist),
                "--qt-prefix", str(root / "unused-qt"),
                "--cmake", str(fake),
                "--ctest", str(fake),
                "--macdeployqt", str(fake),
                "--codesign", str(fake),
                "--ditto", str(fake),
                "--jobs", "1",
                "--dry-run",
            ],
            cwd=app,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout
        assert "不调用 CMake/CTest" in completed.stdout
        assert "-executable=" in completed.stdout
        assert "Contents/Helpers/machome-webull-browser-helper" in completed.stdout
        assert "Contents/Helpers/machome-wind-probe-helper" in completed.stdout
        assert not marker.exists(), completed.stdout
        assert not build.exists(), completed.stdout
        assert not dist.exists(), completed.stdout
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
