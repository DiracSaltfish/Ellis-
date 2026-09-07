#!/usr/bin/env python3
"""520600 minute-price hedge study: same-day labels, nested walk-forward.
No transaction volumes, bid/ask, fills or supposed arbitrage profits.
"""
import argparse
import gzip
import itertools
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_TOOLS=['HSI_FUT','HHI_FUT','HTI_FUT','02800','02828','03032','03033','02845']
DEFAULT_FAMILIES=['HSI','HHI','HSTECH','HSI','HHI','HSTECH','HSTECH','EV_PROXY']
TOOLS=list(DEFAULT_TOOLS)
FAMILIES=list(DEFAULT_FAMILIES)
HORIZONS=[5,15,30,60]
DATA_ROOT=ROOT/'data'
OUTPUT_ROOT=ROOT
RUN_CONFIG={}


def _resolve_path(value):
    path=Path(value)
    return path if path.is_absolute() else ROOT/path


def load_config(path=None):
    """Load the effective run configuration; CLI overrides are applied in main."""
    path=Path(path) if path else ROOT/'config/research_520600.json'
    with path.open(encoding='utf-8') as f: config=json.load(f)
    config['_config_path']=str(path.resolve())
    return config


def apply_config(config):
    """Make configured inputs, outputs, tools and split parameters active."""
    global TOOLS,FAMILIES,HORIZONS,DATA_ROOT,OUTPUT_ROOT,RUN_CONFIG
    TOOLS=list(config.get('tools',DEFAULT_TOOLS))
    family_map=config.get('tool_families',dict(zip(DEFAULT_TOOLS,DEFAULT_FAMILIES)))
    FAMILIES=[family_map.get(name,'UNKNOWN') for name in TOOLS]
    HORIZONS=list(config.get('horizons_minutes',[5,15,30,60]))
    OUTPUT_ROOT=_resolve_path(config.get('output_root','.'))
    DATA_ROOT=_resolve_path(config.get('data_root','data'))
    OUTPUT_ROOT.mkdir(parents=True,exist_ok=True)
    DATA_ROOT.mkdir(parents=True,exist_ok=True)
    RUN_CONFIG=config


def effective_path(key, default):
    value=RUN_CONFIG.get(key,default)
    return _resolve_path(value)


def minute_series(bars):
    """The input label is treated as minute start; price usable at label+1.
    Intraday LAST carry is an explicit preliminary mark, never backward fill.
    """
    values=pd.Series(np.nan,index=np.arange(570,961),dtype=float)
    stamps=values.copy()
    for minute,op,hi,lo,close in bars:
        if 570<=minute<=960:values.loc[minute]=close;stamps.loc[minute]=minute
    return values.ffill(),stamps.ffill()


