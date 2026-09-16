"""Frozen-model single-day audit. Run on machome; no training or raw-file deletion.
Replay uses completed minute closes and previous day's FX; final FX is descriptive only.
"""
from pathlib import Path
import re,html,json,hashlib,sys,io,math
from zipfile import ZipFile
import numpy as np,pandas as pd,joblib
from scipy.special import softmax
R=Path(__file__).resolve().parent;L=R.parents[1];F=L.parent;N=L/'native';ST=L/'study';OUT=R/'results';OUT.mkdir(exist_ok=True)
sys.path[:0]=[str(N/'build'),str(N/'batch'),str(F),str(ST)]
import etf_l2
from intraday import feature_row,expected_minutes,near
from build_panel import make_features
from score import score
DATE='2026-09-02';U=500000
RAW=Path('/Volumes/EllisFiles/Stocksdata/A股逐笔/单标的研究提取/520600_20260902/20260902/520600.SZ')
def dump(obj,p):p.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=lambda x:x.item() if isinstance(x,np.generic) else str(x),allow_nan=False))
def clock_ms(t):
 t=int(t);return f'{t//3600000:02}:{t//60000%60:02}:{t//1000%60:02}.{t%1000:03}'
def ms(x):return x//10000000*3600000+x//100000%100*60000+x//1000%100*1000+x%1000
# Parse the exact PCF already archived by the user's website, date and fingerprint checked.
raw=(R/'source/pcf-2026-09-02.html').read_bytes();body=raw.decode();clean=lambda x:html.unescape(re.sub('<[^>]+>',' ',x)).strip()
assert '2026-09-02日内容信息' in body
fields={clean(a):clean(b) for a,b in re.findall(r'<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>',body,re.S)}
num=lambda key:float(next(v for k,v in fields.items() if key in k).replace(',',''))
assert num('最小申购、赎回单位(单位:份)')==U
cash=num('预估现金部分');nav=num('基金份额净值');components=[]
for row in re.findall(r'<tr[^>]*>(.*?)</tr>',body[body.index('成份股信息内容'):],re.S):
 c=[clean(x) for x in re.findall(r'<td[^>]*>(.*?)</td>',row,re.S)]
 if len(c)==8 and re.fullmatch(r'\d{1,5}',c[0]):
  assert c[7]=='香港交易所' and c[3]!='必须现金替代';components.append((c[0].zfill(5)+'.HK',float(c[2].replace(',',''))))
