"""Fetch only HK minute files absent from the existing cache."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
master = json.loads((ROOT / "inputs" / "hk_union_all_pcf.json").read_text())


def fetch(code: str) -> dict[str, object]:
    path = ROOT / "raw" / "hk" / f"hk{code}_minute.json"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=hk{code}"
    out: dict[str, object] = {"code": code, "url": url, "path": str(path.relative_to(ROOT)), "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    try:
        if path.exists():
            body = path.read_bytes()
            out["cached"] = True
        else:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read(8 * 1024 * 1024)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(body)
            tmp.replace(path)
        doc = json.loads(body)
        series = (doc.get("data") or {}).get(f"hk{code}", {}).get("data") or {}
        rows = series.get("data") or []
        out.update(bytes=len(body), sha256=hashlib.sha256(body).hexdigest(), date=series.get("date"), rows=len(rows), status="TARGET_DATE" if series.get("date") == "20260908" else "DATE_MISMATCH")
    except Exception as exc:
        out.update(status="ERROR", error_type=type(exc).__name__, error=str(exc)[:200])
    return out


codes = sorted(master)
with ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(fetch, codes))
(ROOT / "hk_all_minute_manifest.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
from collections import Counter
print(json.dumps({"requested": len(results), "status": Counter(x.get("status") for x in results), "cached": sum(bool(x.get("cached")) for x in results)}, ensure_ascii=False))
