#!/usr/bin/env python3
from __future__ import annotations
import base64, gzip, hashlib, json, shlex, subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CONTROL=ROOT.parent/"control"
OUT=ROOT/"data/raw/pcf"

def weekdays(start,end):
 d=date.fromisoformat(start); e=date.fromisoformat(end)
 while d<=e:
  if d.weekday()<5: yield d.strftime("%Y%m%d")
  d+=timedelta(days=1)
def sha(p):
 h=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
 return h.hexdigest()
def main():
 funds=json.loads((CONTROL/"fund_ids_B.json").read_text())
 codes=[x.split('.')[0] for x in funds]
 dates=list(weekdays("2026-03-03","2026-08-03"))
 run_started=datetime.now(timezone.utc).isoformat()
 remote=f'''import csv,gzip,json,sys
FUNDS={json.dumps(codes,ensure_ascii=False)}
DATES={json.dumps(dates)}
ROOT="/Volumes/EllisFiles/Stocksdata/PCF导出CSV"
out=gzip.GzipFile(fileobj=sys.stdout.buffer,mode="wb")
for day in DATES:
 p=f"{{ROOT}}/{{day}}_明细.csv"
 try: f=open(p,encoding="utf-8-sig",errors="replace")
 except Exception: continue
 with f:
  for r in csv.DictReader(f):
   if str(r.get("基金代码") or "").zfill(6) not in FUNDS: continue
   row={{"fund_id":str(r.get("基金代码") or "").zfill(6),"date":day,"record_id":r.get("记录ID"),"fund_name":r.get("基金名称"),"component_id":r.get("成分股代码"),"component_name":r.get("成分股名称"),"quantity":r.get("数量股"),"cash_flag":r.get("现金替代标志"),"cash_ratio":r.get("现金替代比例百分比"),"fixed_cash":r.get("固定替代金额元"),"source_path":p}}
   out.write((json.dumps(row,ensure_ascii=False,separators=(",",":"))+"\\n").encode())
out.close()
'''
 encoded=base64.b64encode(remote.encode()).decode()
 remote_cmd=f"import base64; exec(base64.b64decode('{encoded}'))"
 result=subprocess.run(["ssh","-o","BatchMode=yes","-o","ConnectTimeout=15","machome",f"python3 -c {shlex.quote(remote_cmd)}"],capture_output=True,check=False)
 if result.returncode: raise RuntimeError(result.stderr.decode("utf-8",errors="replace"))
 run_finished=datetime.now(timezone.utc).isoformat()
 OUT.mkdir(parents=True,exist_ok=True)
 raw=OUT/"target_pcf_rows.jsonl.gz"; raw.write_bytes(result.stdout)
 counts={(c,d):0 for c in codes for d in dates}; names={}
 with gzip.open(raw,"rt",encoding="utf-8") as f:
  for line in f:
   r=json.loads(line); counts[(r["fund_id"],r["date"])] += 1; names[r["fund_id"]]=r.get("fund_name")
 attempts=[]
 for c in codes:
  for d in dates:
   n=counts[(c,d)]
   attempts.append({"attempt_id":f"B-PCF-{c}-{d}","fund_id":c,"instrument_id":"PCF","data_type":"PCF","source":"machome remote PCF导出CSV filtered fund code","request":{"date":d,"frequency":"daily","fund_code":c},"started_at_utc":run_started,"finished_at_utc":run_finished,"status":"SUCCESS" if n else "NOT_FOUND","error_summary":None if n else "fund row absent in archive date","rows":n,"raw_path":str(raw.relative_to(ROOT.parent.parent)),"sha256":sha(raw),"next_action":"construct PCF basket if rows include nonzero quantity; otherwise evaluate ETF_MARKET_PRICE"})
 (OUT/"target_pcf_attempts.jsonl").write_text("".join(json.dumps(x,ensure_ascii=False,separators=(",",":"))+"\n" for x in attempts),encoding="utf-8")
 summary={"fund_count":len(codes),"requested_dates":len(dates),"raw_path":str(raw.relative_to(ROOT.parent.parent)),"sha256":sha(raw),"rows":sum(counts.values()),"dates_with_rows_by_fund":{c:sum(counts[(c,d)]>0 for d in dates) for c in codes},"fund_names":names,"run_started_at_utc":run_started,"run_finished_at_utc":run_finished,"generated_at_utc":run_finished}
 (OUT/"target_pcf_inventory.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps({"fund_count":len(codes),"requested_dates":len(dates),"rows":sum(counts.values()),"funds_with_any":sum(summary["dates_with_rows_by_fund"][c]>0 for c in codes)},ensure_ascii=False,indent=2))
if __name__=="__main__": main()
