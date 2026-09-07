#!/usr/bin/env python3
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PROJECT=Path("/Users/ellis/工具程序开发/港股通相关性分析")
def main():
 p=ROOT/"assets.json"; obj=json.loads(p.read_text())
 for a in obj["assets"]:
  target=PROJECT/a["path"]
  a["sha256"]=hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
 obj["generated_at_utc"]=datetime.now(timezone.utc).isoformat()
 p.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps({"assets":len(obj["assets"]),"updated_at_utc":obj["generated_at_utc"]},ensure_ascii=False))
if __name__=="__main__":main()
