#!/usr/bin/env python3
"""Render the reviewed pilot outputs and execute the companion notebook."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import nbformat
from nbclient import NotebookClient

ROOT=Path(__file__).resolve().parents[1]


def mdtable(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+
                     ['| '+' | '.join(str(v) for v in row)+' |' for row in rows])


def main():
    m=pd.read_csv(ROOT/'reports/model_comparison.csv');folds=pd.read_csv(ROOT/'reports/folds.csv')
    s=pd.read_csv(ROOT/'reports/sensitivity.csv');result=json.loads((ROOT/'reports/results.json').read_text())
    validation=json.loads((ROOT/'reports/validation.json').read_text())
    pair=m[m.model=='HHI_HTI_fixed_pair'].sort_values('horizon');auto=m[m.model=='selected'].sort_values('horizon')
    ranked=m[m.horizon==30].sort_values('residual_std_bps');latest=folds[folds.horizon==30].iloc[-1]
    chart_contract={'question':'Compare unhedged, fixed HHI+HTI and selected price residual volatility at each horizon',
        'takeaway':'HHI+HTI lowers observed OOS volatility; automatic selection does not consistently improve it',
        'family':'bar','variant':'grouped','rows':12,'grain':'model and holding horizon','surface':'standalone PNG and notebook',
        'renderer':'matplotlib','palette':'hard two-root cap: blue #2864B4 and gold #B78A27; neutral #CBD2DA',
        'non_color':'legend order and hatched automatic selection','source':'reports/model_comparison.csv',
        'time_window':'2026-06-10..2026-08-03; 37 OOS days','units':'standard deviation of holding-period returns, bp',
        'qa':'inspect exported PNG at full resolution; zero baseline; no annualization or cumulation of overlapping returns'}
    (ROOT/'reports/chart_contract.json').write_text(json.dumps(chart_contract,indent=2))
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False})
    fig,ax=plt.subplots(figsize=(10.5,5.8));x=np.arange(4);width=.25
    ax.bar(x-width,pair.target_std_bps,width,label='Unhedged basket',color='#CBD2DA')
    ax.bar(x,pair.residual_std_bps,width,label='HHI + HTI (rolling beta)',color='#2864B4')
    ax.bar(x+width,auto.residual_std_bps,width,label='Nested candidate selection',color='#B78A27',hatch='//')
    for xx,val in zip(x,pair.residual_std_bps):ax.text(xx,val+.7,f'{val:.1f}',ha='center',fontsize=10)
    ax.set_xticks(x,[f'{h} min' for h in pair.horizon]);ax.set_ylabel('Holding-period return volatility (bp)');ax.set_ylim(0,62)
    ax.set_axisbelow(True);ax.grid(axis='y',color='#e4e7eb',linewidth=.6);ax.legend(loc='upper left',frameon=False,fontsize=10)
    fig.suptitle('520600 | Out-of-sample residual risk',x=.095,ha='left',fontsize=18,fontweight='bold')
    ax.set_title('10 Jun–3 Aug 2026 · 37 trading days · afternoon minute-price study',loc='left',fontsize=10,color='#555')
    fig.text(.095,.03,'Source: daily PCF, HK minute marks and IBKR futures. Costs excluded; overlapping windows are not separate trades.',fontsize=9,color='#555')
    fig.subplots_adjust(left=.095,right=.98,top=.83,bottom=.14)
    fig.savefig(ROOT/'reports/residual_risk.png',dpi=180);plt.close(fig)
    horizons=mdtable(['持有期','未对冲波动 bp','HHI＋HTI 残差波动 bp','方差下降','自动选择残差 bp'],
        [[int(r.horizon),f'{r.target_std_bps:.2f}',f'{r.residual_std_bps:.2f}',f'{r.variance_reduction:.1%}',f"{auto[auto.horizon==r.horizon].iloc[0].residual_std_bps:.2f}"] for _,r in pair.iterrows()])
    compare=mdtable(['模型/工具','残差波动 bp','方差下降','上涨 ES95 bp','下跌 ES95 bp'],
        [[r.model,f'{r.residual_std_bps:.2f}',f'{r.variance_reduction:.1%}',f'{r.upside_es95_bps:.2f}',f'{r.downside_es95_bps:.2f}'] for _,r in ranked.iterrows()])
    sensitivity=s[(s.horizon==30)&(s.model=='HHI_HTI_fixed_pair')]
    sens_table=mdtable(['检查','外测日数','样本数','残差波动 bp','方差下降'],
        [[r.scenario,int(r.days),int(r.samples),f'{r.residual_std_bps:.2f}',f'{r.variance_reduction:.1%}'] for _,r in sensitivity.iterrows()])
    report=f'''# 520600 首轮对冲研究

生成日期：2026-09-06。结果状态：**PASS_RISK_ONLY_WITH_CAVEATS**。

## 结论

520600 的数量篮子可优先用 **HHI＋HTI** 建立对冲研究基准，保留 **HTI 单腿**和恒科 ETF 作为对照。
本轮 30 分钟外测中，HHI＋HTI 将波动从 **36.91bp 降至 19.43bp**，方差下降 **72.3%**；按交易日重采样的 95% 区间约 **66.4%—77.3%**。
HTI 单腿残差为 **20.03bp**，双腿只再降低约 **0.60bp**；在未计费用的阶段，这不足以认定双腿净收益一定更高。
名称接近汽车行业的 02845，本轮方差下降仅 **12.1%**，不能因为主题相似就优先采用。
自动候选选择在 30/60 分钟没有胜过固定 HHI＋HTI 对照，不能据此称复杂模型更优。
该固定组合是事先列入的对照；其“本轮较好”不构成未来市场最优工具的证明。

## 数据与用户指定口径

- 原始窗口：2026-03-03—2026-08-03，共 100 个共同文件日。
- 剔除东风集团权益定价未完成的 3 日，剩余 97 日；滚动训练 60 日，外测 **2026-06-10—08-03，共 37 日**。
- 每日 PCF 固定数量篮子；全部港股成分按**强制现金替代**处理。这是用户指定的研究假设，原始替代标志仍保存。
- 使用一分钟 LAST 收盘价格，不计算成交量、盘口、tick 排队、佣金或冲击成本。港股旧成交文件已在远端直接聚合成一分钟 OHLC，未把逐笔明细传回。
- 统一 13:01—15:00 的价格可用时刻；输入分钟标签暂按分钟开始处理，收盘价在 label+1 分钟可用。13:00—14:59 标签共 120 个点；不使用包含 15:00 后成交的 15:00 柱。
- HSI/HHI 复用 newnavnav 已采集数据；HTI 新抓取 18,100 条下午一分钟数据，月份沿用相同的固定 25 日切换规则，绝不跨合约计算日内收益。
- 沪港通日终结算汇率复用本地已有官方历史。买港股的人民币支出用**卖出结算汇兑比率**，卖港股收入用**买入结算汇兑比率**。最终结算率只参与事后人民币金额核算，不参与当时选模。
- 同一日下午结算率为常数，因此篮子收益率中的汇率会抵消。本研究不包含盘中预测结算率的误差、隔夜汇率风险或 IB 账户实际换汇损益。

## 持有期比较

以下是持有期收益率标准差，未年化，方差下降与波动下降是不同指标。

{horizons}

![样本外残差风险]({ROOT}/reports/residual_risk.png)

30 分钟共有 3,330 个重叠收益标签；它们不是 3,330 笔独立可成交套利。所有工具比较使用同一批外测样本。

## 30 分钟候选比较

{compare}

ES95 表示相应方向最坏 5% 残差的平均值，不是最大损失或亏损上限。HHI＋HTI 的上涨尾部仍约 42bp，不能把风险降低误读为无风险套利。

## 映射与敞口比例

对 HHI＋HTI 固定组合，30 分钟外测各日滚动系数均值约 **0.532 HHI＋0.442 HTI**。
最后一个外测日 2026-08-03 使用截至 07-31 的训练数据，系数为 **{latest.fixed_pair_HHI_beta:.3f} HHI＋{latest.fixed_pair_HTI_beta:.3f} HTI**。
这是相对于篮子风险名义金额的比例，**不是期货张数比例，也不是 9 月当前推荐仓位**。
担心申购补券成本上涨时取多头保护；持有篮子担心下跌时取反向空头，必须与实际敞口方向匹配。

给定历史决策时点的篮子 HKD 名义额 N、期货价格 F、乘数 m：理论张数 = beta×N/(F×m)。当前先保留连续理论敞口，整数张数误差放到执行阶段。

## 停复牌与公司行动

1. **02402 亿华通**：已用港交所披露核实 04-01 停牌、05-04 复牌。19 个试跑日以停牌前最后可见价格冻结估值，保留全部数量；冻结名义额最大约占篮子 **0.060%**。这不代表停牌证券的经济风险为零。
2. **00489 东风集团**：03-10 最后交易；股份权益包含 6.68 港元现金与每股 0.3552608 股岚图股份。03-11—13 的 PCF 仍有该项，整日剔除；没有删成分后重新归一化，也没有只按现金部分估值。
3. **02525 禾赛**：07-10 一拆八，实际 PCF 转为临时代码 02983，数量由 07-09 的 55 股变为 07-10 的 438 股，07-24 恢复 02525。按每日实际代码与数量直接估值，不再重复应用拆股因子。
4. 对 60 个成分证券代码（包含临时代码）完成公开公司行动接口筛查，58 个成功，获得 24 条分红和 3 条拆股事件。东风及文远代码的接口失败已单列；文远已核对为 WeRide，不能拼接旧 A8 New Media 历史。该筛查不是全部发行人公告的完整认证。
5. 普通除权除息使用当日未复权价格与当日 PCF；标签不跨日，所以不把隔夜机械除权变化当成对冲收益，也不在盘中交易 PnL 重复加分红。

原始事件、来源及策略分别见 `../data/inventory/verified_events.json`、`corporate_actions_screen.csv` 和 `corporate_actions_fetch.json`。

## 敏感性检查

分钟无新成交时，主研究使用当日最后可见价格延续。五分钟以上陈旧价格对应名义额的权重中位数约 **1.67%**，95 分位约 **16.5%**，这是本轮最主要的数据局限。
fresh 指标签起点和终点的陈旧名义额均不超过门槛；该筛选仅用于稳健性核验，不是实时交易信号。

{sens_table}

只保留陈旧敞口不超过 2% 的样本并重拟合，30 分钟 HHI＋HTI 方差仍下降约 65.3%；时间错位 ±1 分钟的两项检查使用冻结的原始日度系数，不根据哪个偏移更好而重选时间口径。
不同筛选的日期和样本组成不同，不能直接用这些数值宣称策略进一步改善。

## 实现与验证

训练先用过去 60 日的前 50 日拟合，后 10 日选择候选及 ridge 参数，再在过去 60 日重拟合并测试下一日。
每腿 beta 限制在 0—2、合计不超过 2.5，最多两腿，允许不对冲。同指数工具属于互斥组：不能靠同时选恒科期货和恒科 ETF 假装增加一个独立因子。
已完成 5 项单元测试（含独立优化器对照、未来数据扰动、日界/合约界、无后向填充、日内 FX 抵消）以及 3 个真实基金分钟点的独立标量估值复算。
滚动切分、逐日 beta、原始/残差标签及质量拒绝记录均已保存；置信区间按完整交易日重采样。

完整复跑：`scripts/extract_pilot_remote.py` → `collect_hti.py` → `run_pilot.py` → `validate_pilot.py` → `build_deliverables.py`。代码、路径和锁定口径见工程 README 与 `docs/STANDARD_WORKFLOW.md`。

## 使用范围

可用于决定下一阶段优先研究哪些对冲工具；尚不用于判断真实套利盈利、可成交容量、期权尾部封顶或全天/隔夜效果。
下一轮优先扩展早盘和额外时间段验证，并在候选收敛后加入真实成本；不因本轮结论重新索要 tick 或成交量数据。

## 来源

- [上交所参考汇率与结算汇兑比率](https://www.sse.com.cn/services/hkexsc/disclo/ratios/)，本地历史文件来源路径已保留在 `data/raw/local_source_manifest.json`。
- [亿华通停牌公告](https://www.hkexnews.hk/listedco/listconews/sehk/2026/0401/2026033101853.pdf)、[复牌公告](https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0504/2026050400055.pdf)。
- [东风公司行动及最后交易日](https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2026/MO_DT_050_26_e.pdf)。
- [禾赛拆股生效公告](https://investor.hesaitech.com/static-files/cc419257-d34c-4335-9101-77d5deb95aac)。
- [吉利分红公告](https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0318/2026031800313.pdf)，核实普通分红事件的示例。
'''
    (ROOT/'reports/520600_首轮结果.md').write_text(report)
    n=nbformat.v4.new_notebook();n.metadata={'kernelspec':{'display_name':'Python 3','language':'python','name':'python3'}}
    n.cells=[nbformat.v4.new_markdown_cell('# 520600 首轮研究复核\n\n## tl;dr\nHHI＋HTI 在本轮 30 分钟外测中将残差波动降至约 19.43bp；结果为价格风险研究，不含费用。'),
        nbformat.v4.new_markdown_cell('## Context & Methods\n97 个可用日、滚动 60 日训练、37 日外测。\n\n### Key Assumptions\n一分钟 LAST、强制现金替代、日终结算率仅用于人民币事后核算；只研究同日下午，不跨日或合约。'),
        nbformat.v4.new_code_cell("from pathlib import Path\nimport json, pandas as pd, numpy as np\nroot=Path.cwd()\nif not (root/'reports').exists(): root=root.parent\nm=pd.read_csv(root/'reports/model_comparison.csv')\nfolds=pd.read_csv(root/'reports/folds.csv')\nres=pd.read_parquet(root/'reports/oos_residuals.parquet')\nprint('Folds:',len(folds),'OOS days:',res.date.nunique())"),
        nbformat.v4.new_markdown_cell('## Data\n切分与样本一致性检查。'),
        nbformat.v4.new_code_cell("assert (folds.train_end<folds.test_date).all()\nfor h,part in res.groupby('horizon'):\n    counts=part.groupby('model').size()\n    assert counts.nunique()==1\n    print(h,'min:',counts.iloc[0],'labels per method')"),
        nbformat.v4.new_markdown_cell('## Results\n从保存的逐样本残差独立重算关键指标。'),
        nbformat.v4.new_code_cell("p=res[(res.horizon==30)&(res.model=='HHI_HTI_fixed_pair')]\nvalue=1-p.residual.var()/p.y.var()\nexpected=m[(m.horizon==30)&(m.model=='HHI_HTI_fixed_pair')].iloc[0]\nassert abs(value-expected.variance_reduction)<1e-12\nprint('30m residual std bp:',p.residual.std()*10000)\nprint('30m variance reduction:',value)\nm[m.horizon==30][['model','residual_std_bps','variance_reduction']].sort_values('residual_std_bps')"),
        nbformat.v4.new_code_cell("from IPython.display import Image,display\ndisplay(Image(filename=str(root/'reports/residual_risk.png')))"),
        nbformat.v4.new_code_cell("s=pd.read_csv(root/'reports/sensitivity.csv')\ns[(s.horizon==30)&(s.model=='HHI_HTI_fixed_pair')][['scenario','days','samples','variance_reduction']]"),
        nbformat.v4.new_markdown_cell('## Takeaways\n先保留简单 HHI＋HTI 与 HTI 单腿对照；复杂选择未稳定胜出。正式结果、冻结停牌权重、三天复杂权益剔除和事件源缺口详见 reports/520600_首轮结果.md。')]
    nbformat.validate(n);NotebookClient(n,timeout=60,kernel_name='python3',resources={'metadata':{'path':str(ROOT)}}).execute()
    nbformat.write(n,ROOT/'notebooks/520600_research.ipynb')
    print('report, PNG, and 5 executed notebook cells written')


if __name__=='__main__':main()
