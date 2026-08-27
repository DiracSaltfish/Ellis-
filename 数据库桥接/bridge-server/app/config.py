"""配置加载：config.ini + 环境变量覆盖。

环境变量命名：GALAXY_BRIDGE_<节大写>_<键大写>，例如
    GALAXY_BRIDGE_GALAXY_PASSWORD / GALAXY_BRIDGE_BRIDGE_API_KEY
"""
from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path


def _truthy(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y")


@dataclass
class GalaxyCfg:
    username: str = ""
    password: str = ""
    host: str = ""
    port: int = 0
    api_mode: str = "kInternetMode"          # kInternetMode / kColocationMode
    force_logout: bool = True

    @property
    def masked_account(self) -> str:
        u = self.username or ""
        return (u[:4] + "****") if len(u) > 4 else "****"


@dataclass
class CacheCfg:
    root: str = ""                            # AmazingData hdf5 本地缓存根目录


@dataclass
class BridgeCfg:
    listen_host: str = "0.0.0.0"
    listen_port: int = 8900
    api_key: str = ""                         # 留空 = 不鉴权
    max_concurrent: int = 2
    reconnect_interval: int = 60
    log_level: str = "INFO"
    log_file: str = ""
    subscribe: str = ""                       # 可选 JSON，启动后自动订阅


@dataclass
class AppConfig:
    base_dir: Path
    galaxy: GalaxyCfg = field(default_factory=GalaxyCfg)
    cache: CacheCfg = field(default_factory=CacheCfg)
    bridge: BridgeCfg = field(default_factory=BridgeCfg)

    def missing_login_fields(self) -> list[str]:
        miss = []
        g = self.galaxy
        if not g.username or "REPLACE_ME" in g.username:
            miss.append("galaxy.username")
        if not g.password or "REPLACE_ME" in g.password:
            miss.append("galaxy.password")
        if not g.host or "REPLACE_ME" in g.host:
            miss.append("galaxy.host")
        if not g.port:
            miss.append("galaxy.port")
        return miss


def _env(section: str, key: str):
    return os.environ.get(f"GALAXY_BRIDGE_{section}_{key}".upper())


def load_config(path: str | Path) -> AppConfig:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cp.read(path, encoding="utf-8")

    def get(sec, key, default=None):
        v = cp.get(sec, key, fallback=default)
        ev = _env(sec, key)
        return ev if ev is not None else v

    cfg = AppConfig(base_dir=path.parent)
    cfg.galaxy = GalaxyCfg(
        username=str(get("galaxy", "username", "") or "").strip(),
        password=str(get("galaxy", "password", "") or ""),
        host=str(get("galaxy", "host", "") or "").strip(),
        port=int(get("galaxy", "port", 0) or 0),
        api_mode=str(get("galaxy", "api_mode", "kInternetMode") or "kInternetMode").strip(),
        force_logout=_truthy(get("galaxy", "force_logout", "true") or "true"),
    )
    cfg.cache = CacheCfg(root=str(get("local_cache", "root", "") or "").strip())
    cfg.bridge = BridgeCfg(
        listen_host=str(get("bridge", "listen_host", "0.0.0.0") or "0.0.0.0").strip(),
        listen_port=int(get("bridge", "listen_port", 8900) or 8900),
        api_key=str(get("bridge", "api_key", "") or "").strip(),
        max_concurrent=max(1, int(get("bridge", "max_concurrent", 2) or 2)),
        reconnect_interval=max(5, int(get("bridge", "reconnect_interval", 60) or 60)),
        log_level=str(get("bridge", "log_level", "INFO") or "INFO").upper(),
        log_file=str(get("bridge", "log_file", "") or "").strip(),
        subscribe=str(get("bridge", "subscribe", "") or "").strip(),
    )
    return cfg
