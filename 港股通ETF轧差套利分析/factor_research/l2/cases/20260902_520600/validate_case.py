"""Cross-check case outputs against source labels, PCF, raw tape, and frozen models."""
from pathlib import Path
import json,hashlib
import numpy as np,pandas as pd,joblib
R=Path(__file__).resolve().parent;O=R/'results';checks=[]
def check(name,condition):
 if not condition:raise AssertionError(name)
 checks.append(name)
p=json.loads((O/'provenance.json').read_text());a=json.loads((O/'native_audit.json').read_text());pred=pd.read_csv(O/'predictions.csv');s=pd.read_csv(O/'minute_context.csv');f=pd.read_csv(O/'features.csv');g=pd.read_csv(O/'group_attribution.csv');o=pd.read_csv(O/'orders_1445.csv');m=pd.read_csv(O/'minutes_full.csv');new=json.loads((O/'candidate_factors.json').read_text())
check('PCF fingerprint equals website metadata',hashlib.sha256((R/'source/pcf-2026-09-02.html').read_bytes()).hexdigest()==p['pcf_sha256']==json.loads((R/'source/local_minutes.json').read_text())[0]['pcf_sha256'])
check('Same-day share label = 61 x 500000',p['current_shares']-p['prev_shares']==p['actual_net_shares']==61*p['unit'])
check('No same-day FX in predictive proxy',p['fx_previous_date']<'2026-09-02' and p['previous_buy_fx']==.85737 and p['final_buy_fx']==.85759)
check('IOPV reconstruction units and cash',np.allclose(s.mid,(s.hkd_assets*.86497-14.28)/500000,equal_nan=True))
check('Expected 209/210 and 224/225 coverage',np.allclose(f.premium_coverage,[209/210,224/225]))
check('Independent contexts never display ETF prices after 14:57',s.loc[s.minute_id>897,'etf'].isna().all())
for cut,quantity in [('14:30',149244500),('14:45',156439900),('full',164412500)]:
 z=a[cut]['audit'];check('Quote reconciliation '+cut,z['quote_reconciled'] and z['total_trade_quantity']==quantity==z['quote_cumulative_quantity']);check('Order/trade validity '+cut,z['order_features_valid'] and z['trade_features_valid'] and not z['issues'])
check('Auction plus continuous volume = full volume',int((m.active_buy+m.active_sell+m.unknown_direction+m.auction).sum())==164412500)
check('Auction removed from continuous',int(m.auction.sum())==484700)
check('Price-volume monetary conservation',int((m.buy_notional_x10000+m.sell_notional_x10000+m.unknown_notional_x10000+m.auction_notional_x10000).sum())==1612617305000)
check('Every known original order conserves quantity',((o.loc[o.known_original,'original_quantity']-o.loc[o.known_original,'filled']-o.loc[o.known_original,'cancelled'])==o.loc[o.known_original,'remaining']).all())
check('Exact executed unit orders: 31 sell and 2 buy',((o.side==2)&o.active_filled.eq(500000)).sum()==31 and ((o.side==1)&o.active_filled.eq(500000)).sum()==2)
for cut in ['14:30','14:45']:
 z=g[g.cutoff==cut];check('Group attribution additive '+cut,abs(z.contribution_pp.sum()/100+z.background_p_create.iloc[0]-z.case_p_create.iloc[0])<1e-10)
for _,z in pred.iterrows():
 check('Three-class normalized '+z.cutoff+' '+z.variant,abs(z.p_create+z.p_flat+z.p_redeem-1)<1e-10)
check('Both unchanged classifiers miss actual positive direction',pred.direction.eq('flat').all())
check('No 90-percent candidate claimed',pred.p_create.lt(.9).all() and not pred.pass_threshold.any())
check('Actual above every upper interval',pred.hi_baskets.lt(61).all())
check('Model share/basket consistency',np.allclose(pred.pred_shares,pred.pred_baskets*500000))
check('Quantity error computed against actual',np.allclose(pred.error_baskets,pred.pred_baskets-61))
for cut in ['1430','1445']:
 for var in ['premium','premium_l2']:
  model=joblib.load(R.parents[1]/'study/models'/f'{cut}_{var}.joblib');check('Frozen model ends before case '+cut+var,model['fit_through']=='2026-01-27' and model['trained_through']=='2026-02-02');check('Classifier class order '+cut+var,list(model['classifier'].classes_)==[-1,0,1])
check('New candidate factors explicitly unvalidated',new['not_validated'] is True)
raw=Path('/Volumes/EllisFiles/Stocksdata/A股逐笔/单标的研究提取/520600_20260902/20260902/520600.SZ');t=pd.read_csv(raw/'逐笔成交.csv',encoding='gb18030');wo=pd.read_csv(raw/'逐笔委托.csv',encoding='gb18030');check('Independent raw trade total',t['成交数量'].sum()==164412500);check('No cutoff leakage in order features',o.original_quantity_known_at.max()<53100000000 and o.active_last_ms.max()<53100000)
for oid,active,rest in [(5893370,755300,244700),(14999500,692500,307500)]:
 ts=t[(t['叫卖序号']==oid)&t['BS标志'].eq('S')];ws=wo[(wo['交易所委托号']==oid)&wo['委托类型'].eq('A')];check('SH original order reconstruction '+str(oid),ts['成交数量'].sum()==active and ws['委托数量'].sum()==rest and active+rest==1000000)
check('Official redemption cap bound',p['actual_net_baskets']==61 and '10,000,000.00' in p['pcf_fields'].values())
for fn in ['intraday.png','attribution.png']:check('Figure exists '+fn,(R/'figures'/fn).stat().st_size>10000)
# Record source/artifact hashes, including each trusted frozen model, for later reproducibility.
hashes={}
for folder in ['source','results']:
 for file in sorted((R/folder).iterdir()):
  if file.is_file() and file.name not in ['validation.json','artifact_hashes.json']:hashes[str(file.relative_to(R))]=hashlib.sha256(file.read_bytes()).hexdigest()
for model in sorted((R.parents[1]/'study/models').glob('*.joblib')):hashes['frozen_model/'+model.name]=hashlib.sha256(model.read_bytes()).hexdigest()
(O/'artifact_hashes.json').write_text(json.dumps(hashes,ensure_ascii=False,indent=2));out=dict(passed=len(checks),failed=0,checks=checks,limitations=['Historical receive timestamps unavailable','Missing HK symbol substituted from website native TGW','One incomplete basket minute excluded','Source-minute alignment sensitivity is not exchange-feed equivalence']);(O/'validation.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out,ensure_ascii=False))
