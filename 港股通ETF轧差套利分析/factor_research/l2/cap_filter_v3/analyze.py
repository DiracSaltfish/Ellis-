"""Ex-post capacity sensitivity. Frozen scores; no retraining or live-capacity claim."""
from pathlib import Path
import gzip,json,hashlib
import numpy as np,pandas as pd,joblib
R=Path(__file__).resolve().parent;L=R.parent;V=L/'premium_l2_gate_v3';E=L/'expanded_dates_v3'
O=R/'outputs';O.mkdir(parents=True,exist_ok=True)
old=L/'daily_sequence_87/outputs/01a0915e-f9e1-76e3-bbaa-2329e4b4b7e8/data.json'
baseline=pd.DataFrame(json.loads(old.read_text())['main'])
plans=[json.loads((V/'plan.json').read_text()),json.loads((E/'data/plan.json').read_text())]
entries={e['date']:e for p in plans for e in p['entries']};assert len(entries)==87
pcfs={};baskets={};hashes={}
for day,e in entries.items():
 for source in ['pcf','basket']:
  p=Path(e['sources'][source]['path']);h=hashlib.sha256(p.read_bytes()).hexdigest();assert h==e['sources'][source]['sha256'];hashes[str(p)]=h
 pcfs[day]=pd.read_csv(e['sources']['pcf']['path'],encoding='utf-8-sig',dtype={'基金代码':str}).set_index('基金代码');assert pcfs[day].index.is_unique
 baskets[day]=json.load(gzip.open(e['sources']['basket']['path']))
hist=pd.read_parquet(V/'historical_candidates.parquet');hist=hist[hist.fx_basis.eq('lag')&hist.cutoff.eq('14:45')&hist.l2_available&hist.split.isin(['train','validation'])].copy()
key='lag_1445_premium_l2';modelpath=V/'models'/f'{key}.joblib';sel=json.loads((V/'selection.json').read_text())
assert hashlib.sha256(modelpath.read_bytes()).hexdigest()==sel[key]['model_sha256']
model=joblib.load(modelpath);hist['score']=model['classifier'].predict_proba(hist[model['features']])[:,1]
pool=pd.concat([hist,pd.read_parquet(L/'top_one_v3/scored.parquet'),pd.read_parquet(E/'scored.parquet')],ignore_index=True)
pool=pool[pool.fx_basis.eq('lag')&pool.cutoff.eq('14:45')&pool.symbol.ne('513130.SH')].copy()
assert not pool.duplicated(['date','symbol']).any()
labelcache={};verified=0
def enrich(day,symbol,net):
 global verified
 b=baskets[day][symbol];p=pcfs[day].loc[symbol[:6]]
 raw=p['当日累计申购上限份'];cap=pd.to_numeric(raw,errors='coerce');known=bool(np.isfinite(cap) and 0<cap<1e14)
 if b['creation_limit'] is not None and np.isfinite(cap):assert np.isclose(cap,b['creation_limit'])
 if symbol not in labelcache:labelcache[symbol]={r['share_date']:r for r in json.loads((L.parent/'inputs/share_history'/f'{symbol}.json').read_text())['rows']}
 assert np.isclose(labelcache[symbol][day]['share_change_10k']*10000,net,rtol=0,atol=.01)
 verified+=1
 full=known and net>=cap-.01
 state=('净量超过上限（需核验）' if net>cap+.01 else '净量达到申购上限') if full else ('净量低于上限（不保证有额度）' if known else '无可识别有限上限')
 return dict(cap_shares=float(cap) if known else None,cap_U=float(cap/b['unit']) if known else None,cap_raw='' if pd.isna(raw) else str(raw),cap_status=state,excluded=bool(full),unit=float(b['unit']),name=b['name'],cap_minus_net_U=float((cap-net)/b['unit']) if known else None)
