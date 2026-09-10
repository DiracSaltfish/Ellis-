# 内网 IOPV：独立实时估值服务

已完成独立运行服务：C++17计算库、C++ TGW读取器、Go缓存与调度、逐分钟SQLite、REST/SSE、内网估值网页和macOS管理窗口。**服务已实际启动；五合一回接未实施。**

- [估值网页](http://127.0.0.1:18680/) · [内网访问](http://192.168.1.23:18680/) · [本机管理](http://127.0.0.1:18680/manage)
- 管理应用：`dist/内网IOPV管理.app`
- [运行、数据与验收说明](docs/运行与验收.md)
- 后台服务：`com.ellis.intranet-iopv`；关闭管理窗口不会停止采集。

现行通道规则已按用户确认固定：深市ETF采用深圳港股通汇率，沪市ETF采用上海港股通汇率。

## 复现与入口

需要 Go 1.23+、CGO_ENABLED=1；计算库为C++17，TGW读取器为C++20，依赖OpenSSL、simdjson和zstd；Go使用锁定版本的sqlite3驱动。

```sh
cd '/Users/ellis/工具程序开发/内网IOPV计算'
go test ./...
go test -race ./...
go test -run '^$' -bench BenchmarkBasketCore -benchmem
go run ./cmd/replay
go run ./cmd/pcf-probe -symbol 513090.SH -date 2026-09-09
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
ctest --test-dir build --output-on-failure
```

日期参数必须随实际交易日更新。上交所 URL 无历史日期参数；返回 PCF 的 TradingDay 必须与请求日期相等。PCF probe 只读取交易所接口、写入本目录 outputs；不订阅行情或连接交易程序。

- `core.h` / `core.cpp`：无 Qt 依赖的 C ABI，可由五合一 C++ 直接链接，也可由 Go cgo 调用。调用者拥有全部内存，不保留输入指针。
- `pcf.go` / `collector.go`：沪深 XML 适配、身份/日期/条数/现金检查、下载探针。
- `fx.go`：1navs v7 汇率 API 适配，明确通道和方向。
- `runtime.go`：估值输入快照、质量门禁、去重和三组调度。
- `minute.go`：09:30–16:08 时间规则、分钟记录结构和盘口溢价。
- `universe.json`：120 只首批估值候选及 77 只暂缓清单；不是生产启用清单。
- `outputs/replay.json`、`outputs/replay_summary.json`：实际 C++ 回放结果。
- `outputs/subscriptions.json`：750 个去重订阅和每组 ETF 清单。
- `docs/实施方案.md`：完整实施路径、图表、接口和接回五合一的设计。
- `docs/验证记录.md`：本轮测试结果与实际数据源验证。

## 计算口径

定义 H = Σ(港股股数 × 港元最新成交价)，A = Σ(人民币证券股数 × 人民币最新成交价)，C = 必须现金替代的人民币金额之和，E = 当日 PCF 预估现金，U = 最小申赎单位份数：

```text
中间价 IOPV             = (H × 当日 HKD/CNY 中间价 + A + C + E) / U
预估结算汇率 IOPV       = (H × 所选预估结算 HKD/CNY + A + C + E) / U
溢价率百分数             = (ETF价格 / 对应IOPV − 1) × 100
盘口每档溢价率           = (该档价格 / 对应IOPV − 1) × 100
两种IOPV的差             = H × (预估结算汇率 − 中间价) / U
```

HKD/CNY 单位统一为“人民币元/1港元”，不倒数。固定现金与预估现金不二次换汇；允许/退补现金替代行采用数量乘价格，不把申购保证金率当净资产加成。深市 159900、102 市场、零股数、flag=2、名称为“申赎现金”的占位行排除，避免重复计算。未知 flag、必须现金替代申赎金额不一致等情况不猜测，要求增加基金专项规则。股价不复权，份数是股数，不乘港股每手股数。

这里的“结算汇率 IOPV”是**替换换汇假设后的篮子情景估值**，不是基金公布 NAV，也不是一篮申赎最终应收应付。实际补券成交价、费用、税费、轧差及现金退补没有被这条公式完整覆盖。原质控只验证中间价模型，不能据此宣称结算方向模型已通过历史验证。

现按用户明确规则由上市交易所映射结算通道：SZ→shenzhen、SH→shanghai。买入港股取predicted_sell_settlement，卖出港股取predicted_buy_settlement；网页显示选定方向，API同时输出两方向。结算线仍是约定换汇假设下的情景估值，并不代表基金实际每笔补券最终结算。

## 实际核对结果

2026-09-08 的 120 只候选：沪市 89 只单日通过、深市 31 只五日通过。对原始 PCF 独立解析后经 C++ 复算，最大差异 2.22e-16 元/份。控制基金 513090 使用当日预估现金 9,547.19 元、50 万份、HKD/CNY 0.86482，得到 **1.8432886185282**，对公布四位净值 1.8433 的差约 **−0.061745 bp**。

这个数值一致性测试复用此前独立行情证据，不是新获取第二套行情源验证。22 只候选涉及此前使用旧收盘价的情况，详见回放 stale_components。上线需要当天 PCF 与实时全篮子覆盖重新过门禁。120 只仅表示本轮候选范围。

2026-09-09 网站 10:19:38 快照：中间价 **0.86418**；沪市 buy_hk 使用 predicted_sell_settlement **0.8557154630097473**；深市 buy_hk 使用 shenzhen_estimate.predicted_sell_settlement **0.855673863548989**。这是一份有时间戳的测试快照，不是当前最新汇率。原站 `actionable=false`、`reference_only`，深市模型仍待校准，原型保留这些信息。

## 证据入口

- 原任务：`197只ETF PCF估值核验（5bp）`，ID `01a081f5-9ef1-7a01-9947-6b0ee5ed5622`。
- 控制案例：`分析SH513090真实估值方法`，ID `01a081cc-c9f1-7f90-8c07-0185be67ec05`。其中早期使用最终现金的拟合只作事后核账，未用于本轮主估值。
- [交易所披露的公式与公允汇率调整公告（513980）](https://www.sse.com.cn/disclosure/fund/announcement/c/new/2025-03-06/513980_20250306_M1BI.pdf)：支持数量×成交价×汇率、固定现金、预估现金的分解；不是所有基金汇率均为 CFETS 的证明。
- [上交所港股通说明](https://www.sse.com.cn/services/hkexsc/)：参考汇率预冻结与结算概念应区分。
- [1navs 只读汇率 API](https://1navs.com/api/v1/private/hk-connect-fx)：实际取得 v7 JSON；字段与本地 `internal/hkconnectfx/service.go`、部署说明交叉核对。
- PCF 原始 URL、文件哈希、原质控日期保存在 `testdata/manifest.json`；证据导入来源及总表哈希见 `testdata/provenance.json`。

交易所发布 IOPV 的汇率口径应逐基金核对最新公告，不能统一断言所有产品均使用 CFETS。原型完全自行算篮子，交易所 IOPV 将来仅作为单独对照列。
