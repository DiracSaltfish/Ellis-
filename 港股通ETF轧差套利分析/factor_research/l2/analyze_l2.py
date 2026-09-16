"""Exploratory L2 factor study with date-ordered holdout and matched baseline.
No settlement-profit labels are available; targets are SAME-DAY net creation/redemption.
"""
from pathlib import Path
import json
import numpy as np,pandas as pd
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression,Ridge
from sklearn.metrics import average_precision_score,roc_auc_score,brier_score_loss,mean_absolute_error
R=Path(__file__).resolve().parent;O=R/'results_2026'
BASE=['settlement_mean_bp','settlement_p10_bp','settlement_std_bp','settlement_late_minus_early_bp','settlement_above30_fraction','lag_flow_pct','lag5_flow_pct','turnover_pct','basket_return_bp','log_prev_assets']
L2=['l2_active_imbalance','l2_net_volume_pct','l2_buy_parent_hhi','l2_sell_parent_hhi','l2_buy_cancel_ratio','l2_sell_cancel_ratio','l2_buy_large_parent_frac','l2_sell_large_parent_frac','l2_buy_unit_parent_frac','l2_sell_unit_parent_frac','l2_buy_unit_parent_excess','l2_sell_unit_parent_excess','l2_buy_unit_burst_frac','l2_sell_unit_burst_frac','l2_buy_unit_burst_excess','l2_sell_unit_burst_excess','l2_buy_unit_submitted_fill_frac','l2_sell_unit_submitted_fill_frac','l2_buy_passive_unit_fill_frac','l2_sell_passive_unit_fill_frac','l2_buy_positive_premium_frac','l2_sell_positive_premium_frac','l2_buy_negative_premium_frac','l2_sell_negative_premium_frac','l2_buy_compression_frac','l2_sell_compression_frac','l2_flow_premium_change_corr','l2_late_imbalance','l2_flow_imbalance_std','l2_quote_imbalance','l2_spread_bp']

def model():return make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),LogisticRegression(C=.1,max_iter=2000))
def metrics(y,p):
    return dict(n=len(y),base_rate=float(np.mean(y)),ap=float(average_precision_score(y,p)),auc=float(roc_auc_score(y,p)) if len(set(y))>1 else None,brier=float(brier_score_loss(y,p)))
