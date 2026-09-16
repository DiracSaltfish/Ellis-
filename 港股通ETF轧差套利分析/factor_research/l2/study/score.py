"""Score trusted local research model against already-built as-of features.
QMT transport remains deferred. Never load a joblib model from an untrusted party.
"""
from pathlib import Path
import argparse,json
import numpy as np,pandas as pd,joblib
from scipy.special import softmax

def score(d,model):
 if model.get('schema_version')!=1 or model.get('mode')!='research_only':raise ValueError('unsupported model contract')
 required=set(model['features'])|{'date','symbol','cutoff','unit','prev_shares','premium_coverage','l2_quality_valid'}
 if not required.issubset(d):raise ValueError('missing features/metadata: '+str(required-set(d)))
 if d.duplicated(['date','symbol','cutoff']).any():raise ValueError('duplicate prediction keys')
 if not d.cutoff.eq(model['cutoff']).all():raise ValueError('wrong model cutoff')
 if not d.date.gt(model['trained_through']).all():raise ValueError('prediction includes model fit/calibration period')
 if not (d.unit.gt(0)&d.prev_shares.gt(0)&d.premium_coverage.ge(.95)&d.l2_quality_valid.eq(True)).all():raise ValueError('input quality gate failed')
 x=d[model['features']]
 if np.isinf(x.to_numpy(float)).any():raise ValueError('infinite features')
 p=softmax(np.log(np.clip(model['classifier'].predict_proba(x),1e-12,1))/model['temperature'],axis=1)
 r=d[['date','symbol','cutoff','unit','prev_shares']].copy()
 for j,c in enumerate(['p_redeem','p_flat','p_create']):r[c]=p[:,j]
 regs=model['regressors'];point=np.maximum(-100,np.sinh(regs['point'].predict(x)));a=np.sinh(regs['lo'].predict(x));b=np.sinh(regs['hi'].predict(x));lo=np.maximum(-100,np.minimum(a,b)-model['interval_correction']);hi=np.maximum(lo,np.maximum(a,b)+model['interval_correction'])
 for name,value in [('pred',point),('lo',lo),('hi',hi)]:r[name+'_flow_pct']=value;r[name+'_shares']=value*r.prev_shares/100;r[name+'_baskets']=r[name+'_shares']/r.unit
 r['direction']=np.array(['net_redeem','flat','net_create'])[p.argmax(axis=1)];r['status']='research_estimate';r['fx_basis']=model['fx_basis'];r['model_trained_through']=model['trained_through']
 return r

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--model',required=True);ap.add_argument('--input',required=True);ap.add_argument('--output',required=True);args=ap.parse_args();p=Path(args.input);d=pd.read_parquet(p) if p.suffix=='.parquet' else pd.read_csv(p,dtype={'date':str,'symbol':str,'cutoff':str});m=joblib.load(args.model);r=score(d,m);out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True);r.to_csv(out,index=False);print(json.dumps(dict(rows=len(r),mode='research_only')))
if __name__=='__main__':main()
