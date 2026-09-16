"""事后识别短时溢价拉升回落；不将形态命名为已确认的申购卖出。"""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
R=Path(__file__).resolve().parent; O=R/'results'; D=O/'spike_cases';D.mkdir(exist_ok=True)

def detect(s,threshold):
    # 原始行情时间，独立连续交易段；以3分钟中位数排除单点毛刺。
    s=s.copy();s['p']=(s.etf/s.actual_settlement_buy-1)*10000
    s['t']=pd.to_datetime(s.date+' '+s.minute)-pd.Timedelta(minutes=1)
    events=[]
    for _,g in s.groupby((s.t.diff().dt.total_seconds()!=60).cumsum()):
        g=g.reset_index(drop=True);p=g.p.rolling(3).median().to_numpy();e=g.etf.to_numpy();a=g.amount.to_numpy()
        if len(g)<14:continue
        ks=np.arange(10,len(g)-3)
        base=np.median(np.lib.stride_tricks.sliding_window_view(p,5)[ks-10],axis=1)
        rises=p[ks]-base
        ebase=np.median(np.lib.stride_tricks.sliding_window_view(e,5)[ks-10],axis=1)
        active=np.sum(np.lib.stride_tricks.sliding_window_view(a>0,3)[ks-2],axis=1)
        eligible=np.flatnonzero(np.isfinite(rises)&(p[ks]>=threshold)&(rises>=threshold)&(e[ks]>ebase)&(active>=2))
        for idx in eligible:
            k=ks[idx];baseline=float(base[idx]);rise=float(rises[idx])
            for j in range(k+3,min(k+21,len(g))):
                fall=p[k]-p[j]
                if fall>=threshold and fall>=.6*rise:
                    events.append(dict(peak_time=g.t.iloc[k].strftime('%H:%M'),fall_time=g.t.iloc[j].strftime('%H:%M'),
                        baseline_bp=baseline,peak_bp=float(p[k]),rise_bp=float(rise),fall_bp=float(fall),
                        elapsed_minutes=j-k,etf_peak_to_fall_bp=float((e[j]/e[k]-1)*10000),
                        basket_peak_to_fall_bp=float((g.actual_settlement_buy.iloc[j]/g.actual_settlement_buy.iloc[k]-1)*10000)))
                    break
    # 基金日只计一次；最大上冲幅度的候选，排序与净份额标签无关。
    return max(events,key=lambda x:x['rise_bp']) if events else None

def stats(d):
    return dict(n=len(d),funds=d.symbol.nunique(),days=d.date.nunique(),
        positive_rate=float((d.net_baskets>.01).mean()),negative_rate=float((d.net_baskets<-.01).mean()),
        large_rate=float(((d.net_baskets>=10)&(d.net_flow_pct>=.5)).mean()),
        median_net_baskets=float(d.net_baskets.median()))

def ci(d):
    z=d.assign(pos=d.net_baskets>.01).groupby('date').pos.agg(['sum','count']).to_numpy()
    if len(z)<2:return [None,None]
    rng=np.random.default_rng(20260912);v=z[rng.integers(len(z),size=(1000,len(z)))].sum(axis=1)
    return np.quantile(v[:,0]/v[:,1],[.025,.975]).tolist()

