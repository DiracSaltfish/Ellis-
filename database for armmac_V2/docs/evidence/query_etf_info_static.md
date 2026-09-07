# QueryETFInfo 静态对齐证据

- Scope: 仅静态契约。覆盖原生 TGW `QueryETFInfo`（ETF 代码表/申赎基础信息 + 成分股查询）在
  互联网模式下的 PDF/HDR/官方 Python 三方字段比对，为下一轮 Linux oracle/wire capture/Mac
  实现准备任务卡。本轮无登录、无真实请求、无抓包、无订阅、未启动任何服务；未修改任何实现、
  测试、中央矩阵、Excel 或发行包。
  命名更正：任务卡中的 "`MDETFInfo`" 在公开头文件与官方 Python 中实际为 **`MDETFCodeTableRecord`**；
  请求输入结构实际为 **`SubCodeTableItem`**（与 `QuerySecuritiesInfo` 共用）。本证据按真实名称记录。

- PDF:
  - TGW-C++ 手册 PDF 页 39（正文 31）§3.5.2 "12) QueryETFInfo 方法"：双模式（托管机房和互联网）
    适用；原型 `static int32_t QueryETFInfo(const IGMDETFInfoSpi* etf_info_spi, const SubCodeTableItem* item, uint32_t cnt)`；
    参数表：etf_info_spi=派生类实例指针（"必须在调用 Release 函数之后才能销毁该实例"，对应数据回调
    `IGMDETFInfoSpi::OnMDETFInfo`）、item=查询数组首地址、cnt=请求个数；随后给出 `SubCodeTableItem`
    两字段表，与 HDR 一致。注意该页开头把本接口称为"金融资讯数据查询接口"，而 HDR 注释为
    "ETF代码表信息查询接口"——手册内部措辞不一致，按 HDR 语义理解（低影响，登记备查）。
  - PDF 页 44（正文 36）§3.5.6 "11) IGMDETFInfoSpi 接口"：接收 ETF 基础信息和成分股信息数据，
    双模式适用；`OnMDETFInfo(MDETFCodeTableRecord* etf_info, uint32_t cnt)`，数据指针需显式
    `FreeMemory`。PDF 未为本 SPI 单独描述 `OnStatus(RspQueryStatus*)`（全手册 grep 无
    RspQueryStatus 命中）——状态回调属 HDR-only 面。
  - PDF 页 25（正文 17）FreeMemory：回调后数据指针所有权归应用；除 OnLog 外所有回调都必须显式
    释放，否则内存泄漏。
  - 示例：PDF 页 50（正文 42）demo `QueryETFInfoReq`：memset 零初始化 `SubCodeTableItem`，
    `security_code="000001"`、`market=kSZSE`、`cnt=1` 后一次调用即返回错误码（demo 用股票代码仅示意）。
    PDF 页 53（正文 45）主流程注释：spi 生命周期必须长于 API 使用期，数据全部完成后再 delete，
    最后统一 `Release()`。
  - CLI 测试工具：PDF 页 60（正文 52）：命令 `QueryETFInfo ${市场类型} ${证券代码}`，
    示例 `QueryETFInfo SSE 510050`——确认"单市场+单代码"是官方认可的最小样本形态。
  - 输出结构：PDF 页 85–87（正文 77–79）§5.32 `MDETFCodeTableRecord`、§5.33 `ConstituentStockInfo`：
    字段名/顺序与 V1.0.8 头文件一一对应（35 个固定字段 + `std::vector<ConstituentStockInfo>`；
    成分股 13 字段含现金替代标志 1.0/2.1 版取值表）。**PDF 字段表不含缩放说明**（除以
    100/100000/1000000），缩放仅存在于 HDR 注释——见 Header delta。
  - AmazingData 手册（高层交叉参考，不并入本接口范围）：PDF 页 78–82（正文 74–78）
    §3.5.11.1 `get_etf_pcf(code_list)` 返回 `etf_pcf_info`(dataframe) + `etf_pcf_constituent`
    (dict→dataframe)，字段名集合与本接口 `MDETFCodeTableRecord`/`ConstituentStockInfo` 一致，
    可作后续同参交叉校验参考；其底层通道未经证明，不得假设等同 `QueryETFInfo`。
    PDF 页 22（正文 18）`onSnapshotetf` 为实时 L1 快照推送（`Snapshot` 结构），与本接口无关，避免混淆。

