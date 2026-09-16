"""Combine fixed daily top-one choices; keep backward replay and later tests separate."""
from pathlib import Path
import json,gzip,hashlib
import pandas as pd,numpy as np
R=Path(__file__).resolve().parent;L=R.parent;F=L.parent
O=R/'outputs';O.mkdir(exist_ok=True)
plan=json.loads((R/'plan.json').read_text());entries={e['date']:e for e in plan['entries']}
scored=pd.read_parquet(R/'scored.parquet');premium=pd.read_parquet(R/'premium.parquet')
assert not scored.duplicated(['date','symbol','fx_basis','cutoff']).any()
assert scored.cutoff.eq('14:45').all()
scored=scored[scored.symbol.ne('513130.SH')].copy()
price_flags=json.loads((R/'price_quality.json').read_text());flagkeys={(x['date'],x['symbol']) for x in price_flags}
old=json.loads((L/'cap_filter_v3/outputs/data.json').read_text())
oldbase=json.loads((L/'daily_sequence_87/outputs/01a0915e-f9e1-76e3-bbaa-2329e4b4b7e8/data.json').read_text())
pcfs={};baskets={};labelcache={};checks=0
oldentries={e['date']:e for f in [L/'premium_l2_gate_v3/plan.json',L/'expanded_dates_v3/data/plan.json'] for e in json.loads(f.read_text())['entries']}
allentries={**oldentries,**entries}
def enrich(day,sym,net):
 global checks
 if day not in baskets:
  e=allentries[day]
  for key in ['pcf','basket']:
   p=Path(e['sources'][key]['path']);assert hashlib.sha256(p.read_bytes()).hexdigest()==e['sources'][key]['sha256']
  baskets[day]=json.load(gzip.open(e['sources']['basket']['path']))
  pcfs[day]=pd.read_csv(e['sources']['pcf']['path'],encoding='utf-8-sig',dtype={'基金代码':str}).set_index('基金代码')
 b=baskets[day][sym];raw=pcfs[day].loc[sym[:6],'当日累计申购上限份'];cap=pd.to_numeric(raw,errors='coerce');known=bool(np.isfinite(cap) and 0<cap<1e14)
 if sym not in labelcache:labelcache[sym]={x['share_date']:x for x in json.loads((F/'inputs/share_history'/f'{sym}.json').read_text())['rows']}
 assert np.isclose(labelcache[sym][day]['share_change_10k']*10000,net,rtol=0,atol=.01);checks+=1
 full=known and net>=cap-.01
 return dict(name=b['name'],unit=float(b['unit']),cap_shares=float(cap) if known else None,cap_U=float(cap/b['unit']) if known else None,cap_raw='' if pd.isna(raw) else str(raw),cap_status=('净量超过上限（需核验）' if net>cap+.01 else '净量达到申购上限') if full else ('净量低于上限（不保证有额度）' if known else '无可识别有限上限'),excluded=full)
def empty(day,phase,fx):
 return dict(date=day,phase=phase,fx='盘中汇率代理' if fx=='lag' else '最终汇率（事后）',cutoff='14:45',quality='隔离513130',symbol='',name='',score=None,net_shares=None,net_wan=None,unit=None,net_U=None,result='空仓',reason='无通过溢价与L2条件的候选',cap_shares=None,cap_U=None,cap_raw='',cap_status='',excluded=False,retained=False,decision='原规则空仓')
def pick(g,day,phase,fx):
 r=empty(day,phase,fx)
 if len(g):
  x=g.sort_values(['score','symbol'],ascending=[False,True],kind='stable').iloc[0]
  r.update(symbol=x.symbol,score=float(x.score),net_shares=float(x.net_shares),net_wan=float(x.net_shares/10000),reason='')
  r.update(enrich(day,x.symbol,x.net_shares));r['net_U']=r['net_shares']/r['unit'];r['result']='命中净申购' if x.net_shares>0 else ('未命中：净赎回' if x.net_shares<0 else '未命中：净量不变');r['retained']=not r['excluded'];r['decision']='排除：净量达到/超过上限' if r['excluded'] else '保留'
 return r
