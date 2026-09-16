"""固定日终汇率口径的条件关联研究：时间切分、训练/验证选条件、末季留出。
不是实际收益回测，也不估计不可观测双边申赎量。
"""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score, brier_score_loss

ROOT=Path(__file__).resolve().parent;OUT=ROOT/'results'

def describe(d):
    if not len(d):return dict(n=0,funds=0,days=0)
    return dict(n=len(d),funds=d.symbol.nunique(),days=d.date.nunique(),
        positive_rate=float((d.net_baskets>0.01).mean()),negative_rate=float((d.net_baskets<-.01).mean()),
        large_rate=float(d.large.mean()),median_net_baskets=float(d.net_baskets.median()),
        mean_net_flow_pct=float(d.net_flow_pct.mean()),median_net_flow_pct=float(d.net_flow_pct.median()))

def rules(d):
    result={'all':np.ones(len(d),dtype=bool)}
    for p in [0,10,30,50,100]:
        result[f'settlement_mean_gt_{p}bp']=d.settlement_mean_bp>p
        result[f'mid_mean_gt_{p}bp']=d.mid_mean_bp>p
    for f in [.5,.8,.95]:
        result[f'settlement_above30_fraction_ge_{f}']=d.settlement_above30_fraction>=f
    for t in [.1,.5,1,3]:
        result[f'turnover_ge_{t}pct']=d.turnover_pct>=t
        result[f'mean30_duration80_turnover{t}']=((d.settlement_mean_bp>30)&
            (d.settlement_above30_fraction>=.8)&(d.turnover_pct>=t))
    result['between_two_iopv']=((d.settlement_mean_bp>0)&(d.mid_mean_bp<0))
    result['between_two_iopv_duration80']=result['between_two_iopv']&(d.settlement_positive_fraction>=.8)
    result['positive_yesterday']=d.lag_flow_pct>0
    result['mean30_and_positive_yesterday']=(d.settlement_mean_bp>30)&(d.lag_flow_pct>0)
    result['mean30_and_basket_rise']=(d.settlement_mean_bp>30)&(d.basket_return_bp>0)
    return result

def bootstrap_rate(d,iterations=1000):
    grouped=d.groupby('date').large.agg(['sum','count']).to_numpy()
    if len(grouped)<2:return [None,None]
    rng=np.random.default_rng(20260912)
    indices=rng.integers(0,len(grouped),(iterations,len(grouped)))
    sums=grouped[indices].sum(axis=1);rates=sums[:,0]/sums[:,1]
    return np.quantile(rates,[.025,.975]).tolist()

