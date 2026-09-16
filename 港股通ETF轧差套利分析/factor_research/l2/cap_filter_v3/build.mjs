import fs from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';
const root=fileURLToPath(new URL('./outputs/',import.meta.url));
const out=root+'01a0915e-f9e1-76e3-bbaa-2329e4b4b7e8/';await fs.mkdir(out,{recursive:true});
const d=JSON.parse(await fs.readFile(root+'data.json','utf8'));const wb=Workbook.create();
const summary=wb.worksheets.add('结果汇总');const main=wb.worksheets.add('原选标剔除满额');const rerank=wb.worksheets.add('事后重选对照');
function base(s,last,end,widths){s.showGridLines=false;s.tabColor='#153E48';s.getRange(`A1:${last}${end}`).format.font={name:'Arial',size:10,color:'#263445'};s.getRange(`A1:${last}${end}`).format.verticalAlignment='center';widths.forEach((w,i)=>s.getRangeByIndexes(0,i,end,1).format.columnWidthPx=w);}
function title(s,text,notes){s.getRange('A2').values=[[text]];s.getRange('A2').format.font={name:'Arial',size:19,bold:true,color:'#153E48'};s.getRange('A2:N2').format.rowHeight=34;notes.forEach((t,i)=>{s.getRange(`A${i+3}`).values=[[t]];s.getRange(`A${i+3}:N${i+3}`).format.rowHeight=22;s.getRange(`A${i+3}`).format.font={name:'Arial',size:10,color:'#526174'};});}
function header(s,range,values){s.getRange(range).values=[values];s.getRange(range).format.fill='#153E48';s.getRange(range).format.font={name:'Arial',size:10,bold:true,color:'#FFFFFF'};s.getRange(range).format.rowHeight=32;}
function detail(s,rows,isRerank){
 const end=8+rows.length;base(s,'N',end,[105,125,110,246,85,143,122,140,110,130,110,194,192,110]);
 title(s,isRerank?'排除候选后重选第一名（事后对照）':'原每日选标剔除达到申购上限样本',[
  isRerank?'本表用盘后净量排除候选再排名，含事后信息，不是实时可执行选标成绩。':'主规则：14:45，盘中汇率代理，隔离513130，原第一名满额则剔除，不递补。',
  '满额代理：当日实际净申购份额 ≥ 当日PCF累计申购上限。空白上限不等于无限额。',
  '净申购小于上限仍可能实际已满；盘后达到上限也不能判断14:45已满。保留不代表可申购。',
  '数量来源：1navs同日份额增减缓存、当日PCF主表。模型不重训，分数未经概率校准。',
 ]);
 header(s,'A8:N8',['交易日','样本阶段','标的代码','基金名称','模型分数','实际净申赎份额','申赎单位份额','累计申购上限份','实际净申赎U','累计申购上限U','原方向结果','本次处理','限额识别状态','原选中标的']);
 s.getRange(`A9:N${end}`).values=rows.map(r=>[new Date(r.date+'T00:00:00Z'),r.phase,r.symbol,r.name,r.score,r.net_shares,r.unit,r.cap_shares,null,null,r.result,r.decision,r.cap_status,r.original_symbol]);
 s.getRange(`A9:N${end}`).format.rowHeight=34;s.getRange(`D9:D${end}`).format.wrapText=true;s.getRange(`L9:M${end}`).format.wrapText=true;
 for(const [c,f] of [['I','=IF(C9="","",F9/G9)'],['J','=IF(H9="","",H9/G9)']]){s.getRange(`${c}9`).formulas=[[f]];s.getRange(`${c}9:${c}${end}`).fillDown();}
 s.tables.add(`A8:N${end}`,true,isRerank?'RerankTable':'CapDailyTable');
 header(s,'A8:N8',['交易日','样本阶段','标的代码','基金名称','模型分数','实际净申赎份额','申赎单位份额','累计申购上限份','实际净申赎U','累计申购上限U','原方向结果','本次处理','限额识别状态','原选中标的']);
 s.getRange(`A9:A${end}`).setNumberFormat('yyyy-mm-dd');s.getRange(`A9:A${end}`).format.horizontalAlignment='left';s.getRange(`E9:E${end}`).setNumberFormat('0.000');s.getRange(`F9:H${end}`).setNumberFormat('#,##0;[Red]-#,##0;0');s.getRange(`I9:J${end}`).setNumberFormat('#,##0.00;[Red]-#,##0.00;0.00');
 rows.forEach((r,i)=>{if(r.excluded)s.getRange(`L${i+9}:M${i+9}`).format.fill='#FEE2E2';else if(r.result.startsWith('未命中'))s.getRange(`K${i+9}`).format.fill='#FEF3C7';});
 s.freezePanes.freezeRows(8);s.freezePanes.freezeColumns(3);
}
detail(main,d.main,false);detail(rerank,d.reranked,true);
base(summary,'J',35,[190,215,100,102,105,105,105,100,120,138]);
title(summary,'排除满额后的方向命中情况',[
 '主规则沿用上一版。以当日PCF上限核对，未把所有200U一律判为满额。',
 '后续44日是此前训练、验证之外的历史检验；全部87日含训练及验证回放。',
 '仅对原第一名做事后剔除。另列“事后重选”对照，不能当作实时策略回测。',
 '命中定义为实际净申购 > 0。净量不变也计误判；这不是收益或申购成功率。',
]);
header(summary,'A8:J8',['样本范围','规则','原样本数','剔除数','保留数','净申购数','净量不变','方向误判率','方向命中率','上限未识别数']);
const periods=[['后续检验44日',['历史压力检验','上轮检验','本轮新增检验']],['本轮新增13日',['本轮新增检验']],['全部87日',null],['训练及验证43日',['训练期回放','验证期回放']]];
const specs=[];for(const [name,phases] of periods){for(const policy of ['原规则','剔除原选中满额样本','事后剔除候选再选第一'])specs.push({name,phases,policy});}
summary.getRange('A9:J20').values=specs.map(s=>[s.name,s.policy,null,null,null,null,null,null,null,null]);summary.getRange('A9:J20').format.rowHeight=30;
function count(sheet,phases,extra=''){const q=`'${sheet}'!`;const terms=(phases||[null]).map(p=>`COUNTIFS(${q}$G$9:$G$95,">0"${p?`,${q}$B$9:$B$95,"${p}"`:''}${extra.replaceAll('@',q)})`);return terms.join('+');}
specs.forEach((s,i)=>{const r=i+9,sh=s.policy==='事后剔除候选再选第一'?'事后重选对照':'原选标剔除满额';const retained=s.policy==='剔除原选中满额样本'?',@$L$9:$L$95,"保留"':'';
 summary.getRange(`C${r}`).formulas=[['='+count('原选标剔除满额',s.phases)]];
 summary.getRange(`D${r}`).formulas=[[s.policy==='剔除原选中满额样本'?'='+count('原选标剔除满额',s.phases,',@$L$9:$L$95,"排除：净量达到/超过上限"'):'=0']];
 summary.getRange(`E${r}`).formulas=[['='+count(sh,s.phases,retained)]];
 summary.getRange(`F${r}`).formulas=[['='+count(sh,s.phases,retained+',@$F$9:$F$95,">0"')]];
 summary.getRange(`G${r}`).formulas=[['='+count(sh,s.phases,retained+',@$F$9:$F$95,"=0"')]];
 summary.getRange(`H${r}`).formulas=[[`=IF(E${r}=0,"",(E${r}-F${r})/E${r})`]];summary.getRange(`I${r}`).formulas=[[`=IF(E${r}=0,"",F${r}/E${r})`]];
 summary.getRange(`J${r}`).formulas=[['='+count(sh,s.phases,retained+',@$M$9:$M$95,"无可识别有限上限"')]];
});
summary.getRange('C9:G20').setNumberFormat('#,##0');summary.getRange('H9:I20').setNumberFormat('0.00%');summary.getRange('J9:J20').setNumberFormat('#,##0');summary.getRange('A10:J10').format.fill='#E3F1ED';
summary.getRange('A23').values=[['后续44日剔除的4个样本']];summary.getRange('A23').format.font={name:'Arial',size:13,bold:true,color:'#153E48'};
header(summary,'A24:E24',['交易日','标的代码','实际净申购U','上限U','样本阶段']);const removed=d.main.filter(r=>r.excluded&&!['训练期回放','验证期回放'].includes(r.phase));
summary.getRange('A25:E28').values=removed.map(r=>[new Date(r.date+'T00:00:00Z'),r.symbol,r.net_U,r.cap_U,r.phase]);summary.getRange('A25:A28').setNumberFormat('yyyy-mm-dd');summary.getRange('A25:A28').format.horizontalAlignment='left';summary.getRange('C25:D28').setNumberFormat('0');summary.getRange('A25:E28').format.rowHeight=26;
summary.getRange('A30').values=[['保留的28个后续样本中，14个没有可识别有限上限；其余14个净量低于上限，也不能证明尚有额度。']];
summary.getRange('A31').values=[['7.14%为2/28的历史方向误判率，Wilson 95%区间约1.98%—22.65%，不能确认未来误判率稳定低于10%。']];
summary.getRange('A32').values=[['份额只提供净量。需要申购总量、实时剩余额度或实际委托回报，才能进一步判断是否申购得进去。']];
summary.getRange('A33').values=[['候选池另有1条训练期净量超过PCF上限记录，已标注待核验并排除；不涉及原每日第一名样本。']];
summary.getRange('A30:J33').format.rowHeight=24;summary.freezePanes.freezeRows(8);
wb.recalculate();
await fs.writeFile(out+'inspect.json',JSON.stringify(await wb.inspect({kind:'region',sheetId:'结果汇总',range:'A8:J20',maxChars:6000,tableMaxRows:13,tableMaxCols:10})));
await fs.writeFile(out+'formula_errors.json',JSON.stringify(await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:30},maxChars:3000})));
for(const [sheetName,range,file] of [['结果汇总','A1:J33','summary.png'],['原选标剔除满额','A8:N15','main.png'],['事后重选对照','A8:N15','rerank.png']]){const p=await wb.render({sheetName,range,scale:1,format:'png'});await fs.writeFile(out+file,new Uint8Array(await p.arrayBuffer()));}
const x=await SpreadsheetFile.exportXlsx(wb);await x.save(out+'排除满额_每日序列与命中率.xlsx');console.log('Workbook exported');
