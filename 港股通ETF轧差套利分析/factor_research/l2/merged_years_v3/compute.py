"""Frozen daily top-one replay across new 2025 and 2026 data, no fitting."""
from pathlib import Path,PureWindowsPath
from concurrent.futures import ThreadPoolExecutor
import json,sys,gzip,time,argparse
import pandas as pd,numpy as np,joblib
R=Path(__file__).resolve().parent;L=R.parent;F=L.parent;V=L/'premium_l2_gate_v3'
sys.path.insert(0,str(V));import pipeline as p
def save(name,obj):
 (R/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=p.js))
def freeze():
 if (R/'plan.json').exists():return
 old=json.loads((V/'plan.json').read_text());old_days={e['day'] for f in [V/'plan.json',L/'expanded_dates_v3/data/plan.json'] for e in json.loads(f.read_text())['entries']}
 (R/'manifests').mkdir(exist_ok=True);entries=[];excluded=[]
 manifests=sorted((R/'win_manifests').glob('*.json'))+sorted((L/'manifests_2026').glob('????????.json'))
 for mf in manifests:
  day=mf.stem
  if day in old_days:continue
  if not (day.startswith('2025') or '20260101'<=day<='20260707'):excluded.append(dict(day=day,reason='outside_comparable_mainline_dates'));continue
  m=json.loads(mf.read_text());win=day.startswith('2025')
  if not (m.get('status')=='extracted' if win else m.get('verified')):excluded.append(dict(day=day,reason='extraction_not_completed'));continue
  sf=F/'results/series'/f'{day}.parquet';bf=F/'inputs/baskets'/f'{day}.json.gz'
  if not sf.exists():sf=R/'valuation/results/series'/f'{day}.parquet'
  if not bf.exists():bf=R/'valuation/inputs/baskets'/f'{day}.json.gz'
  pf=p.PCF/f'{day}_主表.csv'
  if not all(f.exists() for f in [sf,bf,pf]):excluded.append(dict(day=day,reason='missing_valuation_or_pcf',missing=[str(f) for f in [sf,bf,pf] if not f.exists()]));continue
  files=[]
  if win:
   grouped={}
   for f in m['files']:
    sym=f['symbol'];name=PureWindowsPath(f['path']).name;grouped.setdefault((sym,name),[]).append(f)
   for (sym,name),variants in grouped.items():
    # Prefer exchange-correct original export when both copies exist. Never choose on outcome.
    variants=sorted(variants,key=lambda x:(PureWindowsPath(x['source_member']).parent.name!=sym,x['source_member']))
    f=variants[0];rel=PureWindowsPath(f['path']).relative_to(PureWindowsPath('F:/港股通ETF轧差套利分析/win1_2025/retained'))
    files.append({**f,'path':str(R/'raw2025'/Path(*rel.parts)),'win_source':f['path'],'transfer_relative':str(rel).replace('\\','/'),'variant_count':len(variants)})
  else:
   for f in m['files']:files.append({**f,'symbol':Path(f['path']).parent.name})
  normalized=R/'manifests'/f'{day}.json';normalized.write_text(json.dumps({'source_manifest':str(mf),'files':files},ensure_ascii=False))
  entries.append(dict(day=day,date=f'{day[:4]}-{day[4:6]}-{day[6:]}',split='new_test',cohort='2025跨期回放' if win else ('2026补齐训练期回放' if day<='20260227' else '2026新增检验'),host='win1' if win else 'machome',sources={k:dict(path=str(f),sha256=p.digest(f)) for k,f in [('manifest',normalized),('series',sf),('basket',bf),('pcf',pf)]}))
 _,lh=p.labels();plan={**old,'entries':entries,'excluded_dates':excluded,'cutoffs':['14:45'],'fx_bases':['lag','final'],'label_hashes':lh,'scope':'Frozen 2026 classifiers applied to 2025 as backward replay, not temporal out-of-sample. 2026 new evaluation reported separately. No tuning or retraining.'};save('plan.json',plan)
 save('selection.json',json.loads((V/'selection.json').read_text()));print('FROZEN',len(entries),'dates',pd.Series([e['cohort'] for e in entries]).value_counts().to_dict(),flush=True)
