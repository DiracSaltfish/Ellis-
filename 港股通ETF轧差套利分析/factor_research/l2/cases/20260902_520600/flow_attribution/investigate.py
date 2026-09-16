"""Disjoint seller/buyer execution accounting and frozen quantity-model diagnosis.
Order identifiers are not account identifiers; no inferred matching of creations to trades.
"""
from pathlib import Path
import sys,json,math,hashlib
import numpy as np,pandas as pd,joblib
from sklearn.ensemble import HistGradientBoostingRegressor
R=Path(__file__).resolve().parent;C=R.parent;L=C.parents[1];U=500000;RESULT=R/'results';RESULT.mkdir(exist_ok=True)
sys.path[:0]=[str(L/'native/build'),str(L/'native/batch'),str(L/'study')]
from intraday import near
from score import score
import etf_l2
SRC=Path('/Volumes/EllisFiles/Stocksdata/A股逐笔/单标的研究提取/520600_20260902/20260902/520600.SZ')
t=pd.read_csv(SRC/'逐笔成交.csv',encoding='gb18030');oo=pd.read_csv(SRC/'逐笔委托.csv',encoding='gb18030')
ms=lambda x:x//10000000*3600000+x//100000%100*60000+x//1000%100*1000+x%1000
fmt=lambda x:f'{int(x)//3600000:02}:{int(x)//60000%60:02}:{int(x)//1000%60:02}.{int(x)%1000:03}'
t['ms']=ms(t['时间']);oo['ms']=ms(oo['时间']);t['continuous']=t.ms.between(34200000,41400000)|(t.ms.ge(46800000)&t.ms.lt(53820000));t['amount']=t['成交数量']*t['成交价格']/1e4;t['buy_active']=t['BS标志'].eq('B');t['sell_active']=t['BS标志'].eq('S')
ctx=pd.read_csv(C/'results/minute_context.csv').set_index('minute_id');t['prior_minute']=t.ms//60000;t['premium_known']=t.prior_minute.map(ctx.settlement_bp)
# All categories use the same side's order, so every trade is counted once per side, never twice within one side.
groups=[];sizes=[];timebins=[];counterparties=[];timelines=[];summaries={}
for cut in ['14:30','14:45','full']:
 n=pd.read_csv(C/'results'/f'orders_{cut.replace(":","")}.csv');limit=86400000 if cut=='full' else (int(cut[:2])*60+int(cut[3:]))*60000;tr=t[t.ms.lt(limit)&t.continuous].copy();total=tr['成交数量'].sum();out=[]
 for side,key,col,activecol in [(1,'buy','叫买序号','buy_active'),(2,'sell','叫卖序号','sell_active')]:
  o=n[n.side.eq(side)].copy();known=o.quantity_evidence.isin([1,2]);oi=o.original_quantity.to_numpy();executed=o.active_filled.to_numpy();o['bucket']=np.select([known&near(oi,U),~known&near(executed,U),o.active_filled.ge(.5*U),known&o.original_quantity.ge(.5*U)],['A_known_original_integer_U','B_unknown_original_executed_integer_U','C_other_large_active','D_other_known_large_resting'],default='E_other_orders')
  for bucket,z in o.groupby('bucket'):
   active=z.active_filled.sum();passive=z.passive_filled.sum();groups.append(dict(cutoff=cut,side=key,bucket=bucket,orders=len(z),active_U=active/U,passive_U=passive/U,total_U=(active+passive)/U,auction_U=z.auction_filled.sum()/U))
  check=o.active_filled.sum()+o.passive_filled.sum();assert check==total
  o.to_csv(RESULT/f'{key}_partition_{cut.replace(":","")}.csv',index=False)
  # Exact parent known/executed sizes; inspect multiples and fractional orders without declaring new identities.
  o['size_basis']=np.where(known,'known_original','executed_lower_bound');o['size_shares']=np.where(known,o.original_quantity,o.original_quantity_lower_bound)
  for (basis,q),z in o.groupby(['size_basis','size_shares']):
   sizes.append(dict(cutoff=cut,side=key,basis=basis,size_shares=q,orders=len(z),active_U=z.active_filled.sum()/U,passive_U=z.passive_filled.sum()/U,cancelled_U=z.cancelled.sum()/U,filled_U=(z.active_filled.sum()+z.passive_filled.sum())/U))
  tr['bucket_'+key]=tr[col].map(o.set_index('order_id').bucket)
  assert tr['bucket_'+key].notna().all()
  tr['bin15']=tr.ms//900000*15
  for (bucket,bin15),z in tr.groupby(['bucket_'+key,'bin15']):timebins.append(dict(cutoff=cut,side=key,bucket=bucket,start=f'{int(bin15)//60:02}:{int(bin15)%60:02}',quantity_U=z['成交数量'].sum()/U,active_U=z.loc[z[activecol],'成交数量'].sum()/U,passive_U=z.loc[~z[activecol],'成交数量'].sum()/U))
  for bucket,z in tr.groupby('bucket_'+key):
   out.append(dict(side=key,bucket=bucket,all_U=z['成交数量'].sum()/U,premium_above30_U=z.loc[z.premium_known.gt(30),'成交数量'].sum()/U,unknown_premium_U=z.loc[z.premium_known.isna(),'成交数量'].sum()/U))
  if side==2:
   # Follow each observed seller order into its buyer orders, not buyer accounts.
   target=o[o.bucket.isin(['A_known_original_integer_U','B_unknown_original_executed_integer_U'])&(o.active_filled+o.passive_filled>0)]
   for _,order in target.iterrows():
    z=tr[tr[col].eq(order.order_id)].sort_values(['ms','成交编号']);by=z.groupby('叫买序号')['成交数量'].sum();rest=oo[(oo['交易所委托号']==order.order_id)&oo['委托类型'].eq('A')&oo.ms.lt(limit)]
    counterparties.append(dict(cutoff=cut,seller_order_id=int(order.order_id),bucket=order.bucket,original_known=bool(order.quantity_evidence in [1,2]),original_shares=int(order.original_quantity),active_shares=int(order.active_filled),passive_shares=int(order.passive_filled),buyer_order_count=len(by),max_buyer_shares=int(by.max()),buyer_orders_le10000=int((by<=10000).sum()),shares_to_buyer_orders_le10000=int(by[by<=10000].sum()),first_trade=fmt(z.ms.min()),last_trade=fmt(z.ms.max()),resting_add=fmt(rest.ms.min()) if len(rest) else '',trade_count=len(z)))
   for bucket,z in tr[tr.bucket_sell.isin(['A_known_original_integer_U','B_unknown_original_executed_integer_U'])].groupby('bucket_sell'):
    by=z.groupby('叫买序号')['成交数量'].sum();summaries.setdefault(cut,{})[bucket]=dict(buyer_orders=len(by),matched_U=float(z['成交数量'].sum()/U),buyer_orders_le10000=int(by.le(10000).sum()),matched_shares_to_small_orders=int(by[by.le(10000)].sum()))
 if cut=='14:45':tr.to_csv(RESULT/'continuous_trades_with_disjoint_buckets_1445.csv',index=False)
 pd.DataFrame(out).to_csv(RESULT/f'premium_exposure_{cut.replace(":","")}.csv',index=False)
 # Offer-side versus buy-side close-to-U bucket differences are net executed supply proxies only.
