import csv
from datetime import datetime, timedelta
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import minute_archive_compression as archive
import intraday_minute_store as store
import sina_ws_uploader as ws


NOW = datetime(2026, 9, 14, 18, 10, tzinfo=archive.BEIJING)
DAY = "20260709"
PAYLOAD = 'symbol,name,price\r\nSH513100,"中文,行情",1.23\r\n'.encode()


class CompressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.path = self.put(DAY)

    def put(self, day, payload=PAYLOAD):
        path = self.root / day / archive.FILENAME
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(payload)
        t = (NOW - timedelta(days=10)).timestamp()
        os.utime(path, (t, t))
        return path

    def compact(self, **kw):
        return archive.compact_day(self.root, DAY, apply=True, now=NOW, **kw)

    def test_round_trip_is_byte_exact_and_receipt_matches(self):
        result = self.compact()
        self.assertEqual(result['status'], 'compressed')
        self.assertFalse(self.path.exists())
        gz = self.path.with_suffix('.csv.gz')
        self.assertEqual(gzip.decompress(gz.read_bytes()), PAYLOAD)
        self.assertEqual(result['sha256'], hashlib.sha256(PAYLOAD).hexdigest())
        self.assertEqual(json.loads((self.path.parent / 'minute_quotes.archive.json').read_text()), result)
        with archive.open_minute_quotes(self.root, DAY) as f:
            self.assertEqual(list(csv.DictReader(f))[0]['name'], '中文,行情')
        self.assertEqual(self.compact()['status'], 'already_compressed')

    def test_dry_run_does_not_modify_files(self):
        before = list(self.path.parent.iterdir())
        self.assertEqual(archive.compact_day(self.root, DAY, now=NOW)['status'], 'candidate')
        self.assertEqual(before, list(self.path.parent.iterdir()))

    def test_recent_window_today_and_future_are_retained(self):
        for day in ['20260908', '20260914', '20260915']:
            p = self.put(day)
            self.assertEqual(archive.compact_day(self.root, day, apply=True, now=NOW)['status'], 'retained_recent')
            self.assertTrue(p.exists())
        self.put('20260907')
        self.assertEqual(archive.compact_day(self.root, '20260907', apply=True, now=NOW)['status'], 'compressed')

    def test_recently_modified_old_day_is_retained(self):
        os.utime(self.path, (NOW.timestamp(), NOW.timestamp()))
        self.assertEqual(self.compact()['status'], 'recently_modified')

    def test_identical_existing_archive_can_finish_interrupted_cleanup(self):
        self.path.with_suffix('.csv.gz').write_bytes(gzip.compress(PAYLOAD))
        self.assertEqual(self.compact()['status'], 'compressed')
        self.assertFalse(self.path.exists())

    def test_conflicting_or_corrupt_archive_preserves_both_files(self):
        gz = self.path.with_suffix('.csv.gz')
        for content in [gzip.compress(b'different'), b'broken']:
            gz.write_bytes(content)
            with self.assertRaises((RuntimeError, OSError)):
                self.compact()
            self.assertEqual(self.path.read_bytes(), PAYLOAD)
            self.assertEqual(gz.read_bytes(), content)

    def test_reader_prefers_plain_file_if_both_exist(self):
        self.path.with_suffix('.csv.gz').write_bytes(gzip.compress(b'different'))
        with archive.open_minute_quotes(self.root, DAY) as f:
            self.assertEqual(f.read().encode(), PAYLOAD)

    def test_missing_day_raises_without_creating_directory(self):
        with self.assertRaises(FileNotFoundError):
            with archive.open_minute_quotes(self.root, '20260101'):
                pass
        self.assertFalse((self.root / '20260101').exists())

    def test_source_change_during_compression_never_unlinks(self):
        original = archive.digest_stream
        changed = PAYLOAD + b'SH513330,new,2.0\r\n'
        def mutate(handle):
            answer = original(handle)
            self.path.write_bytes(changed)
            return answer
        with patch.object(archive, 'digest_stream', side_effect=mutate):
            with self.assertRaises(RuntimeError):
                self.compact()
        self.assertEqual(self.path.read_bytes(), changed)
        self.assertFalse(self.path.with_suffix('.csv.gz').exists())

    def test_publish_failure_preserves_source_and_cleans_temporary_file(self):
        with patch.object(archive.os, 'link', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                self.compact()
        self.assertEqual(self.path.read_bytes(), PAYLOAD)
        self.assertEqual(list(self.path.parent.glob('.minute-gzip-*')), [])

    def test_busy_day_lock_prevents_compression(self):
        with archive.day_lock(self.path.parent, exclusive=True):
            with self.assertRaises(BlockingIOError):
                self.compact()
        self.assertTrue(self.path.exists())

    def test_symlink_source_and_destination_are_rejected(self):
        target = self.root / 'outside.csv'
        target.write_bytes(PAYLOAD)
        self.path.unlink()
        self.path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.compact()
        self.path.unlink()
        self.put(DAY)
        self.path.with_suffix('.csv.gz').symlink_to(target)
        with self.assertRaises(ValueError):
            self.compact()
        self.assertEqual(target.read_bytes(), PAYLOAD)

    def test_writer_refuses_to_create_partial_csv_over_archive(self):
        self.compact()
        writer = store.IntradayMinuteArchive(str(self.root))
        t = datetime(2026, 7, 9, 10, 0, tzinfo=archive.BEIJING)
        with self.assertRaises(RuntimeError):
            writer._append_rows(t, [{'symbol': 'SH513100'}], t)
        self.assertFalse(self.path.exists())

    def test_gzip_backfill_and_offset_transition_do_not_duplicate_uploads(self):
        with self.path.open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=store.MINUTE_QUOTE_FIELDS)
            w.writeheader()
            w.writerow(dict(symbol='SH513100', name='中文', market='cn', price=1.23,
                            trading_day='2026-07-09', minute_label='09:30',
                            minute_bucket='2026-07-09T09:30:00+08:00'))
        os.utime(self.path, ((NOW-timedelta(days=10)).timestamp(),)*2)
        t = datetime(2026, 7, 9, 10, 0, tzinfo=archive.BEIJING)
        sync = ws.IntradayMinuteBackfillSync(str(self.root))
        with patch.object(ws, 'upload_intraday_minute_backfill', return_value=1) as upload:
            self.assertEqual(sync.sync(object(), t), 1)
            self.compact()
            self.assertEqual(sync.sync(object(), t), 0)
            self.assertEqual(upload.call_count, 1)
            fresh = ws.IntradayMinuteBackfillSync(str(self.root))
            self.assertEqual(fresh.sync(object(), t), 1)
            self.assertEqual(fresh.sync(object(), t), 0)


if __name__ == '__main__':
    unittest.main()
