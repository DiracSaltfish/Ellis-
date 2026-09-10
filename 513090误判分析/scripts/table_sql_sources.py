from pathlib import Path
import json,sqlite3,csv
R=Path(__file__).resolve().parents[1];raw=R/'raw';a=json.loads((R/'artifact.json').read_text());c=sqlite3.connect(R/'analysis.sqlite');c.row_factory=sqlite3.Row
# Raw independent daily pricing and PCF inputs, not table output re-selection.
c.executescript('DROP TABLE IF EXISTS daily_inputs; CREATE TABLE daily_inputs(date TEXT,code TEXT,quantity INTEGER,close_HKD REAL,fx REAL,final_cash REAL,official_basket_NAV REAL,net_baskets REAL); DROP TABLE IF EXISTS next_pcf; CREATE TABLE next_pcf(code TEXT,quantity INTEGER,amount_CNY REAL);')
dates=['2026-08-28','2026-08-31','2026-09-01','2026-09-02','2026-09-03','2026-09-04','2026-09-07','2026-09-08','2026-09-09'];fxs=[.86498,.86510,.86498,.86497,.86476,.86458,.86465,.86482];previous=None
for i,d in enumerate(dates[:-1]):
 q=json.load(open(raw/f'pcf_{d}_stocklist.json'))['data'];b=json.load(open(raw/f'pcf_{dates[i+1]}_baseinfo.json'))['data']['map'];share=float(next(x['TOT_VOL'] for x in json.load(open(raw/f'shares_{d}.json'))['result'] if x['SEC_CODE']=='513090'));net=(share-previous)/50 if previous is not None else None;previous=share
 for r in q:
  code=r['C_STOCKCODE'];p=float(next(x[2] for x in json.load(open(raw/f'tencent_hk{code}_day.json'))['data']['hk'+code]['day'] if x[0]==d));c.execute('INSERT INTO daily_inputs VALUES (?,?,?,?,?,?,?,?)',(d,code,r['L_NUMBER'],p,fxs[i],b['CASHCOMPONENT'],b['NAVPERCU'],net))
c.executemany('INSERT INTO next_pcf VALUES (?,?,?)',[(x['C_STOCKCODE'],x['L_NUMBER'],x['F_TDJE']) for x in json.load(open(raw/'pcf_2026-09-09_stocklist.json'))['data']]);c.commit()
queries={
'daily':'''SELECT date, printf('%.5f',fx) AS fx, printf('%.8f',(sum(quantity*close_HKD)*fx+final_cash)/500000.0) AS estimate, printf('%.8f',official_basket_NAV/500000.0) AS official, printf('%+.4f',sum(quantity*close_HKD)*fx+final_cash-official_basket_NAV) AS error, CASE WHEN net_baskets IS NULL THEN '—' ELSE printf('%+.0f',net_baskets) END AS net FROM daily_inputs GROUP BY date ORDER BY date;''',
'scenario':'''WITH counts(C) AS (VALUES(1),(10),(50),(100),(200),(500)), basket AS (SELECT sum(quantity*price_HKD) AS stock_HKD FROM minute_quotes WHERE time='16:08') SELECT C,C+17 AS R, printf('%.2f%%',1700.0/(C+17)) AS fraction, printf('%.2f',stock_HKD*(0.855441-0.86482)*17.0/(C+17)) AS pnl FROM counts CROSS JOIN basket ORDER BY C;''',
'stocks':'''SELECT m.code,m.quantity AS qty,printf('%.3f',m.price_HKD) AS p15,printf('%.3f',z.price_HKD) AS close,printf('%.2f',m.quantity*z.price_HKD*0.86482) AS value, printf('%+.5f',n.amount_CNY-n.quantity*z.price_HKD*0.86482) AS next_error FROM minute_quotes m JOIN minute_quotes z ON m.code=z.code AND m.date=z.date AND z.time='16:08' JOIN next_pcf n ON n.code=m.code WHERE m.time='15:00' ORDER BY m.code;'''
}
name={r['C_STOCKCODE']:r['C_STOCKSHORT'] for r in json.load(open(raw/'pcf_2026-09-08_stocklist.json'))['data']}
for id,q in queries.items():
 rows=[dict(r) for r in c.execute(q)]
 if id=='stocks':
  for row in rows:row['name']=name[row['code']]
 a['snapshot']['datasets'][id]=rows;(R/f'scripts/{id}_source.sql').write_text(q+'\n')
 src={'id':id+'_sql','label':'原始PCF及行情SQL交叉复算' if id!='scenario' else '条件申赎分摊测算（非实际盈亏）','path':f'scripts/{id}_source.sql','query':{'engine':'SQLite','language':'sql','sql':q,'description':'对归档原始数量/行情/现金/汇率输入重新聚合。情景汇率为用户截图预测值，不是最终结算汇率。证券简称从原始PCF补入。','tables_used':['daily_inputs'] if id=='daily' else ['minute_quotes','next_pcf'] if id=='stocks' else ['minute_quotes'],'filters':['513090；数据日期2026-08-28至2026-09-08'],'metric_definitions':['NAV/IOPV=(Σ数量×港元价格×当日中间价+现金部分)/500000','条件一申一赎收益=17/(C+17)×港元篮子×(预测卖券汇率−中间价)']}}
 a['manifest']['sources'].append(src)
 tab=next(t for t in a['manifest']['tables'] if t['id']==id);tab['source']=src;tab['sourceId']=src['id']
key=next(t for t in a['manifest']['tables'] if t['id']=='key_minutes');src=next(x for x in a['manifest']['sources'] if x['id']=='minute_sql');key['source']=src;key['sourceId']=src['id']
a['sources']=a['manifest']['sources'];(R/'artifact.json').write_text(json.dumps(a,ensure_ascii=False,indent=2));c.close()
