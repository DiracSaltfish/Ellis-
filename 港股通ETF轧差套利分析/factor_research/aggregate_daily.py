"""用户修订：使用整日所有有效分钟，每基金日仅一行，研究完整路径。"""
from pathlib import Path
import gzip,json
import numpy as np
import pandas as pd
from build_panel import labels,make_features

R=Path(__file__).resolve().parent;I=R/'inputs';O=R/'results'

def longest_run(values,times):
    longest=run=0;last=None
    for good,t in zip(values,times):
        if last is not None and (t-last).total_seconds()>60:run=0
        run=run+1 if good else 0;longest=max(longest,run);last=t
    return longest

def main():
    labs=labels();result=[];rejected=[]
    for file in sorted((O/'series').glob('*.parquet')):
        with gzip.open(I/'baskets'/(file.stem+'.json.gz'),'rt') as h:baskets=json.load(h)
        day=pd.read_parquet(file)
        for symbol,series in day.groupby('symbol',sort=False):
            date=series.date.iloc[0];b=baskets[symbol];lab=labs[(symbol,date)]
            f=make_features(series,b,lab,'15:01')
            if not f:rejected.append(dict(date=date,symbol=symbol,reason='incomplete_full_day'));continue
            f['cut']='all_day'
            mask=((series.minute>='09:31')&(series.minute<='11:31'))|((series.minute>='13:01')&(series.minute<='15:01'))
            s=series.loc[mask].dropna(subset=['etf','mid','actual_settlement_buy']).copy()
            f['valid_minutes']=len(s)
            e=s.etf.to_numpy();m=s.mid.to_numpy();a=s.actual_settlement_buy.to_numpy();amount=s.amount.to_numpy()
            ts=pd.to_datetime(s.date+' '+s.minute);pm=(e/m-1)*10000;ps=(e/a-1)*10000
            f['between_iopv_fraction']=float(np.mean((e>a)&(e<m)))
            for prefix,p in [('mid',pm),('settlement',ps)]:
                f[prefix+'_std_bp']=float(p.std())
                f[prefix+'_p10_bp']=float(np.quantile(p,.1));f[prefix+'_p90_bp']=float(np.quantile(p,.9))
                f[prefix+'_longest_above30_minutes']=longest_run(p>30,ts)
                f[prefix+'_crossings']=int(np.count_nonzero(np.diff(p>0)))
                f[prefix+'_amount_weighted_bp']=float(np.average(p,weights=amount)) if amount.sum()>0 else np.nan
                f[prefix+'_late_minus_early_bp']=float(p[-30:].mean()-p[:30].mean())
                f[prefix+'_slope_bp_per100min']=float(np.polyfit(np.arange(len(p)),p,1)[0]*100)
            er=np.diff(np.log(e));mr=np.diff(np.log(m));same=(ts.diff().dt.total_seconds().iloc[1:]==60).to_numpy()
            er=er[same];mr=mr[same]
            f['tracking_error_bp']=float(np.std(er-mr)*10000)
            f['return_correlation']=float(np.corrcoef(er,mr)[0,1]) if np.std(er)>0 and np.std(mr)>0 else np.nan
            f['signed_turnover_pct']=float(np.sum(np.sign(np.diff(e))*amount[1:])/(lab['prev_shares']*b['prev_nav'])*100)
            # 港股15点后区间独立保存为诊断字段，不混入ETF可交易时段价差。
            hk=series.dropna(subset=['mid']);after=hk[hk.minute>='15:01']
            f['diagnostic_hk_after_close_return_bp']=(after.mid.iloc[-1]/after.mid.iloc[0]-1)*10000 if len(after)>1 else np.nan
            result.append(f)
    d=pd.DataFrame(result)
    assert not d.duplicated(['date','symbol']).any()
    d.to_parquet(O/'factor_panel.parquet',index=False)
    pd.DataFrame(rejected).to_csv(O/'daily_aggregation_exclusions.csv',index=False)
    audit=json.loads((O/'panel_audit.json').read_text());audit.update(rows=len(d),funds=d.symbol.nunique(),
        dates=d.date.nunique(),observation='one complete daily path per fund/date; all valid overlapping trading minutes',
        valid_minute_total=int(d.valid_minutes.sum()),valid_minutes_quantiles=d.valid_minutes.quantile([0,.5,1]).to_dict())
    (O/'panel_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
    print(audit)

if __name__=='__main__':main()
