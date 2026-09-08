# QueryETFInfo：159518 / 513350 PCF 获取与五合一替代评估

评估日期：2026-09-08；时间均为北京时间。结论：**可以作为 PCF 主来源的候选，但目前不能原样、完整地替换交易所／基金公司来源。** 本轮只新增评估工具、合成测试和脱敏证据，没有修改 SDK 查询实现、五合一业务代码或生产配置，没有部署，也没有调用任何委托接口。

> 后续独立复查：08:13 在 bj 原生 Linux 官方 SDK 再次取回两只 PCF，上海限额直接返回仍为整数零；此时上交所最新文件已与 API 不同日，不能拿新增跨日差异判定映射问题。见[原生 Linux 复查报告](query_etf_info_linux_native_recheck.md)。

## Scope

- 互联网模式 `QueryETFInfo`，`return_df_format=False`，深圳 `(102,159518)`、上海 `(101,513350)`。
- Linux 官方 SDK：两个单代码请求，以及一个包含上述两代码的混合市场请求。
- Mac arm64 `tgw_macos`：同一会话中顺序查询两个单代码请求。
- 对照深交所 XML、上交所 XML，以及五合一实际用于 513350 的富国 JSON 来源。
- 仅两个样本、一次成功会话／平台；没有证明全市场覆盖、大批量上限、盘中稳定性、历史日期查询或异步回调。

## 结论与实测结果

| 检查项 | 159518：深交所 | 513350：上交所 |
|---|---|---|
| Linux 单代码 | 返回码 0；1 份 PCF；297 ms | 返回码 0；1 份 PCF；68 ms |
| Mac 单代码 | 返回码 0；1 份 PCF；141 ms | 返回码 0；1 份 PCF；36 ms |
| API 数据形状 | 35 个基础字段；52 行成分，每行 13 字段 | 35 个基础字段；51 行成分，每行 13 字段 |
| Linux / Mac 字段、类型、成分数 | 一致 | 一致 |
| 交易所 XML | 29 个摘要字段；52 行成分；8 个成分字段 | 15 个摘要字段；51 行成分；8 个成分字段 |
| 成分代码集合 | 与交易所一致 | 与交易所一致 |
| 已逐项比较的基础数字字段 | 12 项全部一致 | 7 项一致；申购／赎回限额 2 项不一致 |
| 已逐行比较的成分字段 | 数量、溢价比例、申购替代金额、赎回替代金额：每项 52/52 一致 | 数量、替代总金额：每项 51/51 一致 |
| 日期 | API 与交易所样本日期一致，但不是查询当天 | API 与交易所样本日期一致，但不是查询当天 |

Linux 在约 07:55 发起，Mac 在约 07:58 发起。时间是会话起始时间，耗时仅为该次 API 查询耗时，不包含登录和交易所 HTTP 请求；不能据此作吞吐量承诺。

**日期限制：** 两份 PCF 的 `current_day` 都为 false。本次证明的是盘前可取回、且与同日期交易所样本相符，不能证明当天 PCF 已经发布。生产必须按所需交易日验收，不能因为 API 返回码为 0 就标记“今日清单就绪”。

Linux 的混合请求返回码 0、耗时 131 ms、返回 2 份 PCF，请求身份集合完整。返回顺序与输入相反，接入时必须按“市场＋代码”关联，不能按数组下标关联。**这只证明官方 Linux 两代码请求可行，不证明 Mac 已经支持批量。**

## PDF 与静态契约

- 中国银河证券格物金融服务平台(TGW)开发手册(C++版)：PDF 第 39、44、85–87 页，核对 QueryETFInfo、请求项与 ETF／成分结构。页码为 PDF 文件页序号。
- AmazingData开发手册：PDF 第 78–81 页，`get_etf_pcf(code_list)` 列表入参及返回字段。此高级接口仅查手册，**本轮实际运行的是 QueryETFInfo，不是 get_etf_pcf**。
- 当前 ABI／字段口径以 `reference/vendor-headers/v1.0.8/linux/tgw_struct.h:948–1017` 为准；请求是 `SubCodeTableItem`，返回包装为 `[(basic_dict, component_dict_list), ...]`。同步请求未使用 callback，异步不在本轮范围。

