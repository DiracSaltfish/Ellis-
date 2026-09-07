#!/usr/bin/env python3
"""Merge the append-only old-period and repaired missing-day IBKR manifests."""
from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = Path("/Users/ellis/工具程序开发/港股通相关性分析/outputs/hedge_selection_v2_20260906/agent_B/data/raw/ibkr_new_period/ibkr_fetch_manifest.json")
NEW = ROOT / "data/raw/ibkr_new_period/ibkr_fetch_manifest.json"
OUT = ROOT / "data/raw/ibkr_new_period/ibkr_fetch_manifest_merged.json"

def sha(p):
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1024*1024), b""): h.update(b)
    return h.hexdigest()

old, new = json.loads(OLD.read_text()), json.loads(NEW.read_text())
dates = sorted(set(old.get("dates", [])) | set(new.get("dates", [])))
contracts = dict(old.get("contracts", {})); contracts.update(new.get("contracts", {}))
logs = list(old.get("request_log", []))
seen = {(x.get("date"), x.get("contract_key"), x.get("status")) for x in logs}
for x in new.get("request_log", []):
    key = (x.get("date"), x.get("contract_key"), x.get("status"))
    if key not in seen:
        logs.append(x); seen.add(key)
out = {"retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "host": new.get("host", old.get("host")), "port": new.get("port", old.get("port")), "client_id": new.get("client_id", old.get("client_id")), "read_only_operations": sorted(set(old.get("read_only_operations", [])) | set(new.get("read_only_operations", []))), "dates": dates, "contracts": contracts, "request_log": logs, "source_manifests": [{"path": str(OLD), "sha256": sha(OLD)}, {"path": str(NEW), "sha256": sha(NEW)}]}
OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
NEW.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"dates": len(dates), "request_log": len(logs), "output": str(NEW)}, ensure_ascii=False, indent=2))
