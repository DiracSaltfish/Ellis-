"""TGW backend selection and lifecycle management.

The default backend is the real TLS/WebSocket implementation. Simulation and
the old C++ TCP-only skeleton are opt-in so production code can no longer report
a false successful login after a mere TCP connect.
"""
from __future__ import annotations

import ctypes
import os
from typing import Any

from ._protocol import (
    TgwProtocolError,
    TgwTransportError,
    TgwWssClient,
    build_kline_request,
    build_query_complete_request,
    build_snapshot_request,
    build_third_info_request,
    parse_kline_packets,
    parse_snapshot_packets,
    parse_third_info_packets,
)


class BackendState:
    IDLE, CONNECTED, LOGGED_IN, CLOSED = 0, 1, 2, 3


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.split(b"\0", 1)[0].decode("utf-8")
    return str(value)


def _find_core_lib() -> str | None:
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.environ.get("TGWCORE_DYLIB", ""),
        os.path.join(here, "../../../macos_build/build/libtgw_core.dylib"),
        os.path.join(here, "../../../../macos_build/build/libtgw_core.dylib"),
        "libtgw_core.dylib",
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return os.path.abspath(path)
    return None


def _find_ca_file(path: str = "") -> str | None:
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [os.environ.get("TGW_CA_FILE", "")]
    if path:
        candidates.extend([
            path,
            os.path.join(path, ".ca.crt"),
            os.path.join(path, "cert", ".ca.crt"),
        ])
    candidates.extend([
        os.path.join(here, "cert", ".ca.crt"),
        os.path.join(here, "../../../analysis/tgw_whl/tgw/cert/.ca.crt"),
        os.path.join(here, "../../../../analysis/tgw_whl/tgw/cert/.ca.crt"),
    ])
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None


class BaseBackend:
    """Common backend state and logging; not a successful simulated server."""

    def __init__(self) -> None:
        self.state = BackendState.IDLE
        self.cfg: dict[str, Any] | None = None
        self.api_mode: int | None = None
        self.log_lines: list[str] = []
        self.logon_response: dict[str, Any] | None = None
        self.last_error = ""
        self.log_spi: Any = None

    def set_log_spi(self, spi: Any) -> None:
        self.log_spi = spi

    def log(self, level: str, message: str) -> None:
        line = f"[tgw][{level}] {message}"
        self.log_lines.append(line)
        if self.log_spi is not None:
            callback = getattr(self.log_spi, "on_log", None)
            if callable(callback):
                callback(level, message)
                return
        print(line)

    def init(self, cfg_dict: dict[str, Any], api_mode: int, path: str = "") -> int:
        raise NotImplementedError

    def login(self) -> int:
        raise NotImplementedError

    def close(self) -> None:
        self.state = BackendState.CLOSED

    def subscribe(self, items: list[dict[str, Any]]) -> int:
        raise NotImplementedError

    def unsubscribe(self, items: list[dict[str, Any]]) -> int:
        raise NotImplementedError

    def query(self, kind: str, req: Any) -> Any:
        raise NotImplementedError(f"{kind} wire request is not implemented yet")


