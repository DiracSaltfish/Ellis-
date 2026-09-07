# Webull Qt 6 客户端

`WebullClient` 是四合一运行中心对现有 Python `webull-lv2-gateway` 的异步 Qt 6 客户端。默认只读 v2 行情 API；只有明确配置了本机 `controlBaseUrl` 且网关由本目录的 `webull_control_runner.py` 接管后，才会启用白名单控制。客户端和 runner 都不读取 Chrome Cookie或 Webull 登录凭据，不提供交易接口。

## 依赖和接入

- C++17 或更高版本；当前四合一工程可继续使用 C++20。
- Qt 6.5+：`Core`、`Network`、`WebSockets`。
- CMake 必须启用 `AUTOMOC`，因为 `WebullClient.h` 含 `Q_OBJECT`。

根工程接入时可使用：

```cmake
find_package(Qt6 6.5 REQUIRED COMPONENTS Core Network WebSockets)

target_sources(machome-hub PRIVATE
    app/modules/webull/WebullClient.cpp
    app/modules/webull/WebullClient.h
)
target_link_libraries(machome-hub PRIVATE
    Qt6::Core
    Qt6::Network
    Qt6::WebSockets
)
```

本目录未修改根 CMake；由总工程集成时再加入上述声明。

## 最小用法

```cpp
#include "app/modules/webull/WebullClient.h"

Machome::Webull::WebullClientConfig config;
config.apiBaseUrl = QUrl(QStringLiteral("http://127.0.0.1:18765/v2"));
config.controlBaseUrl = QUrl(QStringLiteral("http://127.0.0.1:18766/v1"));
config.symbol = QStringLiteral("XOP");
config.tokenFile = QStringLiteral(
    "/Users/ellis/WebullLV2Gateway/runtime/api.token"
);
config.controlTokenFile = QStringLiteral(
    "/Users/ellis/WebullLV2Gateway/runtime/control.token"
);

auto *client = new WebullClient(config, window);

connect(client, &WebullClient::statusUpdated, window,
        &MainWindow::onWebullStatus);
connect(client, &WebullClient::bookUpdated, window,
        &MainWindow::onWebullBook);
connect(client, &WebullClient::clientsUpdated, window,
        &MainWindow::onWebullClients);
connect(client, &WebullClient::logEntry, window,
        &MainWindow::onWebullLog);
connect(client, &WebullClient::errorOccurred, window,
        &MainWindow::onWebullError);
connect(client, &WebullClient::controlCompleted, window,
        &MainWindow::onWebullControlCompleted);
connect(client, &WebullClient::controlFailed, window,
        &MainWindow::onWebullControlFailed);

client->start();
```

`start()`、`stop()`、`refreshNow()`、`reloadCredentials()`、`setBearerToken()`、`setTokenFile()` 和 `submitControl()` 全部只投递队列消息并立即返回。`stop()` 只停止本客户端，不会停止生产网关。

## 控制桥：为什么需要 runner

现有 `/v2` 合同只有健康、状态、行情和客户端查询，没有控制路由。因此四合一的 `webull_set_mode` / `webull_restart_browser` / `webull_show_login` **不可能隔空控制已运行的旧进程**。本目录提供的最小安全方案是替代入口：

- runner 在独立 Python/PyQt6 进程中按原 `app.py` 的顺序组装 `StateStore` / `BrowserCollector` / `ApiServer` / `GatewayRuntime` / `GatewayWindow`；
- 只 `import` 原包，不修改原项目文件；
- 继续锁定原 `runtime/gateway.lock`，所以旧入口和 runner 不能双开；
- 控制 HTTP 只绑定 literal `127.0.0.1`，所有 GET/POST 都要 Bearer token，拒绝重定向和非回环 URL；
- HTTP worker 线程只解析协议，通过 `QueuedConnection` 把命令投递到 PyQt 主线程，不在 HTTP 线程操作 `QTimer`、runtime 或窗口；
- 不使用 UI 自动化，不向旧进程注入，不把 PyQt `QApplication` 嵌入 C++ Qt 进程。

runner 还会在启动时检查原 `GatewayRuntime` 的方法/模式合同，并要求 `ui.close_to_tray=true`；否则直接失败，避免原窗口关闭后 runtime 已停止、但控制端口仍报健康的僵态。

runner 不是可附加到当前生产进程的 sidecar，而是计划切换后唯一的 Webull 进程所有者。在切换前，`controlBaseUrl` 必须留空，UI 中的变更按钮必须保持禁用或明确返回 `CONTROL_DISABLED`。

