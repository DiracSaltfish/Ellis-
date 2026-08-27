# 银河「星耀数智」平台 SDK 调研报告

> 调研对象：`AmazingData开发手册.pdf`（V1.0.24，2025-12-16 发布，共 148 页）、
> `tgw-1.0.9.2-py3-none-any.whl`、`AmazingData-1.1.9-cp*.whl`、`TGW-SDK_V1.0.8`（C++ 版）
> 结论先行：这是中国银河证券「格物金融服务平台 TGW」及其上层封装「AmazingData」，
> 官方仅提供 **Linux x86-64 与 Windows x86-64** 二进制，无 macOS 支持。

---

## 1. 产品与 SDK 层次

```
┌────────────────────────────────────────────────┐
│ AmazingData 1.1.9   高层封装（纯 Python .pyc） │  ← 数据查询/订阅/金融算子/因子分析
├────────────────────────────────────────────────┤
│ tgw 1.0.9.2         底层 Python 绑定           │  ← C++ 原生库 (_tgw.pyd / libtgw*.so)
├────────────────────────────────────────────────┤
│ 格物 TGW 网关（C++）                                ← 仅 Redhat7 x64 / Win10 x64
└────────────────────────────────────────────────┘
                 ↕ TCP（互联网模式 / 托管机房专线）
        银河证券行情数据服务端（IP/端口由开户营业部提供）
```

- **同一产品家族**：`TGW-SDK_V1.0.8`（C++ 手册《中国银河证券格物金融服务平台(TGW)开发手册(C++版)》）
  与 Python 包的登录模型完全一致（ServerVip/ServerPort/UserName/Password，ApiMode 1=托管机房 2=互联网）。
- Python wheel 版本更新（1.0.9.2 > C++ 包 1.0.8），开发建议以 Python 包为主。

## 2. 运行环境与安装

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10 64 位 / Redhat 7.2·7.4·7.6（**无 macOS**） |
| Python | 手册声明支持 3.8–3.13（wheel 内含 3.6–3.14 原生包目录） |
| 依赖链 | `AmazingData 1.1.9` 要求 `pydantic>=2.6.4, numba>=0.65, scipy>=1.15.1, statsmodels>=0.11, tgw>=1.0.9.1` |
| 实际推荐 | **Python 3.12 / 3.13 x64**（numba/scipy 新版已放弃 3.8/3.9） |

安装：
```bat
pip install tgw-1.0.9.2-py3-none-any.whl
pip install AmazingData-1.1.9-cp312-none-any.whl   ; 按本机 Python 版本选择
```

wheel 下载渠道（手册 3.1.2）：银河网盘 `https://cloud.chinastock.com.cn/p/DSG36jYQx2IY_Y8CIAA`
或公众号"中国银河证券星耀数智"→ 业务介绍 → 安装包下载。

## 3. 登录机制（关键细节）

```python
import AmazingData as ad
ad.login(username='tgw_xxxx', password='******', host='***.***.***.***', port=xxxx)
```

| 要点 | 说明 |
|---|---|
| 账号来源 | **联系开户营业部申请开通权限后获取** 账号/密码/IP/端口（手册 3.5.1.1） |
| 用户名前缀 | 反编译确认代码强制校验用户名以 `tgw_` 开头，否则抛 `username is illegal` |
| 登录模式 | `kInternetMode`（互联网模式）/ `kColocationMode`（托管机房模式，QTCP/TCP/RTCP 通道） |
| 内置重试 | `ad.login` 内部最多尝试 5 次，间隔 sleep；**全部失败会调用 exit() 直接退出进程** ⚠️ |
| force_logout | 底层 Cfg 支持 `force_logout` 字段，用于踢掉同账号旧会话（防多端互踢） |
| 多端登录 | 免责声明第 4 条：同一账号同时多人登录，公司有权停止服务 → 桥接机应独占账号 |
| 登出 | `ad.logout(username)`；正常使用无需调用 |
| 改密 | `ad.update_password(username, old_password, new_password)`，须先登录 |

