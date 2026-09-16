from pathlib import Path
import json
import pandas as pd,numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
R=Path(__file__).resolve().parent;p=pd.read_parquet(R/'predictions.parquet');f=pd.read_parquet(R/'panel.parquet');selection=json.loads((R/'selection.json').read_text());metrics=json.loads((R/'metrics.json').read_text());(R/'figures').mkdir(exist_ok=True)
selected=[];summary=[]
for cut,v in selection.items():
 z=p[(p.cutoff==cut)&(p.split=='test')&(p.variant==v['variant'])].copy();z=z.merge(f[['date','symbol','cutoff','v2_buy_unit_U','v2_sell_unit_U','v2_buy_unit_fill_ratio','v2_sell_unit_fill_ratio','v2_buy_unit_cancel_ratio','v2_sell_unit_cancel_ratio','settlement_mean_bp']],on=['date','symbol','cutoff'],validate='one_to_one');selected.append(z)
 for scope in ['fresh','all','legacy_dates']:
  for name in ['v1_frozen','old_features_asinh',v['variant']]:
   m=metrics['test'][cut+'_'+name][scope];summary.append(dict(cutoff=cut,scope=scope,variant=name,rows=m['n'],dates=m['dates'],accuracy=m['accuracy'],balanced_accuracy=m['balanced_accuracy'],mae_U=m['mae_U'],zero_mae_U=m['zero_mae_U'],positive_mae_U=m['positive_mae_U'],create_ap=m['create_ap'],create_brier=m['create_brier'],selected_n=m['selected_gate']['n'],selected_precision=m['selected_gate']['precision'],fixed90_n=m['fixed_p90']['n'],fixed90_precision=m['fixed_p90']['precision']))
z=pd.concat(selected,ignore_index=True);z.to_csv(R/'selected_test_predictions.csv',index=False);pd.DataFrame(summary).to_csv(R/'comparison.csv',index=False)
a=z[(z.cutoff=='14:45')&z.date.le('2026-04-08')];a[a.candidate].to_csv(R/'fresh_candidates_1445.csv',index=False)
q=p[(p.cutoff=='14:45')&(p.split=='test')&(p.symbol=='520600.SH')];q.to_csv(R/'520600_test_predictions_long.csv',index=False);wide=q.pivot(index='date',columns='variant',values='pred_baskets');wide['actual_U']=q.groupby('date').net_baskets.first();wide['new_p_create']=q[q.variant=='enriched_hurdle'].set_index('date').p_create;wide.to_csv(R/'520600_test_comparison.csv')
daily=a.groupby('date').apply(lambda x:pd.Series(dict(rows=len(x),candidate_n=int(x.candidate.sum()),candidate_correct=int((x.candidate&x.net_shares.gt(0)).sum()),candidate_wrong=int((x.candidate&x.net_shares.le(0)).sum()),mae_U=float((x.pred_baskets-x.net_baskets).abs().mean()))),include_groups=False);daily.to_csv(R/'fresh_daily_1445.csv')
# Per-input examples can go directly through the CLI; labels are not required by the scorer.
example=f[(f.cutoff=='14:45')&(f.date=='2026-04-27')].copy();example.drop(columns=['net_shares','net_baskets','net_flow_pct'],inplace=True);example.to_csv(R/'example_features_1445.csv',index=False)
fonts=[x for x in fm.findSystemFonts() if 'Arial Unicode' in x or 'PingFang' in x]
for font in sorted(fonts,key=lambda x:'Arial Unicode' not in x):
 try:
  fm.fontManager.addfont(font);plt.rcParams['font.family']=fm.FontProperties(fname=font).get_name();break
 except RuntimeError:continue
plt.rcParams.update({'font.size':11,'axes.spines.top':False,'axes.spines.right':False,'axes.unicode_minus':False,'axes.grid':True,'grid.alpha':.17})
fig,ax=plt.subplots(figsize=(14,6));x=np.arange(len(wide));w=.24
for off,col,label,color in [(-w,'actual_U','实际净申赎','#475569'),(0,'v1_frozen','原冻结模型','#a6b8ce'),(w,'enriched_hurdle','v2验证集选中版本','#009d8c')]:ax.bar(x+off,wide[col],w,label=label,color=color)
ax.axhline(0,c='#64748b',lw=.7);ax.set_xticks(x,[d[5:] for d in wide.index]);ax.set_ylabel('净申赎 / U');ax.set_title('520600.SH · 其他日期14:45预测：部分大净申购低估改善，反向误判仍存在',loc='left',pad=15);ax.legend(ncol=3);fig.text(.08,.025,'仅展示有完整输入的12个测试日；9月2日不在此图。3月20日实际−6U，v2却预测+66.94U。',color='#475569');fig.tight_layout(rect=[0,.07,1,1]);fig.savefig(R/'figures/520600_other_dates.png',dpi=160);plt.close(fig)
release=dict(status='research_only_not_promoted',feature_upgrade_complete=True,independent_test_complete=True,operational_acceptance=False,reasons=['Fresh-date selected gate 33/42 = 78.57%, below 90%','Fresh-date quantity MAE worse than same-data old-feature control and zero baseline','September 2 is a design case, not independent evidence'],selected_architectures=selection,models_unchanged_v1=True,source='metrics.json, validation.json, plan.json');(R/'release_status.json').write_text(json.dumps(release,ensure_ascii=False,indent=2))
print(wide.to_string());print(daily.to_string());print('saved report data and figure')
