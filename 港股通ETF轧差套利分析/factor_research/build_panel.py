"""machome：从当日PCF和港股/ETF未复权分钟价重建序列及盘中因子。

按用户要求，日内固定使用当天最终结算汇率，研究结果条件下的关联。
分钟时间戳统一保守延后一分，绝不使用未来bar或跨日补价。
"""
from pathlib import Path
from zipfile import ZipFile
from collections import Counter
import io, gzip, json, csv, sys
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent; IN=ROOT/'inputs'; OUT=ROOT/'results'
OUT.mkdir(exist_ok=True); (OUT/'series').mkdir(exist_ok=True)
HK=Path('/Volumes/Upan/港股/港股_1分钟')
CUTS=['10:30','11:25','14:00','14:45']

def labels():
    out={}
    for f in (IN/'share_history').glob('*.json'):
        rows=sorted(json.loads(f.read_text()).get('rows',[]),key=lambda r:r['share_date'])
        if len({r['share_date'] for r in rows})!=len(rows):raise ValueError('duplicate label date '+f.name)
        for i,r in enumerate(rows):
            if i==0 or 'share_change_10k' not in r:continue
            prev=rows[i-1]; change=r['shares_10k']-prev['shares_10k']
            if abs(change-r['share_change_10k'])>.021:continue
            out[(f.stem,r['share_date'])]=dict(
                net_shares=r['share_change_10k']*10000, prev_shares=prev['shares_10k']*10000,
                label_prev_date=prev['share_date'],
                lag_flow_pct=prev.get('share_change_pct',np.nan),
                lag5_flow_pct=sum(v.get('share_change_pct',0) for v in rows[max(0,i-5):i]))
    return out

def fxdata():
    mid=json.loads((IN/'midpoint.json').read_text());settle={};lag={};lagdate={}
    for market in ['sh','sz']:
        d=pd.read_csv(IN/f'settlement_{market}.csv').sort_values('适用日期')
        if d['适用日期'].duplicated().any():raise ValueError('duplicate settlement')
        for i,r in d.iterrows():
            settle[(market,r['适用日期'])]=(r['卖出结算汇兑比率'],r['买入结算汇兑比率'])
        dates=sorted(k[1] for k in settle if k[0]==market)
        for date in mid:
            prev=[d for d in dates if d<date]
            if prev:lag[(market,date)]=settle[(market,prev[-1])][0];lagdate[(market,date)]=prev[-1]
    return mid,settle,lag,lagdate

def read_bar(z,sym,date,index):
    d=pd.read_csv(io.BytesIO(z.read(sym+'.csv')),encoding='utf-8-sig')
    t=pd.to_datetime(d['时间'],format='%Y/%m/%d %H:%M')
    if t.duplicated().any() or (t.dt.strftime('%Y-%m-%d')!=date).any():
        raise ValueError('duplicate/wrong-date bars '+sym)
    d.index=t+pd.Timedelta(minutes=1)
    price=pd.to_numeric(d['收盘价'],errors='coerce').where(lambda x:x>0)
    # 仅同日、最多5分钟向前填充，原始缺值不向后填充。
    price=price.reindex(index).ffill(limit=5)
    amount=pd.to_numeric(d['成交额'],errors='coerce').reindex(index).fillna(0)
    return price.to_numpy(),amount.to_numpy()

def make_features(series,b,lab,cut):
    # 只使用两地ETF可交易时段内已完成的bar，不把午休填充值计作持续分钟。
    times=series['minute']; mask=(times<=cut)&(((times>='09:31')&(times<='11:31'))|((times>='13:01')&(times<='15:01')))
    sub=series.loc[mask].copy(); usable=sub.dropna(subset=['etf','mid','actual_settlement_buy'])
    if len(usable)<20 or len(usable)/len(sub)<.9:return None
    if usable.iloc[-1]['minute']!=cut:return None
    e=usable['etf'].to_numpy();m=usable['mid'].to_numpy();l=usable['actual_settlement_buy'].to_numpy()
    pm=(e/m-1)*10000;pl=(e/l-1)*10000
    result=dict(symbol=b['symbol'],date=b['date'],cut=cut,unit=b['unit'],
        net_baskets=lab['net_shares']/b['unit'],net_flow_pct=lab['net_shares']/lab['prev_shares']*100,
        lag_flow_pct=lab['lag_flow_pct'],lag5_flow_pct=lab['lag5_flow_pct'],
        log_prev_assets=np.log(lab['prev_shares']*b['prev_nav']),
        creation_allowed=int(b['creation_allowed']),redemption_allowed=int(b['redemption_allowed']),
        minute_coverage=len(usable)/len(sub),
        etf_return_bp=(e[-1]/e[0]-1)*10000,basket_return_bp=(m[-1]/m[0]-1)*10000,
        turnover_pct=usable['amount'].sum()/(lab['prev_shares']*b['prev_nav'])*100,
        fx_gap_bp=(m[-1]/l[-1]-1)*10000)
    for prefix,p in [('mid',pm),('settlement',pl)]:
        result.update({prefix+'_premium_bp':p[-1],prefix+'_mean_bp':p.mean(),
            prefix+'_max_bp':p.max(),prefix+'_positive_fraction':np.mean(p>0),
            prefix+'_above30_fraction':np.mean(p>30),prefix+'_above50_fraction':np.mean(p>50),
            prefix+'_area_bpmin':np.maximum(p,0).sum(),
            prefix+'_change15_bp':p[-1]-p[max(0,len(p)-16)]})
    return result

