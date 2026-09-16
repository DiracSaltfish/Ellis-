"""Premium screening before native L2; frozen chronological, supplied-data study.

Run on machome. No QMT network calls, trades, archive deletion, or live promotion.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse, gzip, hashlib, json, sys, time
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score

R=Path(__file__).resolve().parent; L=R.parent; F=L.parent; V=L/'study_v2'
sys.path[:0]=[str(V),str(L/'study'),str(L/'native/batch'),str(L/'native/build'),str(F)]
from analyze import BASE, L2, wilson
from build_dataset import labels, digest, RAW, PCF
from build_panel import make_features
from intraday import expected_minutes, feature_row
from features import execution_features, V2_FEATURES
import etf_l2

KEY=['date','symbol','cutoff','fx_basis']
STATIC=['v2_log_previous_U','v2_is_sh','v2_creation_limit_pct','v2_creation_limit_known','v2_redemption_limit_pct','v2_redemption_limit_known']
# Compression features depend on the chosen FX series: never copy lag-FX versions into final-FX.
# Exclude both from the comparison so historical pure-order summaries can be reused exactly.
ORDER=[x for x in L2 if 'compression' not in x]+[x for x in V2_FEATURES if x not in STATIC]
PREMIUM=BASE+STATIC
NEW_DAYS=['20260409','20260410','20260413','20260414','20260415','20260416','20260417','20260420','20260421','20260428','20260429','20260430']

def js(x):
 if isinstance(x,np.generic):return x.item()
 raise TypeError(type(x).__name__)
def save(name,obj):
 (R/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=js))
def gate(d):
 return (d.settlement_mean_bp.ge(10)&d.settlement_positive_fraction.ge(.70)&d.settlement_premium_bp.gt(0)&d.fx_gap_bp.gt(0)&d.creation_allowed.eq(1)&d.redemption_allowed.eq(1)&d.premium_coverage.ge(.95))
def stats(d,mask):
 z=d.loc[mask];n=len(z);k=int(z.net_shares.gt(0).sum());redeem=int(z.net_shares.lt(0).sum())
 return dict(n=n,create=k,redeem=redeem,flat=n-k-redeem,precision=k/n if n else None,error_rate=(n-k)/n if n else None,redemption_rate=redeem/n if n else None,coverage=n/len(d) if len(d) else None,dates=z.date.nunique(),funds=z.symbol.nunique(),wilson95=[float(x) if x is not None else None for x in wilson(k,n)],median_net_U=float(z.net_baskets.median()) if n else None)

def freeze():
 R.mkdir(parents=True,exist_ok=True)
 if (R/'plan.json').exists():return
 old=json.loads((V/'plan.json').read_text());days=[e['day'] for e in old['entries']]+NEW_DAYS
 entries=[];excluded=[]
 for day in sorted(set(days)):
  paths=dict(manifest=L/'manifests_2026'/(day+'.json'),series=F/'results/series'/(day+'.parquet'),basket=F/'inputs/baskets'/(day+'.json.gz'),pcf=PCF/(day+'_主表.csv'))
  if not all(p.exists() for p in paths.values()):excluded.append(dict(day=day,reason='missing_sources',paths=[str(p) for p in paths.values() if not p.exists()]));continue
  m=json.loads(paths['manifest'].read_text())
  if not m.get('verified') or m.get('markets')!=['SH','SZ']:excluded.append(dict(day=day,reason='unverified_manifest'));continue
  date=f'{day[:4]}-{day[4:6]}-{day[6:]}'
  split='train' if date<='2026-02-27' else 'validation' if date<='2026-03-13' else 'new_test' if day in NEW_DAYS else 'seen_stress'
  entries.append(dict(day=day,date=date,split=split,sources={k:dict(path=str(p),sha256=digest(p)) for k,p in paths.items()}))
 _,lh=labels()
 save('plan.json',dict(entries=entries,excluded=excluded,label_hashes=lh,old_panel_sha256=digest(V/'panel.parquet'),cutoffs=['14:30','14:45'],fx_bases=['lag','final'],gate='mean settlement premium >=10bp, fraction >0 >=70%, current premium>0, mid/settlement FX gap>0, both creation/redemption allowed; coverage>=95%',training='Jan5-Feb27; threshold selection Mar2-Mar13; new Apr9-Apr21 and Apr28; other test dates already examined and retrospective only',selection='Each binary model: validation score grid .50:.01:.99; require precision>=90%, n>=30, dates>=5, funds>=5; maximize count then lower threshold. No qualified rule -> null. Fixed .80 and .90 are diagnostic alternatives, never promoted via test.',models=['premium','premium_l2'],parameters=dict(max_iter=150,learning_rate=.05,max_leaf_nodes=7,min_samples_leaf=40,l2_regularization=10,early_stopping=False,random_state=20260912),calibration='Scores are raw binary classifier scores, not calibrated probabilities. Validate realized error separately.',incremental='Train both only on pre-screen positives. Compare same candidates and equal-count per-date ranks retrospectively; no threshold fitting on test.',fx='lag uses previous available settlement daily; final uses realized same-day settlement daily, ex-post only. No original receipt-time audit.',metrics='Net creation >0 versus flat=0 or net redemption<0; NOT profitability. Resample dates, not individual funds.',premium_features=PREMIUM,order_features=ORDER,excluded_order_features='FX-dependent compression fractions excluded from both models; no cross-basis reuse',source_code_sha256=digest(Path(__file__))))
 print('FROZEN',len(entries),'dates',sum(e['split']=='new_test' for e in entries),'new',flush=True)

def premium():
 plan=json.loads((R/'plan.json').read_text());labs,lh=labels();assert lh==plan['label_hashes']
 rec=[];errors=[]
 for e in plan['entries']:
  for x in e['sources'].values():assert digest(Path(x['path']))==x['sha256']
  day=e['day'];date=e['date'];baskets=json.loads(gzip.open(e['sources']['basket']['path'],'rt').read());series=pd.read_parquet(e['sources']['series']['path']);pcf=pd.read_csv(e['sources']['pcf']['path'],encoding='utf-8-sig',dtype={'基金代码':str}).set_index('基金代码');assert pcf.index.is_unique
  m=json.loads(Path(e['sources']['manifest']['path']).read_text());symbols={Path(f['path']).parent.name for f in m['files']}
  for sym,s in series.groupby('symbol'):
   try:
    if sym not in symbols or sym not in baskets or (sym,date) not in labs:raise ValueError('missing raw basket or label')
    b=baskets[sym];lab=labs[(sym,date)];u=b['unit'];prev=lab['prev_shares']
    if lab['label_prev_date']!=b['prev_date'] or prev<=0 or u<=0:raise ValueError('invalid previous shares/PCF date/unit')
    if abs(lab['net_shares']/u-round(lab['net_shares']/u))>.01:raise ValueError('fractional U label')
    s=s.copy();s['minute_id']=s.minute.map(lambda v:int(v[:2])*60+int(v[3:]));assert not s.minute_id.duplicated().any()
    static=dict(v2_log_previous_U=np.log1p(prev/u),v2_is_sh=int(sym.endswith('.SH')))
    for key,col in [('creation','当日累计申购上限份'),('redemption','当日累计赎回上限份')]:
     value=pd.to_numeric(pcf.loc[sym[:6],col],errors='coerce');valid=np.isfinite(value) and 0<=value<1e14;static['v2_'+key+'_limit_pct']=value/prev*100 if valid else np.nan;static['v2_'+key+'_limit_known']=int(valid)
    for cut in plan['cutoffs']:
     for fx in plan['fx_bases']:
      z=s[s.minute_id.isin(expected_minutes(cut))].copy()
      if fx=='lag':z['actual_settlement_buy']=z.lag_settlement
      a=make_features(z,b,lab,cut)
      if a is None:continue
      usable=z.dropna(subset=['etf','mid','actual_settlement_buy']);coverage=len(usable)/len(expected_minutes(cut))
      if coverage<.95:continue
      pr=(usable.etf/usable.actual_settlement_buy-1)*10000
      rec.append(dict(**a,**static,settlement_p10_bp=float(pr.quantile(.1)),settlement_std_bp=float(pr.std(ddof=0)),premium_coverage=coverage,cutoff=cut,fx_basis=fx,prev_shares=prev,net_shares=lab['net_shares'],market=sym[-2:],split=e['split']))
   except Exception as exc:errors.append(dict(date=date,symbol=sym,reason=str(exc)))
  print('premium',day,flush=True)
 d=pd.DataFrame(rec);assert not d.duplicated(KEY).any();d['premium_gate']=gate(d);d.to_parquet(R/'premium.parquet',index=False);save('premium_exclusions.json',errors)
 # Don't print new-test outcomes before fitting and freezing artifacts.
 print('premium ready',len(d),'rows; new labels not summarized',flush=True)

def historical():
 plan=json.loads((R/'plan.json').read_text());assert digest(V/'panel.parquet')==plan['old_panel_sha256']
 p=pd.read_parquet(R/'premium.parquet');p=p[p.split.ne('new_test')&p.premium_gate].copy();old=pd.read_parquet(V/'panel.parquet');keep=['date','symbol','cutoff',*ORDER,'v2_sell_unit_U','v2_buy_unit_U','l2_quality_valid','quote_reconciled']
 d=p.merge(old[keep],on=['date','symbol','cutoff'],how='left',validate='many_to_one');d['l2_available']=d.l2_quality_valid.fillna(False).astype(bool);d.to_parquet(R/'historical_candidates.parquet',index=False)

def train():
 plan=json.loads((R/'plan.json').read_text());p=pd.read_parquet(R/'historical_candidates.parquet');p=p[p.l2_available];thresholds=[];selections={};vp=[];(R/'models').mkdir(exist_ok=True)
 for (fx,cut),d in p.groupby(['fx_basis','cutoff']):
  tr=d[d.split.eq('train')];va=d[d.split.eq('validation')];assert tr.date.max()<va.date.min() and va.date.max()<'2026-04-09'
  for variant,names in [('premium',PREMIUM),('premium_l2',PREMIUM+ORDER)]:
   clf=HistGradientBoostingClassifier(**plan['parameters']).fit(tr[names],tr.net_shares.gt(0).astype(int));assert clf.classes_.tolist()==[0,1]
   v=va.copy();v['score']=clf.predict_proba(v[names])[:,1];key=f'{fx}_{cut.replace(":","")}_{variant}';choices=[]
   for th in np.arange(.50,1,.01):
    st=stats(v,v.score.ge(th));thresholds.append(dict(key=key,threshold=float(th),**st))
    if st['n']>=30 and st['dates']>=5 and st['funds']>=5 and st['precision']>=.90:choices.append((st['n'],-float(th)))
   th=-max(choices)[1] if choices else None
   model=dict(classifier=clf,features=names,cutoff=cut,fx_basis=fx,variant=variant,threshold=th,fit_through=tr.date.max(),trained_through=va.date.max(),mode='research_only',score_is_calibrated=False,gate_definition=plan['gate'])
   joblib.dump(model,R/'models'/(key+'.joblib'));v['variant']=variant;vp.append(v)
   selections[key]=dict(threshold=th,train_n=len(tr),validation_gate=stats(v,np.ones(len(v),bool)),validation_selected=stats(v,v.score.ge(th)) if th is not None else None,model_sha256=digest(R/'models'/(key+'.joblib')))
   print('frozen model',key,'train',len(tr),'val',len(va),'threshold',th,flush=True)
 save('selection.json',selections);pd.DataFrame(thresholds).to_csv(R/'validation_thresholds.csv',index=False);pd.concat(vp).to_parquet(R/'validation_predictions.parquet',index=False)

def new_l2():
 assert (R/'selection.json').exists(),'Models/thresholds must be frozen before new-test L2'
 plan=json.loads((R/'plan.json').read_text());p=pd.read_parquet(R/'premium.parquet');p=p[p.split.eq('new_test')];out=[];audit=[];counts=[]
 for e in plan['entries']:
  if e['split']!='new_test':continue
  day=e['day'];d=p[p.date.eq(e['date'])];c=d[d.premium_gate];m=json.loads(Path(e['sources']['manifest']['path']).read_text());sizes={x['path']:x['bytes'] for x in m['files']};tasks=list(c.groupby(['symbol','cutoff']));start=time.perf_counter()
  def one(task):
   (sym,cut),rows=task;one=rows.iloc[0];files=[RAW/day/sym/n for n in ['逐笔成交.csv','逐笔委托.csv','行情.csv']]
   try:
    for f in files:assert str(f) in sizes and f.stat().st_size==sizes[str(f)],'raw changed'
    native=etf_l2.process_files(*map(str,files),int(sym[:6]),int(day),int(one.unit),cutoff=cut)
    assert native['audit']['order_features_valid'] and native['audit']['trade_features_valid'],str(native['audit']['issues'])
    # These order-only features are basis-independent. Premium context affects only two unused compression fields.
    series=pd.read_parquet(e['sources']['series']['path'],filters=[('symbol','==',sym)])
    series['minute_id']=series.minute.map(lambda v:int(v[:2])*60+int(v[3:]));ctx=series[series.minute_id.isin(expected_minutes(cut))].rename(columns={'mid':'iopv_mid','lag_settlement':'iopv_estimate'}).dropna(subset=['etf','iopv_mid','iopv_estimate'])
    extra=feature_row(native,ctx,int(one.unit),cut);new=execution_features(native,one.unit,one.prev_shares,sym[-2:])
    features={**extra,**new};return [dict(**r,**{k:features[k] for k in ORDER},v2_sell_unit_U=new['v2_sell_unit_U'],v2_buy_unit_U=new['v2_buy_unit_U'],l2_available=True,quote_reconciled=native['audit']['quote_reconciled']) for r in rows.to_dict('records')],[]
   except Exception as exc:return [dict(**r,l2_available=False) for r in rows.to_dict('records')],[dict(day=day,symbol=sym,cutoff=cut,error=str(exc))]
  with ThreadPoolExecutor(max_workers=4) as pool:
   for records,errs in pool.map(one,tasks):out+=records;audit+=errs
  counts.append(dict(date=e['date'],possible_symbol_cutoffs=len(d[['symbol','cutoff']].drop_duplicates()),native_calls=len(tasks),seconds=time.perf_counter()-start,gate_rows_by_fx_cut=c.groupby(['fx_basis','cutoff']).size().to_dict().__str__()))
  print('new L2',day,len(tasks),'native calls',round(time.perf_counter()-start,2),'sec',flush=True)
 d=pd.DataFrame(out);assert not d.duplicated(KEY).any();d.to_parquet(R/'new_candidates.parquet',index=False);save('new_l2_exclusions.json',audit);save('compute_counts.json',counts)

def evaluate():
 plan=json.loads((R/'plan.json').read_text());sel=json.loads((R/'selection.json').read_text());p=pd.read_parquet(R/'premium.parquet');c=pd.concat([pd.read_parquet(R/'historical_candidates.parquet'),pd.read_parquet(R/'new_candidates.parquet')],ignore_index=True);assert not c.duplicated(KEY).any();records=[];summaries=[];paired=[];preds=[];date_rows=[];curves=[];bootstrap=[]
 for (fx,cut,split),universe in p[p.split.isin(['new_test','seen_stress'])].groupby(['fx_basis','cutoff','split']):
  z=c[(c.fx_basis==fx)&(c.cutoff==cut)&(c.split==split)].copy();valid=z[z.l2_available].copy();ids=['date','symbol'];den=len(universe)
  context=dict(fx_basis=fx,cutoff=cut,split=split,total_eligible=den)
  def append(stage,data,mask):
   st=stats(data,mask);st['coverage']=st['n']/den;summaries.append(dict(**context,stage=stage,**st))
  append('all',universe,np.ones(len(universe),bool));append('premium_gate',universe,universe.premium_gate);append('gate_l2_valid',valid,np.ones(len(valid),bool))
  scored={}
  for variant in plan['models']:
   key=f'{fx}_{cut.replace(":","")}_{variant}';path=R/'models'/(key+'.joblib');assert digest(path)==sel[key]['model_sha256'];model=joblib.load(path);assert model['trained_through']<universe.date.min();v=valid.copy();v['score']=model['classifier'].predict_proba(v[model['features']])[:,1];v['variant']=variant;v['selected']=False if model['threshold'] is None else v.score.ge(model['threshold']);v['selected_threshold']=model['threshold'];scored[variant]=v
   for stage,mask in [('selected',v.selected),('score80',v.score.ge(.8)),('score90',v.score.ge(.9))]:
    append(variant+'_'+stage,v,mask)
    for day,g in v.groupby('date'):date_rows.append(dict(**context,stage=variant+'_'+stage,date=day,**stats(g,mask.loc[g.index])))
   for th in [.5,.6,.7,.8,.85,.9,.95]:curves.append(dict(**context,variant=variant,threshold=th,**stats(v,v.score.ge(th))))
   preds.append(v)
  a=scored['premium'];b=scored['premium_l2'];assert list(zip(a.date,a.symbol))==list(zip(b.date,b.symbol))
  # Same number per date: diagnostic ranking contrast, not a tradable future-count rule.
  match=np.zeros(len(a),bool);a=a.reset_index(drop=True);b=b.reset_index(drop=True)
  for day,idx in a.groupby('date').groups.items():
   n=int(b.loc[idx,'selected'].sum());picked=a.loc[idx].sort_values(['score','symbol'],ascending=[False,True]).head(n).index;match[picked]=True
  append('premium_equal_daily_count',a,match)
  before=stats(b,np.ones(len(b),bool));after=stats(b,b.selected)
  paired.append(dict(**context,before=before,after=after,false_positives_removed=before['n']-before['create']-(after['n']-after['create']),true_positives_retained=after['create'],true_positives_rejected=before['create']-after['create'],equal_count_premium=stats(a,match)))
  days=sorted(universe.date.unique());rng=np.random.default_rng(20260912);draws=[]
  # Aggregated date counts include days with no candidates; no pseudo-independent fund bootstrap.
  by=[]
  for day in days:
   aa=a[a.date.eq(day)];bb=b[b.date.eq(day)];am=match[a.date.eq(day).to_numpy()];bm=bb.selected
   by.append([len(bb),int(bb.net_shares.gt(0).sum()),int(bm.sum()),int((bb.net_shares.gt(0)&bm).sum()),int(am.sum()),int(aa.loc[am].net_shares.gt(0).sum())])
  ar=np.array(by)
  for _ in range(2000):
   n,k,n2,k2,n3,k3=ar[rng.integers(0,len(ar),len(ar))].sum(axis=0)
   if n2 and n3 and n:draws.append([k2/n2,k2/n2-k/n,k2/n2-k3/n3])
  degenerate=after['n']>0 and after['create'] in [0,after['n']]
  bootstrap.append(dict(**context,date_clusters=len(days),signal_dates=after['dates'],draws=len(draws),zero_error_or_zero_success_degeneracy=degenerate,l2_precision_ci95=np.quantile(np.array(draws)[:,0],[.025,.975]).tolist() if draws and not degenerate else None,improvement_vs_gate_ci95=np.quantile(np.array(draws)[:,1],[.025,.975]).tolist() if draws else None,improvement_vs_equal_count_premium_ci95=np.quantile(np.array(draws)[:,2],[.025,.975]).tolist() if draws else None,note='Date-cluster percentile bootstrap is descriptive with few dates. All-success samples produce degenerate precision intervals, suppressed here; use Wilson only as an independence-based reference, never proof of zero risk. Equal per-date observed counts can also degenerate the paired interval.'))
 pd.DataFrame(summaries).to_csv(R/'summary.csv',index=False);pd.DataFrame(date_rows).to_csv(R/'by_date.csv',index=False);pd.DataFrame(curves).to_csv(R/'threshold_curves.csv',index=False);pred=pd.concat(preds,ignore_index=True);pred.to_parquet(R/'predictions.parquet',index=False);pred.to_csv(R/'predictions.csv',index=False);pred[pred.variant.eq('premium_l2')&pred.selected&pred.net_shares.le(0)].to_csv(R/'false_positives.csv',index=False);save('paired.json',paired);save('bootstrap.json',bootstrap)
 print(pd.DataFrame(summaries).query("split=='new_test'")[['fx_basis','cutoff','stage','n','create','redeem','flat','precision']].to_string(index=False),flush=True)

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['freeze','premium','historical','train','new_l2','evaluate']);args=parser.parse_args();globals()[args.stage]()
