"""One frozen chronological experiment; choose on validation, evaluate test once."""
from pathlib import Path
import sys,json,hashlib
import numpy as np,pandas as pd,joblib
from scipy.optimize import minimize_scalar
from scipy.special import softmax
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor
from sklearn.metrics import accuracy_score,balanced_accuracy_score,average_precision_score,log_loss
R=Path(__file__).resolve().parent;L=R.parent;sys.path.insert(0,str(L/'study'))
from analyze import BASE,L2,wilson
from score import score as score_old
from features import V2_FEATURES,FEATURE_VERSION
from model import score_v2,predict_arrays

def serial(x):
 if isinstance(x,np.generic):return x.item()
 raise TypeError(type(x).__name__)
def gate(z,mask):
 a=z[mask];n=len(a);k=int(a.net_shares.gt(0).sum());return dict(n=n,successes=k,precision=k/n if n else None,coverage=n/len(z),dates=a.date.nunique(),funds=a.symbol.nunique(),wilson95=wilson(k,n),redeem=int(a.net_shares.lt(0).sum()),flat=int(a.net_shares.eq(0).sum()))
def metrics(z):
 target=z.net_baskets;error=z.pred_baskets-target;y=np.sign(z.net_shares);pred=np.array([-1,0,1])[z[['p_redeem','p_flat','p_create']].to_numpy().argmax(axis=1)];pos=z[target>0];large=z[target>=20]
 return dict(n=len(z),dates=z.date.nunique(),funds=z.symbol.nunique(),accuracy=accuracy_score(y,pred),balanced_accuracy=balanced_accuracy_score(y,pred),zero_accuracy=float(y.eq(0).mean()),create_ap=average_precision_score(y>0,z.p_create),create_brier=float(((z.p_create-y.gt(0))**2).mean()),mae_U=float(error.abs().mean()),zero_mae_U=float(target.abs().mean()),mae_shares=float((z.pred_shares-z.net_shares).abs().mean()),mae_flow_pct=float((z.pred_flow_pct-z.net_flow_pct).abs().mean()),positive_n=len(pos),positive_mae_U=float((pos.pred_baskets-pos.net_baskets).abs().mean()) if len(pos) else None,large_create_n=len(large),large_create_mae_U=float((large.pred_baskets-large.net_baskets).abs().mean()) if len(large) else None,interval_coverage=float((z.net_baskets.ge(z.lo_baskets)&z.net_baskets.le(z.hi_baskets)).mean()),fixed_p90=gate(z,z.p_create.ge(.9)),selected_gate=gate(z,z.candidate),total_pred_shares=float(z.pred_shares.sum()),total_actual_shares=float(z.net_shares.sum()))
def addlabels(out,z,name):
 out=out.copy()
 for f in ['net_shares','net_baskets','net_flow_pct','market','split']:out[f]=z[f].to_numpy()
 out['variant']=name;return out

