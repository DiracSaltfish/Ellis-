'use strict';
const $=id=>document.getElementById(id);
let data=null,busy=false,sortKey='symbol',descending=false,timer=null,connectionError='';
const rowNodes=new Map();
const numeric=v=>typeof v==='number'&&Number.isFinite(v);
const number=v=>numeric(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:2}):'—';
const setText=(el,text)=>{text=String(text);if(el.textContent!==text)el.textContent=text;};
const setClass=(el,name)=>{if(el.className!==name)el.className=name;};
function value(row,key){const f=row.flow||{},v=row.values||{};return ({symbol:row.symbol,buy:v.etfbuyamount,sell:v.etfsellamount,net:f.net_baskets,ratio:f.ratio})[key];}
function render(){
 if(!data)return;
 const query=$('search').value.trim().toLowerCase();
 const rows=(data.items||[]).filter(r=>(r.symbol+' '+r.name).toLowerCase().includes(query));
 rows.sort((a,b)=>{const x=value(a,sortKey),y=value(b,sortKey);if(x==null)return y==null?0:1;if(y==null)return -1;return (typeof x==='string'?x.localeCompare(y):x-y)*(descending?-1:1);});
 const stale=Boolean(data.feed_stale||connectionError);
 setText($('connection'),connectionError?'连接中断':stale?'后台数据过期':data.monitoring?'Wind 监控中':'非监控时段 / 已停止');
 setClass($('connection'),stale?'hk-warn':'');
 // The backend heartbeat is not a market update; do not turn it into a ticking UI clock.
 setText($('summary'),`${rows.length} / ${(data.items||[]).length} 只 · 系统日期 ${data.trading_day} · ${data.monitoring?'每5秒检查更新':'非监控时段每30秒检查更新'}`);
 if($('summary').title!==$('summary').textContent)$('summary').title=$('summary').textContent;
 const body=$('rows');setClass(body,stale?'hk-muted':'');
 const visible=new Set(rows.map(r=>r.windcode||r.symbol));
 for(const [key,tr] of rowNodes)if(!visible.has(key)){tr.remove();rowNodes.delete(key);}
 let cursor=body.firstElementChild;
 for(const r of rows){const key=r.windcode||r.symbol,f=r.flow||{},v=r.values||{};
 let tr=rowNodes.get(key);
 if(!tr){tr=document.createElement('tr');for(let i=0;i<10;i++)tr.append(document.createElement('td'));const link=document.createElement('a');link.className='hk-code';tr.children[0].append(link);rowNodes.set(key,tr);}
 const title=(r.error||'')+'\n'+(f.basket_status||'')+'\n'+JSON.stringify(r.last_change||[]);
 if(tr.title!==title)tr.title=title;
 const cells=[r.symbol,r.name,number(v.etfbuyamount),number(v.etfsellamount),number(f.buy_baskets),number(f.sell_baskets),number(f.net_baskets),f.ratio_label||'等待数据',r.updated_at?new Date(r.updated_at).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai'}):'—',connectionError?'连接中断，保留上次数据':stale?'后台快照已过期':r.status];
 const link=tr.children[0].firstElementChild;
 const windcode=r.windcode||(/^\d{6}$/.test(r.symbol)?r.symbol+'.SZ':r.symbol);
 const href='/?symbol='+encodeURIComponent(windcode)+'&name='+encodeURIComponent(r.name||r.symbol);if(link.getAttribute('href')!==href)link.setAttribute('href',href);
 cells.forEach((text,i)=>setText(i===0?link:tr.children[i],text));
 // Keep existing rows in place unless filtering or sorting really changes their position.
 if(tr!==cursor)body.insertBefore(tr,cursor);
 cursor=tr.nextElementSibling;
 }
}
function schedule(){
 clearTimeout(timer);timer=null;
 if(!document.hidden)timer=setTimeout(refresh,connectionError?10000:data?.monitoring===false?30000:5000);
}
async function refresh(){
 clearTimeout(timer);timer=null;if(busy)return;busy=true;
 try{
 const response=await fetch('/page-data/hk-connect-redemption',{cache:'no-store',signal:AbortSignal.timeout(4000)});
 if(!response.ok)throw Error(await response.text());
 const next=await response.json();if(next.pool_id!=='hk_connect'||!Array.isArray(next.items))throw Error('数据格式不正确');
 data=next;connectionError='';$('error').hidden=!data.error;setText($('error'),data.error||'');render();
 }catch(e){connectionError=e.message||'请求失败';$('error').hidden=false;setText($('error'),'数据连接异常：'+connectionError);setText($('connection'),'连接中断');render();}
 finally{busy=false;schedule();}
}
$('search').addEventListener('input',render);$('refresh').addEventListener('click',refresh);
for(const button of document.querySelectorAll('[data-key]'))button.addEventListener('click',()=>{descending=sortKey===button.dataset.key?!descending:true;sortKey=button.dataset.key;render();});
document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(timer);timer=null;}else refresh();});
refresh();