class LiveBackend(BaseBackend):
    def __init__(self) -> None:
        super().__init__()
        heartbeat = float(os.environ.get("TGW_HEARTBEAT_SEC", "5"))
        timeout = float(os.environ.get("TGW_TIMEOUT_SEC", "15"))
        self.client = TgwWssClient(timeout=timeout, heartbeat_sec=heartbeat)
        self.timeout = timeout
        self.ca_file: str | None = None
        self.server_name: str | None = None

    def init(self, cfg_dict: dict[str, Any], api_mode: int, path: str = "") -> int:
        if self.state != BackendState.IDLE:
            self.last_error = "backend has already been initialized"
            return -1
        if int(api_mode) != 2:
            self.last_error = "native macOS backend currently supports internet mode only"
            return -1
        self.cfg = cfg_dict.copy()
        self.api_mode = int(api_mode)
        self.ca_file = _find_ca_file(path)
        self.server_name = os.environ.get("TGW_TLS_SERVER_NAME") or None
        try:
            self.client.connect(
                _as_text(cfg_dict["server_vip"]),
                int(cfg_dict["server_port"]),
                ca_file=self.ca_file,
                server_name=self.server_name,
            )
        except Exception as exc:
            self.last_error = str(exc)
            self.log("ERROR", f"TLS/WebSocket connection failed: {type(exc).__name__}: {exc}")
            self.client.close()
            return -1
        self.state = BackendState.CONNECTED
        self.log("INFO", "TLS/WebSocket transport established")
        return 0

    def login(self) -> int:
        if self.state != BackendState.CONNECTED or self.cfg is None:
            self.last_error = "backend is not connected"
            return -1
        version = os.environ.get("TGW_CLIENT_VERSION", "V4.3.0.260626-rc2.0-YHZQ")
        try:
            response = self.client.logon(
                _as_text(self.cfg["username"]),
                _as_text(self.cfg["password"]),
                force_logout=bool(self.cfg.get("force_logout", False)),
                client_version=version,
            )
        except (TgwProtocolError, TgwTransportError, TimeoutError, OSError) as exc:
            self.last_error = str(exc)
            self.log("ERROR", f"server logon failed: {type(exc).__name__}: {exc}")
            return -1
        self.logon_response = response
        self.state = BackendState.LOGGED_IN
        self.log("INFO", "server authenticated session established")
        return 0

    def subscribe(self, items: list[dict[str, Any]]) -> int:
        if self.state != BackendState.LOGGED_IN:
            return -1
        try:
            self.client.subscribe(items)
            return 0
        except Exception as exc:
            self.last_error = str(exc)
            self.log("ERROR", f"subscribe failed: {type(exc).__name__}: {exc}")
            return -1

    def unsubscribe(self, items: list[dict[str, Any]]) -> int:
        if self.state != BackendState.LOGGED_IN:
            return -1
        try:
            self.client.subscribe(items, unsubscribe=True)
            return 0
        except Exception as exc:
            self.last_error = str(exc)
            self.log("ERROR", f"unsubscribe failed: {type(exc).__name__}: {exc}")
            return -1

    def query(self, kind: str, req: Any) -> Any:
        if self.state != BackendState.LOGGED_IN or self.cfg is None:
            raise TgwTransportError("backend is not logged in")
        if kind not in {"third_info", "kline", "snapshot"}:
            raise NotImplementedError(f"{kind} internet query is not implemented yet")
        if not isinstance(req, dict) or "task_id" not in req:
            raise ValueError("invalid internet query request")
        request_id = int(req["task_id"])
        if kind == "third_info":
            if not isinstance(req.get("params"), dict):
                raise ValueError("invalid third-info request")
            payload = build_third_info_request(
                self.client.username, self.client.token, request_id, req["params"]
            )
            parser = parse_third_info_packets
        elif kind == "snapshot":
            payload = build_snapshot_request(
                self.client.username, self.client.token, request_id, req.get("request")
            )
            parser = parse_snapshot_packets
        else:
            payload = build_kline_request(
                self.client.username, self.client.token, request_id, req.get("request")
            )
            parser = parse_kline_packets
        configured = os.environ.get(
            "TGW_QUERY_ENDPOINTS", "/amd/dgw/dgw1_query,/amd/dgw/dgw2_query"
        )
        endpoints = [value.strip() for value in configured.split(",") if value.strip()]
        if not endpoints:
            raise ValueError("TGW_QUERY_ENDPOINTS contains no endpoint")
        # Official query task ids begin at 1; map the first task to dgw1 and
        # alternate subsequent one-shot connections across the configured pool.
        start = (request_id - 1) % len(endpoints)
        endpoints = endpoints[start:] + endpoints[:start]

        query_client: TgwWssClient | None = None
        last_connect_error: Exception | None = None
        for endpoint in endpoints:
            candidate = TgwWssClient(
                endpoint=endpoint, timeout=self.timeout, heartbeat_sec=0
            )
            try:
                candidate.connect(
                    _as_text(self.cfg["server_vip"]),
                    int(self.cfg["server_port"]),
                    ca_file=self.ca_file,
                    server_name=self.server_name,
                )
            except Exception as exc:
                candidate.close()
                last_connect_error = exc
                continue
            query_client = candidate
            break
        if query_client is None:
            raise TgwTransportError("all TGW query endpoints failed") from last_connect_error

        try:
            seen_packets: set[int] = set()
            expected_packets: int | None = None

            def complete(message: dict[str, Any]) -> bool:
                nonlocal expected_packets
                if message.get("status") != 0:
                    return True
                headers = message.get("headers")
                if not isinstance(headers, dict):
                    return True
                pack_num = headers.get("pack_num")
                all_pack_num = headers.get("all_pack_num")
                if not isinstance(pack_num, int) or not isinstance(all_pack_num, int):
                    return True
                if expected_packets is None:
                    expected_packets = all_pack_num
                elif expected_packets != all_pack_num:
                    return True
                seen_packets.add(pack_num)
                return seen_packets == set(range(1, expected_packets + 1))

            packets = query_client.request_many(
                request_id, payload, done=complete, timeout=self.timeout
            )
            query_client.send(build_query_complete_request(
                self.client.username, self.client.token, request_id
            ))
            query_client.wait_closed(min(2.0, self.timeout))
            return parser(packets)
        finally:
            query_client.close()

    def close(self) -> None:
        self.client.close()
        if self.cfg is not None:
            self.cfg["password"] = ""
        self.state = BackendState.CLOSED


