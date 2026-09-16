"""Same fixed order buckets on the already-retained 520600 study dates; no threshold fitting."""
from pathlib import Path
import sys,json
import pandas as pd,numpy as np
R=Path(__file__).resolve().parent;C=R.parent;L=C.parents[1];sys.path[:0]=[str(L/'native/build'),str(L/'native/batch')];import etf_l2
from intraday import near
p=pd.read_parquet(L/'study/panel.parquet');p=p[p.symbol.eq('520600.SH')&p.cutoff.eq('14:45')];rows=[];excluded=[]
for _,r in p.iterrows():
 path=Path('/Volumes/EllisFiles/Stocksdata/A股逐笔/港股通ETF保留')/r.date.replace('-','')/'520600.SH'
 if not (path/'逐笔成交.csv').exists():excluded.append(dict(date=r.date,reason='retained_trade_missing'));continue
 n=etf_l2.process_files(str(path/'逐笔成交.csv'),str(path/'逐笔委托.csv'),str(path/'行情.csv'),520600,int(r.date.replace('-','')),int(r.unit),cutoff='14:45');assert n['audit']['order_features_valid'] and n['audit']['trade_features_valid'];o=pd.DataFrame(n['orders']);record=dict(date=r.date,unit=r.unit,actual_net_U=r.net_baskets,actual_flow_pct=r.net_flow_pct,settlement_mean_bp=r.settlement_mean_bp)
 for side,key in [(1,'buy'),(2,'sell')]:
  z=o[o.side.eq(side)];known=z.quantity_evidence.isin([1,2]);mask=known&near(z.original_quantity,r.unit)|~known&near(z.active_filled,r.unit);record[key+'_matched_U']=float((z.loc[mask,'active_filled']+z.loc[mask,'passive_filled']).sum()/r.unit)
 record['net_matched_U']=record['sell_matched_U']-record['buy_matched_U'];rows.append(record)
d=pd.DataFrame(rows);d.to_csv(R/'results/historical_same_fund_proxy.csv',index=False);meta=dict(rows=len(d),excluded=excluded,strictly_positive_proxy_count=int(d.net_matched_U.gt(0).sum()) if len(d) else 0,true_creation_among_positive_proxy=int((d.net_matched_U.gt(0)&d.actual_net_U.gt(0)).sum()) if len(d) else 0,note='Descriptive retrospective case-defined feature diagnostic on original study dates; not independent strategy validation.')
if len(d):meta.update(pearson_net_proxy_actual=float(d.net_matched_U.corr(d.actual_net_U)),MAE_proxy_U=float((d.net_matched_U-d.actual_net_U).abs().mean()),zero_MAE_U=float(d.actual_net_U.abs().mean()))
(R/'results/history_check.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2));print(d.to_string(index=False));print(json.dumps(meta,ensure_ascii=False))
