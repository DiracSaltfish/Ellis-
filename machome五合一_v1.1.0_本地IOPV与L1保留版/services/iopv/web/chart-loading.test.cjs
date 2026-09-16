const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/app.js','utf8');
const snippet=source.slice(source.indexOf('function chartDates()'),source.indexOf('for(const b of document.querySelectorAll(\'[data-days]\')'));
const elements={date:{value:'2026-09-15'},'chart-status':{},tooltip:{style:{}}};
const calls=[];let delayA=true;
const context={ChartRange:require('./chart-range.js'),AbortController,setTimeout,clearTimeout,Date,Map,
 $:id=>elements[id]||(elements[id]={}), selectedRow:()=>null,isChartMinute:()=>true,
 renderNonConnect(){},renderDailyShares(){},renderDetail(){},draw(){},
 fetch:async path=>{calls.push(path);const q=new URL(path,'http://test').searchParams;const symbol=q.get('symbol'),date=q.get('date');
 if(symbol==='A'&&delayA)await new Promise(resolve=>setTimeout(resolve,30));
 if(path.includes('/minutes?'))return {ok:true,json:async()=>[{symbol,trade_date:date,minute:'09:30'}]};
 return {ok:true,json:async()=>({symbol,trade_date:date})};}};
vm.createContext(context);vm.runInContext("let hover=null,loadEpoch=0,historyAbort=null,selected='A',chartDays=5,historyKey='',history=[],rangeHistory=[],rangeErrors=[],dailyShares=null,sharesError=false,connectInfo=null,connectError=false;const minuteCache=new Map();"+snippet,context);
(async()=>{
 const a=context.loadHistory();vm.runInContext("selected='B'",context);const b=context.loadHistory();await Promise.all([a,b]);
 let state=vm.runInContext('({history,rangeHistory,rangeErrors})',context);
 assert.equal(state.rangeHistory.length,5);assert(state.rangeHistory.every(p=>p.symbol==='B'));assert.equal(state.history.length,1);
 const before=calls.length;await context.loadHistory();assert.equal(calls.length-before,3,'closed dates cached; only end date and two metadata requests');
 vm.runInContext('chartDays=3',context);await context.loadHistory();state=vm.runInContext('({history,rangeHistory})',context);assert.equal(state.rangeHistory.length,3);
 context.fetch=async path=>{if(path.includes('date=2026-09-11'))throw Error('network');return {ok:true,json:async()=>path.includes('/minutes?')?[]:{}}};vm.runInContext('minuteCache.clear()',context);await context.loadHistory();state=vm.runInContext('({rangeHistory,rangeErrors})',context);assert.equal(state.rangeHistory.length,0);assert.equal(state.rangeErrors[0],'2026-09-11');assert.match(elements['chart-status'].textContent,/读取失败/);
 console.log('PASS stale response isolation, cache, 5→3 range, partial network failure');
})().catch(e=>{console.error(e);process.exitCode=1});
