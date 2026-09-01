# 22｜原生 TGW 数据类型强化审查

## 1. 审查目标、粒度与证据分级

目标不是复刻 Python `dict/int/list` 的表面结果，而是防止 Python 自动转换把错误类型、
超宽整数、前导零或浮点舍入隐藏掉。审查粒度是“一条 TGW JSON push event”，
下游用途是 full/delta 状态合并、定点数计算和 8421/19195 发布。

证据必须分层，不能把仿真量写成实盘量：

- 沪深实盘：`logs/live-validation/tgw-*-events-20260827.jsonl` 四份文件，
  共 6,593 条、203 只证券；它们是本次全量原生词法类型审计基线。
- HKT 实盘：`docs/17_20260827_02800深股通实盘验证.md` 记录的 6 条 `02800`
  事件（2 full / 4 delta）；自动化回归使用其 Full #1 的原始值。
- `data/raw-20260827.jsonl.zst` 经原始包裹提取后统计为 650,295 条旧版确定性仿真。
  其中 646,474 条 named-schema delta 通过当前严格合同，3,821 条旧格式 full 因缺少
  `variety_category`（并非服务端实盘坏包）被明确拒绝；只用于历史变体回归，
  **不计入实盘类型结论**。
- `database Cpp` 的 wire 取证用于验证 public flag 到 wire tag 的映射。

原生测试固定了三类已验证形态：

- tag 14 full：数字键、十档 pipe string、`orig_time=20260827103715638`；
- tag 14 delta：实盘字段子集，只合并出现的键；
- tag 16 full：五位字符串代码、五档 pipe string、
  `orig_time=20260827154936000`、`total_amount=1952183035644000`。

HKT 的 17 位时间大于 IEEE-754 binary64 的精确整数上限 `2^53-1`，不能经过 `double`
再转回整数。接收边界现在使用 simdjson 的 `INT64/UINT64/DOUBLE` 词法类型区分；
只有 `INT64` token 能进入业务映射，`0.0`、`1e3` 即使数学上为整数也会被拒绝。

## 2. 实盘全量审计结果

`etf-premium-tgw-audit` 用与运行时相同的 `RawEventValidator + simdjson`
扫描上述四份沪深原始文件，不通过 Python/Qt JSON 转型：

| 维度 | 结果 |
| --- | ---: |
| 总事件 | 6,593 |
| full / delta | 1,283 / 5,310 |
| 证券数 | 203 |
| tag 14 / tag 16 | 6,593 / 0 |
| numeric / named schema | 6,593 / 0 |
| 严格验证通过 / 失败 | 6,593 / 0 |
| 最大 JSONL 行 | 596 bytes |

字段 profile 进一步确认：`headers.tag` 6,593/6,593 为 string；`status`、
`is_delta`、`data.1`、`data.4` 全部是 `int64` token；`data.2` 全部是 6-byte
string；`data.12–15` 出现时全部是 10 个可解析 int64 的 pipe token，错误行为 0。
`data.4` 范围是 `20260827102845000–20260827105025357`，`data.18` 最大值为
`333515305600000`，均不能进入 32 位或浮点中间通道。

这是对已保存沪深实盘样本的高置信结论，但时间窗只覆盖约
10:28:45–10:50:25；HKT 只有 30 秒/6 帧的既有证据。无法用这些数据声称已覆盖整日、
断线重连或服务端未来 schema drift，所以周一仍需新的原生收包审计。

## 3. 接收边界的实际 JSON 类型合同

| 位置 | 接受类型 | 拒绝示例/原因 |
| --- | --- | --- |
| `headers` / `data` | object | array/null/string |
| `headers.tag` | string `"14"/"16"` | 数字 14、未知 tag |
| `status` | 精确整数 0 | `"0"`、0.0、非零 |
| `is_delta` | 精确整数 0/1 | bool、`"1"`、2、1.5 |
| 数字 schema 键 `2` | string | 数字 2800 会丢失 `02800` 前导零 |
| tag 14 标量键 | JSON `INT64` | 数字字符串、float/exponent、超范围 |
| tag 14 键 12–15 | pipe string，core 再校验十档及每项 int64 | array/混合字符串 |
| tag 16 标量键 | JSON `INT64` | 字符串/小数/越界 |
| tag 16 键 4、12–15 | string，core 再校验五档 | 非字符串 |
| named 仿真标量 | JSON `INT64` | Python 风格数字字符串 |
| named 仿真盘口 | `INT64` array | float/string/mixed array |

数字 schema 还有结构门禁：tag 14 只允许键 1–21，tag 16 只允许键 1–23；
full 必须具备全部已验证键，delta 至少必须带 market/code/orig_time。任何新数据键、
未知嵌套结构或缺失身份/时间都先 quarantine，经新实盘取证和显式修订合同后才能放行。
审计工具只对已知 A-core 顶层持久化包裹做零重序列化提取；运行时 TGW 接收边界仍只接受
直接 push event。

