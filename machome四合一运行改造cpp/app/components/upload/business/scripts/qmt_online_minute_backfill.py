#!/usr/bin/env python3

import argparse
import csv
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path


DEFAULT_QMT_PORT = 9999
DEFAULT_SERVER = "http://127.0.0.1:8080"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_STORE_ROOT = SCRIPT_DIR / "intraday_minute_store"
DEFAULT_REFERENCE_STORE_ROOT = SCRIPT_DIR / "quote_store"
FIELDS = [
    "trade_date",
    "symbol",
    "minute",
    "timestamp",
    "last_price",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "source",
]
NUMERIC_FIELDS = {"last_price", "open", "high", "low", "close", "volume", "amount"}


def parse_symbols(value):
    raw = str(value or "").replace("，", ",")
    return [item.strip() for item in raw.split(",") if item.strip()]


def internal_to_qmt_symbol(symbol):
    text = str(symbol or "").strip().upper()
    if len(text) == 8 and text[:2] in ("SH", "SZ"):
        return "{}.{}".format(text[2:], text[:2])
    return text


def qmt_to_internal_symbol(symbol):
    text = str(symbol or "").strip().upper()
    if "." in text:
        code, market = text.split(".", 1)
        if len(code) == 6 and market in ("SH", "XSHG", "SSE"):
            return "SH" + code
        if len(code) == 6 and market in ("SZ", "XSHE", "SZSE"):
            return "SZ" + code
    if len(text) == 8 and text[:2] in ("SH", "SZ", "BJ"):
        return text
    if len(text) == 6 and text.isdigit():
        if text.startswith("5"):
            return "SH" + text
        return "SZ" + text
    return text


def fetch_branch_symbols(server):
    url = server.rstrip("/") + "/api/v1/branches"
    with urllib.request.urlopen(url, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    seen = set()
    symbols = []
    for branch in payload:
        for symbol in branch.get("symbols", []):
            qmt_symbol = internal_to_qmt_symbol(symbol)
            if qmt_symbol and qmt_symbol not in seen:
                seen.add(qmt_symbol)
                symbols.append(qmt_symbol)
    return symbols


def chunks(values, size):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def request_qmt_rows(host, port, symbols, trade_date, timeout):
    payload = {
        "symbols": symbols,
        "trade_date": trade_date,
        "download": True,
    }
    with socket.create_connection((host, int(port)), timeout=timeout) as sock:
        sock.settimeout(timeout)
        file_obj = sock.makefile("rwb")
        file_obj.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        file_obj.flush()
        line = file_obj.readline()
    if not line:
        raise RuntimeError("QMT socket returned empty response")
    response = json.loads(line.decode("utf-8"))
    if not response.get("ok"):
        raise RuntimeError(response.get("error") or "QMT socket request failed")
    return response.get("rows") or []


def post_json(server, token, path, payload, timeout, ssh_upload_host=""):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if ssh_upload_host:
        remote_template = (
            "set -e; "
            "APP=/var/www/newnavnav; "
            "TOKEN=$(sudo awk -F= '/^UPLOAD_TOKEN=/{print $2} /^NNN_UPLOAD_TOKEN=/{print $2} /^DATA_UPLOAD_TOKEN=/{print $2}' \"$APP/.env\" | tail -1); "
            "if [ -z \"$TOKEN\" ]; then TOKEN=$(sudo awk -F= '/TOKEN=/{print $2}' \"$APP/.env\" | head -1); fi; "
            "test -n \"$TOKEN\"; "
            "curl -sS --fail-with-body --max-time %d -X POST http://127.0.0.1:8080%s "
            "-H 'Content-Type: application/json' -H \"X-Upload-Token: $TOKEN\" --data-binary @-"
        )
        remote = remote_template % (max(int(timeout), 60), path)
        completed = subprocess.run(
            ["ssh", ssh_upload_host, remote],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(timeout + 30, 90),
            check=False,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", "replace")
            stdout = completed.stdout.decode("utf-8", "replace")
            raise RuntimeError("ssh upload failed {}: {}{}".format(completed.returncode, stderr, stdout))
        return json.loads(completed.stdout.decode("utf-8"))

    url = server.rstrip("/") + path
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Upload-Token": token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError("upload failed {}: {}".format(exc.code, body))


def upload_rows(server, token, source, rows, timeout, ssh_upload_host=""):
    return post_json(
        server,
        token,
        "/api/v1/minute-history/backfill",
        {"source": source, "rows": rows},
        timeout,
        ssh_upload_host,
    )


def upload_historical_rebuild(server, token, source, fund_rows, reference_rows, timeout, ssh_upload_host=""):
    return post_json(
        server,
        token,
        "/api/v1/minute-history/historical-rebuild",
        {
            "source": source,
            "fund_rows": fund_rows,
            "reference_rows": reference_rows,
        },
        max(timeout, 60),
        ssh_upload_host,
    )


def resolve_trade_date(value):
    text = str(value or "").strip()
    if not text:
        return datetime.now().strftime("%Y%m%d")
    for pattern in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, pattern).strftime("%Y%m%d")
        except ValueError:
            pass
    raise ValueError("invalid trade date: {}".format(value))


def trade_date_to_date(value):
    return datetime.strptime(resolve_trade_date(value), "%Y%m%d").date()


def parse_trade_days(args):
    if args.start_date or args.end_date:
        start = trade_date_to_date(args.start_date or args.trade_date)
        end = trade_date_to_date(args.end_date or args.trade_date)
        if end < start:
            raise ValueError("--end-date must be >= --start-date")
        days = []
        cursor = start
        while cursor <= end:
            if args.include_weekends or cursor.weekday() < 5:
                days.append(cursor)
            cursor += timedelta(days=1)
        if not days:
            raise ValueError("no trade days selected")
        return days
    return [trade_date_to_date(args.trade_date)]


def normalize_qmt_row(row, fallback_source):
    symbol = qmt_to_internal_symbol(row.get("symbol") or row.get("code") or "")
    close = as_float(row.get("last_price"))
    if close <= 0:
        close = as_float(row.get("close"))
    source = str(row.get("source") or fallback_source or "").strip()
    return {
        "trade_date": normalize_trade_date_field(row.get("trade_date") or row.get("date")),
        "symbol": symbol,
        "minute": str(row.get("minute") or row.get("timestamp") or "").strip(),
        "timestamp": str(row.get("timestamp") or row.get("minute") or "").strip(),
        "last_price": close,
        "open": as_float(row.get("open")),
        "high": as_float(row.get("high")),
        "low": as_float(row.get("low")),
        "close": close,
        "volume": as_float(row.get("volume")),
        "amount": as_float(row.get("amount")),
        "source": source,
    }


def normalize_qmt_rows(rows, fallback_source):
    out = []
    for row in rows:
        normalized = normalize_qmt_row(row, fallback_source)
        if normalized["symbol"] and normalized["minute"] and normalized["last_price"] > 0:
            out.append(normalized)
    out.sort(key=lambda item: (item["symbol"], item["minute"]))
    return out


def normalize_trade_date_field(value):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        pass
    if len(text) >= 8 and text[:8].isdigit():
        return "{}-{}-{}".format(text[:4], text[4:6], text[6:8])
    return text


def as_float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def write_rows(root, trade_day, rows):
    if not root or not rows:
        return
    folder = Path(root) / trade_day.strftime("%Y%m%d")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "qmt_fund_1m.csv"
    ordered = []
    offsets = {}
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                key = (str(row.get("symbol") or "").strip(), str(row.get("minute") or "").strip())
                if key[0] and key[1]:
                    offsets[key] = len(ordered)
                    ordered.append(row)
    for row in rows:
        key = (str(row.get("symbol") or "").strip(), str(row.get("minute") or "").strip())
        if key[0] and key[1]:
            out = {field: row.get(field, "") for field in FIELDS}
            if key in offsets:
                ordered[offsets[key]] = out
            else:
                offsets[key] = len(ordered)
                ordered.append(out)
    tmp_path = path.with_suffix(".csv.tmp")
    with tmp_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in FIELDS} for row in ordered)
    tmp_path.replace(path)
    print("stored rows={} merged_rows={} path={}".format(len(rows), len(ordered), path), flush=True)


