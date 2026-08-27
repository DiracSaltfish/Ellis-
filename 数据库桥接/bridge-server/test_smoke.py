"""无凭据环境自检：验证 wheel 安装、配置文件、目录结构是否就绪。

用法（Windows 桥接机上）：python test_smoke.py [--config config.ini] [--ping]
"""
from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

OK, WARN, BAD = "[ OK ]", "[WARN]", "[FAIL]"
fails = []


def check(name: str, cond: bool, detail: str = "", warn_only: bool = False):
    tag = OK if cond else (WARN if warn_only else BAD)
    print(f"{tag} {name}" + (f" —— {detail}" if detail else ""))
    if not cond and not warn_only:
        fails.append(name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(Path(__file__).parent / "config.ini"))
    ap.add_argument("--ping", action="store_true",
                    help="尝试 TCP 连通银河服务器（需已填写 host/port）")
    args = ap.parse_args()

    print("=" * 60)
    print("galaxy-bridge 自检")
    print("=" * 60)

    # 1. Python 版本
    vi = sys.version_info
    check("Python 版本", (3, 10) <= vi[:2] <= (3, 13) and sys.maxsize > 2**32,
          f"{vi.major}.{vi.minor}.{vi.micro} {'x64' if sys.maxsize > 2**32 else 'x86(不支持!)'}")

    # 2. 通用依赖
    for mod in ["fastapi", "uvicorn", "pandas", "numpy", "pydantic"]:
        try:
            m = __import__(mod)
            check(f"依赖 {mod}", True, getattr(m, "__version__", "?"))
        except Exception as e:
            check(f"依赖 {mod}", False, str(e))

    # 3. 银河 SDK
    try:
        import tgw
        check("tgw 导入", True,
              f"平台={getattr(tgw, 'os_info', '?')} "
              f"py={sys.version_info.major}{sys.version_info.minor}")
        try:
            v = tgw.GetVersion()
            check("tgw GetVersion", True, str(v))
        except Exception as e:
            check("tgw GetVersion", False, f"{e}（登录后才能调用，可忽略）", warn_only=True)
    except Exception as e:
        check("tgw 导入", False, str(e))

    try:
        import AmazingData as ad
        check("AmazingData 导入", True, f"v{getattr(ad, '__version__', '?')}")
        for cls in ["BaseData", "InfoData", "MarketData", "SubscribeData",
                    "DownloadInfoData"]:
            check(f"AmazingData.{cls}", hasattr(ad, cls), "")
    except Exception as e:
        check("AmazingData 导入", False, str(e))

    # 4. 配置文件
    cfg_path = Path(args.config)
    check("配置文件存在", cfg_path.exists(), str(cfg_path))
    if cfg_path.exists():
        sys.path.insert(0, str(Path(__file__).parent))
        from app.config import load_config
        cfg = load_config(cfg_path)
        miss = cfg.missing_login_fields()
        check("[galaxy] 登录信息已填写", not miss,
              ("缺少: " + ", ".join(miss)) if miss else
              f"{cfg.galaxy.masked_account}@{cfg.galaxy.host}:{cfg.galaxy.port}",
              warn_only=True)
        check("[bridge] api_key 已设置", bool(cfg.bridge.api_key) and
              cfg.bridge.api_key != "CHANGE_ME_TO_A_RANDOM_STRING",
              "建议设置随机令牌防止局域网内未授权访问", warn_only=True)

        # 5. 缓存目录
        if cfg.cache.root:
            p = Path(cfg.cache.root)
            check("[local_cache] root 目录", p.exists(),
                  f"{p}" + ("" if p.exists() else "（首次使用会自动创建，也可手动建）"),
                  warn_only=True)
            try:
                p.mkdir(parents=True, exist_ok=True)
                probe = p / ".write_test"
                probe.write_text("ok")
                probe.unlink()
                check("[local_cache] 可写", True, str(p))
            except Exception as e:
                check("[local_cache] 可写", False, str(e))

        # 6. 网络连通（可选）
        if args.ping and not miss:
            try:
                s = socket.create_connection((cfg.galaxy.host, cfg.galaxy.port),
                                             timeout=5)
                s.close()
                check("银河服务器 TCP 可达", True,
                      f"{cfg.galaxy.host}:{cfg.galaxy.port}")
            except Exception as e:
                check("银河服务器 TCP 可达", False,
                      f"{cfg.galaxy.host}:{cfg.galaxy.port}: {e}")

    print("=" * 60)
    if fails:
        print(f"[FAIL] 有 {len(fails)} 项未通过：{', '.join(fails)}")
        sys.exit(1)
    print("[PASS] 自检通过。编辑 config.ini 填写账号后运行 run_server.bat 即可。")


if __name__ == "__main__":
    main()
