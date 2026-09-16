"""Read-only real-data differential validation. Independent pandas group-by oracle.
Run on machome. Outputs stay in this native/validation directory; no archive mutations.
"""
from pathlib import Path
import argparse,json,sys,time,hashlib,gzip
import numpy as np,pandas as pd
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'build'))
import etf_l2

def reference(path,cutoff=None):
 t=pd.read_csv(path/'逐笔成交.csv',encoding='gb18030',keep_default_na=False,dtype=str)
 o=pd.read_csv(path/'逐笔委托.csv',encoding='gb18030',keep_default_na=False,dtype=str)
 q=pd.read_csv(path/'行情.csv',encoding='gb18030',keep_default_na=False,dtype=str)
 for d,cols in [(t,['自然日','时间','成交编号','成交数量','成交价格','叫买序号','叫卖序号']),(o,['自然日','时间','交易所委托号','委托数量']),(q,['自然日','时间','当日累计成交量'])]:
  for c in cols:d[c]=pd.to_numeric(d[c],errors='raise').astype('int64')
 t=t[t['自然日'].ne(0)].copy();o=o[o['自然日'].ne(0)].copy();q=q[q['自然日'].ne(0)]
 if cutoff:
  bound=int(cutoff.replace(':',''))*100000;t=t[t['时间']<bound];o=o[o['时间']<bound];q=q[q['时间']<bound]
 sh=path.name.endswith('.SH')
 if sh:
  ca=o[o['委托类型'].eq('D')].copy();o=o[o['委托类型'].eq('A')].copy()
 else:
  ca=t[t['成交代码'].eq('C')].copy();t=t[t['成交代码'].eq('0')].copy()
 # Oracle uses full source columns and independent groupby; identical trade IDs must be exact duplicates.
 t=t.drop_duplicates('成交编号');o=o.drop_duplicates('交易所委托号')
 ct=((t['时间']>=93000000)&(t['时间']<=113000000))|((t['时间']>=130000000)&(t['时间']<=(150000000 if sh else 145659999)))
 cont=t[ct].copy();rows=[]
 for flag,idcol in [('B','叫买序号'),('S','叫卖序号')]:
  own=o[o['委托代码'].eq(flag)].set_index('交易所委托号');fills=t.groupby(idcol)['成交数量'].sum()
  cancels=ca[ca['委托代码'].eq(flag)].groupby('交易所委托号')['委托数量'].sum() if sh else ca[ca[idcol]>0].groupby(idcol)['成交数量'].sum()
  ids=sorted(set(own.index)|set(fills.index)|set(cancels.index));g=pd.DataFrame(index=ids)
  g['filled']=fills;g['cancelled']=cancels;g['original_quantity']=own['委托数量'];g['known']=g.original_quantity.notna()
  active=cont[cont['BS标志'].eq(flag)].copy();g['active_filled']=active.groupby(idcol)['成交数量'].sum();g['passive_filled']=cont[cont['BS标志'].ne(flag)].groupby(idcol)['成交数量'].sum()
  if sh:
   active['add_time']=active[idcol].map(own['时间']);pre=active[(active['时间']<=active.add_time)&(active.add_time>=93000000)]
   initial=pre.groupby(idcol)['成交数量'].sum();g['initial_aggressive_filled']=initial.reindex(g.index).fillna(0);g['original_quantity']=g.original_quantity+g.initial_aggressive_filled
  else:g['initial_aggressive_filled']=0
  g=g.fillna(0);g['remaining']=np.where(g.known,g.original_quantity-g.filled-g.cancelled,0)
  g['order_id']=g.index;g['side']=1 if flag=='B' else 2;rows.append(g)
 ref=pd.concat(rows).sort_index()
 return ref,dict(total_trade_quantity=int(t['成交数量'].sum()),total_notional_x10000=int((t['成交数量']*t['成交价格']).sum()),quote_volume=int(q['当日累计成交量'].iloc[-1]),input_trades=len(t),input_orders=len(o))

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cutoff',choices=['14:30','14:45']);ap.add_argument('--raw',default='/Volumes/EllisFiles/Stocksdata/A股逐笔/港股通ETF保留');ap.add_argument('--dates',nargs='+',default=['20260105','20260106','20260107']);ap.add_argument('--symbols',nargs='+',default=['513330.SH','513090.SH','159636.SZ']);ap.add_argument('--output',default=str(R/'validation'/'real_differential.json'));args=ap.parse_args()
 records=[];F=R.parent.parent
 for date in args.dates:
  baskets=json.loads(gzip.open(F/'inputs/baskets'/f'{date}.json.gz','rt').read())
  for symbol in args.symbols:
   path=Path(args.raw)/date/symbol;unit=int(baskets[symbol]['unit']);record=dict(date=date,symbol=symbol,unit=unit)
   begin=time.perf_counter()
   result=etf_l2.process_files(str(path/'逐笔成交.csv'),str(path/'逐笔委托.csv'),str(path/'行情.csv'),int(symbol[:6]),int(date),unit,cutoff=args.cutoff or '')
   record['native_wall_seconds']=time.perf_counter()-begin;record['native_timing']=result['timing'];record['audit']=result['audit']
   begin=time.perf_counter();ref,totals=reference(path,args.cutoff);record['reference_wall_seconds']=time.perf_counter()-begin
   native=pd.DataFrame(result['orders']).set_index('order_id').sort_index()
   assert native.index.tolist()==ref.index.tolist(),(date,symbol,'order key mismatch')
   cols=['side','filled','cancelled','original_quantity','initial_aggressive_filled','active_filled','passive_filled','remaining']
   for col in cols:assert np.array_equal(native[col].to_numpy(),ref[col].to_numpy()),(date,symbol,col,'order mismatch')
   for key in ['total_trade_quantity','total_notional_x10000']:assert result['audit'][key]==totals[key],(date,symbol,key)
   if not args.cutoff:assert result['audit']['quote_reconciled'] and totals['quote_volume']==totals['total_trade_quantity']
   elif result['audit']['quote_reconciled']:assert totals['quote_volume']==totals['total_trade_quantity']
   record['cutoff']=args.cutoff or 'full_day';record['quote_reconciled']=result['audit']['quote_reconciled']
   assert result['audit']['order_features_valid'] and result['audit']['trade_features_valid'],(date,symbol,result['audit'])
   assert native.filled.sum()==totals['total_trade_quantity']*2
   minutes=result['minutes'];assert sum(int(minutes[k].sum()) for k in ['active_buy','active_sell','unknown_direction','auction'])==totals['total_trade_quantity']
   assert sum(int(minutes[k].sum()) for k in ['buy_notional_x10000','sell_notional_x10000','unknown_notional_x10000','auction_notional_x10000'])==totals['total_notional_x10000']
   record.update(totals);record['order_ids_checked']=len(ref);record['fields_per_order_checked']=len(cols);record['passed']=True
   record['source_sha256']={name:hashlib.sha256((path/name).read_bytes()).hexdigest() for name in ['逐笔成交.csv','逐笔委托.csv','行情.csv']}
   records.append(record);out=Path(args.output);out.parent.mkdir(exist_ok=True,parents=True);out.write_text(json.dumps(records,ensure_ascii=False,indent=2));print(date,symbol,'PASS',len(ref),'orders','native',round(record['native_wall_seconds'],3),'reference',round(record['reference_wall_seconds'],3),flush=True)
if __name__=='__main__':main()
