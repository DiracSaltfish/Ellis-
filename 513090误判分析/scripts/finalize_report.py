from pathlib import Path
import json,csv
R=Path(__file__).resolve().parents[1];a=json.loads((R/'artifact.json').read_text());s=json.loads((R/'minute_iopv_summary.json').read_text());blocks=a['manifest']['blocks']
for b in blocks:
 if b['id']=='summary':b['body']='''## Executive Summary\n\n**按你指定的中间价口径重算，9 月 8 日并没有出现截图所显示的持续溢价。** 用当日 PCF 的 17 只成分券数量和逐分钟行情、固定汇率 0.86482、预估现金 9,547.19 元，共生成 **332 个时间点、5,644 条逐券明细**。在与 ETF 正常交易时间匹配的 **242 个分钟样本中，溢价样本为 0**，全部为折价，范围约 **0.2717%—1.1394%**。这描述的是可核对的分钟样本，不推断每一秒。\n\n**15:00 重算 IOPV 为 1.85172772，ETF 为 1.846，对应折价 0.3093%。** 港股收盘竞价结束后，数据源 16:08 的最终价格计算得 **1.84328862**，四舍五入即 **1.8433**，与当天官方公布净值一致。更换为晚间最终现金差额后得 1.84332240，与官方精确篮子净值只差约 2 分钱/篮。\n\n**你据较低参考值推断净申购、进而押注一申一赎的汇差，这一判断基础不成立。** 上交所份额确认当日净赎回 17 篮；在你提供的“收盘附近补券”与全日同侧分摊假设下，汇差方向不利，但实际损益仍待退补款、总申赎量及实际成交分摊核实。\n\n按你的最新要求，本报告以**自行计算的中间价 IOPV**为核心，不再使用交易所发布 IOPV 作为计算输入。研究日期为 2026 年 9 月 8 日，数据抓取于北京时间 9 月 9 日凌晨。'''
 if b['id']=='intraday':b['body']='''## 逐分钟中间价 IOPV 显示正常交易时段持续折价\n\n固定公式：**IOPV(t) = [Σ(9月8日PCF数量 × 各券t分钟港元价格) × 0.86482 + 9,547.19] / 500,000**。17 只股票的时间戳集合完全一致，未补造价格、未用次日股票数量，现金项也没有偷用晚间才公布的现金差额。\n\n完整输出有 332 行；每行保留股票港元合计、人民币股票市值、预估现金、篮子金额、精确 IOPV、四位小数 IOPV 和可匹配的 ETF 分钟价格/折溢价。另有 5,644 行逐券明细，可检查每只股票每一分钟的乘加过程。\n\n**正常 A 股时段匹配到 242 个分钟点，全部折价，范围 -1.1394% 至 -0.2717%。** 下图绘制全部观测分钟，不是五分钟或小时抽样；15:00 后不把 ETF 最后价格冒充持续交易报价。\n\n17 只股票数据源均在 15:59 后跳至 16:08 收盘竞价最终点，没有独立 16:00 记录。因此 **15:59 为 1.84452586，16:00 留空，16:08 为 1.84328862**；不能把后者的标签改成 16:00。\n\n港股午间休市不插值。开盘及 13:00 端点可能带入供应商最后成交价，分钟行情也不等同逐笔报价；“所有分钟样本折价”不代表每一秒均如此。原始行情来自腾讯，收盘价格逐券经过易方达次日 PCF 交叉验证。'''
 if b['id']=='rules':b['body']='''## 中间价 IOPV、最终 NAV 与申赎结算仍需区分\n\n本报告的 **IOPV** 专指你要求的自行重建“中间价 PCF 篮子参考净值”：实时股价乘中间价，再加盘前预估现金。它不需要读取任何发布端 IOPV。\n\n**最终 NAV** 是资产减负债后除以份额；本次八日对账验证了中间价的人民币篮子计价，且 9 月 8 日仅靠盘前预估现金也能重建到官网四位小数。\n\n**最终申赎对价** 还取决于实际代买卖金额、未成交部分、现金差额及费用。招募说明书 PDF 第43—45页区分实际成交与未成交，允许轧差及与净额不同的执行数量。用户提供的客服截图确认：实际成交用港股通结算汇率，未成交及轧差用中间价；同一账户如何参与全日分摊尚未完整确认。\n\n你补充的“16:00 左右补券”保留为本次情景输入。公开规则允许 T 日内执行，不应把实际成本无条件写成最后收盘价。详细公式见后文。'''
 if b['id']=='next':b['body']='''## 用这条重建曲线替代判断依据，再核对结算\n\n1. **估值基础：** 每个交易日更新 PCF 数量和预估现金，用同一时点成分券原始价格、当日中间价重算每分钟 IOPV，并与同分钟 ETF 市价比较。记录价格时间及缺失状态。\n2. **收盘校验：** 区分 15:00、港股连续交易结束与收盘竞价结束，用后者检验官方 NAV。将预估现金到最终现金的修正单列，不能回填为盘中已知数据。\n3. **申赎测算：** 估值折溢价与实际申购成本、赎回收入分别显示。没有全日申赎方向/比例时保留双向情景，不能仅由溢价推出净申购。\n4. **对账这一组：** 申购最终支出=初始扣款−最终退款+补款+费用；赎回最终收入=到账−补款−费用。已含在结算流水的现金差额和费用不重复计算。若同日等份额且没有额外二级市场交易，一组实际盈亏就是最终收入减最终支出。'''
 if b['id']=='questions':b['body']='''## 退补款到账后，核实分摊明细即可定案\n\n可自行询证基金或券商：\n\n“请提供 513090 在 2026 年 9 月 8 日的全日总申购/总赎回篮数、各券实际代买/代卖数量、单位成交成本/收入及最终结算汇率。同一账户一申一赎是否先内部抵消，还是分别参加全日同侧平均分摊？请以我的两笔实际退补款说明现金差额与费用是否已包含。”\n\n此为本地草稿，未向任何人发送。'''
 if b['id']=='caveats':b['body']='''## 结论边界\n\n逐分钟中间价 IOPV 已完成；最终 NAV 和净赎回份额已校验。官方发布 IOPV 不参与本次重建。尚缺基金实际补券明细、最终港股通汇率、全日申赎总量和最终账户流水，所以情景亏损不作为实际亏损。\n\n数据源的 17 只港股分钟点均为同一天、同一时刻标签，未人工平移时间，未填补 16:00；仍保留供应商分钟级数据与逐笔原始行情之间的精度限制。指定 TGW Mac 工程缺本地真实账号配置，且文档验收范围未覆盖本次全篮历史行情，故使用公开行情并对收盘逐券进行交叉验证。\n\n原始证据、逐分钟总表、逐分钟成分券明细、脚本、复现 notebook 和本报告均保存在分析目录中。报告是这次研究的离线快照。'''
 # Correct misleading probability recommendation wording in old narrative if any.
 if b['id']=='nav':b['body']=b['body'].replace('912,097.119264 元','912,097.11926410 元').replace('921,661.199264 元','921,661.19926410 元')
