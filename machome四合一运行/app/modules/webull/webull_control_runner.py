#!/usr/bin/env python3
"""Replacement entry point that adds loopback-only control to Webull Gateway.

This file deliberately lives outside the original webull-lv2-gateway tree. It
imports and composes the original package without patching it. The original
instance lock means this runner and the legacy entry point cannot run together.
"""

from __future__ import annotations

import argparse
from concurrent.futures import Future, TimeoutError as FutureTimeout
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import sys
import threading
import time
from typing import Any


MAX_REQUEST_BYTES = 64 * 1024
MAX_TOKEN_BYTES = 1024
MAX_IDEMPOTENCY_RECORDS = 2048
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")
ALLOWED_ACTIONS = frozenset(
    {
        "set_schedule_mode",
        "collector_start",
        "collector_stop",
        "open_login",
        "restart_browser",
    }
)


def _safe_token_file(path: Path, *, create: bool) -> bytes:
    # Keep the final path component unresolved so O_NOFOLLOW can reject it.
    path = Path(os.path.abspath(os.path.expanduser(str(path))))
    if create:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent_metadata = os.lstat(path.parent)
    if not stat.S_ISDIR(parent_metadata.st_mode):
        raise ValueError("control token parent must be a directory")
    if parent_metadata.st_uid != os.geteuid():
        raise ValueError("control token parent must be owned by the current user")
    if stat.S_IMODE(parent_metadata.st_mode) & 0o077:
        raise ValueError("control token parent must not grant group/other access")
    if create and not path.exists():
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            payload = secrets.token_urlsafe(48).encode("ascii") + b"\n"
            cursor = 0
            while cursor < len(payload):
                cursor += os.write(descriptor, payload[cursor:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("control token must be a regular file")
        if stat.S_IMODE(before.st_mode) != 0o600:
            raise ValueError("control token mode must be exactly 0600")
        if before.st_uid != os.geteuid():
            raise ValueError("control token must be owned by the current user")
        if before.st_nlink != 1:
            raise ValueError("control token must have exactly one hard link")
        if before.st_size <= 0 or before.st_size > MAX_TOKEN_BYTES:
            raise ValueError("control token size is invalid")
        payload = os.read(descriptor, MAX_TOKEN_BYTES + 1)
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_size,
            before.st_nlink,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_uid,
            after.st_size,
            after.st_nlink,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if identity_before != identity_after or len(payload) != after.st_size:
            raise ValueError("control token changed while being read")
    finally:
        os.close(descriptor)

    token = payload.strip()
    if not 32 <= len(token) <= MAX_TOKEN_BYTES:
        raise ValueError("control token must contain 32-1024 bytes")
    if any(value <= 0x20 or value >= 0x7F for value in token):
        raise ValueError("control token must contain printable ASCII without spaces")
    return token


@dataclass(frozen=True)
class ControlInvocation:
    request_id: str
    action: str
    arguments: dict[str, Any]
    future: Future[dict[str, Any]]


def build_dispatcher(runtime: Any, store: Any) -> Any:
    # Imports remain lazy so protocol/server tests do not require PyQt6.
    from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal, pyqtSlot

    class ControlDispatcher(QObject):
        requested = pyqtSignal(object)

        def __init__(self) -> None:
            super().__init__()
            self.requested.connect(
                self._execute,
                Qt.ConnectionType.QueuedConnection,
            )

        @pyqtSlot(object)
        def _execute(self, invocation: ControlInvocation) -> None:
            if invocation.future.done():
                return
            action = invocation.action
            if action == "set_schedule_mode":
                mode = str(invocation.arguments.get("mode", ""))
                if mode not in tuple(runtime.VALID_MODES):
                    invocation.future.set_result(
                        {
                            "ok": False,
                            "request_id": invocation.request_id,
                            "action": action,
                            "error": "INVALID_ARGUMENT",
                            "message": "mode must be auto, force_running or force_stopped",
                        }
                    )
                    return
            try:
                if action == "set_schedule_mode":
                    runtime.set_mode(mode)
                elif action == "collector_start":
                    runtime.start_collector_now()
                elif action == "collector_stop":
                    runtime.stop_collector_now()
                elif action == "open_login":
                    runtime.open_login_window()

                    # When the collector was stopped, the original method
                    # starts its thread and immediately calls show_browser().
                    # The asyncio loop is installed a little later, so that
                    # first call can legitimately be a no-op. Retry only while
                    # the requested mode still permits a running collector;
                    # never resurrect one after a subsequent stop command.
                    def show_when_ready() -> None:
                        try:
                            if (
                                runtime.mode != "force_stopped"
                                and runtime.collector.running
                            ):
                                runtime.collector.show_browser()
                        except Exception:
                            logging.getLogger(__name__).exception(
                                "delayed Webull login-window display failed"
                            )

                    for delay_ms in (250, 750, 2_000):
                        QTimer.singleShot(delay_ms, show_when_ready)
                elif action == "restart_browser":
                    before = store.status().to_dict()
                    runtime.restart_browser()
                    deadline = time.monotonic() + 35.0
                    transition = {
                        "seen": before.get("browser") != "running"
                        or not bool(before.get("collector_running"))
                    }

                    def wait_for_restart_generation() -> None:
                        if invocation.future.done():
                            return
                        try:
                            current = store.status().to_dict()
                            browser = current.get("browser")
                            running = bool(current.get("collector_running"))
                            if browser != "running" or not running:
                                transition["seen"] = True
                            if transition["seen"] and browser == "running" and running:
                                invocation.future.set_result(
                                    {
                                        "ok": True,
                                        "request_id": invocation.request_id,
                                        "action": action,
                                        "schedule_mode": runtime.mode,
                                        "collector_running": True,
                                        "restart_transition_observed": True,
                                        "status": current,
                                    }
                                )
                                return
                            if time.monotonic() >= deadline:
                                invocation.future.set_result(
                                    {
                                        "ok": False,
                                        "request_id": invocation.request_id,
                                        "action": action,
                                        "error": "CONTROL_FAILED",
                                        "message": "browser restart did not complete before 35s deadline",
                                        "outcome_uncertain": True,
                                    }
                                )
                                return
                            QTimer.singleShot(100, wait_for_restart_generation)
                        except Exception:
                            logging.getLogger(__name__).exception(
                                "checking Webull restart completion failed"
                            )
                            invocation.future.set_result(
                                {
                                    "ok": False,
                                    "request_id": invocation.request_id,
                                    "action": action,
                                    "error": "CONTROL_FAILED",
                                    "message": "browser restart completion check failed",
                                    "outcome_uncertain": True,
                                }
                            )

                    QTimer.singleShot(100, wait_for_restart_generation)
                    return
                else:
                    raise ValueError("unsupported action")

                status_payload = store.status().to_dict()
                result = {
                    "ok": True,
                    "request_id": invocation.request_id,
                    "action": action,
                    "schedule_mode": runtime.mode,
                    "collector_running": bool(runtime.collector.running),
                    "status": status_payload,
                }
                invocation.future.set_result(result)
            except Exception:
                logging.getLogger(__name__).exception(
                    "Webull control failed action=%s request_id=%s",
                    invocation.action,
                    invocation.request_id,
                )
                invocation.future.set_result(
                    {
                        "ok": False,
                        "request_id": invocation.request_id,
                        "action": invocation.action,
                        "error": "CONTROL_FAILED",
                        "message": "control operation failed; inspect local gateway log",
                        "outcome_uncertain": True,
                    }
                )

    return ControlDispatcher()


class ControlBroker:
    def __init__(self, dispatcher: Any, timeout_seconds: float = 40.0) -> None:
        self.dispatcher = dispatcher
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._requests: dict[str, tuple[str, Future[dict[str, Any]], float]] = {}

    def submit(
        self,
        request_id: str,
        action: str,
        arguments: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        fingerprint = json.dumps(
            {"action": action, "arguments": arguments},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        now = time.monotonic()
        with self._lock:
            for key, (_, recorded_future, created_at) in list(self._requests.items()):
                # Never evict an unresolved mutation: doing so could execute the
                # same request_id twice after a prolonged Qt main-thread stall.
                if recorded_future.done() and now - created_at > 300:
                    self._requests.pop(key, None)
            existing = self._requests.get(request_id)
            if existing is not None:
                if not hmac.compare_digest(existing[0], fingerprint):
                    return HTTPStatus.CONFLICT, {
                        "ok": False,
                        "error": "IDEMPOTENCY_CONFLICT",
                        "message": "request_id was already used with another command",
                    }
                future = existing[1]
            else:
                if len(self._requests) >= MAX_IDEMPOTENCY_RECORDS:
                    return HTTPStatus.SERVICE_UNAVAILABLE, {
                        "ok": False,
                        "error": "CONTROL_BUSY",
                        "message": "idempotency registry is full; retry later",
                    }
                future = Future()
                self._requests[request_id] = (fingerprint, future, now)
                self.dispatcher.requested.emit(
                    ControlInvocation(request_id, action, arguments, future)
                )

        try:
            result = future.result(timeout=self.timeout_seconds)
        except FutureTimeout:
            return HTTPStatus.GATEWAY_TIMEOUT, {
                "ok": False,
                "request_id": request_id,
                "action": action,
                "error": "CONTROL_TIMEOUT",
                "message": "Qt control dispatcher did not answer before the deadline",
            }
        if result.get("ok"):
            return HTTPStatus.OK, result
        if result.get("error") == "INVALID_ARGUMENT":
            return HTTPStatus.BAD_REQUEST, result
        return HTTPStatus.INTERNAL_SERVER_ERROR, result


class LoopbackControlServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 16

    def __init__(
        self,
        address: tuple[str, int],
        token: bytes,
        broker: ControlBroker,
    ) -> None:
        self.control_token = token
        self.control_broker = broker
        super().__init__(address, ControlRequestHandler)


class ControlRequestHandler(BaseHTTPRequestHandler):
    server: LoopbackControlServer
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5.0)

    def log_message(self, fmt: str, *args: Any) -> None:
        logging.getLogger(__name__).debug("control-http " + fmt, *args)

    def _send(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(int(status_code))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _authorized(self) -> bool:
        try:
            remote = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        if not remote.is_loopback:
            return False
        value = self.headers.get("Authorization", "")
        if not value.lower().startswith("bearer "):
            return False
        try:
            candidate = value[7:].strip().encode("ascii", errors="strict")
        except UnicodeEncodeError:
            return False
        return hmac.compare_digest(candidate, self.server.control_token)

    def _require_authorized(self) -> bool:
        if self._authorized():
            return True
        self._send(
            HTTPStatus.UNAUTHORIZED,
            {"ok": False, "error": "AUTH_REJECTED", "message": "Bearer token required"},
        )
        return False

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if not self._require_authorized():
            return
        if self.path == "/v1/health":
            self._send(HTTPStatus.OK, {"ok": True, "service": "webull-control-runner", "version": 1})
            return
        if self.path == "/v1/capabilities":
            self._send(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "service": "webull-control-runner",
                    "version": 1,
                    "actions": sorted(ALLOWED_ACTIONS),
                },
            )
            return
        self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "NOT_FOUND"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if not self._require_authorized():
            return
        if self.path != "/v1/commands":
            self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "NOT_FOUND"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_REQUEST_BYTES:
            self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "REQUEST_TOO_LARGE"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except (OSError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
            self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "INVALID_JSON"})
            return
        if not isinstance(payload, dict):
            self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "INVALID_REQUEST"})
            return
        request_id = payload.get("request_id")
        action = payload.get("action")
        arguments = payload.get("arguments", {})
        if not isinstance(request_id, str) or not REQUEST_ID_PATTERN.fullmatch(request_id):
            self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "INVALID_REQUEST_ID"})
            return
        if not isinstance(action, str) or action not in ALLOWED_ACTIONS:
            self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "UNSUPPORTED_ACTION"})
            return
        if not isinstance(arguments, dict):
            self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "INVALID_ARGUMENTS"})
            return
        if action == "set_schedule_mode":
            if set(arguments) != {"mode"} or arguments.get("mode") not in {
                "auto",
                "force_running",
                "force_stopped",
            }:
                self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "INVALID_ARGUMENT"})
                return
        elif arguments:
            self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "INVALID_ARGUMENTS"})
            return

        status_code, result = self.server.control_broker.submit(
            request_id,
            action,
            arguments,
        )
        logging.getLogger(__name__).info(
            "control action=%s request_id=%s http=%s ok=%s",
            action,
            request_id,
            int(status_code),
            bool(result.get("ok")),
        )
        self._send(status_code, result)


