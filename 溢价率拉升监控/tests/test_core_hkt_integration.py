from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "build/macos-arm64-debug-make/etf-premium-core"
ADAPTER = ROOT / "adapter/tgw_adapter.py"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_port(port: int, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"port {port} did not open")


class CoreHktIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        if not CORE.exists():
            self.skipTest(f"build first: {CORE}")
        # Unix-domain socket paths are short on macOS; use /tmp explicitly so
        # the adapter socket cannot exceed sockaddr_un.sun_path.
        self.temp = tempfile.TemporaryDirectory(prefix="pm-hkt-", dir="/tmp")
        root = Path(self.temp.name)
        (root / "config").mkdir()
        (root / "runtime").mkdir()
        (root / "data").mkdir()
        (root / "logs").mkdir()
        self.monitor_port = free_port()
        self.legacy_port = free_port()
        config = {
            "mode": "simulation", "listen_host": "127.0.0.1",
            "monitor_port": self.monitor_port, "legacy_l1_port": self.legacy_port,
            "adapter_socket": "runtime/tgw.sock", "watchlist": "config/watchlist.json",
            "l1_hotlist": "config/l1_hotlist.json", "enable_hkt_l1": True,
            "security_names": "config/security_names.tsv", "data_dir": "data", "log_dir": "logs",
            "max_monitor_clients": 8, "max_detail_symbols_per_client": 4,
            "max_l1_clients": 8, "max_l1_symbols_per_client": 8,
            "max_upstream_symbols": 8, "dynamic_unsubscribe_grace_sec": 1,
            "capture_dynamic_market_data": False,
        }
        (root / "config/app.json").write_text(json.dumps(config), encoding="utf-8")
        (root / "config/watchlist.json").write_text(
            json.dumps({"version": 1, "symbols": ["159866.SZ"]}), encoding="utf-8")
        (root / "config/l1_hotlist.json").write_text(
            json.dumps({"version": 1, "symbols": ["02800.HK"]}), encoding="utf-8")
        (root / "config/security_names.tsv").write_text("159866.SZ\t日经ETF工银\n", encoding="utf-8")
        self.root = root
        self.core = subprocess.Popen(
            [str(CORE), "--config", str(root / "config/app.json"), "--simulation"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            wait_port(self.monitor_port)
        except Exception:
            output = self.core.stdout.read() if self.core.poll() is not None and self.core.stdout else ""
            raise AssertionError(f"core failed to listen: rc={self.core.poll()} output={output}")
        self.adapter = subprocess.Popen(
            [sys.executable, str(ADAPTER), "--socket", str(root / "runtime/tgw.sock"),
             "--watchlist", str(root / "config/watchlist.json"), "--simulate"],
            cwd=str(ROOT / "adapter"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )

    async def asyncTearDown(self) -> None:
        for process in (getattr(self, "adapter", None), getattr(self, "core", None)):
            if process is None:
                continue
            process.terminate()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            if process.stdout is not None:
                process.stdout.close()
        self.temp.cleanup()

    async def test_hot_hkt_is_legacy_only_not_b_or_persistence(self) -> None:
        import websockets

        summary_symbols: set[str] = set()
        status: dict[str, object] = {}
        async with websockets.connect(
            f"ws://127.0.0.1:{self.monitor_port}/ws/v2/summary", proxy=None
        ) as ws:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    message = json.loads(await asyncio.wait_for(ws.recv(), 1))
                except asyncio.TimeoutError:
                    continue
                if message.get("type") == "summary":
                    summary_symbols.add(str(message.get("s")))
                if message.get("type") == "status":
                    status = message
                if "159866.SZ" in summary_symbols and status.get("l1_hot_ready") == 1:
                    break
            await ws.send(json.dumps({"op": "set_l1_hotlist", "symbols": ["2800.HK"]}))
            rejected = json.loads(await asyncio.wait_for(ws.recv(), 2))
            while rejected.get("type") != "l1_hotlist_ack":
                rejected = json.loads(await asyncio.wait_for(ws.recv(), 2))
            self.assertFalse(rejected["accepted"])
            self.assertEqual(rejected["symbols"], ["02800.HK"])
            await ws.send(json.dumps({"op": "set_l1_hotlist", "symbols": ["02800.HK", "159866.SZ"]}))
            overlap_ack: dict[str, object] = {}
            while overlap_ack.get("type") != "l1_hotlist_ack":
                overlap_ack = json.loads(await asyncio.wait_for(ws.recv(), 2))
            self.assertTrue(overlap_ack["accepted"])
            await ws.send(json.dumps({"op": "status"}))
            overlap_status: dict[str, object] = {}
            while overlap_status.get("type") != "status":
                overlap_status = json.loads(await asyncio.wait_for(ws.recv(), 2))
            self.assertEqual(overlap_status["l1_hot_symbols"], 2)
            self.assertEqual(overlap_status["unique_pinned_symbols"], 2)
            self.assertEqual(overlap_status["active_upstream_symbols"], 2)

            # Moving the most recent monitored symbol into hot-only service
            # must make its old diagnostic raw frame unavailable to 8421.
            await ws.send(json.dumps({"op": "set_watchlist", "symbols": ["513520.SH"]}))
            watch_ack: dict[str, object] = {}
            while watch_ack.get("type") != "watchlist_ack":
                watch_ack = json.loads(await asyncio.wait_for(ws.recv(), 2))
            self.assertTrue(watch_ack["accepted"])
            await ws.send(json.dumps({"op": "raw_snapshot"}))
            raw: dict[str, object] = {}
            while raw.get("type") != "raw_snapshot":
                raw = json.loads(await asyncio.wait_for(ws.recv(), 2))
            if raw.get("available"):
                self.assertEqual(raw.get("routed_symbol"), "513520.SH")

        self.assertIn("159866.SZ", summary_symbols)
        self.assertNotIn("02800.HK", summary_symbols)
        self.assertGreaterEqual(int(overlap_status.get("l1_hot_ready", 0)), 1)

        reader, writer = await asyncio.open_connection("127.0.0.1", self.legacy_port)
        hello = json.loads(await reader.readline())
        self.assertEqual(hello["defaults"], ["513520.SH"])
        writer.write(json.dumps({"v": 1, "t": "subscribe", "id": "hkt",
                                 "symbols": ["02800.HK"], "interval_ms": 0}).encode() + b"\n")
        await writer.drain()
        book: dict[str, object] = {}
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            message = json.loads(await asyncio.wait_for(reader.readline(), 2))
            if message.get("t") == "l1" and message.get("books"):
                book = message["books"][0]
                break
        self.assertEqual(book.get("s"), "02800.HK")
        self.assertEqual(len(book.get("bp", [])), 5)
        self.assertEqual(len(book.get("ap", [])), 5)
        self.assertNotIn("iopv", book)
        writer.close()
        await writer.wait_closed()

        await asyncio.sleep(0.5)
        for process in (self.adapter, self.core):
            process.terminate()
            process.wait(timeout=4)
            if process.stdout is not None:
                process.stdout.close()
        persisted_lines: list[bytes] = []
        for path in (self.root / "data").glob("*.zst"):
            persisted_lines.extend(
                subprocess.check_output(["zstd", "-q", "-dc", str(path)]).splitlines()
            )
        for path in (self.root / "data").glob("*.jsonl"):
            persisted_lines.extend(path.read_bytes().splitlines())

        persisted_symbols: list[str] = []
        persisted_codes: list[str] = []
        for line in persisted_lines:
            if not line.strip():
                continue
            record = json.loads(line)
            symbol = record.get("s") or record.get("symbol")
            if symbol:
                persisted_symbols.append(str(symbol))
            event = record.get("event")
            if isinstance(event, dict):
                data = event.get("data")
                if isinstance(data, dict):
                    code = data.get("security_code", data.get("2"))
                    if code is not None:
                        persisted_codes.append(str(code))

        self.assertTrue(
            "159866.SZ" in persisted_symbols or "159866" in persisted_codes,
            (persisted_symbols, persisted_codes),
        )
        self.assertNotIn("02800.HK", persisted_symbols)
        self.assertNotIn("02800", persisted_codes)


if __name__ == "__main__":
    unittest.main()
