const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
test('stable rows, cell-only updates, ordering and polling cadence',()=>{
 let writes=0,scheduled=[];
 class El{
 constructor(){this.children=[];this.parent=null;this._text='';this.className='';this.title='';this.value='';}
 get textContent(){return this._text;}set textContent(v){writes++;this._text=v;}
 append(el){this.children.push(el);el.parent=this;}
 get firstElementChild(){return this.children[0]||null;}
 get nextElementSibling(){return this.parent?.children[this.parent.children.indexOf(this)+1]||null;}
 remove(){if(this.parent){this.parent.children.splice(this.parent.children.indexOf(this),1);this.parent=null;writes++;}}
 insertBefore(el,cursor){el.remove();const i=cursor?this.children.indexOf(cursor):this.children.length;this.children.splice(i,0,el);el.parent=this;writes++;}
 getAttribute(name){return this[name]??null;}setAttribute(name,value){this[name]=value;}
 addEventListener(){}
 }
 const ids=Object.fromEntries(['search','connection','summary','rows','error','refresh'].map(k=>[k,new El()]));
 const doc={hidden:false,getElementById:k=>ids[k],createElement:()=>new El(),querySelectorAll:()=>[],addEventListener(){}};
 const ctx=vm.createContext({document:doc,console,AbortSignal,setTimeout:(fn,ms)=>{scheduled.push(ms);return 1;},clearTimeout(){}});
 let src=fs.readFileSync(__dirname+'/hk-redemption.js','utf8');src=src.replace(/refresh\(\);\s*$/,'');vm.runInContext(src,ctx);
 const rows=['159120','158003'].map(symbol=>({symbol,name:symbol,values:{etfbuyamount:100,etfsellamount:20},flow:{ratio_label:'5:1'},status:'数据已更新'}));
 ctx.fixture={pool_id:'hk_connect',monitoring:true,items:rows,trading_day:'2026-09-15'};
 vm.runInContext('data=fixture;render();schedule()',ctx);assert.equal(scheduled.at(-1),5000);
 const first=ids.rows.children[0],cell=first.children[2];assert.equal(first.children[0].firstElementChild.getAttribute('href'),'/?symbol=158003.SZ&name=158003');writes=0;
 vm.runInContext('data.server_time="later";data.sequence=999;render()',ctx);assert.equal(writes,0);assert.equal(ids.rows.children[0],first);
 rows[1].values.etfbuyamount=200;vm.runInContext('render()',ctx);assert.equal(writes,1);assert.equal(first.children[2],cell);
 vm.runInContext('descending=true;render()',ctx);assert.equal(ids.rows.children[1],first);
 ids.search.value='158003';vm.runInContext('render()',ctx);assert.equal(ids.rows.children.length,1);assert.equal(ids.rows.children[0],first);
 vm.runInContext('data.monitoring=false;schedule()',ctx);assert.equal(scheduled.at(-1),30000);
 vm.runInContext('connectionError="offline";render();schedule()',ctx);assert.equal(scheduled.at(-1),10000);assert.match(first.children[9].textContent,/连接中断/);
 doc.hidden=true;const n=scheduled.length;vm.runInContext('schedule()',ctx);assert.equal(scheduled.length,n);
});
