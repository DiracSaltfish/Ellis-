# macOS ARM64 重写版：开发状态、风险与接手说明

更新时间：2026-08-26；当前实现：`1.0.9.2.macos.re5`。

## 1. 来源与性质

厂商没有发布 macOS arm64 wheel。当前实现依据用户授权可访问的数据库账号与 SDK 资料，
从以下证据重建：

1. TGW/AmazingData 开发手册；
2. V1.0.8 Linux/Windows 公开 C++ 头文件与官方 Python wrapper 行为；
3. Linux x86_64 官方 SDK 的最小只读 oracle；
4. 经脱敏的 TLS/WebSocket 请求方法、key/type、枚举转换、tag、分页和压缩形状；
5. Windows PDB、字符串表、Python bytecode 与反编译结果，用于解释控制流和结构差异。

这不是把 x86_64 机器码自动翻译成 arm64，也不是重新链接官方 `.so/.dll`。真正可联网主线
是重新编写的 Python TLS/WebSocket 协议客户端，在 Apple Silicon 上使用原生 Python、
OpenSSL 与 libzstd。它只能声称对已逐项取证的行为兼容，不能声称是官方 SDK 或完整等价物。

## 2. 新工程的权威边界

今后的 Mac 开发只在：

```text
/Users/ellis/工具程序开发/database for armmac
```

旧 `/Users/ellis/工具程序开发/数据库桥接` 保留为历史来源，不再写入新代码、文档、构建
产物、临时抓包或凭据。新目录已经排除旧工程约 383 MiB 的反编译缓存、PDB、x86/Windows
二进制、`.pyc`、原始 session/capture 和真实账号配置。

目录职责：

```text
src/python/tgw_macos/                 当前可运行 Mac 主线
examples/                             用户示例
tests/                                合成协议/ABI 回归
tools/                                Mac 脱敏烟测
tools/oracle/                         Linux 官方 SDK 对照与安全分析工具
docs/MACOS_SDK_USAGE.md               使用者文档
docs/API_STATUS.md                    逐接口状态
docs/evidence/                        逐接口脱敏证据
docs/internal/                        历史逆向/协议定位笔记（非状态权威）
native/experimental/                  原生 C++ 骨架源码
runtime/arm64/experimental/           原生骨架二进制
experimental/amazingdata_compat/      未验高层兼容源码
reference/                            手册与 V1.0.8 公开头文件
certs/                                可审计的厂商公开 CA 副本
```

状态权威顺序：`API_STATUS.md` → 对应 `docs/evidence` → 本文 → `docs/internal`。内部历史笔记
中曾出现“静态完成 100%”等表述，只表示当时的字符串/结构定位，不代表动态 API 完成度。

## 3. 当前实现架构

```text
public ctypes/API
  Cfg / SubscribeItem / SubCodeTableItem / ReqKline / ReqDefault
  Login / Query* / Subscribe / ReceiveRawEvent / Close
                         │
                         ▼
LiveBackend：生命周期、query endpoint、完成/关闭
                         │
                         ▼
TgwWssClient：TLS + WebSocket + request correlation
  push reader/heartbeat │ one-shot query clients
                         ▼
envelope builders + parsers + ZSTD/full/delta raw delivery
```

- 默认 backend 是 `live-wss`；模拟 backend 只有显式 `TGW_BACKEND=sim` 时启用。
- 公共订阅值与 wire 值分层，只允许已证明的 `10→14`、`12→16`。
- push request id 从 1,000,000 开始，query task id 单独递增。
- query parser 校验 status/tag/包号，再解析业务行；未知分支明确失败。
- CA 与主机名验证仍开启；兼容性放宽仅在专用 TLS context。

## 4. 已完成并有动态证据的范围

### 4.1 会话

- internet TLS/WebSocket `/amd/dgw/push`；
- `ReqLogon/OnRspLogon` 真实服务端鉴权和 token；
- heartbeat、WebSocket ping/pong、分片帧与正常 close；
- 基础 `Close`，backend 内存中的密码副本清空。

### 4.2 查询

- ThirdInfo 交易日历：`function_id=A010061003`；
- 日/周/月 K 线：SSE `510300`，`10008→period_type/tag 10100`、`10009→10101`、`10010→10102`；
- ETF 成分：SSE `510300` 单 item 同步查询；Linux/Mac 均返回 1 条 35 字段基础信息与
  300 条 × 13 字段成分，wire 走常驻 push 的 `ReqGetETFCodeTableList`/tag `"111"`；
