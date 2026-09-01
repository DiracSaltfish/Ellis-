# 21｜原生 C++ TGW 改造设计、接口与周一验收

## 1. 当前结论与边界

本分支把 A 端 TGW 路径从 Python `tgw_adapter.py + tgw_macos wheel` 替换为
`etf-premium-tgw + tgw_cpp` 原生 C++20 实现。A-core 后面的业务逻辑和外部协议没有改：

```text
TGW TCP/TLS/RFC6455/Zstd/JSON
              │
              ▼
etf-premium-tgw（C++20，登录/鉴权/订阅/严格原始类型门禁）
              │ 本机 UDS + uint32 大端长度 + BridgeFrame protobuf
              ▼
A-core（原有 full/delta 合并、信号、留存）
       ├── 8421 WebSocket v2 ── B 端
       └── 19195 TCP NDJSON v1 ── 旧 L1 客户端
```

因此 B 端不需要升级协议或跟随部署。`/Users/ellis/工具程序开发/溢价率拉升监控`
和 machome 正在运行的旧版本不属于本次写入范围；截至本文生成时，没有 SSH、上传、远端
启动、远端测试或进程替换。

## 2. 可执行文件与调用接口

新运行进程：`etf-premium-tgw`。

```text
--socket <path>          A-core 的本机 QLocalSocket 路径
--watchlist <path>       启动默认观察清单；连接 core 后以 core 控制帧为准
--account <path>         live 模式 TGW INI
--username-file <path>   可选用户名覆盖；禁止输出内容
--ca-file <path>         TGW 私有 CA；仍执行 CA、SNI、hostname 校验
--log <path>             脱敏 JSONL 运维日志
--simulate               纯 C++ 确定性仿真，绝不连接 TGW
```

A-console 已改为启动此二进制。`mode=live` 缺少账户文件时不再静默降级成仿真，而是让
配置校验/子进程启动失败，防止生产界面显示“有行情”但实际是假数据。回放工具仍可使用
Python，因为它只读取本地审计文件并写 BridgeFrame，不加载 TGW SDK、不参与生产行情。

辅助审计进程为 `etf-premium-tgw-audit <jsonl...>`。它用运行时同一个
simdjson 精确 token validator 扫描脱敏原始 JSONL，输出 `tgw-type-audit/v1`
JSON 报告；不登录、不订阅、不输出原始包。输入既可以是直接 TGW event，也可以是
A-core 持久化的顶层 `event` 包裹。后一种情况由轻量扫描器定位对象边界并传递原始 byte view，
不会先解析再序列化。参数 `-` 表示从标准输入读取，可与 `zstd -dc` 组合审计压缩归档。

## 3. 内部桥接口（保持兼容）

桥帧继续使用 `BridgeFrame`：

| 字段 | C++ 类型 | 语义 |
| --- | --- | --- |
| `kind` | `quint32 enum` | 1=market，2=status，3=control |
| `sequence` | `quint64` | 单次 core 桥连接严格递增 |
| `session_id` | UTF-8 string | TGW 每次重新登录生成新值 |
| `receive_wall_ns` | `qint64` | 系统时钟纳秒 |
| `receive_monotonic_ns` | `qint64` | 稳态时钟纳秒 |
| `is_delta` | bool | 仅由原始 JSON 的精确整数 0/1 生成 |
| `tag` | string | 只接受 `14` 或 `16` |
| `payload_json` | bytes | 服务端原始 JSON 字节，不重排、不重写数字 |
| `sdk_queue_depth` | `quint32` | 原生 adapter 待交付队列深度 |

core→adapter 控制仍为 `{"op":"set_symbols","symbols":[...],"quotes_desired":bool}`。
adapter 对每个 symbol 先做规范校验，再原子替换 desired set。删除证券后，主线程交付前还会
检查当前 desired set，避免已退订证券的排队旧事件重新污染缓存。

## 4. TGW 鉴权与订阅不变量

底层使用相邻 `database Cpp` 的 `tgw_cpp`：

