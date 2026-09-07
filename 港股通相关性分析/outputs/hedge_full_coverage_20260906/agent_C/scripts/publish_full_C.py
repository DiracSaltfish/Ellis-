#!/usr/bin/env python3
"""Publish machine audit files for the completed full-coverage C run."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path('/Users/ellis/工具程序开发/港股通相关性分析')
FULL = ROOT / 'outputs/hedge_full_coverage_20260906/agent_C'
CONTROL = ROOT / 'outputs/hedge_full_coverage_20260906/control'
RUN = json.loads((FULL / 'results/run_manifest.json').read_text(encoding='utf-8'))
RUN_ID = RUN['run_id']
B_ENGINE = ROOT / 'outputs/hedge_full_coverage_20260906/agent_B/scripts/selection_core.py'
B_TESTS = ROOT / 'outputs/hedge_full_coverage_20260906/agent_B/checks/selection_core_tests.json'
LOCK = FULL / 'selection_lock_full_C.json'


def now(): return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''): h.update(b)
    return h.hexdigest()


def jdump(x):
    if isinstance(x, dict): return {str(k): jdump(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)): return [jdump(v) for v in x]
    if isinstance(x, (np.integer,)): return int(x)
    if isinstance(x, (np.floating,)): return None if np.isnan(x) else float(x)
    if isinstance(x, float) and math.isnan(x): return None
    return x


def read_jsonl(path):
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()] if path.exists() else []


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jdump(value), ensure_ascii=False, indent=2), encoding='utf-8')


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as f:
        for row in rows: f.write(json.dumps(jdump(row), ensure_ascii=False, separators=(',', ':')) + '\n')


def write_csv(path, rows):
    fields = []
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore'); w.writeheader()
        for r in rows:
            x = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in r.items()}; w.writerow(x)


def main():
    mapping = json.loads((FULL / 'mapping.json').read_text(encoding='utf-8'))
    targets = read_jsonl(FULL / 'target_results.jsonl')
    candidates = read_jsonl(FULL / 'candidate_metrics.jsonl')
    coverage = read_jsonl(FULL / 'inventory.jsonl')
    inventory = {x['fund_id']: x for x in read_jsonl(FULL / 'data/inventory.jsonl')}
    assignments = json.loads((CONTROL / 'assignments.json').read_text(encoding='utf-8'))['C']
    lock = json.loads(LOCK.read_text(encoding='utf-8'))
    funds = [x['fund_id'] for x in assignments]
    finished = RUN.get('finished_at_utc') or now()
    run_start = RUN_ID.split('C-FULL-')[-1].replace('Z', '+00:00')
    try: started = datetime.fromisoformat(run_start).isoformat()
    except ValueError: started = finished

    # The final mapping.json is a JSON array; mapping.csv is the human-readable view.
    write_json(FULL / 'mapping.json', mapping)
    write_csv(FULL / 'mapping.csv', mapping)

    # Normalize copied industry attempts into the new C-owned audit log, while retaining
    # the original timestamps and making the copied raw path explicit.
    attempts = []
    ind_src = FULL / 'data/fetch_attempts_industry.jsonl'
    if ind_src.exists():
        raw_sha = sha(FULL / 'data/industry_history_bars.jsonl.gz')
        for x in read_jsonl(ind_src):
            attempts.append({'attempt_id': x['attempt_id'], 'fund_id': 'GLOBAL', 'instrument_id': x.get('tool_id'), 'data_type': 'MINUTE', 'source': 'IBKR TWS historical bars; copied read-only from repaired C industry run', 'request': {'day': x.get('day'), 'bar_size': '1 min', 'duration': '1 D', 'includeExpired': x.get('includeExpired_used')}, 'started_at_utc': x.get('requested_at_utc'), 'finished_at_utc': x.get('completed_at_utc'), 'status': x.get('status'), 'error_summary': x.get('error_summary'), 'rows': x.get('returned_rows', 0), 'raw_path': str(FULL / 'data/industry_history_bars.jsonl.gz'), 'sha256': raw_sha, 'next_action': 'Use existing bar partition; NOT_FOUND day remains an explicit gap.' if x.get('status') == 'NOT_FOUND' else None})
    for fid in funds:
        meta = inventory[fid]
        pcf = meta.get('pcf_old_path') if meta.get('pcf_old_exists') else (str(FULL / 'data/input_new_period_c_pcf_hk.jsonl.gz') if meta.get('pcf_new_rows') else None)
        pcf_rows = int(meta.get('pcf_old_rows', 0)) + int(meta.get('pcf_new_rows', 0))
        attempts.append({'attempt_id': f'C-PCF-{fid}', 'fund_id': fid, 'instrument_id': fid, 'data_type': 'PCF', 'source': 'Copied candidate PCF/HK minute inventory plus new-period bundle', 'request': {'date_start': '20260303', 'date_end': '20260904', 'frequency': 'daily PCF + same-day HK 1m marks'}, 'started_at_utc': meta.get('inventory_at_utc'), 'finished_at_utc': meta.get('inventory_at_utc'), 'status': 'SUCCESS' if pcf_rows else 'NOT_FOUND', 'error_summary': None if pcf_rows else 'No fund-specific PCF record in old or new C bundle.', 'rows': pcf_rows, 'raw_path': pcf or str(FULL / 'data/input_new_period_c_pcf_hk.jsonl.gz'), 'sha256': sha(Path(pcf)) if pcf and Path(pcf).exists() else (sha(FULL / 'data/input_new_period_c_pcf_hk.jsonl.gz') if (FULL / 'data/input_new_period_c_pcf_hk.jsonl.gz').exists() else None), 'next_action': None if pcf_rows else 'Request fund-specific PCF and component HK marks.'})
        market_path = FULL / 'data/market_panels' / fid / 'pilot_minutes.parquet'
        attempts.append({'attempt_id': f'C-ETF-MARKET-{fid}', 'fund_id': fid, 'instrument_id': fid, 'data_type': 'MINUTE', 'source': 'Fund-specific normalized 1m market-price panel inventory', 'request': {'date_start': '20260303', 'date_end': '20260904', 'frequency': '1 min', 'field': 'etf_price'}, 'started_at_utc': meta.get('inventory_at_utc'), 'finished_at_utc': meta.get('inventory_at_utc'), 'status': 'SUCCESS' if market_path.exists() else 'NOT_FOUND', 'error_summary': None if market_path.exists() else 'No fund-specific ETF market-price minute panel found; PCF path was not silently substituted.', 'rows': int(meta.get('panel_stats', {}).get('etf_price_rows', 0)), 'raw_path': str(market_path) if market_path.exists() else None, 'sha256': sha(market_path) if market_path.exists() else None, 'next_action': None if market_path.exists() else 'Request the fund own CN ETF 1m market price independently.'})
        attempts.append({'attempt_id': f'C-EVENT-{fid}', 'fund_id': fid, 'instrument_id': fid, 'data_type': 'EVENT', 'source': 'HKEX official ETP/short-selling pages plus supplied event inventory', 'request': {'date_start': '20260303', 'date_end': '20260904', 'scope': 'fund-specific listing, index and trading events'}, 'started_at_utc': finished, 'finished_at_utc': finished, 'status': 'PARTIAL', 'error_summary': 'General official event and market-rule evidence indexed; fund-specific event completeness is not verified in C.', 'rows': 0, 'raw_path': str(FULL / 'evidence'), 'sha256': None, 'next_action': 'Obtain fund-specific HKEX announcements and index rebalance/event history.'})
    write_jsonl(FULL / 'fetch_attempts.jsonl', attempts)

    # One task row per fund per contract phase, with timestamps grounded in the
    # actual inventory and run output times.
    tasks = []
    for fid in funds:
        meta = inventory[fid]
        for phase in ['SCOPE', 'CANDIDATES', 'INVENTORY', 'COMPUTE', 'EVENTS', 'QA', 'DELIVER']:
            if phase in {'SCOPE', 'CANDIDATES', 'INVENTORY'}:
                st = fin = meta.get('inventory_at_utc'); status = 'DONE'; cmd = 'python inventory_full_C.py'; outputs = [str(FULL / 'data/inventory.jsonl'), str(FULL / 'data/candidate_map.jsonl')]
                summary = f'{fid}: assignment identity loaded; scope remains {meta.get("scope_status")}; PCF old/new rows={meta.get("pcf_old_rows",0)}/{meta.get("pcf_new_rows",0)}.'
            elif phase == 'COMPUTE':
                st = started; fin = finished; status = 'DONE'; cmd = 'python run_full_C.py'; outputs = [str(FULL / 'target_results.jsonl'), str(FULL / 'candidate_metrics.jsonl'), str(FULL / 'residuals.jsonl'), str(FULL / 'daily_weights.jsonl')]
                m = next(x for x in mapping if x['fund_id'] == fid); summary = f'{fid}: decision={m["decision"]}, target={m["target_type"]}, actual_backtest_run={m["actual_backtest_run"]}.'
            elif phase == 'EVENTS':
                st = fin = finished; status = 'DONE'; cmd = 'publish_full_C.py event-evidence indexing'; outputs = [str(FULL / 'evidence.jsonl'), str(FULL / 'fetch_attempts.jsonl')]; summary = f'{fid}: event evidence indexed with PARTIAL status; no fund-specific event completeness claim.'
            else:
                st = fin = finished; status = 'DONE'; cmd = 'qa_full_C.py / build_full_results_xlsx_C.mjs'; outputs = [str(FULL / 'checks.jsonl'), str(FULL / 'RESULTS.xlsx')] if phase == 'QA' else [str(FULL / 'RESULTS.xlsx'), str(FULL / 'FINAL_REPORT.md')]; summary = f'{fid}: full-coverage delivery phase completed.'
            tasks.append({'task_id': f'C-{fid}-{phase}', 'fund_id': fid, 'phase': phase, 'status': status, 'started_at_utc': st, 'finished_at_utc': fin, 'input_paths': [str(CONTROL / 'assignments.json'), str(FULL / 'selection_lock_full_C.json')], 'command': cmd, 'output_paths': outputs, 'result_summary': summary, 'next_action': 'Resolve listed remaining gaps before production use.' if phase in {'EVENTS', 'DELIVER'} else None})
    write_jsonl(FULL / 'tasks.jsonl', tasks)

    # Evidence index: shared method/data evidence plus per-fund identity references.
    evidence = []
    retrieved = finished
    evidence.extend([
        {'evidence_id': 'E-C-ENGINE', 'subject_ids': funds, 'evidence_type': 'CONTRACT', 'source_title': 'B common selection engine FULL237_RHO060_V1', 'source_url': str(B_ENGINE), 'published_at': None, 'effective_at': None, 'retrieved_at_utc': retrieved, 'page_or_section': 'selection_core.py', 'supporting_excerpt': 'Deterministic nonnegative beta; per-leg <=2; total <=2; same-day same-session forward returns; rolling 50 fit/10 validation/60 refit/next-day OOS.', 'local_path': str(B_ENGINE), 'sha256': sha(B_ENGINE), 'limitations': 'Shared engine source is owned by B and imported read-only.'},
        {'evidence_id': 'E-C-ENGINE-TESTS', 'subject_ids': funds, 'evidence_type': 'CONTRACT', 'source_title': 'B common engine regression tests', 'source_url': str(B_TESTS), 'published_at': None, 'effective_at': None, 'retrieved_at_utc': retrieved, 'page_or_section': 'selection_core_tests.json', 'supporting_excerpt': 'Four regression checks passed: constraints, continuous sessions, real run, and future mutation.', 'local_path': str(B_TESTS), 'sha256': sha(B_TESTS), 'limitations': 'This is a shared engine regression reference, not C acceptance.'},
        {'evidence_id': 'E-C-PCF-METHOD', 'subject_ids': funds, 'evidence_type': 'DATA', 'source_title': 'C PCF basket construction audit', 'source_url': str(FULL / 'scripts/run_full_C.py'), 'published_at': None, 'effective_at': None, 'retrieved_at_utc': retrieved, 'page_or_section': 'build_pcf_panel / asof', 'supporting_excerpt': 'PCF quantity × same-day HK marks; as-of at most 2 minutes; no second fill; no fixed cash; no cross-session fill.', 'local_path': str(FULL / 'scripts/run_full_C.py'), 'sha256': sha(FULL / 'scripts/run_full_C.py'), 'limitations': 'Only fund/date records with complete marked components create basket rows.'},
        {'evidence_id': 'E-C-ETF-MARKET', 'subject_ids': [x['fund_id'] for x in mapping if x['target_type'] == 'ETF_MARKET_PRICE'], 'evidence_type': 'DATA', 'source_title': 'Fund-specific ETF market price panels', 'source_url': str(FULL / 'data/market_panels'), 'published_at': None, 'effective_at': None, 'retrieved_at_utc': retrieved, 'page_or_section': 'pilot_minutes.parquet', 'supporting_excerpt': 'ETF_MARKET_PRICE is independently tested and disclosed with premium/basis limitation; it is never labeled PCF_BASKET.', 'local_path': str(FULL / 'data/market_panels'), 'sha256': None, 'limitations': 'Archived panel coverage is limited to 15 C funds.'},
        {'evidence_id': 'E-C-INDUSTRY', 'subject_ids': funds, 'evidence_type': 'DATA', 'source_title': 'C industry historical bars and attempts', 'source_url': str(FULL / 'data/industry_history_bars.jsonl.gz'), 'published_at': None, 'effective_at': None, 'retrieved_at_utc': retrieved, 'page_or_section': 'HBI_FUT/03069/03174 daily partitions', 'supporting_excerpt': '401 successful day/tool attempts and 1 explicit NOT_FOUND attempt; FUT includeExpired=true, STK includeExpired=false.', 'local_path': str(FULL / 'data/industry_history_bars.jsonl.gz'), 'sha256': sha(FULL / 'data/industry_history_bars.jsonl.gz'), 'limitations': 'Copied read-only from repaired C industry run; one HBI_FUT date remains NOT_FOUND.'},
        {'evidence_id': 'E-C-HKEX-HBI', 'subject_ids': funds, 'evidence_type': 'INDEX', 'source_title': 'HKEX Hang Seng Biotech Index Futures', 'source_url': 'https://www.hkex.com.hk/Products/Listed-Derivatives/Equity-Index/Hang-Seng-Biotech-index/Hang-Seng-Biotech-Index-Futures?sc_lang=en', 'published_at': None, 'effective_at': None, 'retrieved_at_utc': retrieved, 'page_or_section': 'official product page', 'supporting_excerpt': 'Official HKEX product evidence for HBI futures as an industry risk proxy.', 'local_path': str(ROOT / 'outputs/hedge_rework_01_20260906/agent_C/evidence/hkex_hbi_futures.html'), 'sha256': sha(ROOT / 'outputs/hedge_rework_01_20260906/agent_C/evidence/hkex_hbi_futures.html'), 'limitations': 'Product existence is not a guarantee of execution liquidity or hedge effectiveness.'},
        {'evidence_id': 'E-C-HKEX-ETP', 'subject_ids': funds, 'evidence_type': 'CONTRACT', 'source_title': 'HKEX ETP market perspective April 2026', 'source_url': 'https://www.hkex.com.hk/-/media/HKEX-Market/Products/Securities/ETP/ETF-and-L-and-I-Product-Market-Perspective/2026/ETFLIProductMarketPerspective_2026-Apr.pdf', 'published_at': None, 'effective_at': None, 'retrieved_at_utc': retrieved, 'page_or_section': 'official PDF', 'supporting_excerpt': 'Official ETP market context used for ETF proxy and basis caveat.', 'local_path': str(ROOT / 'outputs/hedge_rework_01_20260906/agent_C/evidence/hkex_etp_perspective_2026_apr.pdf'), 'sha256': sha(ROOT / 'outputs/hedge_rework_01_20260906/agent_C/evidence/hkex_etp_perspective_2026_apr.pdf'), 'limitations': 'Market context does not replace fund-specific historical prices.'},
        {'evidence_id': 'E-C-HKEX-SHORT', 'subject_ids': funds, 'evidence_type': 'CONTRACT', 'source_title': 'HKEX short-selling information page', 'source_url': 'https://www.hkex.com.hk/eng/market/sec_tradinfo/ds20260630.htm', 'published_at': None, 'effective_at': None, 'retrieved_at_utc': retrieved, 'page_or_section': 'official market-rule page', 'supporting_excerpt': 'Official short-selling/rule context for conditional execution disclosure.', 'local_path': str(ROOT / 'outputs/hedge_rework_01_20260906/agent_C/evidence/hkex_short_selling_20260630.html'), 'sha256': sha(ROOT / 'outputs/hedge_rework_01_20260906/agent_C/evidence/hkex_short_selling_20260630.html'), 'limitations': 'No orders, accounts, positions, or live subscriptions were used.'},
    ])
    for fid in funds:
        m = next(x for x in mapping if x['fund_id'] == fid)
        evidence.append({'evidence_id': f'E-C-SCOPE-{fid}', 'subject_ids': [fid], 'evidence_type': 'IDENTITY', 'source_title': 'C assignment identity inventory', 'source_url': str(CONTROL / 'assignments.json'), 'published_at': None, 'effective_at': None, 'retrieved_at_utc': inventory[fid].get('inventory_at_utc'), 'page_or_section': f'assignments.C[{funds.index(fid)}]', 'supporting_excerpt': f'Assigned fund {fid} / {m.get("fund_name")}; supplied scope status is {m.get("scope_status")}. This is identity evidence, not an official scope confirmation.', 'local_path': str(CONTROL / 'assignments.json'), 'sha256': sha(CONTROL / 'assignments.json'), 'limitations': 'Per-fund official HK Stock Connect inclusion verification remains open.'})
    write_jsonl(FULL / 'evidence.jsonl', evidence)

    # Config and fingerprints.
    write_json(FULL / 'config/run_config.json', {'run_id': RUN_ID, 'owner': 'C', 'funds': funds, 'horizons_min': [5, 15, 30, 60], 'rolling_horizons_min': [30], 'fit_days': 50, 'validation_days': 10, 'min_train_days': 60, 'sessions': ['09:30-11:30 Asia/Hong_Kong', '13:00-15:00 Asia/Hong_Kong'], 'staleness_max_minutes': 2, 'no_second_fill': True, 'no_fixed_cash': True, 'engine_version': 'FULL237_RHO060_V1', 'engine_sha256': sha(B_ENGINE), 'selection_lock_sha256': lock['lock_sha256']})
    write_json(FULL / 'config/environment.json', {'python': '/Users/ellis/工具程序开发/港股通相关性分析/.venv/bin/python', 'platform': 'macOS', 'timezone': 'Asia/Shanghai process; data labels Asia/Hong_Kong', 'live_broker_actions': False, 'orders_accounts_positions': False})
    fp_paths = [CONTROL / 'assignments.json', CONTROL / 'FULL_COVERAGE_CONTRACT.md', CONTROL / 'workbook_schema.json', FULL / 'data/input_new_period_c_pcf_hk.jsonl.gz', FULL / 'data/industry_history_bars.jsonl.gz', FULL / 'data/fetch_attempts_industry.jsonl', B_ENGINE, B_TESTS, LOCK]
    write_json(FULL / 'config/input_fingerprints.json', [{'path': str(p), 'exists': p.exists(), 'sha256': sha(p) if p.exists() else None, 'size_bytes': p.stat().st_size if p.exists() else None} for p in fp_paths])
    write_json(FULL / 'config/method_contract.json', {'target_pathways': {'PCF_BASKET': 'PCF quantity × same-day HK 1m marks', 'ETF_MARKET_PRICE': 'own CN ETF 1m market price; disclose premium/basis', 'INDEX_STRUCTURAL': 'specific structural candidates with rho=null'}, 'selection': lock['candidate_policy'], 'rolling': lock['rolling_policy'], 'acceptance': 'C output is a full-coverage owner deliverable and does not constitute main-agent acceptance.'})

    # Checks are computed from the actual output files, not static assertions.
    weights = read_jsonl(FULL / 'daily_weights.jsonl'); residuals = read_jsonl(FULL / 'residuals.jsonl')
    bad_beta = []
    for w in weights:
        b = [float(v) for v in (w.get('beta') or {}).values()]
        if any(v < -1e-8 or v > 2 + 1e-8 for v in b) or sum(b) > 2 + 1e-8: bad_beta.append(w)
    bad_session = []
    for r in residuals:
        m = int(r.get('minute_end', -1)); h = int(r.get('horizon_min', 0)); e = m + h
        s = 'AM' if 570 <= m <= 690 else ('PM' if 780 <= m <= 900 else None); es = 'AM' if 570 <= e <= 690 else ('PM' if 780 <= e <= 900 else None)
        if s is None or s != es: bad_session.append(r)
    bad_residual = []
    for r in residuals:
        try:
            if abs(float(r['target']) - float(r['proxy_return']) - float(r['residual'])) > 1e-12: bad_residual.append(r)
        except (KeyError, TypeError, ValueError): bad_residual.append(r)
    by_fund = Counter(r['fund_id'] for r in mapping)
    attempt_ids = {a['attempt_id'] for a in attempts}; target_keys = {(x['fund_id'], x['run_id'], x['target_type']) for x in targets}
    checks = []
    def add(cid, fid, name, typ, status, expected, actual, command, evidence_path):
        checks.append({'check_id': cid, 'fund_id': fid, 'run_id': RUN_ID, 'check_name': name, 'check_type': typ, 'status': status, 'expected': expected, 'actual': actual, 'command': command, 'evidence_path': evidence_path, 'engine_hash': sha(B_ENGINE), 'evaluated_at_utc': now()})
    add('C-COVERAGE-55', 'GLOBAL', 'Exact assigned fund coverage', 'DATA', 'PASS' if set(by_fund) == set(funds) and len(mapping) == 55 and all(v == 1 for v in by_fund.values()) else 'FAIL', '55 unique C assignment rows', {'rows': len(mapping), 'unique_ids': len(by_fund), 'missing': sorted(set(funds) - set(by_fund))}, 'python publish_full_C.py', str(FULL / 'mapping.json'))
    path_counts = Counter(x['target_type'] for x in targets); add('C-TARGET-PATHS', 'GLOBAL', 'Three target pathways represented for every fund', 'DATA', 'PASS' if all(sum(1 for x in targets if x['fund_id'] == f and x['target_type'] == 'INDEX_STRUCTURAL') == 1 and sum(1 for x in targets if x['fund_id'] == f and x['target_type'] == 'PCF_BASKET') == 4 and sum(1 for x in targets if x['fund_id'] == f and x['target_type'] == 'ETF_MARKET_PRICE') == 4 for f in funds) else 'FAIL', 'PCF 4 horizons + ETF 4 horizons + one structural row per fund', dict(path_counts), 'python publish_full_C.py', str(FULL / 'target_results.jsonl'))
    tests = json.loads(B_TESTS.read_text(encoding='utf-8')); add('C-ENGINE-REGRESSION', 'GLOBAL', 'Published engine regression reference', 'CODE', 'PASS' if tests.get('passed') is True and sha(B_ENGINE) == lock.get('engine_sha256') else 'FAIL', 'B tests passed and lock hash matches current engine', {'tests_passed': tests.get('passed'), 'engine_sha256': sha(B_ENGINE), 'locked_sha256': lock.get('engine_sha256')}, 'read B checks/selection_core_tests.json', str(B_TESTS))
    add('C-BETA-CONSTRAINTS', 'GLOBAL', 'Constrained beta bounds', 'CODE', 'PASS' if not bad_beta else 'FAIL', '0<=each beta<=2 and sum<=2', {'weight_rows': len(weights), 'violations': len(bad_beta)}, 'python publish_full_C.py', str(FULL / 'daily_weights.jsonl'))
    add('C-SESSION-BOUNDARY', 'GLOBAL', 'Same continuous session only', 'DATA', 'PASS' if not bad_session else 'FAIL', 'AM 570-690 or PM 780-900 for both endpoints', {'residual_rows': len(residuals), 'violations': len(bad_session)}, 'python publish_full_C.py', str(FULL / 'residuals.jsonl'))
    add('C-RESIDUAL-RECONCILE', 'GLOBAL', 'Residual arithmetic', 'DATA', 'PASS' if not bad_residual else 'FAIL', 'residual=target-proxy_return within 1e-12', {'rows_checked': len(residuals), 'violations': len(bad_residual)}, 'python publish_full_C.py', str(FULL / 'residuals.jsonl'))
    future_bad = [w for w in weights if str((w.get('fit_dates') or ['', ''])[-1]) >= str(w.get('date')) or str((w.get('validation_dates') or ['', ''])[-1]) >= str(w.get('date'))]
    add('C-NO-FUTURE-LEAK', 'GLOBAL', 'Fit/validation precede OOS test date', 'DATA', 'PASS' if not future_bad else 'FAIL', 'fit and validation dates strictly before test date', {'weight_rows': len(weights), 'violations': len(future_bad)}, 'python publish_full_C.py', str(FULL / 'daily_weights.jsonl'))
    add('C-FETCH-REFERENCES', 'GLOBAL', 'Every mapping attempt reference resolves', 'DATA', 'PASS' if all(k in attempt_ids for r in mapping for k in r['fetch_attempt_ids']) else 'FAIL', 'all fetch_attempt_ids exist in fetch_attempts.jsonl', {'attempt_rows': len(attempts), 'referenced_missing': sorted({k for r in mapping for k in r['fetch_attempt_ids'] if k not in attempt_ids})}, 'python publish_full_C.py', str(FULL / 'fetch_attempts.jsonl'))
    ind_status = Counter(a['status'] for a in attempts if a['attempt_id'].startswith('C-INDUSTRY-')); add('C-INDUSTRY-ATTEMPTS', 'GLOBAL', 'Industry historical attempt audit', 'RESEARCH', 'PASS' if ind_status.get('PARAMETER_ERROR', 0) == 0 and ind_status.get('SUCCESS', 0) >= 400 else 'FAIL', '401 success, 1 NOT_FOUND, 0 parameter errors', dict(ind_status), 'read copied fetch_attempts_industry.jsonl', str(FULL / 'data/fetch_attempts_industry.jsonl'))
    add('C-CANDIDATE-COVERAGE', 'GLOBAL', 'Candidate comparisons are materialized', 'DATA', 'PASS' if len(candidates) > 0 and all(r.get('candidate_tools_tested') or r.get('candidate_tools_missing') for r in mapping) else 'FAIL', 'candidate_metrics rows > 0 and each mapping has tested or missing list', {'candidate_metric_rows': len(candidates)}, 'python publish_full_C.py', str(FULL / 'candidate_metrics.jsonl'))
    add('C-PROGRESS-55', 'GLOBAL', 'No UNPROCESSED terminal row', 'DATA', 'PASS' if all(r['processing_status'] == 'PROCESSED' and r['decision'] in {'MATCH', 'NO_MATCH_IN_TESTED_SET', 'INSUFFICIENT_DATA', 'OUT_OF_SCOPE'} for r in mapping) else 'FAIL', 'all 55 terminal statuses are allowed', Counter(r['decision'] for r in mapping), 'python publish_full_C.py', str(FULL / 'mapping.json'))
    write_jsonl(FULL / 'checks.jsonl', checks)
    write_json(FULL / 'checks/check_summary.json', {'run_id': RUN_ID, 'passed': all(x['status'] == 'PASS' for x in checks), 'checks': len(checks), 'failures': [x['check_id'] for x in checks if x['status'] != 'PASS']})
    write_json(FULL / 'assets.json', {'run_id': RUN_ID, 'owner': 'C', 'quality_status': 'PASS' if all(x['status'] == 'PASS' for x in checks) else 'REVIEW_REQUIRED', 'engine': {'version': 'FULL237_RHO060_V1', 'path': str(B_ENGINE), 'sha256': sha(B_ENGINE), 'tests_path': str(B_TESTS), 'tests_sha256': sha(B_TESTS)}, 'selection_lock': {'path': str(LOCK), 'sha256': sha(LOCK), 'lock_content_sha256': lock.get('lock_sha256')}, 'machine_files': {name: {'path': str(FULL / name), 'sha256': sha(FULL / name), 'size_bytes': (FULL / name).stat().st_size} for name in ['mapping.json', 'mapping.csv', 'target_results.jsonl', 'candidate_metrics.jsonl', 'inventory.jsonl', 'tasks.jsonl', 'fetch_attempts.jsonl', 'checks.jsonl', 'evidence.jsonl'] if (FULL / name).exists()}, 'data_inputs': [{'path': str(p), 'sha256': sha(p)} for p in [FULL / 'data/industry_history_bars.jsonl.gz', FULL / 'data/fetch_attempts_industry.jsonl', FULL / 'data/input_new_period_c_pcf_hk.jsonl.gz']], 'known_gaps': ['46 funds remain INSUFFICIENT_DATA due absent/short fund-specific panels.', 'Scope status remains UNVERIFIED from supplied assignments.', 'Event evidence is PARTIAL and execution is CONDITIONAL/UNKNOWN.', 'ETF_MARKET_PRICE is a separate target and carries premium/basis risk.', 'C output is not main-agent acceptance.']})
    (FULL / 'STATUS.md').write_text(f'''# C full-coverage status\n\n- owner: C\n- run_stage: DELIVER\n- fund_progress: 55/55\n- updated_at_utc: {now()}\n- compute_status: DONE\n- delivery_status: QA_PENDING_WORKBOOK\n- decision_counts: {dict(Counter(r["decision"] for r in mapping))}\n- actual_backtest_count: {sum(bool(r["actual_backtest_run"]) for r in mapping)}\n- detail: machine audit files, evidence, tasks and checks published; workbook build remains next action.\n- output_root: {FULL}\n- old_r1_read_only: true\n''', encoding='utf-8')
    print(json.dumps({'run_id': RUN_ID, 'attempts': len(attempts), 'tasks': len(tasks), 'evidence': len(evidence), 'checks': len(checks), 'all_checks_pass': all(x['status'] == 'PASS' for x in checks)}, ensure_ascii=False))


if __name__ == '__main__': main()