assert len(components)==50 and len(set(x[0] for x in components))==50
website=pd.DataFrame(json.loads((R/'source/local_minutes.json').read_text()))
assert set(website.pcf_sha256)=={hashlib.sha256(raw).hexdigest()}
fx=json.loads((R/'source/fx-520600-central-parity-20260901-20260909.json').read_text());fxdays={x['trade_date']:x for x in fx['days']}
midfx=fxdays[DATE]['hkd_cny_central_parity'];finalfx=fxdays[DATE]['actual_sell_settlement'];lagfx=fxdays['2026-09-01']['actual_sell_settlement']
assert website.settlement_buy_fx.eq(finalfx).all()
rows=sorted(json.loads((R/'source/share_history.json').read_text())['rows'],key=lambda x:x['share_date'])
i=next(i for i,r in enumerate(rows) if r['share_date']==DATE);cur=rows[i];prev=rows[i-1]
assert prev['share_date']=='2026-09-01'
prevshares=prev['shares_10k']*1e4;actual=cur['share_change_10k']*1e4
assert abs((cur['shares_10k']-prev['shares_10k'])*1e4-actual)<1
# Last five actual trading days, not five calendar rows.
history=[x for x in rows[:i] if pd.Timestamp(x['share_date']).weekday()<5][-5:]
assert [x['share_date'] for x in history]==['2026-08-26','2026-08-27','2026-08-28','2026-08-31','2026-09-01']
lab=dict(net_shares=actual,prev_shares=prevshares,lag_flow_pct=prev['share_change_pct'],lag5_flow_pct=sum(x['share_change_pct'] for x in history))
basket=dict(symbol='520600.SH',date=DATE,unit=U,cash=cash,prev_nav=nav,creation_allowed=True,redemption_allowed=True)
index=pd.date_range(DATE+' 09:30',DATE+' 16:09',freq='min');asset=np.zeros(len(index));coverage=np.zeros(len(index),int);hkaudit=[]
native_rows=json.loads(Path('/Users/ellis/Library/Application Support/MachomeHub/data/premium/local-iopv/data/backfill/backfill-520600-20260901-20260909-central-parity/native-tgw-kline.json').read_text())['rows']
with ZipFile('/Volumes/Upan/港股/港股_1分钟/2026-09/20260902_1min.zip') as z:
 for sym,q in components:
  source='supplied_hk_zip'
  if sym+'.csv' in z.namelist():
   d=pd.read_csv(io.BytesIO(z.read(sym+'.csv')),encoding='utf-8-sig');t=pd.to_datetime(d['时间'])
  else:
   source='website_native_tgw_missing_symbol_fallback';nr=[v for v in native_rows[sym] if str(v['kline_time']).startswith('20260902')];d=pd.DataFrame({'时间':[str(v['kline_time']) for v in nr],'收盘价':[v['close_price']/1e6 for v in nr]});t=pd.to_datetime(d['时间'],format='%Y%m%d%H%M');d.to_csv(R/'source'/f'{sym}_fallback.csv',index=False)
  assert not t.duplicated().any();assert (t.dt.strftime('%Y-%m-%d')==DATE).all()
  price=pd.Series(pd.to_numeric(d['收盘价']).to_numpy(),index=t+pd.Timedelta(minutes=1));price=price.where(price>0).reindex(index).ffill(limit=5)
  asset+=price.to_numpy()*q;coverage+=price.notna().to_numpy();hkaudit.append(dict(symbol=sym,source=source,quantity=q,raw_rows=len(d),missing_completed_minute=int(price.reindex(pd.to_datetime([DATE+' '+f'{m//60:02}:{m%60:02}' for m in expected_minutes('14:45')])).isna().sum())))
