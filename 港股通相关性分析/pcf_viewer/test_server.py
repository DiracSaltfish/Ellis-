import csv
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from server import DataStore, ThreadingHTTPServer, code, export_csv, handler, load_data

FIELDS = ['date', 'fund_code', 'fund_name', 'component_code', 'component_name', 'quantity_shares', 'cash_substitution_flag']


class PCFTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.csv'

    def write(self, rows):
        with self.path.open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(FIELDS)
            writer.writerows(rows)

    def row(self, day, ticker='00175', qty=100, name='吉利汽车'):
        return [day, '520600', '测试基金', ticker, name, qty, '退补']

    def test_normalize(self):
        self.assertEqual(code('000175'), '00175')
        self.assertEqual(code('175'), '00175')
        self.assertEqual(code('123456'), '123456')

    def test_missing_zero_aliases_and_changes(self):
        self.write([self.row('2025-01-01', qty=0), self.row('2025-01-02', ticker='0175', qty=10, name='新名字'),
                    self.row('2025-01-03', ticker='00966'), self.row('2025-01-04', qty=50)])
        fund = load_data(self.path)['funds'][0]
        stock = fund['stocks'][0]
        self.assertEqual(stock['values'], [0, 10, None, 50])
        self.assertEqual(stock['changes'], 1)
        self.assertEqual(stock['observations'], 3)
        self.assertEqual(len(stock['aliases']), 2)
        self.assertEqual(stock['name'], '吉利汽车')

    def test_duplicate_normalized_codes_rejected(self):
        self.write([self.row('2025-01-01'), self.row('2025-01-01', ticker='000175')])
        with self.assertRaisesRegex(ValueError, '重复记录'):
            load_data(self.path)

    def test_bad_quantities_rejected(self):
        for qty in [-1, 'nan', 'inf']:
            self.write([self.row('2025-01-01', qty=qty)])
            with self.assertRaises(ValueError):
                load_data(self.path)

    def test_invalid_date_and_empty_rejected(self):
        self.write([self.row('2025-02-30')])
        with self.assertRaises(ValueError):
            load_data(self.path)
        self.write([])
        with self.assertRaises(ValueError):
            load_data(self.path)

    def test_cache_refresh(self):
        self.write([self.row('2025-01-01')])
        store = DataStore(self.path)
        first = store.get()
        self.assertIs(first, store.get())
        self.write([self.row('2025-01-01'), self.row('2025-01-02')])
        self.assertEqual(json.loads(store.get())['rows'], 2)

    def test_export_missing_zero_and_filter(self):
        self.write([self.row('2025-01-01', qty=0), self.row('2025-01-02', qty=0),
                    self.row('2025-01-03', qty=10), self.row('2025-01-04', ticker='00966')])
        query = 'fund=520600&stock=00175&start=2025-01-01&end=2025-01-04'
        body, filename = export_csv(load_data(self.path), query)
        rows = list(csv.reader(io.StringIO(body.decode('utf-8-sig'))))
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[1][4], '0.0')
        self.assertEqual(rows[-1][4], '')
        filtered, _ = export_csv(load_data(self.path), query + '&changes=1')
        self.assertEqual(len(list(csv.reader(io.StringIO(filtered.decode('utf-8-sig'))))), 3)
        self.assertTrue(filename.endswith('.csv'))

    def test_http_allowlist(self):
        self.write([self.row('2025-01-01')])
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(DataStore(self.path)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = 'http://127.0.0.1:%s' % server.server_port
            for path in ['/', '/app.js', '/style.css', '/health', '/api/data']:
                with urlopen(base + path) as response:
                    self.assertEqual(response.status, 200)
            for path in ['/server.py', '/../../etc/passwd']:
                with self.assertRaises(HTTPError) as exc:
                    urlopen(base + path)
                self.assertEqual(exc.exception.code, 404)
                exc.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