def prepare():
    basket_path=effective_path('basket_input',DATA_ROOT/'raw/pilot_520600.jsonl.gz')
    fx_path=effective_path('fx_source',DATA_ROOT/'raw/sse_settlement_rates.csv')
    future_dir=effective_path('future_data_dir',DATA_ROOT/'raw')
    window=RUN_CONFIG.get('window',{})
    start_date=str(window.get('start','00000000')).replace('-','')
    end_date=str(window.get('end','99999999')).replace('-','')
    with gzip.open(basket_path,'rt') as f:
        days=[json.loads(line) for line in f]
    days=[day for day in days if start_date<=day['date']<=end_date]
    fx=pd.read_csv(fx_path,dtype={'适用日期':str}).set_index('适用日期')
    assert fx.index.is_unique
    futures={}
    for name in TOOLS[:3]:
        f=pd.read_csv(future_dir/f'{name}_1min.csv',dtype={'contract_month':str})
        f=f[f.series_role=='primary'].copy()
        stamp=pd.to_datetime(f.timestamp,utc=True).dt.tz_convert('Asia/Hong_Kong')
        f['date']=stamp.dt.strftime('%Y%m%d');f['minute']=stamp.dt.hour*60+stamp.dt.minute
        assert not f.duplicated(['date','minute']).any()
        futures[name]={d:part.set_index('minute') for d,part in f.groupby('date')}
    points=[];quality=[];last_observed={};component_weights=[]
    for day in days:
        date=day['date'];iso=date[:4]+'-'+date[4:6]+'-'+date[6:]
        minute=np.arange(780,900)  # available 13:01..15:00; avoids 15:00 bar look-ahead
        qrow={'date':date,'components':len(day['components']),'missing_members':','.join(day['missing_members'])}
        series={code:minute_series(bars) for code,bars in day['hk'].items()}
        event_strategy=RUN_CONFIG.get('event_strategy','pilot_verified_v1')
        exclude=event_strategy=='pilot_verified_v1' and '00489' in day['missing_members']
        if exclude:qrow['status']='EXCLUDED_COMPLEX_CORPORATE_ACTION'
        if iso not in fx.index:raise ValueError('missing actual settlement rate '+iso)
        buy=float(fx.loc[iso,'买入结算汇兑比率']);sell=float(fx.loc[iso,'卖出结算汇兑比率'])
        basket=np.zeros(len(minute));stale=np.zeros(len(minute));frozen=np.zeros(len(minute));missing=[]
        daily_weights=[]
        for c in day['components']:
            code=f"{int(c['成分股代码']):05d}";qty=float(c['数量股'])
            if qty<0:raise ValueError('negative component quantity')
            if code in series:
                px,ts=series[code];px=px.reindex(minute).to_numpy();age=minute-ts.reindex(minute).to_numpy()
            elif code in last_observed and any(e['code']==code and e['start_date']<=date<e['end_date_exclusive'] for e in RUN_CONFIG.get('verified_halts',[])):
                px=np.full(len(minute),last_observed[code]);age=np.full(len(minute),np.inf);frozen+=qty*px
            else:
                missing.append(code);px=np.full(len(minute),np.nan);age=px
            val=qty*px;basket+=val;stale+=np.where(age>5,val,0)
            daily_weights.append((code,c['成分股名称'],float(val[-1])))
        for code,(px,ts) in series.items():
            valid=px.dropna()
            if len(valid):last_observed[code]=float(valid.iloc[-1])
        if missing and not exclude:qrow['status']='EXCLUDED_UNKNOWN_MISSING_PRICE'
        if not exclude and not missing:
            table=pd.DataFrame({'date':date,'minute':minute,'basket_hkd':basket,
                'stale_weight':stale/basket,'frozen_weight':frozen/basket,'settlement_buy':buy,'settlement_sell':sell})
            for name in TOOLS[3:]:table[name]=series[name][0].reindex(minute).to_numpy() if name in series else np.nan
            for name in TOOLS[:3]:
                part=futures[name].get(date)
                if part is None:table[name]=np.nan;table[name+'_contract']=''
                else:
                    table[name]=part['close'].reindex(minute).to_numpy()
                    table[name+'_contract']=part['local_symbol'].reindex(minute).fillna('').to_numpy()
            unit=float(day['header']['最小申购赎回单位份']);cash=float(day['header']['预估现金差额元'])
            cn,_=minute_series(day['cn']);table['etf_price']=cn.reindex(minute).to_numpy()
            table['basket_cny']=basket*sell
            table['indicative_creation_value_per_share']=(basket*sell+cash)/unit
            table['indicative_redemption_value_per_share']=(basket*buy+cash)/unit
            # Cash is a fixed daily PCF estimate; this is NOT official NAV/IOPV.
            table['expost_premium_bps']=(table.etf_price/table.indicative_creation_value_per_share-1)*10000
            table['known_at_minute']=minute+1
            table['pcf_creation_unit']=unit
            points.append(table)
            qrow.update(status='INCLUDED_PRICE_MARK',valid_basket_minutes=int(np.isfinite(basket).sum()),
                mean_stale_weight=float(np.nanmean(stale/basket)),max_stale_weight=float(np.nanmax(stale/basket)),
                frozen_weight=float(frozen[-1]/basket[-1]))
            for code,name,val in daily_weights:component_weights.append({'date':date,'code':code,'name':name,'weight':val/basket[-1]})
        quality.append(qrow)
    output=pd.concat(points,ignore_index=True)
    (DATA_ROOT/'normalized').mkdir(parents=True,exist_ok=True)
    (OUTPUT_ROOT/'reports').mkdir(parents=True,exist_ok=True)
    output.to_parquet(DATA_ROOT/'normalized/pilot_minutes.parquet',index=False)
    pd.DataFrame(quality).to_csv(OUTPUT_ROOT/'reports/data_quality.csv',index=False)
    pd.DataFrame(component_weights).to_csv(OUTPUT_ROOT/'reports/component_weights.csv',index=False)
    return output,pd.DataFrame(quality)


