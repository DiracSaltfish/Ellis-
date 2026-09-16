"""Reusable v2 research scoring. No live feeds or trading actions."""
import numpy as np,pandas as pd
from scipy.special import softmax

def predict_arrays(model,x):
 p=softmax(np.log(np.clip(model['classifier'].predict_proba(x),1e-12,1))/model['temperature'],axis=1)
 if model['quantity_kind']=='asinh':point=np.sinh(model['regressors']['point'].predict(x));pos=neg=np.full(len(x),np.nan)
 elif model['quantity_kind']=='hurdle':
  pos=model['regressors']['positive'].predict(x);neg=model['regressors']['negative'].predict(x);point=p[:,2]*pos-p[:,0]*neg
 else:raise ValueError('unsupported quantity model')
 return p,np.maximum(-100,point),pos,neg

def score_v2(d,model,check_dates=True):
 if model.get('schema_version')!=2 or model.get('mode')!='research_only':raise ValueError('incompatible v2 model')
 required=set(model['features'])|{'date','symbol','cutoff','unit','prev_shares','premium_coverage','l2_quality_valid'}
 if not required.issubset(d):raise ValueError('missing required features: '+str(required-set(d)))
 if d.duplicated(['date','symbol','cutoff']).any():raise ValueError('duplicate rows')
 if not d.cutoff.eq(model['cutoff']).all():raise ValueError('wrong cutoff')
 if check_dates and not d.date.gt(model['trained_through']).all():raise ValueError('model fitted or calibrated at/after input date')
 if not (d.unit.gt(0)&d.prev_shares.gt(0)&d.premium_coverage.ge(.95)&d.l2_quality_valid.eq(True)).all():raise ValueError('quality gate failed')
 x=d[model['features']]
 if np.isinf(x.to_numpy(float)).any():raise ValueError('infinite feature')
 # Only explicitly unknown PCF limits may be NaN; do not silently score missing L2 or prices.
 regular=[f for f in model['features'] if f not in ['v2_creation_limit_pct','v2_redemption_limit_pct']]
 if x[regular].isna().any().any():raise ValueError('missing non-limit feature')
 p,point,pos,neg=predict_arrays(model,x);r=d[['date','symbol','cutoff','unit','prev_shares']].copy()
 for j,col in enumerate(['p_redeem','p_flat','p_create']):r[col]=p[:,j]
 radius=model.get('interval_radius_pct',0);low=np.maximum(-100,point-radius);high=np.maximum(low,point+radius)
 for name,value in [('pred',point),('lo',low),('hi',high)]:r[name+'_flow_pct']=value;r[name+'_shares']=value*r.prev_shares/100;r[name+'_baskets']=r[name+'_shares']/r.unit
 r['conditional_create_baskets']=pos*r.prev_shares/100/r.unit;r['conditional_redeem_baskets']=neg*r.prev_shares/100/r.unit
 r['direction']=np.array(['net_redeem','flat','net_create'])[p.argmax(axis=1)]
 threshold=model.get('selected_threshold');r['candidate']=False if threshold is None else r.p_create.ge(threshold)
 ranges=model['training_ranges'];ood=np.zeros(len(d),bool)
 for f in ['fx_gap_bp','turnover_pct']:ood|=(d[f].to_numpy()<ranges[f][0])|(d[f].to_numpy()>ranges[f][1])
 r['outside_training_range']=ood;r['candidate']=r.candidate&~r.outside_training_range;r['status']='research_estimate';r['fx_basis']='previous_available_settlement';r['trained_through']=model['trained_through'];return r
