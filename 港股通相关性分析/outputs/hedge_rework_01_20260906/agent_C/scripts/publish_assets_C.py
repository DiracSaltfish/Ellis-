#!/usr/bin/env python3
"""Publish C-owned reusable raw/code findings without touching other groups."""
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/Users/ellis/工具程序开发/港股通相关性分析')
OUT = ROOT / 'outputs/hedge_rework_01_20260906/agent_C'


def sha(path: Path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    now = datetime.now(timezone.utc).isoformat()
    sample = OUT / 'data/industry_history_sample_20260825.jsonl.gz'
    attempts = OUT / 'data/fetch_attempts_sample_20260825.jsonl'
    fetcher = OUT / 'scripts/fetch_industry_history_C.py'
    full_raw = OUT / 'data/industry_history_bars.jsonl.gz'
    full_attempts = OUT / 'data/fetch_attempts_industry.jsonl'
    lock = OUT / 'selection_lock_R1.json'
    pipeline = OUT / 'scripts/run_r1_basket_research_C.py'
    tables = OUT / 'scripts/build_r1_tables_C.py'
    xlsx_builder = OUT / 'scripts/build_results_xlsx_r1_C.mjs'
    new_bundle = OUT / 'data/input_new_period_c_pcf_hk.jsonl.gz'
    old_520760 = ROOT / 'batch_archive_20260906/data/raw/candidates_v2/520760.jsonl.gz'
    workbook = OUT / 'RESULTS.xlsx'
    report = OUT / 'FINAL_REPORT.md'
    assets = [
        {'asset_id': 'C-CODE-TWS-STK-FETCHER-R1', 'owner': 'C', 'kind': 'CODE',
         'security_or_fund_ids': ['HBI_FUT', '03069', '03174'], 'date_start': None, 'date_end': None,
         'path': str(fetcher), 'sha256': sha(fetcher), 'schema_version': 'C_FETCHER_R1',
         'timestamp_semantics': 'IBKR historicalData 1-minute RTH bars; endpoint day 08:00 UTC',
         'quality_status': 'VERIFIED_PARAMETER_REPAIR',
         'known_gaps': ['one historical HBI day returned NOT_FOUND; rerun is possible with the same read-only method'], 'created_at_utc': now},
        {'asset_id': 'C-MINUTE-INDUSTRY-SAMPLE-20260825', 'owner': 'C', 'kind': 'MINUTE',
         'security_or_fund_ids': ['HBI_FUT', '03069', '03174'], 'date_start': '2026-08-25', 'date_end': '2026-08-25',
         'path': str(sample), 'sha256': sha(sample), 'schema_version': 'IBKR_BAR_1M_RTH_V1',
         'timestamp_semantics': 'epoch seconds; 1-minute RTH bars; TRADES; 09:30-16:00 Asia/Hong_Kong',
         'quality_status': 'PASS_SAMPLE_ONLY',
         'known_gaps': ['one trading day only; not a performance confirmation sample'], 'created_at_utc': now},
        {'asset_id': 'C-FETCH-ATTEMPTS-SAMPLE-20260825', 'owner': 'C', 'kind': 'MINUTE',
         'security_or_fund_ids': ['HBI_FUT', '03069', '03174'], 'date_start': '2026-08-25', 'date_end': '2026-08-25',
         'path': str(attempts), 'sha256': sha(attempts), 'schema_version': 'FETCH_ATTEMPT_V1',
         'timestamp_semantics': 'request and response audit; includeExpired_used is explicit',
         'quality_status': 'PASS', 'known_gaps': ['sample request only'], 'created_at_utc': now},
        {'asset_id': 'C-MINUTE-INDUSTRY-FULL-R1', 'owner': 'C', 'kind': 'MINUTE',
         'security_or_fund_ids': ['HBI_FUT', '03069', '03174'], 'date_start': '2026-03-03', 'date_end': '2026-09-04',
         'path': str(full_raw), 'sha256': sha(full_raw), 'schema_version': 'IBKR_BAR_1M_RTH_V1',
         'timestamp_semantics': 'epoch seconds; 1-minute RTH bars; TRADES; 09:30-16:00 Asia/Hong_Kong',
         'quality_status': 'PASS_WITH_ONE_HBI_NOT_FOUND_DAY',
         'known_gaps': ['HBI 2026-04-29 returned NOT_FOUND; attempts table retains the exact error'], 'created_at_utc': now},
        {'asset_id': 'C-FETCH-ATTEMPTS-INDUSTRY-FULL-R1', 'owner': 'C', 'kind': 'AUDIT',
         'security_or_fund_ids': ['HBI_FUT', '03069', '03174'], 'date_start': '2026-03-03', 'date_end': '2026-09-04',
         'path': str(full_attempts), 'sha256': sha(full_attempts), 'schema_version': 'FETCH_ATTEMPT_V1',
         'timestamp_semantics': 'one row per tool/day; status/error/includeExpired used/contract retained',
         'quality_status': 'PASS', 'known_gaps': ['one HBI day has no bars and is not silently filled'], 'created_at_utc': now},
        {'asset_id': 'C-R1-SELECTION-LOCK', 'owner': 'C', 'kind': 'CONFIG',
         'security_or_fund_ids': ['C_GROUP'], 'date_start': '2026-03-03', 'date_end': '2026-09-04',
         'path': str(lock), 'sha256': sha(lock), 'schema_version': 'R1_SELECTION_LOCK_V1',
         'timestamp_semantics': 'candidate pool and policy frozen before model scoring', 'quality_status': 'PASS',
         'known_gaps': ['confirmation requires >=20 new OOS days'], 'created_at_utc': now},
        {'asset_id': 'C-R1-PIPELINE', 'owner': 'C', 'kind': 'CODE',
         'security_or_fund_ids': ['C_GROUP'], 'date_start': '2026-03-03', 'date_end': '2026-09-04',
         'path': str(pipeline), 'sha256': sha(pipeline), 'schema_version': 'R1_BASKET_ENGINE_V1',
         'timestamp_semantics': 'PCF quantity × same-day HK marks; rolling 50/10 validation; OOS Pearson', 'quality_status': 'PASS',
         'known_gaps': ['new PCF/HK bundle is partial; no CN ETF secondary-market quote used in pure PCF basket'], 'created_at_utc': now},
        {'asset_id': 'C-R1-TABLE-BUILDER', 'owner': 'C', 'kind': 'CODE',
         'security_or_fund_ids': ['C_GROUP'], 'date_start': None, 'date_end': None, 'path': str(tables), 'sha256': sha(tables),
         'schema_version': 'R1_TABLES_V1', 'timestamp_semantics': 'base 11 tables plus four additions', 'quality_status': 'PASS',
         'known_gaps': ['event rows remain pending where full manifest is unavailable'], 'created_at_utc': now},
        {'asset_id': 'C-R1-XLSX-BUILDER', 'owner': 'C', 'kind': 'CODE',
         'security_or_fund_ids': ['C_GROUP'], 'date_start': None, 'date_end': None, 'path': str(xlsx_builder), 'sha256': sha(xlsx_builder),
         'schema_version': 'ARTIFACT_TOOL_XLSX_V1', 'timestamp_semantics': '16 sheets; rendered/inspected before export', 'quality_status': 'PASS',
         'known_gaps': ['machine-detail sheets are intentionally wide'], 'created_at_utc': now},
        {'asset_id': 'C-PCF-HK-NEW-BUNDLE-INPUT', 'owner': 'C', 'kind': 'PCF_MINUTE',
         'security_or_fund_ids': ['C_GROUP'], 'date_start': '2026-08-04', 'date_end': '2026-08-13', 'path': str(new_bundle),
         'sha256': sha(new_bundle), 'schema_version': 'PCF_HK_1M_INPUT_V1', 'timestamp_semantics': 'PCF daily snapshot plus HK 1-minute marks',
         'quality_status': 'PARTIAL', 'known_gaps': ['only five PCF dates in the available new-period input; not 20-day confirmation'], 'created_at_utc': now},
        {'asset_id': 'C-PCF-HK-520760-HISTORICAL-INPUT', 'owner': 'C', 'kind': 'PCF_MINUTE',
         'security_or_fund_ids': ['520760.SH'], 'date_start': '2026-03-03', 'date_end': '2026-08-03', 'path': str(old_520760),
         'sha256': sha(old_520760), 'schema_version': 'CANDIDATE_V2_INPUT_V1', 'timestamp_semantics': 'PCF daily snapshot plus HK 1-minute marks',
         'quality_status': 'REUSED_OR_UNPROVEN', 'known_gaps': ['historical input reused for research context; does not substitute for new confirmation'], 'created_at_utc': now},
        {'asset_id': 'C-R1-RESULTS-XLSX', 'owner': 'C', 'kind': 'WORKBOOK',
         'security_or_fund_ids': ['C_GROUP'], 'date_start': None, 'date_end': None, 'path': str(workbook), 'sha256': sha(workbook),
         'schema_version': 'R1_16_SHEET_XLSX_V1', 'timestamp_semantics': 'machine tables plus Reading Notes; rendered before export', 'quality_status': 'PASS',
         'known_gaps': ['research_status remains PARTIAL by design'], 'created_at_utc': now},
        {'asset_id': 'C-R1-FINAL-REPORT', 'owner': 'C', 'kind': 'REPORT',
         'security_or_fund_ids': ['C_GROUP'], 'date_start': None, 'date_end': None, 'path': str(report), 'sha256': sha(report),
         'schema_version': 'R1_REPORT_V1', 'timestamp_semantics': 'separate repair_status/research_status/decision_counts', 'quality_status': 'PASS',
         'known_gaps': ['event and new-confirmation gaps are explicitly described'], 'created_at_utc': now},
    ]
    (OUT / 'assets.json').write_text(json.dumps(assets, ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / 'SHARED_FINDINGS.md').write_text(
        f'# C组返工可复用资产与发现\n\n更新时间：{now}\n\n'
        '## 已修复并验证\n\n'
        '- `fetch_industry_history_C.py` 已永久保存，使用 clientId 参数、共享历史锁、断点续传、逐请求 attempts 记录和错误分类。\n'
        '- `includeExpired` 只对 `FUT` 设置为 `True`；`03069`/`03174` 的 `STK` 请求显式保持 `False`。\n'
        '- 全区间 2026-03-03—2026-09-04：402 次工具/日期尝试，401 SUCCESS、1 个 HBI 2026-04-29 NOT_FOUND、0 个321参数错误；STK 的 `includeExpired=false` 已在全区间审计。\n'
        '- TWS只调用合约详情与历史行情接口；没有订单、持仓、账户或实时订阅请求。\n\n'
        '- R1 520760 30分钟代表性 OOS：03069 单腿 rho≈0.939、残差方差降低≈88.0%；但新确认日只有5日，不能升级为确认。\n'
        '- 55只基金统一输出均把 `HEDGE_RETURN_CORRELATION_GE_060` 与 `RESIDUAL_VARIANCE_REDUCED` 作为研究门槛，并和QA分离。\n\n'
        '## 共享路径\n\n'
        f'- `assets.json`：{OUT / "assets.json"}\n'
        f'- STK/期货取数器：{fetcher}\n'
        f'- 样本K线：{sample}\n'
        f'- 样本取数尝试：{attempts}\n'
        f'- 全区间原始条：{full_raw}\n'
        f'- 全区间取数审计：{full_attempts}\n'
        f'- R1 锁定文件：{lock}\n'
        f'- R1 结果Excel：{OUT / "RESULTS.xlsx"}\n\n'
        '## 尚未完成\n\n'
        '- 新确认期可用PCF/HK日仍不足20日；因此本轮 research_status 保持 PARTIAL，55只均未升级为确认方案。\n'
        '- 需要补全每只基金的官方范围与PCF证券事件证据；当前不能把行业工具样本外表现写成确认结论。\n',
        encoding='utf-8')
    print(json.dumps({'assets': len(assets), 'assets_path': str(OUT / 'assets.json'), 'sha256': sha(OUT / 'assets.json')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
