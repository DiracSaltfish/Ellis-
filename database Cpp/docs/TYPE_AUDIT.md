# 真实数据格式与类型审计

审计日期：2026-08-30（Asia/Shanghai）  
审计目标：消除 Python 自动类型转换在 C++ 迁移中造成的位宽、缺失值、精度和未命名
字段丢失风险。结果来自公开 v1.0.8 头文件、原 Python parser 和当前 Mac 上的真实服务器
同参拉取；没有把账号、密码、token、MAC、主机或原始 payload 写入本项目。

## 1. 线上的实际编码不是一种格式

| 接口 | `data` 的 JSON 类型 | 内层格式 | 本次行数 |
|---|---|---|---:|
| 交易日历 | `string` | 字符串内嵌 JSON，`body.data[]` 为 object | 18 |
| 日 K 线 | `array<string>` | 每行严格 9 槽 CSV | 1 |
| 历史 L1 快照 | `array<string>` | 每行严格 36 槽 CSV，4 槽再用 `|` 包 10 个整数 | 11 |
| 证券信息 | `array<object>` | object 的 key 为字符串 `"1".."43"` | 2 |
| ETF 基本信息 | `array<object>` | key `"1".."36"`；第 36 槽是成分数组 | 1 |
| ETF 成分 | `array<object>` | 每项 key `"1".."13"` | 300 |
| 复权因子 | `array<string>` | 每行严格 5 槽 CSV | 33 |

因此不能用一个“JSON 自动转对象”策略覆盖所有接口。每个 parser 都先校验外层 JSON
类型，再校验二级分隔协议、字段数、字段语法与 C++ 目标范围。

## 2. 公开类型到 C++ 类型

| 数据 | 字段组 | C++ 类型 | 解析规则 |
|---|---|---|---|
| K 线 | `market_type` | `uint8_t` | CSV 整数且在 0..255 |
| K 线 | 时间、OHLC、量、额 | `int64_t` | 完整十进制 token，不接受尾随字符 |
| K 线 | `orig_time`,`variety_category` | `optional<int64_t>`,`optional<uint8_t>` | 九槽 wire 不存在，返回 `nullopt` |
| 快照 | `market_type` | `uint8_t` | 范围校验 |
| 快照 | 时间、价量、笔数、IOPV、涨跌停 | `int64_t` | 标量或 10 元素 packed array |
| 快照 | `variety_category` | `optional<uint8_t>` | wire 不存在，返回 `nullopt` |
| 快照 | 位置 20..35 | `array<string,16>` | 未知语义，原样保留 |
| ETF/成分 | 市场 | `uint8_t` | JSON 必须是 integer，再做范围校验 |
| ETF/成分 | 金额、数量、日期 | `int64_t` | 匹配公开头文件 |
| ETF/成分 | 单字符字段 | `char` | wire 为 0..255 integer；0 为 `\0` |
| ETF/成分 | 字符数组 | `string` | JSON 必须是 string |
| 证券信息 | 市场、品种 | `uint8_t` | JSON integer + 范围校验 |
| 证券信息 | 日期、交割年月、持仓类型 | `uint32_t` | JSON integer + 范围校验 |
| 证券信息 | 价格、数量、份额、金额 | `int64_t` | JSON integer |
| 证券信息 | 代码、名称、状态等 | `string` | JSON string |
| 复权因子 | `ex_date` | `uint32_t` | 非负且不超过 `UINT32_MAX` |
| 复权因子 | 因子 | `double` + 原文 `string` | finite binary64，同时保存准确小数 token |
| ThirdInfo | 未知动态字段 | 已验证 JSON object 文本 | 不猜测、不强转 |

ETF 和证券信息的高层返回已改为 `EtfBasicRow`、`EtfConstituentRow` 和
`SecuritiesInfoRow` 静态结构体；字段错误会在 parser 边界失败，不会在业务代码中才以
`bad_variant_access` 或截断值暴露。

## 3. 本次真实数值范围摘要

