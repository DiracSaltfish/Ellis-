# Subscribe 港股通 02800 L1 对齐证据

- Scope: 互联网模式、港股通标的 `02800`、沪股通路由 `MarketType.kSSE=101`、`SubscribeDataType.kHKTSnapshot=12`、`category_type=0`、原始 full/delta 推送。标的表同时返回 `02800.SH` 与 `02800.SZ`；本轮仅 live 对齐 `.SH`。
- Route: 直接使用 `MarketType.kHKEx=103` 会被官方 SDK 参数检查拒绝为 `Subscribe Market is not support`，不论代码为 `02800`、`2800` 或带 `.HK` 后缀。AmazingData HKT 标的表用 `.SH`/`.SZ` 表示互联互通路由，因此正确请求为市场 101 或 102 + 五位港股代码。
- Linux oracle: 2026-08-26 Linux x86 官方 SDK，`.SH` 路由订阅返回 0；45 秒收到 11 条 `OnMDHKTSnapshot(TGWHKTSnapshot)`，回调错误 0，中位间隔 3.502 秒，最大间隔 9.004 秒。另一次 20 秒抓包样本收到 4 条。
- Wire: method=`ReqSubscribeBatch`，`marketType=[101]`、`categoryType=[0]`、`securityCode=["02800"]`；官方客户端将公开 flag `12` 转换为 `subscribeDataType=[16]`。`OnRspSubscribe` status=0，推送 tag=`"16"`。full 样本有 23 个数字 key，delta 样本仅携带变化 key。未保存价格、数量或其他业务值。
- Permission: Linux 与 Mac 独立账号的登录权限集合一致，均包含 `InternetDataPermission.kHKTSnapshot=6`。
- Arm changes: `VERIFIED_SUBSCRIBE_WIRE_TYPES` 新增 `12:16`。由于当前 Python 运行时无 `zstandard` 且无标准库 zstd，`_decompress_zstd` 新增原生 `libzstd` ctypes 回退，支持帧声明内容长度和 `ZSTD_decompressBound` 未知长度两种情形，并限制解压上限为 64 MiB。
- Tests: `python -m unittest reverse-macos/tests/test_native_protocol.py -v` 共 19 项，19 通过；包含公开 HKT flag `12` → wire/tag `16` 合约和真实 zstd 字节 fixture 解压。`py_compile` 通过。
- Mac live: 使用独立账号、与 Linux 相同 `.SH` 参数，登录和订阅返回成功；30 秒收到 6 条 tag `16`，full 1、delta 5，中位间隔 3.003 秒，最大间隔 6.010 秒。随后已取消订阅并关闭连接。
- Status: `LIVE_ALIGNED(HKT 02800 raw full/delta; SH route)`。
- Open risks: 未验证 `.SZ` live 路由；尚未将数字 key 转换为 `SnapshotHKT` 类型化字段，也未合并 delta 状态；未做断线重连、自动恢复订阅或长时间压力测试，因此不是 `PILOT_READY`。
- Cleanup: Linux 临时 oracle、interposer、原始抓包和 Mac 权限探针均已删除；不保留 token、密码或原始行情。`galaxy-relay` 保持 `inactive`。
