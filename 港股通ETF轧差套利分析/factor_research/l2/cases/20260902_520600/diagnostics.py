from pathlib import Path
import json,sys,hashlib
import pandas as pd,numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
R=Path(__file__).resolve().parent;O=R/'results';G=R/'figures';G.mkdir(exist_ok=True)
s=pd.read_csv(O/'minute_context.csv').set_index('minute_id');m=pd.read_csv(O/'minutes_full.csv').set_index('completed_minute');orders=pd.read_csv(O/'orders_1445.csv');b=pd.read_csv(O/'bursts_1445.csv');src=Path('/Volumes/EllisFiles/Stocksdata/A股逐笔/单标的研究提取/520600_20260902/20260902/520600.SZ');tr=pd.read_csv(src/'逐笔成交.csv',encoding='gb18030');o=pd.read_csv(src/'逐笔委托.csv',encoding='gb18030')
ms=lambda x:x//10000000*3600000+x//100000%100*60000+x//1000%100*1000+x%1000
fmt=lambda x:f'{int(x)//3600000:02}:{int(x)//60000%60:02}:{int(x)//1000%60:02}.{int(x)%1000:03}'
tr['ms']=ms(tr['时间']);o['ms']=ms(o['时间']);tr['amount']=tr['成交价格']*tr['成交数量']/10000;tr['side']=tr['BS标志'].map({'B':1,'S':2});tr['continuous']=tr.ms.between(34200000,41400000)|(tr.ms.ge(46800000)&tr.ms.lt(53820000));a=tr[tr.continuous&tr.ms.lt(53100000)].copy();a['active_id']=np.where(a.side.eq(1),a['叫买序号'],a['叫卖序号'])
# Join the minute already completed when the trade happens, never that trade's future bar.
a['prior_minute']=a.ms//60000;a['known_premium_bp']=a.prior_minute.map(s.settlement_bp);a['large_active']=a.active_id.isin(orders.loc[orders.active_filled.ge(250000),'order_id']);a['unit_active']=a.active_id.isin(orders.loc[orders.active_filled.eq(500000),'order_id'])
new={}
for side,key in [(1,'buy'),(2,'sell')]:
 z=a[a.side.eq(side)];new[key]=dict(large_positive_premium_u=float(z.loc[z.large_active&z.known_premium_bp.gt(30),'成交数量'].sum()/500000),exact_1u_active_positive_premium_u=float(z.loc[z.unit_active&z.known_premium_bp.gt(30),'成交数量'].sum()/500000),coverage_prior_premium_amount=float(z.loc[z.known_premium_bp.notna(),'amount'].sum()/z.amount.sum()))
 ids=orders[orders.side.eq(side)&orders.original_quantity.eq(500000)&orders.known_original]
 new[key].update(exact_1u_submitted_count=len(ids),exact_1u_filled_u=float(ids.filled.sum()/500000),exact_1u_cancelled_u=float(ids.cancelled.sum()/500000),exact_1u_remaining_u=float(ids.remaining.sum()/500000))
# Order evidence with full passive timestamps and submit/cancel details.
chosen=set(orders.nlargest(12,'active_filled').order_id)|set(orders[orders.near_1u_original].nlargest(15,'passive_filled').order_id)|set(orders[orders.active_filled.eq(500000)].order_id)
detail=[]
for oid in sorted(chosen):
 z=orders[orders.order_id.eq(oid)].iloc[0];ts=tr[(tr['叫买序号'].eq(oid) if z.side==1 else tr['叫卖序号'].eq(oid))&tr.ms.lt(53100000)];os=o[o['交易所委托号'].eq(oid)&o.ms.lt(53100000)];ads=os[os['委托类型'].eq('A')]
 detail.append(dict(order_id=oid,side=int(z.side),original_shares=int(z.original_quantity),original_known=bool(z.known_original),original_lower_bound=int(z.original_quantity_lower_bound),active_shares=int(z.active_filled),passive_shares=int(z.passive_filled),cancelled=int(z.cancelled),remaining=int(z.remaining),add_time=fmt(ads.ms.min()) if len(ads) else '',reported_resting=int(z.reported_resting),first_fill=fmt(ts.ms.min()) if len(ts) else '',last_fill=fmt(ts.ms.max()) if len(ts) else '',trade_count=len(ts),active_trade_count=int(z.active_trade_count) if pd.notna(z.active_trade_count) else 0,active_first_time=z.active_first_time if pd.notna(z.active_first_time) else '',vwap=float(ts.amount.sum()/ts['成交数量'].sum()) if len(ts) else 0))
