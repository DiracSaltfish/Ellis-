import fs from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';
const root=process.env.ETF_REPORT_DATA_DIR?process.env.ETF_REPORT_DATA_DIR.replace(/\/$/,'')+'/':fileURLToPath(new URL('./outputs/',import.meta.url));
const out=root+'01a0915e-f9e1-76e3-bbaa-2329e4b4b7e8/';await fs.mkdir(out,{recursive:true});
const d=JSON.parse(await fs.readFile(root+'data.json','utf8'));const wb=Workbook.create();
const summary=wb.worksheets.add('结果汇总');const daily=wb.worksheets.add('每日择优');const final=wb.worksheets.add('最终汇率对照');const rerank=wb.worksheets.add('事后重选对照');
function base(s,last,end,widths){s.showGridLines=false;s.getRange(`A1:${last}${end}`).format.font={name:'Arial',size:10,color:'#263445'};s.getRange(`A1:${last}${end}`).format.verticalAlignment='center';widths.forEach((w,i)=>s.getRangeByIndexes(0,i,end,1).format.columnWidthPx=w);}
function title(s,t,notes){s.getRange('A2').values=[[t]];s.getRange('A2').format.font={name:'Arial',size:16,bold:true,color:'#153E48'};s.getRange('A2:N2').format.rowHeight=30;notes.forEach((n,i)=>{s.getRange(`A${i+3}`).values=[[n]];s.getRange(`A${i+3}:N${i+3}`).format.rowHeight=22;});}
function header(s,range,h){s.getRange(range).values=[h];s.getRange(range).format.fill='#153E48';s.getRange(range).format.font={name:'Arial',size:10,bold:true,color:'#FFFFFF'};s.getRange(range).format.horizontalAlignment='center';s.getRange(range).format.rowHeight=32;}
const cols=['交易日','样本阶段','标的代码','基金名称','模型分数','实际净申赎份额','申赎单位份额','累计申购上限份','实际净申赎U','申购上限U','方向结果','满额处理','限额识别状态','空仓原因','数据质量状态'];
function detail(s,rows,id,note){const end=8+rows.length;base(s,'O',end,[106,170,110,240,90,146,128,148,118,112,165,205,245,240,250]);s.tabColor='#557C86';title(s,s.name,[note,'满额代理：盘后净申购 ≥ 当日PCF正数有限累计申购上限；主序列剔除原第一名，不递补。','2025为使用2026模型的跨期回放；模型分数未经概率校准，净量不变也计误判。','数量来源：1navs同日份额历史缓存、当日PCF主表。原始数据路径及核对记录见配套报告。']);header(s,'A8:O8',cols);
 s.getRange(`A9:O${end}`).values=rows.map(r=>[new Date(r.date+'T00:00:00Z'),r.phase,r.symbol,r.name,r.score,r.net_shares,r.unit,r.cap_shares,null,null,r.result,r.decision,r.cap_status,r.reason||'',r.data_quality_status]);
 s.getRange(`A9:O${end}`).format.rowHeight=30;s.getRange(`D9:D${end}`).format.wrapText=true;
 s.getRange('I9').formulas=[['=IF(G9>0,F9/G9,"")']];s.getRange(`I9:I${end}`).fillDown();s.getRange('J9').formulas=[['=IF(H9>0,H9/G9,"")']];s.getRange(`J9:J${end}`).fillDown();
 s.tables.add(`A8:O${end}`,true,id);header(s,'A8:O8',cols);
 s.getRange(`A9:A${end}`).setNumberFormat('yyyy-mm-dd');s.getRange(`A9:A${end}`).format.horizontalAlignment='left';s.getRange(`E9:E${end}`).setNumberFormat('0.000');s.getRange(`F9:H${end}`).setNumberFormat('#,##0;[Red]-#,##0;0');s.getRange(`I9:J${end}`).setNumberFormat('#,##0.00;[Red]-#,##0.00;0.00');
 s.getRange(`K9:K${end}`).conditionalFormats.add('containsText',{text:'未命中',format:{fill:'#FEF3C7'}});s.getRange(`L9:M${end}`).conditionalFormats.add('containsText',{text:'排除',format:{fill:'#FEE2E2'}});s.freezePanes.freezeRows(8);s.freezePanes.freezeColumns(3);
}
detail(daily,d.main,'DailyChoices',d.market_scope?'主规则：先剔除全部深圳标的，再选沪市第一名；14:45以前数据，盘中汇率代理，隔离513130。':'主规则：14:45以前数据，盘中汇率代理，隔离513130，每日最高模型分数，无概率阈值。');
detail(final,d.final,'FinalFxChoices','只更换为当日最终汇率计算整日溢价；含事后信息，保留用于估值口径比较。');
detail(rerank,d.reranked,'RerankedChoices','事后对照：先以盘后净量剔除满额候选，再选最高分；此表会递补，不是实时策略成绩。');
base(summary,'L',100,[210,195,82,82,82,84,84,104,104,122,122,125]);summary.tabColor='#153E48';
title(summary,d.market_scope?'沪市ETF · 扩充历史后的每日择优结果':'扩充历史数据后的每日择优结果',['命中 = 当日实际净申购 > 0；本表统计方向准确率，不是资金收益率或实际申购成功率。','主表为盘中汇率代理口径。满额剔除使用盘后净量，只是事后敏感性分析，不能证明当时剩余额度。','2025与2026分开看：2025模型训练在其之后；2026后续73日不含训练及验证43日。',d.market_scope?'仅5开头沪市ETF；1开头深圳标的使用实时申赎，不纳入预测。95%区间未处理时间相关性。':'固定模型与规则，不重训、不根据新结果选阈值；95%区间使用Wilson方法，未处理时间相关性。']);
const hdr=['样本范围','规则','日期数','选中数','净申购','净量不变','净赎回','方向命中率','方向误判率','误判95%下界','误判95%上界','上限未识别数'];header(summary,'A8:L8',hdr);
const specs=d.summary.filter(s=>s.fx==='lag'&&s.rule!=='事后剔除候选再选第一');
const end=8+d.main.length;
const phases={'2025跨期回放':['2025跨期回放'],'2026新增29日':['2026新增检验'],'2026后续检验73日':['历史压力检验','上轮检验','本轮新增检验','2026新增检验'],'2026此前检验44日':['历史压力检验','上轮检验','本轮新增检验'],'训练及验证43日':['训练期回放','验证期回放'],'全部353日（混合回放）':null};
function count(spec,extra=''){const ph=phases[spec.cohort]||[null];const q="'每日择优'!";return ph.map(p=>`COUNTIFS(${q}$G$9:$G$${end},">0"${p?`,${q}$B$9:$B$${end},"${p}"`:''}${spec.rule==='剔除原选中满额样本'?`,${q}$L$9:$L$${end},"保留"`:''}${extra.replaceAll('@',q)})`).join('+');}
summary.getRange(`A9:L${8+specs.length}`).values=specs.map(s=>[s.cohort,s.rule,s.days,null,null,null,null,null,null,null,null,null]);
specs.forEach((s,i)=>{const r=i+9;for(const [col,extra] of [['D',''],['E',`,@$F$9:$F$${end},">0"`],['F',`,@$F$9:$F$${end},"=0"`],['G',`,@$F$9:$F$${end},"<0"`],['L',`,@$M$9:$M$${end},"无可识别有限上限"`]])summary.getRange(`${col}${r}`).formulas=[['='+count(s,extra)]];
 summary.getRange(`H${r}`).formulas=[[`=IF(D${r}=0,"",E${r}/D${r})`]];summary.getRange(`I${r}`).formulas=[[`=IF(D${r}=0,"",(D${r}-E${r})/D${r})`]];
 for(const [c,sign] of [['J','-'],['K','+']])summary.getRange(`${c}${r}`).formulas=[[`=IF(D${r}=0,"",(I${r}+1.96^2/(2*D${r})${sign}1.96*SQRT(I${r}*(1-I${r})/D${r}+1.96^2/(4*D${r}^2)))/(1+1.96^2/D${r}))`]];
});summary.getRange('A9:L20').format.rowHeight=30;summary.getRange('H9:K20').setNumberFormat('0.00%');summary.getRange('A14:L14').format.fill='#E3F1ED';
summary.getRange('A23').values=[['排除候选存在质量异常的整日后 · 同一固定规则']];summary.getRange('A23').format.font={name:'Arial',size:13,bold:true,color:'#153E48'};header(summary,'A24:I24',['样本范围','规则','日期数','选中数','净申购','净量不变','净赎回','方向命中率','方向误判率']);
const clean=d.quality_summary.filter(s=>['2025跨期回放','2026新增29日','2026后续检验73日'].includes(s.cohort));summary.getRange('A25:I30').values=clean.map(s=>[s.cohort,s.rule,s.days,null,null,null,null,null,null]);clean.forEach((s,i)=>{const r=25+i;const qual=`,@$O$9:$O$${end},"<>候选数据有缺项/价格精度异常"`;for(const [col,extra] of [['D',''],['E',`,@$F$9:$F$${end},">0"`],['F',`,@$F$9:$F$${end},"=0"`],['G',`,@$F$9:$F$${end},"<0"`]])summary.getRange(`${col}${r}`).formulas=[['='+count(s,qual+extra)]];summary.getRange(`H${r}`).formulas=[[`=IF(D${r}=0,"",E${r}/D${r})`]];summary.getRange(`I${r}`).formulas=[[`=IF(D${r}=0,"",1-H${r})`]];});summary.getRange('A25:I30').format.rowHeight=30;summary.getRange('H25:I30').setNumberFormat('0.00%');
summary.getRange('A33').values=[['逐月表现 · 剔除原选中满额样本（含质量标记日）']];summary.getRange('A33').format.font={name:'Arial',size:13,bold:true,color:'#153E48'};
header(summary,'A34:I34',['月份','日期数','保留数','净申购','净量不变','净赎回','方向命中率','方向误判率','上限未识别数']);
const months=d.monthly.filter(s=>s.rule==='剔除原选中满额样本');summary.getRange(`A35:I${34+months.length}`).values=months.map(s=>[s.month,s.days,s.selected,s.positive,s.zero,s.negative,null,null,s.unknown_cap]);months.forEach((s,i)=>{const r=i+35;summary.getRange(`G${r}`).formulas=[[`=IF(C${r}=0,"",D${r}/C${r})`]];summary.getRange(`H${r}`).formulas=[[`=IF(C${r}=0,"",1-G${r})`]];});summary.getRange(`A35:I${34+months.length}`).format.rowHeight=27;summary.getRange(`G35:H${34+months.length}`).setNumberFormat('0.00%');
const start=37+months.length;summary.getRange(`A${start}`).values=[['未纳入日期：缺少必要源数据或估值异常（不会作为空仓计入）']];summary.getRange(`A${start}`).format.font={name:'Arial',size:13,bold:true,color:'#153E48'};
header(summary,`A${start+1}:C${start+1}`,['交易日','原因','缺失路径见报告']);const ex=d.excluded_dates;summary.getRange(`A${start+2}:C${start+1+ex.length}`).values=ex.map(x=>[new Date(x.day.slice(0,4)+'-'+x.day.slice(4,6)+'-'+x.day.slice(6)+'T00:00:00Z'),x.reason==='outside_comparable_mainline_dates'?'超出可比主线范围':'缺少估值/PCF或估值异常','']);summary.getRange(`A${start+2}:A${start+1+ex.length}`).setNumberFormat('yyyy-mm-dd');summary.getRange(`A${start+2}:C${start+1+ex.length}`).format.rowHeight=25;
wb.recalculate();await fs.writeFile(out+'inspect.json',JSON.stringify(await wb.inspect({kind:'region',sheetId:'结果汇总',range:'A8:L20',maxChars:10000,tableMaxRows:13,tableMaxCols:12})));
await fs.writeFile(out+'formula_errors.json',JSON.stringify(await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:30},maxChars:3000})));
for(const [name,range,file] of [['结果汇总','A1:L20','summary.png'],['结果汇总','A23:I30','quality.png'],['每日择优','A8:O16','daily.png'],['最终汇率对照','A8:O16','final.png'],['事后重选对照','A8:O16','rerank.png']]){const p=await wb.render({sheetName:name,range,scale:1,format:'png'});await fs.writeFile(out+file,new Uint8Array(await p.arrayBuffer()));}
for(const [name,rows,file] of [['每日择优',d.main,'daily_values'],['最终汇率对照',d.final,'final_values'],['事后重选对照',d.reranked,'rerank_values']]){const row=rows.findIndex(r=>r.date==='2026-06-25')+9;for(const [range,part] of [[`A${row}:H${row+5}`,'left'],[`I${row}:O${row+5}`,'right']]){const p=await wb.render({sheetName:name,range,scale:1,format:'png'});await fs.writeFile(out+file+'_'+part+'.png',new Uint8Array(await p.arrayBuffer()));}}
const x=await SpreadsheetFile.exportXlsx(wb);await x.save(out+'每日择优_2025与2026扩充回测.xlsx');console.log('Workbook exported');
