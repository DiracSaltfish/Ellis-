#!/usr/bin/env python3
"""Batch historical K-line puller for the macOS TGW implementation.

Mirrors the native bj worker semantics (/tmp/tgw-history-chunk-worker.py):
one login session per process, one QueryKline per symbol over the requested
date range, rows accumulated to a gzip JSONL file, and only sanitized counts
printed to stdout (no credential or quote values).

Symbols are ``CODE.SUFFIX`` where SUFFIX is one of SH / SZ / HK:
    SH  -> MarketType.kSSE (101)
    SZ  -> MarketType.kSZSE (102)
    HK  -> MarketType.kHKEx (103)

Usage:
    python tools/pull_history_batch.py \
        --config config/galaxy_account.ini \
        --symbols 00177.HK,00700.HK,510300.SH,000001.SZ \
        --begin 20260901 --end 20260909 \
        --cyc-type 10000 \
        --out /absolute/path/outside-repo/history.jsonl.gz

Notes
-----
* The account-to-VIP mapping must be correct (see config/galaxy_account.example.ini).
  A login rejection status -98 usually means the configured VIP does not host
  this account, or a stale session is still active.
* Output rows are the raw protocol integers; scaling is only verified for the
  documented 159691 one-minute sample, so do not treat them as yuan/shares.
* Refuses to write into the repository (raw quotes must never enter the repo).
"""
from __future__ import annotations

import argparse
import configparser
import gzip
import json
import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

import tgw_macos as tgw  # noqa: E402


def strip_inline(value: str) -> str:
    return value.split("#", 1)[0].strip()


def load_config(path: Path) -> dict:
    parser = configparser.ConfigParser()
    with path.open(encoding="utf-8") as stream:
        parser.read_file(stream)
    section = parser["galaxy"]
    hosts = [h for h in strip_inline(section["host"]).replace("，", " ").split() if h]
    if not hosts:
        raise SystemExit("config [galaxy].host is empty")
    return {
        "hosts": hosts,
        "port": section.getint("port", 0),
        "username": strip_inline(section["username"]),
        "password": section["password"].strip(),
        "force_logout": section.getboolean("force_logout", False),
        "api_mode": strip_inline(section.get("api_mode", "kInternetMode")),
    }


def market_for_suffix(symbol: str) -> int:
    suffix = symbol.rsplit(".", 1)[-1].upper()
    mapping = {"SH": tgw.MarketType.kSSE,
               "SZ": tgw.MarketType.kSZSE,
               "HK": tgw.MarketType.kHKEx}
    try:
        return int(mapping[suffix])
    except KeyError as exc:
        raise SystemExit(f"unsupported symbol suffix in {symbol!r}; use SH/SZ/HK") from exc


def default_minute_window(symbol: str) -> tuple[int, int]:
    suffix = symbol.rsplit(".", 1)[-1].upper()
    if suffix == "HK":
        return 930, 1600
    return 930, 1500


def parse_symbols(values: list[str]) -> list[str]:
    symbols: list[str] = []
    for raw in values:
        for token in raw.replace("，", ",").split(","):
            token = token.strip()
            if token:
                symbols.append(token)
    return symbols


def read_symbols_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [line.split("#", 1)[0].strip() for line in text.splitlines() if line.strip()]


