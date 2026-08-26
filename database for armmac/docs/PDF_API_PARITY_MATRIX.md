# PDF 接口与结构对齐矩阵

更新时间：2026-08-26。中央矩阵由验收者维护；执行 Agent 只提交 `docs/evidence/*.md` 和拟议状态。

## 1. 原生 TGW 基础/订阅接口

| 接口 | PDF 页（正文页） | 关键结构/回调 | 模式 | 当前状态 | 下一步 |
|---|---:|---|---|---|---|
| GetVersion | 24（16） | 版本字符串 | 两者 | `INVENTORIED` | 对齐官方版本格式 |
| Init/Login | 24（16） | `Cfg`, `IGMDSpi`, `LogonResponse` | 两者 | `LIVE_ALIGNED(internet login)` | coloc 未覆盖；补完整 SPI |
| Release/Close | 25（17） | 连接和回调生命周期 | 两者 | `LIVE_ALIGNED(internet basic)` | 重连/资源压力测试 |
| FreeMemory | 25（17） | 所有回调数据所有权 | 两者 | `INVENTORIED` | Python/SPI 兼容策略 |
| GetTaskID | 25（17） | `int64_t` task id | 两者 | `ARM_IMPLEMENTED` | 与官方格式/并发唯一性对照 |
| UpdatePassWord | 25（17） | `UpdatePassWordReq` | 两者 | `INVENTORIED` | 单独授权后验证，禁止默认执行写操作 |
| Subscribe | 25–26（17–18） | `SubscribeItem`, 多种推送回调 | 两者 | `LIVE_ALIGNED(ETF L1 + HKT 02800 raw)` | 逐个验证其它公开 flag；HKT 类型化/delta 合并待实现 |
| UnSubscribe | 26（18） | `SubscribeItem` | 两者 | `LIVE_ALIGNED(ETF L1 only)` | 验证重复取消/关闭语义 |
| SubFactor | 26（18） | `SubFactorItem`, `OnFactor` | 两者 | `INVENTORIED` | Linux oracle + wire |
| UnSubFactor | 27（19） | `SubFactorItem` | 两者 | `INVENTORIED` | Linux oracle + wire |
| SubscribeDerivedData | 27–28（19–20） | `SubscribeDerivedDataItem`, order book | coloc only | `OUT_OF_SCOPE_COLOC` | 等 coloc 专项 |

已静态对齐结构：`ColocaCfg=22`、`Cfg=145`、`LogonResponse=14`、`SubscribeItem=42`（均为本地 pack(1) ctypes 大小）。实时输出结构还需逐回调对齐，不能由 request ABI 通过推导为已完成。

## 2. 原生 TGW 查询接口

| 接口 | PDF 页（正文页） | 关键请求/响应 | 模式 | 当前状态 | 下一步 |
|---|---:|---|---|---|---|
| QueryKline | 33–34（25–26） | `ReqKline`, `MDKLine` | 两者 | `LIVE_ALIGNED(daily + weekly + monthly only)` | 下一周期从季线开始，仍逐周期取证 |
| QuerySnapshot | 34（26） | `ReqDefault`, 多类 Snapshot SPI | 两者 | `WIRE_VERIFIED(SZSE ETF L1 sample; Arm CHANGES_REQUESTED)` | 拒绝未验证 `level_type`/品种；对齐 `kDataEmpty` 与异步 SPI 语义后复验 Mac live |
| QueryOrderQueue | 35（27） | `ReqDefault`, `MDOrderQueue` | coloc only | `OUT_OF_SCOPE_COLOC` | 等 coloc 专项 |
| QueryTickExecution | 35（27） | `ReqDefault`, tick execution | coloc only | `OUT_OF_SCOPE_COLOC` | 等 coloc 专项 |
| QueryTickOrder | 35（27） | `ReqDefault`, tick order | coloc only | `OUT_OF_SCOPE_COLOC` | 等 coloc 专项 |
| QueryCodeTable | 36（28） | `MDCodeTable`, code table SPI | 两者 | `STATIC_MATCHED(static contract only)` | 用异步 SPI 累积完整分包后做 Linux shape + wire |
| QuerySecuritiesInfo | 36（28） | `SubCodeTableItem` | 两者 | `INVENTORIED` | 单市场单代码 oracle |
| QueryExFactorTable | 36–37（28–29） | code, ex-factor SPI | 两者 | `INVENTORIED` | 单代码 oracle |
| QueryFactor | 37（29） | `ReqFactor`, `Factor` | 两者 | `INVENTORIED` | 确认权限后最小请求 |
| SetThirdInfoParam | 37–38（29–30） | task → key/value | 两者 | `WIRE_VERIFIED` | 已验证日历参数；保留逐功能号范围 |
| QueryThirdInfo | 38（30） | `ThirdInfoData` 嵌套 JSON | 两者 | `LIVE_ALIGNED(calendar function only)` | 逐 AmazingData 功能号验证 |
| QueryETFInfo | 39（31） | `SubCodeTableItem`, `MDETFCodeTableRecord`, ETF SPI | 两者 | `LIVE_ALIGNED(single SSE ETF only)` | 已闭合 SSE `510300` 同步单 item；下一步分别验证 SZSE、多 item、空结果/错误与异步 SPI |
| ReplayRequest | 45 起 | `ReqReplay`, history SPI | 文档专项 | `INVENTORIED` | 明确模式/服务后再排期 |

