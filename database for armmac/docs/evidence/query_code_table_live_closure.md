# QueryCodeTable 对齐证据（互联网模式全市场闭环）

- Scope: 仅 `QueryCodeTable` 互联网模式（kInternetMode）全市场全量子范围，无业务入参。
  覆盖 PDF/HDR/官方 wrapper 静态契约、Linux 官方 SDK 行为、脱敏 wire 取证、Mac arm64 实现、
  合成测试与一次 Mac live 流控观测。不覆盖 `QuerySecuritiesInfo` / `QueryExFactorTable` /
  `ReqGetReduceCodeTable` 内部变体；不动订阅路径；不做写操作。
- PDF: TGW-C++ 手册 PDF 页 36（正文 28）`6) QueryCodeTable`；回调 `OnMDCodeTable` PDF 42–43
  （正文 34–35）；`§5.24 MDCodeTable` PDF 80（正文 72）；`security_type` 取值表 PDF 89–90；
  `currency` 取值 PDF 90。请求除 SPI 外无参数；`OnMDCodeTable(MDCodeTable*, cnt)` + `OnStatus`。
- Header delta: `tgw_struct.h:841-849` `MDCodeTable`（pack(1)）：
  `security_code[16], symbol[32], english_name[128], market_type:uint8, security_type[10],
  currency[4]` → 本地 sizeof=191，offset 0/16/48/176/177/187（常量来源
  `tgw_datatype.h:18-44`）。V1.0.8 linux==windows，本接口无发行包差异。官方容器 6 列
  `security_code:str, symbol:str, english_name:str, market_type:int, security_type:str,
  currency:str`，无补列。
- Linux oracle: 官方 SDK 登录成功（Bool True）；sync probe 返回 **-83 kTimeout**（`kTimeout`
  = `ErrorCode` 表值）。原因：服务端全市场查询共 12 包，**第 3 包未送达**且对
  `ReqGetPackage {pack_num:"3,"}` 补拉无响应 → 官方同步 wrapper 超时。遵守纪律未密集重试。
  预期错误码族 `-95/-81/-78/-83/-76/-98` 中实际命中 `-83`（服务端丢包/流控，非权限）。
- Wire: 通道为 one-shot **`/amd/dgw/dgw1_query`**（非 push 会话）；请求 method
  **`ReqGetReduceCodeTable`**（推翻静态候选 `ReqGetCodeTableList`），params 仅
  `{"QueryBandWidth": 0.0}`，headers 顺序 `id,userName,token`，id 回显 1；丢包补拉
  **`ReqGetPackage {pack_num:"N,"}`**；响应 tag **11103**（int）、status 0、
  `pack_num/all_pack_num=12` 分页；帧 `0x59 + ZSTD`；`data` 为字符串行数组，行分隔符
  **反引号 U+0060**，恰 6 字段对应 MDCodeTable 顺序
  （security_code 数字 / symbol 中英混 / english_name 可空 / market_type 十进制→int /
  security_type 字母数字 / currency 3 字母或空/空白）。完成 method **未捕获**（同步 probe
  在补拉前即超时），因此沿用 dgw*_query 通道标准 `ReqGetComplete`，`ReqGetCodelistComplete`
  仍是未证候选、未混用（见 Open risks）。
- Arm:
  - `_structures.py`：新增 `MDCodeTable` pack(1) ctypes 镜像（sizeof=191 + offset 测试）。
  - `_protocol.py`：`CODE_TABLE_WIRE_TAG=11103`、`CODE_TABLE_ROW_FIELD_COUNT=6`、
    `CODE_TABLE_COLUMNS`；`build_code_table_request(username, token, request_id)` 纯函数
    （headers `id,userName,token`，method `ReqGetReduceCodeTable`，params
    `{"QueryBandWidth":0.0}`）；`build_get_package_request`（丢包补拉）；`parse_code_table_packets`
    复用 `_ordered_query_packets` 校验 tag/status/pack 完整性后按 6 字段反引号契约解析，
    未知 tag/status/字段数/非整型 market_type 显式失败；security_type/currency 透传不裁剪。
  - `_backend.py`：`"code_table"` kind 分支走 one-shot dgw*_query 通道
    `_query_code_table`（连接端点池 → `ReqGetReduceCodeTable` → `_collect_paged_query` 按
    all_pack_num 收集并在缺包时经 `ReqGetPackage` 低频补拉一次 → 通用 `ReqGetComplete` →
    `wait_closed` → `parse_code_table_packets`）。
  - `interface.py`：`QueryCodeTable(query_spi=None, return_df_format=True)` 返回
    `(list[dict] | DataFrame, err_int)`；**Mac 同步返回累计全部批次**（对齐官方异步总数，
    偏离官方同步 wrapper 的首批竞态）；`query_spi` 传入显式 `NotImplementedError`。
  - `__init__.py`：仅追加 re-export `QueryCodeTable = interface.QueryCodeTable`。
  - `tools/live_smoke.py`：`--code-table` 单旗标，输出脱敏 shape（rows/columns/column_types/
    distinct_market_types/distinct_security_type_count/distinct_currency_count/
    code_length_histogram/duplicate_code_rows）。
