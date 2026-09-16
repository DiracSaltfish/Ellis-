"""Independent pandas order/quantity oracle on representative added SH/SZ samples."""
from pathlib import Path
import json,sys
import pandas as pd,numpy as np
R=Path(__file__).resolve().parent;L=R.parent
sys.path[:0]=[str(L/'native/build'),str(L/'native/tests')]
import etf_l2
from validate_real import reference
es={e['date']:e for e in json.loads((R/'plan.json').read_text())['entries']};d=pd.read_parquet(R/'scored.parquet');d=d[d.fx_basis.eq('lag')]
out=[]
for year in ['2025','2026']:
 for market in ['SH','SZ']:
  pool=d[d.date.str.startswith(year)&d.symbol.str.endswith(market)&d.quote_reconciled.eq(True)]
  if year=='2026':pool=pool[pool.date.ge('2026-07-06')]
  x=pool.sort_values(['date','symbol']).iloc[0];e=es[x.date];m=json.loads(Path(e['sources']['manifest']['path']).read_text());files={Path(f['path']).name:f['path'] for f in m['files'] if f['symbol']==x.symbol}
  n=etf_l2.process_files(files['逐笔成交.csv'],files['逐笔委托.csv'],files['行情.csv'],int(x.symbol[:6]),int(e['day']),int(x.unit),cutoff='14:45',session_profile=x.session_profile)
  ref,totals=reference(Path(files['逐笔成交.csv']).parent,'14:45');native=pd.DataFrame(n['orders']).set_index('order_id').sort_index();assert native.index.tolist()==ref.index.tolist()
  cols=['side','filled','cancelled','original_quantity','initial_aggressive_filled','active_filled','passive_filled','remaining']
  for col in cols:assert np.array_equal(native[col].to_numpy(),ref[col].to_numpy()),(x.date,x.symbol,col)
  for key in ['total_trade_quantity','total_notional_x10000']:assert n['audit'][key]==totals[key]
  assert native.filled.sum()==2*totals['total_trade_quantity']
  assert sum(int(n['minutes'][k].sum()) for k in ['active_buy','active_sell','unknown_direction','auction'])==totals['total_trade_quantity']
  out.append(dict(date=x.date,symbol=x.symbol,profile=x.session_profile,orders=len(ref),fields_checked=cols,totals=totals,passed=True))
(R/'outputs/independent_validation.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out,ensure_ascii=False,indent=2))
