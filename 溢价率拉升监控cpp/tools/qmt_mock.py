#!/usr/bin/env python3
"""Local JSONL QMT backend simulator. It never reaches a broker."""
from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any


class QmtMock:
    def __init__(self, response_delay_ms: int = 0, drop_order_responses: bool = False, log_path: Path | None = None):
        self.response_delay_ms = response_delay_ms
        self.drop_order_responses = drop_order_responses
        self.log_path = log_path
        self.orders: list[dict[str, Any]] = []
        self.positions = [{"code": "159518.SZ", "available": 180_000}]

    async def send(self, writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
        if self.response_delay_ms:
            await asyncio.sleep(self.response_delay_ms / 1000)
        writer.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
        await writer.drain()

    async def client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await self.send(writer, {"type": "welcome", "server": "qmt_mock", "trading": False})
        try:
            while line := await reader.readline():
                try:
                    request = json.loads(line)
                except json.JSONDecodeError:
                    await self.send(writer, {"type": "error", "code": "invalid_json"})
                    continue
                if self.log_path:
                    self.log_path.parent.mkdir(parents=True, exist_ok=True)
                    with self.log_path.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps({"received_at": time.time(), "request": request}, ensure_ascii=False) + "\n")
                kind = request.get("type")
                if kind == "sync_request":
                    await self.send(writer, {"type": "orders_data", "sync_mode": "full", "data": self.orders,
                                             "snapshot_id": 1, "seq": 1})
                    await self.send(writer, {"type": "positions_data", "sync_mode": "full", "data": self.positions,
                                             "snapshot_id": 1, "seq": 1})
                elif kind == "query_orders":
                    await self.send(writer, {"type": "orders_data", "sync_mode": "full", "data": self.orders,
                                             "snapshot_id": 1, "seq": len(self.orders) + 1})
                elif kind == "query_positions":
                    await self.send(writer, {"type": "positions_data", "sync_mode": "full", "data": self.positions,
                                             "snapshot_id": 1, "seq": 1})
                elif kind in {"order", "etf_order"}:
                    order = dict(request)
                    order.update({"order_id": uuid.uuid4().hex[:12], "time": time.strftime("%H:%M:%S"),
                                  "status": "已报", "traded_qty": 0})
                    if kind == "etf_order":
                        order["direction"] = request.get("action")
                    else:
                        order["direction"] = "卖出" if request.get("side") == "SELL" else "买入"
                    self.orders.append(order)
                    if not self.drop_order_responses:
                        result_type = "etf_order_result" if kind == "etf_order" else "order_result"
                        await self.send(writer, {"type": result_type, "client_order_id": request.get("client_order_id"),
                                                 "success": True, "message": "仿真指令已提交"})
                        await self.send(writer, {"type": "orders_data", "sync_mode": "full", "data": self.orders,
                                                 "snapshot_id": 1, "seq": len(self.orders) + 1})
                elif kind == "cancel_order":
                    for order in self.orders:
                        if order.get("order_id") == request.get("order_id"):
                            order["status"] = "CANCELED"
                    await self.send(writer, {"type": "cancel_result", "order_id": request.get("order_id"),
                                             "success": True, "message": "仿真撤单已发送"})
                    await self.send(writer, {"type": "orders_data", "sync_mode": "full", "data": self.orders,
                                             "snapshot_id": 1, "seq": len(self.orders) + 1})
                else:
                    await self.send(writer, {"type": "error", "code": "unknown_type", "request_type": kind})
        finally:
            writer.close()
            await writer.wait_closed()


async def run(args: argparse.Namespace) -> None:
    mock = QmtMock(args.delay_ms, args.drop_order_responses, args.log)
    server = await asyncio.start_server(mock.client, args.host, args.port)
    addresses = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    print(f"QMT mock listening: {addresses}; no broker connection", flush=True)
    async with server:
        await server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19527,
                        help="deliberately differs from production 9527")
    parser.add_argument("--delay-ms", type=int, default=0)
    parser.add_argument("--drop-order-responses", action="store_true")
    parser.add_argument("--log", type=Path)
    try:
        asyncio.run(run(parser.parse_args()))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
