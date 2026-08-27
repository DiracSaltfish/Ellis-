# PDF 公开数据获取接口候选清单（只读盘点）

- 生成时间：2026-08-26；生成者：只读接口盘点子 Agent。
- 任务边界：仅静态盘点 `reference/manuals` 两份 PDF 与 `reference/vendor-headers/v1.0.8`
  公开头文件；未运行任何在线请求；未修改 src/tests/docs/Excel/状态矩阵。
- 来源缩写：
  - `TGW-C++` = 《中国银河证券格物金融服务平台(TGW)开发手册(C++版)》187 页；
  - `AD-Py` = 《AmazingData开发手册》148 页；
  - `HDR-v1.0.8` = reference/vendor-headers/v1.0.8（linux 与 windows 头文件经 CRLF 归一后逐字节一致）。
- 页码换算：`TGW-C++` 正文页 = PDF页 − 8；`AD-Py` 正文页 = PDF页 − 4。
- 证据状态取值沿用 `docs/AGENT_PARITY_WORKFLOW.md` §2 与 `docs/API_STATUS.md`（2026-08-26 版）。
  本清单新登记行统一为 `INVENTORIED`；已有证据的行标注其当前限定范围状态。
- 安全约束回顾：不写凭据/业务原值；写操作（UpdatePassWord）只登记不执行；托管机房专用接口
  登记 `OUT_OF_SCOPE_COLOC`，不得用互联网模式猜测实现。

## 第一部分 TGW-C++ 手册（按 PDF 从前到后）

### 3.5.2 基础接口（PDF 24–25 / 正文 16–17）

| # | 来源PDF | PDF页 | 正文页 | 类别 | 公开API | 模式 | 请求结构 | 响应结构 | 回调 | 关键枚举/参数 | 盘后只读可测 | 当前证据状态 | 推荐最小样本 | 依赖/风险 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | TGW-C++ | 24 | 16 | 基础 | `IGMDApi::GetVersion()` | 双模式 | 无入参 | `const char*` 版本串 | 无 | 无 | 是（本地调用） | `INVENTORIED`(Mac 返回自实现版本) | 调用并比对返回类型为字符串 | Mac 实现返回的是本实现版本号，不等于厂商 SDK 版本 |
| 2 | TGW-C++ | 24 | 16 | 基础 | `IGMDApi::Init(spi,cfg,api_mode,path)` | 双模式 | `Cfg{server_vip[24],server_port,username[32],password[64],force_logout,coloca_cfg}` | int32 错误码 | 登录后走 `OnLogon` | `ApiMode`: kColocationMode=1/kInternetMode=2 | 否（需登录） | `LIVE_ALIGNED(internet login)`（Mac 经 Login 覆盖） | 已有：真实 TLS/WSS 登录一次即关 | HDR `ColocaCfg` pack(1) sizeof=22、`Cfg`=145 已锁定；证书路径默认值行为未取证 |
| 3 | TGW-C++ | 24–25 | 16–17 | 基础 | `IGMDApi::Release()` | 双模式 | 无 | int32 错误码 | 无 | 无 | 是 | `LIVE_ALIGNED(internet basic)`（经 Close 覆盖） | 已有：单连接正常关闭+密码副本清空 | 全局单例，`Close()` 后重登不可靠；正式 Session API 未实现 |
| 4 | TGW-C++ | 25 | 17 | 基础 | `IGMDApi::FreeMemory(void*)` | 双模式 | 回调数据指针 | 无 | 配合所有带指针回调 | 无 | 是（配合回调） | `ARM_IMPLEMENTED(partial)`（所有权语义未完整对齐） | 结构性测试即可 | 回调数据所有权契约不完整；Python 侧无直接等价物 |
| 5 | TGW-C++ | 25 | 17 | 基础 | `IGMDApi::GetTaskID()` | 双模式 | 无 | int64（MMDDHHmmSS+序号1~1000000） | 无 | 格式规则 | 是（本地调用） | `ARM_IMPLEMENTED`（单进程递增整数） | 并发唯一性测试 | 无锁；格式与官方 MMDDHHmmSS 规则未同参对照 |
| 6 | TGW-C++ | 25 | 17 | 基础（写操作） | `UpdatePassWord(UpdatePassWordReq)` | 双模式（托管需 TCP 通道） | `UpdatePassWordReq{username[32],old_password[64],new_password[64]}` | int32 错误码 | 无 | `kTimeoutExit` 语义特殊 | **否（写操作禁止）** | `INVENTORIED`(不实现、不执行) | 无——除非用户对该写操作单独授权 | 凭据类写操作；误触发会变更账号口令 |

### 3.5.3 订阅数据方法（PDF 25–28 / 正文 17–20）

| # | 来源PDF | PDF页 | 正文页 | 类别 | 公开API | 模式 | 请求结构 | 响应结构 | 回调 | 关键枚举/参数 | 盘后只读可测 | 当前证据状态 | 推荐最小样本 | 依赖/风险 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 7 | TGW-C++ | 25–26 | 17–18 | 订阅 | `Subscribe(SubscribeItem*,cnt)` | 双模式 | `SubscribeItem{u8 market,u64 flag,char[32] code,u8 category}` pack(1)=42 | int32 错误码；数据走推送 | 见 #16–#31 | `MarketType`,`SubscribeDataType`,`VarietyCategory` | 受限（盘后推送稀少） | `LIVE_ALIGNED(raw full/delta)`: flag 10→wire14 SZSE `159518`; flag 12→wire16 HKT `02800.SH`(SSE路由) | 已有；扩展须一次一个 flag/市场 | 公共值≠wire 值（仅证明 10→14、12→16）；其余 flag 未验证必须显式失败；订阅数量限制未知 |
| 8 | TGW-C++ | 26 | 18 | 订阅 | `UnSubscribe(item,cnt)` | 双模式 | 同 `SubscribeItem` | int32 错误码 | 无 | 同上 | 是 | ETF `LIVE_ALIGNED`；HKT basic observed | 已有：ETF 正常取消 | HKT 取消路径只有清理期观测记录 |
| 9 | TGW-C++ | 26 | 18 | 订阅 | `SubFactor(SubFactorItem*,cnt)` | 双模式 | `SubFactorItem{factor_type[64],sub_type[64],name[64],code[32],market,category}`（HDR 多出后三字段） | int32；数据走 `OnFactor` | `OnFactor(Factor*)` | 全订阅仅 all/all/all、xxx/all/all、xxx/xxx/all 三种 | 受限（因子权限未知） | `INVENTORIED` | 单一已知父类型的极短订阅（需先确认账号权限） | 因子目录无公开枚举；HDR 与 PDF 的 Item 字段集不一致（PDF 少 code/market/category） |
| 10 | TGW-C++ | 27 | 19 | 订阅 | `UnSubFactor(item,cnt)` | 双模式 | 同 #9 | int32 | 无 | 取消全订阅同样三种方式 | 是 | `INVENTORIED` | 与 #9 成对 | 同 #9 |
| 11 | TGW-C++ | 27–28 | 19–20 | 订阅 | `SubscribeDerivedData(type,dtype,item,cnt)` | 仅托管机房 | `SubscribeDerivedDataItem{i32 market,char[16] code}` | int32 | `OnMDOrderBook/OnMDOrderBookSnapshot` | `SubscribeType` kSet/kAdd/kDel/kCancelAll=0..3；dtype 1委托簿/2委托簿快照 | 否 | `OUT_OF_SCOPE_COLOC` | 无 | 托管机房限定；SSE/SZSE 以外市场不支持 |

### 3.5.4 订阅回调方法（PDF 28–33 / 正文 20–25）

说明：#12–#31 为 `IGMDSpi` 推送回调；除 `OnLog` 外均需 `FreeMemory`。互联网模式下已验证的推送交付路径是 Mac 的 `ReceiveRawEvent`（数字 key raw full/delta），类型化 SPI 未实现。

