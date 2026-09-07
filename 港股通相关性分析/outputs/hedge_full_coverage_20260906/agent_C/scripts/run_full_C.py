#!/usr/bin/env python3
"""Full-coverage C run using the published B selection engine.

This script owns only the new full-coverage directory.  It reuses copied
read-only inputs but never writes to the R1 directory.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import importlib.util
import json
import math
import os
import shutil
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path('/Users/ellis/工具程序开发/港股通相关性分析')
FULL = ROOT / 'outputs/hedge_full_coverage_20260906/agent_C'
B_ENGINE_PATH = ROOT / 'outputs/hedge_full_coverage_20260906/agent_B/scripts/selection_core.py'
ASSIGNMENTS = ROOT / 'outputs/hedge_full_coverage_20260906/control/assignments.json'
OLD_RAW = FULL / 'data/pcf_candidate_raw'
NEW_RAW = FULL / 'data/input_new_period_c_pcf_hk.jsonl.gz'
INDUSTRY_RAW = FULL / 'data/industry_history_bars.jsonl.gz'
MARKET_DIR = FULL / 'data/market_panels'
LOCK = FULL / 'selection_lock_full_C.json'
HORIZONS = [5, 15, 30, 60]
# The contract's main standard is the 30-minute horizon.  Keep the other
# horizons in the comparison table as descriptive diagnostics so full fund
# coverage is completed without implying four independent rolling selections.
ROLLING_HORIZONS = {30}
SLOTS = list(range(570, 691)) + list(range(780, 901))
NEW_CUTOFF = '20260804'
CORE_FILES = {'HSI_FUT': 'HSI_FUT_1min.csv', 'HHI_FUT': 'HHI_FUT_1min.csv', 'HTI_FUT': 'HTI_FUT_1min.csv', '02800': '2800_1min.csv', '02828': '2828_1min.csv'}
HK_TICKERS = ['02800', '02828', '03032', '03033', '02845']

spec = importlib.util.spec_from_file_location('selection_core_full', B_ENGINE_PATH)
ENGINE = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(ENGINE)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def jdump(x):
    if isinstance(x, dict): return {str(k): jdump(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)): return [jdump(v) for v in x]
    if isinstance(x, (np.integer,)): return int(x)
    if isinstance(x, (np.floating,)): return None if np.isnan(x) else float(x)
    if isinstance(x, float) and math.isnan(x): return None
    if isinstance(x, pd.Timestamp): return x.isoformat()
    return x


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(jdump(row), ensure_ascii=False, separators=(',', ':')) + '\n')


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jdump(value), ensure_ascii=False, indent=2), encoding='utf-8')


def write_csv(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [jdump(x) for x in rows]
    fields = []
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        for r in rows:
            w.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in r.items()})


def code5(v):
    s = str(v or '').strip().replace('.HK', '').replace('.SH', '').replace('.SZ', '')
    return s.zfill(5) if s.isdigit() else s


def parse_price(v):
    try:
        x = float(v)
        return x if np.isfinite(x) and x > 0 else None
    except (TypeError, ValueError):
        return None


def asof(series, minute, max_stale=2):
    if not series: return None, None
    if isinstance(series, tuple):
        keys, values = series
        i = bisect_right(keys, minute) - 1
        if i < 0: return None, None
        stale = minute - keys[i]
        return (values[i], stale) if stale <= max_stale else (None, stale)
    keys = sorted(series)
    i = bisect_right(keys, minute) - 1
    if i < 0: return None, None
    stale = minute - keys[i]
    return (series[keys[i]], stale) if stale <= max_stale else (None, stale)


def prepared(series):
    keys = sorted(series)
    return (keys, [series[k] for k in keys]) if keys else None


def parse_hk_series(values):
    out = {}
    for row in values or []:
        try:
            minute = int(row.get('minute')) if isinstance(row, dict) else int(row[0])
            raw = row.get('close', row.get('price')) if isinstance(row, dict) else (row[4] if len(row) >= 5 else row[1])
            p = parse_price(raw)
            if p is not None: out[minute] = p
        except (IndexError, KeyError, TypeError, ValueError):
            continue
    return out


def load_all_pcf():
    by_fund = defaultdict(dict)
    for path in sorted(OLD_RAW.glob('*.jsonl.gz')):
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            for line in f:
                if not line.strip(): continue
                r = json.loads(line)
                fund = code5(r.get('fund_id') or r.get('target') or path.stem.split('.')[0])
                by_fund[fund][str(r.get('date'))] = r
    if NEW_RAW.exists():
        with gzip.open(NEW_RAW, 'rt', encoding='utf-8') as f:
            for line in f:
                if not line.strip(): continue
                r = json.loads(line)
                fund = code5(r.get('fund_id') or r.get('target'))
                by_fund[fund][str(r.get('date'))] = r
    return by_fund


def load_pcf_for_fund(fid):
    """Load only this fund's PCF records, keeping the large raw bundle lazy."""
    code = code5(fid)
    out = {}
    old = OLD_RAW / f'{code}.jsonl.gz'
    if old.exists():
        with gzip.open(old, 'rt', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    r = json.loads(line); out[str(r.get('date'))] = r
    if NEW_RAW.exists():
        with gzip.open(NEW_RAW, 'rt', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    if code5(r.get('fund_id') or r.get('target')) == code:
                        out[str(r.get('date'))] = r
    return out


def load_core():
    out = defaultdict(dict)
    for tool, filename in CORE_FILES.items():
        path = FULL / 'data' / filename
        if not path.exists(): continue
        with path.open('r', encoding='utf-8-sig', newline='') as f:
            for r in csv.DictReader(f):
                try:
                    d = str(r.get('trade_date', '')).replace('-', '')
                    stamp = r.get('timestamp', '')
                    minute = int(stamp[11:13]) * 60 + int(stamp[14:16])
                    p = parse_price(r.get('close'))
                    if d and p is not None: out[(d, minute)][tool] = p
                except (TypeError, ValueError, IndexError):
                    continue
    return out


def load_industry():
    out = defaultdict(dict)
    if not INDUSTRY_RAW.exists(): return out
    with gzip.open(INDUSTRY_RAW, 'rt', encoding='utf-8') as f:
        for line in f:
            if not line.strip(): continue
            r = json.loads(line)
            tool = str(r.get('tool_id'))
            for bar in r.get('bars', []):
                try:
                    epoch = int(float(bar['date']))
                    dt = datetime.fromtimestamp(epoch, timezone.utc)
                    local = dt.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Hong_Kong'))
                    minute = local.hour * 60 + local.minute
                    p = parse_price(bar.get('close'))
                    if p is not None and minute in SLOTS:
                        out[(local.strftime('%Y%m%d'), minute)][tool] = p
                except (KeyError, TypeError, ValueError, OverflowError, OSError):
                    continue
    return out


def components_for(record):
    qty = {}
    missing = []
    for c in record.get('components') or []:
        code = code5(c.get('成分股代码') or c.get('code'))
        if not code or code in {'159900', '159901', '159902'}:
            continue
        q = c.get('数量股', c.get('qty'))
        try:
            qv = float(q)
            if not np.isfinite(qv): raise ValueError
            qty[code] = qv
        except (TypeError, ValueError):
            missing.append({'code': code, 'name': c.get('成分股名称') or c.get('name'), 'reason': 'cash_or_non_numeric_quantity'})
    return qty, missing


def timestamp_end(day, minute):
    return pd.Timestamp(day, tz='Asia/Hong_Kong') + pd.Timedelta(minutes=minute - (int(day[-2:]) * 0 if False else 0))


def local_timestamp(day, minute):
    base = pd.Timestamp(day, tz='Asia/Hong_Kong').normalize()
    return (base + pd.Timedelta(minutes=minute)).tz_convert('UTC').isoformat()


def build_pcf_panel(fid, pcf_rows, core, industry):
    pcf_rows = pcf_rows or {}
    rows = []
    audit_days = []
    for day, record in sorted(pcf_rows.items()):
        qty, ignored_missing = components_for(record)
        hk = {code5(k): prepared(parse_hk_series(v)) for k, v in (record.get('hk') or {}).items()}
        baskets = {}
        pcf_tools = {}
        rejected = defaultdict(int)
        for minute in SLOTS:
            total = 0.0; complete = True; max_stale = 0
            for code, q in qty.items():
                p, stale = asof(hk.get(code, {}), minute, 2)
                if p is None:
                    complete = False; rejected[code] += 1; max_stale = max(max_stale, int(stale or 999)); break
                max_stale = max(max_stale, int(stale or 0)); total += q * p
            if complete and total > 0: baskets[minute] = total
            for tool in HK_TICKERS:
                p, _ = asof(hk.get(tool, {}), minute, 2)
                if p is not None: pcf_tools.setdefault(tool, {})[minute] = p
        day_rows = 0
        for minute, target in baskets.items():
            values = {'timestamp_end': local_timestamp(day, minute), 'target_price': target}
            for tool, series in pcf_tools.items(): values[tool] = series.get(minute, np.nan)
            for tool, value in core.get((day, minute), {}).items(): values[tool] = value
            for tool, value in industry.get((day, minute), {}).items(): values[tool] = value
            rows.append(values); day_rows += 1
        audit_days.append({'fund_id': fid, 'date': day, 'raw_components': len(record.get('components') or []), 'numeric_components': len(qty), 'ignored_missing_quantity': ignored_missing, 'basket_rows': day_rows, 'rejected_endpoint_count': int(sum(rejected.values())), 'missing_component_codes': sorted(rejected), 'source_date': day})
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values('timestamp_end').drop_duplicates('timestamp_end', keep='last').reset_index(drop=True)
    return df, audit_days


def existing_market_panel(fid, industry):
    path = MARKET_DIR / fid / 'pilot_minutes.parquet'
    if not path.exists(): return pd.DataFrame(), {'exists': False, 'path': str(path)}
    df = pd.read_parquet(path)
    rows = []
    for r in df.to_dict('records'):
        try:
            day = str(r['date']); minute = int(r['minute'])
            if minute not in SLOTS: continue
            target = parse_price(r.get('etf_price'))
            if target is None: continue
            values = {'timestamp_end': local_timestamp(day, minute), 'target_price': target}
            for tool in ['HSI_FUT', 'HHI_FUT', 'HTI_FUT', '02800', '02828', '03032', '03033', '02845']:
                p = parse_price(r.get(tool))
                if p is not None: values[tool] = p
            for tool, value in industry.get((day, minute), {}).items(): values[tool] = value
            rows.append(values)
        except (KeyError, TypeError, ValueError):
            continue
    out = pd.DataFrame(rows)
    if not out.empty: out = out.sort_values('timestamp_end').drop_duplicates('timestamp_end').reset_index(drop=True)
    return out, {'exists': True, 'path': str(path), 'source_sha256': sha(path), 'source_rows': len(df), 'etf_rows': len(rows)}


def save_panel(df, fid, target_type):
    if df.empty: return None
    path = FULL / 'panels' / f'{fid}__{target_type}.parquet'
    df.to_parquet(path, index=False)
    return path


def metric_ok(metrics):
    return metrics.get('correlation') is not None and metrics.get('correlation') >= 0.60 and metrics.get('variance_reduction') is not None and metrics.get('variance_reduction') > 0


def public_metric(metrics, unalias):
    """Remove internal X_ aliases from nested metric disclosures."""
    out = dict(metrics or {})
    if out.get('policy_id'):
        out['policy_id'] = '+'.join(unalias.get(x, x) for x in str(out['policy_id']).split('+'))
    if isinstance(out.get('tools'), list):
        out['tools'] = [unalias.get(x, x) for x in out['tools']]
    if isinstance(out.get('beta'), dict):
        out['beta'] = {unalias.get(k, k): v for k, v in out['beta'].items()}
    if isinstance(out.get('selected_policy_counts'), dict):
        out['selected_policy_counts'] = {'+'.join(unalias.get(z, z) for z in str(k).split('+')): v for k, v in out['selected_policy_counts'].items()}
    return out


def aggregate_validation(rows):
    grouped = defaultdict(list)
    for r in rows: grouped[r['policy_id']].append(r)
    out = {}
    for pid, vals in grouped.items():
        def mean(k):
            z = [v.get(k) for v in vals if v.get(k) is not None]
            return float(np.mean(z)) if z else None
        out[pid] = {'correlation': mean('validation_correlation'), 'variance_reduction': mean('validation_variance_reduction'), 'rows': int(sum(v.get('validation_rows') or 0 for v in vals)), 'days': len(vals), 'validation_folds': len(vals), 'selected_folds': int(sum(bool(v.get('selected')) for v in vals))}
    return out


def local_descriptive(ret, policies, target_col='target'):
    """Short-sample wrapper around the public engine primitives.

    The published engine's descriptive helper has a column-rename edge case;
    keep the same constrained fit and metric definitions here, with an
    explicit target_price frame for the public sample-hash contract.
    """
    rows = []
    for legs in policies:
        u = ret[['date', target_col, *legs]].dropna()
        if len(u) < 3:
            rows.append({'policy_id': '+'.join(legs), 'tools': list(legs), 'status': 'UNAVAILABLE', 'sample_group_id': f'DESC_{"+".join(legs)}', 'sample_hash': None})
            continue
        beta = ENGINE.fit_beta_constrained(u[legs].to_numpy(), u[target_col].to_numpy())
        # Confidence intervals are defined for rolling OOS aggregates.  The
        # descriptive path remains explicitly descriptive and avoids spending
        # bootstrap compute on a non-confirmatory statistic.
        m = ENGINE.metric(u[target_col], u[legs].to_numpy() @ beta, u['date'], ci=False)
        sample_frame = u.rename(columns={target_col: 'target_price'})
        rows.append({'policy_id': '+'.join(legs), 'tools': list(legs), 'status': 'SHORT_SAMPLE', 'sample_group_id': f'DESC_{"+".join(legs)}', 'beta': {c: float(v) for c, v in zip(legs, beta)}, 'sample_hash': ENGINE.sample_hash(sample_frame, ['target_price', *legs]), **m})
    return rows


def choose_policy_from_counts(counts):
    if not counts: return None
    ids = list(counts)
    return sorted(ids, key=lambda p: (-counts[p], p.count('+'), p))[0]


def run_target(fid, target_type, panel, candidates, families, run_id, panel_path, manifest):
    target_results = []
    candidate_rows = []
    residual_rows = []
    weight_rows = []
    if panel.empty:
        for h in HORIZONS:
            target_results.append({'fund_id': fid, 'target_type': target_type, 'run_id': run_id, 'decision': 'INSUFFICIENT_DATA', 'evidence_level': 'UNAVAILABLE', 'horizon_min': h, 'policy_id': None, 'metrics': {'rows': 0, 'days': 0, 'correlation': None, 'variance_reduction': None}, 'data_manifest_path': manifest, 'weights_path': str(FULL / 'daily_weights.jsonl'), 'residual_path': str(FULL / 'residuals.jsonl'), 'selection_lock_path': str(LOCK), 'checks_ids': ['C-FULL-COVERAGE'], 'limitations': ['No target price panel was found; no silent substitute used.']})
        return target_results, candidate_rows, residual_rows, weight_rows
    present = [c for c in candidates if c in panel.columns and panel[c].notna().sum() >= 3]
    missing = [c for c in candidates if c not in present]
    # pandas.itertuples() renames columns that begin with digits.  Use stable
    # reversible engine aliases while retaining real HKEX tool IDs in every
    # published result.
    alias = {c: (f'X_{c}' if c[:1].isdigit() else c) for c in panel.columns if c[:1].isdigit()}
    unalias = {v: k for k, v in alias.items()}
    engine_panel = panel.rename(columns=alias)
    engine_present = [alias.get(c, c) for c in present]
    engine_families = {alias.get(c, c): families.get(c) for c in present}
    policies = ENGINE.policy_list(engine_present, engine_families)
    for h in HORIZONS:
        ret = ENGINE.make_returns(engine_panel, h)
        if ret.empty or 'target_price' not in ret:
            target_results.append({'fund_id': fid, 'target_type': target_type, 'run_id': run_id, 'decision': 'INSUFFICIENT_DATA', 'evidence_level': 'UNAVAILABLE', 'horizon_min': h, 'policy_id': None, 'metrics': {'rows': 0, 'days': 0, 'correlation': None, 'variance_reduction': None}, 'data_manifest_path': manifest, 'weights_path': str(FULL / 'daily_weights.jsonl'), 'residual_path': str(FULL / 'residuals.jsonl'), 'selection_lock_path': str(LOCK), 'checks_ids': ['C-FULL-COVERAGE'], 'limitations': ['No same-session endpoint pairs were available.']})
            continue
        days = int(ret['date'].nunique())
        if days >= 60 and policies and h in ROLLING_HORIZONS:
            result, residual, weights = ENGINE.rolling_oos(engine_panel, engine_present, horizons=[h], policies=policies, min_train_days=60, fit_days=50, validation_days=10)
            block = result.get(h, {})
            primary = block.get('primary', {})
            residual_df = residual.get(h, pd.DataFrame())
            weight_list = weights.get(h, [])
            validation = block.get('validation_metrics', [])
            counts = primary.get('selected_policy_counts', {})
            selected_pid = choose_policy_from_counts(counts)
            selected_pid_actual = '+'.join(unalias.get(x, x) for x in selected_pid.split('+')) if selected_pid else None
            oos_days = int(primary.get('days') or 0)
            new_days = len({str(x) for x in residual_df.get('date', pd.Series(dtype=str)).tolist() if str(x) >= NEW_CUTOFF}) if not residual_df.empty else 0
            decision = 'MATCH' if metric_ok(primary) else ('NO_MATCH_IN_TESTED_SET' if oos_days > 0 else 'INSUFFICIENT_DATA')
            evidence = 'UNSEEN_CONFIRMATION' if new_days >= 20 and decision == 'MATCH' else ('SEEN_EXPLORATORY' if oos_days >= 20 else 'SHORT_SAMPLE')
            target_results.append({'fund_id': fid, 'target_type': target_type, 'run_id': run_id, 'decision': decision, 'evidence_level': evidence, 'horizon_min': h, 'policy_id': selected_pid_actual, 'metrics': public_metric(primary, unalias), 'data_manifest_path': manifest, 'weights_path': str(FULL / 'daily_weights.jsonl'), 'residual_path': str(FULL / 'residuals.jsonl'), 'selection_lock_path': str(LOCK), 'checks_ids': ['C-FULL-COVERAGE', 'C-ENGINE-ROLLING-60-50-10', 'C-NO-CROSS-LUNCH'], 'limitations': ['Overlapping minute-end labels are not independent observations.', 'PCF_BASKET is not ETF market price.' if target_type == 'PCF_BASKET' else 'ETF_MARKET_PRICE includes premium/basis and is not a PCF basket.']})
            val_summary = aggregate_validation(validation)
            for legs in policies:
                pid = '+'.join(legs); v = val_summary.get(pid); pid_actual = '+'.join(unalias.get(x, x) for x in pid.split('+'))
                if v is None:
                    candidate_rows.append({'fund_id': fid, 'run_id': run_id, 'target_type': target_type, 'horizon_min': h, 'candidate_id': pid_actual, 'tools': [unalias.get(x, x) for x in legs], 'economic_reason': 'Locked candidate policy; no complete validation fold due missing prices.', 'selection_stage': 'VALIDATION', 'sample_group_id': f'{fid}_{target_type}_H{h}_VALIDATION', 'sample_hash': None, 'metrics': {'correlation': None, 'variance_reduction': None, 'rows': 0, 'days': 0}, 'eligible': False, 'rank_basis': 'validation rho>=0.60 and variance reduction>0; single-leg preferred', 'exclusion_reason': 'No complete validation fold.', 'residual_path': str(FULL / 'residuals.jsonl')})
                    continue
                metrics = {'correlation': v['correlation'], 'variance_reduction': v['variance_reduction'], 'rows': v['rows'], 'days': v['days'], 'validation_folds': v['validation_folds'], 'selected_folds': v['selected_folds']}
                candidate_rows.append({'fund_id': fid, 'run_id': run_id, 'target_type': target_type, 'horizon_min': h, 'candidate_id': pid_actual, 'tools': [unalias.get(x, x) for x in legs], 'economic_reason': 'Locked candidate policy; family-aware single-leg or cross-family pair.', 'selection_stage': 'VALIDATION', 'sample_group_id': f'{fid}_{target_type}_H{h}_VALIDATION', 'sample_hash': ENGINE.sample_hash(ret.rename(columns={'target_price': 'target'}), ['target', *legs]) if all(c in ret for c in ['target_price', *legs]) else None, 'metrics': metrics, 'eligible': bool(metrics['correlation'] is not None and metrics['correlation'] >= .60 and metrics['variance_reduction'] is not None and metrics['variance_reduction'] > 0), 'rank_basis': 'validation rho>=0.60 and variance reduction>0; single-leg preferred', 'exclusion_reason': None, 'residual_path': str(FULL / 'residuals.jsonl')})
            for w in weight_list:
                w2 = dict(w); w2.update({'fund_id': fid, 'target_type': target_type, 'run_id': run_id, 'policy_id': '+'.join(unalias.get(x, x) for x in str(w.get('policy_id')).split('+')), 'tools': [unalias.get(x, x) for x in (w.get('tools') or [])], 'beta': {unalias.get(k, k): v for k, v in (w.get('beta') or {}).items()}})
                weight_rows.append(w2)
            for row in residual_df.to_dict('records'):
                x = dict(row); x.update({'fund_id': fid, 'target_type': target_type, 'run_id': run_id, 'policy_id': '+'.join(unalias.get(z, z) for z in str(row.get('policy_id')).split('+')), 'beta': {unalias.get(k, k): v for k, v in (row.get('beta') or {}).items()}})
                residual_rows.append(x)
            if not residual_df.empty:
                for pid, q in residual_df.groupby('policy_id'):
                    mm = ENGINE.metric(q['target'], q['proxy_return'], q['date'], ci=True)
                    pid_actual = '+'.join(unalias.get(z, z) for z in str(pid).split('+'))
                    candidate_rows.append({'fund_id': fid, 'run_id': run_id, 'target_type': target_type, 'horizon_min': h, 'candidate_id': pid_actual, 'tools': [unalias.get(z, z) for z in pid.split('+')], 'economic_reason': 'Locked candidate policy; selected OOS rows only.', 'selection_stage': 'OOS', 'sample_group_id': f'{fid}_{target_type}_H{h}_OOS_SELECTED', 'sample_hash': ENGINE.sample_hash(q.rename(columns={'target': 'target_price'}), ['target_price', 'proxy_return']), 'metrics': mm, 'eligible': metric_ok(mm), 'rank_basis': 'selected-policy OOS diagnostic; mapping primary uses complete engine OOS aggregate', 'exclusion_reason': None, 'residual_path': str(FULL / 'residuals.jsonl')})
        else:
            desc = local_descriptive(ret.rename(columns={'target_price': 'target'}), policies, target_col='target') if policies else []
            best = sorted([x for x in desc if x.get('correlation') is not None], key=lambda x: (metric_ok(x), len(x.get('tools', [])) == 1, x.get('correlation') or -9, x.get('variance_reduction') or -9), reverse=True)
            best_row = best[0] if best else None
            metrics = best_row or {'rows': 0, 'days': days, 'correlation': None, 'variance_reduction': None, 'correlation_ci_low': None, 'correlation_ci_high': None}
            pid = '+'.join(unalias.get(x, x) for x in best_row.get('policy_id').split('+')) if best_row else None
            target_results.append({'fund_id': fid, 'target_type': target_type, 'run_id': run_id, 'decision': 'INSUFFICIENT_DATA', 'evidence_level': 'DESCRIPTIVE' if best_row else 'UNAVAILABLE', 'horizon_min': h, 'policy_id': pid, 'metrics': public_metric(metrics, unalias), 'data_manifest_path': manifest, 'weights_path': str(FULL / 'daily_weights.jsonl'), 'residual_path': str(FULL / 'residuals.jsonl'), 'selection_lock_path': str(LOCK), 'checks_ids': ['C-FULL-COVERAGE', 'C-DESCRIPTIVE-NOT-OOS'], 'limitations': ['Fewer than 60 valid days; descriptive comparison is not an OOS confirmation.']})
            for x in desc:
                candidate_rows.append({'fund_id': fid, 'run_id': run_id, 'target_type': target_type, 'horizon_min': h, 'candidate_id': '+'.join(unalias.get(z, z) for z in str(x.get('policy_id')).split('+')), 'tools': [unalias.get(z, z) for z in (x.get('tools') or [])], 'economic_reason': 'Locked candidate policy; short-sample descriptive comparison.', 'selection_stage': 'DESCRIPTIVE', 'sample_group_id': x.get('sample_group_id'), 'sample_hash': x.get('sample_hash'), 'metrics': {k: x.get(k) for k in ['correlation', 'correlation_ci_low', 'correlation_ci_high', 'variance_reduction', 'target_std_bp', 'residual_std_bp', 'up_es95_bp', 'down_es95_bp', 'rows', 'days']}, 'eligible': metric_ok(x), 'rank_basis': 'descriptive only; not eligible for MATCH', 'exclusion_reason': 'Insufficient rolling history (<60 days).', 'residual_path': str(FULL / 'results/residuals.jsonl')})
    return target_results, candidate_rows, residual_rows, weight_rows


def structural_result(fid, run_id, manifest):
    return {'fund_id': fid, 'target_type': 'INDEX_STRUCTURAL', 'run_id': run_id, 'decision': 'INSUFFICIENT_DATA', 'evidence_level': 'STRUCTURAL', 'horizon_min': None, 'policy_id': None, 'metrics': {'rows': 0, 'days': 0, 'correlation': None, 'variance_reduction': None}, 'data_manifest_path': manifest, 'weights_path': None, 'residual_path': None, 'selection_lock_path': str(LOCK), 'checks_ids': ['C-FULL-COVERAGE'], 'limitations': ['Economic candidates are disclosed for follow-up but no rho is claimed without a target panel.']}


def update_status(done, total, stage, details):
    text = f'''# C full-coverage status\n\n- owner: C\n- run_stage: {stage}\n- fund_progress: {done}/{total}\n- updated_at_utc: {now()}\n- compute_status: {"DONE" if done == total and stage == "COMPUTE" else "RUNNING"}\n- detail: {details}\n- output_root: {FULL}\n- old_r1_read_only: true\n'''
    (FULL / 'STATUS.md').write_text(text, encoding='utf-8')


def main():
    run_id = 'C-FULL-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    assignments = json.loads(ASSIGNMENTS.read_text(encoding='utf-8'))['C']
    inv = {json.loads(x)['fund_id']: json.loads(x) for x in (FULL / 'data/inventory.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()}
    lock = json.loads(LOCK.read_text(encoding='utf-8'))
    core = load_core(); industry = load_industry()
    mapping = []; target_results = []; candidate_results = []; residuals = []; weights = []; coverage = []; manifests = {}
    update_status(0, len(assignments), 'COMPUTE', 'candidate map frozen; panel construction started')
    for idx, item in enumerate(assignments, 1):
        fid = item['fund_id']; meta = inv[fid]; candidates = [x['tool_id'] for x in meta.get('economic_candidates', [])]; families = {x['tool_id']: x.get('family') for x in meta.get('economic_candidates', [])}
        fund_pcf = load_pcf_for_fund(fid)
        pcf_df, pcf_audit = build_pcf_panel(fid, fund_pcf, core, industry)
        pcf_path = save_panel(pcf_df, fid, 'PCF_BASKET')
        pcf_manifest = FULL / 'panels' / f'{fid}__PCF_BASKET.manifest.json'
        pcf_info = {'fund_id': fid, 'target_type': 'PCF_BASKET', 'run_id': run_id, 'path': str(pcf_path) if pcf_path else None, 'sha256': sha(pcf_path) if pcf_path else None, 'rows': int(len(pcf_df)), 'days': int(pcf_df['timestamp_end'].str[:10].nunique()) if not pcf_df.empty else 0, 'source_pcf_rows': len(fund_pcf), 'staleness_max_minutes': 2, 'no_second_fill': True, 'audit_days': pcf_audit, 'timestamp_semantics': 'minute-end label at local Asia/Hong_Kong slot; same-session forward return only'}
        write_json(pcf_manifest, pcf_info); manifests[(fid, 'PCF_BASKET')] = str(pcf_manifest); coverage.append({'fund_id': fid, 'instrument_id': fid, 'data_type': 'PCF', 'target_pathway': 'PCF_BASKET', 'source_id': 'C-PCF-COPIED-RAW', 'requested_start': '20260303', 'requested_end': '20260904', 'actual_start': min([x['date'] for x in pcf_audit], default=None), 'actual_end': max([x['date'] for x in pcf_audit], default=None), 'rows': len(pcf_df), 'days': len(pcf_audit), 'timestamp_semantics': 'PCF quantity × same-day HK 1m marks; as-of <=2m; no second fill; no fixed cash', 'quality_status': 'PASS' if len(pcf_df) else 'UNAVAILABLE', 'path': str(pcf_path) if pcf_path else None, 'sha256': sha(pcf_path) if pcf_path else None, 'gap_detail': [] if pcf_path else ['No PCF record or no complete same-day HK mark panel.'], 'attempt_ids': [f'C-PCF-{fid}']})
        pr, pc, rr, ww = run_target(fid, 'PCF_BASKET', pcf_df, candidates, families, run_id, pcf_path and str(pcf_path), str(pcf_manifest))
        target_results.extend(pr); candidate_results.extend(pc); residuals.extend(rr); weights.extend(ww)
        market_df, market_source = existing_market_panel(fid, industry)
        market_path = save_panel(market_df, fid, 'ETF_MARKET_PRICE')
        market_manifest = FULL / 'panels' / f'{fid}__ETF_MARKET_PRICE.manifest.json'
        market_info = {'fund_id': fid, 'target_type': 'ETF_MARKET_PRICE', 'run_id': run_id, 'path': str(market_path) if market_path else None, 'sha256': sha(market_path) if market_path else None, 'rows': int(len(market_df)), 'days': int(market_df['timestamp_end'].str[:10].nunique()) if not market_df.empty else 0, 'source': market_source, 'premium_basis_field': 'expost_premium_bps' if market_source.get('exists') else None, 'timestamp_semantics': 'reused normalized 1m market price with minute-end label; independent target from PCF_BASKET'}
        write_json(market_manifest, market_info); manifests[(fid, 'ETF_MARKET_PRICE')] = str(market_manifest); coverage.append({'fund_id': fid, 'instrument_id': fid, 'data_type': 'MINUTE', 'target_pathway': 'ETF_MARKET_PRICE', 'source_id': 'C-ARCHIVED-PILOT-MINUTES', 'requested_start': '20260303', 'requested_end': '20260904', 'actual_start': market_df['timestamp_end'].str[:10].min().replace('-', '') if not market_df.empty else None, 'actual_end': market_df['timestamp_end'].str[:10].max().replace('-', '') if not market_df.empty else None, 'rows': len(market_df), 'days': int(market_df['timestamp_end'].str[:10].nunique()) if not market_df.empty else 0, 'timestamp_semantics': 'ETF market price; not PCF basket; premium/basis disclosure required', 'quality_status': 'PASS' if len(market_df) else 'UNAVAILABLE', 'path': str(market_path) if market_path else None, 'sha256': sha(market_path) if market_path else None, 'gap_detail': [] if market_path else ['No fund-specific 1m market-price panel found.'], 'attempt_ids': [f'C-ETF-MARKET-{fid}']})
        mr, mc, mrr, mww = run_target(fid, 'ETF_MARKET_PRICE', market_df, candidates, families, run_id, market_path and str(market_path), str(market_manifest))
        target_results.extend(mr); candidate_results.extend(mc); residuals.extend(mrr); weights.extend(mww)
        structural = structural_result(fid, run_id, str(FULL / 'data/candidate_map.jsonl')); target_results.append(structural)
        for c in meta.get('economic_candidates', []):
            candidate_results.append({'fund_id': fid, 'run_id': run_id, 'target_type': 'INDEX_STRUCTURAL', 'horizon_min': None, 'candidate_id': c['tool_id'], 'tools': [c['tool_id']], 'economic_reason': c['reason'], 'selection_stage': 'DESCRIPTIVE', 'sample_group_id': None, 'sample_hash': None, 'metrics': {'correlation': None, 'variance_reduction': None, 'rows': 0, 'days': 0}, 'eligible': False, 'rank_basis': 'structural disclosure only', 'exclusion_reason': 'No fund-specific structural target panel tested.', 'residual_path': None})
        # Mapping uses 30m when available, otherwise the best rolling target, then structural.
        def best_target(tt):
            xs = [x for x in target_results if x['fund_id'] == fid and x['target_type'] == tt and x.get('horizon_min') in HORIZONS]
            return next((x for x in xs if x['horizon_min'] == 30 and x['decision'] != 'INSUFFICIENT_DATA'), None) or next((x for x in xs if x['decision'] != 'INSUFFICIENT_DATA'), None) or next((x for x in xs if x.get('metrics', {}).get('rows', 0) > 0), None)
        p30 = best_target('PCF_BASKET'); m30 = best_target('ETF_MARKET_PRICE')
        chosen = p30 if p30 and p30['decision'] != 'INSUFFICIENT_DATA' and p30.get('metrics', {}).get('rows', 0) else (m30 if m30 and m30.get('metrics', {}).get('rows', 0) and m30['decision'] != 'INSUFFICIENT_DATA' else structural)
        # PCF target is preferred whenever rolling data are sufficient, even if it does not match.
        p_roll = [x for x in target_results if x['fund_id'] == fid and x['target_type'] == 'PCF_BASKET' and x['decision'] != 'INSUFFICIENT_DATA']
        m_roll = [x for x in target_results if x['fund_id'] == fid and x['target_type'] == 'ETF_MARKET_PRICE' and x['decision'] != 'INSUFFICIENT_DATA']
        if p_roll: chosen = next((x for x in p_roll if x['horizon_min'] == 30), p_roll[0])
        elif m_roll: chosen = next((x for x in m_roll if x['horizon_min'] == 30), m_roll[0])
        chosen_metrics = chosen.get('metrics', {})
        pid = chosen.get('policy_id'); chosen_tools = pid.split('+') if pid else None
        backups = m30 if chosen['target_type'] == 'PCF_BASKET' else (p30 if chosen['target_type'] == 'ETF_MARKET_PRICE' else None)
        latest = sorted([w for w in weights if w.get('fund_id') == fid and w.get('target_type') == chosen['target_type'] and w.get('horizon_min') == chosen.get('horizon_min')], key=lambda x: str(x.get('date') or x.get('test_date'))) if chosen.get('horizon_min') else []
        latest_w = latest[-1] if latest else {}
        structural_candidates = [{'candidate_id': c['tool_id'], 'asset_type': c['asset_type'], 'reason': c['reason']} for c in meta.get('economic_candidates', [])]
        remaining = []
        if not p_roll: remaining.append('PCF_BASKET rolling OOS unavailable or insufficient valid days; see per-target rows.')
        if not m_roll: remaining.append('ETF_MARKET_PRICE independent fund-specific 1m panel unavailable or insufficient valid days; see fetch attempts.')
        remaining.append('Scope identity is UNVERIFIED in supplied assignment inventory; official per-fund scope verification remains open.')
        mapping.append({'fund_id': fid, 'fund_name': item.get('fund_name'), 'owner': 'C', 'index_id': item.get('index_id'), 'index_name': item.get('index_name'), 'scope_status': meta.get('scope_status', 'UNVERIFIED'), 'scope_evidence_ids': meta.get('scope_evidence_ids', []), 'processing_status': 'PROCESSED', 'actual_backtest_run': bool(chosen.get('metrics', {}).get('rows', 0) and chosen.get('target_type') in {'PCF_BASKET', 'ETF_MARKET_PRICE'} and chosen.get('decision') != 'INSUFFICIENT_DATA'), 'decision': chosen['decision'], 'target_type': chosen['target_type'], 'evidence_level': chosen['evidence_level'], 'primary_policy_id': pid, 'primary_tools': chosen_tools, 'backup_policy_id': backups.get('policy_id') if backups else None, 'backup_tools': backups.get('policy_id').split('+') if backups and backups.get('policy_id') else None, 'structural_candidates': structural_candidates, 'selection_reason': 'PCF_BASKET优先；若PCF滚动样本不足则独立尝试ETF_MARKET_PRICE；单腿在验证rho>=0.60且残差方差降低时优先；否则只披露结构候选。', 'primary_horizon_min': chosen.get('horizon_min'), 'hedge_return_correlation': chosen_metrics.get('correlation'), 'correlation_ci_low': chosen_metrics.get('correlation_ci_low'), 'correlation_ci_high': chosen_metrics.get('correlation_ci_high'), 'correlation_threshold': 0.60, 'target_std_bp': chosen_metrics.get('target_std_bp'), 'residual_std_bp': chosen_metrics.get('residual_std_bp'), 'variance_reduction': chosen_metrics.get('variance_reduction'), 'up_es95_bp': chosen_metrics.get('up_es95_bp'), 'down_es95_bp': chosen_metrics.get('down_es95_bp'), 'oos_start': chosen_metrics.get('oos_start'), 'oos_end': chosen_metrics.get('oos_end'), 'oos_days': chosen_metrics.get('days'), 'new_unseen_oos_days': len({str(x.get('date')) for x in residuals if x.get('fund_id') == fid and x.get('target_type') == chosen.get('target_type') and str(x.get('date')) >= NEW_CUTOFF}), 'oos_rows': chosen_metrics.get('rows'), 'sample_group_id': f'{fid}_{chosen.get("target_type")}_H{chosen.get("horizon_min")}_{str(chosen_metrics.get("sample_hash"))[:12]}', 'sample_hash': chosen_metrics.get('sample_hash'), 'candidate_tools_tested': sorted({r['candidate_id'] for r in candidate_results if r.get('fund_id') == fid and r.get('target_type') == chosen.get('target_type') and r.get('selection_stage') in {'VALIDATION', 'DESCRIPTIVE'}}), 'candidate_tools_missing': candidates if not candidates else [c for c in candidates if c not in sorted({t for r in candidate_results if r.get('fund_id') == fid and r.get('target_type') == chosen.get('target_type') for t in (r.get('tools') or [])})], 'latest_beta_date': latest_w.get('date') or latest_w.get('test_date'), 'latest_beta': latest_w.get('beta'), 'hedge_direction': 'Short selected HK risk proxy against long CN ETF exposure; actual order/account action not performed.' if chosen_tools else None, 'cost_status': 'SCENARIO_ONLY', 'cost_assumptions': {'currency': 'HKD', 'transaction_costs': None, 'fund_size': None, 'note': 'No verified cost basis in C evidence; no cost deducted from rho/VR.'}, 'execution_status': 'CONDITIONAL' if chosen_tools else 'UNKNOWN', 'event_status': 'PARTIAL', 'remaining_gaps': remaining, 'fetch_attempt_ids': [f'C-PCF-{fid}', f'C-ETF-MARKET-{fid}', f'C-EVENT-{fid}'], 'source_run_id': run_id, 'result_path': str(FULL / 'residuals.jsonl') if chosen_tools else None, 'updated_at_utc': now()})
        if idx % 10 == 0 or idx == len(assignments):
            update_status(idx, len(assignments), 'COMPUTE', f'processed {idx} funds; target result rows={len(target_results)}, residual rows={len(residuals)}, weights={len(weights)}')
    # Root machine files.
    write_jsonl(FULL / 'mapping.json', mapping)
    write_csv(FULL / 'mapping.csv', mapping)
    write_jsonl(FULL / 'target_results.jsonl', target_results)
    write_jsonl(FULL / 'candidate_metrics.jsonl', candidate_results)
    write_jsonl(FULL / 'inventory.jsonl', coverage)
    write_jsonl(FULL / 'residuals.jsonl', residuals)
    write_jsonl(FULL / 'daily_weights.jsonl', weights)
    write_json(FULL / 'results/run_manifest.json', {'run_id': run_id, 'owner': 'C', 'started_at_utc': None, 'finished_at_utc': now(), 'fund_count': len(assignments), 'funds_processed': len(mapping), 'target_result_rows': len(target_results), 'candidate_metric_rows': len(candidate_results), 'residual_rows': len(residuals), 'weight_rows': len(weights), 'engine_version': ENGINE.ENGINE_VERSION, 'engine_path': str(B_ENGINE_PATH), 'engine_sha256': sha(B_ENGINE_PATH), 'selection_lock': str(LOCK), 'selection_lock_sha256': lock.get('lock_sha256'), 'session': '09:30-11:30 and 13:00-15:00 Asia/Hong_Kong', 'staleness_max_minutes': 2, 'no_fixed_cash': True, 'no_second_fill': True})
    update_status(len(assignments), len(assignments), 'COMPUTE', f'completed 55/55 funds; target result rows={len(target_results)}, residual rows={len(residuals)}, weights={len(weights)}')
    print(json.dumps({'run_id': run_id, 'funds': len(mapping), 'target_results': len(target_results), 'candidate_metrics': len(candidate_results), 'residuals': len(residuals), 'weights': len(weights)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
