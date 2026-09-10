#!/usr/bin/env python3
"""Dependency-free localhost fixtures for the full Hub smoke test."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import signal
import struct
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


TEST_ROOT = Path("/private/tmp/machome-hub-local-test")
TOKEN = "local-smoke-token-0123456789abcdef"
CONTROL_TOKEN = "local-smoke-control-token-0123456789abcdef"
WEBULL_STATE = {
    "browser": "running",
    "collector_running": True,
    "schedule_mode": "auto",
}


def qmt_checksum(data: list[dict], extra: dict | None = None) -> str:
    payload = {"data": data, "extra": extra or {}}
    wire = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(wire.encode()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


async def read_http_request(reader: asyncio.StreamReader):
    head = await reader.readuntil(b"\r\n\r\n")
    lines = head.decode("latin1").split("\r\n")
    method, target, _ = lines[0].split(" ", 2)
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    body = await reader.readexactly(length) if length else b""
    return method, target, headers, body


async def send_http(writer: asyncio.StreamWriter, payload: object, status: int = 200):
    body = encoded(payload)
    reason = "OK" if status == 200 else "Error"
    writer.write(
        f"HTTP/1.1 {status} {reason}\r\n".encode()
        + b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\n".encode()
        + b"Connection: close\r\n\r\n"
        + body
    )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def upgrade_websocket(writer: asyncio.StreamWriter, headers: dict[str, str]):
    key = headers["sec-websocket-key"]
    accept = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
    ).decode()
    writer.write(
        b"HTTP/1.1 101 Switching Protocols\r\n"
        b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
        + f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode()
    )
    await writer.drain()


async def send_ws(writer: asyncio.StreamWriter, payload: object, opcode: int = 1):
    body = encoded(payload) if not isinstance(payload, bytes) else payload
    first = 0x80 | opcode
    if len(body) < 126:
        header = bytes((first, len(body)))
    elif len(body) < 65536:
        header = bytes((first, 126)) + struct.pack("!H", len(body))
    else:
        header = bytes((first, 127)) + struct.pack("!Q", len(body))
    writer.write(header + body)
    await writer.drain()


async def read_ws(reader: asyncio.StreamReader):
    first, second = await reader.readexactly(2)
    opcode = first & 0x0F
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", await reader.readexactly(8))[0]
    mask = await reader.readexactly(4) if second & 0x80 else b""
    payload = bytearray(await reader.readexactly(length))
    if mask:
        for index in range(length):
            payload[index] ^= mask[index % 4]
    return opcode, bytes(payload)


def webull_book(sequence: int = 1) -> dict:
    timestamp = now()
    return {
        "schema_version": 2,
        "type": "depth_snapshot",
        "symbol": "XOP",
        "ticker_id": "913243629",
        "session_id": "mock-session",
        "sequence": sequence,
        "captured_at": timestamp,
        "published_at": timestamp,
        "changed": True,
        "content_hash": f"mock-{sequence}",
        "source_interval_ms": 500.0,
        "gateway_latency_ms": 2.0,
        "book": {
            "aggregation": "price",
            "depth": 2,
            "bid_depth": 2,
            "ask_depth": 2,
            "bids": [
                {"level": 1, "price": "99.90", "volume": "1200"},
                {"level": 2, "price": "99.80", "volume": "900"},
            ],
            "asks": [
                {"level": 1, "price": "100.00", "volume": "1000"},
                {"level": 2, "price": "100.10", "volume": "800"},
            ],
            "inside_market": {
                "best_bid": "99.90",
                "best_ask": "100.00",
                "spread": "0.10",
                "mid_price": "99.95",
                "state": "normal",
            },
        },
    }


def realtime_snapshot() -> dict:
    return {
        "protocol": 1,
        "type": "snapshot",
        "server_time": now(),
        "monitoring": True,
        "started_by": "mock",
        "wind": {"state": "ready", "running": True, "tbapi_loaded": True},
        "items": [
            {
                "symbol": "159513",
                "windcode": "159513.SZ",
                "name": "纳指科技ETF",
                "status": "monitoring",
                "values": {
                    "etfbuyamount": 1200000,
                    "etfsellamount": 600000,
                    "netamount": 600000,
                },
                "pcf": {
                    "status": "ready",
                    "creation_redemption_unit": 600000,
                    "creation_allowed": True,
                    "redemption_allowed": True,
                    "creation_limit": 3600000,
                    "net_creation_limit": 2400000,
                },
                "opportunity": {
                    "kind": "creation",
                    "label": "盘中申购机会",
                    "actionable": True,
                    "net_baskets": 1,
                },
                "updated_at": now(),
                "last_change": [{"field": "etfsellamount", "text": "赎回份额增加 60 0000"}],
                "age_seconds": 0.1,
            }
        ],
    }


async def websocket_loop(reader, writer, kind: str):
    if kind == "premium":
        await send_ws(writer, {"type": "status", "phase": "continuous", "cn_quotes_desired": True,
                               "hk_quotes_desired": False, "ready_count": 2, "watch_count": 2})
        await send_ws(writer, {"type": "summary", "symbol": "510300.SH", "name": "沪深300ETF",
                               "price": 4.10, "iopv": 4.08, "premium_pct": 0.49, "timestamp": now()})
        await send_ws(writer, {"type": "signal", "symbol": "510300.SH", "model": "premium",
                               "premium_pct": 0.49, "window_sec": 60, "timestamp": now()})
        while True:
            opcode, body = await read_ws(reader)
            if opcode == 8:
                break
            if opcode == 9:
                await send_ws(writer, body, opcode=10)
                continue
            if opcode != 1:
                continue
            command = json.loads(body)
            operation = command.get("op")
            if operation == "status":
                await send_ws(writer, {"type": "status", "phase": "continuous", "cn_quotes_desired": True,
                                       "ready_count": 2, "watch_count": 2})
            elif operation == "sync":
                await send_ws(writer, {"type": "sync_begin", "count": 1})
                await send_ws(writer, {"type": "summary", "symbol": "510300.SH", "price": 4.10,
                                       "iopv": 4.08, "premium_pct": 0.49, "timestamp": now()})
                await send_ws(writer, {"type": "sync_complete", "count": 1})
            elif operation == "set_watchlist":
                await send_ws(writer, {"type": "watchlist_ack", "accepted": True,
                                       "symbols": command.get("symbols", [])})
            elif operation == "set_l1_hotlist":
                await send_ws(writer, {"type": "l1_hotlist_ack", "accepted": True,
                                       "symbols": command.get("symbols", [])})
            elif operation == "raw_snapshot":
                await send_ws(writer, {"type": "raw_snapshot", "symbol": "510300.SH", "seq": 2})
    elif kind == "webull":
        await send_ws(writer, {"type": "hello", "schema_version": 2,
                               "service": "webull-lv2-gateway", "symbol": "XOP",
                               "snapshot_semantics": "full_replace", "aggregation": "price"})
        sequence = 1
        while True:
            await send_ws(writer, {"type": "depth_snapshot", "data": webull_book(sequence)})
            sequence += 1
            try:
                opcode, body = await asyncio.wait_for(read_ws(reader), timeout=0.5)
                if opcode == 8:
                    break
                if opcode == 9:
                    await send_ws(writer, body, opcode=10)
            except asyncio.TimeoutError:
                pass
    else:
        while True:
            await send_ws(writer, realtime_snapshot())
            await send_ws(writer, {"protocol": 1, "type": "heartbeat", "timestamp": now()})
            try:
                opcode, body = await asyncio.wait_for(read_ws(reader), timeout=0.5)
                if opcode == 8:
                    break
                if opcode == 9:
                    await send_ws(writer, body, opcode=10)
            except asyncio.TimeoutError:
                pass


async def handle_http(reader, writer, kind: str):
    try:
        method, target, headers, body = await read_http_request(reader)
        path = urlsplit(target).path
        if headers.get("upgrade", "").lower() == "websocket":
            await upgrade_websocket(writer, headers)
            await websocket_loop(reader, writer, kind)
            writer.close()
            await writer.wait_closed()
            return
        if kind == "upload":
            if path == "/api/v1/health":
                await send_http(writer, {"ok": True, "service": "newnavnav-web",
                                         "module": "upload-website", "build": "mock"})
            elif path == "/api/v1/snapshots/status":
                await send_http(writer, {
                    "branch_count": 2, "quote_count": 8, "uploaded_quote_count": 8,
                    "purchase_info_count": 3, "uploaded_quotes_enabled": True,
                    "refresh_interval_seconds": 5, "last_refresh_at": now(),
                    "last_error": "", "branches": {"qdii": {"rows": 4}},
                })
            else:
                await send_http(writer, {"error": "not_found"}, 404)
            return
        if kind == "webull":
            if path.endswith("/health/live"):
                await send_http(writer, {"ok": True, "service": "webull-lv2-gateway", "api_version": 2})
                return
            expected_token = CONTROL_TOKEN if path == "/v1/commands" else TOKEN
            if headers.get("authorization") != f"Bearer {expected_token}":
                await send_http(writer, {"error": "unauthorized"}, 401)
                return
            if path == "/v1/commands" and method == "POST":
                request = json.loads(body or b"{}")
                action = request.get("action")
                if action not in {"set_schedule_mode", "collector_start", "collector_stop",
                                  "open_login", "restart_browser"}:
                    await send_http(writer, {"ok": False, "error": "UNSUPPORTED_ACTION"}, 400)
                    return
                arguments = request.get("arguments", {})
                if action == "set_schedule_mode":
                    WEBULL_STATE["schedule_mode"] = arguments.get("mode", "auto")
                    if WEBULL_STATE["schedule_mode"] == "force_running":
                        WEBULL_STATE["collector_running"] = True
                        WEBULL_STATE["browser"] = "running"
                    elif WEBULL_STATE["schedule_mode"] == "force_stopped":
                        WEBULL_STATE["collector_running"] = False
                        WEBULL_STATE["browser"] = "stopped"
                elif action in {"collector_start", "restart_browser", "open_login"}:
                    WEBULL_STATE["collector_running"] = True
                    WEBULL_STATE["browser"] = "running"
                elif action == "collector_stop":
                    WEBULL_STATE["collector_running"] = False
                    WEBULL_STATE["browser"] = "stopped"
                response = {
                    "ok": True,
                    "request_id": request.get("request_id"),
                    "action": action,
                    "schedule_mode": WEBULL_STATE["schedule_mode"],
                    "collector_running": WEBULL_STATE["collector_running"],
                    "status": {"data": "flowing"},
                }
                if action == "restart_browser":
                    response["restart_transition_observed"] = True
                await send_http(writer, response)
                return
            status = {
                "browser": WEBULL_STATE["browser"], "auth": "authenticated", "data": "flowing",
                "collector_running": WEBULL_STATE["collector_running"], "api_running": True,
                "api_address": "127.0.0.1:18766", "schedule_mode": WEBULL_STATE["schedule_mode"],
                "schedule_message": "mock market session", "last_depth_at": now(),
                "valid_responses": 42, "invalid_responses": 0, "last_error": "",
            }
            if path.endswith("/health/ready"):
                await send_http(writer, {"ready": True, "status": status})
            elif path.endswith("/status"):
                await send_http(writer, {"status": status, "client_count": 1, "depth": webull_book(1)})
            elif path.endswith("/symbols"):
                await send_http(writer, {"schema_version": 2, "snapshot_semantics": "full_replace",
                                         "price_encoding": "decimal_string", "volume_encoding": "decimal_string",
                                         "symbols": [{"symbol": "XOP", "ticker_id": "913243629",
                                                      "depth_size": 50, "stale_after_ms": 5000}]})
            elif "/book/" in path:
                await send_http(writer, webull_book(1))
            elif path.endswith("/clients"):
                await send_http(writer, {"clients": [{"client_id": "mock-ui", "remote": "127.0.0.1",
                                                       "connected_at": now(), "last_sent_at": now(),
                                                       "messages_sent": 12}]})
            else:
                await send_http(writer, {"error": "not_found"}, 404)
            return

        snapshot = realtime_snapshot()
        if path == "/api/v1/health":
            await send_http(writer, {"protocol": 1, "type": "health", "ok": True,
                                     "module": "etf-realtime-monitor", "monitoring": True,
                                     "server_time": now(), "inside_schedule": True,
                                     "symbols": 1, "connections": 1,
                                     "wind": {"state": "ready", "running": True,
                                              "tbapi_loaded": True}})
        elif path == "/api/v1/snapshot":
            await send_http(writer, snapshot)
        elif path == "/api/v1/watchlist" and method == "GET":
            await send_http(writer, {"symbols": ["159513"]})
        elif path == "/api/v1/history":
            await send_http(writer, {"protocol": 1, "type": "history", "items": [{
                "timestamp": now(), "symbol": "159513.SZ", "direction": "buy",
                "old_value": 600000, "new_value": 1200000, "delta": 600000,
                "basket_count": 1, "status": "actionable"}]})
        elif path == "/api/v1/pcf":
            await send_http(writer, {"items": [{"symbol": "159513.SZ", "trade_date": datetime.now().date().isoformat()}]})
        elif path.startswith("/api/v1/pcf/") and path != "/api/v1/pcf/refresh":
            symbol = path.rsplit("/", 1)[-1]
            await send_http(writer, {"symbol": symbol, "summary": {"basket_size": 600000, "cash_component": 1234.5},
                                     "components": [{"code": "AAPL.O", "quantity": 120}],
                                     "qmt1": {"balance": 1000000, "orders": []},
                                     "qmt2": {"balance": 900000, "orders": []}})
        elif path == "/api/v1/wind/status":
            await send_http(writer, {"protocol": 1, "type": "snapshot", "wind_state": "ready", "items": snapshot["items"]})
        elif path == "/api/v1/watchlist" and method == "PUT":
            received = json.loads(body or b"{}")
            if received != {"symbols": ["159513"]}:
                await send_http(writer, {"detail": "invalid mock watchlist payload"}, 422)
                return
            control_snapshot = realtime_snapshot()
            control_snapshot["received"] = received
            await send_http(writer, control_snapshot)
        elif path == "/api/v1/symbols/159513/name" and method == "PUT":
            received = json.loads(body or b"{}")
            if received != {"name": "测试标的"}:
                await send_http(writer, {"detail": "invalid mock name payload"}, 422)
                return
            control_snapshot = realtime_snapshot()
            control_snapshot["received"] = received
            await send_http(writer, control_snapshot)
        elif path.startswith("/api/v1/") and method in {"POST", "PUT"}:
            control_snapshot = realtime_snapshot()
            control_snapshot["action"] = path.rsplit("/", 2)[-1]
            control_snapshot["received"] = json.loads(body or b"{}")
            await send_http(writer, control_snapshot)
        else:
            await send_http(writer, {"error": "not_found"}, 404)
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        writer.close()


async def handle_l1(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    writer.write(encoded({"v": 1, "t": "hello", "service": "qmt_l1"}) + b"\n")
    await writer.drain()
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            request = json.loads(line)
            operation = request.get("op") or request.get("t")
            if operation == "status":
                response = {"v": 1, "t": "status", "id": request.get("id"), "clients": 1, "symbols": 0}
            else:
                response = {"v": 1, "t": "pong", "id": request.get("id"), "timestamp": now()}
            writer.write(encoded(response) + b"\n")
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        pass
    writer.close()


async def handle_qmt(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, backend: str):
    orders = [{
        "order_id": "10001", "code": "159513.SZ", "time": "09:31:01",
        "name": "纳指科技ETF", "direction": "买入", "price": 0.0,
        "qty": 1, "traded_qty": 0, "traded_price": 0.0,
        "trade_amount": 0.0, "status": "已报", "status_code": 50,
        "remark": "", "client_order_id": "", "sort_seq": 1,
    }]
    positions = [{
        "code": "159513.SZ", "name": "纳指科技ETF",
        "volume": 600000, "available": 600000,
        "cost_price": 0.0, "current_price": 0.0, "profit": 0.0,
        "profit_rate": 0.0, "market_value": 0.0,
    }]
    order_seq = 1

    async def send_sync(target: str = "all"):
        nonlocal order_seq
        if target in {"all", "positions"}:
            cash = 1_000_000.0
            await send_qmt(writer, {
                "type": "positions_data", "sync_mode": "full", "snapshot_id": 1,
                "seq": 1, "count": len(positions), "checksum": qmt_checksum(positions, {"available_cash": cash}),
                "available_cash": cash, "data": positions,
            })
        if target in {"all", "orders"}:
            ordered = sorted(orders, key=lambda item: (item["time"], int(item["order_id"]), item["sort_seq"]), reverse=True)
            await send_qmt(writer, {
                "type": "orders_data", "sync_mode": "full", "snapshot_id": 1,
                "seq": order_seq, "count": len(ordered), "checksum": qmt_checksum(ordered), "data": ordered,
            })

    await send_qmt(writer, {"type": "welcome", "push_sync": True, "backend": backend})
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            message = json.loads(line)
            message_type = message.get("type")
            if message_type == "ping":
                await send_qmt(writer, {"type": "pong", "backend": backend})
            elif message_type == "sync_request":
                await send_sync(str(message.get("target") or "all"))
            elif message_type == "query_status":
                await send_qmt(writer, {"type": "status", "backend": backend, "ready": True})
            elif message_type == "etf_order":
                order_seq += 1
                orders.insert(0, {
                    "order_id": str(10000 + order_seq), "code": message["code"], "time": "09:31:02",
                    "name": "纳指科技ETF", "direction": "买入" if message["action"] == "PURCHASE" else "卖出",
                    "price": 0.0, "qty": message["qty"], "traded_qty": 0,
                    "traded_price": 0.0, "trade_amount": 0.0,
                    "status": "已报", "status_code": 50,
                    "remark": message["client_order_id"],
                    "client_order_id": message["client_order_id"], "sort_seq": order_seq,
                })
                await send_qmt(writer, {
                    "type": "etf_order_result", "success": True, "message": "mock QMT accepted",
                    "client_order_id": message["client_order_id"], "code": message["code"],
                    "action": message["action"], "qty": message["qty"], "order_id": orders[0]["order_id"],
                })
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        pass
    writer.close()


async def send_qmt(writer: asyncio.StreamWriter, payload: dict):
    writer.write(encoded(payload) + b"\n")
    await writer.drain()


async def main():
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(TEST_ROOT, 0o700)
    (TEST_ROOT / "webull.token").write_text(TOKEN + "\n")
    os.chmod(TEST_ROOT / "webull.token", 0o600)
    (TEST_ROOT / "webull-control.token").write_text(CONTROL_TOKEN + "\n")
    os.chmod(TEST_ROOT / "webull-control.token", 0o600)
    health = TEST_ROOT / "upload_health"
    health.mkdir(exist_ok=True)
    for index, source in enumerate(("sina", "xop"), start=1):
        (health / f"{source}.json").write_text(json.dumps({
            "schema_version": 1, "source": source, "pid": 9000 + index,
            "state": "ok", "stage": "uploading",
            "updated_at": now(), "last_success_at": now(), "last_heartbeat_at": now(),
            "accepted": 7, "symbols": ["159513.SZ"],
        }))

    servers = [
        await asyncio.start_server(lambda r, w: handle_http(r, w, "upload"), "127.0.0.1", 18080),
        await asyncio.start_server(lambda r, w: handle_http(r, w, "premium"), "127.0.0.1", 18421),
        await asyncio.start_server(handle_l1, "127.0.0.1", 19196),
        await asyncio.start_server(lambda r, w: handle_http(r, w, "webull"), "127.0.0.1", 18766),
        await asyncio.start_server(lambda r, w: handle_http(r, w, "realtime"), "127.0.0.1", 16787),
        await asyncio.start_server(lambda r, w: handle_qmt(r, w, "QMT1"), "127.0.0.1", 19527),
        await asyncio.start_server(lambda r, w: handle_qmt(r, w, "QMT2"), "127.0.0.1", 19528),
    ]
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    print("READY", flush=True)
    await stop.wait()
    for server in servers:
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
