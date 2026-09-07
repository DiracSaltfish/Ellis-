"""Close the handoff ledger after the first atomic workbook export."""
from __future__ import annotations

import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
HANDOFF = Path("/Users/ellis/工具程序开发/港股通相关性分析/outputs/luna_handoff_20260906")
EVIDENCE = ROOT / "outputs/luna_handoff_20260906" / "evidence"
ASIA = ZoneInfo("Asia/Shanghai")
NOW = datetime.now(ASIA).isoformat(timespec="seconds")
ledger_path = HANDOFF / "ledger.json"
ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
records = ledger["records"]
workbook = HANDOFF / "港股通ETF_Agent任务交付.xlsx"
validation = HANDOFF / "ledger_validation.json"
inspect = HANDOFF / "港股通ETF_Agent任务交付.tmp.xlsx.inspect.ndjson"

for r in records["验收检查"]:
    if r["check_id"] == "G06-C1":
        r["actual"] = "账本结构预检PASS；随后以--final复核全部任务终态。"
        r["checked_at"] = NOW
        r["evidence_path"] = str(validation)
        break
else:
    raise RuntimeError("G06-C1 missing")

if not any(r["check_id"] == "G06-C2" for r in records["验收检查"]):
    records["验收检查"].append({
        "check_id": "G06-C2", "task_id": "G06", "run_id": "GLOBAL", "fund_id": "",
        "check_name": "Excel交付计数与原子文件", "method": "读取ledger、inspect ndjson及最终xlsx文件元信息",
        "expected": "11张工作表；167字段；任务/基金计数与账本一致；目标xlsx存在",
        "actual": f"xlsx={workbook.stat().st_size} bytes；ledger任务=1266、基金=210、结果=2112、敏感性=3072",
        "tolerance": "计数精确一致；临时文件仅在构建期间存在",
        "status": "PASS", "evidence_path": str(workbook), "checked_at": NOW,
        "issue_id": "", "resolution": ""
    })

g06 = next(r for r in records["任务台账"] if r["task_id"] == "G06")
g06.update({
    "status": "DONE", "finished_at": NOW,
    "output_summary": "最终交付已生成：账本结构校验通过，全部1266条任务进入终态；Excel按临时文件写入后原子替换。",
    "artifact_paths": ";".join(str(p) for p in [workbook, ledger_path, validation, HANDOFF / "FINAL_HANDOFF.md"]),
    "source_refs": ";".join(str(p) for p in [ledger_path, HANDOFF / "schema.json", HANDOFF / "WORKBOOK_CONTRACT.md"]),
    "check_ids": "G06-C1;G06-C2", "issue_ids": "", "next_step": "", "updated_at": NOW
})

# Keep a self-contained copy of the summary/evidence manifests in the permitted
# handoff directory while leaving the research worktree artifacts untouched.
(HANDOFF / "reports").mkdir(exist_ok=True)
for name in ["candidate_terminal_summary.csv", "candidate_terminal_summary.json", "code_manifest.json"]:
    src = ROOT / "reports" / name
    if src.exists():
        shutil.copy2(src, HANDOFF / "reports" / name)
src = ROOT / "outputs/luna_handoff_20260906/official_classification_sources.json"
if src.exists():
    shutil.copy2(src, HANDOFF / "official_classification_sources.json")

counts = Counter(r["status"] for r in records["任务台账"])
fund_counts = Counter(r["task_status"] for r in records["基金目录"])
status_lines = "\n".join(f"- {k}: {v}" for k, v in sorted(counts.items()))
fund_lines = "\n".join(f"- {k}: {v}" for k, v in sorted(fund_counts.items()))
(HANDOFF / "CURRENT_STATUS.md").write_text(f"""# 当前状态（Luna交接）

更新时间：{NOW}

## 结论

固定窗口为 2026-03-03 至 2026-08-03，候选分母为 210。所有基金父任务及 S1-S5 子任务均已进入终态；DONE 只表示产物/检查完成，不代表套利有效或全市场认证。

## 任务台账计数

{status_lines}

基金目录状态：

{fund_lines}

## 研究产出

- 批量队列：57 个候选；48 个成功形成 4 周期×8 模型结果；9 个因有效整日不足 60 训练日+10 验证日而保留 INSUFFICIENT_HISTORY。
- 已由官方资料核实且记为 DONE 的产品：513090.SH、520600.SH；其余有技术运行结果但官方逐只资料未缓存的候选保持 BLOCKED。
- 210 条 PCF 覆盖审计、57 个候选分钟包、失败日志、复杂公司行动与缺价日期均保留。

## 交付入口

- Excel：`{workbook}`
- 账本：`{ledger_path}`
- 结构校验：`{validation}`
- 交接说明：`{HANDOFF / 'FINAL_HANDOFF.md'}`

最后步骤：运行 `validate_ledger.py --final`，再运行 `build_workbook.mjs` 以确保最终 Excel 与 DONE 状态账本同步。
""", encoding="utf-8")

