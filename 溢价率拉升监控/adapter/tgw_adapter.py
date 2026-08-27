#!/usr/bin/env python3
"""TGW login/subscription owner and audited raw-event bridge.

No price parsing or premium calculation occurs here. The live path imports the
project-private tgw_macos wheel; simulation is deterministic and needs no SDK.
"""
from __future__ import annotations

import argparse
import configparser
import json
import logging
from logging.handlers import TimedRotatingFileHandler
import queue
import random
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from proto_wire import BridgeFrame, encode_framed, take_frames


LOG = logging.getLogger("tgw-adapter")
MAX_EVENTS = 10_000
SUBSCRIPTION_BATCH_SIZE = 20
MAX_RECONCILE_RETRY_SEC = 30.0


def strip_inline_comment(value: str) -> str:
    return value.split("#", 1)[0].strip()


class Adapter:
    def __init__(self, socket_path: Path, watchlist_path: Path, simulate: bool,
                 account_path: Path | None, username_file: Path | None = None):
        self.socket_path = socket_path
        self.watchlist_path = watchlist_path
        self.simulate = simulate
        self.account_path = account_path
        self.username_file = username_file
        self.credential_secrets: set[str] = set()
        self.session_id = str(uuid.uuid4())
        self.sequence = 0
        self.desired: set[str] = set()
        self.quotes_desired = simulate
        self.events: queue.Queue[tuple[dict[str, Any], int, int]] = queue.Queue(maxsize=MAX_EVENTS)
        self.stop = threading.Event()
        self.dropped = 0
        self.socket: socket.socket | None = None
        self.sdk: Any = None
        self.subscriptions: dict[str, Any] = {}
        self.send_lock = threading.Lock()
        self.connection_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.bridge_epoch = 0
        self.sdk_bridge_epoch = -1
        self.reconcile_retry_at = 0.0
        self.reconcile_retry_delay = 1.0

    def load_defaults(self) -> None:
        payload = json.loads(self.watchlist_path.read_text(encoding="utf-8"))
        values = payload.get("symbols", payload) if isinstance(payload, dict) else payload
        with self.state_lock:
            self.desired = {str(value).upper() for value in values}

    def connect_core(self) -> None:
        with self.connection_lock:
            if self.socket is not None:
                return
            delay = 0.1
            while not self.stop.is_set():
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    sock.connect(str(self.socket_path))
                    sock.settimeout(0.5)
                    self.socket = sock
                    self.bridge_epoch += 1
                    self._status("connected_to_core", {"bridge_epoch": self.bridge_epoch})
                    return
                except OSError:
                    sock.close()
                    time.sleep(delay)
                    delay = min(5.0, delay * 2)

    def _send(self, frame: BridgeFrame) -> None:
        while not self.stop.is_set():
            if self.socket is None:
                self.connect_core()
            try:
                assert self.socket is not None
                with self.send_lock:
                    # Sequence assignment and socket write are one critical
                    # section so status/control and market threads can never
                    # put adapter_seq on the wire out of order.
                    self.sequence += 1
                    frame.sequence = self.sequence
                    frame.session_id = self.session_id
                    payload = encode_framed(frame)
                    self.socket.sendall(payload)
                return
            except OSError as exc:
                LOG.warning("core send failed: %s", exc)
                if self.socket:
                    self.socket.close()
                self.socket = None

    def _status(self, message: str, detail: dict[str, Any] | None = None) -> None:
        self._send(BridgeFrame(kind=2, sdk_queue_depth=self.events.qsize(), message=message,
                               payload_json=json.dumps(detail or {}, separators=(",", ":")).encode()))

    def control_reader(self) -> None:
        buffer = bytearray()
        while not self.stop.is_set():
            if self.socket is None:
                time.sleep(0.1)
                continue
            try:
                chunk = self.socket.recv(65536)
                if not chunk:
                    raise ConnectionError("core closed bridge")
                buffer.extend(chunk)
                for frame in take_frames(buffer):
                    if frame.kind != 3:
                        continue
                    request = json.loads(frame.payload_json or b"{}")
                    if request.get("op") == "set_symbols":
                        desired = {str(value).upper() for value in request.get("symbols", [])}
                        with self.state_lock:
                            self.quotes_desired = bool(request.get("quotes_desired"))
                        self.apply_desired(desired)
            except socket.timeout:
                continue
            except (OSError, ConnectionError, ValueError, json.JSONDecodeError) as exc:
                LOG.warning("control channel reset: %s", exc)
                if self.socket:
                    self.socket.close()
                self.socket = None
                buffer.clear()
                time.sleep(0.2)

    def apply_desired(self, desired: set[str]) -> None:
        # The bridge reader never calls the TGW SDK.  Subscribe, unsubscribe,
        # receive and close are serialized by live_loop on one owner thread.
        with self.state_lock:
            old = set(self.desired)
            self.desired = set(desired)
            quotes_desired = self.quotes_desired
        self.reconcile_retry_at = 0.0
        self._status("subscription_set_updated", {
            "desired": len(desired),
            "added": len(desired - old),
            "removed": len(old - desired),
            "quotes_desired": quotes_desired,
        })

    def desired_snapshot(self) -> tuple[set[str], bool]:
        with self.state_lock:
            return set(self.desired), bool(self.quotes_desired)

    def enqueue_event(self, event: dict[str, Any]) -> None:
        received = (event, time.time_ns(), time.monotonic_ns())
        try:
            self.events.put_nowait(received)
        except queue.Full:
            self.dropped += 1
            # Explicitly count loss; unlike the SDK's historic silent drop, no
            # old market frame is overwritten without an audit record.
            if self.dropped == 1 or self.dropped % 100 == 0:
                self._status("adapter_queue_overflow", {"dropped": self.dropped, "depth": self.events.qsize()})

    def event_writer(self) -> None:
        while not self.stop.is_set():
            try:
                event, received_wall, received_mono = self.events.get(timeout=0.5)
            except queue.Empty:
                continue
            headers = event.get("headers") if isinstance(event.get("headers"), dict) else {}
            try:
                encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
            except (TypeError, ValueError) as exc:
                self._status("raw_json_rejected", {"error": str(exc)})
                continue
            self._send(BridgeFrame(kind=1, receive_wall_ns=received_wall,
                                   receive_monotonic_ns=received_mono,
                                   is_delta=bool(event.get("is_delta")),
                                   tag=str(headers.get("tag", "")), payload_json=encoded,
                                   sdk_queue_depth=self.events.qsize()))

    def simulation_loop(self) -> None:
        randomizer = random.Random(8421)
        states: dict[str, dict[str, Any]] = {}
        full_pending: set[str] = set()
        tick = 0
        observed_bridge_epoch = -1
        while not self.stop.is_set():
            if observed_bridge_epoch != self.bridge_epoch:
                desired, _ = self.desired_snapshot()
                full_pending.update(desired)
                observed_bridge_epoch = self.bridge_epoch
            desired, quotes_desired = self.desired_snapshot()
            if not quotes_desired:
                time.sleep(0.25)
                continue
            for symbol in sorted(desired):
                if symbol not in states:
                    seed = int(symbol[:6]) % 400_000
                    # ETF/LOF 场内报价最小变动单位为 0.001；仿真价也必须落在
                    # price_e6 的 1_000 整数倍，避免 UI 测试被误认为浮点抖动。
                    iopv = 800_000 + (seed // 1_000) * 1_000
                    prices = [iopv - index * 1_000 for index in range(10)]
                    states[symbol] = {
                        "security_code": symbol,
                        "market_type": 101 if symbol.endswith(".SH") else 102,
                        "orig_time": int(time.time() * 1000), "last_price": iopv,
                        "open_price": iopv, "high_price": iopv, "low_price": iopv,
                        "close_price": 0, "pre_close_price": iopv, "bid_price": prices,
                        "offer_price": [iopv + (index + 1) * 1_000 for index in range(10)],
                        "bid_volume": [100_000 + index * 10_000 for index in range(10)],
                        "offer_volume": [110_000 + index * 10_000 for index in range(10)],
                        "total_volume_trade": 1_000_000, "total_value_trade": iopv * 10,
                        "num_trades": 100, "trading_phase_code": "T", "IOPV": iopv,
                        "high_limited": ((iopv * 11 // 10 + 500) // 1_000) * 1_000,
                        "low_limited": ((iopv * 9 // 10 + 500) // 1_000) * 1_000,
                    }
                    full_pending.add(symbol)
            symbols = sorted(desired)
            if not symbols:
                time.sleep(0.25)
                continue
            batch = symbols[tick % len(symbols) : (tick % len(symbols)) + 20]
            if len(batch) < 20:
                batch += symbols[: 20 - len(batch)]
            for symbol in batch:
                state = states[symbol]
                state["orig_time"] = int(time.time() * 1000)
                state["num_trades"] += 1
                state["total_volume_trade"] += 10_000
                # Every 12 seconds one symbol performs a deterministic premium jump.
                jump = 20_000 if tick % 240 >= 180 and symbol == symbols[0] else 0
                noise = randomizer.randint(-1, 1) * 1_000
                bid1 = state["IOPV"] + jump + noise
                state["bid_price"] = [max(1, bid1 - index * 1_000) for index in range(10)]
                state["offer_price"] = [bid1 + (index + 1) * 1_000 for index in range(10)]
                state["last_price"] = bid1
                state["high_price"] = max(state["high_price"], bid1)
                state["low_price"] = min(state["low_price"], bid1)
                if symbol in full_pending:
                    payload = dict(state)
                    full_pending.remove(symbol)
                    is_delta = False
                else:
                    payload = {key: state[key] for key in ("security_code", "orig_time", "last_price", "high_price", "low_price",
                                                           "bid_price", "offer_price", "total_volume_trade", "num_trades")}
                    is_delta = True
                self.enqueue_event({"headers": {"tag": "14"}, "status": 0,
                                    "is_delta": 1 if is_delta else 0, "data": payload,
                                    "simulation": True})
            tick += 20
            time.sleep(0.05)

    def login_live(self) -> None:
        if self.account_path is None:
            raise RuntimeError("live mode requires --account")
        import tgw_macos as tgw  # installed only into the project-private venv

        parser = configparser.ConfigParser()
        with self.account_path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
        section = parser["galaxy"]
        username = strip_inline_comment(section["username"])
        if self.username_file is not None:
            username = self.username_file.read_text(encoding="utf-8").strip()
            if not username:
                raise RuntimeError("--username-file is empty")
        password = section["password"].strip()
        self.credential_secrets = {value for value in (username, password) if value}
        cfg = tgw.Cfg().set(server_vip=strip_inline_comment(section["host"]), server_port=section.getint("port"),
                            username=username, password=password,
                            force_logout=section.getboolean("force_logout", fallback=False))
        mode = getattr(tgw.ApiMode, strip_inline_comment(section.get("api_mode", "kInternetMode")))
        if not tgw.Login(cfg, mode):
            raise RuntimeError("TGW login returned false")
        self.sdk = tgw
        self.sdk_bridge_epoch = self.bridge_epoch
        self.session_id = str(uuid.uuid4())
        self.subscriptions.clear()
        self.reconcile_retry_at = 0.0
        self.reconcile_retry_delay = 1.0
        desired, quotes_desired = self.desired_snapshot()
        self._status("tgw_logged_in", {
            "desired": len(desired),
            "quotes_desired": quotes_desired,
            "sdk_version": getattr(tgw, "__version__", "unknown"),
        })

    def _make_subscribe_item(self, symbol: str) -> Any:
        assert self.sdk is not None
        item = self.sdk.SubscribeItem().set_code(symbol[:6])
        item.market = self.sdk.MarketType.kSSE if symbol.endswith(".SH") else self.sdk.MarketType.kSZSE
        item.flag = self.sdk.SubscribeDataType.kSnapshot
        item.category_type = 0
        return item

    def _subscribe_live_many(self, symbols: list[str]) -> bool:
        _, quotes_desired = self.desired_snapshot()
        if self.sdk is None or not quotes_desired:
            return True
        candidates: list[tuple[str, Any]] = []
        for symbol in symbols:
            if symbol in self.subscriptions:
                continue
            candidates.append((symbol, self._make_subscribe_item(symbol)))
        for offset in range(0, len(candidates), SUBSCRIPTION_BATCH_SIZE):
            batch = candidates[offset:offset + SUBSCRIPTION_BATCH_SIZE]
            started = time.monotonic()
            result = int(self.sdk.Subscribe([item for _, item in batch]))
            detail = {
                "offset": offset,
                "symbols": len(batch),
                "result": result,
                "latency_ms": round((time.monotonic() - started) * 1000, 3),
                "active": len(self.subscriptions),
            }
            if result != 0:
                self._status("subscribe_rejected", detail)
                return False
            self.subscriptions.update(batch)
            detail["active"] = len(self.subscriptions)
            self._status("subscribe_accepted", detail)
        return True

    def _subscribe_live(self, symbol: str) -> bool:
        return self._subscribe_live_many([symbol])

    def _unsubscribe_live_many(self, symbols: list[str]) -> bool:
        if self.sdk is None:
            return True
        candidates = [(symbol, self.subscriptions[symbol])
                      for symbol in symbols if symbol in self.subscriptions]
        for offset in range(0, len(candidates), SUBSCRIPTION_BATCH_SIZE):
            batch = candidates[offset:offset + SUBSCRIPTION_BATCH_SIZE]
            started = time.monotonic()
            request: Any = batch[0][1] if len(batch) == 1 else [item for _, item in batch]
            result = int(self.sdk.UnSubscribe(request))
            detail = {
                "offset": offset,
                "symbols": len(batch),
                "result": result,
                "latency_ms": round((time.monotonic() - started) * 1000, 3),
                "active": len(self.subscriptions),
            }
            if result != 0:
                self._status("unsubscribe_rejected", detail)
                return False
            for symbol, _ in batch:
                self.subscriptions.pop(symbol, None)
            detail["active"] = len(self.subscriptions)
            self._status("unsubscribe_accepted", detail)
        return True

    def _reconcile_live(self) -> bool:
        if self.sdk is None:
            return True
        desired, quotes_desired = self.desired_snapshot()
        active = set(self.subscriptions)
        remove = sorted(active if not quotes_desired else active - desired)
        if remove and not self._unsubscribe_live_many(remove):
            return False
        if quotes_desired:
            add = sorted(desired - set(self.subscriptions))
            if add and not self._subscribe_live_many(add):
                return False
        return set(self.subscriptions) == (desired if quotes_desired else set())

    def live_loop(self) -> None:
        delay = 1.0
        while not self.stop.is_set():
            try:
                _, quotes_desired = self.desired_snapshot()
                if self.sdk is None:
                    if not quotes_desired:
                        time.sleep(0.25)
                        continue
                    self.login_live()
                    delay = 1.0
                elif self.sdk_bridge_epoch != self.bridge_epoch:
                    self.sdk.Close()
                    self.sdk = None
                    self.subscriptions.clear()
                    continue
                if time.monotonic() >= self.reconcile_retry_at:
                    reconciled = self._reconcile_live()
                    if reconciled:
                        self.reconcile_retry_delay = 1.0
                        self.reconcile_retry_at = 0.0
                    else:
                        self.reconcile_retry_at = time.monotonic() + self.reconcile_retry_delay
                        self.reconcile_retry_delay = min(
                            MAX_RECONCILE_RETRY_SEC, self.reconcile_retry_delay * 2
                        )
                _, quotes_desired = self.desired_snapshot()
                if not quotes_desired:
                    # End the upstream session after the final unsubscribe.
                    # A fresh login next session clears SDK queues and forces
                    # every symbol to obtain a new full before publication.
                    if not self.subscriptions and self.sdk is not None:
                        self.sdk.Close()
                        self.sdk = None
                        self._status("tgw_session_closed", {"reason": "quotes_not_desired"})
                    time.sleep(0.25)
                    continue
                try:
                    event = self.sdk.ReceiveRawEvent(timeout=1.0)
                except TimeoutError:
                    continue
                if isinstance(event, dict):
                    self.enqueue_event(event)
            except Exception as exc:  # hard SDK boundary; next pass creates a fresh session
                error_text = str(exc)
                for secret in self.credential_secrets:
                    error_text = error_text.replace(secret, "<redacted>")
                safe_error = f"{type(exc).__name__}: {error_text}"
                LOG.warning("TGW connection attempt failed: %s", safe_error)
                self._status("tgw_connection_failed", {"error": safe_error, "retry_sec": delay})
                try:
                    if self.sdk is not None:
                        self.sdk.Close()
                finally:
                    self.sdk = None
                    self.subscriptions.clear()
                time.sleep(delay)
                delay = min(30.0, delay * 2)

    def run(self) -> None:
        self.load_defaults()
        self.connect_core()
        threading.Thread(target=self.control_reader, name="bridge-control", daemon=True).start()
        threading.Thread(target=self.event_writer, name="bridge-writer", daemon=True).start()
        if self.simulate:
            self.simulation_loop()
        else:
            self.live_loop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("--watchlist", required=True, type=Path)
    parser.add_argument("--account", type=Path)
    parser.add_argument("--username-file", type=Path,
                        help="optional one-run username override; file content is never logged")
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(TimedRotatingFileHandler(args.log, when="midnight", backupCount=30, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    adapter = Adapter(args.socket.resolve(), args.watchlist.resolve(), args.simulate,
                      args.account.resolve() if args.account else None,
                      args.username_file.resolve() if args.username_file else None)
    try:
        adapter.run()
    except KeyboardInterrupt:
        pass
    finally:
        adapter.stop.set()
        if adapter.sdk is not None:
            adapter.sdk.Close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
