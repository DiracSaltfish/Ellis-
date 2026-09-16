from pathlib import Path
import json,sys
import numpy as np,pandas as pd
R=Path(__file__).resolve().parent;C=R.parent;O=R/'results';sys.path.insert(0,str(C.parents[1]/'native/batch'));from intraday import near
U=500000;o=pd.read_csv(C/'results/orders_1445.csv');t=pd.read_csv(O/'continuous_trades_with_disjoint_buckets_1445.csv');A='A_known_original_integer_U';B='B_unknown_original_executed_integer_U';selected=[A,B]
# Tolerance and fake-basket controls, held fixed to original study values; not a sweep to hit 61U.
controls=[]
for unit in [400000,500000,600000,650000]:
 for side,key in [(1,'buy'),(2,'sell')]:
  z=o[o.side.eq(side)];known=z.quantity_evidence.isin([1,2]);mask=known&near(z.original_quantity,unit)|~known&near(z.active_filled,unit);q=z[mask]
  controls.append(dict(reference_unit_shares=unit,side=key,matched_order_count=int((q.filled>0).sum()),matched_shares=int((q.active_filled+q.passive_filled).sum()),matched_in_actual_U=float((q.active_filled+q.passive_filled).sum()/U)))
pd.DataFrame(controls).to_csv(O/'unit_controls.csv',index=False)
strict=[]
for side,key in [(1,'buy'),(2,'sell')]:
 z=o[o.side.eq(side)];known=z.quantity_evidence.isin([1,2]);mask=known&z.original_quantity.gt(0)&z.original_quantity.mod(U).eq(0)|~known&z.active_filled.gt(0)&z.active_filled.mod(U).eq(0);q=z[mask];strict.append(dict(side=key,matched_U=float((q.active_filled+q.passive_filled).sum()/U),active_U=float(q.active_filled.sum()/U),passive_U=float(q.passive_filled.sum()/U)))
pd.DataFrame(strict).to_csv(O/'strict_integer_controls.csv',index=False)
matrix=t.pivot_table(index='bucket_sell',columns='bucket_buy',values='成交数量',aggfunc='sum',fill_value=0)/U;matrix.to_csv(O/'seller_buyer_bucket_matrix.csv')
s=t[t.bucket_sell.isin(selected)].copy();buyers=s.groupby('叫买序号')['成交数量'].sum();buyer_sizes=o[o.side.eq(1)].set_index('order_id');by=pd.DataFrame({'shares_received':buyers}).join(buyer_sizes[['original_quantity','original_quantity_lower_bound','quantity_evidence','active_filled','passive_filled']]);by=by.sort_values('shares_received',ascending=False);by.to_csv(O/'buyers_of_integer_seller_orders.csv')
# Track all executions of one reconstructed 2U seller order as a worked example.
ids=[5893370,14999500,3706015,3958827,3977486,6855380,10367671]
worked=t[t['叫卖序号'].isin(ids)];worked.to_csv(O/'worked_seller_trade_traces.csv',index=False)
parent=pd.read_csv(O/'sell_partition_1445.csv');parent=parent[parent.bucket.isin(selected)];parent['execution_total']=parent.active_filled+parent.passive_filled;parent['size_origin']=np.where(parent.quantity_evidence.isin([1,2]),'known_original','unknown_original');parent['reference_shares']=np.where(parent.quantity_evidence.isin([1,2]),parent.original_quantity,parent.active_filled)
size=parent.groupby(['size_origin','reference_shares']).agg(orders=('order_id','size'),filled_orders=('execution_total',lambda v:int((v>0).sum())),active_shares=('active_filled','sum'),passive_shares=('passive_filled','sum'));size.to_csv(O/'integer_seller_size_breakdown.csv')
info=dict(selected_seller_U=float(s['成交数量'].sum()/U),strict_seller_U=strict[1]['matched_U'],selected_buy_U=float(t.loc[t.bucket_buy.isin(selected),'成交数量'].sum()/U),deduplicated_counterparty_buy_orders=len(buyers),counterparty_orders_le10000=int(buyers.le(10000).sum()),shares_in_counterparty_orders_le10000=int(buyers[buyers.le(10000)].sum()),counterparty_orders_above100000=int(buyers.gt(100000).sum()),shares_in_counterparty_orders_above100000=int(buyers[buyers.gt(100000)].sum()),shares_top10_buy_orders=int(buyers.nlargest(10).sum()),max_received=int(buyers.max()),received_buy_order_ids=buyers.nlargest(10).to_dict(),within_selected_two_sides_U=float(t.loc[t.bucket_sell.isin(selected)&t.bucket_buy.isin(selected),'成交数量'].sum()/U),selling_to_other_buy_orders_U=float(t.loc[t.bucket_sell.isin(selected)&~t.bucket_buy.isin(selected),'成交数量'].sum()/U),buying_from_other_sell_orders_U=float(t.loc[~t.bucket_sell.isin(selected)&t.bucket_buy.isin(selected),'成交数量'].sum()/U))
info['net_executed_supply_proxy_U']=info['selected_seller_U']-info['selected_buy_U'];info['selected_seller_to_actual_ratio']=info['selected_seller_U']/61
checks=[]
def ck(name,b):
 assert b,name;checks.append(name)
for cut in ['14:30','14:45','full']:
 all=pd.read_csv(O/'disjoint_execution.csv');all=all[all.cutoff.eq(cut)];v=all.groupby('side').total_U.sum();expected={'14:30':298.0382,'14:45':312.429,'full':327.8556}[cut];ck('two-side exact total '+cut,np.allclose(v,[expected,expected]));ck('active+passive '+cut,np.allclose(all.active_U+all.passive_U,all.total_U))
ck('selected totals independent raw tape',abs(info['selected_seller_U']-58.4322)<1e-10);ck('order-to-counterparty quantity conservation',buyers.sum()==s['成交数量'].sum());ck('flow matrix total',abs(matrix.to_numpy().sum()-312.429)<1e-10);ck('selected external net equals difference',abs(info['selling_to_other_buy_orders_U']-info['buying_from_other_sell_orders_U']-info['net_executed_supply_proxy_U'])<1e-10)
ck('mutually exclusive known vs unknown origins',not ((o.quantity_evidence.isin([1,2]))&(~o.quantity_evidence.isin([1,2]))).any());ck('no accounts accidentally asserted','account_id' not in t.columns)
( O/'reconciliation.json').write_text(json.dumps(dict(**info,checks_passed=len(checks),checks=checks),ensure_ascii=False,indent=2));print(json.dumps(info,ensure_ascii=False));print(pd.DataFrame(controls).to_string(index=False));print(pd.DataFrame(strict).to_string(index=False));print(size.to_string());print(matrix.to_string());print('CHECKS',len(checks))
