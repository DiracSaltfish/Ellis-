from pathlib import Path
import urllib.request,json
R=Path(__file__).resolve().parents[1]/'raw'
u='https://push2.eastmoney.com/api/qt/stock/get?secid=1.513090&fields='+','.join('f'+str(i) for i in range(1,301))
o=json.load(urllib.request.urlopen(u));(R/'eastmoney_etf_all.json').write_text(json.dumps(o,ensure_ascii=False));print(o['data'])
