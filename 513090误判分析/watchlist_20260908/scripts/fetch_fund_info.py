"""Fetch public fund names and management companies for report labels."""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
symbols = json.loads((ROOT / "inputs" / "symbols.json").read_text())


def fetch(symbol: str) -> dict[str, object]:
    code = symbol.split(".")[0]
    url = f"https://fund.eastmoney.com/{code}.html"
    out: dict[str, object] = {"symbol": symbol, "code": code, "url": url, "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://fund.eastmoney.com/"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read(2 * 1024 * 1024)
        text = body.decode("utf-8-sig", "ignore")
        title = re.search(r"<title>\s*(.*?)\s*</title>", text, re.I | re.S)
        manager = re.search(r"管\s*理\s*人</span>\s*：\s*<a[^>]*>(.*?)</a>", text, re.I | re.S)
        fund_name = html.unescape(re.sub(r"<[^>]+>", "", title.group(1))).strip() if title else ""
        fund_name = re.sub(r"\s*\([^)]*\)\s*基金净值.*$", "", fund_name).strip()
        manager_name = html.unescape(re.sub(r"<[^>]+>", "", manager.group(1))).strip() if manager else ""
        out.update(bytes=len(body), sha256=hashlib.sha256(body).hexdigest(), fund_name=fund_name, manager=manager_name, status="PARSED" if fund_name else "PARSE_INCOMPLETE")
    except Exception as exc:
        out.update(status="ERROR", error_type=type(exc).__name__, error=str(exc)[:200])
    return out


with ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(fetch, symbols))
(ROOT / "fund_info_manifest.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
from collections import Counter
print(json.dumps({"requested": len(results), "status": Counter(x.get("status") for x in results), "manager_populated": sum(bool(x.get("manager")) for x in results)}, ensure_ascii=False))
