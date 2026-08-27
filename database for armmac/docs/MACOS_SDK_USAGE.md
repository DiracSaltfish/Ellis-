# TGW macOS ARM64 使用文档

适用实现：`tgw_macos 1.0.9.2.macos.re6`；更新时间：2026-08-27。

## 1. 先明确可用边界

当前 Mac 版本是在授权账号环境下，根据 TGW/AmazingData 公开手册、V1.0.8 头文件、
Linux x86_64 官方 SDK 行为和脱敏 wire 形状重新实现的 internet-mode 客户端。它在
Apple Silicon 上原生运行 Python 与系统 arm64 库，不加载官方 Linux/Windows 二进制。

可受控使用的范围：

- internet mode 登录和关闭；
- SZSE `159518` 的 L1 原始 full/delta 订阅；
- HKT `02800` 的沪股通路由 L1 原始 full/delta 订阅；
- SSE `510300` 的日 K 线、周 K 线、月 K 线、季 K 线与年 K 线同步查询；
- SSE `510300` 的单 ETF 基础信息与成分股同步查询；
- `A010061003` 的交易日历 ThirdInfo 同步查询；
- SZSE `159518` 历史 L1 快照：数据、空数据错误码（`-76`）与异步 `query_spi` 合约均已
  Linux/Mac 同参对齐（限该市场/代码/data_type 子范围；异步多包交付语义未观测）。

它还不具备无人值守生产 SDK 所需的自动重连、恢复订阅、类型化 SPI、完整错误码、长期
压力验证和全接口覆盖。业务上线前请同时阅读 [`API_STATUS.md`](API_STATUS.md) 和
[`DEVELOPMENT_STATUS_AND_HANDOFF.md`](DEVELOPMENT_STATUS_AND_HANDOFF.md)。

## 2. 环境与安装

### 2.1 运行要求

- Apple Silicon Mac（`arm64`）；
- macOS 自带或独立安装的 Python 3.10+；
- 推荐 Python 3.12/3.13；
- `zstandard` Python 包，或系统 `libzstd`。Python 3.14 无可用 wheel 时可执行
  `brew install zstd`，实现会从 `/opt/homebrew/lib/libzstd.dylib` 回退加载；
- 需要 DataFrame 时安装 `pandas`。

### 2.2 源码安装

```bash
cd "/Users/ellis/工具程序开发/database for armmac"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dataframe]'
python -m unittest discover -s tests -v
```

不需要 DataFrame 时可以只运行 `python -m pip install -e .`，并在查询时传
`return_df_format=False`。

### 2.3 wheel 安装

项目构建后会在 `dist/` 生成纯 Python wheel；它包含厂商服务所需的公开 CA 证书：

```bash
python -m pip install build
python -m build --wheel
python -m pip install dist/tgw_macos_arm64-1.0.9.2.6-py3-none-any.whl
```

wheel 标为 `py3-none-any` 是因为主线没有 CPython ABI 扩展；包自身仍会拒绝非 macOS
平台。`runtime/arm64/experimental` 中的 dylib 不参与 wheel，也不参与真实网络请求。

## 3. 账号配置与 TLS

创建仅本机可读的配置：

```bash
cp config/galaxy_account.example.ini config/galaxy_account.ini
chmod 600 config/galaxy_account.ini
```

```ini
[galaxy]
host = <一个或多个授权 TGW endpoint，以空格分隔>
port = <端口>
username = <授权账号>
password = <密码>
api_mode = kInternetMode
```

不要把账号或密码放在命令行、示例源码、日志、测试 fixture、抓包或 Git 中。配置文件已被
`.gitignore` 排除。

CA 默认从安装包 `tgw_macos/cert/vendor-dgw-ca.crt` 读取，也可显式覆盖：

```bash
export TGW_CA_FILE=/absolute/path/to/approved-ca.crt
export TGW_TLS_SERVER_NAME=www.dgw.com
```

当前厂商端点实测仍使用老 TLS/cipher 组合。实现只在 TGW 专用 `SSLContext` 内放宽
security level，仍保留 CA 链和 `www.dgw.com` 主机名校验。不要设置关闭证书校验的全局
环境变量或 monkey patch。

可选环境变量：

