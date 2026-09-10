"""Fetch the four prior SZSE PCFs needed for the five-day cross-check."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "raw" / "pcf_history"
OUT.mkdir(parents=True, exist_ok=True)
dates = ["20260907", "20260904", "20260903", "20260902"]
symbols = [x for x in json.loads((ROOT / "inputs" / "symbols.json").read_text()) if x.endswith(".SZ")]


def fetch(task: tuple[str, str]) -> dict[str, object]:
    code, day = task
    path = OUT / f"{code}_{day}.xml"
    url = f"https://reportdocs.static.szse.cn/files/text/ETFDown/pcf_{code}_{day}.xml"
    out: dict[str, object] = {"kind": "pcf_sz_history", "code": code, "trading_day_requested": day, "url": url, "path": str(path.relative_to(ROOT)), "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    try:
        if path.exists():
            body = path.read_bytes()
            out["cached"] = True
        else:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.szse.cn/"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read(16 * 1024 * 1024)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(body)
            tmp.replace(path)
        root = ET.fromstring(body)
        fields = {n.tag.split("}")[-1]: (n.text or "").strip() for n in root if len(n) == 0}
        out.update(bytes=len(body), sha256=hashlib.sha256(body).hexdigest(), returned_code=fields.get("SecurityID"), trading_day=fields.get("TradingDay"), status="TARGET_DATE" if fields.get("SecurityID") == code and fields.get("TradingDay") == day else "IDENTITY_OR_DATE_MISMATCH")
    except Exception as exc:
        out.update(status="ERROR", error_type=type(exc).__name__, error=str(exc)[:200])
    return out


tasks = [(s.split(".")[0], d) for s in symbols for d in dates]
with ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(fetch, tasks))
(ROOT / "sz_history_manifest.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
from collections import Counter
print(json.dumps({"requested": len(results), "status": Counter(x.get("status") for x in results)}, ensure_ascii=False))
