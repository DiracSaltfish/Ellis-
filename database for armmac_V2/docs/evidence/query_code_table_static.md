# QueryCodeTable 静态对齐证据

- Scope: 仅静态契约。覆盖 `QueryCodeTable`（无入参代码表查询）在互联网模式下的 PDF/HDR/官方
  Python 三方字段比对；不覆盖 `QuerySecuritiesInfo`/`QueryETFInfo`/`ReqGetReduceCodeTable`
  内部变体。本轮无在线请求、无登录、无抓包；未修改任何实现、测试、中央矩阵或 Excel。
- PDF: TGW-C++ 手册 PDF 页 36（正文 28）`6) QueryCodeTable 方法`：托管机房和互联网模式适用；
  查询当天交易所最新代码表 + 自 2022-08-22 上线以来保存的已退市代码；原型
  `static int32_t QueryCodeTable(const IGMDCodeTableSpi* code_table_spi)`，除 SPI 外无请求参数；
  SPI 实例必须存活到 `Release()` 之后。回调页 PDF 42–43（正文 34–35）：
  `OnMDCodeTable(MDCodeTable* code_tables, uint32_t cnt)` + 需显式 `FreeMemory`。
  输出结构 §5.24 `MDCodeTable` 见 PDF 80（正文 72）；`security_type` 取值表见 PDF 89–90
  （正文 81–82，如 ETF=3001、可转债=4001 等）；`currency` 取值见 PDF 90（正文 82）。
  示例命令 `QueryCodeTable codeTable`（PDF 59 / 正文 51）与 demo `QueryCodeTableReq`
  （PDF 50 / 正文 42）均确认无请求参数、同步返回码即 `ErrorCode`。
- Header delta:
  - V1.0.8 linux 与 windows 五个头文件经 CRLF 归一后逐字节一致（本轮回 `cmp` 复核），
    本接口无 Linux/Windows 发行包差异。
  - `tgw.h:188-194` 与 `tgw_history_spi.h:318-344` 均标注双模式通用；**PDF 正文 34 把
    `OnMDCodeTable` 回调写成"仅托管机房模式适用"，与同手册正文 28 及 HDR 矛盾**
    （PDF 内部冲突登记）。按证据优先级以 HDR 为准：互联网模式适用。
  - `tgw_struct.h:841-849` `MDCodeTable`（pack(1)，§5.24 与 HDR 字段一一对应）：
    `security_code[16], symbol[32], english_name[128], market_type:uint8,
    security_type[10], currency[4]` → **本地 pack(1) sizeof = 191**（16+32+128+1+10+4），
    关键 offset = 0/16/48/176/177/187。常量来源 `tgw_datatype.h:18-44`
    （kSecurityCodeLen=16、kSecuritySymbolLen=32、kSecurityEnglishNameLen=128、
    kSecurityTypeLen=10、kCurrencyLen=4）。
  - 分页/内存语义：公开 API 无分页参数；大批量数据通过多次 `OnMDCodeTable` 批次交付，
    每批指针由 SDK 侧默认体 `FreeMemory`；wire 级分页控制（pack_num/all_pack_num）未取证。
    `OnStatus(RspQueryStatus*)` 为 HDR-only 面（工作流 E-3），其中
    `rsp_union_status.req_type` 注释仅提 K 线，代码表查询状态在六联体中的具体填充未取证。
  - 权限枚举：`InternetDataPermission.kCodeTable=32`、`ColocationDataPermission.kCodeTable=23`
    （`tgw_datatype.h:347/388`），可用于解释 `kPermissionError=-95`，不能反推账号已开通。