| 变量 | 默认 | 含义 |
|---|---|---|
| `TGW_CA_FILE` | 包内证书 | CA 文件绝对路径 |
| `TGW_TLS_SERVER_NAME` | `www.dgw.com` | TLS 主机名 |
| `TGW_CLIENT_VERSION` | 已取证客户端版本串 | 登录 envelope 的 Version |
| `TGW_MAC_ADDRESS` | 本机主地址 | 逗号分隔的小写 MAC；不要记录到日志 |
| `TGW_TIMEOUT_SEC` | `15` | 请求/连接超时秒数 |
| `TGW_HEARTBEAT_SEC` | `5` | push WebSocket ping 周期 |
| `TGW_QUERY_ENDPOINTS` | `dgw1_query,dgw2_query` | 查询路径轮转列表 |
| `TGW_BACKEND` | `live` | 仅测试可设 `sim`；不要在业务中设 `cpp-skeleton` |

## 4. 会话生命周期

### 4.1 登录

```python
import tgw_macos as tgw

cfg = tgw.Cfg().set(
    server_vip="<TGW_HOST>",
    server_port=0,
    username="<AUTHORIZED_USERNAME>",
    password="<AUTHORIZED_PASSWORD>",
    force_logout=False,
)

if not tgw.Login(cfg, tgw.ApiMode.kInternetMode):
    raise RuntimeError("TGW login failed")
```

`Login(config, api_mode, path="") -> bool` 完成 TCP、TLS、WebSocket upgrade、`ReqLogon`
和 `OnRspLogon` token 验证。只有服务端 `status=0`、tag 为 `OnRspLogon` 且 token 非空时
返回 `True`。当前只支持 `ApiMode.kInternetMode=2`；colocation mode 返回失败。

`force_logout=True` 可能踢下同账号的其它会话。除非业务明确需要并理解影响，保持
`False`。Linux oracle 与 Mac 同时验证时应使用两个独立授权账号。

### 4.2 日志

```python
class LogSink:
    def on_log(self, level, message):
        print(level, message)

tgw.SetLogSpi(LogSink())
```

当前日志回调只有 `on_log(level, message)`，不是官方全部 `ILogSpi`/`IGMDSpi` 回调。
不要在日志中输出配置对象、登录响应 headers 或 token。

### 4.3 关闭

所有请求放在 `try/finally` 中：

```python
if not tgw.Login(cfg, tgw.ApiMode.kInternetMode):
    raise RuntimeError("login failed")
try:
    # query / subscribe
    pass
finally:
    tgw.Close()
```

当前全局 backend 关闭后不能在同一解释器中可靠地再次 `Login`；需要重新登录时优先重启
进程。后续将用正式会话对象替代这一全局单例限制。

## 5. 已验证查询

四个已开放查询 API 中，三个（K 线、ThirdInfo、ETF）为**同步调用**：没有 `query_spi` 时返回
`(result, error_code)`，成功路径 `error_code=0`。`QuerySnapshot` 额外支持官方同步/异步两种
错误合约（见 5.4）。除快照外传入 `query_spi` 会明确抛 `NotImplementedError`，因为那些接口的
异步语义尚未取证实现。

普通查询在 `return_df_format=True`（默认）时返回 `pandas.DataFrame`，`False` 返回
`list[dict[str, object]]`，不需要 pandas，也最适合协议调试。ETF 查询是嵌套结果，格式见 5.3。

### 5.1 ThirdInfo 交易日历

```python
task_id = tgw.GetTaskID()
params = {
    "function_id": "A010061003",
    "start_date": "20260801",
    "end_date": "20260826",
    "market": "SSE",
}
for key, value in params.items():
    assert tgw.SetThirdInfoParam(task_id, key, value) == 0

rows, error = tgw.QueryThirdInfo(task_id, return_df_format=False)
assert error == 0
days = [int(row["TRADE_DAYS"]) for row in rows]
```

请求过程：

```text
GetTaskID → 多次 SetThirdInfoParam → ReqGetThirdInfo
→ tag 11101 的 1..N 个包 → 嵌套 JSON body.data
→ ReqGetComplete → 关闭单次 query WSS
```

