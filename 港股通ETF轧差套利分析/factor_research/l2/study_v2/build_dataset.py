"""Freeze 2026 source dates. Build both cutoff panels using native C++ evidence features."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sys,json,gzip,time,hashlib
import numpy as np,pandas as pd
R=Path(__file__).resolve().parent;L=R.parent;F=L.parent;sys.path[:0]=[str(L/'native/build'),str(L/'native/batch'),str(F),str(L)]
import etf_l2
from intraday import expected_minutes,feature_row
from build_panel import make_features
from features import execution_features
RAW=Path('/Volumes/EllisFiles/Stocksdata/A股逐笔/港股通ETF保留');PCF=Path('/Volumes/EllisFiles/Stocksdata/PCF导出CSV')
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def labels():
 dates={p.name[:4]+'-'+p.name[4:6]+'-'+p.name[6:8] for p in PCF.glob('*_主表.csv')};out={};sources={}
 for f in (F/'inputs/share_history').glob('*.json'):
  sources[f.name]=digest(f);raw=sorted(json.loads(f.read_text())['rows'],key=lambda x:x['share_date']);assert len(raw)==len({x['share_date'] for x in raw});rows=[x for x in raw if x['share_date'] in dates]
  for i,r in enumerate(rows):
   if not i:continue
   prev=rows[i-1];inter=[v for v in raw if prev['share_date']<v['share_date']<r['share_date']]
   if any(abs(v['shares_10k']-prev['shares_10k'])>.021 or abs(v.get('share_change_10k',0))>.021 for v in inter):continue
   if abs(r['shares_10k']-prev['shares_10k']-r.get('share_change_10k',0))>.021:continue
   out[(f.stem,r['share_date'])]=dict(net_shares=r['share_change_10k']*10000,prev_shares=prev['shares_10k']*10000,label_prev_date=prev['share_date'],lag_flow_pct=prev.get('share_change_pct',np.nan),lag5_flow_pct=sum(v.get('share_change_pct',0) for v in rows[max(0,i-5):i]))
 return out,sources

def main():
 (R/'daily').mkdir(parents=True,exist_ok=True);labmap,labelhash=labels();planpath=R/'plan.json'
 if not planpath.exists():
  entries=[];excluded=[]
  for p in sorted(RAW.iterdir()):
   if not p.is_dir() or not '20260101'<=p.name<='20260630':continue
   mf=L/'manifests_2026'/(p.name+'.json');sf=F/'results/series'/(p.name+'.parquet');bf=F/'inputs/baskets'/(p.name+'.json.gz')
   if not all(q.exists() for q in [mf,sf,bf]):excluded.append(dict(date=p.name,reason='missing_verified_manifest_or_valuation_or_pcf'));continue
   m=json.loads(mf.read_text())
   if not m.get('verified') or m.get('markets')!=['SH','SZ']:excluded.append(dict(date=p.name,reason='unverified_manifest'));continue
   date=p.name[:4]+'-'+p.name[4:6]+'-'+p.name[6:];split='train' if date<='2026-02-27' else 'validation' if date<='2026-03-13' else 'test';entries.append(dict(day=p.name,date=date,split=split,manifest_sha256=digest(mf),series_sha256=digest(sf),basket_sha256=digest(bf),pcf_main_sha256=digest(PCF/(p.name+'_主表.csv'))))
  plan=dict(feature_version='executed_order_evidence_v2',entries=entries,excluded_dates=excluded,label_hashes=labelhash,case_excluded='2026-09-02 entire date; used only as diagnostic after selection',fresh_test='2026-03-16 through 2026-04-08; Apr22-Apr27 were seen in v1 reporting',selection='Train-only fit; validation selects one fixed candidate and gate; test cannot select model/features/threshold',cutoffs=['14:30','14:45'],quantity_candidates=['old_features_asinh','enriched_asinh','enriched_hurdle'],classifier_parameters=dict(max_iter=200,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=60,l2_regularization=10),gate='validation precision>=.9 with >=30 cases >=5 dates >=5 funds, maximize coverage; report fixed90 as well',new_feature_definition='Known near-integer original OR unknown original near-integer active execution, mutually exclusive; actual continuous filled quantities only; sides separate; PCF limits numeric or unknown')
  planpath.write_text(json.dumps(plan,ensure_ascii=False,indent=2))
 plan=json.loads(planpath.read_text());assert labelhash==plan['label_hashes'],'labels changed after freeze'
 for entry in plan['entries']:
  day=entry['day'];date=entry['date'];out=R/'daily'/(day+'.parquet')
  if out.exists():print(day,'cached',flush=True);continue
  start=time.perf_counter();mf=L/'manifests_2026'/(day+'.json');sf=F/'results/series'/(day+'.parquet');bf=F/'inputs/baskets'/(day+'.json.gz');pf=PCF/(day+'_主表.csv')
  for path,key in [(mf,'manifest_sha256'),(sf,'series_sha256'),(bf,'basket_sha256'),(pf,'pcf_main_sha256')]:assert digest(path)==entry[key],str(path)+' changed'
  manifest=json.loads(mf.read_text());sizes={x['path']:x['bytes'] for x in manifest['files']};baskets=json.loads(gzip.open(bf,'rt').read());series=pd.read_parquet(sf);groups={s:z for s,z in series.groupby('symbol')};pcf=pd.read_csv(pf,encoding='utf-8-sig',dtype={'基金代码':str});assert not pcf['基金代码'].duplicated().any();pcf=pcf.set_index('基金代码');records=[];errors=[]
  def one(path):
   sym=path.name;key=(sym,date);rows=[]
   try:
    if sym not in baskets or sym not in groups or key not in labmap:return [],[dict(date=date,symbol=sym,reason='missing_basket_series_label')]
    b=baskets[sym];lab=labmap[key]
    if lab['label_prev_date']!=b['prev_date'] or lab['prev_shares']<=0:raise ValueError('previous share/pcf date mismatch')
    if abs(lab['net_shares']/b['unit']-round(lab['net_shares']/b['unit']))>.01:raise ValueError('fractional net U label')
    files=[path/n for n in ['逐笔成交.csv','逐笔委托.csv','行情.csv']]
    for f in files:
     if str(f) not in sizes or f.stat().st_size!=sizes[str(f)]:raise ValueError('unverified or changed raw file '+str(f))
    cap=pcf.loc[sym[:6]];limits=dict(creation=pd.to_numeric(cap['当日累计申购上限份'],errors='coerce'),redemption=pd.to_numeric(cap['当日累计赎回上限份'],errors='coerce'))
    for cut in plan['cutoffs']:
     n=etf_l2.process_files(*map(str,files),int(sym[:6]),int(day),int(b['unit']),cutoff=cut)
     if not n['audit']['order_features_valid'] or not n['audit']['trade_features_valid']:errorspec=dict(date=date,symbol=sym,cutoff=cut,reason='native_quality',issues=n['audit']['issues']);return rows,[errorspec]
     s=groups[sym].copy();s['minute_id']=s.minute.map(lambda v:int(v[:2])*60+int(v[3:]));s=s[s.minute_id.isin(expected_minutes(cut))].copy();s['actual_settlement_buy']=s.lag_settlement;base=make_features(s,b,lab,cut)
     if base is None:raise ValueError('insufficient premium context')
     ctx=s.rename(columns={'mid':'iopv_mid','lag_settlement':'iopv_estimate'}).dropna(subset=['etf','iopv_mid','iopv_estimate']);extra=feature_row(n,ctx,int(b['unit']),cut)
     if extra['premium_coverage']<.95:raise ValueError('premium coverage below95')
     new=execution_features(n,b['unit'],lab['prev_shares'],sym[-2:],limits)
     rows.append(dict(**{k:v for k,v in base.items() if k not in extra},**extra,**new,cutoff=cut,market=sym[-2:],prev_shares=lab['prev_shares'],net_shares=lab['net_shares'],l2_quality_valid=True,quote_reconciled=n['audit']['quote_reconciled'],split=entry['split']))
    return rows,[]
   except Exception as exc:return rows,[dict(date=date,symbol=sym,reason=str(exc))]
  paths=[p for p in sorted((RAW/day).iterdir()) if p.is_dir()]
  with ThreadPoolExecutor(max_workers=4) as pool:
   for rows,errs in pool.map(one,paths):records+=rows;errors+=errs
  frame=pd.DataFrame(records);frame.to_parquet(out,index=False);(R/'daily'/(day+'_audit.json')).write_text(json.dumps(errors,ensure_ascii=False,indent=2));print(day,len(records),'rows',len(errors),'excluded',round(time.perf_counter()-start,2),'seconds',flush=True)
 frames=[pd.read_parquet(R/'daily'/(e['day']+'.parquet')) for e in plan['entries']];panel=pd.concat(frames,ignore_index=True);assert not panel.duplicated(['date','symbol','cutoff']).any();assert not panel.date.eq('2026-09-02').any();panel.to_parquet(R/'panel.parquet',index=False)
 summary=dict(rows=len(panel),funddays=len(panel[['date','symbol']].drop_duplicates()),dates=panel.date.nunique(),funds=panel.symbol.nunique(),by_split=panel.groupby('split').agg(rows=('symbol','size'),dates=('date','nunique'),funds=('symbol','nunique')).to_dict('index'),quote_reconciled_fraction=float(panel.quote_reconciled.mean()),exclusions=sum(len(json.loads((R/'daily'/(e['day']+'_audit.json')).read_text())) for e in plan['entries']))
 (R/'dataset_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps(summary),flush=True)
if __name__=='__main__':main()