caps=[enrich(x.date,x.symbol,x.net_shares) for x in pool.itertuples()]
for col in caps[0]:pool[col]=[c[col] for c in caps]
primary=[];reranked=[]
for source in baseline.to_dict('records'):
 day=source['date'];g=pool[pool.date.eq(day)].sort_values(['score','symbol'],ascending=[False,True],kind='stable')
 assert (g.iloc[0].symbol if len(g) else '')==source['symbol']
 r=source.copy();r.update(original_symbol=source['symbol'],original_net_shares=source['net_shares'],cap_shares=None,cap_U=None,cap_raw='',cap_status='',excluded=False,cap_minus_net_U=None)
 if source['symbol']:
  assert abs(g.iloc[0].score-source['score'])<1e-8
  r.update(enrich(day,source['symbol'],source['net_shares']))
 r['retained']=bool(r['symbol'] and not r['excluded']);r['decision']='排除：净量达到/超过上限' if r['excluded'] else ('保留' if r['symbol'] else '原规则空仓')
 primary.append(r)
 z=g[~g.excluded];q={**r,'symbol':'','name':'','score':None,'net_shares':None,'net_wan':None,'unit':None,'net_U':None,'result':'空仓','reason':'排除满额代理后无候选' if len(g) else r['reason'],'cap_shares':None,'cap_U':None,'cap_raw':'','cap_status':'','excluded':False,'retained':False,'decision':'空仓','cap_minus_net_U':None}
 if len(z):
  w=z.iloc[0];q.update({k:w[k] for k in ['symbol','name','score','net_shares','unit','cap_shares','cap_U','cap_raw','cap_status','cap_minus_net_U']});q.update(net_wan=w.net_shares/10000,net_U=w.net_shares/w.unit,result='命中净申购' if w.net_shares>0 else ('未命中：净赎回' if w.net_shares<0 else '未命中：净量不变'),reason='',retained=True,decision='事后改选' if w.symbol!=r['symbol'] else '原标的保留')
 reranked.append(q)
main=pd.DataFrame(primary);rerank=pd.DataFrame(reranked)
def stats(d,mask):
 z=d[mask];n=len(z);good=int(z.net_shares.gt(0).sum());bad=n-good
 if n:
  v=bad/n;den=1+1.96**2/n;c=(v+1.96**2/(2*n))/den;half=1.96*np.sqrt(v*(1-v)/n+1.96**2/(4*n*n))/den;ci=[c-half,c+half]
 else:ci=[None,None]
 return dict(days=len(d),selected=n,positive=good,negative=int(z.net_shares.lt(0).sum()),zero=int(z.net_shares.eq(0).sum()),no_selection=len(d)-n,accuracy=good/n if n else None,error_rate=bad/n if n else None,error_wilson95=ci,known_cap=int(z.cap_shares.notna().sum()),unknown_cap=int(z.cap_shares.isna().sum()))
summaries=[]
for cohort,phases in [('全部87日',main.phase.unique()),('训练及验证43日',['训练期回放','验证期回放']),('后续检验44日',['历史压力检验','上轮检验','本轮新增检验']),('本轮新增13日',['本轮新增检验'])]:
 d=main[main.phase.isin(phases)];rr=rerank[rerank.phase.isin(phases)]
 for rule,frame,mask in [('原规则',d,d.symbol.ne('')),('剔除原选中满额样本',d,d.retained),('事后剔除候选再选第一',rr,rr.retained)]:summaries.append(dict(cohort=cohort,rule=rule,removed_original=int(d.excluded.sum()),**stats(frame,mask)))
cn={'date':'交易日','phase':'样本阶段','symbol':'标的代码','name':'基金名称','score':'模型分数','net_shares':'实际净申赎份额','net_wan':'实际净申赎万份','unit':'申赎单位份额','net_U':'实际净申赎U','cap_shares':'当日累计申购上限份','cap_U':'当日累计申购上限U','cap_raw':'PCF原始限额值','cap_status':'限额识别状态','excluded':'是否按满额代理排除','retained':'是否保留','decision':'本次处理','original_symbol':'原选中标的','original_net_shares':'原标的实际净申赎份额','cap_minus_net_U':'上限减净申购U（非可用额度）','result':'方向结果','reason':'空仓原因','fx':'汇率口径','cutoff':'截止时间','quality':'范围'}
for filename,d in [('排除满额_每日序列87日.csv',main),('排除满额_保留样本.csv',main[main.retained]),('排除满额_剔除明细.csv',main[main.excluded]),('事后重选_每日序列87日.csv',rerank)]:d.rename(columns=cn).to_csv(O/filename,index=False,encoding='utf-8-sig')
pool.to_parquet(O/'candidate_caps.parquet',index=False)
payload={'main':json.loads(main.to_json(orient='records',force_ascii=False)),'reranked':json.loads(rerank.to_json(orient='records',force_ascii=False)),'summary':summaries}
(O/'data.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2))
validation=dict(source_hashes=hashes,frozen_model_sha256=sel[key]['model_sha256'],candidate_rows=len(pool),share_history_checks=verified,original_87_choices_reproduced=True,cap_rule='0 < daily cumulative creation cap < 1e14 and realized net_shares >= cap - 0.01 shares',net_exceeds_cap=pool[pool.cap_status.eq('净量超过上限（需核验）')][['date','symbol','net_shares','cap_shares']].to_dict('records'))
(O/'validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2));print(json.dumps(summaries,ensure_ascii=False,indent=2));print('REMOVED',main[main.excluded][['date','symbol','net_U','cap_U','phase']].to_string(index=False));print('EXCEEDS',len(validation['net_exceeds_cap']))
