#!/usr/bin/env python3
"""Create the pre-registration lock for C's R1 ETF/futures candidate study."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/Users/ellis/工具程序开发/港股通相关性分析')
CONTROL = ROOT / 'outputs/hedge_rework_01_20260906/control'
OUT = ROOT / 'outputs/hedge_rework_01_20260906/agent_C'


def sha(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    now = datetime.now(timezone.utc).isoformat()
    base = {
        'lock_id': 'C-R1-20260906-rho060', 'created_at_utc': now, 'owner': 'C',
        'criteria_version': 'USER_RHO_060_FUTURES_ETF',
        'target_window': {'start': '2026-08-04', 'end': '2026-09-04', 'timezone': 'Asia/Hong_Kong'},
        'representative_fund': {'fund_id': '520760.SH', 'selection_rule': 'pre-registered by index/fund assignment and data availability before strategy comparison', 'index_family': 'HSBIO'},
        'horizons_min': [5, 15, 30, 60], 'primary_horizon_min': 30,
        'basket_definition': 'official daily PCF quantities; each component uses its own same-day HK 1-minute price marks; missing price/quantity is excluded from usable endpoints and never filled with a fixed amount',
        'cash_substitution': 'forced cash substitution research assumption; preserve original PCF cash-substitution flags as factual fields',
        'session': '13:01..15:00 Asia/Hong_Kong; minute-start labels; hedge mark at label+1; no cross-day or lunch-break labels',
        'fx': 'HKD/CNY settlement FX evaluated after the fact only; same-currency intraday returns do not use it as a live signal',
        'candidate_pool': [
            {'tool_id': 'HBI_FUT', 'family': 'HSBIO', 'asset_type': 'HKFE_FUTURES', 'role': 'industry_primary_candidate'},
            {'tool_id': 'HSI_FUT', 'family': 'HSI', 'asset_type': 'HKFE_FUTURES', 'role': 'broad_backup'},
            {'tool_id': 'HHI_FUT', 'family': 'HHI', 'asset_type': 'HKFE_FUTURES', 'role': 'broad_backup'},
            {'tool_id': 'HTI_FUT', 'family': 'HSTECH', 'asset_type': 'HKFE_FUTURES', 'role': 'technology_backup'},
            {'tool_id': '03069', 'family': 'HSBIO', 'asset_type': 'HKEX_ETF', 'role': 'industry_primary_candidate'},
            {'tool_id': '03174', 'family': 'HSBIO', 'asset_type': 'HKEX_ETF', 'role': 'industry_primary_candidate'},
            {'tool_id': '02800', 'family': 'HSI', 'asset_type': 'HKEX_ETF', 'role': 'broad_backup'},
            {'tool_id': '02828', 'family': 'HHI', 'asset_type': 'HKEX_ETF', 'role': 'broad_backup'},
            {'tool_id': '03032', 'family': 'HSTECH', 'asset_type': 'HKEX_ETF', 'role': 'technology_backup'},
            {'tool_id': '03033', 'family': 'HSTECH', 'asset_type': 'HKEX_ETF', 'role': 'technology_backup'},
            {'tool_id': '02845', 'family': 'EV_PROXY', 'asset_type': 'HKEX_ETF', 'role': 'exploratory_only'},
        ],
        'policies': {
            'no_hedge': {'tools': [], 'kind': 'baseline'},
            'single_leg': {'tools': 'each candidate separately', 'kind': 'primary_comparator'},
            'two_leg': {'tools': [['HBI_FUT', 'HHI_FUT'], ['HBI_FUT', 'HTI_FUT'], ['HBI_FUT', '03069'], ['HBI_FUT', '03174'], ['HHI_FUT', 'HTI_FUT']], 'kind': 'backup_only', 'constraints': 'nonnegative beta; beta_i<=2; sum(beta)<=2.5; only if both legs have same endpoint and price risk is reduced'},
        },
        'selection_rule': {
            'correlation': 'OOS Pearson correlation of basket return and forward-risk hedge proxy; rho>=0.60 is the user threshold; do not use abs(rho)',
            'risk': 'nominal regression weights must reduce residual variance versus no_hedge on the same OOS endpoints',
            'preference': 'if any simple single leg meets rho>=0.60 and reduces residual variance, prefer single leg; then lower verified cost / stability; multi-leg only as a clearly labelled backup if materially better',
            'tie_break': 'same leg count: lower evidenced cost, then lower validation residual, then model id',
            'confirmation_sample': 'minimum 20 effective new OOS days; insufficient samples produce exploratory output only',
        },
        'diagnostic_only': ['bootstrap Pearson interval', 'variance reduction', 'two-sided ES95', 'positive non-overlapping 5-day blocks', 'quote coverage', 'stale marks', 'cost scenarios'],
        'not_claimed': ['rho>=0.60 is not 60% variance reduction', 'no fixed cost/borrow/funding assumptions without evidence', 'no full-universe no-solution claim from incomplete data'],
        'sources': {
            'assignments': {'path': str(CONTROL / 'assignments.json'), 'sha256': sha(CONTROL / 'assignments.json')},
            'user_criteria': {'path': str(CONTROL / 'USER_CRITERIA.md'), 'sha256': sha(CONTROL / 'USER_CRITERIA.md')},
            'rework_contract': {'path': str(CONTROL / 'REWORK_CONTRACT.md'), 'sha256': sha(CONTROL / 'REWORK_CONTRACT.md')},
            'schema_base': {'path': str(CONTROL / 'schema_base.json'), 'sha256': sha(CONTROL / 'schema_base.json')},
            'delivery_additions': {'path': str(CONTROL / 'DELIVERY_ADDITIONS.md'), 'sha256': sha(CONTROL / 'DELIVERY_ADDITIONS.md')},
            'method_lock': {'path': str(CONTROL / 'method_lock.json'), 'sha256': sha(CONTROL / 'method_lock.json')},
        },
    }
    canonical = json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    base['lock_sha256'] = hashlib.sha256(canonical).hexdigest()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / 'selection_lock_R1.json'
    path.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'path': str(path), 'lock_sha256': base['lock_sha256']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