- Official Python static discovery（bj 只读检查，2026-08-26；galaxy-relay 前后均为 inactive；
  未建立任何网络连接、未运行真实查询、未读取凭据）：
  - 公开 wrapper：`tgw.QueryCodeTable(query_spi=None, return_df_format=True)`，docstring 明示
    "使用范围：托管机房和互联网模式适用"。同步模式返回 `(result, err)` 元组；
    异步模式经用户 `query_spi.OnResponse(result, err_code)`。
  - 内部链路（interface.py L409-447）：立即错误码非 0 时直接 `(None, err)` 不等待；否则
    `TmpQueryCodeTableSpi(wait_event)` 调 `IGMDApi_QueryCodeTable(spi)` 后 `wait_event.wait()`。
    `TmpQueryCodeTableWaitSpi.OnResponse` 对每个数据批次**整体覆盖 `_result`** 且 wait_event
    在首个批次或 OnStatus 即 set —— 同步 wrapper 在多包响应下可能只保留首个批次（竞态）。
    Linux oracle 计数必须用异步 SPI 收集器逐批累计，并另跑一次同步调用记录该差异。
  - 回调转换：`OnMDCodeTable` 用 `Tools_CodeTableToJson(code_tables, cnt)` → JSON 数组 →
    `json_normalize`（df）或原样 list[dict]。本地构造对象实测 JSON key 恰为结构体序 6 列：
    `security_code:str, symbol:str, english_name:str, market_type:int, security_type:str,
    currency:str`。即官方容器列集合 = 这 6 列，无额外补列（对比 kline 补 orig_time/
    variety_category 的行为）。
  - SWIG `MDCodeTable` 成员与 `ConstField` 全部长度常量与 V1.0.8 头文件一致（逐一打印核对）；
    `IGMDCodeTableSpi` director 暴露 `OnMDCodeTable/OnStatus` 两回调。
  - 二进制字符串（libtgw.so 静态 strings，仅作假设、不作协议依据）：
    wire method 候选 `ReqGetCodeTableList`；专用完成消息候选 `ReqGetCodelistComplete`
    （区别于 kline/snapshot/thirdinfo 已证的通用 `ReqGetComplete`）；另有内部增量变体
    `ReqGetReduceCodeTable`/`IReducedCodeTableSpi`（不在 V1.0.8 公开 IGMDApi 面内，排除在外）。
    符号显示互联网代码表走 `mdga::PushSendRequest::SendCodelistRequest`/`CodeTablelistHandle`
    （挂在 PushDecoder 构造里），并有本地缓存/预热字符串（"The CodeTableList is not prepared"、
    "Query codeTable service is not get ready"、retry/timeout 带 data_type 参数日志）
    —— **代码表的 wire 通道可能与 kline/snapshot 的独立 query WSS 端点不同，必须实捕证明**。
- 下一轮最小 Linux oracle 方案（待验收者安排；本轮未执行）：
  - 入口扩展：`tools/oracle/remote_sdk_oracle.py` 新增独立 `--kind code-table`，沿用
    relay.env 凭据注入与 galaxyrelay 用户约束；先 `systemctl is-active galaxy-relay` 确认
    inactive 再单次运行。
  - 确切调用签名（同步一次）：`tgw.Login(cfg, tgw.ApiMode.kInternetMode)` →
    `result, err = tgw.QueryCodeTable(return_df_format=False)` → 记录后 `tgw.Close()`。
    无市场/日期参数可选——该接口无入参，全市场全量即最小样本。
  - 另跑一次异步收集器：自定义 spi 以 `OnResponse(result, err)` 逐批 append，统计
    批次数/每批行数，用于对照同步 wrapper 的首批竞态。
  - 允许记录的脱敏摘要：登录布尔、err 类型与数值、总行数、批次数、每批行数序列、
    6 列名与 Python 类型集合、不变量（列数恒 6；`market_type` 取值集合应为 {2,101,102} 子集
    —— 仅记录集合本身；`currency`/`security_type` 取值集合；code/symbol 非空比例；
    security_code 长度分布；重复 code 计数存在性布尔）。禁止输出任何具体代码、简称或原值行。
  - 预期错误码：`-95`（权限未开通）、`-81`（缓存不可用）、`-78`（超最大查询数含代码表）、
    `-83`（超时）、`-76`（空）、`-98`（spi 空，仅本地）；非零时停止，不做密集重试。
  - 仍必须由 wire capture 证明的内容：WSS path（push 会话还是 dgw*_query）、请求 method
    （`ReqGetCodeTableList`?）、params key 顺序与是否携带 QueryBandWidth/data_type 类参数、
    request id 空间与关联方式、响应数值 tag（MDDatatype 中无代码表条目，不可推导）、
    status/pack_num/all_pack_num 分页控制、data 容器形状（CSV 行数组还是 JSON 对象数组）、
    完成 method（通用 `ReqGetComplete` 还是 `ReqGetCodelistComplete`）、ZSTD 标记、
    双端关闭语义。oracle 返回容器一致不代表 wire 一致。
