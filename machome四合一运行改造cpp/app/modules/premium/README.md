# Premium A 原生引擎

生产入口是 engine/PremiumAEngine：CoreServer、四分片计算、
MarketSchedule、SignalEngine、持久化、通知门禁、8421 和 19195 均在
四合一产品内拥有。UI 通过 IModuleEngine 事件直接取数，不回环访问
兼容端口。包内 machome-premium-tgw-helper 是唯一允许的 TGW 进程边界，
由引擎托管，没有独立 UI 或 LaunchAgent。

旧 PremiumClient 仅保留为兼容合同对照测试，不是原生模式运行时入口。
本模块不包含旧工程的 client/console/QMT 交易代码，任何账户、订单、撤单
或交易命令都会被 PremiumAEngine 明确拒绝。

## 接入面

- 汇总通道：`ws://<host>:<summaryPort>/ws/v2/summary`，默认端口 `8421`。
- L1 健康探测：`<host>:<l1Port>` 的 TCP NDJSON v1，默认端口 `19195`。
- Qt 依赖：`Core`、`Network`、`WebSockets`；要求 Qt 6.5 或更新版本。

汇总通道识别 `status`、`summary`、`signal`、`sync_begin`、`sync_complete`、`sync`、`raw_snapshot`、`watchlist_ack`、`l1_hotlist_ack`、`symbol_removed`、`error`。所有合法对象先由 `messageReceived(channel, type, object)` 发出，再由对应的强类型 signal 发出；未知类型仍保留在通用 signal 中，便于协议向前兼容。

支持的汇总命令是：

- `requestStatus()` → `{"op":"status"}`
- `requestSync()` → `{"op":"sync"}`
- `setWatchlist(symbols)` → `{"op":"set_watchlist",...}`
- `setL1Hotlist(symbols)` → `{"op":"set_l1_hotlist",...}`
- `requestRawSnapshot()` → `{"op":"raw_snapshot"}`

现有 A-core 只允许 loopback 客户端修改观察清单和 L1 热清单。如果四合一 UI 不运行在 machome 本机，这两条命令会收到服务端拒绝 ACK；客户端不会绕开该安全限制。

`commandSent` 只表示命令已进入本机 socket 发送队列，不代表业务成功。清单页面必须以 `watchlistAcknowledged` / `l1HotlistAcknowledged` 中服务端返回的 `accepted` 与权威 `symbols` 为准，并在拒绝时回滚 UI。

19195 通道要求首条消息为 `{"v":1,"t":"hello","service":"qmt_l1"}`。握手后客户端立即发送一次 `status` 和 `ping`，随后默认每 10 秒探测状态、每 15 秒 ping，并分别发出 `l1HelloReceived`、`l1StatusReceived`、`l1PongReceived`。请求带唯一 `id`，但不会同步等待响应。

## 线程归属与非阻塞保证

套接字和定时器都是 `PremiumClient` 的 QObject 子对象，随客户端共享线程归属。可以在 `start()` 前将客户端移动到专用 `QThread`；启动后不要再 `moveToThread()`。从其他线程调用 public slot 时，调用会自动排队到 owner thread。销毁请在 owner thread 使用 `deleteLater()`。

所有网络动作均使用 `QWebSocket`、`QTcpSocket` 和 `QTimer` 的事件驱动接口；没有 `waitForConnected`、`waitForReadyRead`、sleep 或轮询阻塞。UI 可让四个模块各自驻留独立工作线程，也可以把本客户端留在 GUI 线程（其 I/O 本身不会阻塞）；高频 `summaryReceived` 最好在 adapter 层按 symbol 合并后再刷新模型。

连接失败采用 500 ms、1 s、2 s……最高 30 s 的独立指数退避。8421 和 19195 互不共享 socket、timer、退避计数或输入缓冲，因此一条链路故障不会阻塞另一条。状态通过 `Stopped / Connecting / Connected / Stale / Backoff` 与 detail 文本报告。

新鲜度使用单调时钟，不受系统时间校准影响。默认 8421 超过 15 秒无消息、19195 超过 35 秒无有效响应会进入 `Stale` 并主动探测；超过 60 秒会断开重连。`freshnessChanged` 提供两条链路的布尔新鲜度及 age。WebSocket 在 Qt 解帧层限制为 4 MiB；L1 单行限制 65,536 字节、累计缓冲限制 256 KiB，发送积压限制 1 MiB。阈值均可通过 `Config` 调整。

## 最小接线

```cpp
#include "modules/premium/PremiumClient.h"

auto *premium = new machome::premium::PremiumClient(this);
connect(premium, &machome::premium::PremiumClient::statusReceived,
        dashboard, &Dashboard::applyPremiumStatus);
connect(premium, &machome::premium::PremiumClient::summaryReceived,
        premiumModel, &PremiumModel::upsertSummary);
connect(premium, &machome::premium::PremiumClient::signalReceived,
        premiumModel, &PremiumModel::appendSignal);
premium->start();
```

构建目标需启用 AUTOMOC，并加入：

```cmake
find_package(Qt6 6.5 REQUIRED COMPONENTS Core Network WebSockets)
target_sources(machome-hub PRIVATE
    app/modules/premium/PremiumClient.cpp
    app/modules/premium/PremiumClient.h)
target_link_libraries(machome-hub PRIVATE Qt6::Core Qt6::Network Qt6::WebSockets)
```

代码使用 `Q_SIGNALS` / `Q_SLOTS` / `Q_EMIT`，可与定义了 `QT_NO_KEYWORDS` 的目标共同编译。

## 测试入口

建议建立一个 Qt Test，使用本机 `QWebSocketServer` 与 `QTcpServer`，端口设为 `0` 后把实际端口写入 `Config`。测试至少覆盖：

1. WebSocket URL 路径严格为 `/ws/v2/summary`，五种命令 JSON 与 symbols 数组正确；
2. `status/summary/signal/sync_begin/sync_complete/raw_snapshot` 的 typed signal 与通用 signal 顺序；
3. 19195 首帧 hello 身份校验、status/ping 的 NDJSON framing、id 唯一、pong/status 分发；
4. 畸形 JSON 连续三次、超长 WS 消息、超长或不换行的 L1 输入会断连且进入退避；
5. 两个测试服务分别中断时，另一通道继续收发；退避达到上限后不继续增长；
6. 小阈值配置下验证 `Connected → Stale → Backoff → Connected` 与 freshness age；
7. 把客户端移入 `QThread` 后从测试主线程调用所有 public slot，确认调用排队且无跨线程 socket 警告。

生产只读冒烟测试可连接 machome 后观察 `statusReceived`、`l1HelloReceived`、`l1StatusReceived` 和 `l1PongReceived`；除非明确要更改业务清单，不要在冒烟测试中调用两个 `set*` 方法。`stop()` 只关闭本客户端连接，不会停止服务端进程。
