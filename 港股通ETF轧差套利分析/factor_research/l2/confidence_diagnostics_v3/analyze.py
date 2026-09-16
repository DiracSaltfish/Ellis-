"""Transparent exploratory confidence diagnostics. Never replace original frozen models."""
from pathlib import Path
import json,gzip,hashlib,os
import pandas as pd,numpy as np,joblib
from scipy.stats import fisher_exact
R=Path(__file__).resolve().parent;L=R.parent;V=L/'premium_l2_gate_v3';M=L/'merged_years_v3';SH_ONLY=os.environ.get('HK_ETF_SH_ONLY')=='1';B=L/'sh_only_v3' if SH_ONLY else M;O=R/('outputs_sh' if SH_ONLY else 'outputs');O.mkdir(exist_ok=True)
def save(name,x):
 def convert(v):
  if isinstance(v,np.generic):return v.item()
  if isinstance(v,np.ndarray):return v.tolist()
  raise TypeError(str(type(v)))
 (O/name).write_text(json.dumps(x,ensure_ascii=False,indent=2,default=convert))
base=json.loads((B/'outputs/data.json').read_text());daily_meta={r['date']:r for r in base['main']};sel=json.loads((V/'selection.json').read_text())
hist=pd.read_parquet(V/'historical_candidates.parquet');hist=hist[hist.split.isin(['train','validation'])&hist.l2_available]
pool=pd.concat([hist,pd.read_parquet(L/'top_one_v3/scored.parquet'),pd.read_parquet(L/'expanded_dates_v3/scored.parquet'),pd.read_parquet(M/'scored.parquet')],ignore_index=True)
pool=pool[pool.fx_basis.eq('lag')&pool.cutoff.eq('14:45')&pool.symbol.ne('513130.SH')&pool.date.str.startswith('2026')].copy()
if SH_ONLY:pool=pool[pool.symbol.str.startswith('5')&pool.symbol.str.endswith('.SH')].copy()
assert not pool.duplicated(['date','symbol']).any()
models={}
for key in ['premium','premium_l2']:
 path=V/'models'/f'lag_1445_{key}.joblib';assert hashlib.sha256(path.read_bytes()).hexdigest()==sel[f'lag_1445_{key}']['model_sha256'];models[key]=joblib.load(path)
 pool['premium_score' if key=='premium' else 'score']=models[key]['classifier'].predict_proba(pool[models[key]['features']])[:,1]
pool['phase']=pool.date.map(lambda x:daily_meta[x]['phase']);pool['candidate_complete']=pool.date.map(lambda x:daily_meta[x]['candidate_complete']);pool['set']=np.where(pool.date<='2026-02-27','train',np.where(pool.date<='2026-03-13','validation','test'))
pool['month']=pool.date.str[:7];pool['success']=pool.net_shares.gt(0);pool['net_U']=pool.net_shares/pool.unit
entries={e['date']:e for f in [V/'plan.json',L/'expanded_dates_v3/data/plan.json',M/'plan.json'] for e in json.loads(f.read_text())['entries']}
caps={};names={}
for day,g in pool.groupby('date'):
 e=entries[day];b=json.load(gzip.open(e['sources']['basket']['path']));p=pd.read_csv(e['sources']['pcf']['path'],encoding='utf-8-sig',dtype={'基金代码':str}).set_index('基金代码')
 for sym in g.symbol:
  cap=pd.to_numeric(p.loc[sym[:6],'当日累计申购上限份'],errors='coerce');caps[(day,sym)]=float(cap) if np.isfinite(cap) and 0<cap<1e14 else np.nan;names[(day,sym)]=b[sym]['name']
pool['cap_shares']=[caps[(r.date,r.symbol)] for r in pool.itertuples()];pool['name']=[names[(r.date,r.symbol)] for r in pool.itertuples()];pool['full']=pool.cap_shares.notna()&pool.net_shares.ge(pool.cap_shares-.01)
pool['sell_buy_U_ratio']=pool.v2_sell_unit_U/(pool.v2_buy_unit_U+1e-9);pool['net_unit_U']=pool.v2_sell_unit_U-pool.v2_buy_unit_U
pool=pool.sort_values(['date','score','symbol'],ascending=[True,False,True],kind='stable');pool['rank']=pool.groupby('date').cumcount()+1
pool['premium_rank']=pool.sort_values(['date','premium_score','symbol'],ascending=[True,False,True],kind='stable').groupby('date').cumcount().add(1)
second=pool[pool['rank'].eq(2)].set_index('date').score;pool['score_margin']=pool.score-pool.date.map(second)
# No runner-up means no observed ranking margin, not an imaginary zero-score rival.
top=pool[pool['rank'].eq(1)].copy()
for x in top.itertuples():assert x.symbol==daily_meta[x.date]['symbol'] and abs(x.score-daily_meta[x.date]['score'])<1e-8
pool.to_parquet(O/'candidates.parquet',index=False);top.to_parquet(O/'daily_top1.parquet',index=False)
def stats(z):
 n=len(z);k=int(z.success.sum());a=k/n if n else None
 if n:
  den=1+1.96**2/n;c=(a+1.96**2/(2*n))/den;h=1.96*np.sqrt(a*(1-a)/n+1.96**2/(4*n*n))/den;ci=[max(0,c-h),min(1,c+h)]
 else:ci=[None,None]
 return dict(n=n,positive=k,zero=int(z.net_shares.eq(0).sum()),negative=int(z.net_shares.lt(0).sum()),accuracy=a,wilson95=ci,dates=int(z.date.nunique()),funds=int(z.symbol.nunique()),median_net_U=float(z.net_U.median()) if n else None,at_least_5U=int(z.net_U.ge(5-.01).sum()),at_least_10U=int(z.net_U.ge(10-.01).sum()),full=int(z.full.sum()),unknown_cap=int(z.cap_shares.isna().sum()),mean_score=float(z.score.mean()) if n else None)
