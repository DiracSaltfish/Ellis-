#!/usr/bin/env python3
"""Freeze C's complete candidate map before any scoring is performed."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/Users/ellis/工具程序开发/港股通相关性分析')
FULL = ROOT / 'outputs/hedge_full_coverage_20260906/agent_C'
B_ENGINE = ROOT / 'outputs/hedge_full_coverage_20260906/agent_B/scripts/selection_core.py'
CONTRACT = ROOT / 'outputs/hedge_full_coverage_20260906/control/FULL_COVERAGE_CONTRACT.md'
SCHEMA = ROOT / 'outputs/hedge_full_coverage_20260906/control/workbook_schema.json'
ASSIGNMENTS = ROOT / 'outputs/hedge_full_coverage_20260906/control/assignments.json'
MAP = FULL / 'data/candidate_map.jsonl'


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    rows = [json.loads(x) for x in MAP.read_text(encoding='utf-8').splitlines() if x.strip()]
    by_fund: dict[str, dict] = {}
    for r in rows:
        by_fund.setdefault(r['fund_id'], {'fund_id': r['fund_id'], 'targets': {}})
        by_fund[r['fund_id']]['targets'].setdefault(r['target_type'], []).append({
            'candidate_id': r['candidate_id'],
            'asset_type': r['asset_type'],
            'risk_family': r['risk_family'],
            'economic_reason': r['economic_reason'],
        })
    assignments = json.loads(ASSIGNMENTS.read_text(encoding='utf-8'))['C']
    expected = [x['fund_id'] for x in assignments]
    actual = sorted(by_fund)
    if sorted(expected) != actual:
        raise SystemExit(f'candidate map fund mismatch expected={len(expected)} actual={len(actual)}')
    lock = {
        'lock_id': 'FULL-C-SELECTION-20260906',
        'owner': 'C',
        'locked_at_utc': datetime.now(timezone.utc).isoformat(),
        'engine_version': 'FULL237_RHO060_V1',
        'engine_path': str(B_ENGINE),
        'engine_sha256': sha(B_ENGINE),
        'contract_sha256': sha(CONTRACT),
        'workbook_schema_sha256': sha(SCHEMA),
        'assignments_sha256': sha(ASSIGNMENTS),
        'candidate_map_sha256': sha(MAP),
        'fund_count': len(actual),
        'candidate_policy': 'all declared singles plus cross-family pairs; constrained beta 0<=leg<=2 and total<=2; single-leg preferred when validation rho>=0.60 and variance reduction>0',
        'target_path_policy': ['PCF_BASKET', 'ETF_MARKET_PRICE', 'INDEX_STRUCTURAL'],
        'staleness_max_minutes': 2,
        'session_policy': '09:30-11:30 and 13:00-15:00 Asia/Hong_Kong; same-day same-session only',
        'rolling_policy': 'prior 60 valid days: first 50 fit, next 10 validation; freeze selected coefficients for next-day OOS; refit selected policy on prior full 60',
        'funds': [by_fund[fid] for fid in expected],
    }
    payload = json.dumps(lock, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    lock['lock_sha256'] = hashlib.sha256(payload).hexdigest()
    out = FULL / 'selection_lock_full_C.json'
    out.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'path': str(out), 'lock_sha256': lock['lock_sha256'], 'fund_count': len(actual), 'candidate_rows': len(rows)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