| # | 来源PDF | PDF页 | 正文页 | 类别 | 公开API | 模式 | 请求结构 | 响应结构 | 回调 | 关键枚举/参数 | 盘后只读可测 | 当前证据状态 | 推荐最小样本 | 依赖/风险 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 12 | TGW-C++ | 28 | 20 | 推送回调 | `OnLog(level,log,len)` | 双模式 | 无 | 日志 | 本身 | `LogLevel` 0..5 | 是 | `ARM_IMPLEMENTED`(on_log SPI) | 已有 | 非官方完整日志 SPI |
| 13 | TGW-C++ | 28 | 20 | 推送回调 | `OnEvent(level,code,msg,len)` | 仅托管机房 | 无 | 事件 | 本身 | `EventLevel` 1..3 | 否 | `OUT_OF_SCOPE_COLOC` | 无 | 连接状态事件在互联网模式的对应机制未取证（Mac 重连依赖进程监管） |
| 14 | TGW-C++ | 28–29 | 20–21 | 推送回调 | `OnIndicator(indicator,len)` | 仅托管机房 | 无 | JSON 串 | 本身 | 无 | 否 | `OUT_OF_SCOPE_COLOC` | 无 | — |
| 15 | TGW-C++ | 29 | 21 | 推送回调 | `OnLogon(LogonResponse*)` | 双模式 | 无 | `{api_mode u16, logon_msg_len u32, logon_json char*}` | 本身 | api_mode 1托管/2互联网 | 否（登录时） | `LIVE_ALIGNED(internet login)` | 已有 | logon_json 内容字段未完整建档（含 token，禁止落盘） |
| 16 | TGW-C++ | 29 | 21 | 推送回调 | `OnMDSnapshot(MDSnapshotL1*,cnt)` | 仅互联网 | 订阅 flag=10 | `MDSnapshotL1`（约57字段，价格÷1e6、量÷100、额÷1e5） | 本身 | wire tag 14 | 受限 | `LIVE_ALIGNED(raw)`；类型化字段映射 `CHANGES_REQUESTED` | 已有 60s ETF | 数字 key→结构体映射、缩放、多标的隔离未完成；delta 不由 SDK 合并 |
| 17 | TGW-C++ | 29 | 21 | 推送回调 | `OnMDIndexSnapshot(MDIndexSnapshot*,cnt)` | 仅互联网 | flag=13 | `MDIndexSnapshot` | 本身 | wire tag 未取证 | 受限 | `INVENTORIED` | 沪指单代码 30 秒 | 指数 wire tag/枚举转换未取证 |
| 18 | TGW-C++ | 29–30 | 21–22 | 推送回调 | `OnMDOptionSnapshot(MDOptionSnapshot*,cnt)` | 仅互联网 | flag=11 | `MDOptionSnapshot` | 本身 | 未取证 | 受限 | `INVENTORIED` | 期权主力单代码短订 | 同上 |
| 19 | TGW-C++ | 30 | 22 | 推送回调 | `OnMDHKTSnapshot(MDHKTSnapshot*,cnt)` | 仅互联网 | flag=12 | `MDHKTSnapshot` | 本身 | wire tag 16 | 受限（需港股时段） | `LIVE_ALIGNED(raw; SH route)` | 已有 30s `02800.SH` | `.SZ` 路由未验；市场 101 上游路由规则未建档 |
| 20 | TGW-C++ | 30 | 22 | 推送回调 | `OnMDAfterHourFixedPriceSnapshot(...)` | 仅互联网 | flag=14 | `MDAfterHourFixedPriceSnapshot` | 本身 | 未取证 | 受限（盘后时段） | `INVENTORIED` | 收盘后沪市 ETF 短订 | 盘后窗口短；时点难安排 |
| 21 | TGW-C++ | 30 | 22 | 推送回调 | `OnMDCSIIndexSnapshot(...)` | 仅互联网 | flag=15 | `MDCSIIndexSnapshot` | 本身 | 未取证 | 受限 | `INVENTORIED` | 中证指数单代码短订 | 权限项 kCSIIndexSnapshot=12 需在列 |
| 22 | TGW-C++ | 30–31 | 22–23 | 推送回调 | `OnMDCnIndexSnapshot(...)` | 仅互联网 | flag=16 | `MDCnIndexSnapshot` | 本身 | 未取证 | 受限 | `INVENTORIED` | 国证指数单代码短订 | 注意与 HKT flag 16 同值的公共/wire 分层风险 |
| 23 | TGW-C++ | 31 | 23 | 推送回调 | `OnMDHKTRealtimeLimit(...)` | 仅互联网 | flag=17 | `MDHKTRealtimeLimit` | 本身 | 未取证 | 受限 | `INVENTORIED` | 港股通额度短订 | 额度消息频率低 |
| 24 | TGW-C++ | 31 | 23 | 推送回调 | `OnMDHKTProductStatus(...)` | 仅互联网 | flag=18 | `MDHKTProductStatus` | 本身 | 未取证 | 受限 | `INVENTORIED` | 单产品短订 | 同上 |
| 25 | TGW-C++ | 31 | 23 | 推送回调 | `OnMDHKTVCM(...)` | 仅互联网 | flag=19 | `MDHKTVCM` | 本身 | 未取证 | 受限（VCM 触发才推） | `INVENTORIED` | 无法主动构造样本 | 事件驱动，不可计划测试 |
| 26 | TGW-C++ | 31 | 23 | 推送回调 | `OnMDFutureSnapshot(...)` | 仅互联网 | flag=20 | `MDFutureSnapshot` | 本身 | 未取证；市场 3/4/5/6/7 | 受限 | `INVENTORIED` | 中金所单合约短订（需期货权限） | 期货权限未知；商品期货查询规则见 PDF 6.7 |
| 27 | TGW-C++ | 32 | 24 | 推送回调 | `OnKLine(data,cnt,kline_type)` | 双模式 | 订阅分钟 K flag=1..8 | `MDKLine` | 本身 | `MDDatatype` 10000..10007 | 受限 | `INVENTORIED` | 分钟 K 单代码短订 | 分钟线公共值→wire period 映射未取证 |
| 28 | TGW-C++ | 32 | 24 | 推送回调 | `OnSnapshotDerive(data,cnt)` | 双模式 | flag=9 | `MDSnapshotDerive` | 本身 | 未取证 | 受限 | `INVENTORIED` | 单股票衍生短订 | 衍生权限 InternetDataPermission.kSnapshotDerive=2 |
| 29 | TGW-C++ | 32–33 | 24–25 | 推送回调 | `OnFactor(Factor*)` | 双模式 | `SubFactor` | `Factor{data_size,json_buf}` headers/body JSON | 本身 | 因子名英文 | 受限 | `INVENTORIED` | 同 #9 | JSON schema 仅示例级文档 |
| 30 | TGW-C++ | 33 | 25 | 推送回调 | `OnMDOrderBook(vector<MDOrderBook>&)` | 仅托管机房 | `SubscribeDerivedData` dtype=1 | `MDOrderBook`（含 vector，非定长） | 本身 | OB 上限 500 只 | 否 | `OUT_OF_SCOPE_COLOC` | 无 | 盘中禁动态订阅冲突 |
| 31 | TGW-C++ | 33 | 25 | 推送回调 | `OnMDOrderBookSnapshot(...,cnt)` | 仅托管机房 | dtype=2 | `MDOrderBookSnapshot` | 本身 | 快照上限 3000 只 | 否 | `OUT_OF_SCOPE_COLOC` | 无 | 同上 |

### 3.5.5 查询数据方法（PDF 33–39 / 正文 25–31）