- Header delta:
  - V1.0.8 linux 与 windows 五个头文件经 CRLF 归一后逐字节一致（本轮 `diff` 复核）；
    本接口无 Linux/Windows 发行包差异。
  - `tgw.h:207-216` 原型与 PDF 一致；attention 三条：① 每次使用的 etf_info_spi 入参需保证唯一性
    （同一 spi 不可并发复用于多个在途查询）；② 生命周期持续到数据或状态回调之后（PDF 写成
    "调用 Release 之后才能销毁"，两者语义不一致，PDF 更保守；本地实现按"回调完成后方可释放，
    Release 前必须仍在"的交集处理）；③ **市场当前仅沪深有效**（kSSE=101 / kSZSE=102）。
  - `tgw_history_spi.h:374-398` `IGMDETFInfoSpi`：`OnMDETFInfo(MDETFCodeTableRecord*, uint32_t)` +
    `OnStatus(RspQueryStatus*)`，两个默认体均 `IGMDApi::FreeMemory`。
  - `tgw_struct.h:192-196` `SubCodeTableItem` = `int32_t market` + `char security_code[kFutureSecurityCodeLen]`
    (32)；整文件处于 `#pragma pack(1)`（`tgw_struct.h:12-13/1153`）→ 本地 pack(1) sizeof=36，
    offset market=0/security_code=4。注释：kNone(0) 表示查询所有支持市场（代码表语境），但 ETF 的
    HDR attention 仅沪深有效——oracle 只测单市场单代码，不测 kNone。
  - `tgw_struct.h:948-973` `ConstituentStockInfo`（pack(1)）13 字段 → 本地 sizeof=245；关键 offset：
    security_code=0/market_type=32/underlying_symbol=33/component_share=161/substitute_flag=169/
    premium_ratio=170/discount_ratio=178/creation_cash_substitute=186/redemption_cash_substitute=194/
    substitution_cash_amount=202/underlying_security_id[4]=210/buy_or_sell_to_open=214/reserved[30]=215。
    缩放（HDR 注释）：component_share÷100、premium_ratio/discount_ratio÷1000000、
    creation/redemption_cash_substitute 与 substitution_cash_amount÷100000。
  - `tgw_struct.h:979-1017` `MDETFCodeTableRecord`：固定部分 35 字段共 507 字节（offset 表：
    security_code[16]=0、creation_redemption_unit=16、max_cash_ratio=24、publish=32、creation=33、
    redemption=34、creation_redemption_switch=35、record_num=36、total_record_num=44、
    estimate_cash_component=52、trading_day=60、pre_trading_day=68、cash_component=76、nav_per_cu=84、
    nav=92、market_type(u8)=100、symbol[128]=101、fund_management_company[128]=229、
    underlying_security_id[16]=357、underlying_security_id_source[4]=373、dividend_per_cu=377、
    creation_limit=385、redemption_limit=393、creation_limit_per_user=401、redemption_limit_per_user=409、
    net_creation_limit=417、net_redemption_limit=425、net_creation_limit_per_user=433、
    net_redemption_limit_per_user=441、all_cash_flag=449、all_cash_amount[12]=450、
    all_cash_premium_rate[7]=462、all_cash_discount_rate[7]=469、rtgs_flag=476、reserved[30]=477），
    尾随 `std::vector<ConstituentStockInfo> constituent_stock_infos`（LP64 指针三联体=24B）→
    LP64 sizeof=531。**该结构非 POD（含 vector），ctypes 不得整体镜像**；Mac 解析应基于 wire JSON 形状
    两级展开（basic_info + constituent_stock_info 列表）。缩放（HDR 注释）：
    creation_redemption_unit/record_num/total_record_num 及各 limit 类 ÷100；
    max_cash_ratio/nav_per_cu/nav/dividend_per_cu÷……其中 dividend_per_cu 与各替代金额类 ÷100000，
    nav_per_cu/nav/max_cash_ratio ÷1000000（以 HDR 注释为准，oracle 用不变量复核而非假设）。
  - `tgw_struct.h:1088-1097` `RspQueryStatus`（OnStatus 载荷，pack(1)，LP64）sizeof=77：
    error_code(i32)=0/error_msg_len(i16)=4/error_msg(char\*)=8/rsp_union_status(35B)=16/
    rsp_status(StatusInfo 8B)=51/rsp_stockinfo_status(RspSecuritiesInfoStatus 12B)=59/rsp_thirdinfo_status=71；
    其中 `rsp_stockinfo_status` 注释明确"(含ETFInfo)"——即 ETF 状态回调复用六联体的
    code_table_item_cnt+codes 数组回显请求项。
  - `tgw_datatype.h:18-53` 常量与 SWIG 对象逐一核对一致：kSecurityCodeLen=16、
    kFutureSecurityCodeLen=32、kConsSecurityCodeLen=32、kSymbolETFLen=128、KUnderlyingSecurityID=4、
    KReserved=30、kManagmentETFLen=128、KUnderlyingSecurityIDSource=4、AllCashAmount=12、
    AllCashAremiumRate=7、AllCashDiscountRate=7。
  - 权限枚举：`InternetDataPermission.kSecurityInfo=33` 注释"(含etf的的权限)"——可用于解释
    `-95 kPermissionError`，不能反推账号已开通。
  - MDDatatype 无 ETF 相关条目 → **响应 wire tag 不可由公开枚举推导**，只能实捕取证。

