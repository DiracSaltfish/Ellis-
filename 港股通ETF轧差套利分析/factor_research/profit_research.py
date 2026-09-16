"""高胜率条件研究：14:45前全部分钟，真实净量，明确费用/双边规模情景。"""
from pathlib import Path
import gzip,json
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
R=Path(__file__).resolve().parent;D=R/'profit';D.mkdir(exist_ok=True)

def data():
    path=D/'panel.parquet'
    if path.exists():return pd.read_parquet(path)
    base=pd.read_parquet(R/'reverse/results/enriched_panel.parquet');keys=base.set_index(['date','symbol']);out=[]
    for root in [R,R/'reverse/extension']:
        for file in sorted((root/'results/series').glob('*.parquet')):
            day=pd.read_parquet(file)
            for sym,g in day.groupby('symbol',sort=False):
                date=g.date.iloc[0]
                if (date,sym) not in keys.index:continue
                k=keys.loc[(date,sym)]
                # 存储时间是原bar+1分钟；14:46对应原始14:45。
                t=g.minute;s=g[((t.between('09:31','11:31'))|(t.between('13:01','14:46')))].dropna(subset=['etf','mid','actual_settlement_buy'])
                close=g[g.minute<='16:01'].dropna(subset=['mid','actual_settlement_buy','actual_settlement_sell'])
                if len(s)<220 or s.minute.iloc[-1]!='14:46' or not len(close):continue
                p=(s.etf/s.actual_settlement_buy-1).to_numpy()*10000;am=s.amount.to_numpy();e=s.etf.to_numpy();v=s.actual_settlement_buy.to_numpy();cl=close.iloc[-1]
                out.append(dict(date=date,symbol=sym,unit=k.unit,net_baskets=k.net_baskets,net_flow_pct=k.net_flow_pct,
                    lag_flow_pct=k.lag_flow_pct,lag5_flow_pct=k.lag5_flow_pct,log_prev_assets=k.log_prev_assets,
                    creation_allowed=k.creation_allowed,redemption_allowed=k.redemption_allowed,
                    mean_bp=p.mean(),p10_bp=np.quantile(p,.1),p90_bp=np.quantile(p,.9),positive_fraction=np.mean(p>0),
                    money_positive_share=am[p>10].sum()/am.sum() if am.sum() else 0,
                    amount_weighted_bp=np.average(p,weights=am) if am.sum() else np.nan,
                    turnover_pct=am.sum()/np.exp(k.log_prev_assets)*100,
                    fx_gap_bp=(s.mid.iloc[-1]/s.actual_settlement_buy.iloc[-1]-1)*10000,
                    basket_return_bp=(v[-1]/v[0]-1)*10000,etf_return_bp=(e[-1]/e[0]-1)*10000,
                    M=cl.mid*k.unit,B=cl.actual_settlement_buy*k.unit,Sell=cl.actual_settlement_sell*k.unit,
                    close_time=cl.minute,valid_minutes=len(s)))
    d=pd.DataFrame(out).sort_values(['date','symbol']);assert not d.duplicated(['date','symbol']).any()
    d.to_parquet(path,index=False);return d

def pnl(d,r=1,fee=10,x=1,slippage=0):
    # 未知对向总量= r*abs(净量)，本人成交各x篮；不是对基金实际双边量的估计。
    N=d.net_baskets.to_numpy();n=np.abs(N);w=n/((1+r)*n+x)
    M=d.M.to_numpy();B=d.B.to_numpy();S=d.Sell.to_numpy()
    spread=np.where(N>0,(M-B)-B*slippage/10000,(S-M)-B*slippage/10000)
    gross=x*w*spread
    net=gross-x*B*fee/10000
    return pd.DataFrame(dict(net_yuan=net,return_bp=net/(x*B)*10000,weight=w),index=d.index)

def stats(d,scenario=1,fee=10,x=1):
    if len(d)==0:return dict(n=0,days=0,funds=0)
    p=pnl(d,scenario,fee,x);loss=p.return_bp[p.return_bp<0]
    days=d[['date']].join(p).groupby('date').agg(net=('net_yuan','sum'))
    return dict(n=len(d),funds=d.symbol.nunique(),days=d.date.nunique(),direction_win=float((d.net_baskets>0).mean()),
       scenario_win=float((p.net_yuan>0).mean()),mean_bp=float(p.return_bp.mean()),median_bp=float(p.return_bp.median()),
       p05_bp=float(p.return_bp.quantile(.05)),worst_bp=float(p.return_bp.min()),mean_loss_bp=float(loss.mean()) if len(loss) else 0,
       worst_day_yuan=float(days.net.min()),day_win=float((days.net>0).mean()),total_yuan=float(p.net_yuan.sum()),
       median_baskets=float(d.net_baskets.median()),median_daily_notional=float(d.groupby('date').B.sum().median()),max_daily_notional=float(d.groupby('date').B.sum().max()))

