"""Fixed chronological holdout, L2 ablation, direction and quantity forecasts."""
from pathlib import Path
import json,hashlib
import numpy as np,pandas as pd,joblib
from scipy.optimize import minimize_scalar
from scipy.special import softmax
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor
from sklearn.metrics import accuracy_score,balanced_accuracy_score,average_precision_score,roc_auc_score,log_loss,confusion_matrix,r2_score
R=Path(__file__).resolve().parent
BASE=['lag_flow_pct','lag5_flow_pct','log_prev_assets','creation_allowed','redemption_allowed','turnover_pct','etf_return_bp','basket_return_bp','fx_gap_bp','settlement_mean_bp','settlement_p10_bp','settlement_std_bp','settlement_premium_bp','settlement_max_bp','settlement_positive_fraction','settlement_above30_fraction','settlement_above50_fraction','settlement_change15_bp','mid_mean_bp','mid_premium_bp','mid_max_bp','mid_positive_fraction','mid_change15_bp']
L2=['l2_active_imbalance','l2_buy_large_parent_frac','l2_sell_large_parent_frac','l2_buy_unit_parent_excess','l2_sell_unit_parent_excess','l2_buy_unit_burst_excess','l2_sell_unit_burst_excess','l2_sell_compression_frac','l2_buy_compression_frac','l2_passive_sell_unit_frac']

def serial(v):
 if isinstance(v,(np.integer,)):return int(v)
 if isinstance(v,(np.floating,)):return float(v) if np.isfinite(v) else None
 if isinstance(v,np.ndarray):return v.tolist()
 raise TypeError(type(v).__name__)
def probabilities(clf,x,temp):return softmax(np.log(np.clip(clf.predict_proba(x),1e-12,1))/temp,axis=1)
def wilson(k,n):
 if not n:return [None,None]
 z=1.96;p=k/n;d=1+z*z/n;c=(p+z*z/(2*n))/d;r=z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/d;return [c-r,c+r]
def gate_stats(d,mask):
 a=d[mask];n=len(a);k=int((a.net_shares>0).sum())
 return dict(n=n,dates=a.date.nunique(),funds=a.symbol.nunique(),coverage=n/len(d),precision=k/n if n else None,wilson95=wilson(k,n),net_redeem=int((a.net_shares<0).sum()),flat=int((a.net_shares==0).sum()),median_actual_baskets=float(a.net_baskets.median()) if n else None)
def metrics(d):
 y=np.sign(d.net_shares).astype(int);proba=d[['p_redeem','p_flat','p_create']].to_numpy();pred=np.array([-1,0,1])[proba.argmax(axis=1)];e=d.pred_flow_pct-d.net_flow_pct
 result=dict(n=len(d),dates=d.date.nunique(),class_counts=y.value_counts().sort_index().to_dict(),accuracy=accuracy_score(y,pred),balanced_accuracy=balanced_accuracy_score(y,pred),always_flat_accuracy=float((y==0).mean()),create_ap=average_precision_score(y==1,proba[:,2]),create_auc=roc_auc_score(y==1,proba[:,2]) if len(set(y==1))>1 else None,create_brier=float(np.mean((proba[:,2]-(y==1))**2)),log_loss=log_loss(y,proba,labels=[-1,0,1]),confusion_rows_actual_cols_pred=confusion_matrix(y,pred,labels=[-1,0,1]).tolist(),mae_flow_pct=float(abs(e).mean()),zero_mae_flow_pct=float(abs(d.net_flow_pct).mean()),lag_mae_flow_pct=float(abs(d.lag_flow_pct-d.net_flow_pct).mean()),mae_shares=float(abs(d.pred_shares-d.net_shares).mean()),zero_mae_shares=float(abs(d.net_shares).mean()),mae_baskets=float(abs(d.pred_baskets-d.net_baskets).mean()),zero_mae_baskets=float(abs(d.net_baskets).mean()),median_abs_baskets=float(abs(d.pred_baskets-d.net_baskets).median()),quantity_r2_flow=r2_score(d.net_flow_pct,d.pred_flow_pct),interval80_coverage=float(((d.net_flow_pct>=d.lo_flow_pct)&(d.net_flow_pct<=d.hi_flow_pct)).mean()),interval80_mean_width_pct=float((d.hi_flow_pct-d.lo_flow_pct).mean()),predicted_total_shares=float(d.pred_shares.sum()),actual_total_shares=float(d.net_shares.sum()))
 nz=d[d.net_shares.ne(0)];result['nonzero']=dict(n=len(nz),mae_baskets=float(abs(nz.pred_baskets-nz.net_baskets).mean()),zero_mae_baskets=float(abs(nz.net_baskets).mean()),quantity_sign_accuracy=float((np.sign(nz.pred_shares)==np.sign(nz.net_shares)).mean()))
 result['fixed_p90']=gate_stats(d,d.p_create>=.9)
 return result