pd.DataFrame(detail).to_csv(O/'order_evidence_1445.csv',index=False)
# Event windows are descriptive from first large morning premium peak; no tuning claim.
peak=591;windows=[]
for begin,end in [(571,591),(592,606),(607,621),(622,636),(637,651),(652,690),(781,870),(871,885)]:
 z=s.loc[s.index.to_series().between(begin,end)];flow=m[m.index.to_series().between(begin,end)];windows.append(dict(start=f'{begin//60:02}:{begin%60:02}',end=f'{end//60:02}:{end%60:02}',final_premium_first=float(z.final_settlement_bp.dropna().iloc[0]),final_premium_last=float(z.final_settlement_bp.dropna().iloc[-1]),mean_final_premium=float(z.final_settlement_bp.mean()),buy_amount=float(flow.buy_notional_x10000.sum()/1e4),sell_amount=float(flow.sell_notional_x10000.sum()/1e4),net_active=float((flow.buy_notional_x10000.sum()-flow.sell_notional_x10000.sum())/1e4)))
pd.DataFrame(windows).to_csv(O/'event_windows.csv',index=False)
train=pd.read_parquet(R.parents[1]/'study/panel.parquet');train=train[(train.date<='2026-01-27')&train.cutoff.eq('14:45')];f=pd.read_csv(O/'features.csv');new['training_scope']=dict(funddays=len(train),funds=train.symbol.nunique(),dates=train.date.nunique(),target_funddays=int(train.symbol.eq('520600.SH').sum()),fx_gap_max=float(train.fx_gap_bp.max()),fx_gap_min=float(train.fx_gap_bp.min()),turnover_max=float(train.turnover_pct.max()))
new['not_validated']=True;(O/'candidate_factors.json').write_text(json.dumps(new,ensure_ascii=False,indent=2))
# Readable standalone scientific plots, no external hosting.
fonts=[x for x in fm.findSystemFonts() if 'Arial Unicode' in x or 'PingFang' in x]
if fonts:fm.fontManager.addfont(fonts[0]);plt.rcParams['font.family']=fm.FontProperties(fname=fonts[0]).get_name()
plt.rcParams.update({'font.size':11,'axes.spines.top':False,'axes.spines.right':False,'axes.grid':True,'grid.alpha':.15,'axes.unicode_minus':False,'savefig.facecolor':'white'})
# Compress lunch while preserving actual time labels.
x=lambda v:np.asarray(v)-np.where(np.asarray(v)>=780,90,0)
show=s[s.index.isin(list(range(571,691))+list(range(781,898)))].copy();xv=x(show.index)
fig,ax=plt.subplots(4,1,figsize=(14,15),sharex=True,gridspec_kw={'height_ratios':[1.2,1,1,.8]})
for col,label,color in [('etf','ETF 成交价','#2563eb'),('mid','中间价 IOPV','#b58a00'),('final_settlement','当日最终结算汇率 IOPV','#009d8c')]:ax[0].plot(xv,show[col],label=label,c=color,lw=1.6)
ax[0].set_ylabel('元 / 份');ax[0].legend(loc='upper right',ncol=1);ax[0].set_title('520600.SH · 2026-09-02：持续结算溢价与卖方成交，原模型仍漏判 +61U',loc='left',fontsize=17,pad=18)
ax[1].plot(xv,show.final_settlement_bp,c='#009d8c',label='最终结算溢价');ax[1].plot(xv,show.mid_bp,c='#b58a00',label='中间价溢价');ax[1].axhline(30,c='#94a3b8',ls=':',label='30 bp');ax[1].axhline(0,c='#64748b',lw=.7);ax[1].set_ylabel('溢价 / bp');ax[1].legend(ncol=3,loc='lower left')
mm=m[m.index.isin(show.index)].reindex(show.index).fillna(0);net=(mm.buy_notional_x10000-mm.sell_notional_x10000).cumsum()/1e8;ax[2].plot(xv,net,c='#7c3aed',label='连续竞价主动买入－主动卖出');ax[2].axhline(0,c='#64748b',lw=.7);ax[2].set_ylabel('累计主动净额 / 万元');ax[2].legend(loc='lower left')
for side,label,color in [(1,'主动买单已成交量','#2563eb'),(2,'主动卖单已成交量','#e05a47')]:
 z=orders[orders.side.eq(side)&orders.active_filled.ge(250000)];t=x(z.active_first_ms.to_numpy()/60000);ax[3].scatter(t,z.active_u,c=color,s=28,alpha=.65,label=label)