`SetThirdInfoParam` 把同一 task id 的 key/value 暂存在内存；`QueryThirdInfo` 读取后会移除。
日历分支只验证了 `function_id=A010061003`、`market=SSE` 和日期范围。其它 function id、
市场以及分页大结果必须独立验证，不能因为走同一 ThirdInfo 通道就视为可用。

### 5.2 1 分钟、日、周、月、季与年 K 线

```python
req = tgw.ReqKline().set_code("510300")
req.market_type = tgw.MarketType.kSSE
req.cq_flag = 0
req.cq_date = 0
req.qj_flag = 0
req.cyc_type = 10008
req.cyc_def = 0
req.auto_complete = 1
req.begin_date = 20260825
req.end_date = 20260825
req.begin_time = 0
req.end_time = 0

rows, error = tgw.QueryKline(req, return_df_format=False)
assert error == 0
```

示例使用日线。经同参验证的 1 分钟样本使用 `req.cyc_type=10000`、深市 `market_type=102`、
`begin_time=900`、`end_time=1500`；分钟时间必须传 HHmm 窗口，不能沿用日线的 0/0。
周线把 `req.cyc_type` 改为 `10009` 并设置目标周窗口；月线改为 `10010` 并设置目标月窗口；
季线改为 `10011` 并设置目标季窗口。已验证 request/wire：

| 公开 API | wire method | wire 周期 | 响应 tag | 当前范围 |
|---|---|---|---:|---|
| `cyc_type=10000` | `ReqGetKline` | `period_type=10000` | `10000` | SZSE `159691` 1 分钟线，2026-08-26 09:00–15:00 |
| `cyc_type=10008` | `ReqGetKline` | `period_type=10100` | `10100` | SSE `510300` 日线 |
| `cyc_type=10009` | `ReqGetKline` | `period_type=10101` | `10101` | SSE `510300` 周线 |
| `cyc_type=10010` | `ReqGetKline` | `period_type=10102` | `10102` | SSE `510300` 月线 |
| `cyc_type=10011` | `ReqGetKline` | `period_type=10103` | `10103` | SSE `510300` 季线（2026-08-26 实捕证明） |
| `cyc_type=10012` | `ReqGetKline` | `period_type=10104` | `10104` | SSE `510300` 年线（2026-08-26 实捕证明） |

六个已验证周期的 wire 行均为 9 个 CSV 字段；公开行均为 11 字段 dict，并补
`orig_time=0`、`variety_category=0`。

返回字段按顺序为：

| 字段 | 当前类型/说明 |
|---|---|
| `market_type` | `int` |
| `security_code` | `str` |
| `orig_time` | `int`，当前官方/本地验证样本为常量 0 |
| `kline_time` | `int`；1 分钟样本为 12 位 `yyyyMMddHHmm`，日/周/月/季/年样本为 8 位日期 |
| `open_price` / `high_price` / `low_price` / `close_price` | `int`，原始协议缩放值 |
| `volume_trade` | `int`，原始协议缩放值 |
| `value_trade` | `int`，原始协议缩放值 |
| `variety_category` | `int`，当前为常量 0 |

不要在未核对手册缩放与目标品种前直接把整数当作元/股。除 `10000`、`10008`、`10009`、
`10010`、`10011`、`10012` 外的 `cyc_type`（其余分钟周期 `10001–10007`）会明确失败。

#### 159691.SZ 已核验的单位输出（仅 2026-08-26 当日 1 分钟）

`QueryKline` 的默认输出是协议原值，**不能**把 `open_price` 等整数直接显示为元，也不能把
`volume_trade` 直接显示为股或手。对截图对应的已核验范围，可显式开启标准化输出：

```python
from decimal import Decimal
import tgw_macos as tgw

req = tgw.ReqKline().set_code("159691")
req.market_type = tgw.MarketType.kSZSE
req.cq_flag = req.cq_date = req.qj_flag = req.cyc_def = 0
req.cyc_type = 10000
req.auto_complete = 1
req.begin_date = req.end_date = 20260826
req.begin_time, req.end_time = 900, 1500

rows, error = tgw.QueryKline(req, return_df_format=False, normalized=True)
assert error == 0
first = rows[0]
close_yuan: Decimal = first["close_price_yuan"]
shares: int = first["volume_shares"]
amount_yuan: Decimal = first["value_trade_yuan"]
```

