"""Compact report of daily-top-one historical policy outcomes."""
from pathlib import Path
import json
import pandas as pd
R=Path(__file__).resolve().parent
def pct(x):return '—' if x is None or pd.isna(x) else f'{x*100:.1f}%'
def main():
 s=pd.read_csv(R/'summary.csv');d=pd.read_csv(R/'daily_choices.csv');j=json.loads((R/'summary.json').read_text())
 def one(fx,policy='rank_only',rank='probability',quality='original_frozen',scope='new_test',cut='14:45'):
  return next(a for a in j if (a['scope'],a['fx_basis'],a['cutoff'],a['quality'],a['policy'],a['rank'])==(scope,fx,cut,quality,policy,rank))
 def tbl(quality='original_frozen'):
  lines=['| 汇率口径 | 排序规则 | 参与日 | 净申购 | 净赎回 | 净量不变 | 失误率 | 未参与日 |','|---|---|---:|---:|---:|---:|---:|---:|']
  for fx,fxname in [('final','当天最终汇率，事后'),('lag','前一日汇率，盘中代理')]:
   for policy,rank,name in [('rank_only','probability','分数最高，每个有候选的日子选1只'),('rank_only','amount','预计净申购金额最大'),('threshold','probability','达到原门槛后选分数最高'),('threshold','amount','达到原门槛后选预计金额最大')]:
    a=one(fx,policy,rank,quality);lines.append(f"| {fxname} | {name} | {a['traded_days']} | {a['create_days']} | {a['redemption_days']} | {a['flat_days']} | {pct(a['error_rate'])} | {a['abstain_days']} |")
  return '\n'.join(lines)
 primary=d[d.scope.eq('new_test')&d.cutoff.eq('14:45')&d.quality.eq('original_frozen')&d.policy.eq('rank_only')&d['rank'].eq('probability')].copy()
 primary.to_csv(R/'top_probability_1445.csv',index=False)
 d[d.scope.eq('new_test')&d.cutoff.eq('14:45')&d.quality.eq('original_frozen')&d.policy.eq('rank_only')&d['rank'].eq('amount')].to_csv(R/'top_amount_1445.csv',index=False)
 a=primary[primary.fx_basis.eq('final')].set_index('date');b=primary[primary.fx_basis.eq('lag')].set_index('date')
 lines=['| 日期 | 最终汇率：分数最高标的 | 实际净量U | 前日汇率：分数最高标的 | 实际净量U |','|---|---|---:|---|---:|']
 for day in a.index:
  x=a.loc[day];y=b.loc[day]
  def fmt(row):return ('不参与','—') if row.status!='selected' else (row.symbol,f'{row.net_baskets:.0f}')
  xs,xn=fmt(x);ys,yn=fmt(y);lines.append(f'| {day} | {xs} | {xn} | {ys} | {yn} |')
 ref=one('lag');final=one('final');guard=one('lag',quality='isolate_513130');old=one('lag',scope='seen_stress');oldguard=one('lag',scope='seen_stress',quality='isolate_513130');cost=primary[primary.fx_basis.eq('final')&primary.status.eq('selected')].basket_capital_proxy_cny
 text=f'''# 每天只选一只：失误率复核

**直接答案：**按14:45、原模型“每天选净申购分数最高的一只”，当天最终汇率事后口径是11/11命中，观察到的失误率0%；但更接近盘中可用信息的前一日汇率口径是9/11命中，失误率**18.2%**，其中1次实际净赎回、1次净量不变。按预计净申购金额最大排序，两种口径都为8/11命中，失误率27.3%。

因此，对“每天挑第一名就参与”，目前可参考的是约18%的历史方向失误频率，不能用事后口径的0%当作未来风险。这也不是亏钱概率：真实退补款、轧差分配和费用尚无标签。

## 计算口径

沿用上一轮12个日期、1759个基金－日。先做已冻结的分钟溢价初筛，再要求L2数据合格，从候选中只取1只；并没有从全市场未筛选标的中强行取第一名。4月13日没有合格候选，因此最多参与11天。每天有多个候选也只算1次，空仓不计成功。

“分数最高”使用原冻结L2分类模型。分数不是经过充分校准的真实概率。金额排序则是本轮新增的固定数量回归对照：只使用原1月5日至2月27日训练样本、同一组模型参数，拟合asinh净流量比例；转回预计净份额，再乘前一日NAV得到预计净申购金额。没有用这12日挑数量模型、参数或阈值。金额也不是实际申购所需现金、自己的利润或可赚金额。份额数、篮子数两种替代排名保存在summary.csv，不据此选择一个最好看的策略。

**这12日已经在上一轮看过。本轮是对同一批历史结果的选标策略复核，不是新增独立验证。** 另外保留此前19日压力回溯，单独呈现。输入可疑的513130先保留在原样统计，再给出整只基金隔离后的敏感性结果，不能无说明地删除错单。

## 14:45两种排序、两种参与方式

{tbl()}

“达到门槛”沿用上一轮在验证集选出的分数线：最终汇率0.92、前一日汇率0.97。先过滤再排序，没有为了每天都能交易调低门槛。按前一日汇率，严格门槛只交易1天、空仓11天；1/1无法支持高胜率结论。

## 每天实际会选什么

{chr(10).join(lines)}

前一日汇率口径的两次失误：4月9日513130实际净赎回495U；4月17日159735净量不变。4月9日最终汇率口径选中520990（分数0.970784），而有行情问题的513130分数0.969516，两者只差约0.00127；一次极小的排序差就可能改变这次历史结果，不能将11/11视为稳健保证。

最终汇率第一名中，4月9日、4月14日实际只净申购1U；“方向猜中”并不代表有足够大的轧差优势。

## 不确定性与已有行情问题

- 原样前日汇率9/11，失误率18.2%。即使近似按独立交易日计算，Wilson 95%失误率参考区间仍为{pct(ref['wilson_error95'][0])}—{pct(ref['wilson_error95'][1])}；相邻日期、重复标的也未必独立。
- 最终汇率11/11的Wilson 95%失误率上界仍约{pct(final['wilson_error95'][1])}，并不等于风险为零。它还有最终汇率不可提前知道的限制。
- 把此前已经发现分钟估值问题的513130整只隔离后，前日汇率变为9/10，观察失误率10.0%，多1天空仓。这个变化来自质量隔离，不能算作新模型带来的独立验证成功；其失误率参考区间仍约{pct(guard['wilson_error95'][0])}—{pct(guard['wilson_error95'][1])}。
- 此前19日压力回溯中，原样前日汇率也是9/11，失误率18.2%；整只隔离513130后为10/11，失误率9.1%。两组原样合计18/22、4次失误；这是旧数据汇总，不能冒充22个新验证日。
- 14:30原样概率第一名：最终汇率10/11、前日汇率9/11。不能只挑14:45事后最好的那个数字来宣布策略有效。

隔离513130后的完整敏感性对照如下：

{tbl('isolate_513130')}

## 资金较少，还需要考虑什么

本次没有提供预算上限，因此上述第一名未经过“资金够买1个篮子”的过滤。最终汇率选中的标的，一个篮子的前日NAV估值约{cost.min()/10000:.1f}万—{cost.max()/10000:.1f}万元，中位数{cost.median()/10000:.1f}万元。实际申购预缴资金、现金替代溢价、退补款等待和两侧资金占用可能不同，不能把这个估值直接当下单现金需求。

如果第一名买不起而改做第二名，上述失误率就不再适用；必须先按真实资金约束过滤，再重新取第一名。这些结果支持继续研究“合格候选中按分数排序”，不支持追逐“预计申购金额最大”，也还不足以证明90%以上赚钱胜率。

## 文件与复核

- [逐日概率第一名]({R/'top_probability_1445.csv'})
- [逐日金额第一名]({R/'top_amount_1445.csv'})
- [全部规则汇总]({R/'summary.csv'})
- [全部逐日选择及空仓记录]({R/'daily_choices.csv'})
- [模型、样本与规则说明]({R/'plan.json'})
- [可复跑分析脚本]({R/'analyze.py'})
- [验证记录]({R/'validation.json'})

核对了原分类模型SHA256不变、数量回归仅使用旧训练期、排名不受真实标签变化影响、每次选择确为合格池内最大值、每天至多一只，以及各分组成功/赎回/不变/空仓的计数一致。所有回测与输出仍留在用户指定项目目录，未接入交易。
'''
 (R/'每日只选一只_失误率复核.md').write_text(text)
 print('Report saved')
if __name__=='__main__':main()
