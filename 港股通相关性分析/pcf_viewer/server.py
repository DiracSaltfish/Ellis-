"""Read-only LAN PCF explorer. Python standard library, no CDN or dependencies."""
import argparse
import csv
import hashlib
import io
import json
import math
import threading
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
DEFAULT_CSV = ROOT.parents[1] / 'analysis_520600' / 'daily_components.csv'


def code(value):
    value = value.strip()
    return str(int(value)).zfill(5) if value.isdigit() and int(value) < 100000 else value


def load_data(path):
    raw = path.read_bytes()
    groups, seen = {}, set()
    for line, row in enumerate(csv.DictReader(raw.decode('utf-8-sig').splitlines()), 2):
        fund = row['fund_code'].strip()
        day = date.fromisoformat(row['date']).isoformat()
        ticker = code(row['component_code'])
        key = (fund, day, ticker)
        if key in seen:
            raise ValueError('重复记录，第%s行：%s' % (line, key))
        seen.add(key)
        qty = float(row['quantity_shares'])
        if not math.isfinite(qty) or qty < 0:
            raise ValueError('数量必须为有限非负数，第%s行' % line)
        group = groups.setdefault(fund, {'code': fund, 'name': row['fund_name'], 'days': set(), 'stocks': {}})
        group['days'].add(day)
        stock = group['stocks'].setdefault(ticker, {'code': ticker, 'names': set(), 'by_day': {}})
        stock['names'].add(row['component_name'])
        stock['by_day'][day] = (qty, row['component_name'], row.get('cash_substitution_flag', ''))
    if not seen:
        raise ValueError('CSV 没有可用的 PCF 记录')
    funds = []
    for fund in sorted(groups):
        g = groups[fund]
        dates = sorted(g['days'])
        stocks = []
        for ticker, stock in sorted(g['stocks'].items()):
            by_day = stock['by_day']
            observed = sorted(by_day)
            values = [by_day[d][0] if d in by_day else None for d in dates]
            changes = sum(a is not None and b is not None and a != b for a, b in zip(values, values[1:]))
            stocks.append({'code': ticker, 'name': by_day[observed[-1]][1], 'aliases': sorted(stock['names']),
                           'values': values, 'flags': [by_day[d][2] if d in by_day else None for d in dates],
                           'first': observed[0], 'last': observed[-1], 'observations': len(observed),
                           'changes': changes, 'active': values[-1] is not None})
        funds.append({'code': fund, 'name': g['name'], 'dates': dates, 'stocks': stocks,
                      'rows': sum(s['observations'] for s in stocks)})
    return {'funds': funds, 'source': path.name, 'source_modified': datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec='seconds'),
            'sha256': hashlib.sha256(raw).hexdigest(), 'rows': len(seen),
            'date_basis': 'PCF 公告/清单日期，不是主表中的净值截止日期',
            'quantity_basis': '每最小申购赎回单位的清单股数；不是基金全部实际持仓',
            'missing_basis': 'null 表示该基金已有 PCF 数据的日期中未列出该券，不填零、不前向填充。整日源数据缺失需由主表额外核实。'}


class DataStore:
    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        self.signature = None
        self.body = None

    def get(self):
        with self.lock:
            stat = self.path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if self.signature != signature:
                self.body = json.dumps(load_data(self.path), ensure_ascii=False, allow_nan=False).encode()
                self.signature = signature
            return self.body


def export_csv(data, query):
    params = parse_qs(query)
    fund = next((f for f in data['funds'] if f['code'] == params.get('fund', [''])[0]), None)
    if not fund:
        raise ValueError('基金不存在')
    stock = next((s for s in fund['stocks'] if s['code'] == params.get('stock', [''])[0]), None)
    if not stock:
        raise ValueError('成分券不存在')
    start, end = params.get('start', [''])[0], params.get('end', [''])[0]
    date.fromisoformat(start)
    date.fromisoformat(end)
    if start > end:
        raise ValueError('开始日期晚于结束日期')
    only_changes = params.get('changes', ['0'])[0] == '1'
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['基金代码', '成分券代码', '名称', 'PCF日期', '每最小申购赎回单位股数', '较上一PCF日变化股数', '变化比例', '现金替代标志', '记录状态'])
    values = stock['values']
    for i, day in enumerate(fund['dates']):
        value, previous = values[i], values[i-1] if i else None
        if day < start or day > end or (only_changes and (i == 0 or value == previous)):
            continue
        change = value - previous if value is not None and previous is not None else None
        ratio = change / previous if change is not None and previous != 0 else None
        status = ('未列入（退出记录）' if previous is not None else '未列入') if value is None else ('起始记录' if i == 0 else '重新/首次列入' if previous is None else '数量变化' if change else '数量不变')
        # Neutralize spreadsheet formulas in source-controlled text fields only.
        name = stock['name']
        if name.startswith(('=', '+', '-', '@', '\t', '\r')):
            name = "'" + name
        writer.writerow([fund['code'], stock['code'], name, day, '' if value is None else value,
                         '' if change is None else change, '' if ratio is None else f'{ratio*100:.4f}%',
                         stock['flags'][i] or '', status])
    filename = f"PCF_{fund['code']}_{stock['code']}_{start}_{end}.csv"
    return output.getvalue().encode('utf-8-sig'), filename


def handler(store):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            download = None
            try:
                if path == '/api/data':
                    body, mime = store.get(), 'application/json; charset=utf-8'
                elif path == '/api/export':
                    body, download = export_csv(json.loads(store.get()), urlsplit(self.path).query)
                    mime = 'text/csv; charset=utf-8'
                elif path == '/health':
                    body, mime = b'{"ok":true}', 'application/json'
                else:
                    assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                              '/app.js': ('app.js', 'application/javascript; charset=utf-8'),
                              '/style.css': ('style.css', 'text/css; charset=utf-8')}
                    if path not in assets:
                        self.send_error(404)
                        return
                    name, mime = assets[path]
                    body = (ROOT / name).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(body)))
                if download:
                    self.send_header('Content-Disposition', 'attachment; filename="%s"' % download)
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'")
                self.end_headers()
                self.wfile.write(body)
            except (ValueError, KeyError, OSError) as exc:
                body = json.dumps({'error': str(exc)}, ensure_ascii=False).encode()
                self.send_response(422)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--csv', type=Path, default=DEFAULT_CSV)
    p.add_argument('--host', default='0.0.0.0')
    p.add_argument('--port', type=int, default=8766)
    args = p.parse_args()
    store = DataStore(args.csv.resolve())
    store.get()
    server = ThreadingHTTPServer((args.host, args.port), handler(store))
    print('PCF Viewer http://127.0.0.1:%s — bind %s; source %s' % (args.port, args.host, store.path), flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
