# 原生 IBKR TWS 行情桥

该 sidecar 用 IBKR 官方 C++ API 建立一条只读行情会话，再通过本机 Unix socket 把同一份
快照扇出给多个估值 uploader。首版只替换“连 TWS、订阅、收行情、重连、健康上报”；
PCF、期货换月、汇率锚点、交易日历、估值公式和 upload acknowledgement 仍由现有 Python
负责。这样能先消除重复连接与重复订阅，又不会一次重写高风险业务公式。

## 线程与故障边界

- Qt 主线程：本机协议、定时心跳、健康文件和重连状态机；
- IBKR reader 线程：`EReader::processMsgs()` 与 `EWrapper` 回调；
- `QuoteBook`：互斥保护的内存快照，回调线程不直接操作 socket/UI；
- 独立进程：bridge 崩溃或 TWS 断线不阻塞 Hub Agent、Qt UI、Go 网站、Webull 或实时申赎；
- 慢客户端超过 2 MiB 待写数据即断开，不反压行情线程。

bridge 没有调用任何订单、账户或持仓接口。配置只接受 `127.0.0.1/localhost`，socket 目录、
socket 和健康文件均为 owner-only 权限。

## 构建

IBKR SDK 受其许可约束，不复制进本工程。SDK 新版本的生成代码必须配套同一代 protobuf；
不要让 Homebrew 的另一个 protobuf 版本抢先进入 include path。

```bash
cmake -S app -B build-native \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTING=ON \
  -DMACHOME_BUILD_NATIVE_IBKR_BRIDGE=ON \
  -DMACHOME_IBKR_CPP_API_ROOT=/path/to/official/twsapi/client \
  -DMACHOME_IBKR_PROTOBUF_ROOT=/path/to/matching/protobuf/prefix \
  -DMACHOME_IBKR_ABSL_ROOT=/path/to/matching/abseil/prefix
cmake --build build-native --parallel 6
```

本机已验证的外部依赖组合：

- IBKR：`/Users/ellis/QMTAPI/CPP/third_party/ibkr/twsapi/client`
- protobuf 6.33.4：`/Users/ellis/QMTUI/CPP/third_party/protobuf/33.4_1`
- Abseil：`/Users/ellis/QMTUI/CPP/third_party/abseil/20260107.1`

配置验证不会连接 TWS 或监听 socket：

```bash
./build-native/machome-ibkr-bridge --validate-config \
  --config config/ibkr-native-bridge.example.json
```

Release 打包使用 `scripts/build-release.sh --with-native-ibkr` 和上述三个路径选项；脚本会让
`macdeployqt` 扫描 bridge，把第三方动态库收进 app，然后做未解析依赖检查和配置验证。

## 运行与部署

示例配置见 `config/ibkr-native-bridge.example.json`，严格模式定义见
`contracts/ibkr-native-bridge-config.schema.json`，消费者协议见
`contracts/ibkr-quote-v1.md`。

安装 app 后，先只预演独立 LaunchAgent：

```bash
bash app/scripts/install-ibkr-bridge.sh
```

显式 `--apply` 只安装配置/plist，不改变进程；再加 `--load-bridge` 才加载 bridge。
该脚本从不停止或改写旧 Python uploader。正式启动前须先给 TWS/IB Gateway 启用 socket
客户端、确认端口、为 bridge 分配不冲突的 client ID，并保持 TWS 的 Read-Only API 设置。

bridge 生成 `source=ibkr_native_bridge` 的原子健康 JSON。只要 `health_file` 指向 Upload
模块正在扫描的 health 目录，现有四合一 Upload 二级页会自动出现该 worker，无需改 UI。

生产机若通过内网连接另一台 TWS/IB Gateway，`host` 只接受 RFC1918 IPv4 字面量，且
必须逐项精确列入 `tws.allowed_private_hosts`。默认配置仍只允许
`127.0.0.1/localhost`，不接受公网地址、主机名或隐式远程连接。

## 当前切换边界

本工程已实现并本机验证 native 数据面，但没有修改 `/Users/ellis/newnavnav` 内的 Python
消费者。因此当前版本可以在 machome 做 canary/shadow，不能直接宣布 uploader 已切流。
正式切流必须在维护窗口为每个 Python `IBQuoteStream/INDAMarket/NQMarket/XOPMarketHub`
增加 v1 socket consumer，并保留环境变量控制的 direct-TWS 回退，完成逐组双读比对后再关掉
该组的旧 TWS 会话。
