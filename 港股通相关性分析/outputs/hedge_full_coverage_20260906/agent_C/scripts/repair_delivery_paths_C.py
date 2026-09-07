#!/usr/bin/env python3
"""Repair machine references to the actual C-owned residual/weight files."""
from pathlib import Path
import json

ROOT = Path('/Users/ellis/工具程序开发/港股通相关性分析')
FULL = ROOT / 'outputs/hedge_full_coverage_20260906/agent_C'

def main():
    p = FULL / 'target_results.jsonl'
    rows = [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines() if x.strip()]
    for x in rows:
        x['weights_path'] = str(FULL / 'daily_weights.jsonl')
        x['residual_path'] = str(FULL / 'residuals.jsonl')
    with p.open('w', encoding='utf-8') as f:
        for x in rows: f.write(json.dumps(x, ensure_ascii=False, separators=(',', ':')) + '\n')
    mpath = FULL / 'mapping.json'; mapping = json.loads(mpath.read_text(encoding='utf-8'))
    for x in mapping:
        if x.get('result_path'):
            x['result_path'] = str(FULL / 'residuals.jsonl')
    mpath.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'target_rows': len(rows), 'mapping_rows': len(mapping), 'path': str(FULL / 'residuals.jsonl')}, ensure_ascii=False))

if __name__ == '__main__': main()
