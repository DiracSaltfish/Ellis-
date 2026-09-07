import json,gzip,hashlib,math,shutil
from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

OUT=Path(__file__).resolve().parent
BASE=OUT.parent.parent
RUN=Path('/Users/ellis/.codex/worktrees/e50b/工具程序开发/港股通相关性分析')
archive=BASE/'batch_archive_20260906'
summary=pd.read_csv(OUT/'fund_summary.csv');full=json.loads((OUT/'corrected_results.json').read_text())
sens=json.loads((OUT/'corrected_sensitivity.json').read_text());audit=json.loads((OUT/'audit_summary.json').read_text())
funds=json.loads((OUT/'candidate_review.json').read_text())

# Retain the completed computations and the exact small input packages locally,
# excluding duplicate remote streams, partial downloads, environments and previews.
for rel in ['scripts','tests','config','runs','data/inventory']:
    shutil.copytree(RUN/rel,archive/rel,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
for rel in ['data/raw/candidates_v2','data/raw/hti_chunks']:
    shutil.copytree(RUN/rel,archive/rel,dirs_exist_ok=True)
for name in ['HSI_FUT_1min.csv','HHI_FUT_1min.csv','HTI_FUT_1min.csv','sse_settlement_rates.csv','pilot_520600.jsonl.gz','pilot_513090.jsonl.gz']:
    src=RUN/'data/raw'/name
    if src.exists(): shutil.copy2(src,archive/'data/raw'/name)
for name in ['requirements-research.txt','requirements-probe.txt']:
    src=RUN/name
    if src.exists():shutil.copy2(src,archive/name)

def remap(p):return str(p).replace(str(RUN),str(archive))
for rows in [full,sens,funds]:
    for r in rows:
        for k,v in list(r.items()):
            if isinstance(v,str):r[k]=remap(v)
summary['source']=summary.source.map(remap)
refs=json.loads((OUT/'source_manifest.json').read_text())
for r in refs:
    for key in ['results','config','bundle']:
        original=r[key];copied=Path(remap(original));r[key+'_archived']=str(copied)
        assert copied.exists()
        assert hashlib.sha256(copied.read_bytes()).hexdigest()==r[key+'_sha256']
(OUT/'archived_source_manifest.json').write_text(json.dumps(refs,ensure_ascii=False,indent=2))

# Independently verify the corrected 513090 halt marks, including the last day.
eventroot=OUT/'513090_event_review'
eventresult=json.loads((eventroot/'reports/results.json').read_text())
eventval=json.loads((eventroot/'reports/validation.json').read_text())
cfg=json.loads((OUT/'513090_event_review_config.json').read_text())
with gzip.open(cfg['basket_input'],'rt') as z:raw={r['date']:r for r in map(json.loads,z)}
points=pd.read_parquet(eventroot/'data/normalized/pilot_minutes.parquet');points['date']=points.date.astype(str)
original=pd.read_parquet(RUN/'runs/513090.SH/20260906_batch/data/normalized/pilot_minutes.parquet');original['date']=original.date.astype(str)
common=points.merge(original,on=['date','minute'],suffixes=('_new','_old'))
same_pre=bool(np.allclose(common.basket_hkd_new,common.basket_hkd_old,rtol=0,atol=0))
probes=[]
for day in ['20260303','20260723','20260803']:
    value=0.;frozen=0.
    for c in raw[day]['components']:
        code=str(int(c['成分股代码'])).zfill(5);prior=[b for b in raw[day]['hk'].get(code,[]) if b[0]<=850]
        if prior:price=prior[-1][-1]
        else:
            assert code=='01788' and '20260723'<=day<'20260810'
            previous=sorted(d for d in raw if d<day and raw[d]['hk'].get(code))[-1]
            price=raw[previous]['hk'][code][-1][-1]
            frozen+=float(c['数量股'])*price
        value+=float(c['数量股'])*price
    row=points[(points.date==day)&(points.minute==850)].iloc[0]
    delta=abs(value-row.basket_hkd);fdelta=abs(frozen/value-row.frozen_weight)
    assert delta<=1e-8 and fdelta<=1e-12
    probes.append(dict(date=day,basket_difference_hkd=delta,frozen_weight_difference=fdelta))
res=pd.read_parquet(eventroot/'reports/oos_residuals.parquet')
maxerr=0.
for m in eventresult['metrics']:
    r=res[(res.horizon==m['horizon'])&(res.model==m['model'])]
    maxerr=max(maxerr,abs(np.std(r.residual,ddof=1)*1e4-m['residual_std_bps']),abs(1-np.var(r.residual,ddof=1)/np.var(r.y,ddof=1)-m['variance_reduction']))
assert maxerr<=1e-8 and same_pre
eventaudit=dict(scalar_checks=probes,prior_92_days_unchanged=same_pre,metrics_max_error=maxerr,
    frozen_days=int(points[points.frozen_weight>0].date.nunique()),max_frozen_weight=float(points.frozen_weight.max()),
    oos_days=40,method='Confirmed halt marks only; same model parameters; independent checks include dates skipped by original validator.')
(OUT/'event_review_independent.json').write_text(json.dumps(eventaudit,ensure_ascii=False,indent=2))

eventrows=[]
ci_pair={r['horizon']:r['ci95'] for r in eventval['bootstrap']}
for r in eventresult['metrics']:
    ci=r.get('variance_reduction_ci95') if r['model']=='selected' else ci_pair.get(r['horizon']) if r['model']=='HHI_HTI_fixed_pair' else None
    eventrows.append(dict(fund_id='513090.SH',fund_name='易方达中证香港证券投资主题ETF',index_name='中证香港证券',**r,
        first_oos='20260605',last_oos='20260803',ci_low=ci[0] if ci else None,ci_high=ci[1] if ci else None,
        evidence_tier='官方停牌补核后的冻结估值；仅日内价格风险',source=str(eventroot/'reports/results.json')))
esens=pd.read_csv(eventroot/'reports/sensitivity.csv').replace({np.nan:None}).to_dict('records')

indices=[]
for name,g in summary.groupby('index_name',sort=True):
    indices.append(dict(index_name=name,fund_count=len(g),fund_codes=';'.join(g.fund_id),
        pair_min=float(g.pair_variance_reduction.min()),pair_max=float(g.pair_variance_reduction.max()),
        median_std_bp=float(g.pair_std_bp.median()),fresh_median=float(g.fresh_pair_variance_reduction.median()),
        single_candidates=';'.join(sorted(set(g.best_single))),evidence='原批次技术初筛；同指数基金不算独立样本'))

issues=[
dict(id='A01',severity='高',finding='原Excel向全部模型复制HHI+HTI的置信区间',affected='1920行',action='修正版仅给双腿及selected使用各自区间，未计算的单腿留空',status='本次已修正',source=remap(RUN/'scripts/assemble_luna_handoff.py')),
dict(id='A02',severity='高',finding='仅筛样本误记为重新拟合；selected误关联双腿基准',affected='各1152行',action='从原始sensitivity.csv恢复真实模型、场景与refit标记',status='本次已修正',source=remap(RUN/'scripts/assemble_luna_handoff.py')),
dict(id='A03',severity='高',finding='210候选仅2只完成官方范围核验，其他基金的逐股事件表未补齐',affected='46只有技术结果；其余还有数据/范围缺口',action='不授予全市场/完整基金映射验收；保留技术初筛结论',status='仍未完成',source=str(BASE/'outputs/luna_handoff_20260906/ledger.json')),
dict(id='A04',severity='中',finding='513090的01788停牌导致末段8个基金日被标为未知缺价',affected='513090；其他含01788基金未在本次重跑',action='核验7月23日停牌和8月10日复牌，以冻结价格重跑513090并保留停牌日排除敏感性',status='513090已修正，其余仍需逐基金处理',source='https://www.hkexnews.hk/listedco/listconews/sehk/2026/0723/2026072300081.htm'),
dict(id='A05',severity='中',finding='任务开始时间使用源README mtime，工具锁定时间写为回测历史日期',affected='G01-G06、工具映射456行等',action='这些时间不能作为执行或预注册证据；以实际run日志、配置hash及训练边界核验',status='旧台账不作真实时间线使用',source=remap(RUN/'scripts/assemble_luna_handoff.py')),
dict(id='A06',severity='中',finding='通用引擎仍依赖前3工具固定顺序、固定双腿索引和硬编码事件；event_manifest配置未驱动处理',affected='批量复用与新标的扩展',action='保留已检查的固定8工具运行，不认证任意配置平台；本次513090事件用独立明确配置修正',status='平台泛化仍需修复',source=remap(RUN/'scripts/run_pilot.py')),
dict(id='A07',severity='中',finding='最终文档写4周期×8模型、60+10训练窗口',affected='报告口径',action='改为8工具+无对冲+双腿+自动共11模型，60训练内部50/10，外测32—40日',status='本次已修正',source=str(BASE/'outputs/luna_handoff_20260906/FINAL_HANDOFF.md')),
dict(id='A08',severity='中',finding='官方证据未完成被记为权限问题，存在统一填充PASS和任务末尾批量组装记录',affected='未闭环任务与验收状态',action='结构PASS不等于研究验收；本次独立289项核验代替旧表自报成功',status='旧任务闭环不予全通过',source=remap(RUN/'scripts/assemble_luna_handoff.py')),
dict(id='A09',severity='中',finding='48基金属于25个跟踪指数，且不同基金外测日不完全相同',affected='跨基金排名及多重比较',action='按指数分组，OOS最优单腿仅为事后描述，不宣称未来选模能力',status='已在交付中限定',source=str(OUT/'fund_summary.csv')),
]
for r in funds:
    if r['fund_id']=='513090.SH':r['review_conclusion']='本次已补核01788停牌并重跑；保留冻结估值局限'

def plain(v):
    if isinstance(v,dict):return {k:plain(x) for k,x in v.items()}
    if isinstance(v,list):return [plain(x) for x in v]
    if isinstance(v,(np.integer,)):return int(v)
    if isinstance(v,(np.floating,float)) and not math.isfinite(v):return None
    return v

# Summary reading order is deliberate; original 48-fund results stay unchanged.
bookdata=dict(summary=summary.replace({np.nan:None}).to_dict('records'),indices=indices,results=full,sensitivity=sens,
    candidates=funds,issues=issues,checks=json.loads((OUT/'audit_checks.json').read_text()),event_results=eventrows,event_sensitivity=esens,
    audit=audit,event_audit=eventaudit)
(OUT/'delivery_data.json').write_text(json.dumps(plain(bookdata),ensure_ascii=False,indent=2,allow_nan=False))
for name,rows in [('index_summary',indices),('review_issues',issues),('513090_corrected_metrics',eventrows)]:
    pd.DataFrame(rows).to_csv(OUT/(name+'.csv'),index=False,encoding='utf-8-sig')

# Exportable static chart uses a fixed illustrative selection, not an OOS winners list.
chosen=['520770.SH','520670.SH','513040.SH','520600.SH','513070.SH','520660.SH','520760.SH','513090.SH']
labels=['Broad index 520770','Technology 520670','Internet 513040','Auto 520600','Consumption 513070','Dividend 520660','Biotech 520760','Securities 513090*']
vals=[float(summary[summary.fund_id==f].pair_variance_reduction.iloc[0])*100 for f in chosen]
vals[-1]=next(r['variance_reduction'] for r in eventrows if r['horizon']==30 and r['model']=='HHI_HTI_fixed_pair')*100
fig,ax=plt.subplots(figsize=(10,5.6));fig.subplots_adjust(left=.24,right=.91,bottom=.20,top=.88)
ax.barh(labels,vals,color=['#718ba0']*3+['#245d81']+['#718ba0']*4,height=.6);ax.invert_yaxis();ax.set_xlim(0,100)
for i,v in enumerate(vals):ax.text(v+1,i,f'{v:.1f}%',va='center',fontsize=10)
ax.set_xlabel('30-minute variance reduction, HHI + HTI (%)');ax.set_title('Hedge quality differs materially by index exposure',loc='left',fontsize=13)
ax.spines[['top','right','left']].set_visible(False);ax.grid(axis='x',alpha=.15);ax.set_axisbelow(True)
fig.text(.24,.05,'2026-03-03 to 2026-08-03; 32–40 OOS days. *513090: halt-corrected run.\nOther examples except 520600: technical screening; product/event evidence incomplete.',fontsize=9,color='#555555')
fig.savefig(OUT/'risk_by_index.png',dpi=170);plt.close(fig)

def pct(x):return f'{x*100:.1f}%'
event30={r['model']:r for r in eventrows if r['horizon']==30};ep=event30['HHI_HTI_fixed_pair']
auto=summary[summary.fund_id=='520600.SH'].iloc[0]
doc=f'''# 港股通 ETF 对冲研究：最终结论与验收

本次验收结论为**部分通过**。底层价格风险计算可以复算，520600 的原始结论成立；Luna 的旧 Excel 有汇总错误，全市场分类和逐证券事件核验也没有完成，不能将“1266条任务全部终态”解释为全项目研究通过。本文与同目录修正版 Excel 是本轮结论入口，旧账本保留作过程记录。

## 结论先行

1. **520600 可以保留 HHI＋HTI 与 HTI 单腿作为后续执行成本研究基准。**30分钟双腿将标准差从36.91bp降至19.43bp，方差降低72.3%；严格陈旧敞口筛选并重拟合后为65.3%。HTI单腿20.03bp，双腿相对它仅改善0.60bp；ETF工具03032在本样本的单腿标准差为19.78bp，双腿相对它改善0.35bp。不能凭0.35/0.60bp直接选择双腿交易，因为还没有费用和可成交价格模型。
2. **对冲映射应首先按指数风险分组，再逐基金确认PCF差异。**48只已计算基金属于25个跟踪指数，同指数基金结果相近，不能当成48组独立证据。
3. **宽基与科技风险比较容易用现有工具覆盖；创新药、生物科技及部分红利风险覆盖明显不足。**后者应研究更贴近行业的工具，不能靠加大指数期货名义量把残差当作已经消除。本轮尚未验证新增行业工具的可用性与效果。
4. **复杂自动选模没有稳定胜过简单基准。**30分钟原批次48只中，自动选模仅19只的残差标准差小于固定HHI＋HTI；固定双腿只在10只中胜过事后最优单腿。后一个比较使用OOS后挑选的单腿赢家，仅是描述性参照，不能解释成可事先知道的选模收益。
5. **513090 证券行业风险并没有被宽基工具充分覆盖。**本次补核01788停牌并重跑后，40个OOS日下双腿标准差41.72→32.75bp、方差降低38.4%；HSI单腿为32.47bp、降低39.4%。双腿没有优势，不能沿用520600的72%效果估计。

## 已完成多少

|项目|实际范围|
|---|---:|
|候选目录|210只；不是认证港股通ETF总数|
|Luna完成分钟抽取|57只|
|形成真实技术回测|48只，4周期×11模型=2112行主指标|
|原批次外测长度|每只32—40日，均达到至少20日门槛|
|原批次完整敏感性表|3072行|
|原台账完成官方产品范围核验|2只：520600、513090|
|原台账基金终态|2完成、125阻塞、48范围外、35历史不足|
|本次独立验收|289项数值/目标/估值/边界检查通过，520600差异为0|

48只技术结果与“2只官方范围完成”是交叉关系，不能相加。48只范围外、35只历史不足是Luna记录的分类结果；本次保留它们及原因，并未逐条重做官方排除认证。原表将未完成核验统一记为阻塞，不意味着实际存在权限障碍。

## 30分钟：按风险组看映射方向

以下为原批次样本的描述性初筛；除520600外，产品或完整证券事件证据仍需完善。表中范围为同组基金的双腿方差降低，不是置信区间，各基金OOS日期也不完全相同。

|风险组|HHI＋HTI方差降低|后续基准方向|
|---|---:|---|
|宽基：恒指港股通/港股通50等|约85%—91%|先比较HSI单腿；部分中国30指数用HHI|
|科技/新经济|约90%—91%|保留HTI及03032等恒科代理作同组比较|
|互联网|约77%|HHI＋HTI相对单腿有可见改善，仍需核算代价|
|汽车|约72%|520600以双腿及HTI单腿为基准|
|消费|约59%—62%|保留简单HSI/HHI，仍有行业残差|
|红利/央企红利|约47%—59%|HHI/02828仅覆盖部分风险|
|创新药/生物科技|约33%—36%|现有宽基对冲不足，不能确定可靠最终映射|

![不同风险组的对冲效果]({OUT/'risk_by_index.png'})

原批次48只双腿方差降低中位数为72.3%；16只≥80%，19只在50%—80%，13只<50%。这个分布受候选选择、同指数重复和不同OOS日期影响，不能称为全市场成功率。

## 520600 的可采用结论

|持有时长|未对冲标准差bp|HHI＋HTI残差标准差bp|方差降低|
|---|---:|---:|---:|
|5分钟|14.45|9.87|53.4%|
|15分钟|25.81|14.88|66.8%|
|30分钟|36.91|19.43|72.3%|
|60分钟|51.51|26.99|72.5%|

30分钟双腿按整日bootstrap的95%区间为66.4%—77.3%。上侧ES95为42.28bp，下侧为39.37bp；这是样本尾部平均残差，**不是最坏损失上限**。若仅将样本风险按5000万元篮子名义额线性换算，19.43bp约对应9.72万元标准差，上侧42.28bp约对应21.14万元；不包含费用、冻结资产复牌跳变和结算汇率预测误差。

同日固定数量篮子收益只衡量持有期间市场风险，尚未模拟基金实际补券进度、库存净额化或申赎退款路径，不能把这一研究直接当作申赎套利损益。

## 513090：本次修正的停牌缺口

Luna原运行将2026-07-23至08-03的8个基金日剔除，理由为01788未知缺价。我查到港交所正式通告：该股7月23日09:00停牌，8月10日09:00复牌。来源：[停牌通告](https://www.hkexnews.hk/listedco/listconews/sehk/2026/0723/2026072300081.htm)、[复牌通告](https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0810/2026081000081.htm)。

本次独立运行保留每日PCF的完整数量，以停牌前最后实际价格冻结估值。恢复8个基金日，形成100个面板日和40个OOS日；冻结资产名义权重最大约{pct(eventaudit['max_frozen_weight'])}。原92个非停牌面板日数值完全不变。所有模型参数保持原样，只改变经核实事件的处理。

|场景/模型|外测日数|30分钟残差标准差bp|方差降低|
|---|---:|---:|---:|
|原运行，停牌日剔除，HHI＋HTI|32|33.35|43.1%|
|本次保留停牌冻结估值，HHI＋HTI|40|32.75|38.4%|
|本次保留停牌冻结估值，HSI单腿|40|32.47|39.4%|
|本次自动选模|40|32.31|40.0%|
|陈旧敞口≤2%后重拟合，HHI＋HTI|32|31.12|43.7%|

样本组成不同，43.1%与38.4%的变化不能直接归因为对冲模型变差。冻结估值也看不到潜在私有化或复牌跳变，因此只能用于日内价格风险核算；证券行业最终映射仍不充分。30分钟双腿上侧ES95为83.89bp、下侧61.96bp，残余尾部明显大于520600。

## 验收发现及处置

- 原Excel有1920行使用了不属于该模型的双腿置信区间。本次从results.json与validation.json重建，仅双腿和自动模型保留自己的区间；未计算的单腿为空。
- 原敏感性表1152行误标重新拟合，另1152行把自动模型关联到双腿基准。本次恢复真实场景、模型与refit含义。
- 原“4周期×8模型”应为4周期×11模型；训练是60日，其中前50日拟合、后10日内部验证，随后使用完整60日重拟合预测下一日，不是70日训练。
- 原任务开始时间和工具锁定日期不能作为真实执行时间线或预注册证明。底层训练边界检查通过，但事后台账不能证明所有研究选择在查看外测前已经锁定。
- 代码完成了固定8工具的批处理，但工具排序、事件逻辑等仍有硬编码，缓存也没有完整强制指纹失效校验。本次不授予“任意基金、任意工具配置通用平台”验收。

## 交付与后续工作

本目录 `港股通ETF_最终结论与验收.xlsx` 包含结论总览、48基金原批次汇总、25指数分组、210候选处理、2112行更正模型结果、3072行更正敏感性、513090新运行、修正记录及独立验收。**原批次48只结果与513090新事件运行分表保留，避免混用不同窗口。**所有数值单位和证据层级在表内明确。

批处理代码、配置、已完成运行与所需分钟小包已复制到 `{archive}`，不依赖临时worktree保留。`archived_source_manifest.json`记录输入/输出hash和旧路径到本地归档路径的映射，结果原元数据保留原来源路径作追溯。

下一轮按顺序做：先对46只有技术结果的产品补范围及逐证券事件；再修复通用引擎配置校验与缓存失效；随后使用新的未触碰时间窗口检验本轮选出的映射，最后单独加入交易成本和实际申赎事件。无需先补tick或精确成交量才能完成前两步。

复跑本次验收：使用原项目 `.venv/bin/python` 执行本目录 `audit_results.py`（该脚本检查原worktree快照）；归档复制核对看 `archived_source_manifest.json`。513090修正运行使用本目录 `event_review_engine/run_pilot.py --config 513090_event_review_config.json`，输出单独目录，随后执行同目录的validate_pilot.py。若原worktree日后被清理，按归档根路径更新验收脚本RUN变量和513090配置的输入路径，不能静默换数据。
'''
(OUT/'最终结论与验收.md').write_text(doc)
(archive/'README_ARCHIVE.md').write_text(f'''# 已验收快照归档
原worktree为{RUN}。保留57个候选分钟小包、批量配置与运行、脚本和固定期货/FX输入，排除大批重复压缩流及失败下载。
在本目录运行原项目解释器：
`/Users/ellis/工具程序开发/港股通相关性分析/.venv/bin/python scripts/run_pilot.py --config config/batch/520600_SH.json`
运行会在本归档对应runs子目录输出；建议复制配置并改到新的输出目录保留此次快照。原results.json中的绝对路径是历史来源，不代表归档缺失。正式结论见{OUT/'最终结论与验收.md'}。
''')
print(json.dumps({'event_audit':eventaudit,'archive':str(archive),'workbook_data':str(OUT/'delivery_data.json'),'report':str(OUT/'最终结论与验收.md')},ensure_ascii=False))
