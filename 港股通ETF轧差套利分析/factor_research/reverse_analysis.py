"""按训练标签反查因子，再冻结方案、跨期检验。最终July段不参与选择。"""
from pathlib import Path
import json,joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor
from sklearn.metrics import average_precision_score,roc_auc_score,brier_score_loss,mean_absolute_error
from reverse_features import cohort
R=Path(__file__).resolve().parent;D=R/'reverse';O=D/'results';O.mkdir(exist_ok=True)
SEED=20260912

def enrich(d):
    d=d.sort_values(['symbol','date']).reset_index(drop=True)
    for col,new in [('settlement_mean_bp','premium_vs_20d_bp'),('turnover_pct','relative_turnover_20d')]:
        history=d.groupby('symbol')[col].transform(lambda s:s.shift(1).rolling(20,min_periods=5).median())
        d[new]=d[col]-history if col=='settlement_mean_bp' else d[col]/history.where(history>1e-6)
    d['within_day_premium_rank']=d.groupby('date').settlement_mean_bp.rank(pct=True)
    d['basket_excess_market_bp']=d.basket_return_bp-d.groupby('date').basket_return_bp.transform('median')
    d['pressure_balance']=d.creation_pressure-d.redemption_pressure
    d['flow_positive']=(d.net_baskets>.01).astype(int)
    d['large_positive']=((d.net_baskets>=10)&(d.net_flow_pct>=.5)).astype(int)
    d['large_negative']=((d.net_baskets<=-10)&(d.net_flow_pct<=-.5)).astype(int)
    d['cohort']=cohort(d)
    assert not d.duplicated(['symbol','date']).any()
    return d.sort_values(['date','symbol']).reset_index(drop=True)

def model():return HistGradientBoostingClassifier(max_iter=100,max_leaf_nodes=7,learning_rate=.06,
      min_samples_leaf=80,l2_regularization=10,early_stopping=False,random_state=SEED)

def stats(d):
    return dict(n=len(d),funds=d.symbol.nunique(),days=d.date.nunique(),positive_rate=float(d.flow_positive.mean()),
      large_rate=float(d.large_positive.mean()),negative_rate=float((d.net_baskets<-.01).mean()),
      large_redemption_rate=float(d.large_negative.mean()),median_baskets=float(d.net_baskets.median()),median_flow_pct=float(d.net_flow_pct.median()))

def metrics(d,prob,target,threshold):
    y=d[target].to_numpy();sel=prob>=threshold;selected=d[sel]
    return dict(target=target,base_rate=float(y.mean()),ap=float(average_precision_score(y,prob)),
       roc_auc=float(roc_auc_score(y,prob)) if len(np.unique(y))>1 else np.nan,
       brier=float(brier_score_loss(y,prob)),score_mean=float(np.mean(prob)),threshold=float(threshold),
       precision=float(y[sel].mean()) if sel.sum() else np.nan,recall=float(y[sel].sum()/y.sum()) if y.sum() else np.nan,
       selected_fraction=float(sel.mean()),**stats(selected))

def ci(d,target):
    a=d.groupby('date')[target].agg(['sum','count']).to_numpy()
    if len(a)<2:return [None,None]
    rng=np.random.default_rng(SEED);v=a[rng.integers(len(a),size=(1500,len(a)))].sum(axis=1)
    return np.quantile(v[:,0]/v[:,1],[.025,.975]).tolist()

def rule_masks(d,cutoffs):
    return dict(old_mean30_lag=(d.settlement_mean_bp>30)&(d.lag_flow_pct>0),
      mean30=d.settlement_mean_bp>30,
      duration80=d.settlement_above30_fraction>=.8,
      positive_money80=d.positive_amount_share>=.8,
      pressure_high=d.creation_pressure>=cutoffs['pressure80'],
      pressure_high_lag=(d.creation_pressure>=cutoffs['pressure80'])&(d.lag_flow_pct>0),
      mean30_money80=(d.settlement_mean_bp>30)&(d.positive_amount_share>=.8),
      mean30_money80_lag=(d.settlement_mean_bp>30)&(d.positive_amount_share>=.8)&(d.lag_flow_pct>0),
      relative_premium10=d.premium_vs_20d_bp>10,
      relative10_money80=(d.premium_vs_20d_bp>10)&(d.positive_amount_share>=.8),
      relative10_money80_lag=(d.premium_vs_20d_bp>10)&(d.positive_amount_share>=.8)&(d.lag_flow_pct>0),
      compression_high=(d.compression_turnover_pct>cutoffs['compression80'])&(d.compression_turnover_pct>0),
      compression_high_mean30=(d.compression_turnover_pct>cutoffs['compression80'])&(d.settlement_mean_bp>30),
      low_mid_high_settle=(d.mid_mean_bp<0)&(d.settlement_mean_bp>0))