- Tests: `tests/test_code_table_protocol.py`（18 项）+ 既有 57 项 = **75 项全绿（1 skip
  pandas 缺失分支）**。覆盖 ① MDCodeTable sizeof=191/offset/默认零；② builder key 顺序/类型/
  补拉包；③ parser 单包 + 多包乱序重组/缺包/重复包/错 tag/错 status/data 容器错型/字段数不符/
  非整型 market_type 共 8 类负形状；④ 公开合约：同步元组、`query_spi` 显式失败、未知 kind
  显式失败、re-export。命令：
  ```bash
  PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v   # 75 OK (skipped=1)
  PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q src/python examples tools  # OK
  ```
- Live diff: Mac live 用第二授权账号（stdin 注入，未持久化），两次低频尝试：
  1. dgw1 端点：登录成功、`OnRspLogon`、发起全市场查询；服务端 12 包中仅送达 11 包
     （缺第 3 包），`ReqGetPackage` 补拉无响应 → 缺包超时（与官方 Linux 的 `-83` 同因同果）。
  2. dgw2 备用端点（换端点一次）：登录成功后被服务端以 `1000 / accept conn active close`
     流控回收（workflow 明列的流控/回收证据）。
  两侧（Linux `-83` + Mac 缺包超时 / `1000` close）均被服务端同一类全市场大表流控阻塞，
  未能取得完整 12 包成功样本，因此**无 Linux/Mac 成功同参数据可比**。wire 已证形状
  （11 包共 167,269 行、缺第 3 包、`ReqGetPackage` 无响应）作为离线解析器等效性检查，
  Mac 解析器同参形状（6 列/类型/不变量）与 wire 一致。
- Cleanup: 远端 `/tmp/tgw_ct_oracle/` 全部临时文件（capture.bin/capture_copy.bin/
  replay_frame.bin/interpose.so/ssl_write_interpose.c/分析脚本）、`/tmp/ct_*.py`、
  `/tmp/tgw_ct_oracle` 目录已删除；本地 `ct_*.py`/`ct_deep_shape_full.txt` 中间脚本与
  捕获分析已删，`__pycache__` 清理；仓库扫描无账号/密码/token/MAC/原始行情/完整捕获。
  `ssh bj systemctl is-active galaxy-relay` 任务开始与结束均为 **inactive**（未启动任何
  用户服务）。
- Proposed status: **`ARM_IMPLEMENTED`**。
  依据：结构/协议/parser 已实现并通过合成测试，未知分支显式失败；wire 已证；Mac live 已执行，
  但 Linux 与 Mac 均被服务端全市场代码表大表流控阻塞（`-83` / 缺包 / `1000 close`），无
  Linux/Mac 成功同参结果，不能拟议 `LIVE_ALIGNED`。按 workflow 保留已达状态。
- Open risks:
  1. 服务端全市场代码表查询持续缺包（两次独立会话均缺第 3 包）且对 `ReqGetPackage` 无响应，
     属服务端流控/分片交付问题，非客户端缺陷；完整 12 包成功样本在当前时段不可得。
  2. 完成 method 未捕获：沿用 dgw*_query 通用 `ReqGetComplete`；`ReqGetCodelistComplete`
     候选未证、未混用，需在成功补全样本后再取证。
  3. `currency` 存在空串与空白串（如 `'  '`）、`security_type` 存在空串与数字串：当前透传
     不裁剪，官方 SDK 是否做 trim 未能在成功同参中确认，需成功样本后复核。
  4. `query_spi` 异步公开面按现行政策显式 `NotImplementedError`；`OnStatus`/`FreeMemory`
     回调语义与批量内存所有权未实现。
  5. 账号需 `InternetDataPermission.kCodeTable=32` 权限；本会话未出现 `-95`，但不排除其它
     时段/账号触发。
  6. 全市场行数（动态退市表）随时间小幅漂移属预期；当前因缺包无法给出权威全量行数。