`normalized=True` 会在**发起网络请求前**检查代码、市场、复权/周期参数、日期和时间窗；任何
偏离 `159691`、SZSE、`cyc_type=10000`、2026-08-26、09:00–15:00 的请求都会抛
`NotImplementedError`。这不是通用换算器，目的是防止把未验证品种或日期静默地套用错误倍数。
已有的原始行也可使用
`tgw.NormalizeVerified159691SzseOneMinuteKlineRows(raw_rows)`；它会再次核对每行的范围和结构。

标准化行的字段和单位如下。价格与金额使用 `Decimal`，而非 `float`；CSV 导出可直接写入其
字符串形式，JSON 输出应使用 `str(value)` 或自定义 `Decimal` 编码器。

| 标准化字段 | 类型 | 含义 / 精确换算 |
|---|---|---|
| `open_price_yuan` / `high_price_yuan` / `low_price_yuan` / `close_price_yuan` | `Decimal` | 原始价格 ÷ 1,000,000，人民币元 |
| `volume_shares` | `int` | 原始 `volume_trade` ÷ 100，股 |
| `value_trade_yuan` | `Decimal` | 原始 `value_trade` ÷ 100,000，人民币元 |
| `raw_open_price` … `raw_value_trade` | `int` | 未修改的协议原值，仅用于审计或重新计算；业务展示勿使用 |
| `market_type` / `security_code` / `kline_time` / `orig_time` / `variety_category` | 原类型 | 与原始行一致；`kline_time` 是 12 位 `yyyyMMddHHmm` |

该组倍数通过同日 242 根完整分钟线与独立行情界面逐项交叉核对：开、高、低、最新价完全一致，
并且按标准化行聚合出的成交额、总手和成交均价与界面四舍五入后完全一致。不要仅依据通用结构
说明推断其它标的的缩放；其单位必须先独立验证。

### 5.3 ETF 基础信息与成分股（SSE 单 ETF）

```python
item = tgw.SubCodeTableItem().set_code("510300")
item.market = tgw.MarketType.kSSE

pairs, error = tgw.QueryETFInfo(item, return_df_format=False)
assert error == 0
for basic_info, constituent_rows in pairs:
    print(basic_info.keys(), len(constituent_rows))
```

当前只放行 `market=101(SSE)`、单个 `SubCodeTableItem`、同步返回。JSON 模式返回：

```python
list[tuple[dict[str, object], list[dict[str, object]]]]
```

已验样本为 1 条 ETF 基础信息（35 字段）及 300 条成分股（每条 13 字段）。默认 DataFrame
模式返回 `list[tuple[one_row_basic_dataframe, constituent_dataframe]]`；基础信息 DataFrame 固定
一行。数值保持官方低层整数原值，调用方必须依据头文件注释再处理价格、金额、比例等缩放。

请求不建立一次性 query WSS，而复用登录后的常驻 `/amd/dgw/push` 连接：method 为
`ReqGetETFCodeTableList`，参数 `Security="510300|101"`，响应 tag 为字符串 `"111"`；收到
单帧后发送无 params 的 `ReqGetCodelistComplete`。该路径与订阅共享 reader 生命周期，断线时
查询也会失败。异步 `IGMDETFInfoSpi` 尚未实现；SZSE、多个 item、空结果/错误码、多响应帧均
没有在线证据，当前会显式拒绝或报协议错误。

### 5.4 历史 L1 快照（同步 + 异步错误合约已对齐）

```python
req = tgw.ReqDefault().set_code("159518")
req.market_type = tgw.MarketType.kSZSE
req.date = 20260825
req.begin_time = 93000000
req.end_time = 93030000
req.data_type = 0
req.level_type = 0

rows, error = tgw.QuerySnapshot(req, return_df_format=False)
assert error == 0 or error == tgw.ErrorCode.kDataEmpty
```

唯一放行范围是 `SZSE 159518`、`data_type=0`、`level_type=0`。method 为
`ReqGetSnapshot`，数据响应 tag 为 `11000`。官方 Linux 与 Mac 已同参验证：数据窗 11 行 ×
57 个公开字段且类型一致；空数据窗按官方语义返回 `(None, -76/kDataEmpty)`。

