#!/usr/bin/env python3
"""Mac-home worker for the guarded intraday private-valuation rebuild queue.

The website can only submit a small, declarative job (date, known symbols, and
FX policy).  This agent maps that job to allow-listed local backfill scripts;
it never executes a command received over the network.

`final_cfets` is the only write policy in this first deployment.  Before the
same-day CFETS central parity is available, capture/provisional requests are
kept in ``awaiting_fx`` so a stale or guessed FX quote can never be persisted.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import struct
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = REPO_ROOT / ".sina-uploader.env"

XOP_SYMBOLS = {"SZ159518"}
MONTH_SYMBOLS = {"SH513350", "SZ159605"}
CHINA_INTERNET_SYMBOLS = {"SZ159607", "SH513050"}
US_INDEX_SYMBOLS = {
    "SH513100", "SH513110", "SH513300", "SH513390", "SH513870",
    "SZ159501", "SZ159513", "SZ159632", "SZ159659", "SZ159660", "SZ159696", "SZ159941",
    "SH513500", "SH513650", "SZ159612", "SZ159655",
    "SH513000", "SH513520", "SH513880", "SZ159866",
    "SH513030", "SZ159561",
}
INDIA_SYMBOLS = {"SZ164824"}
LOF_SYMBOLS = {"SZ162411"}


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip("'\"")


class BufferedSocket:
    def __init__(self, sock: socket.socket, buffer: bytes) -> None:
        self.sock = sock
        self.buffer = bytearray(buffer)

    def sendall(self, data: bytes) -> None:
        self.sock.sendall(data)

    def recv(self, size: int) -> bytes:
        if self.buffer:
            result = bytes(self.buffer[:size])
            del self.buffer[:size]
            return result
        return self.sock.recv(size)

    def settimeout(self, timeout: float | None) -> None:
        self.sock.settimeout(timeout)

    def gettimeout(self) -> float | None:
        return self.sock.gettimeout()

    def close(self) -> None:
        self.sock.close()


def read_exact(sock: BufferedSocket, size: int) -> bytes:
    parts = bytearray()
    while len(parts) < size:
        chunk = sock.recv(size - len(parts))
        if not chunk:
            raise RuntimeError("websocket closed")
        parts.extend(chunk)
    return bytes(parts)


def read_http_response(sock: socket.socket) -> tuple[str, bytes]:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > 65536:
            raise RuntimeError("websocket upgrade response too large")
    head, separator, rest = bytes(data).partition(b"\r\n\r\n")
    return (head + separator).decode("iso-8859-1", errors="replace"), rest


def send_frame(sock: BufferedSocket, payload: bytes, opcode: int) -> None:
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length <= 0xFFFF:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    mask = os.urandom(4)
    header.extend(mask)
    encoded = bytes(payload[index] ^ mask[index % 4] for index in range(length))
    sock.sendall(bytes(header) + encoded)


def recv_frame(sock: BufferedSocket) -> tuple[bytes, int]:
    first, second = read_exact(sock, 2)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", read_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", read_exact(sock, 8))[0]
    mask = read_exact(sock, 4) if masked else b""
    payload = read_exact(sock, length) if length else b""
    if masked:
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return payload, opcode


def send_json(sock: BufferedSocket, payload: dict[str, Any]) -> None:
    send_frame(sock, json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 0x1)


def recv_json(sock: BufferedSocket, timeout: float | None = None) -> dict[str, Any] | None:
    original_timeout = sock.gettimeout()
    if timeout is not None:
        sock.settimeout(timeout)
    try:
        payload, opcode = recv_frame(sock)
    except socket.timeout:
        return None
    finally:
        if timeout is not None:
            sock.settimeout(original_timeout)
    if opcode == 0x8:
        raise RuntimeError("websocket closed by server")
    if opcode == 0x9:
        send_frame(sock, payload, 0xA)
        return None
    if opcode != 0x1:
        return None
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("invalid websocket message")
    return value


def connect_websocket(server: str, token: str, timeout: float, origin_ip: str, tls_insecure: bool, ca_file: str) -> BufferedSocket:
    parsed = urlparse(server)
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError("NNN_SERVER_URL must begin with http:// or https://")
    scheme = "wss" if parsed.scheme == "https" else "ws"
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if scheme == "wss" else 80)
    connect_host = origin_ip.strip() or host
    sock: socket.socket = socket.create_connection((connect_host, port), timeout=timeout)
    if scheme == "wss":
        if tls_insecure:
            context = ssl._create_unverified_context()
        else:
            context = ssl.create_default_context(cafile=ca_file or None)
        sock = context.wrap_socket(sock, server_hostname=host)
    sock.settimeout(timeout)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    host_header = host if parsed.port is None else f"{host}:{port}"
    request = (
        "GET /api/v1/private/intraday-rebuild/ws HTTP/1.1\r\n"
        f"Host: {host_header}\r\n"
        f"Origin: {'https' if scheme == 'wss' else 'http'}://{host_header}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"X-Intraday-Rebuild-Token: {token}\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))
    response, leftover = read_http_response(sock)
    status_line = response.splitlines()[0] if response else ""
    if " 101 " not in status_line:
        raise RuntimeError(f"websocket upgrade failed: {status_line}")
    expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
    if expected not in response:
        raise RuntimeError("websocket accept header mismatch")
    return BufferedSocket(sock, leftover)


def command_groups(job: dict[str, Any], args: argparse.Namespace) -> tuple[list[tuple[str, list[str]]], list[str]]:
    symbols = {str(value).strip().upper() for value in job.get("symbols", []) if str(value).strip()}
    day = str(job.get("trade_date", "")).replace("-", "")
    if len(day) != 8 or not day.isdigit():
        raise RuntimeError("job has invalid trade_date")
    common = ["--start", day, "--end", day, "--ib-host", args.ib_host, "--ib-port", str(args.ib_port)]
    env_file = ["--env-file", str(args.env_file)]
    commands: list[tuple[str, list[str]]] = []
    unsupported: list[str] = []

    def append_group(name: str, script: str, selected: set[str], extra: list[str] | None = None) -> None:
        chosen = sorted(symbols & selected)
        if not chosen:
            return
        command = [sys.executable, str(REPO_ROOT / "scripts" / script), "--symbols", ",".join(chosen), *common]
        if extra:
            command.extend(extra)
        command.extend(env_file)
        command.extend(["--ib-client-id", str(args.ib_client_id)])
        commands.append((name, command))

    if symbols & XOP_SYMBOLS:
        commands.append((
            "xop",
            [
                sys.executable, str(REPO_ROOT / "scripts" / "private_valuation_history_backfill.py"),
                *common, *env_file, "--ib-client-id", str(args.ib_client_id),
            ],
        ))
    append_group("month", "private_month_history_backfill.py", MONTH_SYMBOLS)
    append_group("china_internet", "private_china_internet_history_backfill.py", CHINA_INTERNET_SYMBOLS)
    append_group("us_index", "private_us_index_history_backfill.py", US_INDEX_SYMBOLS)

    if symbols & INDIA_SYMBOLS:
        command = [sys.executable, str(REPO_ROOT / "scripts" / "private_164824_history_backfill.py"), *common, "--ib-client-id", str(args.ib_client_id)]
        commands.append(("india", command))
    if symbols & LOF_SYMBOLS:
        command = [sys.executable, str(REPO_ROOT / "scripts" / "private_162411_today_backfill.py"), "--day", day, "--ib-host", args.ib_host, "--ib-port", str(args.ib_port), "--ib-client-id", str(args.ib_client_id), *env_file]
        commands.append(("lof", command))

    # SH513220 needs an audited domestic BID/ASK CSV; TWS alone must not
    # synthesize that input. Keep it visible as a blocked symbol.
    if "SH513220" in symbols:
        unsupported.append("SH513220 requires the audited QMT CN 1-minute BID/ASK CSV")
    known = XOP_SYMBOLS | MONTH_SYMBOLS | CHINA_INTERNET_SYMBOLS | US_INDEX_SYMBOLS | INDIA_SYMBOLS | LOF_SYMBOLS | {"SH513220"}
    unsupported.extend(f"unsupported symbol {symbol}" for symbol in sorted(symbols - known))
    return commands, unsupported


def run_command(command: list[str], send: Any, job_id: str, step: int, total: int) -> None:
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=os.environ.copy(),
    )
    tail: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        line = line.strip()
        if not line:
            continue
        tail.append(line)
        tail = tail[-12:]
        send({"type": "job_progress", "job_id": job_id, "state": "capturing", "progress": max(1, int(step * 100 / max(total, 1))), "message": line[:500]})
    result = process.wait()
    if result != 0:
        detail = " | ".join(tail[-4:]) or f"exit={result}"
        raise RuntimeError(f"backfill command failed ({result}): {detail}")


def is_missing_cfets(exc: Exception) -> bool:
    message = str(exc).lower()
    if "dated inputs unavailable" in message and "fx=" in message:
        return True
    return "cfets" in message and any(marker in message for marker in ("missing", "unavailable", "requires exact", "not for the current"))


def wait_for_cfets(seconds: float, send: Any, job_id: str) -> None:
    deadline = time.monotonic() + max(1.0, seconds)
    while time.monotonic() < deadline:
        time.sleep(min(15.0, max(0.1, deadline - time.monotonic())))
        send({"type": "ping", "job_id": job_id})


def handle_job(job: dict[str, Any], args: argparse.Namespace, send: Any) -> None:
    job_id = str(job.get("id", "")).strip()
    if not job_id:
        raise RuntimeError("job request has no id")
    policy = str(job.get("fx_policy", "capture_only"))
    if policy != "final_cfets":
        send({
            "type": "job_waiting_fx", "job_id": job_id, "progress": 0,
            "message": "当前 Agent 仅在 CFETS 当日中间价可用后写入；任务保留，未使用前日或猜测汇率。",
        })
        return
    commands, unsupported = command_groups(job, args)
    if not commands:
        send({"type": "job_failed", "job_id": job_id, "error": "no allow-listed backfill command for job"})
        return
    send({"type": "job_ack", "job_id": job_id, "state": "capturing", "progress": 1, "message": "Mac-home 已接单，开始调用受限回补脚本"})
    while True:
        try:
            for index, (name, command) in enumerate(commands):
                send({"type": "job_progress", "job_id": job_id, "state": "capturing", "progress": max(1, int(index * 100 / len(commands))), "message": f"正在回补 {name}"})
                run_command(command, send, job_id, index + 1, len(commands))
            message = "TWS 回补与网站历史重建已完成"
            if unsupported:
                message += "；未执行：" + "; ".join(unsupported)
            send({"type": "job_complete", "job_id": job_id, "message": message})
            return
        except Exception as exc:  # keep the web-side audit record accurate
            if is_missing_cfets(exc):
                send({
                    "type": "job_waiting_fx", "job_id": job_id, "progress": 0,
                    "message": f"CFETS 当日中间价尚不可用，{int(args.fx_retry_seconds)} 秒后自动重试；未写入历史。",
                    "error": str(exc),
                })
                wait_for_cfets(args.fx_retry_seconds, send, job_id)
                continue
            send({"type": "job_failed", "job_id": job_id, "error": str(exc), "message": "Mac-home 回补失败；未将失败批次标记为完成"})
            return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "https://1navs.com"))
    parser.add_argument("--token", default=os.getenv("NNN_INTRADAY_REBUILD_AGENT_TOKEN", ""))
    parser.add_argument("--agent-id", default=os.getenv("NNN_INTRADAY_REBUILD_AGENT_ID", socket.gethostname()))
    parser.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    parser.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    parser.add_argument("--origin-tls-insecure", action="store_true", default=os.getenv("NNN_ORIGIN_TLS_INSECURE", "").strip().lower() in {"1", "true", "yes"})
    parser.add_argument("--ib-host", default=os.getenv("NNN_INTRADAY_REBUILD_IB_HOST", os.getenv("NNN_PRIVATE_IB_HOST", "127.0.0.1")))
    parser.add_argument("--ib-port", type=int, default=int(os.getenv("NNN_INTRADAY_REBUILD_IB_PORT", os.getenv("NNN_PRIVATE_IB_PORT", "7496"))))
    parser.add_argument("--ib-client-id", type=int, default=int(os.getenv("NNN_INTRADAY_REBUILD_IB_CLIENT_ID", "159523")))
    parser.add_argument("--connect-timeout", type=float, default=20.0)
    parser.add_argument("--reconnect-seconds", type=float, default=5.0)
    parser.add_argument("--fx-retry-seconds", type=float, default=float(os.getenv("NNN_INTRADAY_REBUILD_FX_RETRY_SECONDS", "300")))
    return parser.parse_args()


def main() -> int:
    preliminary = parse_args()
    load_env_file(preliminary.env_file)
    # Reparse so .sina-uploader.env supplies values when launchd only passes
    # the path. Explicit command-line values remain authoritative.
    args = parse_args()
    if not args.token.strip():
        print("NNN_INTRADAY_REBUILD_AGENT_TOKEN is required", file=sys.stderr, flush=True)
        return 2
    while True:
        connection: BufferedSocket | None = None
        try:
            connection = connect_websocket(args.server, args.token, args.connect_timeout, args.origin_ip, args.origin_tls_insecure, args.origin_ca_file)
            send_json(connection, {"type": "hello", "agent_id": args.agent_id, "version": "1.0.0", "capabilities": ["final_cfets", "xop", "month", "china_internet", "us_index", "india", "lof"]})
            print(f"intraday rebuild agent connected to {args.server}", flush=True)
            last_ping = time.monotonic()
            while True:
                message = recv_json(connection, timeout=1.0)
                if message and message.get("type") == "job_request":
                    received_job = message.get("job")
                    if isinstance(received_job, dict):
                        handle_job(received_job, args, lambda payload: send_json(connection, payload))
                if time.monotonic() - last_ping >= 30:
                    send_json(connection, {"type": "ping"})
                    last_ping = time.monotonic()
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"intraday rebuild agent disconnected: {exc}", file=sys.stderr, flush=True)
            time.sleep(max(1.0, args.reconnect_seconds))
        finally:
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
