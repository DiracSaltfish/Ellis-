from pathlib import Path
import json,re,html,shutil,zipfile,io,sys
import pandas as pd
R=Path(__file__).resolve().parent
W=Path('/Users/ellis/Library/Application Support/MachomeHub/data/premium/local-iopv/data/backfill/backfill-520600-20260901-20260909-central-parity')
for n in ['pcf-2026-09-02.html','fx-520600-central-parity-20260901-20260909.json','pcf-index.json','summary.json']:
 shutil.copy2(W/n,R/'source'/n)
shutil.copy2(R.parents[2]/'inputs/share_history/520600.SH.json',R/'source/share_history.json')
s=(W/'pcf-2026-09-02.html').read_text();clean=lambda x:html.unescape(re.sub('<[^>]+>',' ',x)).strip()
for a,b in re.findall(r'<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>',s,re.S):print(clean(a),clean(b))
print('FX', (W/'fx-520600-central-parity-20260901-20260909.json').read_text())
z=zipfile.ZipFile('/Volumes/Upan/港股/港股_1分钟/2026-09/20260902_1min.zip');print('HK',z.namelist()[:5]);print(z.read(z.namelist()[0])[:1200].decode('utf-8-sig'))
sys.path.insert(0,str(R.parents[1]/'native/build'));import etf_l2
raw=Path('/Volumes/EllisFiles/Stocksdata/A股逐笔/单标的研究提取/520600_20260902/20260902/520600.SZ')
for cut in ['14:30','14:45','']:
 a=etf_l2.process_files(str(raw/'逐笔成交.csv'),str(raw/'逐笔委托.csv'),str(raw/'行情.csv'),520600,20260902,500000,cutoff=cut,session_profile='sh_etf_20260706');print(cut,a['audit']);print(a['timing'])
 print(pd.DataFrame(a['orders']).sort_values('active_filled',ascending=False).head(5).to_string(index=False))
print(pd.read_csv(raw/'逐笔成交.csv',encoding='gb18030').head().to_string(index=False))