请求结构静态状态：`ReqKline=71`、`ReqDefault=55`。`ReqDefault` 的 V1.0.8 头文件比 PDF 多 `level_type:uint16_t=0`，所有 Agent 必须保留此差异证据。

## 3. AmazingData 高层接口

这些接口大多经 `QueryThirdInfo` 的不同 `function_id` 实现。底层 ThirdInfo 日历成功不等于所有高层函数已对齐；每个函数必须独立验证必填参数、列、类型、分页和空结果。

| 类别 | 接口（逐项任务） | PDF 页 | 当前状态 |
|---|---|---:|---|
| 会话 | `login`, `logout` | 11 | `LIVE_ALIGNED(internet basic)` |
| 会话写操作 | `update_password` | 11 | `INVENTORIED`；无单独授权不得执行 |
| 基础资料 | `get_code_info` | 11 | `INVENTORIED` |
| 基础资料 | `get_code_list` | 12 | `INVENTORIED` |
| 基础资料 | `get_future_code_list` | 13 | `INVENTORIED` |
| 基础资料 | `get_option_code_list` | 13 | `INVENTORIED` |
| 复权 | `BaseData.get_backward_factor` | 14 | `INVENTORIED` |
| 复权 | `BaseData.get_adj_factor` | 14 | `INVENTORIED` |
| 历史代码 | `BaseData.get_hist_code_list` | 15 | `INVENTORIED` |
| 日历 | `get_calendar` | 16 | `LIVE_ALIGNED(transport/function_id)`；高层 wrapper 待验 |
| 股票基础 | `get_stock_basic` | 16 | `INVENTORIED` |
| 历史状态 | `get_history_stock_status` | 17 | `INVENTORIED` |
| 北交所映射 | `get_bj_code_mapping` | 18 | `INVENTORIED` |
| 实时 | `onSnapshotindex` | 19 | `INVENTORIED` |
| 实时 | `onSnapshot` | 20 | `INVENTORIED` |
| 实时 | `onSnapshotglra` | 20 | `INVENTORIED` |
| 实时 | `onSnapshotfuture` | 21 | `INVENTORIED` |
| 实时 | `onSnapshotetf` | 22 | `LIVE_ALIGNED(raw wire)`；类型化对象待验 |
| 实时 | `onSnapshotkzz` | 22 | `INVENTORIED` |
| 实时 | `onSnapshothkt` | 23 | `INVENTORIED` |
| 实时 | `onSnapshotoption` | 24 | `INVENTORIED` |
| 实时 K 线 | `OnKLine` | 24 | `INVENTORIED` |
| 历史行情 | `query_snapshot` | 25–26 | `STATIC_MATCHED` |
| 历史行情 | `query_kline` | 26 | `LIVE_ALIGNED(daily + weekly + monthly low-level only)`；AD 高层 wrapper 未验 |
| 财务 | `get_balance_sheet` | 27–35 | `INVENTORIED` |
| 财务 | `get_cash_flow` | 36–44 | `INVENTORIED` |
| 财务 | `get_income` | 45–51 | `INVENTORIED` |
| 业绩 | `get_profit_express` | 52–54 | `INVENTORIED` |
| 业绩 | `get_profit_notice` | 55–57 | `INVENTORIED` |
| 股东 | `get_share_holder` | 57–58 | `INVENTORIED` |
| 股东 | `get_holder_num` | 58–59 | `INVENTORIED` |
| 股本 | `get_equity_structure` | 59–62 | `INVENTORIED` |
| 股本 | `get_equity_pledge_freeze` | 62–64 | `INVENTORIED` |
| 股本 | `get_equity_restricted` | 64–65 | `INVENTORIED` |
| 分红配股 | `get_dividend` | 65–67 | `INVENTORIED` |
| 分红配股 | `get_right_issue` | 67–68 | `INVENTORIED` |
| 两融 | `get_margin_summary` | 68–69 | `INVENTORIED` |
| 两融 | `get_margin_detail` | 69–71 | `INVENTORIED` |
| 交易公开信息 | `get_long_hu_bang` | 71–72 | `INVENTORIED` |
| 大宗交易 | `get_block_trading` | 72–73 | `INVENTORIED` |
| 期权 | `get_option_basic_info` | 73–75 | `INVENTORIED` |
| 期权 | `get_option_std_ctr_specs` | 75–76 | `INVENTORIED` |
| 期权 | `get_option_mon_ctr_specs` | 76–78 | `INVENTORIED` |
| ETF | `get_etf_pcf` | 78–82 | `INVENTORIED` |
| 基金 | `get_fund_share` | 82–83 | `INVENTORIED` |
| 基金 | `get_fund_nav` | 83–84 | `INVENTORIED` |
| 基金 | `get_fund_iopv` | 84–85 | `INVENTORIED` |
| 指数 | `get_index_constituent` | 85–86 | `INVENTORIED` |
| 指数 | `get_index_weight` | 86–88 | `INVENTORIED` |
| 行业 | `get_industry_base_info` | 88–89 | `INVENTORIED` |
| 行业 | `get_industry_constituent` | 89–90 | `INVENTORIED` |
| 行业 | `get_industry_weight` | 90–91 | `INVENTORIED` |
| 行业 | `get_industry_daily` | 91–92 | `INVENTORIED` |
| 可转债 | `get_kzz_issuance` | 92–96 | `INVENTORIED` |
| 可转债 | `get_kzz_share` | 96–98 | `INVENTORIED` |
| 可转债 | `get_kzz_conv` | 98–99 | `INVENTORIED` |
| 可转债 | `get_kzz_conv_change` | 99–101 | `INVENTORIED` |
| 可转债 | `get_kzz_corr` | 101–102 | `INVENTORIED` |
| 可转债 | `get_kzz_call` | 102–103 | `INVENTORIED` |
| 可转债 | `get_kzz_put` | 103–104 | `INVENTORIED` |
| 可转债 | `get_kzz_put_call_item` | 104–106 | `INVENTORIED` |
| 可转债 | `get_kzz_put_explanation` | 106–107 | `INVENTORIED` |
| 可转债 | `get_kzz_call_explanation` | 107–108 | `INVENTORIED` |
| 可转债 | `get_kzz_suspend` | 108–110 | `INVENTORIED` |
| 利率 | `get_treasury_yield` | 110 起 | `INVENTORIED` |

