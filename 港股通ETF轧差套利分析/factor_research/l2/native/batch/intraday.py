"""14:30/14:45 research batch. No order submission, scheduling or QMT network calls.
Input provider contract: completed snapshot + point-in-time context. Model is optional.
"""
from pathlib import Path
from datetime import datetime,time,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor
import argparse,json,sys
import numpy as np,pandas as pd
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'build'))
import etf_l2
TZ=timezone(timedelta(hours=8))
FEATURES=['settlement_mean_bp','settlement_p10_bp','settlement_std_bp','settlement_above30_fraction','mid_mean_bp','fx_gap_bp','l2_active_imbalance','l2_buy_large_parent_frac','l2_sell_large_parent_frac','l2_buy_unit_parent_excess','l2_sell_unit_parent_excess','l2_buy_unit_burst_excess','l2_sell_unit_burst_excess','l2_sell_compression_frac','l2_buy_compression_frac','l2_passive_sell_unit_frac']

def timestamp(s):
 d=datetime.fromisoformat(s)
 if d.tzinfo is None:raise ValueError('timestamps must include UTC offset')
 return d.astimezone(TZ)
def cutoff_at(date,cutoff):
 if cutoff not in ['14:30','14:45']:raise ValueError('supported cutoffs: 14:30/14:45')
 return datetime.fromisoformat(date+'T'+cutoff+':00+08:00')
def expected_minutes(cutoff):
 end=int(cutoff[:2])*60+int(cutoff[3:]);return list(range(571,691))+list(range(781,end+1))
def near(v,u):
 v=np.asarray(v);k=np.maximum(1,np.rint(v/u));return np.abs(v-k*u)<=u*.01

class CsvSnapshotProvider:
 """QMT later implements the same snapshot/items contract, keeping raw provenance."""
 def load(self,item,date,cutoff):
  return etf_l2.process_files(item['trade_file'],item['order_file'],item.get('quote_file',''),int(item['symbol'][:6]),int(date.replace('-','')),int(item['unit']),cutoff=cutoff)

def prepare_context(context,date,cutoff):
 required={'date','symbol','minute','known_at','etf','iopv_mid','iopv_estimate','fx_basis'}
 if not required.issubset(context.columns):raise ValueError('context columns missing: '+str(required-set(context.columns)))
 d=context[context.date.eq(date)].copy();bound=cutoff_at(date,cutoff)
 d=d[d.known_at.map(timestamp)<=bound]
 d['minute_id']=d.minute.map(lambda x:int(x[:2])*60+int(x[3:]))
 d=d[d.minute_id.isin(expected_minutes(cutoff))]
 if d.duplicated(['symbol','minute_id']).any():raise ValueError('duplicate context minute')
 if not d.fx_basis.isin(['previous_available_settlement','intraday_estimate']).all():raise ValueError('final settlement FX is forbidden in intraday mode')
 if not np.isfinite(d[['etf','iopv_mid','iopv_estimate']].to_numpy(dtype=float)).all() or not (d[['etf','iopv_mid','iopv_estimate']]>0).all().all():raise ValueError('invalid prices')
 for _,row in d.iterrows():
  end=datetime.combine(bound.date(),time(int(row.minute[:2]),int(row.minute[3:])),TZ)
  if timestamp(row.known_at)<end:raise ValueError('a completed minute cannot be known before minute end')
 return d

def feature_row(native,context,unit,cutoff):
 m=pd.DataFrame(native['minutes']).set_index('completed_minute');o=pd.DataFrame(native['orders']);b=pd.DataFrame(native['bursts'])
 d=context.set_index('minute_id').sort_index().join(m,how='left')
 for c in ['buy_notional_x10000','sell_notional_x10000']:d[c]=d[c].fillna(0)
 amount=float(d.buy_notional_x10000.sum()+d.sell_notional_x10000.sum())
 if amount<=0:raise ValueError('no continuous turnover in available context')
 premium=(d.etf/d.iopv_estimate-1)*10000;mid=(d.etf/d.iopv_mid-1)*10000
 out=dict(settlement_mean_bp=float(premium.mean()),settlement_p10_bp=float(premium.quantile(.1)),settlement_std_bp=float(premium.std(ddof=0)),settlement_above30_fraction=float((premium>30).mean()),mid_mean_bp=float(mid.mean()),fx_gap_bp=float((d.iopv_mid.iloc[-1]/d.iopv_estimate.iloc[-1]-1)*10000),l2_active_imbalance=float((d.buy_notional_x10000.sum()-d.sell_notional_x10000.sum())/amount),premium_coverage=len(d)/len(expected_minutes(cutoff)),continuous_amount_cny=amount/10000)
 all_amount=float(o.active_notional_x10000.sum())
 for side,sign in [('buy',1),('sell',2)]:
  z=o[o.side.eq(sign)];v=z.active_filled.to_numpy();money=z.active_notional_x10000.to_numpy()
  out[f'l2_{side}_large_parent_frac']=float(money[v>=unit*.5].sum()/max(all_amount,1))
  out[f'l2_{side}_unit_parent_excess']=float((money[near(v,unit)].sum()-np.mean([money[near(v,round(unit*k/100)*100)].sum() for k in [.8,1.2,1.3]]))/max(all_amount,1))
  burst=b[b.side.eq(sign)];vol=burst.executed.to_numpy()
  out[f'l2_{side}_unit_burst_excess']=float((vol[near(vol,unit)].sum()-np.mean([vol[near(vol,round(unit*k/100)*100)].sum() for k in [.8,1.2,1.3]]))/max(o.active_filled.sum(),1))
  delta=premium.diff();valid=(premium.shift()>30)&(delta<0)&d.index.to_series().diff().eq(1)
  out[f'l2_{side}_compression_frac']=float(d.loc[valid,side+'_notional_x10000'].sum()/amount)
 known=o.quantity_evidence.isin([1,2]);z=o[o.side.eq(2)&known]
 out['l2_passive_sell_unit_frac']=float(z.loc[near(z.original_quantity.to_numpy(),unit),'passive_notional_x10000'].sum()/max(all_amount,1))
 return out

