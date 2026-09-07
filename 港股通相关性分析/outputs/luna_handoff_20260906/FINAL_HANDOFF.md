# 港股通ETF相关性分析 — Luna交接

更新时间：2026-09-06T16:59:05+08:00

## 交付结论

本轮完成固定窗口（2026-03-03 至 2026-08-03）的可审计研究交付。210 条候选全部保留在分母中；任务账本共 1266 条记录（G01-G06、210 个基金父任务、每基金 S1-S5）。

机器可复现结果覆盖 57 个分钟抽取候选：48 个完成固定 60+10 滚动外测，9 个因有效整日不足固定历史门槛而停止；未缩短窗口。官方产品范围本轮只逐只核实 513090.SH 与 520600.SH，只有这两只基金父任务为 DONE。技术结果存在但缺少逐只官方资料的候选保持 BLOCKED；QDII、混合 A/H、A股/范围外候选保持 EXCLUDED；历史不足候选保持 INSUFFICIENT_HISTORY。

## 主要文件

- Excel：`/Users/ellis/工具程序开发/港股通相关性分析/outputs/luna_handoff_20260906/港股通ETF_Agent任务交付.xlsx`
- JSON 账本：`/Users/ellis/工具程序开发/港股通相关性分析/outputs/luna_handoff_20260906/ledger.json`
- 最终校验：`/Users/ellis/工具程序开发/港股通相关性分析/outputs/luna_handoff_20260906/ledger_validation.json`
- 候选终态摘要：`/Users/ellis/工具程序开发/港股通相关性分析/outputs/luna_handoff_20260906/reports/candidate_terminal_summary.csv`
- 官方分类来源：`/Users/ellis/工具程序开发/港股通相关性分析/outputs/luna_handoff_20260906/official_classification_sources.json`
- 当前状态：`/Users/ellis/工具程序开发/港股通相关性分析/outputs/luna_handoff_20260906/CURRENT_STATUS.md`

## 复核顺序

1. 先核对 `基金目录` 210 条分母与 `任务台账` 的终态计数。
2. 再核对 G02/G03 的 520600 复现、513090 冒烟与批量配置/运行日志。
3. 抽查 520600（汽车主题）和 513090（科技/证券主题）的 `数据覆盖`、`证券事件`、`工具映射`、`回测结果`、`敏感性`、`验收检查`。
4. 对 BLOCKED、EXCLUDED、INSUFFICIENT_HISTORY 逐条检查对应的 `问题与重试`、PCF审计和失败日志；不要将名称发现池解释为官方港股通认证。

## 口径限制

这是初步价格风险证据，不是可执行套利、利润保证或全市场认证。复杂公司行动日、PCF现金替代、冻结权重、每日结算FX事后评估和分钟缺价均已按任务书披露；敏感性场景的样本变化不能解释为纯模型改善。最终 `validate_ledger.py --final` 已 PASS，最终 Excel 已原子重导出。
