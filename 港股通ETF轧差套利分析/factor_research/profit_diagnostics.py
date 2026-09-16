from pathlib import Path
import numpy as np,pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from profit_research import D,stats,pnl
from profit_two_stage import wilson
h=pd.read_csv(D/'two_stage_selected.csv');out=[]
for r in [0,1,4,9]:
    for fee in [5,10,20,40]:out.append(dict(opposite_ratio=r,fee_bp=fee,**stats(h,r,fee)))
pd.DataFrame(out).to_csv(D/'pooled_sensitivity.csv',index=False)
portfolio=[]
for limit in [1,3,5,10,999]:
    sel=h.sort_values(['date','mean_bp'],ascending=[True,False]).groupby('date').head(limit)
    s=stats(sel);portfolio.append(dict(max_funds=limit,**s))
pd.DataFrame(portfolio).to_csv(D/'participation_count.csv',index=False)
font_manager.fontManager.addfont('/System/Library/Fonts/Supplemental/Arial Unicode.ttf')
plt.rcParams.update({'font.family':'Arial Unicode MS','axes.unicode_minus':False,'font.size':11,'axes.spines.top':False,'axes.spines.right':False})
daily=pd.read_csv(D/'two_stage_portfolio_all.csv');fig,ax=plt.subplots(figsize=(13,5))
ax.bar(np.arange(len(daily)),daily.return_bp,color=np.where(daily.return_bp>0,'#008575','#b54f32'))
ax.axhline(0,color='#333333',lw=.8);ax.set_xticks(range(len(daily)),daily.date.str[5:],rotation=45,ha='right')
ax.set_ylabel('当日组合情景净收益 / 名义篮子金额 / bp')
ax.set_title('22个有机会的交易日：20天情景收益为正；全部符合条件ETF各参与1篮子',pad=15)
fig.text(.08,.02,'假设：对向申赎量=净量，综合成本10bp；已知最终汇率及16点估值。非实收实付回测；无信号日未计入胜率。',fontsize=10)
fig.tight_layout(rect=[0,.04,1,1]);fig.savefig(D/'daily_portfolio.png',dpi=160);plt.close(fig)
s=pd.DataFrame(out);fig,ax=plt.subplots(figsize=(11,5))
for r in [0,1,4,9]:
    g=s[s.opposite_ratio==r];ax.plot(g.fee_bp,g.mean_bp,marker='o',label=f'对向量 / 净量 = {r}')
ax.axhline(0,color='#333333',ls='--');ax.set_xticks([5,10,20,40]);ax.set_xlabel('假设综合成本 / bp');ax.set_ylabel('平均单基金日情景净收益 / bp');ax.legend();ax.grid(axis='y',alpha=.2)
ax.set_title('同一批108个基金日：成本与未知双边规模可以改变盈利结论',pad=12)
fig.tight_layout();fig.savefig(D/'cost_dilution.png',dpi=160);plt.close(fig)
print(pd.DataFrame(portfolio).to_string(index=False));print(s.to_string(index=False))
