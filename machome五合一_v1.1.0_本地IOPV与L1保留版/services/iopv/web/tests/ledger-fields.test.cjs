const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'..'),data=JSON.parse(fs.readFileSync(path.join(root,'ledger.json'))),src=fs.readFileSync(path.join(root,'app.js'),'utf8');
const code=src.slice(src.indexOf('let ledger=null'),src.indexOf("fetch('/ledger.json')"));const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let count=0;
for(const [symbol,r]of Object.entries(data.records)){
 const el={};const ctx={selected:symbol,esc:escape,$:()=>el,URL,data};vm.createContext(ctx);vm.runInContext(code+';ledger=data;renderLedger();',ctx);
 for(const k of Object.keys(r).filter(k=>/^(nav_|iopv_|creation_replacement_fx_|redemption_replacement_fx_)/.test(k)||['same_day_netting_basis','cash_substitution_fee_rule'].includes(k))){assert(code.includes(k),k);if(r[k])assert(el.innerHTML.includes(escape(r[k])),symbol+' '+k);count++}
 assert(!el.innerHTML.includes('RTGS 依据类型'));assert(!el.innerHTML.includes('验收依据与备注'));
}
console.log('ledger render checked',Object.keys(data.records).length,'funds',count,'field values');