def main() -> int:
    cli = argparse.ArgumentParser()
    cli.add_argument("--config", type=Path, default=ROOT / "config" / "galaxy_account.ini")
    cli.add_argument("--symbols", action="append", default=[], metavar="CODE.SUF[,..]")
    cli.add_argument("--symbols-file", type=Path, default=None)
    cli.add_argument("--begin", type=int, required=True, help="yyyyMMdd inclusive")
    cli.add_argument("--end", type=int, required=True, help="yyyyMMdd inclusive")
    cli.add_argument("--cyc-type", type=int, default=10000,
                     help="public cycle: 10000 minute, 10008 daily, ...")
    cli.add_argument("--begin-time", type=int, default=None, help="HHmm; default per suffix")
    cli.add_argument("--end-time", type=int, default=None, help="HHmm; default per suffix")
    cli.add_argument("--force-logout", dest="force", action="store_true", default=None,
                     help="override the config force_logout value")
    cli.add_argument("--max-login-attempts", type=int, default=4,
                     help="bounded retries per host when the server rejects the "
                          "logon (observed -98 when a prior session is still "
                          "active or the login lands on a non-home instance)")
    cli.add_argument("--query-gap-sec", type=float, default=1.5,
                     help="pause between symbols; the query service actively "
                          "closes one-shot connections that arrive too densely")
    cli.add_argument("--query-retries", type=int, default=3,
                     help="bounded retry per symbol on transient server "
                          "'accept conn active close' query-channel closes")
    cli.add_argument("--out", type=Path, required=True,
                     help="absolute gzip JSONL output path OUTSIDE the repository")
    args = cli.parse_args()

    symbols = parse_symbols(args.symbols)
    if args.symbols_file:
        symbols.extend(read_symbols_file(args.symbols_file))
    symbols = list(dict.fromkeys(symbols))
    if not symbols:
        raise SystemExit("no symbols given (--symbols / --symbols-file)")
    if args.begin > args.end:
        raise SystemExit("--begin must be <= --end")

    out_path = args.out.expanduser().resolve()
    try:
        out_path.relative_to(ROOT)
    except ValueError:
        pass
    else:
        raise SystemExit(
            f"refusing to write raw quotes into the repository: {out_path}"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cfg = load_config(args.config)
    force = args.force if args.force is not None else bool(cfg["force_logout"])
    mode = int(getattr(tgw.ApiMode, cfg["api_mode"], tgw.ApiMode.kInternetMode))

    # Print only sanitized shape information; never quote values.
    def report(line: str) -> None:
        print(line, flush=True)

    max_attempts = max(1, int(args.max_login_attempts))
    hosts = cfg["hosts"]
    # The server can answer ReqLogon with status -98 when an earlier session
    # for this account is still active on another instance behind the VIP, or
    # when the request lands on an instance that does not host the account.
    # Bounded retries across hosts let a later attempt land where the stale
    # session lives so force_logout can displace it.  The global singleton is
    # unusable after a failed attempt, so each try rebuilds the backend.
    def open_session(*, phase: str) -> bool:
        """Open a fresh backend; a closed query channel cannot be reused."""
        tgw.Close()
        tgw.interface._g_backend = None
        for attempt in range(max_attempts):
            host = hosts[attempt % len(hosts)]
            c = tgw.Cfg().set(
                server_vip=host,
                server_port=cfg["port"],
                username=cfg["username"],
                password=cfg["password"],
                force_logout=force,
            )
            try:
                ok = tgw.Login(c, mode)
            except Exception:  # pragma: no cover - defensive
                ok = False
            be = tgw.interface._backend()
            last_error = (getattr(be, "last_error", "") or "")[:300]
            report(f"{phase} login attempt={attempt + 1} host={host} ok={ok} "
                   f"detail={last_error or 'session established'}")
            if ok:
                return True
            tgw.Close()
            tgw.interface._g_backend = None
            if attempt + 1 < max_attempts:
                time.sleep(min(3.0 + attempt * 2.0, 12.0))
        return False

    if not open_session(phase="initial"):
        report("NO_LOGIN")
        return 1

    result = {
        "schema": "tgw-macos-history-chunk.v1",
        "begin": args.begin,
        "end": args.end,
        "cyc_type": args.cyc_type,
        "symbols": symbols,
        "errors": {},
    }
    try:
        with gzip.open(out_path, "wt", encoding="utf-8", compresslevel=6) as handle:
            handle.write(json.dumps({"type": "header", **result},
                                    ensure_ascii=False, separators=(",", ":")) + "\n")
            for symbol in symbols:
                market = market_for_suffix(symbol)
                code = symbol.rsplit(".", 1)[0]
                if args.begin_time is None or args.end_time is None:
                    bt, et = default_minute_window(symbol)
                else:
                    bt, et = args.begin_time, args.end_time

                def build_request() -> tgw.ReqKline:
                    req = tgw.ReqKline().set_code(code)
                    req.market_type = market
                    req.cq_flag = req.cq_date = req.qj_flag = req.cyc_def = 0
                    req.cyc_type = args.cyc_type
                    req.auto_complete = 1
                    req.begin_date = args.begin
                    req.end_date = args.end
                    req.begin_time = bt
                    req.end_time = et
                    return req

                rows: list[dict] = []
                error_tag = None
                transient_close = "server closed WebSocket (code=1000, reason=accept conn active close)"
                for attempt in range(max(1, int(args.query_retries))):
                    try:
                        rows, error_code = tgw.QueryKline(
                            build_request(), return_df_format=False
                        )
                        error_tag = error_code if isinstance(error_code, int) else None
                        break
                    except Exception as exc:
                        error_tag = type(exc).__name__
                        transient = transient_close in str(exc)
                        report(f"symbol={symbol} attempt={attempt + 1} "
                               f"{type(exc).__name__}: {str(exc)[:200]} "
                               f"transient_close={transient}")
                        rows = []
                        if not transient or attempt + 1 >= max(1, int(args.query_retries)):
                            break
                        time.sleep(2.0 + attempt * 2.0)
                        if not open_session(phase=f"reconnect symbol={symbol}"):
                            error_tag = "reconnect_failed"
                            break
                rows = rows or []
                result["errors"][symbol] = "ok" if error_tag == 0 or error_tag is None else error_tag
                if rows:
                    first = rows[0].get("kline_time")
                    last = rows[-1].get("kline_time")
                    report(f"symbol={symbol} market={market} rows={len(rows)} "
                           f"first_kline_time={first} last_kline_time={last} error={error_tag}")
                else:
                    report(f"symbol={symbol} market={market} rows=0 error={error_tag}")
                handle.write(json.dumps({"type": "symbol", "symbol": symbol, "rows": rows},
                                        ensure_ascii=False, separators=(",", ":")) + "\n")
                time.sleep(max(0.0, float(args.query_gap_sec)))
            result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            handle.write(json.dumps({"type": "footer", **result},
                                    ensure_ascii=False, separators=(",", ":")) + "\n")
    finally:
        tgw.Close()
        report("CLOSED")
    report(f"output={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
