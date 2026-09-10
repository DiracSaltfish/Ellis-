from pathlib import Path
import json,csv,datetime,hashlib
ROOT=Path(__file__).resolve().parents[1];RAW=ROOT/'raw'
def load(n):return json.loads((ROOT/n).read_text())
def csvrows(n):
 rows=list(csv.DictReader((ROOT/n).open()))
 for row in rows:
  for k,v in row.items():
   if k not in ['date','time','code','name']:
    try:row[k]=float(v) if v else None
    except ValueError:pass
 return rows
summary=load('summary.json');daily=csvrows('daily_reconciliation.csv');stocks=csvrows('constituents.csv');scenarios=csvrows('settlement_scenarios.csv');holdings=csvrows('holdings_comparison.csv')
now=datetime.datetime.now(datetime.timezone.utc).isoformat()
pros=json.loads((RAW/'prospectus_source.json').read_text())['url'];half=json.loads((RAW/'halfyear_source.json').read_text())['url'];notice=json.loads((RAW/'settlement_notice_source.json').read_text())['url']
urls={'pcf':'https://www.efunds.com.cn/fund/513090.shtml','fx':'https://www.safe.gov.cn/AppStructured/hlw/RMBQuery.do','shares':'https://www.sse.com.cn/market/funddata/volumn/etfvolumn/','iopv':'https://yunhq.sse.com.cn:32042/v1/sh1/snap/513090?select=code,name,last,iopv','quotes':'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=hk00388,day,2026-08-27,2026-09-08,20,'}
sections=[]
def md(i,title,body):sections.append({'id':i,'type':'markdown','body':f'## {title}\n\n{body}'})
sections.append({'id':'title','type':'markdown','body':'# 513090 估值与申赎误判复盘'})
md('summary','Executive Summary',f'''**你对最终 NAV 使用中间价的判断，被真实数据验证；“看到 IOPV 溢价 ⇒ 当日会大幅净申购 ⇒ 一申一赎赚钱”这条推理不成立。** 8 个交易日的逐券复算均与官方篮子净值吻合，最大误差仅 0.021 元/篮。9 月 8 日中间价收盘篮子估值为 **1.84332240**，官方精确值为 **1.84332244**。

**盘中参考值混杂，是本次误判的重要来源。** 9 月 8 日 15:00 的中间价篮子估值约 **1.85173**，当时 ETF 价格 1.846，相对它是约 **0.31% 折价**；晚间对最终 NAV 才是约 **0.145% 溢价**。截图 1.8252、上交所公开接口 1.8300 和中间价篮子估值不能直接视为同一口径。官方 IOPV 使用中间价这一点，现有证据没有证实。

**净赎回 17 篮已由上交所原始数据确认，但不能据此算出你的实际亏损。** 还缺总申购/总赎回、实际成交比例与价格、最终汇率、账户分摊及退补款。按“全日同侧平均分摊、仅交易净额、收盘附近成交”的条件模型，本次汇率方向对一申一赎不利；亏损大小取决于净赎回占总赎回的比例，而非直接承担整篮汇差。

分析日为 **2026 年 9 月 8 日**；证据抓取于北京时间 9 月 9 日凌晨。报告将已验证事实、条件测算和待核实事项分开，尚未到账的申赎不作为已实现盈亏。''')
md('nav','正确的收盘估值：当日篮子加现金差额',f'''对本次全部为“允许现金替代”的 17 只成分券，设 qᵢ 为 **T 日 PCF 数量**，Pᵢ 为港股实际收盘价，f 为人民币/港元中间价，U=500,000 份：

**中间价收盘估值 = [Σ(qᵢ × Pᵢ) × f + T 日现金差额] / U。**

9 月 8 日：港股篮子 **1,054,667.005 港元** × **0.86482** = **912,097.119264 元**；加现金差额 **9,564.08 元**，得到 **921,661.199264 元/篮**，即 **1.8433223985 元/份**。官网下一日 PCF 披露的 9 月 8 日篮子净值为 921,661.22 元，两者相差约 **2.07 分钱/篮**，属于逐券金额取分造成的误差。

用盘前就知道的预估现金 **9,547.19 元**代替晚间现金差额，收盘得到 **1.84328862**，四舍五入同样为 **1.8433**；预估现金与最终现金只差 **16.89 元/篮**，本日不可能解释约 1% 的偏差。

**日期不能错位：** 9 月 9 日 PCF 的上一日 NAV/现金差额属于 9 月 8 日，但它的成分券数量属于 9 月 9 日。本报告使用 9 月 8 日数量计算。另以 9 月 9 日数量乘独立查询的 9 月 8 日收盘价及中间价，17 只股票全部与次日 PCF 替代金额吻合，单项误差最大不到 0.005 元。

现金差额是以官方 NAV 定义的残差，因此“最终现金差额 + 篮子”不是完全独立计算真实资产负债表的 NAV。本报告的独立证据在于逐只行情、FX 和日期匹配；**盘前预估现金的收盘检验**避免只靠最终现金残差进行循环验证。来源：[基金官网与 PCF]({urls['pcf']})、[外管局中间价]({urls['fx']})。''')
sections.append({'id':'daily-table','type':'table','tableId':'daily','layout':{'width':12}})
md('daily_note','连续多日吻合，说明中间价篮子法有效','上表 NAV 为次日 PCF 披露的每篮净值除以 50 万，保留了比官网四位小数更多的精度。基金收盘价相对晚间 NAV 的比例是事后口径，不能倒灌为 15:00 的可交易折溢价。8 月 31 日至 9 月 7 日已经连续净赎回，并非只在 9 月 8 日突然发生。所有查询均核对交易日期与 17 只成分券完整性。')
md('intraday','15:00 与港股收盘不同，截图参考值也不同',f'''按 17 只股票各自的分钟行情，15:00 篮子为 **1,059,546.115 港元**，中间价加盘前预估现金得到 **1.85172772**；港股正式收盘篮子比 15:00 下降约 **0.4605%**。因此，ETF 的 15:00 收盘价 1.846 与港股收盘形成的 NAV 1.8433，不是同一时间的资产价格。

本次查得三个不同来源的数值：

- 用户截图及腾讯收盘行情参考字段：**1.8252**，对应通常定义下的溢价 **1.1396%**。
- 上交所公开行情接口显式 `iopv` 字段：**1.8300**，外层日期为 20260908、时间为 162908，对应溢价 **0.8743%**。外层快照时间不等于 IOPV 自身最后计算时间；该接口没有给出字段级时点/汇率来源。
- 15:00 同步成分券、中间价、当日 PCF：**1.85173**，对应约 **-0.3093%**。

**不能把 1.8252 直接认证为上交所当天最终官方 IOPV，更不能把它与 NAV 的差额全部解释为汇差。** 仅作数学反推：若 1.8252 对应本报告的 15:00 股价，隐含汇率约 0.852302；若对应港股收盘价，则约 0.856244。两个解释不同，时点必须先明确。

下图仅展示可重建的中间价篮子估值和 ETF 实际分钟价，**没有把模拟曲线冒充历史官方 IOPV**。港股午间休市及 A 股 15:00 后没有同步 ETF 连续交易，缺口保留。分钟数据源为腾讯，非逐笔交易所直连；同日收盘价又经过次日 PCF 逐券交叉检验。来源：[上交所快照接口]({urls['iopv']})、腾讯行情原始文件。''')
sections.append({'id':'intraday-chart','type':'chart','chartId':'intraday','layout':{'width':12}})
md('rules','IOPV、净值和申赎结算是三个估值对象',f'''**IOPV** 是盘中参考值。2026 年 6 月版招募说明书 PDF 第 37 页规定，允许替代券使用最新成交价和“汇率公允价”，再加预估现金；汇率来源允许实时汇率等公允价格，**没有把 IOPV 永久限定为央行中间价**。基金可另行调整规则，因此具体日内计算源仍应由基金/中证确认。

**最终 NAV** 是基金全部资产减负债后除以份额。说明书 PDF 第 63 页对估值汇率来源留有管理人与托管人协商确定的安排。本次 8 日数据有力验证了这些日期实际使用中间价的人民币篮子计价。

**最终申赎对价** 则包含实际代买卖的成本或收入、未成交部分折算金额及现金差额，并非直接拿 IOPV 或 NAV 下单结算。说明书 PDF 第 43—45 页明确实际成交和未成交分别计价，允许轧差，也允许实际买卖数量不等于净额。你提供的客服截图进一步确认：实际成交部分用港股通结算汇率，未成交及轧差部分用中间价；这条客服证据未确认全部账户分摊细节。

你补充“16:00 左右补券”作为本次测算输入保留；公开条款允许 T 日内任意时刻执行，不能把所有基金实际成交价都确定写成收盘价。来源：[最新版招募说明书]({pros})、用户提供的客服截图。''')
md('flows','净赎回 17 篮不能推出无人申购，也不能推出整篮汇差亏损',f'''上交所直接查询确认：9 月 7 日 **970,998.8 万份**，9 月 8 日 **970,148.8 万份**。差额 -850 万份 ÷ 50 万份/篮 = **净赎回 17 篮**。这部分与你的网站截图一致，无需再怀疑“日期仍是 9 月 7 日”。

但期末份额只给出 **C − R = −17**，没有分别给出总申购 C、总赎回 R。例如 10 申/27 赎、100 申/117 赎，都产生同一净额。你自己的一申一赎对净份额变化贡献为零。

“看见溢价”只能描述某个分母下的价格，不能识别其他投资者的申赎需求，更不能识别基金实际交易比例。市场投资者也可能关注更高的中间价篮子成本，而非较低的屏幕参考值；这与净赎回并不矛盾。不同资金动机没有账户级证据，本报告不将其猜测为已证实原因。来源：[上交所 ETF 规模]({urls['shares']})及已保存的逐日接口结果。''')
md('settlement','一申一赎的收益，取决于实际成交部分如何分摊','''令 V 为一个篮子按收盘中间价折算的股票价值，B 为整篮实际买入人民币成本（含费用），S 为整篮实际卖出人民币收入（扣费用），D 为最终现金差额。

在“全日同侧统一平均分摊、只买卖净额、同一账户不先内部抵消”的**条件模型**下：

- 净申购 C > R：申购结算价 A = D + (R/C)·V + [(C−R)/C]·B；赎回结算价 Z = D + V。一申一赎毛收益 **Z−A = [(C−R)/C]·(V−B)**。
- 净赎回 R > C：A = D + V；Z = D + (C/R)·V + [(R−C)/R]·S。一申一赎毛收益 **Z−A = [(R−C)/R]·(S−V)**。

本日 V≈**912,097.12 元**。仅将你盘中截图的**预测卖券汇率 0.855441**用于情景，假设实际卖价等于收盘且先不计交易费用，则 S−V≈**−9,891.72 元/整篮**。由于 R−C=17，你的一组毛收益是 **−9,891.72 × 17/R 元**，还须扣自身申赎费用及资金成本。这个汇率不是已核实的最终港股通结算汇率。

下表是不同未知总量下的敏感性分析，**不是实际成交记录或已实现亏损**。表内 C、R 视为含你这一组的全日总数。''')
sections.append({'id':'scenario-table','type':'table','tableId':'scenario','layout':{'width':12}})
md('sensitivity','即使净方向判断正确，利润也不会在零点突然跳满','''原会话中的“净申购越过零点，新增篮子突然获得完整低汇率成本”不适用于上述统一平均分摊模型。若 100 赎/101 申，申购侧实际买入比例仅 1/101≈0.99%，只获得很小一部分整篮汇差；不是每个申购者都按整篮港股通汇率结算。收益随净交易比例连续变化。

净赎回本身也不在所有情形下保证亏损。按预测汇率 0.855441，实际卖券的港元加权均价需比收盘价高约 **1.0964%**，才可抵消本例中间价差（尚未含交易费）。如果确实在 16:00 附近执行，更应比较当时真实成交价、收盘竞价和费用，而不是当天早盘价格。

因此当前能确认的是：**在你给出的收盘附近成交条件下，本日净赎回使汇差方向不利；究竟亏多少，要等退补款和分摊明细。** 公开净份额无法唯一反解 R、C 或每只股票实际执行比例。''')
md('holdings','半年报用于解释资产结构，逐日 PCF 更适合篮子估值',f'''2026 年半年报披露全部 **17 只股票**，与本日 PCF 的证券集合相同，但权重和数量已变化。半年报股票占总资产 **96.88%**，换成 NAV 分母实际为 **98.6440%**；剩余净资产不能简单当成无风险现金，因为还包含应收股利、清算款和负债。9 月 8 日本篮最终现金差额约占 NAV **1.0377%**。

例如中信证券的半年报 NAV 权重为 14.47%，本日 PCF 收盘股票价值/NAV 约为 **16.82%**；不能机械套用数月前权重，也不能用指数的单股调样上限否定期间自然漂移。完整数量、价格和逐券检验见下表；相同 NAV 分母的权重对比已另存 holdings_comparison.csv。

腾讯日线还显示香港交易所 9 月 1 日除息 **7.43 港元/股**。用旧价/复权价/原始价混合估值可能产生跳变，日 PCF 的开盘参考价与现金部分应同时处理权益事项。此项用于提示具体复核点，不能在没有网站运行数据的情况下认定它就是网站偏差来源。

7 月 24 日公告曾对国泰君安国际停牌时的申赎清算作特别安排。本次 9 月 8 日已经有该股分钟交易与收盘价，不能把七月停牌自动延伸到九月。半年报足以覆盖最新完整披露持仓，故没有再用更旧年报持仓替代每日 PCF。来源：[2026 半年报]({half})、[特别清算公告]({notice})。''')
sections.append({'id':'stock-table','type':'table','tableId':'stocks','layout':{'width':12}})
md('website','网站的栏目标签与模型含义不一致','''截图已经显示删除线和 stale，且持仓基准为 6 月 2 日，17/17 仅代表覆盖数，不代表权重和估值可靠。按本地代码 internal/valuation/engine.go 的 estimateFromHoldings：先取基准 NAV，再用持仓权重推算股票相对变动；“官方”分支也经过相对价格变动，“实时”分支另除以每只股票的 FXAdjust，再交给 buildRow 展示。

因此截图 **1.8523 不是必然等于原始 9 月 7 日 NAV**。我先前将二者直接对照并怀疑原始净值错配，需要收窄：现已证实本地这条模型路径把“推算后的值”放进官方栏；尚未证实线上原始净值存错。官方 9 月 7 日 NAV 是 **1.8885**，栏目应明确区分原始 NAV、估算 NAV 和结算成本。

本地实现用相对收益法，不是日 PCF 数量×股价加现金的篮子法；所有持仓都参与 FXAdjust 也不等于同时实现中间价 NAV 与现金申赎成本模型。截图中的 1.8396 与图中末端还可能有时点差，不能仅凭图片精确归因其数值偏差。

本次只读核查本地源码，没有把本地版本等同线上构建，也没有修改或部署网站。源码定位、截图信息和检查边界保存在 source_notes.md。''')
md('next','建议把估值拆成三条，并按结算单完成最后一步','''1. **中间价篮子净值线：** 当日 PCF 数量 × 同步原始股价 × 当日中间价，加当日预估现金；单列 ETF 市价相对它的比例。每日港股收盘后用官方 NAV 校验，保留现金修正差。
2. **官方 IOPV 线：** 原样保存来源、字段、有效时点及汇率说明，不用供应商“实时估值”覆盖它；与自行复算值不一致时给出偏差状态。
3. **申购/赎回成本区间：** 分别计算中间价未成交部分与实际交易部分，显示所假设的申赎总量、执行比例、买卖汇率和费用。无法知道净方向时，同时展示两边情景，不把 IOPV 溢价当成净申购概率。
4. **对账这一组：** 申购最终支出 = 初始扣款 − 最终退款 + 补款 + 申购费用；赎回最终收入 = 最终到账 − 赎回补款 − 赎回费用，现金差额若已含在流水中不重复加入。若同日等份额、无额外二级市场买卖，一组净收益就是最终收入减最终支出；费用已净额扣收时同样不要重复扣。

这四步比单纯选择一个“真实估值数字”更接近你实际要解决的交易判断问题。''')
md('questions','需要基金运营确认的最后几项','''可以将下面这段作为询证草稿，自行向基金/券商确认：

“请确认 513090 在 2026 年 9 月 8 日的 IOPV 实际计算汇率来源及有效时点。上交所公开快照 iopv=1.8300，而供应商参考值=1.8252，二者是否同一字段？申赎结算请提供全日总申购/总赎回篮数、各券实际代买/代卖数量、单位成交成本/收入及结算汇率；同一账户一申一赎是否先净额抵消，还是各自参加全日同侧平均分摊？请按该账户实际退补款说明现金差额与费用是否已包含。”

这只是本地草稿，未向任何人发送。''')
md('caveats','结论边界','''收盘中间价篮子法和净赎回份额已验证；逐分钟官方 IOPV、最终港股通汇率、基金实际补券明细和账户最终流水尚缺。你补充的 16:00 附近执行用于条件测算，不被提升为本次基金成交单证据。腾讯分钟与收盘行情用于独立市场复算，收盘逐券与 PCF 金额交叉一致；分钟有效时间仍不具有逐笔交易所级精度。

指定 TGW Mac 工程文档已读取：本地没有真实账号配置，历史快照公开验收范围也未覆盖 513090/港股全篮历史。因此没有改 SDK 白名单或将未验接口强行用于定论，改用已保存的公开行情。所有数据和脚本在本分析目录，网页报告是本次研究快照，不是实时行情服务。''')
# Sources use portable relative identity; raw files contain the full original HTTP evidence.
sources=[{'id':'calc','label':'逐日 PCF × 腾讯独立行情 × 外管局中间价复算','path':'scripts/analyze.py','query':{'engine':'Python','language':'python','description':'按 PCF 交易日连接17只股票原始日线/分钟价格；现金差额取下一交易日PCF，估值汇率取SAFE当日港元中间价。','tables_used':['raw/pcf_*_baseinfo.json','raw/pcf_*_stocklist.json','raw/tencent_hk*_day.json','raw/tencent_hk*_minute.json','raw/safe_fx.html'],'filters':['513090；2026-08-28至2026-09-08；17只全部允许现金替代券；人民币/港元','分钟报价仅2026-09-08；同一分钟交集；收盘用未复权日线'],'metric_definitions':['重建NAV=(Σ数量×港元收盘价×中间价+最终现金差额)/500000','盘中中间价估值=(Σ数量×港元分钟价×中间价+预估现金)/500000'],'executed_at':now}}, {'id':'sse','label':'上交所清算后ETF总份额','href':urls['shares'],'query':{'description':'读取COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L并筛选SEC_CODE=513090，单位为万份。','tables_used':['raw/shares_2026-*_*.json','daily_reconciliation.csv']}}, {'id':'scenario','label':'申赎分摊条件模型与用户提供预测汇率','path':'settlement_scenarios.csv','query':{'description':'非真实成交；假设全日同侧分摊且只卖净额，股价取9/8实际收盘，卖券汇率0.855441为用户截图预测值，未计手续费。','tables_used':['settlement_scenarios.csv','raw/user_fx_prediction.png'],'metric_definitions':['一申一赎毛收益=(R-C)/R×(S-V)，R-C=17'],'executed_at':now}}, {'id':'pros','label':'易方达2026年6月更新招募说明书，PDF37、43—45、63页','href':pros}, {'id':'half','label':'易方达2026年中期报告，PDF13、37—38页','href':half}, {'id':'iopv','label':'上交所行情显式iopv字段（快照日期20260908）','href':urls['iopv']}]
# Precise audit tables use formatted strings to preserve useful digits.
dtab=[]
for r in daily:dtab.append({'date':r['date'],'fx':f"{r['fx']:.5f}",'estimate':f"{r['reconstructed_NAV']:.8f}",'official':f"{r['official_NAV']:.8f}",'error':f"{r['error_CNY_per_basket']:+.4f}",'net':('—' if r['net_baskets'] is None else f"{r['net_baskets']:+.0f}")})
stab=[{'C':int(r['creations']),'R':int(r['redemptions']),'fraction':f"{r['actual_sell_fraction']:.2%}",'pnl':f"{r['roundtrip_CNY']:,.2f}"} for r in scenarios]
ctab=[{'code':r['code'],'name':r['name'],'qty':int(r['q_T']),'p15':f"{r['price_1500']:.3f}",'close':f"{r['close_HKD']:.3f}",'value':f"{r['value_T_CNY']:,.2f}",'next_error':f"{r['next_pcf_error_CNY']:+.5f}"} for r in stocks]
chartrows=[]
for r in csvrows('intraday.csv'):
 if int(r['time'][-2:])%5:continue
 tm=r['time'][:2]+':'+r['time'][2:]
 for label,field in [('中间价篮子估值','estimate_mid'),('ETF成交价','ETF')]:
  if label=='ETF成交价' and r['time']>'1500':continue
  if r[field] is not None:chartrows.append({'time':tm,'series':label,'value':r[field],'stock_HKD':r['stock_HKD'],'fx':.86482,'estimated_cash_CNY':9547.19,'basket_units':500000,'date':'2026-09-08'})
