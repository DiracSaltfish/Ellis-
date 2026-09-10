from pathlib import Path
import csv,numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager,ticker
R=Path(__file__).resolve().parents[1]
font='/System/Library/Fonts/Supplemental/Arial Unicode.ttf';font_manager.fontManager.addfont(font)
plt.rcParams.update({'font.family':font_manager.FontProperties(fname=font).get_name(),'axes.unicode_minus':False,'font.size':12,'axes.spines.top':False,'axes.spines.right':False,'axes.edgecolor':'#BCC6D3','axes.labelcolor':'#46546A','xtick.color':'#46546A','ytick.color':'#46546A','savefig.facecolor':'white'})
rows=list(csv.DictReader((R/'minute_iopv_20260908.csv').open(encoding='utf-8-sig')))
def xpos(t):
 h,m=map(int,t.split(':'));v=h*60+m-570
 return v-(60 if h>=13 else 0)
x=np.array([xpos(r['time']) for r in rows]);est=np.array([float(r['IOPV_exact']) for r in rows]);market=np.array([float(r['ETF_price']) if r['ETF_price'] else np.nan for r in rows]);prem=np.array([float(r['ETF_premium'])*100 if r['ETF_premium'] else np.nan for r in rows]);valid=np.isfinite(market)
blue='#2563EB';gold='#BD8700';ink='#17283F';grey='#64748B'
fig,(ax,ap)=plt.subplots(2,1,figsize=(16,10),sharex=True,gridspec_kw={'height_ratios':[3.2,1.15],'hspace':.14})
fig.subplots_adjust(left=.075,right=.955,top=.80,bottom=.22)
fig.text(.075,.945,'SH513090  |  香港证券ETF',fontsize=25,color=ink,weight='bold')
fig.text(.075,.902,'2026年9月8日：实际分时价格 × 中间价重算 IOPV',fontsize=18,color=ink)
fig.text(.075,.861,'当日 PCF 17只成分券   ·   HKD/CNY 0.86482   ·   预估现金 9,547.19元   ·   每篮50万份',fontsize=12,color=grey)
for a in [ax,ap]:
 a.set_facecolor('white');a.grid(axis='y',color='#E6EBF1',linewidth=.8);a.set_axisbelow(True)
 a.axvspan(120,150,color='#F1F5F9',zorder=0)
 a.axvspan(270,338,color='#FAF7EE',zorder=0)
 a.axvline(270,color='#94A3B8',linestyle='--',linewidth=1)
ax.plot(x,est,color=gold,lw=2.3,label='中间价重算 IOPV',zorder=4)
ax.plot(x,market,color=blue,lw=2,label='ETF实际价格',zorder=5)
ax.fill_between(x,market,est,where=valid,color=blue,alpha=.05)
ax.set_ylim(1.834,1.902);ax.set_ylabel('人民币元 / 份',labelpad=12)
ax.yaxis.set_major_formatter(ticker.FormatStrFormatter('%.3f'))
ax.legend(loc='upper right',frameon=False,ncol=2,bbox_to_anchor=(1,1.10),fontsize=12)
ax.text(135,1.898,'A股午休',ha='center',fontsize=10,color=grey)
ax.text(304,1.898,'A股收盘后\n仅港股篮子继续估值',ha='center',va='top',fontsize=10,color=grey)
idx=next(i for i,r in enumerate(rows) if r['time']=='15:00')
ax.scatter([270,270,338],[est[idx],market[idx],est[-1]],c=[gold,blue,gold],s=32,zorder=6)
ax.annotate('15:00  估值 1.8517',xy=(270,est[idx]),xytext=(223,1.874),fontsize=11,color=gold,arrowprops={'arrowstyle':'-','color':gold,'lw':1},bbox={'facecolor':'white','edgecolor':'none','pad':3})
ax.annotate('15:00  ETF 1.8460',xy=(270,market[idx]),xytext=(213,1.838),fontsize=11,color=blue,arrowprops={'arrowstyle':'-','color':blue,'lw':1},bbox={'facecolor':'white','edgecolor':'none','pad':3})
ax.annotate('16:08 收盘估值 1.8433\n当日官方净值 1.8433',xy=(338,est[-1]),xytext=(283,1.865),fontsize=11,color=ink,arrowprops={'arrowstyle':'-','color':grey,'lw':1},bbox={'facecolor':'white','edgecolor':'none','pad':3})
ap.axhline(0,color='#64748B',linewidth=1)
ap.fill_between(x,prem,0,where=np.isfinite(prem),color=blue,alpha=.12)
ap.plot(x,prem,color=blue,lw=1.5)
ap.set_ylim(-1.30,.10);ap.set_ylabel('ETF折溢价率',labelpad=12)
ap.yaxis.set_major_formatter(ticker.FuncFormatter(lambda y,_:f'{y:.1f}%'))
ap.text(304,-.60,'不沿用15:00 ETF价格\n计算盘后“实时”溢价',ha='center',fontsize=10,color=grey)
ap.scatter([270],[prem[idx]],s=22,color=blue,zorder=5)
ap.annotate('-0.3093%',xy=(270,prem[idx]),xytext=(222,-.12),fontsize=11,color=blue,arrowprops={'arrowstyle':'-','color':blue,'lw':1})
ap.set_xlim(0,342);ap.set_xticks([0,60,120,150,210,270,330,338]);ap.set_xticklabels(['09:30','10:30','11:30','12:00 / 13:00','14:00','15:00','16:00','16:08'])
# Close auction labels are near each other, use a minor endpoint annotation instead of crowded tick text.
ap.set_xticks([0,60,120,150,210,270,338]);ap.set_xticklabels(['09:30','10:30','11:30','12:00 / 13:00','14:00','15:00','16:08'])
ap.set_xlabel('北京时间（港股12:00—13:00午休已压缩；16:00无独立报价）',labelpad=10)
fig.text(.075,.102,'242个可匹配分钟样本全部折价：-1.1394% ～ -0.2717%；没有溢价样本。',fontsize=14,color=ink,weight='bold')
fig.text(.075,.067,'估值 = [Σ(9月8日PCF数量 × 当分钟港股价格) × 0.86482 + 9,547.19] / 500,000。',fontsize=10.5,color=grey)
fig.text(.075,.039,'来源：易方达PCF、腾讯分钟行情、外管局中间价。332个观测点；15:59后为16:08收盘最终点，未插造16:00数据。',fontsize=10.5,color=grey)
fig.savefig(R/'513090_20260908_分时价格与估值.png',dpi=160)
fig.savefig(R/'513090_20260908_分时价格与估值.svg')
plt.close(fig)
print(R/'513090_20260908_分时价格与估值.png')