### runner 启动方式

```bash
/Users/ellis/Desktop/ETF交割/WebullData/webull_lv2_gateway/.venv/bin/python \
  /Users/ellis/工具程序开发/machome四合一运行/app/modules/webull/webull_control_runner.py \
  --gateway-source /Users/ellis/Desktop/ETF交割/WebullData/webull_lv2_gateway \
  --config /Users/ellis/Desktop/ETF交割/WebullData/webull_lv2_gateway/config.json \
  --control-token-file /Users/ellis/Desktop/ETF交割/WebullData/webull_lv2_gateway/runtime/control.token \
  --control-port 18766
```

C++ adapter 强制数据 API 的 `tokenFile` 与变更控制的 `controlTokenFile` 分离，并拒绝同一路径。控制凭据每次变更前安全读取，不长期复用可能分发给 LAN 订阅者的数据 token。两个文件及父目录必须当前用户所有；文件必须精确 `0600`，父目录不得向 group/other 授权。token 不放入 argv、URL、日志或 signal。

runner 和原 `ApiServer` 都只在进程启动时读取 token，不支持在线热轮换。轮换顺序必须是：在维护窗口 bootout 当前 runner → 原子替换合规 0600 文件 → bootstrap runner → 让 C++ client 自动检测或主动 `reloadCredentials()`。不得在 runner 存活时只替换文件，否则数据 API/控制桥仍持有旧值。

当前最小方案的本机威胁边界需要明确：Bearer 在 plain loopback HTTP 上发送，虽然 C++ 已对 REST/WS 强制 `NoProxy`、runner 只绑 `127.0.0.1`，但 TCP 本身不证明监听进程的身份。因此本版本只允许在“MacHome 上的本地进程全部可信”前提下启用 mutation，并且必须由 supervisor 先启动 runner、确认 `18766` 的 LISTEN PID 正是该 LaunchAgent，再将四合一切到 `control_enabled=true`。如果机器上有不可信本地账号/进程，这是生产控制的阻断条件：必须先升级到带 peer-UID 校验和 `0600` socket 目录的 Unix domain socket/`QLocalSocket`，或者对请求和响应做双向 HMAC 认证。不应用“多试一个端口”规避该问题。

生产 LaunchAgent 的 ProgramArguments/WorkingDirectory/40 秒退出宽限示例在 [com.ellis.webull-lv2-gateway.control.plist.template](/Users/ellis/工具程序开发/machome四合一运行/app/modules/webull/com.ellis.webull-lv2-gateway.control.plist.template)。模板保留现有 label，旨在维护窗口**替换**旧 plist，不可直接作为第二个 job 加载。它使用 `--no-create-control-token`，所以上线前必须已有合规 token；模板仅是审核材料，本项目不会自动复制或 `launchctl bootstrap` 它。

### 控制合同

| 四合一动作 | runner `action` | 调用的原 runtime 方法 |
| --- | --- | --- |
| `webull_set_mode` | `set_schedule_mode` + `{"mode":"auto|force_running|force_stopped"}` | `set_mode(mode)` |
| 采集启动 | `collector_start` | `start_collector_now()` |
| 采集停止 | `collector_stop` | `stop_collector_now()` |
| `webull_show_login` | `open_login` | `open_login_window()` |
| `webull_restart_browser` | `restart_browser` | `restart_browser()` |

`POST /v1/commands` 请求格式：

```json
{"request_id":"webull-command-0001","action":"set_schedule_mode","arguments":{"mode":"force_running"}}
```

`request_id` 必须是 8–128 位白名单 ASCII；runner 保留 5 分钟幂等结果，且永远不淘汰尚未完成的 mutation。相同 ID + 相同 payload 返回同一结果，相同 ID + 不同 payload 返回 HTTP 409。C++ 端不会自动重试可变更命令；超时、断线或响应过大会发出 `controlFailed(..., outcomeUncertain=true)`。`restart_browser` 只在 runner 观测到“非 running → running”的完整过渡后返回成功；四合一端还会使用控制 ACK 后才发出的 v2 status/ready 做第二次核验。`open_login` 在采集器未运行时会切换 `force_running`，因此必须按高风险动作审批；窗口可见性仍需真机人工确认。

### ModuleWorker 最小接线

