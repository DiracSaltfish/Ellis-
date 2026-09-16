"""本机：抽取目标PCF/ETF行情，获取官方中间价和用户网站份额标签。
仅写本研究目录。行情按日压缩，远端分析无需搬运原始全市场档案。
"""
from pathlib import Path
import calendar, csv, gzip, io, json, shutil, time, urllib.request, urllib.parse, os
from concurrent.futures import ThreadPoolExecutor
from zipfile import ZipFile, ZIP_DEFLATED

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'inputs'; OUT.mkdir(exist_ok=True)
START='20250701'; END='20260630'
MID_MONTHS=[(2025,m) for m in range(6,13)]+[(2026,m) for m in range(1,7)]
STOCKS=Path(os.environ.get('STOCKS_ROOT','/Volumes/EllisFiles/Stocksdata'))

def get_json(url, post=False):
    for attempt in range(3):
        try:
            req=urllib.request.Request(url,data=b'' if post else None,
                headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.chinamoney.com.cn/chinese/bkccpr/'})
            with urllib.request.urlopen(req,timeout=45) as h: return json.load(h)
        except Exception:
            if attempt==2: raise
            time.sleep(2+attempt)

def number(v):
    try: return float(v)
    except (TypeError,ValueError): return None

def main():
    boardfile=OUT/'universe_board.json'
    if not boardfile.exists():boardfile=ROOT.parent/'evidence/universe_board.json'
    board=json.loads(boardfile.read_text())
    # 当前站点范围；实际入样还必须能用港股成分完整重建。
    candidates=[r for r in board['rows'] if '港股通QDII' in r.get('branch_names',[]) or
                any(k in r['name'] for k in ['港股','香港','恒生','H股','HK'])]
    candidates=[r for r in candidates if r['symbol'] not in {'SH513800'}] # 东证，日本
    universe={r['symbol'][2:]+'.'+r['symbol'][:2]:r for r in candidates}
    (OUT/'universe.json').write_text(json.dumps(universe,ensure_ascii=False,indent=2))
    labelsdir=OUT/'share_history';labelsdir.mkdir(exist_ok=True)
    def fetch_share(item):
        sym,row=item; p=labelsdir/(sym+'.json')
        if not p.exists():
            d=get_json('https://1navs.com/api/v1/funds/'+row['symbol']+'/share-history?days=3660')
            p.write_text(json.dumps(d,ensure_ascii=False))
        d=json.loads(p.read_text());return sym,len(d.get('rows',[]))
    with ThreadPoolExecutor(max_workers=3) as pool:
        shares=list(pool.map(fetch_share,universe.items()))
    print('share_history',len(shares),'rows',sum(x[1] for x in shares),flush=True)
    # 中国货币网按月分页，保留全部原始响应。
    raw=OUT/'central_parity_raw';raw.mkdir(exist_ok=True); mid={}
    for year,month in MID_MONTHS:
        page=1; total=1
        while page<=total:
            p=raw/f'{year}-{month:02d}-{page}.json'
            if not p.exists():
                q=urllib.parse.urlencode(dict(startDate=f'{year}-{month:02d}-01',
                    endDate=f'{year}-{month:02d}-{calendar.monthrange(year,month)[1]}',
                    currency='HKD/CNY',pageNum=page,pageSize=10))
                d=get_json('https://www.chinamoney.com.cn/ags/ms/cm-u-bk-ccpr/CcprHisNew?'+q,True)
                p.write_text(json.dumps(d,ensure_ascii=False));time.sleep(.2)
            d=json.loads(p.read_text())
            assert d['head']['rep_code']=='200' and not d['data']['flagMessage']
            assert d['data']['currency']=='HKD/CNY'
            total=d['data']['pageTotal']
            for r in d['records']: mid[r['date']]=float(r['values'][0])
            page+=1
    (OUT/'midpoint.json').write_text(json.dumps(mid,indent=2));print('midpoint',len(mid),flush=True)
    fx=Path('/Users/ellis/newnavnav/港股通汇率研究')
    if not (OUT/'settlement_sz.csv').exists():
        shutil.copyfile(fx/'data/港股通结算汇兑比率_20200101起.csv',OUT/'settlement_sz.csv')
    if not (OUT/'settlement_sh.csv').exists():
        shutil.copyfile(fx/'沪港通结算汇兑比率/data/沪港通结算汇兑比率_20200101起.csv',OUT/'settlement_sh.csv')
    hk={r['代码'] for r in csv.DictReader((OUT/'hk_symbols.csv').open(encoding='utf-8-sig'))}
    pcfdir=STOCKS/'PCF导出CSV'; basketdir=OUT/'baskets';basketdir.mkdir(exist_ok=True)
    etfdir=OUT/'etf';etfdir.mkdir(exist_ok=True)
    counts=[];rejects=[];flags={}
    files=sorted(p for p in pcfdir.glob('*_主表.csv') if START<=p.name[:8]<=END)
    for i,p in enumerate(files):
        date=p.name[:8]; date_iso=date[:4]+'-'+date[4:6]+'-'+date[6:]
        details=p.with_name(p.name.replace('主表','明细'))
        if not details.exists(): rejects.append([date,'*','missing_detail']);continue
        mains={}
        with p.open(encoding='utf-8-sig') as h:
            for r in csv.DictReader(h):
                code=r['基金代码'];s=code+('.SH' if code.startswith('5') else '.SZ')
                if s in universe: mains[code]=(s,r)
        comps={code:[] for code in mains}
        with details.open(encoding='utf-8-sig') as h:
            for r in csv.DictReader(h):
                if r['基金代码'] in comps: comps[r['基金代码']].append(r)
        baskets={}
        for code,(s,r) in mains.items():
            unit=number(r['最小申购赎回单位份']);cash=number(r['预估现金差额元']); rows=comps[code]
            reasons=[];items=[];fixed=0
            if not unit or cash is None: reasons.append('missing_unit_or_estimated_cash')
            if not rows or len(rows)!=int(float(r['成分股数量只'])): reasons.append('component_count_mismatch')
            for c in rows:
                sym=c['成分股代码'];flag=c['现金替代标志'];flags[flag]=flags.get(flag,0)+1
                if sym=='159900' and c['成分股名称']=='申赎现金': continue
                qty=number(c['数量股']);amount=number(c['固定替代金额元'])
                if flag=='必须':
                    if amount is None or amount<0: reasons.append('invalid_mandatory_cash')
                    else: fixed+=amount
                    continue
                # 退补为有数量的代买代卖证券，估值用数量×现价；不得再加预收替代款。
                if flag not in ['允许','禁止','退补']: reasons.append('unknown_substitution');continue
                if not sym.isdigit() or len(str(int(sym)))>5: reasons.append('non_hk_code');continue
                sym=f'{int(sym):05d}.HK'
                if sym not in hk: reasons.append('not_in_hk_registry');continue
                if qty is None or qty<=0: reasons.append('invalid_quantity');continue
                items.append([sym,qty])
            if not items: reasons.append('no_priced_hk_components')
            if len(set(c[0] for c in items))!=len(items): reasons.append('duplicate_component')
            if reasons: rejects.append([date,s,','.join(sorted(set(reasons)))]);continue
            baskets[s]=dict(date=date_iso,symbol=s,name=r['基金名称'],unit=unit,
                cash=cash+fixed,estimated_cash=cash,fixed_cash=fixed,components=items,
                prev_nav=number(r['基金份额净值元']),prev_date=r['净值截止日期'],
                creation_allowed=r['是否允许申购']=='是',redemption_allowed=r['是否允许赎回']=='是',
                creation_limit=number(r['当日累计申购上限份']))
        with gzip.open(basketdir/(date+'.json.gz'),'wt',encoding='utf-8') as h:json.dump(baskets,h,ensure_ascii=False)
        etfsource=STOCKS/'基金_分钟数据/ETF_分钟数据/1分钟_按月归档'/(date[:4]+'-'+date[4:6])/(date+'_1min.zip')
        available=[]
        if etfsource.exists():
            with ZipFile(etfsource) as z,ZipFile(etfdir/(date+'.zip'),'w',ZIP_DEFLATED) as out:
                names=set(z.namelist())
                for s in baskets:
                    if s+'.csv' in names:out.writestr(s+'.csv',z.read(s+'.csv'));available.append(s)
        counts.append(dict(date=date_iso,main_funds=len(mains),valid_baskets=len(baskets),etf_files=len(available)))
        if i%30==0:print('prepared',i+1,len(files),date,len(baskets),flush=True)
    (OUT/'preparation_audit.json').write_text(json.dumps(dict(date_start=START,date_end=END,
        daily=counts,rejected=rejects,flags=flags,universe_size=len(universe)),ensure_ascii=False,indent=2))
    print('done',len(counts),'baskets',sum(r['valid_baskets'] for r in counts),flush=True)

if __name__=='__main__':main()