| # | 来源PDF | PDF页 | 正文页 | 类别 | 公开API | 模式 | 请求结构 | 响应结构 | 回调 | 关键枚举/参数 | 盘后只读可测 | 当前证据状态 | 推荐最小样本 | 依赖/风险 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 32 | TGW-C++ | 33–34 | 25–26 | 查询 | `QueryKline(kline_spi,ReqKline)` | 双模式 | `ReqKline{code[38],market u8,cq_flag u8,cq_date,qj_flag,cyc_type u16,cyc_def,auto_complete,begin/end_date,begin/end_time}` pack(1)=71 | `MDKLine` 数组；wrapper 补 orig_time/variety_category 共11字段 | `IGMDKlineSpi::OnMDKLine`+`OnStatus` | `cyc_type` 10000..10012；cq_flag 0/1/2 | 是（历史区间） | `LIVE_ALIGNED(daily + weekly + monthly + quarterly + yearly only)`：10008→10100，10009→10101，10010→10102，10011→10103，10012→10104（季/年已实捕证明）；响应 tag 同 wire 周期 | 已有 SSE `510300` 日线单日、周线单周、月线单月、季线单季与年线单年 | 分钟族显式拒绝；非零 status（kDataEmpty=-76）转异常未对齐官方错误码返回；备用端点受 `1000 accept conn active close` 流控 |
| 33 | TGW-C++ | 34 | 26 | 查询 | `QuerySnapshot(snapshot_spi,ReqDefault)` | 双模式 | `ReqDefault{code[38],market u8,date,begin/end_time,data_type u16,level_type u16}`（level_type 仅 HDR 有）pack(1)=55 | data_type=0:`MDSnapshotL1`(互联网)；1:`MDHKExOrderSnapshot`；2:`MDHKExOrderBrokerSnapshot`；L2 仅托管 | `IGMDSnapshotSpi::OnMDSnapshotL1/L2/OnMDHKTSnapshot/...`+`OnStatus` | data_type 0/1/2 其余无效；level_type 默认0 | 是（单日历史） | `LIVE_ALIGNED(SZSE ETF L1, data_type=0; sync+async error contract)`：数据/协议同参 + 空数据 `(None,-76)`/异步 `(True,None)`→`(None,-76)` 均已 Linux/Mac 同参（2026-08-26） | 已有单日窄窗 | 多包异步交付语义未观测；data_type=1/2 未取证；其它市场未验；错误标签映射仅 `"DataEmpty"` 已捕获 |
| 34 | TGW-C++ | 35 | 27 | 查询 | `QueryOrderQueue(spi,ReqDefault)` | 仅托管机房 | `ReqDefault` | `MDOrderQueue`（volume[50] 定长） | `OnMDOrderQueue`+`OnStatus` | 无 | 否 | `OUT_OF_SCOPE_COLOC` | 无 | — |
| 35 | TGW-C++ | 35 | 27 | 查询 | `QueryTickExecution(spi,ReqDefault)` | 冲突：PDF 标仅托管机房；HDR `tgw.h` 标双模式 | `ReqDefault` | `MDTickExecution` | `OnMDTickExecution`+`OnStatus` | 无 | 待取证 | `INVENTORIED`（冲突登记） | 若走互联网先做 Linux oracle 单次观测 | PDF/HDR 模式边界矛盾，须以 oracle 实测定界后再实现 |
| 36 | TGW-C++ | 35–36 | 27–28 | 查询 | `QueryTickOrder(spi,ReqDefault)` | 仅托管机房（HDR 无模式限定注记） | `ReqDefault` | `MDTickOrder` | `OnMDTickOrder`+`OnStatus` | 无 | 否 | `OUT_OF_SCOPE_COLOC`（按 PDF） | 无 | HDR 未标模式，存在与 #35 同类矛盾，需一并澄清 |
| 37 | TGW-C++ | 36 | 28 | 查询 | `QueryCodeTable(code_table_spi)` | 双模式（回调章节误标 coloc，HDR/方法页为双模式） | 无入参 | `MDCodeTable{code[16],symbol[32],en_name[128],market u8,type[10],currency[4]}` pack(1)=191 | `OnMDCodeTable`+`OnStatus` | 含 2022-08-22 起退市代码 | 是 | `ARM_IMPLEMENTED` | wire 已证：`dgw*_query` one-shot、`ReqGetReduceCodeTable`、tag 11103、反引号 6 字段、缺包 `ReqGetPackage`；Linux `-83` 与 Mac 缺包超时同因同果（服务端缺第 3 包）；待成功同参闭环 | 服务端全市场大表持续缺第 3 包且对补拉无响应，完整 12 包成功样本当前不可得；完成 method（`ReqGetComplete`/`ReqGetCodelistComplete`）未证 |
| 38 | TGW-C++ | 36–37 | 28–29 | 查询 | `QuerySecuritiesInfo(spi,SubCodeTableItem*,cnt)` | 双模式 | `SubCodeTableItem{i32 market,char[32] code}` | `MDCodeTableRecord`（大结构，含涨跌停/单位/期权属性） | `OnMDSecuritiesInfo`+`OnStatus` | market kNone=全市场；支持 SSE/SZSE/NEEQ | 是 | `LIVE_ALIGNED(SSE single code only)` | 已闭合 SSE `510300` 单 item：push 通道 `ReqGetCodeTableList`、tag `"109"`、43 字段/`MDCodeTableRecord` sizeof=555；Linux/Mac 同参 1 行 43 列一致 | 全市场/多 item/SZSE/NEEQ/空结果/多帧分页未验且显式拒绝 |
| 39 | TGW-C++ | 36–37 | 28–29 | 查询 | `QueryExFactorTable(ex_factor_spi,code)` | 双模式 | `const char* code` | `MDExFactorTable{inner_code,code,ex_date,ex_factor double,cum_factor double}` | `OnMDExFactor`+`OnStatus` | 无 | 是 | `LIVE_ALIGNED(000001 only)` | 已闭合 `000001` 单代码：one-shot `ReqGetExFactor`、tag 11102、5 字段 CSV、double 18 位小数字符串上线、33 行；Linux/Mac 同参（含 cum_factor 单调违例 2 处吻合） | 空结果/非零 status wire 形状未取证；多包未线上观测；异步 SPI 未实现 |
| 40 | TGW-C++ | 37 | 29 | 查询 | `QueryFactor(factor_spi,ReqFactor)` | 双模式 | `ReqFactor{task_id,3×char[64],begin/end_date,begin/end_time,code[32],market,category,count,key1,key2}` | `Factor` JSON | `OnFactor`+`OnStatus` | begin==end_date（仅单日）；count 默认1000（HDR）；offset 分页暂不支持 | 是 | `INVENTORIED` | 单日单因子单代码 | 因子名称目录缺失；PDF 表格缺 count 默认值差异（PDF 写默认100） |
| 41 | TGW-C++ | 37–38 | 29–30 | 查询 | `SetThirdInfoParam(task_id,key,value)` | 双模式 | 逐 key/value 设置；必须先设 `function_id` | int32 错误码 | 无 | task_id 由 GetTaskID 生成 | 是 | `LIVE_ALIGNED(calendar function only)` | 已有 `A010061003` | 未验功能号必须显式失败；taskid 并发唯一性弱 |
| 42 | TGW-C++ | 38 | 30 | 查询 | `QueryThirdInfo(third_info_spi,task_id)` | 双模式 | task_id | `ThirdInfoData{task_id,data_size,json_data}`；JSON `{"code","msg","body":{"data":[...]}}` | `OnThirdInfo`+`OnStatus` | 功能号全集见第三部分 | 是 | `LIVE_ALIGNED(calendar function only)`（wire `ReqGetThirdInfo`,tag 11101） | 已有日历 | 其它功能号未验；分页/超长 JSON 截断行为未取证 |
| 43 | TGW-C++ | 39 | 31 | 查询 | `QueryETFInfo(etf_info_spi,item,cnt)` | 双模式 | `SubCodeTableItem{market int32,code[32]}` pack(1)=36 | `MDETFCodeTableRecord` 固定 35 字段/507 字节 + LP64 `std::vector` 24 字节；成分股 `ConstituentStockInfo` pack(1)=245 | `OnMDETFInfo`+`OnStatus` | 市场仅沪深有效；spi 生命周期须持续到回调后 | 是 | `LIVE_ALIGNED(single SSE ETF only)`：push WSS `ReqGetETFCodeTableList`，`Security="code\|market"`，tag `"111"`，完成消息 `ReqGetCodelistComplete` | 已有 `510300.SSE`：Linux/Mac 均 1 条基础信息 35 字段 + 300 条成分 × 13 字段 | SZSE、多 item、空结果/错误、多帧和异步 SPI 未验；返回含 `std::vector`，不可整体 ctypes 化 |

### 3.5.6 查询回调方法（PDF 39–44 / 正文 31–36）——合并进 #32/#33/#37/#38/#39/#40/#42/#43 的“回调”列

