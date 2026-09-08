#!/usr/bin/env python3
"""Bounded, read-only PCF assessment for the two user-selected ETFs.

Never logs credentials or raw business rows. API/exchange values stay in memory;
only keys, types, counts and comparison booleans are emitted.
"""
import argparse
import contextlib
import datetime as dt
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import platform
import signal
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

TARGETS = ((102, '159518'), (101, '513350'))
BASIC_MAP = {
 'creation_redemption_unit': ('CreationRedemptionUnit', 100),
 'max_cash_ratio': ('MaxCashRatio', 1000000),
 'estimate_cash_component': ('EstimateCashComponent', 100000),
 'cash_component': ('CashComponent', 100000),
 'nav_per_cu': ('NAVperCU', 1000000), 'nav': ('NAV', 1000000),
 'creation_limit': ('CreationLimit', 100), 'redemption_limit': ('RedemptionLimit', 100),
 'net_creation_limit': ('NetCreationLimit', 100), 'net_redemption_limit': ('NetRedemptionLimit', 100),
 'record_num': ('RecordNum', 100), 'total_record_num': ('TotalRecordNum', 100),
}

@contextlib.contextmanager
def quiet():
    sys.stdout.flush(); sys.stderr.flush()
    saved = [os.dup(1), os.dup(2)]
    with open(os.devnull, 'w') as sink:
        os.dup2(sink.fileno(), 1); os.dup2(sink.fileno(), 2)
        try: yield
        finally:
            sys.stdout.flush(); sys.stderr.flush()
            for fd, backup in zip((1, 2), saved): os.dup2(backup, fd); os.close(backup)

def number(x):
    try: return Decimal(str(x).replace(',', '').strip())
    except InvalidOperation: return None

def scalar_equal(raw, exchange, scale):
    a,b=number(raw),number(exchange)
    return None if a is None or b is None else a / scale == b

def xml_parts(body):
    root=ET.fromstring(body); top={}; rows=[]
    def visit(node, component=False):
        name=node.tag.split('}')[-1]
        if name in ('Component', 'ComponentRecord'):
            rows.append({x.tag.split('}')[-1]:(x.text or '').strip() for x in node.iter() if x is not node and len(x)==0})
            return
        if len(node)==0:
            top[name]=(node.text or '').strip()
        else:
            for child in node:visit(child)
    visit(root)
    return root.tag.split('}')[-1],top,rows

def fetch(url, referer):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':referer})
    try:
        with urllib.request.urlopen(req,timeout=20) as response:
            body=response.read(8*1024*1024+1)
            if len(body)>8*1024*1024:return None,{'error':'oversized'}
            return body,{'http_status':response.status,'bytes':len(body)}
    except Exception as exc:
        return None,{'error_type':type(exc).__name__,'http_status':getattr(exc,'code',None)}