class SimBackend(BaseBackend):
    """Explicit test double. Enable only with ``TGW_BACKEND=sim``."""

    def init(self, cfg_dict: dict[str, Any], api_mode: int, path: str = "") -> int:
        self.cfg = cfg_dict.copy()
        self.api_mode = int(api_mode)
        self.state = BackendState.CONNECTED
        return 0

    def login(self) -> int:
        if self.state != BackendState.CONNECTED:
            return -1
        self.logon_response = {"headers": {"tag": "OnRspLogon"}, "status": 0, "data": {}}
        self.state = BackendState.LOGGED_IN
        return 0

    def subscribe(self, items: list[dict[str, Any]]) -> int:
        return 0 if self.state == BackendState.LOGGED_IN else -1

    def unsubscribe(self, items: list[dict[str, Any]]) -> int:
        return 0 if self.state == BackendState.LOGGED_IN else -1

    def query(self, kind: str, req: Any) -> list[Any]:
        return []


class CppSkeletonBackend(SimBackend):
    """Legacy arm64 loadability probe, never selected automatically."""

    def __init__(self, dylib: str) -> None:
        super().__init__()
        self.lib = ctypes.CDLL(dylib)
        self.lib.tgw_core_version.restype = ctypes.c_char_p

    def login(self) -> int:
        version = self.lib.tgw_core_version().decode("utf-8")
        self.log("WARN", f"using TCP-only C++ skeleton {version}; no server authentication")
        return super().login()


def get_backend() -> tuple[BaseBackend, str]:
    mode = os.environ.get("TGW_BACKEND", "live").strip().lower()
    if mode == "live":
        return LiveBackend(), "live-wss"
    if mode == "sim":
        return SimBackend(), "explicit-sim"
    if mode in {"cpp", "cpp-skeleton"}:
        dylib = _find_core_lib()
        if not dylib:
            raise RuntimeError("TGW_BACKEND=cpp-skeleton but libtgw_core.dylib was not found")
        return CppSkeletonBackend(dylib), dylib
    raise ValueError(f"unknown TGW_BACKEND mode: {mode!r}")
