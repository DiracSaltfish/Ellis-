"""Read-only fetch of SSE ETF PCF XML for the fixed validation date.

The endpoint is treated as a rolling/latest source: every response is saved only
after the XML fund code and TradingDay are checked. Credentials are not used.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = "20260908"
URL = "https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode={}"
OUT_DIR = ROOT / "raw" / "pcf_sse"
OUT_DIR.mkdir(parents=True, exist_ok=True)

symbols = [x for x in json.loads((ROOT / "inputs" / "symbols.json").read_text()) if x.endswith(".SH")]


def scalar_fields(body: bytes) -> dict[str, str]:
    root = ET.fromstring(body)
    return {
        node.tag.split("}")[-1]: (node.text or "").strip()
        for node in root.iter()
        if len(node) == 0
    }


def fetch(code: str) -> dict[str, object]:
    path = OUT_DIR / f"{code}_{TARGET}.xml"
    url = URL.format(code)
    out: dict[str, object] = {
        "kind": "pcf_sse",
        "code": code,
        "url": url,
        "path": str(path.relative_to(ROOT)),
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    try:
        if path.exists():
            body = path.read_bytes()
            out["cached"] = True
        else:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://www.sse.com.cn/",
                },
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read(16 * 1024 * 1024)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(body)
            tmp.replace(path)
        fields = scalar_fields(body)
        out.update(
            bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
            returned_code=fields.get("FundInstrumentID"),
            trading_day=fields.get("TradingDay"),
            status=(
                "TARGET_DATE"
                if fields.get("FundInstrumentID") == code and fields.get("TradingDay") == TARGET
                else "IDENTITY_OR_DATE_MISMATCH"
            ),
        )
    except Exception as exc:  # preserve a bounded, non-sensitive failure record
        out.update(status="ERROR", error_type=type(exc).__name__, error=str(exc)[:200])
    return out


results: list[dict[str, object]] = []
for idx, symbol in enumerate(symbols, 1):
    code = symbol.split(".")[0]
    result = fetch(code)
    results.append(result)
    if idx % 10 == 0:
        print(f"completed {idx}/{len(symbols)}", flush=True)
    # Keep the rolling exchange endpoint low-frequency and predictable.
    if idx < len(symbols) and not result.get("cached"):
        time.sleep(0.25)

(ROOT / "sse_pcf_manifest.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
from collections import Counter

print(json.dumps({"requested": len(results), "status": Counter(x.get("status") for x in results)}, ensure_ascii=False))
