"""Fetch unadjusted historical HK daily bars for the PCF union.

The endpoint is queried with an explicit date window. Existing 513090 daily
evidence is reused from baseline_513090; no minute endpoint is used here.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "raw" / "hk_daily"
OUT.mkdir(parents=True, exist_ok=True)
master = json.loads((ROOT / "inputs" / "hk_union_all_pcf.json").read_text())
start, end = "2026-08-28", "2026-09-08"


def fetch(code: str) -> dict[str, object]:
    path = OUT / f"hk{code}_day.json"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=hk{code},day,{start},{end},20,"
    out: dict[str, object] = {"code": code, "url": url, "path": str(path.relative_to(ROOT)), "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    try:
        baseline = ROOT / "baseline_513090" / f"tencent_hk{code}_day.json"
        if baseline.exists():
            body = baseline.read_bytes()
            out["reused_baseline"] = True
        elif path.exists():
            body = path.read_bytes()
            out["cached"] = True
        else:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read(4 * 1024 * 1024)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(body)
            tmp.replace(path)
        doc = json.loads(body)
        series = (doc.get("data") or {}).get(f"hk{code}", {})
        rows = series.get("day") or []
        dates = [r[0] for r in rows if isinstance(r, list) and r]
        out.update(bytes=len(body), sha256=hashlib.sha256(body).hexdigest(), dates=dates, rows=len(rows), status="TARGET_DATE" if "2026-09-08" in dates else "TARGET_DATE_MISSING")
        # Preserve reused baseline as the canonical daily file for uniform parsing.
        if baseline.exists() and not path.exists():
            path.write_bytes(body)
    except Exception as exc:
        out.update(status="ERROR", error_type=type(exc).__name__, error=str(exc)[:200])
    return out


codes = sorted(master)
with ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(fetch, codes))
(ROOT / "hk_daily_manifest.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
from collections import Counter
print(json.dumps({"requested": len(results), "status": Counter(x.get("status") for x in results), "reused_baseline": sum(bool(x.get("reused_baseline")) for x in results)}, ensure_ascii=False))