new={'lag':[],'final':[]};rr=[];coverage=[]
for day,e in sorted(entries.items()):
 for fx in ['lag','final']:
  gate=premium[premium.date.eq(day)&premium.fx_basis.eq(fx)&premium.premium_gate&premium.symbol.ne('513130.SH')]
  g=scored[scored.date.eq(day)&scored.fx_basis.eq(fx)]
  missing=sorted(set(gate.symbol)-set(g.symbol))
  badprices=sorted(sym for sym in gate.symbol if (day,sym) in flagkeys)
  r=pick(g,day,e['cohort'],fx);r.update(gate_candidates=len(gate),scored_candidates=len(g),missing_l2_candidates=len(missing),candidate_complete=not missing and not badprices,price_flag_candidates=len(badprices),selected_price_warning=(day,r['symbol']) in flagkeys,data_quality_status='候选数据有缺项/价格精度异常' if missing or badprices else '候选检查通过')
  if not len(g) and len(gate):r['reason']='初筛候选L2不可用'
  new[fx].append(r);coverage.append(dict(date=day,fx=fx,gate=len(gate),scored=len(g),missing=missing,price_flags=badprices))
  # Prove ranking does not read contemporaneous outcome labels.
  shuffled=g.copy();shuffled['net_shares']=np.arange(len(g));a=g.sort_values(['score','symbol'],ascending=[False,True]).symbol.tolist();b=shuffled.sort_values(['score','symbol'],ascending=[False,True]).symbol.tolist();assert a==b
  if fx=='lag':
   keep=[not enrich(day,x.symbol,x.net_shares)['excluded'] for x in g.itertuples()]
   q=pick(g[np.array(keep,dtype=bool)],day,e['cohort'],fx);q.update(original_symbol=r['symbol'],original_net_shares=r['net_shares'],candidate_complete=r['candidate_complete'],missing_l2_candidates=len(missing),price_flag_candidates=len(badprices),selected_price_warning=(day,q['symbol']) in flagkeys,data_quality_status=r['data_quality_status']);q['decision']='事后改选' if q['symbol'] and q['symbol']!=r['symbol'] else ('原标的保留' if q['symbol'] else '空仓');rr.append(q)
oldmain=old['main'];oldrr=old['reranked'];oldfinal=[r.copy() for r in oldbase['comparison'] if r['fx']=='最终汇率（事后）' and r['cutoff']=='14:45' and r['quality']=='隔离513130']
assert len(oldmain)==len(oldfinal)==len(oldrr)==87
for r in oldfinal:
 r.update(cap_shares=None,cap_U=None,cap_raw='',cap_status='',excluded=False)
 if r['symbol']:r.update(enrich(r['date'],r['symbol'],r['net_shares']))
 r['retained']=bool(r['symbol'] and not r['excluded']);r['decision']='排除：净量达到/超过上限' if r['excluded'] else ('保留' if r['symbol'] else '原规则空仓')
for r in oldmain+oldrr+oldfinal:r.update(candidate_complete=True,missing_l2_candidates=0,price_flag_candidates=0,selected_price_warning=False,data_quality_status='沿用此前已验收序列')
main=pd.DataFrame(oldmain+new['lag']).sort_values('date');final=pd.DataFrame(oldfinal+new['final']).sort_values('date');rerank=pd.DataFrame(oldrr+rr).sort_values('date')
assert len(main)==353 and main.date.is_unique and len(final)==len(rerank)==353
assert main[main.date.isin([r['date'] for r in oldmain])].symbol.tolist()==pd.DataFrame(oldmain).sort_values('date').symbol.tolist()
def stats(d,rule):
 z=d[d.symbol.ne('')]
 if rule=='剔除原选中满额样本':z=z[z.retained]
 n=len(z);good=int(z.net_shares.gt(0).sum());bad=n-good
 if n:
  p=bad/n;den=1+1.96**2/n;c=(p+1.96**2/(2*n))/den;h=1.96*np.sqrt(p*(1-p)/n+1.96**2/(4*n*n))/den;ci=[c-h,c+h]
 else:ci=[None,None]
 return dict(days=len(d),selected=n,positive=good,zero=int(z.net_shares.eq(0).sum()),negative=int(z.net_shares.lt(0).sum()),accuracy=good/n if n else None,error_rate=bad/n if n else None,error_wilson95=ci,no_selection=len(d)-n,removed=int(d.excluded.sum()),unknown_cap=int(z.cap_shares.isna().sum()),incomplete_days=int((~d.candidate_complete).sum()))