pd.DataFrame(groups).to_csv(RESULT/'disjoint_execution.csv',index=False);pd.DataFrame(sizes).to_csv(RESULT/'size_distribution.csv',index=False);pd.DataFrame(timebins).to_csv(RESULT/'bucket_timeline.csv',index=False);pd.DataFrame(counterparties).to_csv(RESULT/'seller_to_buyer_orders.csv',index=False)
# Quantity contribution decomposition, separate from the preceding classifier decomposition.
trainall=pd.read_parquet(L/'study/panel.parquet');d=trainall[trainall.cutoff.eq('14:45')];split=json.loads((L/'study/split.json').read_text());tr=d[d.date.isin(split['train'])];va=d[d.date.isin(split['validation'])];te=d[d.date.isin(split['test'])]
case=pd.read_csv(C/'results/features.csv');case=case[case.cutoff.eq('14:45')].copy();model=joblib.load(L/'study/models/1445_premium_l2.joblib');names=model['features'];reg=model['regressors']['point'];casepred=float(np.sinh(reg.predict(case[names]))[0]);prev=float(case.prev_shares.iloc[0]);convert=prev/100/U
featuregroups={'active_imbalance':['l2_active_imbalance'],'large_active_orders':['l2_buy_large_parent_frac','l2_sell_large_parent_frac'],'unit_active_orders':['l2_buy_unit_parent_excess','l2_sell_unit_parent_excess'],'unit_bursts':['l2_buy_unit_burst_excess','l2_sell_unit_burst_excess'],'premium_compression':['l2_sell_compression_frac','l2_buy_compression_frac'],'passive_unit_sell':['l2_passive_sell_unit_frac'],'premium_state':[f for f in names if not f.startswith('l2_') and f not in ['lag_flow_pct','lag5_flow_pct','log_prev_assets','creation_allowed','redemption_allowed','turnover_pct']],'history_and_size':['lag_flow_pct','lag5_flow_pct','log_prev_assets','creation_allowed','redemption_allowed','turnover_pct']}
background=tr[names].sample(n=32,random_state=520600).reset_index(drop=True);gn=list(featuregroups);blocks=[]
for mask in range(256):
 x=background.copy()
 for j,k in enumerate(gn):
  if mask>>j&1:
   for f in featuregroups[k]:x[f]=case[f].iloc[0]
 blocks.append(x)
