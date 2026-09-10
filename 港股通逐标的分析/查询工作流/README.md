# 批量查询工作流

目标：对 200+ 个标的重复回答“最新更新招募说明书、补券时间、RTGS 交收、同日申赎轧差”四个问题，并保留可以复核的证据链。

## 运行顺序

```bash
# 1. 先批量抓取东财“发行运作”公告
python3 query_eastmoney_notices.py --codes 159518

# 2. 临时抓取新浪 PCF 摘要和成份股镜像，用于交叉校验
python3 fetch_sina_pcf.py --codes 159518
```

批量时可直接把带 `.SZ/.SH` 后缀的原始清单作为 `--codes-file`；脚本读取首列、跳过中文表头、去掉市场后缀并保留原始顺序：

```bash
python3 query_eastmoney_notices.py --codes-file /Volumes/Upan/premiumWatchlistSymbols.csv
python3 fetch_sina_pcf.py --codes-file /Volumes/Upan/premiumWatchlistSymbols.csv
```

若只验收前 10 个，可先准备一个只含前 10 行代码的 CSV，或使用 `--codes 159217 520700 ...`。所有结果写入 `../临时数据/<代码>/`。

生成指定批次时，`build_ledger.py` 支持 `--skip` 和 `--limit`，例如重新生成前 10 个台账：

```bash
python3 build_ledger.py --symbols-file /Volumes/Upan/premiumWatchlistSymbols.csv --skip 0 --limit 10 --output-prefix 前10标的_
```

## 运行前提：项目 RTGS 验收口径

只要基金特定招募书或适用的中国结算/交易所规则出现“日间 RTGS 交收”（或“日间实行 RTGS 交收”等同义表述），`rtgs_subscription` 就填 `PASS`。资金足额、券商是否选择/勾单、日终回退方式继续写入备注，不影响该项验收。

## 每个标的的判定顺序

### 1. 找最新文件

- 东财页面 `jjgg_<code>.html` 只作为入口；
- API `JJGG` 的 `type=1` 是发行运作；
- 翻完全部页后，分别筛选“更新招募说明书”和“基金产品资料概要更新”；
- 如果公告详情页和基金管理人/中国结算 PDF 都存在，保存两者，优先以 PDF 原文为结论来源；
- PDF 的更新日期与公告发布日期都要记录，不能只取网页爬取日期。

### 2. 判定补券/代买时间

在招募书“基金份额的申购、赎回”及“现金替代”段落中查找：

```text
T日内买入 / T日后 / T+2 / T+3 / 时间优先 / 实时申报 / 未能买入 / 未购入
```

需要分开记录：

- 申购：基金管理人代投资者买入被替代证券的最迟时点；
- 赎回：基金管理人代投资者卖出被替代证券的最迟时点；
- 未成交证券：按哪个收盘价/估值汇率计退补款；
- 现金替代退补款：明细发送日、交收日。

对 159518，招募书明确是“申购 T 日内买入、赎回 T 日内卖出”。所以台账中的“补券时间”填 T 日内；T 日日终未完成的部分转为按收盘价/最近交易日收盘价估值并计算退补款，T+3 等日期只记录退补款明细和资金交收，不改写为补券日期。

不要把“补券时间”泛化成 ETF 份额到账时间，也不要把境内中国结算的交收日当成境外底层证券的市场交收日。

### 3. 判定 RTGS

查找 `RTGS|实时逐笔全额|逐笔全额非担保|代收代付`，并做拆分：

- 只要命中“日间 RTGS 交收”，该项直接 PASS；
- 申购的 ETF 份额 + 现金替代；
- 赎回的 ETF 份额；
- 申购/赎回的现金替代；
- 现金差额；
- 现金替代退补款。

只有对具体结算内容命中 RTGS，才在该字段写“是”；条件和回退方式仅作备注，不影响项目 PASS。

### 4. 判定同日申赎轧差

必须拆成两层：

1. **中国结算清算层**：看中国结算对应 ETF 类别的结算原则，是多边净额、逐笔全额、RTGS 还是代收代付；
2. **基金管理人底层交易层**：看该基金招募书是否写“时间优先、实时申报”、是否写“按申赎轧差后的净额买卖”，以及 T 日未完成交易如何处理。

按上市市场选规则来源：深市优先查中国结算深圳分公司 ETF 登记结算业务指南；沪市优先查中国结算上海分公司 ETF 登记结算业务指南及上交所 ETF RTGS 技术实施文件。不能用深市规则直接替代沪市规则，或反过来替代。

如果基金特定文件没有写清底层交易层，不要用另一个基金的招募书替代；填 `PENDING`，并生成一条向基金管理人/托管人的确认问题。

## 建议的批量输出字段

推荐把每个基金输出成一行，至少包含：

