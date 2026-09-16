from pathlib import Path
import csv,json,ast
R=Path(__file__).resolve().parent;D=R/'results/spike_cases'
rows=list(csv.DictReader((D/'summary.csv').open()));cases=list(csv.DictReader((D/'case_index.csv').open()))
def pct(x):return f'{float(x)*100:.1f}%'
labels={'all':'全部样本','all_mean_le30':'全天均值≤30 bp的全部样本','spike_20':'20 bp拉升回落','spike_30':'30 bp拉升回落（主定义）','spike_50':'50 bp拉升回落','spike_30_etf_down10':'30 bp形态且ETF价格回落≥10 bp','spike_30_mean_le30':'30 bp形态且全天均值≤30 bp'}
s='''# 典型日内路径：拉升溢价后快速回落

**结论：形态与净申购存在关联，但不能单独解释为“大概率被申购砸下来”。** 全年30基点形态中63.9%为净申购；最后一季降至40.6%，另有26.6%净赎回、32.8%份额不变。即使要求ETF价格自身也下降，最后一季净申购比例仍只有38.2%。因此需要保留它作为路径特征，而不能凭形态确认净申购，更不能确认因果。

## 时段核对

已独立从保存的分钟序列重新计算全部基金日的平均结算溢价：只纳入原始行情时间09:30—11:30、13:00—15:00的有效共同分钟。15:00之后和午休不纳入。内部时间为原始时间加一分钟，因此15:01代表原始15:00收盘数据；本页图表全部还原成原始行情时间。

'''
a=json.loads((D/'session_audit.json').read_text());s+=f"核对{a['fund_days']:,}个基金日、{a['minutes']:,}分钟，原始15:00之后被纳入的分钟数为{a['post1500_minutes']}，重算均值最大差异{a['max_mean_difference_bp']:.3g}基点。港股15:00—16:00仅画在灰区供参照，ETF价留空。\n\n"
s+='''## 形态如何定义

逐分钟遍历所有样本，以结算溢价的三分钟滚动中位数识别形态，避免一笔异常价造成假信号。对每个候选时刻：

1. 与之前第10至第6分钟的五个平滑溢价值中位数相比，溢价上升至少30基点，且当前平滑溢价本身至少30基点。
2. ETF当前价也高于该早期窗口的价格中位数，最近三分钟至少两分钟有成交额。
3. 随后3—20分钟内，平滑溢价下降至少30基点，并回吐至少60%的前述升幅。
4. 不跨午休或缺失区间；同一基金日无论发生几次，只统计一次。日内有多个候选时，保存升幅最大者，选择过程不使用净份额结果。

另报20/50基点敏感性。更严格的“ETF也跌”要求识别起止时刻ETF价格下降至少10基点（0.1%），用来区分ETF下跌和篮子上涨造成的溢价收敛。这些定义是本次新增探索，不是此前已冻结模型的前瞻检验。

## 全样本与最后一季对照

“大量净申购”沿用净增≥10篮子且≥昨日份额0.5%；“净申购”指当日可见净量为正。下面每个分母都是去重后的基金日，不能当成相互独立的交易次数。

'''
for period,title in [('all','完整一年'),('test','2026年4—6月')]:
 s+=f'### {title}\n\n| 条件 | 基金日 | 净申购 | 大量净申购 | 净赎回 | 净篮子中位数 |\n|---|---:|---:|---:|---:|---:|\n'
 for r in rows:
  if r['period']==period and r['condition'] in labels:
   s+=f"| {labels[r['condition']]} | {r['n']} | {pct(r['positive_rate'])} | {pct(r['large_rate'])} | {pct(r['negative_rate'])} | {float(r['median_net_baskets']):.1f} |\n"
 s+='\n'
r=next(x for x in rows if x['period']=='test' and x['condition']=='spike_30');interval=ast.literal_eval(r['positive_ci']);s+=f"主定义在最后一季覆盖{r['funds']}只基金、{r['days']}个日期。按日期聚类自助抽样的净申购比例95%区间为{pct(interval[0])}—{pct(interval[1])}。\n\n"
s+='''**全天均值的确会遗漏部分局部机会。** 在最后一季，全天平均结算溢价≤30基点的全部样本净申购比例为7.6%；其中出现本次30基点形态的44个样本，净申购比例为34.1%，大量净申购比例为18.2%。这说明形态在低均值样本中有筛选信息，但样本小、未经按规模与波动率匹配，不能认定独立的因果或稳定增量收益。

## 案例图

每张图从上到下依次为三条价格/估值序列、结算溢价、ETF分钟成交额。橙区为识别窗口，灰区为ETF闭市后的港股时段。图中日净份额是盘后公布的全日结果，不是橙区内的成交或申购确认量。

选图先按结果分组以同时展示典型与反例，各组选择升幅中位数附近的样本，尽量不同基金；未专挑净增最大的案例。持续溢价对照则选择此前简单条件中平均溢价居中的无尖峰样本。这些选图不用于计算前面的命中率。

'''
for i,r in enumerate(cases,1):
 s+=f"### {i}. {r['category']}：{r['symbol']}，{r['date']}\n\n当日净增{float(r['net_baskets']):+.1f}篮子，净份额变化{float(r['net_flow_pct']):+.2f}%；全天平均结算溢价{float(r['settlement_mean_bp']):.1f}基点。\n\n![{r['category']}](results/spike_cases/case_{i:02d}.png)\n\n[分钟明细](results/spike_cases/case_{i:02d}_minutes.csv)\n\n"
s+='''## 解读限制与复现

“先拉升溢价再回落”是可观测价格形态；不能仅凭日线净份额认定回落由申购卖盘引发，也不能定位基金日净申购发生的分钟。日终净量为正可证明全天申购份额超过赎回份额，但不能还原双方总量。净赎回与份额不变的反例同样保留。

本轮继承之前的PCF和行情缺失排除、当日最终汇率固定、当前标的名单选择等口径。因此这里的比例适用于完整可估值样本；未将缺失数据的159570末季样本补成零。形态发生后的回落是定义的一部分，整套筛查是事后研究。

运行 `spike_cases.py` 生成事件表、交易时段核对与图表，运行 `write_spike_report.py` 生成本报告。识别器通过四项检查：真实多分钟形态、单点毛刺排除、不跨午休、持续溢价不误判为回落。完整事件和分组结果见 [事件明细](results/spike_cases/events.csv)、[统计汇总](results/spike_cases/summary.csv)、[分月结果](results/spike_cases/monthly.csv)。所有计算在machome同名目录完成。
'''
(R/'典型案例与溢价回落研究.md').write_text(s)