def main():
    raw=pd.read_parquet(O/'l2_panel.parquet');d=raw[raw.l2_quality_pass].copy().sort_values(['date','symbol']).reset_index(drop=True)
    dates=sorted(d.date.unique());n=len(dates);assert n>=10
    hold=max(3,n//5);train_dates=dates[:n-2*hold];val_dates=dates[n-2*hold:n-hold];test_dates=dates[n-hold:]
    tr=d[d.date.isin(train_dates)].copy();va=d[d.date.isin(val_dates)].copy();te=d[d.date.isin(test_dates)].copy()
    for z in [d,tr,va,te]:
        z['create']=(z.net_baskets>0).astype(int);z['redeem']=(z.net_baskets<0).astype(int);z['large_create']=((z.net_baskets>=10)&(z.net_flow_pct>=.5)).astype(int)
    correlations=[]
    for f in BASE+L2:
        def corr(z):return z[f].corr(z.net_flow_pct,method='spearman') if z[f].nunique()>1 else np.nan
        bydate=tr.groupby('date').apply(corr)
        low,high=tr[f].quantile([.25,.75]);lo=te[te[f]<=low];hi=te[te[f]>=high]
        correlations.append(dict(factor=f,family='l2' if f in L2 else 'baseline',train_rho=corr(tr),train_daily_rho=bydate.mean(),train_daily_positive_frac=(bydate.dropna()>0).mean(),validation_rho=corr(va),test_rho=corr(te),train_q25=low,train_q75=high,test_bottom_n=len(lo),test_top_n=len(hi),test_bottom_create=lo.create.mean(),test_top_create=hi.create.mean(),test_bottom_redeem=lo.redeem.mean(),test_top_redeem=hi.redeem.mean()))
    c=pd.DataFrame(correlations);c.to_csv(O/'factor_correlations.csv',index=False)
    # Use prespecified economic families for compact incremental model; no test-driven factor selection.
    compact=['l2_active_imbalance','l2_sell_large_parent_frac','l2_buy_large_parent_frac','l2_sell_unit_parent_excess','l2_buy_unit_parent_excess','l2_sell_compression_frac']
    sets={'baseline':BASE,'l2_only':L2,'baseline_l2_compact':BASE+compact,'baseline_l2_all':BASE+L2}
    results=[];scores=[];coefficients=[];rules=[];rolling=[]
    for target in ['create','redeem','large_create']:
        for name,cols in sets.items():
            m=model();m.fit(tr[cols],tr[target]);pv=m.predict_proba(va[cols])[:,1];pt=m.predict_proba(te[cols])[:,1]
            results.append(dict(target=target,model=name,split='validation',**metrics(va[target],pv)))
            results.append(dict(target=target,model=name,split='test',**metrics(te[target],pt)))
            for split,z,pred in [('validation',va,pv),('test',te,pt)]:
                zz=z[['date','symbol','net_baskets','net_flow_pct',target]].copy();zz['target']=target;zz['label']=z[target];zz['model']=name;zz['split']=split;zz['score']=pred;scores.append(zz.drop(columns=[target]))
            imp=m.named_steps['simpleimputer'];names=imp.get_feature_names_out(cols);co=m.named_steps['logisticregression'].coef_[0]
            coefficients.extend(dict(target=target,model=name,factor=f,coef=float(v)) for f,v in zip(names,co))
            # Threshold selected on validation only, min10 signals,>=2 dates; absent if none qualifies.
            for goal in [.8,.9]:
                candidates=[]
                for q in np.arange(.1,.951,.025):
                    ix=pv>=q
                    if ix.sum()>=10 and va.loc[ix,'date'].nunique()>=2 and va.loc[ix,'symbol'].nunique()>=3 and va.loc[ix,target].mean()>=goal:candidates.append((int(ix.sum()),float(q)))
                if not candidates:
                    rules.append(dict(target=target,model=name,goal=goal,status='no_validation_rule'));continue
                _,threshold=max(candidates);ix=pt>=threshold;iv=pv>=threshold
                rules.append(dict(target=target,model=name,goal=goal,status='selected',threshold=threshold,val_n=int(iv.sum()),val_precision=va.loc[iv,target].mean(),test_n=int(ix.sum()),test_precision=te.loc[ix,target].mean() if ix.any() else None,test_dates=te.loc[ix,'date'].nunique(),test_funds=te.loc[ix,'symbol'].nunique()))
            if name in ['baseline','baseline_l2_compact','baseline_l2_all']:
                for i in range(4,len(dates)):
                    train=d[d.date.isin(dates[:i])];test=d[d.date.eq(dates[i])]
                    mm=model();mm.fit(train[cols],train[target]);pred=mm.predict_proba(test[cols])[:,1]
                    rolling.extend(dict(date=row.date,symbol=row.symbol,target=target,model=name,label=int(row[target]),score=float(p)) for (_,row),p in zip(test.iterrows(),pred))
    scores=pd.concat(scores,ignore_index=True);scores.to_csv(O/'holdout_scores.csv',index=False)
    pd.DataFrame(results).to_csv(O/'model_comparison.csv',index=False);pd.DataFrame(coefficients).to_csv(O/'coefficients.csv',index=False);pd.DataFrame(rules).to_csv(O/'validation_rules.csv',index=False)
    roll=pd.DataFrame(rolling);roll.to_csv(O/'rolling_scores.csv',index=False)
    roll_metrics=[dict(target=t,model=m,**metrics(z.label,z.score)) for (t,m),z in roll.groupby(['target','model'])]
    pd.DataFrame(roll_metrics).to_csv(O/'rolling_metrics.csv',index=False)
    # Paired date-block bootstrap, shared dates across all funds; 8 dates still a small sample.
    rng=np.random.default_rng(20260912);ci=[]
    for target in ['create','redeem','large_create']:
        z=roll[roll.target.eq(target)].pivot(index=['date','symbol','label'],columns='model',values='score').reset_index();ds=sorted(z.date.unique())
        for name in ['baseline_l2_compact','baseline_l2_all']:
            vals=[]
            for _ in range(1000):
                zz=pd.concat([z[z.date.eq(dt)] for dt in rng.choice(ds,len(ds),replace=True)])
                vals.append(average_precision_score(zz.label,zz[name])-average_precision_score(zz.label,zz.baseline))
            ci.append(dict(target=target,model=name,ap_gain=average_precision_score(z.label,z[name])-average_precision_score(z.label,z.baseline),ci_low=float(np.quantile(vals,.025)),ci_high=float(np.quantile(vals,.975)),dates=len(ds)))
    pd.DataFrame(ci).to_csv(O/'rolling_ap_gain_bootstrap.csv',index=False)
    # Net flow quantity regression, fixed feature sets and train/validation/test split.
    reg=[]
    for name,cols in sets.items():
        mm=make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),Ridge(alpha=100))
        mm.fit(tr[cols],tr.net_flow_pct.clip(-10,10));pp=mm.predict(te[cols]);reg.append(dict(model=name,mae_flow_pct=mean_absolute_error(te.net_flow_pct,pp),rho=pd.Series(pp).corr(te.net_flow_pct.reset_index(drop=True),method='spearman')))
    pd.DataFrame(reg).to_csv(O/'quantity_regression.csv',index=False)
    report=dict(raw_rows=len(raw),quality_rows=len(d),funds=d.symbol.nunique(),dates=dates,train_dates=train_dates,validation_dates=val_dates,test_dates=test_dates,split_rows=[len(tr),len(va),len(te)],class_counts={k:int(d[k].sum()) for k in ['create','redeem','large_create']},fx_gap_quantiles=d.fx_gap_bp.quantile([0,.5,1]).to_dict(),l2_features=L2,baseline_features=BASE,compact_features=compact,unit_null='0.8/1.2/1.3 times unit rounded to100; diagnostic sensitivity, not a formal randomization p-value',limits=['Source suffixes corrected using six-digit ETF codes; SH/SZ lifecycle rules differ','Final daily FX and full-day paths are ex-post','Net flow is a proxy target, not settlement P&L','Date holdout small; 90% not established'])
    (O/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
