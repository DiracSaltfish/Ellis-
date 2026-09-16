import fs from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';
const out=fileURLToPath(new URL('./outputs/01a0915e-f9e1-76e3-bbaa-2329e4b4b7e8/',import.meta.url));
const data=JSON.parse(await fs.readFile(out+'data.json','utf8'));
const wb=Workbook.create();
const main=wb.worksheets.add('每日序列');const comp=wb.worksheets.add('规则对照');
const fields=['date','phase','symbol','name','score','net_shares','net_wan','unit','net_U','result','reason'];
const headers=['交易日','样本阶段','选中标的','基金名称','模型分数','实际净申赎份额','实际净申赎万份','申赎单位份额','净申赎篮子U','判断结果','空仓原因'];
function build(sh,rows,comparison){
 const keys=comparison?[...fields,'fx','cutoff','quality']:fields;
 const head=comparison?[...headers,'汇率口径','观察截止','标的范围']:headers;
 const end=8+rows.length,last=comparison?'N':'K';
 sh.showGridLines=false;sh.tabColor=comparison?'#64748B':'#0F766E';
 sh.getRange(`A1:${last}${end}`).format.font={name:'Arial',size:10,color:'#263445'};
 sh.getRange(`A1:${last}${end}`).format.verticalAlignment='center';
 const widths=[100,122,105,250,86,140,135,125,105,158,190,145,85,115];
 keys.forEach((_,i)=>sh.getRangeByIndexes(0,i,end,1).format.columnWidthPx=widths[i]);
 sh.getRange('A2').values=[[comparison?'每日选标 · 规则对照':'每日选标与实际净申赎｜87个可计算日']];
 sh.getRange('A2').format.font={name:'Arial',size:19,bold:true,color:'#153E48'};
 sh.getRange(`A2:${last}2`).format.rowHeight=34;
 sh.getRange('A3').values=[[comparison?'按汇率、14:30 / 14:45、是否隔离513130筛选；每个规则每天至多一只。':'主规则：14:45；上一可得结算汇率代理；隔离513130；溢价初筛后，L2模型分数最高的一只。']];
 sh.getRange('A4').values=[['模型分数未经概率校准；主规则不设分数阈值。训练期与验证期属于回放，不可当作独立检验。']];
 sh.getRange('A5').values=[['份额来源：1navs历史份额缓存；当日增减对应当日净申赎。单位取当日PCF；正数为净申购，负数为净赎回。']];
 sh.getRange('A6').values=[['区间：2026-01-05—2026-05-26；空仓保留、实际数量留空；最终汇率对照为事后口径。方向命中不等于盈利。']];
 sh.getRange(`A3:${last}6`).format.rowHeight=22;
 sh.getRange(`A3:${last}6`).format.font={name:'Arial',size:10,color:'#526174'};
 sh.getRange(`A8:${last}8`).values=[head];
 sh.getRange(`A9:${last}${end}`).values=rows.map(r=>keys.map(k=>k==='date'?new Date(r[k]+'T00:00:00Z'):r[k]));
 sh.getRange(`A9:${last}${end}`).format.rowHeight=32;
 sh.getRange(`D9:D${end}`).format.wrapText=true;
 for(const [col,formula] of [['G','=IF(C9="","",F9/10000)'],['I','=IF(C9="","",F9/H9)'],['J','=IF(C9="","空仓",IF(F9>0,"命中净申购",IF(F9<0,"未命中：净赎回","未命中：净量不变")))']]){
  sh.getRange(`${col}9`).formulas=[[formula]];sh.getRange(`${col}9:${col}${end}`).fillDown();
 }
 sh.tables.add(`A8:${last}${end}`,true,comparison?'ComparisonTable':'DailyTable');
 sh.getRange(`A8:${last}8`).format.fill='#153E48';sh.getRange(`A8:${last}8`).format.font={name:'Arial',size:10,bold:true,color:'#FFFFFF'};
 sh.getRange(`A8:${last}8`).format.rowHeight=30;
 sh.getRange(`A9:A${end}`).setNumberFormat('yyyy-mm-dd');
 sh.getRange(`A9:A${end}`).format.horizontalAlignment='left';
 sh.getRange(`E9:E${end}`).setNumberFormat('0.000');
 sh.getRange(`F9:F${end}`).setNumberFormat('#,##0;[Red]-#,##0;0');
 sh.getRange(`G9:G${end}`).setNumberFormat('#,##0.00;[Red]-#,##0.00;0.00');
 sh.getRange(`H9:H${end}`).setNumberFormat('#,##0');
 sh.getRange(`I9:I${end}`).setNumberFormat('#,##0.00;[Red]-#,##0.00;0.00');
 rows.forEach((r,i)=>{if(r.result.startsWith('未命中'))sh.getRange(`J${i+9}:K${i+9}`).format.fill='#FDE8E7';else if(r.result==='空仓')sh.getRange(`J${i+9}:K${i+9}`).format.fill='#EEF1F4';});
 sh.freezePanes.freezeRows(8);sh.freezePanes.freezeColumns(3);
}
build(main,data.main,false);build(comp,data.comparison,true);
wb.recalculate();
await fs.writeFile(out+'workbook_inspect.json',JSON.stringify(await wb.inspect({kind:'region',sheetId:'每日序列',range:'A8:K12',maxChars:4500,tableMaxCols:11,tableMaxRows:5})));
await fs.writeFile(out+'formula_errors.json',JSON.stringify(await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:20},maxChars:2000})));
for(const [sheetName,range,file] of [['每日序列','A1:K15','main_preview.png'],['规则对照','A8:N14','comparison_preview.png']]){
 const p=await wb.render({sheetName,range,scale:1,format:'png'});await fs.writeFile(out+file,new Uint8Array(await p.arrayBuffer()));
}
const x=await SpreadsheetFile.exportXlsx(wb);await x.save(out+'每日选标与实际净申赎_87日.xlsx');
console.log('Exported 87 main rows and 696 comparison rows.');