- 历史快照：SZSE `159518`、`data_type=0`、`level_type=0` 的 57-key 低层结果已完成
  Linux/Mac 同参数据对齐，但公开错误与异步合约未通过验收，仍为实验接口。

### 4.3 订阅

- SZSE `159518` L1：公开 flag 10 → wire/tag 14；Mac 60 秒 19 条，full 2、delta 17；
- HKT `02800.SH`：市场 101、公开 flag 12 → wire/tag 16；Mac 30 秒 6 条，full 1、delta 5；
- 支持普通 JSON、ZSTD、`0x59 + ZSTD` 解压；
- 提供 `ReceiveRawEvent`，但只交付数字 key 的 raw full/delta。

### 4.4 静态/测试

- pack(1) 大小：`ColocaCfg=22`、`Cfg=145`、`LogonResponse=14`、
  `SubscribeItem=42`、`SubCodeTableItem=36`、`ReqKline=71`、`ReqDefault=55`；
- `ReqDefault.level_type` 的发行包差异已锁定；
- `QueryCodeTable` 已完成 PDF、V1.0.8 HDR 和官方 Python wrapper 的静态契约核对，但尚未
  暴露为可调用 Mac API；`QueryETFInfo` 在静态核对之上已完成 SSE 单 ETF 在线闭环；
- envelope、枚举转换、ZSTD fixture、多包排序/缺包/重复包/错 tag/status、CSV 形状均有
  合成测试；
- 新工程统一套件当前为 50 项，2026-08-26 在本机 50/50 通过；
- 真实账号、token、MAC 和原始价格不在 fixture 中。

证据位于 `docs/evidence/`。任何范围扩展必须新建或更新对应证据文件，不能只改状态表。

## 5. 二进制与“原生服务”的真实状态

`runtime/arm64/experimental/lib/libtgw_core.dylib` 和 `bin/tgw_demo` 确实是 Mach-O arm64，
但它们只有 TCP probe 和本地生命周期骨架。C++ `Handshake()` 会做本地状态迁移，未发送
TGW 登录协议；`Subscribe/QueryKline` 也只记录日志。它们不能证明服务端鉴权、不能返回
行情，禁止用于生产。

当前真实服务能力在纯 Python wheel 中。若未来要获得完整 C++ 原生服务，必须把已验证的
TLS/WSS、request correlation、ZSTD、query lifecycle 和 parser 逐层迁移到 C++，并再次
做 Linux/Mac 同参验证；不能因为 dylib 可加载就切换 backend。

## 6. 生产阻塞项与已知问题

### P0：使用前必须理解

1. **无自动重连与订阅恢复。** reader 退出后不会重新登录/订阅；业务应退出进程并由
   supervisor 指数退避重启，重启后等待新 full。
2. **推送未类型化。** ETF/HKT `data` 仍是数字 key；字段映射、价格/数量缩放和多标的
   身份隔离未完成。
3. **delta 未由 SDK 合并。** 使用者必须从 full 建立状态；丢包或重连后旧状态无效。
4. **事件队列可能丢最旧消息。** 容量 10,000，消费过慢时会主动腾位；没有 gap sequence
   或持久化保证。
5. **全局单例无法可靠重登。** `Close()` 后应重启解释器，正式 Session API 尚未实现。
6. **TLS 服务端陈旧。** 当前兼容 TLSv1–1.2/低 security-level cipher；虽仍验证 CA 与
   主机名，但这是服务端升级前不可消除的风险。
7. **持续性证据太短。** ETF 最长 60 秒、HKT 45/30 秒，没有小时级、交易日级、断网、
   睡眠唤醒、带宽压力或资源泄漏验证。

### P1：公开合约差异

1. 官方类型化 push SPI (`OnMDSnapshot/OnMDHKTSnapshot`) 未实现；传 `push_spi` 明确报错。
2. 官方异步 query SPI 未实现；传 `query_spi` 明确报错。
3. 查询非零 status（尤其 `kDataEmpty=-76`）目前转为异常，未对齐官方同步错误码返回。
4. `GetErrorMsg` 只认识 0；日志 SPI、`FreeMemory`、回调数据所有权不完整。
5. `GetTaskID` 只是单例整数递增，没有锁和并发唯一性测试。
6. 服务端曾对 query WSS 返回 `1000 / accept conn active close`；准入、频率、IP/账号并发
   规则未知，不能密集重试。