异步模式与官方 wrapper 合约一致：

```python
class SnapshotCollector:
    def __call__(self, result, err_code):
        # 数据批次: (list_or_DataFrame, None)
        # 错误/空数据: (None, int 错误码)，如 (None, -76)
        ...

submitted, submit_err = tgw.QuerySnapshot(req, query_spi=SnapshotCollector(),
                                          return_df_format=False)
# 提交成功立即返回 (True, None)；本地校验失败同步抛错。
```

超时经回调交付 `(None, -83/kTimeout)`；内部异常按官方行为透传 `(None, str(exc))`。
回调在后台线程触发，用户对象被直接调用。

已知边界：

1. 异步多包交付语义未观测——当前实现将收齐的全部行放入一次回调；官方 C++ 为每包一次；
2. 空数据 wire 帧为字符串 tag `"DataEmpty"` + status=-100，客户端映射为公开 -76；
   其它错误标签未捕获，出现即显式报协议错；
3. 只验证了一个市场、代码、日期和窄窗口；DataFrame 列序与官方 `json_normalize`
   的逐列一致性未验。

57 个低层字段包括：`market_type`、`security_code`、`variety_category`、`orig_time`、
`trading_phase_code`、六个基础价格、10 档 `bid_priceN/bid_volumeN/offer_priceN/
offer_volumeN`、`num_trades`、`total_volume_trade`、`total_value_trade`、`IOPV`、
`high_limited`、`low_limited`。返回的是官方低层整数缩放值；wire 尾部 16 个未证明槽位
按设计丢弃，不会猜测字段含义。

### 5.5 代码表 `QueryCodeTable`（`ARM_IMPLEMENTED`，2026-08-26）

```python
rows, err = tgw.QueryCodeTable(return_df_format=False)  # rows: list[dict]，6 列
```

公开接口无入参；返回 6 列 `security_code / symbol / english_name / market_type /
security_type / currency`。Mac 同步返回**累计全部批次**（对齐官方异步总数，偏离官方
同步 wrapper 的首批竞态）。注意事项：

1. wire 已证：one-shot `dgw*_query` 通道、method `ReqGetReduceCodeTable`、
   tag `11103`、行分隔符反引号、缺包时经 `ReqGetPackage` 补拉一次；
2. 服务端全市场大表当前**持续缺第 3 包**且对补拉无响应：Linux 官方 SDK 同步返回
   `-83 kTimeout`，Mac 因缺包超时抛 `TgwTimeoutError`（同因同果）；尚无成功同参样本，
   状态 `ARM_IMPLEMENTED`；
3. `query_spi` 传入显式抛 `NotImplementedError`；`OnStatus`/`FreeMemory` 未实现；
4. 完成 method 未捕获（沿用 `ReqGetComplete`）；`currency`/`security_type` 空白串的
   trim 行为待成功样本复核。

### 5.6 证券基础信息 `QuerySecuritiesInfo`（SSE 单代码，2026-08-26）

```python
item = tgw.SubCodeTableItem()
item.market = tgw.MarketType.kSSE   # 101
item.security_code = "510300"
rows, err = tgw.QuerySecuritiesInfo(item, return_df_format=False)
```

已验证范围：SSE `510300` 单 item 同步返回 1 行 **43 字段**（`MDCodeTableRecord`，
pack(1) sizeof=555）。wire：常驻 push 通道 `ReqGetCodeTableList`、`Security="code|market"`、
tag 字符串 `"109"`、`ReqGetCodelistComplete` 完成。注意事项：

1. 全市场（market=kNone）、多 item、SZSE/NEEQ、异步 `query_spi` 均显式拒绝；
2. 空结果/非零 status 的 wire 形状未取证；数值字段为原始缩放值，调用方需按手册换算；
3. push 连接与订阅/ETF 共享生命周期，当前无自动重连。

### 5.7 除权因子 `QueryExFactorTable`（`000001` 单代码，2026-08-26）

```python
rows, err = tgw.QueryExFactorTable("000001", return_df_format=False)
```

已验证范围：`000001` 单代码同步返回 33 行 5 字段（`inner_code`/`security_code` str、
`ex_date` int、`ex_factor`/`cum_factor` float，double 18 位小数往返无损）。wire：
one-shot `dgw*_query` 通道 `ReqGetExFactor`、tag `11102`、5 字段 CSV。注意事项：

