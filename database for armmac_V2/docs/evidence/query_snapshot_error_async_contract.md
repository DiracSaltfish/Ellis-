# QuerySnapshot 错误码与异步 SPI 合约对齐证据（SZSE ETF L1 子范围续篇）

- Scope: 互联网模式、SZSE `159518`、`data_type=0`、`level_type=0`（构造默认）、历史交易日
  20260825。本轮只补齐既有子范围的**同步非零错误码语义**（重点 `kDataEmpty=-76`）与**官方
  异步 `query_spi` 回调合约**，并做 Linux/Mac 同参复验；不扩大市场、代码、data_type 或时间窗。
  数据面 wire/解析契约见 [query_snapshot_szse_etf.md](query_snapshot_szse_etf.md)，静态契约见
  [query_etf_info_static.md](query_etf_info_static.md) 的结构部分。

- PDF: C++ 手册 PDF 页 34（正文 26）`QuerySnapshot/ReqDefault`；PDF 页 39–44（正文 31–36）
  查询回调章节的 `OnStatus(RspQueryStatus*)`（HDR-only 面，登记号 E-3）；PDF 页 62（正文 54）
  ErrorCode 表（含 `kDataEmpty=-76`）。AmazingData 手册 PDF 页 25–26（正文 21–22）注明
  “result 为 None 时查看 err_code”。

- Header delta: 无新增差异。`ReqDefault` 维持 pack(1)=55、`level_type` offset=53。

- 官方 wrapper 静态合约（bj 上 V1.0.9.2 wheel 源码逐行核对）：
  - 同步：`IGMDApi_QuerySnapshot` 提交即返错误码，非 0 → `(None, error_code)`；
    否则等待事件后返回 `(result, err_code)`，空数据经 `OnStatus(status.error_code)` 得到
    `(None, kDataEmpty)`。
  - 异步：提交失败 → `(False, error_code)`；成功 → `(True, None)` 立即返回，结果由后台线程
    **直接调用用户对象** `spi(result, err_code)`：数据批次 `(list_or_df, None)`，
    `OnStatus` → `(None, error_code:int)`，内部异常 → `(None, str(e))`。
  - `error_code.py` 提供全部公开错误码中文文案；`GetErrorMsg` 未收录时返回
    "unknown error code"。

- Linux oracle: 2026-08-26 在 bj 用 Linux x86_64 官方 SDK（galaxyrelay 用户、venv python、
  凭据仅读 relay.env）执行三次独立会话，执行前后 `galaxy-relay` 均为 inactive：
  1. 同步空窗口（20260825 12:00:00.000–12:00:01.000）：登录 true、`query_error` 整数 **-76**、
     result=NoneType；
  2. 异步数据窗口（93000000–93030000）：submit_return=`(True, None)`，**1 个回调**
     `(list, records=11, err=None)`，首次回调延迟 0.456s；
  3. 异步空窗口：submit_return=`(True, None)`，**1 个回调** `(None, err=-76)`，
     延迟 10.368s（服务端生成空结果的耗时）。
  未输出任何价格/数量值。

- Wire（新增错误帧形状，SSL_write/read 脱敏捕获一次，分析后立即删除）：
  空查询响应帧为
  `headers={id:<请求id回显>, tag:"DataEmpty", pack_num:0, all_pack_num:0}`、
  `status:-100`（wire 层通用失败）、`data:""`（空字符串）；header key 顺序
  `id, tag, pack_num, all_pack_num`。**wire tag 是字符串 "DataEmpty"、status 是 -100**，
  官方 SDK 将其映射为公开 `kDataEmpty=-76`。随后服务端发 WebSocket close
  `1000 "Query connection will exit"`；**官方客户端在错误路径不发送 ReqGetComplete**，
  直接关闭。数据帧路径维持既有证据（tag=11000、pack 计数器、ReqGetComplete）。
  映射表只收录已捕获标签；未知标签显式失败。