同名回调（OnMDKLine、OnMDIndexSnapshot、OnMDHKTSnapshot、OnMDOptionSnapshot、OnMDFutureSnapshot、OnFactor、OnThirdInfo 等）与 3.5.4 推送回调共用同一结构，不再重复立行。以下两条为 3.5.6 独有、无推送对应的回调：

| # | 来源PDF | PDF页 | 正文页 | 类别 | 公开API | 模式 | 请求结构 | 响应结构 | 回调 | 关键枚举/参数 | 盘后只读可测 | 当前证据状态 | 推荐最小样本 | 依赖/风险 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 44 | TGW-C++ | 40 | 32 | 查询回调 | `IGMDSnapshotSpi::OnMDSnapshotL2(L2*,cnt)` | 仅托管机房 | #33 data_type=0 | `MDSnapshotL2`（57+ 字段） | 本身 | — | 否 | `OUT_OF_SCOPE_COLOC` | 无 | — |
| 45 | TGW-C++ | 41 | 33 | 查询回调 | `OnMDHKExOrderSnapshot` / `OnMDHKExOrderBrokerSnapshot` | 双模式（PDF）/商业行情权限 | #33 data_type=1/2 | `MDHKExOrderSnapshot`（嵌套 20 档）、`MDHKExOrderBrokerSnapshot`（40 席位） | 本身 | `InternetDataPermission.kHKExSnapshot=29` | 是（历史单日，若权限开通） | `INVENTORIED` | 单港股单日 data_type=1 | 商业港股权限大概率未开通；结构嵌套深 |

补充（仅 HDR，PDF 缺失）：每个查询 Spi 都有 `OnStatus(RspQueryStatus*)`，结构含 error_code/error_msg/rsp_union_status(req_type)/status/stockinfo/thirdinfo 六联体——Mac 错误语义对齐时必须覆盖此结构（见第五部分 E-3）。

### 3.5.7 回放数据方法（PDF 44–46 / 正文 36–38）

| # | 来源PDF | PDF页 | 正文页 | 类别 | 公开API | 模式 | 请求结构 | 响应结构 | 回调 | 关键枚举/参数 | 盘后只读可测 | 当前证据状态 | 推荐最小样本 | 依赖/风险 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 46 | TGW-C++ | 44–45 | 36–37 | 回放 | `ReplayKline(history_spi,ReqReplayKline)` | 仅托管机房(RTCP) | `ReqReplayKline`+`ReqHistoryItem[]` | `MDKLine` 带 task_id | `OnMDKline(task_id,...)` | cyc_type 不支持周月季年；replay_speed 暂不可用 | 否 | `OUT_OF_SCOPE_COLOC` | 无 | — |
| 47 | TGW-C++ | 45–46 | 37–38 | 回放 | `ReplayRequest(history_spi,ReqReplay)` | 仅托管机房(RTCP) | `ReqReplay{md_data_type,date,time,speed,task_id,items[],cnt}` | `MDSnapshotL2`/`MDTickExecution` 带 task_id | `OnMDSnapshot/OnMDTickExecution(task_id,...)` | md_data_type=kTickExecution=10013/kSnapshot=10014 | 否 | `OUT_OF_SCOPE_COLOC` | 无 | — |
| 48 | TGW-C++ | 46 | 38 | 回放 | `CancelTask(task_id)` | 仅托管机房 | int64 task_id（0=全部） | int32 | `OnRspTaskStatus` | `HistoryTaskStatus` 0..4 | 否 | `OUT_OF_SCOPE_COLOC` | 无 | — |

### 3.5.8 回放回调方法（PDF 46–47 / 正文 38–39）：`OnRspTaskStatus` 结构 `{task_id,status,process_rate,error_code,error_msg_len,error_msg}`，随 #46–#48 合并，状态 `OUT_OF_SCOPE_COLOC`。

## 第二部分 TGW-C++ 第 4/5 章关键字典与结构（引用基准）

| # | 来源PDF | PDF页 | 正文页 | 类别 | 条目 | 关键取值/要点 | 盘后只读可测 | 当前证据状态 | 依赖/风险 |
|---|---|---|---|---|---|---|---|---|---|
| 49 | TGW-C++ | 62 | 54 | 枚举 | ErrorCode | kFailure=-100 … kSuccess=0（含 kDataEmpty=-76、kNonQueryTimePeriod=-88、kFlowOverLimit=-82、kOverMaxQueryLimit=-78 等） | 是 | `STATIC_MATCHED(official table)`（Mac `ErrorCode`/`GetErrorMsg` 已对齐官方 wheel 全表；快照空数据 `-76` 已线上同参，其余码触发条件未逐项取证） | 其余错误码的线上触发条件需随各接口逐步验证 |
| 50 | TGW-C++ | 64–65 | 56–57 | 枚举 | MDDatatype | 10000..10012 K线周期、10013 逐笔(托管)、10014 快照(托管) | 是 | daily=10008、weekly=10009、monthly=10010 已证；其余 INVENTORIED | 季/年及分钟族 wire 值未取证 |
| 51 | TGW-C++ | 65 | 57 | 枚举 | MarketType | kNone=0,kNEEQ=2,kSHFE=3,kCFFEX=4,kDCE=5,kCZCE=6,kINE=7,kSSE=101,kSZSE=102,kHKEx=103,kBK=201 | 部分（101/102/103 已涉） | LIVE_ALIGNED 子范围外 INVENTORIED | HKT `.SZ` 路由未验 |
| 52 | TGW-C++ | 66–67 | 58–59 | 枚举 | SubscribeDataType | 0..20 见 PDF；HDR 另有 21 kSnapshotL2/22 kTickOrder/23 kTickExecution/24 kOrderQueue（PDF 缺） | 受限 | 10/12 已证 | 21–24 仅存在于 HDR，PDF 未记载，属 HDR-only 面 |
| 53 | TGW-C++ | 67 | 59 | 枚举 | VarietyCategory / SubscribeType / SubscribeDerivedDataType | 0..9,255；0..3；1/2 | 是 | INVENTORIED | category_type 仅互联网有效 |
| 54 | TGW-C++ | 68–87 | 60–79 | 数据结构 | 第5章全部行情结构（MDKLine…ConstituentStockInfo/MDETFCodeTableRecord） | 价格÷1e6、量÷100、金额÷1e5、比率÷1e5、汇率÷1e8 | 是（离线核对） | `Cfg=145`、`SubscribeItem=42`、`SubCodeTableItem=36`、`ReqKline=71`、`ReqDefault=55`、`LogonResponse=14`、`ColocaCfg=22`、`MDCodeTable=191` 已锁定；ETF 返回已按 35+成分13 字段 wire 容器验收 | HDR 为 pack(1)；MDETFCodeTableRecord/MDOrderBook 含 vector 非定长，不整体 ctypes 化 |

## 第三部分 TGW-C++ 第 7 章资讯功能号（底层均为 #41+#42 `SetThirdInfoParam`+`QueryThirdInfo`，响应均为 `{"code","msg","body":{"data":[...]}}`）