TGW 原始 JSON 在 validator 只读解析后，BridgeFrame 仍保存同一段 bytes；不会像 Python
`json.loads → dict → json.dumps` 一样重新构造对象。这保留了字段存在性、数字词法和后续
审计证据。业务 mapper 统一落到 `QuoteSnapshot` 的 `qint64` 定点字段，不使用浮点价格。

## 4. 字段宽度和业务单位

| 数据 | C++ 存储 | 已见量级 | 规则 |
| --- | --- | --- | --- |
| exchange `orig_time` | `qint64` | 17 位，约 `2.0e16` | 精确整数，不当 Unix ms 直接使用 |
| 价格/IOPV/涨跌停 | `qint64`，E6 | ETF 常见 `3e5–3e6` | 原始整数，UI 才按 1e6 缩放 |
| 盘口量 | `qint64`，E2 | 已见单档超过 `1e10` | pipe token 用 `toLongLong` |
| 总成交量 | `qint64`，E2 | 已见超过 `2.5e11` | 只允许非负 |
| 总成交额 | `qint64`，E5 | 沪深已见 `3.3e14`，HKT 约 `2.0e15` | 不用 32 位，不经 double |
| sequence/bridge epoch | `quint64` | 单连接递增 | 状态帧也占序号 |
| queue depth | `quint32` | 上限 10,000 | 仅监控，不参与行情数学 |
| market | TGW `int32_t`；业务字符串 | 101/102 | HKT 固定走 102 |
| public subscribe flag | `uint64_t` | 10/12 | wire 分别为 14/16 |
| category | `uint8_t` | 0 | 日志显示时需显式扩大，避免当 char |

当前 tag 14 数字位置映射继续标记
`numeric-live-20260827-UNVERIFIED`。这不是类型不安全，而是明确表示“字段位置虽有历史
样本证据，但新的原生收包路径尚未在盘中与 Python 并行对照”。周一完成同证券双路径比对后
才能决定是否升级映射版本，不能因为编译/仿真通过就改成 verified。

## 5. Fail-closed 与合并规则

1. validator 类型/字段集不符：整帧 quarantine，不进入 full/delta 状态。
2. delta-before-full：不发布，等待同 session 新 full。
3. session/bridge 变化：清全部 parser 状态。
4. removed symbol：交付前再次检查 desired set；旧排队事件直接丢弃且不分配 sequence。
5. adapter 队列溢出：断桥并重建 session，不允许缺帧后继续合并。
6. HKT 时间或累计成交量/额倒退：保留最后一个已验证状态，拒绝候选 delta。
7. 必需字段/档数错误：不发布；新字段不自动容忍，先生成 schema-drift 证据。

## 6. 可重复验证

`premium-native-tgw-tests` 覆盖：

- 101/102、flag 10/12、category `uint8_t(0)` 的精确订阅映射；
- 已脱敏 tag 14 实盘 full/delta 的大整数、档位、字段 19=IOPV 合并语义；
- 超过 `2^53` 的 HKT 17 位时间和 15–16 位成交额精确性；
- 原始 bytes 不重序列化；
- A-core 持久化包裹内 `event` 的词法 bytes 精确提取，包裹内 `0.0` 仍会被拒绝；
- 数字字符串、fractional number、bool `is_delta`、数字 tag、数字型 HK code 明确拒绝；
- 未知嵌套键、缺失 delta 时间、不完整 full 明确拒绝。

运行回归：

```bash
ctest --test-dir build/native-macos-arm64-debug --output-on-failure \
  -R 'premium-native-tgw-tests|premium-domain-tests'
```

对新脱敏原始包执行 C++ 全量审计。工具直接读取 JSONL；压缩文件通过标准输入流式解压，
不生成中间明文文件：

```bash
build/native-macos-arm64-debug/etf-premium-tgw-audit \
  logs/live-validation/tgw-*-events-20260827.jsonl

zstd -dc data/raw-20260827.jsonl.zst | \
  build/native-macos-arm64-debug/etf-premium-tgw-audit -
```

输出协议为 `tgw-type-audit/v1` JSON；包含每文件 SHA-256、full/delta/tag/schema/证券数、
字段覆盖率、词法类型、整数值域、pipe 槽位与错误分组。任一无效行返回码 1，
文件不可读返回码 2；工具不输出原始行，避免无意泄露数据。

剩余必须盘中验证的不是 C++ 基本类型，而是当前 TGW 服务端是否仍返回相同字段集合、tag、
full/delta 时序和单位，以及全量订阅下是否出现新的可选字段或容量/节流行为。