v=np.sinh(reg.predict(pd.concat(blocks,ignore_index=True))).reshape(256,32).mean(axis=1)*convert;at=[]
for j,k in enumerate(gn):
 amount=0
 for mask in range(256):
  if mask>>j&1:continue
  count=bin(mask).count('1');weight=math.factorial(count)*math.factorial(7-count)/math.factorial(8);amount+=weight*(v[mask|(1<<j)]-v[mask])
 at.append(dict(group=k,contribution_U=amount,baseline_U=v[0],final_U=v[-1]))
assert abs(sum(x['contribution_U'] for x in at)+v[0]-v[-1])<1e-10
pd.DataFrame(at).to_csv(RESULT/'quantity_group_attribution.csv',index=False)
# No parameter/feature search: one controlled diagnostic changes only the target transform.
rawreg=HistGradientBoostingRegressor(**reg.get_params()).fit(tr[names],tr.net_flow_pct)
rawstats=[]
for label,z in [('validation',va),('test',te),('case',case)]:
 original=np.sinh(reg.predict(z[names]));raw=rawreg.predict(z[names]);actual=z.net_flow_pct.to_numpy();factor=z.prev_shares.to_numpy()/100/z.unit.to_numpy()
 for name,pred in [('frozen_asinh_target',original),('diagnostic_raw_target',raw)]:
  rawstats.append(dict(split=label,model=name,rows=len(z),MAE_U=float((abs(pred-actual)*factor).mean()),MAE_flow_pct=float(abs(pred-actual).mean()),mean_pred_U=float((pred*factor).mean()),actual_mean_U=float((actual*factor).mean()),pred_total_U=float((pred*factor).sum())))
pd.DataFrame(rawstats).to_csv(RESULT/'target_transform_experiment.csv',index=False)
# Nearest observed training cases in empirical percentile coordinates, each feature equally weighted.
ranks=[];vr=[]
for f in names:
 vals=np.sort(tr[f].dropna().to_numpy());ranks.append(np.searchsorted(vals,tr[f].to_numpy(),side='right')/len(vals));vr.append(np.searchsorted(vals,case[f].iloc[0],side='right')/len(vals))
distance=np.sqrt(np.mean((np.array(ranks).T-np.array(vr))**2,axis=1));neighbors=tr.copy();neighbors['rank_distance']=distance;neighbors.nsmallest(25,'rank_distance').to_csv(RESULT/'nearest_training_cases.csv',index=False)
fund=tr[tr.symbol.eq('520600.SH')];fund.to_csv(RESULT/'target_fund_training_days.csv',index=False)
# Terminal snapshots give offered book inventory, not holder inventory.
orders_full=pd.read_csv(C/'results/orders_full.csv');known=orders_full.quantity_evidence.isin([1,2]);book={key:dict(remaining_U=float(orders_full.loc[known&orders_full.side.eq(side),'remaining'].sum()/U),positive_remaining_orders=int((known&orders_full.side.eq(side)&orders_full.remaining.gt(0)).sum())) for side,key in [(1,'buy'),(2,'sell')]}
late=t[t.ms.ge(53100000)];late_cont=late[late.continuous]
info=dict(actual_net_U=61,actual_net_pct=12.930961385181542,original_pred_U=casepred*convert,original_pred_pct=casepred,training_rows=len(tr),training_positive=int(tr.net_flow_pct.gt(0).sum()),training_flat=int(tr.net_flow_pct.eq(0).sum()),training_negative=int(tr.net_flow_pct.lt(0).sum()),training_above_case=int(tr.net_flow_pct.ge(12.930961385181542).sum()),training_pct_quantiles=tr.net_flow_pct.quantile([.5,.9,.95,.99,1]).to_dict(),target_fund_training_days=len(fund),target_fund_positive_days=int(fund.net_flow_pct.gt(0).sum()),target_fund_max_positive_pct=float(fund.net_flow_pct.max()),target_fund_max_positive_U=float(fund.net_baskets.max()),training_target_units=sorted(fund.unit.unique().tolist()),training_target_prev_shares_range=[float(fund.prev_shares.min()),float(fund.prev_shares.max())],late_all_traded_U=float(late['成交数量'].sum()/U),late_continuous_U=float(late_cont['成交数量'].sum()/U),late_active_sell_U=float(late_cont.loc[late_cont.sell_active,'成交数量'].sum()/U),late_active_buy_U=float(late_cont.loc[late_cont.buy_active,'成交数量'].sum()/U),book_orders_remaining=book,counterparty_summary=summaries,identification_limit='No account, ownership/lot lineage, creation timestamp or primary-market declaration in tape; reconstructed orders are not investors.',experiment='Raw target regression fitted on original train dates only, unchanged parameters; diagnostic, not adopted or validated as a new strategy.')
(RESULT/'summary.json').write_text(json.dumps(info,ensure_ascii=False,indent=2));print(json.dumps(info,ensure_ascii=False));print(pd.DataFrame(groups).query('cutoff=="14:45"').to_string(index=False));print(pd.DataFrame(at).to_string(index=False));print(pd.DataFrame(rawstats).to_string(index=False))