- TCP → TLS（证书链、SNI、`www.dgw.com` hostname）→ RFC6455；
- `/amd/dgw/push`，客户端版本、MAC、ProcessId、ForceLogout 等与已取证协议一致；
- `[galaxy].force_logout` 与旧 Python 一样按 bool 读取，缺省为 false，非法值拒绝启动；
- 登录仅在 `status=0 + tag=OnRspLogon + 非空 token` 同时成立时成功；
- token 只保留在底层会话，不进入 adapter/core 日志和状态；
- 普通沪深：`market=101/102, public flag=10 → wire tag 14, category=0`；
- 深港通：`02800.HK → code=02800, market=102, public flag=12 → wire tag 16`；
- 普通与 HKT 分组，每批最多 20；批次明确被拒绝时二分到单证券，单证券 1–300 秒退避；
- 每轮最多 64 次 subscribe，防止系统性拒绝形成请求风暴。

## 5. 线程、背压与故障恢复

- Qt 主线程：UDS 连接/控制解码、BridgeFrame 严格排序、日志。
- TGW 工作线程：连接登录、订阅协调、阻塞收包或 C++ 仿真。
- 跨线程事件队列：上限 10,000；主线程每轮最多取 512，避免饿死控制事件。
- UDS 待写上限：16 MiB。

任何 adapter 队列溢出或 UDS 写积压都视为 delta 链已经不可信：断开桥、清空事件、关闭
TGW 会话，core 因 adapter 断开清缓存；重连后重新登录/订阅并等新 full。不会只记一条
日志后继续把 delta 接到缺帧状态。

## 6. 构建与本机运行

依赖：Qt 6.5+、OpenSSL、libzstd、simdjson、CMake 3.24+。默认从
`../database Cpp` 编译 `tgw_cpp`；也可设置 `PREMIUM_TGW_CPP_SOURCE_DIR`，或留空并使用已
安装的 `tgw_cppConfig.cmake`。

`PREMIUM_BUILD_NATIVE_TGW` 在 macOS 默认开启；Windows B 端 preset 显式关闭它，
因此 POSIX TGW/TLS 依赖不会渗透到 Windows 客户端构建。

```bash
cd '/Users/ellis/工具程序开发/溢价率拉升监控cpp'
cmake --preset macos-arm64-debug
cmake --build --preset macos-arm64-debug -j8
ctest --test-dir build/native-macos-arm64-debug --output-on-failure
```

离线仿真（不连 TGW）：先启动 core `--simulation --force-quotes`，再启动
`etf-premium-tgw --simulate`。现有 8421/19195 smoke 工具可只读验证 A→B 合同。

已保存实盘数据的严格类型审计：

```bash
build/native-macos-arm64-debug/etf-premium-tgw-audit \
  logs/live-validation/tgw-*-events-20260827.jsonl

# A-core 的 zstd 压缩 JSONL：先流式解压，不产生中间明文文件
zstd -dc data/raw-20260827.jsonl.zst | \
  build/native-macos-arm64-debug/etf-premium-tgw-audit -
```

## 7. 周一盘中验收门禁（通过前禁止替换 machome）

1. 在本机确认 `mode=live`，检查账户/CA 权限和系统时间；不要开 `force_logout`，除非已确认
   不会踢掉现有生产账号。
2. 先以单证券 `159866.SZ` 连接，记录登录 tag/status、订阅确认、首个 full、后续 delta；
   只保存脱敏事件，不保存 token/账号/密码。
3. 把原生事件与已验收 Python 同证券字段集合、JSON 类型、数值和单位逐项比对。
4. 扩到沪深各一只和 `02800.HK`；验证 tag 14/16、market/flag、前导零、17 位时间、五/十档。
5. 扩到当前完整清单，验证每个证券先 full 后 delta、ready 恢复、无 adapter gap/queue drop、
   无持续 quarantine。
6. 同时从 8421 summary/detail 和 19195 拉取，核对 symbol、价格、档位、IOPV、时间与接收延迟。
7. 做一次可控 unsubscribe/re-subscribe，确认删除后的排队事件不会回填，重订阅重新等 full。
8. 运行至少一个完整高峰窗口，生成类型审计和容量报告；全部通过后才制定 machome 停旧、
   备份、部署、健康检查和一键回滚步骤。

本阶段只完成 1 之前的本机静态/仿真准备；周末无持续行情不作为失败，也不能据此宣布实盘通过。
