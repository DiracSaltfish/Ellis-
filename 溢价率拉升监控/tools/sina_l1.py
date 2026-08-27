#!/usr/bin/env python3
"""Small, auditable Sina A-share/ETF L1 puller.

The parser follows the field layout already used by CPPETF UI V11.  It keeps
the exchange quote time separate from the local HTTP receive time so the data
can be used as an independent, lower-frequency comparison source for TGW.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo


SINA_URL = "https://hq.sinajs.cn/list="
SHANGHAI = ZoneInfo("Asia/Shanghai")
HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def normalize_symbol(value: str) -> tuple[str, str]:
    compact = value.strip().upper().replace(" ", "")
    market = ""
    code = ""
    if len(compact) == 8 and compact[:2] in {"SH", "SZ", "BJ"}:
        market, code = compact[:2], compact[2:]
    elif "." in compact:
        left, right = compact.split(".", 1)
        aliases = {"SH": "SH", "SSE": "SH", "SHSE": "SH", "XSHG": "SH",
                   "SZ": "SZ", "SZSE": "SZ", "XSHE": "SZ", "BJ": "BJ", "BSE": "BJ"}
        if len(left) == 6 and left.isdigit():
            code, market = left, aliases.get(right, "")
        elif len(right) == 6 and right.isdigit():
            code, market = right, aliases.get(left, "")
    elif len(compact) == 6 and compact.isdigit():
        code = compact
        market = "SH" if compact[0] in "569" else "BJ" if compact[0] in "48" else "SZ"
    if market not in {"SH", "SZ", "BJ"} or len(code) != 6 or not code.isdigit():
        raise ValueError(f"unsupported Sina symbol: {value!r}")
    return f"{code}.{market}", f"{market.lower()}{code}"


def _number(parts: list[str], index: int) -> float:
    if index >= len(parts) or not parts[index].strip():
        return 0.0
    return float(parts[index])


def _integer(parts: list[str], index: int) -> int:
    return int(round(_number(parts, index)))


def _quote_epoch_ms(date_text: str, time_text: str) -> int:
    value = datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M:%S")
    return int(value.replace(tzinfo=SHANGHAI).timestamp() * 1000)


def parse_payload(payload: bytes, requested: Iterable[str], received_ms: int | None = None) -> list[dict]:
    receive_time = received_ms if received_ms is not None else time.time_ns() // 1_000_000
    lookup: dict[str, str] = {}
    for value in requested:
        normalized, sina = normalize_symbol(value)
        lookup[sina] = normalized
    text = payload.decode("gb18030", errors="replace").strip()
    snapshots: list[dict] = []
    for statement in text.split(";"):
        statement = statement.strip()
        prefix = "var hq_str_"
        if not statement.startswith(prefix) or "=" not in statement:
            continue
        name, encoded = statement.split("=", 1)
        sina_symbol = name[len(prefix):]
        symbol = lookup.get(sina_symbol)
        if symbol is None:
            continue
        first, last = encoded.find('"'), encoded.rfind('"')
        if first < 0 or last <= first:
            continue
        parts = encoded[first + 1:last].split(",")
        if len(parts) < 32:
            continue
        try:
            quote_ms = _quote_epoch_ms(parts[30].strip(), parts[31].strip())
            snapshot = {
                "source": "sina_l1",
                "s": symbol,
                "name": parts[0].strip(),
                "qt": quote_ms,
                "rt": receive_time,
                "lp": _number(parts, 3),
                "o": _number(parts, 1),
                "h": _number(parts, 4),
                "l": _number(parts, 5),
                "pc": _number(parts, 2),
                "vol": _integer(parts, 8),
                "amt": _number(parts, 9),
                "bp": [_number(parts, index) for index in (11, 13, 15, 17, 19)],
                "bv": [_integer(parts, index) for index in (10, 12, 14, 16, 18)],
                "ap": [_number(parts, index) for index in (21, 23, 25, 27, 29)],
                "av": [_integer(parts, index) for index in (20, 22, 24, 26, 28)],
                "state": parts[32].strip() if len(parts) > 32 else "",
                "source_date": parts[30].strip(),
                "source_time": parts[31].strip(),
            }
        except (ValueError, OverflowError):
            continue
        snapshots.append(snapshot)
    return snapshots


def chunks(values: list[str], size: int) -> Iterator[list[str]]:
    for offset in range(0, len(values), size):
        yield values[offset:offset + size]


class SinaL1Fetcher:
    def __init__(self, timeout: float = 5.0, chunk_size: int = 80):
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        self.timeout = timeout
        self.chunk_size = chunk_size

    def fetch(self, symbols: Iterable[str]) -> tuple[list[dict], list[dict]]:
        normalized = [normalize_symbol(value)[0] for value in symbols]
        snapshots: list[dict] = []
        requests: list[dict] = []
        for group in chunks(normalized, self.chunk_size):
            sina_symbols = [normalize_symbol(value)[1] for value in group]
            started = time.time_ns() // 1_000_000
            request = urllib.request.Request(SINA_URL + ",".join(sina_symbols), headers=HEADERS)
            with urllib.request.urlopen(request, timeout=self.timeout) as reply:
                body = reply.read()
                status = int(getattr(reply, "status", 200))
            received = time.time_ns() // 1_000_000
            parsed = parse_payload(body, group, received)
            snapshots.extend(parsed)
            requests.append({"started_ms": started, "received_ms": received,
                             "latency_ms": received - started, "http_status": status,
                             "requested": len(group), "parsed": len(parsed), "bytes": len(body)})
        return snapshots, requests


def load_symbols(value: str | None, watchlist: Path | None) -> list[str]:
    symbols: list[str] = []
    if watchlist is not None:
        data = json.loads(watchlist.read_text(encoding="utf-8"))
        raw = data.get("symbols", data) if isinstance(data, dict) else data
        symbols.extend(str(item) for item in raw)
    if value:
        symbols.extend(item for item in value.split(",") if item.strip())
    result = list(dict.fromkeys(normalize_symbol(item)[0] for item in symbols))
    if not result:
        raise ValueError("at least one symbol is required")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect independent Sina five-level L1 snapshots")
    parser.add_argument("--symbols", help="comma-separated symbols")
    parser.add_argument("--watchlist", type=Path)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--chunk-size", type=int, default=80)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    symbols = load_symbols(args.symbols, args.watchlist)
    fetcher = SinaL1Fetcher(args.timeout, args.chunk_size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, args.duration)
    polls = snapshots = errors = 0
    with args.output.open("a", encoding="utf-8") as stream:
        while True:
            started = time.monotonic()
            try:
                books, requests = fetcher.fetch(symbols)
                record = {"poll_wall_ms": time.time_ns() // 1_000_000,
                          "books": books, "requests": requests}
                stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                stream.flush()
                snapshots += len(books)
            except Exception as exc:
                errors += 1
                stream.write(json.dumps({"poll_wall_ms": time.time_ns() // 1_000_000,
                                         "error": f"{type(exc).__name__}: {exc}"},
                                        ensure_ascii=False, separators=(",", ":")) + "\n")
                stream.flush()
            polls += 1
            if time.monotonic() >= deadline:
                break
            time.sleep(min(max(0.0, args.interval - (time.monotonic() - started)),
                           max(0.0, deadline - time.monotonic())))
    print(json.dumps({"ok": errors == 0, "symbols": len(symbols), "polls": polls,
                      "snapshots": snapshots, "errors": errors, "output": str(args.output)},
                     ensure_ascii=False))
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