def premium():
 p.R=R;p.premium()
 d=pd.read_parquet(R/'premium.parquet');d=d[d.premium_gate];files=[]
 for e in json.loads((R/'plan.json').read_text())['entries']:
  if e['host']!='win1':continue
  wanted=set(d[d.date.eq(e['date'])].symbol);m=json.loads(Path(e['sources']['manifest']['path']).read_text())
  for f in m['files']:
   if f['symbol'] in wanted and Path(f['path']).name in ['逐笔成交.csv','逐笔委托.csv','行情.csv']:files.append(f)
 save('transfer_files.json',files);(R/'transfer_list.txt').write_text('\n'.join(f['transfer_relative'] for f in files)+'\n',encoding='utf-8')
 print('TRANSFER',len(files),'files',round(sum(f['bytes'] for f in files)/1e9,3),'GB',flush=True)
def native():
 plan=json.loads((R/'plan.json').read_text());premium=pd.read_parquet(R/'premium.parquet');(R/'daily').mkdir(exist_ok=True)
 for e in plan['entries']:
  day=e['day'];dest=R/'daily'/f'{day}.parquet'
  if dest.exists():continue
  d=premium[premium.date.eq(e['date'])&premium.premium_gate];m=json.loads(Path(e['sources']['manifest']['path']).read_text());lookup={(f['symbol'],Path(f['path']).name):f for f in m['files']};records=[];errs=[];start=time.monotonic()
  def one(task):
   (sym,cut),rows=task;r=rows.iloc[0]
   try:
    src=[lookup[(sym,name)] for name in ['逐笔成交.csv','逐笔委托.csv','行情.csv']]
    for f in src:assert Path(f['path']).stat().st_size==f['bytes'],'file missing or changed size'
    profile='cn_etf_2025' if day.startswith('2025') else ('sh_etf_20260706' if sym.endswith('.SH') else 'sz_etf_20260706') if day>='20260706' else 'cn_etf_2026_h1'
    n=p.etf_l2.process_files(*[f['path'] for f in src],int(sym[:6]),int(day),int(r.unit),cutoff=cut,session_profile=profile)
    assert n['audit']['order_features_valid'] and n['audit']['trade_features_valid'],str(n['audit']['issues'])
    s=pd.read_parquet(e['sources']['series']['path'],filters=[('symbol','==',sym)]);s['minute_id']=s.minute.map(lambda x:int(x[:2])*60+int(x[3:]));ctx=s[s.minute_id.isin(p.expected_minutes(cut))].rename(columns={'mid':'iopv_mid','lag_settlement':'iopv_estimate'}).dropna(subset=['etf','iopv_mid','iopv_estimate'])
    extra=p.feature_row(n,ctx,int(r.unit),cut);new=p.execution_features(n,r.unit,r.prev_shares,sym[-2:]);features={**extra,**new}
    return [dict(**x,**{k:features[k] for k in p.ORDER},v2_sell_unit_U=new['v2_sell_unit_U'],v2_buy_unit_U=new['v2_buy_unit_U'],l2_available=True,quote_reconciled=n['audit']['quote_reconciled'],session_profile=profile) for x in rows.to_dict('records')],[]
   except Exception as exc:return [dict(**x,l2_available=False) for x in rows.to_dict('records')],[dict(day=day,symbol=sym,error=str(exc))]
  with ThreadPoolExecutor(max_workers=4) as pool:
   for rec,err in pool.map(one,list(d.groupby(['symbol','cutoff']))):records+=rec;errs+=err
  pd.DataFrame(records,columns=None if records else list(premium.columns)+['l2_available']).to_parquet(dest,index=False);save('daily/'+day+'_audit.json',errs)
  print('NATIVE',day,len(d),'rows',len(errs),'errors',round(time.monotonic()-start,2),'sec',flush=True)
def score():
 plan=json.loads((R/'plan.json').read_text());d=pd.concat([pd.read_parquet(R/'daily'/f'{e["day"]}.parquet') for e in plan['entries']],ignore_index=True);d=d[d.l2_available.eq(True)].copy();out=[]
 for fx,z in d.groupby('fx_basis'):
  key=f'{fx}_1445_premium_l2';path=V/'models'/f'{key}.joblib';assert p.digest(path)==json.loads((R/'selection.json').read_text())[key]['model_sha256'];model=joblib.load(path);z=z.copy();z['score']=model['classifier'].predict_proba(z[model['features']])[:,1];out.append(z)
 pd.concat(out,ignore_index=True).to_parquet(R/'scored.parquet',index=False)
 print('SCORED',len(d),'rows',d.date.nunique(),'signal dates',flush=True)
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('stage',choices=['freeze','premium','native','score']);a=ap.parse_args();globals()[a.stage]()
