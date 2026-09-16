"""Snapshot newly available dates; score existing frozen models without refitting."""
from pathlib import Path
from datetime import datetime
import argparse,importlib.util,json,sys
import numpy as np,pandas as pd,joblib
R=Path(__file__).resolve().parent;L=R.parent;F=L.parent;V=L/'premium_l2_gate_v3';T=L/'top_one_v3';D=R/'data'
sys.path.insert(0,str(V));import pipeline as p
spec=importlib.util.spec_from_file_location('top_one_contract',T/'analyze.py');top=importlib.util.module_from_spec(spec);spec.loader.exec_module(top)
def save(name,obj):(R/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=top.serial))
def freeze():
 R.mkdir(exist_ok=True);D.mkdir(exist_ok=True)
 if (R/'plan.json').exists():print('Using existing snapshot');return
 old=json.loads((V/'plan.json').read_text());seen={e['day'] for e in old['entries']};inventory=[];entries=[]
 for folder in sorted(p.RAW.glob('2026*')):
  day=folder.name;paths=dict(manifest=L/'manifests_2026'/(day+'.json'),series=F/'results/series'/(day+'.parquet'),basket=F/'inputs/baskets'/(day+'.json.gz'),pcf=p.PCF/(day+'_主表.csv'))
  missing=[k for k,f in paths.items() if not f.exists()];mf=json.loads(paths['manifest'].read_text()) if paths['manifest'].exists() else {};ready=not missing and mf.get('verified') and mf.get('markets')==['SH','SZ'] and day<='20260630'
  inventory.append(dict(day=day,ready=bool(ready),missing=missing,verified=mf.get('verified'),previously_used=day in seen,reason='September design case excluded' if day>'20260630' else 'HK market holiday' if day in ['20260403','20260407','20260525'] else 'source missing' if missing else 'available'))
  if ready and day not in seen:
   entries.append(dict(day=day,date=f'{day[:4]}-{day[4:6]}-{day[6:]}',split='new_test',sources={k:dict(path=str(f),sha256=p.digest(f)) for k,f in paths.items()}))
 _,labelhash=p.labels();selection=json.loads((V/'selection.json').read_text());qm=json.loads((T/'quantity_models.json').read_text());models={}
 for key,s in selection.items():
  path=V/'models'/(key+'.joblib');assert p.digest(path)==s['model_sha256'];models[str(path)]=p.digest(path)
 for s in qm:
  path=T/'models'/(s['key']+'_quantity.joblib');assert p.digest(path)==s['sha256'];models[str(path)]=p.digest(path)
 plan=dict(snapshot_at=datetime.now().astimezone().isoformat(),inventory=inventory,entries=entries,previous_dates=len(seen),available_dates=sum(i['ready'] for i in inventory),raw_2026_dates=len(inventory),new_dates=len(entries),model_hashes=models,label_hashes=labelhash,policy='Freeze original probability and quantity models, thresholds and premium-first gate. No fitting on new dates. Both original and predeclared 513130 isolation; all cutoff/FX results retained.',old_plan_sha256=p.digest(V/'plan.json'),old_choices_sha256=p.digest(T/'daily_choices.csv'),scope='2026 only. Model fits through Feb27, selection through Mar13. New dates disjoint from original 74-day v3 plan.',holiday_source='https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2025/ce_SEHK_CT_075_2025.pdf')
 save('plan.json',plan)
 data_plan={**old,'entries':entries,'label_hashes':labelhash,'training':'No fit in this expansion: data generation only'};(D/'plan.json').write_text(json.dumps(data_plan,ensure_ascii=False,indent=2));(D/'selection.json').write_text(json.dumps(selection,ensure_ascii=False,indent=2))
 print('SNAPSHOT',plan['raw_2026_dates'],'raw dates',plan['available_dates'],'aligned dates',len(entries),'new', [e['day'] for e in entries],flush=True)
def compute():
 p.R=D;p.premium();p.new_l2()
