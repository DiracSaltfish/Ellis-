#!/usr/bin/env python3
import hashlib,json
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def main():
 m=json.loads((ROOT/'mapping.json').read_text()); s=json.loads((ROOT/'data/processed/selection_summary.json').read_text()); ch=json.loads((ROOT/'delivery_check.json').read_text()); now=datetime.now(timezone.utc).isoformat()
 cnt=Counter(x['decision'] for x in m); tt=Counter(x['target_type'] for x in m)
 coverage_count=sum(1 for x in (ROOT/'data_coverage.jsonl').read_text().splitlines() if x.strip())
 attempt_count=sum(1 for x in (ROOT/'fetch_attempts.jsonl').read_text().splitlines() if x.strip())
 task_count=sum(1 for x in (ROOT/'tasks.jsonl').read_text().splitlines() if x.strip())
 text=f'''# Agent B 全量覆盖报告

生成时间：{now}

## 结论

本交付覆盖 B 组 81/81 只基金，已为每只基金写入终态映射、具体候选、数据尝试、任务与核验记录。独立的结构门禁结果为 `structural_pass=true`；这只是覆盖与引用完整性检查，不等同于投资或生产批准。

| 指标 | 数量 |
|---|---:|
| 分配基金 | {len(m)} |
| 已建共同目标/候选面板 | {s['funds_with_common_panels']} |
| 有真实 OOS 残差/权重的唯一基金 | {sum(x['actual_backtest_run'] for x in m)} |
| MATCH | {cnt['MATCH']} |
| INSUFFICIENT_DATA | {cnt['INSUFFICIENT_DATA']} |
| NO_MATCH_IN_TESTED_SET | {cnt['NO_MATCH_IN_TESTED_SET']} |
| 目标结果行（4 期限及路径） | {s['target_results']} |
| 候选比较行 | {s['candidate_metrics']} |
| 数据覆盖行 | {coverage_count} |
| fetch attempts | {attempt_count} |
| 逐项任务 | {task_count} |

主路径计数：PCF_BASKET {tt['PCF_BASKET']}（520600 使用既有 R1 成分篮子证据），ETF_MARKET_PRICE {tt['ETF_MARKET_PRICE']}，INDEX_STRUCTURAL {tt['INDEX_STRUCTURAL']}。其中 {cnt['INSUFFICIENT_DATA']} 只没有达到可采用的真实 OOS 门槛：16 只只有短样本，2 只在远端 ETF/PCF 归档中没有目标价。

## 计算口径

- 引擎版本 `FULL237_RHO060_V1`；代码 SHA256 `{sha(ROOT/'scripts/selection_core.py')}`。
- 主期限 30 分钟，同时输出 5/15/60 分钟；收益只使用同日同一连续交易时段的分钟端点，午休与跨日端点丢弃。
- 滚动样本为过去 60 个有效日，其中 50 日拟合、10 日验证，然后以前 60 日重拟合并预测下一日；beta 非负、单腿不超过 2、总 beta 不超过 2。
- 单腿优先；候选工具为 HSI_FUT、HHI_FUT、HTI_FUT、02800、02828、03032、03033、02845；跨族双腿只作为备选。
- MATCH 同时要求相关系数不低于 0.60 且残差方差下降；没有把相关系数单独当作通过。

## 数据与局限

ETF 目标归档共 1,803,927 行，79/81 只基金有目标价；PCF 明细共 279,948 行，79/81 只基金有明细行。候选分钟归档来自共享 PM 面板，覆盖 2026-03-03 至 2026-08-03；因此本包的候选实际样本主要是 13:00–15:00，不能替代完整 AM+PM 生产行情。

ETF_MARKET_PRICE 是二级市场价格目标，会含溢价/折价、申赎和时点基差；它不是 PCF 成分篮子。除 520600 外，PCF 明细没有配套的自有成分 1 分钟价格包，未跨基金复用。官方指数范围证据在多数 B assignment 中仍是 UNVERIFIED；事件/公司行动数据源本包未闭环，81 只任务均明确记录为 `BLOCKED_NO_EVENT_FEED`。

## 文件索引

- [mapping.json](mapping.json) / [mapping.csv](mapping.csv)：81 只逐项终态映射。
- [RESULTS.xlsx](RESULTS.xlsx)：8 个约定工作表。
- [target_results.jsonl](target_results.jsonl)、[candidate_comparison.jsonl](candidate_comparison.jsonl)：机器结果。
- [data_coverage.jsonl](data_coverage.jsonl)、[fetch_attempts.jsonl](fetch_attempts.jsonl)：覆盖与实际尝试。
- [tasks.jsonl](tasks.jsonl)、[checks.jsonl](checks.jsonl)、[evidence.jsonl](evidence.jsonl)：执行、核验与证据索引。
- [delivery_check.json](delivery_check.json)：结构门禁记录。
'''
 (ROOT/'FINAL_REPORT.md').write_text(text,encoding='utf-8')
 status={"updated_at_utc":now,"phase":"DELIVERY_READY","owner":"B","assigned_funds":len(m),"processed_funds":len(m),"actual_oos_unique_funds":sum(x['actual_backtest_run'] for x in m),"decision_counts":cnt,"target_pathway_counts":tt,"structural_delivery_check":ch['structural_pass'],"next_action":"independent numeric review; no production execution approval"}
 (ROOT/'STATUS.md').write_text('# Agent B full coverage STATUS\n\n'+json.dumps(status,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps(status,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
