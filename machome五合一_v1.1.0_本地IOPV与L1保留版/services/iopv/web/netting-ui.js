/* Read-only scenario UI; consumes the existing valuation stream and minute archive. */
(function(){
'use strict';
const el=id=>document.getElementById(id),num=id=>el(id).value.trim()===''?NaN:Number(el(id).value);
const money=x=>x==null?'—':x.toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
const nav=x=>x==null?'—':x.toFixed(6);
const text=(id,value)=>{if(el(id).textContent!==value)el(id).textContent=value};
let context='',ctx=null,active=false,timer=null,redraw=()=>{},cacheKey='',cacheResult=null;
function fees(){return [num('net-buy-fee'),num('net-sell-fee')]}
function args(){return [num('net-s'),num('net-r'),num('net-c-adjust'),num('net-v-adjust')]}
function clearInverse(){text('net-inverse-result','输入同一业务日的两侧单篮结算对价与净份额变动后，点击反推。')}
function calculate(p){return Netting.forward(Netting.fromPoint(p,...fees()),...args())}
function update(next){
 ctx=next;const key=next.symbol+'|'+next.date;
 if(key!==context){context=key;active=false;el('net-overlay').checked=false;el('net-forward-form').reset();el('net-inverse-form').reset();el('net-manual-fields').hidden=true;el('net-buy-fee').value='12';el('net-sell-fee').value='12';el('net-c-adjust').value='0';el('net-v-adjust').value='0';clearInverse();}
 const p=next.point;
 let state='缺少所选日期估值；请稍后重试或选择有档案的日期。';
 if(p&&Number.isFinite(p.unit)&&p.unit>0&&p.minute){
  const flags=[];if(p.fx_status==='closed_reference')flags.push('收盘参考汇率');else if(p.fx_status==='historical_final')flags.push('当日最终汇率');else flags.push('预估结算汇率');
  if((p.stale||[]).length)flags.push('含较早成交价');if((p.missing||[]).length)flags.push('成分缺价');
  if(p.trade_date===new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'})&&Date.now()-Date.parse(p.calculated_at)>120000)flags.push('快照距今超过2分钟');
  state=`基准 ${p.trade_date} ${p.minute} · 每篮 ${money(p.unit)} 份 · ${flags.join(' · ')}`;
 }
 text('net-source',state);
 el('net-use-shares').disabled=!Number.isFinite(next.shares?.share_change_10k);
 render();
}
function render(){
 if(!active){text('net-forward-result','默认示例为申购 5 篮、赎回 1 篮。修改后点击计算。');return}
 try{
  const a=calculate(ctx?.point);
  text('net-forward-result',`净${a.n>=0?'申购':'赎回'} ${Math.abs(a.n)} 篮 · 轧差 ${a.m} 篮\n申购方：${a.s?money(a.C)+' 元/篮 · '+nav(a.c)+' 元/份':'无申购'}\n赎回方：${a.r?money(a.V)+' 元/篮 · '+nav(a.v)+' 元/份':'无赎回'}\n平均每申购篮交易费 +${money(a.buyFeePerBasket)} 元；每赎回篮交易费 −${money(a.sellFeePerBasket)} 元\n净交易总费用 ${money(a.totalTradeFee)} 元（按上述假设全市场篮子数）`);
 }catch(e){text('net-forward-result',e.message)}
}
function overlay(data,isPct){
 const enabled=active&&el('net-overlay').checked&&!isPct;
 el('net-chart-legend').hidden=!enabled;
 if(!enabled)return {data,series:[]};
 // Reuse computed points during mouse movement. No timer or new market requests.
 const key=JSON.stringify([context,fees(),args(),data.map(p=>[p.trade_date,p.minute,p.calculated_at,p.unit,p.hkd_assets,p.cny_assets,p.midpoint_fx,p.settlement_buy_fx,p.settlement_sell_fx,p.midpoint_iopv,p.settlement_buy_iopv,p.settlement_sell_iopv,p.missing,p.reasons])]);
 if(key===cacheKey)return cacheResult;
 const out=data.map(p=>{
  if(p.trade_date!==ctx.date)return p;
  try{const a=calculate(p);return {...p,net_creation:a.c,net_redemption:a.v}}catch{return {...p,net_creation:null,net_redemption:null}}
 });
 cacheKey=key;cacheResult={data:out,series:[{id:'netCreation',key:'net_creation',color:'#9b48ba'},{id:'netRedemption',key:'net_redemption',color:'#e07824'}]};return cacheResult;
}
function tooltip(p){
 if(!active||!el('net-overlay').checked||p.trade_date!==ctx?.date)return '';
 try{const a=calculate(p);return `<br>申购对价估值　${nav(a.c)} 元/份（${money(a.C)} 元/篮）<br>赎回对价估值　${nav(a.v)} 元/份（${money(a.V)} 元/篮）`}catch{return '<br>该分钟无法计算轧差对价'}
}
function inversePoint(){
 const minute=el('net-inverse-minute').value;
 if(!minute)return ctx?.point;
 return [...(ctx?.history||[]),ctx?.point].filter(p=>p&&p.symbol===ctx.symbol&&p.trade_date===ctx.date&&p.minute===minute).at(-1);
}
function runInverse(e){
 e.preventDefault();
 try{
  const manual=el('net-manual').checked;let b,label;
  if(manual){b={U:num('net-unit'),M:num('net-m'),B:num('net-b'),D:num('net-d')};label='手工基准（B 已含费、D 已扣费；不再叠加 bp）'}
  else{const p=inversePoint();b=Netting.fromPoint(p,...fees());label=`${p.trade_date} ${p.minute} 网页估值 · 买/卖费率 ${fees().join('/')} bp`}
  const a=Netting.inverse(b,num('net-c'),num('net-v'),num('net-share-change'),num('net-tolerance'),num('net-c-adjust'),num('net-v-adjust'));
  const pair=p=>`申购 ${p.s.toLocaleString('zh-CN',{maximumFractionDigits:4})} 篮 / 赎回 ${p.r.toLocaleString('zh-CN',{maximumFractionDigits:4})} 篮`;
  let message=a.message;
  if(a.status==='unique')message='给定基准及容差下的唯一整数候选：'+pair(a.min)+'。';
  if(a.status==='range')message=(a.estimate?'连续估计：'+pair(a.estimate)+'。\n':'')+(a.max?`整数候选共 ${a.count} 组：${pair(a.min)} 至 ${pair(a.max)}。`:`整数候选至少 ${pair(a.min)}，无有限上界。`)+(a.candidates.length?'\n'+a.candidates.map(pair).join('；'):'');
  text('net-inverse-result',`${ctx.symbol} · ${ctx.date} · ${label}\n${message}${a.unstable?'\n对价差接近金额容差，反推敏感，不能给出可靠单点数量。':''}\n${manual?'结果依赖所填结算基准和轧差规则。':'这是估值模型反推，不是已验证的实际申赎总量。'}\n结果固定于本次点击；更换基准或数据更新后可重新反推。`);
 }catch(err){text('net-inverse-result',err.message)}
}
el('net-forward-form').onsubmit=e=>{e.preventDefault();active=true;el('net-overlay').checked=true;render();redraw()};
el('net-overlay').onchange=()=>redraw();
el('net-use-shares').onclick=()=>{const value=ctx?.shares?.share_change_10k;if(Number.isFinite(value)){el('net-share-change').value=String(value*10000);clearInverse()}};
el('net-manual').onchange=()=>{el('net-manual-fields').hidden=!el('net-manual').checked;clearInverse()};
el('net-fill-basis').onclick=()=>{
 try{const b=Netting.fromPoint(inversePoint(),...fees());for(const [id,k] of [['net-unit','U'],['net-m','M'],['net-b','B'],['net-d','D']])el(id).value=b[k]==null?'':String(b[k]);clearInverse()}catch(e){text('net-inverse-result',e.message)}
};
el('net-inverse-form').onsubmit=runInverse;
el('netting-panel').addEventListener('input',()=>{clearInverse();clearTimeout(timer);timer=setTimeout(()=>{render();redraw()},120)});
window.NettingUI={update,overlay,tooltip,init(fn){redraw=fn}};
})();
