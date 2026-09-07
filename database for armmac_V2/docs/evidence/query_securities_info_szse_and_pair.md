# `QuerySecuritiesInfo`：SZSE 单项及 SSE+SZSE 双项同步对齐证据

- Scope: 仅互联网模式的同步 `QuerySecuritiesInfo`，新增两种精确输入：
  1. `SubCodeTableItem{market=102(SZSE), security_code="159919"}`；
  2. 输入顺序固定的
     `[SubCodeTableItem{101, "510300"}, SubCodeTableItem{102, "159919"}]`。
  保留既有 SSE `510300` 单项范围；不支持全市场/空代码、NEEQ、任意其它 SZSE 代码、
  其它双项顺序或组合、三项以上、异步 `query_spi`、错误/空结果和多响应帧。
- Public/static contract: Linux 官方函数签名是
  `(req_security_info_cfg, query_spi=None, return_df_format=True)`；Mac 的公开参数名已
  更正为相同拼写。C++ V1.0.8 `SubCodeTableItem` 仍是 pack(1) 的 signed
  `int32 market + char[32] security_code`（sizeof=36），结果为 43 字段
  `MDCodeTableRecord`（sizeof=555）。价格/数量保留官方整数原值，不在本接口缩放。

## Linux 官方 oracle 与脱敏 wire

- Linux x86 官方 `tgw` 以一次性非回显 stdin 的授权账号登录，密码仅来自远端受保护
  配置。脚本没有写出或保存证券、名称、价格、token、MAC、端点或原始响应。
  两次同步调用均为 `error_code=0`：SZSE 单项为 1 行，双项为 2 行；每行均为 dict，
  固定 43 个按 ABI 顺序的字段，值类型仅 `int`/`str`，日期位数集合 `[1, 8]`，
  `variety_category` 集合 `[2]`。
- 双项的**公开输入市场顺序**是 `[101, 102]`，官方返回记录的
  `market_type` 顺序为 **`[102, 101]`**。这是服务端返回顺序；Mac 不排序也不按请求
  顺序重写，必须保留该顺序。SZSE 单项返回市场序列 `[102]`。
- 一次 SSL write/read capture 只在远端临时文件中解码为安全 shape 后删除。捕获证明：
  常驻 `/amd/dgw/push` 连接上，SZSE 单项以独立低位 codelist id `1` 写入一条
  `ReqGetCodeTableList`；双项以 id `2` 写入**一条**同名请求。请求 headers 顺序仍为
  `id,userName,token`，params 只有字符串 `Security`；双项为逗号分隔的两个
  `code|market` 段，段内市场顺序 `[101,102]`。安全摘要没有保留任一代码字符串。
- 两个响应均为单个 ZSTD push 帧：`status=0`、字符串 tag `"109"`、id 回显、没有
  `pack_num/all_pack_num`；单项 `code_num=1` 且 `data` 长度 1，双项
  `code_num=2` 且 `data` 长度 2。每个记录是槽位 `"1".."43"` 的对象，槽位类型符合
  结构定义。官方客户端每个请求均发送无 `params` 的
  `ReqGetCodelistComplete`；未观察 completion ack、分页或第二数据帧，故实现不猜测它们。

## Mac 实现与同参结果

- `_protocol.build_secinfo_request` 现在只接受单项 `(101,"510300")` 或
  `(102,"159919")`，以及严格按 `(101,"510300"),(102,"159919")` 排列的唯一双项；
  后者编码为一个逗号分隔的 `Security` 字符串。其它市场、代码、批量内容或顺序显式
  `NotImplementedError`，空/超长代码继续显式失败。
- `interface.QuerySecuritiesInfo` 只允许 1 或 2 个公开 item，保留输入顺序交给
  backend；response parser 不重排，并新增严格的 `code_num:int == len(data)` 校验，
  防止服务端不完整帧被当成成功结果。
- Mac 使用另一授权账号、相同两种输入并在两次查询之间低频冷却。登录成功，两个查询
  都为 `error_code=0`；SZSE 单项 1×43、双项 2×43，字段顺序/类型/日期及类别不变量与
  Linux 一致，双项同样返回 `market_type=[102,101]`。只输出形状和布尔不变量。

## Files, tests, cleanup and status

- Changed: `src/python/tgw_macos/_protocol.py`、
  `src/python/tgw_macos/interface.py`、
  `tools/oracle/analyze_ssl_write_capture.py`；added
  `tools/oracle/remote_query_securities_info_items_oracle.py`、
  `tools/oracle/macos_query_securities_info_items_oracle.py`、
  `tests/test_ssl_write_capture_sanitizer.py`，并扩展
  `tests/test_securities_info_protocol.py`。
- Tests cover exact single/pair wire encoding、未观测批量/次序拒绝、独立低位 id、
  43 槽位/类型、双项服务器顺序保留、`code_num` 错型/不匹配拒绝、公开签名及安全
  capture redaction。所有 fixture 均为合成记录；不包含返回业务值。
- Cleanup: 官方 Linux 每次 `tgw.Close()` 成功；远端 oracle、interposer source/.so、
  analyzer 与原始 capture 已逐路径删除，`galaxy-relay` 最终为 `inactive`。没有产生
  wheel、没有写中央 API 状态/PDF 矩阵，也没有保留凭据或 capture。
- Proposed status: **`LIVE_ALIGNED(QuerySecuritiesInfo: SSE 510300 single; SZSE
  159919 single; ordered [SSE 510300, SZSE 159919] sync pair only)`**。不外推到
  任意代码/市场、空代码全市场、NEEQ、异步、错误/空响应、分页/多帧或自动重连。
