#!/usr/bin/env python3
"""校验 AmazingData wheel 与当前 Python 版本匹配。

用法：
    python tools/check_wheels.py                 # 检查当前解释器
    python tools/check_wheels.py --list          # 列出全部可用 wheel
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WHL_DIR = ROOT / "AmazingData"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    wheels = sorted(WHL_DIR.glob("AmazingData-*.whl")) if WHL_DIR.exists() else []
    tags = sorted(re.search(r"-(cp\d{2})-", w.name).group(1) for w in wheels)

    if args.list:
        for w in wheels:
            print(w.name)
        return

    vi = sys.version_info
    mine = f"cp{vi.major}{vi.minor}"
    print(f"当前 Python: {vi.major}.{vi.minor}.{vi.micro} ({sys.platform})")
    if not wheels:
        print(f"[FAIL] 未找到 whl 目录: {WHL_DIR}")
        sys.exit(1)
    print("可用 wheel 标签:", ", ".join(tags))
    if mine in tags and sys.platform == "win32":
        print(f"[OK] 在 Windows 上请安装: pip install AmazingData-1.1.9-{mine}-none-any.whl")
    elif mine in tags:
        print(f"[OK] 版本标签 {mine} 存在 —— 但注意 tgw 原生库不支持 macOS！"
              "本客户端机不需要安装这些 wheel。")
    else:
        print(f"[FAIL] 没有 {mine} 对应的 wheel。建议改用 Python 3.12/3.13 x64。")


if __name__ == "__main__":
    main()