⚠️ **桥接实现注意**：因 `ad.login` 失败会 `exit()` 杀进程，桥接服务应绕开该封装，
直接构造 `tgw.Cfg` 并调用 `tgw.Login(cfg, api_mode)` 自主控制重试（本项目已如此实现，
逻辑与 `ad.login` 内部完全等价，见反编译核对记录）。

## 4. 数据接口全景（约 60 个）

### 4.1 三大查询类

| 类 | 构造 | 说明 |
|---|---|---|
| `ad.BaseData()` | 无参 | 代码表/交易日历/复权因子/证券基础信息 |
| `ad.InfoData()` | 无参 | 财务/股东/融资融券/异动/期权/ETF/指数/可转债/公告等 ~45 个接口 |
| `ad.MarketData(calendar)` | 需先取交易日历 `BaseData.get_calendar()` | 历史快照、历史 K 线 |
| `ad.DownloadInfoData()` | 无参 | 批量下载落地（hdf5 本地缓存） |

### 4.2 接口清单（函数名 / 核心入参）

**BaseData**
| 接口 | 入参 |
|---|---|
| get_calendar | data_type/market/date（均可默认） |
| get_code_info(security_type) | 每日最新证券信息 |
| get_code_list(security_type) | 每日最新代码表（沪深北） |
| get_future_code_list(security_type) | 期货代码表 |
| get_option_code_list(security_type) | 期权代码表 |
| get_hist_code_list(security_type, start_date, end_date, local_path) | 历史代码表 |
| get_backward_factor(code_list, local_path, is_local) | 后复权因子 |
| get_adj_factor(code_list, local_path, is_local) | 单次复权因子 |
| get_etf_pcf(code_list) | ETF 申赎清单+成分股 |

**MarketData(calendar)**
| 接口 | 入参 |
|---|---|
| query_snapshot(code_list, begin_date, end_date[, begin_time, end_time]) | 历史 L1 快照，返回 `{code: DataFrame}` |
| query_kline(code_list, begin_date, end_date, period[, begin_time, end_time]) | 全周期 K 线，返回 `{code: DataFrame}` |

时间参数约定：日期一律 8 位 int（如 `20240101`）；快照时分秒毫秒 8~9 位 int（`93000000`=9:30:00.000）；K 线时分 3~4 位 int（`930`=9:30，`1725`=17:25）。

**InfoData（均返回 DataFrame；`local_path/is_local` 为本地缓存参数组）**
get_stock_basic, get_history_stock_status, get_bj_code_mapping,
get_balance_sheet, get_income, get_cash_flow, get_profit_express, get_profit_notice,
get_share_holder, get_holder_num, get_equity_structure, get_equity_pledge_freeze, get_equity_restricted,
get_dividend, get_right_issue, get_margin_summary(date), get_margin_detail,
get_long_hu_bang, get_block_trading,
get_option_basic_info, get_option_std_ctr_specs, get_option_mon_ctr_specs,
get_fund_share, get_fund_nav, get_fund_iopv,
get_index_constituent, get_index_weight,
get_industry_base_info, get_industry_constituent, get_industry_weight, get_industry_daily,
get_kzz_issuance/share/conv/conv_change/corr/call/put/put_call_item/put_explanation/call_explanation/suspend,
get_treasury_yield, get_announcement_stock(_list)/fund/bond 系列 …

### 4.3 实时订阅（SubscribeData）

```python
sub = ad.SubscribeData()
@sub.register(code_list=codes, period=ad.constant.Period.snapshot.value)
def on_snapshot(data, period): ...
sub.run()      # 阻塞式接收循环
```

- 回调按品种自动分发：股票/ETF/可转债→`Snapshot`，指数→`SnapshotIndex`，期货→`SnapshotFuture`，
  港股通→`SnapshotHKT`，ETF 期权→`SnapshotOption`，K 线→`Kline`。