def main():
    labs=labels();mid,settle,lag,lagdate=fxdata();features=[];audit=[];daily=[]
    files=sorted((IN/'baskets').glob('*.json.gz'))
    for i,f in enumerate(files):
        key=f.name[:8];date=f'{key[:4]}-{key[4:6]}-{key[6:]}'
        hkpath=HK/date[:7]/(key+'_1min.zip'); ep=IN/'etf'/(key+'.zip')
        if not hkpath.exists() or not ep.exists() or date not in mid:
            audit.append(dict(date=date,symbol='*',reason='missing_hk_etf_or_midpoint'));continue
        with gzip.open(f,'rt',encoding='utf-8') as h:baskets=json.load(h)
        index=pd.date_range(date+' 09:30',date+' 16:08',freq='min')
        times=index.strftime('%H:%M');end_series=[]
        with ZipFile(hkpath) as hz,ZipFile(ep) as ez:
            hnames=set(hz.namelist());enames=set(ez.namelist());prices={}
            required={c[0] for b in baskets.values() for c in b['components']}
            for sym in sorted(required):
                if sym+'.csv' not in hnames:continue
                try:prices[sym]=read_bar(hz,sym,date,index)[0]
                except Exception as e:audit.append(dict(date=date,symbol=sym,reason=str(e)))
            for sym,b in baskets.items():
                market=sym[-2:].lower();lab=labs.get((sym,date))
                reason=None
                if sym+'.csv' not in enames:reason='missing_etf'
                elif any(c[0] not in prices for c in b['components']):reason='missing_hk_component'
                elif not lab:reason='missing_label'
                elif lab['prev_shares']<=0 or lab['label_prev_date']!=b['prev_date']:reason='label_previous_date_mismatch'
                elif not b['prev_nav']:reason='missing_previous_nav'
                elif (market,date) not in settle:reason='missing_actual_settlement'
                if reason:audit.append(dict(date=date,symbol=sym,reason=reason));continue
                try:e,amount=read_bar(ez,sym,date,index)
                except Exception as error:audit.append(dict(date=date,symbol=sym,reason=str(error)));continue
                hp=np.sum([prices[s]*q for s,q in b['components']],axis=0)
                a=(hp*mid[date]+b['cash'])/b['unit']
                lf=(hp*lag[(market,date)]+b['cash'])/b['unit']
                actual=settle.get((market,date),(np.nan,np.nan))
                ab=(hp*actual[0]+b['cash'])/b['unit'];ass=(hp*actual[1]+b['cash'])/b['unit']
                # 极端估值/份额单位异常的隔离检查；不按溢价正负筛样。
                ratio=np.nanmedian(e/a)
                if not np.isfinite(ratio) or not .85<ratio<1.15:
                    audit.append(dict(date=date,symbol=sym,reason='etf_iopv_ratio_outlier',ratio=float(ratio)));continue
                ser=pd.DataFrame(dict(date=date,symbol=sym,minute=times,etf=e,mid=a,
                    actual_settlement_buy=ab,actual_settlement_sell=ass,lag_settlement=lf,
                    amount=amount,hkd_assets=hp,cash=b['cash'],unit=b['unit'],
                    midpoint_fx=mid[date],actual_buy_fx=actual[0],lag_buy_fx=lag[(market,date)]))
                # ETF闭市后保留港股估值曲线；不伪造ETF成交价格。
                ser.loc[(ser['minute']>'15:01')|((ser['minute']>'11:31')&(ser['minute']<'13:01')),'etf']=np.nan
                end_series.append(ser)
                n=0
                for cut in CUTS:
                    feat=make_features(ser,b,lab,cut)
                    if feat:features.append(feat);n+=1
                daily.append(dict(date=date,symbol=sym,features=n,median_etf_mid_ratio=ratio,
                    finite_iopv_minutes=int(np.isfinite(a).sum()),net_baskets=lab['net_shares']/b['unit']))
        if end_series:pd.concat(end_series,ignore_index=True).to_parquet(OUT/'series'/(key+'.parquet'),index=False)
        if i%15==0:print('build',i+1,len(files),date,'funddays',len(daily),'features',len(features),flush=True)
    panel=pd.DataFrame(features);panel.to_parquet(OUT/'factor_panel.parquet',index=False)
    pd.DataFrame(audit).to_csv(OUT/'excluded.csv',index=False)
    pd.DataFrame(daily).to_csv(OUT/'fund_day_quality.csv',index=False)
    info=dict(rows=len(panel),funds=panel.symbol.nunique() if len(panel) else 0,
        dates=panel.date.nunique() if len(panel) else 0,excluded_reasons=dict(Counter(x['reason'] for x in audit)),
        minute_policy='bar_time + 1 minute; same-day ffill <=5 minutes; no future fill',
        scope='current website universe, compatible pure-HK basket reconstruction only',
        channel_assumption='ETF SH uses Shanghai, SZ uses Shenzhen; actual fund routing not observable',
        final_settlement_is_expost=True)
    (OUT/'panel_audit.json').write_text(json.dumps(info,ensure_ascii=False,indent=2));print(info,flush=True)

if __name__=='__main__':main()
