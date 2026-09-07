#!/usr/bin/env python3
"""Read-only resumable HK STK 1-minute fetcher for 520600 PCF members.

It is intentionally separate from the futures fetcher: STK contracts use
includeExpired=False, and every contract/history attempt is retained.
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import gzip
import hashlib
import io
import json
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.wrapper import EWrapper


ROOT = Path("/Users/ellis/工具程序开发/港股通相关性分析")
REWORK = ROOT / "outputs/hedge_rework_01_20260906/agent_B"
LOCK = ROOT / "outputs/hedge_selection_v2_20260906/resource_locks/ibkr_historical.lock"


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path: Path):
    h = hashlib.sha256()
    if not path.exists():
        return ""
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def days(start: str, end: str):
    d, e = date.fromisoformat(start), date.fromisoformat(end)
    while d <= e:
        if d.weekday() < 5:
            yield d.strftime("%Y%m%d")
        d += timedelta(days=1)


def cdict(c: Contract):
    fields = ["conId", "symbol", "localSymbol", "tradingClass", "lastTradeDateOrContractMonth", "multiplier", "exchange", "primaryExchange", "currency", "secType", "includeExpired"]
    return {k: getattr(c, k, None) for k in fields}


class App(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.ready = threading.Event(); self.events = {}; self.contracts = {}; self.bars = {}; self.errors = {}

    def nextValidId(self, orderId):
        self.ready.set()

    def error(self, reqId, errorCode, errorString, *args):
        item = {"request_id": reqId, "code": errorCode, "message": errorString, "received_at_utc": now()}
        self.errors.setdefault(reqId, []).append(item)
        if reqId in self.events and errorCode not in (2104, 2106, 2158) and errorCode in (162, 200, 321, 366, 504, 502):
            self.events[reqId].set()

    def contractDetails(self, reqId, details):
        self.contracts.setdefault(reqId, []).append(details.contract)

    def contractDetailsEnd(self, reqId):
        self.events.setdefault(reqId, threading.Event()).set()

    def historicalData(self, reqId, bar):
        self.bars.setdefault(reqId, []).append({"date": str(bar.date), "open": float(bar.open), "high": float(bar.high), "low": float(bar.low), "close": float(bar.close), "volume": float(getattr(bar, "volume", 0.0)), "barCount": int(getattr(bar, "barCount", 0)), "average": float(getattr(bar, "average", 0.0))})

    def historicalDataEnd(self, reqId, start, end):
        self.events.setdefault(reqId, threading.Event()).set()

    def begin(self, req):
        self.events[req] = threading.Event(); self.contracts[req] = []; self.bars[req] = []; self.errors[req] = []


def stock_query(code: str):
    c = Contract(); c.secType = "STK"; c.symbol = code.lstrip("0") or "0"; c.exchange = "SEHK"; c.primaryExchange = "SEHK"; c.currency = "HKD"; c.includeExpired = False
    return c


def classify(errors, bars, finished):
    codes = {int(e.get("code", -1)) for e in errors}
    if bars and finished: return "SUCCESS"
    if 321 in codes: return "PARAMETER_ERROR"
    if 162 in codes or 200 in codes or 366 in codes: return "NOT_FOUND"
    if 502 in codes or 504 in codes: return "NETWORK_ERROR"
    return "PARTIAL" if bars else "OTHER_ERROR"


def request_contract(app, req, query):
    app.begin(req); app.reqContractDetails(req, query); finished = app.events[req].wait(30)
    return finished, list(app.contracts.get(req, [])), list(app.errors.get(req, []))


def request_history(app, req, contract, day, duration):
    app.begin(req); end = f"{day} 08:00:00 UTC"
    with LOCK.open("a+") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            app.reqHistoricalData(req, contract, end, duration, "1 min", "TRADES", 1, 2, False, [])
            finished = app.events[req].wait(45)
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
    return finished, list(app.bars.get(req, [])), list(app.errors.get(req, []))


def load_seen(path: Path):
    seen = set()
    if not path.exists(): return seen
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            try:
                x = json.loads(line); code = x.get("security_id"); bars = x.get("bars") or []
                if not bars: continue
                # A 1M response is stored as one record whose bars cover many
                # dates.  Mark every actual HK trading date as seen so a
                # repair pass does not re-request the whole archive.
                for bar in bars:
                    stamp = datetime.fromtimestamp(int(bar["date"]), tz=timezone.utc) + timedelta(hours=8)
                    seen.add((code, stamp.strftime("%Y%m%d")))
            except json.JSONDecodeError: pass
    return seen


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--codes", required=True); ap.add_argument("--start", default="2026-08-04"); ap.add_argument("--end", default="2026-09-04"); ap.add_argument("--client-id", type=int, default=7312); ap.add_argument("--sleep-seconds", type=float, default=1.0); ap.add_argument("--max-days", type=int, default=0); ap.add_argument("--duration", default="1 D")
    args = ap.parse_args(); codes = [x.strip().zfill(5) for x in args.codes.split(",") if x.strip()]; date_list = list(days(args.start, args.end)); date_list = date_list[:args.max_days] if args.max_days else date_list
    out = REWORK / "data/raw/new_period_520600/520600_stk_1m.jsonl.gz"; attempts = REWORK / "data/raw/new_period_520600/520600_stk_fetch_attempts.jsonl"; out.parent.mkdir(parents=True, exist_ok=True); attempts.parent.mkdir(parents=True, exist_ok=True)
    seen = load_seen(out); app = App(); app.connect("127.0.0.1", 7496, clientId=args.client_id); threading.Thread(target=app.run, daemon=True).start()
    if not app.ready.wait(15): raise RuntimeError("TWS nextValidId timeout")
    run_id = f"B-STK-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"; req = 12000; resolved = {}; contract_log = {}
    for code in codes:
        finished, matches, errors = request_contract(app, req, stock_query(code)); req += 1
        exact = matches[0] if len(matches) == 1 else next((x for x in matches if str(getattr(x, "symbol", "")).zfill(5) == code), None)
        resolved[code] = exact; contract_log[code] = {"finished": finished, "matches": [cdict(x) for x in matches], "errors": errors}; time.sleep(0.8)
    mode = "ab" if out.exists() else "wb"
    with out.open(mode) as raw, attempts.open("a", encoding="utf-8") as af:
        gz = gzip.GzipFile(fileobj=raw, mode="ab" if mode == "ab" else "wb"); text = io.TextIOWrapper(gz, encoding="utf-8")
        try:
            for day in date_list:
                for code in codes:
                    if (code, day) in seen: continue
                    started = now(); contract = resolved.get(code)
                    if contract is None:
                        status, bars, errors = "NOT_FOUND", [], []
                    else:
                        contract.includeExpired = False; finished, bars, errors = request_history(app, req, contract, day, args.duration); req += 1; status = classify(errors, bars, finished)
                    if contract is not None:
                        text.write(json.dumps({"run_id": run_id, "security_id": code, "day": day, "duration": args.duration, "contract": cdict(contract), "bars": bars, "history_request_finished": bool(contract and finished), "errors": errors}, ensure_ascii=False, separators=(",", ":")) + "\n"); text.flush()
                    attempt = {"attempt_id": f"{run_id}-{code}-{day}", "security_id": code, "day": day, "duration": args.duration, "status": status, "attempted_at_utc": started, "completed_at_utc": now(), "error_code": errors[-1].get("code") if errors else None, "error_summary": "; ".join(str(e.get("message", "")) for e in errors) if errors else None, "returned_rows": len(bars), "returned_start": bars[0]["date"] if bars else None, "returned_end": bars[-1]["date"] if bars else None, "raw_path": str(out.relative_to(ROOT.parent.parent)), "includeExpired_used": False, "contract": cdict(contract) if contract else None}
                    af.write(json.dumps(attempt, ensure_ascii=False) + "\n"); af.flush(); seen.add((code, day)); time.sleep(max(0.0, args.sleep_seconds))
        finally:
            text.detach(); gz.close()
    app.disconnect()
    summary = {"run_id": run_id, "codes": codes, "requested_days": date_list, "contracts": contract_log, "raw_path": str(out.relative_to(ROOT.parent.parent)), "raw_sha256": sha(out), "attempts_path": str(attempts.relative_to(ROOT.parent.parent)), "attempts_sha256": sha(attempts), "generated_at_utc": now()}
    (REWORK / "data/raw/new_period_520600/520600_stk_run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
