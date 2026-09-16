const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/app.js','utf8'),start=source.indexOf('function exactFundSymbol('),end=source.indexOf("$('search').oninput",start);
const input={value:'159125'},calls=[];const ctx={URL,rows:[{symbol:'159125.SZ'}],ledger:{records:{'513090.SH':{}}},selected:'513090.SH',$:()=>input,location:{href:'http://test/?name=old'},localStorage:{setItem:(...a)=>calls.push(a)},window:{history:{replaceState:(a,b,url)=>calls.push(String(url))}},loadHistory(){},renderList(){},renderDetail(){}};
vm.createContext(ctx);vm.runInContext(source.slice(start,end),ctx);
assert.equal(ctx.exactFundSymbol('159125'),'159125.SZ');assert.equal(ctx.exactFundSymbol(' 513090.sh '),'513090.SH');assert.equal(ctx.exactFundSymbol('15912'),null);assert.equal(ctx.exactFundSymbol('999999'),null);assert.equal(ctx.exactFundSymbol('科技'),null);
assert(ctx.jumpToExactFund());assert.equal(ctx.selected,'159125.SZ');assert(calls.includes('http://test/?symbol=159125.SZ'));
console.log('PASS exact code, suffix, ledger fallback, partial/unknown rejection and direct URL selection');