def labels(points,horizon,shift=0):
    """Endpoint returns with fixed quantities and a single actual contract.
    All candidate methods are evaluated on identical complete rows.
    """
    pieces=[]
    for date,day in points.groupby('date',sort=True):
        day=day.sort_values('minute').reset_index(drop=True)
        hedge=day[TOOLS].shift(shift)
        out=pd.DataFrame({'date':date,'minute':day.minute,'end_minute':day.minute+horizon,
            'y':day.basket_hkd.shift(-horizon)/day.basket_hkd-1,
            'stale_weight':np.maximum(day.stale_weight,day.stale_weight.shift(-horizon)),
            'frozen_weight':day.frozen_weight})
        for name in TOOLS:out[name]=hedge[name].shift(-horizon)/hedge[name]-1
        mask=(day.minute.shift(-horizon)-day.minute==horizon)
        for name in TOOLS[:3]:
            contract=day[name+'_contract'].shift(shift)
            mask &= (contract==contract.shift(-horizon)) & contract.ne('')
        mask &= out[['y',*TOOLS]].notna().all(axis=1)
        out=out[mask].copy()
        pieces.append(out)
    return pd.concat(pieces,ignore_index=True)


def ridge_fit(x,y,alpha):
    """Exact 1/2-variable constrained ridge: 0<=beta<=2, sum<=2.5.
    No alpha/intercept is credited to hedge PnL.
    """
    k=x.shape[1]
    if k==0:return np.empty(0)
    g=x.T@x/len(y);z=x.T@y/len(y)
    a=g+np.eye(k)*(alpha*np.trace(g)/k+1e-16)
    if k==1:return np.array([np.clip(z[0]/a[0,0],0,2)])
    candidates=[]
    b=np.linalg.solve(a,z)
    if np.all(b>=0) and np.all(b<=2) and b.sum()<=2.5:candidates.append(b)
    for j in [0,1]:
        other=1-j
        for fixed in [0.,2.]:
            b=np.zeros(2);b[j]=fixed;b[other]=np.clip((z[other]-a[other,j]*fixed)/a[other,other],0,min(2,2.5-fixed));candidates.append(b)
    base=np.array([0.,2.5]);direction=np.array([1.,-1.])
    t=np.clip(direction@(z-a@base)/(direction@a@direction),.5,2.)
    candidates.append(base+t*direction)
    return min(candidates,key=lambda b:b@a@b-2*z@b)


def score(residual):
    # Price-risk only; no assertion about transaction costs or liquidity.
    k=max(1,int(np.ceil(len(residual)*.05)))
    return np.sqrt(np.mean(residual**2))+.2*max(np.sort(residual)[-k:].mean(),np.sort(-residual)[-k:].mean())


def metrics(y,residual):
    k=max(1,int(np.ceil(len(y)*.05)))
    return {'samples':len(y),'target_std_bps':float(np.std(y,ddof=1)*10000),
        'residual_std_bps':float(np.std(residual,ddof=1)*10000),
        'variance_reduction':float(1-np.var(residual,ddof=1)/np.var(y,ddof=1)),
        'residual_mean_bps':float(np.mean(residual)*10000),
        'upside_es95_bps':float(np.sort(residual)[-k:].mean()*10000),
        'downside_es95_bps':float(np.sort(-residual)[-k:].mean()*10000),
        'abs_p95_bps':float(np.quantile(np.abs(residual),.95)*10000)}


