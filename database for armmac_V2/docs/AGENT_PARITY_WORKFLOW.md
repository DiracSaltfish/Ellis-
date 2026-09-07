# Linux x86 官方 SDK 与 macOS arm64 对齐工作流

## 1. 目标与边界

本工作流用于把 TGW/AmazingData 的一个公开接口，从开发手册、V1.0.8 发行包头文件、Linux x86 官方 SDK 实际行为和线上协议，逐项对齐到 `tgw_macos`。

每个任务只处理一个接口或一个非常小的同族接口组。不得把“登录成功”“同步调用返回 0”或“能收到任意一帧”当成接口已对齐。只有请求参数、协议控制字段、响应分包、字段类型、错误语义和生命周期都通过同参对照，才可进入 `LIVE_ALIGNED`。

当前主范围是互联网模式。手册明确标为“仅托管机房”的接口先登记为 `OUT_OF_SCOPE_COLOC`，不得用互联网模式的猜测实现冒充。

## 2. 状态定义

| 状态 | 含义 | 进入条件 |
|---|---|---|
| `INVENTORIED` | 已登记 | 找到 PDF 页码、公开接口和相关结构 |
| `STATIC_MATCHED` | 静态契约已核对 | PDF、V1.0.8 头文件、Python 官方对象三方字段已比对 |
| `LINUX_OBSERVED` | Linux 行为已观测 | 官方 SDK 用最小只读请求成功；只保存形状和控制信息 |
| `WIRE_VERIFIED` | 线上协议已证明 | 已确认请求方法、字段名、枚举转换、响应 tag、分页/完成语义 |
| `ARM_IMPLEMENTED` | Arm 已实现 | 未验证分支显式失败；已添加协议和结构单测 |
| `LIVE_ALIGNED` | 同参线上对齐 | Linux 与 Arm 的返回行数、列集合、类型、不变量和错误语义一致 |
| `PILOT_READY` | 可受控试点 | 另通过超时、关闭、重连/恢复、资源清理和持续观测验收 |

状态必须逐级提升。某个枚举值或周期只验证了一个取值时，状态要写成子范围，例如 `LIVE_ALIGNED(daily only)`，不能把整个函数标绿。

## 3. 证据优先级

发生冲突时按以下顺序处理，并在证据报告中保留冲突：

1. V1.0.8 实际发行包的头文件/官方 Python 对象：确定当前发布 ABI 和可设置字段。
2. PDF：确定公开语义、取值、单位、适用模式和回调契约。
3. Linux x86 官方 SDK 的实际返回：确定 wrapper 默认值、返回容器和错误语义。
4. 经脱敏的 TLS/WSS 明文形状摘要：确定 internet wire 方法、字段、tag、分页和压缩。
5. 反汇编/PDB：用于解释遗漏和定位转换逻辑，不单独作为线上协议通过依据。

已发现的典型冲突：C++ 手册的 `ReqDefault` 表格只列到 `data_type`，V1.0.8 Linux/Windows 头文件还包含 `uint16_t level_type`，默认值为 0。本地结构必须跟发行包 ABI，而不是只照 PDF 抄写。

## 4. Agent 单接口作业流程

### 4.1 建任务卡

先从 `PDF_API_PARITY_MATRIX.md` 领取一行，并写清：

- 接口和本次只验证的枚举/周期/市场；
- PDF 文件页码和正文页码；
- request、callback、output 三类相关结构；
- 互联网/托管模式边界；
- 最小只读样本：单代码、单日或很窄时间窗、有限订阅时长；
- 要修改的源文件与测试文件。

一个 Agent 不得顺手扩展到未分配的接口。不同 Agent 不应同时改 `_protocol.py` 的同一函数；无法隔离时，先各自提交证据报告，由验收者串行合并实现。

### 4.2 静态契约表

逐字段建立下表，不能只列字段名：

| 字段 | PDF 类型/语义 | V1.0.8 头文件 | 官方 Python 可写性/默认值 | 本地类型/offset | 结论 |
|---|---|---|---|---|---|
| 示例 | `uint16_t` | 存在，默认 0 | 可写 | `c_uint16` | 一致 |

必须记录：整数位宽、有无符号、字符数组长度、`#pragma pack(1)`、默认值、时间格式、价格/金额缩放、市场和品种枚举。对 ctypes 结构增加 `sizeof` 与关键 offset 测试。

### 4.3 Linux 官方 SDK 最小请求

先做远端只读检查：

```bash
ssh -o BatchMode=yes bj 'systemctl is-active galaxy-relay 2>/dev/null || true'
```