- Arm:
  - `_protocol.py`：新增 `SNAPSHOT_ERROR_TAGS={"DataEmpty":-76}` 与 `_snapshot_error_code`；
    `parse_snapshot_packets` 改为返回 `(rows, error_code|None)`——错误帧与数据帧互斥出现，
    混合帧形、多个不同错误码、未知错误标签均抛 `TgwProtocolError`；数据路径校验不变。
  - `_backend.py`：`LiveBackend.query` 拆分为同步 `build_query`（会话校验 + envelope 构造 +
    端点轮转）与 `run_query`（一次性 query WSS 交换），使异步模式的“提交阶段”可与官方一样
    同步报错；捕获到错误响应时跳过 `ReqGetComplete`（对齐官方关闭行为）后直接关闭。
    `etf_info` 路径保持原有 push 通道实现不变。
  - `interface.py`：`QuerySnapshot` 重写——同步模式返回 `(rows,0)`/`(None,-76)`（DataFrame
    模式在空数据同样返回 `(None,-76)`）；异步模式提交成功立即 `(True,None)`，后台线程交付
    `(rows_or_df,None)`、`(None,error_code)`、超时映射 `kTimeout=-83`、内部异常
    `(None,str(exc))`，与官方 wrapper 一致地直接调用用户对象。
  - `__init__.py`：`ErrorCode` 扩充为完整公开表（kFailure=-100 … kDqsError=-69,
    kSuccess=0，值与官方 wheel 一致；移除非官方的 `kFail=-1` 占位）。
  - `interface.GetErrorMsg` 对齐全量官方中文文案，未收录返回 "unknown error code"。
  - `tools/live_smoke.py` 新增 `--snapshot-async`（打印 submit 返回与回调计数摘要，
    不含业务原值）；`tools/oracle/remote_sdk_oracle.py` 新增 `--kind snapshot --snapshot-async`
    收集器（submit 返回、回调次数、记录数、err_code、首回调延迟）。

- Tests: `python3 -m unittest discover -s tests -v` 共 **57 项全部通过**（50 → 57）：
  新增 DataEmpty 错误帧→-76 映射、未知错误标签拒绝、数据/错误混合帧拒绝、多错误帧一致/
  冲突分支（第二标签仅合成测试）、ErrorCode 关键值与 GetErrorMsg 文案、同步空数据
  `(None,-76)` 双格式、异步 SPI 成功交付、异步超时 `-83`/异常 str 透传、异步空数据
  `(None,-76)` 回调。`python3 -m compileall -q src/python examples tools` 通过。

- Live diff: 2026-08-26 盘后在 Mac（Apple Silicon、第二授权账号经 stdin 注入）完成同参复验：
  | 场景（完全同参） | Linux 官方 SDK | macOS arm64 |
  |---|---|---|
  | 同步·数据窗 93000000–93030000 | error=0，11 行 × 57 列 | `snapshot_query_error=0 rows=11 columns=<57 键>` |
  | 同步·空窗 12000000–12001000 | `(None, -76)` | `snapshot_query_error=-76 rows=0` |
  | 异步·数据窗 | submit `(True,None)`；1 批 `(list,11,None)` | submit True/None；回调 `[('list',11,None)]` |
  | 异步·空窗 | submit `(True,None)`；1 批 `(None,-76)` | submit True/None；回调 `[(None,None,-76)]` |
  本轮查询通道三次遭遇服务端准入回收（WebSocket close `1000 "accept conn active close"`，
  间隔数分钟、含备用端点各一次），按工作流停止密集重试、低频间隔后单次尝试成功；该现象
  与既有流控登记一致，不影响上表四个场景的对齐结论。未保存任何业务原值。

- Cleanup: 远端 `/tmp/tgw_snap_oracle/`（oracle 副本、interpose `.so/.c`、capture.bin）已删除
  并复核不存在；本地临时 capture 与分析文件已删除；`galaxy-relay` 任务前后均为 inactive。

- Proposed status: `LIVE_ALIGNED(SZSE ETF L1 snapshot; data_type=0 sync+async error contract)`。
  仅覆盖既有市场/代码/data_type/level_type 子范围及其同步+异步错误语义。

- Open risks:
  1. 多包异步交付语义未观测：当前实现把收齐的全部行放在一个回调交付；官方 C++ 为每包
     一次回调。线上 >1 包样本出现前不得宣称分批等价；
  2. 除 `DataEmpty` 外的错误标签（如非查询时段、无权限）未捕获，映射表为空集之外一律显式
     失败；
  3. 异步内部异常按官方 wrapper 透传 `str(e)`，超时映射 `-83` 属合理推断而非逐项取证；
  4. 查询通道准入回收（1000 accept conn active close）机制未知，低频纪律仍必须遵守；
  5. data_type=1/2、其他市场/代码、DataFrame 列序与官方 `json_normalize` 的逐列一致性仍未验。
