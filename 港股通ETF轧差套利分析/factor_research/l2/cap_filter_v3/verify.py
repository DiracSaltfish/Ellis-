from pathlib import Path
import json,math,openpyxl
R=Path(__file__).resolve().parent/'outputs';d=json.loads((R/'data.json').read_text());p=R/'01a0915e-f9e1-76e3-bbaa-2329e4b4b7e8/排除满额_每日序列与命中率.xlsx';w=openpyxl.load_workbook(p,data_only=True)
lookup={(x['cohort'],x['rule']):x for x in d['summary']}
s=w['结果汇总']
for i in range(9,21):
 row=[s.cell(i,c).value for c in range(1,11)];x=lookup[tuple(row[:2])];orig=lookup[(row[0],'原规则')]
 expected=[orig['selected'],x['removed_original'] if row[1]=='剔除原选中满额样本' else 0,x['selected'],x['positive'],x['zero'],x['error_rate'],x['accuracy'],x['unknown_cap']]
 for a,b in zip(row[2:],expected):assert math.isclose(a,b,abs_tol=1e-10),(i,row,expected)
for sheet,key in [('原选标剔除满额','main'),('事后重选对照','reranked')]:
 s=w[sheet];assert s.max_row==95
 for i,r in enumerate(d[key],9):
  assert (s.cell(i,3).value or '')==r['symbol']
  assert s.cell(i,1).value.strftime('%Y-%m-%d')==r['date']
  for c,k in [(6,'net_shares'),(7,'unit'),(8,'cap_shares'),(9,'net_U'),(10,'cap_U')]:
   v=s.cell(i,c).value
   if r[k] is None:assert v in ['',None],(i,c,v)
   else:assert math.isclose(v,r[k],abs_tol=1e-6),(i,c,v,r[k])
  assert s.cell(i,12).value==r['decision']
for s in w:
 assert not [(c.coordinate,c.value) for row in s for c in row if c.data_type=='e']
print('PASS: 12 summary rows independently reconciled with Python; 174 detail rows, dates, quantities, caps and cached formulas verified.')
