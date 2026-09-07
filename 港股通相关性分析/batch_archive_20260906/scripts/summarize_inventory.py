#!/usr/bin/env python3
"""Rebuild lightweight inventory outputs from cached evidence; no remote access."""
import csv
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
folder = root / 'data/inventory'
p = json.loads((folder / 'remote_inventory.json').read_text())
assert p['scope'].startswith('file availability')
sets = {k: {x['date'] for x in v} for k, v in p['files'].items()}
assert sorted(set.intersection(*sets.values())) == p['common_dates']
with (folder / 'candidate_universe.csv').open('w', encoding='utf-8-sig', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=list(p['candidates'][0]))
    writer.writeheader(); writer.writerows(p['candidates'])
with (folder / 'date_coverage.csv').open('w', newline='') as f:
    writer = csv.writer(f); writer.writerow(['date', *sets, 'all_three_files_present'])
    for day in sorted(set.union(*sets.values())):
        if day < '20250101': continue
        flags = [int(day in dates) for dates in sets.values()]
        writer.writerow([day, *flags, int(all(flags))])
print(json.dumps({'candidate_count': len(p['candidates']), 'common_file_days': len(p['common_dates']),
                  'first_common_day': p['common_dates'][0], 'last_common_day': p['common_dates'][-1],
                  'sampled_dates': len(p['samples'])}, ensure_ascii=False, indent=2))
