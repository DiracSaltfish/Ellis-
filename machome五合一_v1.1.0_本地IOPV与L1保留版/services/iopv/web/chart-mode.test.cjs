const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/app.js','utf8').match(/function chartSeries\([^\n]+/)[0];
const ctx={};vm.createContext(ctx);vm.runInContext(source,ctx);
assert.equal(ctx.chartSeries('valuation','buy').length,3);
assert.deepEqual(Array.from(ctx.chartSeries('premium','sell'),x=>x.key),['midpoint_premium_pct','settlement_sell_premium_pct']);
assert.equal(ctx.chartSeries('valuation','sell')[1].key,'settlement_sell_iopv');
console.log('PASS single-chart price/premium series and settlement direction');
