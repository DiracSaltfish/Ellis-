"""新增交易路径特征；仅使用价格、成交额、PCF和历史已公布净量。"""
from pathlib import Path
import json,sys
import numpy as np
import pandas as pd
R=Path(__file__).resolve().parent;D=R/'reverse';D.mkdir(exist_ok=True)

def cohort(d):
    return np.select([(d.net_baskets>=10)&(d.net_flow_pct>=.5),(d.net_baskets<=-10)&(d.net_flow_pct<=-.5),d.net_baskets>.01,d.net_baskets<-.01],['large_create','large_redeem','small_create','small_redeem'],'flat')

def extract(g,assets):
    tm=g.minute;mask=tm.between('09:31','11:31')|tm.between('13:01','15:01')
    g=g.loc[mask].dropna(subset=['etf','mid','actual_settlement_buy']).copy()
    p=(g.etf/g.actual_settlement_buy-1).to_numpy()*10000;amount=g.amount.to_numpy();total=amount.sum()
    e=g.etf.to_numpy();v=g.actual_settlement_buy.to_numpy();t=pd.to_datetime(g.date+' '+g.minute)
    contiguous=(t.diff().dt.total_seconds().to_numpy()==60)
    dp=np.r_[0,np.diff(p)];de=np.r_[0,np.diff(e)/e[:-1]*10000];dv=np.r_[0,np.diff(v)/v[:-1]*10000]
    prev=np.r_[np.nan,p[:-1]]
    f=dict(positive_amount_share=float(amount[p>10].sum()/total) if total else np.nan,
      negative_amount_share=float(amount[p< -10].sum()/total) if total else np.nan,
      creation_pressure=float(np.sum(amount*np.maximum(p-10,0))/assets*100),
      redemption_pressure=float(np.sum(amount*np.maximum(-p-10,0))/assets*100),
      compression_turnover_pct=float(amount[(dp< -10)&(prev>30)&(de<0)&contiguous].sum()/assets*100),
      expansion_turnover_pct=float(amount[(dp>10)&(p>30)&(de>0)&contiguous].sum()/assets*100),
      traded_minute_fraction=float(np.mean(amount>0)),
      afternoon_amount_share=float(amount[g.minute.to_numpy()>='13:01'].sum()/total) if total else np.nan,
      high_premium_amount_share=float(amount[p>30].sum()/total) if total else np.nan,
      low_premium_amount_share=float(amount[p< -30].sum()/total) if total else np.nan,
      etf_realized_vol_bp=float(np.std(de[contiguous])),basket_realized_vol_bp=float(np.std(dv[contiguous])),
      premium_in_ticks=float(np.mean((e-v)/.001)),
      basket_drawdown_bp=float(np.min(v/np.maximum.accumulate(v)-1)*10000))
    return f,g,p

def run(extension=False):
    root=R/'reverse/extension' if extension else R
    p=pd.read_parquet(root/'results/factor_panel.parquet').set_index(['date','symbol']);new=[];profiles={}
    for idx,file in enumerate(sorted((root/'results/series').glob('*.parquet'))):
        day=pd.read_parquet(file)
        for sym,g in day.groupby('symbol',sort=False):
            date=g.date.iloc[0];key=(date,sym)
            if key not in p.index:continue
            row=p.loc[key];f,s,pr=extract(g,np.exp(row.log_prev_assets));new.append(dict(date=date,symbol=sym,**f))
            # 只在训练期按结果反查走势；新验证期不用于发现图案。
            if date<'2026-01-01' and not extension:
                group=cohort(pd.DataFrame([row]))[0]
                curves=np.array([pr,(s.actual_settlement_buy/s.actual_settlement_buy.iloc[0]-1).to_numpy()*10000,
                                 np.cumsum(s.amount)/s.amount.sum() if s.amount.sum()>0 else np.zeros(len(s))])
                if len(s)==240:profiles.setdefault(group,[]).append(curves)
        if idx%40==0:print('features',extension,idx+1,flush=True)
    out=p.reset_index().merge(pd.DataFrame(new),on=['date','symbol'],validate='one_to_one');out.to_parquet(D/('extension_features.parquet' if extension else 'base_features.parquet'),index=False)
    if profiles:
        arrays={}
        for k,vals in profiles.items():
            a=np.stack(vals);arrays[k+'_median']=np.nanmedian(a,axis=0);arrays[k+'_q25']=np.nanquantile(a,.25,axis=0);arrays[k+'_q75']=np.nanquantile(a,.75,axis=0);arrays[k+'_n']=np.array(len(vals))
        np.savez_compressed(D/'reverse_profiles.npz',**arrays)
    print('saved',len(out),flush=True)

if __name__=='__main__':run('--extension' in sys.argv)
