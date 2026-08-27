# QueryKline 日线对齐证据

- Scope: 互联网模式、SSE 单代码、`cyc_type=10008` 日线、单日、`return_df_format=False`。不覆盖分钟/周/月线。
- PDF: C++ 手册 PDF 页 33–34（正文 25–26），请求 `ReqKline`，响应 `MDKLine`。
- Header delta: V1.0.8 `tgw_struct.h` 与 PDF 字段一致；pack(1) 本地 `ReqKline` 大小为 71。`auto_complete` 官方构造默认 1。
- Linux oracle: 2026-08-26 使用 Linux x86 官方 SDK 完成只读请求；登录成功、查询错误码为整数 0、返回 list 长度 1；首项为 11 个公开字段，类型为 10 个 int + 1 个 str。未输出字段值。
- Wire: query 路径为 `/amd/dgw/dgw1_query`/`dgw2_query`；method=`ReqGetKline`。公共日线 `cyc_type=10008` 转为 `period_type=10100`；响应 tag=`10100`，含 `pack_num/all_pack_num`；`data` 为字符串数组，每行 9 个 CSV 字段；完成后发送 `ReqGetComplete`。
- Wrapper semantics: CSV 解析后输出 `market_type,security_code,orig_time,kline_time,open_price,high_price,low_price,close_price,volume_trade,value_trade,variety_category`；官方 wrapper 对本样本补 `orig_time=0`、`variety_category=0`。
- Arm: envelope/parser 位于 `_protocol.py`，查询生命周期位于 `_backend.py`，公开 wrapper 位于 `interface.py`。未验证周期显式抛 `NotImplementedError`。
- Tests: 2026-08-26 运行 `python -m unittest discover -s reverse-macos/tests -v`，共 14 项：13 项通过，1 项因运行时无 zstd 模块跳过；日线 envelope/响应和 pack(1) 契约均通过。
- Live diff: 既有同日验证证明 Arm 返回相同 11 字段。此次复验中，默认账号、备用 query 路径，以及用户授权的独立 Mac 账号均在成功登录后收到 `1000 / accept conn active close`；说明当前是服务端/IP/查询通道准入，不支持据此判定 parser 回归，也没有扩大已验证范围。
- Cleanup: 官方进程已 `Close()`；远端临时目录已删除；`galaxy-relay` 前后均为 inactive；未保留原始捕获。
- Proposed status: `LIVE_ALIGNED(daily only)`，但生产试点仍受查询通道主动回收风险约束。
- Open risks: 非日线映射未知；查询连接的限频/并发规则未知；自动退避和 admission 可观测性未完成。
