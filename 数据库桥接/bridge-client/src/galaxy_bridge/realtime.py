"""实时行情订阅客户端（装饰器风格，贴近原生 SubscribeData 用法）。

用法：
    from galaxy_bridge import GalaxyBridgeClient, RealtimeSubscriber

    c = GalaxyBridgeClient("http://192.168.1.50:8900", api_key="...")
    sub = RealtimeSubscriber(c)

    @sub.register(code_list=["510300.SH", "510500.SH"], period="snapshot")
    def on_snap(data, period):
        print(data.security_code, data.last_price if hasattr(data,'last_price') else data)

    sub.run(block=True)     # 自动重连；Ctrl+C 退出
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable

import websocket

from .client import _unpack

log = logging.getLogger("bridge.realtime")

DEFAULT_PING_INTERVAL = 20


class RealtimeSubscriber:
    def __init__(self, client_or_url, api_key: str | None = None,
                 reconnect_delay: float = 3.0,
                 ping_interval: float = DEFAULT_PING_INTERVAL):
        if hasattr(client_or_url, "base_url"):          # GalaxyBridgeClient
            self._url = (client_or_url.base_url.replace("http", "ws", 1) + "/ws")
            self._api_key = client_or_url.api_key
        else:
            self._url = str(client_or_url).rstrip("/") + "/ws"
            self._api_key = api_key or ""
        self.reconnect_delay = reconnect_delay
        self.ping_interval = ping_interval
        self._lock = threading.RLock()
        self._callbacks: list[dict] = []                # {"periods":set,"codes":set,"fn":...}
        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._want_run = False

    # ---------------- 注册回调 ----------------

    def register(self, code_list: list[str], period: str | list[str],
                 once: bool = False) -> Callable:
        """装饰器：注册回调 fn(data, period)。

        data 为 SimpleNamespace（字段同手册附录快照/K线结构），
        period 为归一化名称（snapshot/min1/…）。
        """
        periods = {period} if isinstance(period, str) else set(period)
        codes = {"*"} if "*" in code_list else set(code_list)

        def decorator(fn: Callable) -> Callable:
            with self._lock:
                self._callbacks.append(
                    {"periods": periods, "codes": codes, "fn": fn, "once": once})
            return fn
        return decorator

    # ---------------- 内部 ----------------

    def _dispatch(self, payload: dict):
        if payload.get("topic") != "md":
            return
        period = payload.get("period", "")
        code = payload.get("code", "")
        data = _unpack(payload.get("data", {}))
        with self._lock:
            targets = [cb for cb in self._callbacks
                       if period in cb["periods"]
                       and ("*" in cb["codes"] or code in cb["codes"])]
        for cb in targets:
            try:
                cb["fn"](data, period)
            except Exception:
                log.exception("回调执行异常 period=%s code=%s", period, code)

    def _on_open(self, ws):
        log.info("WS 已连接 %s", self._url)
        periods = sorted({p for cb in self._callbacks for p in cb["periods"]})
        codes = sorted({c for cb in self._callbacks for c in cb["codes"]})
        ws.send(json.dumps({"action": "filter",
                            "periods": periods,
                            "codes": ["*"] if "*" in codes else codes},
                           ensure_ascii=False))
        # 若服务端尚未订阅这些代码，顺带补一枪（幂等）
        subs: dict[str, list[str]] = {}
        for cb in self._callbacks:
            for p in cb["periods"]:
                subs.setdefault(p, [])
                for c in cb["codes"]:
                    if c != "*" and c not in subs[p]:
                        subs[p].append(c)
        if subs:
            ws.send(json.dumps({"action": "sub",
                                "subs": [{"period": p, "code_list": cs}
                                         for p, cs in subs.items()]},
                               ensure_ascii=False))

    def _on_message(self, ws, raw):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if msg.get("topic") == "welcome":
            log.info("桥接服务欢迎消息 conn_id=%s", msg.get("conn_id"))
            return
        self._dispatch(msg)

    def _on_close(self, ws, code, reason):
        log.warning("WS 断开 code=%s reason=%s", code, reason)

    def _on_error(self, ws, err):
        log.debug("WS error: %s", err)

    def _build_url(self) -> str:
        url = self._url
        if self._api_key:
            url += ("&" if "?" in url else "?") + "api_key=" + self._api_key
        return url

    def _run_forever(self):
        url = self._build_url()
        while not self._stop.is_set():
            self._ws = websocket.WebSocketApp(
                url, on_open=self._on_open, on_message=self._on_message,
                on_close=self._on_close, on_error=self._on_error)
            try:
                self._ws.run_forever(ping_interval=self.ping_interval,
                                     ping_payload='{"action":"ping"}')
            except Exception:
                log.exception("WS run_forever 异常")
            if self._stop.is_set():
                break
            log.info("%.1fs 后重连...", self.reconnect_delay)
            self._stop.wait(self.reconnect_delay)

    def _spawn(self):
        self._thread = threading.Thread(target=self._run_forever,
                                        name="galaxy-rt", daemon=True)
        self._thread.start()

    # ---------------- 对外 ----------------

    def start(self):
        """后台线程启动接收循环。"""
        with self._lock:
            if self._want_run:
                return
            self._want_run = True
            self._stop.clear()
        self._spawn()

    def run(self, block: bool = True):
        """启动并可选择阻塞当前线程。"""
        self.start()
        if block:
            try:
                while self._thread and self._thread.is_alive():
                    self._thread.join(1)
            except KeyboardInterrupt:
                log.info("收到 Ctrl+C，退出")
                self.close()

    def close(self):
        self._want_run = False
        self._stop.set()
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass
