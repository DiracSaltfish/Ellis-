"""Independent raw SZ reference/volume checks for an excluded date and control date."""
import io,json
from zipfile import ZipFile
import pandas as pd
import pipeline as p

def main():
 out=[]
 for day in ['20260410','20260413']:
  sym='159105.SZ';root=p.RAW/day/sym
  trade=pd.read_csv(root/'逐笔成交.csv',encoding='gb18030');order=pd.read_csv(root/'逐笔委托.csv',encoding='gb18030');quote=pd.read_csv(root/'行情.csv',encoding='gb18030')
  trade=trade[trade['时间']<=144500000];order=order[order['时间']<=144500000];quote=quote[quote['时间']<=144500000]
  # Shenzhen originals are present in the order stream, keyed by 交易所委托号.
  ids=set(order['交易所委托号']);fills=trade[trade['成交代码'].astype(str).ne('C')&trade['成交价格'].gt(0)]
  bad=fills[(~fills['叫买序号'].isin(ids)&fills['叫买序号'].gt(0))|(~fills['叫卖序号'].isin(ids)&fills['叫卖序号'].gt(0))]
  missing=sorted(({int(v) for v in fills['叫买序号'] if v>0}|{int(v) for v in fills['叫卖序号'] if v>0})-ids)
  out.append(dict(day=day,symbol=sym,cutoff='14:45',order_rows=len(order),fill_rows=len(fills),trade_type_counts=trade['成交代码'].value_counts().to_dict(),original_order_ids_absent_in_raw=len(missing),missing_ids_first20=missing[:20],fills_with_absent_reference=len(bad),affected_filled_shares=int(bad['成交数量'].sum()),total_filled_shares=int(fills['成交数量'].sum()),quote_time=int(quote.iloc[-1]['时间']),last_quote_daily_volume=int(quote.iloc[-1]['当日累计成交量']),meaning='Raw SZ reference-set test, independent of C++ reconstruction; snapshot time may lag last trade, so raw totals are not forced equal.'))
 p.save('raw_source_spotcheck.json',out);print(out)
 stale=[];plan=json.loads((p.R/'plan.json').read_text())
 for e in plan['entries']:
  if e['split']!='new_test':continue
  s=pd.read_parquet(e['sources']['series']['path']);s=s[((s.minute>='09:31')&(s.minute<='11:30'))|((s.minute>='13:01')&(s.minute<='14:45'))]
  for sym,g in s.groupby('symbol'):
   g=g.dropna(subset=['etf','hkd_assets'])
   if len(g)>=214 and g.hkd_assets.nunique()==1 and g.etf.nunique()>1:stale.append(dict(date=e['date'],symbol=sym,minutes=len(g),hk_assets_unique=1,etf_unique=g.etf.nunique(),reason='constant underlying valuation with moving ETF; suspect stale HK source, not confirmed by alternate feed'))
 pd.DataFrame(stale).to_csv(p.R/'suspect_static_iopv.csv',index=False)
 with ZipFile('/Volumes/Upan/港股/港股_1分钟/2026-04/20260409_1min.zip') as z:raw=pd.read_csv(io.BytesIO(z.read('03033.HK.csv')),encoding='utf-8-sig')
 prefix=raw[pd.to_datetime(raw['时间']).dt.strftime('%H:%M')<='14:45']
 p.save('hk_03033_spotcheck.json',dict(date='2026-04-09',symbol='03033.HK',rows=len(raw),columns=list(raw.columns),close_unique=int(raw['收盘价'].nunique()),close_min=float(raw['收盘价'].min()),close_max=float(raw['收盘价'].max()),amount_total=float(raw['成交额'].sum()),first_positive_amount_time=raw.loc[raw['成交额'].gt(0),'时间'].iloc[0],through1445_rows=len(prefix),through1445_close_unique=int(prefix['收盘价'].nunique()),through1445_amount=float(prefix['成交额'].sum()),note='Original supplied HK minute ZIP; no independent market-feed confirmation.'))

if __name__=='__main__':main()
