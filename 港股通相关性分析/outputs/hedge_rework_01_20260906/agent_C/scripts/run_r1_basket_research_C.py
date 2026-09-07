#!/usr/bin/env python3
"""Run the R1 PCF-basket price-risk pipeline for C funds.

This engine is intentionally independent of the prior VR-only summaries. It
builds each basket from that fund/date's PCF quantities and same-day HK marks,
reads the locked futures/ETF/industry tool columns, fits rolling 60-day models
(50 fit + 10 validation), and reports OOS Pearson rho, residual variance,
tail diagnostics, and exploratory policy comparisons. Missing inputs are
excluded and recorded; no fixed cash amount or price is fabricated.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import os
import argparse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


ROOT = Path('/Users/ellis/工具程序开发/港股通相关性分析')
REWORK = ROOT / 'outputs/hedge_rework_01_20260906/agent_C'
OLD_RAW = ROOT / 'batch_archive_20260906/data/raw/candidates_v2'
NEW_RAW = ROOT / 'outputs/hedge_selection_v2_20260906/agent_C/data/new_period_c_pcf_hk.jsonl.gz'
INDUSTRY_RAW = REWORK / 'data/industry_history_bars.jsonl.gz'
LOCK = REWORK / 'selection_lock_R1.json'

TOOLS = ['HBI_FUT', 'HSI_FUT', 'HHI_FUT', 'HTI_FUT', '03069', '03174', '02800', '02828', '03032', '03033', '02845']
PAIRS = [('HBI_FUT', 'HHI_FUT'), ('HBI_FUT', 'HTI_FUT'), ('HBI_FUT', '03069'), ('HBI_FUT', '03174'), ('HHI_FUT', 'HTI_FUT')]
HORIZONS = [5, 15, 30, 60]
SESSION_START, SESSION_END = 781, 900  # 13:01..15:00, minute starts in local HK time.
MIN_STALE_MINUTES = 5

_FUTURES_CACHE = None
_INDUSTRY_CACHE = None


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def corr(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 3 or np.std(x) == 0 or np.std(y) == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def variance(x):
    x = np.asarray(x, float)
    return float(np.var(x, ddof=1)) if len(x) > 1 else None


def es_values(x):
    x = np.asarray(x, float)
    if len(x) < 3: return None, None
    k = max(1, int(math.ceil(len(x) * 0.05)))
    s = np.sort(x)
    return float(np.mean(s[-k:])), float(abs(np.mean(s[:k])))


def canonical_code(v):
    return str(v or '').strip().replace('.HK', '').zfill(5)


def parse_bar_value(row):
    if isinstance(row, dict): return float(row.get('close', row.get('price')))
    if len(row) >= 5: return float(row[-1])
    return float(row[-1])


def asof_series(rows):
    """Convert sparse minute marks to a minute->(price, stale_minutes) map."""
    out = {}
    for r in rows or []:
        try:
            m = int(r.get('minute')) if isinstance(r, dict) else int(r[0])
            p = parse_bar_value(r)
            if p > 0: out[m] = p
        except (TypeError, ValueError, KeyError):
            continue
    return out


def aligned_price(series, minute):
    keys = [k for k in series.keys() if k <= minute]
    if not keys: return None, None
    k = max(keys); stale = minute - k
    return series[k], stale


def load_fund_raw(fid):
    code = fid.split('.')[0]
    path = OLD_RAW / f'{code}.jsonl.gz'
    rows = []
    if path.exists():
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            rows.extend(json.loads(line) for line in f if line.strip())
    if NEW_RAW.exists():
        with gzip.open(NEW_RAW, 'rt', encoding='utf-8') as f:
            for line in f:
                if not line.strip(): continue
                x = json.loads(line)
                if x.get('fund_id') == code: rows.append(x)
    by_date = {}
    for r in rows: by_date[str(r.get('date'))] = r
    return by_date


def load_assignments():
    d = json.loads((ROOT / 'outputs/hedge_rework_01_20260906/control/assignments.json').read_text(encoding='utf-8'))
    return d['C']


def load_futures():
    global _FUTURES_CACHE
    if _FUTURES_CACHE is not None:
        return _FUTURES_CACHE
    result = defaultdict(dict)
    for tid, fn in [('HSI_FUT', 'HSI_FUT_1min.csv'), ('HHI_FUT', 'HHI_FUT_1min.csv'), ('HTI_FUT', 'HTI_FUT_1min.csv')]:
        path = ROOT / 'data/raw' / fn
        if not path.exists(): continue
        with path.open(encoding='utf-8-sig', newline='') as f:
            for r in csv.DictReader(f):
                day = r.get('trade_date', '').replace('-', '')
                stamp = r.get('timestamp', '')
                try:
                    minute = int(stamp[11:13]) * 60 + int(stamp[14:16])
                    result[day].setdefault(tid, {})[minute] = float(r['close'])
                except (ValueError, KeyError): continue
    _FUTURES_CACHE = result
    return result


def load_industry():
    global _INDUSTRY_CACHE
    if _INDUSTRY_CACHE is not None:
        return _INDUSTRY_CACHE
    result = defaultdict(dict)
    if not INDUSTRY_RAW.exists():
        _INDUSTRY_CACHE = result
        return result
    with gzip.open(INDUSTRY_RAW, 'rt', encoding='utf-8') as f:
        for line in f:
            if not line.strip(): continue
            x = json.loads(line)
            day = str(x.get('day'))
            tid = x.get('tool_id')
            bars = {}
            for b in x.get('bars', []):
                try:
                    epoch = int(float(b['date']))
                    dt = datetime.fromtimestamp(epoch, timezone.utc)
                    # IBKR bars are requested at UTC endpoints and represent HK local timestamps.
                    minute = dt.hour * 60 + dt.minute + 8 * 60
                    minute %= 24 * 60
                    if SESSION_START <= minute <= 960: bars[minute] = float(b['close'])
                except (KeyError, TypeError, ValueError, OverflowError): continue
            if bars: result[day][tid] = bars
    _INDUSTRY_CACHE = result
    return result


def load_hk_and_basket(raw):
    """Return per-day basket prices and ETF-proxy tool prices from raw PCF records."""
    day_out = {}
    for day, r in raw.items():
        comps = r.get('components') or []
        qty = {}
        for c in comps:
            code = canonical_code(c.get('成分股代码') or c.get('code'))
            q = c.get('数量股', c.get('qty'))
            try: qty[code] = float(q)
            except (TypeError, ValueError): qty[code] = None
        hk = r.get('hk') or {}
        comp_series = {canonical_code(k): asof_series(v) for k, v in hk.items()}
        basket = {}
        tools = {}
        for tid in ['02800', '02828', '03032', '03033', '02845']:
            if tid in comp_series: tools[tid] = comp_series[tid]
        for minute in range(570, 961):
            total = 0.0; complete = True
            for code, q in qty.items():
                if q is None: complete = False; break
                p, stale = aligned_price(comp_series.get(code, {}), minute)
                if p is None or stale is None or stale > MIN_STALE_MINUTES:
                    complete = False; break
                total += q * p
            if complete and total > 0: basket[minute] = total
        if basket: day_out[day] = {'basket': basket, 'tools': tools, 'component_count': len(comps), 'qty_missing': sum(q is None for q in qty.values()), 'missing_components': [c for c in qty if c not in comp_series]}
    return day_out


def make_day_series(fid):
    raw = load_fund_raw(fid)
    days = load_hk_and_basket(raw)
    fut = load_futures(); industry = load_industry()
    for day, item in days.items():
        all_tools = dict(item['tools'])
        for tid, series in fut.get(day, {}).items(): all_tools[tid] = series
        for tid, series in industry.get(day, {}).items(): all_tools[tid] = series
        item['tools'] = all_tools
    return days, raw


def endpoint_samples(day_item, horizon, tool_ids):
    b = day_item['basket']; tools = day_item['tools']
    out = []
    for m in range(SESSION_START, SESSION_END - horizon + 1):
        bp0, bs0 = aligned_price(b, m); bp1, bs1 = aligned_price(b, m + horizon)
        if bp0 is None or bp1 is None or bs0 is None or bs1 is None or bs0 > MIN_STALE_MINUTES or bs1 > MIN_STALE_MINUTES: continue
        target = bp1 / bp0 - 1.0
        vals = {}
        for tid in tool_ids:
            s = tools.get(tid)
            if not s: continue
            p0, st0 = aligned_price(s, m); p1, st1 = aligned_price(s, m + horizon)
            if p0 is not None and p1 is not None and st0 is not None and st1 is not None and st0 <= MIN_STALE_MINUTES and st1 <= MIN_STALE_MINUTES and p0 > 0:
                vals[tid] = p1 / p0 - 1.0
        out.append({'minute': m, 'target': target, 'tools': vals})
    return out


def flat_samples(day_map, days, horizon, tool_ids):
    rows = []
    for d in days:
        for x in endpoint_samples(day_map[d], horizon, tool_ids): rows.append((d, x))
    return rows


def fit_beta(target, X, max_beta=2.0, max_total=2.5):
    target = np.asarray(target, float); X = np.asarray(X, float)
    if len(target) < max(20, X.shape[1] * 10): return None
    beta = np.linalg.lstsq(X, target, rcond=None)[0]
    beta = np.maximum(beta, 0.0)
    beta = np.minimum(beta, max_beta)
    if beta.sum() > max_total: beta *= max_total / beta.sum()
    return beta


def eval_policy(rows, policy):
    if not rows: return None
    usable = [r for r in rows if all(t in r[1]['tools'] for t in policy)]
    if len(usable) < max(30, len(policy) * 10): return None
    y = np.array([r[1]['target'] for r in usable])
    X = np.array([[r[1]['tools'][t] for t in policy] for r in usable])
    beta = fit_beta(y, X)
    if beta is None: return None
    hedge = X @ beta; residual = y - hedge
    return {'tools': list(policy), 'beta': beta.tolist(), 'target': y, 'hedge': hedge, 'residual': residual,
            'rho': corr(y, hedge), 'target_var': variance(y), 'residual_var': variance(residual),
            'target_std_bp': float(np.std(y, ddof=1) * 1e4), 'residual_std_bp': float(np.std(residual, ddof=1) * 1e4),
            'residual_mean_bp': float(np.mean(residual) * 1e4), 'sample_count': len(y), 'days': len(set(d for d, _ in usable))}


def bootstrap_corr(day_rows, policy, beta, reps=1000, seed=520760):
    groups = defaultdict(list)
    for day, row in day_rows:
        if all(t in row['tools'] for t in policy):
            target = row['target']; hedge = sum(b * row['tools'][t] for b, t in zip(beta, policy))
            groups[day].append((target, hedge))
    if len(groups) < 3: return None, None
    days = list(groups); rng = np.random.default_rng(seed)
    vals = []
    for _ in range(reps):
        picked = rng.choice(days, size=len(days), replace=True)
        yy = []; xx = []
        for d in picked:
            for y, x in groups[d]: yy.append(y); xx.append(x)
        c = corr(yy, xx)
        if c is not None: vals.append(c)
    if not vals: return None, None
    return float(np.quantile(vals, .025)), float(np.quantile(vals, .975))


def bootstrap_corr_arrays(z, reps=1000, seed=520760):
    """Day-block bootstrap for already aggregated target/hedge returns."""
    groups = defaultdict(list)
    for row in z:
        groups[str(row['date'])].append((float(row['target_bp']), float(row['hedge_bp'])))
    if len(groups) < 3:
        return None, None
    days = list(groups)
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(reps):
        picked = rng.choice(days, size=len(days), replace=True)
        yy, xx = [], []
        for d in picked:
            for y, x in groups[d]:
                yy.append(y); xx.append(x)
        c = corr(yy, xx)
        if c is not None:
            vals.append(c)
    if not vals:
        return None, None
    return float(np.quantile(vals, .025)), float(np.quantile(vals, .975))


def policy_space():
    policies = [tuple([t]) for t in TOOLS]
    policies += [tuple(x) for x in PAIRS]
    return policies


def choose_policy(validation, all_candidates):
    eligible = [x for x in all_candidates if x and x.get('rho') is not None and x['rho'] >= 0.60 and x.get('residual_var') is not None and x.get('target_var') is not None and x['residual_var'] < x['target_var']]
    if eligible:
        singles = [x for x in eligible if len(x['tools']) == 1]
        pool = singles or eligible
        return sorted(pool, key=lambda x: (len(x['tools']), x['residual_var'], ''.join(x['tools'])))[0], 'RHO_GE_060_AND_RESIDUAL_VAR_REDUCED'
    rho_only = [x for x in all_candidates if x and x.get('rho') is not None and x['rho'] >= 0.60]
    if rho_only:
        return sorted(rho_only, key=lambda x: (-x['rho'], len(x['tools']), x.get('residual_var') or 1e9))[0], 'RHO_GE_060_BUT_RESIDUAL_NOT_REDUCED'
    return None, 'NO_RHO_GE_060_POLICY'


def serial_result(x):
    if not x: return None
    out = {k: v for k, v in x.items() if k not in ('target', 'hedge', 'residual')}
    for k in ('target_var', 'residual_var'): out[k] = None if out.get(k) is None else float(out[k])
    return out


def run_fund(item):
    fid = item['fund_id']; day_map, raw = make_day_series(fid); days = sorted(day_map)
    if not days: return {'fund_id': fid, 'days': [], 'rows': [], 'policies': [], 'weights': [], 'gates': [], 'coverage': {'raw_days': len(raw), 'basket_days': 0}}
    policies = policy_space(); rows_out=[]; weights=[]; gates=[]; policy_summaries=[]
    for h in HORIZONS:
        daily_rows = {d: endpoint_samples(day_map[d], h, TOOLS) for d in days}
        usable_days = [d for d in days if daily_rows[d]]
        if len(usable_days) <= 70:
            gates.append({'fund_id': fid, 'horizon_min': h, 'gate': 'ROLLING_60_50_10_AVAILABLE', 'actual': len(usable_days), 'threshold': 71, 'status': 'NOT_RUN'})
            # Still provide a direct exploratory comparison on all available endpoints.
            all_rows = [(d, x) for d in usable_days for x in daily_rows[d]]
            for p in policies:
                ev = eval_policy(all_rows, p)
                if ev: policy_summaries.append({'fund_id': fid, 'horizon_min': h, 'window': 'ALL_AVAILABLE_EXPLORATORY', 'policy': serial_result(ev), 'selection_status': 'EXPLORATORY'})
            # These are explicit NOT_RUN gates: exploratory fit results are not
            # allowed to masquerade as OOS confirmation.
            for gate, threshold in [('HEDGE_RETURN_CORRELATION_GE_060', 0.60), ('RESIDUAL_VARIANCE_REDUCED', 'no_hedge variance'), ('NEW_CONFIRMATION_MIN_20_DAYS', 20)]:
                gates.append({'fund_id': fid, 'horizon_min': h, 'gate': gate, 'threshold': threshold, 'actual': None, 'status': 'NOT_RUN'})
            continue
        gates.append({'fund_id': fid, 'horizon_min': h, 'gate': 'ROLLING_60_50_10_AVAILABLE', 'actual': len(usable_days), 'threshold': 71, 'status': 'PASS'})
        for idx in range(70, len(usable_days)):
            test_day = usable_days[idx]; fit_days = usable_days[idx-70:idx-20]; val_days = usable_days[idx-20:idx]
            fit_rows = [(d, x) for d in fit_days for x in daily_rows[d]]; val_rows = [(d, x) for d in val_days for x in daily_rows[d]]
            val_candidates = [eval_policy(val_rows, p) for p in policies]
            selected_val, selection_status = choose_policy(val_candidates, val_candidates)
            if not selected_val: continue
            # Refit the selected policy on the complete 60-day history before the OOS day.
            full_rows = [(d, x) for d in usable_days[idx-60:idx] for x in daily_rows[d]]
            refit = eval_policy(full_rows, tuple(selected_val['tools']))
            if not refit: continue
            beta = refit['beta']; oos_rows = daily_rows[test_day]
            usable_oos = [x for x in oos_rows if all(t in x['tools'] for t in refit['tools'])]
            if not usable_oos: continue
            weights.append({'fund_id': fid, 'horizon_min': h, 'test_date': test_day, 'fit_start': usable_days[idx-60], 'fit_end': usable_days[idx-1], 'validation_start': val_days[0], 'validation_end': val_days[-1], 'policy_id': '+'.join(refit['tools']), 'tools': refit['tools'], 'beta': beta, 'selection_status': selection_status})
            for x in usable_oos:
                hedge = sum(b * x['tools'][t] for b, t in zip(beta, refit['tools']))
                rows_out.append({'fund_id': fid, 'date': test_day, 'horizon_min': h, 'policy_id': '+'.join(refit['tools']), 'minute': x['minute'], 'target_bp': x['target'] * 1e4, 'hedge_bp': hedge * 1e4, 'residual_bp': (x['target'] - hedge) * 1e4, 'beta': beta, 'sample_status': 'NEW_EXPLORATORY' if test_day >= '20260804' else 'REUSED_OR_UNPROVEN'})
        # OOS aggregate by selected policy on the same endpoints.
        for pol in sorted(set(r['policy_id'] for r in rows_out if r['fund_id'] == fid and r['horizon_min'] == h)):
            z = [r for r in rows_out if r['fund_id'] == fid and r['horizon_min'] == h and r['policy_id'] == pol]
            if not z: continue
            y = np.array([r['target_bp'] for r in z]); x = np.array([r['hedge_bp'] for r in z]); res = np.array([r['residual_bp'] for r in z])
            rho = corr(y, x); tvar=variance(y); rvar=variance(res); up_t, down_t=es_values(y); up_r, down_r=es_values(res)
            ci_l, ci_h = bootstrap_corr_arrays(z, reps=500, seed=520760+h)
            policy_summaries.append({'fund_id': fid, 'horizon_min': h, 'window': 'OOS_AGGREGATE', 'policy_id': pol, 'sample_status': 'NEW_EXPLORATORY' if any(r['sample_status']=='NEW_EXPLORATORY' for r in z) else 'REUSED_OR_UNPROVEN', 'oos_days': len(set(r['date'] for r in z)), 'sample_count': len(z), 'hedge_return_correlation': rho, 'correlation_ci_low': ci_l, 'correlation_ci_high': ci_h, 'correlation_method': 'Pearson OOS', 'correlation_threshold': 0.60, 'target_std_bp': float(np.std(y, ddof=1)), 'residual_std_bp': float(np.std(res, ddof=1)), 'variance_reduction': None if tvar in (None,0) else float(1-rvar/tvar), 'up_es95_bp': up_r, 'down_es95_bp': down_r, 'target_up_es95_bp': up_t, 'target_down_es95_bp': down_t, 'residual_mean_bp': float(np.mean(res)), 'selection_rule': 'rolling validation; single-leg preferred when rho>=0.60 and residual variance reduced'})
    for h in HORIZONS:
        new_days = len({r['date'] for r in rows_out if r['horizon_min']==h and r['sample_status']=='NEW_EXPLORATORY'})
        gates.append({'fund_id': fid, 'horizon_min': h, 'gate': 'HEDGE_RETURN_CORRELATION_GE_060', 'threshold': 0.60, 'actual': max([p.get('hedge_return_correlation') for p in policy_summaries if p.get('horizon_min')==h and p.get('window')=='OOS_AGGREGATE' and p.get('hedge_return_correlation') is not None] or [None]), 'status': 'PASS' if any(p.get('horizon_min')==h and p.get('window')=='OOS_AGGREGATE' and p.get('hedge_return_correlation') is not None and p['hedge_return_correlation']>=.60 for p in policy_summaries) else 'FAIL'})
        vr_values = [p.get('variance_reduction') for p in policy_summaries if p.get('horizon_min') == h and p.get('window') == 'OOS_AGGREGATE' and p.get('variance_reduction') is not None]
        gates.append({'fund_id': fid, 'horizon_min': h, 'gate': 'RESIDUAL_VARIANCE_REDUCED', 'threshold': '> 0 vs no-hedge variance', 'actual': max(vr_values) if vr_values else None, 'status': 'PASS' if any(v > 0 for v in vr_values) else 'FAIL'})
        gates.append({'fund_id': fid, 'horizon_min': h, 'gate': 'NEW_CONFIRMATION_MIN_20_DAYS', 'threshold': 20, 'actual': new_days, 'status': 'PASS' if new_days>=20 else 'FAIL'})
    return {'fund_id': fid, 'days': days, 'rows': rows_out, 'policies': policy_summaries, 'weights': weights, 'gates': gates, 'coverage': {'raw_days': len(raw), 'basket_days': len(days), 'new_pcf_days': len([d for d in days if d >= '20260804']), 'component_count_by_day': {d: day_map[d]['component_count'] for d in days[-5:]}}}


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=lambda x: x.item() if hasattr(x,'item') else x), encoding='utf-8')


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text('', encoding='utf-8-sig'); return
    fields=[]
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w=csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for r in rows:
            x=dict(r)
            for k,v in list(x.items()):
                if isinstance(v,(list,dict)): x[k]=json.dumps(v,ensure_ascii=False)
            w.writerow(x)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--funds', nargs='*', help='Optional fund ids for a focused run; default is all C assignments.')
    ap.add_argument('--max-funds', type=int, default=None)
    args = ap.parse_args()
    started=now(); assignments=load_assignments(); results=[]
    if args.funds:
        wanted = set(args.funds)
        assignments = [x for x in assignments if x['fund_id'] in wanted]
    if args.max_funds is not None:
        assignments = assignments[:args.max_funds]
    for item in assignments:
        results.append(run_fund(item))
    all_policies=[p for r in results for p in r['policies']]; all_weights=[w for r in results for w in r['weights']]; all_gates=[g for r in results for g in r['gates']]; all_rows=[x for r in results for x in r['rows']]
    run={'run_id': 'C-R1-BASKET-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'), 'started_at_utc': started, 'finished_at_utc': now(), 'criteria_version':'USER_RHO_060_FUTURES_ETF', 'selection_lock':str(LOCK), 'selection_lock_sha256': json.loads(LOCK.read_text(encoding='utf8'))['lock_sha256'], 'candidate_pool':TOOLS, 'horizons_min':HORIZONS, 'session':'13:01..15:00 Asia/Hong_Kong', 'fund_count':len(assignments), 'funds_with_basket_days':sum(bool(r['days']) for r in results), 'total_oos_rows':len(all_rows), 'total_policy_rows':len(all_policies), 'total_weight_rows':len(all_weights)}
    write_json(REWORK/'results/r1_basket_run.json',run); write_json(REWORK/'results/r1_fund_runs.json',results); write_csv(REWORK/'results/r1_oos_residuals.csv',all_rows); write_csv(REWORK/'results/r1_exploratory_policies.csv',all_policies); write_csv(REWORK/'results/r1_daily_weights.csv',all_weights); write_csv(REWORK/'results/r1_research_gates.csv',all_gates)
    print(json.dumps(run,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