### Header delta

`MDETFCodeTableRecord` 含 `std::vector<ConstituentStockInfo>`，不能把整个 C++ 对象直接当作普通 ctypes 内存结构拷贝。Python 返回 35 个基础字段和 13 个成分字段；部分字段是市场专用或预留默认值，字段数量更多不代表信息更完整。本轮没有 ABI 修改，既有结构测试随全量测试执行。

| 业务含义 | API 字段与换算 | 深交所 XML | 上交所 XML／当前富国来源 |
|---|---|---|---|
| 清单日期 | `trading_day`：YYYYMMDD → ISO 日期 | TradingDay | TradingDay；富国 tradeDate 等日期字段需按现有解析逻辑校验 |
| 最小申赎单位 | `creation_redemption_unit / 100` | CreationRedemptionUnit | CreationRedemptionUnit；富国 minShdy |
| 预估现金差额 | `estimate_cash_component / 100000` | EstimateCashComponent | EstimatedCashComponent；富国 minYgcash |
| 前日现金差额 | `cash_component / 100000` | CashComponent | PreCashComponent |
| 基金份额净值 | `nav / 1000000` | NAV | NAV |
| 最小单位净值 | `nav_per_cu / 1000000` | NAVperCU | NAVperCU；富国 minShnav |
| 最大现金替代比例 | `max_cash_ratio / 1000000` | MaxCashRatio | MaxCashRatio |
| 成分总数 | `total_record_num / 100` | TotalRecordNum | RecordNumber；不能用仅表示深市成分数的 record_num 代替 |
| 申购／赎回状态 | SZ：creation、redemption 的 Y/N；SSE：creation_redemption_switch | Creation / Redemption | CreationRedemptionSwitch：0 均不允许，1 均允许，2 仅申购，3 仅赎回 |
| 总额／净额／账户限额 | 各 limit 字段 `/100`，头文件标注仅深圳有效 | 对应各 Limit 字段 | 不能使用 API 默认 0 代替上海实际限额 |
| 成分数量 | `component_share / 100` | ComponentShare | Quantity |
| 溢价／折价比例 | `premium_ratio`、`discount_ratio / 1000000` | PremiumRatio 等 | CreationPremiumRate / RedemptionDiscountRate；本轮未对这两个上海字段做值相等验证 |
| 成分替代金额 | SZ：creation/redemption_cash_substitute `/100000`；SSE：substitution_cash_amount `/100000` | CreationCashSubstitute / RedemptionCashSubstitute | SubstitutionCashAmount |
| 现金替代标志 | `substitute_flag`，保留市场语义 | SubstituteFlag：0–2 | SubstitutionFlag：0–8，不能套用深圳枚举 |
| 名称／指数等元数据 | symbol、fund_management_company 等，头文件标注仅深圳有效 | 原字段 | 上海样本多项为空；需要其他来源补齐 |

所有数字比较使用 Decimal，不把缺失值转换为 0。上表既包含实际比较项，也包含头文件映射建议；实际比较范围以结果表和 `mac.json` 的 `numeric_matches` / `component_matches` 为准，**没有宣称全部字段语义和值都已对齐**。例如现金替代标志逐成分语义、上海溢折价比率、全现金和 RTGS 扩展字段仍需后续覆盖。

## 关键差异：上海限额不可丢失

513350 的 API `creation_limit`、`redemption_limit` 均为默认零，而同日期上交所 XML 中对应限额非零。V1.0.8 头文件明确说这组 API 限额“仅深圳有效”，所以这是字段覆盖不足，不能解释成上海基金没有限制。