- 服务若为 `active`，不要再启动独立官方 SDK 会话；记录并等待验收者安排。
- 服务若为 `inactive`，保持它为 inactive，只运行一次独立 oracle。
- 使用 `/opt/galaxy-relay/venv/bin/python`，以 `galaxyrelay` 用户运行。
- 凭据只从现有 `/etc/galaxy-relay/relay.env` 和服务配置读取，禁止出现在命令行、代码、日志、fixture 或提交中。
- 若用户明确提供两个授权账号，可让 Linux oracle 与 Mac oracle 分别使用独立账号，避免同账号并发会话互踢；账号覆盖必须通过 stdin/受保护环境注入，不得持久化到仓库。`tools/live_smoke.py --username-stdin` 只在当前进程内覆盖用户名，密码仍从权限为 0600 的本地配置读取。
- oracle 只能打印：登录布尔值、返回码类型/数值、行数、列名、Python 类型、不含业务值的不变量、回调计数和间隔。

现有安全 oracle：

- `tools/oracle/remote_sdk_oracle.py`：ThirdInfo 日历与日 K 线；新查询应在这里增加独立 `--kind`。
- `tools/oracle/remote_push_oracle.py`：`159518` ETF L1 或 `02800.SH` HKT L1，只输出回调类型、计数和间隔。

每次查询结束必须调用官方 `Close()`；每次订阅必须先 `UnSubscribe()` 再 `Close()`。远端临时目录、捕获和动态库在取回摘要后立即清理。

### 4.4 协议取证

官方返回容器相同，不代表 wire 相同。使用 `tools/oracle/ssl_write_interpose.c` 对官方进程的 `SSL_write/SSL_read` 做一次最小捕获，再用 `tools/oracle/analyze_ssl_write_capture.py` 生成脱敏形状摘要。

只允许保存和提交下列信息：

- WSS 路径；
- 请求 `method`；
- JSON key 顺序与类型；
- 公共枚举到 wire 值的转换；
- request id 起点和关联方式；
- 响应 `tag/status/pack_num/all_pack_num`；
- `data` 是对象、数组、字符串数组或嵌套 JSON；
- CSV 字段数及类型序列；
- ZSTD 标记、全量/增量标志、完成/关闭语义。

不得保存或提交 username、password、token、MAC、原始证券价格、原始响应行或完整捕获文件。分析器输出若暴露值，先修分析器的脱敏逻辑，再继续任务。

一个标准查询必须验证完整时序：

```text
push 连接登录 → 独立 query WSS 登录/鉴权 → ReqGet* → 1..N 响应包
→ 校验 tag/status/pack_num/all_pack_num → ReqGetComplete → 双端正常关闭
```

### 4.5 Arm 实现

实现时遵守：

- 公共枚举和 internet wire 枚举分层，使用“只包含已证明取值”的显式映射；禁止默认假定两者相等。
- envelope 构造器与响应 parser 分开，均为可单测的纯函数。
- 响应先校验状态、tag、包号完整性和重复包，再解析业务字段。
- 未取证的周期、data type、tag 或回调明确抛 `NotImplementedError`，不能返回空成功。
- 查询 task id 与订阅 request id 分开管理；不能因恰好可用而共用序列。
- 推送处理要区分 `0x59 + ZSTD`、全量和 delta。未实现状态合并时，应向上层明确交付 raw/delta 语义。
- 保留 CA 链和主机名校验；TLS 兼容设置只能作用于 TGW 专用 context。

### 4.6 单元与差分测试

每个接口至少提交：

1. 结构大小、offset、默认值测试；
2. 请求 JSON key、顺序、类型、枚举转换测试；
3. 单包响应解析测试；
4. 多包乱序重组、缺包、重复包、错误 status/tag 测试；
5. 返回容器、列集合、字段类型和公开默认字段测试；
6. 一次 Linux 与 Arm 同参数 live 结果摘要。

严禁把生产 token 或原始行情做 fixture。协议 fixture 使用合成值，并保持与真实捕获相同的形状。

### 4.7 线上同参验收

Linux 与 Arm 必须使用完全相同的代码、市场、日期、时间窗、复权、周期和返回格式。比对：

- 返回码与异常类别；
- 行数和包数；
- 列集合和顺序（若官方保证顺序）；
- 每列 Python 类型；
- 时间/缩放/默认字段不变量；
- 订阅回调次数、错误数、tag、全量/增量计数、中位/最大间隔；
- 正常取消、完成和关闭。

`1000 / accept conn active close` 视为服务端主动回收/流控证据，不是 parser 失败。此时停止密集重试；最多对备用 query 端点做一次低频尝试，仍失败则保留 `WIRE_VERIFIED` 或 `ARM_IMPLEMENTED` 状态，不得提升到 `LIVE_ALIGNED`。

### 4.8 清理与远端状态恢复