class ControlServerThread:
    def __init__(self, server: LoopbackControlServer) -> None:
        self.server = server
        self.thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.2},
            name="webull-control-http",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self.thread is not threading.current_thread():
            self.thread.join(timeout=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Webull gateway with safe local control bridge")
    parser.add_argument(
        "--gateway-source",
        type=Path,
        default=Path("/Users/ellis/Desktop/ETF交割/WebullData/webull_lv2_gateway"),
        help="original webull_lv2_gateway project root",
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--control-port", type=int, default=18766)
    parser.add_argument("--control-token-file", type=Path, required=True)
    parser.add_argument(
        "--create-control-token",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not 1 <= args.control_port <= 65535:
        print("control port must be between 1 and 65535", file=sys.stderr)
        return 2

    source_root = args.gateway_source.expanduser().resolve()
    package_root = source_root / "src"
    if not (package_root / "webull_lv2_gateway" / "app.py").is_file():
        print(f"gateway source is invalid: {source_root}", file=sys.stderr)
        return 2
    sys.path.insert(0, str(package_root))

    try:
        from webull_lv2_gateway.app import _acquire_instance_lock, _configure_logging
        from webull_lv2_gateway.api_server import ApiServer
        from webull_lv2_gateway.collector import BrowserCollector
        from webull_lv2_gateway.config import load_config
        from webull_lv2_gateway.main_window import GatewayWindow
        from webull_lv2_gateway.runtime import GatewayRuntime
        from webull_lv2_gateway.store import StateStore
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication

        config = load_config(args.config)
        if not config.ui.close_to_tray:
            raise ValueError(
                "control runner requires ui.close_to_tray=true to avoid a "
                "closed-window/runtime-stopped process with a live control port"
            )
        control_token_path = Path(
            os.path.abspath(os.path.expanduser(str(args.control_token_file)))
        )
    except Exception as exc:
        print(f"runner configuration failed: {exc}", file=sys.stderr)
        return 2

    config.storage.runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        instance_lock = _acquire_instance_lock(
            config.storage.runtime_dir / "gateway.lock"
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 3

    # Do not create or otherwise touch the token before acquiring the original
    # instance lock. An accidental launch while the legacy process owns Webull
    # must be a no-op apart from the lock probe.
    try:
        control_token = _safe_token_file(
            control_token_path,
            create=args.create_control_token,
        )
    except Exception as exc:
        print(f"runner control token failed validation: {exc}", file=sys.stderr)
        instance_lock.close()
        return 2

    try:
        store = StateStore(config.storage)
        _configure_logging(config.storage.log_dir, store)
        log = logging.getLogger(__name__)
        log.info("starting Webull gateway through loopback control runner")

        app = QApplication(sys.argv[:1])
        app.setApplicationName("Webull LV2 Gateway")
        app.setOrganizationName("Ellis")
        app.setQuitOnLastWindowClosed(False)

        collector = BrowserCollector(config, store)
        api = ApiServer(config, store)
        runtime = GatewayRuntime(config, store, collector, api)
        expected_methods = {
            "set_mode",
            "start_collector_now",
            "stop_collector_now",
            "open_login_window",
            "restart_browser",
            "shutdown",
        }
        missing_methods = sorted(
            name for name in expected_methods if not callable(getattr(runtime, name, None))
        )
        if missing_methods:
            raise RuntimeError(
                "incompatible GatewayRuntime; missing methods: " + ", ".join(missing_methods)
            )
        if not {"auto", "force_running", "force_stopped"}.issubset(
            set(runtime.VALID_MODES)
        ):
            raise RuntimeError("incompatible GatewayRuntime.VALID_MODES")
        window = GatewayWindow(config, store, runtime, api)
        dispatcher = build_dispatcher(runtime, store)
        broker = ControlBroker(dispatcher)
    except Exception as exc:
        print(f"runner bootstrap failed: {exc}", file=sys.stderr)
        instance_lock.close()
        return 5
    try:
        control_http = LoopbackControlServer(
            ("127.0.0.1", args.control_port),
            control_token,
            broker,
        )
    except OSError as exc:
        print(f"cannot bind loopback control server: {exc}", file=sys.stderr)
        instance_lock.close()
        return 4
    control_server = ControlServerThread(control_http)

    stopped = False

    def shutdown() -> None:
        nonlocal stopped
        if stopped:
            return
        stopped = True
        try:
            control_server.stop()
        except Exception:
            log.exception("Webull control server shutdown failed")
        try:
            runtime.shutdown()
        except Exception:
            log.exception("Webull gateway shutdown failed")

    app.aboutToQuit.connect(shutdown)
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal_timer = QTimer()
    signal_timer.start(500)
    signal_timer.timeout.connect(lambda: None)

    try:
        runtime.start()
    except Exception:
        log.exception("Webull gateway startup failed")
        try:
            runtime.shutdown()
        except Exception:
            log.exception("Webull gateway cleanup after startup failure failed")
        control_http.server_close()
        instance_lock.close()
        return 5
    control_server.start()
    if config.ui.start_minimized:
        window.hide()
    else:
        window.show()

    try:
        return app.exec()
    finally:
        try:
            shutdown()
        finally:
            instance_lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