| # | 来源PDF | PDF页 | 正文页 | 类别 | 公开API(function_id) | 请求参数（除 function_id/taskid） | 响应要点 | 盘后只读可测 | 当前证据状态 | 推荐最小样本 | 依赖/风险 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 55 | TGW-C++ | 94 | 86 | 资讯-基础 | A010010001 A股基本资料 | （章节内详列） | 字段表 | 是 | `INVENTORIED` | 单代码单次 | 未验功能号显式失败 |
| 56 | TGW-C++ | 95 | 87 | 资讯-基础 | A010010002 A股行业分类 | 同上 | 字段表 | 是 | `INVENTORIED` | 单代码 | 同上 |
| 57 | TGW-C++ | 96 | 88 | 资讯-基础 | A010010003 公司简介 | 同上 | 字段表 | 是 | `INVENTORIED` | 单代码 | 同上 |
| 58 | TGW-C++ | 99 | 91 | 资讯-基础 | A010010004 股本结构 | 同上 | 字段表 | 是 | `INVENTORIED` | 单代码 | 对应 AD `get_equity_structure`（推断） |
| 59 | TGW-C++ | 102–103 | 94–95 | 资讯-基础 | A010010005 公司主营业务 | 同上 | 字段表 | 是 | `INVENTORIED` | 单代码 | — |
| 60 | TGW-C++ | 104 | 96 | 资讯-基础 | A010010006 历史股票列表(京沪深A) | 日期区间 | 代码列表 | 是 | `INVENTORIED` | 一天窄窗 | — |
| 61 | TGW-C++ | 105 | 97 | 资讯-基础 | A010010007 历史列表(含期货期权指数) | 日期区间 | 代码列表 | 是 | `INVENTORIED` | 一天窄窗 | — |
| 62 | TGW-C++ | 106–111 | 98–103 | 资讯-发行 | A010020001 A股首次公开发行 | 章节详列 | 字段表 | 是 | `INVENTORIED` | 单代码 | — |
| 63 | TGW-C++ | 111–115 | 103–107 | 资讯-发行 | A010020002 A股增发 | 章节详列 | 字段表 | 是 | `INVENTORIED` | 单代码 | — |
| 64 | TGW-C++ | 115–116 | 107–108 | 资讯-分配 | A010030001 A股分红 | 章节详列 | 字段表 | 是 | `INVENTORIED` | 单代码 | 对应 AD `get_dividend`（推断） |
| 65 | TGW-C++ | 117–120 | 109–112 | 资讯-分配 | A010030002 A股配股 | 章节详列 | 字段表 | 是 | `INVENTORIED` | 单代码 | 对应 AD `get_right_issue`（推断） |
| 66 | TGW-C++ | 120–121 | 112–113 | 资讯-分配 | A010030003 除权除息 | 章节详列 | 字段表 | 是 | `INVENTORIED` | 单代码单日 | — |
| 67 | TGW-C++ | 121–123 | 113–115 | 资讯-分配 | A010030004 复权因子表 | 章节详列 | 字段表 | 是 | `INVENTORIED` | 单代码 | 与 TGW 查询接口 #39 语义重叠，来源通道不同 |
| 68 | TGW-C++ | 123–124 | 115–116 | 资讯-股本股东 | A010040001 十大股东 | 章节详列 | 字段表 | 是 | `INVENTORIED` | 单代码 | 对应 AD `get_share_holder`（推断） |
| 69 | TGW-C++ | 124–125 | 116–117 | 资讯-股本股东 | A010040002 限售解禁明细 | 章节详列 | 字段表 | 是 | `INVENTORIED` | 单代码 | — |
| 70 | TGW-C++ | 125–126 | 117–118 | 资讯-股本股东 | A010040003 解禁数据 | 章节详列 | 字段表 | 是 | `INVENTORIED` | 单代码 | — |
| 71 | TGW-C++ | 126–128 | 118–120 | 资讯-股本股东 | A010040004 股权冻结/质押 | 章节详列 | 字段表 | 是 | `INVENTORIED` | 单代码 | 对应 AD `get_equity_pledge_freeze`（推断） |
| 72 | TGW-C++ | 128–137 | 120–129 | 资讯-财务 | A010050001 资产负债表 | 章节详列 | 大宽表 | 是 | `INVENTORIED` | 单代码单报告期 | 列数极大，建议只取 shape 摘要；对应 AD `get_balance_sheet`（推断） |
| 73 | TGW-C++ | 137–144 | 129–136 | 资讯-财务 | A010050002 利润表 | 章节详列 | 大宽表 | 是 | `INVENTORIED` | 同上 | 对应 AD `get_income`（推断） |
| 74 | TGW-C++ | 144–152 | 136–144 | 资讯-财务 | A010050003 现金流量表 | 章节详列 | 大宽表 | 是 | `INVENTORIED` | 同上 | 对应 AD `get_cash_flow`（推断） |
| 75 | TGW-C++ | 152–162 | 144–154 | 资讯-财务 | A010050004 财务指标 | 章节详列 | 字段表 | 是 | `INVENTORIED` | 单代码单报告期 | — |
| 76 | TGW-C++ | 162–164 | 154–156 | 资讯-财务 | A010050005 业绩预告 | 章节详列 | 字段表 | 是 | `INVENTORIED` | 单代码 | 对应 AD `get_profit_notice`（推断） |
| 77 | TGW-C++ | 164–172 | 156–164 | 资讯-财务 | A010050006 财务衍生指标 | 章节详列 | 字段表 | 是 | `INVENTORIED` | 单代码 | — |
| 78 | TGW-C++ | 172–173 | 164–165 | 资讯-交易日历 | A010060001 交易日历(京沪深A) | start_date,end_date(≤30天) | EXCHANGE(+INT),TRADE_DAYS,LAST/NEXT_TRADE_DAYS | 是 | 相邻子范围 `LIVE_ALIGNED(calendar function only)`（实测用 A010061003） | 已有 SSE 区间 | **PDF 未收录 A010061003**；实测功能号与 PDF 目录不一致，需在证据中保留该差异 |
| 79 | TGW-C++ | 173–174 | 165–166 | 资讯-交易日历 | A010061001 交易日历(含期货期权) | 同上+market 可选 | 同上（CFFEX/CZCE 示例） | 是 | `INVENTORIED` | 期货市场 3 天窗 | 同上差异背景下的新增功能号须先 oracle |
| 80 | TGW-C++ | 175–176 | 167–168 | 资讯-异动 | A010070001 大宗交易 | market_code,start/end_date(0=最新) | 成交价/量/金额/营业部 | 是 | `INVENTORIED` | 单代码最新日 | 对应 AD `get_block_trading`（推断） |
| 81 | TGW-C++ | 176–177 | 168–169 | 资讯-异动 | A010070002 龙虎榜营业部 | 章节详列 | 字段表 | 是 | `INVENTORIED` | 单代码最新日 | 对应 AD `get_long_hu_bang`（推断） |
| 82 | TGW-C++ | 177–179 | 169–171 | 资讯-两融 | A010080001 融资融券交易明细 | 章节详列 | 字段表 | 是 | `INVENTORIED` | 单市场最新日 | 对应 AD `get_margin_detail`（推断） |
| 83 | TGW-C++ | 179–180 | 171–172 | 资讯-两融 | A010080002 融资融券成交汇总 | 章节详列 | 字段表 | 是 | `INVENTORIED` | 单市场最新日 | 对应 AD `get_margin_summary`（推断） |
| 84 | TGW-C++ | 180 | 172 | 资讯-基金 | A010090001 基金最新指标 | 章节详列 | 字段表 | 是 | `INVENTORIED` | 单基金 | — |
| 85 | TGW-C++ | 180–181 | 172–173 | 资讯-指数 | A010200001 A股指数成分股 | 章节详列 | 成分列表 | 是 | `INVENTORIED` | 单指数 | 对应 AD `get_index_constituent`（推断） |
| 86 | TGW-C++ | 181–182 | 173–174 | 资讯-元数据 | queryLastFuncDataTime 更新日期 | function 相关 | 更新日期 | 是 | `INVENTORIED` | 任一已验功能号 | 低风险，适合作为功能号探活的旁证 |

## 第四部分 AD-Py 手册（高层 Python wrapper，按 PDF 从前到后；“底层映射”列中【已证】=本项目已有证据，【推断】=两份 PDF 均未明示、需取证）

