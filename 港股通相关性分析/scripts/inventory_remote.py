#!/usr/bin/env python3
"""Read-only, standard-library data audit. Run on machome; stdout is JSON.
Only directory metadata, ZIP indexes and selected CSV members are read.
No source files are rewritten; no trading/account rows are exported.
"""
import argparse
import csv
import io
import json
import re
import statistics
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def rows(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        yield from csv.DictReader(f)


def dates(paths):
    return {p.name[:8]: p for p in paths if re.match(r'^\d{8}', p.name)}


def member_profile(z, member, kind):
    with z.open(member) as f:
        rr = csv.DictReader(io.TextIOWrapper(f, encoding='utf-8-sig'))
        fields = rr.fieldnames
        ts, prices, ratios = [], [], []
        bad = 0
        for row in rr:
            ts.append(row.get('时间', ''))
            try:
                price = float(row['价格' if kind == 'hk' else '收盘价'])
                prices.append(price)
                if price <= 0: bad += 1
                if kind == 'cn':
                    v = float(row['成交量']); a = float(row['成交额'])
                    if v > 0 and price > 0: ratios.append(a / (v * price))
            except (ValueError, KeyError): bad += 1
        return {'member': member, 'fields': fields, 'rows': len(ts),
                'min_time': min(ts, default=None), 'max_time': max(ts, default=None),
                'distinct_time_labels': len(set(ts)), 'nonpositive_or_invalid_prices': bad,
                'time_order_violations': sum(a > b for a, b in zip(ts, ts[1:])),
                'median_amount_over_volume_close': statistics.median(ratios) if ratios else None,
                'minute_precision_only': bool(ts) and all(re.search(r'\d\d:\d\d$', t) for t in ts)}


def audit(root):
    cn = dates((root / '基金_分钟数据/ETF_分钟数据/1分钟_按月归档').glob('*/*.zip'))
    hk = dates((root / '港股_分笔成交/港股_分笔成交_按月归档').glob('*/*.zip'))
    pcf = dates((root / 'PCF导出CSV').glob('*_明细.csv'))
    common = sorted(set(cn) & set(hk) & set(pcf))
    out = {'generated_at': datetime.now(timezone.utc).isoformat(), 'root': str(root),
           'scope': 'file availability plus sampled contents; not full-history completeness certification',
           'files': {}, 'common_dates': common, 'candidates': [], 'samples': [], 'auxiliary': []}
    for name, mapping in [('cn_etf_1m', cn), ('hk_trades', hk), ('pcf_detail', pcf)]:
        out['files'][name] = [{'date': d, 'path': str(p.relative_to(root)), 'bytes': p.stat().st_size,
                               'mtime_ns': p.stat().st_mtime_ns} for d, p in sorted(mapping.items())]
    base = list(rows(root / '基金_分钟数据/ETF基础信息列表.csv'))
    for row in base:
        text = ' '.join(row.get(k, '') for k in ['ETF简称', '基金全称', '跟踪指数名称'])
        if re.search('港股|恒生|恒指|香港|中概', text):
            out['candidates'].append({**row, 'classification_status': 'name_discovery_only'})
    selected = {'520600', '513180', '513130', '513060', '513330', '159920', '513090'}
    sample_dates = sorted(set(common[::max(1, len(common)//10)] + common[-3:] + ['20260813']))
    for d in sample_dates:
        item = {'date': d, 'pcf': {}, 'hk': {}, 'cn': {}}
        if d in pcf:
            focus = {c: [] for c in selected}
            for row in rows(pcf[d]):
                if row['基金代码'] in focus: focus[row['基金代码']].append(row)
            headers = {r['基金代码']: r for r in rows(pcf[d].with_name(d + '_主表.csv')) if r['基金代码'] in selected}
            for c, rr in focus.items():
                components = [r['成分股代码'] for r in rr]
                item['pcf'][c] = {'rows': len(rr), 'distinct_components': len(set(components)),
                    'duplicate_components': len(components)-len(set(components)),
                    'flags': dict(Counter(r['现金替代标志'] for r in rr)),
                    'code_lengths': dict(Counter(len(s) for s in components)),
                    'missing_quantities': sum(not r['数量股'] for r in rr),
                    'header_component_count': headers.get(c, {}).get('成分股数量只'),
                    'components': [{'code': r['成分股代码'], 'name': r['成分股名称']} for r in rr]}
        for kind, mapping, symbols in [('hk', hk, ['02800.HK','02828.HK','03032.HK','03033.HK','02845.HK','03067.HK']),
                                        ('cn', cn, [c + ('.SZ' if c.startswith('15') else '.SH') for c in sorted(selected)])]:
            if d not in mapping: continue
            with zipfile.ZipFile(mapping[d]) as z:
                names = set(z.namelist())
                item[kind]['member_count'] = len(names)
                item[kind]['availability'] = {s: s + '.csv' in names for s in symbols}
                if kind == 'hk' and '520600' in item['pcf']:
                    cc = item['pcf']['520600']['components']
                    # An explicit HK-only pilot; never apply this conversion to mixed A/H baskets.
                    matched = [x for x in cc if x['code'].isdigit() and f"{int(x['code']):05d}.HK.csv" in names]
                    item[kind]['pilot_520600_component_members'] = {'total_pcf_rows': len(cc), 'matched': len(matched),
                        'unmatched': [x for x in cc if x not in matched]}
                if d in common[-3:]:
                    item[kind]['profiles'] = [member_profile(z,s+'.csv',kind) for s in symbols[:3] if s+'.csv' in names]
        out['samples'].append(item)
    for rel in ['A股分钟数据/A股_分时数据_沪深/1分钟_按年汇总','基金_分钟数据/基金_复权因子',
                '复权因子','复权因子_tushare','QMT定时快照/archives/2026']:
        pp = root / rel
        ff = sorted(p.name for p in pp.iterdir() if p.is_file() and not p.name.startswith('.')) if pp.exists() else []
        out['auxiliary'].append({'path': rel, 'count': len(ff), 'first': ff[:4], 'last': ff[-4:]})
    return out


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--root', default='/Volumes/EllisFiles/Stocksdata')
    a = p.parse_args()
    print(json.dumps(audit(Path(a.root)), ensure_ascii=False, indent=2))