## 4. AmazingData 输出结构

| 结构 | PDF 页（正文页） | 当前状态 | 验收重点 |
|---|---:|---|---|
| `Snapshot` | 140–141（136–137） | `WIRE_VERIFIED(raw keys/delta)` | 5 档价格/量、IOPV、交易阶段、缩放和 delta 合并 |
| `SnapshotOption` | 141 起（137 起） | `INVENTORIED` | 持仓、结算、竞价字段及档位 |
| `SnapshotFuture` | 142（138） | `INVENTORIED` | 结算、持仓、期货代码和单位 |
| `SnapshotIndex` | 143（139） | `INVENTORIED` | 指数价格缩放为 1e6 |
| `SnapshotHKT` | 144（140） | `WIRE_VERIFIED(raw numeric keys/delta)` | 港股状态及市场特有字段；建立数字 key 映射与 delta 状态合并 |
| `Kline` | 145（141） | `LIVE_ALIGNED(daily + weekly + monthly low-level)` | 日期/时间、价格金额缩放；其它周期仍待验 |

## 5. 推荐领取顺序

1. `QuerySnapshot` 已有 SZSE ETF 子范围：补 `kDataEmpty`、同步/异步错误合约。
2. `QueryCodeTable`：用异步回调收齐批次，避免官方同步 wrapper 的首批竞态。
3. `QueryKline` 的单个未验证周期（季/年/分钟族）：每个 Agent 只领取一个周期。
4. `QueryETFInfo` 的 SZSE 单 ETF：作为独立市场子范围取证；之后才研究多 item/异步。
5. AmazingData `get_code_info/get_code_list`，再按类别推进其它 ThirdInfo 功能号。

写操作 `update_password` 和仅 coloc 接口不在默认队列中。
