#!/usr/bin/env python3
"""Create the pre-registered candidate/method lock for agent C."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/Users/ellis/工具程序开发/港股通相关性分析')
OUT = ROOT / 'outputs/hedge_selection_v2_20260906/agent_C'
CTRL = ROOT / 'outputs/hedge_selection_v2_20260906/control'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    input_paths = {
        'assignment_C': CTRL / 'assignment_C.json',
        'schema': CTRL / 'schema.json',
        'workflow': CTRL / 'WORKFLOW.md',
        'prior_acceptance': ROOT / 'outputs/final_review_20260906/最终结论与验收.md',
        'candidate_universe': ROOT / 'data/inventory/candidate_universe.csv',
        'contract_map': ROOT / 'data/inventory/futures_contract_probe.csv',
        'local_source_manifest': ROOT / 'data/raw/local_source_manifest.json',
        'remote_inventory_snapshot': Path('/tmp/hedge_v2_20260906_C_remote_inventory.json'),
        'remote_C_target_audit': Path('/tmp/hedge_v2_20260906_C_remote_c_audit.json'),
        'extraction_script': Path('/Users/ellis/.codex/worktrees/e135/工具程序开发/extract_new_period_remote.py'),
        'new_period_bundle': OUT / 'data/new_period_c_pcf_hk.jsonl.gz',
        'candidate_probe_script': Path('/Users/ellis/.codex/worktrees/e135/工具程序开发/probe_c_candidates.py'),
        'candidate_probe': OUT / 'data/tws_candidate_probe.json',
    }
    sources = {k: {'path': str(v), 'sha256': sha(v)} for k, v in input_paths.items() if v.exists()}
    payload = {
        'lock_id': 'C-20260906-v2',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'owner': 'C',
        'target_window': {'start': '2026-08-04', 'end': '2026-09-04', 'timezone': 'Asia/Hong_Kong'},
        'primary_horizon_min': 30,
        'secondary_horizons_min': [5, 15, 60],
        'sample_rules': {
            'training_days': 60,
            'inner_fit_days': 50,
            'inner_validation_days': 10,
            'minimum_confirmation_oos_days': 20,
            'main_session': '13:01..15:00 Asia/Hong_Kong; interval labels are minute starts; hedge price usable at label+1',
            'quote_age_max_min': 2,
            'stale_nominal_max': 0.02,
            'unexplained_missing_nominal_max': 0.005,
            'cash_substitution': 'forced cash substitution only when PCF quantity and source prices are available; no fixed replacement amount',
        },
        'candidate_pool': [
            {'tool_id': 'HSI_FUT', 'risk_family': 'HSI', 'asset_type': 'HKFE_futures', 'status': 'locked_core'},
            {'tool_id': 'HHI_FUT', 'risk_family': 'HHI', 'asset_type': 'HKFE_futures', 'status': 'locked_core'},
            {'tool_id': 'HTI_FUT', 'risk_family': 'HSTECH', 'asset_type': 'HKFE_futures', 'status': 'locked_core'},
            {'tool_id': '02800', 'risk_family': 'HSI', 'asset_type': 'HKEX_ETF', 'status': 'locked_core'},
            {'tool_id': '02828', 'risk_family': 'HHI', 'asset_type': 'HKEX_ETF', 'status': 'locked_core'},
            {'tool_id': '03032', 'risk_family': 'HSTECH', 'asset_type': 'HKEX_ETF', 'status': 'locked_core'},
            {'tool_id': '03033', 'risk_family': 'HSTECH', 'asset_type': 'HKEX_ETF', 'status': 'locked_core'},
            {'tool_id': '02845', 'risk_family': 'EV_PROXY', 'asset_type': 'HKEX_ETF', 'status': 'locked_core_exploratory'},
            {'tool_id': 'HBI_FUT', 'risk_family': 'HSBIO', 'asset_type': 'HKFE_futures', 'status': 'industry_candidate_pending_quote_probe'},
            {'tool_id': '03069', 'risk_family': 'HSBIO', 'asset_type': 'HKEX_ETF', 'status': 'industry_candidate_pending_quote_probe'},
            {'tool_id': '03174', 'risk_family': 'HSBIO', 'asset_type': 'HKEX_ETF', 'status': 'industry_candidate_pending_quote_probe'},
        ],
        'model_grid': {
            'baselines': ['no_hedge', 'single_leg_ols_ridge'],
            'pairs': 'at most two legs; only cross-family pairs; nonnegative beta; each beta <=2; total beta <=2.5',
            'selection': 'validation variance reduction >=50% first, then residual score; 1 bootstrap SE tie tolerance; fewer legs, lower evidenced cost, then model id',
            'confirmation_gate': 'VR >=50%, block bootstrap 95% CI lower >=30%, two-sided ES no worse, positive 5-day-block fraction >=70%, strict refit VR >=40%, quote/event/data gates pass',
            'industry_candidates': 'audited for official existence but excluded from confirmation until actual minute quote history is available; no full-universe none conclusion',
        },
        'official_candidate_sources': [
            'https://www.hkex.com.hk/Products/Listed-Derivatives/Equity-Index/Hang-Seng-Biotech-index/Hang-Seng-Biotech-Index-Futures?sc_lang=en',
            'https://ifp.hkex.hk/fund-repository/fund/BQQ795',
            'https://www.hkex.com.hk/-/media/HKEX-Market/Products/Securities/ETP/ETF-and-L-and-I-Product-Market-Perspective/2026/ETFLIProductMarketPerspective_2026-Apr.pdf',
            'https://www.hkex.com.hk/eng/market/sec_tradinfo/ds20260630.htm',
        ],
        'source_hashes': sources,
        'not_claimed': ['global optimum', 'executable arbitrage profit', 'full market candidate coverage', 'independent confirmation before target-window evidence exists'],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    payload['selection_lock_sha256'] = hashlib.sha256(canonical).hexdigest()
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / 'selection_lock.json.tmp'
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(OUT / 'selection_lock.json')
    print(OUT / 'selection_lock.json')
    print(payload['selection_lock_sha256'])


if __name__ == '__main__':
    main()
