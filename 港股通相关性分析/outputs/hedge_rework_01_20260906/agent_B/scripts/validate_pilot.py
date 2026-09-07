#!/usr/bin/env python3
"""Numerical reconciliation, day-block robustness and mark/timestamp sensitivity."""
import gzip
import json
import argparse
import numpy as np
import pandas as pd
import run_pilot as engine
from run_pilot import ROOT,TOOLS,labels,metrics,walkforward,bootstrap_daily


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',default=None)
    args=parser.parse_args()
    config=engine.load_config(args.config)
    engine.apply_config(config)
    data_root=engine.DATA_ROOT;output_root=engine.OUTPUT_ROOT
    points=pd.read_parquet(data_root/'normalized/pilot_minutes.parquet')
    residuals=pd.read_parquet(output_root/'reports/oos_residuals.parquet')
    folds=pd.read_csv(output_root/'reports/folds.csv',dtype={'test_date':str})
    checks=[]
    basket_path=engine.effective_path('basket_input',data_root/'raw/pilot_520600.jsonl.gz')
    with gzip.open(basket_path,'rt') as f:raw={r['date']:r for r in map(json.loads,f)}
    usable_dates=sorted(points.date.astype(str).unique())
    probe_dates=[usable_dates[0],usable_dates[len(usable_dates)//2],usable_dates[-1]]
    for date in probe_dates:
        record=raw[date];minute=850;missing=False
        value=0.
        for c in record['components']:
            code=f"{int(c['成分股代码']):05d}"
            if code not in record['hk']:
                missing=True;break
            eligible=[bar for bar in record['hk'][code] if bar[0]<=minute]
            if not eligible:
                missing=True;break
            value+=float(c['数量股'])*eligible[-1][-1]
        if missing: continue
        row=points[(points.date==date)&(points.minute==minute)].iloc[0]
        assert abs(value-row.basket_hkd)<1e-8
        indicative=(value*row.settlement_sell+float(record['header']['预估现金差额元']))/float(record['header']['最小申购赎回单位份'])
        assert abs(indicative-row.indicative_creation_value_per_share)<1e-12
        checks.append({'date':date,'minute':minute,'check':'independent_scalar_pcf_valuation','status':'passed'})
    sensitivity=[]
    for h in [5,15,30,60]:
        oos=residuals[residuals.horizon==h]
        for cutoff in [.02,.05]:
            subset=oos[oos.stale_weight<=cutoff]
            for model in ['selected','HHI_HTI_fixed_pair','HTI_FUT']:
                p=subset[subset.model==model]
                sensitivity.append({'horizon':h,'scenario':f'fresh_endpoint_{cutoff:.0%}_evaluation_subset','model':model,
                    'days':p.date.nunique(),**metrics(p.y.to_numpy(),p.residual.to_numpy())})
        # Freeze all baseline daily weights; only shift the hedge timestamps.
        for shift in [-1,1]:
            shifted=labels(points,h,shift)
            pieces={'selected':[],'HHI_HTI_fixed_pair':[]}
            for _,fold in folds[folds.horizon==h].iterrows():
                sample=shifted[shifted.date==fold.test_date]
                b=fold[TOOLS].to_numpy(dtype=float)
                for model,beta in [('selected',b),('HHI_HTI_fixed_pair',np.array([0,fold.fixed_pair_HHI_beta,fold.fixed_pair_HTI_beta,0,0,0,0,0]))]:
                    p=sample[['date','y']].copy();p['residual']=sample.y-sample[TOOLS].to_numpy()@beta;pieces[model].append(p)
            for model,data in pieces.items():
                p=pd.concat(data);sensitivity.append({'horizon':h,'scenario':f'hedge_timestamp_shift_{shift:+d}m_frozen_weights','model':model,
                    'days':p.date.nunique(),**metrics(p.y.to_numpy(),p.residual.to_numpy())})
        base=labels(points,h)
        for scenario,subset in [('fresh_2pct_refit',base[base.stale_weight<=.02]),('exclude_suspended_days_refit',base[base.frozen_weight==0])]:
            if subset.date.nunique()<61:
                sensitivity.append({'horizon':h,'scenario':scenario,'status':'insufficient_training_days','days':subset.date.nunique()});continue
            training=config.get('training',{})
            result,_=walkforward(subset,h,int(training.get('train_days',60)),int(training.get('validation_days',10)))
            for model in ['selected','HHI_HTI_fixed_pair','HTI_FUT']:
                p=result[result.model==model]
                sensitivity.append({'horizon':h,'scenario':scenario,'model':model,'days':p.date.nunique(),
                    'first_oos':p.date.min(),'last_oos':p.date.max(),**metrics(p.y.to_numpy(),p.residual.to_numpy())})
            print('validated',h,scenario,flush=True)
    pd.DataFrame(sensitivity).to_csv(output_root/'reports/sensitivity.csv',index=False)
    additional=[]
    for h in [5,15,30,60]:
        p=residuals[(residuals.horizon==h)&(residuals.model=='HHI_HTI_fixed_pair')]
        additional.append({'horizon':h,'model':'HHI_HTI_fixed_pair','ci95':bootstrap_daily(p)})
    (output_root/'reports/validation.json').write_text(json.dumps({'scalar_checks':checks,'bootstrap':additional,
        'conclusion':'Preliminary price risk evidence; excluded complex entitlement days and explicit stale-mark assumptions remain.',
        'not_claimed':['executable arbitrage profits','full universe coverage','full corporate action certification']},indent=2))


if __name__=='__main__':main()
