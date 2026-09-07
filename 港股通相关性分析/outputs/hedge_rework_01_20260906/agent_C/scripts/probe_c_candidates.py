#!/usr/bin/env python3
"""Read-only TWS probe for the locked industry hedge candidates.

Only contract metadata and one historical 1-minute day are requested.  Each
historical request uses the shared fcntl lock required by the research
contract.  No account, order, position, or live subscription API is called.
"""
import fcntl
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.wrapper import EWrapper


ROOT = Path('/Users/ellis/工具程序开发/港股通相关性分析')
LOCK = ROOT / 'outputs/hedge_selection_v2_20260906/resource_locks/ibkr_historical.lock'
OUT = ROOT / 'outputs/hedge_selection_v2_20260906/agent_C/data/tws_candidate_probe.json'


class Probe(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.ready = threading.Event()
        self.events = {}
        self.contracts = {}
        self.bars = {}
        self.errors = []

    def nextValidId(self, orderId):
        self.ready.set()

    def managedAccounts(self, accountsList):
        pass

    def error(self, reqId, errorCode, errorString, *args):
        self.errors.append({'request_id': reqId, 'code': errorCode, 'message': errorString})
        if reqId in self.events and errorCode not in (2104, 2106, 2158):
            self.events[reqId].set()

    def contractDetails(self, reqId, details):
        self.contracts.setdefault(reqId, []).append(details.contract)

    def contractDetailsEnd(self, reqId):
        self.events.setdefault(reqId, threading.Event()).set()

    def historicalData(self, reqId, bar):
        self.bars.setdefault(reqId, []).append(bar)

    def historicalDataEnd(self, reqId, start, end):
        self.events.setdefault(reqId, threading.Event()).set()

    def start_request(self, reqId):
        self.events[reqId] = threading.Event()
        self.contracts[reqId] = []
        self.bars[reqId] = []


def contract_dict(c):
    return {k: getattr(c, k, None) for k in [
        'conId', 'symbol', 'localSymbol', 'tradingClass',
        'lastTradeDateOrContractMonth', 'multiplier', 'exchange', 'primaryExchange', 'currency', 'secType'
    ]}


def make_requests():
    f = Contract(); f.secType = 'FUT'; f.symbol = 'HBI'; f.exchange = 'HKFE'; f.currency = 'HKD'; f.lastTradeDateOrContractMonth = '202609'; f.includeExpired = True
    e3069 = Contract(); e3069.secType = 'STK'; e3069.symbol = '3069'; e3069.exchange = 'SEHK'; e3069.primaryExchange = 'SEHK'; e3069.currency = 'HKD'
    e3174 = Contract(); e3174.secType = 'STK'; e3174.symbol = '3174'; e3174.exchange = 'SEHK'; e3174.primaryExchange = 'SEHK'; e3174.currency = 'HKD'
    return [('HBI_FUT', f), ('03069', e3069), ('03174', e3174)]


def run(client_id):
    app = Probe()
    app.connect('127.0.0.1', 7496, clientId=client_id)
    threading.Thread(target=app.run, daemon=True).start()
    ready = app.ready.wait(12)
    out = {'client_id': client_id, 'api_ready': ready, 'requests': [], 'errors': app.errors}
    if not ready:
        app.disconnect(); return out
    req_id = 100
    for tool_id, query in make_requests():
        app.start_request(req_id)
        app.reqContractDetails(req_id, query)
        finished = app.events[req_id].wait(20)
        contracts = app.contracts.get(req_id, [])
        item = {'tool_id': tool_id, 'contract_request_finished': finished, 'contracts': [contract_dict(c) for c in contracts]}
        chosen = contracts[0] if len(contracts) == 1 else None
        req_id += 1
        if chosen is not None:
            chosen.includeExpired = True
            # The single historical request is serialized with all agents.
            with LOCK.open('a+') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    app.start_request(req_id)
                    app.reqHistoricalData(req_id, chosen, '20260825 08:00:00 UTC', '1 D', '1 min', 'TRADES', 1, 2, False, [])
                    hist_finished = app.events[req_id].wait(25)
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
            bars = app.bars.get(req_id, [])
            item.update({
                'history_request_finished': hist_finished,
                'bar_count': len(bars),
                'first_bar_epoch': bars[0].date if bars else None,
                'last_bar_epoch': bars[-1].date if bars else None,
            })
            req_id += 1
            time.sleep(0.75)
        out['requests'].append(item)
    out['errors'] = app.errors
    app.disconnect()
    return out


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    result = {'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'host': '127.0.0.1', 'port': 7496,
              'operations': ['contractDetails', 'historicalData'], 'read_only': True}
    last_error = None
    for cid in [7313, 7316]:
        try:
            result['probe'] = run(cid)
            if result['probe'].get('api_ready'):
                break
        except Exception as exc:
            last_error = {'type': type(exc).__name__, 'message': str(exc), 'client_id': cid}
    if last_error:
        result['exception'] = last_error
    tmp = OUT.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(OUT)
    print(OUT)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