1. 无显式市场参数（代码默认路由，wire 无市场字段）；
2. 空结果/非零 status 的 wire 形状未取证，parser 非零 status 一律报错；
3. double 缩放（N38(15)）由调用方按手册处理；异步 `query_spi` 显式拒绝。

## 6. 已验证 L1 订阅

### 6.1 大陆 ETF `159518`

```python
item = tgw.SubscribeItem().set_code("159518")
item.market = tgw.MarketType.kSZSE
item.flag = tgw.SubscribeDataType.kSnapshot
item.category_type = 0

assert tgw.Subscribe(item) == 0
```

公开 flag `10` 会转换为 wire `14`；推送 tag 为字符串 `"14"`。Linux 官方 30 秒样本
10 条，Mac 60 秒样本 19 条（full 2、delta 17）。

### 6.2 港股通 `02800`（沪股通路由）

```python
item = tgw.SubscribeItem().set_code("02800")
item.market = tgw.MarketType.kSSE       # 注意：不是 kHKEx
item.flag = tgw.SubscribeDataType.kHKTSnapshot
item.category_type = 0

assert tgw.Subscribe(item) == 0
```

港股通标的使用互联互通路由：本轮验证的是 `02800.SH`，所以 market 为 `kSSE=101`；
直接传 `kHKEx=103` 会被官方客户端拒绝。公开 flag `12` 转换为 wire `16`，推送 tag 为
`"16"`。`.SZ` 路由尚未 live 验证。

### 6.3 批量订阅后同会话追加

`Subscribe`/`UnSubscribe` 既接受单个 `SubscribeItem`，也接受 `list[SubscribeItem]`。
已验证的 202 标的调用方式是每 20 个一批，全部成功后在**同一登录会话**对
新标的再调一次 `Subscribe(extra)`：

```python
def l1_item(symbol: str):
    item = tgw.SubscribeItem().set_code(symbol[:6])
    item.market = (
        tgw.MarketType.kSSE if symbol.endswith(".SH")
        else tgw.MarketType.kSZSE
    )
    item.flag = tgw.SubscribeDataType.kSnapshot
    item.category_type = 0
    return item

initial_items = [l1_item(symbol) for symbol in initial_202_symbols]
subscribed = []
try:
    for offset in range(0, len(initial_items), 20):
        batch = initial_items[offset:offset + 20]
        if tgw.Subscribe(batch) != 0:
            raise RuntimeError(f"subscription batch rejected at offset {offset}")
        subscribed.extend(batch)

    extra = l1_item("164824.SZ")
    if tgw.Subscribe(extra) != 0:
        raise RuntimeError("incremental subscription rejected")
    subscribed.append(extra)

    # ReceiveRawEvent() 持续返回原 202 标的和追加标的的独立 dict。
    event = tgw.ReceiveRawEvent(timeout=5.0)
finally:
    for offset in range(0, len(subscribed), 20):
        tgw.UnSubscribe(subscribed[offset:offset + 20])
    tgw.Close()
```

2026-08-27 re6 wheel 实盘结果：11 个初始批次全部返回 0；追加 `164824.SZ`
返回 0，调用耗时 33.787 ms。30 秒采集中共 1342 个事件（full 356 / delta 986），
203 个标的全部出现且全部有 full，
status 全为 0。这只证明该时段的 202+1 短时能力，不代表 1000 标的、长时或重连恢复已验收。

同一日又对“从初始批次中单独移除”做了独立测试：202 标的按 20 分批后，
`tgw.UnSubscribe(item_for_159866)` 返回 0，耗时 30.866 ms。移除前 10 秒中
`159866.SZ` 有 4 条事件；返回后 3 秒在途宽限期为 0 条，后续 20 秒仍为 0 条，
而其它标的同期继续产生 918 条事件。这证明当前服务会精确移除该代码，
未观察到整批取消。业务层仍应允许少量在途帧，并以移除完成时刻+宽限窗口判定违例。
加长复验第二次返回 0（31.180 ms），3 秒宽限+后 30 秒内目标仍为 0 条，剩余
201/201 标的全部继续更新，共 1332 条事件。

