import pathlib,json,bisect,collections,datetime
root=pathlib.Path('/tmp/rank-audit-20260915');witness=json.loads((root/'sina-date-witness.json').read_text());source={}
for sym,w in witness.items():
 try:
  rows=json.loads((root/('sina-'+sym[:5]+'.json')).read_text())['result']['data']
  if w[17]!='2026/09/15' or float(w[12])<=0 or not rows:continue
  bars=[(r['m'][:5],float(r['p']),float(r['v'])) for r in rows];assert all(v>=0 and p>0 for m,p,v in bars);assert all(bars[i][0]<bars[i+1][0] for i in range(len(bars)-1));source[sym]=bars
 except:continue
out=[];stat=[];recovered=collections.Counter()
for f in root.glob('*.SH.json'):
 ps=json.loads(f.read_text());valid=0
 for p in ps:
  if not ('09:30'<=p['minute']<'11:30' or '13:00'<=p['minute']<'15:00'):continue
  bad=set(p.get('reasons',[]))-{'SETTLEMENT_FX_UNAVAILABLE','MIDPOINT_MISSING'}
  if bad=={'STALE_COMPONENT_MARKS'} and p.get('stale'):
   issues={x['symbol']:x for x in p.get('component_issues',[])};okay=True
   for sym in p['stale']:
    bars=source.get(sym,[]);issue=issues.get(sym,{});obs=issue.get('observed_at','')[11:16];trades=[m for m,price,v in bars if v>0 and m<=p['minute']]
    # No minute containing a trade may overlap or follow the original quote timestamp.
    if not trades or not obs or trades[-1]>=obs:okay=False;break
   if okay:
    p['mode']='historical_no_trade_verified';p['recovery_source']='sina_hk_minline.no_trade.v1';p['recovered_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();p['recovery_carry_components']=p['stale'][:];p['eligible_for_signal']=False;out.append(p);recovered[p['symbol']]+=1;bad=set()
  if not bad and all(p.get(k) is not None for k in ['etf_price','hkd_assets','cny_assets','cumulative_amount_cny']):valid+=1
 stat.append((f.stem,valid))
(root/'verified-no-trade.jsonl').write_text(''.join(json.dumps(p,ensure_ascii=False)+'\n' for p in out));print('sources',len(source),'verified',len(out),'funds',len(recovered),'potential95',sum(n>=228 for s,n in stat));print('after counts',[(s,n,recovered[s]) for s,n in stat if recovered[s]])