| # | 来源PDF | PDF页 | 正文页 | 类别 | 公开API | 模式 | 请求结构 | 响应结构 | 回调 | 关键枚举/参数 | 盘后只读可测 | 当前证据状态 | 推荐最小样本 | 依赖/风险 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 87 | AD-Py | 10–11 | 6–7 | 基础 | `ad.login(username,password,host,port)` | 互联网 | str×2+host/port | 会话建立 | 无 | — | 否 | 【已证】经 TGW Login `LIVE_ALIGNED(internet login)` | 已有 | wheel 为 cp3xx 官方编译版，Mac arm64 无官方 wheel |
| 88 | AD-Py | 11 | 7 | 基础 | `logout(username)` | 互联网 | username | 无 | 无 | — | 是 | `EXPERIMENTAL`(amazingdata_compat 未验收) | 结构测试 | 正常使用无需登出 |
| 89 | AD-Py | 11 | 7 | 基础（写） | `update_password(old,new)` | 互联网 | 密码三元组 | 无 | 无 | — | **否（写操作禁止）** | `INVENTORIED`(不执行) | 无 | 同 #6 |
| 90 | AD-Py | 11–12 | 7–8 | 基础数据 | `BaseData.get_code_info(security_type)` | 互联网 | security_type 默认 EXTRA_STOCK_A | dataframe(symbol,security_status,pre_close,high/low_limited,price_tick) | 无 | 附录 security_type 族 | 是 | 【推断】≈TGW #38 QuerySecuritiesInfo；`INVENTORIED` | 单类型全表 shape | security_type 枚举全集只在附录文字，无数值表 |
| 91 | AD-Py | 12 | 8 | 基础数据 | `get_code_list(security_type)` | 互联网 | 同上 | list[code] | 无 | 同上 | 是 | 【推断】≈#38/#37 子集；`INVENTORIED` | EXTRA_ETF 全列表 | — |
| 92 | AD-Py | 13 | 9 | 基础数据 | `get_future_code_list(security_type)` | 互联网 | ZJ_FUTURE 等 | list | 无 | 期货 security_type | 是 | `INVENTORIED` | 单交易所列表 | 需期货权限 |
| 93 | AD-Py | 13 | 9 | 基础数据 | `get_option_code_list(security_type)` | 互联网 | EXTRA_ETF_OP 等 | list | 无 | 期权 security_type | 是 | `INVENTORIED` | ETF 期权列表 | 同上 |
| 94 | AD-Py | 14 | 10 | 基础数据 | `get_backward_factor(code_list,local_path,is_local)` | 互联网 | codes+本地缓存参数 | df(index=日期,column=代码) | 无 | 缓存方案 §4.4 | 是 | 【推断】复权因子另有 TGW #67/#39 两路；`INVENTORIED` | 单 ETF 后复权一列 | hdf5 本地缓存与 Mac 实现无关，属 wrapper 层 |
| 95 | AD-Py | 14–15 | 10–11 | 基础数据 | `get_adj_factor(...)` | 互联网 | 同上 | df | 无 | 同上 | 是 | `INVENTORIED` | 单 ETF 单次因子 | 同上 |
| 96 | AD-Py | 15 | 11 | 基础数据 | `get_hist_code_list(type,start,end,local_path)` | 互联网 | 类型+闭区间 | list | 无 | — | 是 | `INVENTORIED` | 一周窄窗 | 历史代码表底层服务未见于 TGW 手册 |
| 97 | AD-Py | 16 | 12 | 基础数据 | `get_calendar(data_type,market)` | 互联网 | market 默认 SH | List[int] | 无 | market: SH 等 | 是 | 【已证】底层 ThirdInfo 日历 `LIVE_ALIGNED(calendar function only)` | 已有 | AD 层参数→TGW 功能号的对应关系未在 PDF 明示 |
| 98 | AD-Py | 16–17 | 12–13 | 基础数据 | `InfoData.get_stock_basic(code_list)` | 互联网 | codes | df(MARKET_CODE…IS_LISTED) | 无 | — | 是 | 【推断】≈A010010001/3 组；`INVENTORIED` | 单代码 | — |
| 99 | AD-Py | 17–18 | 13–14 | 基础数据 | `get_history_stock_status(codes,local_path,is_local,begin,end)` | 互联网 | codes+区间/缓存 | df(PRECLOSE,HIGH/LOW_LIMITED,ST/停牌/除权除息标志…) | 无 | — | 是 | `INVENTORIED` | 单代码一月 | 无 TGW 手册对应功能号，底层未知 |
| 100 | AD-Py | 18–19 | 14–15 | 基础数据 | `get_bj_code_mapping(local_path,is_local)` | 互联网 | 缓存参数 | df(OLD_CODE,NEW_CODE,… ) | 无 | — | 是 | `INVENTORIED` | 全表一次 | 北交所新旧代码对照，底层未知 |
| 101 | AD-Py | 19 | 15 | 实时订阅 | `@SubscribeData.register(period=snapshot)`→`onSnapshotindex` | 互联网 | code_list(北/上/深指数) | SnapshotIndex 对象 | 装饰器回调 | Period.snapshot | 受限 | 【推断】≈flag 13；`INVENTORIED` | 指数 30 秒 | 指数 wire tag 未取证 |
| 102 | AD-Py | 20 | 16 | 实时订阅 | `onSnapshot` | 互联网 | codes(北/上/深股票) | Snapshot | 同上 | Period.snapshot | 受限 | 【已证】raw 层 `LIVE_ALIGNED(flag10)` | 已有 | 类型化 Snapshot 对象未实现（数字 key） |
| 103 | AD-Py | 20–21 | 16–17 | 实时订阅 | `onSnapshotglra` | 互联网 | 逆回购 codes | Snapshot | 同上 | Period.snapshot | 受限 | `INVENTORIED` | R-001 30 秒 | 品种归类(category)未取证 |
| 104 | AD-Py | 21–22 | 17–18 | 实时订阅 | `onSnapshotfuture` | 互联网 | 中金所 codes | SnapshotFuture | 同上 | Period.snapshotfuture | 受限 | 【推断】≈flag 20；`INVENTORIED` | IF 主力 30 秒 | 期货权限未知 |
| 105 | AD-Py | 22 | 18 | 实时订阅 | `onSnapshotetf` | 互联网 | ETF codes | Snapshot | 同上 | Period.snapshot | 受限 | 【已证】raw 层同 #102 | 已有 `159518` | — |
| 106 | AD-Py | 22–23 | 18–19 | 实时订阅 | `onSnapshotkzz` | 互联网 | 可转债 codes | Snapshot | 同上 | Period.snapshot | 受限 | `INVENTORIED` | 单转债 30 秒 | — |
| 107 | AD-Py | 23 | 19 | 实时订阅 | `onSnapshothkt` | 互联网 | HKT codes | SnapshotHKT | 同上 | Period.snapshotHKT | 受限 | 【已证】raw 层 `LIVE_ALIGNED(flag12; SH route)` | 已有 `02800.SH` | `.SZ` 未验；示例代码存在 period 写成 snapshot 的文档笔误（PDF 24 页示例） |
| 108 | AD-Py | 24 | 20 | 实时订阅 | `onSnapshotoption` | 互联网 | ETF 期权 codes | SnapshotOption | 同上 | Period.snapshotoption | 受限 | 【推断】≈flag 11；`INVENTORIED` | 期权主力 30 秒 | — |
| 109 | AD-Py | 24–25 | 20–21 | 实时订阅 | `OnKLine`(实时 K 线注册) | 互联网 | codes+Period.min1 等 | Kline | 同上 | Period 1..120min/日…年 | 受限 | 【推断】≈flag 1..8；`INVENTORIED` | min1 单代码 5 分钟 | 分钟线 wire 映射未取证 |
| 110 | AD-Py | 25–26 | 21–22 | 历史行情 | `MarketData.query_snapshot(codes,begin,end,begin_time,end_time)` | 互联网 | codes+8位日期+HHmmssSSS 时间戳 | dict{code:df(Snapshot 族,index=datetime)} | 无 | 时间 8/9 位整型 | 是 | 【已证】协议层同参（tag 11000）；AD 合约 `CHANGES_REQUESTED` | 已有 SZSE 单日 | AD 层 dict-of-df 封装未验收 |
| 111 | AD-Py | 26–27 | 22–23 | 历史行情 | `query_kline(codes,begin,end,period,begin_time,end_time)` | 互联网 | codes+period+时分(3/4位) | dict{code:df(Kline)} | 无 | Period 附录 | 是 | 【已证】底层日线+周线+月线 `LIVE_ALIGNED`；AD 高层 wrapper `INVENTORIED` | 已有底层证据 | AD 的 dict-of-DataFrame 封装与 `Period` 到 TGW 周期映射仍未验；不能由底层取值反推高层已完成 |
| 112 | AD-Py | 27 | 23 | 财务 | `get_balance_sheet(codes,local_path,is_local,begin,end)` | 互联网 | codes+缓存组 | dict{code:df(百列级)} | 无 | REPORT_TYPE/STATEMENT_TYPE 附录 | 是 | 【推断】≈A010050001；`INVENTORIED` | 单代码单报告期 shape | 报表类型 1..91 枚举庞大，先 shape 后字段 |
| 113 | AD-Py | 36 | 32 | 财务 | `get_cash_flow(...)` | 互联网 | 同上 | dict{code:df} | 无 | 同上 | 是 | 【推断】≈A010050003；`INVENTORIED` | 同上 | — |
| 114 | AD-Py | 45 | 41 | 财务 | `get_income(...)` | 互联网 | 同上 | dict{code:df} | 无 | 同上 | 是 | 【推断】≈A010050002；`INVENTORIED` | 同上 | — |
| 115 | AD-Py | 52 | 48 | 财务 | `get_profit_express(...)` | 互联网 | 同上 | df | 无 | — | 是 | `INVENTORIED` | 单代码 | 业绩快报在 TGW 第7章无对应功能号 |
| 116 | AD-Py | 55 | 51 | 财务 | `get_profit_notice(...)` | 互联网 | 同上 | df | 无 | — | 是 | 【推断】≈A010050005；`INVENTORIED` | 单代码 | — |
| 117 | AD-Py | 57 | 53 | 股东股本 | `get_share_holder(...)` | 互联网 | codes+缓存组 | df | 无 | — | 是 | 【推断】≈A010040001；`INVENTORIED` | 单代码 | — |
| 118 | AD-Py | 58 | 54 | 股东股本 | `get_holder_num(...)` | 互联网 | 同上 | df | 无 | — | 是 | `INVENTORIED` | 单代码 | TGW 手册无对应功能号 |
| 119 | AD-Py | 59 | 55 | 股东股本 | `get_equity_structure(...)` | 互联网 | 同上 | df | 无 | PROGRESS 1..26 | 是 | 【推断】≈A010010004；`INVENTORIED` | 单代码 | 进度代码两套（分红/配股）不同 |
| 120 | AD-Py | 62 | 58 | 股东股本 | `get_equity_pledge_freeze(...)` | 互联网 | 同上 | df | 无 | — | 是 | 【推断】≈A010040004；`INVENTORIED` | 单代码 | — |
| 121 | AD-Py | 64 | 60 | 股东股本 | `get_equity_restricted(...)` | 互联网 | 同上 | df | 无 | — | 是 | 【推断】≈A010040002/3；`INVENTORIED` | 单代码 | — |
| 122 | AD-Py | 65 | 61 | 股东权益 | `get_dividend(...)` | 互联网 | 同上 | df | 无 | DIV_PROGRESS 1..19 | 是 | 【推断】≈A010030001；`INVENTORIED` | 单代码 | — |
| 123 | AD-Py | 67 | 63 | 股东权益 | `get_right_issue(...)` | 互联网 | 同上 | df | 无 | PROGRESS | 是 | 【推断】≈A010030002；`INVENTORIED` | 单代码 | — |
| 124 | AD-Py | 68 | 64 | 两融 | `get_margin_summary(...)` | 互联网 | 同上 | df | 无 | — | 是 | 【推断】≈A010080002；`INVENTORIED` | 单市场单日 | — |
| 125 | AD-Py | 69 | 65 | 两融 | `get_margin_detail(...)` | 互联网 | 同上 | df | 无 | — | 是 | 【推断】≈A010080001；`INVENTORIED` | 单市场单日 | — |
| 126 | AD-Py | 71 | 67 | 异动 | `get_long_hu_bang(...)` | 互联网 | 同上 | df | 无 | — | 是 | 【推断】≈A010070002；`INVENTORIED` | 单市场最新日 | — |
| 127 | AD-Py | 72 | 68 | 异动 | `get_block_trading(...)` | 互联网 | 同上 | df | 无 | — | 是 | 【推断】≈A010070001；`INVENTORIED` | 单代码最新日 | — |
| 128 | AD-Py | 73 | 69 | 期权 | `get_option_basic_info(...)` | 互联网 | 同上 | df | 无 | — | 是 | `INVENTORIED` | 单合约 | TGW 手册无对应功能号；底层疑似 DQS/其它服务 |
| 129 | AD-Py | 75 | 71 | 期权 | `get_option_std_ctr_specs(...)` | 互联网 | 同上 | df | 无 | — | 是 | `INVENTORIED` | 单标的 | 同上 |
| 130 | AD-Py | 76 | 72 | 期权 | `get_option_mon_ctr_specs(...)` | 互联网 | 同上 | df(UNIT_NEW,CHANGE_REASON…) | 无 | — | 是 | `INVENTORIED` | 单合约月 | 同上 |
| 131 | AD-Py | 78–82 | 74–78 | ETF | `get_etf_pcf(codes)` →(etf_pcf_info, etf_pcf_constituent) | 互联网 | codes | df+dict{code:df} | 无 | — | 是 | TGW 低层 #43 已闭环，但 AD 高层映射仍只是【推断】；`INVENTORIED` | `510300` 单只 | AD 返回拆两个容器；不能由低层 QueryETFInfo 成功外推高层 wrapper 已完成 |
| 132 | AD-Py | 82 | 78 | ETF | `get_fund_share(...)` | 互联网 | codes+缓存组 | df | 无 | — | 是 | `INVENTORIED` | 单基金 | TGW 手册无对应 |
| 133 | AD-Py | 83 | 79 | ETF | `get_fund_nav(...)` | 互联网 | 同上 | df | 无 | — | 是 | `INVENTORIED` | 单基金 | 同上 |
| 134 | AD-Py | 84 | 80 | ETF | `get_fund_iopv(...)` | 互联网 | 同上 | df | 无 | — | 是 | `INVENTORIED` | 单基金收盘 iopv | 同上 |
| 135 | AD-Py | 85 | 81 | 指数 | `get_index_constituent(...)` | 互联网 | codes | df | 无 | — | 是 | 【推断】≈A010200001；`INVENTORIED` | 单指数 | — |
| 136 | AD-Py | 86 | 82 | 指数 | `get_index_weight(...)` | 互联网 | codes | df | 无 | — | 是 | `INVENTORIED` | 单指数单日 | TGW 手册无对应功能号 |
| 137 | AD-Py | 88 | 84 | 行业指数 | `get_industry_base_info(...)` | 互联网 | codes | df | 无 | — | 是 | `INVENTORIED` | 单行业 | 同上（疑似 DQS 服务） |
| 138 | AD-Py | 89 | 85 | 行业指数 | `get_industry_constituent(...)` | 互联网 | 同上 | df | 无 | — | 是 | `INVENTORIED` | 单行业 | 同上 |
| 139 | AD-Py | 90 | 86 | 行业指数 | `get_industry_weight(...)` | 互联网 | 同上 | df | 无 | — | 是 | `INVENTORIED` | 单行业单日 | 同上 |
| 140 | AD-Py | 91 | 87 | 行业指数 | `get_industry_daily(...)` | 互联网 | 同上 | df | 无 | — | 是 | `INVENTORIED` | 单行业一日 | 同上 |
| 141 | AD-Py | 92 | 88 | 可转债 | `get_kzz_issuance(...)` | 互联网 | codes+缓存组 | df | 无 | — | 是 | `INVENTORIED` | 单转债 | TGW 手册无对应；可转债族共 11 个函数 |
| 142 | AD-Py | 96 | 92 | 可转债 | `get_kzz_share(...)` | 互联网 | 同上 | df | 无 | — | 是 | `INVENTORIED` | 单转债 | 同上 |
| 143 | AD-Py | 98 | 94 | 可转债 | `get_kzz_conv(...)` | 互联网 | 同上 | df | 无 | — | 是 | `INVENTORIED` | 单转债 | 同上 |
| 144 | AD-Py | 99 | 95 | 可转债 | `get_kzz_conv_change(...)` | 互联网 | 同上 | df | 无 | — | 是 | `INVENTORIED` | 单转债 | 同上 |
| 145 | AD-Py | 101 | 97 | 可转债 | `get_kzz_corr(...)` | 互联网 | 同上 | df | 无 | — | 是 | `INVENTORIED` | 单转债 | 同上 |
| 146 | AD-Py | 102 | 98 | 可转债 | `get_kzz_call(...)` | 互联网 | 同上 | df | 无 | — | 是 | `INVENTORIED` | 单转债 | 同上 |
| 147 | AD-Py | 103 | 99 | 可转债 | `get_kzz_put(...)` | 互联网 | 同上 | df | 无 | — | 是 | `INVENTORIED` | 单转债 | 同上 |
| 148 | AD-Py | 104 | 100 | 可转债 | `get_kzz_put_call_item(...)` | 互联网 | 同上 | df | 无 | — | 是 | `INVENTORIED` | 单转债 | 同上 |
| 149 | AD-Py | 106 | 102 | 可转债 | `get_kzz_put_explanation(...)` | 互联网 | 同上 | df | 无 | — | 是 | `INVENTORIED` | 单转债 | 同上 |
| 150 | AD-Py | 107 | 103 | 可转债 | `get_kzz_call_explanation(...)` | 互联网 | 同上 | df | 无 | — | 是 | `INVENTORIED` | 单转债 | 同上 |
| 151 | AD-Py | 108 | 104 | 可转债 | `get_kzz_suspend(...)` | 互联网 | 同上 | df | 无 | — | 是 | `INVENTORIED` | 单转债 | 同上 |
| 152 | AD-Py | 110 | 106 | 利率 | `get_treasury_yield(...)` | 互联网 | 缓存组 | df | 无 | — | 是 | `INVENTORIED` | 单日全期限 | TGW 手册无对应 |
| 153 | AD-Py | 111–131 | 107–127 | 本地算子 | `MathFunction/StatFunction/TimeSeries/CrossSection`（ABS…WMA 等约百个 Series 算子） | 本地计算 | Series/DataFrame | Series | 无 | 无网络交互 | 是（纯本地） | `INVENTORIED`（不属于服务端数据获取面） | 单算子冒烟 | 依赖 pandas；与 parity 工作流无关，不建议纳入对齐范围 |
| 154 | AD-Py | 133–139 | 129–135 | 附录 | security_type/market/trading_phase/security_status/REPORT_TYPE/STATEMENT_TYPE/DIV_PROGRESS/PROGRESS 文字表 | 参考 | — | — | — | STATEMENT_TYPE 1..91 | 是 | `INVENTORIED` | — | 多为文字描述，无数值化枚举，自动化校验困难 |
| 155 | AD-Py | 136 | 132 | 附录 | Period 枚举 | min1..min120/day/week/month/season/year（value 数值未印出，day 及以后未给值） | — | — | — | 与 TGW MDDatatype 10000..10012 对齐关系待证 | 是 | `INVENTORIED` | 对照表测试 | **AD 手册未给出 Period 数值**；TGW 低层已证日/周/月不能替代 AD `Period` 自身取值与映射取证 |
| 156 | AD-Py | 140–145 | 136–141 | 附录 | Snapshot/SnapshotOption/SnapshotFuture/SnapshotIndex/SnapshotHKT/Kline 结构 | 字段名与 TGW 结构对应但为 float 化简版 | — | — | — | 价格已还原为浮点 | 是 | `INVENTORIED` | 字段映射表测试 | AD 层字段名 ≠ TGW wire 数字 key；映射需逐字段取证 |
| 157 | AD-Py | 145–146 | 141–142 | 附录 | 本地缓存方案(local_path/is_local vs begin_date/end_date 两组参数互斥) | wrapper 行为 | — | — | — | hdf5 存储 ≥500GB 建议 | 是 | `INVENTORIED` | — | 属 wrapper 本地行为，Mac 重写可不实现，但需在兼容层声明 |

