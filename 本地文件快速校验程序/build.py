"""在当前操作系统打包本应用；Windows 与 macOS 请分别在对应系统执行。"""

from __future__ import annotations

import os
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent
    # 不污染或依赖用户主目录下的 PyInstaller 缓存；也方便受限环境打包。
    os.environ.setdefault("PYINSTALLER_CONFIG_DIR", str(root / "build" / "pyinstaller-config"))
    try:
        import PyInstaller.__main__
    except ImportError as exc:
        raise SystemExit("请先执行：python -m pip install -r requirements.txt") from exc

    PyInstaller.__main__.run(
        [
            "--noconfirm",
            "--windowed",
            "--name",
            "ArchiveHashCheck",
            "--distpath",
            str(root / "dist"),
            "--workpath",
            str(root / "build"),
            "--specpath",
            str(root / "build"),
            str(root / "main.py"),
        ]
    )


if __name__ == "__main__":
    main()
