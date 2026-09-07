"""Independent review of Luna artifacts; never mutates the source run or ledger."""
import json,gzip,hashlib,math
from pathlib import Path
from collections import Counter
import numpy as np
import pandas as pd

OUT=Path(__file__).resolve().parent
BASE=OUT.parent.parent
RUN=Path('/Users/ellis/.codex/worktrees/e50b/工具程序开发/港股通相关性分析')
HAND=BASE/'outputs/luna_handoff_20260906'
ledger=json.loads((HAND/'ledger.json').read_text())['records']
funds={x['fund_id']:x for x in ledger['基金目录']}
checks=[]; summaries=[]; full=[]; sens=[]; source_manifest=[]
def check(fid,name,ok,actual,expected):
    checks.append(dict(fund_id=fid,check=name,passed=bool(ok),actual=actual,expected=expected))
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def clean(x):
    if isinstance(x,dict):return {k:clean(v) for k,v in x.items()}
    if isinstance(x,list):return [clean(v) for v in x]
    if isinstance(x,(np.integer,)):return int(x)
    if isinstance(x,(float,np.floating)) and not math.isfinite(x):return None
    return x

for p in sorted(RUN.glob('runs/*/20260906_batch/reports/results.json')):
    result=json.loads(p.read_text());fid=result['target'];root=p.parent.parent
    cfg=RUN/'config/batch'/f"{fid.replace('.', '_')}.json"
    config=json.loads(cfg.read_text());bundle=RUN/config['basket_input']
    oos=pd.read_parquet(p.parent/'oos_residuals.parquet');oos['date']=oos.date.astype(str)
    panel=pd.read_parquet(root/'data/normalized/pilot_minutes.parquet');panel['date']=panel.date.astype(str)
    folds=pd.read_csv(p.parent/'folds.csv',dtype={c:str for c in ['test_date','fit_start','inner_fit_end','validation_start','train_end']})
    quality=pd.read_csv(p.parent/'data_quality.csv',dtype={'date':str})
    val=json.loads((p.parent/'validation.json').read_text())
    pairs_ci={x['horizon']:x['ci95'] for x in val['bootstrap'] if x['model']=='HHI_HTI_fixed_pair'}
    maxerr=0.;counts_ok=True;same_endpoints=True;residualerr=0.;foldok=True
    for m in result['metrics']:
        h=m['horizon'];model=m['model'];r=oos[(oos.horizon==h)&(oos.model==model)]
        y=r.y.to_numpy();e=r.residual.to_numpy();k=math.ceil(len(y)*.05)
        independently={'target_std_bps':np.std(y,ddof=1)*1e4,'residual_std_bps':np.std(e,ddof=1)*1e4,
            'variance_reduction':1-np.var(e,ddof=1)/np.var(y,ddof=1),'residual_mean_bps':np.mean(e)*1e4,
            'upside_es95_bps':np.mean(np.sort(e)[-k:])*1e4,'downside_es95_bps':-np.mean(np.sort(e)[:k])*1e4,
            'abs_p95_bps':np.percentile(np.abs(e),95)*1e4}
        maxerr=max(maxerr,max(abs(independently[x]-m[x]) for x in independently))
        counts_ok &= len(r)==m['samples'] and r.date.nunique()==m['days']
        ci=m.get('variance_reduction_ci95') if model=='selected' else pairs_ci.get(h) if model=='HHI_HTI_fixed_pair' else None
        full.append(dict(fund_id=fid,fund_name=funds[fid]['fund_name'],index_name=funds[fid]['index_name'],
            horizon=h,model=model,**independently,days=r.date.nunique(),samples=len(r),first_oos=r.date.min(),last_oos=r.date.max(),
            ci_low=ci[0] if ci else None,ci_high=ci[1] if ci else None,
            evidence_tier='已完成520600事件核查' if fid=='520600.SH' else '已核对产品范围；停牌末段原运行剔除' if fid=='513090.SH' else '技术结果；产品与事件待核验',
            source=str(p),config_sha256=digest(cfg)))
    for h in [5,15,30,60]:
        labels=pd.read_parquet(root/f'data/normalized/labels_{h}m.parquet');labels['date']=labels.date.astype(str)
        dates=sorted(labels.date.unique())
        ref=oos[(oos.horizon==h)&(oos.model=='no_hedge')][['date','minute','end_minute']].reset_index(drop=True)
        for model,r in oos[oos.horizon==h].groupby('model'):
            same_endpoints &= r[['date','minute','end_minute']].reset_index(drop=True).equals(ref)
        for _,f in folds[folds.horizon==h].iterrows():
            idx=dates.index(f.test_date)
            foldok &= idx>=60 and f.fit_start==dates[idx-60] and f.inner_fit_end==dates[idx-11] and f.validation_start==dates[idx-10] and f.train_end==dates[idx-1]
            sample=labels[labels.date==f.test_date].sort_values('minute')
            for model in ['HHI_HTI_fixed_pair','selected']:
                rr=oos[(oos.horizon==h)&(oos.model==model)&(oos.date==f.test_date)].sort_values('minute')
                if model=='HHI_HTI_fixed_pair':pred=sample.HHI_FUT.to_numpy()*f.fixed_pair_HHI_beta+sample.HTI_FUT.to_numpy()*f.fixed_pair_HTI_beta
                else:pred=sample[config['tools']].to_numpy()@f[config['tools']].to_numpy(dtype=float)
                residualerr=max(residualerr,float(np.max(np.abs(sample.y.to_numpy()-pred-rr.residual.to_numpy()))))
    check(fid,'独立复算所有主指标',maxerr<=1e-8,maxerr,'绝对误差≤1e-8')
    check(fid,'计数及模型共用端点',counts_ok and same_endpoints,len(oos),'同基金同周期全部模型同样本')
    check(fid,'60日训练内部50/10及未来隔离边界',foldok,len(folds),'训练截止早于测试日且索引吻合')
    check(fid,'冻结日度beta重建双腿与自动模型残差',residualerr<=1e-12,residualerr,'绝对收益误差≤1e-12')
    with gzip.open(bundle,'rt') as z:raw=[json.loads(line) for line in z]
    targets={r.get('target',fid.split('.')[0]) for r in raw}
    check(fid,'抽取目标代码',targets=={fid.split('.')[0]},sorted(targets),fid.split('.')[0])
    scalar=[];rawdict={r['date']:r for r in raw};ds=sorted(panel.date.unique())
    for day in [ds[0],ds[len(ds)//2],ds[-1]]:
        value=0.;usable=True;minute=850
        for c in rawdict[day]['components']:
            code=str(int(c['成分股代码'])).zfill(5);bars=rawdict[day]['hk'].get(code,[])
            prior=[bar for bar in bars if bar[0]<=minute]
            if not prior:usable=False;break
            value+=float(c['数量股'])*prior[-1][-1]
        if usable:
            saved=panel[(panel.date==day)&(panel.minute==minute)].basket_hkd.iloc[0]
            scalar.append(abs(value-saved))
    check(fid,'原始PCF数量乘当时价格标量复算',len(scalar)==3 and max(scalar,default=1)<=1e-8,dict(count=len(scalar),max_error=max(scalar,default=None)),'3点均通过；无法复算不能默认为通过')
    scope=oos[oos.horizon==30]
    by={m['model']:m for m in full if m['fund_id']==fid and m['horizon']==30}
    singles=[x for k,x in by.items() if k not in ['selected','HHI_HTI_fixed_pair','no_hedge']]
    best=min(singles,key=lambda x:x['residual_std_bps']);pair=by['HHI_HTI_fixed_pair'];auto=by['selected']
    q=quality.status.value_counts().to_dict();bad=quality[quality.status!='INCLUDED_PRICE_MARK']
    sr=pd.read_csv(p.parent/'sensitivity.csv')
    for r in sr.to_dict('records'):
        r.update(fund_id=fid,refit='_refit' in r['scenario'],base_model=r.get('model'),source=str(p.parent/'sensitivity.csv'))
        # Never copy the pair's base ID onto selected or infer N/A numeric values as zero.
        r['review_status']='样本不足' if r.get('status')=='insufficient_training_days' else '仅评价' if not r['refit'] else '重新拟合'
        sens.append(r)
    fresh=sr[(sr.horizon==30)&(sr.scenario=='fresh_2pct_refit')&(sr.model=='HHI_HTI_fixed_pair')]
    summaries.append(dict(fund_id=fid,fund_name=funds[fid]['fund_name'],index_name=funds[fid]['index_name'],
        evidence_tier=pair['evidence_tier'],luna_status=funds[fid]['task_status'],usable_days=len(ds),oos_days=pair['days'],oos_start=pair['first_oos'],oos_end=pair['last_oos'],
        meets_20_oos=pair['days']>=20,unhedged_std_bp=pair['target_std_bps'],pair_std_bp=pair['residual_std_bps'],pair_variance_reduction=pair['variance_reduction'],
        pair_up_es95_bp=pair['upside_es95_bps'],pair_down_es95_bp=pair['downside_es95_bps'],pair_ci_low=pair['ci_low'],pair_ci_high=pair['ci_high'],
        best_single=best['model'],single_std_bp=best['residual_std_bps'],single_variance_reduction=best['variance_reduction'],
        pair_improvement_vs_single_bp=best['residual_std_bps']-pair['residual_std_bps'],auto_std_bp=auto['residual_std_bps'],auto_variance_reduction=auto['variance_reduction'],
        fresh_pair_variance_reduction=None if fresh.empty else fresh.iloc[0].variance_reduction,
        fresh_pair_oos_days=None if fresh.empty else int(fresh.iloc[0].days),
        stale_p50=panel.stale_weight.median(),stale_p95=panel.stale_weight.quantile(.95),frozen_max=panel.frozen_weight.max(),
        excluded_days=len(bad),missing_codes=';'.join(sorted(set(str(x) for x in bad.missing_members.dropna()))),
        scalar_probes=len(scalar),source=str(p)))
    source_manifest.append(dict(fund_id=fid,results=str(p),results_sha256=digest(p),config=str(cfg),config_sha256=digest(cfg),bundle=str(bundle),bundle_sha256=digest(bundle)))

summary=pd.DataFrame(summaries)
main=pd.DataFrame(full)
baseline=pd.read_csv(BASE/'reports/model_comparison.csv').sort_values(['horizon','model']).reset_index(drop=True)
rerun=pd.read_csv(RUN/'runs/520600.SH/20260906_batch/reports/model_comparison.csv').sort_values(['horizon','model']).reset_index(drop=True)
cols=baseline.select_dtypes('number').columns
delta=float(np.nanmax(np.abs(baseline[cols].to_numpy()-rerun[cols].to_numpy())))
check('520600.SH','原工程与批量运行44行数值复现',delta<=1e-8,delta,'绝对误差≤1e-8')
allfunds=[]
for fid,f in funds.items():
    s=next((x for x in summaries if x['fund_id']==fid),{})
    allfunds.append(dict(fund_id=fid,fund_name=f['fund_name'],index_name=f['index_name'],luna_status=f['task_status'],classification=f['classification'],
        has_technical_result=bool(s),oos_days=s.get('oos_days'),reason=f['reason'],review_conclusion='520600初筛可采用' if fid=='520600.SH' else '保留部分日期结果；补停牌处理' if fid=='513090.SH' else '仅技术初筛待核验' if s else '未形成可采用回测',official_url=f.get('official_url')))

wrong_ci=sum(r['model_id']!='HHI_HTI_fixed_pair' and r.get('ci_low') is not None for r in ledger['回测结果'])
wrong_refit=sum(bool(r['refit'])!=('_refit' in r['scenario']) for r in ledger['敏感性'])
wrong_base=sum(r['model_id']=='selected' and 'HHI_HTI_fixed_pair' in r['base_result_id'] for r in ledger['敏感性'])
report=clean(dict(candidate_count=len(funds),run_count=len(summaries),main_rows=len(full),sensitivity_rows=len(sens),
    checks=len(checks),failed_checks=[r for r in checks if not r['passed']],baseline_max_delta=delta,
    luna_status_counts=dict(Counter(f['task_status'] for f in funds.values())),
    oos_days_range=[int(summary.oos_days.min()),int(summary.oos_days.max())],under_20_oos=summary[summary.oos_days<20].fund_id.tolist(),
    wrong_ci_cells_as_row_count=wrong_ci,wrong_refit_rows=wrong_refit,wrong_base_rows=wrong_base,
    median_pair_variance_reduction=float(summary.pair_variance_reduction.median()),
    pair_variance_bins={'gte80pct':int((summary.pair_variance_reduction>=.8).sum()),'50to80pct':int(((summary.pair_variance_reduction>=.5)&(summary.pair_variance_reduction<.8)).sum()),'below50pct':int((summary.pair_variance_reduction<.5).sum())},
    pair_beats_best_single=int((summary.pair_improvement_vs_single_bp>0).sum()),
    auto_beats_pair=int((summary.auto_std_bp<summary.pair_std_bp).sum()),
    original_task_timestamps='G01-G06 use source README mtime as started_at; cannot be treated as real execution chronology',
    best_single_counts=summary.best_single.value_counts().to_dict()))
for name,records in [('fund_summary',summaries),('corrected_results',full),('corrected_sensitivity',sens),('candidate_review',allfunds),('audit_checks',checks)]:
    pd.DataFrame(records).to_csv(OUT/(name+'.csv'),index=False,encoding='utf-8-sig')
    (OUT/(name+'.json')).write_text(json.dumps(clean(records),ensure_ascii=False,indent=2,allow_nan=False))
(OUT/'audit_summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
(OUT/'source_manifest.json').write_text(json.dumps(source_manifest,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2))
print(summary[summary.fund_id.isin(['520600.SH','513090.SH'])].to_string(index=False))
print(summary[['fund_id','index_name','oos_days','pair_variance_reduction','best_single','single_variance_reduction','auto_variance_reduction']].to_string(index=False))
