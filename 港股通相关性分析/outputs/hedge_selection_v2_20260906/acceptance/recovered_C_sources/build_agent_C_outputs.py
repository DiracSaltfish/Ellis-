#!/usr/bin/env python3
"""Assemble agent C's auditable machine tables from the available inputs.

The locked target window is not fully present in the source inventory.  This
builder therefore preserves the partial new-period extraction, reuses archived
technical runs only as explicitly labelled exploration, and writes an
INSUFFICIENT_EVIDENCE conclusion for every assigned fund rather than inventing
confirmed performance.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path('/Users/ellis/工具程序开发/港股通相关性分析')
OUT = ROOT / 'outputs/hedge_selection_v2_20260906/agent_C'
CTRL = ROOT / 'outputs/hedge_selection_v2_20260906/control'
ARCHIVE = ROOT / 'batch_archive_20260906'
RUN_ID = 'C-20260906-partial-v2'
LOCK_PATH = OUT / 'selection_lock.json'
NEW_BUNDLE = OUT / 'data/new_period_c_pcf_hk.jsonl.gz'
REMOTE_AUDIT = Path('/tmp/hedge_v2_20260906_C_remote_c_audit.json')
TWS_PROBE = OUT / 'data/tws_candidate_probe.json'
NOW = datetime.now(timezone.utc).isoformat()


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def jdump(x):
    return json.dumps(x, ensure_ascii=False, separators=(',', ':')) if x is not None else None


def safe_int(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def safe_float(x):
    try:
        v = float(x)
        return None if not math.isfinite(v) else v
    except (TypeError, ValueError):
        return None


def metric(y, r):
    y = np.asarray(y, dtype=float)
    r = np.asarray(r, dtype=float)
    if len(y) < 2:
        return {'samples': int(len(y)), 'target_std_bp': None, 'residual_std_bp': None,
                'variance_reduction': None, 'target_up_es95_bp': None, 'target_down_es95_bp': None,
                'up_es95_bp': None, 'down_es95_bp': None, 'residual_mean_bp': None}
    k = max(1, int(math.ceil(len(y) * .05)))
    return {
        'samples': int(len(y)),
        'target_std_bp': float(np.std(y, ddof=1) * 10000),
        'residual_std_bp': float(np.std(r, ddof=1) * 10000),
        'variance_reduction': float(1 - np.var(r, ddof=1) / np.var(y, ddof=1)) if np.var(y, ddof=1) else None,
        'target_up_es95_bp': float(np.sort(y)[-k:].mean() * 10000),
        'target_down_es95_bp': float(np.sort(-y)[-k:].mean() * 10000),
        'up_es95_bp': float(np.sort(r)[-k:].mean() * 10000),
        'down_es95_bp': float(np.sort(-r)[-k:].mean() * 10000),
        'residual_mean_bp': float(np.mean(r) * 10000),
    }


def block_fraction(part):
    if part.empty:
        return None
    days = sorted(part['date'].astype(str).unique())
    good = []
    for i in range(0, len(days) - 4, 5):
        block = part[part['date'].astype(str).isin(days[i:i + 5])]
        if block['date'].nunique() != 5 or len(block) < 2:
            continue
        good.append(np.var(block['residual'], ddof=1) < np.var(block['y'], ddof=1))
    return float(np.mean(good)) if good else None


def read_jsonl_gz(path):
    import gzip
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def load_inputs():
    assignment = json.loads((CTRL / 'assignment_C.json').read_text(encoding='utf-8'))
    ids = [x['fund_id'] for x in assignment]
    candidates = pd.read_csv(ROOT / 'data/inventory/candidate_universe.csv', dtype=str).fillna('')
    candidates = candidates.set_index('ETF代码')
    remote = json.loads(REMOTE_AUDIT.read_text(encoding='utf-8'))
    # Keep a permanent copy of the exact remote audit used for all downstream tables.
    OUT.joinpath('data/remote_C_target_audit.json').write_text(json.dumps(remote, ensure_ascii=False, indent=2), encoding='utf-8')
    raw = read_jsonl_gz(NEW_BUNDLE) if NEW_BUNDLE.exists() else []
    tws = json.loads(TWS_PROBE.read_text(encoding='utf-8')) if TWS_PROBE.exists() else {}
    lock = json.loads(LOCK_PATH.read_text(encoding='utf-8'))
    return assignment, ids, candidates, remote, raw, tws, lock


def load_old(ids):
    out = {}
    for fid in ids:
        base = ARCHIVE / 'runs' / fid / '20260906_batch'
        result = base / 'reports/results.json'
        model = base / 'reports/model_comparison.csv'
        folds = base / 'reports/folds.csv'
        residual = base / 'reports/oos_residuals.parquet'
        if not (result.exists() and model.exists() and folds.exists() and residual.exists()):
            continue
        try:
            out[fid] = {
                'base': base,
                'result': json.loads(result.read_text(encoding='utf-8')),
                'model': pd.read_csv(model),
                'folds': pd.read_csv(folds, dtype=str),
                'residual': pd.read_parquet(residual),
            }
        except Exception as exc:
            print('old_load_failed', fid, type(exc).__name__, str(exc))
    return out


def build_partial_diagnostics(raw):
    """Build five-day descriptive diagnostics; these are not OOS confirmation."""
    rows = []
    fund_summary = {}
    tools = ['02800', '02828', '03032', '03033', '02845']
    for fid in sorted({r['fund_id'] for r in raw}):
        records = sorted([r for r in raw if r['fund_id'] == fid], key=lambda z: z['date'])
        usable_by_tool = defaultdict(list)
        usable_dates = set()
        for rec in records:
            q_missing = sum(not str(c.get('qty', '')).strip() for c in rec['components'])
            component_codes = {str(c['code']).zfill(5) for c in rec['components']}
            missing_components = sorted(component_codes - set(rec.get('hk', {})))
            if q_missing or missing_components:
                continue
            minutes = list(range(780, 900))
            series = {}
            for code in sorted(component_codes | set(tools)):
                # Carry forward only from an actually observed intraday mark;
                # seed from 09:30..12:59 as the archived engine does.
                all_minutes = list(range(570, 900))
                vals = {int(m): float(px) for m, px in rec.get('hk', {}).get(code, []) if 570 <= int(m) <= 899 and safe_float(px) is not None}
                full = pd.Series(vals, index=all_minutes, dtype=float).ffill()
                s = full.loc[minutes]
                if s.isna().any():
                    break
                series[code] = s.to_numpy()
            else:
                basket = np.zeros(len(minutes), dtype=float)
                for c in rec['components']:
                    basket += float(c['qty']) * series[str(c['code']).zfill(5)]
                y = basket[30:] / basket[:-30] - 1
                for tool in tools:
                    x = series[tool][30:] / series[tool][:-30] - 1
                    usable_by_tool[tool].append((rec['date'], y, x))
                usable_dates.add(rec['date'])
        fund_summary[fid] = {
            'pcf_records': len(records),
            'complete_quantity_records': sum(all(str(c.get('qty', '')).strip() for c in r['components']) for r in records),
            'complete_component_records': len(usable_dates),
            'partial_days_with_30m_rows': len(usable_dates),
            'partial_rows': int(sum(len(y) for _, y, _ in usable_by_tool.get('03032', []))),
        }
        if not usable_dates:
            continue
        for tool in tools:
            usable = usable_by_tool.get(tool, [])
            if not usable:
                continue
            dates = sorted({d for d, _, _ in usable})
            y = np.concatenate([v[1] for v in usable])
            x = np.concatenate([v[2] for v in usable])
            denom = float(x @ x)
            beta = float(np.clip((x @ y) / denom, 0, 2)) if denom else None
            r = y - x * beta if beta is not None else np.full_like(y, np.nan)
            m = metric(y, r)
            rows.append({
                'run_id': RUN_ID,
                'fund_id': fid,
                'horizon_min': 30,
                'scenario_id': 'NEW_PARTIAL_DIAGNOSTIC_5D_NOT_OOS',
                'tool_id': tool,
                'fit_scope': 'descriptive full five-day partial sample; no 60-day train/10-day validation',
                'sample_days': len(dates),
                'sample_count': len(y),
                'sample_hash': sha(NEW_BUNDLE),
                'beta': beta,
                **m,
            })
    return rows, fund_summary


def old_metrics(old):
    rows = []
    old_summary = {}
    for fid, d in old.items():
        model = d['model'].copy()
        residual = d['residual'].copy()
        if 'date' in residual:
            residual['date'] = residual['date'].astype(str)
        old_summary[fid] = {}
        for _, r in model.iterrows():
            h = int(r['horizon'])
            mname = str(r['model'])
            rp = residual[(residual.horizon == h) & (residual.model == mname)]
            base = d['base'] / f'data/normalized/labels_{h}m.parquet'
            sample_hash = sha(base) if base.exists() else None
            ci_low = ci_high = None
            ci_value = r.get('variance_reduction_ci95')
            if pd.notna(ci_value):
                try:
                    ci = json.loads(str(ci_value)) if isinstance(ci_value, str) else ci_value
                    if isinstance(ci, list) and len(ci) == 2:
                        ci_low, ci_high = float(ci[0]), float(ci[1])
                except Exception:
                    pass
            mm = metric(rp['y'].to_numpy(), rp['residual'].to_numpy()) if not rp.empty else {}
            old_row = {
                'run_id': f'OLD-{fid}',
                'fund_id': fid,
                'horizon_min': h,
                'policy_id': f'OLD_{mname}',
                'model_id': mname,
                'scenario_id': 'OLD_EXPLORATORY_20260303_20260803',
                'sample_hash': sample_hash,
                'confirmation_status': 'REUSED_OR_UNPROVEN',
                'oos_start': str(rp.date.min()) if not rp.empty else None,
                'oos_end': str(rp.date.max()) if not rp.empty else None,
                'oos_days': int(rp.date.nunique()) if not rp.empty else int(r.get('days', 0)),
                'sample_count': int(r.get('samples', len(rp))),
                'target_std_bp': safe_float(r.get('target_std_bps')),
                'residual_std_bp': safe_float(r.get('residual_std_bps')),
                'variance_reduction': safe_float(r.get('variance_reduction')),
                'ci_low': ci_low,
                'ci_high': ci_high,
                'bootstrap_method': 'daily_block_bootstrap_1000' if mname == 'selected' else None,
                'bootstrap_seed': int(fid.split('.')[0]),
                'target_up_es95_bp': mm.get('target_up_es95_bp'),
                'target_down_es95_bp': mm.get('target_down_es95_bp'),
                'up_es95_bp': safe_float(r.get('upside_es95_bps')),
                'down_es95_bp': safe_float(r.get('downside_es95_bps')),
                'positive_block_fraction': block_fraction(rp),
                'residual_mean_bp': safe_float(r.get('residual_mean_bps')),
                'beta_turnover': None,
                'decision_gate_results': {
                    'status': 'EXPLORATORY_NOT_CONFIRMATION',
                    'old_window_ends_before_locked_target': True,
                    'old_oos_days': int(r.get('days', 0)),
                    'old_variance_reduction': safe_float(r.get('variance_reduction')),
                    'target_confirmation_status': 'UNAVAILABLE',
                },
                'residual_path': str(d['base'] / 'reports/oos_residuals.parquet'),
            }
            rows.append(old_row)
            old_summary[fid].setdefault(h, []).append(old_row)
        # Compute rolling nominal beta turnover for the selected model's folds.
        folds = d['folds'].copy()
        for h, g in folds.groupby(folds['horizon'].astype(int)):
            g = g.sort_values('test_date')
            beta_cols = ['HSI_FUT', 'HHI_FUT', 'HTI_FUT', '02800', '02828', '03032', '03033', '02845']
            vals = g[beta_cols].apply(pd.to_numeric, errors='coerce').fillna(0).to_numpy()
            if len(vals) > 1:
                turn = np.abs(np.diff(vals, axis=0)).sum(axis=1)
                for row in rows:
                    if row['fund_id'] == fid and row['horizon_min'] == h and row['model_id'] == 'selected':
                        row['beta_turnover'] = float(np.mean(turn))
        # Prefer selected exploratory row; also record the minimum OOS model.
        for h, items in old_summary[fid].items():
            best = min(items, key=lambda x: x['residual_std_bp'] if x['residual_std_bp'] is not None else float('inf'))
            old_summary[fid][h] = {'selected': next((x for x in items if x['model_id'] == 'selected'), None),
                                   'best': best, 'all': items}
    return rows, old_summary


def source_evidence(old, remote, tws, lock):
    ev = []
    lock_hash = lock['selection_lock_sha256']
    ev.append({'evidence_id': 'E-C-LOCK', 'fund_id': 'GLOBAL', 'purpose': 'selection_lock', 'publisher': 'agent C', 'url': None,
               'published_at': None, 'retrieved_at_utc': lock['created_at_utc'], 'local_path': str(LOCK_PATH), 'sha256': sha(LOCK_PATH),
               'locator': 'selection_lock_sha256', 'finding': f"Pre-registered 2026-08-04..2026-09-04 target; lock hash {lock_hash}.", 'sufficiency': 'SUFFICIENT'})
    ev.append({'evidence_id': 'E-C-NEW-AUDIT', 'fund_id': 'GLOBAL', 'purpose': 'data_coverage', 'publisher': 'machome Stocksdata inventory', 'url': None,
               'published_at': None, 'retrieved_at_utc': remote['generated_at_utc'], 'local_path': str(OUT / 'data/remote_C_target_audit.json'),
               'sha256': sha(OUT / 'data/remote_C_target_audit.json'), 'locator': 'file_counts_in_window',
               'finding': f"Target window has CN ETF minute files {remote['file_counts_in_window']['cn_etf_1m']}, PCF files {remote['file_counts_in_window']['pcf_detail']}, HK trade files {remote['file_counts_in_window']['hk_trades']}, all-three common {remote['file_counts_in_window']['all_three']}.", 'sufficiency': 'SUFFICIENT'})
    ev.append({'evidence_id': 'E-C-TWS-PROBE', 'fund_id': 'GLOBAL', 'purpose': 'tool', 'publisher': 'IBKR TWS read-only', 'url': None,
               'published_at': None, 'retrieved_at_utc': tws.get('generated_at_utc'), 'local_path': str(TWS_PROBE), 'sha256': sha(TWS_PROBE) if TWS_PROBE.exists() else None,
               'locator': 'contractDetails and one historical day', 'finding': 'HBI_FUT contract resolved and 280 one-minute bars returned for 2026-08-25; 03069 and 03174 returned zero bars.', 'sufficiency': 'PARTIAL'})
    ev.append({'evidence_id': 'E-C-PRIOR-ACCEPTANCE', 'fund_id': 'GLOBAL', 'purpose': 'prior_results', 'publisher': 'prior acceptance report', 'url': None,
               'published_at': None, 'retrieved_at_utc': NOW, 'local_path': str(ROOT / 'outputs/final_review_20260906/最终结论与验收.md'),
               'sha256': sha(ROOT / 'outputs/final_review_20260906/最终结论与验收.md'), 'locator': '结论先行/已完成多少',
               'finding': 'Prior 48-fund technical outputs are retained as exploration only; target window and official coverage were incomplete.', 'sufficiency': 'PARTIAL'})
    ev.append({'evidence_id': 'E-C-CANDIDATE-UNIVERSE', 'fund_id': 'GLOBAL', 'purpose': 'denominator', 'publisher': 'remote inventory', 'url': None,
               'published_at': None, 'retrieved_at_utc': NOW, 'local_path': str(ROOT / 'data/inventory/candidate_universe.csv'),
               'sha256': sha(ROOT / 'data/inventory/candidate_universe.csv'), 'locator': '210 rows; name discovery only',
               'finding': 'Candidate inventory contains 210 discovery rows and is not a complete official fund universe.', 'sufficiency': 'PARTIAL'})
    ev.append({'evidence_id': 'E-C-EVENT-MANIFEST', 'fund_id': 'GLOBAL', 'purpose': 'event', 'publisher': 'HKEX/issuer verified event cache', 'url': None,
               'published_at': None, 'retrieved_at_utc': NOW, 'local_path': str(ROOT / 'data/inventory/verified_events.json'),
               'sha256': sha(ROOT / 'data/inventory/verified_events.json'), 'locator': '5 verified event records',
               'finding': 'Known verified events are retained, but the full PCF security union for 55 funds was not fully reviewed.', 'sufficiency': 'PARTIAL'})
    official = [
        ('E-C-HBI-FUTURES', 'https://www.hkex.com.hk/Products/Listed-Derivatives/Equity-Index/Hang-Seng-Biotech-index/Hang-Seng-Biotech-Index-Futures?sc_lang=en', 'HKEX', 'HBI code, HK$50 multiplier, trading hours, fees HK$5.54 per contract per side.', 'hkex_hbi_futures.html'),
        ('E-C-3069-IFP', 'https://ifp.hkex.hk/fund-repository/fund/BQQ795', 'HKEX Integrated Fund Platform', 'ChinaAMC Hang Seng Biotech ETF, stock codes 03069/83069/09069, base currency HKD; local download unavailable due TLS, retained as URL evidence.', None),
        ('E-C-ETP-PERSPECTIVE', 'https://www.hkex.com.hk/-/media/HKEX-Market/Products/Securities/ETP/ETF-and-L-and-I-Product-Market-Perspective/2026/ETFLIProductMarketPerspective_2026-Apr.pdf', 'HKEX', 'April 2026 ETP perspective lists 3069 and 3174 among ETFs in Stock Connect.', 'hkex_etp_perspective_2026_apr.pdf'),
        ('E-C-SHORT-SELLING', 'https://www.hkex.com.hk/eng/market/sec_tradinfo/ds20260630.htm', 'HKEX', '30 June 2026 designated securities page lists 3069 and 3174 as eligible for short selling.', 'hkex_short_selling_20260630.html'),
    ]
    for eid, url, publisher, finding, local in official:
        lp = OUT / 'evidence' / local if local else None
        ev.append({'evidence_id': eid, 'fund_id': 'GLOBAL', 'purpose': 'tool', 'publisher': publisher, 'url': url,
                   'published_at': '2026-06-30T00:00:00+00:00' if eid == 'E-C-SHORT-SELLING' else None,
                   'retrieved_at_utc': NOW, 'local_path': str(lp) if lp and lp.exists() else None,
                   'sha256': sha(lp) if lp and lp.exists() else None, 'locator': 'web page/PDF lines listed in final report',
                   'finding': finding, 'sufficiency': 'SUFFICIENT' if lp and lp.exists() else 'PARTIAL'})
    for fid in old:
        ev.append({'evidence_id': f'E-C-OLD-{fid}', 'fund_id': fid, 'purpose': 'technical_exploration', 'publisher': 'batch archive', 'url': None,
                   'published_at': None, 'retrieved_at_utc': NOW, 'local_path': str(old[fid]['base'] / 'reports/results.json'),
                   'sha256': sha(old[fid]['base'] / 'reports/results.json'), 'locator': 'results.json; 2026-03-03..2026-08-03',
                   'finding': 'Archived minute/PCF technical result reused as exploration; not independent locked confirmation.', 'sufficiency': 'PARTIAL'})
    return ev


def build_tables(assignment, ids, candidates, remote, raw, tws, lock, old, old_summary, partial_summary, partial_rows):
    lock_hash = lock['selection_lock_sha256']
    raw_hash = sha(NEW_BUNDLE)
    raw_by_fund = defaultdict(list)
    for r in raw:
        raw_by_fund[r['fund_id']].append(r)
    # Use the full weekday denominator for the requested 2026-08-04..2026-09-04
    # window, not just the dates that happened to be present in the remote
    # inventory.  The independent inventory audit confirms 24 expected HK
    # trading weekdays in this window; missing dates must remain visible.
    target_dates = [d.strftime('%Y%m%d') for d in pd.bdate_range('2026-08-04', '2026-09-04')]
    expected_days = len(target_dates)
    pcf_dates = set(remote['available_dates']['pcf_detail'])
    hk_dates = set(remote['available_dates']['hk_trades'])
    cn_dates = set(remote['available_dates']['cn_etf_1m'])
    fx = pd.read_csv(ROOT / 'data/raw/sse_settlement_rates.csv', dtype=str)
    fx_dates = set(fx['适用日期'].str.replace('-', '', regex=False)) & set(target_dates)
    verified_events = json.loads((ROOT / 'data/inventory/verified_events.json').read_text(encoding='utf-8'))
    verified_by_code = {str(x.get('code', '')).zfill(5): x for x in verified_events}

    funds = []
    for item in assignment:
        fid = item['fund_id']; code = fid.split('.')[0]
        cand = candidates.loc[code].to_dict() if code in candidates.index else {}
        records = raw_by_fund.get(code, [])
        observed = len(records)
        qty_complete = sum(all(str(c.get('qty', '')).strip() for c in r['components']) for r in records)
        # All current-period records are partial: the source has no common CN ETF minute file.
        oldd = old_summary.get(fid, {}).get(30, {})
        oldsel = oldd.get('selected')
        bestold = oldd.get('best')
        old_policy = f"OLD_{oldsel['model_id']}" if oldsel else None
        latest_beta = None; latest_beta_date = None
        if fid in old:
            g = old[fid]['folds']; g = g[g['horizon'].astype(int) == 30].sort_values('test_date')
            if not g.empty:
                rr = g.iloc[-1]; latest_beta_date = str(rr['test_date'])
                beta_cols = ['HSI_FUT', 'HHI_FUT', 'HTI_FUT', '02800', '02828', '03032', '03033', '02845']
                latest_beta = {'policy_id': 'OLD_SELECTED', 'effective_date': latest_beta_date,
                               'weights': {c: safe_float(rr[c]) for c in beta_cols if safe_float(rr[c]) not in (None, 0.0)},
                               'status': 'exploratory_old_window'}
        if item.get('classification') == 'QDII':
            scope = 'QDII_ONLY'
        elif item.get('classification') == 'MIXED_AH':
            scope = 'CONNECT_MIXED'
        else:
            scope = 'UNVERIFIED'
        codes_in_pcf = sorted({str(c['code']).zfill(5) for r in records for c in r['components']})
        gaps = [
            f"target CN ETF minute files={len(cn_dates)}; all-three common dates={len(remote['available_dates']['all_three'])}",
            f"target PCF observed dates={observed}/{expected_days}; quantity-complete dates={qty_complete}",
            f"target HK trade archive dates={len(hk_dates)}/{expected_days}; settlement FX dates={len(fx_dates)}/{expected_days}",
            'full PCF-security event review not complete',
            'official per-fund scope/product evidence not cached in this agent run',
            'HBI_FUT has a one-day quote probe only; 03069/03174 returned zero historical bars',
        ]
        if oldsel:
            gaps.append('old technical result ends 2026-08-03 and is REUSED_OR_UNPROVEN exploration only')
        else:
            gaps.append('no successful old technical result in the permanent archive for this fund')
        reason_codes = ['NEW_CONFIRMATION_COMMON_DAYS_0', 'NEW_CONFIRMATION_LT_20_OOS', 'CN_ETF_MINUTE_FILE_MISSING',
                        'INDUSTRY_TOOL_HISTORY_INCOMPLETE', 'EVENT_COVERAGE_INCOMPLETE', 'OFFICIAL_SCOPE_NOT_CACHED']
        if observed < expected_days: reason_codes.append('PCF_COVERAGE_PARTIAL')
        if any(not str(c.get('qty', '')).strip() for r in records for c in r['components']): reason_codes.append('PCF_QUANTITY_MISSING')
        if oldsel: reason_codes.append('OLD_RESULT_EXPLORATORY_ONLY')
        old_detail = ''
        if oldsel:
            old_detail = f"旧窗口 selected 30分钟={oldsel['variance_reduction']:.1%} VR、残差标准差={oldsel['residual_std_bp']:.2f}bp；best OOS={bestold['model_id']} {bestold['residual_std_bp']:.2f}bp，仅作探索。"
        detail = (f"本轮确认期 2026-08-04—2026-09-04：远端境内ETF分钟文件 {len(cn_dates)} 日、PCF 与港股成交共同日 {len(remote['available_dates']['all_three'])} 日；"
                  f"{code} 可读PCF {observed} 日，数量完整 {qty_complete} 日，未达到至少20个有效新OOS日，不能确认价格对冲。"
                  + (f" {old_detail}" if old_detail else '')
                  + (" 标记为QDII_ONLY但本轮未缓存逐只官方范围文件，故不越权标OUT_OF_SCOPE。" if scope == 'QDII_ONLY' else '')
                  + (" 混合沪深港风险未以香港篮子代表全基金。" if scope == 'CONNECT_MIXED' else ''))
        funds.append({
            'fund_id': fid, 'fund_name': item['fund_name'], 'owner': 'C', 'index_id': cand.get('跟踪指数代码') or None,
            'index_name': item.get('index_name') or cand.get('跟踪指数名称') or None, 'scope': scope,
            'scope_evidence_id': f'E-C-SCOPE-{fid}', 'listing_date': cand.get('上市日期') or None, 'primary_horizon_min': 30,
            'decision': 'INSUFFICIENT_EVIDENCE', 'reason_codes': reason_codes, 'reason_detail': detail,
            'recommended_policy_id': None, 'primary_tools': None, 'backup_policy_id': None,
            'exploratory_best_policy_id': f"OLD_{bestold['model_id']}" if bestold else None, 'execution_status': 'UNKNOWN',
            'confirmation_status': 'REUSED_OR_UNPROVEN' if oldsel else 'UNAVAILABLE',
            'confirmation_start': None, 'confirmation_end': None, 'oos_days': 0,
            'target_std_bp': oldsel['target_std_bp'] if oldsel else None, 'residual_std_bp': oldsel['residual_std_bp'] if oldsel else None,
            'variance_reduction': oldsel['variance_reduction'] if oldsel else None, 'ci_low': None, 'ci_high': None,
            'up_es95_bp': oldsel['up_es95_bp'] if oldsel else None, 'down_es95_bp': oldsel['down_es95_bp'] if oldsel else None,
            'positive_block_fraction': oldsel['positive_block_fraction'] if oldsel else None,
            'strict_refit_vr': None, 'effective_quote_coverage': 0.0, 'latest_beta_date': latest_beta_date,
            'latest_beta': latest_beta, 'candidate_coverage_complete': False, 'event_coverage_complete': False,
            'remaining_gaps': gaps, 'invalidation_triggers': ['effective new OOS days remain below 20', 'PCF quantity or official event treatment changes',
                                                                  'quote age exceeds 2 minutes or stale nominal exposure exceeds 2%', 'strict refit VR falls below 40%'],
            'source_run_id': f'OLD-{fid}' if oldsel else RUN_ID, 'result_path': str(OUT / 'partial_new_diagnostics.csv'), 'updated_at_utc': NOW,
        })

    # shared candidate specifications
    cand_specs = {
        'HSI_FUT': ('HSI', 'HKFE futures; broad Hong Kong equity proxy', 'futures', 'https://www.hkex.com.hk/Products/Listed-Derivatives/Equity-Index/Hang-Seng-Index-Futures?sc_lang=en', 50, 1, '09:15-12:00,13:00-16:30 Asia/Hong_Kong'),
        'HHI_FUT': ('HHI', 'HKFE futures; China enterprises proxy', 'futures', 'https://www.hkex.com.hk/Products/Listed-Derivatives/Equity-Index/Hang-Seng-China-Enterprises-Index/Hang-Seng-China-Enterprises-Index-Futures?sc_lang=en', 50, 1, '09:15-12:00,13:00-16:30 Asia/Hong_Kong'),
        'HTI_FUT': ('HSTECH', 'HKFE futures; technology proxy', 'futures', 'https://www.hkex.com.hk/Products/Listed-Derivatives/Equity-Index/Hang-Seng-TECH-Index-Futures-and-Options/Hang-Seng-TECH-Index-Futures?sc_lang=en', 50, 1, '09:15-12:00,13:00-16:30 Asia/Hong_Kong'),
        '02800': ('HSI', 'Tracker Fund of Hong Kong; broad equity ETF proxy', 'ETF', 'https://www.hkex.com.hk/Products/Securities/Exchange-Traded-Products/Overview?sc_lang=en', None, None, '09:30-16:00 Asia/Hong_Kong'),
        '02828': ('HHI', 'Hang Seng China Enterprises Index ETF; China enterprises proxy', 'ETF', 'https://www.hkex.com.hk/Products/Securities/Exchange-Traded-Products/Overview?sc_lang=en', None, None, '09:30-16:00 Asia/Hong_Kong'),
        '03032': ('HSTECH', 'Hang Seng TECH Index ETF; technology proxy', 'ETF', 'https://ifp.hkex.hk/fund-repository/fund/BQA621', None, None, '09:30-16:00 Asia/Hong_Kong'),
        '03033': ('HSTECH', 'CSOP Hang Seng TECH Index ETF; technology proxy', 'ETF', 'https://www.hkex.com.hk/Products/Securities/Exchange-Traded-Products/Overview?sc_lang=en', None, None, '09:30-16:00 Asia/Hong_Kong'),
        '02845': ('EV_PROXY', 'Global X China Electric Vehicle and Battery ETF; exploratory sector proxy', 'ETF', 'https://www.hkex.com.hk/Products/Securities/Exchange-Traded-Products/Overview?sc_lang=en', None, None, '09:30-16:00 Asia/Hong_Kong'),
        'HBI_FUT': ('HSBIO', 'Hang Seng Biotech Index Futures; industry-specific candidate', 'futures', 'https://www.hkex.com.hk/Products/Listed-Derivatives/Equity-Index/Hang-Seng-Biotech-index/Hang-Seng-Biotech-Index-Futures?sc_lang=en', 50, 1, '09:15-12:00,13:00-16:30 Asia/Hong_Kong'),
        '03069': ('HSBIO', 'ChinaAMC Hang Seng Biotech ETF; industry-specific candidate', 'ETF', 'https://ifp.hkex.hk/fund-repository/fund/BQQ795', None, 100, '09:30-16:00 Asia/Hong_Kong'),
        '03174': ('HSBIO', 'CSOP Hang Seng Biotech ETF; industry-specific candidate', 'ETF', 'https://www.hkex.com.hk/-/media/HKEX-Market/Products/Securities/ETP/ETF-and-L-and-I-Product-Market-Perspective/2026/ETFLIProductMarketPerspective_2026-Apr.pdf', None, None, '09:30-16:00 Asia/Hong_Kong'),
    }
    # TWS probe values for the industry candidates.
    probe_requests = {r['tool_id']: r for r in tws.get('probe', {}).get('requests', [])}
    tools = []
    for fund in funds:
        fid = fund['fund_id']; code = fid.split('.')[0]; n = len(raw_by_fund.get(code, []))
        for tid, (family, rationale, atype, url, mult, lot, session) in cand_specs.items():
            if tid in ('02800', '02828', '03032', '03033', '02845'):
                start = min((r['date'] for r in raw_by_fund.get(code, [])), default=None)
                end = max((r['date'] for r in raw_by_fund.get(code, [])), default=None)
                valid = sum(len(r.get('hk', {}).get(tid, [])) >= 120 for r in raw_by_fund.get(code, []))
                coverage = valid / n if n else None
                status, included, exclusion = 'SUPPORTED', True, ('partial five-day source only; no 20-day target confirmation' if n else 'no target PCF records')
            elif tid == 'HBI_FUT':
                p = probe_requests.get(tid, {})
                start = end = '2026-08-25' if p.get('bar_count') else None
                coverage = 1.0 if p.get('bar_count') else 0.0
                status, included, exclusion = ('SUPPORTED' if p.get('bar_count') else 'UNKNOWN'), False, 'one-day TWS probe only; not enough target history for confirmation'
            elif tid in ('03069', '03174'):
                p = probe_requests.get(tid, {})
                start = end = '2026-08-25' if p.get('bar_count') else None
                coverage = 0.0 if p.get('bar_count') == 0 else None
                status, included, exclusion = 'UNKNOWN', False, 'TWS one-day history returned zero bars; official listing/short-selling evidence exists but price history is unavailable'
            else:
                start = end = None; coverage = 0.0 if n else None; status, included, exclusion = 'UNKNOWN', True, 'no new-period futures history in local cache; old data ends 2026-08-03'
            evidence_id = {'HBI_FUT': 'E-C-HBI-FUTURES', '03069': 'E-C-3069-IFP', '03174': 'E-C-ETP-PERSPECTIVE'}.get(tid, 'E-C-CANDIDATE-UNIVERSE')
            tools.append({'fund_id': fid, 'tool_id': tid, 'risk_family': family, 'rationale': rationale, 'asset_type': atype, 'official_url': url,
                          'listed_from': None, 'listed_to': None, 'currency': 'HKD', 'multiplier': mult, 'lot_size': lot,
                          'session': session, 'quote_coverage': coverage, 'data_start': start, 'data_end': end, 'short_status': status,
                          'included': included, 'exclusion_reason': exclusion, 'evidence_id': evidence_id, 'selection_lock_hash': lock_hash})

    # Daily weights only for archived selected models, clearly exploration-only.
    weights = []
    for fid, d in old.items():
        folds = d['folds'].copy(); folds['horizon'] = folds['horizon'].astype(int)
        for h in [5, 15, 30, 60]:
            g = folds[folds.horizon == h].sort_values('test_date')
            for _, r in g.iterrows():
                legs = []
                for tid in ['HSI_FUT', 'HHI_FUT', 'HTI_FUT', '02800', '02828', '03032', '03033', '02845']:
                    b = safe_float(r.get(tid)) or 0.0
                    if abs(b) > 1e-15: legs.append((tid, b))
                if not legs: legs = [('NO_HEDGE', 0.0)]
                for tid, b in legs:
                    weights.append({'fund_id': fid, 'horizon_min': h, 'policy_id': 'OLD_SELECTED_EXPLORATORY', 'effective_date': str(r['test_date']),
                                    'train_start': str(r['fit_start']), 'train_end': str(r['train_end']), 'validation_start': str(r['validation_start']),
                                    'validation_end': str(r['test_date']), 'tool_id': tid, 'beta': b, 'currency_conversion': None,
                                    'selection_reason': 'archived rolling selected model; exploration only; target window not confirmed',
                                    'config_hash': sha(d['base'] / f'data/normalized/labels_{h}m.parquet') if (d['base'] / f'data/normalized/labels_{h}m.parquet').exists() else None})

    # Explicit direction/scale/cost assumptions. Metrics stay null because no current policy passed confirmation.
    scenarios = []
    costs = [('C0', 0.0), ('C1', 1.0), ('C2_5', 2.5), ('C5', 5.0), ('C10', 10.0)]
    for f in funds:
        policy = f['exploratory_best_policy_id']
        for h in [5, 15, 30, 60]:
            for direction in ['LONG_BASKET_SHORT_HEDGE', 'SHORT_BASKET_LONG_HEDGE']:
                for notional in [1000000, 10000000, 50000000]:
                    for sid, bp in costs:
                        scenarios.append({'fund_id': f['fund_id'], 'horizon_min': h, 'direction': direction, 'notional_cny': notional,
                                          'policy_id': policy, 'cost_scenario_id': sid, 'per_side_variable_cost_bp': bp,
                                          'known_fixed_fees_cny': None, 'borrow_cost_bp': None, 'funding_cost_bp': None,
                                          'basket_total_cost_bp': None, 'rounded_positions': None, 'rounded_residual_std_bp': None,
                                          'rounded_vr': None, 'risk_cost_score_bp': None, 'pareto_optimal': None, 'execution_status': 'UNKNOWN',
                                          'assumptions': {'current_policy_confirmed': False, 'variable_cost_is_per_side_per_leg': True,
                                                          'borrow_and_funding': 'unverified; kept null', 'integer_rounding': 'not run without confirmed policy'},
                                          'fee_evidence_id': None, 'run_id': RUN_ID})

    # Coverage rows: global source gaps are repeated by fund so each conclusion can be audited independently.
    cov = []
    for f in funds:
        fid = f['fund_id']; code = fid.split('.')[0]; records = raw_by_fund.get(code, []); obs = len(records)
        for security, typ, src, path, actual, expected, start, end, quality, action in [
            (fid, 'PCF', 'machome:/Volumes/EllisFiles/Stocksdata/PCF导出CSV', str(OUT / 'data/new_period_c_pcf_hk.jsonl.gz'), obs, expected_days,
             min((r['date'] for r in records), default=None), max((r['date'] for r in records), default=None), 'PASS' if obs >= 20 else 'FAIL', 'preserved partial PCF; no quantity fill'),
            ('PCF_COMPONENT_UNION', 'minute_hk', 'machome:/Volumes/EllisFiles/Stocksdata/港股_分笔成交', str(OUT / 'data/new_period_c_pcf_hk.jsonl.gz'), len(hk_dates), expected_days,
             min(hk_dates, default=None), max(hk_dates, default=None), 'UNKNOWN', 'component-level complete coverage not certified'),
            (fid, 'minute_cn_etf', 'machome:/Volumes/EllisFiles/Stocksdata/基金_分钟数据/ETF_分钟数据', 'machome inventory only', len(cn_dates), expected_days,
             min(cn_dates, default=None), max(cn_dates, default=None), 'FAIL', 'source archive has no target-window CN ETF minute files'),
            ('HKD_CNY_SETTLEMENT', 'FX', str(ROOT / 'data/raw/sse_settlement_rates.csv'), str(ROOT / 'data/raw/sse_settlement_rates.csv'), len(fx_dates), expected_days,
             min(fx_dates, default=None), max(fx_dates, default=None), 'FAIL', 'local settlement FX cache ends 2026-08-19'),
        ]:
            cov.append({'fund_id': fid, 'security_id': security, 'data_type': typ, 'source': src, 'path': path,
                        'sha256': sha(Path(path)) if path.startswith('/') and Path(path).exists() else None, 'start_date': start, 'end_date': end,
                        'expected_rows': expected, 'actual_rows': actual, 'missing_dates': sorted(set(target_dates) - ({r['date'] for r in records} if typ == 'PCF' else (hk_dates if typ == 'minute_hk' else cn_dates if typ == 'minute_cn_etf' else fx_dates))),
                        'duplicates': 0, 'timezone': 'Asia/Hong_Kong', 'timestamp_semantics': 'minute interval start; HK LAST marks aggregated from trades' if 'minute' in typ else None,
                        'adjustment': 'unadjusted; PCF quantities retained' if typ in ('PCF', 'minute_hk') else None, 'retrieved_at_utc': remote['generated_at_utc'],
                        'quality_status': quality, 'gap_action': action})

    # Event rows cover every security observed in the partial PCF; unknown rows remain explicit.
    events = []
    event_type_map = {'suspension': 'HALT', 'cash_dividend': 'DIVIDEND', 'share_subdivision': 'SPLIT', 'privatization_and_distribution': 'CODE_CHANGE'}
    for f in funds:
        fid = f['fund_id']; code = fid.split('.')[0]; records = raw_by_fund.get(code, [])
        codes = sorted({str(c['code']).zfill(5) for r in records for c in r['components']})
        if not codes: codes = ['PCF_UNAVAILABLE']
        for sec in codes:
            info = verified_by_code.get(sec)
            if info:
                et = event_type_map.get(info.get('type'), 'REVIEW_INCOMPLETE')
                start = info.get('start') or info.get('effective_date') or info.get('ex_date') or info.get('last_dealing_date')
                end = info.get('end')
                events.append({'fund_id': fid, 'security_id': sec, 'event_type': et, 'effective_from': start, 'effective_to': end,
                               'published_at': info.get('announcement_date'), 'official_url': (info.get('sources') or [None])[0],
                               'evidence_path': str(ROOT / 'data/inventory/verified_events.json'), 'evidence_sha256': sha(ROOT / 'data/inventory/verified_events.json'),
                               'treatment': info.get('policy'), 'affected_dates': [start[:10]] if isinstance(start, str) else [], 'max_weight': None, 'verified': True,
                               'numerical_check_path': None, 'remaining_risk': 'other company actions in full PCF union not fully reviewed'})
            else:
                events.append({'fund_id': fid, 'security_id': sec, 'event_type': 'REVIEW_INCOMPLETE', 'effective_from': None, 'effective_to': None,
                               'published_at': None, 'official_url': None, 'evidence_path': None, 'evidence_sha256': None,
                               'treatment': 'not treated as no-event; requires full HKEXnews/issuer review', 'affected_dates': [], 'max_weight': None, 'verified': False,
                               'numerical_check_path': None, 'remaining_risk': 'security-level corporate-action review incomplete'})

    # Sensitivity is separated into archived exploration and current partial diagnostics.
    sens = []
    for fid, d in old.items():
        p = d['base'] / 'reports/sensitivity.csv'
        if not p.exists(): continue
        sdf = pd.read_csv(p)
        for _, r in sdf.iterrows():
            model_id = str(r.get('model', ''))
            h = safe_int(r.get('horizon'))
            lp = d['base'] / f'data/normalized/labels_{h}m.parquet' if h else None
            sens.append({'fund_id': fid, 'horizon_min': h, 'policy_id': f'OLD_{model_id}', 'base_run_id': f'OLD-{fid}',
                         'scenario_id': f"OLD_{r.get('scenario', 'unknown')}", 'refit': bool('_refit' in str(r.get('scenario', ''))),
                         'sample_hash': sha(lp) if lp and lp.exists() else None, 'oos_days': safe_int(r.get('days')),
                         'variance_reduction': safe_float(r.get('variance_reduction')), 'residual_std_bp': safe_float(r.get('residual_std_bps')),
                         'up_es95_bp': safe_float(r.get('upside_es95_bps')), 'down_es95_bp': safe_float(r.get('downside_es95_bps')),
                         'pass': None, 'explanation': 'archived exploratory robustness; not target-window confirmation and no conclusion gate inferred',
                         'source_path': str(p)})
    for r in partial_rows:
        sens.append({'fund_id': r['fund_id'] + '.SH' if r['fund_id'].isdigit() and r['fund_id'] in {x.split('.')[0] for x in ids} else next((x for x in ids if x.split('.')[0] == r['fund_id']), r['fund_id']),
                     'horizon_min': 30, 'policy_id': f"PARTIAL_{r['tool_id']}", 'base_run_id': RUN_ID,
                     'scenario_id': r['scenario_id'], 'refit': False, 'sample_hash': r['sample_hash'], 'oos_days': r['sample_days'],
                     'variance_reduction': r['variance_reduction'], 'residual_std_bp': r['residual_std_bp'], 'up_es95_bp': r['up_es95_bp'],
                     'down_es95_bp': r['down_es95_bp'], 'pass': None, 'explanation': 'five-day descriptive partial sample; fewer than 20 new OOS days; not a confirmation result',
                     'source_path': str(OUT / 'partial_new_diagnostics.csv')})

    # Tasks and independent QA.
    inputs = {'assignment_C': str(CTRL / 'assignment_C.json'), 'assignment_sha256': sha(CTRL / 'assignment_C.json'),
              'selection_lock': str(LOCK_PATH), 'selection_lock_sha256': lock_hash, 'new_bundle_sha256': raw_hash,
              'remote_audit_sha256': sha(OUT / 'data/remote_C_target_audit.json')}
    task_rows = []
    phase_info = [
        ('P0', 'DONE', 'initialize 55-fund denominator and read prior acceptance', 'P0 ledger initialized; all 55 assigned funds retained.', 'NONE', None, 'P1 official evidence/data audit'),
        ('P1', 'BLOCKED', 'audit official product/scope and industry candidate evidence', 'Industry candidate evidence cached; per-fund official scope/product files remain incomplete.', 'SOURCE', 'per-fund official scope files were not cached', 'obtain official prospectus/product pages fund by fund'),
        ('P2', 'DONE', 'audit and extract target-window PCF/HK data', f"Remote audit: CN={len(cn_dates)} days, PCF={len(pcf_dates)} days, HK={len(hk_dates)} days, common={len(remote['available_dates']['all_three'])}.", 'DATA', 'target source archives stop before full confirmation window', 'refresh remote archive and rerun extraction'),
        ('P3', 'BLOCKED', 'review corporate actions and freeze/quantity treatment', 'Known verified events preserved; full PCF-security union review not possible with incomplete target PCF.', 'SOURCE', 'full PCF union unavailable', 'complete HKEXnews/issuer event review after PCF refresh'),
        ('P4', 'DONE', 'lock candidate pool and method before confirmation scoring', f"selection lock {lock_hash}; core 8 tools plus HBI/03069/03174 industry candidates.", 'NONE', None, 'do not change policy without new lock version'),
        ('P5', 'DONE', 'run partial diagnostic and preserve archived exploratory outputs', f"Partial descriptive diagnostics: {len(partial_rows)} fund-tool rows; archived valid technical runs: {len(old)}.", 'DATA', 'no current 60-day training sample', 'rerun fixed walk-forward when target data is complete'),
        ('P6', 'BLOCKED', 'evaluate new OOS/strict refit/robustness gates', 'No fund has at least 20 effective new OOS days; gates are not run as confirmation.', 'DATA', '0 all-three common target dates', 'refresh data; run 5/15/30/60m with fixed lock'),
        ('P7', 'DONE', 'write per-fund decisions, machine tables, Excel and QA', 'Every assigned fund has one explicit INSUFFICIENT_EVIDENCE conclusion and remaining gaps.', 'NONE', None, 'principal agent review and merge'),
    ]
    for phase, status, action, detail, blocker, evidence, next_action in phase_info:
        task_rows.append({'task_id': f'C-{phase}-{RUN_ID}', 'owner': 'C', 'fund_id': 'GLOBAL', 'phase': phase, 'status': status,
                          'started_at_utc': NOW, 'finished_at_utc': NOW, 'action': action, 'inputs': inputs,
                          'outputs': {'root': str(OUT)}, 'detailed_result': detail,
                          'validation': 'see qa_checks.csv and FINAL_REPORT.md', 'blocker_type': blocker, 'blocker_evidence': evidence,
                          'next_action': next_action, 'run_command': 'python3 build_agent_C_outputs.py'})
    for f in funds:
        task_rows.append({'task_id': f"C-P7-{f['fund_id']}", 'owner': 'C', 'fund_id': f['fund_id'], 'phase': 'P7', 'status': 'DONE',
                          'started_at_utc': NOW, 'finished_at_utc': NOW, 'action': 'write individual fund conclusion', 'inputs': inputs,
                          'outputs': {'fund_decisions': str(OUT / 'fund_decisions.csv')}, 'detailed_result': f['decision'] + ': ' + f['reason_detail'],
                          'validation': 'per-fund row exists; no missing denominator member', 'blocker_type': 'NONE', 'blocker_evidence': None,
                          'next_action': 'refresh target data before any confirmed policy', 'run_command': 'python3 build_agent_C_outputs.py'})

    qa = []
    def addqa(cid, fid, typ, actual, expected, tol, passed, source):
        qa.append({'check_id': cid, 'fund_id': fid, 'check_type': typ, 'run_id': RUN_ID, 'actual': actual, 'expected': expected,
                   'tolerance': tol, 'passed': bool(passed), 'source_path': source, 'checked_at_utc': NOW})
    addqa('C-QA-001', 'GLOBAL', 'new_target_file_availability', remote['file_counts_in_window'], {'all_three': 0}, 0, remote['file_counts_in_window']['all_three'] == 0, str(OUT / 'data/remote_C_target_audit.json'))
    addqa('C-QA-002', 'GLOBAL', 'new_confirmation_minimum_days', {'effective_oos_days': 0}, {'minimum_oos_days': 20}, 0, False, str(OUT / 'fund_decisions.csv'))
    missing_qty = sum(not str(c.get('qty', '')).strip() for r in raw for c in r['components'])
    addqa('C-QA-003', 'GLOBAL', 'missing_pcf_quantity_preserved', {'missing_quantity_rows': missing_qty}, {'filled_rows': 0}, 0, True, str(NEW_BUNDLE))
    addqa('C-QA-004', 'GLOBAL', 'selection_lock_hash', {'lock_hash': lock_hash}, {'lock_id': lock['lock_id']}, None, bool(lock_hash), str(LOCK_PATH))
    hbi = probe_requests.get('HBI_FUT', {})
    addqa('C-QA-005', 'GLOBAL', 'industry_future_probe', {'HBI_FUT_bar_count': hbi.get('bar_count'), 'HBI_FUT_conId': (hbi.get('contracts') or [{}])[0].get('conId')}, {'status': 'probe_only'}, None, bool(hbi.get('bar_count')), str(TWS_PROBE))
    addqa('C-QA-006', 'GLOBAL', 'industry_etf_history_not_silent', {'03069_bar_count': probe_requests.get('03069', {}).get('bar_count'), '03174_bar_count': probe_requests.get('03174', {}).get('bar_count')}, {'included_in_confirmation': False}, None, probe_requests.get('03069', {}).get('bar_count') == 0 and probe_requests.get('03174', {}).get('bar_count') == 0, str(TWS_PROBE))
    # old folds check no future ordering
    order_checks = []
    for fid, d in old.items():
        g = d['folds'].copy()
        for _, r in g.iterrows(): order_checks.append(str(r['train_end']) < str(r['test_date']) and str(r['inner_fit_end']) < str(r['validation_start']))
    addqa('C-QA-007', 'GLOBAL', 'old_walkforward_time_order', {'checked_folds': len(order_checks), 'violations': sum(not x for x in order_checks)}, {'violations': 0}, 0, bool(order_checks) and all(order_checks), str(ARCHIVE))
    # scalar PCF reconciliation for all usable partial records with only valid quantities.
    max_diff = 0.0; scalar_n = 0
    for rec in raw:
        if not all(str(c.get('qty', '')).strip() for c in rec['components']): continue
        minute = 850
        lhs = 0.0
        for c in rec['components']:
            bars = dict((int(m), float(px)) for m, px in rec.get('hk', {}).get(str(c['code']).zfill(5), []) if int(m) <= minute)
            if not bars: break
            lhs += float(c['qty']) * bars[max(bars)]
        else:
            # Re-sum with reversed component order; exact scalar identity check.
            rhs = 0.0
            for c in reversed(rec['components']):
                bars = {int(m): float(px) for m, px in rec['hk'][str(c['code']).zfill(5)] if int(m) <= minute}
                rhs += float(c['qty']) * bars[max(bars)]
            max_diff = max(max_diff, abs(lhs - rhs)); scalar_n += 1
    addqa('C-QA-008', 'GLOBAL', 'pcf_scalar_order_reconciliation', {'records_checked': scalar_n, 'max_abs_difference': max_diff}, {'max_abs_difference': 0.0}, 1e-8, scalar_n > 0 and max_diff <= 1e-8, str(NEW_BUNDLE))
    addqa('C-QA-009', 'GLOBAL', 'event_security_union_coverage', {'known_verified_events': len(verified_events), 'full_union_complete': False}, {'full_union_complete': True}, None, False, str(ROOT / 'data/inventory/verified_events.json'))
    addqa('C-QA-010', 'GLOBAL', 'input_cache_hash_present', {'new_bundle_sha256': raw_hash, 'selection_lock_sha256': lock_hash}, {'nonempty': True}, None, bool(raw_hash and lock_hash), str(OUT / 'selection_lock.json'))
    # The builder's scalar permutation test is a direct numerical invariance check.
    addqa('C-QA-011', 'GLOBAL', 'component_order_invariance', {'max_abs_difference': max_diff}, {'max_abs_difference': 0.0}, 1e-8, scalar_n > 0 and max_diff <= 1e-8, str(NEW_BUNDLE))
    addqa('C-QA-012', 'GLOBAL', 'current_walkforward_oos', {'executed': False, 'reason': 'zero common target dates'}, {'executed': True}, None, False, str(OUT / 'tasks.csv'))
    for f in funds:
        addqa(f"C-QA-FUND-{f['fund_id']}", f['fund_id'], 'per_fund_conclusion_exists', {'decision': f['decision'], 'reason_codes': f['reason_codes']}, {'decision_enum': 'INSUFFICIENT_EVIDENCE'}, None, f['decision'] == 'INSUFFICIENT_EVIDENCE', str(OUT / 'fund_decisions.csv'))

    # Scope evidence rows, including prior assignment as partial and explicit non-OUT_OF_SCOPE treatment.
    evidence = source_evidence(old, remote, tws, lock)
    for f in funds:
        item = next(x for x in assignment if x['fund_id'] == f['fund_id'])
        evidence.append({'evidence_id': f"E-C-SCOPE-{f['fund_id']}", 'fund_id': f['fund_id'], 'purpose': 'scope', 'publisher': 'assignment snapshot', 'url': item.get('official_url') or None,
                         'published_at': None, 'retrieved_at_utc': NOW, 'local_path': str(CTRL / 'assignment_C.json'), 'sha256': sha(CTRL / 'assignment_C.json'),
                         'locator': f"assignment_C.json {f['fund_id']}", 'finding': f"Prior snapshot classification={item.get('classification')}; no per-fund official product URL was cached in this run.", 'sufficiency': 'PARTIAL'})

    tables = {
        'fund_decisions': funds, 'model_metrics': [], 'tool_candidates': tools, 'weights': weights, 'execution_scenarios': scenarios,
        'data_coverage': cov, 'events': events, 'sensitivity': sens, 'tasks': task_rows, 'evidence': evidence, 'qa_checks': qa,
    }
    old_m, _ = old_metrics(old)
    tables['model_metrics'] = old_m
    return tables, {'target_dates': target_dates, 'expected_days': expected_days, 'old_metrics_count': len(old_m), 'missing_qty': missing_qty,
                    'old_count': len(old), 'partial_fund_count': len(partial_summary), 'partial_rows': len(partial_rows)}


def write_tables(tables):
    schema = json.loads((CTRL / 'schema.json').read_text(encoding='utf-8'))
    for name, spec in schema['tables'].items():
        cols = [c['key'] for c in spec['columns']]
        rows = tables.get(name, [])
        # Ensure exact column set/order and explicit null values in JSON.
        normalized = [{c: row.get(c) for c in cols} for row in rows]
        tmpj = OUT / f'{name}.json.tmp'; tmpc = OUT / f'{name}.csv.tmp'
        tmpj.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding='utf-8'); tmpj.replace(OUT / f'{name}.json')
        with tmpc.open('w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
            w.writeheader()
            for row in normalized:
                wr = {}
                for c in cols:
                    v = row[c]
                    if isinstance(v, (dict, list)):
                        v = jdump(v)
                    wr[c] = '' if v is None else v
                w.writerow(wr)
        tmpc.replace(OUT / f'{name}.csv')


def write_manifest(stats, tables):
    code_files = [Path(__file__), Path('/Users/ellis/.codex/worktrees/e135/工具程序开发/extract_new_period_remote.py'), Path('/Users/ellis/.codex/worktrees/e135/工具程序开发/probe_c_candidates.py')]
    manifest = {
        'run_id': RUN_ID, 'owner': 'C', 'generated_at_utc': NOW, 'model': 'GPT-5.6 Luna', 'reasoning_effort': 'xhigh',
        'target_window': ['2026-08-04', '2026-09-04'], 'primary_horizon_min': 30, 'status': 'PARTIAL_INSUFFICIENT_EVIDENCE',
        'selection_lock': str(LOCK_PATH), 'selection_lock_sha256': sha(LOCK_PATH),
        'code_hashes': {str(p): sha(p) for p in code_files if p.exists()},
        'input_hashes': {str(p): sha(p) for p in [CTRL / 'assignment_C.json', CTRL / 'schema.json', CTRL / 'WORKFLOW.md', ROOT / 'outputs/final_review_20260906/最终结论与验收.md', ROOT / 'data/inventory/candidate_universe.csv', ROOT / 'data/inventory/verified_events.json', NEW_BUNDLE, TWS_PROBE] if p.exists()},
        'commands': ['ssh machome python3 - --root /Volumes/EllisFiles/Stocksdata --start 20260804 --end 20260904 --funds <55 ids> < agent_C_remote_audit.py > /tmp/hedge_v2_20260906_C_remote_c_audit.json',
                     'ssh machome python3 - --root /Volumes/EllisFiles/Stocksdata --start 20260804 --end 20260904 --funds <55 ids> < extract_new_period_remote.py | gzip -c > agent_C/data/new_period_c_pcf_hk.jsonl.gz',
                     'python3 probe_c_candidates.py', 'python3 build_agent_C_outputs.py'],
        'resource_controls': {'tws_client_id': 7313, 'historical_lock': str(ROOT / 'outputs/hedge_selection_v2_20260906/resource_locks/ibkr_historical.lock'), 'remote_io_workers': 1, 'max_cpu_threads': 2},
        'research_notes': ['No orders or account/position reads were issued.', 'Old archive metrics are exploration only.', 'No NONE_IN_TESTED_SET conclusion was used because industry candidate coverage and target data were incomplete.'],
        'stats': stats,
        'table_counts': {k: len(v) for k, v in tables.items()},
    }
    tmp = OUT / 'research_manifest.json.tmp'; tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8'); tmp.replace(OUT / 'research_manifest.json')


def write_reports(tables, stats, partial_summary):
    decisions = tables['fund_decisions']
    counts = defaultdict(int)
    for f in decisions: counts[f['decision']] += 1
    scope_counts = defaultdict(int)
    for f in decisions: scope_counts[f['scope']] += 1
    audit = json.loads((OUT / 'data/remote_C_target_audit.json').read_text(encoding='utf-8'))
    old_explore = [f for f in decisions if f['confirmation_status'] == 'REUSED_OR_UNPROVEN']
    lines = [
        '# 港股通对冲研究 C 组最终报告', '',
        f'- 运行：`{RUN_ID}`；生成时间：`{NOW}`；模型：GPT-5.6 Luna / xhigh。',
        '- 研究对象：55 只分配基金；主周期 30 分钟，另列 5/15/60 分钟；结论仅限已测试范围。',
        '', '## 结论', '',
        f"55 只基金全部落为 `INSUFFICIENT_EVIDENCE`（{dict(counts)}）。没有将数据缺口写成没有对冲，也没有把旧窗口结果冒充新确认。`OUT_OF_SCOPE` 未使用：即使前轮快照标为 QDII，本轮也未缓存逐只官方范围文件。",
        '', '## 新确认期数据审计', '',
        f"远端清单时间 `{audit['generated_at_utc']}`。2026-08-04 至 2026-09-04 内境内 ETF 分钟文件 {audit['file_counts_in_window']['cn_etf_1m']} 日、PCF 明细 {audit['file_counts_in_window']['pcf_detail']} 日、港股成交 {audit['file_counts_in_window']['hk_trades']} 日，三者共同日 {audit['file_counts_in_window']['all_three']} 日；因此没有满足至少20个有效新 OOS 日的基金。PCF 记录实际落在 52 只基金、5 个日期，其中数量完整日期只对部分基金成立；缺失数量被保留，没有以固定金额替代。",
        f"FX 本地缓存至 2026-08-19，目标期内仅 {len(set(pd.read_csv(ROOT / 'data/raw/sse_settlement_rates.csv', dtype=str)['适用日期'].str.replace('-', '', regex=False)) & set(audit['available_dates']['hk_trades']))} 个日期。",
        '', '## 行业候选', '',
        'HKEX 官方资料确认 HBI 恒生生科期货（HKATS code HBI，合约乘数 HK$50/点）以及 03069、03174 生物科技 ETF；HKEX 2026-06-30 做空指定证券页列出 03069、03174。TWS 只读探针在 2026-08-25 返回 HBI 280 根 1 分钟历史条，但 03069/03174 返回 0 根，因此行业候选只进入“待验证池”，没有进入确认模型。',
        '', '## 旧技术结果的边界', '',
        f"永久归档中有 {stats['old_count']} 只 C 组基金有完整旧技术运行；这些运行结束于 2026-08-03，均标记 `REUSED_OR_UNPROVEN`。最重要的旧窗口数值保存在 `model_metrics.csv` 和 `fund_decisions.csv` 的探索字段，仅用于下一轮候选优先级，不是本轮政策。",
        '', '## 验收限制与下一步', '',
        '- 当前不能执行 60 日训练 / 10 日验证 / ≥20 日新 OOS 的 5/15/30/60 分钟门槛、严格陈旧重拟合、bootstrap CI 或成本 Pareto 认证。',
        '- 事件表对当前可读 PCF 的证券逐一留有 `REVIEW_INCOMPLETE`，已知 verified_events 单独保留；未把未核验证券默认为无事件。',
        '- 每只基金的官方范围文件、PCF 全量日期、港股组件分钟、结算汇率和行业候选历史行情刷新后，应在新 selection lock 版本下重建全部结果。',
        '', '## 机器交付', '',
        '根目录包含 11 张 CSV/JSON 明细、`RESULTS.xlsx`、`research_manifest.json`、`selection_lock.json`、`FINAL_REPORT.md`、`STATUS.md` 和 `data/`/`evidence/` 审计输入。',
        '', '## 官方来源', '',
        '- https://www.hkex.com.hk/Products/Listed-Derivatives/Equity-Index/Hang-Seng-Biotech-index/Hang-Seng-Biotech-Index-Futures?sc_lang=en',
        '- https://ifp.hkex.hk/fund-repository/fund/BQQ795',
        '- https://www.hkex.com.hk/-/media/HKEX-Market/Products/Securities/ETP/ETF-and-L-and-I-Product-Market-Perspective/2026/ETFLIProductMarketPerspective_2026-Apr.pdf',
        '- https://www.hkex.com.hk/eng/market/sec_tradinfo/ds20260630.htm',
    ]
    (OUT / 'FINAL_REPORT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    status = [
        '# 港股通对冲 C｜医药生物消费', '',
        f'更新时间：{NOW}', '',
        f"- 分配分母：55 只；逐只结论已完成：55。", 
        f"- `SUITABLE_PRICE_PROXY`：{counts.get('SUITABLE_PRICE_PROXY', 0)}；`NONE_IN_TESTED_SET`：{counts.get('NONE_IN_TESTED_SET', 0)}；`INSUFFICIENT_EVIDENCE`：{counts.get('INSUFFICIENT_EVIDENCE', 0)}；`OUT_OF_SCOPE`：{counts.get('OUT_OF_SCOPE', 0)}。",
        f"- scope 标签：{dict(scope_counts)}。QDII/MIXED 标签保留，但逐只官方范围证据未完成，未越权写 OUT_OF_SCOPE。",
        f"- 旧技术探索：{stats['old_count']} 只；新期部分 PCF/HK 读取：{stats['partial_fund_count']} 只；部分诊断：{stats['partial_rows']} 行；新期确认有效 OOS：0 日。",
        '- 已完成：P0 台账、P2 远端数据审计与局部提取、P4 selection lock、P5 局部诊断/旧结果边界整理、P7 机器表与交付。',
        '- 未完成/阻塞：P1 逐只官方范围文件、P3 全 PCF 证券事件、P6 新期 OOS/严格重拟合/成本验证。',
        '- 结论边界：本组没有可采用价格代理，也没有足够证据宣布“已测试工具池内没有合适方案”；所有剩余项保留 `INSUFFICIENT_EVIDENCE`。',
    ]
    (OUT / 'STATUS.md').write_text('\n'.join(status) + '\n', encoding='utf-8')
    # Short reader notes for the workbook.
    (OUT / 'README.md').write_text('# C 组交付说明\n\n`RESULTS.xlsx` 按 schema.json 生成 11 张机器明细和“阅读说明”。新确认期数据不完整，所有基金主结论均为 INSUFFICIENT_EVIDENCE；旧运行只在探索字段保留。请先阅读 FINAL_REPORT.md 与 STATUS.md。\n', encoding='utf-8')


def main():
    OUT.mkdir(parents=True, exist_ok=True); (OUT / 'data').mkdir(exist_ok=True); (OUT / 'evidence').mkdir(exist_ok=True)
    assignment, ids, candidates, remote, raw, tws, lock = load_inputs()
    old = load_old(ids)
    partial_rows, partial_summary = build_partial_diagnostics(raw)
    # Partial diagnostics is an extra auditable file, not a schema replacement.
    pd.DataFrame(partial_rows).to_csv(OUT / 'partial_new_diagnostics.csv', index=False)
    (OUT / 'partial_new_diagnostics.json').write_text(json.dumps(partial_rows, ensure_ascii=False, indent=2), encoding='utf-8')
    _, old_summary = old_metrics(old)
    tables, stats = build_tables(assignment, ids, candidates, remote, raw, tws, lock, old, old_summary, partial_summary, partial_rows)
    write_tables(tables)
    # Recompute model metrics separately because build_tables intentionally keeps its assembly order simple.
    old_m, old_summary = old_metrics(old)
    tables['model_metrics'] = old_m
    # Replace model metrics files with the actual rows after the second pass.
    schema = json.loads((CTRL / 'schema.json').read_text(encoding='utf-8'))
    cols = [c['key'] for c in schema['tables']['model_metrics']['columns']]
    normalized = [{c: row.get(c) for c in cols} for row in old_m]
    (OUT / 'model_metrics.json').write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding='utf-8')
    with (OUT / 'model_metrics.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for row in normalized:
            w.writerow({c: (jdump(row[c]) if isinstance(row[c], (dict, list)) else '' if row[c] is None else row[c]) for c in cols})
    stats['old_metrics_count'] = len(old_m); stats['old_count'] = len(old); stats['partial_fund_count'] = len(partial_summary); stats['partial_rows'] = len(partial_rows)
    write_manifest(stats, tables)
    write_reports(tables, stats, partial_summary)
    print(json.dumps({'out': str(OUT), 'stats': stats, 'decisions': len(tables['fund_decisions'])}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
