# QuerySnapshot 验收记录

日期：2026-08-26  
结论：**CHANGES_REQUESTED**。接受已取证范围的 wire 协议和解码主体，中央矩阵升为 `WIRE_VERIFIED(SZSE ETF L1 sample; Arm CHANGES_REQUESTED)`；暂不接受执行 Agent 拟议的 `LIVE_ALIGNED` 状态。由于工作流定义 `ARM_IMPLEMENTED` 要求未验证分支必须显式失败，当前实现在收口前也不进入该状态。

## 已独立复现

- Linux x86 官方 SDK：互联网模式、SZSE ETF `159518`、20260825、09:30:00–09:30:30、`data_type=0`，登录和查询错误码为 0，返回 11 行。每行官方容器为 57 个有序 key，值类型仅为 `int`/`str`。
- 抓包请求：路径 `/amd/dgw/dgw1_query`，method=`ReqGetSnapshot`；params 顺序为 `security_code,market_type,date,begin_time,end_time,data_type,QueryBandWidth`，已验证分支不传 `level_type`。
- 抓包响应：tag=11000，status=0，单包，11 条 36 字段 CSV；pos10–13 为 10 档管道符数组。实际抓包字节经本地 `parse_snapshot_packets` 解码后为 11 行、57 key，列集合、顺序及标量类型与官方容器一致。
- 本地测试：18 项中 17 通过、1 项因当前 Python 无 zstd 支持跳过；改动文件通过 `py_compile`。
- 验收者的单次 Mac live 复验中，主连接登录成功，query WSS 被服务端以 `1000 / accept conn active close` 回收；按工作流未密集重试。因此执行 Agent 记录的 Mac 11 行成功样本可作证据，但本次验收未能独立再现。

## 必须修改

1. **限制未验证分支。** `build_snapshot_request` 已拒绝 `data_type != 0`，但非零 `level_type` 仍会被静默丢弃，并且对任意市场/品种都发送同一请求、使用同一 `MDSnapshotL1` 解码器。在获得对应 oracle 前，应显式 `NotImplementedError`，不得把未验证输入伪装成已支持。
2. **保留官方错误合约。** `_ordered_query_packets` 把所有非零 status（包括 `kDataEmpty=-76`）转成 `TgwProtocolError`，而官方同步 wrapper 通过 `OnStatus` 返回 `(None, error_code)`。公开 API 应保留可程序化的错误码/空结果语义，而不是改变控制流。
3. **收口 `query_spi` 语义。** 官方 wrapper 的异步模式先返回提交结果，再由 SPI 异步回调；当前 Arm wrapper 先阻塞执行整个查询，然后在返回前同步调用回调。应实现真异步，或在本轮范围内对 `query_spi is not None` 显式拒绝。

## 复验门槛

- 增加上述三类合约测试，现有测试保持通过。
- 使用 Linux 官方 SDK 对照一个空结果/非零 status 样本，比对同步与异步公开返回。
- 在不密集重试的前提下，用分离账号各完成一次 Linux/Mac 同参 live diff。两端错误码、行数、57-key 顺序和类型一致后，才可提交 `LIVE_ALIGNED(SZSE ETF L1 snapshot query, data_type=0, synchronous)`。

## 环境恢复

验收用 Linux 临时脚本、interposer 和抓包已删除；本地协议抓包及 PDF 渲染临时目录已移入废纸篓；`galaxy-relay` 保持 `inactive`。未持久化账号、密码、token 或原始行情值。
