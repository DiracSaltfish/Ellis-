# C++ API 参考

公共入口头文件为 `<tgw/client.hpp>`，命名空间为 `tgw`。库不暴露 token，
不要求调用方持有 JSON DOM，也没有 Python/DataFrame 依赖。

## 1. 配置与生命周期

### `Config load_ini_config(const std::string& path)`

读取：

```ini
[galaxy]
host = host1 host2
port = 8600
username = <authorized user>
password = <secret>
api_mode = kInternetMode
```

支持与原主线一致的环境覆盖：

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `TGW_CA_FILE` | 构建树 `certs/vendor-dgw-ca.crt` | 专用 CA 文件 |
| `TGW_TLS_SERVER_NAME` | `www.dgw.com` | SNI 和证书主机名 |
| `TGW_CLIENT_VERSION` | `V4.3.0.260626-rc2.0-YHZQ` | 登录版本字符串 |
| `TGW_MAC_ADDRESS` | 首个 UP 非 loopback 的 6 字节 MAC | 逗号分隔设备地址 |
| `TGW_TIMEOUT_SEC` | `15` | 连接/请求超时 |
| `TGW_HEARTBEAT_SEC` | `5` | push ping 周期；`0` 禁用 |
| `TGW_QUERY_ENDPOINTS` | 两个 `dgw*_query` 路径 | 逗号分隔路径池 |

安装到系统后应显式设置 `TGW_CA_FILE` 为安装后的
`share/tgw_cpp/certs/vendor-dgw-ca.crt`，避免依赖构建树绝对路径。

### `Session`

```cpp
tgw::Session session(config);
session.connect();
tgw::LoginInfo info = session.login();
// 或一次完成：session.connect_and_login()
session.close();
```

`Session` 不可复制、可移动，析构时自动关闭。`close()` 可重复调用，并清零内部
密码和 token 缓冲。服务端鉴权拒绝由 `LoginInfo{authenticated=false,status,tag}`
表示；网络/TLS/协议问题抛异常。

### 异常

- `TransportError`：DNS、TCP、TLS、WebSocket、读线程或连接状态错误；
- `TimeoutError`：请求或 push 等待超时；
- `ProtocolError`：服务端包与已验证 wire 形状不一致；
- `std::invalid_argument`：调用参数超出本地已验证范围。

## 2. 查询接口

### K 线

```cpp
std::vector<KlineRow> Session::query_kline(const KlineRequest&);
```

已取证 public → wire 周期映射：

| `cyc_type` | wire/tag | 周期 |
|---:|---:|---|
| 10000 | 10000 | 1 分钟 |
| 10008 | 10100 | 日 |
| 10009 | 10101 | 周 |
| 10010 | 10102 | 月 |
| 10011 | 10103 | 季 |
| 10012 | 10104 | 年 |

`KlineRow` 保留服务器原始整数，不隐式缩放。`market_type` 是厂商头文件对应的
`uint8_t`；价格、时间、成交量和成交额是 `int64_t`。已验证的九字段 wire 行不携带
`orig_time` 与 `variety_category`，因此两者是空的 `std::optional`，不再模仿 Python
容器制造数值 0。

### 历史 L1 快照

```cpp
QueryResult<SnapshotRow> Session::query_snapshot(const SnapshotRequest&);
```

当前只接受 `(102,"159518")` 和 `(101,"510300")`，且 `data_type=0`、
`level_type=0`。成功时 `error_code=0`；已捕获 `DataEmpty` 返回空行和 `-76`。
`SnapshotRow` 以四个 `std::array<int64_t,10>` 表示十档买卖价量，`market_type`
为 `uint8_t`。wire 不携带 `variety_category`，所以该字段是空 `optional<uint8_t>`。
CSV 位置 20..35 的语义尚未取证，但真实数据并非全空；它们以
`array<string,16> unverified_tail_raw` 原样保留，禁止猜测性转型或静默丢弃。

### ThirdInfo

