# Upload / 实时申购赎回 Qt6 桥接

本目录是两个不依赖原 Python/Go 工程内部实现的 Qt6 客户端。它们只通过已存在的 HTTP/WebSocket/健康 JSON 边界集成，因此不会改写原工程配置，也不会接管或终止现有进程。

## 构建与线程约束

库需要 C++20 和 Qt 6.5 以上：

```cmake
set(CMAKE_AUTOMOC ON)
target_sources(your_target PRIVATE
    modules/bridge/UploadClient.cpp
    modules/bridge/UploadClient.h
    modules/bridge/RealtimeClient.cpp
    modules/bridge/RealtimeClient.h
)
target_link_libraries(your_target PRIVATE Qt6::Core Qt6::Network Qt6::WebSockets)
```

两个 `QObject` 都应在自己所属的模块事件循环中创建和调用。如果中心 agent 为每个模块分配一条 `QThread`，UI 只用 queued signal/slot 与它们交互。HTTP 和 WebSocket 都是事件驱动；`UploadClient` 的目录扫描则投递到 Qt 全局线程池，不占用模块事件循环。

客户端不输出请求体、响应体或完整 URL，也不存储 token/cookie。向 UI 暴露的错误文本会截断并遮蔽常见密钥形式。

## `UploadClient`

主站默认 origin 为 `http://127.0.0.1:8080`。`start()` 以非阻塞方式定时请求：

```text
GET /api/v1/health
```

当前 NewNavNav 该接口只返回 `{"ok":true}`，因此它只代表 Web liveness，不代表 upload 业务已成功。应通过 `setHealthDirectory()` 配置部署机的 `scripts/.runtime/upload_health/`，聚合其中的 `*.json`。可识别的 schema_version=1 字段为：

```text
source, pid, state, stage, updated_at,
last_success_at, last_failure_at, last_heartbeat_at,
accepted, symbols, detail, last_error
```

扫描最多读 128 个文件，单文件最多 256 KiB，忽略符号链接，并按 `source` 去重。`workerFreshnessMs` 默认 35 秒，与原 upload health monitor 的默认时效一致。`workersExpected` 是调度策略输入：交易日运行窗口内设为 `true`；窗口外设为 `false` 时，站点正常可报告 `ScheduledIdle`，避免把合法的定时休眠误报为故障。

Hub 通过模块设置 `workers_expected` 显式启用 worker 门禁，并在 Upload 页面展示。默认值是 `true`（fail-closed）。启用时必须同时提供合同为 `newnavnav-upload-health-monitor-v1` 的 `worker_monitor_schedule`；其中明确列出时区、工作日、每个 source 的监控窗口。Agent 只对当前当班 source 要求新鲜成功确认，窗口外报告 `ScheduledIdle`。这组窗口逐项复刻原 `upload_health_monitor.py`，不从健康文件是否陈旧反推市场状态。

核心信号：

- `siteStatusChanged`：站点可达性、`ok`、HTTP 状态和延迟。
- `workerStatusesChanged`：每个 source 的最新成功/失败/心跳及时效。
- `aggregateStatusChanged`：`Healthy / ScheduledIdle / Degraded / Offline`，可直接驱动首页卡片。
- `healthScanFinished`：有效、无效文件数及是否触发文件数上限。

为保留原 SPA 二级页面能力，可用下列同源深链方法：

```text
homeUrl()                    /
debugUrl()                   /debug
navSettingsUrl()             /navsettings
fundUrl(symbol)              /funds/{SH|SZxxxxxx}
effectiveRatioHistoryUrl()   /funds/{symbol}/effective-ratio-history
shareHistoryUrl()            /funds/{symbol}/share-history
```

`deepLink()` 仅接受站内绝对路径，不会生成跨站 URL。

## `RealtimeClient`

默认 origin 为 `http://127.0.0.1:6787`，协议版本默认为 `1`。`start()` 启动周期 health 和 WebSocket；`refreshAll()` 另外请求 snapshot/watchlist/PCF 列表。

只读 REST：

```text
GET /api/v1/health
GET /api/v1/snapshot
GET /api/v1/watchlist
GET /api/v1/history?date=YYYY-MM-DD&symbol=159518&limit=500
GET /api/v1/pcf
GET /api/v1/pcf/{6位深圳代码}
```