1. 从 module settings 读取 `control_base_url` 和独立 `control_token_file`到 `WebullClientConfig::controlBaseUrl/controlTokenFile`；
2. 在 `control_enabled=true` 且 `ownership=logic|owner` 时才调用 `submitControl()`；`shadow` 必须禁用；
3. 用 command ID 作 request ID，并保留 request ID 到 pending-command 的映射；
4. `controlCompleted` 只表示 runner 结果可识别；必须在 ACK 后新发出的 v2 status/ready 满足 action-specific 后置条件才结束 pending；`controlFailed` 把稳定错误码和 `outcomeUncertain` 写入审计事件；
5. 独立映射上表中的 UI 动作，不得将 UI 传入的任意字符串当作 runner action。

### 所有者切换条件

只有同时满足以下条件才能把 `com.ellis.webull-lv2-gateway` 的 launch owner 切到 runner：

1. 只读 client 已 shadow 运行至少一个完整交易日，旧 UI 与新 UI 的 session/sequence/hash/status 一致；
2. runner 在离线 config 和 mock runtime 上通过全部命令、超时、幂等、错 token 和主线程测试；
3. 确认 `18766` 空闲、token/parent 权限合规，四合一配置仍只指向 `127.0.0.1`；启动后再确认 LISTEN PID 与 LaunchAgent PID 一致；
4. 在预约维护窗口停止旧 launch owner，再启动 runner；绝不绕过 `gateway.lock` 或复用同一 Chrome profile 双开；
5. 以 `/v2/health/live` 成功判定进程启动，以 ready/status/book/stream 判定业务就绪；
6. 逐个验证模式切换、采集启停、登录窗口和浏览器重启，同时确认 LAN 行情客户端仍能连接原 `18765` 数据 API；
7. 保留可回退的旧 launch 配置；失败时先退出 runner，确认锁释放后再恢复旧入口。

此切换需要明确的运维授权；本次实现和测试没有停止或启动生产进程。

## 线程归属

`WebullClient` 本身是调用方线程中的 facade，通常属于 Qt UI 主线程。构造函数创建名为 `webull-client-network` 的内部 `QThread`；以下对象只在该线程创建、使用和销毁：

- `QNetworkAccessManager` 和全部 `QNetworkReply`；
- `QWebSocket`；
- REST 轮询、超时、重连和新鲜度 `QTimer`；
- JSON 校验、订单簿校验及 0600 token 文件读取。

返回 UI 的对象都是值类型，通过 queued invocation 后再从 facade 发出 signal。UI 不应读取 worker 内部对象，也不应把 `QWebSocket` 移到其他线程。析构函数只在最终释放时等待内部事件线程完成清理；正常启动、刷新、重连、轮询和停止路径均不阻塞 UI。

## 网关合同

客户端使用已核对的 v2 合同：

| 用途 | 请求 | 鉴权 |
| --- | --- | --- |
| 进程存活 | `GET /v2/health/live` | 无 Bearer，但服务端仍检查 CIDR |
| 数据就绪 | `GET /v2/health/ready` | Bearer |
| 完整状态 | `GET /v2/status` | Bearer |
| 标的及 stale 阈值 | `GET /v2/symbols` | Bearer |
| 轮询盘口回退 | `GET /v2/book/XOP` | Bearer |
| WS 客户端列表 | `GET /v2/clients` | Bearer |
| 实时盘口 | `GET ws://host:18765/v2/stream?symbol=XOP` | Upgrade 请求携带 Bearer |

REST base URL 可传 `http://host:port` 或 `http://host:port/v2`。空 `streamUrl` 会根据 REST URL 推导 `ws://`/`wss://` 和 `/v2/stream`。显式 stream URL 的 query 会被替换为唯一的 `symbol` 参数，避免把凭据放入 URL。

WebSocket 必须先收到以下已验证的 `hello` 才接受业务消息：

- `schema_version == 2`；
- `service == "webull-lv2-gateway"`；
- 标的与配置一致；
- `snapshot_semantics == "full_replace"`；
- `aggregation == "price"`。

之后处理 `depth_snapshot` 和 `heartbeat`。heartbeat 只证明链路存活，绝不会延长行情新鲜度。网关约 25 秒无盘口时应发送应用层 heartbeat；默认 60 秒完全静默会判为半开连接、启用 REST 回退并重连。未知消息类型为前向兼容而忽略且不会延长 watchdog；binary frame、hello 缺失或协议不匹配会产生明确错误。

## signals 和字段

