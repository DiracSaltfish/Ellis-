"""Generate the durable report and standard static figures from audited outputs."""
import json, ast
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
import pipeline as p

def pct(v):return '—' if pd.isna(v) else f'{v*100:.1f}%'
def main():
 R=p.R;plan=json.loads((R/'plan.json').read_text());summary=pd.read_csv(R/'summary.csv');pred=pd.read_parquet(R/'predictions.parquet');premium=pd.read_parquet(R/'premium.parquet');selection=json.loads((R/'selection.json').read_text());audit=json.loads((R/'validation.json').read_text());counts=json.loads((R/'compute_counts.json').read_text());quality=json.loads((R/'new_l2_exclusions.json').read_text());rawcheck=json.loads((R/'raw_source_spotcheck.json').read_text());hk=json.loads((R/'hk_03033_spotcheck.json').read_text());stale=pd.read_csv(R/'suspect_static_iopv.csv');boots=json.loads((R/'bootstrap.json').read_text());levels=pd.read_csv(R/'net_quantity_levels.csv');importance=pd.read_csv(R/'permutation_groups.csv')
 new=summary[summary.split.eq('new_test')];q=pred[pred.split.eq('new_test')&pred.variant.eq('premium_l2')];final=q[q.fx_basis.eq('final')&q.cutoff.eq('14:45')];lag=q[q.fx_basis.eq('lag')&q.cutoff.eq('14:45')]
 def get(fx,cut,stage,scope='new_test'):return summary[(summary.fx_basis==fx)&(summary.cutoff==cut)&(summary.stage==stage)&(summary.split==scope)].iloc[0]
 def result_table(fx):
  lines=['| 截点 | 规则 | 样本 | 净申购 | 净赎回 | 净量不变 | 命中率 | 误判率 |','|---|---|---:|---:|---:|---:|---:|---:|']
  names={'premium_gate':'分钟溢价初筛','gate_l2_valid':'初筛＋L2数据合格','premium_selected':'再用溢价模型复核','premium_l2_selected':'再用溢价＋L2复核','premium_equal_daily_count':'溢价模型：每日同数量对照','premium_l2_score90':'L2模型分数≥0.90（诊断）'}
  for cut in ['14:30','14:45']:
   for stage,name in names.items():
    a=get(fx,cut,stage);lines.append(f'| {cut} | {name} | {a.n} | {a.create} | {a.redeem} | {a.flat} | {pct(a.precision)} | {pct(a.error_rate)} |')
  return '\n'.join(lines)
 def link(name):return f'[{name}]({R/name})'
 (R/'figures').mkdir(exist_ok=True)
 for font in sorted([x for x in fm.findSystemFonts() if 'Arial Unicode' in x or 'PingFang' in x],key=lambda x:'Arial Unicode' not in x):
  try:fm.fontManager.addfont(font);plt.rcParams['font.family']=fm.FontProperties(fname=font).get_name();break
  except RuntimeError:continue
 plt.rcParams.update({'font.size':11,'axes.spines.top':False,'axes.spines.right':False,'axes.unicode_minus':False})
 fig,axes=plt.subplots(1,2,figsize=(15,5.6),sharex=True)
 stages=['gate_l2_valid','premium_selected','premium_l2_selected','premium_l2_score90'];labels=['溢价初筛，L2数据合格','溢价模型复核','溢价＋L2复核','溢价＋L2，分数≥0.90']
 for ax,fx,title in zip(axes,['final','lag'],['当天最终汇率 · 仅事后分析','前一日汇率 · 历史盘中代理']):
  rows=[get(fx,'14:45',s) for s in stages];left=np.zeros(4)
  for col,name,color in [('create','净申购','#0b9488'),('flat','净量不变','#bec7d4'),('redeem','净赎回','#e46958')]:
   value=np.array([r[col]/r.n*100 if r.n else 0 for r in rows]);ax.barh(np.arange(4),value,left=left,color=color,height=.6,label=name);left+=value
  for i,r in enumerate(rows):ax.text(102,i,f'{r.create}/{r.n}',va='center',fontsize=11)
  ax.set_yticks(range(4),labels);ax.invert_yaxis();ax.set_xlim(0,117);ax.set_xticks([0,25,50,75,100],['0%','25%','50%','75%','100%']);ax.set_title(title,loc='left',pad=13);ax.axvline(90,c='#334155',ls=':',lw=1);ax.set_xlabel('候选实际结局占比（右侧为净申购次数 / 候选数）')
 axes[1].legend(loc='upper center',bbox_to_anchor=(.5,-.16),ncol=3);fig.suptitle('12个新增日期 · 14:45：筛选后命中率提高，L2额外贡献未被证明',x=.02,ha='left',fontsize=16);fig.text(.02,.025,'严格规则来自旧验证集。2/2只发生在同一天，不能据此认定零误判。两侧样本重叠，不可相加。',color='#475569');fig.tight_layout(rect=[0,.1,1,.93]);fig.savefig(R/'figures/stages_1445.png',dpi=160);plt.close(fig)
 days=[e['date'] for e in plan['entries'] if e['split']=='new_test'];selected=final[final.selected];daily=[]
 for day in days:
  a=selected[selected.date.eq(day)];daily.append(dict(date=day,n=len(a),create=int(a.net_shares.gt(0).sum()),redeem=int(a.net_shares.lt(0).sum()),flat=int(a.net_shares.eq(0).sum())))
 daily=pd.DataFrame(daily);daily.to_csv(R/'new_selected_by_date_1445.csv',index=False)
 fig,ax=plt.subplots(figsize=(13,4.8));bottom=np.zeros(len(days))
 for col,name,color in [('create','净申购','#0b9488'),('flat','净量不变','#bec7d4'),('redeem','净赎回','#e46958')]:ax.bar(np.arange(len(days)),daily[col],bottom=bottom,color=color,label=name);bottom+=daily[col].to_numpy()
 ax.set_xticks(range(len(days)),[d[5:] for d in days]);ax.set_ylabel('基金－日候选数');ax.set_title('最终汇率事后口径 · 14:45 L2复核候选的逐日结局',loc='left');ax.legend(ncol=3);ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True));fig.text(.06,.02,'4月13日初筛通过的L2样本全部未通过完整性检查；无信号不能解释成预测全部正确。');fig.tight_layout(rect=[0,.08,1,1]);fig.savefig(R/'figures/daily_1445.png',dpi=160);plt.close(fig)
 # A valuation-quality sensitivity, discovered after outcomes: explicitly never promoted as holdout success.
 flagged=set(zip(stale.date,stale.symbol));sens=[]
 for (fx,cut),a in q.groupby(['fx_basis','cutoff']):
  sel=a[a.selected];keep=np.array([(d,s) not in flagged for d,s in zip(sel.date,sel.symbol)]);sens.append(dict(fx_basis=fx,cutoff=cut,removed=len(sel)-int(keep.sum()),**p.stats(sel,keep),status='posthoc_data_quality_sensitivity_not_new_validation'))
 pd.DataFrame(sens).to_csv(R/'static_iopv_sensitivity.csv',index=False)
 failures=selected[selected.net_shares.le(0)].copy();cols=['date','symbol','score','net_baskets','settlement_mean_bp','settlement_positive_fraction','v2_sell_unit_U','v2_buy_unit_U','v2_sell_unit_fill_ratio','v2_sell_unit_cancel_ratio'];failures[cols].to_csv(R/'new_false_cases_1445.csv',index=False)
 lines=['| 日期 | 标的 | 模型分数 | 实际净量U | 平均溢价bp | 卖方近整篮成交U | 买方近整篮成交U |','|---|---|---:|---:|---:|---:|---:|']
 for a in failures.itertuples():lines.append(f'| {a.date} | {a.symbol} | {a.score:.3f} | {a.net_baskets:.0f} | {a.settlement_mean_bp:.1f} | {a.v2_sell_unit_U:.2f} | {a.v2_buy_unit_U:.2f} |')
 frow=get('final','14:45','premium_l2_selected');prow=get('final','14:45','premium_selected');vrow=get('final','14:45','gate_l2_valid');lrow=get('lag','14:45','premium_l2_selected');wc=ast.literal_eval(frow.wilson95);wb=next(x for x in boots if x['fx_basis']=='final' and x['cutoff']=='14:45' and x['split']=='new_test');qty=levels[(levels.fx_basis=='final')&(levels.cutoff=='14:45')&(levels.stage=='selected')].iloc[0]
 sensitivity=next(x for x in sens if x['fx_basis']=='final' and x['cutoff']=='14:45');totalcalls=sum(x['native_calls'] for x in counts);possible=sum(x['possible_symbol_cutoffs'] for x in counts);runtime=sum(x['seconds'] for x in counts);bad_days=pd.Series([e['day'] for e in quality]).value_counts().to_dict()
 # Shared-candidate ranking AP, without selecting a threshold from the test curve.
 from sklearn.metrics import average_precision_score
 aps=[]
 for fx in ['final','lag']:
  for var in ['premium','premium_l2']:
   a=pred[(pred.fx_basis==fx)&pred.variant.eq(var)&pred.split.eq('new_test')&pred.cutoff.eq('14:45')];aps.append((fx,var,average_precision_score(a.net_shares.gt(0),a.score)))
 p.save('ap_comparison.json',[dict(fx_basis=f,variant=v,cutoff='14:45',ap=float(a)) for f,v,a in aps])
 text=f'''# 溢价先筛、L2再复核：新增12日回测

本轮结论：**可以显著收窄候选，但尚不能证明L2带来了稳定的额外收益，也不能支持“90%以上赚钱胜率”。** 最终汇率事后口径14:45命中{frow.create}/{frow.n}，方向误判率{pct(frow.error_rate)}；同数量的溢价模型对照也命中{frow.create}/{frow.n}。历史盘中代理口径的严格规则只留下{lrow.n}次，全部发生在同一天。

报告基于冻结的本地历史数据生成；研究可复跑，未接QMT、未发交易、未替换旧模型。汇率与份额均为历史缓存，不声称重新逐日抓取了网站。

## 1. 数据、时间和比较方法

- 共74个2026年交易日；训练1月5日至2月27日33天，验证3月2日至13日10天。新增检验为4月9、10、13、14、15、16、17、20、21、28、29、30日12天；此前看过的3月16日至4月8日及4月22日至27日共19天，只作回溯压力检验。
- 每个截点新增有1759个可估值基金－日，实际净申购205、净赎回556、净量不变998；基础净申购比例11.7%。一个基金一天分别有14:30和14:45输出，两种汇率口径也重复同一批标签，**不能把四份记录当四倍独立样本**。
- T日网站份额变动对应T日盘中净申赎。用PCF日历核对前期份额，检查净量是否接近整篮单位；没有把标签向后错移一天。
- 所有已完成分钟都参与计算：09:31—11:30、13:01—截点，分别210/225个预期分钟，要求有效覆盖≥95%。午休及15:00—16:00不进入均值或持续时间。原始bar按既有保守口径延后一分，同日最多向前填充5分钟。
- **final**：按照你的简化要求，整天使用当日最终结算汇率；它是事后解释，不是可在14:45预知的信息。**lag**：整天使用此前最近一次可用结算汇率，检验盘中可用代理；没有当时实际接收时间记录，所以仍是历史重建。
- 当前网站标的池、能重建PCF的样本及已有L2共同限定研究范围；不能外推至所有基金规则、所有市场阶段。

初筛规则在新日期结果检验前固定：平均结算溢价≥10bp，正溢价分钟比例≥70%，截点仍为正溢价，中间价IOPV高于结算IOPV，PCF同时允许申购赎回。10bp是研究筛选线，不是扣除全部费用后的套利盈亏线。

两种模型都只在初筛通过的历史样本上训练，使用相同训练数据、模型复杂度和静态PCF信息。“溢价模型”包含全分钟路径统计、换手、此前净流量和基金规模；“溢价＋L2”额外加入逐笔特征。验证集选择命中率≥90%、至少30条、5天、5只基金的阈值，优先保留更多样本。分数不是校准后的真实概率，0.97不等于97%实际胜率。阈值详见{link('selection.json')}。

## 2. 最终汇率事后回测

{result_table('final')}

14:45最终L2候选占全部1759个基金－日的{pct(frow.coverage)}。在L2合格的初筛池内，原有{int(vrow.n-vrow.create)}个错误候选，复核后剩{int(frow.n-frow.create)}个，剔除了{int(vrow.n-vrow.create-frow.n+frow.create)}个；但也同时丢掉了{int(vrow.create-frow.create)}个真实净申购，只保留{int(frow.create)}/{int(vrow.create)}。

**不能把48.9%升至89.7%全部归功于L2。** 仅溢价模型也可通过更严格筛选达到接近的结果；每天挑选与L2相同数量时，命中数量完全一致。这个“同数量”对照只用来比较排序，因为当天数量由L2阈值确定，不能冒充独立可部署规则。

![两阶段筛选比较]({R/'figures/stages_1445.png'})

## 3. 前一日汇率：更接近盘中能做的判断

{result_table('lag')}

严格L2规则在14:30为7/7，仅4个信号日、4只基金；14:45为2/2，仅1个信号日、2只基金。即使暂按相互独立样本计算，Wilson 95%命中率下界也仅64.6%和34.2%。不能把零次观察到的错误解释为零风险；全部成功的小样本会让普通bootstrap产生虚假的100%—100%区间，本报告对此不提供精度区间。

放宽至模型分数≥0.90，14:45为19/23，误判4次：1次净赎回、3次净量不变，误判率17.4%。这里同时展示固定分数线，是提前约定的诊断对照，没有用新增日期选择一个更好看的阈值。

## 4. 误判意味着什么

最终汇率14:45误判3次，其中净赎回2次、净量不变1次；**发生净赎回的比例为2/29=6.9%**。将净量不变也计入“未判断对净申购”，总误判率为3/29=10.3%。按独立样本近似的Wilson 95%误判率参考区间为{pct(1-wc[1])}—{pct(1-wc[0])}；按日期重采样的命中率区间为{pct(wb['l2_precision_ci95'][0])}—{pct(wb['l2_precision_ci95'][1])}，只有12个日期，仍很不稳定。

{chr(10).join(lines)}

513130出现了可疑的底层分钟行情停滞，见数据质量部分。159735虽然有卖方近整篮成交，最终份额没有增加；520920虽有卖方近整篮供给，最终仍净赎回。逐笔单可以来自已有库存、普通交易及做市行为，无法仅凭“像1U”的成交确认新申购份额。

![候选逐日结局]({R/'figures/daily_1445.png'})

14:30与14:45信号大量重合；不同ETF也受同一天市场环境影响。增加参与基金数量不能直接按独立伯努利试验稀释风险，日期分布和跨基金共同错误必须保留。

## 5. 哪些L2信号有帮助

本轮复用沪深分开的C++撮合与主动方向识别，使用真实主动成交、被动成交及委托恢复证据，未以涨跌tick简单替代主动方向。新增日只对通过溢价初筛的标的调用内核。

- 主动买卖金额差、大额主动母单成交比例。
- 接近整篮母单成交、邻近成交群，以及0.8U/1.2U/1.3U假单位背景对照。
- 已知原始委托量接近整数U的实际成交，分开统计主动/被动部分；未知原始量只用可确认的主动成交证据。两类不重叠，撤单不算成交。
- 近整篮委托成交率、撤单率、未成交比例；卖方与买方分别输入。卖方减买方仅为二级市场供给代理，不直接等于净申购。
- 本轮为了让两种FX口径严格可比，没有复用依赖旧FX口径的“溢价压缩成交比例”两列。

对新增日期作组内置换诊断：最终汇率14:45打乱“卖方整篮成交证据”后，平均排序AP下降约0.0055；主动资金/大单组约0.0008。前一日汇率下卖方整篮组的AP变化反而约−0.0054。其增量不稳定，不能据此制定一个“卖出多少U就确认申购”的公式。相关特征之间可互相替代，置换结果也不是因果解释。完整分组与桶统计见{link('permutation_groups.csv')}、{link('factor_buckets.csv')}。

“净申购”也不等于“大量净申购”：最终汇率14:45的29个候选中，实际≥10U仅{int(qty.actual_at_least_10U)}次（{int(qty.actual_at_least_10U)/29:.1%}），≥20U仅{int(qty.actual_at_least_20U)}次（{int(qty.actual_at_least_20U)/29:.1%}），净量中位数{qty.median_net_U:.0f}U。这些是候选真实量的分布，不是准确的数量预测。详见{link('net_quantity_levels.csv')}。

## 6. 数据质量及其实际影响

1. 新增候选有{len(quality)}次标的－截点C++调用未通过完整性检查，日期分布为{bad_days}。最终汇率14:45从259个初筛样本降至229个可复核样本；被隔离样本不能当作L2成功剔除误判。
2. 独立核对159105的原始CSV：4月10日截至14:45，成交引用的原始委托全部存在，逐笔成交量96,823,702股与快照一致；4月13日有8个被成交引用的委托号根本不在同截止时刻的原始委托文件中，涉及12笔成交、75,000股，逐笔汇总与最新同截点快照另差300股。至少这个样本的问题存在于原始文件，不能通过关闭C++报错来补齐。完整性结论仅在检查范围内，未推断缺失的账户或交易。
3. 在12个新增日期中发现11个基金－日“底层估值全程相同、ETF价格变化”的可疑样本，均为513130。其PCF底层为03033.HK。4月9日原始港股ZIP截至14:45的收盘价只有1种、成交额合计0，首笔正成交额时间为{hk['first_positive_amount_time']}。这会让错误或过期IOPV产生看似持续的溢价，L2不能替估值补真值。需要正确的03033.HK早盘至14:45分钟数据核实和修复。
4. 仅作事后敏感性检查：移除上述可疑估值日，最终汇率14:45候选变为{sensitivity['create']}/{sensitivity['n']}，命中率{pct(sensitivity['precision'])}。**这个结果不能作为新的90%验证证据**：异常是复核误判时发现的，模型仍曾在历史旧估值上训练，尚未拿到正确行情；更不是L2筛选提高了胜率。原始结果完整保留在主表。

数据本身有缺口时，系统应输出“输入不合格/无法确认”，不把它填为零买卖或强行给交易信号。文件清单与抽查证据：{link('new_l2_exclusions.json')}、{link('raw_source_spotcheck.json')}、{link('hk_03033_spotcheck.json')}、{link('suspect_static_iopv.csv')}、{link('static_iopv_sensitivity.csv')}。

## 7. 是否支持参与

目前更适合用作研究候选清单，**不具备按高胜率策略批量参与的证据**。值得保留的是“溢价先筛”的计算流程；当前L2应承担辅助证据与质量检查，不能作为申购已发生的确认。

按你描述的净额结算假设，设外部申购C、赎回R、N=C−R>0，自己申购并赎回q篮，中间价结算一篮价值M、实际买入一篮成本B，且两侧确实使用你描述的轧差规则，则费用前价差为 q×N/(C+q)×(M−B)。历史份额只提供N，无法观测C，因此方向判断正确，也无法单靠这轮数据确定轧差占比、退补款和实际利润。这里不重新质疑你已经实测可同日分别结算的前提。

下一轮应先补齐4月13日逐笔缺失和03033.HK早盘行情，固定质量规则后，在新日期检验；同时保留仅溢价模型的同覆盖对照。实际退补款与费用尚缺，本报告所有“命中率”均为净申购方向命中率，**不是赚钱胜率**。

## 8. 性能、代码与复现

新增数据若对每个可估值标的、每个截点全部解析，需要{possible}次；实际两种汇率初筛的并集仅调用{totalcalls}次，减少{1-totalcalls/possible:.1%}。machome本次C++解析及特征阶段累计{runtime:.1f}秒；不含解压、分钟估值构建、模型训练，不能把它当作完整线上延迟。

本轮选择性提取并CRC校验了4月28—30日压缩包的目标ETF，原压缩包保留。研究目录所有变更均在用户指定项目下；没有删除外部数据。旧v1/v2模型保持独立。

审计：3项入口单元测试、{audit['checks']}项数据/计算核对通过，其中48条独立分钟均值和原始份额抽查；通过范围是代码计算与冻结数据一致，不代表源行情已全部真实可靠。未使用新增结果改动模型或阈值，后续仅修正置信区间展示和添加源数据诊断。

主脚本 {link('pipeline.py')}，复核 {link('validate.py')}，逐条输出 {link('predictions.csv')}，筛选比较 {link('summary.csv')}，原计划 {link('plan.json')}，阈值 {link('selection.json')}，逐日期 {link('by_date.csv')}，验证记录 {link('validation.json')}。历史19日压力检验在summary.csv的seen_stress分组，不能冒充本轮新增日期。

在machome项目虚拟环境依次运行：`pipeline.py freeze`、`pipeline.py premium`、`pipeline.py historical`、`pipeline.py train`、`pipeline.py new_l2`、`pipeline.py evaluate`、`validate.py`、`diagnostics.py`、`source_audit.py`、`report.py`。plan.json冻结74日；新增未来日期应另建计划，避免无记录地改变检验集。

来源：PCF与港股、ETF分钟、L2文件路径及SHA256见plan.json；份额为1navs缓存并沿用你给出的T日定义。官方汇率查询入口为[上交所历史港股通汇兑比率](https://www.sse.com.cn/services/hkexsc/disclo/ratios/)；本次网页只核对入口，历史数值沿用冻结缓存。旧升级说明见[模型升级与其他日期验收报告]({p.V/'模型升级与其他日期验收报告.md'})。
'''
 (R/'溢价初筛与L2复核_新增12日回测报告.md').write_text(text)
 p.save('release_status.json',dict(status='research_only_not_promoted',direction_precision_90_established=False,profitability_established=False,model_thresholds_unchanged=True,limitations=['L2 has no demonstrated incremental precision at matched daily counts','2/2 live-proxy strict results occur on a single date','Missing original orders on Apr13','Suspect static underlying valuation for 513130; posthoc exclusion is not validation','Gross creations/redemptions and realized redemption/creation costs unavailable']))
 print('report and figures saved',totalcalls,possible,runtime,'sensitivity',sens)

if __name__=='__main__':main()
