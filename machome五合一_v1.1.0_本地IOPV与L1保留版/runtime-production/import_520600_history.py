#!/usr/bin/env python3
"""Idempotently import a validated 520600 historical bundle into IOPV SQLite."""
from __future__ import annotations

import argparse, datetime as dt, hashlib, json, sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    parser.add_argument("--db", required=True)
    parser.add_argument("--replace-existing", action="store_true", help="replace only matching 520600 historical dates after an online backup")
    args = parser.parse_args()
    archive, db_path = Path(args.archive), Path(args.db)
    rows_path, pcf_path = archive / "validated.jsonl", archive / "pcf-index.json"
    rows = [json.loads(line) for line in rows_path.read_text().splitlines() if line]
    pcfs = json.loads(pcf_path.read_text())
    expected = hashlib.sha256(rows_path.read_bytes()).hexdigest()
    if not rows or any(r["symbol"] != "520600.SH" or r["mode"] != "historical_reconstruction" or r["eligible_for_signal"] for r in rows): raise ValueError("invalid point policy")
    keys = {(r["symbol"], r["trade_date"], r["minute"]) for r in rows}
    if len(keys) != len(rows): raise ValueError("duplicate minute key")
    dates = sorted({r["trade_date"] for r in rows})
    if set(dates) != set(pcfs): raise ValueError("PCF dates do not match point dates")
    prefix = "before-520600-central-parity-" if args.replace_existing else "before-520600-history-"
    backup = db_path.with_name(prefix + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + ".sqlite")
    db = sqlite3.connect(db_path, timeout=20); db.execute("pragma busy_timeout=20000")
    backup_db = sqlite3.connect(backup); db.backup(backup_db); backup_db.close(); backup.chmod(0o600)
    before = db.execute("select count(*) from minutes").fetchone()[0]
    placeholders = ",".join("?" for _ in dates)
    existing_keys = {
        tuple(row)
        for row in db.execute(
            f"select symbol,trade_date,minute from minutes where symbol=? and trade_date in ({placeholders})",
            ("520600.SH", *dates),
        )
    }
    existing = len(existing_keys)
    if args.replace_existing and not existing_keys.issubset(keys):
        raise ValueError("refusing replacement: existing 520600 keys are outside the validated artifact")
    with db:
        for day, pcf in pcfs.items():
            raw = (archive / f"pcf-{day}.html").read_bytes()
            if hashlib.sha256(raw).hexdigest() != pcf["pcf_sha256"]: raise ValueError("PCF hash mismatch")
            db.execute("insert or ignore into pcf(symbol,trade_date,hash,raw,fetched_at) values(?,?,?,?,?)", ("520600.SH", day, pcf["pcf_sha256"], raw, dt.datetime.now(dt.timezone.utc).isoformat()))
        if args.replace_existing:
            db.execute(f"delete from minutes where symbol=? and trade_date in ({placeholders})", ("520600.SH", *dates))
        changed = db.total_changes
        db.executemany("insert or ignore into minutes(symbol,trade_date,minute,payload) values(?,?,?,?)", [(r["symbol"], r["trade_date"], r["minute"], json.dumps(r, ensure_ascii=False, allow_nan=False)) for r in rows])
        inserted = db.total_changes - changed
        kind = "historical_backfill_central_parity_correction" if args.replace_existing else "historical_backfill"
        db.execute("insert into events(at,kind,message) values(?,?,?)", (dt.datetime.now(dt.timezone.utc).isoformat(), kind, json.dumps({"symbol":"520600.SH","artifact_sha256":expected,"inserted":inserted,"replaced_existing":existing if args.replace_existing else 0,"backup":str(backup)})))
    if db.execute("pragma quick_check").fetchone()[0] != "ok": raise RuntimeError("SQLite integrity failed")
    after = db.execute("select count(*) from minutes").fetchone()[0]
    print(json.dumps({"before":before,"after":after,"inserted":inserted,"existing_kept":len(rows)-inserted,"replaced_existing":existing if args.replace_existing else 0,"backup":str(backup),"artifact_sha256":expected,"integrity":"ok"}, ensure_ascii=False))


if __name__ == "__main__": main()
