from pathlib import Path
import csv,json,sqlite3
R=Path(__file__).resolve().parents[1];conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
conn.execute('CREATE TABLE minute_quotes(date TEXT,time TEXT,code TEXT,quantity INTEGER,price_HKD REAL)')
rows=list(csv.DictReader((R/'minute_constituents_20260908.csv').open(encoding='utf-8-sig')))
conn.executemany('INSERT INTO minute_quotes VALUES (?,?,?,?,?)',[(r['date'],r['time'],r['code'],int(r['quantity']),float(r['price_HKD'])) for r in rows])
conn.execute('CREATE TABLE etf_quotes(date TEXT,time TEXT,price REAL)')
em=json.loads((R/'raw/tencent_sh513090_minute.json').read_text())['data']['sh513090']['data']
conn.executemany('INSERT INTO etf_quotes VALUES (?,?,?)',[('2026-09-08',s.split()[0][:2]+':'+s.split()[0][2:],float(s.split()[1])) for s in em['data']])
query=(R/'scripts/chart_source.sql').read_text();chart=[dict(x) for x in conn.execute(query)]
exact={r['time']:float(r['IOPV_exact']) for r in csv.DictReader((R/'minute_iopv_20260908.csv').open(encoding='utf-8-sig'))}
err=max(abs(r['value']-exact[r['time']]) for r in chart if r['series']=='中间价 IOPV');assert err<1e-12
conn.commit();out=sqlite3.connect(R/'analysis.sqlite');conn.backup(out);out.close()
a=json.loads((R/'artifact.json').read_text());source={'id':'minute_sql','label':'17只成分券逐分钟原始价格的独立SQL复算','path':'scripts/chart_source.sql','query':{'engine':'SQLite','language':'sql','sql':query,'description':'按每分钟17只PCF数量×原始港股价格聚合，中间价0.86482，预估现金9547.19，篮子50万份；仅正常A股时段对齐ETF。结果与Decimal重建交叉验证。','tables_used':['minute_quotes','etf_quotes'],'filters':['2026-09-08','17只PCF允许替代券','不填补缺失时间，不将15:00后ETF最后价延用'],'metric_definitions':['IOPV=(ΣPCF数量×分钟港元价格×0.86482+9547.19)/500000']}}
a['manifest']['sources'].append(source);a['sources']=a['manifest']['sources'];a['manifest']['charts'][0]['source']=source;a['manifest']['charts'][0]['sourceId']='minute_sql';a['snapshot']['datasets']['intraday']=chart
for src in a['manifest']['sources']:
 if src['id'] in ['calc','scenario']:src['query'].pop('sql',None)
for table in a['manifest']['tables']:table['source'].get('query',{}).pop('sql',None) if 'source' in table else None
(R/'artifact.json').write_text(json.dumps(a,ensure_ascii=False,indent=2));print('SQL/Decimal max difference',err,'chart rows',len(chart))
