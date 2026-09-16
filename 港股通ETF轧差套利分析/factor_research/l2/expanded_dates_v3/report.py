"""Expansion receipt, daily decisions, cumulative error rates and uncertainty figure."""
from pathlib import Path
import json
import numpy as np,pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
R=Path(__file__).resolve().parent
def pct(x):return '—' if x is None or pd.isna(x) else f'{100*x:.1f}%'
def main():
 plan=json.loads((R/'plan.json').read_text());summaries=json.loads((R/'summary.json').read_text());daily=pd.read_csv(R/'new_daily_choices.csv');prem=pd.read_parquet(R/'data/premium.parquet');errs=json.loads((R/'data/new_l2_exclusions.json').read_text());validation=json.loads((R/'validation.json').read_text());counts=json.loads((R/'data/compute_counts.json').read_text())
 def get(scope,quality='original_frozen',rank='probability',policy='rank_only',fx='lag',cut='14:45'):
  return next(a for a in summaries if (a['scope'],a['quality'],a['rank'],a['policy'],a['fx_basis'],a['cutoff'])==(scope,quality,rank,policy,fx,cut))
 def table(fx='lag',quality='original_frozen'):
  lines=['| 数据范围 | 规则 | 研究日 | 参与日 | 净申购 | 净赎回 | 净量不变 | 失误率 | 空仓日 |','|---|---|---:|---:|---:|---:|---:|---:|---:|']
  for scope,title in [('fresh_expansion','本次新增'),('prior12_plus_new','上轮12日＋本次'),('all_evaluation_dates','全部非训练/验证日期')]:
   for rank,policy,name in [('probability','rank_only','每天分数第一'),('amount','rank_only','每天预计金额第一'),('probability','threshold','原门槛通过后分数第一')]:
    a=get(scope,quality,rank,policy,fx);lines.append(f"| {title} | {name} | {a['calendar_days']} | {a['traded_days']} | {a['create_days']} | {a['redemption_days']} | {a['flat_days']} | {pct(a['error_rate'])} | {a['abstain_days']} |")
  return '\n'.join(lines)
 z=daily[(daily.fx_basis=='lag')&(daily.cutoff=='14:45')&(daily.quality=='original_frozen')&(daily.policy=='rank_only')&(daily['rank']=='probability')].copy();z.to_csv(R/'新增日期_每日概率第一.csv',index=False)
 lines=['| 日期 | 第一名 | 模型分数 | 实际净申赎U | 一篮子前日NAV估值（万元） |','|---|---|---:|---:|---:|']
 for row in z.itertuples():
  if row.status!='selected':lines.append(f'| {row.date} | 无合格候选，空仓 | — | — | — |')
  else:lines.append(f'| {row.date} | {row.symbol} | {row.score:.3f} | {row.net_baskets:.0f} | {row.basket_capital_proxy_cny/10000:.2f} |')
 (R/'figures').mkdir(exist_ok=True)
 for font in sorted([f for f in fm.findSystemFonts() if 'Arial Unicode' in f or 'PingFang' in f],key=lambda f:'Arial Unicode' not in f):
  try:fm.fontManager.addfont(font);plt.rcParams['font.family']=fm.FontProperties(fname=font).get_name();break
  except RuntimeError:continue
 plt.rcParams.update({'font.size':11,'axes.unicode_minus':False,'axes.spines.top':False,'axes.spines.right':False})
 fig,ax=plt.subplots(figsize=(12,5));scopes=['prior_19_dates','prior_12_dates','fresh_expansion','all_evaluation_dates'];x=np.arange(4)
 for offset,quality,label,color in [(-.10,'original_frozen','原样规则','#4573bc'),(.10,'isolate_513130','预先隔离513130','#159b88')]:
  a=[get(s,quality) for s in scopes];v=np.array([r['error_rate'] for r in a]);bounds=np.array([r['wilson_error95'] for r in a]);yerr=np.vstack([np.maximum(0,v-bounds[:,0]),np.maximum(0,bounds[:,1]-v)])
  ax.errorbar(x+offset,v*100,yerr=yerr*100,fmt='o',capsize=4,color=color,label=label,ms=7)
  for i,r in enumerate(a):ax.annotate(f"{r['redemption_days']+r['flat_days']}/{r['traded_days']}",(x[i]+offset,v[i]*100),xytext=(0,-18 if offset<0 else 10),textcoords='offset points',ha='center',color=color)
 ax.axhline(10,color='#a8583d',ls='--',lw=1,label='10%失误参考线');ax.set_ylim(-6,58);ax.set_yticks([0,10,20,30,40,50],['0%','10%','20%','30%','40%','50%']);ax.set_xticks(x,['此前19日','上轮12日','本次新增13日','累计44日']);ax.set_ylabel('净申购方向失误率');ax.set_title('14:45每天取分数第一 · 盘中汇率代理口径',loc='left',pad=15);ax.legend(ncol=3,loc='upper center');fig.text(.06,.02,'标注为失误次数 / 实际参与次数。区间为Wilson 95%独立样本参考；重复基金、相邻日期未必独立。累计列包含前三组。',fontsize=10,color='#475569');fig.tight_layout(rect=[0,.07,1,1]);fig.savefig(R/'figures/top_one_error_expansion.png',dpi=160);plt.close(fig)
 fresh=get('fresh_expansion');total=get('all_evaluation_dates');clean=get('all_evaluation_dates','isolate_513130');calls=sum(a['native_calls'] for a in counts);possible=sum(a['possible_symbol_cutoffs'] for a in counts);seconds=sum(a['seconds'] for a in counts);error_days=pd.Series([a['day'] for a in errs]).value_counts().to_dict();small=z[z.status.eq('selected')&z.net_baskets.lt(10)];rawrows=len(prem[['date','symbol']].drop_duplicates());funds=prem.symbol.nunique();newdates='、'.join(e['date'][5:] for e in plan['entries'])
 excluded='\n'.join(f"- {a['day']}：{a['reason']}；缺少文件类型：{', '.join(a['missing']) or '无'}。" for a in plan['inventory'] if not a['ready'])
 text=f'''# 扩充到87日：每天只选一只的新增样本检验

**本轮结果：新增13日中，14:45按盘中汇率代理选分数第一，11天参与、11次净申购、2天空仓。累计全部44个非训练/验证日期，原样规则29/33次净申购，失误率12.1%；按上轮已明确的513130质量隔离规则，则为30/32次净申购，观察失误率6.3%。**

数据量扩大后，质量过滤＋分数排名仍值得继续检验。但32次选择中仍只有30次命中，95%失误率参考区间约{pct(clean['wilson_error95'][0])}—{pct(clean['wilson_error95'][1])}；不能据此保证未来失误低于10%，更不能等同于赚钱胜率。

## 1. 目录里到底有多少可用日期

冻结时间：{plan['snapshot_at']}。在ssh machome的`/Volumes/EllisFiles/Stocksdata/A股逐笔/港股通ETF保留`检查：

- 2026年L2日期目录 **{plan['raw_2026_dates']}天**。
- 已校验清单＋PCF主表＋篮子＋分钟估值文件齐备的主研究日期 **{plan['available_dates']}天**，从1月5日至5月26日。文件齐备不代表所有基金都通过逐笔完整性检查，也不代表每天都有可参与信号。
- 上轮研究74天，本次新纳入 **{plan['new_dates']}天**：{newdates}。新增{rawrows}个可估值基金－日、{funds}只基金；两种汇率×两个截点生成{len(prem)}行，不能将重复口径算成四倍样本。
- 87天由33天训练、10天验证、44天非训练/验证观察组成。44天包括此前19日压力回溯、上轮12日及本次真正新增13日。只有本次13日是在原规则已确定之后首次用于此次检验；旧样本没有被改名为新测试。
- 5月20日目前找到的文件仍为`.7z.baiduyun.p.downloading`，没有按完整逐笔数据使用。5月25日虽然有国内ETF L2，但港股证券市场休市，不纳入这套盘中估值回测。[港交所2026年证券市场休市表]({plan['holiday_source']})

未纳入主面板的已保留日期：

{excluded}

9月2日仍留在此前520600专项案例内，不把其不完整的主面板数据补成全市场测试。2025年数据沿用你的要求不回补。本次选择性提取并校验5月25、26日目标ETF，保留原压缩包。

## 2. 模型和规则没有趁扩样重调

使用原v3概率模型，以及上一轮已保存的数量回归模型，核对12个模型文件SHA256不变。本次没有重新训练、没有重新选择阈值。数量排序仍按预测净份额×前一日NAV计算净申购金额，不是预计自己的利润。

流程仍为：截至14:30或14:45所有已完成交易分钟→平均结算溢价≥10bp、正溢价分钟≥70%、截点正溢价、中间价与结算价方向符合假设、允许申赎→仅对候选计算C++ L2→排名。午休及15:00—16:00不进入筛选均值。前一日汇率作为盘中代理；当天最终汇率单独作事后对照。原始接收时间未留存，不能把历史重建宣称为完整线上复现。

“每天第一名”只在初筛及质量合格池中选，不在没有候选的日子强行交易。并保留“达到原严格阈值后再选第一名”的可空仓版本。513130整只隔离的规则在本批新日期之前已经明确，本轮没有按新结果增删隔离名单；它对旧样本的改善仍是回溯结果。

## 3. 主要结果：14:45、盘中汇率代理

### 原样冻结规则

{table()}

原样累计33次参与中有2次净赎回、2次净量不变；方向失误率4/33=12.1%，实际发生净赎回的比例2/33=6.1%。这些不是亏损频率。

### 预先隔离已知行情异常的513130

{table(quality='isolate_513130')}

累计32次参与、30次净申购、2次净量不变，12天空仓。没有观察到净赎回不能理解为以后不会发生。金额排序累计28/32，失误率12.5%；在这批数据中不如分数排序。两者使用的是相同冻结候选流程，不是直接观察到实际申购单。

![扩样后的失误率与样本不确定性]({R/'figures/top_one_error_expansion.png'})

## 4. 新增13日逐日第一名

{chr(10).join(lines)}

本批11个选择中，有{len(small)}个实际净申购不足10U。尤其5月7日第一名分数只有0.309，最终仅净申购1U；它在这次历史样本上碰巧命中，不代表这种低分可以视为高把握机会。严格分数线为0.97，本批通过后只剩3个交易日、3次命中；不能把3/3当成可靠的零失误策略。

## 5. 最终汇率事后对照

{table(fx='final')}

本批最终汇率13天每天有候选，概率第一名12/13，失误1次净量不变；与盘中代理11/11不完全同选标。扩大到44日后，原样最终汇率34/37，失误8.1%。汇率口径会改变候选池和第一名，不能只选择较漂亮的一组结果作为可实现收益。

## 6. 质量、计算量和验证

新增批次共有{len(errs)}次标的－截点L2处理未通过质量检查，日期分布{error_days}，已保存原始问题列表；这些样本不被计作L2成功排除的误判。5月22、26日盘中代理口径没有满足溢价初筛的标的，属于空仓，不是失败，也不是预测成功。

对新增{possible}个可估值标的－截点，实际只调用C++ {calls}次（两种汇率筛选的并集），减少{1-calls/possible:.1%}；解析与特征阶段累计{seconds:.1f}秒，不含解压、估值构建和评分。继续符合先溢价初筛、后L2处理的要求。

{validation['checks']}项核对通过：冻结模型不变、新日期不重叠、原始份额与分钟价格的32条独立抽查、真实标签不会改变选择、初筛前不调用候选L2、每组每日最多一个结果。完整性检查通过只能证明在所检范围内计算一致，不能证明所有行情数据真实无误。

预算上限仍未给出，未筛除一篮子资金不足的标的。表中资金仅为前日NAV估值，实际申购预缴、现金替代、退补款和资金占用不同。历史净份额只揭示净量，不能确定总申购、总赎回及实际轧差分配；这份报告检验的是净申购方向，不是实际套利盈亏。

## 7. 文件和复现

- [本轮冻结目录清单与模型SHA256]({R/'plan.json'})
- [新增逐日概率第一]({R/'新增日期_每日概率第一.csv'})
- [全部日期、所有排名及空仓明细]({R/'all_daily_choices.csv'})
- [新旧批次与累计汇总]({R/'summary.csv'})
- [初筛、L2合格与阈值筛选统计]({R/'stages.csv'})
- [本批L2质量问题]({R/'data/new_l2_exclusions.json'})
- [验证记录]({R/'validation.json'})
- [扩样脚本]({R/'run.py'})

在machome项目虚拟环境依次运行`run.py freeze`、`run.py compute`、`run.py score`、`run.py validate`、`report.py`。已有plan.json固定本轮13日；后续下载完成的日期应另建快照，以免悄悄改变检验分母。所有文件均位于用户指定研究目录，没有发起交易、QMT联网或外部发布。
'''
 (R/'新增13日与累计87日_每日一只回测报告.md').write_text(text)
 print('Report saved',rawrows,'fund-days',funds,'funds',calls,'calls',seconds,'seconds')
if __name__=='__main__':main()
