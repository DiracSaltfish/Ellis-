from pathlib import Path
R=Path('/Users/ellis/工具程序开发/machome五合一_v1.1.0_本地IOPV与L1保留版/services/iopv')
p=R/'internal/live/store.go';s=p.read_text();s=s.replace('CREATE TABLE IF NOT EXISTS fx_blocks(', 'CREATE TABLE IF NOT EXISTS daily_shares(symbol TEXT NOT NULL,trade_date TEXT NOT NULL,shares_10k REAL,share_change_10k REAL,source TEXT NOT NULL,source_updated_at TEXT NOT NULL,PRIMARY KEY(symbol,trade_date)) WITHOUT ROWID; CREATE TABLE IF NOT EXISTS fx_blocks(',1).replace('ORDER BY trade_date DESC LIMIT 120','ORDER BY trade_date DESC');p.write_text(s)
p=R/'internal/live/http.go';s=p.read_text().replace('mux.HandleFunc("GET /api/v1/minutes",','mux.HandleFunc("GET /api/v1/daily-shares", s.dailySharesHandler)\n\tmux.HandleFunc("GET /api/v1/minutes",',1);p.write_text(s)
p=R/'web/index.html';s=p.read_text().replace('<div class="marketgrid">','<div id="daily-shares" class="daily-shares" role="status" aria-live="polite">所选日期份额变化：加载中…</div>\n<div class="marketgrid">',1);p.write_text(s)
p=R/'web/app.js';s=p.read_text();s=s.replace("let displayedToday=today();","let displayedToday=today();\nlet dailyShares=null,sharesError=false;\nfunction renderDailyShares(){const value=dailyShares?.share_change_10k;const valid=typeof value==='number'&&Number.isFinite(value);$('daily-shares').innerHTML=`<span>${esc($('date').value)} 当日份额变化</span><strong class=\"${valid?(value>0?'positive':value<0?'negative':''):''}\">${valid?(value>0?'+':'')+value.toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})+' 万份':sharesError?'读取失败':dailyShares?'暂无数据':'加载中…'}</strong><small>盘后份额净变化${dailyShares?.shares_10k!=null?' · 当日总份额 '+Number(dailyShares.shares_10k).toLocaleString('zh-CN',{maximumFractionDigits:2})+' 万份':''}</small>`}")
start=s.index('async function loadHistory()');end=s.index("\n$('date').onchange",start)
s=s[:start]+'''async function loadHistory(){
 const epoch=++loadEpoch;history=[];dailyShares=null;sharesError=false;renderDailyShares();draw();
 const query=`symbol=${encodeURIComponent(selected)}&date=${$('date').value}`;
 const fetchJSON=async path=>{const r=await fetch(path);if(!r.ok)throw Error('历史数据读取失败');return r.json()};
 await Promise.allSettled([
  fetchJSON('/api/v1/minutes?'+query).then(v=>{if(epoch===loadEpoch){history=v;renderDetail();draw()}}).catch(e=>{if(epoch===loadEpoch)$('quality').textContent=e.message}),
  fetchJSON('/api/v1/daily-shares?'+query).then(v=>{if(epoch===loadEpoch){dailyShares=v;renderDailyShares()}}).catch(()=>{if(epoch===loadEpoch){sharesError=true;renderDailyShares()}})
 ]);
}'''+s[end:]
s=s.replace("function renderDetail(){renderLedger();const p=selectedRow();if(!p)return;", "function renderDetail(){renderLedger();const p=selectedRow()||history.at(-1);if(!p)return;")
s=s.replace("const v=shown||{};renderComponentIssues(v,historical);", "const v=shown||{};if(historical)$('pcf').textContent=v.components?`PCF ${v.trade_date} · ${v.components} 只成分 · 每篮 ${Number(v.unit).toLocaleString()} 份`:'该日暂无 PCF 档案';if(v.mode==='historical_reconstruction')$('pcf').textContent+=' · 历史重算 · 当日最终汇率';renderComponentIssues(v,historical);")
s=s.replace("let hover=null,chartMode='valuation';", "let hover=null,chartMode='valuation';\nfunction settlementLabel(){return history.some(p=>p.fx_status==='historical_final')?'最终结算':'预估结算'}")
s=s.replace("(isPct?'预估结算溢价率':'预估结算 IOPV')", "(isPct?settlementLabel()+'溢价率':settlementLabel()+' IOPV')")
s=s.replace('<br>预估结算 IOPV　${fmt(p[', '<br>${p.fx_status===\'historical_final\'?\'最终结算\':\'预估结算\'} IOPV　${fmt(p[')
s=s.replace("'暂无该时段记录 · 服务从启动后逐分钟采集'", "'暂无该时段记录'")
s=s.replace("${!v.calculated_at?'等待估值数据':", "${v.mode==='historical_reconstruction'?'历史分钟重建；不提供当时的实时缺价与停牌核实状态':!v.calculated_at?'等待估值数据':")
p.write_text(s)
p=R/'web/style.css';s=p.read_text();s+='\n.daily-shares{display:flex;align-items:baseline;flex-wrap:wrap;gap:12px;margin:0 0 18px;padding:14px 20px;background:#fff;border:1px solid #dce5f1;border-radius:12px;color:#35465e}.daily-shares strong{font-size:21px}.daily-shares small{color:#73839a}\n';p.write_text(s)