def main():
 p=pd.read_parquet(R/'panel.parquet');plan=json.loads((R/'plan.json').read_text());assert not p.date.eq('2026-09-02').any();(R/'models').mkdir(exist_ok=True);allpred=[];validation_results={};selected={};testresults={};thresholds=[]
 for cutoff in plan['cutoffs']:
  d=p[p.cutoff.eq(cutoff)].sort_values(['date','symbol']).reset_index(drop=True);tr=d[d.split.eq('train')];va=d[d.split.eq('validation')];te=d[d.split.eq('test')];assert tr.date.max()<va.date.min()<=va.date.max()<te.date.min();models={};vp={}
  common=dict(max_iter=200,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=60,l2_regularization=10,early_stopping=False,random_state=20260912)
  for name,names in [('old_features_asinh',BASE+L2),('enriched_asinh',BASE+L2+V2_FEATURES)]:
   print('fit',cutoff,name,'train',len(tr),'validation',len(va),flush=True)
   clf=HistGradientBoostingClassifier(**common).fit(tr[names],np.sign(tr.net_shares).astype(int));assert clf.classes_.tolist()==[-1,0,1]
   pp=clf.predict_proba(va[names]);temp=float(minimize_scalar(lambda t:log_loss(np.sign(va.net_shares).astype(int),softmax(np.log(np.clip(pp,1e-12,1))/t,axis=1),labels=[-1,0,1]),bounds=(.5,3),method='bounded').x)
   reg=HistGradientBoostingRegressor(**common).fit(tr[names],np.arcsinh(tr.net_flow_pct))
   models[name]=dict(schema_version=2,mode='research_only',features=names,feature_version=FEATURE_VERSION,classifier=clf,temperature=temp,quantity_kind='asinh',regressors={'point':reg},cutoff=cutoff,fit_through=tr.date.max(),trained_through=va.date.max(),training_ranges={f:[float(tr[f].min()),float(tr[f].max())] for f in ['fx_gap_bp','turnover_pct']},selected_threshold=None,interval_radius_pct=0,variant=name)
  names=BASE+L2+V2_FEATURES;model=models['enriched_asinh'].copy();regs={}
  for sign,key in [(1,'positive'),(-1,'negative')]:
   z=tr[tr.net_flow_pct*sign>0];regs[key]=HistGradientBoostingRegressor(loss='poisson',**common).fit(z[names],sign*z.net_flow_pct)
  model.update(quantity_kind='hurdle',regressors=regs,variant='enriched_hurdle');models['enriched_hurdle']=model
  for name,m in models.items():
   probs,point,_,_=predict_arrays(m,va[m['features']]);res=np.sort(abs(va.net_flow_pct.to_numpy()-point));rank=min(len(res),int(np.ceil((len(res)+1)*.8)));m['interval_radius_pct']=float(res[rank-1]);result=addlabels(score_v2(va,m,check_dates=False),va,name)
   choices=[]
   for th in np.arange(.5,1,.01):
    g=gate(result,result.p_create.ge(th)&~result.outside_training_range);thresholds.append(dict(cutoff=cutoff,variant=name,threshold=float(th),**g))
    if g['n']>=30 and g['dates']>=5 and g['funds']>=5 and g['precision']>=.9:choices.append((g['n'],-float(th)))
   m['selected_threshold']=-max(choices)[1] if choices else None;result=addlabels(score_v2(va,m,check_dates=False),va,name);vp[name]=result;validation_results[cutoff+'_'+name]=metrics(result)
  # Choose architecture only from enriched alternatives, by predeclared validation quantity MAE.
  chosen=min(['enriched_asinh','enriched_hurdle'],key=lambda name:validation_results[cutoff+'_'+name]['mae_U']);selected[cutoff]=dict(variant=chosen,criterion='minimum validation MAE_U among the two enriched models',validation_metrics=validation_results[cutoff+'_'+chosen]);print('selected before test',cutoff,chosen,flush=True)
  # Freeze selected artifacts before materializing test predictions.
  for name,m in models.items():joblib.dump(m,R/'models'/f'{cutoff.replace(":","")}_{name}.joblib')
  joblib.dump(models[chosen],R/'models'/f'{cutoff.replace(":","")}_selected.joblib');(R/'selection.json').write_text(json.dumps(selected,indent=2,default=serial))
  for name,m in models.items():
   z=addlabels(score_v2(te,m),te,name);allpred.extend([vp[name],z]);testresults[cutoff+'_'+name]={'all':metrics(z),'fresh':metrics(z[z.date.le('2026-04-08')]),'legacy_dates':metrics(z[z.date.ge('2026-04-22')])}
  old=joblib.load(L/'study/models'/f'{cutoff.replace(":","")}_premium_l2.joblib');z=addlabels(score_old(te,old),te,'v1_frozen');z['candidate']=z.p_create.ge(old['selected_threshold']) if old['selected_threshold'] is not None else False;z['outside_training_range']=False;allpred.append(z);testresults[cutoff+'_v1_frozen']={'all':metrics(z),'fresh':metrics(z[z.date.le('2026-04-08')]),'legacy_dates':metrics(z[z.date.ge('2026-04-22')])}
 pred=pd.concat(allpred,ignore_index=True);pred.to_parquet(R/'predictions.parquet',index=False);pred.to_csv(R/'predictions.csv',index=False);pd.DataFrame(thresholds).to_csv(R/'validation_thresholds.csv',index=False)
 (R/'metrics.json').write_text(json.dumps(dict(validation=validation_results,test=testresults),ensure_ascii=False,indent=2,default=serial));(R/'selection.json').write_text(json.dumps(selected,ensure_ascii=False,indent=2,default=serial))
 # Date-cluster resampling on fresh dates; selection already frozen. Not row-independent confidence.
 rng=np.random.default_rng(812);bs={}
 for cutoff,sel in selected.items():
  z=pred[(pred.cutoff==cutoff)&(pred.split=='test')&pred.date.le('2026-04-08')];a=z[z.variant.eq('old_features_asinh')].sort_values(['date','symbol']);b=z[z.variant.eq(sel['variant'])].sort_values(['date','symbol']);assert list(zip(a.date,a.symbol))==list(zip(b.date,b.symbol));a=a.reset_index(drop=True);b=b.reset_index(drop=True);dates=sorted(a.date.unique());diff=[]
  for _ in range(1000):
   ix=np.concatenate([np.flatnonzero(a.date.eq(day)) for day in rng.choice(dates,len(dates),replace=True)]);aa=a.iloc[ix];bb=b.iloc[ix];diff.append(float((aa.pred_baskets-aa.net_baskets).abs().mean()-(bb.pred_baskets-bb.net_baskets).abs().mean()))
  bs[cutoff]=dict(dates=len(dates),MAE_improvement_U_ci95=np.quantile(diff,[.025,.975]).tolist(),meaning='positive means enriched selected model lower MAE than same-data old-feature control; dates resampled')
 (R/'bootstrap.json').write_text(json.dumps(bs,indent=2));print('DONE',json.dumps({k:{'mae':v['all']['mae_U'],'fresh_mae':v['fresh']['mae_U'],'p90':v['fresh']['fixed_p90']} for k,v in testresults.items()},default=serial),flush=True)
if __name__=='__main__':main()