def scope(d,name):
 if name=='验证期':return d[d['set'].eq('validation')]
 if name=='此前44日':return d[d.phase.isin(['历史压力检验','上轮检验','本轮新增检验'])]
 if name=='新增29日':return d[d.phase.eq('2026新增检验')]
 if name=='后续73日':return d[d['set'].eq('test')]
 if name=='3至5月后续':return d[d['set'].eq('test')&d.date.lt('2026-06-01')]
 if name=='6至7月后续':return d[d['set'].eq('test')&d.date.ge('2026-06-01')]
 raise ValueError(name)
rules={'原每日第一名':lambda z:pd.Series(True,index=z.index)}
for t in [.80,.90,.95,.97,.98,.99]:rules[f'分数≥{t:.2f}']=lambda z,t=t:z.score.ge(t)
rules.update({
 '分数≥0.90且溢价模型≥0.90':lambda z:z.score.ge(.9)&z.premium_score.ge(.9),
 '分数≥0.90且整U净供给>0':lambda z:z.score.ge(.9)&z.net_unit_U.gt(0),
 '分数≥0.90且卖出≥5U及买入2倍':lambda z:z.score.ge(.9)&z.v2_sell_unit_U.ge(5)&z.v2_sell_unit_U.ge(2*z.v2_buy_unit_U),
 '分数≥0.90且正溢价≥90%及P10>0':lambda z:z.score.ge(.9)&z.settlement_positive_fraction.ge(.9)&z.settlement_p10_bp.gt(0),
 '分数≥0.90且领先第二名≥0.10':lambda z:z.score.ge(.9)&z.score_margin.ge(.1),
 '分数≥0.95且整U净供给>0':lambda z:z.score.ge(.95)&z.net_unit_U.gt(0),
 '分数≥0.95且卖出≥5U及买入2倍':lambda z:z.score.ge(.95)&z.v2_sell_unit_U.ge(5)&z.v2_sell_unit_U.ge(2*z.v2_buy_unit_U),
 '分数≥0.95且正溢价≥90%及P10>0':lambda z:z.score.ge(.95)&z.settlement_positive_fraction.ge(.9)&z.settlement_p10_bp.gt(0),
 '分数≥0.90且两模型均排名第一':lambda z:z.score.ge(.9)&z.premium_rank.eq(1),
})
records=[];sequence=[]
for quality in ['完整候选日期','沿用全部日期']:
 z=top[top.candidate_complete] if quality=='完整候选日期' else top
 for name in ['验证期','此前44日','新增29日','后续73日','3至5月后续','6至7月后续']:
  a=scope(z,name)
  for rule,fn in rules.items():
   selected=a[fn(a)]
   for cap in ['未事后剔除','事后剔除满额']:
    b=selected[~selected.full] if cap=='事后剔除满额' else selected
    records.append(dict(quality=quality,cohort=name,rule=rule,cap_policy=cap,original_selected=len(a),abstained=len(a)-len(selected),cap_removed=int(selected.full.sum()) if cap=='事后剔除满额' else 0,**stats(b)))
for r in top.to_dict('records'):
 one=top[(top.date==r['date'])]
 for rule,fn in rules.items():sequence.append({k:r[k] for k in ['date','symbol','name','score','premium_score','net_shares','net_U','full','phase','candidate_complete']}|dict(rule=rule,trigger=bool(fn(one).iloc[0])))
save('rules.json',records);pd.DataFrame(sequence).to_csv(O/'信号规则逐日对照.csv',index=False,encoding='utf-8-sig')
# Calendar decomposition, candidate base rates and calibration of daily maximum scores.
periods=[]
for key in ['验证期','此前44日','新增29日','后续73日','3至5月后续','6至7月后续']:
 for level,z in [('初筛可评分候选',pool),('每日第一名',top)]:
  z=scope(z[z.candidate_complete],key);periods.append(dict(cohort=key,level=level,**stats(z),brier=float(((z.score-z.success)**2).mean()) if len(z) else None))