202 标的推送会触发服务端批量压缩帧：WebSocket payload 为 `0x59 + ZSTD`，
解压后是多个以 ASCII `0x60` 分隔的 JSON object，末尾可有 C NUL。SDK 已在 reader
内部拆分，调用方不得自行解压，也不会从 `ReceiveRawEvent` 收到 list/batch 包装。

### 6.4 原始事件“回调”逻辑

当前 `Subscribe(item, push_spi)` 不实现官方类型化异步 SPI；传非空 `push_spi` 会抛错。
临时接口是从 reader 线程维护的有界队列中阻塞取事件：

```python
event = tgw.ReceiveRawEvent(timeout=5.0)
```

已观测事件 envelope：

```python
{
    "headers": {"tag": "14"},   # HKT 为 "16"
    "status": 0,
    "is_delta": 0,               # 0=full，1=delta
    "data": {"<numeric-key>": "<scalar-or-value>"},
}
```

实际 headers 可能还有关联字段。不要打印完整事件，因为其中包含实时行情值。当前 `data`
仍使用数字 key，尚未转换成手册中的 `Snapshot`/`TGWHKTSnapshot` 字段。

reader thread 失败时 `ReceiveRawEvent` 会抛 `TgwTransportError`（属于 `RuntimeError`）；等待
超时会抛 `TgwTimeoutError`（属于 `TimeoutError`）。前者应终止当前进程/会话，后者可以继续
等待并结合“最后成功事件时间”判断数据是否停滞。

单标的临时 full/delta 合并：

```python
state = None
while True:
    event = tgw.ReceiveRawEvent(timeout=5)
    data = event.get("data") if isinstance(event, dict) else None
    if not isinstance(data, dict):
        continue
    if not event.get("is_delta"):
        state = dict(data)
    elif state is not None:
        state.update(data)
    # 只有拿到 full 后的 state 才可视为当前完整原始状态
```

对多标的订阅，必须先完成数字 key 与证券身份字段映射，再按 `(market, code, tag)` 隔离
状态；不要把多个标的的 delta 合并进同一 dict。重连后必须丢弃旧 state，等待新的 full。

事件队列容量为 10,000，满时会丢弃最旧事件以保证 reader 不阻塞。因此业务需要监测处理
延迟和丢包，不得假设队列是持久消息系统。

### 6.5 取消与关闭

```python
try:
    assert tgw.Subscribe(item) == 0
    # receive events
finally:
    tgw.UnSubscribe(item)
    tgw.Close()
```

取消使用同一个 `SubscribeItem`，wire method 为 `ReqUnSubscribeBatch`。当前没有重复取消、
部分批次失败、断线取消等完整语义；无论取消结果如何都应执行 `Close()`。

## 7. ctypes 请求结构

所有结构遵循 V1.0.8 头文件 `#pragma pack(1)`；测试锁定大小和关键 offset。

### 7.1 `Cfg`，145 bytes

| 字段 | ctypes | 说明 |
|---|---|---|
| `server_vip` | `char[24]` | endpoint |
| `server_port` | `uint16` | 端口 |
| `username` | `char[32]` | 授权账号 |
| `password` | `char[64]` | 密码；关闭时 backend 副本会清空 |
| `force_logout` | `bool` | 是否顶掉已有会话 |
| `coloca_cfg` | `ColocaCfg`，22 bytes | 当前 internet 实现不使用 |

`Cfg().set(...)` 会把字符串按 UTF-8 编码写入定长字符数组；超长值由 ctypes 拒绝。

### 7.2 `SubscribeItem`，42 bytes

| 字段 | ctypes | 当前支持 |
|---|---|---|
| `market` | `uint8` | `102` + ETF；`101` + HKT 沪股通 |
| `flag` | `uint64` | 仅 `10`、`12` |
| `security_code` | `char[32]` | `159518` / `02800` |
| `category_type` | `uint8` | 已验证为 0 |

### 7.3 `ReqKline`，71 bytes

字段顺序：`security_code[38]`、`market_type:uint8`、`cq_flag:uint8`、
`cq_date:uint32`、`qj_flag:uint32`、`cyc_type:uint16`、`cyc_def:uint32`、
`auto_complete:uint8`、`begin_date:uint32`、`end_date:uint32`、
`begin_time:uint32`、`end_time:uint32`。构造默认 `auto_complete=1`。

