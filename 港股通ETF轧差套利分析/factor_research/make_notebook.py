"""生成并执行可复算的结果审阅笔记，含三序列图和因子分桶图。"""
from pathlib import Path
import json, os, sys
import nbformat as nbf
from nbclient import NotebookClient

R=Path(__file__).resolve().parent
nb=nbf.v4.new_notebook()
md=nbf.v4.new_markdown_cell; code=nbf.v4.new_code_cell
nb.cells=[
md('# 港股通ETF：日内价差与当日净新增份额\n\n研究区间2025-07至2026-06。用户指定当日最终结算率固定全天；不估计隐藏双边申赎量。结果与完整方法见同目录研究报告。'),
md('## 方法与来源\n\n主标签：净增至少10篮子，且至少为前一日份额的0.5%。训练期2025年7—12月，验证期2026年1—3月，测试期2026年4—6月。网站份额日期直接对应业务日，不作平移。\n\n股票分钟、ETF分钟、PCF路径和排除规则见README.md；汇率源为中国货币网中间价及沪深交易所日终结算汇兑比率。完整原始行情在machome处理。结果使用当前站内标的名单，存在存续标的选择偏差。'),
code("from pathlib import Path\nimport json\nimport numpy as np\nimport pandas as pd\nimport matplotlib.pyplot as plt\nfrom matplotlib import font_manager\nR=Path.cwd()\nO=R/'results'\nplt.rcParams.update({'figure.figsize':(12,5), 'font.size':11, 'axes.spines.top':False, 'axes.spines.right':False})\nfont=Path('/System/Library/Fonts/Supplemental/Arial Unicode.ttf')\nif font.exists():\n    font_manager.fontManager.addfont(str(font))\n    plt.rcParams['font.family']=font_manager.FontProperties(fname=str(font)).get_name()\nplt.rcParams['axes.unicode_minus']=False\naudit=json.loads((O/'panel_audit.json').read_text())\nprint({k:audit[k] for k in ['rows','funds','dates']})"),
md('## 样本和基准\n\n每行是一个基金-交易日，纳入全天所有有效重叠交易分钟；本轮共32,447个基金日、7,787,280个分钟观测。'),
code("baseline=pd.read_csv(O/'baselines.csv')\ndisplay(baseline[baseline.split=='test'][['cut','n','funds','days','positive_rate','large_rate','negative_rate']].round(4))"),
md('## 三组序列样例\n\n159570.SZ，2025-07-02，固定当日最终汇率。时间戳保守延后一分；午休与闭市后ETF价格留空。此图用于核对重建，不是事后挑选的盈利交易。'),
code("sample_path=O/'series'/'20250702.parquet'\nif sample_path.exists():\n    s=pd.read_parquet(sample_path)\n    s=s[s.symbol=='159570.SZ'].copy()\n    s.to_csv(O/'sample_159570_20250702.csv',index=False)\nelse:\n    s=pd.read_csv(O/'sample_159570_20250702.csv')\nassert len(s)>0\nx=pd.to_datetime(s['date']+' '+s.minute)\nfig,ax=plt.subplots(figsize=(12,5))\nfor field,label,color,style in [('mid','中间价 IOPV','#b47b00','-'),('actual_settlement_buy','结算价 IOPV（买港股）','#008575','--'),('etf','国内 ETF 价','#2864dc','-')]:\n    ax.plot(x,s[field],label=label,color=color,linestyle=style,linewidth=1.6)\nax.set_title('159570.SZ · 2025-07-02 · 当日固定汇率重建')\nax.set_ylabel('人民币元 / 基金份额')\nax.grid(axis='y',alpha=.2)\nax.legend(loc='upper left')\nimport matplotlib.dates as mdates\nax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))\nax.set_xticks(pd.to_datetime(['2025-07-02 '+t for t in ['09:30','10:30','11:30','13:00','14:00','15:00','16:08']]))\nax.set_xlim(x.iloc[0],x.iloc[-1])\nfig.tight_layout()\nfig.savefig(O/'sample_three_series.png',dpi=160)\nplt.show()"),
md('## 结算溢价因子的跨期分桶\n\n分桶边界只用训练期确定；下面展示全天路径在测试期的各桶命中率。每桶分母为该桶基金日数量。'),
code("buckets=pd.read_csv(O/'factor_buckets.csv')\nb=buckets[(buckets.cut=='all_day')&(buckets.feature=='settlement_mean_bp')&(buckets.split=='test')].copy()\nassert len(b)>0\nfig,ax=plt.subplots(figsize=(12,5))\nax.bar(np.arange(len(b)),b.large_rate*100,color='#287f78')\nbase=baseline[(baseline.cut=='all_day')&(baseline.split=='test')].large_rate.iloc[0]*100\nax.axhline(base,color='#555555',linestyle='--',label=f'全样本基准 {base:.1f}%')\nax.set_xticks(np.arange(len(b)),[str(t)+'\\nn='+str(n) for t,n in zip(b.bucket,b.n)])\nax.set_xlabel('全天平均结算溢价分桶（基点；训练期边界）')\nax.set_ylabel('大量净申购占比（%）')\nax.set_title('2026年4—6月测试期 · 结算溢价与大量净申购')\nax.set_ylim(0,max(30,(b.large_rate.max()*100+5)))\nax.legend()\nfig.tight_layout()\nfig.savefig(O/'factor_bucket_test.png',dpi=160)\nplt.show()\ndisplay(b[['bucket','n','funds','large_rate','positive_rate','negative_rate']].round(4))"),
md('## 验证期选定条件的测试结果\n\n规则与模型的选择均不使用测试期标签；不是将全部条件中的测试最好值当作结果。概率区间按交易日聚类重采样。'),
code("choices=json.loads((O/'selected_conditions.json').read_text())\nsummary=[]\nfor r in choices:\n    summary.append(dict(cut=r['cut'],kind=r['kind'],name=r['name'],**r['test']))\ndisplay(pd.DataFrame(summary).round(4))"),
md('## 数据核对与限制\n\n实际结算率是用户指定的日内固定事后参数，所以时间切分证明的是跨期条件关联，不能直接宣称盘中可执行或已经取得套利收益。多数新基金历史不足一年；缺少成分或标签的基金日明确剔除。'),
code("q=pd.read_csv(O/'pcf_nav_reconciliation.csv')\nprint('上一日净值复核绝对偏差（基点）')\ndisplay(q.difference_bp.abs().quantile([.5,.9,.95,.99]).round(3))\nprint('缺失/异常排除统计')\ndisplay(pd.DataFrame(list(audit['excluded_reasons'].items()),columns=['reason','count']))")]
nb.metadata['kernelspec']={'display_name':'HK ETF Research','language':'python','name':'research'}
nb.metadata['language_info']={'name':'python','version':sys.version.split()[0]}
j=R/'.jupyter';kernel=j/'kernels/research';kernel.mkdir(parents=True,exist_ok=True)
(kernel/'kernel.json').write_text(json.dumps(dict(argv=[sys.executable,'-m','ipykernel_launcher','-f','{connection_file}'],
    display_name='HK ETF Research',language='python')))
os.environ['JUPYTER_PATH']=str(j)
nbf.validate(nb)
client=NotebookClient(nb,timeout=120,kernel_name='research',resources={'metadata':{'path':str(R)}})
client.execute()
nbf.write(nb,R/'研究复核.ipynb')
print('Notebook executed:',R/'研究复核.ipynb')