- 预计 Mac 实现面（本轮不改代码）：
  - `_structures.py`：新增 `MDCodeTable` pack(1) ctypes 镜像（sizeof=191 + offset 测试）；
    `SubCodeTableItem` 留给 #38/#43 任务，不顺手实现。
  - `_protocol.py`：capture 后新增 `CODE_TABLE_WIRE_TAG` 与 `build_code_table_request(username,
    token, request_id)`（纯函数、合成 fixture 可测；wire 未证前任何实现必须抛
    `NotImplementedError`）；`parse_code_table_packets(packets, expected_tag)` 复用
    `_ordered_query_packets` 做 tag/status/pack 校验，再按 6 字段契约解析；未知 market/
    security_type/currency 值透传不裁剪、未知 tag 显式失败。
  - `_backend.py`：`query()` 增加 `"code_table"` kind 分支（若 capture 证明走 push 会话则改为
    push 通道投递，不得沿用 dgw query 端点猜测）。
  - `interface.py`：新增 `QueryCodeTable(query_spi=None, return_df_format=True)`，与官方元组
    合约对齐：`(list[dict] | DataFrame, err_int)`；`query_spi` 传入时按现行政策显式抛
    `NotImplementedError`（异步 SPI 公开面统一 `NOT_IMPLEMENTED`）。
  - `tools/live_smoke.py`：加 `--code-table` 单旗标路径，输出脱敏 shape 摘要。
  - 测试清单（全部合成 fixture）：① 结构 sizeof=191/offset/默认零测试；② builder key 顺序/
    类型/枚举映射测试（capture 后落地）；③ 单包解析 + 多包乱序重组/缺包/重复包/错 tag/
    错 status/data 容器错型/字段数不符共 7 类负形状；④ 公开合约：同步元组、spi 显式失败、
    pandas 缺失降级；⑤ 未证分支显式 `NotImplementedError`。
- Tests: 本轮为静态任务，未新增/修改测试；仓库现有
  `python -m unittest discover -s tests -v` 与 `python -m compileall -q src/python examples tools`
  基线未被触碰。
- Live diff: 无（本轮禁止在线操作）。
- Cleanup: 未创建远端临时文件；未启动/停止任何服务；`galaxy-relay` 任务前后均为 inactive；
  本地仅读取 reference 与远端只读自省，无捕获文件落盘。
- Proposed status: `STATIC_MATCHED(QueryCodeTable static contract only)`。
  依据：PDF §6/§5.24/§6.2/§6.3、V1.0.8 头文件（linux==windows）、官方 Python SWIG 对象与
  wrapper 行为三方逐字段比对完成；`LINUX_OBSERVED` 起需真实最小请求，本轮未执行。
- Open risks:
  1. wire 通道假设未证：符号暗示代码表可能复用 push 会话与本地缓存预热，若属实则
     dgw*_query 一次性连接模式不适用，Mac 实现面要改。
  2. 完成语义候选 `ReqGetCodelistComplete` 与已证通用 `ReqGetComplete` 并存，未证前不得混用。
  3. 官方同步 wrapper 多批竞态可能导致行数低估；Linux/Mac 同参验收必须以异步逐批计数为准。
  4. 响应 tag 无公开枚举可推导；任何"猜 tag"实现都被禁止。
  5. 账号需 `InternetDataPermission.kCodeTable=32` 权限；`-95` 视为权限证据而非实现缺陷。
  6. 全市场+退市代码量大，注意流控（`1000 accept conn active close` 先例）与单次执行纪律。
  7. AD 高层 `get_code_list/get_code_info` 的 pyc 字符串出现 `SubCodeTableItem`/
     `QuerySecuritiesInfor`，提示其底层更可能是 #38 而非本接口；两接口边界保持独立取证。