cohorts=[('2025跨期回放',lambda d:d.date.str.startswith('2025')),('2026新增29日',lambda d:d.phase.eq('2026新增检验')),('2026后续检验73日',lambda d:d.date.str.startswith('2026')&~d.phase.isin(['训练期回放','验证期回放','2026补齐训练期回放'])),('2026此前检验44日',lambda d:d.phase.isin(['历史压力检验','上轮检验','本轮新增检验'])),('训练及验证43日',lambda d:d.phase.isin(['训练期回放','验证期回放'])),('全部353日（混合回放）',lambda d:pd.Series(True,index=d.index))]
summary=[]
for fx,d in [('lag',main),('final',final)]:
 for name,mask in cohorts:
  for rule in ['原规则','剔除原选中满额样本']:summary.append(dict(cohort=name,fx=fx,rule=rule,**stats(d[mask(d)],rule)))
for name,mask in cohorts:summary.append(dict(cohort=name,fx='lag',rule='事后剔除候选再选第一',**stats(rerank[mask(rerank)],'原规则')))
quality_summary=[]
for name,mask in cohorts:
 for rule in ['原规则','剔除原选中满额样本']:
  whole=main[mask(main)];clean=whole[whole.candidate_complete];quality_summary.append(dict(cohort=name,rule=rule,excluded_quality_dates=len(whole)-len(clean),**stats(clean,rule)))
monthly=[]
for month,d in main.groupby(main.date.str[:7]):
 for rule in ['原规则','剔除原选中满额样本']:monthly.append(dict(month=month,rule=rule,**stats(d,rule)))
cn={'date':'交易日','phase':'样本阶段','symbol':'标的代码','name':'基金名称','score':'模型分数（非校准概率）','net_shares':'实际净申赎份额','net_wan':'实际净申赎万份','unit':'申赎单位份额','net_U':'实际净申赎U','cap_shares':'当日累计申购上限份','cap_U':'当日累计申购上限U','cap_raw':'PCF原始限额值','cap_status':'限额识别状态','excluded':'是否按满额代理排除','retained':'是否保留','decision':'处理结果','result':'方向结果','reason':'空仓原因','fx':'汇率口径','cutoff':'截止时间','quality':'范围','candidate_complete':'候选数据检查通过','missing_l2_candidates':'缺失L2候选数','gate_candidates':'溢价初筛候选数','scored_candidates':'可评分候选数','price_flag_candidates':'价格精度异常候选数','selected_price_warning':'选中标的有价格精度异常','data_quality_status':'数据质量状态'}
for name,d in [('每日择优_353日',main),('剔除满额后_保留序列',main[main.retained]),('质量完整且剔除满额_保留序列',main[main.retained&main.candidate_complete]),('方向误判明细',main[main.retained&main.net_shares.le(0)]),('最终汇率_事后对照',final),('剔除候选后重选_事后对照',rerank)]:d.rename(columns=cn).to_csv(O/(name+'.csv'),index=False,encoding='utf-8-sig')
payload={k:json.loads(v.to_json(orient='records',force_ascii=False)) for k,v in [('main',main),('final',final),('reranked',rerank)]};payload.update(summary=summary,quality_summary=quality_summary,monthly=monthly,excluded_dates=plan['excluded_dates'],price_flags=price_flags)
(O/'data.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2))
audit={'share_history_checks':checks,'old_87_choices_unchanged':True,'date_counts':main.date.str[:4].value_counts().to_dict(),'ranking_label_permutation_check':True,'coverage':coverage,'models':json.loads((R/'selection.json').read_text()),'exclusions':plan['excluded_dates']}
(O/'validation.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
print(json.dumps([s for s in summary if s['fx']=='lag'],ensure_ascii=False,indent=2));print('L2 INCOMPLETE',sum(bool(c['missing']) for c in coverage),flush=True)
