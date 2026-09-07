#!/usr/bin/env python3
"""Incrementally collect HTI minute prices using the existing HHI month map.
One request at a time; contract discovery + historical data only.
"""
import csv
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract

ROOT=Path(__file__).resolve().parents[1]
TZ=ZoneInfo('Asia/Hong_Kong')


class Client(EWrapper,EClient):
    def __init__(self):
        EClient.__init__(self,self);self.ready=threading.Event();self.events={};self.data={};self.errors={}
    def nextValidId(self,orderId):self.ready.set()
    def managedAccounts(self,accountsList):pass
    def error(self,reqId,errorCode,errorString,*extra):
        if reqId in self.events:
            self.errors[reqId]={'code':errorCode,'message':errorString};self.events[reqId].set()
    def contractDetails(self,reqId,details):self.data[reqId].append(details.contract)
    def contractDetailsEnd(self,reqId):self.events[reqId].set()
    def historicalData(self,reqId,bar):self.data[reqId].append(bar)
    def historicalDataEnd(self,reqId,start,end):self.events[reqId].set()
    def request(self,i):self.events[i]=threading.Event();self.data[i]=[]
    def wait(self,i,timeout=45):
        if not self.events[i].wait(timeout):raise TimeoutError('request '+str(i))
        if i in self.errors:raise RuntimeError(str(self.errors[i]))
        return self.data[i]


def main():
    manifest=json.loads((ROOT/'data/inventory/remote_inventory.json').read_text())
    days=set(manifest['common_dates'][-100:])
    groups={}
    with (ROOT/'data/raw/HHI_FUT_1min.csv').open() as f:
        for row in csv.DictReader(f):
            day=row['trade_date'].replace('-','')
            if day in days and row['series_role']=='primary':groups.setdefault(row['contract_month'],set()).add(day)
    cache=ROOT/'data/raw/hti_chunks';cache.mkdir(exist_ok=True)
    app=Client();i=1000
    try:
        app.connect('127.0.0.1',7496,clientId=9061839)
        threading.Thread(target=app.run,daemon=True).start()
        if not app.ready.wait(12):raise TimeoutError('API handshake')
        for month,dates in sorted(groups.items()):
            c=Contract();c.secType='FUT';c.exchange='HKFE';c.currency='HKD';c.symbol='HSTECH'
            c.lastTradeDateOrContractMonth=month;c.includeExpired=True
            app.request(i);app.reqContractDetails(i,c);contracts=app.wait(i);i+=1
            if len(contracts)!=1:raise ValueError('contract ambiguous '+month)
            c=contracts[0];c.includeExpired=True
            start=datetime.strptime(min(dates),'%Y%m%d');last=datetime.strptime(max(dates),'%Y%m%d')
            while start<=last:
                end=min(start+timedelta(days=6),last)
                path=cache/(month+'_'+end.strftime('%Y%m%d')+'.json')
                if not path.exists():
                    stamp=end.replace(hour=16,minute=1,tzinfo=TZ).astimezone(ZoneInfo('UTC')).strftime('%Y%m%d %H:%M:%S UTC')
                    app.request(i);app.reqHistoricalData(i,c,stamp,'7 D','1 min','TRADES',1,2,False,[])
                    bars=app.wait(i);i+=1
                    if not bars:raise ValueError('empty history '+month)
                    output={'contract_month':month,'con_id':c.conId,'local_symbol':c.localSymbol,'multiplier':c.multiplier,
                            'request_end':stamp,'bars':[[int(b.date),b.open,b.high,b.low,b.close] for b in bars]}
                    path.with_suffix('.tmp').write_text(json.dumps(output));path.with_suffix('.tmp').replace(path)
                    print(month,end.date(),len(bars),flush=True);time.sleep(.4)
                start=end+timedelta(days=1)
    finally:app.disconnect()
    records={}
    for path in sorted(cache.glob('*.json')):
        chunk=json.loads(path.read_text());month=chunk['contract_month']
        for epoch,op,hi,lo,cl in chunk['bars']:
            dt=datetime.fromtimestamp(epoch,TZ);day=dt.strftime('%Y%m%d')
            if day not in groups[month] or not (13*60<=dt.hour*60+dt.minute<=16*60):continue
            records[dt.isoformat()]={'timestamp':dt.isoformat(),'trade_date':dt.date().isoformat(),'instrument':'HTI_FUT',
                'series_role':'primary','contract_month':month,'con_id':chunk['con_id'],'local_symbol':chunk['local_symbol'],
                'multiplier':chunk['multiplier'],'close':cl,'source':'IBKR_TWS'}
    with (ROOT/'data/raw/HTI_FUT_1min.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(next(iter(records.values()))));w.writeheader();w.writerows(records[k] for k in sorted(records))
    print('DONE',len(records),'bars',flush=True)


if __name__=='__main__':main()
