/* Mainland ETF trading calendar. Update annually from the exchange notice.
 * 2026: https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml
 * Unknown years explicitly fall back to weekdays (reported in chart status).
 */
(function(root){
'use strict';
const closedRanges=[['2026-01-01','2026-01-03'],['2026-02-15','2026-02-23'],['2026-04-04','2026-04-06'],['2026-05-01','2026-05-05'],['2026-06-19','2026-06-21'],['2026-09-25','2026-09-27'],['2026-10-01','2026-10-07']];
function shift(date,days){const d=new Date(date+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+days);return d.toISOString().slice(0,10)}
function trading(date){const day=new Date(date+'T12:00:00Z').getUTCDay();return day!==0&&day!==6&&!closedRanges.some(([a,b])=>date>=a&&date<=b)}
function adjacent(date,delta){do{date=shift(date,delta)}while(!trading(date));return date}
function defaultDate(now=new Date()){const parts=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',hourCycle:'h23'}).formatToParts(now).map(p=>[p.type,p.value]));const date=parts.year+'-'+parts.month+'-'+parts.day;return Number(parts.hour)<9||!trading(date)?adjacent(date,-1):date}
function dates(end,count){if(!/^\d{4}-\d{2}-\d{2}$/.test(end))return [];const result=[end];while(result.length<count)result.unshift(adjacent(result[0],-1));return result}
function position(point,days){const [h,m]=point.minute.split(':').map(Number);const minute=h*60+m;return days.indexOf(point.trade_date)*338+minute-570-(minute>=780?60:0)}
function contiguous(a,b){return !!a&&a.trade_date===b.trade_date&&(position(b,[b.trade_date])-position(a,[a.trade_date])===1||(a.minute==='12:00'&&b.minute==='13:00'))}
const api={shift,trading,adjacent,defaultDate,dates,position,contiguous};if(typeof module!=='undefined')module.exports=api;else root.ChartRange=api;
})(typeof window==='undefined'?this:window);
