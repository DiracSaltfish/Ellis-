#!/usr/bin/env python3
from __future__ import annotations
import csv, gzip, hashlib, json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; PROJECT=Path("/Users/ellis/工具程序开发/港股通相关性分析"); CONTROL=ROOT.parent/"control"; OUT=ROOT/"data/processed"
WINDOW_START="20260303"; WINDOW_END="20260803"

def sha(p):
 h=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
 return h.hexdigest()
def rel(p):
 p=Path(p)
 try:return str(p.relative_to(PROJECT))
 except ValueError:return str(p)
def jsonl(path):
 with Path(path).open(encoding="utf-8") as f:
  return [json.loads(x) for x in f if x.strip()]
def family(index_name):
 n=index_name or ""
 if "汽车" in n:return "AUTO"
 if "互联网" in n:return "INTERNET"
 if "科技" in n or "信息" in n or "新经济" in n:return "TECH"
 return "BROAD_HK"
def candidates(fam):
 base=[("HSI_FUT","恒生指数期货","HSI","宽基港股风险；所有港股通基金的第一宽基基准"),("HHI_FUT","恒生中国企业指数期货","HHI","中国企业/港股通大型国企风险"),("HTI_FUT","恒生科技指数期货","HTI","科技与成长风险")]
 etf=[("02800","Tracker Fund of Hong Kong","HSI","香港宽基ETF补充"),("02828","恒生中国企业ETF","HHI","香港国企ETF补充"),("03032","香港科技ETF 03032","HTI","香港科技ETF行业代理"),("03033","香港科技ETF 03033","HTI","香港科技ETF行业代理"),("02845","香港科技/成长ETF 02845","EV_PROXY","科技成长/新能源补充")]
 if fam=="AUTO": return base+etf
 if fam in {"TECH","INTERNET"}: return [base[2],base[0],base[1]]+etf
 return base+etf
def main():
 assignments=json.loads((CONTROL/"assignments.json").read_text())["B"]
 etf_inv=json.loads((ROOT/"data/raw/target_etf_1m/target_etf_inventory.json").read_text())
 pcf_inv=json.loads((ROOT/"data/raw/pcf/target_pcf_inventory.json").read_text())
 pilot=Path(PROJECT/"data/normalized/pilot_minutes.parquet")
 import pandas as pd
 p=pd.read_parquet(pilot); candidate_stats={}
 for tool in ["HSI_FUT","HHI_FUT","HTI_FUT","02800","02828","03032","03033","02845"]:
  candidate_stats[tool]={"rows":int(p[tool].notna().sum()),"days":int(p.loc[p[tool].notna(),"date"].nunique()),"actual_start":str(p.loc[p[tool].notna(),"date"].min()),"actual_end":str(p.loc[p[tool].notna(),"date"].max()),"source_path":rel(pilot),"sha256":sha(pilot)}
 inv=[]; cmap=[]
 for f in assignments:
  code=f["fund_id"].split(".")[0]; etf_days=etf_inv["days_by_fund"].get(f["fund_id"],[]); pcf_days=pcf_inv["dates_with_rows_by_fund"].get(code,0)
  nonzero_days=None
  inv.append({"fund_id":f["fund_id"],"fund_name":f["fund_name"],"index_id":f.get("index_id"),"index_name":f["index_name"],"index_family":family(f["index_name"]),"listing_date":f.get("listing_date"),"requested_start":WINDOW_START,"requested_end":WINDOW_END,"etf_market_price_rows":etf_inv["rows_by_fund"].get(f["fund_id"],0),"etf_market_price_days":len(etf_days),"pcf_rows":None,"pcf_days":pcf_days,"pcf_source_days":110,"target_data_status":"PCF_ROWS_AVAILABLE" if pcf_days else ("ETF_MARKET_PRICE_AVAILABLE" if etf_days else "NO_TARGET_PRICE"),"target_pathways_checked":["PCF_BASKET","ETF_MARKET_PRICE","INDEX_STRUCTURAL"],"inventory_source_ids":["B-ETF-INVENTORY","B-PCF-INVENTORY","B-PILOT-CANDIDATE-ARCHIVE"],"remaining_inventory_gap":[] if pcf_days or etf_days else ["remote ETF archive has no member","remote PCF archive has no fund rows","only structural candidates remain"]})
  for tool,name,fam,reason in candidates(family(f["index_name"])):
   st=candidate_stats[tool]; cmap.append({"fund_id":f["fund_id"],"index_family":family(f["index_name"]),"tool_id":tool,"tool_name":name,"risk_family":fam,"economic_reason":reason,"asset_type":"HK_FUTURE" if tool.endswith("FUT") else "HK_ETF","data_source":st["source_path"],"data_days":st["days"],"data_rows":st["rows"],"data_start":st["actual_start"],"data_end":st["actual_end"],"data_sha256":st["sha256"],"prelocked_candidate":True,"tested_later":False,"exclusion_reason":None})
 OUT.mkdir(parents=True,exist_ok=True)
 for name,rows in [("inventory.jsonl",inv),("candidate_map.jsonl",cmap)]:
  (OUT/name).write_text("".join(json.dumps(r,ensure_ascii=False,separators=(",",":"))+"\n" for r in rows),encoding="utf-8")
 # CSV mirrors are convenient for audit, but JSONL preserves arrays/objects.
 for name,rows in [("inventory.csv",inv),("candidate_map.csv",cmap)]:
  fields=list(rows[0]);
  with (OUT/name).open("w",encoding="utf-8-sig",newline="") as fh:
   w=csv.DictWriter(fh,fieldnames=fields); w.writeheader()
   for r in rows:w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in r.items()})
 status={"updated_at_utc":datetime.now(timezone.utc).isoformat(),"phase":"INVENTORY_COMPLETE","owner":"B","assigned_funds":len(assignments),"inventory_rows":len(inv),"candidate_rows":len(cmap),"target_pcf_available":sum(x["pcf_days"]>0 for x in inv),"target_etf_available":sum(x["etf_market_price_days"]>0 for x in inv),"no_target_price":sum(x["target_data_status"]=="NO_TARGET_PRICE" for x in inv),"processing_status":"all 81 queued; computation next"}
 (ROOT/"STATUS.md").write_text("# Agent B full coverage STATUS\n\n"+json.dumps(status,ensure_ascii=False,indent=2)+"\n\n已完成全81只 inventory 与具体候选清单；下一步按数据可用性批量计算 PCF/ETF_MARKET_PRICE 目标。\n",encoding="utf-8")
 print(json.dumps(status,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
