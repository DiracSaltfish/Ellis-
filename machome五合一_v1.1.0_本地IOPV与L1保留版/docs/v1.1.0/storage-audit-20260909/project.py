import sqlite3,json,pathlib,zlib
r=pathlib.Path(__file__).parent
s=sqlite3.connect(r/'source.sqlite');keys=[(a,b,c) for a,b,c,p in s.execute('select symbol,trade_date,minute,payload from minutes') if json.loads(p)['mode']!='live'];res=json.loads((r/'results.json').read_text());n=22680;daily=121*340
for v in ['original','compact_json','round_json','lossless_zlib','chart_fixed']:
 src=sqlite3.connect(r/(v+'.sqlite'));p=r/'temp.sqlite'
 if p.exists():p.unlink()
 c=sqlite3.connect(p);src.backup(c);src.close()
 if v=='chart_fixed':c.executemany('delete from minutes where symbol=? and day=? and minute=?',[(a,int(b.replace('-','')),int(m[:2])*60+int(m[3:])) for a,b,m in keys])
 else:c.executemany('delete from minutes where symbol=? and trade_date=? and minute=?',keys)
 c.commit();c.execute('vacuum');assert c.execute('select count(*) from minutes').fetchone()[0]==n
 res[v]['live_bytes_per_row_with_index']=p.stat().st_size/n;res[v]['projected_full_day_minutes_bytes']=p.stat().st_size/n*daily;c.close();p.unlink()
(r/'results.json').write_text(json.dumps(res,indent=2));print(json.dumps(res,indent=2))
