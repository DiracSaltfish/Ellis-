"""REST + WebSocket 路由。"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .hub import WsHub, SubscriptionPipeline
from .runtime import AdRuntime, BridgeError
from .serialize import pack

log = logging.getLogger("bridge.routes")


def envelope(data: Any = None, t0: float | None = None,
             err_code: int | None = None, message: str | None = None) -> dict:
    out: dict = {"ok": err_code is None}
    if err_code is not None:
        out["err_code"] = err_code
        out["message"] = message
    else:
        out["data"] = data
    if t0 is not None:
        out["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


class CallBody(BaseModel):
    args: list[Any] = []
    kwargs: dict[str, Any] = {}


class SubSpec(BaseModel):
    period: str
    code_list: list[str]


class SubBody(BaseModel):
    subs: list[SubSpec]


def _translate_period(runtime: AdRuntime, p) -> Any:
    """'min5'/'day' 等名称 → ad.constant.Period 枚举值（int）。数字原样返回。"""
    if isinstance(p, (int, float)):
        return int(p)
    try:
        return int(getattr(runtime._ad.constant.Period, str(p).strip().lower()).value)
    except Exception:
        raise BridgeError(f"period '{p}' 无效；可用: min1,min3,min5,min10,min15,"
                          "min30,min60,min120,day,week,month,season,year")


def _auth_ok(request: Request, api_key: str) -> bool:
    if not api_key:
        return True
    key = request.headers.get("x-api-key") or request.query_params.get("api_key")
    return key == api_key


def build_router(runtime: AdRuntime, hub: WsHub, pipeline: SubscriptionPipeline,
                 api_key: str, subscribe_cfg_json: str) -> APIRouter:
    router = APIRouter()

    def guard(request: Request):
        if not _auth_ok(request, api_key):
            raise HTTPException(status_code=401, detail="X-API-Key 无效")

    # ---------------- 基础 ----------------

    @router.get("/health")
    def health():
        s = runtime.status()
        s.update(ws=hub.stats(), subscriptions=pipeline.stats())
        return s

    @router.get("/api/v1/meta/methods")
    def meta_methods(request: Request):
        guard(request)
        return envelope(runtime.list_methods(), time.perf_counter())

    @router.post("/api/v1/call/{group}/{method}")
    def generic_call(group: str, method: str, body: CallBody, request: Request):
        guard(request)
        t0 = time.perf_counter()
        try:
            result = runtime.call(group, method, body.args, body.kwargs)
            return envelope(pack(result), t0)
        except BridgeError as e:
            return envelope(err_code=e.err_code, message=e.message, t0=t0)

    # ---------------- 快捷端点 ----------------

    @router.post("/api/v1/kline")
    def kline(body: dict, request: Request):
        guard(request)
        t0 = time.perf_counter()
        try:
            md = runtime.instance_for("market")
            res = md.query_kline(
                code_list=body["code_list"],
                begin_date=int(body["begin_date"]),
                end_date=int(body["end_date"]),
                period=_translate_period(runtime, body.get("period", "day")),
                begin_time=body.get("begin_time"),
                end_time=body.get("end_time"),
            )
            return envelope(pack(res), t0)
        except BridgeError as e:
            return envelope(err_code=e.err_code, message=e.message, t0=t0)

    @router.post("/api/v1/snapshot")
    def snapshot(body: dict, request: Request):
        guard(request)
        t0 = time.perf_counter()
        try:
            md = runtime.instance_for("market")
            res = md.query_snapshot(
                code_list=body["code_list"],
                begin_date=int(body["begin_date"]),
                end_date=int(body["end_date"]),
                begin_time=body.get("begin_time"),
                end_time=body.get("end_time"),
            )
            return envelope(pack(res), t0)
        except BridgeError as e:
            return envelope(err_code=e.err_code, message=e.message, t0=t0)

    @router.get("/api/v1/code_list/{security_type}")
    def code_list(security_type: str, request: Request):
        guard(request)
        t0 = time.perf_counter()
        try:
            res = runtime.call("base", "get_code_list",
                               kwargs={"security_type": security_type})
            return envelope(pack(res), t0)
        except BridgeError as e:
            return envelope(err_code=e.err_code, message=e.message, t0=t0)

    @router.get("/api/v1/calendar")
    def calendar(request: Request):
        guard(request)
        t0 = time.perf_counter()
        try:
            res = runtime.call("base", "get_calendar")
            return envelope(pack(res), t0)
        except BridgeError as e:
            return envelope(err_code=e.err_code, message=e.message, t0=t0)

    # ---------------- 订阅管理 ----------------

    @router.post("/api/v1/sub/add")
    def sub_add(body: SubBody, request: Request):
        guard(request)
        t0 = time.perf_counter()
        try:
            return envelope(pipeline.add([s.model_dump() for s in body.subs]), t0)
        except Exception as e:
            return envelope(err_code=-3, message=str(e), t0=t0)

    @router.post("/api/v1/sub/remove")
    def sub_remove(body: SubBody, request: Request):
        guard(request)
        t0 = time.perf_counter()
        try:
            return envelope(pipeline.remove([s.model_dump() for s in body.subs]), t0)
        except Exception as e:
            return envelope(err_code=-3, message=str(e), t0=t0)

    @router.get("/api/v1/sub/list")
    def sub_list(request: Request):
        guard(request)
        return envelope(pipeline.describe())

    # ---------------- 管理 ----------------

    @router.post("/admin/login")
    def admin_login(request: Request):
        guard(request)
        t0 = time.perf_counter()
        try:
            return envelope(runtime.relogin(), t0)
        except BridgeError as e:
            return envelope(err_code=e.err_code, message=e.message, t0=t0)

    @router.post("/admin/logout")
    def admin_logout(request: Request):
        guard(request)
        runtime.logout()
        return {"ok": True}

    # ---------------- 实时推送 ----------------

    @router.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        if not _auth_ok(websocket, api_key):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        cid = hub.register(websocket)
        log.info("WS 客户端接入 #%s（当前 %s 个）", cid, hub.stats()["clients"])
        try:
            await websocket.send_json({"topic": "welcome", "conn_id": cid})
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                action = msg.get("action")
                if action == "filter":
                    r = hub.set_filter(cid, msg.get("periods", []),
                                       msg.get("codes", ["*"]))
                    await websocket.send_json({"topic": "filter_ack", **r})
                elif action == "ping":
                    await websocket.send_json({"topic": "pong"})
                elif action == "sub":
                    pipeline.add(msg.get("subs", []))
                elif action == "unsub":
                    pipeline.remove(msg.get("subs", []))
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("WS 异常断开 #%s", cid)
        finally:
            hub.unregister(cid)
            log.info("WS 客户端断开 #%s", cid)

    # ---------------- 启动时自动订阅 ----------------

    if subscribe_cfg_json:
        @router.on_event("startup")
        def _auto_subscribe():   # pragma: no cover - 需真实环境
            def _worker():
                for _ in range(60):           # 至多等待 10 分钟登录成功
                    if runtime.logged_in():
                        break
                    time.sleep(10)
                if not runtime.logged_in():
                    log.warning("自动订阅跳过：始终未登录")
                    return
                try:
                    spec = json.loads(subscribe_cfg_json)
                    log.info("自动订阅：%s", pipeline.add(spec))
                except Exception:
                    log.exception("解析/执行 [bridge] subscribe 配置失败")
            import threading
            threading.Thread(target=_worker, name="auto-subscribe",
                             daemon=True).start()

    return router