- Official Python static discovery（bj 只读检查，2026-08-26；galaxy-relay 任务前后均为 inactive；
  未建立任何行情连接、未登录、未运行真实查询、未读取凭据；仅 import 包内省与本地构造合成对象转换）：
  - 发行包版本 `tgw 1.0.9.2`；wrapper 位于 `tgw/interface.py:511`：
    `QueryETFInfo(req_etf_info_cfg, query_spi=None, return_df_format=True)`，docstring 明示
    "使用范围：托管机房和互联网模式适用"。`req_etf_info_cfg` 接受单个 `tgw.SubCodeTableItem`
    或其列表；列表经 `Tools_CreateSubCodeTableItem(n)`/`Tools_SetSubCodeTableItem(items,i,item)`/
    `Tools_DestroyCodeTableItem` 打包后一次 `IGMDApi_QueryETFInfo(spi, items, n)`；单项直接传对象、cnt=1。
  - 同步模式（query_spi=None）：构造 `TmpQueryETFInfoWaitSpi` + `TmpQueryETFInfoSpi(wait_event,
    return_df_format)`；立即返回码≠0 → `(None, err)` 且不等待；否则 `wait_event.wait()`（**无超时参数，
    理论上可永久阻塞**）后 `GetResult()` 返回 `(result, err)` 元组。
  - 异步模式：用户 spi 经 `SetSpi()` 包装，追加进全局 `g_list_query_spi` 保活；立即返回码≠0 →
    `(False, err)`，否则 `(True, None)`；结果经用户 `OnResponse(result, err_code)` 交付。
  - 数据转换：`OnMDETFInfo` → `tgw.Tools_ETFInfoToJson(etf_info, cnt)` → JSON 字符串 → `json.loads`
    → list；每元素形如 `{"basic_info": {...}, "constituent_stock_info": [...]}`。df 格式对两级分别
    `json_normalize` → `[(df_basic, df_cons), ...]`；json 格式 → `[(dict_basic, [dict_cons...]), ...]`。
  - 实测（本地构造合成 `MDETFCodeTableRecord`，无网络）：`basic_info` 恰为 35 键，与结构体固定字段
    一一对应；类型映射 char[]→str、单 char→str（NUL 截断，如 publish='Y'）、int64→int、uint8→int；
    空 vector 序列化为 `[]`。SWIG 的 `constituent_stock_infos` 成员返回裸 SwigPyObject（未绑定
    std_vector 方法），Python 侧无法填充成分股——成分股键集只能由 C++ 侧生成；libtgw.so strings 见
    standalone 键候选：underlying_symbol/component_share/substitute_flag/premium_ratio/discount_ratio/
    creation_cash_substitute/redemption_cash_substitute/substitution_cash_amount/buy_or_sell_to_open/
    underlying_security_id（以及 basic_info/constituent_stock_info 两个容器键）。
  - `OnStatus(status)`: 调 `self._spi(None, status.error_code)` → WaitSpi 把 `_err` 设为该 int；
    同样 set wait_event。即完成信号 = 数据批次或状态回调二者其一先到者。
  - **多包竞态（与 QueryCodeTable 已登记问题同型）**：每个 OnMDETFInfo 批次经 OnResponse
    **整体覆盖** `WaitSpi._result`（不累计），且 wait_event 在首个数据批次的 finally 即 set ——
    多批次响应下同步结果取决于线程时序（可能只保留首/末批次之一，非确定性）。
    Linux oracle 行数统计必须另用异步收集器逐批累计，并单独跑一次同步调用记录差异。
  - SWIG `SubCodeTableItem` 默认值 market=0/security_code=''；成员与 V1.0.8 头文件一致；
    `IGMDETFInfoSpi` director 暴露 `OnMDETFInfo/OnStatus` 两回调。

