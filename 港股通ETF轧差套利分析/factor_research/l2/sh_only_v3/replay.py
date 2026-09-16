"""Exclude Shenzhen BEFORE daily ranking. Preserve frozen scores and inspect every date."""
from pathlib import Path
import json,gzip,hashlib
import pandas as pd,numpy as np,joblib
R=Path(__file__).resolve().parent;L=R.parent;M=L/'merged_years_v3';V=L/'premium_l2_gate_v3';E=L/'expanded_dates_v3';O=R/'outputs';O.mkdir(exist_ok=True)
old=json.loads((M/'outputs/data.json').read_text());meta={r['date']:r for r in old['main']};es={e['date']:e for p in [V/'plan.json',E/'data/plan.json',M/'plan.json'] for e in json.loads(p.read_text())['entries']};assert len(es)==353
hist=pd.read_parquet(V/'historical_candidates.parquet');hist=hist[hist.split.isin(['train','validation'])&hist.l2_available]
pool=pd.concat([hist,pd.read_parquet(L/'top_one_v3/scored.parquet'),pd.read_parquet(E/'scored.parquet'),pd.read_parquet(M/'scored.parquet')],ignore_index=True);pool=pool[pool.cutoff.eq('14:45')&pool.symbol.str.startswith('5')&pool.symbol.str.endswith('.SH')&pool.symbol.ne('513130.SH')].copy();assert not pool.duplicated(['date','symbol','fx_basis']).any()
sel=json.loads((V/'selection.json').read_text())
for fx,g in pool.groupby('fx_basis'):
 p=V/'models'/f'{fx}_1445_premium_l2.joblib';assert hashlib.sha256(p.read_bytes()).hexdigest()==sel[f'{fx}_1445_premium_l2']['model_sha256'];m=joblib.load(p);pool.loc[g.index,'score']=m['classifier'].predict_proba(g[m['features']])[:,1]
premium=pd.concat([pd.read_parquet(V/'premium.parquet'),pd.read_parquet(E/'data/premium.parquet'),pd.read_parquet(M/'premium.parquet')],ignore_index=True);premium=premium[premium.cutoff.eq('14:45')&premium.premium_gate&premium.symbol.str.startswith('5')&premium.symbol.ne('513130.SH')];assert not premium.duplicated(['date','symbol','fx_basis']).any()
flags={(x['date'],x['symbol']) for x in old['price_flags']};baskets={};pcfs={};history={};checks=0
def enrich(day,sym,net):
 global checks
 if day not in baskets:
  e=es[day]
  for k in ['pcf','basket']:
   p=Path(e['sources'][k]['path']);assert hashlib.sha256(p.read_bytes()).hexdigest()==e['sources'][k]['sha256']
  baskets[day]=json.load(gzip.open(e['sources']['basket']['path']));pcfs[day]=pd.read_csv(e['sources']['pcf']['path'],encoding='utf-8-sig',dtype={'基金代码':str}).set_index('基金代码')
 b=baskets[day][sym];raw=pcfs[day].loc[sym[:6],'当日累计申购上限份'];cap=pd.to_numeric(raw,errors='coerce');known=bool(np.isfinite(cap) and 0<cap<1e14);full=known and net>=cap-.01
 if sym not in history:history[sym]={x['share_date']:x for x in json.loads((L.parent/'inputs/share_history'/f'{sym}.json').read_text())['rows']}
 assert np.isclose(history[sym][day]['share_change_10k']*10000,net,rtol=0,atol=.01);checks+=1
 return dict(name=b['name'],unit=float(b['unit']),cap_shares=float(cap) if known else None,cap_U=float(cap/b['unit']) if known else None,cap_raw='' if pd.isna(raw) else str(raw),cap_status=('净量超过上限（需核验）' if net>cap+.01 else '净量达到申购上限') if full else ('净量低于上限（不保证有额度）' if known else '无可识别有限上限'),excluded=bool(full))
def pick(g,day,fx):
 r=dict(date=day,phase=meta[day]['phase'],fx='盘中汇率代理' if fx=='lag' else '最终汇率（事后）',cutoff='14:45',quality='仅沪市且隔离513130',symbol='',name='',score=None,net_shares=None,net_wan=None,unit=None,net_U=None,result='空仓',reason='无符合条件的沪市候选',cap_shares=None,cap_U=None,cap_raw='',cap_status='',excluded=False,retained=False,decision='原规则空仓')
 if len(g):
  x=g.sort_values(['score','symbol'],ascending=[False,True],kind='stable').iloc[0];r.update(symbol=x.symbol,score=float(x.score),net_shares=float(x.net_shares),net_wan=float(x.net_shares/10000),reason='');r.update(enrich(day,x.symbol,x.net_shares));r['net_U']=r['net_shares']/r['unit'];r['result']='命中净申购' if x.net_shares>0 else ('未命中：净赎回' if x.net_shares<0 else '未命中：净量不变');r['retained']=not r['excluded'];r['decision']='排除：净量达到/超过上限' if r['excluded'] else '保留'
 return r
