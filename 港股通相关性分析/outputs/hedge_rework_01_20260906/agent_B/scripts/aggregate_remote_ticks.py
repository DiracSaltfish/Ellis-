#!/usr/bin/env python3
"""Aggregate locally copied remote Hong Kong tick archives to minute closes.

This is an independent cross-check source.  IBKR is still the primary input
for the research panel because the remote archive ends on 2026-08-25.
"""
from __future__ import annotations
import csv, gzip, hashlib, json, zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data/raw/remote_tick_2026_08"
PCF = ROOT / "data/raw/new_period_520600/520600_pcf_parsed.jsonl.gz"
OUT = ROOT / "data/raw/new_period_520600"
HK = ZoneInfo("Asia/Hong_Kong")

def sha(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024), b""): h.update(b)
    return h.hexdigest()

def main():
    codes = set()
    with gzip.open(PCF, "rt", encoding="utf-8") as f:
        for line in f:
            codes |= {str(x["成分股代码"]) for x in json.loads(line)["components"] if (x.get("数量股") or 0) > 0}
    rows=[]; coverage=[]
    for zpath in sorted(SRC.glob("*.zip")):
        day = zpath.stem[-8:]
        minute_last = {}
        with zipfile.ZipFile(zpath) as z:
            csv_names = [n for n in z.namelist() if n.lower().endswith(".csv")]
            for name in csv_names:
                code_from_name = name.rsplit("/", 1)[-1].split(".", 1)[0].zfill(5)
                with z.open(name) as raw:
                    text = (line.decode("utf-8-sig", errors="replace") for line in raw)
                    reader = csv.DictReader(text)
                    for r in reader:
                        code = code_from_name
                        if code not in codes: continue
                        ts = (r.get("时间") or "").strip(); price = (r.get("价格") or "").strip()
                        if not ts or not price: continue
                        try:
                            dt = datetime.strptime(ts, "%Y/%m/%d %H:%M")
                            px = float(price)
                        except (ValueError, TypeError): continue
                        minute_last[(code, dt.strftime("%Y%m%d %H:%M"))] = px
        for (code, minute), px in sorted(minute_last.items()):
            rows.append({"security_id":code,"datetime_hk":minute,"date":minute[:8],"price":px,"source":"remote_tick_archive"})
        coverage.append({"date":day,"zip_path":str(zpath.relative_to(ROOT.parent.parent)),"zip_sha256":sha(zpath),"codes_with_minutes":len({k[0] for k in minute_last}),"minute_rows":len(minute_last),"status":"PASS" if minute_last else "NO_ROWS"})
    with gzip.open(OUT / "remote_tick_1m_crosscheck.jsonl.gz", "wt", encoding="utf-8") as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False, separators=(",",":"))+"\n")
    observed = {x["date"] for x in coverage}
    with gzip.open(PCF, "rt", encoding="utf-8") as f:
        requested = {json.loads(line)["date"] for line in f}
    missing = sorted(requested-observed)
    result={"source_dir":str(SRC.relative_to(ROOT.parent.parent)),"coverage":coverage,"observed_dates":sorted(observed),"missing_from_archive":missing,"rows":len(rows),"output":str((OUT/"remote_tick_1m_crosscheck.jsonl.gz").relative_to(ROOT.parent.parent))}
    (OUT/"remote_tick_crosscheck_coverage.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
if __name__ == "__main__": main()
