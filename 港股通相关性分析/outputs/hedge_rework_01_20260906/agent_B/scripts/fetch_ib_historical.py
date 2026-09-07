#!/usr/bin/env python3
"""Read-only IBKR contract discovery and one-day historical bars.

This script deliberately has no order API calls.  The caller supplies a small
date list; one request is made at a time under the shared historical-data
fcntl lock, with a pacing delay between requests.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.wrapper import EWrapper


ROOT = Path("/Users/ellis/工具程序开发/港股通相关性分析")
REWORK_ROOT = ROOT / "outputs/hedge_rework_01_20260906"
LOCK_PATH = ROOT / "outputs/hedge_selection_v2_20260906/resource_locks/ibkr_historical.lock"
OUT_ROOT = REWORK_ROOT / "agent_B/data/raw/ibkr_new_period"


class Reader(EWrapper, EClient):
    def __init__(self) -> None:
        EClient.__init__(self, self)
        self.ready = threading.Event()
        self.contract_done = threading.Event()
        self.history_done = threading.Event()
        self.contracts: list[Contract] = []
        self.bars = []
        self.errors = []

    def nextValidId(self, orderId):  # noqa: N802
        print(f"nextValidId callback {orderId}", flush=True)
        self.ready.set()

    def error(self, reqId, errorCode, errorString, *args):  # noqa: N802
        self.errors.append({"request_id": reqId, "code": errorCode, "message": errorString})
        if reqId >= 0 and errorCode not in (2104, 2106, 2158, 2108, 2157):
            # Let the request loop inspect the error list; the end callback is
            # not guaranteed for all rejected requests.
            if errorCode in (162, 200, 321, 354, 420, 502):
                self.history_done.set()

    def contractDetails(self, reqId, contractDetails):  # noqa: N802
        self.contracts.append(contractDetails.contract)

    def contractDetailsEnd(self, reqId):  # noqa: N802
        self.contract_done.set()

    def historicalData(self, reqId, bar):  # noqa: N802
        self.bars.append({
            "date": str(bar.date),
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": float(getattr(bar, "volume", 0.0)),
            "wap": float(getattr(bar, "wap", 0.0)),
            "count": int(getattr(bar, "barCount", 0)),
        })

    def historicalDataEnd(self, reqId, start, end):  # noqa: N802
        self.history_done.set()


def spec_contract(spec: dict) -> Contract:
    c = Contract()
    for key, value in spec.items():
        setattr(c, key, value)
    c.includeExpired = c.secType == "FUT"
    return c


def contract_dict(c: Contract) -> dict:
    keys = [
        "conId", "symbol", "localSymbol", "secType", "exchange",
        "primaryExchange", "currency", "lastTradeDateOrContractMonth",
        "multiplier", "tradingClass", "minTick",
    ]
    return {k: getattr(c, k, None) for k in keys}


def request_contracts(app: Reader, req_id: int, spec: dict) -> list[Contract]:
    app.contract_done.clear()
    app.contracts = []
    app.reqContractDetails(req_id, spec_contract(spec))
    app.contract_done.wait(20)
    return list(app.contracts)


def request_day(app: Reader, req_id: int, contract: Contract, day: str) -> dict:
    app.history_done.clear()
    app.bars = []
    app.errors = []
    d = datetime.strptime(day, "%Y%m%d")
    end = (d + timedelta(days=1)).strftime("%Y%m%d") + " 00:00:00 UTC"
    c = spec_contract(contract_dict(contract))
    # Contract dict may contain read-only metadata; reset only fields IB needs.
    c.conId = contract.conId
    c.secType = contract.secType
    c.exchange = contract.exchange
    c.primaryExchange = contract.primaryExchange
    c.currency = contract.currency
    c.symbol = contract.symbol
    c.localSymbol = contract.localSymbol
    c.tradingClass = contract.tradingClass
    c.includeExpired = c.secType == "FUT"
    app.reqHistoricalData(req_id, c, end, "1 D", "1 min", "TRADES", 1, 2, False, [])
    app.history_done.wait(35)
    return {
        "date": day,
        "contract": contract_dict(contract),
        "bars": list(app.bars),
        "errors": list(app.errors),
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--contracts-only", action="store_true")
    p.add_argument("--client-id", type=int, default=7312)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7496)
    p.add_argument("--dates", nargs="+", required=True)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    specs = {
        "HSI_U6": {"symbol": "HSI", "secType": "FUT", "exchange": "HKFE", "currency": "HKD", "localSymbol": "HSIU6"},
        "HHI_U6": {"symbol": "HHI.HK", "secType": "FUT", "exchange": "HKFE", "currency": "HKD", "localSymbol": "HHIU6"},
        "HTI_U6": {"symbol": "HSTECH", "secType": "FUT", "exchange": "HKFE", "currency": "HKD", "localSymbol": "HTIU6"},
        "520600_SSE": {"symbol": "520600", "secType": "STK", "exchange": "SSE", "currency": "CNY", "primaryExchange": "SSE"},
    }
    app = Reader()
    output = {
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": args.host,
        "port": args.port,
        "client_id": args.client_id,
        "read_only_operations": ["contractDetails", "historicalData"],
        "dates": args.dates,
        "contracts": {},
        "request_log": [],
    }
    with LOCK_PATH.open("a+") as lock_file:
        print("waiting for historical lock", flush=True)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            print("lock acquired; connecting", flush=True)
            app.connect(args.host, args.port, clientId=args.client_id)
            print("connect returned", flush=True)
            def run_api_loop():
                try:
                    app.run()
                except BaseException as exc:  # pragma: no cover - API thread diagnostic
                    print(f"IB API loop failed: {type(exc).__name__}: {exc}", flush=True)
                    app.ready.set()
            threading.Thread(target=run_api_loop, daemon=True).start()
            print("waiting for nextValidId", flush=True)
            if not app.ready.wait(15):
                print("nextValidId timeout", flush=True)
                output["connect_error"] = "nextValidId timeout"
                return _save(output)
            chosen = {}
            req_id = 7000
            for name, spec in specs.items():
                matches = request_contracts(app, req_id, spec)
                req_id += 1
                output["contracts"][name] = [contract_dict(c) for c in matches]
                # Exact localSymbol is preferred; otherwise only one match is
                # accepted. This avoids silently selecting a different month.
                exact = [c for c in matches if name.endswith("_U6") and c.localSymbol in (spec.get("localSymbol"),)]
                if exact:
                    chosen[name] = exact[0]
                elif len(matches) == 1:
                    chosen[name] = matches[0]
                output["request_log"].append({"kind": "contractDetails", "name": name, "matches": len(matches)})
                time.sleep(1.0)
            if args.contracts_only:
                return _save(output)
            for name, contract in chosen.items():
                for day in args.dates:
                    row = request_day(app, req_id, contract, day)
                    req_id += 1
                    path = OUT_ROOT / f"{day}_{name}.json"
                    path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(f"saved {name} {day}: {len(row['bars'])} bars", flush=True)
                    output["request_log"].append({"kind": "historicalData", "name": name, "date": day, "bars": len(row["bars"]), "errors": row["errors"]})
                    time.sleep(2.0)
        finally:
            if app.isConnected():
                app.disconnect()
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    return _save(output)


def _save(output: dict) -> int:
    path = OUT_ROOT / "ibkr_fetch_manifest.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
