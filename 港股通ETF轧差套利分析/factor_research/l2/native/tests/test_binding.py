"""Portable CSV/CLI/pybind regression tests. Synthetic fixtures never touch market archives."""
import csv,gc,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import etf_l2
ROOT=Path(__file__).resolve().parents[1]
OH=['万得代码','交易所代码','自然日','时间','委托编号','交易所委托号','委托类型','委托代码','委托价格','委托数量']
TH=['万得代码','交易所代码','自然日','时间','成交编号','成交代码','委托代码','BS标志','成交价格','成交数量','叫卖序号','叫买序号']
QH=['field'+str(i) for i in range(66)]
for i,name in {0:'万得代码',1:'交易所代码',2:'自然日',3:'时间',11:'当日累计成交量',17:'申卖价1',37:'申买价1',27:'申卖量1',47:'申买量1'}.items():QH[i]=name
class BindingTests(unittest.TestCase):
 def setUp(self):
  base=ROOT/'build'/'testdata';base.mkdir(parents=True,exist_ok=True)
  self.tmp=tempfile.TemporaryDirectory(dir=base);self.dir=Path(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def fixture(self,sh=False,encoding='gb18030',qty=100):
  code=513330 if sh else 159636;sym=str(code)+'.SZ';date=20260105
  orders=[[sym,code,date,92900000,0,2,'A' if sh else '0','S',10000,qty]]
  if not sh:orders.append([sym,code,date,92900000,0,1,'0','B',10000,qty])
  trades=[[sym,code,date,93000000,10,'' if sh else '0',0,'B',10000,qty,2,1]]
  quote=[0]*66
  for i,v in {0:sym,1:code,2:date,3:150000000,11:qty,17:10010,37:10000,27:100,47:100}.items():quote[i]=v
  for name,head,rows in [('orders',OH,orders),('trades',TH,trades),('quotes',QH,[quote])]:
   self.write(name,head,rows,encoding)
  return dict(trade_file=str(self.dir/'trades.csv'),order_file=str(self.dir/'orders.csv'),quote_file=str(self.dir/'quotes.csv'),code=code,date=date,unit=100)
 def write(self,name,head,rows,encoding='gb18030'):
  with (self.dir/(name+'.csv')).open('w',encoding=encoding,newline='') as h:
   w=csv.writer(h);w.writerow(head+['']);w.writerows([r+[''] for r in rows])
 def test_explicit_september_sh_profile(self):
  kw=self.fixture(sh=True)
  for name in ['orders','trades','quotes']:
   p=self.dir/(name+'.csv');p.write_text(p.read_text(encoding='gb18030').replace('20260105','20260902'),encoding='gb18030')
  kw['date']=20260902
  with self.assertRaises(ValueError):etf_l2.process_files(**kw)
  r=etf_l2.process_files(**kw,session_profile='sh_etf_20260706');self.assertTrue(r['audit']['order_features_valid']);self.assertEqual(r['audit']['total_trade_quantity'],100)
 def test_gb18030_sz(self):
  r=etf_l2.process_files(**self.fixture());self.assertEqual(r['audit']['total_trade_quantity'],100);self.assertTrue(r['audit']['quote_reconciled']);self.assertTrue(r['audit']['order_features_valid'])
 def test_utf8_bom(self):
  self.assertTrue(etf_l2.process_files(**self.fixture(encoding='utf-8-sig'))['audit']['quote_reconciled'])
 def test_sh_suffix_and_unknown_original(self):
  r=etf_l2.process_files(**self.fixture(sh=True));self.assertGreater(r['suffix_corrected_rows'],0);self.assertEqual(r['orders']['original_quantity'][0],0);self.assertEqual(r['orders']['quantity_evidence'][0],3)
 def test_sh_nul_empty_trade_code_equivalent(self):
  kw=self.fixture(sh=True);old=etf_l2.process_files(**kw);p=self.dir/'trades.csv';s=p.read_text(encoding='gb18030').replace(',10,,0,B,',',10,\0,\0,B,');p.write_text(s,encoding='gb18030')
  new=etf_l2.process_files(**kw);self.assertEqual(old['audit'],new['audit']);np.testing.assert_array_equal(old['orders']['filled'],new['orders']['filled'])
 def test_sz_nul_trade_code_rejected(self):
  kw=self.fixture();p=self.dir/'trades.csv';s=p.read_text(encoding='gb18030').replace(',10,0,0,B,',',10,\0,0,B,');p.write_text(s,encoding='gb18030')
  with self.assertRaises(RuntimeError):etf_l2.process_files(**kw)
 def test_zero_copy_owner_lifetime_and_readonly(self):
  r=etf_l2.process_files(**self.fixture());a=r['orders']['filled'];self.assertFalse(a.flags.owndata);self.assertFalse(a.flags.writeable);del r;gc.collect();self.assertEqual(a.sum(),200)
  with self.assertRaises(ValueError):a[0]=0
 def test_parallel_calls_independent(self):
  kw=self.fixture(sh=True)
  with ThreadPoolExecutor(4) as pool:r=list(pool.map(lambda _:etf_l2.process_files(**kw)['audit']['total_trade_quantity'],range(16)))
  self.assertEqual(r,[100]*16)
 def test_wrong_day_rejected(self):
  kw=self.fixture();kw['date']=20260106
  with self.assertRaises(RuntimeError):etf_l2.process_files(**kw)
 def test_wrong_security_rejected(self):
  kw=self.fixture();kw['code']=159920
  with self.assertRaises(RuntimeError):etf_l2.process_files(**kw)
 def test_bad_header_rejected(self):
  kw=self.fixture();p=self.dir/'trades.csv';s=p.read_text(encoding='gb18030').replace('成交数量','数量错误');p.write_text(s,encoding='gb18030')
  with self.assertRaises(ValueError):etf_l2.process_files(**kw)
 def test_column_shift_rejected(self):
  kw=self.fixture();p=self.dir/'trades.csv';lines=p.read_text(encoding='gb18030').splitlines();lines[1]=lines[1]+',5';p.write_text('\n'.join(lines),encoding='gb18030')
  with self.assertRaises(RuntimeError):etf_l2.process_files(**kw)
 def test_cancel_not_trade(self):
  kw=self.fixture(qty=100);sym='159636.SZ';r=[sym,159636,20260105,93100000,11,'C',0,' ',0,50,0,1]
  with (self.dir/'trades.csv').open('a',encoding='gb18030',newline='') as h:csv.writer(h).writerow(r+[''])
  out=etf_l2.process_files(**kw);self.assertEqual(out['audit']['total_trade_quantity'],100);self.assertEqual(out['audit']['overfilled_orders'],1);self.assertEqual(out['orders']['cancelled'][0],50)
 def test_ambiguous_cancel_reference_rejected(self):
  kw=self.fixture();p=self.dir/'trades.csv';s=p.read_text(encoding='gb18030').replace(',0,0,B,',',C,0,B,');p.write_text(s,encoding='gb18030')
  with self.assertRaises(RuntimeError):etf_l2.process_files(**kw)
 def test_placeholder_rows_ignored(self):
  kw=self.fixture();row=['159636.SZ',159636,0,92600000,0,' ',' ','\0',0,0,0,0]
  with (self.dir/'trades.csv').open('a',encoding='gb18030',newline='') as h:csv.writer(h).writerow(row+[''])
  r=etf_l2.process_files(**kw);self.assertEqual(r['placeholder_rows'],1);self.assertEqual(r['audit']['total_trade_quantity'],100)
 def test_unknown_code_rejected(self):
  kw=self.fixture();p=self.dir/'trades.csv';s=p.read_text(encoding='gb18030').replace(',0,0,B,',',X,0,B,');p.write_text(s,encoding='gb18030')
  with self.assertRaises(RuntimeError):etf_l2.process_files(**kw)
 def test_checked_integer_parser(self):
  kw=self.fixture();p=self.dir/'trades.csv';s=p.read_text(encoding='gb18030').replace(',10000,100,',',999999999999999999999,100,');p.write_text(s,encoding='gb18030')
  with self.assertRaises(RuntimeError):etf_l2.process_files(**kw)
 def test_sz_zero_price_U_order_preserves_quantity(self):
  kw=self.fixture();p=self.dir/"orders.csv";s=p.read_text(encoding="gb18030").replace(",0,B,10000,",",U,B,0,");p.write_text(s,encoding="gb18030")
  r=etf_l2.process_files(**kw);self.assertTrue(r["audit"]["order_features_valid"]);self.assertEqual(r["orders"]["filled"].sum(),200)
 def test_invalid_timestamp(self):
  kw=self.fixture();p=self.dir/'trades.csv';s=p.read_text(encoding='gb18030').replace('93000000','96000000');p.write_text(s,encoding='gb18030')
  with self.assertRaises(RuntimeError):etf_l2.process_files(**kw)
 def test_parser_memory_preflight(self):
  kw=self.fixture();kw["memory_budget_bytes"]=1
  with self.assertRaises(RuntimeError):etf_l2.process_files(**kw)
 def test_file_cutoff_ignores_future_order_trade_and_quote(self):
  kw=self.fixture(sh=True);kw['cutoff']='14:45'
  old=etf_l2.process_files(**kw)
  with (self.dir/'orders.csv').open('a',encoding='gb18030',newline='') as h:csv.writer(h).writerow(['513330.SZ',513330,20260105,144600000,0,1,'A','B',10000,200,''])
  with (self.dir/'trades.csv').open('a',encoding='gb18030',newline='') as h:csv.writer(h).writerow(['513330.SZ',513330,20260105,144600000,11,'',0,'B',10000,1000,4,3,''])
  new=etf_l2.process_files(**kw);self.assertEqual(new['audit']['total_trade_quantity'],old['audit']['total_trade_quantity']);self.assertEqual(new['orders']['original_quantity'][0],0);self.assertEqual(new['audit']['cutoff_time_us'],53100000000)
 def test_bad_cutoff_rejected(self):
  kw=self.fixture();kw['cutoff']='14:90'
  with self.assertRaises(ValueError):etf_l2.process_files(**kw)
 def test_input_arrays_cover_full_market_amount(self):
  r=etf_l2.process_files(**self.fixture());m=r['minutes'];total=sum(m[k].sum() for k in ['buy_notional_x10000','sell_notional_x10000','unknown_notional_x10000','auction_notional_x10000']);self.assertEqual(total,r['audit']['total_notional_x10000'])
 def test_cli_and_no_overwrite(self):
  kw=self.fixture();cli=Path(etf_l2.__file__).parent/'etf_l2_cli';out=self.dir/'output';cmd=[str(cli),'20260105','159636','100',kw['trade_file'],kw['order_file'],kw['quote_file'],str(out)]
  r=subprocess.run(cmd,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr);old=(out/'summary.json').read_bytes();r=subprocess.run(cmd,capture_output=True,text=True);self.assertNotEqual(r.returncode,0);self.assertEqual(old,(out/'summary.json').read_bytes())
if __name__=='__main__':unittest.main()
