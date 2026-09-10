'use strict';
const $ = id => document.getElementById(id);
const number = new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 4});
const fmt = x => x == null ? '—' : number.format(x);
const signed = x => x == null ? '—' : (x > 0 ? '+' : '') + fmt(x);
const pct = (a, b) => a == null || b == null || b === 0 ? '—' : signed((a-b)/b*100) + '%';
const state = {data:null, fund:null, stock:null, lo:0, hi:0, hover:null};
let geometry, drag = null, brush = null;
function text(id, value) { $(id).textContent = value; }
function error(message) { text('error', message); $('error').hidden = !message; }
function delta(i) { const v=state.stock.values; return i>0 && v[i]!=null && v[i-1]!=null ? v[i]-v[i-1] : null; }
function status(i) {
  const v=state.stock.values;
  if (v[i]==null) return i>0 && v[i-1]!=null ? '未列入（退出记录）' : '未列入';
  if (i===0) return '起始记录';
  if (v[i-1]==null) return '重新/首次列入';
  return delta(i)!==0 ? '数量变化' : '数量不变';
}
function changed(i) { return i>0 && state.stock.values[i]!==state.stock.values[i-1]; }
function setRange(lo, hi) {
  const n=state.fund.dates.length;
  let span=Math.min(n-1, Math.max(0, Math.round(hi-lo)));
  lo=Math.max(0, Math.min(n-1-span, Math.round(lo)));
  state.lo=lo; state.hi=lo+span; state.hover=null;
  $('tooltip').hidden=true;
  document.querySelectorAll('[data-window]').forEach(b=>b.classList.remove('active'));
  render();
}
function fullRange() { setRange(0,state.fund.dates.length-1); document.querySelector('[data-window="all"]').classList.add('active'); }
function zoom(factor, anchor=.5) {
  const span=state.hi-state.lo, min=Math.min(4,state.fund.dates.length-1);
  const next=Math.min(state.fund.dates.length-1,Math.max(min,Math.round(Math.max(span,1)*factor)));
  const center=state.lo+span*anchor;
  setRange(center-next*anchor,center+next*(1-anchor));
}
function selectStock(ticker) {
  state.stock=state.fund.stocks.find(s=>s.code===ticker) || state.fund.stocks[0];
  state.hover=null; $('tooltip').hidden=true;
  try {localStorage.setItem('pcf-selection',state.fund.code+':'+state.stock.code);} catch {}
  renderList(); render();
}
function renderList() {
  const query=$('search').value.trim().toLowerCase();
  const stocks=state.fund.stocks.filter(s=>[s.code,...s.aliases].join(' ').toLowerCase().includes(query));
  text('stockCount',`${stocks.length} / ${state.fund.stocks.length} 个历史成分券`);
  const fragment=document.createDocumentFragment();
  for (const stock of stocks) {
    const b=document.createElement('button'); b.className='stock'+(stock===state.stock?' selected':'');
    b.setAttribute('aria-pressed',String(stock===state.stock)); b.title=stock.code+' '+stock.name;
    for (const [cls,value] of [['dot'+(stock.active?'':' inactive'),''],['ticker',stock.code],['name',stock.name]]) {
      const e=document.createElement('span'); e.className=cls; e.textContent=value; b.append(e);
    }
    b.addEventListener('click',()=>selectStock(stock.code)); fragment.append(b);
  }
  if (!stocks.length) { const e=document.createElement('p');e.className='empty-list';e.textContent='没有匹配的成分券';fragment.append(e); }
  $('stockList').replaceChildren(fragment);
}
function render() {
  if (!state.stock) return;
  const {fund,stock,lo,hi}=state, dates=fund.dates, v=stock.values;
  text('stockName',stock.name);text('stockCode',stock.code+' · HK');
  for(const id of ['dateStart','dateEnd']){$(id).min=dates[0];$(id).max=dates.at(-1);}
  text('stockMeta',`逐日 PCF 清单数量 · 股 / 最小申购赎回单位 · ${stock.active?'最新清单在列':'历史成分券'} · 非基金实际持仓`);
  text('lastQty',v[hi]==null?'未列入':fmt(v[hi]));text('lastDate','窗口末日 '+dates[hi]);
  text('lastDelta',signed(delta(hi)));$('lastDelta').className=delta(hi)>0?'positive':delta(hi)<0?'negative':'';
  text('deltaPct',`较上一 PCF 日 ${pct(v[hi],v[hi-1])}`);
  const indices=[];let changes=0;
  for(let i=lo;i<=hi;i++){if(v[i]!=null)indices.push(i);if(i>lo && delta(i)!=null && delta(i)!==0)changes++;}
  const first=indices[0],last=indices.at(-1);
  text('rangeDelta',indices.length?signed(v[last]-v[first]):'—');
  text('rangeDates',indices.length?`${dates[first]} → ${dates[last]}`:'窗口内没有记录');
  text('changeCount',fmt(changes));text('observed',`${indices.length} 个有值日 / ${hi-lo+1} 个 PCF 日`);
  $('dateStart').value=dates[lo];$('dateEnd').value=dates[hi];
  text('rangeLabel',`${dates[lo]} — ${dates[hi]} · ${hi-lo+1} 个 PCF 日 / 共 ${dates.length} 日`);
  $('noData').hidden=indices.length>0;
  text('sourceNote',`数据：${state.data.source} · ${fund.code} · ${dates[0]} 至 ${dates.at(-1)} · ${fmt(fund.rows)} 条记录 · 文件更新 ${state.data.source_modified.replace('T',' ')} · SHA256 ${state.data.sha256.slice(0,12)}`);
  renderTable(); draw();
}
function rowIndices() {
  const result=[];for(let i=state.hi;i>=state.lo;i--) if(!$('onlyChanges').checked || changed(i))result.push(i);return result;
}
function renderTable() {
  const fragment=document.createDocumentFragment();
  for(const i of rowIndices()) {
    const tr=document.createElement('tr'), d=delta(i), v=state.stock.values;
    const values=[state.fund.dates[i],v[i]==null?'—':fmt(v[i]),signed(d),pct(v[i],v[i-1]),state.stock.flags[i]||'—',status(i)];
    values.forEach((value,j)=>{const td=document.createElement('td');td.textContent=value;if(j===2)td.className=d>0?'positive':d<0?'negative':'';tr.append(td);});
    fragment.append(tr);
  }
  if(!fragment.childNodes.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=6;td.textContent='当前范围没有符合筛选条件的记录';tr.append(td);fragment.append(tr);}
  $('details').replaceChildren(fragment);
}
function canvasContext(id) {
  const canvas=$(id),r=canvas.getBoundingClientRect(),dpr=window.devicePixelRatio||1;
  canvas.width=Math.round(r.width*dpr);canvas.height=Math.round(r.height*dpr);
  const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.font='11px system-ui';return {ctx,w:r.width,h:r.height};
}
function line(ctx,x1,y1,x2,y2,color,width=1) {ctx.strokeStyle=color;ctx.lineWidth=width;ctx.beginPath();ctx.moveTo(x1,y1);ctx.lineTo(x2,y2);ctx.stroke();}
function draw() {
  if(!state.stock)return;
  const {ctx,w,h}=canvasContext('chart'),{lo,hi,stock,fund}=state,v=stock.values;
  const left=67,right=w-24,top=30,bottom=h*.64,deltaMid=h*.81,deltaHeight=h*.105;
  const x=i=>lo===hi?(left+right)/2:left+(i-lo)/Math.max(1,hi-lo)*(right-left);
  const visible=v.slice(lo,hi+1).filter(q=>q!=null);
  let low=visible.length?Math.min(...visible):0,high=visible.length?Math.max(...visible):1;
  const pad=Math.max((high-low)*.1,Math.abs(high)*.008,1);
  low=$('zeroAxis').checked?0:Math.max(0,low-pad);high+=pad;
  const y=q=>bottom-(q-low)/(high-low)*(bottom-top);
  geometry={left,right,x,y,w,h};
  ctx.fillStyle='#73858e';ctx.fillText($('zeroAxis').checked?'数量（股）· 零起点':'数量（股）· 自动纵轴',left,15);
  for(let t=0;t<=4;t++){
    const q=low+(high-low)*t/4,py=y(q);line(ctx,left,py,right,py,'#e8edef');
    ctx.fillStyle='#73858e';ctx.textAlign='right';ctx.fillText(fmt(Math.round(q*100)/100),left-10,py+4);
  }
  ctx.save();ctx.beginPath();ctx.rect(left-3,top-3,right-left+6,bottom-top+6);ctx.clip();
  ctx.strokeStyle='#087e82';ctx.lineWidth=2;ctx.beginPath();let previous=null;
  for(let i=lo;i<=hi;i++){
    if(v[i]==null){previous=null;continue;}
    if(previous==null)ctx.moveTo(x(i),y(v[i]));else{ctx.lineTo(x(i),y(v[previous]));ctx.lineTo(x(i),y(v[i]));}previous=i;
  }ctx.stroke();
  for(let i=lo;i<=hi;i++)if(v[i]!=null&&(hi-lo<65||v[i-1]==null||v[i+1]==null)){
    ctx.fillStyle='#087e82';ctx.beginPath();ctx.arc(x(i),y(v[i]),2.5,0,Math.PI*2);ctx.fill();
  }ctx.restore();
  let maxDelta=1;for(let i=lo;i<=hi;i++)maxDelta=Math.max(maxDelta,Math.abs(delta(i)||0));
  line(ctx,left,deltaMid,right,deltaMid,'#bfcbd0');ctx.fillStyle='#73858e';ctx.textAlign='left';ctx.fillText('较上一 PCF 日变化（股）',left,bottom+26);
  ctx.textAlign='right';ctx.fillText('+'+fmt(maxDelta),left-10,deltaMid-deltaHeight+4);ctx.fillText('0',left-10,deltaMid+4);ctx.fillText('−'+fmt(maxDelta),left-10,deltaMid+deltaHeight+4);
  const bw=Math.min(12,Math.max(1,(right-left)/Math.max(hi-lo,1)*.65));
  for(let i=lo;i<=hi;i++){
    const d=delta(i);if(d==null||d===0)continue;const py=deltaMid-d/maxDelta*deltaHeight;
    ctx.fillStyle=d>0?'#19875b':'#c55942';ctx.fillRect(x(i)-bw/2,Math.min(py,deltaMid),bw,Math.max(1,Math.abs(py-deltaMid)));
  }
  const ticks=Math.max(1,Math.min(6,Math.floor((right-left)/105),hi-lo));
  for(let t=0;t<=ticks;t++){
    const i=Math.round(lo+(hi-lo)*t/ticks);ctx.fillStyle='#73858e';ctx.textAlign=t===0?'left':t===ticks?'right':'center';ctx.fillText(fund.dates[i],x(i),h-9);
    if(lo===hi)break;
  }
  if(state.hover!=null){const i=state.hover;ctx.setLineDash([3,3]);line(ctx,x(i),top,x(i),deltaMid+deltaHeight,'#889da4');ctx.setLineDash([]);if(v[i]!=null){ctx.fillStyle='#087e82';ctx.beginPath();ctx.arc(x(i),y(v[i]),4,0,Math.PI*2);ctx.fill();}}
  drawOverview();
}
function drawOverview() {
  const {ctx,w,h}=canvasContext('overview'),v=state.stock.values,n=v.length,all=v.filter(q=>q!=null);
  const min=Math.min(...all),max=Math.max(...all),x=i=>8+i/Math.max(1,n-1)*(w-16),y=q=>h-10-(q-min)/Math.max(1,max-min)*(h-20);
  ctx.strokeStyle='#6da9a9';ctx.lineWidth=1.4;ctx.beginPath();let prev=null;
  v.forEach((q,i)=>{if(q==null){prev=null;return;}if(prev==null)ctx.moveTo(x(i),y(q));else{ctx.lineTo(x(i),y(v[prev]));ctx.lineTo(x(i),y(q));}prev=i;});ctx.stroke();
  const a=x(state.lo),b=x(state.hi);ctx.fillStyle='rgba(232,238,240,.7)';ctx.fillRect(0,0,a,h);ctx.fillRect(b,0,w-b,h);
  ctx.fillStyle='rgba(8,126,130,.06)';ctx.fillRect(a,0,b-a,h);ctx.strokeStyle='#087e82';ctx.strokeRect(a,1,Math.max(1,b-a),h-2);
  for(const px of [a,b]){ctx.fillStyle='#087e82';ctx.fillRect(px-3,h/2-11,6,22);}
}
function pointerX(event,canvas) {return event.clientX-canvas.getBoundingClientRect().left;}
function hover(event) {
  if(!geometry||!state.stock)return;const px=pointerX(event,$('chart'));
  const i=Math.max(state.lo,Math.min(state.hi,Math.round(state.lo+(px-geometry.left)/(geometry.right-geometry.left)*(state.hi-state.lo))));
  state.hover=i;const tip=$('tooltip');tip.replaceChildren();
  for(const value of [state.fund.dates[i],`${state.stock.code} ${state.stock.name}`,`PCF 数量：${state.stock.values[i]==null?'未列入':fmt(state.stock.values[i])+' 股'}`,`较前日：${signed(delta(i))} 股 · ${pct(state.stock.values[i],state.stock.values[i-1])}`,status(i)]){const div=document.createElement('div');div.textContent=value;tip.append(div);}
  tip.hidden=false;tip.style.left=Math.max(5,Math.min(px+16,geometry.w-tip.offsetWidth-8))+'px';
  tip.style.top=Math.max(5,Math.min(event.clientY-$('chart').getBoundingClientRect().top+14,geometry.h-tip.offsetHeight-8))+'px';draw();
}
$('chart').addEventListener('wheel',e=>{if(!state.stock)return;e.preventDefault();if(Math.abs(e.deltaX)>Math.abs(e.deltaY))setRange(state.lo+Math.sign(e.deltaX)*Math.max(1,(state.hi-state.lo)*.05),state.hi+Math.sign(e.deltaX)*Math.max(1,(state.hi-state.lo)*.05));else zoom(e.deltaY>0?1.2:.8,Math.max(0,Math.min(1,(pointerX(e,$('chart'))-geometry.left)/(geometry.right-geometry.left))));},{passive:false});
$('chart').addEventListener('pointerdown',e=>{if(!state.stock)return;drag={x:e.clientX,lo:state.lo,hi:state.hi};$('chart').setPointerCapture(e.pointerId);$('chart').focus();});
$('chart').addEventListener('pointermove',e=>{if(drag){const shift=Math.round((drag.x-e.clientX)/(geometry.right-geometry.left)*Math.max(1,drag.hi-drag.lo));setRange(drag.lo+shift,drag.hi+shift);}else hover(e);});
for(const type of ['pointerup','pointercancel','lostpointercapture'])$('chart').addEventListener(type,()=>drag=null);
$('chart').addEventListener('pointerleave',()=>{if(!drag){state.hover=null;$('tooltip').hidden=true;draw();}});
$('chart').addEventListener('dblclick',()=>state.stock&&fullRange());
$('chart').addEventListener('keydown',e=>{if(!state.stock)return;const step=Math.max(1,Math.round((state.hi-state.lo)*.1));if(['ArrowLeft','ArrowRight','+','=','-','Home'].includes(e.key))e.preventDefault();if(e.key==='ArrowLeft')setRange(state.lo-step,state.hi-step);if(e.key==='ArrowRight')setRange(state.lo+step,state.hi+step);if(e.key==='+'||e.key==='=')zoom(.8);if(e.key==='-')zoom(1.2);if(e.key==='Home')fullRange();});
$('overview').addEventListener('pointerdown',e=>{
  if(!state.stock)return;const w=$('overview').getBoundingClientRect().width,px=pointerX(e,$('overview')),scale=(state.fund.dates.length-1)/(w-16),i=(px-8)*scale;
  const mode=Math.abs(i-state.lo)<10*scale?'left':Math.abs(i-state.hi)<10*scale?'right':i>=state.lo&&i<=state.hi?'move':'center';
  if(mode==='center'){const span=state.hi-state.lo;setRange(i-span/2,i+span/2);}
  brush={i,lo:state.lo,hi:state.hi,mode:mode==='center'?'move':mode,scale};$('overview').setPointerCapture(e.pointerId);
});
$('overview').addEventListener('pointermove',e=>{if(!brush)return;const i=(pointerX(e,$('overview'))-8)*brush.scale,shift=i-brush.i;
  if(brush.mode==='left')setRange(Math.max(0,Math.min(brush.hi-1,brush.lo+shift)),brush.hi);
  else if(brush.mode==='right')setRange(brush.lo,Math.min(state.fund.dates.length-1,Math.max(brush.lo+1,brush.hi+shift)));
  else setRange(brush.lo+shift,brush.hi+shift);
});
for(const type of ['pointerup','pointercancel','lostpointercapture'])$('overview').addEventListener(type,()=>brush=null);
$('search').addEventListener('input',()=>state.fund&&renderList());
$('fund').addEventListener('change',()=>{state.fund=state.data.funds.find(f=>f.code===$('fund').value);state.lo=0;state.hi=state.fund.dates.length-1;text('fundName',state.fund.name);$('search').value='';selectStock(state.fund.stocks[0].code);});
document.querySelectorAll('[data-window]').forEach(b=>b.addEventListener('click',()=>{if(!state.stock)return;const n=state.fund.dates.length;setRange(b.dataset.window==='all'?0:Math.max(0,n-Number(b.dataset.window)),n-1);b.classList.add('active');}));
$('applyDates').addEventListener('click',()=>{
  if(!state.stock)return;const a=$('dateStart').value,b=$('dateEnd').value,d=state.fund.dates;
  if(!a||!b||a>b){error('请选择有效日期，开始日期不能晚于结束日期。');return;}
  const lo=d.findIndex(x=>x>=a);let hi=d.length-1;while(hi>=0&&d[hi]>b)hi--;
  if(lo<0||hi<lo){error('所选日期范围没有 PCF 记录。');return;}error('');setRange(lo,hi);
});
$('zoomIn').onclick=()=>state.stock&&zoom(.7);$('zoomOut').onclick=()=>state.stock&&zoom(1.4);$('reset').onclick=()=>state.stock&&fullRange();
$('zeroAxis').onchange=draw;$('onlyChanges').onchange=()=>state.stock&&renderTable();
$('export').onclick=()=>{
  if(!state.stock)return;const {fund,stock}=state;
  const params=new URLSearchParams({fund:fund.code,stock:stock.code,start:fund.dates[state.lo],end:fund.dates[state.hi],changes:$('onlyChanges').checked?'1':'0'}),a=document.createElement('a');
  a.href='/api/export?'+params;a.download='';document.body.append(a);a.click();a.remove();
};
async function load() {
  $('reload').disabled=true;error('');
  try {
    const response=await fetch('/api/data',{cache:'no-store'}),data=await response.json();if(!response.ok)throw Error(data.error||'数据读取失败');
    let saved='520600:00175';try{saved=localStorage.getItem('pcf-selection')||saved;}catch{}
    const [fc,sc]=saved.split(':');state.data=data;state.fund=data.funds.find(f=>f.code===fc)||data.funds[0];
    $('fund').replaceChildren(...data.funds.map(f=>{const opt=document.createElement('option');opt.value=f.code;opt.textContent=f.code;return opt;}));$('fund').value=state.fund.code;text('fundName',state.fund.name);
    state.lo=0;state.hi=state.fund.dates.length-1;$('search').value='';
    for(const id of ['dateStart','dateEnd']){$(id).min=state.fund.dates[0];$(id).max=state.fund.dates.at(-1);}
    selectStock(sc);fullRange();
  }catch(e){error('无法载入 PCF 数据：'+e.message);}finally{$('reload').disabled=false;}
}
$('reload').onclick=load;new ResizeObserver(()=>draw()).observe($('chartWrap'));load();
