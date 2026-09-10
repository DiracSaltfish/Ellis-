# 513090 误判分析（2026-09-08）

主报告：report.html。逐分钟中间价 IOPV：minute_iopv_20260908.csv（332 行）；逐券明细：minute_constituents_20260908.csv（5,644 行）。CSV 使用 UTF-8 BOM，港股代码列应按文本导入以保留前导零。

## 离线复算

在本目录执行 `python3 scripts/compute_minute_iopv.py`。只读 raw 内固定的 9 月 8 日 PCF 与分钟行情；不依赖任何发布端 IOPV。使用 Decimal 计算并按四舍五入保留四位小数。精确值也保留在表内。

`reproduce.ipynb` 为复现入口；`scripts/analyze.py` 和 `scripts/extend_analysis.py` 提供多日 NAV、份额与条件结算模型。`scripts/package_chart.py` 运行独立 SQLite 聚合复算并与 Decimal 逐点比较，最大容许误差 1e-12。

全部报告生成顺序为：build_report.py → finalize_report.py → package_chart.py → table_sql_sources.py → Data Analytics 插件 portable builder。HTML 为由 artifact.json 生成的单文件报告。重新抓取行情可能取到别的交易日；主复算只使用已归档原始文件并校验日期。

公式：(Σ当日PCF数量×港股分钟价×0.86482 + 9547.19)/500000。

15:59 后数据源直接给出16:08收盘竞价最终点，没有独立16:00报价，未补造该记录。A股午间和15:00后ETF列留空。所有242个正常A股时段匹配样本为折价，这不是逐秒结论。

申赎测算中0.855441/0.855559来自用户截图预测值，不是已核实最终结算汇率；未知总申购、总赎回、各券实际执行比例及费用，故不报告已实现亏损。

本任务未修改网站或SDK，未部署，未调用任何交易接口。来源与QA边界见 source_notes.md。

独立分时叠加图：513090_20260908_分时价格与估值.png；可缩放版本同名.svg。重画命令：`.venv/bin/python scripts/plot_intraday.py`。图已检查；HTML通过结构校验，当前环境无Chromium，未完成其浏览器交互验收。