WebSocket：

```text
WS /ws/v1/changes
client -> {"type":"get_snapshot"}
client -> {"type":"ping"}
server -> snapshot | change | status | heartbeat | pong
```

`snapshot/change/status/heartbeat` 必须带 `protocol:1`。不匹配时发出 `protocolMismatch`，中止该连接并停止自动重连；修改要求版本或显式调用 `connectStream()` 后才再试。其他断线使用 20% 抖动的指数退避，默认从 1 秒增长到 30 秒。服务端正常每 15 秒发心跳；客户端默认 45 秒无任何流消息则判定 `Stale` 并重连。

本机控制 REST：

```text
PUT  /api/v1/watchlist                 {"symbols":["159518"]}
PUT  /api/v1/symbols/{symbol}/name     {"name":"..."}
POST /api/v1/monitor/start              {}
POST /api/v1/monitor/stop               {}
GET  /api/v1/wind/status
POST /api/v1/wind/start                 {}
POST /api/v1/wind/shutdown-cleanup      {}
POST /api/v1/pcf/refresh                {}
```

服务端根据 TCP peer 限制变更操作仅允许 loopback。客户端又做一层预防：只有 base URL 的 host 是 `localhost`/`localhost.` 或数字 loopback IP 时，`controlsAllowed()` 才为真。内网 IP、普通主机名和 `0.0.0.0` 一律只读；客户端不会通过 DNS 猜测主机名是否指向本机。被本地阻止的操作同时发出 `controlRejected` 和 `requestFailed`。

每个 REST 调用返回一个 request ID，通过下列信号完成：

- 通用：`requestStarted / requestFinished / requestFailed`。
- 查询：`healthReceived / snapshotReceived / watchlistReceived / historyReceived / pcfListReceived / pcfDetailReceived / windStatusReceived`。
- 控制：`controlFinished`；如返回 snapshot，同时发出 `snapshotReceived`。
- 实时流：`streamStateChanged / streamEventReceived / changeReceived / statusReceived / heartbeatReceived`。

REST 普通查询默认 5 秒超时；Wind/监控/PCF 刷新等控制以及可能现场补缓存的 PCF detail 默认使用独立的 120 秒超时，可用 `setControlTimeoutMs()` 调整。单响应上限 8 MiB，最多 64 个在途请求，禁止跟随重定向。为避免重复执行有副作用的命令，REST 控制不自动重试；是否在人工确认服务端状态后重试，由上层 lifecycle controller 决定。

`freshnessUpdated` 与 WebSocket 连接状态相互独立。它基于 snapshot/change 每个 item 的 `age_seconds` 和单调时钟继续累加，默认 10 秒以内才算新鲜；任一观察标的缺少时效或过期都会使整体 `fresh=false`。因为份额未变时 WebSocket 不会为此发送 change，客户端在 health 显示 `monitoring=true` 或 `inside_schedule=true` 时，还会默认每 5 秒非阻塞拉取一次 snapshot；时段外不轮询，可用 `setSnapshotPollIntervalMs()` 调整。因此，`health.ok=true` 只能作为服务 liveness，首页业务 ready 应组合：

```text
health ready + protocol compatible + expected schedule + monitoring/wind state + data freshness
```

## 当前端点假设与后续 C++ 替换边界

- NewNavNav health 约定是 HTTP 2xx JSON 对象且 `ok=true`；它不包含 uploader ready 信息。
- uploader 状态文件依据原 `upload_monitor_status.py` 的 schema_version=1，文件由原进程原子替换；本客户端只读。
- 实时服务的 health/snapshot/history 以及版本化 WebSocket 事件均使用整数 `protocol=1`；watchlist 和 PCF 当前未带协议字段。
- 实时服务的 LAN 只读面当前没有 token，不得映射到公网。这个桥接也不接受 URL user-info 或自动携带认证值。
- 将来用原生 C++ 替换 Go/Python 时，保持上述 HTTP/WS/health-file schema 和信号语义即可逐模块切换；UI 不应依赖 Wind TBAPI、PCF 拉取器或 NewNavNav uploader 的内部类。
- 如未来协议变更，先增加新 protocol 版本的兼容解析，再改默认版本；不应在 UI 中静默忽略版本不匹配。