def score():
 plan=json.loads((R/'plan.json').read_text());selection=json.loads((V/'selection.json').read_text())
 for name,digest in plan['model_hashes'].items():assert p.digest(Path(name))==digest
 c=pd.read_parquet(D/'new_candidates.parquet');c=c[c.l2_available].copy();out=[];stage=[];premium=pd.read_parquet(D/'premium.parquet')
 for fx in ['final','lag']:
  for cut in ['14:30','14:45']:
   z=c[c.fx_basis.eq(fx)&c.cutoff.eq(cut)].copy();key=f'{fx}_{cut.replace(":","")}_premium_l2';model=joblib.load(V/'models'/(key+'.joblib'));q=joblib.load(T/'models'/(key+'_quantity.joblib'));assert model['trained_through']<min(e['date'] for e in plan['entries']);assert q['fit_through']<'2026-03-01'
   if len(z):
    regular=[x for x in model['features'] if x not in ['v2_creation_limit_pct','v2_redemption_limit_pct']];assert np.isfinite(z[regular].to_numpy(float)).all()
    z['score']=model['classifier'].predict_proba(z[model['features']])[:,1];z['pred_net_flow_pct']=np.maximum(-100,np.sinh(q['regressor'].predict(z[q['features']])));z['pred_net_shares']=z.pred_net_flow_pct*z.prev_shares/100;z['prev_nav']=np.exp(z.log_prev_assets)/z.prev_shares;z['pred_net_U']=z.pred_net_shares/z.unit;z['pred_net_amount_cny']=z.pred_net_shares*z.prev_nav;z['basket_capital_proxy_cny']=z.prev_nav*z.unit;z['frozen_threshold']=model['threshold'];z['scope']='fresh_expansion';out.append(z)
   for name,frame,mask in [('premium_gate',premium[premium.fx_basis.eq(fx)&premium.cutoff.eq(cut)],None),('l2_valid',z,None),('l2_selected',z,z.score.ge(model['threshold']) if len(z) else np.zeros(0,bool))]:
    if name=='premium_gate':mask=frame.premium_gate
    if mask is None:mask=np.ones(len(frame),bool)
    stage.append(dict(fx_basis=fx,cutoff=cut,stage=name,**p.stats(frame,mask)))
 scored=pd.concat(out,ignore_index=True);scored.to_parquet(R/'scored.parquet',index=False);pd.DataFrame(stage).to_csv(R/'stages.csv',index=False)
 daily=[]
 for fx in ['final','lag']:
  for cut in ['14:30','14:45']:
   z=scored[scored.fx_basis.eq(fx)&scored.cutoff.eq(cut)];threshold=selection[f'{fx}_{cut.replace(":","")}_premium_l2']['threshold']
   for quality in ['original_frozen','isolate_513130']:
    for policy in ['rank_only','threshold']:
     for rank,metric in top.RANKS.items():
      for e in plan['entries']:
       day=e['date'];g=z[z.date.eq(day)];row,status=top.choose(g,metric,threshold,policy,quality);rec=dict(scope='fresh_expansion',date=day,fx_basis=fx,cutoff=cut,quality=quality,policy=policy,rank=rank,eligible_before_quality=len(g),threshold=threshold,status=status)
       if row is not None:
        for f in ['symbol','score','net_shares','net_baskets','pred_net_shares','pred_net_U','pred_net_amount_cny','basket_capital_proxy_cny','settlement_mean_bp','mid_mean_bp','v2_sell_unit_U','v2_buy_unit_U']:rec[f]=row[f]
       daily.append(rec)
 new=pd.DataFrame(daily);new.to_csv(R/'new_daily_choices.csv',index=False);assert p.digest(T/'daily_choices.csv')==plan['old_choices_sha256'];old=pd.read_csv(T/'daily_choices.csv');old['scope']=old.scope.map({'new_test':'prior_12_dates','seen_stress':'prior_19_dates'});combined=pd.concat([old,new],ignore_index=True);combined.to_csv(R/'all_daily_choices.csv',index=False)
 summaries=[];expanded=pd.concat([combined,combined[combined.scope.ne('prior_19_dates')].assign(scope='prior12_plus_new'),combined.assign(scope='all_evaluation_dates')],ignore_index=True)
 for keys,g in expanded.groupby(['scope','fx_basis','cutoff','quality','policy','rank']):
  a=g[g.status.eq('selected')];assert not g.date.duplicated().any();n=len(a);bad=int(a.net_shares.le(0).sum());red=int(a.net_shares.lt(0).sum());flat=int(a.net_shares.eq(0).sum());assert bad==red+flat
  summaries.append(dict(**dict(zip(['scope','fx_basis','cutoff','quality','policy','rank'],keys)),calendar_days=len(g),traded_days=n,abstain_days=len(g)-n,create_days=n-bad,redemption_days=red,flat_days=flat,error_rate=bad/n if n else None,redemption_rate=red/n if n else None,wilson_error95=top.wilson(bad,n),coverage=n/len(g),max_losing_streak=top.longest(g),median_actual_U=float(a.net_baskets.median()) if n else None))
 save('summary.json',summaries);s=pd.DataFrame(summaries);s.to_csv(R/'summary.csv',index=False);print(s[s.scope.eq('fresh_expansion')&s.cutoff.eq('14:45')&s['rank'].isin(['probability','amount'])].to_string(index=False),flush=True)
