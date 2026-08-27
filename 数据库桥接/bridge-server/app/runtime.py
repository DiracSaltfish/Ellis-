"""银河 SDK 运行时：登录、重连、实例池、通用方法分发、限流。

设计要点（详见 docs/SDK调研报告.md 第 9 节）：
1. 绕开 ad.login —— 其失败路径会调用 exit() 直接杀死进程；这里直接构造
   tgw.Cfg 并调用 tgw.Login()，逻辑与 ad.login 内部 set_cfg 完全等价，
   但重试策略/日志/错误处理全部由本服务自管。
2. BaseData / InfoData / MarketData / DownloadInfoData 惰性创建；
   MarketData 需要交易日历，首次使用时获取并每日刷新。
3. SDK 线程安全性未知 → 信号量限流 + 登录互斥锁。
4. 任一疑似连接类异常 → 标记离线，后台看门狗按配置间隔自动重连。
"""
from __future__ import annotations

import datetime as _dt
import threading
import time
import logging
from typing import Any, Callable

import pandas as pd

from .config import AppConfig

log = logging.getLogger("bridge.runtime")


class BridgeError(RuntimeError):
    def __init__(self, message: str, err_code: int = -1):
        super().__init__(message)
        self.err_code = err_code
        self.message = message


class NotConfiguredError(BridgeError):
    pass


_CONN_HINTS = ("login", "logon", "connect", "disconnect", "session", "logout",
               "network", "socket", "timeout", "uninitialized", "init")

# 连接类关键词粗筛，命中才触发“离线→自动重连”，普通参数错误不折腾登录态
def _looks_like_conn_error(e: Exception) -> bool:
    s = f"{type(e).__name__}: {e}".lower()
    return any(k in s for k in _CONN_HINTS)


class TgwLogSpi:
    """把 tgw 引擎日志桥接到 Python logging。"""

    def __init__(self, logger: logging.Logger):
        self._lg = logger

    def OnLog(self, level, log_msg, length):  # noqa: N802
        lv = {0: 10, 1: 10, 2: 20, 3: 30}.get(int(level) if level is not None else 2, 20)
        self._lg.log(lv, "[tgw] %s", log_msg)

    def OnLogon(self, data):  # noqa: N802
        self._lg.info("[tgw] logon: %s", data)


