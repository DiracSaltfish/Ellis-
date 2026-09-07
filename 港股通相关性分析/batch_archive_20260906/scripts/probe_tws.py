#!/usr/bin/env python3
"""Read-only TWS contract and one-day historical-data probe. No order APIs."""
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract


class Probe(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.ready = threading.Event()
        self.done = threading.Event()
        self.contracts = []
        self.bars = []
        self.errors = []

    def nextValidId(self, orderId): self.ready.set()
    def managedAccounts(self, accountsList): pass
    def error(self, reqId, errorCode, errorString, *args):
        self.errors.append({'request_id': reqId, 'code': errorCode, 'message': errorString})
        if reqId >= 0 and errorCode not in (2104, 2106, 2158): self.done.set()
    def contractDetails(self, reqId, details): self.contracts.append(details.contract)
    def contractDetailsEnd(self, reqId): self.done.set()
    def historicalData(self, reqId, bar): self.bars.append(bar)
    def historicalDataEnd(self, reqId, start, end): self.done.set()


def run():
    app = Probe()
    out = {'generated_at': datetime.now(timezone.utc).isoformat(), 'host': '127.0.0.1',
           'port': 7496, 'read_only_operations': ['contractDetails', 'historicalData'], 'results': []}
    try:
        app.connect('127.0.0.1', 7496, clientId=9061637)
        threading.Thread(target=app.run, daemon=True).start()
        out['api_ready'] = app.ready.wait(12)
        if out['api_ready']:
            for i, symbol in enumerate(['HSI', 'HHI', 'HTI']):
                app.done.clear(); app.contracts = []; app.bars = []
                c = Contract(); c.secType = 'FUT'
                c.exchange = 'HKFE'; c.currency = 'HKD'
                c.localSymbol = symbol + 'N6'; c.includeExpired = True
                app.reqContractDetails(100+i, c)
                ended = app.done.wait(15)
                result = {'symbol': symbol, 'contract_request_finished': ended,
                          'contracts': [{k: getattr(x,k) for k in ['conId','localSymbol','symbol','lastTradeDateOrContractMonth','multiplier','exchange','currency']} for x in app.contracts]}
                if ended and len(app.contracts) == 1:
                    app.done.clear()
                    actual = app.contracts[0]; actual.includeExpired = True
                    app.reqHistoricalData(200+i,actual,'20260730 08:00:00 UTC','1 D','1 min','TRADES',1,2,False,[])
                    result['history_request_finished'] = app.done.wait(20)
                    result['bar_count'] = len(app.bars)
                    result['first_bar'] = app.bars[0].date if app.bars else None
                    result['last_bar'] = app.bars[-1].date if app.bars else None
                    if not result['history_request_finished']: app.cancelHistoricalData(200+i)
                out['results'].append(result)
    except Exception as e:
        out['exception_type'] = type(e).__name__
    finally:
        app.disconnect()
    out['errors'] = app.errors
    return out


if __name__ == '__main__':
    target = Path(__file__).resolve().parents[1] / 'data/inventory/tws_probe.json'
    target.write_text(json.dumps(run(), ensure_ascii=False, indent=2))
    print(target)