def walkforward(sample,horizon,train_days=60,validation_days=10):
    dates=sorted(sample.date.unique());n=len(TOOLS)
    if validation_days>=train_days: raise ValueError('validation_days must be less than train_days')
    fit_days=train_days-validation_days
    combos=[()]+[(j,) for j in range(n)]+[pair for pair in itertools.combinations(range(n),2) if FAMILIES[pair[0]]!=FAMILIES[pair[1]]]
    out=[];folds=[]
    for index in range(train_days,len(dates)):
        train_dates=dates[index-train_days:index];test_date=dates[index]
        fit=sample[sample.date.isin(train_dates[:fit_days])];valid=sample[sample.date.isin(train_dates[fit_days:])]
        train=sample[sample.date.isin(train_dates)];test=sample[sample.date==test_date]
        assert fit.date.max()<valid.date.min() and train.date.max()<test.date.min()
        xf=fit[TOOLS].to_numpy();yf=fit.y.to_numpy();xv=valid[TOOLS].to_numpy();yv=valid.y.to_numpy()
        best=(float('inf'),(),0.)
        for legs in combos:
            for alpha in ([0.] if not legs else [0.,.01,.1]):
                beta=ridge_fit(xf[:,legs],yf,alpha)
                value=score(yv-xv[:,legs]@beta)
                if value<best[0]-1e-12:best=(value,legs,alpha)
        xr=train[TOOLS].to_numpy();yr=train.y.to_numpy();xt=test[TOOLS].to_numpy();yt=test.y.to_numpy()
        value,legs,alpha=best;beta=ridge_fit(xr[:,legs],yr,alpha)
        models={'no_hedge':((),np.empty(0)),'selected':(legs,beta)}
        for j,name in enumerate(TOOLS):models[name]=((j,),ridge_fit(xr[:,[j]],yr,.01))
        models['HHI_HTI_fixed_pair']=((1,2),ridge_fit(xr[:,[1,2]],yr,.01))
        for name,(legids,b) in models.items():
            result=test[['date','minute','end_minute','y','stale_weight','frozen_weight']].copy()
            result['residual']=yt-xt[:,legids]@b;result['model']=name;result['horizon']=horizon
            out.append(result)
        weights={TOOLS[j]:float(b) for j,b in zip(legs,beta)}
        folds.append({'horizon':horizon,'test_date':test_date,'fit_start':train_dates[0],'inner_fit_end':train_dates[fit_days-1],
                      'validation_start':train_dates[fit_days],'train_end':train_dates[-1],
                      'train_rows':len(train),'test_rows':len(test),'selection_score':value,
                      'alpha':alpha,'legs':'+'.join(weights) or 'no_hedge',**{name:weights.get(name,0.) for name in TOOLS},
                      'fixed_pair_HHI_beta':float(models['HHI_HTI_fixed_pair'][1][0]),
                      'fixed_pair_HTI_beta':float(models['HHI_HTI_fixed_pair'][1][1])})
    if not out:raise ValueError('fewer than 61 usable days')
    return pd.concat(out,ignore_index=True),pd.DataFrame(folds)


