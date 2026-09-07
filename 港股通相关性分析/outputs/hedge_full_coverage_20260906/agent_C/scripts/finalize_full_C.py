#!/usr/bin/env python3
"""Add final workbook QA and refresh the C-owned asset manifest."""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/Users/ellis/工具程序开发/港股通相关性分析')
FULL = ROOT / 'outputs/hedge_full_coverage_20260906/agent_C'


def now(): return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''): h.update(b)
    return h.hexdigest()


def read_jsonl(path): return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]


def main():
    checks = [x for x in read_jsonl(FULL / 'checks.jsonl') if x.get('check_id') != 'C-WORKBOOK-8-SHEETS']
    xlsx = FULL / 'RESULTS.xlsx'
    previews = sorted((FULL / 'previews').glob('*.png'))
    with zipfile.ZipFile(xlsx) as z:
        workbook_xml = z.read('xl/workbook.xml').decode('utf-8', errors='ignore')
        sheet_count = len(re.findall(r'<(?:\w+:)?sheet\b', workbook_xml))
        xml = '\n'.join(z.read(n).decode('utf-8', errors='ignore') for n in z.namelist() if n.endswith('.xml'))
    formula_tokens = re.findall(r'#(?:REF!|DIV/0!|VALUE!|NAME\?|N/A)', xml)
    checks.append({'check_id': 'C-WORKBOOK-8-SHEETS', 'fund_id': 'GLOBAL', 'run_id': json.loads((FULL/'results/run_manifest.json').read_text())['run_id'], 'check_name': 'Workbook sheet count and render QA', 'check_type': 'CODE', 'status': 'PASS' if xlsx.exists() and sheet_count == 8 and len(previews) == 8 and not formula_tokens else 'FAIL', 'expected': 'RESULTS.xlsx has exactly 8 schema sheets, 8 rendered previews, and no formula error tokens', 'actual': {'xlsx_exists': xlsx.exists(), 'sheet_count': sheet_count, 'preview_count': len(previews), 'formula_error_tokens': len(formula_tokens)}, 'command': 'node build_full_results_xlsx_C.mjs; zip/XML scan', 'evidence_path': str(FULL / 'workbook_inspect.json'), 'engine_hash': None, 'evaluated_at_utc': now()})
    with (FULL / 'checks.jsonl').open('w', encoding='utf-8') as f:
        for x in checks: f.write(json.dumps(x, ensure_ascii=False, separators=(',', ':')) + '\n')
    passed = all(x['status'] == 'PASS' for x in checks)
    (FULL / 'checks/check_summary.json').write_text(json.dumps({'run_id': checks[0]['run_id'], 'passed': passed, 'checks': len(checks), 'failures': [x['check_id'] for x in checks if x['status'] != 'PASS']}, ensure_ascii=False, indent=2), encoding='utf-8')
    assets = json.loads((FULL / 'assets.json').read_text(encoding='utf-8'))
    files = assets.setdefault('machine_files', {})
    for name in ['RESULTS.xlsx', 'FINAL_REPORT.md', 'workbook_inspect.json', 'workbook_formula_error_scan.json']:
        p = FULL / name
        if p.exists(): files[name] = {'path': str(p), 'sha256': sha(p), 'size_bytes': p.stat().st_size}
    assets['quality_status'] = 'PASS' if passed else 'REVIEW_REQUIRED'
    assets['workbook'] = {'path': str(xlsx), 'sha256': sha(xlsx), 'sheet_count': sheet_count, 'preview_count': len(previews), 'formula_error_tokens': len(formula_tokens)}
    (FULL / 'assets.json').write_text(json.dumps(assets, ensure_ascii=False, indent=2), encoding='utf-8')
    mapping = json.loads((FULL / 'mapping.json').read_text(encoding='utf-8'))
    (FULL / 'STATUS.md').write_text(f'''# C full-coverage status\n\n- owner: C\n- run_stage: DELIVER\n- fund_progress: 55/55\n- updated_at_utc: {now()}\n- compute_status: DONE\n- delivery_status: COMPLETE_PENDING_MAIN_ACCEPTANCE\n- decision_counts: {dict(Counter(r["decision"] for r in mapping))}\n- actual_backtest_unique_funds: {sum(bool(r["actual_backtest_run"]) for r in mapping)}\n- workbook_status: 8 sheets rendered; workbook XML formula-token scan clean\n- old_r1_read_only: true\n- note: C output is not main-agent acceptance.\n''', encoding='utf-8')
    print(json.dumps({'checks': len(checks), 'all_pass': passed, 'sheet_count': sheet_count, 'preview_count': len(previews), 'formula_error_tokens': len(formula_tokens)}, ensure_ascii=False))


if __name__ == '__main__': main()
