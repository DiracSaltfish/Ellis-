"""Copy only public audit evidence. No live credentials or original-repo edits."""
import csv, hashlib, json, shutil
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path('/Users/ellis/工具程序开发/513090误判分析/watchlist_20260908')
def read(name):
    with (SOURCE/'outputs'/name).open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))
rows = read('watchlist_validation.csv')
selected = [r for r in rows if r['single_day_status']=='PASS_SINGLE_DAY' and r['multi_day_status'] in ('SH_SINGLE_DAY_ONLY','MULTI_DAY_5D_PASS') and r['asset_type']=='HK_equities']
dest=ROOT/'testdata'; (dest/'pcf').mkdir(parents=True,exist_ok=True)
manifest=[]
for r in selected:
    src=SOURCE/r['pcf_path']; target=dest/'pcf'/f"{r['code']}.xml"
    shutil.copyfile(src,target)
    manifest.append(dict(symbol=r['symbol'],name=r['fund_name'],date=r['pcf_date'],expected=float(r['estimated_nav']),published=float(r['published_nav']),qc=r['multi_day_status'],source=r['pcf_source'],sha256=hashlib.sha256(target.read_bytes()).hexdigest(),stale_components=json.loads(r['stale_components'])))
codes={r['code'] for r in selected}
prices={}
for r in read('component_contributions_20260908.csv'):
    if r['fund_code'] in codes and r['valuation_method']=='unadjusted_close_times_fixed_fx':
        key=r['component_code']+'.HK'; value=dict(price=float(r['price_hkd']),date=r['price_date'])
        assert key not in prices or prices[key]==value
        prices[key]=value
def save(path,obj): path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
save(dest/'manifest.json',manifest);save(dest/'close_prices.json',prices)
save(ROOT/'universe.json',dict(policy='SH single-day pass; SZ five-day pass; HK equities only',evidence_date='2026-09-08',candidates=manifest,deferred=[dict(symbol=r['symbol'],single=r['single_day_status'],multi=r['multi_day_status']) for r in rows if r['code'] not in codes]))
save(dest/'provenance.json',dict(source=str(SOURCE),thread='01a081f5-9ef1-7a01-9947-6b0ee5ed5622',watchlist_sha256=hashlib.sha256((SOURCE/'outputs/watchlist_validation.csv').read_bytes()).hexdigest(),historical_fx=0.86482,note='Historical replay only. Old close dates remain explicit; numerical match is not live readiness.'))
shutil.copyfile('/tmp/iopv-fx-response.json',dest/'fx_20260909.json')
print(json.dumps(dict(candidates=len(manifest),hk_subscriptions=len(prices),deferred=len(rows)-len(manifest))))
