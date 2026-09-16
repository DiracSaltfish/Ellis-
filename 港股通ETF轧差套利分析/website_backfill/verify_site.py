from pathlib import Path
import json,sqlite3,urllib.request,urllib.parse,zlib,math
R=Path(__file__).resolve().parent
BASE='http://127.0.0.1:18680'
def get(path,**q):return json.load(urllib.request.urlopen(BASE+path+'?'+urllib.parse.urlencode(q),timeout=20))
c=sqlite3.connect(R/'staged.sqlite')
# Stratify examples across both exchanges and the history endpoints.
examples=[]
for symbol in ['159570.SZ','159125.SZ','513090.SH']:
 rows=c.execute('SELECT trade_date,payload FROM minute_blocks WHERE symbol=? ORDER BY trade_date',(symbol,)).fetchall()
 if rows: examples.extend((symbol,*r) for r in [rows[0],rows[-1]])
checks=[]
for symbol,date,payload in examples:
 expected=json.loads(zlib.decompress(payload[5:]));actual=get('/api/v1/minutes',symbol=symbol,date=date)
 assert len(expected)==len(actual),(symbol,date,len(expected),len(actual))
 for a,b in zip(expected,actual):
  assert b['trade_date']==date and b['minute']==a['q']['minute']
  assert not b['eligible_for_signal'] and b['mode']=='historical_reconstruction'
  prices=[None if x is None else x/(1000 if i==0 else 10000) for i,x in enumerate(a['p'])]
  for key,value in zip(['etf_price','midpoint_iopv','settlement_buy_iopv','settlement_sell_iopv'],prices):assert b[key]==value,(key,b[key],value)
  for key,nav in zip(['midpoint_premium_pct','settlement_buy_premium_pct','settlement_sell_premium_pct'],prices[1:]):
   if prices[0] is None:assert b[key] is None
   else:assert math.isclose(b[key],(prices[0]/nav-1)*100,abs_tol=1e-10)
 dates=get('/api/v1/dates',symbol=symbol);assert date in dates
 shares=get('/api/v1/daily-shares',symbol=symbol,date=date)
 source=c.execute('SELECT shares_10k,share_change_10k FROM daily_shares WHERE symbol=? AND trade_date=?',(symbol,date)).fetchone()
 assert (shares['shares_10k'],shares['share_change_10k'])==source
 checks.append(dict(symbol=symbol,date=date,minutes=len(actual),share_change_10k=shares['share_change_10k']))
missing=get('/api/v1/daily-shares',symbol='159125.SZ',date='2026-09-12');assert missing['share_change_10k'] is None
current=get('/api/v1/daily-shares',symbol='159125.SZ',date='2026-09-10')
source=c.execute("SELECT share_change_10k FROM daily_shares WHERE symbol='159125.SZ' AND trade_date='2026-09-10'").fetchone()[0];assert current['share_change_10k']==source
(R/'verification.json').write_text(json.dumps(dict(checks=checks,screenshot_example=current,missing_is_null=True),ensure_ascii=False,indent=2));print(checks)