ax[3].axhline(1,c='#64748b',ls=':',label='1U = 50万份');ax[3].set_ylabel('订单主动成交 / U');ax[3].legend(ncol=3,loc='upper right')
for aa in ax:
 aa.axvspan(690,691,color='#cbd5e1',alpha=.4);aa.axvline(x(870),c='#475569',ls='--',lw=1);aa.axvline(x(885),c='#475569',ls='--',lw=1);aa.axvspan(x(885),x(897),color='#e2e8f0',alpha=.5)
ax[0].text(x(870)-1,ax[0].get_ylim()[0],'14:30',rotation=90,va='bottom',ha='right');ax[0].text(x(885)-1,ax[0].get_ylim()[0],'14:45',rotation=90,va='bottom',ha='right')
ticks=[571,600,630,660,690,810,840,870,897];ax[-1].set_xticks(x(ticks),['09:31','10:00','10:30','11:00','11:30 / 13:00','13:30','14:00','14:30','14:57']);ax[-1].set_xlabel('已完成分钟（午休压缩；14:57后集合竞价与15:00–16:00不计入因子）')
fig.text(.08,.015,'价格图使用当日最终汇率作事后解释；预测使用前一日结算汇率。灰色末段不属于14:45预测输入。1U成交量不能证明申赎身份。',fontsize=10,color='#475569');fig.tight_layout(rect=[0,.035,1,1]);fig.savefig(G/'intraday.png',dpi=160);plt.close(fig)
g=pd.read_csv(O/'group_attribution.csv');g=g[g.cutoff.eq('14:45')].sort_values('contribution_pp');names={'active_imbalance':'主动买卖差额','large_active_orders':'大额主动成交订单','unit_active_orders':'接近整U的主动订单','unit_bursts':'接近整U的短时成交簇','premium_compression':'溢价回落期间成交','passive_unit_sell':'整U被动卖单成交','premium_state':'溢价水平与走势','history_and_size':'历史份额、规模及换手'}
fig,aa=plt.subplots(figsize=(11,6));aa.barh(g.group.map(names),g.contribution_pp,color=np.where(g.contribution_pp>=0,'#009d8c','#e05a47'))
for j,v in enumerate(g.contribution_pp):aa.text(v+(.3 if v>=0 else -.3),j,f'{v:+.2f}',va='center',ha='left' if v>=0 else 'right')
aa.axvline(0,c='#475569',lw=.7);aa.set_xlim(-20,23);aa.set_xlabel('对净申购概率的贡献 / 百分点');aa.set_title('14:45 模型解释：背景概率19.54% → 当日40.87%',loc='left',pad=18);fig.text(.05,.02,'32个真实训练样本作背景，8组因子全部256种组合的精确分解；反映模型响应，不是因果证明。',fontsize=10);fig.tight_layout(rect=[0,.05,1,1]);fig.savefig(G/'attribution.png',dpi=160);plt.close(fig)
print(json.dumps(new,ensure_ascii=False));print(pd.DataFrame(windows).to_string(index=False));print('FIGURES',str(G))