## 第五部分 HDR-v1.0.8 独有 / PDF-HDR 差异登记（不在任一 PDF 目录中）

| # | 来源 | 位置 | 类别 | 条目 | 说明 | 当前证据状态 | 依赖/风险 |
|---|---|---|---|---|---|---|---|
| E-1 | HDR-v1.0.8 | linux&windows `tgw.h:247`、`tgw_struct.h ReqHQFactor` | 查询 | `QueryHQFactor(factor_spi,ReqHQFactor)` DQS 因子 | 入参含 std::string/vector（非 pack(1) POD）；query_type/query_fields/security_list | `INVENTORIED` | 两份 PDF 均无章节；无公开取值文档；实现前必须 oracle 取证 |
| E-2 | HDR-v1.0.8 | `tgw_datatype.h:250–253` | 订阅枚举 | SubscribeDataType 21 kSnapshotL2 / 22 kTickOrder / 23 kTickExecution / 24 kOrderQueue | PDF 表只到 20 | `INVENTORIED` | 未经取证不得下发 |
| E-3 | HDR-v1.0.8 | `tgw_history_spi.h` 各 Spi | 查询回调 | `OnStatus(RspQueryStatus*)` 六联体状态回调 | PDF 3.5.6 未记载该回调 | `ARM_IMPLEMENTED(partial)`（错误语义未对齐） | 错误码语义对齐的必经之路 |
| E-4 | HDR-v1.0.8 | `tgw_struct.h:171` | 结构差异 | `ReqDefault.level_type(uint16_t,默认0)` | PDF ReqDefault 表止于 data_type；工作流已登记此冲突 | 已锁定（发行包 ABI 优先） | 本地结构跟 HDR，不跟 PDF |
| E-5 | HDR-v1.0.8 | `tgw_struct.h SubFactorItem` | 结构差异 | 因子订阅项多出 `security_code/market/category` 三字段 | PDF 表只有三个 factor 字段 | `INVENTORIED` | 实现 #9/#10 时按 HDR 字段集 |
| E-6 | HDR-v1.0.8 | `tgw_datatype.h InternetDataPermission/ColocationDataPermission` | 权限枚举 | 账号数据权限数值表 | PDF 未记载 | `INVENTORIED` | 可用于解释 kPermissionError=-95，但不能反推权限开通 |
| E-7 | 项目证据 | docs/evidence/* | 状态差异 | 实测日历功能号 `A010061003` 在 TGW-C++ 目录中不存在（PDF 只有 A01006001/A010061001） | 来自官方 Python wrapper 行为或服务端更新 | `LIVE_ALIGNED(calendar function only)` | 证据文件必须继续记录该不一致，避免后人“纠正”回 PDF 值 |

## 遗漏风险汇总

1. **功能号/枚举取值大量未数字化**：AD-Py 的 `Period`、`security_type`、`market` 等附录多为文字，无数值；TGW 附件 1 各功能号请求参数分散在 94–182 页，本清单只登记到章节粒度。领任务卡时必须回到具体页抄全字段，不能只凭本清单。
2. **PDF↔HDR 边界冲突未决**：`QueryTickExecution` 模式（#35）、`SubFactorItem` 字段集（E-5）、`ReqDefault.level_type`（E-4）、`SubscribeDataType` 21–24（E-2）。凡冲突处以发行包 HDR + Linux oracle 实测为准，PDF 不单独作准。
3. **AD-Py 高层→TGW 底层映射大多为推断**（标注【推断】的 30 余行）：官方未发布映射文档；`amazingdata_compat` 现状为 `EXPERIMENTAL`。任何一行要升级状态，都必须先用 Linux oracle 观测其真实 wire 方法，再决定挂到哪个 TGW 底部接口。
4. **盘后可测性受限的行**：所有实时订阅（#101–#109）依赖开市时段或事件触发（VCM #25 不可计划）；回放/托管机房行（#11/#13/#14/#30/#31/#34/#36/#44/#46–#48）在互联网主线永不可测。
5. **本清单本身不是状态提升依据**：全部新行仅为 `INVENTORIED`；按工作流，`STATIC_MATCHED` 起需要逐字段三方比对，`LIVE_ALIGNED` 起需要 Linux/Mac 同参证据。中央状态仍以 `docs/API_STATUS.md` 为权威，验收者未复跑前不得据本清单改状态。
6. **安全边界**：#6/#89（改密）与一切写操作保持“只登记、不实现、不执行”；登录返回中的 token、logon_json 内容不得进入后续任何 fixture 或日志。