tr=pd.read_csv(RAW/'逐笔成交.csv',encoding='gb18030');orders_raw=pd.read_csv(RAW/'逐笔委托.csv',encoding='gb18030');tr['ms']=ms(tr['时间']);tr=tr.sort_values(['ms','成交编号']);tr['amount']=tr['成交数量']*tr['成交价格']/10000
tr['continuous']=((tr.ms>=34200000)&(tr.ms<=41400000))|((tr.ms>=46800000)&(tr.ms<53820000))
tr['endminute']=tr.ms//60000+1;tr.loc[tr.ms.eq(41400000),'endminute']=690
ct=tr[tr.continuous];bars=ct.groupby('endminute').agg(etf=('成交价格','last'),amount=('amount','sum'),volume=('成交数量','sum'));bars.etf/=1e4
s=pd.DataFrame(dict(date=DATE,symbol='520600.SH',minute=index.strftime('%H:%M'),minute_id=index.hour*60+index.minute,mid=(asset*midfx+cash)/U,lag_settlement=(asset*lagfx+cash)/U,final_settlement=(asset*finalfx+cash)/U,hkd_assets=asset,components_priced=coverage)).set_index('minute_id')
s=s.join(bars);s['etf']=s.etf.ffill(limit=5);s['amount']=s.amount.fillna(0)
# Continuity only within each session; displayed auction close is separate from continuous features.
s.loc[~s.index.isin(list(range(571,691))+list(range(781,898))),'etf']=np.nan
s['actual_settlement_buy']=s.lag_settlement;s['settlement_bp']=(s.etf/s.lag_settlement-1)*1e4;s['final_settlement_bp']=(s.etf/s.final_settlement-1)*1e4;s['mid_bp']=(s.etf/s.mid-1)*1e4
s.reset_index().to_csv(OUT/'minute_context.csv',index=False)
# Compare independent basket reconstruction with the website at the same underlying source minute.
w=website.copy();w['minute_id']=w.minute.map(lambda x:int(x[:2])*60+int(x[3:]))+1;comp=s.join(w.set_index('minute_id')[['midpoint_iopv','etf_price']],how='inner');cm=comp[comp.index.isin(expected_minutes('14:45'))]
comparison=dict(midpoint_abs_diff_max=float((cm.mid-cm.midpoint_iopv).abs().max()),midpoint_abs_diff_median=float((cm.mid-cm.midpoint_iopv).abs().median()),etf_abs_diff_median=float((cm.etf-cm.etf_price).abs().median()),etf_abs_diff_max=float((cm.etf-cm.etf_price).abs().max()),note='Website native TGW and supplied HK bars are distinct feeds; source bars both shifted one minute for comparison.')
train=pd.read_parquet(ST/'panel.parquet');preds=[];feats=[];featuredetails=[];audit={};summary={};sensitivity=[]
for cut in ['14:30','14:45','full']:
 n=etf_l2.process_files(str(RAW/'逐笔成交.csv'),str(RAW/'逐笔委托.csv'),str(RAW/'行情.csv'),520600,20260902,U,cutoff='' if cut=='full' else cut,session_profile='sh_etf_20260706')
 assert n['audit']['order_features_valid'] and n['audit']['trade_features_valid'] and n['audit']['quote_reconciled'];audit[cut]=dict(audit=n['audit'],timing=n['timing'])
 o=pd.DataFrame(n['orders']);m=pd.DataFrame(n['minutes']);b=pd.DataFrame(n['bursts']);cutms=86400000 if cut=='full' else (int(cut[:2])*60+int(cut[3:]))*60000;tc=tr[tr.ms<cutms]
 active=tc[tc.continuous].copy();active['active_id']=np.where(active['BS标志'].eq('B'),active['叫买序号'],active['叫卖序号']);active['active_side']=np.where(active['BS标志'].eq('B'),1,2)
 timings=active.groupby(['active_id','active_side']).agg(active_first_ms=('ms','min'),active_last_ms=('ms','max'),active_trade_count=('ms','size'),price_min=('成交价格','min'),price_max=('成交价格','max')).reset_index().rename(columns={'active_id':'order_id','active_side':'side'})
 o=o.merge(timings,on=['order_id','side'],how='left');o['original_u']=o.original_quantity/U;o['active_u']=o.active_filled/U;o['passive_u']=o.passive_filled/U;o['known_original']=o.quantity_evidence.isin([1,2]);o['near_1u_original']=o.known_original&(o.original_quantity-U).abs().le(U*.01);o['near_multiple_original']=o.known_original&near(o.original_quantity,U);o['near_multiple_active']=near(o.active_filled,U)
 for k in ['active_first_ms','active_last_ms']:o[k.replace('_ms','_time')]=o[k].map(lambda x:clock_ms(x) if pd.notna(x) else '')
 o['active_amount_cny']=o.active_notional_x10000/1e4;o['passive_amount_cny']=o.passive_notional_x10000/1e4
 o.to_csv(OUT/f'orders_{cut.replace(":","")}.csv',index=False);m.to_csv(OUT/f'minutes_{cut.replace(":","")}.csv',index=False);b['start_time']=b.start.map(lambda x:clock_ms(x/1000));b['end_time']=b.end.map(lambda x:clock_ms(x/1000));b.to_csv(OUT/f'bursts_{cut.replace(":","")}.csv',index=False)
 denom=o.active_amount_cny.sum();summary[cut]=dict(continuous_amount=float(denom),active_buy=float(o.loc[o.side.eq(1),'active_amount_cny'].sum()),active_sell=float(o.loc[o.side.eq(2),'active_amount_cny'].sum()),continuous_volume=int(o.active_filled.sum()),auction_volume=int(m.auction.sum()),known_original_count=int(o.known_original.sum()))
 for side in [1,2]:
  z=o[o.side.eq(side)];key='buy' if side==1 else 'sell'
  summary[cut][key]=dict(large_active_count=int(z.active_filled.ge(.5*U).sum()),large_active_u=float(z.loc[z.active_filled.ge(.5*U),'active_u'].sum()),large_active_amount=float(z.loc[z.active_filled.ge(.5*U),'active_amount_cny'].sum()),exact_1u_original_count=int((z.known_original&z.original_quantity.eq(U)).sum()),near_1u_original_count=int(z.near_1u_original.sum()),near_1u_original_filled_u=float(z.loc[z.near_1u_original,'filled'].sum()/U),near_1u_original_cancelled_u=float(z.loc[z.near_1u_original,'cancelled'].sum()/U),exact_1u_active_count=int(z.active_filled.eq(U).sum()),near_multiple_active_count=int(z.near_multiple_active.sum()),near_multiple_active_amount=float(z.loc[z.near_multiple_active,'active_amount_cny'].sum()),near_multiple_original_passive_amount=float(z.loc[z.near_multiple_original,'passive_amount_cny'].sum()))
 if cut=='full':continue
 ss=s.loc[s.index.isin(expected_minutes(cut))].reset_index();base=make_features(ss,basket,lab,cut);assert base is not None
 ctx=ss.rename(columns={'mid':'iopv_mid','lag_settlement':'iopv_estimate'}).dropna(subset=['etf','iopv_mid','iopv_estimate']);extra=feature_row(n,ctx,U,cut);assert extra['premium_coverage']>=.95
 row=dict(**{k:v for k,v in base.items() if k not in extra},**extra,cutoff=cut,prev_shares=prevshares,l2_quality_valid=True);feats.append(row);d=pd.DataFrame([row]);tra=train[(train.cutoff==cut)&(train.date<='2026-01-27')]
 for variant in ['premium','premium_l2']:
  model=joblib.load(ST/'models'/f'{cut.replace(":","")}_{variant}.joblib');assert model['fit_through']=='2026-01-27';pr=score(d,model);pr['variant']=variant;pr['actual_shares']=actual;pr['actual_baskets']=actual/U;pr['selected_threshold']=model['selected_threshold'];pr['pass_threshold']=pr.p_create>=model['selected_threshold'];pr['error_baskets']=pr.pred_baskets-actual/U;preds.append(pr)
  if variant!='premium_l2':continue
  for f in model['features']:
   vals=tra[f].dropna();v=float(row[f]);med=float(vals.median());alter=d.copy();alter[f]=med;ap=score(alter,model).iloc[0]
   featuredetails.append(dict(cutoff=cut,feature=f,value=v,training_median=med,training_percentile=float((vals<=v).mean()*100),p_create_difference_pp=float((pr.p_create.iloc[0]-ap.p_create)*100),pred_baskets_difference=float(pr.pred_baskets.iloc[0]-ap.pred_baskets),interpretation='single feature replacement by training median; non-additive, non-causal'))
  groups={'active_imbalance':['l2_active_imbalance'],'large_active_orders':['l2_buy_large_parent_frac','l2_sell_large_parent_frac'],'unit_active_orders':['l2_buy_unit_parent_excess','l2_sell_unit_parent_excess'],'unit_bursts':['l2_buy_unit_burst_excess','l2_sell_unit_burst_excess'],'premium_compression':['l2_sell_compression_frac','l2_buy_compression_frac'],'passive_unit_sell':['l2_passive_sell_unit_frac'],'premium_state':[f for f in model['features'] if not f.startswith('l2_') and f not in ['lag_flow_pct','lag5_flow_pct','log_prev_assets','creation_allowed','redemption_allowed','turnover_pct']],'history_and_size':['lag_flow_pct','lag5_flow_pct','log_prev_assets','creation_allowed','redemption_allowed','turnover_pct']}
  assert sorted(sum(groups.values(),[]))==sorted(model['features'])
  # Exact group Shapley across all 256 coalitions, averaged over 32 real training rows.
  bg=tra[model['features']].sample(n=32,random_state=520600).reset_index(drop=True);g=list(groups);blocks=[]
  for mask in range(1<<len(g)):
   xx=bg.copy()
   for j,key in enumerate(g):
    if mask>>j&1:
     for f in groups[key]:xx[f]=row[f]
   blocks.append(xx)
  xx=pd.concat(blocks,ignore_index=True);pp=softmax(np.log(np.clip(model['classifier'].predict_proba(xx),1e-12,1))/model['temperature'],axis=1)[:,2].reshape(-1,len(bg)).mean(axis=1)
  sh=[]
  for j,key in enumerate(g):
   v=0
   for mask in range(1<<len(g)):
    if mask>>j&1:continue
    k=bin(mask).count('1');weight=math.factorial(k)*math.factorial(len(g)-k-1)/math.factorial(len(g));v+=weight*(pp[mask|(1<<j)]-pp[mask])
   sh.append(v);sensitivity.append(dict(cutoff=cut,group=key,contribution_pp=v*100,background_p_create=pp[0],case_p_create=pp[-1],background_rows=32))
  assert abs(sum(sh)-(pp[-1]-pp[0]))<1e-10
  # Feed robustness: the independently cached website minute prices, shifted by one minute, lag FX derived from mid FX.
  ws=ss.copy();ww=w.set_index('minute_id');ws['etf']=ws.minute_id.map(ww.etf_price);ws['mid']=ws.minute_id.map(ww.midpoint_iopv);ws['lag_settlement']=((ws['mid']*U-cash)/midfx*lagfx+cash)/U;ws['actual_settlement_buy']=ws.lag_settlement
  wb=make_features(ws,basket,lab,cut);wc=ws.rename(columns={'mid':'iopv_mid','lag_settlement':'iopv_estimate'});we=feature_row(n,wc,U,cut);wr=dict(**{k:v for k,v in wb.items() if k not in we},**we,cutoff=cut,prev_shares=prevshares,l2_quality_valid=True)
  wp=score(pd.DataFrame([wr]),model);wp['variant']='premium_l2_website_feed_sensitivity';wp['actual_shares']=actual;wp['actual_baskets']=actual/U;wp['selected_threshold']=model['selected_threshold'];wp['pass_threshold']=wp.p_create>=model['selected_threshold'];wp['error_baskets']=wp.pred_baskets-actual/U;preds.append(wp)
 # Minutes in which a >30bp lag-FX premium compresses; include both buy and sell amounts.
 p=ctx.set_index('minute_id');p['premium_bp']=(p.etf/p.iopv_estimate-1)*1e4;p['delta_bp']=p.premium_bp.diff();p['eligible']=p.premium_bp.shift().gt(30)&p.delta_bp.lt(0)&p.index.to_series().diff().eq(1);p=p.join(m.set_index('completed_minute'));p[p.eligible].to_csv(OUT/f'compression_{cut.replace(":","")}.csv')
