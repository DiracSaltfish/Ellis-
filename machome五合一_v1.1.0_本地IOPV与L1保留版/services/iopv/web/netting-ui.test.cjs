const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),N=require('./netting.js');
function harness(){
 const elements=new Map();const defaults={'net-s':'5','net-r':'1','net-buy-fee':'12','net-sell-fee':'12','net-c-adjust':'0','net-v-adjust':'0','net-tolerance':'1'};
 function el(id){if(!elements.has(id))elements.set(id,{value:defaults[id]||'',textContent:'',checked:false,hidden:false,disabled:false,reset(){for(const key of (id==='net-forward-form'?['net-s','net-r']:['net-c','net-v','net-share-change','net-inverse-minute','net-unit','net-m','net-b','net-d','net-tolerance']))el(key).value=defaults[key]||'';if(id==='net-inverse-form')el('net-manual').checked=false},addEventListener(){}});return elements.get(id)}
 const c={window:{},document:{getElementById:el},Netting:N,Date,setTimeout,clearTimeout};vm.createContext(c);vm.runInContext(fs.readFileSync(__dirname+'/netting-ui.js','utf8'),c);return {ui:c.window.NettingUI,el};
}
const p={symbol:'513090.SH',trade_date:'2026-09-15',minute:'16:08',calculated_at:'2026-09-15T16:08:00+08:00',unit:500000,hkd_assets:1000000,cny_assets:10000,midpoint_fx:1,settlement_buy_fx:.99,settlement_sell_fx:.99,midpoint_iopv:2.02,settlement_buy_iopv:2,settlement_sell_iopv:2};
const ctx={symbol:p.symbol,date:p.trade_date,point:p,history:[p],shares:null};
test('stream update preserves inputs; date/fund switch clears previous scenario and settlement inputs',()=>{
 const {ui,el}=harness();ui.update(ctx);el('net-s').value='9';el('net-c').value='1000000';el('net-forward-form').onsubmit({preventDefault(){}});ui.update({...ctx,point:{...p,midpoint_fx:1.01}});assert.equal(el('net-s').value,'9');assert.equal(el('net-c').value,'1000000');assert(el('net-overlay').checked);
 ui.update({...ctx,date:'2026-09-16',point:undefined,history:[]});assert.equal(el('net-c').value,'');assert(!el('net-overlay').checked);assert.match(el('net-source').textContent,/缺少/);
});
test('overlay is read-only, selected-day-only, cached and absent in premium mode',()=>{
 const {ui,el}=harness();ui.update(ctx);el('net-forward-form').onsubmit({preventDefault(){}});
 const prev={...p,trade_date:'2026-09-14'},missing={...p,minute:'10:00',missing:['00001.HK']};const data=[prev,missing,p],r=ui.overlay(data,false);assert.equal(r.series.length,2);assert.equal(r.data[0].net_creation,undefined);assert.equal(r.data[1].net_creation,null);assert(Math.abs(r.data[2].net_creation-2.0059008)<1e-12);assert.equal(p.net_creation,undefined);assert.equal(ui.overlay(data,false),r);assert.equal(ui.overlay(data,true).series.length,0);
});
test('manual all-in costs ignore bp inputs and inverse results stay pinned across stream updates',()=>{
 const {ui,el}=harness();ui.update(ctx);el('net-manual').checked=true;
 for(const [id,v] of Object.entries({'net-unit':500000,'net-m':1010000,'net-b':1001188,'net-d':998812,'net-c':1002950.4,'net-v':1010000,'net-share-change':2000000,'net-buy-fee':300,'net-sell-fee':500}))el(id).value=String(v);
 el('net-inverse-form').onsubmit({preventDefault(){}});assert.match(el('net-inverse-result').textContent,/申购 5 篮 \/ 赎回 1 篮/);const result=el('net-inverse-result').textContent;ui.update({...ctx,point:{...p,midpoint_fx:1.01}});assert.equal(el('net-inverse-result').textContent,result);
});
