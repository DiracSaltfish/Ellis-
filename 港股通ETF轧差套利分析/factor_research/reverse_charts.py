from pathlib import Path
import json
import numpy as np,pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
R=Path(__file__).resolve().parent;D=R/'reverse';O=D/'results'
font_manager.fontManager.addfont('/System/Library/Fonts/Supplemental/Arial Unicode.ttf')
plt.rcParams.update({'font.family':'Arial Unicode MS','axes.unicode_minus':False,'font.size':11,'axes.spines.top':False,'axes.spines.right':False})
labels={'large_create':'大量净申购','small_create':'小额净申购','flat':'份额不变','small_redeem':'小额净赎回','large_redeem':'大量净赎回'}
colors={'large_create':'#007f78','small_create':'#57a7a1','flat':'#777777','small_redeem':'#c78868','large_redeem':'#ad492b'}
profiles=np.load(D/'reverse_profiles.npz')
fig,axs=plt.subplots(3,1,figsize=(13,10),sharex=True)
for group,label in labels.items():
    a=profiles[group+'_median'];n=int(profiles[group+'_n'])
    for i in range(3):axs[i].plot(a[i]*(100 if i==2 else 1),label=f'{label}（n={n:,}）',color=colors[group],ls='--' if group.startswith('small') else '-',lw=1.7)
axs[0].set_ylabel('结算溢价中位数 / bp');axs[1].set_ylabel('篮子日内涨幅中位数 / bp');axs[2].set_ylabel('累计成交额占比中位数 / %')
axs[0].legend(loc='lower left',bbox_to_anchor=(0,1.02),ncol=3,frameon=False)
for ax in axs:ax.grid(axis='y',alpha=.2);ax.axvline(119.5,color='#bbbbbb',ls=':');ax.set_xlim(0,239)
axs[0].axhline(0,color='#aaaaaa',lw=.8);axs[1].axhline(0,color='#aaaaaa',lw=.8)
axs[2].set_xticks([0,59,119.5,179,239],['09:31','10:30','11:30 / 13:01','14:00','15:00']);axs[2].set_xlabel('原始行情时间；只含有效交易分钟，午休压缩')
fig.suptitle('从盘后净份额反查全天路径 · 2025年7—12月发现样本',fontsize=16,y=.99)
fig.text(.09,.01,'各组为基金日等权的逐分钟中位数，不是一只可交易基金的路径；分组使用全日结果，仅用于因子发现。',fontsize=10)
fig.tight_layout(rect=[0,.025,1,.95]);fig.savefig(O/'reverse_paths.png',dpi=160);plt.close(fig)

features={'settlement_mean_bp':'平均结算溢价','settlement_p10_bp':'溢价10%分位数','positive_amount_share':'正溢价成交额占比','creation_pressure':'申购方向压力','redemption_pressure':'赎回方向压力','compression_turnover_pct':'溢价回落成交强度','turnover_pct':'成交额 / 昨日资产','lag_flow_pct':'前一日净份额变化','premium_vs_20d_bp':'较自身20日溢价变化','relative_turnover_20d':'较自身20日成交强度'}
d=pd.read_parquet(O/'enriched_panel.parquet');tr=d[d.date<'2026-01-01'];matrix=[]
for group in labels:
    g=tr[tr.cohort==group];line=[]
    for f in features:
        scale=tr[f].quantile(.75)-tr[f].quantile(.25)
        line.append((g[f].median()-tr[f].median())/(scale if scale>0 else 1))
    matrix.append(line)
fig,ax=plt.subplots(figsize=(14,5));a=np.array(matrix);im=ax.imshow(a,cmap='RdBu',vmin=-2,vmax=2,aspect='auto')
ax.set_yticks(range(5),list(labels.values()));ax.set_xticks(range(len(features)),list(features.values()),rotation=25,ha='right')
for i in range(5):
    for j in range(len(features)):ax.text(j,i,f'{a[i,j]:.2f}',ha='center',va='center',color='white' if abs(a[i,j])>1.2 else '#222222',fontsize=10)
fig.colorbar(im,ax=ax,label='相对全体中位数的偏离 / 四分位距；颜色截在±2')
ax.set_title('净申赎分组的因子共性 · 仅使用2025年发现样本',pad=15);fig.tight_layout();fig.savefig(O/'reverse_factor_heatmap.png',dpi=160);plt.close(fig)

m=pd.read_csv(O/'forward_models.csv');m=m[m.period=='fresh'];names={'history_only':'历史净流量','simple_levels':'均值等基础因子','prior_full':'此前全部因子','enriched':'反向筛选后因子'}
fig,axs=plt.subplots(1,2,figsize=(13,5.5))
for ax,target,title in zip(axs,['flow_positive','large_positive'],['识别净申购','识别大量净申购']):
    g=m[(m.target==target)&m.model.isin(names)].set_index('model').loc[list(names)]
    ax.bar(range(4),g.precision*100,color=['#aab4be','#7f9ea6','#548a96','#007f78'])
    ax.set_xticks(range(4),list(names.values()),rotation=15,ha='right');ax.set_ylim(0,100);ax.set_ylabel('入选样本命中率 / %')
    ax.axhline(g.base_rate.iloc[0]*100,color='#ad492b',ls='--',label=f'全体基准 {g.base_rate.iloc[0]*100:.1f}%')
    for i,(_,r) in enumerate(g.iterrows()):ax.text(i,r.precision*100+2,f'{r.precision*100:.1f}%\nn={int(r.n)}',ha='center',fontsize=10)
    ax.set_title(title);ax.legend(loc='upper left',frameon=False)
fig.suptitle('新增验证段 · 2026年7月至8月3日 · 用6月评分阈值固定筛选',fontsize=14);fig.tight_layout();fig.savefig(O/'fresh_validation.png',dpi=160);plt.close(fig)

rolling=pd.read_csv(O/'rolling_months.csv');fig,axs=plt.subplots(1,2,figsize=(13,5))
for ax,target,title in zip(axs,['flow_positive','large_positive'],['净申购命中率','大量净申购命中率']):
    for name,color in [('history_only','#888888'),('simple_levels','#548a96'),('enriched','#007f78')]:
        g=rolling[(rolling.target==target)&(rolling.model==name)]
        ax.plot(g.month,g.precision*100,marker='o',label=names[name],color=color)
    b=rolling[(rolling.target==target)&(rolling.model=='enriched')];ax.plot(b.month,b.base_rate*100,color='#ad492b',ls='--',label='当月全体基准')
    ax.set_title(title);ax.set_ylim(0,100);ax.tick_params(axis='x',rotation=20);ax.set_ylabel('%');ax.grid(axis='y',alpha=.2)
axs[0].legend(loc='lower left',fontsize=10)
fig.suptitle('历史逐月前推 · 每次只训练过去、以前月评分固定阈值（这些月份此前已看过）',fontsize=13)
fig.tight_layout();fig.savefig(O/'rolling_validation.png',dpi=160);plt.close(fig)
print('4 charts saved')