def main():
    panel=pd.read_parquet(O/'factor_panel.parquet'); keys=set(zip(panel.date,panel.symbol));events=[];aud=[]
    for n,f in enumerate(sorted((O/'series').glob('*.parquet'))):
        day=pd.read_parquet(f)
        for sym,g in day.groupby('symbol',sort=False):
            date=g.date.iloc[0]
            if (date,sym) not in keys:continue
            t=pd.to_datetime(g.date+' '+g.minute)-pd.Timedelta(minutes=1)
            mask=((t.dt.strftime('%H:%M')>='09:30')&(t.dt.strftime('%H:%M')<='11:30'))|((t.dt.strftime('%H:%M')>='13:00')&(t.dt.strftime('%H:%M')<='15:00'))
            s=g.loc[mask].dropna(subset=['etf','mid','actual_settlement_buy']).copy()
            mean=float(((s.etf/s.actual_settlement_buy-1)*10000).mean())
            aud.append(dict(date=date,symbol=sym,recomputed_mean_bp=mean,n=len(s),post1500_count=int(((pd.to_datetime(s.date+' '+s.minute)-pd.Timedelta(minutes=1)).dt.strftime('%H:%M')>'15:00').sum())))
            for th in [20,30,50]:
                ev=detect(s,th)
                if ev:events.append(dict(date=date,symbol=sym,threshold_bp=th,**ev))
        if n%30==0:print('scanned',n+1,'events',len(events),flush=True)
    a=pd.DataFrame(aud).merge(panel[['date','symbol','settlement_mean_bp']],on=['date','symbol'],validate='one_to_one')
    assert len(a)==len(panel) and a.post1500_count.max()==0
    assert (a.recomputed_mean_bp-a.settlement_mean_bp).abs().max()<1e-8
    (D/'session_audit.json').write_text(json.dumps(dict(fund_days=len(a),minutes=int(a.n.sum()),post1500_minutes=int(a.post1500_count.sum()),max_mean_difference_bp=float((a.recomputed_mean_bp-a.settlement_mean_bp).abs().max())),indent=2))
    ev=pd.DataFrame(events).merge(panel,on=['date','symbol'],validate='many_to_one');ev.to_csv(D/'events.csv',index=False)
    summary=[]
    for period,start,end in [('all','2025-07-01','2026-06-30'),('train','2025-07-01','2025-12-31'),('validation','2026-01-01','2026-03-31'),('test','2026-04-01','2026-06-30')]:
        base=panel[panel.date.between(start,end)];summary.append(dict(period=period,condition='all',**stats(base)))
        summary.append(dict(period=period,condition='all_mean_le30',**stats(base[base.settlement_mean_bp<=30])))
        for th in [20,30,50]:
            sel=ev[(ev.threshold_bp==th)&ev.date.between(start,end)]
            summary.append(dict(period=period,condition=f'spike_{th}',**stats(sel),positive_ci=ci(sel)))
            # 用户“砸下来”更严格版本：ETF自身价格也下降至少0.1%，而不仅是篮子上涨。
            down=sel[sel.etf_peak_to_fall_bp<=-10]
            summary.append(dict(period=period,condition=f'spike_{th}_etf_down10',**stats(down),positive_ci=ci(down)))
            if th==30:
                matched=sel[sel.settlement_mean_bp<=30]
                summary.append(dict(period=period,condition='spike_30_mean_le30',**stats(matched),positive_ci=ci(matched)))
    pd.DataFrame(summary).to_csv(D/'summary.csv',index=False)
    main=ev[ev.threshold_bp==30]
    month=[dict(month=k,**stats(g)) for k,g in main.assign(month=main.date.str[:7]).groupby('month')]
    pd.DataFrame(month).to_csv(D/'monthly.csv',index=False)
    # 典型与反例：各组取升幅中位附近，非最大净申购；尽量不同基金，优先留出季度。
    chosen=[];used=set()
    tests=main[main.date>='2026-04-01']
    groups=[('大量净申购',tests[(tests.net_baskets>=10)&(tests.net_flow_pct>=.5)]),
            ('低全天均值但净申购',tests[(tests.net_baskets>.01)&(tests.settlement_mean_bp<=30)]),
            ('ETF价格回落且净申购',tests[(tests.net_baskets>.01)&(tests.etf_peak_to_fall_bp<=-10)]),
            ('净赎回反例',tests[tests.net_baskets<-.01]),('份额不变反例',tests[tests.net_baskets.abs()<=.01])]
    for category,g in groups:
        available=g[~g.symbol.isin(used)].copy()
        if available.empty:available=g.copy()
        if available.empty:continue
        available['distance']=(available.rise_bp-available.rise_bp.median()).abs()
        row=available.sort_values(['distance','date','symbol']).iloc[0].to_dict();row['category']=category
        chosen.append(row);used.add(row['symbol'])
    # 持续溢价作为路径对照，选择测试期满足旧规则且没有30bp尖峰的中位样本。
    existing=set(zip(main.date,main.symbol));sustain=panel[(panel.date>='2026-04-01')&(panel.settlement_mean_bp>30)&(panel.lag_flow_pct>0)].copy()
    sustain=sustain[[k not in existing for k in zip(sustain.date,sustain.symbol)]]
    if len(sustain):
        sustain['distance']=(sustain.settlement_mean_bp-sustain.settlement_mean_bp.median()).abs()
        row=sustain.sort_values(['distance','date','symbol']).iloc[0].to_dict();row['category']='持续溢价对照';chosen.append(row)
    pd.DataFrame(chosen).to_csv(D/'case_index.csv',index=False)
    render_cases(chosen)
    print(pd.DataFrame(summary).to_string(index=False),flush=True)