save('periods.json',periods)
cal=[]
for name in ['验证期','此前44日','新增29日','后续73日']:
 z=scope(top[top.candidate_complete],name)
 for lo,hi in [(0,.8),(.8,.9),(.9,.95),(.95,.97),(.97,1.000001)]:cal.append(dict(cohort=name,band=f'{lo:.2f}–{min(hi,1):.2f}',**stats(z[z.score.ge(lo)&z.score.lt(hi)])))
save('calibration.json',cal)
features=['score','premium_score','score_margin','settlement_mean_bp','settlement_p10_bp','settlement_premium_bp','settlement_positive_fraction','settlement_std_bp','settlement_change15_bp','fx_gap_bp','mid_mean_bp','turnover_pct','etf_return_bp','basket_return_bp','lag_flow_pct','lag5_flow_pct','l2_active_imbalance','l2_passive_sell_unit_frac','l2_sell_unit_parent_excess','v2_sell_unit_U','v2_buy_unit_U','net_unit_U','v2_unit_net_supply_pct','v2_sell_unit_excess_pct','v2_sell_unit_active_pct','v2_sell_unit_passive_pct','v2_sell_unit_fill_ratio','v2_sell_unit_cancel_ratio','v2_sell_unknown_quantity_fraction','v2_log_previous_U']
drift=[]
for name in ['此前44日','新增29日','3至5月后续','6至7月后续']:
 z=scope(top[top.candidate_complete&~top.full],name)
 for outcome,g in [('全部',z),('净申购',z[z.success]),('未命中',z[~z.success])]:
  for f in features:drift.append(dict(cohort=name,outcome=outcome,feature=f,n=len(g),median=float(g[f].median()) if len(g) else None,q25=float(g[f].quantile(.25)) if len(g) else None,q75=float(g[f].quantile(.75)) if len(g) else None))
save('feature_distributions.json',drift)
top[top['set'].eq('test')&~top.success][['date','symbol','name','net_U','candidate_complete',*features]].to_csv(O/'误判信号特征明细.csv',index=False,encoding='utf-8-sig')
# Group ablation anchored to training medians: a model sensitivity, not causal attribution.
model=models['premium_l2'];tr=pool[pool['set'].eq('train')];test=top[top['set'].eq('test')].copy();groups={
 '溢价与汇差':[f for f in model['features'] if f.startswith(('settlement_','mid_')) or f=='fx_gap_bp'],
 '历史份额与规模':['lag_flow_pct','lag5_flow_pct','log_prev_assets','v2_log_previous_U'],
 '申赎限额':[f for f in model['features'] if 'limit_' in f or f in ['creation_allowed','redemption_allowed']],
 '整U执行供给':[f for f in model['features'] if f.startswith('v2_') and f not in ['v2_log_previous_U','v2_is_sh'] and 'limit_' not in f],
 '其他L2':[f for f in model['features'] if f.startswith('l2_')],
 '行情与换手':['turnover_pct','etf_return_bp','basket_return_bp'],
}
sens=[]
for name,fields in groups.items():
 x=test[model['features']].copy()
 for f in fields:x[f]=tr[f].median()
 scores=model['classifier'].predict_proba(x)[:,1]
 for r,alt in zip(test.itertuples(),scores):sens.append(dict(date=r.date,symbol=r.symbol,actual_U=r.net_U,group=name,original_score=r.score,training_median_score=float(alt),score_difference=float(r.score-alt)))
save('model_group_sensitivity.json',sens)
# Validation has too few daily signals for the original proposed n>=20 support requirement.
validated=[]
for rule in rules:
 x=next(r for r in records if r['quality']=='完整候选日期' and r['cohort']=='验证期' and r['rule']==rule and r['cap_policy']=='未事后剔除')
 if x['n']>=20 and x['funds']>=5 and x['accuracy']>=.9:validated.append(rule)
save('validation_selection.json',dict(rule_count=len(rules),minimum_daily_signals=20,minimum_funds=5,minimum_observed_precision=.9,qualified=validated,statement='No claim of a calibrated 90% probability from post-hoc small subsets.'))
save('metadata.json',dict(frozen_models={k:sel['lag_1445_'+k]['model_sha256'] for k in models},candidate_rows=len(pool),daily_signals=len(top),date_scope=len([r for r in daily_meta.values() if r['date'].startswith('2026')]),original_top1_reproduced=True,rule_count=len(rules)))
print(pd.DataFrame(records).query('quality=="完整候选日期" and cohort=="后续73日" and cap_policy=="事后剔除满额"')[['rule','n','positive','zero','negative','accuracy','median_net_U']].to_string(index=False),flush=True)
print('VALIDATION QUALIFIED',validated,flush=True)