- Binary strings candidates（libtgw.so 静态 strings，仅作下一轮捕获假设，不作协议依据）：
  - wire method 候选 `ReqGetETFCodeTableList`；内部发送符号 `SendETFInfoReq`；响应处理 `DoHandleETFInfo`；
    内部消息类型字符串 `kETFCodeTable`。
  - 完成消息候选：通用 `ReqGetComplete` 与专用 `ReqGetCodelistComplete` 并存；符号显示 internet 侧
    ETF 由 `mdga::CodeTablelistHandle::QueryETFInfo/HandleETFInfo`、`CodelistRequestCache::Init`、
    `CodelistResponseCache::UpdateResponse(ETFCodeTableRecord*, uint)` 处理——提示 ETF 查询与代码表
    共用 codelist 通道/缓存基础设施，wire 通道可能与 dgw\*_query 一次性端点不同，**必须实捕证明**。
  - 流控/就绪字符串："Query ETFInfo data too frequently, the query interval is at least {1}ms"、
    "Query ETFInfo service is not get ready, please try again later."、"Send CodeTableList or ETFInfo failed"
    ——存在最小查询间隔与服务预热窗口，oracle 禁止密集重试。
  - internet/coloc 分离：`InternetETFInfoSpi` vs `ColocETFInfoSpi`；内部 mdga OnStatus 用
    RspCodeListStatus（internet）、rqa 用 RspQueryStatus（coloc）；公开头统一为 RspQueryStatus。
    `mdga::Utils::ETFInfoConvert(ETFCodeTableRecord*, rapidjson::Value&)` 暗示内部 JSON↔record 转换。

- Linux oracle: 未执行（本轮禁止在线操作）。下一轮最小方案（待验收者安排）：
  - 入口扩展：`tools/oracle/remote_sdk_oracle.py` 新增独立 `--kind etf-info`，沿用 relay.env 凭据注入与
    galaxyrelay 用户约束；先 `systemctl is-active galaxy-relay` 确认 inactive 再单次运行，结束必须 `Close()`。
  - 最小样本（与 CLI 示例及既有样本对齐）：`item.market = tgw.MarketType.kSSE; item.security_code = "510300"`，
    `result, err = tgw.QueryETFInfo(item, return_df_format=False)`；再跑一次异步收集器版本统计批次
    （自定义 spi 逐批 append，对照同步 wrapper 的首批覆盖竞态）。
  - 允许记录的脱敏指标：登录布尔、err 类型与数值、总条数、批次数、每批条数序列、basic_info 列集合
    （应恒等于上述 35 键集合）、每列 Python 类型集合、成分股列表长度分布（仅数值）、不变量
    （publish∈{Y,N,''}、creation/redemption∈{Y,N,''}、market_type 取值集合⊆{101,102}、
    trading_day/pre_trading_day 为 8 位整数布尔、creation_redemption_unit>0 存在性布尔、
    nav≥0 存在性布尔、重复 security_code 计数存在性布尔）。禁止输出净值、金额、比例、名称、
    成分股代码或任何原值行。
  - 预期错误码族：0 / -95(权限) / -88(非查询时段) / -76(空) / -83(超时) / -78(超最大查询数) /
    -98(spi 空，仅本地) / -73(接口重入)。出现流控字符串对应情形立即停止，不做密集重试。
  - wire capture 必证清单（oracle 返回容器一致不代表 wire 一致）：WSS path（push 会话还是 dgw\*_query
    还是 codelist 专用通道）、request method（`ReqGetETFCodeTableList`?）、params key 顺序与类型
    （是否携带 QueryBandWidth）、request id 空间与关联方式、响应数值 tag（不可推导）、
    status/pack_num/all_pack_num 分页控制、data 容器形状（JSON 对象数组还是 CSV 行数组还是嵌套）、
    完成 method（通用 `ReqGetComplete` 还是 `ReqGetCodelistComplete`）、ZSTD 标记、双端关闭语义。
    path/method/tag/分页/完成消息在本轮全部保留为待捕获。