class AdRuntime:
    GROUP_FACTORIES: dict[str, str] = {
        "base": "_base",
        "info": "_info",
        "market": "_market",
        "download": "_download",
    }

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self._lock = threading.RLock()
        self._sem = threading.BoundedSemaphore(cfg.bridge.max_concurrent)
        self._logged_in = False
        self._login_error: str | None = None
        self._login_ts: float = 0.0
        self._started_at = time.time()
        self._stop = threading.Event()
        self._watchdog: threading.Thread | None = None

        self._tgw = None
        self._ad = None
        self._base = None
        self._info = None
        self._download = None
        self._market = None
        self._calendar_df: pd.DataFrame | None = None
        self._calendar_day: str | None = None

        self._import_sdk()

    # ---------------- SDK 导入与登录 ----------------

    def _import_sdk(self):
        import tgw as _tgw            # noqa: PLC0415  (Windows x64 才可用)
        import AmazingData as _ad     # noqa: PLC0415
        self._tgw = _tgw
        self._ad = _ad
        try:
            _tgw.SetLogSpi(TgwLogSpi(log))   # interface 层封装，安全
        except Exception as e:               # pragma: no cover
            log.warning("SetLogSpi 失败（不影响功能）: %s", e)

    @property
    def tgw(self):
        return self._tgw

    def logged_in(self) -> bool:
        return self._logged_in

    def status(self) -> dict:
        miss = self.cfg.missing_login_fields()
        return {
            "service": "galaxy-bridge",
            "sdk_version_ad": getattr(self._ad, "__version__", "?"),
            "logged_in": self._logged_in,
            "login_error": self._login_error,
            "account": self.cfg.galaxy.masked_account,
            "server": f"{self.cfg.galaxy.host}:{self.cfg.galaxy.port}"
                      if not miss else "(未配置)",
            "api_mode": self.cfg.galaxy.api_mode,
            "uptime_s": round(time.time() - self._started_at, 1),
            "missing_config": miss,
        }

    def _build_tgw_cfg(self):
        g = self.cfg.galaxy
        if not g.username.startswith("tgw_"):
            raise BridgeError("username 必须以 tgw_ 开头（银河侧规定）")
        c = self._tgw.Cfg()
        c.username = g.username
        c.password = g.password
        c.server_vip = g.host
        c.server_port = int(g.port)
        try:
            c.force_logout = bool(g.force_logout)
        except Exception:
            pass
        # 托管机房模式参数：镜像 ad.login 内部默认值（互联网模式不涉及）
        if g.api_mode == "kColocationMode":
            defaults = {
                "channel_mode": self._tgw.ColocatChannelMode.kTCP,
                "qtcp_channel_thread": 8,
                "qtcp_max_req_cnt": 3000,
                "qtcp_req_time_out": 10,
                "enable_order_book": 0,
                "entry_size": 20,
                "order_queue_size": 50,
            }
            holder = getattr(c, "coloca_cfg", None)
            for k, v in defaults.items():
                try:
                    if holder is not None and hasattr(holder, k):
                        setattr(holder, k, v)
                    elif hasattr(c, k):
                        setattr(c, k, v)
                except Exception:
                    pass
        return c

    def login(self) -> dict:
        """同步登录（可重复调用；已登录则直接返回）。"""
        with self._lock:
            if self._logged_in:
                return {"ok": True, "already": True}
            miss = self.cfg.missing_login_fields()
            if miss:
                raise NotConfiguredError(f"登录信息未配置: {', '.join(miss)}")
            mode_name = self.cfg.galaxy.api_mode
            mode = (self._tgw.ApiMode.kColocationMode
                    if mode_name == "kColocationMode"
                    else self._tgw.ApiMode.kInternetMode)
            cfgt = self._build_tgw_cfg()
            ok = bool(self._tgw.Login(cfgt, mode))
            if not ok:
                self._logged_in = False
                self._login_error = f"tgw.Login 失败 ({mode_name})，检查账号/IP/端口/网络"
                raise BridgeError(self._login_error, err_code=-2)
            self._logged_in = True
            self._login_error = None
            self._login_ts = time.time()
            # 重置实例缓存（重连后旧实例可能失效）
            self._base = self._info = self._download = self._market = None
            self._calendar_df = None
            log.info("银河 SDK 登录成功（%s@%s:%s）",
                     self.cfg.galaxy.masked_account,
                     self.cfg.galaxy.host, self.cfg.galaxy.port)
            return {"ok": True}

    def logout(self):
        with self._lock:
            try:
                self._tgw.Close()
            except Exception:
                pass
            self._logged_in = False
            self._base = self._info = self._download = self._market = None
            log.info("已登出银河 SDK")

    def relogin(self) -> dict:
        with self._lock:
            try:
                self._tgw.Close()
            except Exception:
                pass
            self._logged_in = False
        return self.login()

    def start_watchdog(self):
        if self._watchdog and self._watchdog.is_alive():
            return
        def _loop():
            iv = self.cfg.bridge.reconnect_interval
            while not self._stop.wait(iv if not self._logged_in else 10):
                if self._logged_in:
                    continue
                try:
                    self.login()
                except Exception as e:
                    log.warning("后台重连失败：%s", e)
        self._watchdog = threading.Thread(target=_loop, name="relogin-watchdog",
                                          daemon=True)
        self._watchdog.start()

    def shutdown(self):
        self._stop.set()

    # ---------------- 实例池 ----------------

    def _inst_market(self):
        with self._lock:
            today = _dt.date.today().isoformat()
            if self._market is None or self._calendar_day != today:
                cal = self.instance_for("base").get_calendar()
                self._calendar_df = cal
                self._calendar_day = today
                self._market = self._ad.MarketData(cal)
            return self._market

    def instance_for(self, group: str):
        if group not in self.GROUP_FACTORIES:
            raise BridgeError(f"未知分组 '{group}'，可选: {list(self.GROUP_FACTORIES)}")
        attr = self.GROUP_FACTORIES[group]
        inst = getattr(self, attr)
        if inst is not None:
            return inst
        with self._lock:
            if getattr(self, attr) is None:
                if group == "base":
                    self._base = self._ad.BaseData()
                elif group == "info":
                    self._info = self._ad.InfoData()
                elif group == "market":
                    self._inst_market()
                elif group == "download":
                    self._download = self._ad.DownloadInfoData()
            return getattr(self, attr)

    # ---------------- 通用分发 ----------------

    def resolve_method(self, group: str, method: str) -> Callable:
        if method.startswith("_"):
            raise BridgeError("非法方法名")
        inst = self.instance_for(group)
        fn = getattr(inst, method, None)
        if not callable(fn):
            raise BridgeError(f"{group}.{method} 不存在；可用方法见 /api/v1/meta/methods")
        return fn

    def list_methods(self) -> dict:
        out = {}
        for group in self.GROUP_FACTORIES:
            names: list[str] = []
            try:
                inst = self.instance_for(group)
                names = sorted(n for n in dir(inst)
                               if not n.startswith("_") and callable(getattr(inst, n)))
            except Exception as e:
                names = [f"<需登录后可用: {e}>"]
            out[group] = names
        return out

    def _normalize_kwargs(self, group: str, method: str, kwargs: dict) -> dict:
        """对客户端友好的参数归一化：
        market.query_kline 的 period 允许传 'min5'/'day' 等名称，
        自动翻译为 ad.constant.Period 枚举值；数字原样通过。"""
        if group == "market" and method == "query_kline":
            p = kwargs.get("period")
            if isinstance(p, str):
                try:
                    kwargs["period"] = int(
                        getattr(self._ad.constant.Period, p.strip().lower()).value)
                except AttributeError:
                    valid = "min1,min3,min5,min10,min15,min30,min60,min120," \
                            "day,week,month,season,year"
                    raise BridgeError(f"period '{p}' 无效；可用: {valid}")
        return kwargs

    def call(self, group: str, method: str,
             args: list | tuple = (), kwargs: dict | None = None) -> Any:
        kwargs = self._normalize_kwargs(group, method, dict(kwargs or {}))
        # local_path 收口：客户端无需知道服务器路径
        if "local_path" in kwargs:
            lp = kwargs.get("local_path")
            root = self.cfg.cache.root
            if (lp is None or lp == "") and root:
                sep = "\\" if "\\" in root or ":" in root else "/"
                kwargs["local_path"] = root if root.endswith(("\\", "/")) else root + sep
        fn = self.resolve_method(group, method)
        with self._sem:
            t0 = time.perf_counter()
            try:
                self.ensure_logged_in()
                result = fn(*args, **kwargs)
                return result
            except BridgeError:
                raise
            except Exception as e:
                if _looks_like_conn_error(e):
                    self._mark_down(e)
                log.exception("调用 %s.%s 失败", group, method)
                raise BridgeError(f"{group}.{method} 调用失败: {type(e).__name__}: {e}") from e
            finally:
                log.debug("%s.%s 耗时 %.0fms", group, method,
                          (time.perf_counter() - t0) * 1000)

    def ensure_logged_in(self):
        if not self._logged_in:
            self.login()

    def _mark_down(self, e: Exception):
        with self._lock:
            if self._logged_in:
                log.error("检测到连接类错误，标记离线并等待看门狗重连：%s: %s",
                          type(e).__name__, e)
            self._logged_in = False
            self._login_error = f"{type(e).__name__}: {e}"
