"""HTTP 客户端：把桥接服务的 JSON 信封还原为 pandas DataFrame / Python 对象。"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pandas as pd
import requests


class BridgeError(RuntimeError):
    """桥接调用失败（含底层 SDK 错误码与信息）。"""

    def __init__(self, message: str, err_code: int = -1, elapsed_ms: float | None = None):
        super().__init__(message)
        self.err_code = err_code
        self.message = message
        self.elapsed_ms = elapsed_ms


# ---------------- 信封还原 ----------------

def _unpack(v: Any) -> Any:
    if isinstance(v, list):
        return [_unpack(x) for x in v]
    if not isinstance(v, dict):
        return v
    t = v.get("__type__")
    if t == "df":
        return _to_df(v)
    if t == "obj":
        return SimpleNamespace(**{k: _unpack(x) for k, x in v.get("fields", {}).items()})
    return {k: _unpack(x) for k, x in v.items()}


def _to_df(env: dict) -> pd.DataFrame:
    index = env.get("index")
    idx_dtype = env.get("index_dtype", "")
    if index is None:
        df = pd.DataFrame(env["rows"], columns=env["columns"])
    else:
        if "datetime" in (idx_dtype or ""):
            index = pd.to_datetime(index)
        df = pd.DataFrame(env["rows"], columns=env["columns"], index=index)
    for col, dtype in (env.get("dtypes") or {}).items():
        if col not in df.columns:
            continue
        if str(dtype).startswith("datetime"):
            df[col] = pd.to_datetime(df[col])
        elif dtype == "object":
            df[col] = df[col].astype(object)
    return df


class _GroupProxy:
    """c.base_data.get_xxx(...) → POST /api/v1/call/base/get_xxx"""

    def __init__(self, client: "GalaxyBridgeClient", group: str):
        self._client = client
        self._group = group
        self._cache: dict[str, Any] = {}

    def __getattr__(self, method: str):
        if method.startswith("_"):
            raise AttributeError(method)
        client, group = self._client, self._group

        def _call(*args, **kwargs):
            return client.call(group, method, args=list(args), kwargs=kwargs)
        _call.__name__ = method
        self._cache[method] = _call
        return _call


class GalaxyBridgeClient:
    def __init__(self, base_url: str, api_key: str | None = None,
                 timeout: float = 300.0, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""
        self.timeout = timeout
        self.session = session or requests.Session()

    # ---- 底层请求 ----

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    def _post(self, path: str, body: dict | None = None) -> dict:
        r = self.session.post(self.base_url + path, json=body or {},
                              headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _get(self, path: str) -> dict:
        r = self.session.get(self.base_url + path, headers=self._headers(),
                             timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _unwrap(resp: dict) -> Any:
        if resp.get("ok"):
            return _unpack(resp.get("data"))
        raise BridgeError(resp.get("message", "unknown"),
                          err_code=int(resp.get("err_code", -1)),
                          elapsed_ms=resp.get("elapsed_ms"))

    # ---- 公共 API ----

    def health(self) -> dict:
        """/health 探活（无需 api_key）。"""
        r = self.session.get(self.base_url + "/health", timeout=10)
        r.raise_for_status()
        return r.json()

    def login_status_ok(self) -> bool:
        try:
            return bool(self.health().get("logged_in"))
        except Exception:
            return False

    def call(self, group: str, method: str,
             args: list | tuple = (), kwargs: dict | None = None) -> Any:
        """通用分发：覆盖服务端全部查询接口。"""
        resp = self._post(f"/api/v1/call/{group}/{method}",
                          {"args": list(args), "kwargs": kwargs or {}})
        return self._unwrap(resp)

    def methods(self) -> dict[str, list[str]]:
        """列出各分组可用方法（base/info/market/download）。"""
        return self._unwrap(self._get("/api/v1/meta/methods"))

    def relogin(self) -> dict:
        return self._unwrap(self._post("/admin/login"))

    # 分组代理：与原生 AmazingData 用法对齐
    @property
    def base_data(self) -> _GroupProxy:
        return _GroupProxy(self, "base")

    @property
    def info_data(self) -> _GroupProxy:
        return _GroupProxy(self, "info")

    @property
    def market_data(self) -> _GroupProxy:
        return _GroupProxy(self, "market")

    @property
    def download_data(self) -> _GroupProxy:
        return _GroupProxy(self, "download")

    # ---- 高频快捷方式 ----

    def get_code_list(self, security_type: str = "EXTRA_STOCK_A") -> list[str]:
        return self.call("base", "get_code_list",
                         kwargs={"security_type": security_type})

    def query_kline(self, code_list: list[str], begin_date: int, end_date: int,
                    period: str | int = "day",
                    begin_time: int | None = None, end_time: int | None = None) -> dict:
        """返回 {code: DataFrame}。period 可用 'min1'/'min5'/'day' 等。"""
        body = {"code_list": code_list, "begin_date": begin_date,
                "end_date": end_date, "period": period}
        if begin_time is not None:
            body["begin_time"] = begin_time
        if end_time is not None:
            body["end_time"] = end_time
        return self._unwrap(self._post("/api/v1/kline", body))

    def query_snapshot(self, code_list: list[str], begin_date: int, end_date: int,
                       begin_time: int | None = None,
                       end_time: int | None = None) -> dict:
        body = {"code_list": code_list, "begin_date": begin_date,
                "end_date": end_date}
        if begin_time is not None:
            body["begin_time"] = begin_time
        if end_time is not None:
            body["end_time"] = end_time
        return self._unwrap(self._post("/api/v1/snapshot", body))

    # ---- 订阅管理（实时推送见 realtime.py）----

    def sub_add(self, subs: list[dict]) -> dict:
        return self._unwrap(self._post(
            "/api/v1/sub/add",
            {"subs": [{"period": s["period"], "code_list": s["code_list"]}
                      for s in subs]}))

    def sub_remove(self, subs: list[dict]) -> dict:
        return self._unwrap(self._post(
            "/api/v1/sub/remove",
            {"subs": [{"period": s["period"], "code_list": s["code_list"]}
                      for s in subs]}))

    def sub_list(self) -> dict:
        return self._unwrap(self._get("/api/v1/sub/list"))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.session.close()
