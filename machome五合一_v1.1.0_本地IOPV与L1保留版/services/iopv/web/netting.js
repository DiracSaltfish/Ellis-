/* Pure netting model. Amounts are CNY per basket; rates are basis points. */
(function(root){
'use strict';
const finite=x=>typeof x==='number'&&Number.isFinite(x);
const positive=x=>finite(x)&&x>0;
function requireValue(ok,message){if(!ok)throw Error(message)}
function feeRate(bp){requireValue(finite(bp)&&bp>=0&&bp<10000,'交易费率须为 0 至小于 10000 bp');return bp/10000}
function fromPoint(p,buyBP=12,sellBP=12){
 requireValue(p&&positive(p.unit)&&finite(p.hkd_assets)&&p.hkd_assets>=0&&finite(p.cny_assets)&&positive(p.midpoint_fx)&&positive(p.midpoint_iopv),'缺少完整的篮子资产、份额或中间价，暂不能计算');
 requireValue(!(p.missing||[]).length&&!(p.reasons||[]).some(x=>['PCF_PENDING','PCF_DATE_MISMATCH','QUOTE_MISSING','INVALID_VALUATION'].includes(x)),'当前快照的 PCF 或成分报价不完整');
 const h=p.hkd_assets,k=p.cny_assets,fb=feeRate(buyBP),fs=feeRate(sellBP);
 const buy=positive(p.settlement_buy_fx)&&positive(p.settlement_buy_iopv)?h*p.settlement_buy_fx:null;
 const sell=positive(p.settlement_sell_fx)&&positive(p.settlement_sell_iopv)?h*p.settlement_sell_fx:null;
 const b={U:p.unit,M:h*p.midpoint_fx+k,B:buy==null?null:buy*(1+fb)+k,D:sell==null?null:sell*(1-fs)+k,buyFee:buy==null?null:buy*fb,sellFee:sell==null?null:sell*fs};
 requireValue(positive(b.M)&&(b.B==null||positive(b.B))&&(b.D==null||positive(b.D)),'篮子对价无效');return b;
}
function forward(b,s,r,cAdjust=0,vAdjust=0){
 requireValue(positive(b.U)&&positive(b.M),'篮子份额与轧差价值必须大于零');
 requireValue([s,r].every(x=>Number.isSafeInteger(x)&&x>=0&&x<=1e9),'申购和赎回须为 0 至 10 亿的整数篮子');
 requireValue(finite(cAdjust)&&finite(vAdjust),'两侧调整金额必须为有限数值');
 const n=s-r,m=Math.min(s,r);
 if(n>0)requireValue(positive(b.B),'缺少买港股结算基准');
 if(n<0)requireValue(positive(b.D),'缺少卖港股结算基准');
 const C=s?b.M+(n>0?n/s*(b.B-b.M):0)+cAdjust:null;
 const V=r?b.M+(n<0?-n/r*(b.D-b.M):0)+vAdjust:null;
 requireValue((C==null||positive(C))&&(V==null||positive(V)),'调整后的单篮对价必须大于零');
 return {s,r,n,m,C,V,c:C==null?null:C/b.U,v:V==null?null:V/b.U,
  buyFeePerBasket:s&&n>0&&finite(b.buyFee)?n/s*b.buyFee:0,
  sellFeePerBasket:r&&n<0&&finite(b.sellFee)?-n/r*b.sellFee:0,
  totalTradeFee:n>0&&finite(b.buyFee)?n*b.buyFee:n<0&&finite(b.sellFee)?-n*b.sellFee:0};
}
function inverse(b,C,V,shareChange,tolerance=1,cAdjust=0,vAdjust=0){
 requireValue(positive(b.U)&&positive(b.M)&&positive(C)&&positive(V),'请输入有效篮子份额、轧差价值和两侧单篮对价');
 requireValue(finite(shareChange)&&finite(tolerance)&&tolerance>=0&&finite(cAdjust)&&finite(vAdjust),'净份额、金额容差或调整金额无效');
 const rawN=shareChange/b.U,n=Math.round(rawN);
 requireValue(Math.abs(rawN-n)<1e-7&&Math.abs(n)<=1e9,'净份额变动不是当日最小申赎单位的整数倍，请核对日期、单位及份额调整');
 C-=cAdjust;V-=vAdjust;
 const eps=Math.max(1e-7,Math.abs(b.M)*1e-12),tol=tolerance+eps;
 if(n===0)return Math.abs(C-b.M)<=tol&&Math.abs(V-b.M)<=tol?{status:'ambiguous',message:'净变动为零，只能确定申购与赎回相等，双边规模无法唯一反推。'}:{status:'inconsistent',message:'净变动为零，但两侧对价与轧差价值不一致，请核对基准、费用和结算规则。'};
 const buy=n>0,q=Math.abs(n),base=buy?b.B:b.D,invariant=buy?V:C,target=buy?C:V;
 requireValue(positive(base),buy?'缺少含费买入成本基准':'缺少扣费卖出所得基准');
 if(Math.abs(invariant-b.M)>tol)return {status:'inconsistent',message:(buy?'赎回':'申购')+'对价与轧差价值不符，无法按当前模型反推。'};
 const delta=base-b.M,diff=target-b.M;
 if(Math.abs(delta)<=eps)return Math.abs(diff)<=tol?{status:'ambiguous',message:'净交易基准与轧差价值相同，价格无法区分双边规模。'}:{status:'inconsistent',message:'基准没有价差，但输入对价存在价差。'};
 const bounds=[(diff-tol)/delta,(diff+tol)/delta].sort((a,b)=>a-b);
 const low=Math.max(0,bounds[0]),high=Math.min(1,bounds[1]);
 if(high<=0||low>high)return {status:'inconsistent',message:'输入对价超出轧差与净交易基准之间的可行范围。'};
 // Both input sides have a realised consideration, so both sides must exist.
 const lower=Math.max(q+1,Math.ceil(q/high-1e-8)),upper=low>0?Math.floor(q/low+1e-8):null;
 if(!Number.isSafeInteger(lower)||(upper!=null&&!Number.isSafeInteger(upper)))return {status:'ambiguous',message:'对价差太小，反推规模超出可靠整数精度；需要更准确的结算基准。'};
 if(upper!=null&&lower>upper)return {status:'inconsistent',message:'给定容差内无整数篮子解；请核对对价精度或调整金额容差。'};
 const pair=k=>buy?{s:k,r:k-q}:{s:k-q,r:k};
 const w=diff/delta,major=w>0&&w<=1?q/w:null;
 const estimate=major!=null&&Number.isFinite(major)?pair(major):null;
 const count=upper==null?null:upper-lower+1;
 return {status:count===1?'unique':'range',n,estimate,min:pair(lower),max:upper==null?null:pair(upper),count,
  unstable:low===0||Math.abs(diff)<=10*tol,
  candidates:count!=null&&count<=10?Array.from({length:count},(_,i)=>pair(lower+i)):[]};
}
const api={fromPoint,forward,inverse};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.Netting=api;
})(typeof globalThis!=='undefined'?globalThis:this);