P=pd.concat(preds,ignore_index=True);P.to_csv(OUT/'predictions.csv',index=False);pd.DataFrame(feats).to_csv(OUT/'features.csv',index=False);pd.DataFrame(featuredetails).to_csv(OUT/'feature_diagnostics.csv',index=False);pd.DataFrame(sensitivity).to_csv(OUT/'group_attribution.csv',index=False)
# Full-day time buckets and source rows of the strongest active and passive orders.
full=pd.read_csv(OUT/'orders_full.csv');m=pd.read_csv(OUT/'minutes_full.csv').set_index('completed_minute');bucket=(ct.ms//(15*60000))*15
flow=ct.assign(bucket=bucket,buy_amount=ct.amount.where(ct['BS标志'].eq('B'),0),sell_amount=ct.amount.where(ct['BS标志'].eq('S'),0)).groupby('bucket').agg(amount=('amount','sum'),buy_amount=('buy_amount','sum'),sell_amount=('sell_amount','sum'),volume=('成交数量','sum'))
flow['net_active']=flow.buy_amount-flow.sell_amount;flow['time']=flow.index.map(lambda x:f'{x//60:02}:{x%60:02}');flow.to_csv(OUT/'flow_15min.csv',index=False)
chosen=set(full.nlargest(12,'active_filled').order_id)|set(full[full.near_1u_original].nlargest(12,'passive_filled').order_id)
tr[tr['叫买序号'].isin(chosen)|tr['叫卖序号'].isin(chosen)].to_csv(OUT/'selected_order_trades.csv',index=False)
orders_raw[orders_raw['交易所委托号'].isin(chosen)].to_csv(OUT/'selected_order_submissions.csv',index=False)
full[full.order_id.isin(chosen)].sort_values('active_filled',ascending=False).to_csv(OUT/'selected_orders.csv',index=False)
# Additional auction and market-shape diagnostics; excluded from prediction inputs.
continuous=s[s.index.isin(list(range(571,691))+list(range(781,898)))].dropna(subset=['etf','mid','final_settlement']);late=tr[tr.ms>=53820000]
profile=dict(continuous_minutes=len(continuous),final_premium_mean_bp=float(continuous.final_settlement_bp.mean()),final_positive_fraction=float(continuous.final_settlement_bp.gt(0).mean()),final_above30_fraction=float(continuous.final_settlement_bp.gt(30).mean()),mid_premium_mean_bp=float(continuous.mid_bp.mean()),mid_positive_fraction=float(continuous.mid_bp.gt(0).mean()),final_premium_max_bp=float(continuous.final_settlement_bp.max()),final_premium_max_time=continuous.final_settlement_bp.idxmax(),last_trade_price=float(tr.iloc[-1]['成交价格']/1e4),closing_auction_shares=int(late['成交数量'].sum()),last_trade_time=clock_ms(tr.ms.max()))
provenance=dict(date=DATE,symbol='520600.SH',pcf_sha256=hashlib.sha256(raw).hexdigest(),unit=U,cash=cash,prev_nav=nav,components=len(components),mid_fx=midfx,final_buy_fx=finalfx,previous_buy_fx=lagfx,fx_previous_date='2026-09-01',actual_net_shares=actual,actual_net_baskets=actual/U,prev_shares=prevshares,current_shares=cur['shares_10k']*1e4,share_label=cur,prior_label_dates=[x['share_date'] for x in history],pcf_fields=fields,comparison=comparison,profile=profile,raw_trade_rows=len(tr),raw_order_rows=len(orders_raw),quality='all three L2 replays pass quote volume reconciliation and order/trade gates',availability='historical backfill without original receive timestamps; point-in-time availability assumed, not live validation',feature_source='independent supplied HK minute zip, raw L2 ETF completed bars; website cross-check only')
dump(provenance,OUT/'provenance.json');dump(audit,OUT/'native_audit.json');dump(summary,OUT/'order_signal_summary.json');pd.DataFrame(hkaudit).to_csv(OUT/'hk_coverage.csv',index=False)
print(P[['cutoff','variant','p_create','p_flat','p_redeem','pred_baskets','lo_baskets','hi_baskets','actual_baskets','pass_threshold']].to_string(index=False));print(json.dumps(profile,default=str));print(json.dumps(comparison));print(json.dumps(summary,ensure_ascii=False));print(pd.DataFrame(sensitivity).to_string(index=False))
