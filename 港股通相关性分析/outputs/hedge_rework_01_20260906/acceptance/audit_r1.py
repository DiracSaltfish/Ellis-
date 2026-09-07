from pathlib import Path
import json,gzip,collections,hashlib,math
import numpy as np
import pandas as pd

OUT=Path(__file__).resolve().parent; BASE=OUT.parent; PROJECT=BASE.parents[1]
def load(p):return json.loads(p.read_text())
def jl(p):return [json.loads(x) for x in (gzip.open(p,'rt') if str(p).endswith('.gz') else p.open()) if x.strip()]
def save(n,x):(OUT/n).write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str))
def stat(y,h):
    y=np.asarray(y);h=np.asarray(h);e=y-h;k=max(1,math.ceil(len(y)*.05))
    return {'rho':float(np.corrcoef(y,h)[0,1]) if np.std(h)>0 else None,'vr':float(1-np.var(e,ddof=1)/np.var(y,ddof=1)),'target_std_bp':float(np.std(y,ddof=1)*1e4),'residual_std_bp':float(np.std(e,ddof=1)*1e4),'up_es95_bp':float(np.sort(e)[-k:].mean()*1e4),'down_es95_bp':float(np.sort(-e)[-k:].mean()*1e4)}

funds=[]
for a in 'ABC':
    d=BASE/f'agent_{a}';rs=jl(d/'tables/fund_decisions.jsonl') if a=='B' else load(d/'fund_decisions.json')
    funds += [{**r,'audit_owner':a} for r in rs]
assert len(funds)==237 and len({r['fund_id'] for r in funds})==237
summary={'funds':237,'raw_decisions':dict(collections.Counter(r['decision'] for r in funds))}

# B: reconstruct labels without calling its research functions.
b=BASE/'agent_B/data/raw/new_period_520600'
panel=pd.DataFrame(jl(b/'520600_basket_panel_1m.jsonl.gz'))
saved=pd.DataFrame(jl(b/'520600_new_period_residuals.jsonl.gz'))
lock=load(b/'520600_selection_lock.json');metrics=load(b/'520600_new_period_metrics.json')
bp=[];match=[]
for h in [5,15,30,60]:
    coef=lock['policies'][str(h)]['beta']; rows=[]
    for day,g in panel.groupby('date'):
        g=g.set_index('minute')
        for m in g.index:
            end=m+h
            if end not in g.index or m<570 or 720<=m<780 or 720<=end<780:continue
            y=g.loc[end,'basket_hkd']/g.loc[m,'basket_hkd']-1
            returns={t:g.loc[end,c]/g.loc[m,c]-1 for t,c in [('HSI_FUT','HSI_U6'),('HHI_FUT','HHI_U6'),('HTI_FUT','HTI_U6')]}
            hedge=sum(coef[t]*returns[t] for t in coef)
            rows.append({'date':day,'minute':int(m),'y':y,'hedge':hedge,**returns})
    q=pd.DataFrame(rows);z=saved[saved.horizon_min==h].copy();z['date']=z.date.astype(str);q['date']=q.date.astype(str)
    merged=q.merge(z,on=['date','minute'],suffixes=('','_saved'),validate='one_to_one')
    err=float(max(np.max(np.abs(merged.y-merged.y_saved)),np.max(np.abs(merged.hedge-merged.locked_proxy))))
    ss=stat(q.y,q.hedge);r=next(x for x in metrics if x['horizon_min']==h)
    match.append({'horizon':h,'days':int(q.date.nunique()),'labels':len(q),'saved_labels':len(z),'max_label_error':err,'rho_error':abs(ss['rho']-r['correlation']),'vr_error':abs(ss['vr']-r['variance_reduction'])})
    for session,q2 in [('HK_FULL',q),('CN_OVERLAP_END_BAR_BEFORE_1500',q[q.minute+h<900])]:
        for name in ['LOCKED_PAIR','HSI_FUT','HHI_FUT','HTI_FUT']:
            ss=stat(q2.y,q2.hedge if name=='LOCKED_PAIR' else q2[name])
            bp.append({'horizon':h,'session':session,'model':name,'days':int(q2.date.nunique()),'labels':len(q2),**ss,'weight_note':'existing locked pair' if name=='LOCKED_PAIR' else 'unit-beta descriptive comparator; rho scale invariant, not a preselected live policy'})
save('B_labels_checks.json',match);save('B_price_comparators.json',bp)