def exchange_compare(basic, cons, market, code):
    day=str(basic.get('trading_day',''))
    if len(day)!=8 or not day.isdigit():return {'error':'invalid_api_trading_day'}
    url=(f'https://reportdocs.static.szse.cn/files/text/ETFDown/pcf_{code}_{day}.xml'
         if market==102 else f'https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode={code}')
    body,report=fetch(url,'https://www.szse.cn/' if market==102 else 'https://www.sse.com.cn/')
    if body is None:return report
    try: root,top,rows=xml_parts(body)
    except Exception as exc:return dict(report,parse_error=type(exc).__name__)
    report.update(root=root,summary_fields=sorted(top),component_fields=sorted({k for row in rows for k in row}),component_rows=len(rows))
    report['same_trading_day']=top.get('TradingDay','').replace('-','')==day
    if not report['same_trading_day']:
        report['comparisons_skipped']='trading_day_mismatch'
        return report
    mapping=dict(BASIC_MAP)
    if market==101:
        mapping.update(estimate_cash_component=('EstimatedCashComponent',100000),cash_component=('PreCashComponent',100000),total_record_num=('RecordNumber',100))
    report['numeric_matches']={key:scalar_equal(basic.get(key),top[field],scale) for key,(field,scale) in mapping.items() if field in top}
    report['api_unavailable_limit_fields']=[key for key in ('creation_limit','redemption_limit') if market==101 and basic.get(key)==0 and number(top.get(mapping[key][0])) not in (None,Decimal(0))]
    report['flags_match']={key:basic.get(key)==top[field] for key,field in [('publish','Publish'),('creation','Creation'),('redemption','Redemption'),('creation_redemption_switch','CreationRedemptionSwitch')] if field in top}
    codekey='UnderlyingSecurityID' if market==102 else 'InstrumentID'
    api={str(x.get('security_code','')):x for x in cons}
    report['component_identity_sets_equal']=set(api)=={x.get(codekey,'') for x in rows}
    comparisons=[]
    for row in rows:
        a=api.get(row.get(codekey,''))
        if not a:continue
        fields={'component_share':('ComponentShare' if market==102 else 'Quantity',100),
                'premium_ratio':('PremiumRatio',1000000),'discount_ratio':('DiscountRatio',1000000),
                'creation_cash_substitute':('CreationCashSubstitute',100000),
                'redemption_cash_substitute':('RedemptionCashSubstitute',100000),
                'substitution_cash_amount':('SubstitutionCashAmount',100000)}
        comparisons.append({key:scalar_equal(a.get(key),row[field],scale) for key,(field,scale) in fields.items() if field in row})
    report['component_matches']={key:{'compared':sum(key in row for row in comparisons),'equal':sum(row.get(key) is True for row in comparisons)} for key in sorted({k for row in comparisons for k in row})}
    if code=='513350':
        body,manager=fetch(f'https://wap.fullgoal.com.cn/ws-business-server/fund/getFundSg?siteno=main&merchantId=&productCode=513350&tradeDate={day[:4]}-{day[4:6]}-{day[6:]}','https://www.fullgoal.com.cn/')
        if body:
            try:
                data=json.loads(body).get('data',{})
                manager['fields']=sorted(data)
                manager['numeric_matches']={k:scalar_equal(basic.get(k),data[field],scale) for k,field,scale in [('creation_redemption_unit','minShdy',100),('estimate_cash_component','minYgcash',100000),('nav_per_cu','minShnav',1000000)] if field in data}
            except Exception as exc:manager['parse_error']=type(exc).__name__
        report['current_fullgoal_source']=manager
    return report

def summarize(result, targets):
    if not isinstance(result,list):return {'result_type':type(result).__name__}
    expected={code:market for market,code in targets}; entries=[];seen=[]
    for basic,cons in result:
        code=str(basic.get('security_code',''));seen.append(code)
        entry={'requested_identity_matches':basic.get('market_type')==expected.get(code),
               'basic_fields':sorted(basic),'basic_types':{k:type(v).__name__ for k,v in basic.items()},
               'basic_empty_fields':sorted(k for k,v in basic.items() if v in ('',None)),
               'basic_zero_fields':sorted(k for k,v in basic.items() if type(v) is int and v==0),
               'component_rows':len(cons),'component_fields':sorted({k for row in cons for k in row}),
               'component_zero_fields':sorted(k for k in (cons[0] if cons else {}) if all(type(row.get(k)) is int and row.get(k)==0 for row in cons)),
               'component_empty_fields':sorted(k for k in (cons[0] if cons else {}) if all(row.get(k) in ('',None) for row in cons)),
               'component_types':{k:sorted({type(row.get(k)).__name__ for row in cons}) for k in (cons[0] if cons else {})},
               'declared_component_count_matches':scalar_equal(basic.get('total_record_num'),len(cons),100),
               'current_day':str(basic.get('trading_day'))==dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime('%Y%m%d')}
        entries.append(entry)
    return {'records':len(result),'returned_exact_requested_set':len(seen)==len(expected) and set(seen)==set(expected),'entries':entries}