- `statusUpdated(GatewayStatus)`：API live/ready、WS/轮询回退、browser/auth/data 三套状态、collector、调度、登录提醒、客户端数、计数器、延迟、last error 和本地 freshness。
- `bookUpdated(BookSnapshot)`：完整 full-replace 盘口、inside market、session/sequence、源时间与网关延迟、来源（`websocket`、`rest-book` 或 `rest-status`）、本地 `fresh/ageMs`。fresh/stale 边界变化时会用相同 sequence/hash 重发值对象，让二级页面不会保留过期的 `fresh=true`；这不代表出现了新行情。
- `clientsUpdated(ClientList)`：client ID、远端地址、连接时间、最后发送时间及消息数。
- `symbolsUpdated(SymbolList)`：symbol、ticker ID、最大深度和服务端 stale 阈值。
- `logEntry(LogEntry)`：本 adapter 的脱敏运行日志，包括重连、回退、序号跳跃、协议与新鲜度事件。
- `errorOccurred(ApiError)`：稳定错误码、端点、HTTP 状态、Qt 网络错误值和 retryable 标志。
- `freshnessChanged(bool, ageMs)`：仅在新鲜/过期边界变化时发出。
- `transportStateChanged(apiLive, streamConnected, pollingFallback)`：仅在传输状态变化时发出；其中 `streamConnected` 只有在 WebSocket Upgrade **且 v2 hello 校验成功**后才为真。

现有网关没有日志查询端点，因此 `logEntry` 是客户端诊断日志，不是假装读取服务端 `gateway.log`。`/v2/status.status.last_error` 发生变化时会转换成 `GATEWAY_LAST_ERROR` 日志。若 UI 要展示完整服务端日志，应由 Hub Agent 另行提供受限、分页、脱敏的日志通道。

## 盘口防护

每个快照在发出前会验证：

- schema/type、XOP 标的、ticker ID、非空 session、正整数 sequence；
- `captured_at`/`published_at` 带明确时区，且发布时间不早于采集时间；
- 时间戳不能超过允许的未来时钟偏差；
- price/volume 是普通十进制字符串，不接受指数、NaN、Inf 或二进制浮点替代；
- 两侧非空且不超过配置档数，level 从 1 连续；
- bids 严格降序、asks 严格升序；
- `book.depth/bid_depth/ask_depth` 与数组一致；
- inside best bid/ask 与一档一致，normal/locked/crossed 与价差方向一致；
- latency 和可选 source interval 有限且非负。

同一 session 的旧序号和重复序号不会覆盖新盘口；相同 `(session, sequence)` 但 hash 不同会报协议错误。序号跳跃可记录后直接应用，因为 v2 是完整快照。REST 与 WS 返回同一序号时自动去重。

`/v2/book/XOP` 在服务端数据 stale 时仍可能返回 HTTP 200。因此客户端采用 fail-closed 判定，`dataFresh` 同时要求：

1. 本机当前 UTC 与 `captured_at` 的差值不超过 `/v2/symbols.stale_after_ms`；
2. 已成功校验 `/v2/symbols` 中当前标的及其 stale 阈值；
3. 已取得 `/v2/status` 或 `/v2/health/ready` 的服务端状态，且 `data` 明确等于 `flowing`；
4. 客户端当前处于 started 状态。

快照接收时同时记录单调时钟基线；后续 age 取 UTC 计算值与单调递增值的较大者，因此系统/NTP 时钟回拨不会把旧盘口重新变“年轻”。

旧盘口仍可用于带 STALE 标识的 UI 展示，但业务逻辑必须以 `fresh == true` 为准。

## 凭据与错误处理

直接 token 通过 `bearerToken`/`setBearerToken()` 传入；生产建议只配置 `tokenFile`。Unix/macOS 上文件必须满足：

- 常规文件且不是 symlink；
- 精确权限 `0600`；
- owner 是当前有效用户；
- 32–1024 个可打印 ASCII 字符。

Unix/macOS 读取使用 `O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK` 打开后再 `fstat`，并在读取前后核对 inode、owner、mode、size、mtime/ctime，避免路径替换、symlink/FIFO 与读取中轮换竞态。直接值优先于文件。值从不进入 signal、URL、错误文本或日志；HTTP 重定向被禁用，防止 Authorization header 被转发。文件的存在性、inode、owner、权限或时间戳变化后会自动安全重载，也可调用 `reloadCredentials()`。重载失败会清空旧凭据、撤销 ready/fresh 状态并断开旧 WebSocket。