def predict(row,model,date,cutoff):
 if model is None:return dict(status='features_ready',direction=None,probabilities=None,candidate=False)
 if model.get('schema_version')!=1 or not model.get('asof_compatible') or cutoff not in model.get('cutoffs',[]):raise ValueError('model lacks compatible as-of metadata')
 if model['trained_through']>=date:raise ValueError('model training includes decision date/future')
 names=model['features'];x=np.array([row[n] for n in names],float);center=np.array(model['center']);scale=np.array(model['scale']);coef=np.array(model['coefficients']);intercept=np.array(model['intercepts'])
 if scale.shape!=x.shape or center.shape!=x.shape or coef.shape!=(3,len(x)) or intercept.shape!=(3,) or np.any(scale<=0):raise ValueError('invalid model dimensions/scales')
 if model.get('classes')!=['net_create','flat','net_redeem']:raise ValueError('unknown class order')
 if not all(np.isfinite(a).all() for a in [x,center,scale,coef,intercept]):raise ValueError('non-finite model/input')
 logits=coef@((x-center)/scale)+intercept;p=np.exp(logits-logits.max());p/=p.sum()
 candidate=bool(p[0]>=model.get('min_p_create',.9) and row['fx_gap_bp']>model.get('cost_buffer_bp',30) and row['premium_coverage']>=.95 and row['l2_quality_valid'])
 return dict(status='research_candidate' if candidate else 'model_scored',direction=model['classes'][int(p.argmax())],probabilities=dict(zip(model['classes'],p.tolist())),candidate=candidate,calibration_validated=bool(model.get('calibration_validated',False)))

def run_batch(manifest,context,cutoff,model=None,workers=2,provider=None):
 if manifest.get('schema_version')!=1:raise ValueError('unknown snapshot schema')
 date=manifest['date'];bound=cutoff_at(date,cutoff)
 if timestamp(manifest['snapshot_until'])<bound or not manifest.get('complete_from_open'):raise ValueError('snapshot does not cover start-of-day through cutoff')
 items=manifest['items'];symbols=[x['symbol'] for x in items]
 if len(set(symbols))!=len(symbols):raise ValueError('duplicate snapshot symbol')
 context=prepare_context(context,date,cutoff);provider=provider or CsvSnapshotProvider()
 def one(item):
  sym=item['symbol'];base=dict(date=date,cutoff=cutoff,symbol=sym)
  try:
   expected=sym[:6]+('.SH' if sym.startswith('5') else '.SZ')
   if sym!=expected or timestamp(item['pcf_known_at'])>bound:raise ValueError('bad canonical symbol or PCF known in future')
   native=provider.load(item,date,cutoff);d=context[context.symbol.eq(sym)];f=feature_row(native,d,int(item['unit']),cutoff)
   f['l2_quality_valid']=bool(native['audit']['order_features_valid'] and native['audit']['trade_features_valid'])
   pred=predict(f,model,date,cutoff)
   if not f['l2_quality_valid'] or f['premium_coverage']<.95:pred.update(status='quality_blocked',candidate=False)
   return dict(**base,**f,**pred,audit=native['audit'],timing=native['timing'])
  except Exception as e:return dict(**base,status='error',candidate=False,error=str(e))
 with ThreadPoolExecutor(max_workers=workers) as pool:rows=list(pool.map(one,items))
 return dict(schema_version=1,date=date,cutoff=cutoff,mode='research_only',qmt_transport='not_connected',model_loaded=model is not None,rows=rows)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--manifest',required=True);ap.add_argument('--context',required=True);ap.add_argument('--cutoff',choices=['14:30','14:45'],required=True);ap.add_argument('--model');ap.add_argument('--output',required=True);ap.add_argument('--workers',type=int,default=2);args=ap.parse_args()
 manifest=json.loads(Path(args.manifest).read_text());context=pd.read_csv(args.context,dtype={'symbol':str,'date':str,'minute':str});model=json.loads(Path(args.model).read_text()) if args.model else None
 result=run_batch(manifest,context,args.cutoff,model,args.workers);out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True);tmp=out.with_suffix(out.suffix+'.tmp');tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False));tmp.replace(out)
 print(json.dumps(dict(rows=len(result['rows']),statuses=pd.Series([x['status'] for x in result['rows']]).value_counts().to_dict()),ensure_ascii=False))
 if any(x['status']=='error' for x in result['rows']):sys.exit(2)
if __name__=='__main__':main()