| 字段/字段组 | 本次观测范围 |
|---|---:|
| K 线 `value_trade` | 343,598,927,060,000 |
| K 线 `volume_trade` | 74,525,680,000 |
| 快照 `orig_time` | 20,260,825,093,000,000 .. 20,260,825,093,030,000 |
| 快照十档买价 | 4,593,000 .. 4,608,000 |
| 快照十档买量 | 80,000 .. 187,190,000 |
| 快照 `total_value_trade` | 1,736,798,340,000 .. 4,712,829,650,000 |
| 证券 `outstanding_share` | 0 .. 628,491,667,600 |
| ETF `nav_per_cu` | 4,226,657,200,000 |
| ETF 成分 `substitution_cash_amount` | 0 .. 17,322,400,000 |
| 复权日期 | 19910403 .. 20260612 |
| `ex_factor` binary64 | 0.97909807287713047 .. 2 |
| `cum_factor` binary64 | 1 .. 85.329578999999995 |

这些值证明 JavaScript 安全整数或 32 位有符号整数不足以承载若干字段；库和审计输出
不会经由 `double` 中转整数。审计 JSON 中的整数最小/最大值使用字符串输出，避免查看
工具再次丢精度。

## 4. 快照未知尾部的实际发现

Python 主线只解析位置 0..19，并丢弃 20..35。本次 11 行真实结果显示：

- 位置 20：11/11 为数字文本，范围 2,099,530,000 .. 3,425,050,000；
- 位置 21：11/11 为数字文本，范围 3,559,970,000 .. 3,802,740,000；
- 位置 22：11/11 为数字文本，范围 4,539,000 .. 4,559,000；
- 位置 23：11/11 为数字文本，范围 4,719,000 .. 4,728,000；
- 位置 24..28：11/11 为空；
- 位置 29..34：11/11 都是一位数字文本；
- 位置 35：11/11 为空。

目前没有足够证据为这些槽命名或决定有符号性/缩放规则。C++ 因此将 16 槽全部保存为
`unverified_tail_raw`，而不是沿用 Python 的静默丢弃，也不把“看起来像整数”当成正式
schema。未来只有取得公开定义和独立动态证据后才能升级成命名字段。

## 5. Python 对照结论

原 Python 实现的在线输出将 `uint8_t`、`uint32_t`、`int64_t` 全部报告为 Python
`int`。同参日 K 线和证券信息实际复拉确认了这一点；ETF 同参也成功返回 1+300 行。
此外源码和同日 C++ 实包共同确认以下转换差异：

- Python 为 K 线 `orig_time`/`variety_category` 和快照 `variety_category` 制造 0；
- Python 丢弃快照 20..35 的真实 wire 数据；
- Python 将 ETF 单字符整数转为空字符串或一字符字符串；
- Python `float` 只保留 binary64 结果，无法恢复复权因子的原始十进制 token；
- Python 的任意精度 `int` 不会在 `uint8_t`/`uint32_t` 边界自动报错。

C++ 的处理分别是 `optional`、原文数组、`char`、`double+raw string` 和显式范围检查。
原 Python 在线对照的部分重复请求曾收到服务器正常关闭
`1000 / accept conn active close`；这是同日间歇性服务器行为，不被当成类型差异。

## 6. 重现和验收规则

```bash
./build/tgw_type_audit --config /secure/path/galaxy_account.ini
```

工具只输出 schema、C++ 类型、计数、范围、空值数和最大字符串字节数，不输出字符串
内容或原始包。所有接口成功时退出码为 0；任一接口失败时仍输出其它可审计结果，最终以
退出码 4 结束。判断规则：

1. 固定字段只能出现声明的单一 C++ 类型；
2. integer 必须完整解析且落入目标类型范围；
3. 字段数、slot key、packed array 长度和公开字符数组字节容量必须完全匹配；
4. wire 缺失字段保持 `optional`，不得填哨兵 0；
5. 未知字段保留原文，取得双重证据前不得命名或缩放；
6. 新证券品种、市场或服务版本上线后必须重跑审计，样本范围不是永久上限。

全市场代码表不在本次“成功真实行”矩阵中：服务端仍存在已知缺包，C++ 会补拉后明确
超时，不会拿不完整数据生成类型结论。其六字段 wire 形状仍有离线 fixture 和历史证据，
但必须等服务端返回完整包后才能加入在线类型验收。
