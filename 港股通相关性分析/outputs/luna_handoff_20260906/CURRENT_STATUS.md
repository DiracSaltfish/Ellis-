# 当前状态（Luna交接）

更新时间：2026-09-06T16:59:05+08:00

## 结论

固定窗口为 2026-03-03 至 2026-08-03，候选分母为 210。所有基金父任务及 S1-S5 子任务均已进入终态；DONE 只表示产物/检查完成，不代表套利有效或全市场认证。

## 任务台账计数

- BLOCKED: 612
- DONE: 156
- EXCLUDED: 288
- INSUFFICIENT_HISTORY: 210

基金目录状态：

- BLOCKED: 125
- DONE: 2
- EXCLUDED: 48
- INSUFFICIENT_HISTORY: 35

## 研究产出

- 批量队列：57 个候选；48 个成功形成 4 周期×8 模型结果；9 个因有效整日不足 60 训练日+10 验证日而保留 INSUFFICIENT_HISTORY。
- 已由官方资料核实且记为 DONE 的产品：513090.SH、520600.SH；其余有技术运行结果但官方逐只资料未缓存的候选保持 BLOCKED。
- 210 条 PCF 覆盖审计、57 个候选分钟包、失败日志、复杂公司行动与缺价日期均保留。

## 交付入口

- Excel：`/Users/ellis/工具程序开发/港股通相关性分析/outputs/luna_handoff_20260906/港股通ETF_Agent任务交付.xlsx`
- 账本：`/Users/ellis/工具程序开发/港股通相关性分析/outputs/luna_handoff_20260906/ledger.json`
- 结构校验：`/Users/ellis/工具程序开发/港股通相关性分析/outputs/luna_handoff_20260906/ledger_validation.json`
- 交接说明：`/Users/ellis/工具程序开发/港股通相关性分析/outputs/luna_handoff_20260906/FINAL_HANDOFF.md`

最终步骤已完成：`validate_ledger.py --final` PASS，随后已用 `build_workbook.mjs` 原子重导出，Excel 与 DONE 状态账本同步。