当前 wire 只使用已验证日线/周线/月线所需字段；`cq_date/qj_flag/cyc_def` 的其它组合未验。

### 7.4 `ReqDefault`，55 bytes

字段顺序：`security_code[38]`、`market_type:uint8`、`date:uint32`、
`begin_time:uint32`、`end_time:uint32`、`data_type:uint16`、`level_type:uint16`。

注意：开发手册表格没有列 `level_type`，但 V1.0.8 Linux/Windows 头文件与官方 Python
对象都包含它，默认 0，offset=53。Mac ABI 必须保留；当前非零值明确拒绝。

### 7.5 `SubCodeTableItem`，36 bytes

| 字段 | ctypes | 当前支持 |
|---|---|---|
| `market` | `int32`（有符号） | 仅 `101`（SSE）完成在线取证 |
| `security_code` | `char[32]` | 当前只放行 `510300` 单 ETF 样本 |

注意它的 `market` 是有符号 `int32`，不同于 `SubscribeItem.market:uint8`。返回结构
`MDETFCodeTableRecord` 内含 C++ `std::vector`，因此本实现不把整个返回对象 ctypes 化，而在
wire 解码后生成“基础信息 dict + 成分股 list”的两级 Python 容器。

## 8. 请求、线程和错误行为

- push 连接长期存在，reader daemon thread 负责读帧，heartbeat thread 每 5 秒发 WebSocket
  ping；服务端 ping 会立即回 pong；
- 订阅 request id 从 `1_000_000` 开始；查询 task id 从 1 递增，二者分离；
- K 线、快照与 ThirdInfo 查询建立独立 `/amd/dgw/dgw1_query` 或 `dgw2_query` WSS，收齐包后
  发 `ReqGetComplete` 并关闭；ETF 查询例外，复用常驻 push WSS 并发送
  `ReqGetCodelistComplete`；
- ZSTD 帧既支持标准 magic，也支持厂商 `0x59 + ZSTD` 前缀；解压上限 64 MiB；
- 协议会校验 status、tag、包号一致性、缺包、重复包和返回形状；
- `GetErrorMsg` 覆盖官方全量公开错误码文案（如 `-76` → “数据为空”）；未收录码返回
  "unknown error code"。快照查询的空数据/错误已按官方合约返回错误码而不是抛异常；
- 登录/订阅失败通常返回 `False/-1` 并在 backend 记录 `last_error`；查询网络/协议失败会抛
  `TgwTransportError`、`TgwProtocolError` 或 `TgwTimeoutError`；这些类当前位于内部模块，
  应用可先按 `RuntimeError/TimeoutError` 捕获（快照异步模式例外：经回调交付，见 5.4）；
- 服务端 `1000 / accept conn active close` 是主动关闭/准入或流控证据。停止密集重试，
  记录端点与时间，退避并保留 Linux 官方 SDK 回退路径。

## 9. 推荐运行方式

当前只建议单进程、小订阅集、低频查询和显式进程监管：

1. 启动进程并登录；
2. 建立少量订阅，等待 full 后再消费 delta；
3. 监测最后事件时间、事件处理延迟、异常与进程存活；
4. 断线即退出进程，由 supervisor 做指数退避重启；不要在当前 singleton 上循环重连；
5. 重启后重新订阅，丢弃旧状态，等待新 full；
6. 每次关闭先取消订阅，再 `Close()`；
7. 保留 Linux 官方 SDK 作为查询/结果对照和故障回退。

在线烟测只打印 shape/计数，不打印行情值：

```bash
python tools/live_smoke.py --config config/galaxy_account.ini
python tools/live_smoke.py --config config/galaxy_account.ini \
  --etf-info 510300 --market 101
python tools/live_smoke.py --config config/galaxy_account.ini \
  --subscribe 159518 --market 102 --data-type 10 --duration 30
python tools/live_smoke.py --config config/galaxy_account.ini \
  --subscribe 02800 --market 101 --data-type 12 --duration 30
```

完整示例见 `examples/query_verified.py` 和 `examples/subscribe_raw.py`。