常见错误码：

- `AUTH_REJECTED`：HTTP 401 或 WS 握手认证失败；
- `CIDR_REJECTED`：HTTP 403，客户端地址不在网关 `allowed_cidrs`；
- `CONNECTION_REFUSED` / `HOST_NOT_FOUND` / `REQUEST_TIMEOUT`；
- `TLS_ERROR`：严格 TLS 验证失败，不调用 `ignoreSslErrors()`；
- `RESPONSE_TOO_LARGE` / `WEBSOCKET_MESSAGE_TOO_LARGE`；
- `WEBSOCKET_TIMEOUT` / `WEBSOCKET_HELLO_TIMEOUT`：Upgrade 或 v2 hello 超时；
- `WEBSOCKET_SILENCE_TIMEOUT`：hello 后长期没有盘口或应用层 heartbeat；
- `PROTOCOL_ERROR`：JSON 或 v2 字段合同不符；
- `TOKEN_FILE_PERMISSIONS` / `TOKEN_FILE_OWNER` / `TOKEN_INVALID`。

相同错误 30 秒内合并，避免 UI 日志洪泛。瞬时 REST 故障按配置从正常轮询周期指数退避到最大周期；WebSocket 使用带约 10% jitter 的独立指数退避。WS 断开期间 REST status/book 继续工作，`pollingFallback=true`。

## 测试方式

### 编译检查

```bash
clang++ -std=c++20 -fsyntax-only \
  $(pkg-config --cflags Qt6Core Qt6Network Qt6WebSockets) \
  app/modules/webull/WebullClient.cpp
```

完整链接检查必须同时对 `WebullClient.h` 运行 AUTOMOC，并链接：

```text
Qt6::Core Qt6::Network Qt6::WebSockets
```

### 自动化协议测试建议

用本机临时 HTTP/WebSocket fixture server 和 Qt Test 覆盖：

1. live 200、ready 200/503、status/book/clients/symbols 正常响应；
2. 401 token、403 CIDR、404 版本不匹配、500、拒绝连接、超时和 TLS 失败；
3. Content-Length 超限、chunked body 超限、WS 超限/binary/hello 缺失；
4. 正常盘口、空侧、超档、乱序、重复价格、非法十进制、错误 inside market；
5. session 切换、重复 sequence、hash 冲突、sequence gap 和 WS/REST 去重；
6. heartbeat 不延长 freshness、服务端 stale 即使 book=200 仍保持 stale；
7. WS 断开后轮询继续，重连成功后恢复实时流；
8. token 文件 0600 成功，0644、symlink、owner 错误、过短和运行中轮换均失败或安全重载；
9. 连续慢响应及频繁断线时 UI 线程 timer 仍按期触发，证明无阻塞。

runner 的可重复 mock 测试：

```bash
cd /Users/ellis/工具程序开发/machome四合一运行/app/modules/webull
/Users/ellis/Desktop/ETF交割/WebullData/webull_lv2_gateway/.venv/bin/python \
  -m unittest -v test_webull_control_runner.py
```

该测试仅绑定 `127.0.0.1` 随机端口，覆盖未鉴权拒绝、health/capabilities、全部 5 个命令、白名单参数、幂等重放/冲突、0600 token 和“HTTP worker signal 在 PyQt owner 线程执行”。

实现阶段已用仅绑定 `127.0.0.1` 的临时 Qt HTTP/WebSocket fixture 跑通四类实际冒烟路径：完整 REST + 合法 v2 hello/盘口、WebSocket Upgrade 后缺失 hello 的超时/REST 回退/重连、同一盘口从 fresh 跨越 stale 边界后的值对象刷新，以及 C++ `submitControl()` 的成功响应/本地参数拒绝/超时结果不确定。测试未连接或改动生产网关。

### 生产 shadow 验证

只连接 `127.0.0.1`（不要使用可能解析成 `::1` 的 `localhost`，当前服务端 CIDR 未声明 IPv6 loopback），在不操作生产进程的情况下对比：

- 旧 PyQt 面板与 `statusUpdated` 的 browser/auth/data/API/client count；
- 旧盘口与 `bookUpdated` 的 `(session_id, sequence, content_hash)`；
- WS 正常时 source 为 `websocket`，断开后 `rest-book` 继续且 freshness 不被 heartbeat 伪造。

完成一个完整交易日、收盘调度、断网重连和登录过期场景后，再把该 adapter 接到四合一首页与 Webull 二级页面。
