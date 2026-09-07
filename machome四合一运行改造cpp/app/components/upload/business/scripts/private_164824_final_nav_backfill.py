#!/usr/bin/env python3
"""Replay SZ164824 final NAV from the T-2 official NAV baseline.

This importer is intentionally separate from private_164824_history_backfill:
it reads the four T-2 and T-day INDA anchors (Japan, Hong Kong, Europe and
the United States) and their SAFE USD/CNY central parities, then writes only
the final-NAV review table.  It never writes China-session minute rows and
therefore cannot replace the live NIFTY bridge IOPV.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import private_164824_history_backfill as history  # noqa: E402
import private_164824_valuation_uploader as india  # noqa: E402
import private_nasdaq_valuation_uploader as index_common  # noqa: E402
import private_valuation_uploader as common  # noqa: E402


SYMBOL = india.SYMBOL
HISTORY_PATH = f"/api/v1/private/funds/{SYMBOL}/final-nav-history/import"
SOURCE = "mac-home-private-164824-final-nav-backfill.v1"
DEFAULT_RUNTIME_DIR = Path(__file__).resolve().parent / ".runtime" / "private_164824_final_nav_backfill"
MAX_COMPRESSED_BYTES = 1 << 20
MAX_DECOMPRESSED_BYTES = 1 << 20


SourceUnavailableError = history.SourceUnavailableError


def weighted_anchor_price(anchors: tuple[india.AnchorObservation, ...]) -> float:
    """Return the production-model, normalized four-market INDA anchor."""
    if not anchors:
        raise SourceUnavailableError("INDA anchors are missing")
    total_weight = sum(float(anchor.weight) for anchor in anchors)
    if not math.isfinite(total_weight) or abs(total_weight - 1.0) > 1e-7:
        raise SourceUnavailableError(f"INDA anchor weights must sum to 1, got {total_weight}")
    value = sum(float(anchor.weight) * float(anchor.price) for anchor in anchors)
    if not math.isfinite(value) or value <= 0:
        raise SourceUnavailableError("INDA weighted anchor is invalid")
    return value


def final_nav(base_nav: float, investment_ratio: float, static_ratio: float, base_anchor: float, target_anchor: float, base_fx: float, target_fx: float) -> float:
    value = base_nav * (static_ratio + investment_ratio * (target_anchor / base_anchor) * (target_fx / base_fx))
    if not math.isfinite(value) or value <= 0:
        raise SourceUnavailableError("replayed final NAV is invalid")
    return value


def build_row(day: date, base_nav: india.OfficialNAV, base_fx: Any, target_fx: Any, base_anchors: tuple[india.AnchorObservation, ...], target_anchors: tuple[india.AnchorObservation, ...]) -> dict[str, Any]:
    base_anchor = weighted_anchor_price(base_anchors)
    target_anchor = weighted_anchor_price(target_anchors)
    estimate = final_nav(
        base_nav.value,
        india.INVESTMENT_RATIO,
        india.STATIC_RATIO,
        base_anchor,
        target_anchor,
        base_fx.rate,
        target_fx.rate,
    )
    return {
        "target_date": day.isoformat(),
        "base_nav_date": base_nav.trading_day.isoformat(),
        "base_nav": base_nav.value,
        "investment_ratio": india.INVESTMENT_RATIO,
        "static_ratio": india.STATIC_RATIO,
        "base_anchor_price": base_anchor,
        "target_anchor_price": target_anchor,
        "base_fx": base_fx.rate,
        "target_fx": target_fx.rate,
        "source": SOURCE,
        "generated_at": common.iso_timestamp(datetime.now(india.SHANGHAI)),
        # Server computes the same field before persistence. Retaining the
        # local check in the manifest makes each imported row easy to audit.
        "_local_final_estimate_nav": estimate,
    }


def completed_target_days(days: list[date], as_of: datetime | None = None) -> list[date]:
    """A T-day final NAV cannot exist until the next China calendar day.

    The U.S. component of a China T-day valuation closes around 04:00 BJT on
    T+1.  Excluding the current Shanghai calendar day keeps a 09:20 scheduled
    replay from querying a not-yet-existent U.S. close, while still allowing
    the immediately preceding T day to be rebuilt every morning.
    """
    now = as_of or datetime.now(india.SHANGHAI)
    return [day for day in days if day < now.date()]


def upload(args: argparse.Namespace, rows: list[dict[str, Any]]) -> int:
    payload_rows = [{key: value for key, value in row.items() if not key.startswith("_")} for row in rows]
    raw_payload = json.dumps({"rows": payload_rows}, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(raw_payload) > MAX_DECOMPRESSED_BYTES:
        raise SourceUnavailableError("final-NAV history payload exceeds size limit")
    compressed = gzip.compress(raw_payload, compresslevel=6, mtime=0)
    if len(compressed) > MAX_COMPRESSED_BYTES:
        raise SourceUnavailableError("compressed final-NAV history payload exceeds size limit")
    request = urllib.request.Request(
        args.server.rstrip("/") + HISTORY_PATH,
        data=compressed,
        method="POST",
        headers=common.gzip_server_headers(args.server, args.token),
    )
    try:
        with common.open_server_request(request, args.timeout, args.origin_ip, args.origin_tls_insecure, args.origin_ca_file) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SourceUnavailableError(f"final-NAV history import HTTP {exc.code}: {detail}") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise SourceUnavailableError(f"final-NAV history import was not acknowledged: {result}")
    return int(result.get("imported") or 0)


def write_manifest(runtime: Path, records: list[dict[str, Any]]) -> Path:
    path = runtime / "manifests" / f"run-{datetime.now(india.SHANGHAI):%Y%m%dT%H%M%S}.json"
    common._atomic_write_bytes(path, json.dumps({
        "schema_version": 1,
        "source": SOURCE,
        "generated_at": common.iso_timestamp(datetime.now(india.SHANGHAI)),
        "formula": "base_nav*(static_ratio+investment_ratio*(target_anchor/base_anchor)*(target_fx/base_fx))",
        "records": records,
    }, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    return path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "https://1navs.com"))
    result.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    result.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    result.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    result.add_argument("--origin-tls-insecure", action="store_true", default=common.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")))
    result.add_argument("--start", type=history.parse_day)
    result.add_argument("--end", type=history.parse_day)
    result.add_argument("--max-days", type=int, default=0)
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--runtime-dir", default=str(DEFAULT_RUNTIME_DIR))
    result.add_argument("--ib-host", default="127.0.0.1")
    result.add_argument("--ib-port", type=int, default=7496)
    result.add_argument("--ib-client-id", type=int, default=264825)
    result.add_argument("--timeout", type=float, default=float(os.getenv("NNN_UPLOAD_TIMEOUT", "45")))
    result.add_argument("--ib-request-delay", type=float, default=0.4)
    return result


def run(args: argparse.Namespace) -> int:
    if args.end is not None and args.start is not None and args.end < args.start:
        raise SourceUnavailableError("--end must not precede --start")
    if args.max_days < 0 or args.ib_request_delay < 0:
        raise SourceUnavailableError("invalid pacing or max-days setting")
    if not args.dry_run and not str(args.token).strip():
        raise SourceUnavailableError("NNN_UPLOAD_TOKEN or --token is required unless --dry-run is used")
    runtime = Path(args.runtime_dir).expanduser().resolve()
    candidates = history.public_history_days(args.server, args.timeout, args.start, args.end)
    targets = completed_target_days(candidates)
    if args.max_days:
        targets = targets[-args.max_days:]
    print(f"{SYMBOL} final-NAV plan targets={len(targets)}", flush=True)
    if not targets:
        return 0
    navs = history.official_nav_series(args.timeout, min(targets), max(targets))
    fx_cache: dict[date, Any] = {}
    anchor_cache: dict[date, tuple[india.AnchorObservation, ...]] = {}
    records: list[dict[str, Any]] = []
    ready_rows: list[dict[str, Any]] = []
    market = history.HistoricalINDAMarket(args.ib_host, args.ib_port, args.ib_client_id, args.timeout)
    try:
        for day in targets:
            record: dict[str, Any] = {"symbol": SYMBOL, "day": day.isoformat()}
            try:
                base_nav = history.t_minus_two_nav(day, navs)
                for fx_day in (base_nav.trading_day, day):
                    if fx_day not in fx_cache:
                        fx_cache[fx_day] = index_common.fetch_safe_central_parity(fx_day, args.timeout, index_common.NASDAQ_FAMILY)
                for anchor_day in (base_nav.trading_day, day):
                    if anchor_day not in anchor_cache:
                        anchor_cache[anchor_day] = history.historical_anchors(market, runtime, anchor_day)
                row = build_row(
                    day,
                    base_nav,
                    fx_cache[base_nav.trading_day],
                    fx_cache[day],
                    anchor_cache[base_nav.trading_day],
                    anchor_cache[day],
                )
                ready_rows.append(row)
                record.update({
                    "status": "ready",
                    "base_nav_date": row["base_nav_date"],
                    "base_nav": row["base_nav"],
                    "base_anchor_price": row["base_anchor_price"],
                    "target_anchor_price": row["target_anchor_price"],
                    "base_fx": row["base_fx"],
                    "target_fx": row["target_fx"],
                    "final_estimate_nav": row["_local_final_estimate_nav"],
                    "base_anchors": {anchor.key: anchor.price for anchor in anchor_cache[base_nav.trading_day]},
                    "target_anchors": {anchor.key: anchor.price for anchor in anchor_cache[day]},
                })
                print(
                    f"{SYMBOL} {day:%Y%m%d} T-2={base_nav.trading_day} "
                    f"anchor={row['base_anchor_price']:.4f}/{row['target_anchor_price']:.4f} "
                    f"estimate={row['_local_final_estimate_nav']:.6f}",
                    flush=True,
                )
            except Exception as exc:
                record.update({"status": "source_unavailable", "error": str(exc)})
                print(f"{SYMBOL} {day:%Y%m%d} SKIP {exc}", file=sys.stderr, flush=True)
            records.append(record)
            if args.ib_request_delay:
                time.sleep(args.ib_request_delay)
    finally:
        market.close()
    imported = 0 if args.dry_run or not ready_rows else upload(args, ready_rows)
    if not args.dry_run:
        for record in records:
            if record.get("status") == "ready":
                record["status"] = "uploaded"
        print(f"{SYMBOL} final-NAV imported={imported}", flush=True)
    manifest = write_manifest(runtime, records)
    ready = sum(record.get("status") in {"ready", "uploaded"} for record in records)
    print(f"summary ready={ready} skipped={len(records) - ready} imported={imported} manifest={manifest}", flush=True)
    return 0 if ready == len(records) else 2


def main() -> int:
    common.load_env_file(".sina-uploader.env")
    args = parser().parse_args()
    try:
        return run(args)
    except (SourceUnavailableError, RuntimeError, urllib.error.URLError) as exc:
        common.log(str(exc), error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