# B raw PCF × stock close independent scalar probes, one each day.
pcfs={r['date']:r for r in jl(b/'520600_pcf_parsed.jsonl.gz')};prices={}
for rec in jl(b/'520600_stk_1m.jsonl.gz'):
    code=rec.get('security_id')
    for bar in rec.get('bars',[]):
        t=pd.Timestamp(int(bar['date']),unit='s',tz='UTC').tz_convert('Asia/Hong_Kong')
        prices[(code,t.strftime('%Y%m%d'),t.hour*60+t.minute)]=float(bar['close'])
scalars=[]
for day,g in panel.groupby('date'):
    r=g.iloc[len(g)//2]; comps=pcfs[day]['components'];v=sum(float(c['数量股'] or 0)*prices[(c['成分股代码'],day,int(r.minute))] for c in comps if float(c['数量股'] or 0)>0)
    scalars.append({'date':day,'minute':int(r.minute),'components':len(comps),'error_hkd':v-r.basket_hkd})
save('B_scalar_checks.json',scalars)

# C: independently recompute all OOS-aggregate metrics, check new-period day claims.
crows=pd.read_csv(BASE/'agent_C/results/r1_oos_residuals.csv',dtype={'date':str,'policy_id':str})
cm=load(BASE/'agent_C/model_metrics.json');cc=[];wrong_new=[]
for r in cm:
    if r['scenario_id']!='OOS_AGGREGATE':continue
    q=crows[(crows.fund_id==r['fund_id'])&(crows.horizon_min==r['horizon_min'])&(crows.policy_id==r['policy_id'])]
    s=stat(q.target_bp/1e4,q.hedge_bp/1e4)
    cc.append({'fund_id':r['fund_id'],'horizon':r['horizon_min'],'policy_id':r['policy_id'],'rows':len(q),'rho_error':abs(s['rho']-r['hedge_return_correlation']),'vr_error':abs(s['vr']-r['variance_reduction'])})
    if r['confirmation_status']=='NEW_LOCKED':
        nd=q[q.date>='20260804'].date.nunique()
        if nd<20:wrong_new.append({'fund_id':r['fund_id'],'horizon':r['horizon_min'],'policy_id':r['policy_id'],'reported_oos_days':r['oos_days'],'actual_new_days':int(nd)})
save('C_oos_checks.json',cc);save('C_mislabeled_new_rows.json',wrong_new)
case=[]
for (fid,h),q in crows.groupby(['fund_id','horizon_min']):
    if fid!='520760.SH' or h!=30:continue
    for label,q2 in [('ALL_SELECTED_POLICY_DAYS',q),('NEW_ONLY',q[q.date>='20260804'])]:
        if len(q2)>1:case.append({'fund_id':fid,'horizon':int(h),'window':label,'days':int(q2.date.nunique()),'policies':{str(k):int(v) for k,v in q2.policy_id.value_counts().items()},**stat(q2.target_bp/1e4,q2.hedge_bp/1e4)})
save('C_520760_recomputed.json',case)

# A: ES-labelled fields are quantiles, so quantify all new-run affected rows.
am=load(BASE/'agent_A/model_metrics.json');ar=pd.read_csv(BASE/'agent_A/research/513090_oos_residuals.csv',dtype={'model_id':str})
bad_es=[]
for r in am:
    if r['run_id']!='A-R1-513090-OLD':continue
    q=ar[(ar.horizon_min==r['horizon_min'])&(ar.model_id==r['model_id'])];s=stat(q.basket_return,q.hedge_return)
    if r.get('up_es95_bp') is not None and abs(r['up_es95_bp']-s['up_es95_bp'])>1e-8:
        bad_es.append({'horizon':r['horizon_min'],'model':r['model_id'],'reported_up_es95':r['up_es95_bp'],'actual_up_es95':s['up_es95_bp'],'reported_down_es95':r['down_es95_bp'],'actual_down_es95':s['down_es95_bp']})
save('A_tail_field_errors.json',bad_es)
summary.update({'B_four_horizons_numerically_match':all(r['max_label_error']<1e-12 and r['rho_error']<1e-8 and r['vr_error']<1e-8 and r['labels']==r['saved_labels'] for r in match),'B_raw_scalar_days':len(scalars),'B_max_scalar_error_hkd':max(abs(r['error_hkd']) for r in scalars),'C_oos_metric_rows_checked':len(cc),'C_max_rho_error':max(r['rho_error'] for r in cc),'C_mislabeled_NEW_LOCKED_rows':len(wrong_new),'A_mislabeled_ES_rows':len(bad_es),'acceptance':'PARTIAL_AS_RESEARCH_NOT_FULL_MAPPING'})
save('summary.json',summary)
print(json.dumps(summary,ensure_ascii=False,indent=2));print('B30',json.dumps([r for r in bp if r['horizon']==30],ensure_ascii=False));print('C520760',json.dumps(case,ensure_ascii=False))