def render_cases(chosen):
    font_manager.fontManager.addfont('/System/Library/Fonts/Supplemental/Arial Unicode.ttf')
    plt.rcParams.update({'font.family':'Arial Unicode MS','axes.unicode_minus':False,'font.size':11,'axes.spines.top':False,'axes.spines.right':False})
    universe=json.loads((R/'inputs/universe.json').read_text())
    for i,row in enumerate(chosen,1):
        date=row['date'];sym=row['symbol'];g=pd.read_parquet(O/'series'/(date.replace('-','')+'.parquet'));g=g[g.symbol==sym].copy()
        g['time']=pd.to_datetime(g.date+' '+g.minute)-pd.Timedelta(minutes=1)
        g=g[g.time.dt.strftime('%H:%M').between('09:30','16:00')]
        x=g.time;fig,axs=plt.subplots(3,1,figsize=(13,9),sharex=True,gridspec_kw={'height_ratios':[2.2,1.5,.8]})
        for field,label,color,style in [('mid','中间价 IOPV','#b47b00','-'),('actual_settlement_buy','结算价 IOPV','#008575','--'),('etf','ETF成交价','#2864dc','-')]:
            axs[0].plot(x,g[field],label=label,color=color,linestyle=style,lw=1.5)
        axs[0].set_ylabel('元 / 份');axs[0].legend(loc='lower left',bbox_to_anchor=(0,1.01),ncol=3,frameon=False)
        p=(g.etf/g.actual_settlement_buy-1)*10000
        axs[1].plot(x,p,color='#2864dc',lw=1.3,label='原始分钟结算溢价')
        axs[1].axhline(0,color='#888888',lw=.8);axs[1].axhline(row['settlement_mean_bp'],color='#c45b2c',ls='--',label=f"全天均值 {row['settlement_mean_bp']:.1f} bp")
        axs[1].set_ylabel('结算溢价 / bp');axs[1].legend(loc='lower left',bbox_to_anchor=(0,1.01),ncol=2,frameon=False)
        axs[2].bar(x,g.amount/10000,width=.00065,color='#8296af');axs[2].set_ylabel('成交额 / 万元')
        for ax in axs:
            ax.axvspan(pd.Timestamp(date+' 15:00'),pd.Timestamp(date+' 16:00'),color='#eeeeee',zorder=0)
            ax.grid(axis='y',alpha=.18)
            if row.get('peak_time'):
                peak=pd.Timestamp(date+' '+row['peak_time']);end=pd.Timestamp(date+' '+row['fall_time'])
                ax.axvspan(peak,end,color='#d69a45',alpha=.16)
        event_note=''
        if row.get('peak_time'):
            event_note=f"识别窗口 {row['peak_time']}—{row['fall_time']}：平滑溢价上升 {row['rise_bp']:.0f} bp，随后回落 {row['fall_bp']:.0f} bp"
        name=universe.get(sym,{}).get('name','')
        fig.suptitle(f"{row['category']} · {sym} {name} · {date}\n当日净增 {row['net_baskets']:+.1f} 篮子 / {row['net_flow_pct']:+.2f}%\n{event_note}",fontsize=14,y=.99)
        import matplotlib.dates as mdates
        axs[2].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        axs[2].set_xticks(pd.to_datetime([date+' '+t for t in ['09:30','10:30','11:30','13:00','14:00','15:00','16:00']]))
        axs[2].set_xlim(pd.Timestamp(date+' 09:30'),pd.Timestamp(date+' 16:00'))
        fig.text(.08,.012,'原始行情时间；灰区为ETF闭市后，仅展示港股估值，不计全天均值。橙区为形态识别区间，不代表已确认的申购成交。',fontsize=10,color='#555555')
        fig.tight_layout(rect=[0,.035,1,.935]);fig.savefig(D/f'case_{i:02d}.png',dpi=150);plt.close(fig)
        g.to_csv(D/f'case_{i:02d}_minutes.csv',index=False)

if __name__=='__main__':main()