# Full 332 timestamps, with regular-session ETF matches only.
minute=list(csv.DictReader((R/'minute_iopv_20260908.csv').open(encoding='utf-8-sig')))
chart=[]
for r in minute:
 for label,field in [('中间价 IOPV','IOPV_exact'),('ETF成交价','ETF_price')]:
  if r[field]:chart.append({'time':r['time'],'series':label,'value':float(r[field]),'date':r['date'],'stock_HKD':float(r['stock_HKD']),'stock_CNY':float(r['stock_CNY']),'estimated_cash_CNY':9547.19,'fx':.86482,'unit_shares':500000})
a['snapshot']['datasets']['intraday']=chart
key=[]
for r in s['key_minutes']:
 key.append({'time':r['time'],'IOPV':r['IOPV_4dp'],'exact':f"{float(r['IOPV_exact']):.8f}",'ETF':r['ETF_price'] or '—','premium':f"{float(r['ETF_premium'])*100:+.4f}%" if r['ETF_premium'] else '—'})
a['snapshot']['datasets']['key_minutes']=key
a['manifest']['tables'].append({'id':'key_minutes','title':'逐分钟计算的关键时点','dataset':'key_minutes','sourceId':'calc','defaultSort':{'field':'time','direction':'asc'},'columns':[{'field':f,'label':l} for f,l in [('time','北京时间'),('IOPV','IOPV 四位'),('exact','IOPV 八位'),('ETF','ETF价格'),('premium','ETF折溢价')]]})
pos=next(i for i,b in enumerate(blocks) if b['id']=='intraday-chart');blocks.insert(pos+1,{'id':'key-minute-table','type':'table','tableId':'key_minutes','layout':{'width':12}})
c=a['manifest']['charts'][0];c['title']='9月8日逐分钟中间价IOPV与ETF价格';c['subtitle']='人民币元/份；332个实际行情时间点，242个正常交易时段ETF匹配点；预估现金保持盘前值。'
a['snapshot']['accessIssues']=[{'id':'settlement-pending','message':'逐分钟重建已完成；最终退补款尚未到账，实际申赎盈亏需结算明细。'}]
a['manifest']['sources']=[x for x in a['manifest']['sources'] if x['id']!='iopv']
for x in a['manifest']['sources']:
 if x['id']=='calc':
  x['path']='scripts/compute_minute_iopv.py';x['query']['sql']=(R/'scripts/compute_minute_iopv.py').read_text();x['query']['tables_used'].append('minute_iopv_20260908.csv')
 if x['id']=='scenario':x['query']['sql']=(R/'scripts/extend_analysis.py').read_text()
a['sources']=a['manifest']['sources']
(R/'artifact.json').write_text(json.dumps(a,ensure_ascii=False,indent=2))
# Notebook starts with the requested minute calculation.
nb=json.loads((R/'reproduce.ipynb').read_text());nb['cells'].insert(1,{'cell_type':'code','metadata':{},'execution_count':None,'outputs':[],'source':['import runpy\n','runpy.run_path("scripts/compute_minute_iopv.py")\n']});(R/'reproduce.ipynb').write_text(json.dumps(nb,ensure_ascii=False,indent=2))
print('complete minute rows',len(minute),'chart observations',len(chart),'key rows',len(key))
