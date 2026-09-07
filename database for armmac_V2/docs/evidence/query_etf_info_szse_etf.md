# QueryETFInfo 单 ETF（SZSE `159919`）对齐证据

- Scope: 互联网模式的 `QueryETFInfo`，仅
  `SubCodeTableItem{market=102(SZSE), security_code="159919"}`、**单 item**、同步
  `return_df_format=False` JSON 成对容器。多 item、异步 SPI、空/非零 status、未证市场和服务端
  多响应帧均不在本范围；它们保持显式拒绝。本文独立于
  [SSE 证据](query_etf_info_sse_etf.md)，不以其业务 shape 推导本结论。

- PDF: TGW C++ 手册 PDF 页 39（正文 31）给出双模式 `QueryETFInfo` 原型和
  `SubCodeTableItem`；页 44（正文 36）给出 `IGMDETFInfoSpi::OnMDETFInfo`，并要求显式
  `FreeMemory`。AmazingData PDF 页 78–81（正文 74–77）的 `get_etf_pcf` 仅作高层字段映射
  交叉参考：它将沪深 ETF 拆为基础 DataFrame 与成分股 DataFrame，不能据此假设底层通道相同；页
  82 已核对，为不相关的 `get_fund_share` 起始页。

- Static contract (PDF/HDR/official Python/local): V1.0.8 的 Linux/Windows 头文件无差异；
  `tgw.h` 限定本接口市场为沪深，`tgw_struct.h` 处于 `#pragma pack(1)`。官方 Python 1.0.9.2
  接受一个 `SubCodeTableItem`，JSON 模式回传
  `[(basic_info_dict, constituent_stock_info_list), ...]`；本地按已捕获的数字槽位解码为完全同名的
  有序 dict，不 ctypes 镜像含 `std::vector` 的外层记录。

  | 输入字段 | PDF / V1.0.8 | 官方 Python 对象 | 本地 ABI / 校验 | 结论 |
  |---|---|---|---|---|
  | `market` | `int32_t`，本接口仅 SSE/SZSE | 可写，默认 `0` | `c_int32`，offset 0 | 一致 |
  | `security_code` | `char[32]` | 可写，默认空串 | `char[32]`，offset 4 | 一致 |
  | 整体 | `pack(1)`，`sizeof=36` | 单项或 list 均可传入官方 wrapper | `sizeof=36`；本范围只放行单项 | 一致、范围收窄 |

  基础记录的固定部分为 35 个槽；表中 `i64/u8/char/a[n]` 为 PDF/HDR 类型，官方 Python 与本地
  结果列为 `int/str`（单 `char` 在线上是 ASCII 整数，Mac 转为字符串，NUL 转空串）：

  | 槽 | 字段 | PDF/HDR | 官方 Python / 本地 |
  |---:|---|---|---|
  | 1 | `security_code` | `a[16]` | `str` |
  | 2 | `creation_redemption_unit` | `i64` | `int` |
  | 3 | `max_cash_ratio` | `i64` | `int` |
  | 4 | `publish` | `char` | `str` |
  | 5 | `creation` | `char` | `str` |
  | 6 | `redemption` | `char` | `str` |
  | 7 | `creation_redemption_switch` | `char` | `str` |
  | 8 | `record_num` | `i64` | `int` |
  | 9 | `total_record_num` | `i64` | `int` |
  | 10 | `estimate_cash_component` | `i64` | `int` |
  | 11 | `trading_day` | `i64` | `int` |
  | 12 | `pre_trading_day` | `i64` | `int` |
  | 13 | `cash_component` | `i64` | `int` |
  | 14 | `nav_per_cu` | `i64` | `int` |
  | 15 | `nav` | `i64` | `int` |
  | 16 | `market_type` | `u8` | `int` |
  | 17 | `symbol` | `a[128]` | `str` |
  | 18 | `fund_management_company` | `a[128]` | `str` |
  | 19 | `underlying_security_id` | `a[16]` | `str` |
  | 20 | `underlying_security_id_source` | `a[4]` | `str` |
  | 21 | `dividend_per_cu` | `i64` | `int` |
  | 22 | `creation_limit` | `i64` | `int` |
  | 23 | `redemption_limit` | `i64` | `int` |
  | 24 | `creation_limit_per_user` | `i64` | `int` |
  | 25 | `redemption_limit_per_user` | `i64` | `int` |
  | 26 | `net_creation_limit` | `i64` | `int` |
  | 27 | `net_redemption_limit` | `i64` | `int` |
  | 28 | `net_creation_limit_per_user` | `i64` | `int` |
  | 29 | `net_redemption_limit_per_user` | `i64` | `int` |
  | 30 | `all_cash_flag` | `char` | `str` |
  | 31 | `all_cash_amount` | `a[12]` | `str` |
  | 32 | `all_cash_premium_rate` | `a[7]` | `str` |
  | 33 | `all_cash_discount_rate` | `a[7]` | `str` |
  | 34 | `rtgs_flag` | `char` | `str` |
  | 35 | `reserved` | `a[30]` | `str` |

  槽 36 是 `ConstituentStockInfo[]`；外层 `MDETFCodeTableRecord` 有 `std::vector`，故不是固定
  ctypes ABI。其 13 个字段的 PDF/HDR、官方 Python 和本地 parser 顺序一致：

  | 槽 | 字段 | PDF/HDR | 官方 Python / 本地 |
  |---:|---|---|---|
  | 1 | `security_code` | `a[32]` | `str` |
  | 2 | `market_type` | `u8` | `int` |
  | 3 | `underlying_symbol` | `a[128]` | `str` |
  | 4 | `component_share` | `i64` | `int` |
  | 5 | `substitute_flag` | `char` | `str` |
  | 6 | `premium_ratio` | `i64` | `int` |
  | 7 | `discount_ratio` | `i64` | `int` |
  | 8 | `creation_cash_substitute` | `i64` | `int` |
  | 9 | `redemption_cash_substitute` | `i64` | `int` |
  | 10 | `substitution_cash_amount` | `i64` | `int` |
  | 11 | `underlying_security_id` | `a[4]` | `str` |
  | 12 | `buy_or_sell_to_open` | `char` | `str` |
  | 13 | `reserved` | `a[30]` | `str` |

