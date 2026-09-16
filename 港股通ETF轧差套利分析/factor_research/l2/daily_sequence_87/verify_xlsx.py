from pathlib import Path
import json,math,openpyxl
R=Path(__file__).resolve().parent/'outputs/01a0915e-f9e1-76e3-bbaa-2329e4b4b7e8'
d=json.loads((R/'data.json').read_text()); path=R/'每日选标与实际净申赎_87日.xlsx'
w=openpyxl.load_workbook(path,data_only=True); f=openpyxl.load_workbook(path,data_only=False)
checks=0
for name,key in [('每日序列','main'),('规则对照','comparison')]:
 s=w[name]
 assert s.max_row==len(d[key])+8
 for i,r in enumerate(d[key],9):
  assert s.cell(i,1).value.strftime('%Y-%m-%d')==r['date']
  assert (s.cell(i,3).value or '')==r['symbol']
  assert s.cell(i,10).value==r['result']
  for c,k in [(6,'net_shares'),(7,'net_wan'),(8,'unit'),(9,'net_U')]:
   actual=s.cell(i,c).value
   if r[k] is None:assert actual in [None,''],(name,i,c,actual)
   else:assert math.isclose(actual,r[k],abs_tol=1e-6)
  assert f[name].cell(i,7).data_type=='f' and f[name].cell(i,9).data_type=='f'
  checks+=1
 for row in s:
  assert all(c.data_type!='e' for c in row)
print(f'PASS: {checks} exported rows reconciled; cached formulas, blank days, dates and quantities verified.')