```cpp
std::vector<std::string> Session::query_third_info(
    const std::vector<std::pair<std::string,std::string>>& parameters,
    int64_t offset = 0,
    int64_t count = 1000);
```

必须含 `function_id`。每个返回字符串是已经过 simdjson 校验并压缩格式化的独立
JSON object；这是为未知/可变资讯 schema 设计的零 Python 输出协议。当前可声明在线
对齐的 function 是交易日历 `A010061003`。

### ETF 信息

```cpp
std::vector<EtfRecord> Session::query_etf_info(const SecurityItem&);
```

只接受单项 SSE(101)/SZSE(102)。`EtfRecord.basic` 是静态 `EtfBasicRow`，
`constituents` 是 `vector<EtfConstituentRow>`。二者逐字段匹配公开头文件：市场为
`uint8_t`，金额/数量为 `int64_t`，字符数组为 `std::string`，单字符字段为 `char`
（wire 数字 0 保留为 `\0`）。不再向调用方返回动态 `variant` 字典。

### 证券信息

```cpp
std::vector<SecuritiesInfoRow> Session::query_securities_info(
    const std::vector<SecurityItem>&);
```

只接受已验证单项 `101/510300`、`102/159919`，或固定输入顺序的两项组合。
每个 `SecuritiesInfoRow` 恰有 43 个静态字段；批量输出保留服务端顺序。其中
`market_type`、`variety_category` 是 `uint8_t`，到期/上市/交割等日期和
`position_type` 是 `uint32_t`，价格、数量、份额和金额是 `int64_t`。解析器会先验证
JSON 原始类型，再校验目标宽度，越界即抛 `ProtocolError`。

### 除权因子

```cpp
std::vector<ExFactorRow> Session::query_ex_factor(std::string_view code);
```

每行 5 字段；`ex_date` 为 `uint32_t`。小数字符串按 C++ `double` 解析，与公开
结构一致；同时在 `ex_factor_raw` / `cum_factor_raw` 保存准确 CSV token，避免
binary64 舍入后无法恢复 N38(15) 原文。已完成闭环的代码是 `000001`。

### 代码表

```cpp
std::vector<CodeTableRow> Session::query_code_table();
```

实现多包累计、缺包一次 `ReqGetPackage` 补拉及 `ReqGetComplete`。服务端历史上
持续缺过第 3 包，因此该接口可能正确抛 `TimeoutError`；这不等于可用的完整成功样本。

## 3. 订阅接口

```cpp
session.subscribe(items);
session.unsubscribe(items);
std::string event = session.receive_raw_event(std::chrono::seconds(10));
```

公开 flag 只支持 `10 → wire 14`（大陆 L1）和 `12 → wire 16`（港股通 L1）。
事件返回单个、已经过 JSON 对象校验的 UTF-8 字符串。一次 ZSTD buffer 内的多对象
会拆成多次 `receive_raw_event`。队列上限 10,000，满时丢弃最旧事件；它不是持久队列。

## 4. 低层协议 API

`<tgw/protocol.hpp>` 暴露所有 request builder、packet parser、
`decode_server_payload`、`inspect_envelope` 和 `record_to_json`。适合做抓包回放、
协议回归和自定义调度；业务代码通常只需要 `Session`。

固定 schema 的高层查询全部返回静态结构体。低层工具中的 `Record` 是
`vector<pair<string, Scalar>>`，`Scalar` 为
`variant<int64_t,uint32_t,uint8_t,double,string>`，不会像 Python `int` 一样抹掉
位宽/符号信息。动态 ThirdInfo 不强转成 `Scalar`，而是保留经过验证的 JSON object。

完整的 wire 类型、真实样本范围与 Python 差异见 [类型审计](TYPE_AUDIT.md)。

注意 `uint8_t` 在部分标准库中是 `unsigned char` 的别名；直接交给 `operator<<` 可能按
字符打印。日志中应使用 `static_cast<unsigned>(row.market_type)`，不要为了显示方便把
存储类型扩大回无边界的 `int`。