- Header delta: PDF 的通用 `kNone`/全市场文字来自代码表语境；V1.0.8 `tgw.h` 对
  `QueryETFInfo` 明确仅沪深有效，因此本范围不放行 `0`、NEEQ 或其它市场。各数值缩放仅取自
  HDR 注释，API 保持官方 wrapper 的原始整数，不在此接口做缩放。

- Linux oracle: 2026-08-29，先后检查 `galaxy-relay` 都为 `inactive`。以 bj 上官方 x86
  `tgw 1.0.9.2` 和受保护配置中的密码运行**一条**同步请求；用户名仅经安全 stdin 的一次性覆盖
  注入，没有出现在参数、日志或文件。登录成功、错误码类型 `int`/数值 `0`；结果为一条 pair，基础
  记录 35 键、成分记录 **301 × 13 键**。基础和成分列的类型集合与上表相符（仅 `int/str`）。
  脱敏不变量：`market_type=[102]`、交易日为 8 位、存在正的申赎单位和非负 NAV、无重复证券代码、
  非空发布类标志只在允许集合中。未打印或保存任何业务字段值。

- Wire: 同一条 Linux 请求用短生命周期 `SSL_read/SSL_write` interposer 取证；原始 capture 只在
  bj 的专用临时目录存在，分析后立即删除。观测到常驻 push WSS 路径
  `/amd/dgw/push`，请求 `ReqGetETFCodeTableList`，`params` 仅 `Security:str`；安全分析器只保留
  `code|market` 格式和 **market=102**，不保留代码。完成消息为无 `params` 的
  `ReqGetCodelistComplete`。响应 `status=0`、字符串 tag=`"111"`，响应 headers 包含
  `id/code_num/tag` 而**没有** `pack_num/all_pack_num`；`data` 为一个对象记录的数组，记录槽
  `1..35` 加槽 36 成分数组，成分数组为 301 个 `1..13` 对象。安全摘要仅保留键、类型、长度、tag
  和方法；未保留帐号、token、MAC、业务值或完整 capture。单次摘要没有持久化 header 序列化顺序，
  因此不把该顺序作为本范围的独立证明。

- Arm: `_protocol.py` 只将已完整验收的 102 加入 `VERIFIED_ETF_INFO_MARKETS`，并拒绝意外的
  packet-counter 分页形状；`_backend.py` 修复异常根因：ETF codelist push 请求不能使用公开的时间
  格式 `GetTaskID`，而应使用独立的低位 wire 序列 `1,2,...`（官方回显关联）。`interface.py` 的
  JSON/DataFrame 容器保持不变，未知市场、多 item 和 async 仍失败。`remote_sdk_oracle.py` 新增仅
  当前进程的安全用户名覆盖与 `--etf-sync-only`，以确保本轮官方查询恰为一条；
  `analyze_ssl_write_capture.py` 只输出 ETF `Security` 的格式和 market token，绝不输出代码段。

- Tests: 合成 fixture 无业务值。新增/覆盖 `SubCodeTableItem` pack/offset/默认值，SZSE builder
  参数和 market 白名单，单帧/多帧 parser，空响应、缺槽/多槽、错误 tag/status、ID 不匹配、未知
  packet counter（缺失或重复计数）和错误容器/成分形状；同时锁定公开同步 tuple/DataFrame、async/
  多 item 拒绝，以及修复后的 codelist wire id 独立于 `GetTaskID`。本接口测试 27/27 通过；
  `python3 -m unittest discover -s tests -v` 为 145/145 通过，
  `python3 -m compileall -q src/python examples tools` 通过。

- Live diff: Linux/Mac 使用不同授权账号、同一市场/代码/同步 JSON 参数，各自低频单次成功。两端
  返回码均为 0；基础记录数 `1`、基础列 `35`、成分记录数 `301`、成分列 `13`，列集合、列顺序和
  `int/str` 类型一致；两端不变量一致。Linux 官方进程在 `finally` 中 `Close()`；Mac smoke 的
  `finally` 也调用 `Close()`。首次 Mac 尝试曾超时，根因是错误地把公共 TaskID 用作 codelist
  wire id；修复后重跑成功，未改变服务端或帐号状态。

- Cleanup: bj 临时目录中的 oracle 副本、interposer、`.so`、capture 和分析脚本均已逐项删除并
  确认目录不存在；`galaxy-relay` 前后均为 `inactive`。本地仅删除本任务生成的 6 张 PDF 渲染 PNG，
  未触碰已有的其它 `tmp/pdfs` 内容；未创建本地凭据或 capture 文件。

- Proposed status: `LIVE_ALIGNED(QueryETFInfo SZSE 159919 single ETF, synchronous JSON only)`。

- Open risks: 多 item、异步 SPI、空/非零 status、其它 SZSE ETF、多响应帧和任意 packet-counter
  分页未验；单帧 codelist 请求与订阅共用 push 生命周期，断线/重连/恢复、长时资源和流控尚未验收，
  故不拟议 `PILOT_READY`。