rows={'lag':[],'final':[]};rr=[];coverage=[]
for day in sorted(es):
 for fx in ['lag','final']:
  g=pool[pool.date.eq(day)&pool.fx_basis.eq(fx)];gate=premium[premium.date.eq(day)&premium.fx_basis.eq(fx)];missing=sorted(set(gate.symbol)-set(g.symbol));bad=sorted(sym for sym in gate.symbol if (day,sym) in flags)
  r=pick(g,day,fx);r.update(candidate_complete=not missing and not bad,data_quality_status='候选数据有缺项/价格精度异常' if missing or bad else '候选检查通过',gate_candidates=len(gate),scored_candidates=len(g),missing_l2_candidates=len(missing),price_flag_candidates=len(bad),selected_price_warning=(day,r['symbol']) in flags,all_market_symbol=meta[day]['symbol'],all_market_net_U=meta[day]['net_U'])
  if not len(g) and len(gate):r['reason']='沪市初筛候选无法评分'
  assert not r['symbol'] or r['symbol'].startswith('5')
  rows[fx].append(r);coverage.append(dict(date=day,fx=fx,missing=missing,price_flags=bad))
  if fx=='lag':
   keep=np.array([not enrich(day,x.symbol,x.net_shares)['excluded'] for x in g.itertuples()],dtype=bool);q=pick(g[keep],day,fx);q.update(candidate_complete=r['candidate_complete'],data_quality_status=r['data_quality_status'],original_symbol=r['symbol'],original_net_shares=r['net_shares']);q['decision']='事后改选' if q['symbol'] and q['symbol']!=r['symbol'] else ('原标的保留' if q['symbol'] else '空仓');rr.append(q)
main=pd.DataFrame(rows['lag']);final=pd.DataFrame(rows['final']);rerank=pd.DataFrame(rr)
def stats(d,cap):
 z=d[d.symbol.ne('')];z=z[z.retained] if cap else z;n=len(z);k=int(z.net_shares.gt(0).sum());error=(n-k)/n if n else None
 if n:
  den=1+1.96**2/n;c=(error+1.96**2/(2*n))/den;h=1.96*np.sqrt(error*(1-error)/n+1.96**2/(4*n*n))/den;ci=[max(0,c-h),min(1,c+h)]
 else:ci=[None,None]
 return dict(days=len(d),selected=n,positive=k,zero=int(z.net_shares.eq(0).sum()),negative=int(z.net_shares.lt(0).sum()),accuracy=k/n if n else None,error_rate=error,error_wilson95=ci,no_selection=len(d)-n,removed=int(d.excluded.sum()),unknown_cap=int(z.cap_shares.isna().sum()),incomplete_days=int((~d.candidate_complete).sum()))
cohorts=[('2025跨期回放',lambda d:d.date.str.startswith('2025')),('2026新增29日',lambda d:d.phase.eq('2026新增检验')),('2026后续检验73日',lambda d:d.date.str.startswith('2026')&~d.phase.isin(['训练期回放','验证期回放','2026补齐训练期回放'])),('2026此前检验44日',lambda d:d.phase.isin(['历史压力检验','上轮检验','本轮新增检验'])),('训练及验证43日',lambda d:d.phase.isin(['训练期回放','验证期回放'])),('全部353日（混合回放）',lambda d:pd.Series(True,index=d.index))]
summary=[];quality_summary=[];monthly=[]
for fx,d in [('lag',main),('final',final)]:
 for name,fn in cohorts:
  for rule,cap in [('原规则',False),('剔除原选中满额样本',True)]:summary.append(dict(cohort=name,fx=fx,rule=rule,**stats(d[fn(d)],cap)))
for name,fn in cohorts:
 summary.append(dict(cohort=name,fx='lag',rule='事后剔除候选再选第一',**stats(rerank[fn(rerank)],False)))
 for rule,cap in [('原规则',False),('剔除原选中满额样本',True)]:
  z=main[fn(main)];quality_summary.append(dict(cohort=name,rule=rule,excluded_quality_dates=int((~z.candidate_complete).sum()),**stats(z[z.candidate_complete],cap)))
for month,g in main.groupby(main.date.str[:7]):
 for rule,cap in [('原规则',False),('剔除原选中满额样本',True)]:monthly.append(dict(month=month,rule=rule,**stats(g,cap)))
payload={k:json.loads(d.to_json(orient='records',force_ascii=False)) for k,d in [('main',main),('final',final),('reranked',rerank)]};payload.update(summary=summary,quality_summary=quality_summary,monthly=monthly,excluded_dates=old['excluded_dates'],price_flags=[x for x in old['price_flags'] if x['symbol'].startswith('5')],market_scope='仅沪市5开头ETF，先排除深圳再每日排名')
(O/'data.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2));pool.to_parquet(O/'candidates.parquet',index=False)
cn={'date':'交易日','phase':'样本阶段','symbol':'沪市第一名','name':'基金名称','score':'模型分数（非概率）','net_shares':'实际净申赎份额','net_wan':'实际净申赎万份','unit':'申赎单位份额','net_U':'实际净申赎U','cap_shares':'当日累计申购上限份','cap_U':'上限U','cap_raw':'上限原始值','cap_status':'上限状态','excluded':'事后满额剔除','retained':'事后保留','decision':'处理','result':'方向结果','reason':'空仓原因','fx':'汇率口径','cutoff':'截止时间','quality':'范围','candidate_complete':'沪市候选检查通过','data_quality_status':'数据质量状态','all_market_symbol':'原沪深范围第一名','all_market_net_U':'原沪深第一名实际净申赎U'}
for name,d in [('沪市每日择优_353日',main),('沪市剔除满额_保留序列',main[main.retained]),('沪市质量完整且剔除满额_保留序列',main[main.retained&main.candidate_complete]),('沪市最终汇率_事后对照',final),('沪市剔除候选再重选_事后对照',rerank)]:d.rename(columns=cn).to_csv(O/(name+'.csv'),index=False,encoding='utf-8-sig')
(O/'validation.json').write_text(json.dumps(dict(share_history_checks=checks,filter_before_ranking=True,only_shanghai_codes=True,unique_days=len(main),model_hashes=sel,coverage=coverage),ensure_ascii=False,indent=2))
print(pd.DataFrame(quality_summary)[['cohort','rule','days','selected','positive','zero','negative','accuracy','incomplete_days']].to_string(index=False),flush=True)
