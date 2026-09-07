#!/usr/bin/env python3
"""Read-only, resumable TWS historical fetcher for C-group industry tools.

The important repair is deliberate: ``includeExpired`` is set only on FUT
contracts.  STK requests use the resolved SEHK contract without that flag.
All requests are serialized through the shared historical-data lock, and the
raw bars plus every attempt are persisted locally for independent checking.
No account, order, position, or live subscription API is called.
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import gzip
import hashlib
import json
import os
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.wrapper import EWrapper


ROOT = Path('/Users/ellis/工具程序开发/港股通相关性分析')
REWORK = ROOT / 'outputs/hedge_rework_01_20260906/agent_C'
LOCK = ROOT / 'outputs/hedge_selection_v2_20260906/resource_locks/ibkr_historical.lock'


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def contract_dict(c: Contract) -> dict:
    keys = ['conId', 'symbol', 'localSymbol', 'tradingClass',
            'lastTradeDateOrContractMonth', 'multiplier', 'exchange',
            'primaryExchange', 'currency', 'secType', 'includeExpired']
    return {k: getattr(c, k, None) for k in keys}


class HistoryApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.ready = threading.Event()
        self.events: dict[int, threading.Event] = {}
        self.contracts: dict[int, list] = {}
        self.bars: dict[int, list] = {}
        self.errors: list[dict] = []
        self.error_by_req: dict[int, list[dict]] = {}

    def nextValidId(self, orderId):
        self.ready.set()

    def managedAccounts(self, accountsList):
        # Intentionally ignored: account/position data are out of scope.
        return None

    def error(self, reqId, errorCode, errorString, *args):
        item = {'request_id': reqId, 'code': errorCode, 'message': errorString, 'received_at_utc': iso_now()}
        self.errors.append(item)
        self.error_by_req.setdefault(reqId, []).append(item)
        # Connectivity notices are not request completion signals.
        if reqId in self.events and errorCode not in (2104, 2106, 2158):
            if errorCode in (162, 200, 321, 366, 504, 502):
                self.events[reqId].set()

    def contractDetails(self, reqId, details):
        self.contracts.setdefault(reqId, []).append(details.contract)

    def contractDetailsEnd(self, reqId):
        self.events.setdefault(reqId, threading.Event()).set()

    def historicalData(self, reqId, bar):
        self.bars.setdefault(reqId, []).append({
            'date': str(bar.date), 'open': float(bar.open), 'high': float(bar.high),
            'low': float(bar.low), 'close': float(bar.close), 'volume': float(bar.volume),
            'barCount': int(bar.barCount), 'average': float(bar.average),
        })

    def historicalDataEnd(self, reqId, start, end):
        self.events.setdefault(reqId, threading.Event()).set()

    def begin(self, req_id: int):
        self.events[req_id] = threading.Event()
        self.contracts[req_id] = []
        self.bars[req_id] = []
        self.error_by_req[req_id] = []


def make_stock(symbol: str) -> Contract:
    c = Contract(); c.secType = 'STK'; c.symbol = symbol; c.exchange = 'SEHK'; c.primaryExchange = 'SEHK'; c.currency = 'HKD'
    return c


def make_future() -> Contract:
    c = Contract(); c.secType = 'FUT'; c.symbol = 'HBI'; c.exchange = 'HKFE'; c.currency = 'HKD'; c.includeExpired = True
    return c


def parse_day(s: str) -> date:
    return datetime.strptime(s, '%Y%m%d').date()


def date_range(start: str, end: str):
    d, last = parse_day(start), parse_day(end)
    while d <= last:
        if d.weekday() < 5:
            yield d.strftime('%Y%m%d')
        d += timedelta(days=1)


def choose_future(contracts: list[Contract], day: str) -> Contract | None:
    if not contracts:
        return None
    # Prefer the nearest listed expiry on/after the requested day; if the
    # historical response only exposes expired contracts, use the latest one.
    scored = []
    for c in contracts:
        raw = str(getattr(c, 'lastTradeDateOrContractMonth', '') or '')
        digits = ''.join(ch for ch in raw if ch.isdigit())
        expiry = digits[:8] if len(digits) >= 8 else (digits + '28' if len(digits) == 6 else '')
        scored.append((expiry or '99999999', c))
    on_or_after = [x for x in scored if x[0] >= day]
    return sorted(on_or_after or scored, key=lambda x: x[0])[0][1]


def classify(errors: list[dict], bars: list[dict], finished: bool) -> str:
    if bars and finished:
        return 'SUCCESS'
    codes = {int(x.get('code', -1)) for x in errors}
    if 321 in codes:
        return 'PARAMETER_ERROR'
    if 162 in codes or 200 in codes or 366 in codes:
        return 'NOT_FOUND'
    if 502 in codes or 504 in codes:
        return 'NETWORK_ERROR'
    return 'PARTIAL' if bars else 'OTHER_ERROR'


def do_contract_request(app: HistoryApp, req_id: int, query: Contract, timeout: float = 30.0):
    app.begin(req_id)
    app.reqContractDetails(req_id, query)
    finished = app.events[req_id].wait(timeout)
    return finished, list(app.contracts.get(req_id, [])), list(app.error_by_req.get(req_id, []))


def do_history_request(app: HistoryApp, req_id: int, contract: Contract, day: str, timeout: float = 40.0):
    app.begin(req_id)
    # 08:00 UTC is the end of the 16:00 Asia/Hong_Kong RTH day.
    end_dt = f'{day} 08:00:00 UTC'
    with LOCK.open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            app.reqHistoricalData(req_id, contract, end_dt, '1 D', '1 min', 'TRADES', 1, 2, False, [])
            finished = app.events[req_id].wait(timeout)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    return finished, list(app.bars.get(req_id, [])), list(app.error_by_req.get(req_id, []))


def connect_with_fallback(client_ids: list[int]):
    last = None
    for client_id in client_ids:
        app = HistoryApp()
        try:
            app.connect('127.0.0.1', 7496, clientId=client_id)
            threading.Thread(target=app.run, daemon=True).start()
            if app.ready.wait(15):
                return app, client_id, None
            last = {'client_id': client_id, 'error': 'TWS API ready timeout'}
            app.disconnect()
        except Exception as exc:
            last = {'client_id': client_id, 'error': f'{type(exc).__name__}: {exc}'}
            try: app.disconnect()
            except Exception: pass
    return None, None, last


def load_resume(path: Path):
    seen = set()
    if path.exists():
        opener = gzip.open if path.suffix == '.gz' else open
        with opener(path, 'rt', encoding='utf-8') as f:
            for line in f:
                try:
                    x = json.loads(line)
                    if x.get('tool_id') and x.get('day'):
                        seen.add((x['tool_id'], x['day']))
                except json.JSONDecodeError:
                    continue
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='20260303')
    ap.add_argument('--end', default='20260904')
    ap.add_argument('--tools', default='HBI_FUT,03069,03174')
    ap.add_argument('--client-ids', default='7313,7314,7315,7316')
    ap.add_argument('--sleep-seconds', type=float, default=0.85)
    ap.add_argument('--max-days', type=int, default=0, help='0 means all weekdays in range')
    ap.add_argument('--output', default=str(REWORK / 'data/industry_history_bars.jsonl.gz'))
    ap.add_argument('--attempts', default=str(REWORK / 'data/fetch_attempts_industry.jsonl'))
    args = ap.parse_args()
    out = Path(args.output); attempts_path = Path(args.attempts)
    out.parent.mkdir(parents=True, exist_ok=True); attempts_path.parent.mkdir(parents=True, exist_ok=True)
    tools = [x.strip() for x in args.tools.split(',') if x.strip()]
    days = list(date_range(args.start, args.end))
    if args.max_days:
        days = days[:args.max_days]
    seen = load_resume(out)
    client_ids = [int(x) for x in args.client_ids.split(',')]
    app, client_id, connection_error = connect_with_fallback(client_ids)
    run = {'run_id': f'C-INDUSTRY-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}', 'generated_at_utc': iso_now(),
           'start': args.start, 'end': args.end, 'tools': tools, 'client_id': client_id,
           'read_only': True, 'shared_lock': str(LOCK), 'includeExpired_rule': 'FUT_only',
           'connection_error': connection_error, 'attempted_days': len(days), 'resumed_records': len(seen),
           'contracts': {}, 'summary': {'SUCCESS': 0, 'PARTIAL': 0, 'PARAMETER_ERROR': 0, 'NOT_FOUND': 0, 'NETWORK_ERROR': 0, 'OTHER_ERROR': 0, 'SKIPPED': 0}}
    if app is None:
        print(json.dumps(run, ensure_ascii=False, indent=2)); return 2
    req_id = 1000
    queries = {'HBI_FUT': make_future(), '03069': make_stock('3069'), '03174': make_stock('3174')}
    resolved: dict[str, list[Contract]] = {}
    for tool_id in tools:
        finished, contracts, errors = do_contract_request(app, req_id, queries[tool_id]); req_id += 1
        resolved[tool_id] = contracts
        run['contracts'][tool_id] = {'request_finished': finished, 'contracts': [contract_dict(c) for c in contracts], 'errors': errors}
    with out.open('ab') as raw_f, attempts_path.open('a', encoding='utf-8') as att_f:
        gz = gzip.GzipFile(fileobj=raw_f, mode='ab')
        text = __import__('io').TextIOWrapper(gz, encoding='utf-8')
        try:
            for day in days:
                for tool_id in tools:
                    key = (tool_id, day)
                    if key in seen:
                        run['summary']['SKIPPED'] += 1; continue
                    contract = choose_future(resolved[tool_id], day) if tool_id == 'HBI_FUT' else (resolved[tool_id][0] if resolved[tool_id] else None)
                    started = iso_now()
                    if contract is None:
                        attempt = {'attempt_id': f'{run["run_id"]}-{tool_id}-{day}', 'tool_id': tool_id, 'day': day, 'requested_at_utc': started,
                                   'status': 'NOT_FOUND', 'error_code': None, 'error_summary': 'No contractDetails result', 'returned_rows': 0,
                                   'returned_start': None, 'returned_end': None, 'raw_path': str(out), 'sha256': None,
                                   'includeExpired_used': False, 'contract': None}
                    else:
                        # Critical repair: STK receives False/default; only FUT keeps True.
                        contract.includeExpired = (getattr(contract, 'secType', '') == 'FUT')
                        finished, bars, errors = do_history_request(app, req_id, contract, day); req_id += 1
                        status = classify(errors, bars, finished)
                        run['summary'][status] = run['summary'].get(status, 0) + 1
                        record = {'run_id': run['run_id'], 'tool_id': tool_id, 'day': day, 'secType': contract.secType,
                                  'contract': contract_dict(contract), 'bar_count': len(bars), 'bars': bars,
                                  'history_request_finished': finished, 'errors': errors}
                        text.write(json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n'); text.flush()
                        attempt = {'attempt_id': f'{run["run_id"]}-{tool_id}-{day}', 'tool_id': tool_id, 'day': day,
                                   'requested_at_utc': started, 'completed_at_utc': iso_now(), 'status': status,
                                   'error_code': errors[-1]['code'] if errors else None,
                                   'error_summary': '; '.join(str(e.get('message', '')) for e in errors) if errors else None,
                                   'returned_rows': len(bars), 'returned_start': bars[0]['date'] if bars else None,
                                   'returned_end': bars[-1]['date'] if bars else None, 'raw_path': str(out), 'sha256': None,
                                   'includeExpired_used': bool(contract.includeExpired), 'contract': contract_dict(contract)}
                    att_f.write(json.dumps(attempt, ensure_ascii=False) + '\n'); att_f.flush()
                    seen.add(key)
                    time.sleep(max(0.0, args.sleep_seconds))
        finally:
            text.detach(); gz.close()
    app.disconnect()
    # The final content hash is recorded after all bars are written.
    run['raw_sha256'] = sha(out) if out.exists() else None
    run['attempts_path'] = str(attempts_path); run['attempts_sha256'] = sha(attempts_path) if attempts_path.exists() else None
    summary_path = REWORK / 'data/industry_history_run.json'
    summary_path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(run, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
