# macOS arm64 原生实现状态

## 当前可用范围

- 严格校验的 TLS + WebSocket v13 连接，路径 `/amd/dgw/push`
- 标准客户端 masking、PING/PONG 心跳和分片消息接收
- `ReqLogon` / `OnRspLogon` 服务端真实鉴权
- `ReqSubscribeBatch` / `ReqUnSubscribeBatch`
- 公开 `SubscribeDataType.kSnapshot=10` 到 internet wire flag/tag `14` 的实测转换
- 独立查询 WebSocket（`/amd/dgw/dgw1_query`、`dgw2_query`）
- `SetThirdInfoParam` / `ReqGetThirdInfo` / `ReqGetComplete`，含 ZSTD 响应和多包重组
- `QueryThirdInfo(..., return_df_format=False)` JSON 行结果；安装 pandas 后支持 DataFrame
- `QueryKline`：已完成官方 Linux SDK 与 Mac 实测对照的日线（`cyc_type=10008`），返回 11 个官方字段
- 服务端普通 JSON，以及 `0x59 + ZSTD frame + JSON` 推送解码
- `Cfg`、`SubscribeItem`、`ReqKline`、`ReqDefault` 等公开结构的 pack(1) ABI 布局

2026-08-26 对 `159518` （SZSE）L1 快照做了持续对照：Linux 官方 SDK 30 秒收到 10 条；Mac arm64 60 秒收到 19 条，2 条全量、17 条增量，中位间隔 2.981 秒、最大间隔 6.043 秒。

默认后端是 `live-wss`。模拟后端必须显式设置：

```bash
export TGW_BACKEND=sim
```

## 尚未完成

- QueryKline 的非日线周期；QuerySnapshot 的线上请求 envelope/响应解码
- 数字字段键到全部官方行情结构的映射
- L1 `is_delta=1` 增量包与最近全量包的状态合并（当前向调用方交付原始 JSON）
- 除 L1 快照以外的公开订阅枚举到 internet wire tag 映射
- SPI 回调对象、异步查询和 `FreeMemory` 兼容层
- 自动重连、订阅恢复、流控和完整错误码
- coloc QTCP/RTCP 模式

未完成接口会明确抛出 `NotImplementedError`，不会再返回伪造成功或空结果。

## 生产可用性结论

当前可用于受控生产试点的范围是：互联网模式登录、订阅/取消订阅、原始推送 JSON、ThirdInfo 交易日历与日 K 线。它还不是官方 SDK 的完整替代；如业务依赖快照历史查询、非日线、完整 SPI 类型化回调或无人值守自动重连，不应直接切全量。

建议先以单进程、小订阅集、明确查询频率与日志监控的方式灰度，保留 Linux 官方 SDK 作为对照/回退路径。

## 配置

- `TGW_CA_FILE`：厂商 CA 文件路径
- `TGW_TLS_SERVER_NAME`：证书校验所需的服务名（仅当连接地址是 IP 且证书不含该 IP 时）
- `TGW_CLIENT_VERSION`：兼容协议版本字符串
- `TGW_MAC_ADDRESS`：逗号分隔的小写 MAC；默认使用本机主接口地址
- `TGW_TIMEOUT_SEC` / `TGW_HEARTBEAT_SEC`
- `TGW_QUERY_ENDPOINTS`：逗号分隔的查询路径；默认在 `dgw1_query/dgw2_query` 间轮转

凭据不得放入命令行参数、捕获 fixture 或版本库。

## TLS 兼容性风险

2026-08-26 实测厂商端只协商 `TLSv1 / ECDHE-RSA-AES256-SHA`，证书名为
`www.dgw.com`；随 SDK 提供的 CA 未满足 OpenSSL 3 对 Basic Constraints critical
标志的严格要求。实现仅在 TGW 专用 `SSLContext` 内降低 cipher security level 并关闭
`VERIFY_X509_STRICT`，CA 链验证和 `www.dgw.com` 主机名验证仍然开启。TLS 1.0 已过时，
在厂商升级服务端之前应将其视为不可消除的传输层生产风险。

## 查询通道准入风险

2026-08-26 的一次复验中，Linux 官方 SDK 日线查询成功后，Mac 使用默认账号、备用 query 路径以及另一个独立授权账号，均能完成主连接登录，但 query WSS 随后被服务端以 `1000 / accept conn active close` 正常关闭。独立账号没有消除此现象，因此应按 IP/服务端查询通道准入或回收处理：停止密集重试、记录端点和关闭原因，并在获得新窗口后低频复验。不能把该关闭帧当成解析失败，也不能在没有本轮同参结果时宣称新的周期已对齐。