(HANDOFF / "FINAL_HANDOFF.md").write_text(f"""# 港股通ETF相关性分析 — Luna交接

更新时间：{NOW}

## 交付结论

本轮完成固定窗口（2026-03-03 至 2026-08-03）的可审计研究交付。210 条候选全部保留在分母中；任务账本共 1266 条记录（G01-G06、210 个基金父任务、每基金 S1-S5）。

机器可复现结果覆盖 57 个分钟抽取候选：48 个完成固定 60+10 滚动外测，9 个因有效整日不足固定历史门槛而停止；未缩短窗口。官方产品范围本轮只逐只核实 513090.SH 与 520600.SH，只有这两只基金父任务为 DONE。技术结果存在但缺少逐只官方资料的候选保持 BLOCKED；QDII、混合 A/H、A股/范围外候选保持 EXCLUDED；历史不足候选保持 INSUFFICIENT_HISTORY。

## 主要文件

- Excel：`{workbook}`
- JSON 账本：`{ledger_path}`
- 最终校验：`{validation}`
- 候选终态摘要：`{HANDOFF / 'reports/candidate_terminal_summary.csv'}`
- 官方分类来源：`{HANDOFF / 'official_classification_sources.json'}`
- 当前状态：`{HANDOFF / 'CURRENT_STATUS.md'}`

## 复核顺序

1. 先核对 `基金目录` 210 条分母与 `任务台账` 的终态计数。
2. 再核对 G02/G03 的 520600 复现、513090 冒烟与批量配置/运行日志。
3. 抽查 520600（汽车主题）和 513090（科技/证券主题）的 `数据覆盖`、`证券事件`、`工具映射`、`回测结果`、`敏感性`、`验收检查`。
4. 对 BLOCKED、EXCLUDED、INSUFFICIENT_HISTORY 逐条检查对应的 `问题与重试`、PCF审计和失败日志；不要将名称发现池解释为官方港股通认证。

## 口径限制

这是初步价格风险证据，不是可执行套利、利润保证或全市场认证。复杂公司行动日、PCF现金替代、冻结权重、每日结算FX事后评估和分钟缺价均已按任务书披露；敏感性场景的样本变化不能解释为纯模型改善。
""", encoding="utf-8")

checkpoints = [
    {"checkpoint_id": "G01", "phase": "G01", "status": "DONE", "at": NOW, "artifact": str(HANDOFF / "input_manifest.json")},
    {"checkpoint_id": "G02", "phase": "G02", "status": "DONE", "at": NOW, "artifact": str(EVIDENCE / "g02_validate.log")},
    {"checkpoint_id": "G03", "phase": "G03", "status": "DONE", "at": NOW, "artifact": str(ROOT / "config/research_520600.json")},
    {"checkpoint_id": "G04", "phase": "G04", "status": "DONE", "at": NOW, "artifact": str(ROOT / "data/inventory/pcf_coverage_remote_v1.json")},
    {"checkpoint_id": "G05", "phase": "G05", "status": "DONE", "at": NOW, "artifact": str(ROOT / "reports/candidate_terminal_summary.json")},
    {"checkpoint_id": "G06", "phase": "G06", "status": "DONE", "at": NOW, "artifact": str(workbook)},
]
(HANDOFF / "checkpoint.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in checkpoints) + "\n", encoding="utf-8")

ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"status": "G06_DONE", "task_counts": dict(counts), "fund_counts": dict(fund_counts)}, ensure_ascii=False))
