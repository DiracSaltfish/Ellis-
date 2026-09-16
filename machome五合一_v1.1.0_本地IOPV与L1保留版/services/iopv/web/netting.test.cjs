const test=require('node:test'),assert=require('node:assert/strict'),N=require('./netting.js');
const near=(a,b,tol=1e-7)=>assert.ok(Math.abs(a-b)<tol,`${a} != ${b}`);
const p={unit:500000,hkd_assets:1000000,cny_assets:10000,midpoint_fx:1,settlement_buy_fx:.99,settlement_sell_fx:.99,midpoint_iopv:2.02,settlement_buy_iopv:2,settlement_sell_iopv:2};
test('12 bp on HK securities only, excludes cash and matched amount',()=>{
 const b=N.fromPoint(p);near(b.M,1010000);near(b.B,1001188);near(b.D,998812);
 const a=N.forward(b,5,1);near(a.C,1002950.4);near(a.V,1010000);near(a.c,2.0059008);near(a.buyFeePerBasket,950.4);near(a.totalTradeFee,4752);
 const zero=N.forward(N.fromPoint(p,0,0),5,1);near(a.C-zero.C,950.4);near(a.V-zero.V,0);
 const r=N.forward(b,1,5);near(r.C,1010000);near(r.V,1001049.6);near(r.sellFeePerBasket,950.4);
});
test('equal baskets, one side empty, both empty, and zero net trading fees',()=>{
 const b=N.fromPoint(p);for(const v of [1,100,999999]){const a=N.forward(b,v,v);near(a.C,b.M);near(a.V,b.M);near(a.totalTradeFee,0)}
 assert.equal(N.forward(b,3,0).V,null);near(N.forward(b,3,0).C,b.B);assert.equal(N.forward(b,0,3).C,null);near(N.forward(b,0,3).V,b.D);
 assert.equal(N.forward(b,0,0).C,null);assert.equal(N.forward(b,0,0).V,null);
});
test('forward and inverse round trips for net buy/sell, with adjustments and different fees',()=>{
 const b=N.fromPoint(p,12,23);
 for(let s=1;s<=20;s++)for(let r=1;r<=20;r++)if(s!==r){const a=N.forward(b,s,r,13.22,-5.11);const v=N.inverse(b,a.C,a.V,(s-r)*p.unit,.001,13.22,-5.11);assert.equal(v.status,'unique');assert.deepEqual(v.min,{s,r})}
});
test('manual all-in basis is not charged twice; zero and negative spreads work',()=>{
 const b={U:100,M:1000,B:1005,D:1010};let a=N.forward(b,5,1);near(a.C,1004);assert.deepEqual(N.inverse(b,a.C,a.V,400,.01).min,{s:5,r:1});
 a=N.forward(b,1,5);near(a.V,1008);assert.deepEqual(N.inverse(b,a.C,a.V,-400,.01).min,{s:1,r:5});
});
test('zero net and collapsed spread cannot identify hidden matched volume',()=>{
 const b=N.fromPoint(p);assert.equal(N.inverse(b,b.M,b.M,0).status,'ambiguous');assert.equal(N.inverse(b,b.M+10,b.M,0).status,'inconsistent');
 assert.equal(N.inverse({...b,B:b.M},b.M,b.M,500000).status,'ambiguous');
});
test('incompatible consideration and noninteger flows never manufacture an integer result',()=>{
 const b=N.fromPoint(p);assert.equal(N.inverse(b,b.M+100,b.M,500000).status,'inconsistent');assert.equal(N.inverse(b,1005000,b.M+100,500000).status,'inconsistent');
 assert.throws(()=>N.inverse(b,1005000,b.M,500001),/整数倍/);
 const a=N.forward(b,5,1);assert.equal(N.inverse(b,a.C+40,a.V,4*p.unit,0).status,'inconsistent');
});
test('near-zero price difference yields range or no finite upper bound, not fake point certainty',()=>{
 const b=N.fromPoint(p);const a=N.forward(b,1000000,999999);const v=N.inverse(b,a.C,a.V,p.unit,1);assert.equal(v.status,'range');assert.equal(v.max,null);assert.equal(v.unstable,true);
 const many=N.inverse(b,...(()=>{const a=N.forward(b,1000,999);return[a.C,a.V]})(),p.unit,.1);assert.equal(many.status,'range');assert(many.count>1);
});
test('invalid rates, missing precise assets, missing price and NaN rejected; balanced case needs no buy/sell quote',()=>{
 assert.throws(()=>N.fromPoint(p,-1,12));assert.throws(()=>N.fromPoint(p,NaN,12));assert.throws(()=>N.fromPoint({...p,hkd_assets:null}));assert.throws(()=>N.fromPoint({...p,missing:['00001.HK']}));
 const b=N.fromPoint({...p,settlement_buy_fx:null,settlement_sell_fx:null});near(N.forward(b,1,1).C,b.M);assert.throws(()=>N.forward(b,2,1));
 assert.throws(()=>N.forward(N.fromPoint(p),.5,1));assert.throws(()=>N.forward(N.fromPoint(p),-1,1));assert.throws(()=>N.forward(N.fromPoint(p),NaN,1));
});