接入时应把上海这些 API 字段标成“来源不支持／未知”，通过交易所或基金公司补齐，并保留字段来源。深圳文档中“0 表示无限制”的规则不得跨市场套用。其余上海账户限额、净额限额也不能因样本中为 0 就认为真实为 0。

513350 富国接口本次也成功返回，已比较的 `minShdy`、`minYgcash`、`minShnav` 与 API 换算结果一致；富国返回还有账户限额、全现金、RTGS 等字段，未做完整语义等价验证。

## 与当前五合一代码的关系

核查包含当前未提交修改的工作目录，未丢弃或覆盖脏修改。项目名／目录仍含“四合一”，本报告按用户当前五合一版本称呼。

1. **ETF 申赎监控并非已经统一支持沪深抓取。** `app/modules/redemption/RedemptionEngine.cpp:1377` 的 `fetchNextPcf` 默认是深交所两种文件名路径；`parsePcfResponse`（约 1443 行）主要归一化深圳字段。加入上海 API 时，必须显式映射 Shanghai switch、代码、日期、现金差额和成分字段，不能只改 URL。
2. **网站上传有另一套 PCF 来源。** `app/modules/upload/UploadEngine.cpp:938` 的 `defaultPcfDefinitions` 分别配置 SZSE、SSE；513350 特殊配置为 FULLGOAL_JSON。`app/modules/upload/UploadCollectors.cpp:387` 的 `parsePcf` 已包含不同市场 XML 归一化，以及代码、日期、单位、成分数量和适用标的等校验。新来源要保留这些校验。
3. **包内真实 Upload 业务也要一起考虑。** `app/components/upload/business/scripts/private_513350_valuation_uploader.py` 中 `pcf_url_for_day` 和 `parse_pcf_response` 使用富国 JSON，核验当日清单、单位、现金、净值等，并留存文件摘要。仅改 C++ 采集器，不能宣称全部 PCF 请求都已迁移。
4. **不能因换源改变估值模型。** 513350 包内脚本使用既定 XOP 系数，PCF 用于现金与审计；API 返回 51 行成分，不意味着应该把已有估值模型改为逐成分估值。
5. **布尔值缺失有业务含义。** `RedemptionCore::normalizePcf` 和 `classifyIntradayOpportunity` 使用归一化结果；后者对明确 false 的允许标志拦截。适配器不能漏掉上海开关，让缺失标志成为默认放行；数据不完整时应保持清单未就绪。这是接入要求，本轮未调用相关交易接口。

## Wire 与批量获取边界

本轮没有新增线上抓包，因此没有新增 WIRE_VERIFIED 结论。既有 Mac 实现通过 ReqGetETFCodeTableList 查询，相关代码：

- `src/python/tgw_macos/interface.py:260` 的 QueryETFInfo 对多代码列表显式拒绝；异步回调也未实现。
- `src/python/tgw_macos/_protocol.py` 只放行已验证市场和单项请求。
- `src/python/tgw_macos/_backend.py` 的 ETF 查询路径按当前单次响应完成条件读取，并发送完成通知。

不能只移除列表长度检查：真正批量还需证明请求编码、响应关联、分包结束条件、缺包／重包／超时和跨市场归属。当前首帧完成假设在更大的响应下可能截断。官方两代码结果和 AmazingData 的列表型公开接口可支持后续可行性研究，但不替代 Mac wire 取证，也未证明服务端最大批量或频率限制。

## 建议接入方案

推荐先做 **API 主来源＋交易所／基金公司补充与回退**，不直接删除现有来源。

