"""两阶段：用广样本识别净申购，再用经济条件过滤；记录首轮联合门槛样本不足。"""
import json,shutil
import numpy as np,pandas as pd
from profit_research import data,pnl,stats,interval,D

def rules(d):
    out={};op=(d.creation_allowed==1)&(d.redemption_allowed==1)
    for m in [20,30,40,50,70,100]:
        for lag in [0,.5,1]:
            for q in [0,1,2]:
                quality=np.ones(len(d),bool) if q==0 else (d.p10_bp>(0 if q==1 else 20))&(d.money_positive_share>=(.8 if q==1 else .95))
                out[f'mean{m}_lag{lag}_quality{q}']=op&(d.mean_bp>m)&(d.lag_flow_pct>lag)&quality
    return out

def wilson(w,n):
    if not n:return [None,None]
    p=w/n;z=1.96;den=1+z*z/n;c=(p+z*z/(2*n))/den;delta=z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [float(c-delta),float(c+delta)]

def main():
    first=D/'joint_gate_attempt';first.mkdir(exist_ok=True)
    for f in ['rule_search.csv','score_search.csv','choices.json','chosen_summary.csv']:
        if (D/f).exists() and not (first/f).exists():shutil.copy(D/f,first/f)
    d=data();splits={'train':d[d.date<'2026-01-01'],'validation':d[d.date.between('2026-01-01','2026-03-31')],
        'apr_jun':d[d.date.between('2026-04-01','2026-06-30')],'jul_aug':d[d.date>'2026-06-30'],'later_apr_jul':d[d.date>='2026-04-01']}
    rr=[]
    for period,g in splits.items():
        for name,mask in rules(g).items():rr.append(dict(period=period,rule=name,**stats(g[mask])))
    rr=pd.DataFrame(rr);rr.to_csv(D/'two_stage_direction_search.csv',index=False)
    tr=rr[rr.period=='train'].set_index('rule');v=rr[(rr.period=='validation')&(rr.n>=40)&(rr.funds>=8)&(rr.days>=10)].copy()
    v=v[(v.rule.map(tr.n)>=100)&(v.rule.map(tr.direction_win)>=.9)]
    eligible=v[v.direction_win>=.9]
    if len(eligible):w=eligible.sort_values(['n','direction_win'],ascending=False).iloc[0];name=w.rule
    else:
        v=rr[(rr.period=='validation')&(rr.n>=40)&(rr.funds>=8)&(rr.days>=10)].copy();v=v[v.rule.map(tr.n)>=100]
        w=v.sort_values(['direction_win','n'],ascending=False).iloc[0];name=w.rule
    selected_rule=dict(rule=name,meets_train_val_90=bool(len(eligible)),validation=w.to_dict(),economic_gap_bp=60,
        selection='train >=100, validation >=40, >=8funds >=10days; both direction rates>=90%; choose maximum validation coverage',
        warning='joint FX gate gave no train-supported candidate; two-stage is an exploratory revision, not untouched holdout')
    (D/'two_stage_choice.json').write_text(json.dumps(selected_rule,indent=2))
    sums=[];sels=[];sens=[];port=[]
    for period,g in splits.items():
        h=g[rules(g)[name]]
        for stage,x in [('direction_only',h),('economic_gate',h[h.fx_gap_bp>=60])]:
            p=pnl(x);sums.append(dict(period=period,stage=stage,**stats(x),scenario_win_wilson=wilson(int((p.net_yuan>0).sum()),len(x)),direction_win_wilson=wilson(int((x.net_baskets>0).sum()),len(x))))
        h=h[h.fx_gap_bp>=60]
        if period not in ['apr_jun','jul_aug'] or not len(h):continue
        sels.append(h.assign(period=period))
        for r in [0,1,4,9]:
            for fee in [5,10,20,40]:sens.append(dict(period=period,opposite_ratio=r,fee_bp=fee,own_baskets=1,**stats(h,r,fee)))
        for own in [1,5,10,50]:sens.append(dict(period=period,opposite_ratio=1,fee_bp=10,own_baskets=own,**stats(h,1,10,own)))
    pd.DataFrame(sums).to_csv(D/'two_stage_summary.csv',index=False);pd.DataFrame(sens).to_csv(D/'two_stage_sensitivity.csv',index=False)
    held=pd.concat(sels).sort_values(['date','symbol']) if sels else d.iloc[:0]
    held.to_csv(D/'two_stage_selected.csv',index=False);kept=[];counts=[]
    for date,g in held.groupby('date'):
        history=d[d.date<date];dates=sorted(history.date.unique())[-60:]
        matrix=history[history.date.isin(dates)].pivot(index='date',columns='symbol',values='basket_return_bp');corr=matrix.corr(min_periods=20)
        taken=[]
        for ix,row in g.sort_values('mean_bp',ascending=False).iterrows():
            if all(row.symbol not in corr or s not in corr or not np.isfinite(corr.loc[row.symbol,s]) or corr.loc[row.symbol,s]<.85 for s in taken):taken.append(row.symbol);kept.append(ix)
        counts.append(dict(date=date,all=len(g),kept=len(taken)))
    for mode,h in [('all',held),('corr85_one',held.loc[kept])]:
        if not len(h):continue
        p=pnl(h);daily=h[['date','B']].join(p).groupby('date').agg(n=('B','size'),notional=('B','sum'),net_yuan=('net_yuan','sum'))
        daily['return_bp']=daily.net_yuan/daily.notional*10000;daily.to_csv(D/f'two_stage_portfolio_{mode}.csv')
        port.append(dict(mode=mode,**stats(h),portfolio_worst_bp=float(daily.return_bp.min()),portfolio_mean_bp=float(daily.return_bp.mean()),
            median_count=float(daily.n.median()),max_count=int(daily.n.max()),loss_days=int((daily.net_yuan<0).sum()),
            day_win_wilson=wilson(int((daily.net_yuan>0).sum()),len(daily))))
    pd.DataFrame(port).to_csv(D/'two_stage_portfolios.csv',index=False);pd.DataFrame(counts).to_csv(D/'two_stage_diversification.csv',index=False)
    print(json.dumps(selected_rule),flush=True);print(pd.DataFrame(sums).to_string(index=False),flush=True);print(pd.DataFrame(port).to_string(index=False),flush=True)
if __name__=='__main__':main()