def interval(d):
    y=(pnl(d).net_yuan>0);z=d.assign(win=y).groupby('date').win.agg(['sum','count']).to_numpy()
    if len(z)<2:return [None,None]
    rng=np.random.default_rng(20260912);a=z[rng.integers(len(z),size=(2000,len(z)))].sum(axis=1)
    return np.quantile(a[:,0]/a[:,1],[.025,.975]).tolist()

def rulebook(d):
    # 预列54组，汇率差至少60bp；不根据后段结果改动。
    open_=(d.creation_allowed==1)&(d.redemption_allowed==1)&(d.fx_gap_bp>=60)
    out={}
    for mean in [20,30,40,50,70,100]:
        for lag in [0,.5,1]:
            for mode in [0,1,2]:
                quality=np.ones(len(d),dtype=bool) if mode==0 else (d.p10_bp> (0 if mode==1 else 20))&(d.money_positive_share>=(.8 if mode==1 else .95))
                out[f'mean{mean}_lag{lag}_quality{mode}']=open_&(d.mean_bp>mean)&(d.lag_flow_pct>lag)&quality
    return out

def main():
    d=data();train=d[d.date<'2026-01-01'];val=d[d.date.between('2026-01-01','2026-03-31')]
    splits={'train':train,'validation':val,'later_apr_jul':d[d.date>='2026-04-01'],'apr_jun':d[d.date.between('2026-04-01','2026-06-30')],'jul_aug':d[d.date>'2026-06-30']}
    (D/'assumptions.json').write_text(json.dumps(dict(cutoff='14:45 original bar time, use all completed bars through cutoff',
       final_fx_known='user-specified retrospective condition, not a live available FX feed',
       price_execution='both sides valued using last available basket prices by 16:00; real fills unknown',
       own_roundtrip_baskets=1,opposite_volume_ratio=1,all_in_fee_bp=10,fx_gap_filter_bp=60,
       validation_min=40,train_min=100,validation_min_funds=8,validation_min_days=10,
       target_scenario_win=.9,warning='All historical periods already inspected in prior research; no untouched holdout remains'),ensure_ascii=False,indent=2))
    rules=[]
    for period,g in splits.items():
        for name,mask in rulebook(g).items():rules.append(dict(period=period,rule=name,**stats(g[mask])))
    rr=pd.DataFrame(rules);rr.to_csv(D/'rule_search.csv',index=False)
    tr=rr[rr.period=='train'].set_index('rule');va=rr[(rr.period=='validation')&(rr.n>=40)&(rr.funds>=8)&(rr.days>=10)].copy()
    va=va[va.rule.map(tr.n)>=100]
    choices=[]
    if len(va):
        qualifying=va[(va.scenario_win>=.9)&(va.mean_bp>0)]
        win=(qualifying.sort_values(['n','scenario_win'],ascending=False).iloc[0] if len(qualifying) else va.sort_values(['scenario_win','n'],ascending=False).iloc[0])
        choices.append(dict(kind='rule',name=win.rule,validation_meets90=bool(len(qualifying)),validation=win.to_dict()))
    # 模型训练目标为给定情景的扣费盈利，不把申购方向直接当盈利。
    features=['lag_flow_pct','lag5_flow_pct','log_prev_assets','mean_bp','p10_bp','p90_bp','positive_fraction','money_positive_share','amount_weighted_bp','turnover_pct','fx_gap_bp','basket_return_bp','etf_return_bp']
    m=HistGradientBoostingClassifier(max_iter=100,max_leaf_nodes=7,min_samples_leaf=80,l2_regularization=10,learning_rate=.06,early_stopping=False,random_state=20260912)
    m.fit(train[features],(pnl(train).net_yuan>0).astype(int));d=d.copy();d['score']=m.predict_proba(d[features])[:,1]
    score_rows=[]
    for th in [.5,.6,.7,.8,.85,.9,.95]:
        for period,g in splits.items():
            scores=d.loc[g.index,'score'];sel=(scores>=th)&(g.fx_gap_bp>=60)&(g.creation_allowed==1)&(g.redemption_allowed==1)
            score_rows.append(dict(period=period,threshold=th,**stats(g[sel])))
    sr=pd.DataFrame(score_rows);sr.to_csv(D/'score_search.csv',index=False)
    eligible=sr[(sr.period=='validation')&(sr.n>=40)&(sr.funds>=8)&(sr.days>=10)&(sr.scenario_win>=.9)&(sr.mean_bp>0)]
    if len(eligible):
        w=eligible.sort_values('n',ascending=False).iloc[0];choices.append(dict(kind='model',name=f'score{w.threshold}',threshold=float(w.threshold),validation_meets90=True,validation=w.to_dict()))
    if not choices:
        (D/'choices.json').write_text('[]')
        (D/'joint_gate_status.json').write_text(json.dumps(dict(status='no_candidate',reason='No candidate meets the preset sample-size and date-count requirements'),indent=2))
        print('No candidate meets pre-set sample requirements; retain search tables without selecting a strategy.',flush=True)
        return
    selected=[];summary=[];sensitivity=[];portfolios=[];clusterrows=[]
    for c in choices:
        for period,g in splits.items():
            mask=rulebook(g)[c['name']] if c['kind']=='rule' else ((d.loc[g.index,'score']>=c['threshold'])&(g.fx_gap_bp>=60)&(g.creation_allowed==1)&(g.redemption_allowed==1))
            h=g[mask].copy();h['score']=d.loc[h.index,'score']
            summary.append(dict(kind=c['kind'],name=c['name'],period=period,**stats(h),scenario_win_ci=interval(h)))
            if period not in ['apr_jun','jul_aug']:continue
            selected.append(h.assign(kind=c['kind'],period=period))
            for r in [0,1,4,9]:
                for fee in [5,10,20,40]:sensitivity.append(dict(kind=c['kind'],period=period,opposite_ratio=r,fee_bp=fee,own_baskets=1,**stats(h,r,fee)))
            for x in [1,5,10,50]:
                sensitivity.append(dict(kind=c['kind'],period=period,opposite_ratio=1,fee_bp=10,own_baskets=x,**stats(h,1,10,x)))
        # 每天一篮子参与每个符合条件的基金；与按过去60日篮子收益相关性去重对比。
        held=d[d.date>='2026-04-01'].copy();mask=rulebook(held)[c['name']] if c['kind']=='rule' else ((held.score>=c['threshold'])&(held.fx_gap_bp>=60)&(held.creation_allowed==1)&(held.redemption_allowed==1))
        held=held[mask];dedup=[]
        for date,g in held.groupby('date'):
            history=d[d.date<date];dates=sorted(history.date.unique())[-60:];pv=history[history.date.isin(dates)].pivot(index='date',columns='symbol',values='basket_return_bp');corr=pv.corr(min_periods=20)
            kept=[]
            for _,row in g.sort_values(['score','mean_bp'],ascending=False).iterrows():
                if all(row.symbol not in corr or k not in corr or not np.isfinite(corr.loc[row.symbol,k]) or corr.loc[row.symbol,k]<.85 for k in kept):kept.append(row.symbol);dedup.append(row.name)
            clusterrows.append(dict(kind=c['kind'],date=date,total=len(g),kept=len(kept)))
        for mode,h in [('all',held),('corr85_one',held.loc[dedup])]:
            p=pnl(h);daily=h[['date','B']].join(p).groupby('date').agg(n=('B','size'),notional=('B','sum'),net_yuan=('net_yuan','sum'),losers=('net_yuan',lambda x:int((x<0).sum())))
            daily['return_bp']=daily.net_yuan/daily.notional*10000;daily['kind']=c['kind'];daily['mode']=mode
            daily.to_csv(D/f'portfolio_{c["kind"]}_{mode}.csv')
            portfolios.append(dict(kind=c['kind'],mode=mode,**stats(h),portfolio_worst_bp=float(daily.return_bp.min()),portfolio_mean_bp=float(daily.return_bp.mean()),median_count=float(daily.n.median()),max_count=int(daily.n.max()),loss_days=int((daily.net_yuan<0).sum())))
    (D/'choices.json').write_text(json.dumps(choices,indent=2));pd.DataFrame(summary).to_csv(D/'chosen_summary.csv',index=False)
    pd.DataFrame(sensitivity).to_csv(D/'cost_dilution_sensitivity.csv',index=False);pd.concat(selected).to_csv(D/'selected_fund_days.csv',index=False)
    pd.DataFrame(portfolios).to_csv(D/'portfolio_comparison.csv',index=False);pd.DataFrame(clusterrows).to_csv(D/'correlation_filter_counts.csv',index=False)
    d.to_parquet(D/'scored_panel.parquet',index=False)
    print('choices',json.dumps(choices),flush=True);print(pd.DataFrame(summary).to_string(index=False),flush=True);print(pd.DataFrame(portfolios).to_string(index=False),flush=True)

if __name__=='__main__':main()
