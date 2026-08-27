"""WebSocket 枢纽 + 动态订阅管线。

关键机制（源自对 tgw 源码的分析，见 docs/方案设计.md 第 3.2 节）：
- tgw 全局推送适配器 TmpPushSpi 在【第一次】tgw.Subscribe(..., push_spi) 时锁定内部 SPI；
  之后所有订阅/退订都复用同一 SPI。因此桥接服务使用自研 BridgePushSpi 作为唯一内部 SPI，
  直接驱动 tgw.Subscribe / tgw.UnSubscribe 实现完全动态的增删订阅，无需重启管线。
- TmpPushSpi 已把 C++ 原生结构转换为纯 Python 包装对象（TGWSnapshotL1/TGWKLine 等）
  再回调内部 SPI，本层只需序列化与分发。
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import threading
from typing import Any

log = logging.getLogger("bridge.hub")

# period 名称 -> tgw.SubscribeDataType 成员名（仅列可推送类型；日/周/月线无推送，走 REST 轮询）
PERIOD_TO_DT = {
    "snapshot": "kSnapshot",
    "snapshotindex": "kIndexSnapshot",
    "snapshotfuture": "kFutureSnapshot",
    "snapshotoption": "kOptionSnapshot",
    "snapshothkt": "kHKTSnapshot",
    "min1": "k1MinKline", "min3": "k3MinKline", "min5": "k5MinKline",
    "min10": "k10MinKline", "min15": "k15MinKline", "min30": "k30MinKline",
    "min60": "k60MinKline", "min120": "k120MinKline",
}
DT_VALUE_TO_PERIOD: dict[int, str] = {}   # 运行时反向填充


def market_from_code(code: str) -> tuple[str, str]:
    """'510300.SH' -> ('510300', MarketType 后缀)。"""
    if "." not in code:
        raise ValueError(f"代码必须带市场后缀（如 510300.SH / 000001.SZ / 899050.BJ）：{code}")
    sym, suf = code.rsplit(".", 1)
    return sym.strip(), suf.strip().upper()


class _BridgePushSpi:
    """作为 tgw 全局唯一内部推送 SPI；把 SDK 回调转为 hub 广播。

    方法签名与 tmp_spi.TmpPushSpi 对内部 SPI 的调用约定一致：
        OnMDSnapshot(snapshot_obj, err) / OnKLine(kline_obj, kline_type, err) ...
    """

    def __init__(self, on_item):
        self._on_item = on_item   # callable(period:str, obj) -> None

    def _emit(self, period: str, obj):
        try:
            self._on_item(period, obj)
        except Exception:
            log.exception("推送处理失败")

    def OnMDSnapshot(self, data, err):          self._emit("snapshot", data)
    def OnMDIndexSnapshot(self, data, err):     self._emit("snapshotindex", data)
    def OnMDFutureSnapshot(self, data, err):    self._emit("snapshotfuture", data)
    def OnMDOptionSnapshot(self, data, err):    self._emit("snapshotoption", data)
    def OnMDHKTSnapshot(self, data, err):       self._emit("snapshothkt", data)
    def OnMDAfterHourFixedPriceSnapshot(self, d, e): self._emit("afterhour", d)
    def OnSnapshotDerive(self, d, e):           self._emit("derive", d)
    def OnFactor(self, d, e):                   self._emit("factor", d)

    def OnKLine(self, kline, kline_type, err):
        period = DT_VALUE_TO_PERIOD.get(int(kline_type), f"kline_{kline_type}")
        self._emit(period, kline)

    # 其余事件仅记日志
    def OnEvent(self, level, code, event_msg, ln=None):
        log.info("[tgw-event] L%s %s %s", level, code, event_msg)


class WsHub:
    """管理 WS 连接与每连接过滤器，线程安全（SDK 线程调用 broadcast）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._conns: dict[int, dict] = {}
        self._next_id = 0
        self.loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop

    def register(self, ws) -> int:
        with self._lock:
            self._next_id += 1
            cid = self._next_id
            self._conns[cid] = {"ws": ws, "periods": set(), "codes": {"*"}}
            return cid

    def unregister(self, cid: int):
        with self._lock:
            self._conns.pop(cid, None)

    def set_filter(self, cid: int, periods: list[str], codes: list[str]) -> dict:
        with self._lock:
            c = self._conns.get(cid)
            if not c:
                return {"ok": False}
            c["periods"] = {str(p).strip().lower() for p in periods}
            c["codes"] = {"*"} if "*" in codes else set(codes)
            return {"ok": True, "periods": sorted(c["periods"]),
                    "codes": sorted(c["codes"])[:200]}

    def stats(self) -> dict:
        with self._lock:
            return {"clients": len(self._conns)}

    def send_json(self, ws, payload: dict):
        text = json.dumps(payload, ensure_ascii=False, default=str)
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(ws.send_text(text), self.loop)

    def broadcast_md(self, period: str, code: str, fields: dict):
        payload = {
            "topic": "md", "period": period, "code": code,
            "recv_ts": _dt.datetime.now().isoformat(timespec="milliseconds"),
            "data": fields,
        }
        text = json.dumps(payload, ensure_ascii=False, default=str)
        with self._lock:
            targets = [c["ws"] for c in self._conns.values()
                       if period in c["periods"]
                       and ("*" in c["codes"] or code in c["codes"])]
        if not targets or self.loop is None:
            return
        for ws in targets:
            asyncio.run_coroutine_threadsafe(ws.send_text(text), self.loop)


