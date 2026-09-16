"""Wind-style GB18030 historical L2 -> auditable SZ order/trade/flow features.
SH is deliberately unsupported until real target SH schema is supplied and validated.
"""
from pathlib import Path
import json,sys,hashlib
import numpy as np
import pandas as pd
R=Path(__file__).resolve().parent;F=R.parent;sys.path.insert(0,str(F))
import build_panel as b
from labels_calendar import labels
b.labels=labels
RAW=Path('/Volumes/EllisFiles/Stocksdata/A股逐笔/港股通ETF保留');OUT=R/'results_2026'
for x in [OUT,OUT/'minutes',OUT/'parents']:x.mkdir(parents=True,exist_ok=True)

def numeric(d,c):return pd.to_numeric(d[c],errors='raise').astype('int64')
def ms(t):return (t//10000000)*3600000+(t//100000%100)*60000+(t//1000%100)*1000+t%1000

def load_sz(path):
    sh=path.name.endswith('.SH')
    raw=pd.read_csv(path/'逐笔成交.csv',encoding='gb18030',keep_default_na=False,dtype=str)
    ro=pd.read_csv(path/'逐笔委托.csv',encoding='gb18030',keep_default_na=False,dtype=str)
    date=int(path.parent.name)
    t=pd.DataFrame(dict(day=numeric(raw,'自然日'),t=ms(numeric(raw,'时间')),seq=numeric(raw,'成交编号'),kind=raw['成交代码'].str.strip(),flag=raw['BS标志'].str.strip(),price=numeric(raw,'成交价格')/10000,vol=numeric(raw,'成交数量'),buy=numeric(raw,'叫买序号'),sell=numeric(raw,'叫卖序号')))
    o=pd.DataFrame(dict(day=numeric(ro,'自然日'),t=ms(numeric(ro,'时间')),id=numeric(ro,'交易所委托号'),side=ro['委托代码'].str.strip(),kind=ro['委托类型'].str.strip(),price=numeric(ro,'委托价格')/10000,vol=numeric(ro,'委托数量')))
    assert set(t.loc[t.day.ne(0),'day'])<={date} and set(o.loc[o.day.ne(0),'day'])<={date},'date mismatch'
    t=t[t.day.eq(date)].copy();o=o[o.day.eq(date)&o.id.gt(0)&o.side.isin(['B','S'])].copy()
    assert not t.seq.duplicated().any(),'duplicate trade sequence'
    if sh:
        assert set(o.kind)<={'A','D'},'unknown SH order kind'
        cancel_orders=o[o.kind.eq('D')].copy()
        o=o[o.kind.eq('A')].copy()
        assert set(t.kind)<={''},'unknown SH trade code'
        t['kind']='0'
    assert not o.id.duplicated().any(),'duplicate order id: possible missing channel'
    assert set(t.kind)<={'0','C'},'unknown execution codes'
    trades=t[t.kind.eq('0')].copy();cancels=t[t.kind.eq('C')].copy()
    if sh:
        cancels=cancel_orders.rename(columns={'id':'seq'}).copy()
        cancels['buy']=cancels.seq.where(cancels.side.eq('B'),0);cancels['sell']=cancels.seq.where(cancels.side.eq('S'),0)
    assert trades.price.gt(0).all() and trades.vol.gt(0).all()
    assert trades.buy.gt(0).all() and trades.sell.gt(0).all()
    trades['side']=trades.flag.map({'B':1,'S':-1}).fillna(0).astype(int)
    trades['amount']=trades.price*trades.vol
    trades=trades.sort_values(['t','seq'],kind='stable')
    trades['minute']=((trades.t//60000+1)//60).astype(str).str.zfill(2)+':'+((trades.t//60000+1)%60).astype(str).str.zfill(2)
    return raw,ro,trades,cancels,o

def continuous(t,sh=False):return ((t>=34200000)&(t<41400000))|((t>=46800000)&(t<(54000000 if sh else 53820000)))
def near(v,u):
    q=np.rint(v/u)
    return (q>=1)&(np.abs(v-q*u)<=u*.01)

def features(path,unit):
    raw,ro,alltr,c,o=load_sz(path);tr=alltr[continuous(alltr.t,path.name.endswith('.SH'))].copy();v=tr.vol.sum();amt=tr.amount.sum()
    if amt<=0:raise ValueError('no continuous trades')
    feat=dict(l2_trade_rows=len(alltr),l2_continuous_rows=len(tr),l2_order_rows=len(o),l2_cancel_rows=len(c),l2_amount=amt,l2_volume=v,
       l2_unknown_direction_frac=tr.loc[tr.side.eq(0),'amount'].sum()/amt,
       l2_flag_id_agreement=(tr.side.eq(np.where(tr.buy>tr.sell,1,-1))).mean(),
       l2_auction_amount_frac=1-amt/alltr.amount.sum(),l2_active_imbalance=(tr.side*tr.amount).sum()/amt,
       l2_active_buy_frac=tr.loc[tr.side.eq(1),'amount'].sum()/amt)
    parent=[]
    for side,key,sign in [('buy','buy',1),('sell','sell',-1)]:
        g=alltr.groupby(key).agg(filled=('vol','sum'),amount=('amount','sum'),first=('t','min'),last=('t','max'),trades=('vol','size'))
        own=o[o.side.eq('B' if sign==1 else 'S')].set_index('id')
        ca=c[c[key]>0].groupby(key).vol.sum()
        g=g.join(own[['vol','t']],how='outer').rename(columns={'vol':'submitted','t':'submitted_t'})
        g['resting_reported']=g.submitted
        if path.name.endswith('.SH'):
            initial=tr[tr.side.eq(sign)].copy();initial['rest_time']=initial[key].map(own.t)
            initial=initial[initial.t<=initial.rest_time]
            prefill=initial.groupby(key).vol.sum()
            g['initial_aggressive_filled']=prefill.reindex(g.index).fillna(0)
            g['submitted']=g.submitted+g.initial_aggressive_filled
        g['cancelled']=ca;g[['filled','amount','trades','cancelled']]=g[['filled','amount','trades','cancelled']].fillna(0)
        cont_amount=tr.groupby(key).amount.sum()
        g['continuous_amount']=cont_amount.reindex(g.index).fillna(0)
        active=tr[tr.side.eq(sign)].groupby(key).agg(active_volume=('vol','sum'),active_amount=('amount','sum'))
        g=g.join(active);g[['active_volume','active_amount']]=g[['active_volume','active_amount']].fillna(0)
        feat[f'l2_{side}_orphan_fill_frac']=g.loc[g.submitted.isna(),'amount'].sum()/alltr.amount.sum()
        feat[f'l2_{side}_unexplained_passive_frac']=(g.loc[g.submitted.isna(),'continuous_amount']-g.loc[g.submitted.isna(),'active_amount']).sum()/amt
        feat[f'l2_{side}_overfilled_orders']=int((g.filled+g.cancelled>g.submitted).sum())
        feat[f'l2_{side}_cancel_ratio']=g.cancelled.sum()/g.submitted.sum()
        feat[f'l2_{side}_parent_hhi']=np.square(g.active_amount/amt).sum()
        feat[f'l2_{side}_large_parent_frac']=g.loc[g.active_volume>=unit*.5,'active_amount'].sum()/amt
        feat[f'l2_{side}_unit_parent_frac']=g.loc[near(g.active_volume,unit),'active_amount'].sum()/amt
        feat[f'l2_{side}_unit_submitted_fill_frac']=g.loc[near(g.submitted,unit),'active_amount'].sum()/amt
        feat[f'l2_{side}_passive_unit_fill_frac']=(g.loc[near(g.submitted,unit),'continuous_amount']-g.loc[near(g.submitted,unit),'active_amount']).sum()/amt
        placebo=np.mean([g.loc[near(g.active_volume,round(unit*x/100)*100),'active_amount'].sum()/amt for x in [.8,1.2,1.3]])
        feat[f'l2_{side}_unit_parent_excess']=feat[f'l2_{side}_unit_parent_frac']-placebo
        g['side']=side;g['order_id']=g.index;parent.append(g.reset_index(drop=True))
    # Consecutive same-side trades, <=1s gaps; no splitting at target volume.
    group=(tr.side.ne(tr.side.shift())|tr.t.diff().gt(1000)).cumsum()
    bursts=tr.groupby(group).agg(side=('side','first'),vol=('vol','sum'),amount=('amount','sum'),start=('t','min'),end=('t','max'))
    for side,sign in [('buy',1),('sell',-1)]:
        z=bursts[bursts.side.eq(sign)]
        feat[f'l2_{side}_unit_burst_frac']=z.loc[near(z.vol,unit),'amount'].sum()/amt
        placebo=np.mean([z.loc[near(z.vol,round(unit*x/100)*100),'amount'].sum()/amt for x in [.8,1.2,1.3]])
        feat[f'l2_{side}_unit_burst_excess']=feat[f'l2_{side}_unit_burst_frac']-placebo
    q=pd.read_csv(path/'行情.csv',encoding='gb18030')
    feat['l2_quote_volume_ratio']=alltr.vol.sum()/pd.to_numeric(q['当日累计成交量']).max()
    qt=ms(numeric(q,'时间'));q=q.loc[continuous(qt,path.name.endswith('.SH'))].copy()
    qb=pd.to_numeric(q['申买量1']);qa=pd.to_numeric(q['申卖量1'])
    feat['l2_quote_imbalance']=((qb-qa)/(qb+qa)).replace([np.inf,-np.inf],np.nan).mean()
    feat['l2_spread_bp']=((q['申卖价1']-q['申买价1'])/((q['申卖价1']+q['申买价1'])/2)*10000).where((q['申买价1']>0)&(q['申卖价1']>0)).mean()
    tr['buy_amount']=tr.amount.where(tr.side.eq(1),0);tr['sell_amount']=tr.amount.where(tr.side.eq(-1),0)
    minute=tr.groupby('minute').agg(l2_buy_amount=('buy_amount','sum'),l2_sell_amount=('sell_amount','sum'),l2_volume=('vol','sum'),l2_trades=('seq','size'))
    return feat,minute,pd.concat(parent,ignore_index=True),alltr

def main():
    labs=b.labels();panel=pd.read_parquet(F/'results/factor_panel.parquet').set_index(['symbol','date']);out=[];audit=[];minout=[]
    for dp in sorted(RAW.iterdir()):
        if not dp.is_dir() or not dp.name.isdigit() or not dp.name.startswith('2026'):continue
        # Ignore a date still being extracted until its manifest exists.
        manifest=R/'manifests_2026'/f'{dp.name}.json'
        if not manifest.exists() or not json.loads(manifest.read_text()).get('verified'):continue
        date=f'{dp.name[:4]}-{dp.name[4:6]}-{dp.name[6:]}'
        series=pd.read_parquet(F/'results/series'/f'{dp.name}.parquet')
        sm={s:g for s,g in series.groupby('symbol')}
        for path in sorted(dp.iterdir()):
            sym=path.name;key=(sym,date)
            if key not in panel.index:
                audit.append(dict(date=date,symbol=sym,status='missing_valuation_or_label'));continue
            base=panel.loc[key].to_dict()
            try:feat,m,parents,alltr=features(path,base['unit'])
            except Exception as exc:
                audit.append(dict(date=date,symbol=sym,status='error',error=str(exc)));continue
            s=sm[sym].copy();s=s.merge(m,on='minute',how='left',validate='one_to_one')
            cols=['l2_buy_amount','l2_sell_amount','l2_volume','l2_trades'];s[cols]=s[cols].fillna(0)
            s['premium_bp']=(s.etf/s.actual_settlement_buy-1)*10000
            s=s[s.etf.notna()].copy();a=feat['l2_amount'];lab=labs[key]
            feat['l2_net_volume_pct']=(alltr.loc[continuous(alltr.t,path.name.endswith('.SH')),'side']*alltr.loc[continuous(alltr.t,path.name.endswith('.SH')),'vol']).sum()/lab['prev_shares']*100
            feat['l2_etf_amount_ratio']=alltr.amount.sum()/s.amount.sum()
            feat['l2_etf_price_diff_bp']=(alltr.iloc[-1].price/s.etf.iloc[-1]-1)*10000
            for side in ['buy','sell']:
                amt=s['l2_'+side+'_amount']
                feat[f'l2_{side}_positive_premium_frac']=amt.where(s.premium_bp>30,0).sum()/a
                feat[f'l2_{side}_negative_premium_frac']=amt.where(s.premium_bp< -30,0).sum()/a
                # A previous completed minute is used to condition flow; avoid current-price causal interpretation.
                lag=s.premium_bp.shift();change=s.premium_bp.diff();same=s.minute.ne('13:01')
                feat[f'l2_{side}_compression_frac']=amt.where((lag>30)&(change<0)&same,0).sum()/a
            s['l2_signed_amount']=s.l2_buy_amount-s.l2_sell_amount
            flow=s.l2_signed_amount
            feat['l2_flow_premium_change_corr']=flow.corr(s.premium_bp.diff())
            feat['l2_late_imbalance']=s.loc[s.minute>='14:01','l2_signed_amount'].sum()/a
            feat['l2_flow_imbalance_std']=(flow/(s.l2_buy_amount+s.l2_sell_amount)).std()
            for field in ['large_parent_frac','unit_parent_frac','unit_parent_excess','unit_burst_frac','unit_burst_excess','unit_submitted_fill_frac','passive_unit_fill_frac']:
                feat['l2_sell_minus_buy_'+field]=feat['l2_sell_'+field]-feat['l2_buy_'+field]
            quality=(feat['l2_buy_overfilled_orders']==0 and feat['l2_sell_overfilled_orders']==0 and feat['l2_buy_unexplained_passive_frac']<.001 and feat['l2_sell_unexplained_passive_frac']<.001 and abs(feat['l2_quote_volume_ratio']-1)<.001 and abs(feat['l2_etf_amount_ratio']-1)<.01 and abs(feat['l2_etf_price_diff_bp'])<1 and feat['l2_unknown_direction_frac']<.001)
            row=dict(symbol=sym,date=date,**base,**feat,l2_quality_pass=quality);out.append(row)
            audit.append(dict(date=date,symbol=sym,status='ok' if quality else 'quality_failed',**{k:v for k,v in feat.items() if any(x in k for x in ['ratio','agreement','orphan','unexplained','overfilled','diff_bp','unknown'])}))
            s.to_parquet(OUT/'minutes'/f'{dp.name}_{sym}.parquet',index=False)
            parents.to_parquet(OUT/'parents'/f'{dp.name}_{sym}.parquet',index=False)
        print(date,'cumulative',len(out),'failures',sum(x['status']!='ok' for x in audit),flush=True)
    pd.DataFrame(out).to_parquet(OUT/'l2_panel.parquet',index=False)
    pd.DataFrame(out).to_csv(OUT/'l2_panel.csv',index=False)
    pd.DataFrame(audit).to_csv(OUT/'quality_audit.csv',index=False)
    print('done',len(out),flush=True)
if __name__=='__main__':main()