- Arm（预计修改点，本轮不改代码）：
  - `_structures.py`：新增 `SubCodeTableItem` pack(1) ctypes 镜像（sizeof=36 + offset 0/4 测试）。
    不镜像 `MDETFCodeTableRecord` 整体（含 std::vector 非 POD）；如需 ABI 记录，仅以常量
    507(+24)/245 注释留档。
  - `_protocol.py`：capture 后新增 `ETF_WIRE_TAG`、`build_etf_info_request(username, token,
    request_id, items)`（纯函数：校验 market∈{101,102} 否则 NotImplementedError、code 非空且≤32 字节、
    支持数组）、`parse_etf_info_packets(packets, expected_tag)` 复用 `_ordered_query_packets`
    做 tag/status/pack 校验后按两级 JSON 形状解析；输出容器对齐官方
    `list[{"basic_info": dict(35键), "constituent_stock_info": list}]`；未知 tag/缺键/多键/成分股元素
    错型显式失败。wire 未证前任何实现入口保持 NotImplementedError。
  - `_backend.py`：`query()` 增加 `"etf_info"` kind 分支（若 capture 证明走 push/codelist 通道则改投递
    路径，不得沿用 dgw query 端点猜测）。
  - `interface.py`：新增 `QueryETFInfo(req_etf_info_cfg, query_spi=None, return_df_format=True)`，
    同步元组合约 `(result, err_int|str)`；`query_spi` 传入按现行政策显式抛 NotImplementedError；
    `return_df_format=True` 而 pandas 缺失时明确报错（与既有 Query* 一致）。
  - `tools/live_smoke.py` 加 `--etf-info` 单旗标路径，输出脱敏 shape 摘要。
  - 合成测试清单（全部合成 fixture，禁用原值）：① SubCodeTableItem sizeof=36/offset/默认零；
    ② builder key 顺序/类型/market 白名单校验（capture 后落地）；③ 单包解析 + 多包乱序重组/
    缺包/重复包/错 tag/错 status/data 容器错型/basic_info 缺键或多键/成分股元素错型负形状；
    ④ 公开合约：同步元组 `(None|list, int)`、异步 spi 显式失败、pandas 缺失降级、err=-76 空结果形状；
    ⑤ 未证分支（非沪深市场、空代码超长代码、未证通道）显式 NotImplementedError。

- Tests: 本轮为静态任务，未新增/修改任何测试或实现。基线核验（2026-08-26）：
  `python3 -m unittest discover -s tests` 通过；`python3 -m compileall -q src/python examples tools` 通过。
  注意：本任务执行期间仓库被另一 Agent 并发修改（月线 K 线子范围加入 `_protocol.py`/tests，
  文件 mtime 18:50–18:51），套件从 26 项（期间瞬时 1 失败）演进到 27 项全通过；该变化与本任务无关，
  本任务零文件改动（唯一新增为本证据文件）。
- Live diff: 无（本轮禁止在线操作）。
- Cleanup: 未创建远端临时文件；未启动/停止任何服务；`galaxy-relay` 任务前后均为 inactive（两次
  `systemctl is-active` 确认）。本地临时物仅限系统临时目录
  `/var/folders/.../T/opencode/etf_static/`（pypdf venv 与提取脚本、尺寸计算脚本），报告后精确删除。
- Proposed status: `STATIC_MATCHED(QueryETFInfo static contract only)`。
  依据：C++ 手册 §3.5.2/§3.5.6/§5.32/§5.33、V1.0.8 头文件（linux==windows）、官方 Python 1.0.9.2
  wrapper/SWIG 对象三方逐字段比对完成，且 wrapper 数据转换经合成对象实测；`LINUX_OBSERVED` 起
  需真实最小只读请求与 wire capture，本轮均未执行。
- Open risks:
  1. wire 通道假设未证：符号强烈暗示 internet ETF 查询复用 codelist/CodeTablelistHandle 基础设施，
     可能不走 dgw\*_query 一次性端点；Mac `_backend.query()` 投递路径须等 capture 定案。
  2. 完成语义候选 `ReqGetCodelistComplete` 与通用 `ReqGetComplete` 并存，未证前不得混用。
  3. 官方同步 wrapper 多批覆盖竞态可能导致行数低估；Linux/Mac 同参验收必须以异步逐批计数为准。
  4. `wait_event.wait()` 无超时：既无数据也无 OnStatus 时同步调用永久阻塞；Mac 同步实现必须有超时。
  5. 响应 tag 无公开枚举可推导；任何"猜 tag"实现都被禁止。
  6. 二进制流控字符串表明存在最小查询间隔与预热窗口；oracle 单次执行、失败即停。
  7. 账号需 `InternetDataPermission.kSecurityInfo=33` 权限；`-95` 视为权限证据而非实现缺陷。
  8. 缩放系数仅见于 HDR 注释，PDF 表格缺失；Mac 数值缩放测试须以 HDR 注释为准并在 oracle 用
     不变量（如 nav≥0、比例为 [0,1000000] 区间的存在性）复核，不得凭 PCF 高层文档反推。
  9. AD 高层 `get_etf_pcf` 与本接口字段同名但底层通道未证；两接口边界保持独立取证。