def validate():
 plan=json.loads((R/'plan.json').read_text());old=json.loads((V/'plan.json').read_text());assert not {e['day'] for e in old['entries']}&{e['day'] for e in plan['entries']};scored=pd.read_parquet(R/'scored.parquet');daily=pd.read_csv(R/'new_daily_choices.csv');premium=pd.read_parquet(D/'premium.parquet');candidate=pd.read_parquet(D/'new_candidates.parquet');checks=0
 for name,h in plan['model_hashes'].items():assert p.digest(Path(name))==h;checks+=1
 assert not premium.duplicated(p.KEY).any();assert not scored.duplicated(p.KEY).any();assert set(map(tuple,candidate[p.KEY].to_numpy()))==set(map(tuple,premium[premium.premium_gate][p.KEY].to_numpy()));checks+=3
 for x in daily.itertuples():
  pool=scored[scored.date.eq(x.date)&scored.fx_basis.eq(x.fx_basis)&scored.cutoff.eq(x.cutoff)];alter=pool.copy();alter['net_shares']=0;alter['net_baskets']=-123;r,status=top.choose(alter,top.RANKS[x.rank],x.threshold,x.policy,x.quality);assert status==x.status
  if x.status=='selected':assert r.symbol==x.symbol
  checks+=1
 # Independent direct arithmetic from minute prices and share cache on new rows.
 for row in premium.sample(min(32,len(premium)),random_state=73).itertuples():
  raw=pd.read_parquet(F/'results/series'/(row.date.replace('-','')+'.parquet'),filters=[('symbol','==',row.symbol)]);minutes=raw.minute.str[:2].astype(int)*60+raw.minute.str[3:].astype(int);end=int(row.cutoff[:2])*60+int(row.cutoff[3:]);raw=raw[((minutes>=571)&(minutes<=690))|((minutes>=781)&(minutes<=end))];col='lag_settlement' if row.fx_basis=='lag' else 'actual_settlement_buy';raw=raw.dropna(subset=['etf','mid',col]);v=(raw.etf.to_numpy()/raw[col].to_numpy()-1)*10000;assert abs(v.sum()/len(v)-row.settlement_mean_bp)<1e-8
  h=json.loads((F/'inputs/share_history'/(row.symbol+'.json')).read_text())['rows'];label=next(q for q in h if q['share_date']==row.date);assert abs(label['share_change_10k']*10000-row.net_shares)<1e-5;checks+=2
 save('validation.json',dict(status='passed',checks=checks,frozen_models_verified=len(plan['model_hashes']),new_dates_disjoint=True,labels_do_not_affect_choices=True,premium_gate_precedes_L2=True,independent_minute_label_samples=min(32,len(premium)),profits_not_tested=True));print('VALIDATION',checks,'passed',flush=True)
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('stage',choices=['freeze','compute','score','validate']);globals()[ap.parse_args().stage]()