任务结束必须确认：

```bash
ssh -o BatchMode=yes bj 'systemctl is-active galaxy-relay 2>/dev/null || true'
```

如果任务开始时服务为 inactive，结束时也必须为 inactive。删除远端 oracle 副本、`.so`、捕获和本地 `tmp/pdfs`/捕获临时文件。不要停止任务开始前已经运行的用户服务。

## 5. Agent 交付格式

每个 Agent 新建 `docs/evidence/<接口名小写>.md`，内容固定为：

```markdown
# <接口> 对齐证据

- Scope: 本次验证到哪个枚举/周期/市场
- PDF: 文件、PDF 页、正文页、request/callback/output 结构
- Header delta: PDF 与 V1.0.8 头文件差异
- Linux oracle: 命令类别、日期、返回码、shape、invariants（无业务值）
- Wire: path/method/request keys/enum mapping/response tag/paging/data shape
- Arm: 修改文件和实现边界
- Tests: 命令与通过数
- Live diff: Linux/Arm 同参与差异
- Cleanup: 远端临时文件已删；服务前后状态
- Proposed status: 只能从本工作流状态表选择
- Open risks: 未验证分支、流控、TLS、资源生命周期
```

Agent 最终回复只报告：实现范围、证据文件、测试结果、拟议状态、未通过项。Agent 不直接把中央矩阵标为通过；由验收者复跑关键测试、抽查捕获形状和清理状态后更新。

## 6. 验收者清单

- [ ] PDF 页、结构、模式边界准确。
- [ ] V1.0.8 头文件差异没有被 PDF 覆盖掉。
- [ ] Linux oracle 无敏感/业务原值。
- [ ] method、字段名、wire enum、tag 和分页来自实际捕获。
- [ ] 本地只实现已证明的分支，未知分支明确失败。
- [ ] 合成 fixture 与协议形状一致。
- [ ] Linux/Arm 同参，而不是“各自成功”。
- [ ] 查询完成、取消订阅、关闭和临时文件清理完整。
- [ ] 远端服务恢复到任务开始状态。
- [ ] 中央矩阵的状态粒度没有扩大。

## 7. 已完成的示范闭环

### 7.1 `QueryKline`，日线、周线与月线三个独立子范围

- PDF/头文件：`ReqKline` 为 pack(1)，当前本地 `sizeof=71`。
- 公共值 `cyc_type=10008` 在官方 Linux SDK 中被转换为 wire `period_type=10100`，响应 tag 同为 `10100`。
- 公共值 `cyc_type=10009`（周线）独立取证为 wire `period_type=10101`，响应 tag 同为 `10101`。
- 公共值 `cyc_type=10010`（月线）独立取证为 wire `period_type=10102`，响应 tag 同为 `10102`。
- wire method 为 `ReqGetKline`；响应是带包号控制字段的字符串数组，每行 9 个 CSV 字段。
- 官方 Python wrapper 返回 11 字段 dict，额外补 `orig_time=0`、`variety_category=0`。
- 2026-08-26 重新运行 Linux x86 官方 SDK：登录成功、错误码 0、返回 1 行且 11 字段类型与既有契约一致。
- 日线保留历史同参证据；周线与月线于 2026-08-26 由 Linux 官方 SDK 与 Mac 分别完成同参
  查询，均返回错误码 0、1 行、11 列（10 个 `int` + 1 个 `str`），控制字段与关闭语义一致。
- Arm 实现只放行 10008/10009/10010，其余周期抛 `NotImplementedError`；parser 按请求周期校验响应 tag。

结论：`LIVE_ALIGNED(daily + weekly + monthly only)`；分钟、季/年周期仍为 pending。

### 7.2 `Subscribe`，仅 ETF L1 `159518`

- PDF/头文件：`SubscribeItem={uint8 market,uint64 flag,char[32] security_code,uint8 category_type}`，pack(1) 后 `sizeof=42`。
- 公共 `SubscribeDataType.kSnapshot=10` 不是 wire 值；官方实际转换为 `subscribeDataType=14`，推送 tag 为 `14`。
- 首个订阅 request id 为 `1,000,000`；请求 method 为 `ReqSubscribeBatch`，参数按数组传递。
- 推送包含 `0x59 + ZSTD + JSON`，同时存在全量与 delta。
- 同日持续观测：Linux 官方 SDK 30 秒 10 条、错误 0；Mac arm64 60 秒 19 条，其中全量 2、delta 17，中位间隔 2.981 秒、最大间隔 6.043 秒。

结论：原始 L1 推送为 `LIVE_ALIGNED(ETF snapshot raw full/delta)`；类型化 `Snapshot` 回调和 delta 状态合并仍未完成，不能标 `PILOT_READY`。