def main():
 p=pd.read_parquet(R/'panel.parquet');dates=sorted(p.date.unique());h=max(4,len(dates)//5);split=dict(train=dates[:-2*h],validation=dates[-2*h:-h],test=dates[-h:]);assert len(split['train'])>=8
 (R/'split.json').write_text(json.dumps(split,indent=2));(R/'models').mkdir(exist_ok=True)
 print('split',split,flush=True);results={};predictions=[];factors=[];importance=[];thresholds=[]
 for cutoff in ['14:30','14:45']:
  d=p[p.cutoff.eq(cutoff)].sort_values(['date','symbol']).copy();sets={k:d[d.date.isin(v)].copy() for k,v in split.items()};tr,va,te=[sets[k] for k in ['train','validation','test']]
  for variant,features in [('premium',BASE),('premium_l2',BASE+L2)]:
   key=cutoff+'_'+variant;print('fit',key,len(tr),len(va),len(te),flush=True)
   clf=HistGradientBoostingClassifier(max_iter=150,learning_rate=.05,max_leaf_nodes=7,min_samples_leaf=50,l2_regularization=10,early_stopping=False,random_state=20260912).fit(tr[features],np.sign(tr.net_shares).astype(int))
   assert clf.classes_.tolist()==[-1,0,1]
   opt=minimize_scalar(lambda t:log_loss(np.sign(va.net_shares).astype(int),probabilities(clf,va[features],t),labels=[-1,0,1]),bounds=(.5,3),method='bounded');temp=float(opt.x)
   regs={}
   for name,loss,q in [('point','squared_error',None),('lo','quantile',.1),('hi','quantile',.9)]:
    kw=dict(loss=loss,max_iter=150,learning_rate=.05,max_leaf_nodes=7,min_samples_leaf=50,l2_regularization=10,early_stopping=False,random_state=20260912)
    if q is not None:kw['quantile']=q
    regs[name]=HistGradientBoostingRegressor(**kw).fit(tr[features],np.arcsinh(tr.net_flow_pct.to_numpy()))
   vl=np.sinh(regs['lo'].predict(va[features]));vh=np.sinh(regs['hi'].predict(va[features]));a=np.minimum(vl,vh);b=np.maximum(vl,vh)
   residual=np.maximum(a-va.net_flow_pct.to_numpy(),va.net_flow_pct.to_numpy()-b);n=len(residual);rank=min(n,int(np.ceil((n+1)*.8)));correction=max(0,float(np.sort(residual)[rank-1]))
   outputs={}
   for name,z in sets.items():
    z=z.copy();pr=probabilities(clf,z[features],temp)
    for j,col in enumerate(['p_redeem','p_flat','p_create']):z[col]=pr[:,j]
    z['pred_flow_pct']=np.maximum(-100,np.sinh(regs['point'].predict(z[features])));lo=np.sinh(regs['lo'].predict(z[features]));hi=np.sinh(regs['hi'].predict(z[features]));z['lo_flow_pct']=np.maximum(-100,np.minimum(lo,hi)-correction);z['hi_flow_pct']=np.maximum(z.lo_flow_pct,np.maximum(lo,hi)+correction)
    for target,col in [('pred_shares','pred_flow_pct'),('lo_shares','lo_flow_pct'),('hi_shares','hi_flow_pct')]:z[target]=z[col]*z.prev_shares/100
    z['pred_baskets']=z.pred_shares/z.unit;z['lo_baskets']=z.lo_shares/z.unit;z['hi_baskets']=z.hi_shares/z.unit;z['split']=name;z['variant']=variant;outputs[name]=z
   eligible=[]
   for t in np.arange(.5,.991,.01):
    gs=gate_stats(outputs['validation'],outputs['validation'].p_create>=t);thresholds.append(dict(cutoff=cutoff,variant=variant,threshold=t,**gs))
    if gs['n']>=20 and gs['dates']>=3 and gs['funds']>=5 and gs['precision']>=.9:eligible.append((gs['n'],float(t)))
   chosen=max(eligible)[1] if eligible else None
   result=dict(features=features,temperature=temp,interval_calibration=correction,threshold_selected_on_validation=chosen,validation=metrics(outputs['validation']),test=metrics(outputs['test']),test_by_market={m:metrics(z) for m,z in outputs['test'].groupby('market')},test_by_date={day:metrics(z) for day,z in outputs['test'].groupby('date')})
   result['selected_gate_test']=gate_stats(outputs['test'],outputs['test'].p_create>=chosen) if chosen is not None else None
   results[key]=result
   for name in ['validation','test']:predictions.append(outputs[name])
   bundle=dict(schema_version=1,features=features,classifier=clf,temperature=temp,regressors=regs,interval_correction=correction,trained_through=split['validation'][-1],fit_through=split['train'][-1],cutoff=cutoff,variant=variant,fx_basis='previous_available_settlement',selected_threshold=chosen,mode='research_only')
   joblib.dump(bundle,R/'models'/f'{cutoff.replace(":","")}_{variant}.joblib')
   if variant=='premium_l2':
    rng=np.random.default_rng(20260912);orig=result['test']['create_ap']
    for f in features:
     scores=[]
     for repeat in range(8):
      x=te[features].copy()
      for day,indices in te.groupby('date').groups.items():x.loc[indices,f]=rng.permutation(x.loc[indices,f].to_numpy())
      scores.append(orig-average_precision_score(te.net_shares>0,probabilities(clf,x,temp)[:,2]))
     importance.append(dict(cutoff=cutoff,factor=f,mean_test_ap_drop=float(np.mean(scores)),std=float(np.std(scores)),role='heldout_diagnostic_not_model_selection'))
    for f in L2:
     edges=np.unique(np.quantile(tr[f].dropna(),[0,.2,.4,.6,.8,1]));edges[0]=-np.inf;edges[-1]=np.inf
     if len(edges)<2:continue
     for name,z in sets.items():
      bins=pd.cut(z[f],edges,include_lowest=True)
      for bucket,g in z.groupby(bins,observed=True):factors.append(dict(cutoff=cutoff,factor=f,split=name,bucket=str(bucket),n=len(g),create_rate=float((g.net_shares>0).mean()),redeem_rate=float((g.net_shares<0).mean()),mean_flow_pct=float(g.net_flow_pct.mean()),median_baskets=float(g.net_baskets.median())))
 (R/'metrics.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=serial,allow_nan=False))
 out=pd.concat(predictions,ignore_index=True);out.to_parquet(R/'predictions.parquet',index=False);out.to_csv(R/'predictions.csv',index=False)
 pd.DataFrame(factors).to_csv(R/'factor_buckets.csv',index=False);pd.DataFrame(importance).to_csv(R/'permutation_importance.csv',index=False);pd.DataFrame(thresholds).to_csv(R/'validation_thresholds.csv',index=False)
 bootstrap={};rng=np.random.default_rng(914)
 for cutoff in ['14:30','14:45']:
  a=out[(out.cutoff==cutoff)&(out.split=='test')&(out.variant=='premium')].sort_values(['date','symbol']);b=out[(out.cutoff==cutoff)&(out.split=='test')&(out.variant=='premium_l2')].sort_values(['date','symbol']);assert list(zip(a.date,a.symbol))==list(zip(b.date,b.symbol));a=a.reset_index(drop=True);b=b.reset_index(drop=True);ds=sorted(a.date.unique());samples=[]
  for i in range(1000):
   idx=np.concatenate([np.flatnonzero(a.date.eq(day)) for day in rng.choice(ds,len(ds),replace=True)]);aa=a.iloc[idx];bb=b.iloc[idx];samples.append([average_precision_score(bb.net_shares>0,bb.p_create)-average_precision_score(aa.net_shares>0,aa.p_create),abs(aa.pred_baskets-aa.net_baskets).mean()-abs(bb.pred_baskets-bb.net_baskets).mean()])
  bootstrap[cutoff]=dict(test_dates=len(ds),l2_ap_improvement_ci95=np.quantile(np.array(samples)[:,0],[.025,.975]).tolist(),l2_basket_mae_improvement_ci95=np.quantile(np.array(samples)[:,1],[.025,.975]).tolist(),caution='date-cluster percentile bootstrap; very few dates, descriptive only')
 (R/'bootstrap.json').write_text(json.dumps(bootstrap,indent=2));print('DONE',json.dumps({k:v['test'] for k,v in results.items()},default=serial),flush=True)
if __name__=='__main__':main()