class SubscriptionPipeline:
    """维护期望订阅集，驱动 tgw 上游订阅，接收推送并广播。"""

    def __init__(self, runtime, hub: WsHub, pack_fn):
        self.rt = runtime
        self.hub = hub
        self.pack = pack_fn                      # serialize.pack
        self._lock = threading.RLock()
        self.desired: dict[str, set[str]] = {}   # period -> codes
        self.applied: dict[str, set[str]] = {}
        self.spi = _BridgePushSpi(self._on_item)
        self._spi_registered = False

    # ---------- 推送入口（SDK 线程） ----------

    _MKT_SUFFIX_BY_INT: dict[int, str] = {}

    def _on_item(self, period: str, obj):
        if obj is None:
            return
        fields = getattr(obj, "__dict__", None)
        cls = type(obj).__name__
        if isinstance(fields, dict):
            clean = {"__type__": "obj", "class": cls,
                     "fields": {str(k): self.pack(v) for k, v in fields.items()}}
            raw_fields = fields
        else:
            clean = self.pack(obj)
            raw_fields = {}
        code = str(raw_fields.get("security_code", ""))
        mt = raw_fields.get("market_type")
        suffix = self._suffix_for(mt)
        if suffix and "." not in code:
            code = f"{code}.{suffix}"
        # 注入完整代码（与原生 AmazingData 对象的 .code 一致，如 510300.SH）
        if isinstance(clean, dict) and clean.get("__type__") == "obj":
            clean["fields"]["code"] = code
        self.hub.broadcast_md(period, code, clean)

    @classmethod
    def _suffix_for(cls, mt) -> str | None:
        table = cls._SUFFIX_TABLE
        return table.get(mt) if mt is not None else None

    _SUFFIX_TABLE: dict[Any, str] = {}   # 运行时由 init_market_map 填充

    # ---------- 订阅管理 ----------

    def init_market_map(self):
        tgw = self.rt.tgw
        MT = tgw.MarketType
        cls = type(self)
        cls._SUFFIX_TABLE = {
            MT.kSSE: "SH", MT.kSZSE: "SZ", MT.kBK: "BJ",
            MT.kCFFEX: "CFE", MT.kHKEx: "HK",
        }
        for pname, dtname in PERIOD_TO_DT.items():
            DT_VALUE_TO_PERIOD[getattr(tgw.SubscribeDataType, dtname)] = pname

    def _dt_for(self, period: str):
        tgw = self.rt.tgw
        name = PERIOD_TO_DT.get(str(period).lower())
        if not name:
            raise ValueError(
                f"period '{period}' 不支持实时推送；可选：{sorted(PERIOD_TO_DT)}"
                "（日线及以上请用 REST query_kline 轮询）")
        return getattr(tgw.SubscribeDataType, name)

    def _mt_for(self, suffix: str):
        tgw = self.rt.tgw
        m = {"SH": tgw.MarketType.kSSE, "SZ": tgw.MarketType.kSZSE,
             "BJ": tgw.MarketType.kBK, "CFE": tgw.MarketType.kCFFEX,
             "HK": tgw.MarketType.kHKEx}
        if suffix not in m:
            raise ValueError(f"不支持的市场后缀 .{suffix}")
        return m[suffix]

    def _send_upstream(self, items_spec: list[tuple[str, str]], subscribe: bool):
        """items_spec: [(period, 'SYMBOL.SUF'), ...]"""
        import tgw.interface as _tif   # noqa: PLC0415  全局 SPI 持有处
        tgw = self.rt.tgw
        n = len(items_spec)
        if n == 0:
            return
        handle = tgw.Tools_CreateSubscribeItem(n)
        try:
            for i, (period, code) in enumerate(items_spec):
                sym, suf = market_from_code(code)
                it = tgw.SubscribeItem()
                it.market = self._mt_for(suf)
                it.security_code = sym
                it.category_type = self._dt_for(period)
                tgw.Tools_SetSubscribeItem(handle, i, it)
            if subscribe and not self._spi_registered and not _tif.g_push_spi:
                # 与 interface.Subscribe 首次调用行为一致：注册全局唯一内部推送 SPI
                _tif.g_push_spi = self.spi
                _tif.g_spi.SetSpi(self.spi)
            self._spi_registered = True
            if subscribe:
                err = tgw.IGMDApi_Subscribe(handle, n)
            else:
                err = tgw.IGMDApi_UnSubscribe(handle, n)
            if err != 0:
                msg = tgw.GetErrorMsg(err) if hasattr(tgw, "GetErrorMsg") else str(err)
                raise RuntimeError(f"上游{'订阅' if subscribe else '退订'}失败: {msg} ({err})")
        finally:
            tgw.Tools_DestroySubscribeItem(handle)

    def add(self, subs: list[dict]) -> dict:
        """subs: [{"period":"snapshot","code_list":["510300.SH",...]}, ...]"""
        with self._lock:
            self.rt.ensure_logged_in()
            delta: list[tuple[str, str]] = []
            for s in subs:
                period = str(s["period"]).strip().lower()
                codes = [str(c).strip() for c in s.get("code_list", []) if str(c).strip()]
                cur = self.desired.setdefault(period, set())
                app = self.applied.setdefault(period, set())
                for c in codes:
                    cur.add(c)
                    if c not in app:
                        delta.append((period, c))
            if delta:
                try:
                    self._send_upstream(delta, subscribe=True)
                    for p, c in delta:
                        self.applied.setdefault(p, set()).add(c)
                except Exception:
                    log.exception("增量订阅失败（desired 已记录，可重试 add）")
            return self.describe()

    def remove(self, subs: list[dict]) -> dict:
        with self._lock:
            gone: list[tuple[str, str]] = []
            for s in subs:
                period = str(s["period"]).strip().lower()
                codes = {str(c).strip() for c in s.get("code_list", [])}
                app = self.applied.get(period, set())
                for c in codes & app:
                    gone.append((period, c))
                if period in self.desired:
                    self.desired[period] -= codes
            if gone:
                try:
                    self._send_upstream(gone, subscribe=False)
                    for p, c in gone:
                        self.applied.get(p, set()).discard(c)
                except Exception:
                    log.exception("退订失败（本地已停止转发）")
            return self.describe()

    def describe(self) -> dict:
        return {p: sorted(cs) for p, cs in self.desired.items()}

    def stats(self) -> dict:
        with self._lock:
            return {"desired": {p: len(c) for p, c in self.desired.items()},
                    "applied": {p: len(c) for p, c in self.applied.items()}}
