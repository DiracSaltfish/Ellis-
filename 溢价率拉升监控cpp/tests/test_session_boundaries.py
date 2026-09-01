from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapter"))

from proto_wire import BridgeFrame, decode, encode_framed  # noqa: E402


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_path(path: Path, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise TimeoutError(f"socket path did not appear: {path}")


def wait_port(port: int, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.02)
    raise TimeoutError(f"port did not open: {port}")


def read_frame(stream: socket.socket, timeout: float = 5.0) -> BridgeFrame:
    stream.settimeout(timeout)
    header = b""
    while len(header) < 4:
        part = stream.recv(4 - len(header))
        if not part:
            raise EOFError("bridge closed before frame header")
        header += part
    size = struct.unpack(">I", header)[0]
    payload = b""
    while len(payload) < size:
        part = stream.recv(size - len(payload))
        if not part:
            raise EOFError("bridge closed before frame payload")
        payload += part
    return decode(payload)


def read_json_line(stream, wanted: str, timeout: float = 5.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = stream.readline()
        if not line:
            raise EOFError("legacy connection closed")
        message = json.loads(line)
        if message.get("t") == wanted:
            return message
    raise TimeoutError(f"legacy message not received: {wanted}")


def domestic_full() -> bytes:
    now = datetime.now()
    orig_time = int(now.strftime("%Y%m%d%H%M%S") + f"{now.microsecond // 1000:03d}")
    event = {
        "headers": {"tag": "14"},
        "status": 0,
        "is_delta": 0,
        "data": {
            "1": 102,
            "2": "159866",
            "3": 2,
            "4": orig_time,
            "5": "T111\u0000\u0000\u0000",
            "6": 1_650_000,
            "7": 1_651_000,
            "8": 1_655_000,
            "9": 1_648_000,
            "10": 1_654_000,
            "11": 0,
            "12": "1653000|1652000|1651000|1650000|1649000|0|0|0|0|0",
            "13": "100000|90000|80000|70000|60000|0|0|0|0|0",
            "14": "1654000|1655000|1656000|1657000|1658000|0|0|0|0|0",
            "15": "110000|120000|130000|140000|150000|0|0|0|0|0",
            "16": 100,
            "17": 1_000_000,
            "18": 1_654_000_000,
            "19": 1_650_000,
            "20": 1_815_000,
            "21": 1_485_000,
        },
    }
    return json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode()


class CoreInvalidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="pm-boundary-", dir="/tmp")
        root = Path(self.temp.name)
        for name in ("config", "runtime", "data", "logs"):
            (root / name).mkdir()
        self.monitor_port = free_port()
        self.legacy_port = free_port()
        config = {
            "mode": "simulation",
            "listen_host": "127.0.0.1",
            "monitor_port": self.monitor_port,
            "legacy_l1_port": self.legacy_port,
            "adapter_socket": "runtime/tgw.sock",
            "watchlist": "config/watchlist.json",
            "l1_hotlist": "config/l1_hotlist.json",
            "enable_hkt_l1": True,
            "security_names": "config/security_names.tsv",
            "data_dir": "data",
            "log_dir": "logs",
            "max_monitor_clients": 4,
            "max_detail_symbols_per_client": 4,
            "max_l1_clients": 4,
            "max_l1_symbols_per_client": 4,
            "max_upstream_symbols": 8,
            "dynamic_unsubscribe_grace_sec": 1,
        }
        (root / "config/app.json").write_text(json.dumps(config), encoding="utf-8")
        (root / "config/watchlist.json").write_text(
            json.dumps({"version": 1, "symbols": ["159866.SZ"]}), encoding="utf-8"
        )
        (root / "config/l1_hotlist.json").write_text(
            json.dumps({"version": 1, "symbols": []}), encoding="utf-8"
        )
        (root / "config/security_names.tsv").write_text("159866.SZ\t日经ETF工银\n", encoding="utf-8")
        self.root = root
        core = Path(os.environ["PREMIUM_CORE_BINARY"])
        self.process = subprocess.Popen(
            [str(core), "--config", str(root / "config/app.json"), "--simulation", "--force-quotes"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        wait_port(self.legacy_port)
        wait_path(root / "runtime/tgw.sock")
        self.bridge = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.bridge.connect(str(root / "runtime/tgw.sock"))
        # Consume core's set_symbols control so a malformed/partial unread frame
        # cannot obscure failures in the reverse direction.
        control = read_frame(self.bridge)
        self.assertEqual(control.kind, 3)

    def tearDown(self) -> None:
        self.bridge.close()
        self.process.terminate()
        try:
            self.process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)
        if self.process.stdout is not None:
            self.process.stdout.close()
        self.temp.cleanup()

    def subscribe_once(self) -> dict[str, object]:
        connection = socket.create_connection(("127.0.0.1", self.legacy_port), timeout=2)
        stream = connection.makefile("rwb", buffering=0)
        read_json_line(stream, "hello")
        stream.write(b'{"v":1,"t":"subscribe","symbols":["159866.SZ"],"interval_ms":0}\n')
        read_json_line(stream, "ack")
        result = read_json_line(stream, "l1")
        stream.close()
        connection.close()
        return result

    def test_same_session_failure_clears_every_downstream_cache(self) -> None:
        session = "native-live-test-session"
        self.bridge.sendall(encode_framed(BridgeFrame(
            kind=2, sequence=1, session_id=session, message="tgw_logged_in"
        )))
        self.bridge.sendall(encode_framed(BridgeFrame(
            kind=1,
            sequence=2,
            session_id=session,
            receive_wall_ns=time.time_ns(),
            receive_monotonic_ns=time.monotonic_ns(),
            tag="14",
            payload_json=domestic_full(),
        )))
        deadline = time.monotonic() + 4
        initial: dict[str, object] = {}
        while time.monotonic() < deadline:
            initial = self.subscribe_once()
            if initial.get("books"):
                break
            time.sleep(0.05)
        self.assertTrue(initial.get("books"), initial)

        # The failure deliberately keeps the same session id. This reproduces
        # the old Python adapter's cross-day failure mode: session-id-only
        # invalidation is insufficient.
        self.bridge.sendall(encode_framed(BridgeFrame(
            kind=2, sequence=3, session_id=session, message="tgw_connection_failed"
        )))
        deadline = time.monotonic() + 4
        stale: dict[str, object] = {}
        while time.monotonic() < deadline:
            stale = self.subscribe_once()
            if not stale.get("books"):
                break
            time.sleep(0.05)
        self.assertEqual(stale.get("books"), [])
        self.assertEqual(stale.get("missing"), ["159866.SZ"])


class NativeAdapterBoundaryTests(unittest.TestCase):
    def test_disable_enable_creates_new_session_and_new_full(self) -> None:
        native = Path(os.environ["PREMIUM_TGW_BINARY"])
        with tempfile.TemporaryDirectory(prefix="pm-native-cycle-", dir="/tmp") as temporary:
            root = Path(temporary)
            socket_path = root / "core.sock"
            watchlist = root / "watchlist.json"
            watchlist.write_text(json.dumps({"version": 1, "symbols": ["159866.SZ"]}), encoding="utf-8")
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(socket_path))
            server.listen(1)
            server.settimeout(5)
            process = subprocess.Popen(
                [str(native), "--simulate", "--socket", str(socket_path),
                 "--watchlist", str(watchlist), "--log", str(root / "native.log")],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            connection = None
            try:
                connection, _ = server.accept()
                first = read_frame(connection)
                self.assertEqual(first.message, "connected_to_core")

                def control(enabled: bool) -> None:
                    payload = json.dumps({
                        "op": "set_symbols",
                        "symbols": ["159866.SZ"],
                        "quotes_desired": enabled,
                    }, separators=(",", ":")).encode()
                    connection.sendall(encode_framed(BridgeFrame(kind=3, payload_json=payload)))

                def wait_session() -> tuple[str, BridgeFrame]:
                    announced = ""
                    deadline = time.monotonic() + 6
                    while time.monotonic() < deadline:
                        frame = read_frame(connection, max(0.2, deadline - time.monotonic()))
                        if frame.message == "simulation_session_started":
                            announced = frame.session_id
                        if frame.kind == 1 and announced:
                            return announced, frame
                    raise TimeoutError("simulation session/full not received")

                control(True)
                first_session, first_market = wait_session()
                self.assertFalse(first_market.is_delta)

                control(False)
                closed = None
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    candidate = read_frame(connection, max(0.2, deadline - time.monotonic()))
                    if candidate.message == "tgw_session_closed":
                        closed = candidate
                        break
                self.assertIsNotNone(closed)
                self.assertNotEqual(closed.session_id, first_session)

                control(True)
                second_session, second_market = wait_session()
                self.assertNotEqual(second_session, first_session)
                self.assertFalse(second_market.is_delta)
            finally:
                if connection is not None:
                    connection.close()
                server.close()
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
                if process.stdout is not None:
                    process.stdout.close()


if __name__ == "__main__":
    unittest.main()
