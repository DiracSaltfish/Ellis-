"""Freeze verified L2 date snapshot; build strictly truncated 14:30/14:45 panels.
Final daily FX and today's share changes are never features. Cached share changes are labels.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sys,json,gzip,time,hashlib
import pandas as pd,numpy as np
R=Path(__file__).resolve().parent;L=R.parent;F=L.parent;N=L/'native'
sys.path[:0]=[str(N/'build'),str(N/'batch'),str(L),str(F)]
import etf_l2
from intraday import feature_row,expected_minutes
from labels_calendar import labels
from build_panel import make_features
RAW=Path('/Volumes/EllisFiles/Stocksdata/A股逐笔/港股通ETF保留')

def main():
 R.mkdir(exist_ok=True);(R/'daily').mkdir(exist_ok=True)
 snapshot=R/'date_snapshot.json'
 if not snapshot.exists():
  entries=[]
  for p in sorted((L/'manifests_2026').glob('*.json')):
   m=json.loads(p.read_text())
   if m.get('verified') and m.get('markets')==['SH','SZ'] and (F/'results/series'/(p.stem+'.parquet')).exists():entries.append(dict(date=p.stem,manifest_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),target_files=len(m['files'])))
  snapshot.write_text(json.dumps(entries,ensure_ascii=False,indent=2))
 dates=json.loads(snapshot.read_text());labs=labels();audit=[]
 for entry in dates:
  day=entry['date'];date=day[:4]+'-'+day[4:6]+'-'+day[6:];dest=R/'daily'/f'{day}.parquet'
  if dest.exists():print(day,'cached',flush=True);continue
  baskets=json.loads(gzip.open(F/'inputs/baskets'/f'{day}.json.gz','rt').read());series=pd.read_parquet(F/'results/series'/f'{day}.parquet');groups={s:d for s,d in series.groupby('symbol')}
  paths=sorted(p for p in (RAW/day).iterdir() if p.is_dir());records=[];start=time.perf_counter()
  def one(path):
   sym=path.name;key=(sym,date);out=[];errors=[]
   if sym not in baskets or sym not in groups or key not in labs:return [],[dict(date=date,symbol=sym,status='no_pcf_series_or_label')]
   basket=baskets[sym];lab=labs[key]
   if lab['label_prev_date']!=basket['prev_date']:return [],[dict(date=date,symbol=sym,status='previous_date_mismatch')]
   if lab['prev_shares']<=0:return [],[dict(date=date,symbol=sym,status='zero_previous_shares')]
   nb=lab['net_shares']/basket['unit']
   if abs(nb-round(nb))>.01:return [],[dict(date=date,symbol=sym,status='non_integer_net_baskets',net_baskets=nb)]
   for cutoff in ['14:30','14:45']:
    try:
     native=etf_l2.process_files(str(path/'逐笔成交.csv'),str(path/'逐笔委托.csv'),str(path/'行情.csv'),int(sym[:6]),int(day),int(basket['unit']),cutoff=cutoff)
     a=native['audit']
     if not a['order_features_valid'] or not a['trade_features_valid']:
      errors.append(dict(date=date,symbol=sym,cutoff=cutoff,status='l2_quality_failed',issues=a['issues']));continue
     s=groups[sym].copy();s=s[s.minute.map(lambda x:int(x[:2])*60+int(x[3:])).isin(expected_minutes(cutoff))].copy();s['actual_settlement_buy']=s['lag_settlement']
     base=make_features(s,basket,lab,cutoff)
     if base is None:raise ValueError('insufficient minute data')
     ctx=s.rename(columns={'mid':'iopv_mid','lag_settlement':'iopv_estimate'})
     ctx['minute_id']=ctx.minute.map(lambda x:int(x[:2])*60+int(x[3:]));ctx=ctx[ctx.minute_id.isin(expected_minutes(cutoff))].dropna(subset=['etf','iopv_mid','iopv_estimate'])
     if (ctx[['etf','iopv_mid','iopv_estimate']]<=0).any().any():raise ValueError('nonpositive prices')
     extra=feature_row(native,ctx,int(basket['unit']),cutoff)
     if extra['premium_coverage']<.95:raise ValueError('context coverage below95%')
     out.append(dict(**{k:v for k,v in base.items() if k not in extra},**extra,cutoff=cutoff,market=sym[-2:],prev_shares=lab['prev_shares'],net_shares=lab['net_shares'],l2_quality_valid=True,unknown_original_orders=a['unknown_original_orders'],native_parse_seconds=native['timing']['parse_seconds'],native_core_seconds=sum(native['timing'][k] for k in ['index_seconds','replay_seconds','aggregation_seconds'])))
    except Exception as exc:errors.append(dict(date=date,symbol=sym,cutoff=cutoff,status='error',error=str(exc)))
   return out,errors
  with ThreadPoolExecutor(max_workers=4) as pool:
   for good,bad in pool.map(one,paths):records+=good;audit+=bad
  pd.DataFrame(records).to_parquet(dest,index=False)
  (R/'daily'/f'{day}_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2,default=str));audit=[]
  print(day,'targets',len(paths),'rows',len(records),'seconds',round(time.perf_counter()-start,1),flush=True)
 frames=[pd.read_parquet(R/'daily'/f'{x["date"]}.parquet') for x in dates];panel=pd.concat(frames,ignore_index=True)
 assert not panel.duplicated(['date','symbol','cutoff']).any()
 panel.to_parquet(R/'panel.parquet',index=False);panel.to_csv(R/'panel.csv',index=False)
 errors=[x for e in dates for x in json.loads((R/'daily'/f'{e["date"]}_audit.json').read_text())];(R/'exclusions.json').write_text(json.dumps(errors,ensure_ascii=False,indent=2,default=str))
 meta=dict(rows=len(panel),fund_days=panel[['date','symbol']].drop_duplicates().shape[0],dates=sorted(panel.date.unique()),funds=panel.symbol.nunique(),native_version=etf_l2.__version__,label_source='1navs cached share-history; same-day net change',fx_feature_basis='previous trading day settlement proxy; publication availability assumed, final same-day FX excluded',cutoff='exclusive L2 boundary; completed minute context only',features_availability='exchange timestamps; historical receive timestamps unavailable',source_snapshot=str(snapshot))
 (R/'dataset_summary.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2));print(json.dumps(meta,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