def main():
    panel=pd.read_parquet(OUT/'factor_panel.parquet').sort_values(['date','symbol','cut'])
    assert not panel.duplicated(['symbol','date','cut']).any()
    panel['large']=(panel.net_baskets>=10)&(panel.net_flow_pct>=.5)
    panel['split']=np.where(panel.date<'2026-01-01','train',np.where(panel.date<'2026-04-01','validation','test'))
    baselines=[];rule_rows=[];factor_rows=[];model_rows=[];chosen=[];predictions=[];buckets=[]
    feature_names=[c for c in panel.columns if c not in ['symbol','date','cut','unit','net_baskets','net_flow_pct',
        'large','split','minute_coverage','valid_minutes'] and not c.startswith(('expost','diagnostic_'))]
    for cut,df in panel.groupby('cut'):
        split={k:df[df.split==k].copy() for k in ['train','validation','test']}
        for k,d in split.items():baselines.append(dict(cut=cut,split=k,**describe(d)))
        train,val,test=(split[k] for k in ['train','validation','test'])
        for feature in feature_names:
            corr=train[feature].corr(train.net_flow_pct,method='spearman')
            # 固定训练期分位数边界用于各段，防止事后重新切桶。
            edges=np.unique(train[feature].dropna().quantile([0,.2,.4,.6,.8,1]).to_numpy())
            if len(edges)<3:continue
            edges[0]=-np.inf;edges[-1]=np.inf
            for k,d in split.items():
                factor_rows.append(dict(cut=cut,feature=feature,split=k,
                    spearman_flow_pct=float(d[feature].corr(d.net_flow_pct,method='spearman')),
                    train_direction=float(np.sign(corr)) if np.isfinite(corr) else 0))
                bucket=pd.cut(d[feature],edges,include_lowest=True)
                for bucket_id,dd in d.groupby(bucket,observed=True):
                    buckets.append(dict(cut=cut,feature=feature,split=k,bucket=str(bucket_id),**describe(dd)))
        for k,d in split.items():
            for name,mask in rules(d).items():rule_rows.append(dict(cut=cut,split=k,rule=name,**describe(d[mask])))
        # 按验证期条件命中率挑选；至少100样本、10基金、20交易日、训练期50样本。
        vc=[r for r in rule_rows if r['cut']==cut and r['split']=='validation' and r['rule']!='all'
            and r['n']>=100 and r['funds']>=10 and r['days']>=20]
        tc={r['rule']:r for r in rule_rows if r['cut']==cut and r['split']=='train'}
        vc=[r for r in vc if tc[r['rule']]['n']>=50]
        vc.sort(key=lambda r:r['large_rate'],reverse=True)
        if vc:
            winner=vc[0]['rule'];held=test[rules(test)[winner]]
            chosen.append(dict(cut=cut,kind='rule',name=winner,validation=vc[0],test=describe(held),
                               test_large_rate_ci=bootstrap_rate(held)))
        if train.large.nunique()<2 or val.large.nunique()<2:continue
        models={
            'logistic':make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),
                                    LogisticRegression(C=.1,max_iter=1000)),
            'hist_gradient':HistGradientBoostingClassifier(max_iter=120,max_leaf_nodes=7,
                    learning_rate=.06,l2_regularization=10,min_samples_leaf=80,random_state=20260912)}
        scores={}
        for name,model in models.items():
            model.fit(train[feature_names],train.large)
            vp=model.predict_proba(val[feature_names])[:,1]
            threshold=float(np.quantile(vp,.9))
            scores[name]=average_precision_score(val.large,vp)
            for k,d in [('validation',val),('test',test)]:
                prob=model.predict_proba(d[feature_names])[:,1];selection=prob>=threshold
                model_rows.append(dict(cut=cut,model=name,split=k,pr_auc=float(average_precision_score(d.large,prob)),
                    roc_auc=float(roc_auc_score(d.large,prob)),brier=float(brier_score_loss(d.large,prob)),
                    threshold=threshold,**{'selected_'+key:v for key,v in describe(d[selection]).items()}))
                if k=='test':predictions.append(d[['symbol','date','cut','net_baskets','net_flow_pct','large']].assign(
                    model=name,probability=prob,selected=selection))
        winner=max(scores,key=scores.get);model=models[winner];prob=model.predict_proba(test[feature_names])[:,1]
        threshold=float(np.quantile(model.predict_proba(val[feature_names])[:,1],.9))
        held=test[prob>=threshold]
        chosen.append(dict(cut=cut,kind='model',name=winner,test=describe(held),
            test_large_rate_ci=bootstrap_rate(held),threshold=threshold))
    pd.DataFrame(baselines).to_csv(OUT/'baselines.csv',index=False)
    pd.DataFrame(rule_rows).to_csv(OUT/'rules.csv',index=False)
    pd.DataFrame(factor_rows).to_csv(OUT/'factor_correlations.csv',index=False)
    pd.DataFrame(buckets).to_csv(OUT/'factor_buckets.csv',index=False)
    pd.DataFrame(model_rows).to_csv(OUT/'models.csv',index=False)
    (OUT/'selected_conditions.json').write_text(json.dumps(chosen,ensure_ascii=False,indent=2))
    preds=pd.concat(predictions,ignore_index=True);preds.to_parquet(OUT/'test_predictions.parquet',index=False)
    pd.DataFrame([dict(cut=c,month=m,**describe(d)) for (c,m),d in
        panel.assign(month=panel.date.str[:7]).groupby(['cut','month'])]).to_csv(OUT/'monthly_baselines.csv',index=False)
    # 仅对验证期选定的条件作预先列出的标签敏感性检查，不能据测试期结果反选门槛。
    sensitivity=[];funds=[];months=[]
    for choice in chosen:
        c=choice['cut'];d=panel[(panel.cut==c)&(panel.split=='test')].copy()
        if choice['kind']=='rule': mask=np.asarray(rules(d)[choice['name']])
        else:
            p=preds[(preds.cut==c)&(preds.model==choice['name'])]
            d=d.merge(p[['date','symbol','selected']],on=['date','symbol'],validate='one_to_one');mask=d.selected
        for n,pct in [(5,.2),(10,.5),(20,1)]:
            d['large']=(d.net_baskets>=n)&(d.net_flow_pct>=pct)
            sensitivity.append(dict(cut=c,kind=choice['kind'],name=choice['name'],basket_threshold=n,
                pct_threshold=pct,base_rate=float(d.large.mean()),**describe(d[mask])))
        d['large']=(d.net_baskets>=10)&(d.net_flow_pct>=.5);selected=d[mask]
        for sym,dd in selected.groupby('symbol'):funds.append(dict(cut=c,kind=choice['kind'],symbol=sym,**describe(dd)))
        for month,dd in selected.groupby(selected.date.str[:7]):months.append(dict(cut=c,kind=choice['kind'],month=month,**describe(dd)))
    pd.DataFrame(sensitivity).to_csv(OUT/'label_sensitivity.csv',index=False)
    pd.DataFrame(funds).to_csv(OUT/'selected_by_fund.csv',index=False)
    pd.DataFrame(months).to_csv(OUT/'selected_by_month.csv',index=False)
    print('features',feature_names)
    print(pd.DataFrame(baselines).to_string(index=False))
    print(json.dumps(chosen,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
