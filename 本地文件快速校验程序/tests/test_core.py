from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from archive_hash_checker.core import (
    DEFAULT_ALGORITHM,
    FileRecord,
    ScanMode,
    compare_records,
    discover_files,
    hash_file,
    load_manifest,
    save_manifest,
    scan_records,
)


class CoreTests(unittest.TestCase):
    def test_blake2b_uses_streamed_256_bit_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            file_path = Path(directory) / "archive.part01.rar"
            file_path.write_bytes(b"archive bytes" * 100)
            record = FileRecord.from_path(file_path)

            hash_file(record)

            self.assertEqual(record.algorithm, DEFAULT_ALGORITHM)
            self.assertEqual(record.digest, hashlib.blake2b(file_path.read_bytes(), digest_size=32).hexdigest())
            self.assertIsNone(record.error)

    def test_same_relative_file_matches_after_hdd_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left_root = root / "left"
            right_root = root / "right"
            (left_root / "series").mkdir(parents=True)
            (right_root / "series").mkdir(parents=True)
            (left_root / "series" / "part01.rar").write_bytes(b"same")
            (right_root / "series" / "part01.rar").write_bytes(b"same")
            left = discover_files([left_root])
            right = discover_files([right_root])

            scan_records(left, DEFAULT_ALGORITHM, ScanMode.HDD, max_workers=8)
            scan_records(right, DEFAULT_ALGORITHM, ScanMode.HDD, max_workers=8)
            left_status, right_status = compare_records(left, right)

            self.assertEqual(left_status[id(left[0])].status, "match")
            self.assertEqual(right_status[id(right[0])].status, "match")

    def test_same_name_with_different_bytes_is_yellow_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left_file = root / "left" / "movie.part1.rar"
            right_file = root / "right" / "movie.part1.rar"
            left_file.parent.mkdir()
            right_file.parent.mkdir()
            left_file.write_bytes(b"before upload")
            right_file.write_bytes(b"damaged copy")
            left = discover_files([left_file])
            right = discover_files([right_file])
            scan_records(left, DEFAULT_ALGORITHM, ScanMode.SSD, max_workers=2)
            scan_records(right, DEFAULT_ALGORITHM, ScanMode.SSD, max_workers=2)

            left_status, right_status = compare_records(left, right)

            self.assertEqual(left_status[id(left[0])].status, "different")
            self.assertEqual(right_status[id(right[0])].status, "different")

    def test_manifest_is_portable_and_preserves_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.rar"
            manifest = root / "snapshot.json"
            source.write_bytes(b"snapshot")
            records = discover_files([source])
            scan_records(records, DEFAULT_ALGORITHM, ScanMode.HDD, max_workers=1)

            save_manifest(manifest, records, "原始文件")
            restored = load_manifest(manifest)

            self.assertEqual(restored[0].digest, records[0].digest)
            self.assertEqual(restored[0].source_path, None)
            self.assertTrue(restored[0].is_hashed)


if __name__ == "__main__":
    unittest.main()