### P2：覆盖率

- QueryKline 只有日线、周线与月线三个独立子范围；
- QueryETFInfo 只有 SSE `510300` 单 item 同步子范围；SZSE、多 item、空结果/错误、多帧与
  异步 SPI 未验证；
- QuerySnapshot 只有一个 SZSE ETF 样本；
- ThirdInfo 只有日历 function id；
- HKT 只有 `.SH` 路由；
- 其它订阅 flag、指数、期权、期货、代码表、因子、财务、回放等均未动态对齐；
- coloc/QTCP/RTCP 不在当前范围；
- `experimental/amazingdata_compat` 中 `BaseData` 等高层 wrapper 尚未验收，故不随 wheel 安装。

## 7. 后续修复优先级

推荐顺序：

1. 引入显式 `Session` 对象，支持关闭后新建会话，并对 task/request id 加锁；
2. 实现连接状态事件、指数退避重连、订阅清单恢复和“必须等 full”状态机；
3. 为 tag 14/16 建立完整数字 key → 官方结构映射和缩放测试；
4. 在 SDK 内按 `(market, code, tag)` 合并 delta，增加 gap/overflow 指标；
5. 对齐 query 空结果/非零错误码，并决定实现真异步 SPI 或固定只提供同步 API；
6. 做 1 小时、半日、完整交易日 soak，监测 RSS、线程、队列、最后事件间隔和重连；
7. 再按一个接口/一个枚举粒度扩展 QueryCodeTable、QueryKline 季/年/分钟周期、
   QueryETFInfo 的 SZSE/多 item 分支、HKT `.SZ` 等；
8. 最后才把 Python 已验协议迁移到 C++ 原生层。

## 8. Agent 接手工作流

新 Agent 必须先读根目录 `AGENTS.md` 和 `docs/AGENT_PARITY_WORKFLOW.md`。一个任务只领取
`API_STATUS.md` 的一个明确子范围，并按下列顺序：

1. 从 `reference/manuals` 找完整 PDF 页，从 `reference/vendor-headers/v1.0.8` 核对
   pack/type/default/适用模式；
2. 在 `ssh bj` 上确认 `galaxy-relay` 初始状态；只有工作流允许时运行一次官方 Linux SDK
   最小只读 oracle，凭据只从远端受保护配置读取；
3. 如需 wire 取证，只保留 path/method/key/type/enum/tag/pack/data shape，随后删除原始捕获；
4. 只实现已证明分支，未知输入 `NotImplementedError`；
5. 加结构、envelope、parser、错误形状、多包和返回容器测试；
6. 用两个授权账号做 Linux/Mac 同参单次验证，避免同账号互踢；
7. 写 `docs/evidence/<接口>.md`，包含命令类别、shape、测试、清理、拟议状态和开放风险；
8. 确认远端服务恢复初始状态，删除临时脚本、interposer、`.so` 和 capture；
9. 交给验收者复跑。执行 Agent 不自行扩大中央状态。

验收最低命令：

```bash
python -m unittest discover -s tests -v
python -m compileall -q src/python examples tools
python -m build --wheel
unzip -l dist/*.whl
file runtime/arm64/experimental/lib/libtgw_core.dylib \
     runtime/arm64/experimental/bin/tgw_demo
```

验收者还必须检查 wheel 包含 CA、没有配置/凭据/抓包、未验证输入明确失败，并抽查本次新增
证据是否真的是 Linux/Mac 同参。

## 9. 文档追加规则

后续每验证一个新方法或子范围：

- 在 `docs/evidence/` 新增独立证据，不把多个不相关接口混在一份报告；
- 更新 `API_STATUS.md` 的**限定范围**；
- 在 `MACOS_SDK_USAGE.md` 增加可运行示例、结构、返回字段、回调和清理逻辑；
- 在本文的已知问题/优先级中关闭或新增条目；
- 记录版本与日期，但不写账号、endpoint、token、MAC、价格、原始返回或完整 capture；
- 没有资源、重连、持续运行验收时，最多标 `LIVE_ALIGNED`，不能标 `PILOT_READY`。

这样后续 Agent 能从证据而不是从“某次似乎成功”的口头结论继续修复。