```text
fund_code, latest_prospectus_date, prospectus_url, pcf_asof,
fund_class, cash_substitution_flag, creation_buy_deadline,
redemption_sell_deadline, subscription_cash_substitution_mode,
subscription_cash_substitution_settlement_time,
subscription_fund_units_settlement_time, subscription_fund_units_available_time,
cash_difference_announcement_time, cash_difference_clearing_time,
cash_difference_settlement_time, subscription_cash_substitution_refund_time,
redemption_fund_units_settlement_time, redemption_cash_substitution_mode,
redemption_cash_substitution_arrival_time, rtgs_subscription, rtgs_fallback,
redemption_settlement, cash_difference_schedule,
cash_substitution_adjustment_schedule, same_day_netting_cnaps,
same_day_netting_manager, evidence_grade, status, open_question
```

其中 `same_day_netting_cnaps` 和 `same_day_netting_manager` 不能合并成一个字段。

新增的时间列按“投资者资金/份额路径”拆分：底层证券的实际补券时间仍看
`creation_buy_deadline` / `redemption_sell_deadline`；现金替代到账看
`redemption_cash_substitution_arrival_time`；现金差额的公告、清算、交收分别看三个对应字段。
字段中的 T+N 必须结合原文注明的开放日、工作日、港股通交易日或共同交易日理解，不能直接按自然日计算。

## 图3扩展字段：收盘净值、汇率与 IOPV

批量台账另行增加以下字段，以覆盖图3中“汇率与折算”和“IOPV 计算方法”的信息密度：

- `nav_calculation_time`、`nav_announcement_time`、`nav_price_basis`、`nav_precision`：基金收盘净值的计算/公告时点、证券价格口径和精度；
- `nav_fx_source`、`nav_fx_normal_method`、`nav_fx_reference_time`、`nav_fx_currency_scope`、`nav_fx_fallback`：基金资产净值估值汇率的原文来源、正常港币汇率结论、取价时点、币种和备用机制；
- `creation_replacement_fx_*`：申购补券实际买入成本及未买入部分折算的汇率规则；
- `redemption_replacement_fx_*`：赎回代卖实际卖出金额及未卖出部分折算的汇率规则；
- `cash_substitution_fee_rule`：实际买入成本是否含费用、实际卖出金额是否扣费用；
- `constituent_suspension_handling`：成分股停牌、长期停牌/流动性不足、复牌及无交易时的处理；
- `subscription_consideration_calculation` / `redemption_consideration_calculation`：申购/赎回对价和现金替代金额的计算组成；
- `subscription_replacement_purchase_rule` / `redemption_replacement_sale_rule`：补券/代卖动作、时点、可不成交及轧差影响；
- `subscription_unpurchased_price_rule` / `redemption_unsold_price_rule`：未补券/未卖出部分的收盘价及无交易兜底价格；
- `iopv_formula`、`iopv_price_basis`、`iopv_fx_source`、`iopv_fx_reference_time`、`iopv_publisher`、`iopv_frequency_precision`：盘中 IOPV 的公式、价格、汇率、发布方、频率和精度。

### PDF 原文驱动的停牌与对价复核

全量台账新增的七个字段均从各标的所选最新招募说明书 PDF（及其文本提取）中定位“现金替代处理程序”后生成，不用其他基金条款补写。对“停牌”会区分：PDF 明确列为必须现金替代、长期停牌导致价格不公允时的估值调整、以及停牌导致当日无交易时的收盘价兜底；没有专门停牌条款的标的会保留这一事实，不强行推断。

申购字段分别记录预收替代金额/保证金、实际买入成本、未买入部分计值和多退少补；赎回字段分别记录实际卖出所得（扣费用）、未卖出部分计值和应支付的赎回现金替代金额。`creation_buy_deadline` / `redemption_sell_deadline` 是动作时点，新增的价格字段回答的是 T 日终未成交部分使用什么价格。

三类汇率必须分开：

1. `nav_fx_*` 只回答“基金收盘净值如何估值”。本批标的均按港股通口径，主结论只展示港币/港元对人民币的正常估值汇率；
2. `creation_replacement_fx_*` / `redemption_replacement_fx_*` 只回答“现金替代实际补券/代卖结算如何折算”；
3. `iopv_*` 只回答“盘中参考净值如何计算和发布”。

如果招募书只写“折算为人民币”但没有写汇率来源或取价时点，台账填“原文未明确”，不能从 IOPV 的实时汇率或同类基金条款反推。图3中的“未明确”因此是有效结果，不是空缺错误。未公布的其他币种、美元中间货币套算等非正常港股通港币路径不进入正式台账主结论；完整原文仅在自动摘录证据中保留。

## 频率与质量控制

- 东财公告：每次批量运行都重新翻页，保存抓取时间和 API 元数据；
- PCF：按交易日抓取，保留原文件；同一交易日重复抓取时以原文件 hash 去重；
- 规则：规则版本变更时重新跑所有已受影响标的；
- 第三方源：只作线索或交叉核对；若与官方源不一致，保留冲突并在台账中升级为 `PASS_WITH_WARNING`；
- 200+ 标的完成后，再按 `PENDING` 聚合出需要统一向基金公司/托管人发出的确认清单。