def bootstrap_daily(part,seed=520600,repeats=1000):
    # Resample complete trading days; never treat overlapping minute labels as IID.
    stats=[]
    for day,x in part.groupby('date'):
        y=x.y.to_numpy();r=x.residual.to_numpy();stats.append([len(x),y.sum(),(y*y).sum(),r.sum(),(r*r).sum()])
    stats=np.array(stats);rng=np.random.default_rng(seed)
    boot=stats[rng.integers(0,len(stats),(repeats,len(stats)))].sum(axis=1)
    count,sy,sy2,sr,sr2=boot.T
    reductions=1-(sr2/count-(sr/count)**2)/(sy2/count-(sy/count)**2)
    return [float(x) for x in np.quantile(reductions,[.025,.975])]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cached',action='store_true',help='Reuse normalized data after explicitly validating its provenance')
    parser.add_argument('--config',default=None,help='Effective JSON research configuration')
    parser.add_argument('--fund-id',default=None)
    parser.add_argument('--start-date',default=None)
    parser.add_argument('--end-date',default=None)
    parser.add_argument('--channel',default=None)
    parser.add_argument('--fx-source',default=None)
    parser.add_argument('--data-root',default=None)
    parser.add_argument('--output-root',default=None)
    parser.add_argument('--event-strategy',default=None)
    parser.add_argument('--tools',default=None,help='Comma-separated configured tool IDs')
    parser.add_argument('--train-days',type=int,default=None)
    parser.add_argument('--validation-days',type=int,default=None)
    parser.add_argument('--seed',type=int,default=None)
    args=parser.parse_args()
    config=load_config(args.config)
    if args.fund_id: config['fund_id']=args.fund_id
    if args.channel: config['channel']=args.channel
    if args.fx_source: config['fx_source']=args.fx_source
    if args.data_root: config['data_root']=args.data_root
    if args.output_root: config['output_root']=args.output_root
    if args.event_strategy: config['event_strategy']=args.event_strategy
    if args.tools: config['tools']=[x.strip() for x in args.tools.split(',') if x.strip()]
    window=config.setdefault('window',{})
    if args.start_date: window['start']=args.start_date
    if args.end_date: window['end']=args.end_date
    training=config.setdefault('training',{})
    if args.train_days is not None: training['train_days']=args.train_days
    if args.validation_days is not None: training['validation_days']=args.validation_days
    if args.seed is not None: config['bootstrap_seed']=args.seed
    apply_config(config)
    train_days=int(training.get('train_days',60));validation_days=int(training.get('validation_days',10))
    bootstrap_seed=int(config.get('bootstrap_seed',520600))
    if args.cached:
        points=pd.read_parquet(DATA_ROOT/'normalized/pilot_minutes.parquet');quality=pd.read_csv(OUTPUT_ROOT/'reports/data_quality.csv')
    else:points,quality=prepare()
    (DATA_ROOT/'normalized').mkdir(parents=True,exist_ok=True)
    (OUTPUT_ROOT/'reports').mkdir(parents=True,exist_ok=True)
    summaries=[];all_oos=[];all_folds=[];label_audit=[]
    for h in HORIZONS:
        data=labels(points,h)
        data.to_parquet(DATA_ROOT/f'normalized/labels_{h}m.parquet',index=False)
        oos,folds=walkforward(data,h,train_days,validation_days);all_oos.append(oos);all_folds.append(folds)
        for name,part in oos.groupby('model'):
            summary={'horizon':h,'model':name,'days':part.date.nunique(),**metrics(part.y.to_numpy(),part.residual.to_numpy())}
            if name=='selected':summary['variance_reduction_ci95']=bootstrap_daily(part,seed=bootstrap_seed)
            summaries.append(summary)
        label_audit.append({'horizon':h,'usable_days':data.date.nunique(),'labels':len(data),'oos_days':oos.date.nunique(),'first_oos':oos.date.min(),'last_oos':oos.date.max()})
        print('finished',h,'minutes',label_audit[-1],flush=True)
    combined=pd.concat(all_oos,ignore_index=True);folds=pd.concat(all_folds,ignore_index=True)
    combined.to_parquet(OUTPUT_ROOT/'reports/oos_residuals.parquet',index=False)
    folds.to_csv(OUTPUT_ROOT/'reports/folds.csv',index=False)
    pd.DataFrame(summaries).to_csv(OUTPUT_ROOT/'reports/model_comparison.csv',index=False)
    results={'scope':'INITIAL_PRICE_ONLY_RESEARCH','target':config.get('fund_id','520600.SH'),
        'window':[config.get('window',{}).get('start','2026-03-03'),config.get('window',{}).get('end','2026-08-03')],
        'channel':config.get('channel','UNKNOWN'),'event_strategy':config.get('event_strategy','pilot_verified_v1'),
        'fx_source':str(effective_path('fx_source',DATA_ROOT/'raw/sse_settlement_rates.csv')),
        'contract_map_path':str(effective_path('contract_map_path',ROOT/'data/inventory/futures_contract_probe.csv')),
        'config_path':config.get('_config_path'),'data_root':str(DATA_ROOT),'output_root':str(OUTPUT_ROOT),
        'training':{'train_days':train_days,'validation_days':validation_days,'outer_test_days':config.get('training',{}).get('outer_test_days',1)},
        'bootstrap_seed':bootstrap_seed,
        'session':'13:01..15:00 Asia/Hong_Kong; prices at minute end under explicit timestamp assumption',
        'cash_substitution':'all components forced cash, user assumption',
        'family_exclusivity':dict(zip(TOOLS,FAMILIES)),
        'fx':'SSE actual daily settlement, sell rate for creation cash; evaluation only, not a live feature',
        'file_days':100,'quality_status_counts':quality.status.value_counts().to_dict(),'label_audit':label_audit,'metrics':summaries}
    (OUTPUT_ROOT/'reports/results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