- `register(code_list, period)` 可多次调用累积；`run()` 启动内部线程池（pool_num 可配）。
- period 取值见下表。

## 5. 常用枚举（附录 4.1）

**security_type（代码表类型）**
`EXTRA_STOCK_A`(沪深北A股全量) / `SH_A` `SZ_A` `BJ_A` / `EXTRA_INDEX_A` `SH_INDEX` `SZ_INDEX` `BJ_INDEX` /
`EXTRA_ETF` `SH_ETF` `SZ_ETF` / `EXTRA_KZZ` `SH_KZZ` `SZ_KZZ` / `EXTRA_HKT` `SH_HKT` `SZ_HKT` /
`EXTRA_GLRA`(逆回购) / 期货：`ZJ_FUTURE`(中金所) / 期权：`EXTRA_ETF_OP` `SH_OPTION` `SZ_OPTION`

**market（市场）**：`SH` `SZ` `BJ` `CFE` `SHN` `SZN` `HK`

**Period（K 线周期，int）**
min1/min3/min5/min10/min15/min30/min60/min120=1..120 分钟，day/week/month/season/year
订阅快照用 `Period.snapshot`，期货快照 `snapshotfuture`，期权 `snapshotoption`，港股通 `snapshotHKT`。

## 6. 本地缓存方案（手册 4.4）

- 参数组 1：`local_path`（hdf5 存储绝对路径，如 `'D:\\AmazingData_local_data\\'`）+ `is_local`
  —— 全量历史落盘、增量更新、读盘加速。
- 参数组 2：`begin_date` + `end_date` —— 直连服务器按区间查，不落缓存。
- 两组互斥，组内必须成对出现。磁盘建议 ≥500GB。
- `is_local=True`：本地有数据直接读本地（不再拉最新）；`False`：强制联网并刷新本地。

## 7. 快照/K 线数据结构（附录 4.2，字段节选）

- `Snapshot`：code, trade_time, last/open/high/low/close, pre_close, volume, amount,
  ask_price1~5 / bid_price1~5 及对应 volume, iopv, trading_phase_code …
- `SnapshotIndex`：code, trade_time, last/pre_close/open/high/low/close, volume, amount
- `SnapshotFuture`：另含 action_day/trading_day, open_interest, settle, average_price …
- `SnapshotHKT`：nominal_price, ref_price, 买卖盘上下限价, 冷静期涨跌停 …
- `SnapshotOption`：total_long_position, auction_price, exercise_price, expire_date …
- `Kline`：code, kline_time, open/high/low/close, volume, amount

K 线算法：开盘集合竞价量并入当日第一根 K 线、收盘集合竞价并入最后一根；
`9:30` 的 1 分钟线覆盖 9:30:00.000–9:30:59.999。

## 8. 合规红线（免责声明要点）

1. 账号**仅供本人使用**，不得向任何第三人转移/出售/公开数据资料。
2. 同一账号多人同时登录可能被停止服务 → 桥接机应独占该账号。
3. 公司有权要求配合升级 SDK；权限到期即关闭。
4. 本桥接方案仅用于打通本人名下的局域网设备自用，**不可对外提供服务或转发数据**。

## 9. 对桥接方案的直接影响

| 发现 | 设计对策 |
|---|---|
| 仅 Win/Linux x64 二进制 | 桥接服务部署在 Windows x86 机器上 |
| `ad.login` 失败 `exit()` 杀进程 | 服务端直连 `tgw.Cfg/tgw.Login`，自管重试 |
| MarketData 依赖 calendar | 服务端登录后自动初始化并每日刷新 |
| 订阅回调模型阻塞 | 服务端统一持有订阅管线，WebSocket 广播给多个客户端，各客户端独立过滤 |
| local_path 参数 | 客户端无需关心，服务端统一替换为配置的缓存根目录 |
| 约 60 个查询接口 | 采用通用 `/call/{group}/{method}` 分发端点，天然覆盖全部现有及未来接口 |
