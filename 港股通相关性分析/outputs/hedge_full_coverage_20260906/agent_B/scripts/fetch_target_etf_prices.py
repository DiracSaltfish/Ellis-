#!/usr/bin/env python3
"""Fetch selected mainland ETF 1-minute members from machome without copying full zips."""
from __future__ import annotations
import base64, gzip, hashlib, json, shlex, subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT.parent / "control"
OUT = ROOT / "data/raw/target_etf_1m"

def weekdays(start, end):
    d, e = date.fromisoformat(start), date.fromisoformat(end)
    while d <= e:
        if d.weekday() < 5:
            yield d.strftime("%Y%m%d")
        d += timedelta(days=1)

def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()

def main():
    funds = json.loads((CONTROL / "fund_ids_B.json").read_text())
    dates = list(weekdays("2026-03-03", "2026-08-03"))
    run_started = datetime.now(timezone.utc).isoformat()
    remote = f'''import csv,gzip,json,sys,zipfile
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
CODES={json.dumps(funds, ensure_ascii=False)}
DATES={json.dumps(dates)}
ROOT="/Volumes/EllisFiles/Stocksdata/基金_分钟数据/ETF_分钟数据/1分钟_按月归档"
writer=gzip.GzipFile(fileobj=sys.stdout.buffer, mode="wb")
for day in DATES:
    zpath=f"{{ROOT}}/{{day[:4]}}-{{day[4:6]}}/{{day}}_1min.zip"
    try: z=zipfile.ZipFile(zpath)
    except Exception: continue
    for code in CODES:
        try: raw=z.open(f"{{code}}.csv")
        except KeyError: continue
        with raw:
            for r in csv.DictReader((line.decode("utf-8-sig", errors="replace") for line in raw)):
                ts=(r.get("时间") or "").strip(); px=(r.get("收盘价") or "").strip()
                if not ts or not px: continue
                try:
                    dt=datetime.strptime(ts, "%Y/%m/%d %H:%M").replace(tzinfo=ZoneInfo("Asia/Hong_Kong"))+timedelta(minutes=1)
                    close=float(px)
                except Exception: continue
                row={{"fund_id":code,"date":day,"timestamp_end":dt.isoformat(),"close":close,"open":float(r.get("开盘价") or px),"high":float(r.get("最高价") or px),"low":float(r.get("最低价") or px),"source_path":zpath}}
                writer.write((json.dumps(row,ensure_ascii=False,separators=(",",":"))+"\\n").encode())
    z.close()
writer.close()
'''
    encoded = base64.b64encode(remote.encode()).decode()
    remote_cmd = f"import base64; exec(base64.b64decode('{encoded}'))"
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "machome", f"python3 -c {shlex.quote(remote_cmd)}"], capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
    run_finished = datetime.now(timezone.utc).isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    raw = OUT / "target_etf_1m.jsonl.gz"
    raw.write_bytes(result.stdout)
    counts = {c: 0 for c in funds}; days = {c: set() for c in funds}
    with gzip.open(raw, "rt", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line); counts[r["fund_id"]] += 1; days[r["fund_id"]].add(r["date"])
    attempts = []
    for c in funds:
        for d in dates:
            status = "SUCCESS" if d in days[c] else "NOT_FOUND"
            attempts.append({"attempt_id":f"B-ETF-{c}-{d}","fund_id":c,"instrument_id":c,"data_type":"ETF_1M","source":"machome remote zip filtered member","request":{"date":d,"frequency":"1 min","member":f"{c}.csv"},"started_at_utc":run_started,"finished_at_utc":run_finished,"status":status,"error_summary":None if status=="SUCCESS" else "member absent or archive date unavailable","rows":None,"raw_path":str(raw.relative_to(ROOT.parent.parent)),"sha256":sha(raw),"next_action":"use available dates for ETF_MARKET_PRICE; if none, retain structural candidates"})
    (OUT / "target_etf_attempts.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n" for x in attempts), encoding="utf-8")
    summary = {"fund_count":len(funds),"requested_dates":len(dates),"raw_path":str(raw.relative_to(ROOT.parent.parent)),"sha256":sha(raw),"rows_by_fund":counts,"days_by_fund":{c:sorted(days[c]) for c in funds},"run_started_at_utc":run_started,"run_finished_at_utc":run_finished,"generated_at_utc":run_finished}
    (OUT / "target_etf_inventory.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"fund_count":len(funds),"requested_dates":len(dates),"total_rows":sum(counts.values()),"funds_with_data":sum(bool(days[c]) for c in funds)},ensure_ascii=False,indent=2))
if __name__ == "__main__": main()
