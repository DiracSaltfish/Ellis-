#!/usr/bin/env python3
"""Lossless, verified compaction of closed minute_quotes.csv day files.

No network requests. Today and the recent retention window are never compacted.
The lock is shared with the minute writer and reader. A conflicting existing gzip
is an error: neither copy is discarded. Use --apply to opt in to removing CSVs.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo("Asia/Shanghai")
FILENAME = "minute_quotes.csv"


def day_folder(root, day):
    if datetime.strptime(day, "%Y%m%d").strftime("%Y%m%d") != day:
        raise ValueError("Expected YYYYMMDD")
    root = Path(root).absolute()
    if root.is_symlink():
        raise ValueError("Archive root must not be a symlink")
    # macOS /var is itself a system symlink; normalize parent aliases first.
    root = root.resolve()
    folder = root / day
    if folder.is_symlink():
        raise ValueError("Archive paths must not contain symlinks")
    return folder


@contextmanager
def day_lock(folder, exclusive=False, nonblocking=False):
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
    fd = os.open(Path(folder) / ".minute_quotes.lock", flags, 0o600)
    try:
        fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
                    | (fcntl.LOCK_NB if nonblocking else 0))
        yield
    finally:
        os.close(fd)


@contextmanager
def open_minute_quotes(root, day):
    """Read plain CSV first, or its gzip archive; missing days raise FileNotFoundError."""
    folder = day_folder(root, day)
    with day_lock(folder):
        path = folder / FILENAME
        compressed = path.with_suffix(".csv.gz")
        if path.is_symlink() or compressed.is_symlink():
            raise ValueError("Minute files must not be symlinks")
        if path.exists():
            handle = path.open("r", encoding="utf-8", newline="")
        else:
            handle = gzip.open(compressed, "rt", encoding="utf-8", newline="")
        with handle:
            yield handle


def digest_stream(handle):
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
        size += len(chunk)
    return size, digest.hexdigest()


def fingerprint(path):
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode):
        raise ValueError("Not a regular file: " + str(path))
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def sync_directory(folder):
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_receipt(path, data):
    fd, name = tempfile.mkstemp(prefix=".minute-receipt-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(data, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def compact_day(root, day, *, keep_days=7, apply=False, now=None):
    now = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    if keep_days < 1:
        raise ValueError("keep_days must be at least 1")
    folder = day_folder(root, day)
    date = datetime.strptime(day, "%Y%m%d").date()
    result = {"day": day, "root": str(Path(root).absolute())}
    if date >= now.date() - timedelta(days=keep_days - 1):
        return dict(result, status="retained_recent")
    path = folder / FILENAME
    compressed = folder / (FILENAME + ".gz")
    if not path.exists() and not path.is_symlink():
        return dict(result, status="already_compressed" if compressed.is_file() else "missing")
    initial = fingerprint(path)
    if initial[3] / 1e9 > now.timestamp() - 3600:
        return dict(result, status="recently_modified")
    if not apply:
        return dict(result, status="candidate", bytes=initial[2])
    with day_lock(folder, exclusive=True, nonblocking=True):
        if fingerprint(path) != initial:
            raise RuntimeError("Source changed before compression")
        tmp = None
        try:
            if compressed.exists() or compressed.is_symlink():
                fingerprint(compressed)
                with path.open("rb") as source:
                    source_info = digest_stream(source)
            else:
                fd, tmp = tempfile.mkstemp(prefix=".minute-gzip-", dir=folder)
                digest = hashlib.sha256()
                size = 0
                with os.fdopen(fd, "wb") as raw:
                    with gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                                       compresslevel=6, mtime=0) as out, path.open("rb") as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            out.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                    raw.flush()
                    os.fsync(raw.fileno())
                source_info = size, digest.hexdigest()
            candidate = Path(tmp) if tmp else compressed
            with gzip.open(candidate, "rb") as check:
                if digest_stream(check) != source_info:
                    raise RuntimeError("Gzip content differs from CSV; both copies retained")
            # Re-read the source as well, detecting writers that do not honor our lock.
            with path.open("rb") as source:
                if digest_stream(source) != source_info:
                    raise RuntimeError("Source content changed during compression")
            if fingerprint(path) != initial:
                raise RuntimeError("Source changed during compression")
            if tmp:
                os.link(tmp, compressed)  # Atomic publication; never overwrite an archive.
                sync_directory(folder)
            result.update(status="verified", bytes=source_info[0], sha256=source_info[1],
                          gzip_bytes=compressed.stat().st_size, verified_at=now.isoformat())
            receipt = folder / "minute_quotes.archive.json"
            write_receipt(receipt, result)
            if fingerprint(path) != initial:
                raise RuntimeError("Source changed before unlink; both copies retained")
            path.unlink()
            sync_directory(folder)
            result["status"] = "compressed"
            write_receipt(receipt, result)
            return result
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--day", help="One YYYYMMDD day; otherwise inspect every dated directory")
    parser.add_argument("--keep-days", type=int, default=7,
                        help="Keep this many calendar dates including today as CSV (default 7)")
    parser.add_argument("--apply", action="store_true", help="Verify gzip then remove corresponding old CSV")
    args = parser.parse_args()
    if not args.root.is_dir():
        parser.error("root is not an existing directory")
    if args.keep_days < 1:
        parser.error("keep-days must be at least 1")
    days = [args.day] if args.day else sorted(p.name for p in args.root.iterdir()
                    if p.is_dir() and len(p.name) == 8 and p.name.isdigit())
    failed = 0
    for day in days:
        try:
            result = compact_day(args.root, day, keep_days=args.keep_days, apply=args.apply)
        except BlockingIOError:
            result = {"day": day, "status": "busy"}
        except Exception as exc:
            failed += 1
            result = {"day": day, "status": "error", "error": str(exc)}
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
