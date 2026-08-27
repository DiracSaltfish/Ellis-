"""galaxy-bridge 服务入口（Windows x64）。

用法：
    python main.py [--config config.ini]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.config import load_config
from app.hub import SubscriptionPipeline, WsHub
from app.routes import build_router
from app.runtime import AdRuntime
from app.serialize import pack


def _lan_ips() -> list[str]:
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips or ["127.0.0.1"]


def setup_logging(cfg):
    fmt = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if cfg.bridge.log_file:
        p = Path(cfg.bridge.log_file)
        if not p.is_absolute():
            p = cfg.base_dir / p
        p.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(p, encoding="utf-8"))
    logging.basicConfig(level=getattr(logging, cfg.bridge.log_level, logging.INFO),
                        format=fmt, handlers=handlers)
    # 收敛三方噪音
    for noisy in ("uvicorn.access", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def build_app(cfg) -> FastAPI:
    runtime = AdRuntime(cfg)
    hub = WsHub()
    pipeline = SubscriptionPipeline(runtime, hub, pack)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.bind_loop(asyncio.get_running_loop())
        runtime.start_watchdog()

        def first_login():
            try:
                runtime.login()
            except Exception as e:
                logging.getLogger("bridge").error("初始登录失败：%s（服务保持运行，"
                                                  "将按间隔自动重连；可稍后 POST /admin/login）", e)
        import threading
        threading.Thread(target=first_login, name="first-login",
                         daemon=True).start()
        yield
        runtime.shutdown()

    app = FastAPI(title="galaxy-bridge",
                  description="银河星耀 AmazingData 局域网桥接服务",
                  version="1.0.0", lifespan=lifespan)
    pipeline.init_market_map()
    app.include_router(build_router(runtime, hub, pipeline,
                                    cfg.bridge.api_key,
                                    cfg.bridge.subscribe))
    app.state.runtime = runtime
    app.state.hub = hub
    app.state.pipeline = pipeline
    return app


def main():
    ap = argparse.ArgumentParser(description="银河星耀 局域网桥接服务")
    ap.add_argument("--config", default=str(Path(__file__).parent / "config.ini"))
    args = ap.parse_args()

    cfg = load_config(args.config)
    setup_logging(cfg)
    log = logging.getLogger("bridge")

    miss = cfg.missing_login_fields()
    log.info("=" * 60)
    log.info("galaxy-bridge 启动")
    if miss:
        log.warning("登录信息未配置项：%s —— 请编辑 %s 的 [galaxy] 节",
                    ", ".join(miss), args.config)
    else:
        log.info("目标服务器 %s:%s（账号 %s，模式 %s）",
                 cfg.galaxy.host, cfg.galaxy.port,
                 cfg.galaxy.masked_account, cfg.galaxy.api_mode)
    log.info("局域网客户端请连接：http://<以下IP>:%s  （Mac 侧 BRIDGE_URL）",
             cfg.bridge.listen_port)
    for ip in _lan_ips():
        log.info("  http://%s:%s   ws://%s:%s/ws", ip, cfg.bridge.listen_port,
                 ip, cfg.bridge.listen_port)
    log.info("=" * 60)

    import uvicorn
    uvicorn.run(build_app(cfg), host=cfg.bridge.listen_host,
                port=cfg.bridge.listen_port, log_config=None)


if __name__ == "__main__":
    main()
