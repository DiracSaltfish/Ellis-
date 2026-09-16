from pathlib import Path
import json,sys,gzip,hashlib,platform
import numpy as np,pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
R=Path(__file__).resolve().parent;F=R.parent.parent;N=R.parent/'native';sys.path.insert(0,str(N/'build'));import etf_l2
FONT='/System/Library/Fonts/Supplemental/Arial Unicode.ttf'
if Path(FONT).exists():font_manager.fontManager.addfont(FONT);plt.rcParams['font.family']=font_manager.FontProperties(fname=FONT).get_name()
plt.rcParams.update({'axes.unicode_minus':False,'font.size':11,'axes.spines.top':False,'axes.spines.right':False,'axes.grid':True,'grid.alpha':.18,'figure.facecolor':'white'})
BLUE='#2563a7';GOLD='#b98209';TEAL='#087f80';GRAY='#697582';ORANGE='#c9692a'

def pct(x):return f'{x*100:.1f}%'
def main():
 m=json.loads((R/'metrics.json').read_text());meta=json.loads((R/'dataset_summary.json').read_text());split=json.loads((R/'split.json').read_text());p=pd.read_parquet(R/'predictions.parquet');z=p[(p.split=='test')&(p.cutoff=='14:45')&(p.variant=='premium_l2')].copy();figdir=R/'figures';figdir.mkdir(exist_ok=True)
 fig,axes=plt.subplots(1,2,figsize=(13,4.7),layout='constrained')
 names=['恒预测零变化','溢价＋历史份额','再加L2'];acc=[m['14:45_premium_l2']['test']['always_flat_accuracy'],m['14:45_premium']['test']['accuracy'],m['14:45_premium_l2']['test']['accuracy']];mae=[m['14:45_premium_l2']['test']['zero_mae_baskets'],m['14:45_premium']['test']['mae_baskets'],m['14:45_premium_l2']['test']['mae_baskets']]
 for ax,values,title,unit in [(axes[0],np.array(acc)*100,'三分类方向准确率（越高越好）','%'),(axes[1],mae,'净申赎数量平均绝对误差（越低越好）','篮子')]:
  bars=ax.bar(names,values,color=[GRAY,BLUE,TEAL],width=.6);ax.set_title(title,pad=15);ax.set_ylabel(unit);ax.set_ylim(0,max(values)*1.23)
  for b,v in zip(bars,values):ax.text(b.get_x()+b.get_width()/2,v+max(values)*.035,f'{v:.2f}',ha='center')
 fig.suptitle('14:45 历史外推测试｜2026-04-22 至 04-27，4日、592基金日',fontsize=15);fig.savefig(figdir/'holdout_comparison.png',dpi=160);plt.close(fig)
 fig,ax=plt.subplots(figsize=(8,6),layout='constrained');c=z.p_create>=.9
 ax.scatter(z.loc[~c,'net_baskets'],z.loc[~c,'pred_baskets'],s=18,alpha=.35,color=GRAY,label='其他样本');ax.scatter(z.loc[c,'net_baskets'],z.loc[c,'pred_baskets'],s=40,facecolors='none',edgecolors=BLUE,label='模型净申购概率 ≥90%')
 lim=max(abs(z.net_baskets).max(),abs(z.pred_baskets).max())*1.2;ax.plot([-lim,lim],[-lim,lim],color='#333333',linestyle='--',lw=1,label='完全准确');ax.axhline(0,color=GRAY,lw=.8);ax.axvline(0,color=GRAY,lw=.8);ax.set_xscale('symlog',linthresh=5);ax.set_yscale('symlog',linthresh=5);ax.set_xlim(-lim,lim);ax.set_ylim(-lim,lim);ax.set_xlabel('实际净申赎篮子数（正申购、负赎回）');ax.set_ylabel('预测净申赎篮子数');ax.set_title('数量预测仍存在明显偏差｜592基金日\n两轴同尺度，±5篮子内线性、之外对称对数');ax.legend(loc='upper left');fig.savefig(figdir/'quantity_scatter.png',dpi=150);plt.close(fig)
 cases=[('2026-04-23','513010.SH','方向与数量较接近'),('2026-04-23','159297.SZ','方向正确、低估数量'),('2026-04-24','513630.SH','高概率信号却净赎回'),('2026-04-27','159170.SZ','预测净申购、实际零变化')];case_rows=[]
 for date,sym,caption in cases:
  row=z[(z.date==date)&(z.symbol==sym)].iloc[0];day=date.replace('-','');s=pd.read_parquet(F/'results/series'/f'{day}.parquet');s=s[s.symbol.eq(sym)].copy();s['minute_id']=s.minute.map(lambda t:int(t[:2])*60+int(t[3:]));s=s[((s.minute_id>=571)&(s.minute_id<=690))|((s.minute_id>=781)&(s.minute_id<=885))].set_index('minute_id');raw=Path('/Volumes/EllisFiles/Stocksdata/A股逐笔/港股通ETF保留')/day/sym
  r=etf_l2.process_files(str(raw/'逐笔成交.csv'),str(raw/'逐笔委托.csv'),str(raw/'行情.csv'),int(sym[:6]),int(day),int(row.unit),cutoff='14:45');minute=pd.DataFrame(r['minutes']).set_index('completed_minute');d=s.join(minute,how='left');d[['buy_notional_x10000','sell_notional_x10000']]=d[['buy_notional_x10000','sell_notional_x10000']].fillna(0);d['settlement_premium_bp']=(d.etf/d.lag_settlement-1)*10000;d.to_csv(figdir/f'{day}_{sym}_source.csv')
  fig,ax=plt.subplots(3,1,figsize=(12,9),sharex=True,layout='constrained',gridspec_kw={'height_ratios':[1.5,1,1]})
  fig.set_constrained_layout_pads(w_pad=.15,h_pad=.1)
  for low,high in [(571,690),(781,885)]:
   q=d.loc[low:high]
   for col,label,color,style in [('etf','ETF成交价',BLUE,'-'),('mid','中间价IOPV',GOLD,'-'),('lag_settlement','结算IOPV代理（前日汇率）',TEAL,'--')]:ax[0].plot(q.index,q[col],color=color,ls=style,lw=1.6,label=label if low==571 else None)
   ax[1].plot(q.index,q.settlement_premium_bp,color=TEAL,lw=1.4)
  ax[0].set_ylabel('人民币 / 份');ax[0].legend(loc='upper left',ncol=3,fontsize=10);ax[1].set_ylabel('相对结算代理溢价 / bp');ax[1].axhline(30,color=GRAY,ls='--',lw=.9);ax[1].axhline(0,color=GRAY,lw=.8)
  cum_buy=d.buy_notional_x10000.cumsum()/1e12;cum_sell=d.sell_notional_x10000.cumsum()/1e12
  for low,high in [(571,690),(781,885)]:
   idx=d.loc[low:high].index;ax[2].plot(idx,cum_buy.loc[idx],color=BLUE,label='累计主动买入' if low==571 else None);ax[2].plot(idx,cum_sell.loc[idx],color=ORANGE,ls='--',label='累计主动卖出' if low==571 else None)
  ax[2].set_ylabel('连续竞价成交额 / 亿元');ax[2].legend(loc='upper left',ncol=2,fontsize=10)
  for a in ax:
   a.axvspan(690,781,color='#eeeeee');a.axvline(870,color=GRAY,ls=':',lw=1);a.set_xlim(571,885)
  ax[2].set_xticks([571,630,690,781,840,870,885],['09:31','10:30','11:30','13:01','14:00','14:30','14:45']);ax[2].set_xlabel('已完成交易分钟；灰色为午休；各ETF价格、成交额使用各自尺度')
  title=f'{sym} · {date}｜{caption}\n14:45预测 {row.pred_baskets:+.1f} 篮子；实际 {row.net_baskets:+.0f}；模型净申购概率 {row.p_create:.1%}'
  fig.suptitle(title,fontsize=15);fig.savefig(figdir/f'{day}_{sym}.png',dpi=150);plt.close(fig)
  case_rows.append(dict(date=date,symbol=sym,caption=caption,p_create=row.p_create,pred_shares=row.pred_shares,net_shares=row.net_shares,pred_baskets=row.pred_baskets,net_baskets=row.net_baskets,lo_baskets=row.lo_baskets,hi_baskets=row.hi_baskets,image=f'figures/{day}_{sym}.png'))
 pd.DataFrame(case_rows).to_csv(R/'cases.csv',index=False)
 # Persist exact source/code fingerprints without copying raw L2 datasets.
 receipt=dict(python=platform.python_version(),native_version=etf_l2.__version__,sources={},code={},source_scope='retained L2 file CRC/size in frozen manifests; cached 1navs share histories; PCF-derived baskets; reconstructed minute series')
 for entry in json.loads((R/'date_snapshot.json').read_text()):
  day=entry['date']
  for file in [F/'inputs/baskets'/f'{day}.json.gz',F/'results/series'/f'{day}.parquet']:receipt['sources'][str(file)]=hashlib.sha256(file.read_bytes()).hexdigest()
 for sym in pd.read_parquet(R/'panel.parquet').symbol.unique():
  file=F/'inputs/share_history'/f'{sym}.json';receipt['sources'][str(file)]=hashlib.sha256(file.read_bytes()).hexdigest()
 for file in list(R.glob('*.py'))+list((N/'src').glob('*.cpp'))+list((N/'include').glob('*.hpp'))+[N/'batch/intraday.py',F/'build_panel.py',R.parent/'labels_calendar.py']:
  receipt['code'][str(file)]=hashlib.sha256(file.read_bytes()).hexdigest()
 (R/'source_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
 lines=['# 2026年L2净申赎方向与数量：首次严格日期外推检验','',
 '结论：可以输出“净申购多少份”的数值，但当前数量误差尚未达到实用水平。加入L2后，全样本方向判断没有稳定改善。验证集选择的一组14:45严格信号在测试集得到9/10的净申购命中率；样本太小，不能认定已实现稳定90%准确率，更不能换成90%赚钱胜率。','',
 '## 数据和检验口径','',
 f"- 本次冻结25个已验证L2日期。其中24日具备可用联合估值数据，共170只ETF、3,481基金日、6,962条截断记录。14:30和14:45是同一批基金日的两个版本，不是两倍独立样本。",
 '- 2026-01-15剔除：多个港股分钟文件只含15:21以后数据，例如03690.HK仅40行，无法重建ETF交易时段的完整IOPV；缺的是当天早盘至15:00的相关港股分钟数据。其余441个基金日因缺兼容PCF、估值序列或合格份额标签而不进入样本，详见 exclusions.json。',
 '- 上海按照六位代码识别，纠正原文件.SZ后缀；空成交代码兼容单个NUL。保留订单原量不确定性，不把逐笔委托量直接当成所有上海母单原量。',
 '- 标签为1navs当日盘后份额减上一实际交易日份额，单位换算为份；当日PCF最小申赎单位换算为篮子。只观察净量，不假装知道双边总申赎量。',
 '- 14:30使用09:31—11:30及13:01—14:30共210个已完成分钟，14:45为225个。L2严格截断到时点之前；不使用15:00—16:00数据，不使用当天最终份额或最终汇率作特征。',
 '- 估值每日固定用上一交易日结算汇率作盘中代理，辅以当日中间价。历史实际接收时间、前日结算汇率发布可知时间未逐条复原，属于可用性假设；不是已验证的QMT实时效果。与早期使用当日最终汇率的事后研究口径不同。',
 f"- 训练：{split['train'][0]}—{split['train'][-1]}，16日、2,293基金日；验证：{split['validation'][0]}—{split['validation'][-1]}，4日、596基金日；测试：{split['test'][0]}—{split['test'][-1]}，4日、592基金日（292沪市、300深市）。中间日期缺口较大。",
 '- 固定参数的梯度提升分类与回归；训练集拟合，验证集做温度校准、区间校准及阈值选择，测试集只评估。没有随机把同一天ETF拆开，也没有在测试集寻找最好阈值。','',
 '## 方向判断','',
 '| 时点 | 输入 | 三分类准确率 | 净申购AP | 模型概率≥90%的实际命中 | 信号覆盖率 |', '|---|---|---:|---:|---:|---:|']
 for cutoff in ['14:30','14:45']:
  for variant,label in [('premium','溢价＋历史份额等'),('premium_l2','上述输入＋L2')]:
   a=m[cutoff+'_'+variant]['test'];g=a['fixed_p90'];lines.append(f"| {cutoff} | {label} | {pct(a['accuracy'])} | {a['create_ap']:.3f} | {pct(g['precision'])}（{round(g['n']*g['precision'])}/{g['n']}） | {pct(g['coverage'])} |")
 lines+=['','测试集实际净申购100、零变化358、净赎回134。始终预测零变化也有60.5%准确率；AP衡量净申购排序质量，不能当作命中率。14:45加入L2后的三分类混淆矩阵如下（行实际、列预测）：','', '| | 预测赎回 | 预测零变化 | 预测申购 |','|---|---:|---:|---:|','| 实际赎回 |58|71|5|','| 实际零变化 |21|293|44|','| 实际申购 |0|28|72|','',
 '固定“概率≥90%”时，14:45 L2信号实际13/15为净申购，另1个零变化、1个净赎回；模型给出90%并不表示真实命中率已经90%。','',
 '验证阶段另按“至少20个信号、3个日期、5只基金，验证命中率≥90%”选最大覆盖阈值。14:45 L2选出0.92，测试命中9/10，覆盖1.69%，仅5只基金、3日。普通二项Wilson 95%区间约59.6%—98.2%；ETF同日相关使独立样本假设也偏乐观。14:30对应验证阈值0.88，测试仅14/20=70%。两时点均报告，不能只挑9/10的结果。','',
 'L2增量并不稳健：14:45 AP从0.706降到0.701；按日期聚类重采样的AP增量描述区间为-0.032至+0.030，数量MAE改善区间为-0.41至+0.82篮子，均跨零。只有4个测试日期，这些区间本身也不宜过度解释。','',
 '![外推比较](figures/holdout_comparison.png)','',
 '## 能否预估净申购xxx份','',
 '已输出每基金的净申赎点估计、份额数、篮子数、净流入占前日份额比例，以及名义80%预测区间。点估计来自asinh变换后的净流入比例回归再反变换，不声称是经过校准的条件均值；预测可为小数篮子，实际标签按整数篮子核验。','',
 '| 14:45方法 | 平均绝对误差：份 | 平均绝对误差：篮子 |','|---|---:|---:|']
 for label,a,zero in [('恒预测零',m['14:45_premium_l2']['test'],True),('溢价等基础模型',m['14:45_premium']['test'],False),('再加L2',m['14:45_premium_l2']['test'],False)]:lines.append(f"| {label} | {a['zero_mae_shares' if zero else 'mae_shares']/10000:,.1f}万 | {a['zero_mae_baskets' if zero else 'mae_baskets']:.2f} |")
 lines+=['','**数量模型整体仍差于预测零，暂不适合按点估计决定投入篮子数。** L2模型平均误差1,216.6万份、13.41篮子；中位误差1.26篮子说明误差受部分大偏差影响。实际非零的234个样本中，MAE从零基线25.61降到24.54篮子，仅小幅改善；“实际非零”是事后分组，不能盘中据此过滤。',
 '592个基金日的预测净份额合计约+32.60亿，实际为-2.705亿，存在明显正向偏差。训练期间大额净申购较多、测试期间状态不同是一个待进一步验证的解释，不能简单用测试结果反向修正模型再声称样本外有效。',
 '名义80%区间在测试覆盖80.2%，平均宽度为前日份额的1.275个百分点。覆盖接近名义水平不代表数量很准；不少区间很宽。校准只用4日验证集，时间分布变化下没有未来覆盖保证。','',
 '![数量散点](figures/quantity_scatter.png)','',
 '| 日期 / ETF | 预测净份额 | 实际净份额 | 预测 / 实际篮子 | 模型申购概率 | 名义80%篮子区间 |','|---|---:|---:|---:|---:|---:|']
 for c in case_rows:lines.append(f"| {c['date']} / {c['symbol']} | {c['pred_shares']/10000:+,.1f}万 | {c['net_shares']/10000:+,.0f}万 | {c['pred_baskets']:+.1f} / {c['net_baskets']:+.0f} | {pct(c['p_create'])} | [{c['lo_baskets']:.1f}, {c['hi_baskets']:.1f}] |")
 lines+=['','这些案例为测试后的解释性选择，特意同时展示成功与失败，不代表总体表现。513630在4月24日预测约净申购5,801万份，实际净赎回400万份，且落在区间之外；这是严格信号中那一次方向错误。','',
 '## 因子分析：哪些想法有依据','',
 '1. **单独看主买主卖净额不够。** 按训练集固定边界分组，主动净买入占比最低至最高五组，测试净申购率约16.4%、16.5%、16.3%、16.9%、20.0%，没有足够强的单调关系。二级市场每笔成交都有买卖两方，主动方向不能直接等同一级申赎方向。',
 '2. **“有溢价、随后回落”比单独主卖强弱更有信息。** 以此前一分钟结算代理溢价>30bp、相邻一分钟溢价下降时的成交额，除以全天截至时点的成交额。卖方成交占比超过训练80%分位（约1.03%）的测试组，净申购率39.9%（208个样本），低组4.4%（384个）。但高组仍有9.1%净赎回，其余大量零变化，不能把压溢价动作直接当作已发生申购。',
 '3. **这主要是溢价与成交时段信息，不宜归功于“主卖即申购”。** 同一回落条件下的主动买入占比也得到近似区分：高组39.4%、低组4.2%。两侧都有效，说明溢价状态及成交活跃时段可能是共同来源。L2加入已有溢价因子后的总体增益并不显著。',
 '4. **接近整篮子有一些关联，但不能按它直接数申购。** 主动卖出母单的整篮子超额指标：真实PCF单位附近±1%的成交额占比，减去0.8/1.2/1.3倍单位的对照均值。训练最高组净申购率42.5%，测试降到24.1%；最低组测试12.1%。有信号衰减，也没有足够的概率精度。短成交簇使用先按时间、方向、价格分段再测篮子距离的方式，避免为了凑篮子而分段。',
 '5. **当前应保留为辅助特征。** 日期内置换诊断中，14:45较重要的仍是中间价最大溢价、前日/前5日净份额变化、基金规模；L2中回落阶段成交占比较有贡献。置换重要性受相关特征影响，只表示这个模型的依赖，不能当因果解释。详细分桶与重要性已导出CSV。','',
 '## 四个日内案例','']
 for c in case_rows:lines += [f"### {c['date']} {c['symbol']}：{c['caption']}",'',f"![{c['caption']}]({c['image']})",'']
 lines+=['## 实现、验证与下一步边界','',
 '- C++核心、pybind11、文件批量入口与研究模型已落地。QMT传输按要求暂未接入；研究非线性模型使用 study/score.py，不能直接放入旧线性JSON模型接口。',
 '- 核心与Python/批量三组CTest通过；本机AddressSanitizer/UBSan核心测试通过。真实数据独立pandas核验覆盖9个1月全日基金样本及9个2月/4月14:45截断样本，逐订单8字段及成交数量、成交金额一致。它验证计算，不验证预测胜率。',
 '- 另发现2026-02-02 513330行情累计成交量存在32位回绕：4,293,950,856之后变为337,760，全天行情量较逐笔合计少2^32。本次不擅自修改源文件；该日全日行情对账失败会阻断对应质量标志。14:45逐笔计算经独立聚合核验；陈旧快照不被冒充完全对账。',
 '- 研究使用现有网站目标集合及兼容PCF，存在范围选择/存续偏差；不能泛化到所有港股通产品。沪深分别测试结果见 metrics.json。',
 '- 后续应把当前模型和阈值冻结在新增、连续的日期上继续检验，重点补充不同净流入状态；先验证方向与数量，不按这批测试集的失败案例反复调参。今日实际数量仍需当日真实L2、PCF、分钟估值和前日份额，当前文件仅是历史回测。',
 '- 净申购方向正确≠赚取轧差利润；本次没有不可观测的双边总量、实际退补款和完整成本，因此不计算或宣称真实套利胜率。','',
 '## 文件索引','',
 '- predictions.csv / predictions.parquet：验证集及测试集逐条概率、点估计、区间、实际标签，含两时点与两模型；筛选 split=test、variant=premium_l2、cutoff=14:45 查看主结果。',
 '- metrics.json、split.json、bootstrap.json：完整指标、日期划分、日期聚类重采样。',
 '- panel.parquet、date_snapshot.json、source_receipt.json、exclusions.json：因子、冻结输入及审计。',
 '- factor_buckets.csv、permutation_importance.csv、validation_thresholds.csv：分桶、增量诊断、仅验证集的阈值搜索。',
 '- models/*.joblib、score.py：已冻结的研究模型和批量数值预测入口。',
 '- figures/*_source.csv：案例图的逐分钟可核对底表。',
 '']
 (R/'L2方向与数量回测报告.md').write_text('\n'.join(lines));print('report and figures written')
if __name__=='__main__':main()