1. 建共享 PCF Provider，按“市场、代码、交易日”缓存，统一单位、日期、开关和成分模型，记录字段来源和完整性。ETF 监控、C++ Upload 与包内业务使用同一份经校验结果，避免各自重复取数。
2. Mac 第一阶段可用已验证的“同会话逐只查询”，低频刷新并去重。优先协调复用既有银河会话，避免重复登录和账号互踢；具体并发复用仍需验收。这是业务批量调度，不是一次 API 批量请求。
3. 深圳已测核心字段适合 API 主取；上海保留交易所／基金公司获取缺失限额和元数据。只合并同代码、同日期的数据；跨日不拼接。缺失值与合法 0 分开处理。
4. 日期未更新、响应缺项、成分计数错误、开关无法解释时，不发布为当日可用清单。回退来源也执行同样的日期／单位／成分校验。
5. 独立完成 Mac 多代码协议取证及分包实现后，再启用真正批量；之后用全部实际观察列表、盘前发布切换、连接失败／重连和连续交易日做影子对照。两个 ETF 的成功样本不足以批准全量切换。

## Linux oracle

使用 bj 的官方 Python SDK、galaxyrelay 用户、既有受保护配置。运行前 galaxy-relay 为 inactive，未启动或停止该服务。一个会话先做两个单代码请求，再做混合请求，请求之间至少间隔 5 秒，force_logout=False，finally Close 成功。

脱敏证据：[official.json](pcf_eval_20260908/official.json)。只保存请求范围、字段名、类型、计数、返回码、耗时与不变量，不保存凭据或原始 PCF 行。

初次 Linux 工具的 XML 对照未递归进入 ComponentList，产生了错误的零成分计数。**其交易所比较结果全部从正式证据投影中排除，未用作源差异结论**；API 形状摘要保留。修正递归解析后，由 Mac 成功运行生成本文使用的交易所比较，并增加合成回归测试。

## Arm

没有改 SDK 或五合一实现。新增：

- `tools/oracle/pcf_api_eval.py`：固定两个样本的只读 API／HTTP 对照，输出脱敏摘要。
- `tools/oracle/run_pcf_mac_from_bj.py`：受保护凭据经内存和 stdin 传入 Mac 探针，不落盘。
- `tests/test_pcf_api_eval.py`：4 个合成测试。

Mac 首次探针登录后因工具调用 ctypes 项不存在的 set 方法而失败，尚未执行查询；finally Close 成功。改为设置 market / security_code 字段后，顺序查询两只成功。不是 SDK 数据接口失败。成功证据：[mac.json](pcf_eval_20260908/mac.json)。

## Tests 与 Live diff

- `python3 -m unittest discover -s tests -v`：181 项，179 通过、2 跳过；见 [tests.txt](pcf_eval_20260908/tests.txt)。新增测试覆盖嵌套成分解析、精确缩放／缺失值、上海不支持限额、混合请求身份完整性。
- `python3 -m compileall -q src/python examples tools`：通过。
- 两平台同请求的基础字段、类型、成分字段、类型与行数全部一致；见 [live_diff.json](pcf_eval_20260908/live_diff.json)。这是形状和已列不变量比较，不是留存原始数据后的逐值 Linux/Mac 差分。

## Cleanup

Linux 与 Mac 会话都执行 Close。bj 临时探针目录与传输副本已删除；galaxy-relay 前后都是 inactive。没有生产订阅、交易调用、machome／DMIT 部署或服务停止。凭据只在内存中使用；证据没有原始业务行、token、MAC 或密码。任务专用本地 PDF 提取／渲染临时目录已清理。

## Proposed status 与 Open risks

- 本次新增的官方混合市场两代码范围：**LINUX_OBSERVED**。
- Mac 两个单代码分支是既有实现的补充实测证据，**本轮不提高中央 API_STATUS／矩阵等级**，不宣称整个接口 LIVE_ALIGNED 或 PILOT_READY。
- 未通过／未覆盖：Mac 真批量与大响应分包；全标的字段覆盖；上海限额及扩展字段完整性；当日 PCF 发布时间和跨日刷新；长期稳定性、频控、超时与重连恢复；生产多模块会话协调。

**可作接入开发依据，不能作为立即删除交易所来源或直接上线全量替换的验收结论。**
