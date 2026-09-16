# 当前预测范围：仅沪市港股通ETF

用户于2026-09-12明确：1开头深圳ETF有实时申赎数据，不需要预测；预测候选只保留5开头沪市ETF。必须先筛市场再做每日排名，不从旧结果里简单删深圳。

`replay.py`用已经计算好的逐笔因子重放353日，冻结模型不重训。历史训练集仍为原沪深样本，推理只包含沪市。`outputs/沪市回测与高置信度信号报告.md`和本目录CSV/工作簿为最新结果；旧混合范围报告仅作历史记录。

实验主候选：原沪市第一名分数≥0.95，整U卖出执行份额>整U买入执行份额，候选完整及L2质量通过。`signal_gate.py`只做否决、不递补，不接收当日真实净量或盘后满额信息。上日净申购库存仅作风险列，不作主门槛。未接入QMT或实际交易，未替换原默认模型。

重放在machome项目虚拟环境运行`replay.py`。诊断用`HK_ETF_SH_ONLY=1`依次运行`../confidence_diagnostics_v3/analyze.py`、`inventory_check.py`及`late_fx.py`；同步小型结果后运行`export_signals.py`和`write_report.py`。测试运行`python3 -m unittest discover -s factor_research/l2/sh_only_v3 -p test_gate.py`。

后续新增数据验证应保持当前候选定义冻结。15/15为探索性历史结果，不能视作已证明未来胜率≥90%。
