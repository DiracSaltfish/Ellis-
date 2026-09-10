# 197 只 ETF PCF 独立估值核验

目标日：2026-09-08。分析按 `WORKFLOW.md` 执行，未修改 SDK、站点或交易数据。

## 结论

- 输入 197 只，197/197 唯一；目标日 NAV、PCF 和代码/日期身份均覆盖。
- 单日严格通过 171 只，明确超出 5 bp 20 只，四位小数净值舍入边界 5 只，不适用 1 只。
- 上海 102 只只做 2026-09-08 单日：89 只通过，11 只明确超差，1 只边界，1 只不适用。上交所接口没有被用于上海历史 PCF 推断。
- 深市单日通过 82 只中，31 只五个共同交易日均严格通过，34 只五日不稳定，17 只因历史 PCF 部分可得。
- 513090：估值 `1.8432886185282`，公布单位净值 `1.8433`，误差 `-0.061745 bp`，严格通过。

## 估值口径

`(允许现金替代数量 × 港股不复权收盘价 × 0.86482 + 必选现金替代金额 + T 日估计现金) / 申赎单位`

严格条件为 `abs(error_bp) < 5`。公布单位净值按四位小数处理；只有整个 `±0.00005` 舍入区间仍在 5 bp 内才记为 `PASS_SINGLE_DAY`。深市 `159900` 申赎现金占位行排除，不与 T 日估计现金重复计算。T+1 最终现金没有用于主估值。

## 数据边界

- 港股成分去重后 646 只；目标日日线 643 只精确成交，00853、02172、02252 无 9/8 成交，27 只基金明确记录沿用 8/31 最后收盘价。
- 港股分钟数据 646 只中 455 只返回目标日、每只 332 条；170 只基金具备完整共同时间戳分钟证据。分钟 CSV 每行带 `minute_source_ref`，具体文件列表在 `outputs/intraday/chart_manifest.json` 和 `validation_summary.json` 中；513090 的 01788 分钟数据使用目录内既有控制证据。
- 深市历史 PCF 请求 380 次中 292 次成功、88 次被 CDN 返回 403；失败不填充、不冒充历史复验通过。
- `513800.SH` 为非港股底层（TOPIX ETF），本规则没有独立价格/汇率估值定义，标记 `NOT_APPLICABLE`。

## 源与哈希

原始响应没有放进报告 HTML，而是按交易所/数据源保存在 `raw/`，对应 URL、抓取状态、字节数和 SHA-256 在根目录的各个 `*_manifest.json` 中：NAV 使用 Eastmoney 历史净值接口；深市 PCF 使用 SZSE ETFDown XML；沪市目标日 PCF 使用 SSE `downloadETF2Bulletin.do`；港股日线和分钟价使用 Tencent 行情接口。报告源按钮引用 `outputs/report_data.sqlite`，逐基金/逐成分回溯入口仍保留在 CSV 与 manifest 中。

## 主要输出

- `outputs/report.html`：自包含 HTML 报告，主交付物。
- `outputs/watchlist_validation.csv`：197 只逐基金审计明细，每行含 PCF/NAV 路径、估值、误差、状态和缺口。
- `outputs/component_contributions_20260908.csv`：9/8 逐成分数量、估值方法、价格日期、金额和来源。
- `outputs/multi_day_validation.csv`：深市逐日复验明细；上海只保留目标日单日行。
- `outputs/intraday_iopv_20260908.csv`：分钟代理 IOPV 序列；公布净值只作参考线。
- `outputs/intraday/overview.svg`：控制、边界和偏差案例的分钟图总览。
- `outputs/intraday/plots/*.svg`：170 只具备完整共同时间戳证据的逐基金分钟图。
- `outputs/intraday/chart_manifest.json`：逐图代码、行数、公布净值、状态和对应数据 CSV。
- `outputs/quality_checks.json`：独立质量门禁结果，当前 `status = PASS`。
- `outputs/artifact.json`：报告的标准化 canonical artifact 输入。
- `outputs/report_data.sqlite`：报告卡片、图表和表格使用的本地只读证据库。

## 图表地图

- 单日状态分布：bar，回答 197 只目标日结果如何分布。
- 历史复验结果：bar，回答深市五日证据与沪市单日边界如何分开。
- 513090 分钟代理 IOPV：line，回答独立分钟估值是否沿着公布单位净值附近运行。
- 170 只基金的单基金 SVG：同一 line 口径的审计补充图；静态图只展示代理序列与公布净值参考线。

## 可复现运行

在本目录执行：

```bash
python3 scripts/validate_watchlist.py
python3 scripts/quality_checks.py
python3 scripts/plot_intraday.py
python3 scripts/build_artifact.py
```

报告用 Data Analytics portable builder 生成：

```bash
cd /Users/ellis/.codex/plugins/cache/openai-curated-remote/data-analytics/0.2.10-13ceeea1f599
npm run report:deliver -- \
  --input /Users/ellis/工具程序开发/513090误判分析/watchlist_20260908/outputs/artifact.json \
  --output /Users/ellis/工具程序开发/513090误判分析/watchlist_20260908/outputs/report.html
```

报告构建器返回 `validation=passed`、`package=passed`、`verification=structural_only`；当前环境没有 Chromium headless，因此自动浏览器交互和 source dialog 未执行，报告仍保留可读的语义表格回退。SVG 总览和 HTML 顶部阅读顺序已通过本机预览检查。