def read_reference_rows(root, trade_day):
    path = Path(root) / trade_day.strftime("%Y%m%d") / "ibkr_reference_1m.csv"
    if not path.exists():
        print("warning: missing reference rows {}".format(path), file=sys.stderr, flush=True)
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        rows = [{field: row.get(field, "") for field in FIELDS} for row in csv.DictReader(handle)]
    rows = [coerce_row_types(row) for row in rows]
    return [row for row in rows if row.get("symbol") and row.get("minute") and as_float(row.get("last_price")) > 0]


def read_stored_rows(root, trade_day, symbols):
    path = Path(root) / trade_day.strftime("%Y%m%d") / "qmt_fund_1m.csv"
    if not path.exists():
        print("warning: missing stored QMT rows {}".format(path), file=sys.stderr, flush=True)
        return []
    wanted = {qmt_to_internal_symbol(symbol) for symbol in symbols if symbol}
    with path.open(newline="", encoding="utf-8") as handle:
        rows = [{field: row.get(field, "") for field in FIELDS} for row in csv.DictReader(handle)]
    out = []
    for row in rows:
        row["symbol"] = qmt_to_internal_symbol(row.get("symbol"))
        row = coerce_row_types(row)
        if wanted and row["symbol"] not in wanted:
            continue
        if row.get("symbol") and row.get("minute") and as_float(row.get("last_price")) > 0:
            out.append(row)
    out.sort(key=lambda item: (item["symbol"], item["minute"]))
    return out


def coerce_row_types(row):
    out = {field: row.get(field, "") for field in FIELDS}
    for field in NUMERIC_FIELDS:
        out[field] = as_float(out.get(field))
    return out