tables=[]
for id,title,cols,sort,src in [('daily','多日收盘复算及净申赎', [('date','日期'),('fx','中间价'),('estimate','重建NAV'),('official','官方精确NAV'),('error','误差 元/篮'),('net','净申购 篮')],'date','calc'),('scenario','净赎回17篮下的条件损益',[('C','总申购篮'),('R','总赎回篮'),('fraction','实际卖出分摊率'),('pnl','一组毛收益 元')],'C','scenario'),('stocks','9月8日逐券数量与价格',[('code','港股代码'),('name','股票'),('qty','T日数量'),('p15','15:00 港元'),('close','收盘 港元'),('value','收盘市值 元'),('next_error','次日PCF误差 元')],'code','calc')]:
 tables.append({'id':id,'title':title,'dataset':id,'columns':[{'field':f,'label':l} for f,l in cols],'defaultSort':{'field':sort,'direction':'asc'},'sourceId':src,'density':'spacious'})
chart={'id':'intraday','title':'9月8日分钟估值与ETF价格','subtitle':'人民币元/份；5分钟抽样。中间价篮子采用当日PCF和预估现金；15:00后仅显示港股篮子。','type':'line','dataset':'intraday','sourceId':'calc','encodings':{'x':{'field':'time','type':'ordinal','label':'北京时间'},'y':{'field':'value','type':'quantitative','label':'人民币元/份'},'color':{'field':'series','type':'nominal'}},'valueFormat':'number','layout':{'width':12}}
artifact={'surface':'report','manifest':{'version':1,'surface':'report','title':'513090 估值与申赎误判复盘','generatedAt':now,'blocks':sections,'tables':tables,'charts':[chart],'sources':sources},'snapshot':{'version':1,'status':'partial','generatedAt':now,'accessIssues':[{'id':'settlement-pending','message':'最终退补款未到账；缺官方IOPV字段级时点、实际补券与账户分摊明细，实际盈亏尚不能定案。'}],'datasets':{'daily':dtab,'scenario':stab,'stocks':ctab,'intraday':chartrows}},'sources':sources}
(ROOT/'artifact.json').write_text(json.dumps(artifact,ensure_ascii=False,indent=2))
# Reproducibility notes and notebook are supporting evidence, not a parallel report.
(ROOT/'source_notes.md').write_text('''# Source and QA notes\n\nDelivery: user-requested local file; portable HTML. Audience: stakeholder. Structure: title → Executive Summary → verified findings/tables/chart → next steps → questions → caveats. Tables chosen for exact multi-day and constituent audit; one chronological chart for synchronized intraday comparison.\n\nCode inspection: read-only local /Users/ellis/newnavnav/internal/valuation/engine.go, estimateFromHoldings (368–448), discovered via graph, no edits. Not a verified production build. Official-labelled output is a holdings-relative estimate, not necessarily raw NAV.\n\nSDK: documentation read, no local config/galaxy_account.ini; historical snapshots limited to verified target tuples, no SDK mutation. Market data fallback to Tencent, with every closing stock amount checked against next-day manager PCF. Minute effective time remains lower confidence than exchange ticks.\n\nIOPV: Tencent qt array position78=1.8252 also matches supplied screenshot; raw SSE select=iopv=1.8300. Field-level timestamp missing. The SSE line probe returned null for requested fields and is not used as historical IOPV. Failed provider probes retained only as access evidence.\n\nShares: TOT_VOL unit 10,000 shares; check exact STAT_DATE and SEC_CODE after retrieving full response. Net baskets=delta TOT_VOL/50. First sample has no prior baseline in this report.\n\nFX values read from SAFE table; displayed per100 HKD converted to CNY per1 HKD. Roundtrip FX0.855441 and0.855559 are user-screenshot predictions, not independently established final rates.\n\nNo test calls to trading endpoints; no messages sent; no deployment or production modifications.\n''')
nb={'nbformat':4,'nbformat_minor':5,'metadata':{'kernelspec':{'display_name':'Python 3','language':'python','name':'python3'}},'cells':[{'cell_type':'markdown','metadata':{},'source':['# 513090 可复核计算\n','离线重算：在本目录运行。依赖原始 PCF、腾讯行情文件；输入与来源见 source_notes.md。\n','分摊测算为条件模型，不是实际盈亏。']},{'cell_type':'code','metadata':{},'execution_count':None,'outputs':[],'source':['import runpy\n','runpy.run_path("scripts/analyze.py")\n']},{'cell_type':'code','metadata':{},'execution_count':None,'outputs':[],'source':['import sys\n','sys.path.insert(0, "scripts")\n','runpy.run_path("scripts/extend_analysis.py")\n']},{'cell_type':'code','metadata':{},'execution_count':None,'outputs':[],'source':['import json, csv\n','from pathlib import Path\n','rows=list(csv.DictReader(open("daily_reconciliation.csv")))\n','assert len(rows)==8\n','assert all(abs(float(r["error_CNY_per_basket"]))<0.03 for r in rows)\n','assert float(rows[-1]["net_baskets"]) == -17\n','rows\n']}]}
(ROOT/'reproduce.ipynb').write_text(json.dumps(nb,ensure_ascii=False,indent=2))
manifest=[]
for p in sorted(RAW.iterdir()):
 if p.is_file():manifest.append({'file':'raw/'+p.name,'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
(ROOT/'evidence_manifest.json').write_text(json.dumps({'recorded_at':now,'files':manifest},ensure_ascii=False,indent=2))
print('report blocks',len(sections),'chart rows',len(chartrows),'evidence files',len(manifest))