def main():
    base=pd.read_parquet(D/'base_features.parquet');ext=pd.read_parquet(D/'extension_features.parquet')
    d=enrich(pd.concat([base,ext],ignore_index=True));d.to_parquet(O/'enriched_panel.parquet',index=False)
    train=d[d.date<'2026-01-01'];val=d[d.date.between('2026-01-01','2026-03-31')]
    old=d[d.date.between('2026-04-01','2026-06-30')];fresh=d[d.date>'2026-06-30']
    excluded=['symbol','date','cut','unit','net_baskets','net_flow_pct','minute_coverage','valid_minutes','flow_positive','large_positive','large_negative','cohort']
    candidates=[c for c in base.columns if c not in excluded and not c.startswith(('diagnostic_','expost'))]
    relative=['premium_vs_20d_bp','relative_turnover_20d','within_day_premium_rank','basket_excess_market_bp','pressure_balance']
    candidates+=relative
    original=pd.read_parquet(R/'results/factor_panel.parquet');prior=[c for c in original.columns if c not in excluded and not c.startswith(('diagnostic_','expost'))]
    history=['lag_flow_pct','lag5_flow_pct','log_prev_assets']
    simple=history+['settlement_mean_bp','settlement_p10_bp','settlement_positive_fraction','mid_mean_bp','fx_gap_bp','turnover_pct']
    pressure=['positive_amount_share','negative_amount_share','creation_pressure','redemption_pressure','compression_turnover_pct','expansion_turnover_pct','high_premium_amount_share','low_premium_amount_share','pressure_balance']
    # 稳定性只用2025训练期：月度相关符号至少2/3一致，整体|rho|>=.08。
    factor_rows=[];bucket_rows=[];cohort_rows=[];stable=[]
    for f in candidates:
        rho=train[f].corr(train.net_flow_pct,method='spearman')
        monthly=[g[f].corr(g.net_flow_pct,method='spearman') for _,g in train.groupby(train.date.str[:7])]
        agree=float(np.mean(np.sign(monthly)==np.sign(rho)))
        selected=np.isfinite(rho) and abs(rho)>=.08 and agree>=2/3
        if selected:stable.append(f)
        within=train[f]-train.groupby('symbol')[f].transform('median')
        ywithin=train.net_flow_pct-train.groupby('symbol').net_flow_pct.transform('median')
        daily=[g[f].corr(g.net_flow_pct,method='spearman') for _,g in train.groupby('date') if len(g)>=30]
        factor_rows.append(dict(feature=f,train_rho=rho,monthly_sign_agreement=agree,within_fund_rho=within.corr(ywithin,method='spearman'),
            median_daily_rho=float(np.nanmedian(daily)),stable=selected,validation_rho=val[f].corr(val.net_flow_pct,method='spearman')))
        edges=np.unique(train[f].dropna().quantile([0,.2,.4,.6,.8,1]))
        if len(edges)>2:
            edges[0]=-np.inf;edges[-1]=np.inf
            for period,g in [('train',train),('validation',val)]:
                for bucket,sub in g.groupby(pd.cut(g[f],edges,include_lowest=True),observed=True):
                    bucket_rows.append(dict(feature=f,period=period,bucket=str(bucket),**stats(sub)))
        for group,g in train.groupby('cohort'):
            q=g[f].quantile([.25,.5,.75]);cohort_rows.append(dict(feature=f,cohort=group,n=len(g),q25=q.iloc[0],median=q.iloc[1],q75=q.iloc[2]))
    pd.DataFrame(factor_rows).sort_values('train_rho',ascending=False).to_csv(O/'reverse_factor_evidence.csv',index=False)
    pd.DataFrame(bucket_rows).to_csv(O/'discovery_buckets.csv',index=False);pd.DataFrame(cohort_rows).to_csv(O/'reverse_cohorts.csv',index=False)
    groups={'history_only':history,'simple_levels':simple,'prior_full':prior,'enriched':list(dict.fromkeys(stable+history)),
            'enriched_no_history':[f for f in stable if f not in history],
            'enriched_no_pressure':[f for f in list(dict.fromkeys(stable+history)) if f not in pressure],
            'enriched_no_relative':[f for f in list(dict.fromkeys(stable+history)) if f not in relative]}
    cutoffs=dict(pressure80=float(train.creation_pressure.quantile(.8)),compression80=float(train.compression_turnover_pct.quantile(.8)))
    # 到此冻结全部配置，尚未计算新样本结果。
    (O/'frozen_spec.json').write_text(json.dumps(dict(discovery_end='2025-12-31',validation_end='2026-03-31',
        fit_end='2026-05-31',threshold_calibration='2026-06',fresh_start='2026-07-01',groups=groups,cutoffs=cutoffs,
        classifier='HGB 100 iterations 7 leaves min_leaf80 l2=10; no tuning on fresh dates',
        caution='Jan-Jun previously inspected; only July/Aug newly examined this turn'),ensure_ascii=False,indent=2))
    print('frozen',len(stable),'stable of',len(candidates),'fresh rows',len(fresh),flush=True)
    # 规则仅按Jan-Mar选，然后冻结评估其余时间。
    rules=[];winners={}
    for period,g in [('train',train),('validation',val)]:
        for name,mask in rule_masks(g,cutoffs).items():rules.append(dict(period=period,rule=name,**stats(g[mask])))
    for target,metric in [('flow_positive','positive_rate'),('large_positive','large_rate')]:
        valid=[x for x in rules if x['period']=='validation' and x['n']>=100 and x['funds']>=10 and x['days']>=20]
        winners[target]=max(valid,key=lambda x:x[metric])['rule']
    (O/'frozen_rule_selection.json').write_text(json.dumps(winners,indent=2))
    for period,g in [('historical_reused',old),('fresh',fresh)]:
        for name,mask in rule_masks(g,cutoffs).items():rules.append(dict(period=period,rule=name,**stats(g[mask])))
    pd.DataFrame(rules).to_csv(O/'rules.csv',index=False)
    # 最终模型统一使用截至5月的训练，6月评分90分位数作冻结筛选阈值。
    fit=d[d.date<'2026-06-01'];cal=d[d.date.between('2026-06-01','2026-06-30')]
    models=[];predictions=[];fitted={};intervals=[];calibration=[]
    for target in ['flow_positive','large_positive','large_negative']:
        for name,features in groups.items():
            m=model();m.fit(fit[features],fit[target]);cp=m.predict_proba(cal[features])[:,1];th=float(np.quantile(cp,.9))
            for period,g in [('calibration',cal),('fresh',fresh)]:
                prob=m.predict_proba(g[features])[:,1];models.append(dict(model=name,period=period,**metrics(g,prob,target,th)))
                if period=='fresh':
                    predictions.append(g[['date','symbol','net_baskets','net_flow_pct',target]].rename(columns={target:'actual'}).assign(target=target,model=name,score=prob,selected=prob>=th))
                    if name in ['enriched','simple_levels','prior_full']:
                        intervals.append(dict(target=target,model=name,precision_ci=ci(g[prob>=th],target)))
                        deciles=pd.qcut(pd.Series(prob,index=g.index).rank(method='first'),10,labels=False)
                        for k,sub in g.assign(prob=prob,decile=deciles).groupby('decile'):
                            calibration.append(dict(target=target,model=name,decile=int(k),n=len(sub),mean_score=sub.prob.mean(),observed=sub[target].mean()))
            fitted[(target,name)]=(m,features,th)
        print('fitted',target,flush=True)
    pd.DataFrame(models).to_csv(O/'forward_models.csv',index=False);pd.concat(predictions).to_parquet(O/'fresh_predictions.parquet',index=False)
    (O/'precision_intervals.json').write_text(json.dumps(intervals,indent=2));pd.DataFrame(calibration).to_csv(O/'score_calibration.csv',index=False)
    # 保存固定模型，便于后续相同输入直接推算；不拿新标签重新训练。
    joblib.dump(fitted,D/'frozen_models.joblib')
    # 历史扩展窗口正向检验：训练截止前2个月、上月定阈值、当月检验。
    rolling=[]
    for month in pd.period_range('2026-01','2026-06',freq='M'):
        current=str(month);previous=str(month-1)
        tr=d[d.date.str[:7]<previous];ca=d[d.date.str[:7]==previous];te=d[d.date.str[:7]==current]
        for target in ['flow_positive','large_positive']:
            for name in ['history_only','simple_levels','enriched']:
                fs=groups[name];m=model();m.fit(tr[fs],tr[target]);th=float(np.quantile(m.predict_proba(ca[fs])[:,1],.9))
                pr=m.predict_proba(te[fs])[:,1];rolling.append(dict(month=current,model=name,**metrics(te,pr,target,th)))
        print('rolling',current,flush=True)
    pd.DataFrame(rolling).to_csv(O/'rolling_months.csv',index=False)
    # 预测数量：直接回归净份额百分比；净篮子由预测百分比×当日已知规模/单位换算。
    reg=[];regpred=[]
    for name in ['simple_levels','enriched']:
        fs=groups[name];m=HistGradientBoostingRegressor(loss='absolute_error',max_iter=100,max_leaf_nodes=7,min_samples_leaf=80,l2_regularization=10,early_stopping=False,random_state=SEED)
        m.fit(fit[fs],fit.net_flow_pct);pr=m.predict(fresh[fs])
        # ratio可直接从known昨日shares/unit推导；此处用标签比例仅做恒等式重建，非模型输入。
        # 改为读取缓存份额以排除零净量日的0/0。
        ratios=[]
        cache={}
        for _,row in fresh.iterrows():
            sym=row.symbol
            if sym not in cache:
                rec=json.loads((R/'inputs/share_history'/(sym+'.json')).read_text())['rows'];cache[sym]={x['share_date']:x for x in rec}
            r=cache[sym][row.date];prev=r.get('previous_shares_10k')
            if prev is None:prev=r['shares_10k']-r['share_change_10k']
            ratios.append(prev*10000/row.unit/100)
        predicted_baskets=pr*np.array(ratios)
        for baseline,pct in [(name,pr),('zero',np.zeros(len(fresh))),('yesterday',fresh.lag_flow_pct.fillna(0).to_numpy())]:
            reg.append(dict(model=baseline,flow_pct_mae=mean_absolute_error(fresh.net_flow_pct,pct),baskets_mae=mean_absolute_error(fresh.net_baskets,pct*np.array(ratios)),
              flow_spearman=pd.Series(pct,index=fresh.index).corr(fresh.net_flow_pct,method='spearman')))
        regpred.append(fresh[['date','symbol','net_baskets','net_flow_pct']].assign(model=name,predicted_flow_pct=pr,predicted_baskets=predicted_baskets))
    pd.DataFrame(reg).drop_duplicates('model').to_csv(O/'quantity_regression.csv',index=False);pd.concat(regpred).to_csv(O/'quantity_predictions.csv',index=False)
    # 新样本中的单因子方向，作为诊断展示，不再反向改模型。
    post=[]
    for f in candidates:post.append(dict(feature=f,fresh_rho=fresh[f].corr(fresh.net_flow_pct,method='spearman')))
    pd.DataFrame(post).to_csv(O/'fresh_factor_direction.csv',index=False)
    # 分基金与分月观察集中度，避免只靠少数标的。
    byfund=[];bymonth=[]
    for target in ['flow_positive','large_positive']:
        m,fs,th=fitted[(target,'enriched')];g=fresh.assign(score=m.predict_proba(fresh[fs])[:,1]);g=g[g.score>=th]
        for symbol,sub in g.groupby('symbol'):byfund.append(dict(target=target,symbol=symbol,**stats(sub)))
        for month,sub in g.groupby(g.date.str[:7]):bymonth.append(dict(target=target,month=month,**stats(sub)))
    pd.DataFrame(byfund).to_csv(O/'selected_by_fund.csv',index=False);pd.DataFrame(bymonth).to_csv(O/'selected_by_month.csv',index=False)
    baseline=[dict(period=k,**stats(g)) for k,g in [('discovery',train),('validation',val),('historical_reused',old),('fresh',fresh)]]
    pd.DataFrame(baseline).to_csv(O/'baselines.csv',index=False)
    print(pd.DataFrame(models).query("period=='fresh' and model in ['history_only','simple_levels','prior_full','enriched']").to_string(index=False),flush=True)
    print(pd.DataFrame(reg).drop_duplicates('model').to_string(index=False),flush=True)

if __name__=='__main__':main()
