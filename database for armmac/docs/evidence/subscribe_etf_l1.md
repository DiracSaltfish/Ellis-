# Subscribe ETF L1 对齐证据

- Scope: 互联网模式、SZSE ETF `159518`、`SubscribeDataType.kSnapshot`、原始 full/delta 推送。未覆盖类型化 Snapshot 对象或 delta 状态合并。
- PDF: C++ 手册 PDF 页 25–26（正文 17–18）的 `Subscribe/SubscribeItem`；AmazingData 手册 PDF 页 22（正文 18）的 `onSnapshotetf`；输出 `Snapshot` 见 PDF 页 140–141（正文 136–137）。
- Header delta: `SubscribeItem={uint8_t market,uint64_t flag,char security_code[32],uint8_t category_type}`；pack(1) 本地大小 42，与 V1.0.8 头文件一致。
- Linux oracle: 2026-08-26 Linux x86 官方 SDK 持续 30 秒，订阅返回 0、回调错误 0、收到 10 条；中位间隔约 3.017 秒、最大间隔约 3.052 秒。只保存计数/间隔。
- Wire: method=`ReqSubscribeBatch`，参数为 `marketType/categoryType/subscribeDataType/securityCode` 四个数组；首个订阅 request id 为 1,000,000。公共 flag 10 转为 wire 14，推送 tag 也为 14。推送载荷观测到 `0x59 + ZSTD + JSON`，包含 full 与 delta。
- Arm: ETF 分支使用 `VERIFIED_SUBSCRIBE_WIRE_TYPES` 中的 `10:14`；后续新增的 HKT `12:16` 有独立证据文档。request id 序列从 1,000,000 开始。
- Tests: 2026-08-26 全套协议单测共 14 项：13 项通过、1 项跳过；包含 public flag→wire tag 和 request id 起点断言。
- Live diff: Mac arm64 持续 60 秒收到 19 条，full 2、delta 17，中位间隔 2.981 秒、最大间隔 6.043 秒；tag 为 14。Linux/Arm 均无回调错误。
- Cleanup: 已取消订阅并关闭；未保留 token、价格或原始行情捕获；远端服务保持 inactive。
- Proposed status: `LIVE_ALIGNED(ETF snapshot raw full/delta)`。
- Open risks: 尚未把数字 key 全部转换为手册 `Snapshot` 字段；delta 未与最近 full 合并；自动重连、订阅恢复和长时间压力测试未完成，因此不是完整 `PILOT_READY`。