def main():
    parser = argparse.ArgumentParser(description="Pull QMT online 1m bars and backfill web minute history")
    parser.add_argument("--qmt-host", required=True, help="QMT computer IP or hostname")
    parser.add_argument("--qmt-port", type=int, default=DEFAULT_QMT_PORT)
    parser.add_argument("--server", default=os.environ.get("NNN_SERVER_URL", DEFAULT_SERVER))
    parser.add_argument("--token", default=os.environ.get("NNN_UPLOAD_TOKEN", ""))
    parser.add_argument("--symbols", default="", help="Comma-separated QMT symbols. Empty means all web branch fund symbols.")
    parser.add_argument("--trade-date", default="", help="YYYYMMDD. Empty means today.")
    parser.add_argument("--start-date", default="", help="Inclusive start date for multi-day backfill.")
    parser.add_argument("--end-date", default="", help="Inclusive end date for multi-day backfill.")
    parser.add_argument("--include-weekends", action="store_true")
    parser.add_argument("--source", default="qmt_online_backfill")
    parser.add_argument("--store-root", default=os.environ.get("NNN_QMT_STORE_DIR", str(DEFAULT_STORE_ROOT)))
    parser.add_argument("--reference-store-root", default=os.environ.get("NNN_QUOTE_STORE_DIR", str(DEFAULT_REFERENCE_STORE_ROOT)))
    parser.add_argument("--symbol-chunk-size", type=int, default=40)
    parser.add_argument("--upload-chunk-size", type=int, default=20000)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--historical-rebuild", action="store_true", help="Upload rows to historical rebuild endpoint with IBKR reference rows.")
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--use-stored", action="store_true", help="Read qmt_fund_1m.csv from --store-root instead of requesting QMT.")
    parser.add_argument("--ssh-upload-host", default="", help="Upload by running curl on this SSH host, usually txy.")
    args = parser.parse_args()

    if not args.token and not args.no_upload and not args.ssh_upload_host:
        raise SystemExit("missing token: pass --token or set NNN_UPLOAD_TOKEN")
    trade_days = parse_trade_days(args)
    symbols = parse_symbols(args.symbols)
    if not symbols:
        symbols = fetch_branch_symbols(args.server)
    if not symbols:
        raise SystemExit("no symbols to request")

    total_rows = 0
    total_accepted = 0
    qmt_symbols = [internal_to_qmt_symbol(symbol) for symbol in symbols]
    print(
        "qmt minute backfill dates={}..{} days={} symbols={}".format(
            trade_days[0].isoformat(),
            trade_days[-1].isoformat(),
            len(trade_days),
            len(qmt_symbols),
        ),
        flush=True,
    )
    for trade_day in trade_days:
        trade_date = trade_day.strftime("%Y%m%d")
        if args.use_stored:
            day_rows = read_stored_rows(args.store_root, trade_day, symbols)
            print("loaded {} stored rows for {}".format(len(day_rows), trade_date), flush=True)
        else:
            day_rows = []
            for symbol_chunk in chunks(qmt_symbols, max(1, args.symbol_chunk_size)):
                rows = request_qmt_rows(args.qmt_host, args.qmt_port, symbol_chunk, trade_date, args.timeout)
                rows = normalize_qmt_rows(rows, args.source)
                day_rows.extend(rows)
                print("pulled {} rows for {} {}".format(len(rows), trade_date, ",".join(symbol_chunk)), flush=True)
            write_rows(args.store_root, trade_day, day_rows)
        total_rows += len(day_rows)
        if args.no_upload or not day_rows:
            continue
        if args.historical_rebuild:
            reference_rows = read_reference_rows(args.reference_store_root, trade_day)
            result = upload_historical_rebuild(
                args.server,
                args.token,
                args.source,
                day_rows,
                reference_rows,
                args.timeout,
                args.ssh_upload_host,
            )
            accepted = int(result.get("accepted") or 0)
            total_accepted += accepted
            warnings = result.get("warnings") or []
            print(
                "historical rebuild date={} fund_rows={} reference_rows={} accepted={} skipped={} warnings={}".format(
                    trade_date,
                    len(day_rows),
                    len(reference_rows),
                    accepted,
                    result.get("skipped") or 0,
                    len(warnings),
                ),
                flush=True,
            )
            if warnings:
                print("first warning:", warnings[0], flush=True)
            continue
        for row_chunk in chunks(day_rows, max(1, args.upload_chunk_size)):
            result = upload_rows(args.server, args.token, args.source, row_chunk, args.timeout, args.ssh_upload_host)
            accepted = int(result.get("accepted") or 0)
            total_accepted += accepted
            warnings = result.get("warnings") or []
            print("uploaded accepted={} skipped={} warnings={}".format(accepted, result.get("skipped") or 0, len(warnings)))
            if warnings:
                print("first warning:", warnings[0])

    print(
        "done dates={}..{} symbols={} pulled={} accepted={}".format(
            trade_days[0].strftime("%Y%m%d"),
            trade_days[-1].strftime("%Y%m%d"),
            len(symbols),
            total_rows,
            total_accepted,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("error:", exc, file=sys.stderr)
        raise