def alarm(*_):raise TimeoutError('bounded oracle timeout')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--backend',choices=['official','mac'],required=True)
    parser.add_argument('--credential-stdin',action='store_true');parser.add_argument('--pair',action='store_true')
    args=parser.parse_args();report={'backend':args.backend,'at':dt.datetime.now(dt.timezone.utc).isoformat(),'queries':[]}
    signal.signal(signal.SIGALRM,alarm)
    with quiet():
        if args.credential_stdin: creds=json.load(sys.stdin)
        else:
            import yaml
            config=yaml.safe_load(Path('/opt/galaxy-relay/config/config.yaml').read_text())['amazingdata']
            env={}
            for line in Path('/etc/galaxy-relay/relay.env').read_text().splitlines():
                if '=' in line and not line.lstrip().startswith('#'):
                    k,v=line.split('=',1);env[k.strip()]=v.strip().strip("'\"")
            creds={'username':env[config['username_env']],'password':env[config['password_env']],**config['hosts'][0]}
        if args.backend=='official':import tgw
        else:
            sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src/python'))
            import tgw_macos as tgw
        report['runtime']={'os':platform.system(),'machine':platform.machine(),
                           'python':sys.executable,'sdk_module':str(tgw.__file__),
                           'mac_module_loaded':any(k=='tgw_macos' or k.startswith('tgw_macos.') for k in sys.modules)}
        maps=Path('/proc/self/maps')
        if maps.exists():
            report['runtime']['native_tgw_libraries']=sorted({line.split()[-1] for line in maps.read_text().splitlines() if '.so' in line and ('tgw' in line.lower() or 'amazing' in line.lower())})
        cfg=tgw.Cfg()
        if args.backend=='mac':cfg.set(username=creds['username'],password=creds['password'],server_vip=creds['host'],server_port=int(creds['port']),force_logout=False)
        else:
            cfg.username=creds['username'];cfg.password=creds['password'];cfg.server_vip=creds['host'];cfg.server_port=int(creds['port']);cfg.force_logout=False
        try:
            signal.alarm(45)
            report['login']=bool(tgw.Login(cfg,tgw.ApiMode.kInternetMode));signal.alarm(0)
            if report['login']:
                groups=[(x,) for x in TARGETS]+([TARGETS] if args.pair and args.backend=='official' else [])
                for index,targets in enumerate(groups):
                    if index:time.sleep(5)
                    items=[]
                    for market,code in targets:
                        item=tgw.SubCodeTableItem()
                        if args.backend=='mac':item.market=market;item.security_code=code.encode('ascii')
                        else:item.market=market;item.security_code=code
                        items.append(item)
                    query={'mode':'pair' if len(items)>1 else 'single','requested':[{ 'market':m,'symbol':c} for m,c in targets]}
                    started=time.monotonic()
                    try:
                        signal.alarm(45)
                        result,error=tgw.QueryETFInfo(items if len(items)>1 else items[0],return_df_format=False)
                        signal.alarm(0);query.update(error_code=error,error_type=type(error).__name__,elapsed_ms=round((time.monotonic()-started)*1000),summary=summarize(result,targets))
                        if error==0 and isinstance(result,list) and len(items)==1:
                            query['exchange_comparison']=exchange_compare(*result[0],*targets[0])
                    except Exception as exc:
                        signal.alarm(0);query['error_type']=type(exc).__name__
                    report['queries'].append(query)
                    if query.get('error_code') not in (0,None) or query.get('error_type') not in ('int',):break
        except Exception as exc:report['error_type']=type(exc).__name__
        finally:
            signal.alarm(15)
            try:tgw.Close();report['closed']=True
            except Exception as exc:report['close_error_type']=type(exc).__name__
            signal.alarm(0)
    print(json.dumps(report,ensure_ascii=False,sort_keys=True))

if __name__=='__main__': main()
