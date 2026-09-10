#!/usr/bin/env python3
"""批量查询东财基金公告，并分别识别更新招募书与产品资料概要。

用法：
  python3 query_eastmoney_notices.py --codes 159518
  python3 query_eastmoney_notices.py --codes-file codes.csv

脚本只使用标准库，默认将结果写入 ../临时数据/<代码>/。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = ROOT / "临时数据"
API_URL = "https://api.fund.eastmoney.com/f10/JJGG"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 Chrome/131 Safari/537.36"


def fetch_jsonp(code: str, page_index: int, page_size: int = 50, type_id: int = 1) -> dict:
    query = urlencode(
        {
            "callback": "jQueryCodex",
            "fundcode": code,
            "pageIndex": page_index,
            "pageSize": page_size,
            "type": type_id,
        }
    )
    url = f"{API_URL}?{query}"
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": f"https://fundf10.eastmoney.com/jjgg_{code}.html",
        },
    )
    with urlopen(req, timeout=30) as response:
        body = response.read().decode("utf-8", errors="replace").strip()
    match = re.match(r"^[^(]+\((.*)\)\s*$", body, re.S)
    if not match:
        raise RuntimeError(f"unexpected JSONP response for {code} page {page_index}")
    return json.loads(match.group(1))


def query_all(code: str, type_id: int = 1) -> tuple[list[dict], dict]:
    first = fetch_jsonp(code, 1, type_id=type_id)
    total = int(first.get("TotalCount") or 0)
    page_size = int(first.get("PageSize") or 50)
    notices = list(first.get("Data") or [])
    pages = max(1, (total + page_size - 1) // page_size)
    for page_index in range(2, pages + 1):
        page = fetch_jsonp(code, page_index, page_size=page_size, type_id=type_id)
        notices.extend(page.get("Data") or [])
    return notices, {"total_count": total, "page_size": page_size, "pages": pages, "type": type_id}


def is_prospectus(title: str) -> bool:
    return "招募说明书" in title and "更新" in title and "产品资料概要" not in title


def is_product_summary(title: str) -> bool:
    return "产品资料概要" in title and "更新" in title


def is_any_prospectus(title: str) -> bool:
    """识别初始招募书和更新招募书，供新成立基金兜底使用。"""
    return "招募说明书" in title and "产品资料概要" not in title


def notice_date(item: dict) -> str:
    return str(item.get("PUBLISHDATE") or item.get("PUBLISHDATEDesc") or "")[:10]


def enrich(code: str, item: dict) -> dict:
    announcement_id = item.get("ID", "")
    return {
        **item,
        "notice_date": notice_date(item),
        "notice_url": f"https://fund.eastmoney.com/gonggao/{code},{announcement_id}.html",
        "eastmoney_pdf_url": f"https://pdf.dfcfw.com/pdf/H2_{announcement_id}_1.pdf",
    }


def run(code: str) -> Path:
    notices, meta = query_all(code, type_id=1)
    enriched = [enrich(code, item) for item in notices]
    prospectuses = [x for x in enriched if is_prospectus(str(x.get("TITLE") or ""))]
    prospectuses_any = [x for x in enriched if is_any_prospectus(str(x.get("TITLE") or ""))]
    product_summaries = [x for x in enriched if is_product_summary(str(x.get("TITLE") or ""))]
    payload = {
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "fund_code": code,
        "source_page": f"https://fundf10.eastmoney.com/jjgg_{code}.html",
        "source_api": API_URL,
        "api_meta": meta,
        "latest_updated_prospectus": max(prospectuses, key=lambda x: x["notice_date"]) if prospectuses else None,
        "latest_prospectus_any": max(prospectuses_any, key=lambda x: x["notice_date"]) if prospectuses_any else None,
        "latest_product_summary_update": max(product_summaries, key=lambda x: x["notice_date"]) if product_summaries else None,
        "all_issuance_operation_notices": enriched,
    }
    out_dir = TEMP_ROOT / code
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{code}_eastmoney_issuance_operation_notices.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", nargs="+", help="基金代码，可传多个")
    parser.add_argument("--codes-file", type=Path, help="每行一个基金代码，或 CSV 首列为基金代码；支持 .SZ/.SH 后缀")
    args = parser.parse_args()
    codes = list(args.codes or [])
    if args.codes_file:
        with args.codes_file.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.reader(f):
                if row and row[0].strip():
                    codes.append(row[0].strip())
    normalized = []
    seen = set()
    for raw_code in codes:
        code = re.sub(r"\D", "", raw_code)
        if len(code) != 6 or code in seen:
            continue
        seen.add(code)
        normalized.append(code)
    codes = normalized
    if not codes:
        parser.error("请提供 --codes 或 --codes-file")
    for code in codes:
        try:
            print(run(code))
        except Exception as exc:
            print(f"ERROR {code}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
